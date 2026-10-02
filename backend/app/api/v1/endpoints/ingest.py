"""Upload intake and the ingestion job queue.

`POST /ingest/upload` acknowledges, it does not load. Ingestion holds the DuckDB
single-writer lock and must not occupy a request thread, so the response is a job
id and the client polls `GET /ingest/jobs/{id}`. Saying so in the route
description is deliberate: a client that treats the 200 as "data is in" will read
empty dashboards and file it as a scoring bug.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.api.v1.deps import WriterDep, ingestion_service
from app.api.v1.errors import NotImplementedResponse, pending, pending_meta
from app.config import get_settings
from app.schemas.common import Page
from app.schemas.ingestion import IngestionJobOut, UploadAccepted
from app.services.ingestion_service import IngestionError

router = APIRouter(tags=["ingestion"])


@router.post(
    "/ingest/upload",
    response_model=UploadAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Accept a submission for an entity and queue it for loading",
    description=(
        "Returns **202 with a job id**, not a result. The load runs under the "
        "DuckDB single-writer lock; poll `GET /ingest/jobs/{job_id}` for the "
        "outcome. A `202` here means the files were staged and a submission was "
        "registered — nothing has been parsed, validated or loaded yet."
    ),
    responses={
        400: {"description": "Upload rejected by the security checks"},
        404: {"description": "No such entity"},
        413: {"description": "Upload exceeds the configured size limit"},
    },
)
async def upload(
    writer: WriterDep,
    entity_id: Annotated[str, Form(description="Entity the submission is for")],
    period_start: Annotated[date, Form(description="Inclusive, YYYY-MM-DD")],
    period_end: Annotated[date, Form(description="Inclusive, YYYY-MM-DD")],
    files: Annotated[
        list[UploadFile] | None,
        File(description="One or more source files. At least one is required."),
    ] = None,
    declared_kpis: Annotated[
        str | None,
        Form(
            description=(
                "Optional JSON object of the entity's own declared KPIs, e.g. "
                '{"mttr_minutes": 45}. Taken as the submission states them, not '
                "as Orion's measurement — the distinction is what stops a declared "
                "figure from being read as a computed one."
            )
        ),
    ] = None,
) -> UploadAccepted:
    if period_start > period_end:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"period_start {period_start} is after period_end {period_end}.",
        )
    if not files:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No files supplied. A submission with no files is a registration with "
            "no evidence, which scores as an entity that has demonstrated nothing "
            "— a different claim from one that has not submitted.",
        )

    kpis: dict[str, float] | None = None
    if declared_kpis:
        try:
            kpis = {str(k): float(v) for k, v in json.loads(declared_kpis).items()}
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"declared_kpis is not a JSON object of numbers: {exc}",
            ) from exc

    # Read in the event loop, stage in a worker thread. Reading is async I/O and
    # belongs here; writing the staged files to disk is blocking and would stall
    # every other request on the host for the duration of a large upload.
    limit = get_settings().max_upload_bytes
    payload: list[tuple[str, bytes]] = []
    total = 0
    for index, upload_file in enumerate(files):
        chunk = await upload_file.read()
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"Upload exceeds the {limit} byte limit. Split the submission.",
            )
        payload.append((upload_file.filename or f"upload_{index}", chunk))

    try:
        return await run_in_threadpool(
            ingestion_service().accept_upload,
            entity_id=entity_id,
            files=payload,
            period_start=period_start,
            period_end=period_end,
            actor=writer.name,
            declared_kpis=kpis,
        )
    except IngestionError as exc:
        # `accept_upload` funnels every security rejection through IngestionError,
        # so the message carries the specific reason (zip bomb, oversized cell,
        # extension mismatch). Passing it through is what lets an examiner fix the
        # file rather than guess.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get(
    "/ingest/jobs",
    response_model=Page[IngestionJobOut],
    summary="The ingestion queue",
    description=(
        "One row per accepted upload. `quarantined` is a real outcome and not an "
        "error to hide: a submission that fails a security rule is parked with the "
        "reason recorded rather than dropped, so a missing file is always "
        "answerable."
    ),
)
def list_jobs(
    entity_id: Annotated[str | None, Query()] = None,
    job_status: Annotated[
        str | None,
        Query(alias="status", description="queued, running, complete, failed, quarantined"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[IngestionJobOut]:
    # Over-fetch by `offset` so the page can be sliced without a second query. The
    # service applies its own limit; asking for offset+limit rows and dropping the
    # first `offset` is what makes the returned `total` mean something.
    rows = ingestion_service().list_jobs(entity_id, job_status, limit + offset, 0)
    return Page[IngestionJobOut](
        items=rows[offset : offset + limit],
        total=len(rows),
        limit=limit,
        offset=offset,
        has_more=offset + limit < len(rows),
    )


@router.get(
    "/ingest/jobs/{job_id}",
    response_model=IngestionJobOut,
    summary="Poll one ingestion job",
    description=(
        "The 202 from `POST /ingest/upload` hands back a job id and the load is "
        "asynchronous, because staging a multi-gigabyte file must not hold the "
        "request open. Poll this until `job_status` is terminal."
    ),
    responses={404: {"description": "No such job"}},
)
def get_job(job_id: str) -> IngestionJobOut:
    try:
        return ingestion_service().get_job(job_id)
    except IngestionError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "/ingest/seed/{seed_id}",
    response_model=NotImplementedResponse,
    summary="Load a synthetic demonstration cohort (Phase 19)",
    description=(
        "In the frozen v1 contract. Returns 503 until the seed loader lands in "
        "Phase 19 — the SOCSim presets (OQ-8) are Dev 3's to decide, so the "
        "fixture set this route would load is not yet fixed."
    ),
    responses={503: {"model": NotImplementedResponse, "description": "Not built yet"}},
    openapi_extra=pending_meta("seed_loader", "Phase 19 (2.19)"),
)
def load_seed(seed_id: str, writer: WriterDep) -> Any:
    return pending(
        "POST /api/v1/ingest/seed/{seed_id}", "seed_loader", "Phase 19 (2.19)"
    )
