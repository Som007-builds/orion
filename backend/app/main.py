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
""".strip()


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
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # Loopback only — an air-gapped tool has no business accepting cross-origin
    # requests, and the Next.js dev server runs on localhost.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["system"])
    def health() -> dict:
        """Liveness probe. `offline: true` is a constant, not a claim."""
        return {
            "status": "ok",
            "offline": True,
            "version": "2.0.0",
            "outputs": "hypotheses_with_evidence",
        }

    # Routers are mounted in Phase 12, once the endpoints exist.
    return app


app = create_app()