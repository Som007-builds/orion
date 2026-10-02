"""`/api/v1` — the aggregate router.

The full v1 surface, mounted under one prefix. Every route in the plan's §5 table
appears here, including the ones whose backing service has not been written: those
return a typed 503 naming the service and the phase that delivers it. See
`app/api/v1/errors.py` for why they are registered rather than omitted.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    entities,
    exports,
    ingest,
    ledger,
    packs,
    policy,
    review_packs,
    runs,
    submissions,
    trends,
)

api_router = APIRouter()

# Order matters only for readability in the generated OpenAPI; routing itself is
# matched by path. Grouped by domain rather than alphabetically.
api_router.include_router(entities.router)
api_router.include_router(submissions.router)
api_router.include_router(ingest.router)
api_router.include_router(runs.router)
api_router.include_router(review_packs.router)
api_router.include_router(trends.router)
api_router.include_router(policy.router)
api_router.include_router(exports.router)
api_router.include_router(packs.router)
api_router.include_router(ledger.router)

__all__ = ["api_router"]
