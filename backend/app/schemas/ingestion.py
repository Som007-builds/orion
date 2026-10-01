"""Ingestion and submission schemas."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from app.schemas.common import DataTier, OrionModel, Severity


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class PipelineStage(str, Enum):
    """The ten ingestion stages (plan §4). Rejecting rows are never dropped."""

    RECEIVE = "receive"
    VERIFY = "verify"
    DETECT = "detect"
    MAP = "map"
    NORMALISE = "normalise"
    VALIDATE = "validate"
    QUARANTINE = "quarantine"
    PSEUDONYMISE = "pseudonymise"
    LOAD = "load"
    LEDGER = "ledger"


class UploadFormat(str, Enum):
    CSV = "csv"
    TSV = "tsv"
    JSON = "json"
    NDJSON = "ndjson"
    PARQUET = "parquet"
    SQLITE = "sqlite"
    SQL_DUMP = "sql_dump"
    XLSX = "xlsx"


class MappingSuggestionOut(OrionModel):
    """One fuzzy-mapping suggestion. A human approves every mapping."""

    source_column: str
    suggested_target: str | None
    confidence: float = Field(..., ge=0.0, le=1.0)
    sample_values: list[str] = Field(default_factory=list)
    reason: str | None = None


class DQComponentOut(OrionModel):
    component: str
    score: float = Field(..., ge=0.0, le=1.0)
    weight: float = Field(..., ge=0.0)
    detail: str | None = None


class DQReportOut(OrionModel):
    """`GET /submissions/{id}` — data quality, fully decomposed."""

    submission_id: str
    entity_id: str
    dq_score: float = Field(..., ge=0.0, le=1.0)
    components: list[DQComponentOut] = Field(default_factory=list)

    data_tier: DataTier
    n_rows_loaded: int = Field(..., ge=0)
    n_rows_quarantined: int = Field(..., ge=0)

    quarantine_summary: list["QuarantineSummaryOut"] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    interpretation: str = Field(
        ...,
        description=(
            "Plain-language statement of what this submission can and cannot "
            "support. Never presented as a quality verdict on the entity."
        ),
    )


class QuarantineSummaryOut(OrionModel):
    stage: PipelineStage
    reason: str
    count: int = Field(..., ge=0)
    sample_row_refs: list[str] = Field(
        default_factory=list,
        description="Row references the examiner can request via /submissions/{id}",
    )


class QuarantineRowOut(OrionModel):
    quarantine_id: str
    source_table: str | None = None
    source_file: str | None = None
    source_row: int | None = None
    failed_stage: PipelineStage
    reason: str
    raw_payload: str | None = None
    created_ts: str


class SubmissionOut(OrionModel):
    submission_id: str
    entity_id: str
    entity_name: str | None = None
    period_start: str
    period_end: str
    received_ts: str
    manifest_hash: str
    schema_profile: str | None = None
    row_counts: dict[str, int] = Field(default_factory=dict)
    declared_kpis: dict[str, float] | None = None
    dq_score: float | None = Field(None, ge=0.0, le=1.0)
    data_tier: DataTier | None = None
    version: int = Field(..., ge=1)


class MappingOut(OrionModel):
    """`GET /submissions/{id}/mapping`."""

    submission_id: str
    detected_profile: str | None = None
    vendor: str | None = None
    profile_version: str | None = None
    approved: bool
    approved_by: str | None = None
    timezone: str | None = None
    column_map: dict[str, str] = Field(default_factory=dict)
    severity_map: dict[str, str] = Field(default_factory=dict)
    status_map: dict[str, str] = Field(default_factory=dict)
    suggestions: list[MappingSuggestionOut] = Field(default_factory=list)
    unmapped_columns: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MappingApprovalIn(OrionModel):
    """`POST /submissions/{id}/mapping/approve`. Ledgered."""

    profile_id: str | None = None
    column_map: dict[str, str] = Field(default_factory=dict)
    severity_map: dict[str, str] = Field(default_factory=dict)
    status_map: dict[str, str] = Field(default_factory=dict)
    timezone: str | None = None
    notes: str | None = None


class MappingApprovalOut(OrionModel):
    submission_id: str
    profile_id: str
    approved: bool
    ledger_entry_hash: str
    approved_at: str


class UploadAccepted(OrionModel):
    """`POST /ingest/upload` — the job is queued, not done.

    Ingestion runs in the background under the DuckDB single-writer lock, so
    this is an acknowledgement, not a result.
    """

    job_id: str
    submission_id: str | None = None
    entity_id: str
    status: JobStatus
    accepted_files: list[str] = Field(default_factory=list)
    total_bytes: int = Field(..., ge=0)
    detected_format: UploadFormat | None = None
    detected_profile: str | None = None
    message: str


class IngestionJobOut(OrionModel):
    job_id: str
    submission_id: str | None = None
    entity_id: str | None = None
    status: JobStatus
    stage: PipelineStage | None = None
    created_ts: str
    started_ts: str | None = None
    finished_ts: str | None = None
    error: str | None = None
    n_loaded: int = Field(0, ge=0)
    n_quarantined: int = Field(0, ge=0)


class SeverityMapEntry(OrionModel):
    raw_value: str
    severity: Severity | None = Field(
        None, description="None means quarantine — unmapped values are never guessed"
    )
    quarantine_reason: str | None = None


DQReportOut.model_rebuild()