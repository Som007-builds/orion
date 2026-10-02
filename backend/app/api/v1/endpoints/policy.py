"""Policy profiles and cohort benchmarks.

`POST /policy-profiles/{id}/activate` is the most consequential write in the
system. Activating a profile retroactively changes what every previously computed
score *meant* — the same SAP number was a T2 under the old cutpoints and is a T1
under the new ones. That is why it is gated to Supervisor and Administrator, and
why the response names the profile it replaced: an operator needs to be able to
answer "which interpretation was in force when this number was produced", and the
stored scores do not record that on their own.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.v1.deps import PeriodDep, PolicyAdminDep
from app.schemas.entity import BenchmarkOut
from app.schemas.ledger import PolicyActivateIn, PolicyActivateOut, PolicyProfileOut
from app.services.policy_profile import PolicyProfileService
from app.services.scoring_service import get_scoring_service

log = logging.getLogger("orion.api")

router = APIRouter(tags=["policy"])


@router.get(
    "/policy-profiles",
    response_model=list[PolicyProfileOut],
    summary="Every known policy profile, newest first",
    description=(
        "A profile is the versioned definition of what counts as a gap: dimension "
        "weights, severity ramps, tier cutpoints, family caps. Scores are only "
        "reproducible against a named profile, which is why they are listed "
        "rather than buried."
    ),
)
def list_profiles() -> list[PolicyProfileOut]:
    rows = PolicyProfileService().list_profiles()
    return [PolicyProfileOut(**row) for row in rows]


@router.get(
    "/policy-profiles/active",
    response_model=PolicyProfileOut,
    summary="The profile new runs are computed under",
    description=(
        "The interpretation in force *now*, which is not necessarily the one a "
        "stored score was computed under — each run keeps its own `policy_hash`. "
        "When explaining a number from an earlier run, resolve the profile by that "
        "hash, not by calling this.\n\n"
        "`summary` carries the derivation itself (`dimension_weights`, "
        "`tier_cutpoints`, `fdr_q`, `dts_floor`, `min_measurable_share`, `ramps`) "
        "so the UI can show how a tier was reached instead of restating the "
        "conclusion."
    ),
)
def active_profile() -> PolicyProfileOut:
    profile = PolicyProfileService().active()
    return PolicyProfileOut(
        profile_id=profile.profile_id,
        version=profile.version,
        # The dataclass holds a `date`; the schema holds a string, matching what
        # the registry row returns, so both routes report an effective date in
        # one format rather than two.
        effective_from=profile.effective_from.isoformat(),
        content_hash=profile.content_hash,
        signature=profile.signature,
        active=True,
        previous_id=None,
        summary={
            "dimension_weights": profile.dimension_weights,
            "tier_cutpoints": profile.tier_cutpoints,
            # Properties, not methods. Published whole rather than trimmed so the
            # frontend can render the same derivation the scorer used instead of
            # re-deriving it from thresholds.
            "fdr_q": profile.fdr_q,
            "dts_floor": profile.dts_floor,
            "min_measurable_share": profile.min_measurable_share,
            "ramps": profile.ramps,
        },
    )


@router.post(
    "/policy-profiles/{profile_id}/activate",
    response_model=PolicyActivateOut,
    summary="Make a profile the one new runs are computed under. Ledgered.",
    description=(
        "Does not rewrite history. Scores already stored keep the policy hash they "
        "were computed under, so a stored figure is always interpretable against "
        "the profile that produced it. What changes is what the *next* run means, "
        "which is why `previous_id` comes back in the response."
    ),
    responses={
        403: {"description": "Role may not activate a policy profile"},
        404: {"description": "No such profile"},
    },
)
def activate_profile(
    profile_id: str, body: PolicyActivateIn, admin: PolicyAdminDep
) -> PolicyActivateOut:
    try:
        profile, entry_hash, previous_id = PolicyProfileService().activate(
            profile_id, admin.name, notes=body.notes
        )
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No such policy profile: {profile_id}. Available profiles are on "
            "GET /policy-profiles.",
        ) from exc

    return PolicyActivateOut(
        profile_id=profile.profile_id,
        version=profile.version,
        activated=True,
        previous_id=previous_id,
        ledger_entry_hash=entry_hash,
    )


@router.get(
    "/benchmarks",
    response_model=list[BenchmarkOut],
    summary="Cohort baselines for the executive view",
    description=(
        "Peer distributions, never thresholds. A median and a MAD describe a "
        "population; they do not say what a population *should* look like, and "
        "nothing here is a target. With no period given, the most recent scoring "
        "run is used."
    ),
)
def benchmarks(
    period: PeriodDep,
    metric: Annotated[str, Query(description="egi, nsi, dts or sap")] = "egi",
    exclude: Annotated[
        str | None,
        Query(description="Entity to exclude, so it is not compared against itself"),
    ] = None,
) -> list[BenchmarkOut]:
    from app.services.baseline_service import get_baseline_service

    if metric not in ("egi", "nsi", "dts", "sap"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown metric '{metric}'. One of: egi, nsi, dts, sap.",
        )

    values = get_scoring_service().score_snapshot(period.start, period.end, metric)
    if not values:
        # An empty list rather than a 404: the metric is real, there is simply
        # nothing scored to describe yet, and the UI shows "no run yet" for an
        # empty array without treating it as an error.
        return []

    result = get_baseline_service().benchmark(metric, values, exclude=exclude)
    # A cohort with too few peers for a robust scale is a real condition the
    # operator needs to see, so it is refused rather than silently replaced by
    # the covariate model — the flag says which happened.
    if result.get("fell_back_to_covariate_model"):
        log.info(
            "benchmark for %s fell back to the covariate model (n=%s)",
            metric,
            result.get("n_peers"),
        )
    return [BenchmarkOut(**result)]
