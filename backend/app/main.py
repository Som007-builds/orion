"""FastAPI application factory and lifecycle.

Lifecycle (plan §2, `app/main.py`):
  startup  → open stores, verify ledger chain, record head hash
  shutdown → close stores, join background worker

The background worker owns the single DuckDB write connection. Request handlers
never take a write lock (architecture principle §1.5).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

logger = logging.getLogger("orion")

DESCRIPTION = """
Supervisory Analytics Tool for SOC Assessment (SAT-SA) for NCIIPC.

Outputs are **prioritised hypotheses with evidence**, never verdicts or
compliance grades. `Not assessable` is a first-class outcome, distinct from
"low risk".

Fully offline: no cloud APIs, no CDN, no generative AI in the decision path.

## Reading this contract

`Not assessable` is a **value**, not an absence. Every score field is nullable
and `null` means "could not be assessed" — never "scored zero". The frontend must
not coalesce `null` to `0`; a dimension with no data and a dimension with a
measured score of zero are different claims.

`POST /verdicts` records **a supervisor's own conclusion**, written by a human.
No Orion-derived payload in this API contains a verdict, a compliance grade, or a
pass/fail.

Routes marked `503` are part of this frozen contract but their backing service is
not written yet. The response body names the service and the phase that delivers
it. They are listed rather than omitted so the surface does not change shape
mid-project; `GET /api/v1` splits them into `live` and `pending`.
""".strip()

TAGS_METADATA = [
    {
        "name": "entities",
        "description": (
            "Registered entities, their supervisory scores, and what can and "
            "cannot be assessed about them."
        ),
    },
    {
        "name": "submissions",
        "description": (
            "What an entity submitted, how good it is, the mapping a human "
            "approved, and the rows the pipeline refused."
        ),
    },
    {
        "name": "ingestion",
        "description": (
            "Upload intake and the job queue. Uploads are accepted, not loaded: "
            "loading holds the single-writer lock and runs outside the request."
        ),
    },
    {
        "name": "runs",
        "description": (
            "Scoring runs and the indicator catalogue. A run is the reproducibility "
            "unit: every score and finding below it is traceable to one run id."
        ),
    },
    {
        "name": "findings",
        "description": (
            "Finding cards and their evidence. A card is not a score and not a "
            "verdict — it is one surviving indicator with the rows behind it, why "
            "the other indicators stayed quiet, and what would clear it."
        ),
    },
    {
        "name": "review packs",
        "description": (
            "Stratified samples for human review, and the supervisor's own "
            "recorded conclusion about them."
        ),
    },
    {"name": "trends", "description": "Scored metrics over successive periods."},
    {
        "name": "policy",
        "description": (
            "Policy profiles — the versioned definition of what counts as a gap — "
            "and peer cohort baselines."
        ),
    },
    {"name": "exports", "description": "Brief and pack export formats."},
    {"name": "packs", "description": "Pack lifecycle: stage, shadow, promote, rollback."},
    {
        "name": "ledger",
        "description": (
            "The append-only audit chain and its verification. Nothing in this API "
            "writes, edits or deletes a ledger entry."
        ),
    },
    {"name": "system", "description": "Liveness and the contract itself."},
]


# One definition. It appears in the OpenAPI `info.version`, in `/health` and in
# `GET /api/v1`, and three literals is three chances for a client's version check
# to disagree with the server's.
API_VERSION = "2.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info("Orion starting (offline, air-gapped)")

    from app.db.sqlite import init_db
    from app.services.ledger import Ledger

    init_db()

    # Verify the audit chain on boot. A broken chain must be loud, not silent:
    # it means either tampering or a partial restore.
    ledger = Ledger()
    verify_result = ledger.verify()
    if verify_result.valid:
        logger.info("ledger verified: %d entries, head=%s", verify_result.entries_checked,
                    verify_result.head_hash)
    else:
        logger.error(
            "LEDGER CHAIN BROKEN at seq=%s — possible tampering or partial restore",
            verify_result.first_break_seq,
        )

    yield

    logger.info("Orion shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Orion (SAT-SA) Core Backend",
        description=DESCRIPTION,
        version=API_VERSION,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
        openapi_tags=TAGS_METADATA,
    )

    # Loopback only — an air-gapped tool has no business accepting cross-origin
    # requests, and the Next.js dev server runs on localhost.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.api.v1.errors import install_handlers
    from app.api.v1.router import api_router

    # Installed before the routers so a handler is in place for anything they can
    # raise, including during dependency resolution.
    install_handlers(app)
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health", tags=["system"])
    def health() -> dict:
        """Liveness probe. `offline: true` is a constant, not a claim.

        The sovereignty check in `backend/scripts/sovereignty_check.py` is what
        turns that constant into evidence; nothing here can prove it at runtime,
        so this endpoint does not pretend to.
        """
        return {
            "status": "ok",
            "offline": True,
            "version": API_VERSION,
            "outputs": "hypotheses_with_evidence",
        }

    @app.get("/api/v1", tags=["system"], summary="The contract, as a list")
    def contract() -> dict:
        """A machine-readable index of the surface.

        Cheaper than fetching the whole OpenAPI document, and it answers the one
        question a client asks at startup: which of these can I actually call?
        Routes whose service is not built are listed with their phase rather than
        omitted, so a client can tell "not implemented yet" from "does not exist".
        """
        from app.api.v1.errors import is_pending

        live, not_yet = [], []
        for route in api_router.routes:
            path = getattr(route, "path", None)
            methods = sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"})
            if not path:
                continue
            entry = {"path": f"/api/v1{path}", "methods": methods}
            if is_pending(route):
                extra = (route.openapi_extra or {}).get("x-orion", {})
                entry["service"] = extra.get("service")
                entry["phase"] = extra.get("phase")
                not_yet.append(entry)
            else:
                live.append(entry)
        return {
            "version": API_VERSION,
            "live": sorted(live, key=lambda e: e["path"]),
            "pending": sorted(not_yet, key=lambda e: e["path"]),
            "openapi": "/openapi.json",
        }

    return app


app = create_app()