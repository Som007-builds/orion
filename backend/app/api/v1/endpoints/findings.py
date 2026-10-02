"""Finding cards and their evidence.

Read-only: findings are materialised by a scoring run, never by a request.
Every route re-executes the stored evidence query rather than showing a
snapshot, so the rows on screen are the rows the indicator saw.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.v1.deps import ActorDep, PeriodDep
from app.schemas.common import Page
from app.schemas.finding import (
    CounterfactualOut,
    EvidenceOut,
    FindingCardOut,
    FindingListItem,
)
from app.services.evidence_service import FindingNotFound, get_evidence_service

router = APIRouter(tags=["findings"])


@router.get(
    "/findings",
    response_model=Page[FindingListItem],
    summary="Surviving findings, most adverse first",
    description=(
        "Findings from completed scoring runs, ordered by effect size (most "
        "adverse first) then period. Low-confidence leads are excluded: a lead "
        "is a candidate that lacks the evidence or attribution to be presented "
        "as a finding, and this list only shows findings.\n\n"
        "Filter by `entity_id` for one entity, and/or give a period window; "
        "without a window every period with findings is included."
    ),
)
def list_findings(
    entity_id: Annotated[
        str | None, Query(description="Limit to one entity")
    ] = None,
    period: PeriodDep = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    _actor: ActorDep = None,
) -> Page[FindingListItem]:
    items = get_evidence_service().list_findings(
        entity_id=entity_id,
        period_start=period.start if period else None,
        period_end=period.end if period else None,
    )
    return Page[FindingListItem](
        items=items[offset : offset + limit],
        total=len(items),
        limit=limit,
        offset=offset,
        has_more=offset + limit < len(items),
    )


@router.get(
    "/findings/{finding_id}",
    response_model=FindingCardOut,
    summary="One finding card",
    description=(
        "A card is not a score: it is one surviving indicator with the rows "
        "behind it, the other indicators that ran and stayed quiet (and the ones "
        "that could not run at all), what would clear the finding, and the "
        "lineage of the run that produced it. The shape is frozen in "
        "`app/schemas/finding.py`."
    ),
    responses={404: {"description": "No such finding"}},
)
def get_finding(finding_id: str, _actor: ActorDep = None) -> FindingCardOut:
    try:
        return get_evidence_service().get_finding(finding_id)
    except FindingNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get(
    "/findings/{finding_id}/evidence",
    response_model=EvidenceOut,
    summary="The rows behind a finding, re-derived",
    description=(
        "Re-runs the stored `evidence_query`, so the rows on screen are the "
        "rows the indicator saw at run time, not a snapshot that can drift from "
        "them. `row_id_hash` fingerprints the ordered row ids; "
        "`scripts/reproduce_finding.py` asserts the same ids against the rows "
        "persisted at materialisation."
    ),
    responses={404: {"description": "No such finding"}},
)
def get_finding_evidence(finding_id: str, _actor: ActorDep = None) -> EvidenceOut:
    try:
        return get_evidence_service().get_evidence(finding_id)
    except FindingNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get(
    "/findings/{finding_id}/counterfactual",
    response_model=CounterfactualOut,
    summary="What would clear or raise this finding",
    description=(
        "The counterfactual is the value or field change that would clear the "
        "finding, computed from the run's own FDR bound and the stored peer "
        "baseline. `peer_sensitivity` is deliberately empty: peer-count effects "
        "need the peer values the baseline was built from, which are not "
        "stored, so it is omitted rather than approximated."
    ),
    responses={404: {"description": "No such finding"}},
)
def get_counterfactual(
    finding_id: str, _actor: ActorDep = None
) -> CounterfactualOut:
    try:
        return get_evidence_service().get_counterfactual(finding_id)
    except FindingNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc