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
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence

from app.config import get_settings
from app.db.sqlite import get_connection, transaction
from app.schemas.common import (
    AttentionTier,
    CoverageType,
    RankInterval,
    SizeTier,
    SocModel,
)
from app.schemas.entity import (
    DimensionScoreOut,
    EntityListItem,
    EntityOut,
    EntitySummaryOut,
)
from app.schemas.indicator import Assessability, Dimension, IndicatorResult, Period
from app.schemas.ledger import RunOut
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
    # The full per-indicator results behind the signals. The signals are the
    # distilled view after FDR and suppression; the cards, counterfactuals and
    # "why was this not flagged" answers need the rich objects (evidence rows,
    # baseline, confidence breakdown) that were already computed and would
    # otherwise have to be recomputed after the fact — recomputation over the
    # live evidence is how a card drifts from the run it claims to describe.
    indicator_results: dict[str, IndicatorResult] = field(default_factory=dict)

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
# One term of a confidence breakdown, when present. The breakdown may be None
# (an early not-assessable result never builds one), and a missing term must
# not become 0.0 — a 0.0 term would read as "this factor contributed nothing",
# which is a measurement of a quantity that was never measured.
def _term(
    breakdown, key: str
) -> float | None:
    if breakdown is None:
        return None
    return getattr(breakdown, key, None)


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
    ) -> tuple[list[DimensionResult], dict[str, float]]:
        """Capped noisy-OR per dimension, gated on the §4.4 assessability matrix.

        Returns the per-dimension results *and* the subset of `family_scores`
        belonging to dimensions that survived the gate, so that EGI and NSI index
        the same evidence the dimension layer was willing to publish. Indexing the
        ungated scores separately was how an entity ended up with a non-zero EGI
        and eight `None` dimensions.

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
        admissible: dict[str, float] = {}
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

            admissible.update({f: family_scores[f] for f in families if f in family_scores})

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
        return results, admissible

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
        indicators: Sequence[str] | None = None,
    ) -> EntityScore:
        """Score one entity for one period. No writes.

        `dts` is accepted so a caller scoring a whole period can compute it once
        from the cohort-wide completeness picture; omit it and it is derived here.

        `indicators` narrows the run to named indicator ids. Every dimension is
        still scored, but one with no indicators behind it comes out
        not-assessable rather than zero — a targeted run must not look like a
        full one that happened to find nothing in the dimensions it skipped.
        """
        period = Period(start=period_start, end=period_end)
        results = self.rules.run_all(entity_id, period_start, period_end, only=indicators)

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
        dimensions, admissible_families = self._dimension_results(
            entity_id, signals, family_scores
        )

        # Indexed over the *admissible* families only. The §4.4 gate already refuses
        # a dimension whose evidence is absent, but EGI and NSI were being computed
        # from the raw family scores, so a detector running against evidence the
        # gate had just declared unusable still moved a published entity-level
        # number. An entity could then show a non-zero EGI with every dimension
        # reporting `None` — an adverse index built entirely from evidence nobody
        # could examine, which is the shape this tool must never produce.
        egi = self._index(self.policy.egi_families, admissible_families)
        nsi = self._index(self.policy.nsi_families, admissible_families)
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
            indicator_results=results,
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

    def _overall(self, dimensions: Sequence[DimensionResult]) -> Assessability:
        """Overall state, weighted to the weakest evidence.

        Deliberately not a mean of multipliers — averaging is exactly what turns
        missing evidence into a middling score, which reads as "partly assessed"
        when most of it was absent.

        Coverage, not calibration, is the question here. `not_assessable` is
        reserved for the case where *nothing* could be measured, because that is
        the one reading that says "we could not examine this entity" — and an
        entity whose detectors correctly return no adverse signal must never be
        filed there. It is the mirror image of the error this tool exists to
        avoid, and it would hit every well-run entity in the country.

        Anything short of full coverage is `partial`, including a thin
        measurement, which is why this and `sap_tier` may legitimately disagree:
        an entity measured on two dimensions of eight *was* partly assessed, and
        still cannot be placed against cutpoints calibrated on eight. Reporting
        `not_assessable` for that would overstate the blindness; letting it reach
        a tier would overstate the precision. The pair is read together for that
        reason, and `entity_summary` persists this value so the list and the
        scorecard cannot disagree about it.
        """
        measured = sum(1 for d in dimensions if d.score is not None)
        if measured == 0:
            return Assessability.NOT_ASSESSABLE
        if measured == len(dimensions) and all(
            d.assessability is Assessability.ASSESSABLE for d in dimensions
        ):
            return Assessability.ASSESSABLE
        return Assessability.PARTIAL

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
        indicators: Sequence[str] | None = None,
        only_entity_ids: Sequence[str] | None = None,
    ) -> list[EntityScore]:
        """Score every entity in the period, rank them, and persist.

        Ranking is inherently cohort-relative, so it cannot be done one entity at
        a time - the rank of an entity depends on who else is present.

        `only_entity_ids` narrows what is *returned*, never what is ranked or
        persisted. Ranking a subset against itself would produce ranks that are
        not comparable to any other run's, which is the one thing a rank has to
        be; and persisting a subset would leave every unpersisted entity reading
        as `not_assessable` through `GET /entities`, erasing its last real score.
        The full cohort is therefore always scored and stored, and the response is
        filtered. The subset that was asked for is recorded in the ledger so the
        run record says what the caller wanted.
        """
        cohort = self.entity_ids(period_start, period_end)
        if not cohort:
            raise ScoringError(
                f"No submissions overlap {period_start.isoformat()}.."
                f"{period_end.isoformat()}, so there is nothing to score. This is "
                "not the same as 'no entities scored well'."
            )

        scores = [
            self.score_entity(entity_id, period_start, period_end, indicators=indicators)
            for entity_id in cohort
        ]
        self._rank(scores)

        if persist:
            run_id = self._create_run(period_start, period_end, cohort)
            self._persist(run_id, scores)
            for score in scores:
                score.run_id = run_id
            self._ledger_complete(run_id, period_start, period_end, scores, actor=actor)
            if only_entity_ids:
                get_ledger().append(
                    actor=actor,
                    action=LedgerAction.RUN_TRIGGERED,
                    payload={
                        "run_id": run_id,
                        "requested_entities": list(only_entity_ids),
                        "note": (
                            "Response filtered to the requested entities. The full "
                            "cohort was scored and persisted so ranks stay "
                            "comparable across runs."
                        ),
                    },
                )

        if only_entity_ids:
            wanted = set(only_entity_ids)
            unknown = sorted(wanted - set(cohort))
            if unknown:
                raise ScoringError(
                    f"No submissions overlap this period for: {', '.join(unknown)}. "
                    "Known entity ids for this period are on GET /entities."
                )
            return [s for s in scores if s.entity_id in wanted]
        return scores

    # -- API-shaped reads --------------------------------------------------
    # These live here rather than in the routers so that the SQL stays in the
    # service layer and the routers stay a translation of service results into
    # response models. Nothing is computed twice: every number below has already
    # been through the scoring pipeline above.
    def entity_list(
        self,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> list[EntityListItem]:
        """`GET /entities` — every registered entity, scored where it can be.

        Entities with no submission are included with null scores, because the
        executive view has to be able to say "registered, nothing submitted" as
        distinct from "submitted, nothing found". A list that only ever contains
        entities with data cannot express that difference.

        The period is optional. With no period given, the most recent completed
        scoring run is used, and an entity is matched on its **latest** score
        rather than being dropped when an older row exists.
        """
        conn = get_connection()
        run = self._latest_run(period_start, period_end)
        if run is None:
            period = None
            scores: dict[str, sqlite3.Row] = {}
        else:
            period = (str(run["period_start"]), str(run["period_end"]))
            scores = {
                str(row["entity_id"]): row
                for row in conn.execute(
                    "SELECT * FROM entity_score WHERE run_id = ? ORDER BY entity_id",
                    (run["run_id"],),
                ).fetchall()
            }

        rows = conn.execute(
            "SELECT entity_id, name, sector, soc_model, coverage_type, size_tier, "
            "critical_asset_count FROM entity ORDER BY name, entity_id"
        ).fetchall()

        out: list[EntityListItem] = []
        for row in rows:
            entity_id = str(row["entity_id"])
            score = scores.get(entity_id)
            # `sap_tier` is a NOT NULL column, so an unscored entity has to be
            # given the separate `not_assessable` state rather than a numeric tier.
            # Defaulting it to T4 here would file every never-submitted entity as
            # low risk, which is the exact conflation plan §6.3 forbids.
            out.append(
                EntityListItem(
                    entity_id=entity_id,
                    name=str(row["name"]),
                    sector=str(row["sector"]),
                    soc_model=SocModel(str(row["soc_model"])),
                    size_tier=SizeTier(str(row["size_tier"])),
                    coverage_type=CoverageType(str(row["coverage_type"])),
                    critical_asset_count=int(row["critical_asset_count"] or 0),
                    egi=score["egi"] if score else None,
                    nsi=score["nsi"] if score else None,
                    dts=score["dts"] if score else None,
                    sap=score["sap"] if score else None,
                    sap_tier=(
                        AttentionTier(str(score["sap_tier"])) if score
                        else AttentionTier.NOT_ASSESSABLE
                    ),
                    sap_rank_interval=(
                        RankInterval(
                            rank=int(score["sap_rank"]),
                            low=int(score["sap_rank_low"] or score["sap_rank"]),
                            high=int(score["sap_rank_high"] or score["sap_rank"]),
                            method="weight_perturbation_dirichlet+bootstrap_over_peers",
                        )
                        if score and score["sap_rank"] is not None
                        else None
                    ),
                    period_start=period[0] if period else None,
                    period_end=period[1] if period else None,
                    overall_assessability=(
                        # From the persisted row, so the executive list and the
                        # per-entity summary state the same thing about the same
                        # entity. An entity registered but never scored has no
                        # row at all and is reported not-assessable, which is what
                        # "nothing has been submitted" means.
                        Assessability(score["assessability"])
                        if score and score["assessability"]
                        else Assessability.NOT_ASSESSABLE
                    ),
                )
            )
        return out

    def _latest_run(
        self, period_start: date | None, period_end: date | None
    ) -> sqlite3.Row | None:
        """The run whose scores a read should reflect, with its period attached.

        `run` itself has no period columns — the window a run covered lives on the
        score rows it wrote, so it is derived here by aggregating `entity_score`.
        A run that wrote no scores (crashed, or every entity scored as
        not-assessable and nothing persisted) is invisible to this query, which is
        correct: there is nothing in it to show.

        The period filter matches a run that *contains* the requested window, not
        one that equals it. A run over a quarter contains any month inside it, and
        refusing to show a stored score because the request named a narrower window
        would make the endpoint look like it lost data.
        """
        return get_connection().execute(
            "SELECT r.*, MIN(s.period_start) AS period_start, "
            "       MAX(s.period_end) AS period_end "
            "FROM run r JOIN entity_score s ON s.run_id = r.run_id "
            "WHERE r.status = 'complete' "
            "GROUP BY r.run_id "
            "HAVING (?1 IS NULL OR (MIN(s.period_start) <= ?2 AND MAX(s.period_end) >= ?3)) "
            "ORDER BY r.finished_ts DESC, r.run_id DESC LIMIT 1",
            (
                period_start.isoformat() if period_start else None,
                period_end.isoformat() if period_end else "",
                period_start.isoformat() if period_start else "",
            ),
        ).fetchone()

    def entity_summary(
        self, entity_id: str, period_start: date | None = None, period_end: date | None = None
    ) -> EntitySummaryOut:
        """`GET /entities/{id}/summary` — the executive scorecard.

        Scores come from the persisted run rather than being recomputed, so a
        summary never disagrees with the `entity_score` row an auditor can read.
        When no run covers the entity the dimension scores are still produced from
        a live pass, because "never scored" is a gap in the summary, not a reason
        to return an empty object.
        """
        conn = get_connection()
        entity = conn.execute(
            "SELECT entity_id, name, sector, soc_model, coverage_type, size_tier, "
            "critical_asset_count FROM entity WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        if entity is None:
            raise ScoringError(f"No such entity: {entity_id}")

        run = self._latest_run(period_start, period_end)
        score_row = None
        dimensions: list[DimensionScoreOut] = []
        caveats: list[str] = []

        if run is not None:
            score_row = conn.execute(
                "SELECT * FROM entity_score WHERE run_id = ? AND entity_id = ?",
                (run["run_id"], entity_id),
            ).fetchone()
            # One assessability pass, not one per dimension: the report already
            # carries every dimension's state and its missing fields, and calling
            # it eight times would rescan the same evidence tables eight times.
            report = self.assessability.report(entity_id=entity_id)
            by_dim = {d.dimension.value: d for d in report.dimensions}
            for row in conn.execute(
                "SELECT * FROM dimension_score WHERE run_id = ? AND entity_id = ? "
                "ORDER BY dimension",
                (run["run_id"], entity_id),
            ).fetchall():
                dimension = Dimension(str(row["dimension"]))
                dim_report = by_dim.get(dimension.value)
                # Score and interval come from the run — they are what the
                # persisted audit row says. State and missing fields come from the
                # live report, because `dimension_score` stores the state as a
                # 0.0/0.5/1.0 multiplier and has no column for which fields were
                # missing. A run can predate the latest submission, so where the
                # report covers a different window a caveat says so rather than
                # letting the two quietly disagree.
                dimensions.append(
                    DimensionScoreOut(
                        dimension=dimension,
                        score=row["score"],
                        assessability=(
                            dim_report.assessability if dim_report is not None
                            else _assessability_from_multiplier(row["assessability"])
                        ),
                        ci_low=row["ci_low"],
                        ci_high=row["ci_high"],
                        missing_fields=(
                            list(dim_report.missing_fields) if dim_report else []
                        ),
                    )
                )
            if report.period_start and report.period_end and (
                report.period_start != str(run["period_start"])
                or report.period_end != str(run["period_end"])
            ):
                caveats.append(
                    f"Scores are from the run covering {run['period_start']} to "
                    f"{run['period_end']}, but the data currently on file covers "
                    f"{report.period_start} to {report.period_end}. Assessability "
                    "reflects the later window."
                )

        if not dimensions or score_row is None:
            live = self.score_entity(
                entity_id,
                period_start or date.today().replace(day=1),
                period_end or date.today(),
            )
            dimensions = [
                DimensionScoreOut(
                    dimension=d.dimension,
                    score=d.score,
                    assessability=d.assessability,
                    missing_fields=list(d.missing_fields),
                    ci_low=d.ci_low,
                    ci_high=d.ci_high,
                )
                for d in live.dimensions
            ]
            caveats = list(live.caveats)
            if score_row is None:
                caveats.insert(
                    0,
                    "No scoring run covers this entity for the requested period, "
                    "so these figures were computed on demand and are not "
                    "recorded in a run.",
                )
            egi, nsi, dts, sap = live.egi, live.nsi, live.dts, live.sap
            tier = AttentionTier(live.sap_tier)
            rank_interval = live.rank_interval()
            period = (live.period_start, live.period_end)
            overall = live.overall_assessability
            n_not_assessable = live.n_not_assessable_dimensions
            # A finding, in the pre-Phase-10 sense: an indicator that survived
            # correction and is not suppressed. Counted here so the summary is
            # not silently missing a column the UI is contracted to show.
            n_findings = sum(
                1 for s in live.signals if not s.suppressed_reason and s.signal > 0.0
            )
        else:
            egi, nsi, dts, sap = (
                score_row["egi"], score_row["nsi"], score_row["dts"], score_row["sap"]
            )
            tier = AttentionTier(str(score_row["sap_tier"]))
            rank_interval = (
                RankInterval(
                    rank=int(score_row["sap_rank"]),
                    low=int(score_row["sap_rank_low"] or score_row["sap_rank"]),
                    high=int(score_row["sap_rank_high"] or score_row["sap_rank"]),
                    method="weight_perturbation_dirichlet+bootstrap_over_peers",
                )
                if score_row["sap_rank"] is not None
                else None
            )
            period = (date.fromisoformat(str(run["period_start"])),
                      date.fromisoformat(str(run["period_end"])))
            # From the persisted row, so this view and `GET /entities` state the same thing
            # about the same entity. The live report is the fallback for a row
            # written before the column existed; the period caveat above already
            # warns when the two cover different windows. Reconstructing the state
            # from the tier would be wrong in the ordinary case: a `T1` with a
            # partial overall is normal, and reporting that as fully assessable
            # would overstate what the scores rest on.
            overall = (
                Assessability(score_row["assessability"])
                if score_row["assessability"]
                else report.overall
            )
            n_not_assessable = sum(1 for d in dimensions if d.score is None)
            # Counted from the `finding` table for this run. Table exists from
            # Phase 1; Phase 10 fills it, and a low-confidence lead is not a
            # finding (plan §4.8.1) so only the non-lead rows count.
            n_findings = int(
                conn.execute(
                    "SELECT COUNT(*) FROM finding "
                    "WHERE run_id = ? AND entity_id = ? "
                    "AND is_low_confidence_lead = 0",
                    (run["run_id"], entity_id),
                ).fetchone()[0]
            )

        return EntitySummaryOut(
            entity=EntityOut(
                entity_id=entity_id,
                name=str(entity["name"]),
                sector=str(entity["sector"]),
                soc_model=SocModel(str(entity["soc_model"])),
                size_tier=SizeTier(str(entity["size_tier"])),
                coverage_type=CoverageType(str(entity["coverage_type"])),
                critical_asset_count=int(entity["critical_asset_count"] or 0),
            ),
            period_start=period[0].isoformat(),
            period_end=period[1].isoformat(),
            egi=egi,
            nsi=nsi,
            dts=dts,
            dimensions=dimensions,
            sap=sap,
            sap_tier=tier,
            sap_rank_interval=rank_interval,
            n_findings=n_findings,
            n_not_assessable_dimensions=n_not_assessable,
            overall_assessability=overall,
            caveats=caveats,
        )

    def score_snapshot(
        self,
        period_start: date | None = None,
        period_end: date | None = None,
        metric: str = "egi",
    ) -> dict[str, float]:
        """`{entity_id: value}` for one metric across the most recent run.

        Entities whose value is null are **omitted**, not included as zero. A
        cohort built to characterise a distribution must not contain entities that
        have no value for the metric: including them as 0.0 would drag the median
        down and make every real entity look high, and including them as `None`
        would make the median silently drop them while still reporting them in
        `n_peers`.
        """
        if metric not in ("egi", "nsi", "dts", "sap"):
            raise ScoringError(
                f"Unknown metric {metric!r}. One of: egi, nsi, dts, sap."
            )
        run = self._latest_run(period_start, period_end)
        if run is None:
            return {}
        rows = get_connection().execute(
            f"SELECT entity_id, {metric} AS value FROM entity_score "
            "WHERE run_id = ? AND value IS NOT NULL ORDER BY entity_id",
            (run["run_id"],),
        ).fetchall()
        return {str(r["entity_id"]): float(r["value"]) for r in rows}

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunOut]:
        """`GET /runs` — newest first, each with its finding count."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT r.*, (SELECT COUNT(*) FROM finding f WHERE f.run_id = r.run_id) "
            "AS n_findings FROM run r "
            "ORDER BY r.created_ts DESC, r.run_id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [_run_out(row, int(row["n_findings"] or 0)) for row in rows]

    def get_run(self, run_id: str) -> RunOut:
        """`GET /runs/{id}`."""
        row = get_connection().execute(
            "SELECT r.*, (SELECT COUNT(*) FROM finding f WHERE f.run_id = r.run_id) "
            "AS n_findings FROM run r WHERE r.run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ScoringError(f"No such run: {run_id}")
        return _run_out(row, int(row["n_findings"] or 0))

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

        pack_version = self._active_pack_version()

        run_id = f"run_{uuid.uuid4().hex[:16]}"
        with transaction() as conn:
            conn.execute(
                "INSERT INTO run (run_id, created_ts, started_ts, status, "
                " input_manifest_hashes, config_hash, pack_version, policy_hash, "
                " policy_profile_id, code_version, seed) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    "running",
                    manifest_blob,
                    hashlib.sha256(config_blob.encode("utf-8")).hexdigest(),
                    pack_version,
                    self.policy.content_hash,
                    # Stored next to the hash because both belong to lineage: the
                    # hash proves which document, the id says which profile — a
                    # card needs to tell the examiner what to *ask for* and what
                    # to *verify*, which are different things.
                    self.policy.profile_id,
                    _code_version(),
                    self.seed,
                ),
            )
        return run_id

    def _active_pack_version(self) -> str | None:
        """The pack version that governed this run, when one did.

        A promotion makes the pack's policy the active policy. A run executed
        under that policy — detected by the active policy row's content hash
        equalling the policy this service is bound to — is lineage of the pack
        and records the pack's version on the run row, so a finding's run can be
        traced to the pack that produced it. A run under a manually activated
        profile records no pack version.
        """
        active = get_connection().execute(
            "SELECT content_hash FROM policy_profile WHERE active = 1 LIMIT 1"
        ).fetchone()
        if active is None or active["content_hash"] != self.policy.content_hash:
            return None
        pack = get_connection().execute(
            "SELECT version FROM pack WHERE status = 'active' ORDER BY promoted_ts "
            "DESC LIMIT 1"
        ).fetchone()
        return str(pack["version"]) if pack else None

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
                    " assessability, sap_rank, sap_rank_low, sap_rank_high, "
                    " sap_tier, run_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        score.entity_id,
                        score.period_start.isoformat(),
                        score.period_end.isoformat(),
                        score.egi,
                        score.nsi,
                        score.dts,
                        score.sap,
                        # Stored rather than re-derived on read. An entity whose
                        # tier is withheld for measurability must say *why* in the
                        # persisted row, not leave a reader to infer it from a
                        # null SAP -- "scored nothing" and "could not be scored"
                        # are different facts and the table has to hold both.
                        score.overall_assessability.value,
                        score.sap_rank,
                        score.sap_rank_low,
                        score.sap_rank_high,
                        score.sap_tier,
                        run_id,
                    ),
                )
            self._persist_indicator_results(conn, run_id, scores)
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

    def _persist_indicator_results(
        self, conn, run_id: str, scores: Sequence[EntityScore]
    ) -> None:
        """Write `indicator_status`, `finding` and `finding_evidence` for a run.

        Same transaction as the scores, so a run is atomic: either the scores
        and their findings both land, or neither does.

        A finding is a signal that survived FDR, was not suppressed, and is
        positive — exactly the rule `entity_summary` counts with. Everything
        else an indicator said is persisted to `indicator_status` so "why was
        this NOT flagged?" is answered from the record, never by a silent gap.
        """
        catalogue = {
            c["indicator_id"]: c for c in self.rules.catalogue()
        }
        now = datetime.now(timezone.utc).isoformat()
        # Deterministic, so a re-run of the same period under the same inputs
        # writes the same finding ids rather than accumulating duplicates.
        def finding_id(entity_id: str, indicator_id: str) -> str:
            digest = hashlib.sha256(
                f"{run_id}|{entity_id}|{indicator_id}".encode("utf-8")
            ).hexdigest()
            return f"F-{digest[:24]}"

        for score in scores:
            signal_by_id = {s.indicator_id: s for s in score.signals}
            for indicator_id, result in sorted(score.indicator_results.items()):
                spec_entry = catalogue.get(indicator_id, {})
                is_stub = self.rules.is_stub(result)
                signal = signal_by_id.get(indicator_id)
                raised = (
                    signal is not None
                    and signal.suppressed_reason is None
                    and signal.signal > 0.0
                )
                baseline = result.peer_baseline
                self_baseline = result.self_baseline
                conn.execute(
                    "INSERT OR REPLACE INTO indicator_status "
                    "(run_id, entity_id, indicator_id, period_start, period_end, "
                    " value, value_units, effect_size, n, confidence, "
                    " assessability, missing_fields, required_tier, raised, "
                    " suppressed_reason, note, not_computable_reason, is_stub, "
                    " created_ts) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        score.entity_id,
                        indicator_id,
                        result.period.start.isoformat(),
                        result.period.end.isoformat(),
                        result.value,
                        result.value_units,
                        result.effect_size,
                        result.n,
                        result.confidence,
                        result.assessability.multiplier,
                        json.dumps(result.missing_fields),
                        spec_entry.get("min_tier"),
                        1 if raised else 0,
                        signal.suppressed_reason if signal else None,
                        # Measured but no comparison formed (e.g. a single-valued
                        # cohort): the engine's caveat must survive, or the card
                        # would read "ran and stayed quiet" for a reading that was
                        # never compared to anything.
                        (result.notes if result.value is not None
                         and result.effect_size is None else None),
                        None if result.value is not None else result.notes,
                        1 if is_stub else 0,
                        now,
                    ),
                )
                if not raised:
                    continue
                fid = finding_id(score.entity_id, indicator_id)
                conn.execute(
                    "INSERT INTO finding "
                    "(finding_id, run_id, entity_id, indicator_id, "
                    " period_start, period_end, value, value_units, "
                    " peer_median, peer_mad, peer_percentile, n_peers, "
                    " baseline_method, baseline_cohort, self_median, self_mad, "
                    " self_periods, effect_size, n, confidence, "
                    " conf_n_term, conf_assessability_term, conf_data_trust_term, "
                    " evidence_query, benign_explanations, required_fields, "
                    " missing_fields, assessability, family, primary_dimension, "
                    " secondary_dimensions, source, is_low_confidence_lead, "
                    " actor_type_inferred, notes, created_ts) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                    "        ?,?,?,?,?,?,?,?,?,?)",
                    (
                        finding_id(score.entity_id, indicator_id),
                        run_id,
                        score.entity_id,
                        indicator_id,
                        result.period.start.isoformat(),
                        result.period.end.isoformat(),
                        result.value,
                        result.value_units,
                        baseline.median if baseline else None,
                        baseline.mad if baseline else None,
                        baseline.percentile if baseline else None,
                        baseline.n_peers if baseline else None,
                        baseline.method if baseline else None,
                        baseline.cohort if baseline else None,
                        self_baseline.median if self_baseline else None,
                        self_baseline.mad if self_baseline else None,
                        self_baseline.periods if self_baseline else None,
                        result.effect_size,
                        result.n,
                        result.confidence,
                        _term(result.confidence_breakdown, "n_term"),
                        _term(result.confidence_breakdown, "assessability_term"),
                        _term(result.confidence_breakdown, "data_trust_term"),
                        result.evidence_query,
                        json.dumps(list(result.benign_explanations)),
                        json.dumps(list(result.required_fields)),
                        json.dumps(list(result.missing_fields)),
                        result.assessability.multiplier,
                        result.family,
                        result.primary_dimension.value
                        if result.primary_dimension
                        else None,
                        json.dumps(
                            [d.value for d in result.secondary_dimensions]
                        ),
                        result.source.value,
                        1 if result.is_low_confidence_lead else 0,
                        1 if result.actor_type_inferred else 0,
                        result.notes,
                        now,
                    ),
                )
                table = self._evidence_table(result)
                for ordinal, row_id in enumerate(result.evidence_row_ids):
                    conn.execute(
                        "INSERT OR IGNORE INTO finding_evidence "
                        "(finding_id, table_name, row_id, ordinal) "
                        "VALUES (?,?,?,?)",
                        (fid, table, str(row_id), ordinal),
                    )

    @staticmethod
    def _evidence_table(result: IndicatorResult) -> str:
        """The table the finding's evidence row ids live in.

        Taken from the stored query record, not parsed from SQL: the record was
        stamped with the table when the query was assembled, and parsing SQL to
        discover it would be a second opinion about what the query means.
        Without a record the table is unknown, and an unknown table is reported
        as such rather than guessed — a row id with the wrong table name is a
        row id that cannot be found.
        """
        if not result.evidence_query:
            return ""
        try:
            return json.loads(result.evidence_query).get("table") or ""
        except (TypeError, ValueError):
            return ""

    def _ledger_complete(
        self,
        run_id: str,
        period_start: date,
        period_end: date,
        scores: Sequence[EntityScore],
        actor: str = "scoring_service",
    ) -> None:
        """One ledger entry for the scoring pass.

        Counts, not per-entity scores: the ledger records that a decision pass
        happened under a given policy version and seed, and the numbers live in
        the tables where they can be queried and re-derived.

        `actor` is the person who asked for the run, not the component that did
        it. The chain records "a supervisor triggered this under policy X"; a
        hardcoded service name would make every entry look like it came from
        nobody in particular, which is the same unattributability the API's write
        gate refuses to allow.
        """
        get_ledger().append(
            actor=actor,
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


def _assessability_from_multiplier(multiplier: Any) -> Assessability:
    """Recover the readable state from what `dimension_score.assessability` stores.

    The column holds the 1.0 / 0.5 / 0.0 weight the dimension contributed, because
    that is what the SAP numerator needs to be auditable after the fact. Anything
    that is not one of those three is treated as not assessable: an unreadable
    multiplier must not be reported as a healthy dimension.
    """
    if multiplier is None:
        return Assessability.ASSESSABLE
    try:
        value = float(multiplier)
    except (TypeError, ValueError):
        return Assessability.NOT_ASSESSABLE
    if value >= 1.0:
        return Assessability.ASSESSABLE
    if value > 0.0:
        return Assessability.PARTIAL
    return Assessability.NOT_ASSESSABLE


def _run_out(row: sqlite3.Row, n_findings: int = 0) -> RunOut:
    """Shape a `run` row for the API.

    `input_manifest_hashes` is stored as a JSON blob because a run spans many
    submissions; it is unpacked here so the client gets a list rather than a
    string it has to parse. An unparseable value yields an empty list and no
    exception — a manifest that cannot be read should not hide the run.
    """
    raw = row["input_manifest_hashes"] or "[]"
    try:
        hashes = [str(h) for h in json.loads(raw)]
    except (TypeError, ValueError):
        hashes = []
    return RunOut(
        run_id=str(row["run_id"]),
        created_ts=str(row["created_ts"]),
        started_ts=row["started_ts"],
        finished_ts=row["finished_ts"],
        status=str(row["status"]),
        input_manifest_hashes=hashes,
        config_hash=row["config_hash"],
        pack_version=row["pack_version"],
        policy_hash=row["policy_hash"],
        code_version=row["code_version"],
        seed=row["seed"],
        output_hash=row["output_hash"],
        error=row["error"],
        n_findings=n_findings,
    )


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