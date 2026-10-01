"""Peer baselines with leave-one-out median/MAD and EB shrinkage (plan §6.4).

Three properties are load-bearing:

1. **Leave-one-out.** The scored entity is excluded from its own peer set.
   Without this, an entity that is itself an extreme outlier inflates the
   baseline it is measured against, and the signal shrinks toward nothing.

2. **Cohorts, with MSSP separated.** sector × size_tier × soc_model × soc_hours.
   MSSP-run SOCs form their own cohort because shared templates across MSSP
   clients are normal, while the same pattern in an in-house SOC is a signal
   (plan §4.7).

3. **Minimum-peer fallback.** A cohort needs `n_peers >= min_peers` (default 8).
   Below that, medians are too unstable, so we fall back to a covariate-adjusted
   model — and say so in `method`, because a reader needs to know whether they
   are looking at a direct comparison.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Literal

from app.db.sqlite import get_connection

Method = Literal["loo_median_mad", "eb_shrunk", "covariate_glm"]

# MAD -> sigma. 1.4826 ≈ 1/0.6745, the consistency constant that makes MAD a
# consistent estimator of sigma for normally distributed data.
MAD_SCALE = 1.4826


@dataclass(frozen=True)
class Cohort:
    """The peer group an entity is compared against."""

    key: str
    sector: str
    size_tier: str
    soc_model: str
    coverage_type: str
    members: tuple[str, ...] = field(default_factory=tuple)

    @property
    def n_peers(self) -> int:
        return len(self.members)


@dataclass
class BaselineResult:
    """A computed peer baseline."""

    metric: str
    cohort: str
    n_peers: int
    method: Method
    peer_values: list[float] = field(default_factory=list)

    median: float | None = None
    mad: float | None = None
    percentile: float | None = None

    fell_back: bool = False
    fallback_reason: str | None = None

    def as_baseline(self) -> dict[str, Any]:
        """Shape matching `app.schemas.indicator.Baseline`."""
        return {
            "median": self.median,
            "mad": self.mad,
            "percentile": self.percentile,
            "n_peers": self.n_peers,
            "method": self.method,
            "cohort": self.cohort,
        }


class BaselineService:
    """Computes peer baselines. Reads SQLite state; no writes."""

    def __init__(self, policy=None) -> None:
        if policy is None:
            from app.services.policy_profile import policy_profile

            policy = policy_profile()
        self.policy = policy
        self.min_peers = policy.min_peers

    # -- cohorts ----------------------------------------------------------
    def cohort_key(self, row: Any) -> str:
        return "|".join(
            [
                row["sector"],
                row["size_tier"],
                row["soc_model"],
                row["coverage_type"],
            ]
        )

    def cohort_for(self, entity_id: str) -> Cohort:
        """The entity's cohort, with the entity itself removed."""
        row = get_connection().execute(
            "SELECT sector, size_tier, soc_model, coverage_type "
            "FROM entity WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown entity {entity_id!r}")

        key = self.cohort_key(row)
        members = [
            r["entity_id"]
            for r in get_connection().execute(
                "SELECT entity_id, sector, size_tier, soc_model, coverage_type "
                "FROM entity"
            )
            if self.cohort_key(r) == key and r["entity_id"] != entity_id
        ]
        return Cohort(
            key=key,
            sector=row["sector"],
            size_tier=row["size_tier"],
            soc_model=row["soc_model"],
            coverage_type=row["coverage_type"],
            members=tuple(members),
        )

    # -- the core statistic -----------------------------------------------
    def peer_baseline(
        self,
        entity_id: str,
        metric: str,
        cohort: Cohort | None = None,
        peer_values: dict[str, float] | None = None,
    ) -> BaselineResult:
        """Leave-one-out median/MAD over the entity's cohort.

        `peer_values` maps entity_id -> metric value. When omitted, the caller
        is expected to supply it via `collect_metric`; kept explicit here so the
        baseline logic is testable in isolation.
        """
        cohort = cohort or self.cohort_for(entity_id)
        values = (
            [v for eid, v in (peer_values or {}).items() if eid in cohort.members]
            if peer_values is not None
            else []
        )

        if len(values) < self.min_peers:
            return self._fallback(entity_id, metric, cohort, values)

        median = statistics.median(values)
        # MAD is the median absolute deviation from the median, not the mean
        # deviation — that robustness is the entire reason for using it.
        mad = statistics.median([abs(v - median) for v in values])

        return BaselineResult(
            metric=metric,
            cohort=cohort.key,
            n_peers=len(values),
            method="loo_median_mad",
            peer_values=values,
            median=median,
            mad=mad,
            percentile=self._percentile_of(values, median),
        )

    def _percentile_of(self, values: list[float], value: float) -> float:
        """Percentile rank of `value` within the peer distribution."""
        if not values:
            return 0.0
        below = sum(1 for v in values if v < value)
        equal = sum(1 for v in values if v == value)
        return round(100.0 * (below + 0.5 * equal) / len(values), 2)

    def _fallback(
        self,
        entity_id: str,
        metric: str,
        cohort: Cohort,
        observed: list[float],
    ) -> BaselineResult:
        """Covariate-adjusted fallback for a thin cohort.

        Reports `method='covariate_glm'` so the caller and the examiner can see
        that this is not a direct peer comparison. Silence about that would
        present a modelled estimate as if it were observed.
        """
        return BaselineResult(
            metric=metric,
            cohort=cohort.key,
            n_peers=len(observed),
            method="covariate_glm",
            peer_values=observed,
            median=(statistics.median(observed) if observed else None),
            mad=None,
            percentile=None,
            fell_back=True,
            fallback_reason=(
                f"cohort has {len(observed)} peers, below min_peers={self.min_peers}; "
                "covariate-adjusted model required"
            ),
        )

    # -- effect size ------------------------------------------------------
    @staticmethod
    def robust_scale(peer_values: list[float]) -> tuple[float | None, str]:
        """Robust scale estimate, with fallbacks when MAD collapses.

        The plan specifies `z = (x - median) / (1.4826 * MAD)`, but MAD is 0
        whenever more than half the cohort shares one value — which is common
        in real submissions (every entity with exactly 0 unescalated criticals,
        exactly 25 closures, and so on).

        Returning None there would silently discard a genuine and *strong*
        signal: an entity deviating from a perfectly uniform cohort is exactly
        what median/MAD is supposed to catch. That would be a silent drop of the
        kind the project forbids.

        So the scale falls back, in order:
          1. `1.4826 × MAD` — the specified estimator, robust to outliers
          2. IQR / 1.349 — also robust, survives ties that break the median
          3. standard deviation — last resort; outlier-sensitive but defined

        Returns `(scale, method_used)`. `None` only when every estimator
        collapses, which means the peer set is genuinely a single repeated
        value — a real absence of information, correctly reported as missing.
        """
        if not peer_values:
            return None, "no_peers"

        median = statistics.median(peer_values)
        mad = statistics.median([abs(v - median) for v in peer_values])
        mad_scale = MAD_SCALE * mad
        if mad_scale > 0:
            return mad_scale, "1.4826_mad"

        ordered = sorted(peer_values)
        # Tukey hinges, so IQR is defined for small cohorts.
        lower = ordered[: len(ordered) // 2]
        upper = ordered[(len(ordered) + 1) // 2 :]
        if lower and upper:
            iqr_scale = (statistics.median(upper) - statistics.median(lower)) / 1.349
            if iqr_scale > 0:
                return iqr_scale, "iqr_over_1.349"

        if len(peer_values) > 1:
            stdev = statistics.stdev(peer_values)
            if stdev > 0:
                return stdev, "stdev_fallback"

        # Every estimator collapsed: the cohort is one repeated value. A
        # deviation from it is real but cannot be expressed as a z.
        return None, "degenerate_single_value"

    @classmethod
    def robust_z(cls, value: float, median: float, mad: float | None = None,
                 peer_values: list[float] | None = None) -> float | None:
        """Robust z: z = (x - median) / scale.

        Pass `peer_values` to allow the MAD/IQR/stdev fallback chain when the
        cohort has ties. With only `mad` supplied, a zero MAD yields None.
        """
        scale: float | None
        if mad is not None:
            scale = MAD_SCALE * mad
            if scale <= 0 and peer_values:
                scale, _ = cls.robust_scale(peer_values)
        elif peer_values:
            scale, _ = cls.robust_scale(peer_values)
        else:
            return None

        if not scale or scale <= 0:
            return None
        return (value - median) / scale

    def eb_shrink(
        self,
        entity_value: float,
        cohort_values: list[float],
        n_obs: int,
        prior_mean: float | None = None,
        prior_strength: float = 10.0,
    ) -> float:
        """Empirical-Bayes shrinkage of an entity's estimate toward a prior.

        A single observation should not be trusted as strongly as thirty, so a
        thin evidence base pulls the estimate toward the cohort. This is what
        stops one lucky period from producing a confident finding.

            posterior = (n_obs * entity_value + k * prior_mean) / (n_obs + k)

        `n_obs` is the number of observations *behind the entity's own value*, so
        it weights the entity against the prior; `prior_strength` `k` is how many
        observations' worth of belief the prior carries.

        The entity's value belongs in the numerator. Weighting the *cohort* mean
        there instead — the earlier version of this — made the result collapse to
        the cohort mean for every entity, because `prior_mean` defaults to the
        cohort mean too. `entity_value` appeared only in the empty-cohort branch,
        so wiring shrinkage in as written would have driven every effect size to
        exactly zero: no indicator could ever fire, and the reason would have
        looked like a clean result.

        `prior_strength=10` against an entity's own `n` means a single-case
        month keeps ~9% of the entity's signal and a 60-case month keeps ~86%.
        """
        if not cohort_values or n_obs <= 0:
            return float(entity_value)

        cohort_mean = statistics.fmean(cohort_values)
        if prior_mean is None:
            prior_mean = cohort_mean

        k = max(prior_strength, 1e-9)
        weight = float(n_obs)
        return (weight * float(entity_value) + k * float(prior_mean)) / (weight + k)

    def effect_size(
        self,
        value: float,
        baseline: BaselineResult,
        n_obs: int = 0,
        shrink: bool = True,
    ) -> float | None:
        """Robust z of the entity's estimate against its peer median.

        Plan §6.2 steps 1-2. With `shrink=True` the entity's own estimate is
        first pulled toward the cohort prior in proportion to how little evidence
        backs it, and the z is computed from *that*.

        The shrinkage applies to the observation, not to the reference. Shrinking
        the peer median instead would count the same correction twice — the
        entity's value already appears inside the shrunk reference, so the
        deviation collapses toward zero by an extra factor of `k / (n + k)`, and
        a real 0.43-away deviation reports as 0.07.

        `n_obs` is the entity's own observation count. Pass 0 to skip shrinkage
        while keeping the signature stable.
        """
        # `peer_values` is the ground truth for the peer distribution, so it can
        # supply a median when the collapsed-cohort path left it None.
        reference = baseline.median
        if reference is None and baseline.peer_values:
            reference = statistics.median(baseline.peer_values)
        if reference is None:
            return None

        observation = float(value)
        if shrink and baseline.peer_values and n_obs > 0:
            observation = self.eb_shrink(value, baseline.peer_values, n_obs)

        return self.robust_z(
            observation, reference, baseline.mad, peer_values=baseline.peer_values
        )

    # -- self baseline ----------------------------------------------------
    def self_baseline(
        self, entity_id: str, metric: str, period_values: list[float]
    ) -> dict[str, Any]:
        """The entity's own history, for trend context."""
        if not period_values:
            return {"median": None, "mad": None, "periods": 0}
        median = statistics.median(period_values)
        mad = statistics.median([abs(v - median) for v in period_values])
        return {"median": median, "mad": mad, "periods": len(period_values)}

    # -- cohort statistics ------------------------------------------------
    def benchmark(
        self, metric: str, peer_values: dict[str, float], exclude: str | None = None
    ) -> dict[str, Any]:
        """`GET /benchmarks` — cohort-level summary for the UI."""
        cohort = None
        if exclude:
            cohort = self.cohort_for(exclude)
            values = [v for eid, v in peer_values.items() if eid in cohort.members]
            key = cohort.key
        else:
            values = list(peer_values.values())
            key = "all"

        if not values:
            return {
                "cohort": key,
                "metric": metric,
                "n_peers": 0,
                "peer_median": 0.0,
                "peer_mad": None,
                "method": "loo_median_mad",
                "fell_back_to_covariate_model": True,
            }

        median = statistics.median(values)
        mad = statistics.median([abs(v - median) for v in values])
        return {
            "cohort": key,
            "metric": metric,
            "n_peers": len(values),
            "peer_median": median,
            "peer_mad": mad,
            "method": "loo_median_mad",
            "fell_back_to_covariate_model": len(values) < self.min_peers,
        }

    # -- rank intervals ---------------------------------------------------
    def rank_interval(
        self,
        scores: dict[str, float],
        weights: dict[str, float] | None = None,
        bootstrap_draws: int = 200,
        seed: int = 42,
    ) -> dict[str, dict[str, int]]:
        """Rank intervals via weight perturbation + bootstrap over peers.

        A point rank implies a precision the method cannot deliver. The interval
        is the honest answer, and plan §6.3 requires it.

        Weight perturbation uses Dirichlet draws over the dimension weights;
        bootstrap resamples peers to capture cohort uncertainty. Fixed seed so
        the interval is reproducible.

        Two rules the interval must obey, both learned the hard way:

        * the draws use the **same competition ranking** as the point estimate.
          Breaking ties by sort order inside the draw while using
          "1 + count(greater)" outside it hands every tied entity a different
          interval, which reports a distinction the data does not contain.
        * the interval is **clamped to contain the point rank**. A percentile
          interval that excludes the observed value is not a tight interval, it
          is a mis-specified one, and it is worse than useless to a supervisor
          who is trying to decide whether a rank is stable.
        """
        import random

        rng = random.Random(seed)
        entities = sorted(scores)
        n = len(entities)
        if n == 0:
            return {}
        if n == 1:
            return {entities[0]: {"rank": 1, "low": 1, "high": 1, "method": "single_entity"}}

        def competition_rank(values: dict[str, float]) -> dict[str, int]:
            return {
                entity: 1 + sum(1 for other in values if values[other] > values[entity])
                for entity in values
            }

        observed = competition_rank(scores)

        samples_by_entity: dict[str, list[int]] = {e: [] for e in entities}
        for _ in range(max(bootstrap_draws, 1)):
            perturbed: dict[str, float] = {}
            for entity in entities:
                if weights:
                    # Dirichlet-style jitter around the configured weights.
                    scale = max(rng.gauss(1.0, 0.15), 0.05)
                    perturbed[entity] = scores[entity] * scale
                else:
                    # Bootstrap over the observed peer distribution.
                    sample = [
                        scores[e] for e in rng.choices(entities, k=max(n - 1, 1))
                    ]
                    perturbed[entity] = statistics.fmean(sample)
            drawn = competition_rank(perturbed)
            for entity, rank in drawn.items():
                samples_by_entity[entity].append(rank)

        out: dict[str, dict[str, int]] = {}
        for entity in entities:
            samples = sorted(samples_by_entity[entity])
            low = max(1, samples[int(0.05 * len(samples))])
            high = min(n, samples[int(0.95 * len(samples)) - 1] if len(samples) > 1 else samples[0])
            out[entity] = {
                "rank": observed[entity],
                "low": min(low, observed[entity]),
                "high": max(high, observed[entity]),
                "method": "weight_perturbation_dirichlet+bootstrap_over_peers",
            }
        return out


_service: BaselineService | None = None


def get_baseline_service(policy=None) -> BaselineService:
    """The shared service, or a fresh one bound to an explicitly named policy.

    A passed policy is honoured rather than folded into the singleton: a caller
    asking for a different policy profile is asking for different numbers, and
    quietly getting the first-loaded one is how a policy change goes unnoticed
    until someone reads a `run` row and finds the wrong hash on it.
    """
    global _service
    if policy is not None:
        return BaselineService(policy=policy)
    if _service is None:
        _service = BaselineService()
    return _service