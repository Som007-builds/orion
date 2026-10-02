"""Export formats.

503 until Phase 13 (2.13), which is on the compression ladder above the cut line.
The OpenAPI answer to the format question — **all eight** — is recorded on
`/exports/formats` as a typed enum of what the tool will emit, so the frontend's
export menu is built against the final answer even though the export itself is
not written yet.

Every format is a rendering of one canonical brief. A CSV of findings, a Parquet
of evidence rows and a PDF of the review pack must not be able to disagree about
what the data said; they are produced from the same serialised brief, not from
three independent code paths.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import Field

from app.api.v1.deps import ActorDep, WriterDep
from app.api.v1.errors import NotImplementedResponse, pending, pending_meta
from app.schemas.common import OrionModel

router = APIRouter(tags=["exports"])

PENDING = {"model": NotImplementedResponse, "description": "Not built yet"}


class ExportFormat(str, Enum):
    """The eight output formats. Frozen: this is the answer to the open question."""

    CSV = "csv"
    JSON = "json"
    PARQUET = "parquet"
    NDJSON = "ndjson"
    XLSX = "xlsx"
    PDF = "pdf"
    DOCX = "docx"
    MARKDOWN = "md"


class ExportFormatInfo(OrionModel):
    format: ExportFormat
    mime_type: str
    extension: str
    renders: str = Field(
        ..., description="What this format is a rendering of, in one line."
    )


FORMATS: list[ExportFormatInfo] = [
    ExportFormatInfo(format=ExportFormat.CSV, mime_type="text/csv", extension=".csv",
                     renders="Flat tables: findings, evidence rows, dimension scores."),
    ExportFormatInfo(format=ExportFormat.JSON, mime_type="application/json", extension=".json",
                     renders="The full brief as one nested object, schema-faithful."),
    ExportFormatInfo(format=ExportFormat.PARQUET, mime_type="application/vnd.apache.parquet",
                     extension=".parquet",
                     renders="Typed columnar tables for analysis. No loss to CSV."),
    ExportFormatInfo(format=ExportFormat.NDJSON, mime_type="application/x-ndjson",
                     extension=".ndjson",
                     renders="One JSON object per line, for streaming into other tools."),
    ExportFormatInfo(format=ExportFormat.XLSX, mime_type=(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        extension=".xlsx",
        renders="The same tables as CSV, formatted for an examiner without tooling."),
    ExportFormatInfo(format=ExportFormat.PDF, mime_type="application/pdf", extension=".pdf",
                     renders="The review pack as a document, evidence rows included."),
    ExportFormatInfo(format=ExportFormat.DOCX, mime_type=(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        extension=".docx",
        renders="The review pack, editable — the format an examiner returns with notes."),
    ExportFormatInfo(format=ExportFormat.MARKDOWN, mime_type="text/markdown", extension=".md",
                     renders="The brief in plain text. Diffable, greppable, no tooling."),
]


@router.get(
    "/exports/formats",
    response_model=list[ExportFormatInfo],
    summary="The export formats this deployment produces",
    description=(
        "Live even though export is not implemented. The frontend's export menu is "
        "built against this list, so the answer to 'which formats' is a contract "
        "rather than a conversation — and it is all eight, not a subset chosen "
        "later."
    ),
)
def formats() -> list[ExportFormatInfo]:
    return FORMATS


@router.get(
    "/exports/{entity_id}",
    response_model=NotImplementedResponse,
    summary="Export one entity's brief in the requested format",
    description=(
        "503 until Phase 13 (2.13). Formats come from "
        "`GET /exports/formats`, which is live now. The export must carry the same "
        "caveats and the same nulls as the screen: a dimension that could not be "
        "assessed exports as absent, not as zero."
    ),
    responses={503: PENDING},
    openapi_extra=pending_meta("report_generator", "Phase 13 (2.13)"),
)
def export_entity(
    entity_id: str,
    actor: ActorDep,
    fmt: Annotated[ExportFormat, Query(description="Output format")] = ExportFormat.JSON,
) -> Any:
    return pending("GET /api/v1/exports/{id}", "report_generator", "Phase 13 (2.13)")


@router.post(
    "/exports/brief",
    response_model=NotImplementedResponse,
    summary="Export a multi-entity brief in the requested format",
    description=(
        "503 until Phase 13. A brief covering several entities is the artefact "
        "that goes to NCIIPC, so it is generated once and rendered into whichever "
        "format is asked for — the formats must not be able to tell different "
        "stories."
    ),
    responses={503: PENDING},
    openapi_extra=pending_meta("report_generator", "Phase 13 (2.13)"),
)
def export_brief(
    writer: WriterDep,
    fmt: Annotated[ExportFormat, Query()] = ExportFormat.PDF,
) -> Any:
    return pending("POST /api/v1/exports/brief", "report_generator", "Phase 13 (2.13)")
