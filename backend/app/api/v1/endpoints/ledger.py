"""The audit ledger.

`GET /ledger/verify` is the most important route in the API and the cheapest. If
the chain is broken, every score derived from the data it covered is in question,
and an operator should be able to find that out without running anything. It
returns where the chain diverges as well as whether it does, because "invalid"
alone cannot distinguish deliberate tampering from a half-finished restore, and
those two need opposite responses.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse

from app.schemas.common import Page
from app.schemas.ledger import LedgerEntryOut, LedgerVerifyOut
from app.services.ledger import get_ledger

router = APIRouter(tags=["ledger"])


@router.get(
    "/ledger",
    response_model=Page[LedgerEntryOut],
    summary="Audit entries, newest first",
    description=(
        "Append-only. There is no write, edit or delete route for ledger entries "
        "and there will not be one; SQLite triggers on the table raise on UPDATE "
        "and DELETE as well, so the guarantee does not rest on the API alone."
    ),
)
def page_ledger(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    action: Annotated[str | None, Query(description="Filter by action")] = None,
    actor: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
) -> Page[LedgerEntryOut]:
    ledger = get_ledger()
    items = ledger.page(
        limit=limit, offset=offset, action=action, actor=actor, entity_id=entity_id
    )
    total = ledger.count(action=action, actor=actor, entity_id=entity_id)
    return Page[LedgerEntryOut](
        # The service already unpacks the JSON payload column, so `payload` is a
        # dict here rather than a string the client would have to parse.
        items=[LedgerEntryOut(**vars(entry)) for entry in items],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + limit < total,
    )


@router.get(
    "/ledger/verify",
    summary="Verify the hash chain end to end",
    description=(
        "Walks every entry, recomputing each hash from the previous one and the "
        "entry's own content. Returns **200 with `valid: false`** when the chain "
        "is broken, not a 4xx or 5xx: a broken chain is a finding about the "
        "data, and a client that treats a non-2xx as 'request failed' would not "
        "show it to the operator. `first_break_seq` and `break_reason` say where "
        "and why."
    ),
)
def verify_ledger() -> Any:
    result = get_ledger().verify()
    body = LedgerVerifyOut(
        valid=result.valid,
        head_hash=result.head_hash,
        entries_checked=result.entries_checked,
        first_break_seq=result.first_break_seq,
        break_reason=result.break_reason,
        verified_at=_now(),
    ).model_dump(mode="json")
    # 200 either way, per the description. A JSONResponse is returned so the status
    # line is chosen here rather than by the decorator.
    return JSONResponse(status_code=status.HTTP_200_OK, content=body)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
