"""Pack lifecycle: stage, shadow, promote, rollback.

503 until Phase 14 (2.14), which is the **first** thing to cut if the deadline
bites — the tool works without a pack manager, and a pack can be managed by hand
from `data/packs/` until then.

The route set is published now because it is the part of the contract most likely
to need a second version later: a rollback route that has to be *added* once
someone tries to use it is a rollback route nobody has tested. Having it in the
contract with a 503 makes its existence and its ordering unambiguous from day one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.v1.deps import WriterDep
from app.api.v1.errors import NotImplementedResponse, pending, pending_meta
from app.schemas.ledger import PackStageIn

router = APIRouter(tags=["packs"])

PENDING = {"model": NotImplementedResponse, "description": "Not built yet"}
PHASE = "Phase 14 (2.14)"


@router.post(
    "/packs/stage",
    response_model=NotImplementedResponse,
    summary="Stage a pack version without activating it",
    description=(
        "503 until Phase 14. Staging writes to `data/packs/staged/` and changes "
        "nothing that is live. A pack that has been staged but not shadow-run and "
        "not promoted is inert, which is the point: a pack version should be able "
        "to exist on disk without being reachable."
    ),
    responses={503: PENDING},
    openapi_extra=pending_meta("pack_manager", PHASE),
)
def stage_pack(body: PackStageIn, writer: WriterDep) -> Any:
    return pending("POST /api/v1/packs/stage", "pack_manager", PHASE)


@router.post(
    "/packs/{pack_id}/shadow",
    response_model=NotImplementedResponse,
    summary="Run a staged pack in shadow against the live one",
    description=(
        "503 until Phase 14. Shadow execution scores with the candidate pack while "
        "continuing to serve the live one, so a regression shows up as a diff "
        "between two result sets rather than as a changed dashboard nobody was "
        "watching."
    ),
    responses={503: PENDING},
    openapi_extra=pending_meta("pack_manager", PHASE),
)
def shadow_pack(pack_id: str, writer: WriterDep) -> Any:
    return pending("POST /api/v1/packs/{id}/shadow", "pack_manager", PHASE)


@router.post(
    "/packs/{pack_id}/promote",
    response_model=NotImplementedResponse,
    summary="Make a shadow-run pack the live one. Ledgered.",
    description=(
        "503 until Phase 14. Promotion is refused without a shadow run that has "
        "been compared against the live pack — an unreviewed promotion is how a "
        "detector change silently alters every stored score's meaning."
    ),
    responses={503: PENDING},
    openapi_extra=pending_meta("pack_manager", PHASE),
)
def promote_pack(pack_id: str, writer: WriterDep) -> Any:
    return pending("POST /api/v1/packs/{id}/promote", "pack_manager", PHASE)


@router.post(
    "/packs/{pack_id}/rollback",
    response_model=NotImplementedResponse,
    summary="Return to the previous live pack. Ledgered.",
    description=(
        "503 until Phase 14. **This is the first route to cut if time runs out** — "
        "but it is the one nobody should want to discover is missing, because "
        "rollback is what you need precisely when something has gone wrong."
    ),
    responses={503: PENDING},
    openapi_extra=pending_meta("pack_manager", PHASE),
)
def rollback_pack(pack_id: str, writer: WriterDep) -> Any:
    return pending("POST /api/v1/packs/{id}/rollback", "pack_manager", PHASE)
