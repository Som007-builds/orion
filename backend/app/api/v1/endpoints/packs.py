"""Pack lifecycle (plan §4.10, Phase 14): stage, shadow, promote, rollback.

A pack is a signed, self-contained bundle (`manifest.json` plus the policy it
carries) that can be compared against the live run, made the active policy, and
restored — without ever editing a profile in place.

The four steps are deliberately asymmetric:

* **stage** copies the bundle into `data/packs/staged/`, hashes and signs it,
  and changes nothing live. A staged pack is inert by construction.
* **shadow** executes the pack's policy over the live window *without
  persisting anything* and diffs its raised findings against the stored
  findings of the latest completed run, with a mechanical promote/hold
  recommendation.
* **promote** — the first live step — requires a shadow run, installs the
  bundle into `data/packs/active/`, adopts the bundled policy as the active
  policy, and demotes the previous live pack to `rolled_back`.
* **rollback** restores the *immediate* predecessor, one step at a time.

Every step is ledgered. Routes map `PackNotFound` to 404; rule refusals
(`PackError` and friends) map to 400 through the shared exception classifier.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.v1.deps import WriterDep
from app.schemas.ledger import (
    PackOut,
    PackShadowOut,
    PackStageIn,
    PackTransitionOut,
)
from app.services.pack_manager import PackNotFound, get_pack_manager

router = APIRouter(tags=["packs"])


@router.post(
    "/packs/stage",
    response_model=PackOut,
    status_code=201,
    summary="Stage a pack version without activating it",
    description=(
        "Copy a pack bundle (manifest.json + policy/<profile_id>.yaml) into "
        "`data/packs/staged/<pack_id>/`, hash its contents deterministically, "
        "sign the hash with the host key, and ledger `pack_staged`. Staging "
        "changes nothing live: a staged pack is inert until a shadow run "
        "compares it with the live result set and an operator promotes it."
    ),
    responses={
        404: {"description": "No such pack id or bundle"},
        400: {"description": "Bundle validation or pack id/version mismatch"},
    },
)
def stage_pack(body: PackStageIn, writer: WriterDep) -> Any:
    try:
        return get_pack_manager().stage(body, actor=writer.name)
    except PackNotFound as exc:  # pragma: no cover - stage validates locally
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/packs/{pack_id}/shadow",
    response_model=PackShadowOut,
    summary="Run the staged policy against the live result set without activating it",
    description=(
        "Execute the pack's policy over the same window the latest completed run "
        "covered, without persisting anything, and diff its raised findings "
        "against that run's stored findings. `n_findings_added/removed/changed` "
        "and the full `diff_report` are recorded on the pack row, with a "
        "mechanical recommendation (promote unless the candidate adds findings "
        "or intensifies existing ones). The run changes nothing live."
    ),
    responses={
        404: {"description": "No such pack"},
        400: {"description": "Pack not staged, or no completed run to compare with"},
    },
)
def shadow_pack(pack_id: str, writer: WriterDep) -> Any:
    try:
        return get_pack_manager().shadow(pack_id, actor=writer.name)
    except PackNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/packs/{pack_id}/promote",
    response_model=PackTransitionOut,
    summary="Make a shadow-run pack the live pack",
    description=(
        "Requires the pack to have been shadow-run. Copies the bundle from "
        "`staged/` to `active/`, adopts the bundled policy as the active policy "
        "(skipped when its content hash already matches the active one), "
        "demotes the previous live pack to `rolled_back`, and ledgeres "
        "`pack_promoted`. This is the only pack step that changes what scoring "
        "reads."
    ),
    responses={
        404: {"description": "No such pack"},
        400: {"description": "Pack not shadow-run"},
    },
)
def promote_pack(pack_id: str, writer: WriterDep) -> Any:
    try:
        return get_pack_manager().promote(pack_id, actor=writer.name)
    except PackNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/packs/{pack_id}/rollback",
    response_model=PackTransitionOut,
    summary="Restore the immediately previous live pack",
    description=(
        "`pack_id` names the pack to restore: it must be the immediate "
        "predecessor of the current live pack (status `rolled_back`, promoted "
        "directly before the active one). One step per call — the statuses swap, "
        "the active bundle is replaced from `staged/`, the restored pack's "
        "policy is re-adopted, and `pack_rolled_back` is ledgered."
    ),
    responses={
        404: {"description": "No such pack"},
        400: {"description": "Pack not superseded, or not the immediate predecessor"},
    },
)
def rollback_pack(pack_id: str, writer: WriterDep) -> Any:
    try:
        return get_pack_manager().rollback(pack_id, actor=writer.name)
    except PackNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc