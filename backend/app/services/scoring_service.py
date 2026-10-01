"""Supervisory scoring — EGI, NSI, DTS, eight dimensions, SAP (plan §6).

Turns `IndicatorResult` objects into the supervisory scores. This is the *only*
place that decides what a number on a card is worth.

The pipeline, exactly as plan §6.2:

    1  robust z            computed in `rules_engine` (§6.2 steps 1-2)
    2  EB shrinkage        also in `rules_engine`, where the peer values exist
    3  ramp to 0..1        configurable z0..z1 from the policy profile
    4  confidence          min(1, n/n_min) x assessability x data_trust
    5  BH FDR              Benjamini-Hochberg, per entity per cycle, at fdr_q
    6  family max          aggregate indicator families by MAXIMUM
    7  capped noisy-OR     1 - prod(1 - w_f * min(family_f, cap))

**Why steps 1 and 2 live in the rules engine.** Empirical-Bayes shrinkage needs
the peer values, and those exist only in the single read that produced
`dict[entity_id, Metric]`. Re-deriving them here would mean a second, subtly
different read of the same lake — which is how a subject and its cohort drift
apart and the tool starts comparing two different things. The rules engine
therefore publishes `effect_size` as the robust z of the **EB-shrunk** estimate,
and this module starts at step 3.

Four load-bearing decisions:

1. **A not-assessable dimension scores `None`, never `0.0`, and is dropped from
   the SAP average entirely.** So it cannot lower the score (no unfair penalty
   for absent data) and cannot raise the tier — plan §6.3 requires exactly that.
   Both halves matter. Imputing 0.0 punished every entity with thin coverage;
   letting an absent dimension count as benign silently rewarded it.

2. **Family score is `max(signal x confidence)`, not `max(signal)`.** §6.2 step 6
   says "maximum", but it computes step 4's confidence for a reason. An indicator
   firing on four observations and one firing on four hundred are not the same
   evidence, and a bare maximum discards that distinction precisely where it
   stops being visible on a card.

3. **DTS suppresses confidence; it is never itself a signal.** A low-quality
   submission must not look like a well-behaved one that happened to score low.
   Below `dts_floor`, confidence scales down in proportion to how far below the
   floor it sits, reaching zero only at zero data trust.

4. **The p-value is a normal approximation of the robust z, and is used only to
   order indicators within one entity's cycle.** A robust z is not a test
   statistic with a known null distribution, so this is not a calibrated
   significance test and no card may say "significant at p < 0.05". BH still does
   the real work — it controls the expected false-discovery rate regardless of how
   each p-value was derived.

The 0-100 composite index from v1 is **removed** as a decision artefact. Nothing
here produces one; the UI may render a convenience value at display time, but it
is never stored.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence

from app.config import get_settings
from app.db.sqlite import get_connection, transaction
from app.schemas.common import AttentionTier, RankInterval
from app.schemas.indicator import Assessability, Dimension, IndicatorResult, Period
from app.services.assessability import DIMENSION_SPECS, AssessabilityService
from app.services.baseline_service import get_baseline_service
from app.services.ledger import LedgerAction, get_ledger
from app.services.policy_profile import PolicyProfile, policy_profile
from app.services.rules_engine import get_rules_engine

log = logging.getLogger(__name__)

#: Weight of a dimension that is only `Partial`. A partial dimension still says
#: something, so it counts; it just counts for half.
PARTIAL_WEIGHT = 0.5

#: Most a dimension interval may span from its point estimate. The interval is an
#: honest report of spread, not a calibrated confidence interval, so it is bounded
#: rather than allowed to reach 0 or 1 on thin evidence.
MAX_INTERVAL_SPAN = 0.25


# --------------------------------------------------------------------- results --
@dataclass(frozen=True)
class Signal:
    """One indicator after steps 3-5 of plan §6.2.

    `signal` is the ramped 0..1 magnitude, `p_value` the normal approximation of
    the robust z, `survives_fdr` whether Benjamini-Hochberg kept it.
    """

    indicator_id: str
    family: str
    dimension: Dimension
    signal: float
    confidence: float
    p_value: float | None
    survives_fdr: bool
    effect_size: float | None
    suppressed_reason: str | None = None

    @property
    def weighted(self) -> float:
        return self.signal * self.confidence


@dataclass(frozen=True)
class DimensionResult:
    """One of the eight capability dimensions."""

    dimension: Dimension
    score: float | None
    assessability: Assessability
    missing_fields: tuple[str, ...] = ()
    families: tuple[str, ...] = ()
    ci_low: float | None = None
    ci_high: float | None = None
    reason: str | None = None

    @property
    def sap_weight(self) -> float:
        """Contribution weight in the SAP average. Zero means "leave it out".

        `NOT_ASSESSABLE` is excluded from numerator *and* denominator, which is
        what satisfies plan §6.3 in both directions at once: a missing dimension
        neither drags the score down nor lifts it into a higher tier.
        """
        if self.score is None or self.assessability is Assessability.NOT_ASSESSABLE:
            return 0.0
        return PARTIAL_WEIGHT if self.assessability is Assessability.PARTIAL else 1.0


@dataclass
class EntityScore:
    """Everything plan §6.1 asks for, for one entity in one period."""

    entity_id: str
    period_start: date
    period_end: date

    egi: float | None = None
    nsi: float | None = None
    dts: float | None = None
    sap: float | None = None
    sap_tier: str = AttentionTier.NOT_ASSESSABLE.value
    sap_rank: int | None = None
    sap_rank_low: int | None = None
    sap_rank_high: int | None = None

    dimensions: list[DimensionResult] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    family_scores: dict[str, float] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    run_id: str | None = None
    overall_assessability: Assessability = Assessability.NOT_ASSESSABLE
    measurable_share: float = 0.0

    def rank_interval(self) -> RankInterval | None:
        if self.sap_rank is None:
            return None
        return RankInterval(
            rank=self.sap_rank,
            low=self.sap_rank_low or self.sap_rank,
            high=self.sap_rank_high or self.sap_rank,
            method="weight_perturbation_dirichlet+bootstrap_over_peers",
        )

    @property
    def n_not_assessable_dimensions(self) -> int:
        return sum(1 for d in self.dimensions if d.score is None)


class ScoringError(Exception):
    """Raised when scoring cannot be attempted at all."""


# ----------------------------------------------------------------- statistics --
def normal_p(z: float) -> float:
    """Two-sided p-value for a standard normal deviate.

    Read this for what it is: a yardstick for *ranking* indicators against each
    other within one cycle, not a calibrated significance test. It exists so BH
    has something monotone to threshold, and BH is what actually controls the
    false-discovery rate.
    """
    return math.erfc(abs(z) / math.sqrt(2.0))


def benjamini_hochberg(p_values: dict[str, float], q: float) -> dict[str, bool]:
    """BH step-up procedure. Returns the kept set.

    Ties break by name so the outcome is deterministic: two indicators with
    identical p-values must not swap places between runs, or the same submission
    would score differently on two passes with no change in the data.
    """
    if not p_values:
        return {}
    ordered = sorted(p_values.items(), key=lambda kv: (kv[1], kv[0]))
    n = len(ordered)
    kept: dict[str, bool] = {name: False for name, _ in ordered}

    cutoff_rank = 0
    for index, (_name, p) in enumerate(ordered, start=1):
        if p <= q * index / n:
            cutoff_rank = index
    for name, _ in ordered[:cutoff_rank]:
        kept[name] = True
    return kept


def capped_noisy_or(terms: Sequence[tuple[float, float, float]]) -> float:
    """`1 - Π(1 - w_f x min(family_f, cap))` — plan §6.2 step 7.

    The cap is what stops correlated indicators double-counting: EG-01 and EG-02
    both read the same rapid-thin-closure evidence, so a dimension fed by both
    would otherwise report the same suspicious pattern twice at full strength.
    """
    residual = 1.0
    for family, weight, cap in terms:
        bounded = min(max(family, 0.0), cap)
        residual *= 1.0 - (weight * bounded)
    return max(0.0, min(1.0, 1.0 - residual))


# ---------------------------------------------------------------- the service --
class ScoringService:
    """Plan §6 in one place."""

    def __init__(
        self,
        policy: PolicyProfile | None = None,
        rules=None,
        baseline=None,
        assessability=None,
    ) -> None:
        self.policy = policy or policy_profile()
        self.rules = rules or get_rules_engine(self.policy)
        self.bsl = baseline or get_baseline_service(self.policy)
        self.assessability = assessability or AssessabilityService()
        self.seed = get_settings().seed

    # -- DTS ---------------------------------------------------------------
    def _dq_score(self, entity_id: str, period: Period) -> float | None:
        """The submission's own `dq_score` for this period, or `None`.

        `None` rather than 0.0: an entity with no submission has no data-quality
        score because nothing was ever assessed, and reporting 0.0 would state
        that the data was terrible rather than that there was none.
        """
        row = get_connection().execute(
            "SELECT dq_score FROM submission WHERE entity_id = ? "
            "AND period_start <= ? AND period_end >= ? "
            "ORDER BY version DESC LIMIT 1",
            (entity_id, period.end.isoformat(), period.start.isoformat()),
        ).fetchone()
        if row is None or row["dq_score"] is None:
            return None
        return max(0.0, min(1.0, float(row["dq_score"])))

    def _dts(self, dq: float | None, computed_share: float | None) -> float | None:
        """Data Trust Score: `dq_score` damped by what could actually be measured.

        A submission can be internally pristine and still answer two of eight
        questions. Calling that 1.0 data trust would overstate it, so the
        completeness term is blended in at half weight — DQ describes the quality
        of what arrived, this describes how much arrived that could be used, and
        neither alone is "trustworthy".

        `None` when there is no `dq_score`, and also when `computed_share` is
        `None` — which means no indicator was implemented to measure completeness
        against. That is an unknown, not a zero, and blending in a zero there
        would halve the trust of a submission nobody has built a way to assess yet.
        """
        if dq is None or computed_share is None:
            return None
        share = max(0.0, min(1.0, computed_share))
        blended = 0.5 * dq + 0.5 * share
        return round(max(0.0, min(1.0, blended)), 6)

    def _confidence(self, result: IndicatorResult, dts: float | None) -> float:
        """Step 4, then the global DTS suppression.

        The rules engine already computed `min(1, n/n_min) x assessability x
        data_trust`. What is left is the one factor that is a property of the
        *submission* rather than of the indicator: below `dts_floor` a submission
        cannot support a confident finding about anything, so confidence scales
        down in proportion to how far below the floor it sits.
        """
        base = float(result.confidence)
        floor = max(1e-6, self.policy.dts_floor)
        if dts is None or dts >= floor:
            return base
        # Nil penalty at the floor, zero only at zero data trust.
        return base * max(0.0, dts / floor)

    # -- steps 3-5 ---------------------------------------------------------
    def _raw_signals(self, results: dict[str, IndicatorResult], dts: float | None) -> list[Signal]:
        """Steps 3-4 for every indicator that has something to say.

        Three different things can happen to an indicator and they must not be
        collapsed into one:

        * it is a **reference stub** — no detector exists, so there is no opinion.
          Dropped. Carrying it as 0.0 would make "we never looked" identical to
          "we looked and found nothing".
        * it **computed but has no usable peer baseline** — the measurement
          happened, the comparison did not. Dropped, and named in the caveats,
          because no effect size can honestly be derived from it.
        * it **computed with an effect size** — kept, *including when the effect
          size is zero*. A zero is the answer "measured, and nothing was found",
          and it is the only answer that lets a clean entity be reported as
          examined-and-clean. Omitting it would make every compliant entity look
          like one Orion could not see, which is the same false accusation as
          reporting a dirty entity as clean.
        """
        out: list[Signal] = []
        for indicator_id, result in results.items():
            if not result.is_computable or result.value is None:
                continue
            if not result.family or result.primary_dimension is None:
                log.warning(
                    "%s returned a result with no family or dimension; excluded "
                    "from scoring rather than guessing a bucket",
                    indicator_id,
                )
                continue

            if self.rules.is_stub(result):
                log.debug("%s is a reference stub; no signal contributed", indicator_id)
                continue

            z = result.effect_size
            if z is None:
                log.debug(
                    "%s computed a value but no effect size; no signal contributed",
                    indicator_id,
                )
                continue

            out.append(
                Signal(
                    indicator_id=indicator_id,
                    family=str(result.family),
                    dimension=result.primary_dimension,
                    signal=self.policy.ramp(z),
                    confidence=self._confidence(result, dts),
                    # A zero deviation has p = 1.0, not None. Passing None here
                    # would exempt it from BH, and an exempt test is a test that
                    # cannot be caught being wrong.
                    p_value=normal_p(z),
                    survives_fdr=False,
                    effect_size=z,
                )
            )
        return out

    def _apply_fdr(self, signals: Sequence[Signal]) -> list[Signal]:
        """Step 5. BH per entity per cycle, at `fdr_q`.

        An indicator that does not survive contributes no signal. That is the
        point of the correction: across a whole catalogue of detectors firing at
        every entity, some always look extreme, and without correction the union
        of them approaches a finding on everything.
        """
        testable = [s for s in signals if s.p_value is not None]
        if not testable:
            return list(signals)

        kept = benjamini_hochberg(
            {s.indicator_id: float(s.p_value) for s in testable},  # type: ignore[arg-type]
            self.policy.fdr_q,
        )
        out: list[Signal] = []
        for signal in signals:
            if signal.p_value is None:
                out.append(signal)
                continue
            if kept.get(signal.indicator_id, False):
                out.append(signal)
                continue
            out.append(
                Signal(
                    indicator_id=signal.indicator_id,
                    family=signal.family,
                    dimension=signal.dimension,
                    signal=0.0,
                    confidence=signal.confidence,
                    p_value=signal.p_value,
                    survives_fdr=False,
                    effect_size=signal.effect_size,
                    suppressed_reason=(
                        f"did not survive Benjamini-Hochberg at q="
                        f"{self.policy.fdr_q:g} within this entity's cycle"
                    ),
                )
            )
        return out

    # -- step 6 ------------------------------------------------------------
    def _family_scores(self, signals: Sequence[Signal]) -> dict[str, float]:
        """Step 6: family score is the maximum of its indicators' weighted signals.

        Weighted by confidence rather than bare, so an indicator on four
        observations cannot outrank one on four hundred for the same family.
        Maximum rather than sum, so correlated detectors inside one family do not
        add their strength together.

        A family whose detectors all ran and all came back null scores **0.0**
        rather than being absent from the mapping. Presence and magnitude are
        different facts: `0.0` says "this family was measured and nothing adverse
        was found", and a missing key would say "this family was never measured".
        Reporting the second when the first is true is how a well-run entity ends
        up filed as unexaminable.
        """
        by_family: dict[str, list[Signal]] = {}
        for signal in signals:
            by_family.setdefault(signal.family, []).append(signal)

        scores: dict[str, float] = {}
        for family, members in by_family.items():
            live = [m.weighted for m in members if not m.suppressed_reason]
            scores[family] = max(live) if live else 0.0
        return scores

    # -- step 7 ------------------------------------------------------------
    def _dimension_results(
        self,
        entity_id: str,
        signals: Sequence[Signal],
        family_scores: dict[str, float],
    ) -> list[DimensionResult]:
        """Capped noisy-OR per dimension, gated on the §4.4 assessability matrix.

        The gate is not cosmetic. A dimension whose evidence is absent reports
        `None` even when some family carries a score from an unrelated detector —
        otherwise the noise floor of whichever detectors did run would present as
        a finding about a dimension nobody could examine.
        """
        report = self.assessability.report(entity_id=entity_id)
        by_dimension = {d.dimension: d for d in report.dimensions}

        families_for: dict[Dimension, set[str]] = {}
        for signal in signals:
            families_for.setdefault(signal.dimension, set()).add(signal.family)

        results: list[DimensionResult] = []
        for spec in DIMENSION_SPECS:
            dimension = spec.dimension
            gate = by_dimension.get(dimension)
            state = gate.assessability if gate else Assessability.NOT_ASSESSABLE
            missing = tuple(gate.missing_fields) if gate else ()
            families = tuple(sorted(families_for.get(dimension, set())))
            terms = [
                (family_scores[f], self.policy.family_weight(f), self.policy.family_cap(f))
                for f in families
                if f in family_scores
            ]

            if state is Assessability.NOT_ASSESSABLE:
                results.append(
                    DimensionResult(
                        dimension=dimension,
                        score=None,
                        assessability=state,
                        missing_fields=missing,
                        families=families,
                        reason=(
                            "the evidence this dimension needs is not in the "
                            "submission; no score is reported rather than a "
                            "neutral-looking one"
                        ),
                    )
                )
                continue

            if not terms:
                results.append(
                    DimensionResult(
                        dimension=dimension,
                        score=None,
                        assessability=state,
                        missing_fields=missing,
                        families=families,
                        reason=(
                            "the dimension is measurable, but no indicator ran "
                            "against it this cycle"
                        ),
                    )
                )
                continue

            score = capped_noisy_or(terms)
            ci_low, ci_high = self._dimension_interval(signals, score)
            results.append(
                DimensionResult(
                    dimension=dimension,
                    score=round(score, 6),
                    assessability=state,
                    missing_fields=missing,
                    families=families,
                    ci_low=ci_low,
                    ci_high=ci_high,
                )
            )
        return results

    def _dimension_interval(
        self,
        signals: Sequence[Signal],
        point: float,
    ) -> tuple[float, float]:
        """A propagated spread for a dimension score.

        Deliberately an **approximation**, and labelled as one wherever it is
        surfaced. Each surviving indicator's z is re-read with one standard error
        of slack, and the resulting shift is applied to the point estimate. This
        is not a calibrated confidence interval — that would need the sampling
        distribution of the noisy-OR itself, which depends on family correlation
        structure we do not measure. It is honest about *spread* and silent about
        coverage, so it is never rendered as "95% CI".
        """
        contributors = [s for s in signals if not s.suppressed_reason and s.effect_size]
        if not contributors:
            return (round(max(0.0, point), 6), round(min(1.0, point), 6))

        widest = 0.0
        for signal in contributors:
            z = abs(float(signal.effect_size or 0.0))
            # Confidence stands in for how many observations are behind the z, so
            # a thin estimate gets a proportionally wider interval.
            n_effective = max(1.0, signal.confidence * self.policy.n_min)
            widest = max(widest, z / math.sqrt(n_effective))

        span = min(MAX_INTERVAL_SPAN, widest * 0.05)
        return (
            round(max(0.0, point - span), 6),
            round(min(1.0, point + span), 6),
        )

    # -- EGI / NSI ---------------------------------------------------------
    def _index(self, families: Iterable[str], family_scores: dict[str, float]) -> float | None:
        """Capped noisy-OR over a named set of families, or `None` if none ran.

        `None` rather than `0.0`: with no evidence in any family there is neither
        an execution gap nor negative space, which is a different statement from
        "we found nothing wrong".
        """
        terms = [
            (family_scores[f], self.policy.family_weight(f), self.policy.family_cap(f))
            for f in families
            if f in family_scores
        ]
        if not terms:
            return None
        return round(capped_noisy_or(terms), 6)

    # -- SAP ---------------------------------------------------------------
    def _sap(self, dimensions: Sequence[DimensionResult]) -> float | None:
        """Weighted mean of dimension scores, over the dimensions that produced one.

        Renormalising over the surviving dimensions is what makes a missing
        dimension inert. Weights come from the policy; `sap_weight` supplies the
        assessability discount and drops not-assessable rows entirely.
        """
        weights = self.policy.dimension_weights
        numerator = 0.0
        denominator = 0.0
        for dimension_result in dimensions:
            share = dimension_result.sap_weight
            if share <= 0.0:
                continue
            policy_weight = float(
                weights.get(dimension_result.dimension.value, 0.0)
            )
            if policy_weight <= 0.0:
                continue
            numerator += policy_weight * share * float(dimension_result.score)
            denominator += policy_weight * share
        if denominator <= 0.0:
            return None
        return round(numerator / denominator, 6)

    # -- entity scoring ----------------------------------------------------
    def score_entity(
        self,
        entity_id: str,
        period_start: date,
        period_end: date,
        dts: float | None = None,
    ) -> EntityScore:
        """Score one entity for one period. No writes.

        `dts` is accepted so a caller scoring a whole period can compute it once
        from the cohort-wide completeness picture; omit it and it is derived here.
        """
        period = Period(start=period_start, end=period_end)
        results = self.rules.run_all(entity_id, period_start, period_end)

        # Completeness is the share of *implemented* indicators that produced a
        # value. Stubs are excluded from the denominator: an unwritten detector
        # says nothing about the quality of this entity's submission, and counting
        # it would drag every entity's DTS to zero until Dev 3 lands. With nothing
        # implemented yet there is no denominator at all, so completeness is
        # unmeasured (`None`) rather than zero.
        implemented = [r for r in results.values() if not self.rules.is_stub(r)]
        computed = [r for r in implemented if r.is_computable and r.value is not None]
        computed_share = len(computed) / len(implemented) if implemented else None
        if dts is None:
            dts = self._dts(self._dq_score(entity_id, period), computed_share)

        signals = self._apply_fdr(self._raw_signals(results, dts))
        family_scores = self._family_scores(signals)
        dimensions = self._dimension_results(entity_id, signals, family_scores)

        egi = self._index(self.policy.egi_families, family_scores)
        nsi = self._index(self.policy.nsi_families, family_scores)
        sap = self._sap(dimensions)

        score = EntityScore(
            entity_id=entity_id,
            period_start=period_start,
            period_end=period_end,
            egi=egi,
            nsi=nsi,
            dts=dts,
            sap=sap,
            dimensions=dimensions,
            signals=signals,
            family_scores=family_scores,
        )
        score.overall_assessability = self._overall(dimensions)
        score.measurable_share = self._measurable_share(dimensions)
        score.sap_tier = self._tier(score)
        score.caveats = self._caveats(score, results)
        return score

    def _measurable_share(self, dimensions: Sequence[DimensionResult]) -> float:
        """Share of §6.1 weight that produced an actual score.

        Weighted, not a dimension count: the eight dimensions are not equal
        contributions to the priority figure, and a submission missing the two
        cheapest ones is in a materially different position from one missing the
        two dearest.
        """
        weights = self.policy.dimension_weights
        total = sum(float(w) for w in weights.values())
        if total <= 0.0:
            return 0.0
        measured = sum(
            float(weights.get(d.dimension.value, 0.0))
            for d in dimensions
            if d.score is not None
        )
        return round(measured / total, 6)

    def _tier(self, score: EntityScore) -> str:
        """Attention tier, or `not_assessable` when the picture is too thin.

        Two separate refusals, and both matter. No SAP means nothing was measured.
        A SAP computed from too little of the dimension set means the cutpoints
        cannot be applied to it at all -- the top tier is unreachable by
        construction, so a genuinely adverse entity reads as a low-priority one.
        The number is still published; only the tier is withheld.
        """
        if score.sap is None:
            return AttentionTier.NOT_ASSESSABLE.value
        if score.overall_assessability is Assessability.NOT_ASSESSABLE:
            return AttentionTier.NOT_ASSESSABLE.value
        if score.measurable_share < self.policy.min_measurable_share:
            return AttentionTier.NOT_ASSESSABLE.value
        return self.policy.tier_for(score.sap)

    @staticmethod
    def _overall(dimensions: Sequence[DimensionResult]) -> Assessability:
        """Overall state, weighted to the weakest evidence.

        Deliberately not a mean of multipliers — averaging is exactly what turns
        missing evidence into a middling score, which reads as "partly assessed"
        when most of it was absent.
        """
        assessable = sum(1 for d in dimensions if d.assessability is Assessability.ASSESSABLE)
        partial = sum(1 for d in dimensions if d.assessability is Assessability.PARTIAL)
        not_assessable = sum(
            1 for d in dimensions if d.assessability is Assessability.NOT_ASSESSABLE
        )
        if not_assessable == 0 and partial == 0:
            return Assessability.ASSESSABLE
        if assessable == 0:
            return Assessability.NOT_ASSESSABLE
        if partial >= assessable:
            return Assessability.PARTIAL
        return Assessability.ASSESSABLE

    def _caveats(
        self, score: EntityScore, results: dict[str, IndicatorResult]
    ) -> list[str]:
        """Plain-language reasons the result is incomplete or uncertain.

        Every one of these is a statement about what Orion could not see. None of
        them is a statement about the entity's conduct, which is the distinction
        the whole tool turns on.
        """
        caveats: list[str] = []

        if score.dts is None:
            caveats.append(
                "No data-quality score is on record for this period, so no "
                "result here is expressed with confidence."
            )
        elif score.dts < self.policy.dts_floor:
            caveats.append(
                f"Data trust is {score.dts:.2f}, below the policy floor of "
                f"{self.policy.dts_floor:.2f}; every confidence figure has been "
                "scaled down accordingly."
            )

        stubs = sorted(i for i, r in results.items() if self.rules.is_stub(r))
        if stubs:
            caveats.append(
                f"{len(stubs)} negative-space indicator(s) have no detector in "
                f"this build ({', '.join(stubs)}). Their absence says nothing "
                "about this entity."
            )

        gaps = [d.dimension.value for d in score.dimensions if d.score is None]
        if gaps:
            caveats.append(
                f"No score is reported for {len(gaps)} of {len(score.dimensions)} "
                f"dimensions ({', '.join(gaps)}). They are excluded from the "
                "priority figure rather than counted as clean."
            )
        if score.sap is not None and score.measurable_share < self.policy.min_measurable_share:
            caveats.append(
                f"Only {score.measurable_share:.0%} of the weighted dimension set "
                f"could be measured, below the {self.policy.min_measurable_share:.0%} "
                "needed to assign an attention tier, so no tier is given. The "
                "priority figure above is real but cannot be graded."
            )

        suppressed = sorted(s.indicator_id for s in score.signals if s.suppressed_reason)
        if suppressed:
            caveats.append(
                f"{len(suppressed)} indicator(s) did not survive multiple-testing "
                f"correction ({', '.join(suppressed)}) and contribute no signal."
            )

        # Read from `results`, not from `signals`. An indicator with no usable
        # peer baseline never becomes a signal at all, so asking the signals for
        # it would report nothing and quietly hide the limitation.
        unbaselined = sorted(
            indicator_id
            for indicator_id, result in results.items()
            if not self.rules.is_stub(result)
            and result.is_computable
            and result.value is not None
            and result.effect_size is None
        )
        if unbaselined:
            caveats.append(
                f"{len(unbaselined)} indicator(s) had no usable peer baseline "
                f"({', '.join(unbaselined)}); their values are reported without "
                "an effect size and contribute no signal."
            )

        return caveats

    # -- period scoring ----------------------------------------------------
    def entity_ids(self, period_start: date, period_end: date) -> list[str]:
        """Every entity with a submission overlapping this period.

        Entities with no submission are deliberately **not** included. They have
        no data, and scoring them would produce a row of nulls that looks like a
        result. They surface as `not_assessable` through the entity list instead.
        """
        rows = get_connection().execute(
            "SELECT DISTINCT entity_id FROM submission "
            "WHERE period_start <= ? AND period_end >= ? "
            "ORDER BY entity_id",
            (period_end.isoformat(), period_start.isoformat()),
        ).fetchall()
        return [str(row["entity_id"]) for row in rows]

    def score_period(
        self,
        period_start: date,
        period_end: date,
        persist: bool = True,
        actor: str = "scoring_service",
    ) -> list[EntityScore]:
        """Score every entity in the period, rank them, and persist.

        Ranking is inherently cohort-relative, so it cannot be done one entity at
        a time — the rank of an entity depends on who else is present.
        """
        entity_ids = self.entity_ids(period_start, period_end)
        if not entity_ids:
            raise ScoringError(
                f"No submissions overlap {period_start.isoformat()}.."
                f"{period_end.isoformat()}, so there is nothing to score. This is "
                "not the same as 'no entities scored well'."
            )

        scores = [
            self.score_entity(entity_id, period_start, period_end) for entity_id in entity_ids
        ]
        self._rank(scores)

        if persist:
            run_id = self._create_run(period_start, period_end, entity_ids)
            self._persist(run_id, scores)
            for score in scores:
                score.run_id = run_id
            self._ledger_complete(run_id, period_start, period_end, scores)
        return scores

    def _rank(self, scores: list[EntityScore]) -> None:
        """Assign SAP ranks with intervals, then tiers.

        Entities with no SAP are excluded from the ranking entirely rather than
        ranked at the bottom: `None` means the entity could not be assessed, and a
        rank is a statement that it was compared against everyone else.
        """
        rankable = {s.entity_id: float(s.sap) for s in scores if s.sap is not None}
        intervals = self.bsl.rank_interval(
            rankable,
            weights=self.policy.dimension_weights,
            bootstrap_draws=self.policy.draws["bootstrap"],
            seed=self.seed,
        )
        for score in scores:
            interval = intervals.get(score.entity_id)
            if interval:
                score.sap_rank = int(interval["rank"])
                score.sap_rank_low = int(interval["low"])
                score.sap_rank_high = int(interval["high"])
            score.sap_tier = self._tier(score)

    # -- persistence -------------------------------------------------------
    def _create_run(
        self, period_start: date, period_end: date, entity_ids: Sequence[str]
    ) -> str:
        """Open a `run` row so every score has a lineage anchor.

        `dimension_score` and `entity_score` both reference `run(run_id)`, and a
        score with no run behind it cannot be traced to the policy version, the
        seed, or the inputs that produced it.
        """
        rows = get_connection().execute(
            "SELECT submission_id, manifest_hash FROM submission "
            "WHERE period_start <= ? AND period_end >= ?",
            (period_end.isoformat(), period_start.isoformat()),
        ).fetchall()
        manifests = sorted({str(row["manifest_hash"]) for row in rows if row["manifest_hash"]})
        manifest_blob = json.dumps(manifests)
        config_blob = json.dumps(
            {
                "policy_profile": self.policy.profile_id,
                "policy_version": self.policy.version,
                "policy_hash": self.policy.content_hash,
                "ramps": self.policy.ramps,
                "fdr_q": self.policy.fdr_q,
                "dts_floor": self.policy.dts_floor,
                "seed": self.seed,
            },
            sort_keys=True,
        )

        run_id = f"run_{uuid.uuid4().hex[:16]}"
        with transaction() as conn:
            conn.execute(
                "INSERT INTO run (run_id, created_ts, started_ts, status, "
                " input_manifest_hashes, config_hash, policy_hash, code_version, seed) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    "running",
                    manifest_blob,
                    hashlib.sha256(config_blob.encode("utf-8")).hexdigest(),
                    self.policy.content_hash,
                    _code_version(),
                    self.seed,
                ),
            )
        return run_id

    def _persist(self, run_id: str, scores: Sequence[EntityScore]) -> None:
        """Write `dimension_score` and `entity_score`.

        `assessability` is stored as the 1.0/0.5/0.0 multiplier the DDL's CHECK
        constraint allows, and the readable state is recovered from
        `score IS NULL` — which is the same distinction the API reports and the
        same one the table was designed to hold.
        """
        with transaction() as conn:
            for score in scores:
                for dimension in score.dimensions:
                    conn.execute(
                        "INSERT OR REPLACE INTO dimension_score "
                        "(entity_id, period_start, period_end, dimension, score, "
                        " ci_low, ci_high, assessability, dts, tier, run_id) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            score.entity_id,
                            score.period_start.isoformat(),
                            score.period_end.isoformat(),
                            dimension.dimension.value,
                            dimension.score,
                            dimension.ci_low,
                            dimension.ci_high,
                            dimension.assessability.multiplier
                            if dimension.score is not None
                            else None,
                            score.dts,
                            None,
                            run_id,
                        ),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO entity_score "
                    "(entity_id, period_start, period_end, egi, nsi, dts, sap, "
                    " sap_rank, sap_rank_low, sap_rank_high, sap_tier, run_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        score.entity_id,
                        score.period_start.isoformat(),
                        score.period_end.isoformat(),
                        score.egi,
                        score.nsi,
                        score.dts,
                        score.sap,
                        score.sap_rank,
                        score.sap_rank_low,
                        score.sap_rank_high,
                        score.sap_tier,
                        run_id,
                    ),
                )
        total = len(scores)
        output_blob = json.dumps(
            {
                "entities": sorted(s.entity_id for s in scores),
                "n_entities": total,
                "egi_reported": sum(1 for s in scores if s.egi is not None),
                "nsi_reported": sum(1 for s in scores if s.nsi is not None),
                "sap_reported": sum(1 for s in scores if s.sap is not None),
            },
            sort_keys=True,
        )
        with transaction() as conn:
            conn.execute(
                "UPDATE run SET finished_ts = ?, status = 'complete', output_hash = ? "
                "WHERE run_id = ?",
                (
                    datetime.now(timezone.utc).isoformat(),
                    hashlib.sha256(output_blob.encode("utf-8")).hexdigest(),
                    run_id,
                ),
            )
        log.info(
            "scored %d entities for %s..%s",
            total,
            scores[0].period_start.isoformat() if scores else "-",
            scores[0].period_end.isoformat() if scores else "-",
        )

    def _ledger_complete(
        self,
        run_id: str,
        period_start: date,
        period_end: date,
        scores: Sequence[EntityScore],
    ) -> None:
        """One ledger entry for the scoring pass.

        Counts, not per-entity scores: the ledger records that a decision pass
        happened under a given policy version and seed, and the numbers live in
        the tables where they can be queried and re-derived.
        """
        get_ledger().append(
            actor="scoring_service",
            action=LedgerAction.SCORING_COMPLETED,
            payload={
                "kind": "scoring",
                "run_id": run_id,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "policy_profile": self.policy.profile_id,
                "policy_version": self.policy.version,
                "policy_hash": self.policy.content_hash,
                "seed": self.seed,
                "n_entities": len(scores),
                "n_with_egi": sum(1 for s in scores if s.egi is not None),
                "n_with_nsi": sum(1 for s in scores if s.nsi is not None),
                "n_not_assessable": sum(
                    1
                    for s in scores
                    if s.overall_assessability is Assessability.NOT_ASSESSABLE
                ),
            },
        )


def _code_version() -> str:
    """Best-effort code identity for the run record.

    A git hash when the tree is available, otherwise a hash of the service
    sources. Never raises: a missing VCS is not a reason to fail a scoring pass,
    but it *is* a reason to fall back to something reproducible.
    """
    try:
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
        return f"sha256:{digest.hexdigest()[:16]}"
    except Exception:  # pragma: no cover - defensive
        return "unknown"


_service: ScoringService | None = None


def get_scoring_service(policy: PolicyProfile | None = None) -> ScoringService:
    """The shared service, or a fresh one bound to an explicitly named policy.

    A caller that passes a policy gets exactly that policy, always. Handing back a
    singleton bound to a different profile would mean the policy hash written into
    `run.config_hash` describes a scoring pass that never happened, and
    `content_hash` cannot be used to detect that: it identifies the file a
    profile was loaded from, not the dict in hand.
    """
    global _service
    if policy is not None:
        return ScoringService(policy=policy)
    if _service is None:
        _service = ScoringService()
    return _service