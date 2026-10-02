"""Review packs: selection for human review, and the supervisor's own record.

Everything in this module is registered in the OpenAPI contract; the five pack
and verdict routes are live since Phase 11 (2.11). `GET /review-packs/{id}/export`
stays 503 until Phase 13 (2.13).

One naming note that the route descriptions carry deliberately: `POST /verdicts`
records **the supervisor's own conclusion**, written by a human. It is not Orion
issuing a verdict, and no Orion-derived payload anywhere in this API contains a
verdict, a compliance grade or a pass/fail. The distinction is the product.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from app.api.v1.deps import ActorDep, WriterDep
from app.api.v1.errors import NotImplementedResponse, pending, pending_meta
from app.schemas.common import Page
from app.schemas.review_pack import (
    ReviewPackGenerateIn,
    ReviewPackListItem,
    ReviewPackOut,
    VerdictIn,
    VerdictOut,
)
from app.services.review_pack_service import (
    PackNotFound,
    get_review_pack_service,
)

router = APIRouter(tags=["review packs"])

PENDING = {"model": NotImplementedResponse, "description": "Not built yet"}


@router.get(
    "/review-packs",
    response_model=Page[ReviewPackListItem],
    summary="Review packs, newest first",
    description=(
        "A pack is a *sample* chosen for human review — it must keep "
        "P(evidence | in pack) equal to P(evidence | population), which is why "
        "selection is stratified and randomised rather than ranked by score. A "
        "pack built by taking the highest-scoring entities would make every "
        "examiner conclusion about it unusable as evidence about the "
        "population. Each row carries the HT prevalence estimate its pack "
        "produced, so the shelf of packs is comparable at a glance."
    ),
)
def list_packs(
    _actor: ActorDep = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ReviewPackListItem]:
    service = get_review_pack_service()
    items = service.list(limit=limit, offset=offset)
    total = service.count()
    return Page[ReviewPackListItem](
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + limit < total,
    )


@router.post(
    "/review-packs",
    response_model=ReviewPackOut,
    summary="Create a review pack. Ledgered.",
    description=(
        "Ledgered because the pack is the sampling decision, and a sample that "
        "cannot be shown to have been drawn without reference to the scores is "
        "the one artefact that would make every conclusion drawn from it "
        "contestable. The request records the selection rule (targets, "
        "controls, strata, seed); the response carries every item with its "
        "inclusion probability π, its risk score, and prose saying why it was "
        "selected. Without a period the most recent completed scoring run's "
        "window is used, so a bare request cannot silently pick a different "
        "quarter than the frontend is showing."
    ),
    responses={
        400: {"description": "No population in the window, or an unknown entity"},
    },
)
def create_pack(body: ReviewPackGenerateIn, writer: WriterDep) -> ReviewPackOut:
    return get_review_pack_service().generate(body, writer.name)


@router.get(
    "/review-packs/{pack_id}",
    response_model=ReviewPackOut,
    summary="One pack with its stratum, controls and hypotheses",
    description=(
        "Carries the stratum, the selection probabilities (π), the controls "
        "and the hypotheses, so that `fair_selection` — P(evidence | in pack) "
        "= P(evidence | population) — is checkable from the response rather "
        "than asserted by the tool that built the pack. Each control item "
        "states its stratum and draw counts; each targeted item states its "
        "rank by risk and why the risk score is what it is."
    ),
    responses={404: {"description": "No such pack"}},
)
def get_pack(pack_id: str, _actor: ActorDep = None) -> ReviewPackOut:
    try:
        return get_review_pack_service().get(pack_id)
    except PackNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get(
    "/review-packs/{pack_id}/export",
    response_model=NotImplementedResponse,
    summary="Export a pack as a document",
    description=(
        "503 until Phase 13 (2.13). The export is the artefact a supervisor "
        "leaves the building with, so it carries the same evidence rows and "
        "caveats as the screen rather than a summary of them."
    ),
    responses={503: PENDING},
    openapi_extra=pending_meta("report_generator", "Phase 13 (2.13)"),
)
def export_pack(
    pack_id: str,
    _actor: ActorDep = None,
    fmt: Annotated[str, Query(description="pdf, docx, md or json")] = "pdf",
) -> Any:
    return pending(
        "GET /api/v1/review-packs/{id}/export", "report_generator", "Phase 13 (2.13)"
    )


@router.post(
    "/verdicts",
    response_model=VerdictOut,
    summary="Record a supervisor's own conclusion about a finding",
    description=(
        "The supervisor's judgement, entered by a person and attributed to "
        "them. Orion proposes hypotheses with evidence; it does not decide, "
        "and this route is where the human decision is recorded. The verdict "
        "is ledgered with `verdict_recorded`; a recorded conclusion is never "
        "edited or withdrawn, only superseded by a later one. `evidence_seen` "
        "lists the row ids the examiner actually checked."
    ),
    responses={
        400: {"description": "The case is not an item of the pack"},
        404: {"description": "No such pack"},
    },
)
def record_verdict(body: VerdictIn, writer: WriterDep) -> VerdictOut:
    return get_review_pack_service().record_verdict(body, writer.name)


@router.get(
    "/verdicts",
    response_model=Page[VerdictOut],
    summary="Recorded supervisor conclusions",
    description=(
        "Read-only by design — a recorded conclusion is never edited or "
        "withdrawn, only superseded by a later one. Each row is attributed to "
        "the supervisor who wrote it, and that attribution is part of the "
        "record rather than an audit detail. Filter by `pack_id` and/or "
        "`case_id`."
    ),
)
def list_verdicts(
    _actor: ActorDep = None,
    pack_id: Annotated[str | None, Query(description="Limit to one pack")] = None,
    case_id: Annotated[str | None, Query(description="Limit to one case")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[VerdictOut]:
    service = get_review_pack_service()
    items = service.list_verdicts(pack_id=pack_id, case_id=case_id)
    return Page[VerdictOut](
        items=items[offset : offset + limit],
        total=len(items),
        limit=limit,
        offset=offset,
        has_more=offset + limit < len(items),
    )