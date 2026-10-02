"""Review packs: selection for human review, and the supervisor's own record.

Everything in this module is registered in the OpenAPI contract but returns 503
until Phase 11 (2.11). The routes exist so the frontend can be built against the
final shape now.

One naming note that the route descriptions carry deliberately: `POST /verdicts`
records **the supervisor's own conclusion**, written by a human. It is not Orion
issuing a verdict, and no Orion-derived payload anywhere in this API contains a
verdict, a compliance grade or a pass/fail. The distinction is the product.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.api.v1.deps import ActorDep, WriterDep
from app.api.v1.errors import NotImplementedResponse, pending, pending_meta
from app.schemas.review_pack import (
    ReviewPackGenerateIn,
    VerdictIn,
)

router = APIRouter(tags=["review packs"])

PENDING = {"model": NotImplementedResponse, "description": "Not built yet"}

PHASE = "Phase 11 (2.11)"


@router.get(
    "/review-packs",
    response_model=NotImplementedResponse,
    summary="Review packs, newest first",
    description=(
        "In the frozen contract; 503 until Phase 11. A pack is a *sample* chosen "
        "for human review — it must keep P(evidence | in pack) equal to "
        "P(evidence | population), which is why selection is stratified and "
        "randomised rather than ranked by score. A pack built by taking the "
        "highest-scoring entities would make every examiner conclusion about it "
        "unusable as evidence about the population."
    ),
    responses={503: PENDING},
    openapi_extra=pending_meta("review_pack", PHASE),
)
def list_packs(actor: ActorDep) -> Any:
    return pending("GET /api/v1/review-packs", "review_pack", PHASE)


@router.post(
    "/review-packs",
    response_model=NotImplementedResponse,
    summary="Create a review pack. Ledgered.",
    description=(
        "In the frozen contract; 503 until Phase 11. Ledgered because the pack is "
        "the sampling decision, and a sample that cannot be shown to have been "
        "drawn without reference to the scores is the one artefact that would "
        "make every conclusion drawn from it contestable."
    ),
    responses={503: PENDING},
    openapi_extra=pending_meta("review_pack", PHASE),
)
def create_pack(body: ReviewPackGenerateIn, writer: WriterDep) -> Any:
    return pending("POST /api/v1/review-packs", "review_pack", PHASE)


@router.get(
    "/review-packs/{pack_id}",
    response_model=NotImplementedResponse,
    summary="One pack with its stratum, controls and hypotheses",
    description=(
        "In the frozen contract; 503 until Phase 11. Carries the stratum, the "
        "selection probabilities (π), the controls and the hypotheses, so that "
        "`fair_selection` — P(evidence | in pack) = P(evidence | population) — is "
        "checkable from the response rather than asserted by the tool that built "
        "the pack."
    ),
    responses={503: PENDING},
    openapi_extra=pending_meta("review_pack", PHASE),
)
def get_pack(pack_id: str, actor: ActorDep) -> Any:
    return pending("GET /api/v1/review-packs/{id}", "review_pack", PHASE)


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
    actor: ActorDep,
    fmt: Annotated[str, Query(description="pdf, docx, md or json")] = "pdf",
) -> Any:
    return pending(
        "GET /api/v1/review-packs/{id}/export", "report_generator", "Phase 13 (2.13)"
    )


@router.post(
    "/verdicts",
    response_model=NotImplementedResponse,
    summary="Record a supervisor's own conclusion about a finding",
    description=(
        "The supervisor's judgement, entered by a person and attributed to them. "
        "Orion proposes hypotheses with evidence; it does not decide, and this "
        "route is where the human decision is recorded. 503 until Phase 11."
    ),
    responses={503: PENDING},
    openapi_extra=pending_meta("review_pack", PHASE),
)
def record_verdict(body: VerdictIn, writer: WriterDep) -> Any:
    return pending("POST /api/v1/verdicts", "review_pack", PHASE)


@router.get(
    "/verdicts",
    response_model=NotImplementedResponse,
    summary="Recorded supervisor conclusions",
    description=(
        "In the frozen contract; 503 until Phase 11. Read-only by design — a "
        "recorded conclusion is never edited or withdrawn, only superseded by a "
        "later one. Each row is attributed to the supervisor who wrote it, and "
        "that attribution is part of the record rather than an audit detail."
    ),
    responses={503: PENDING},
    openapi_extra=pending_meta("review_pack", PHASE),
)
def list_verdicts(actor: ActorDep) -> Any:
    return pending("GET /api/v1/verdicts", "review_pack", PHASE)
