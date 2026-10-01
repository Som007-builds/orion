"""The ten-stage ingestion pipeline (plan §4).

```
 1 receive       stage files; size, magic-byte, zip-bomb, path-traversal checks
 2 verify        per-file content hash, submission manifest hash
 3 detect        format + destination-table inference + vendor profile match
 4 map           resolve a human-approved mapping (never auto-apply a fuzzy match)
 5 normalise     vendor vocabulary -> canonical vocabulary, UTC, CSV-injection
 6 validate      primary keys, required fields, enum domains, cross-table refs
 7 quarantine    persist every rejected row with the stage that rejected it
 8 pseudonymise  analysts, IPs, hostnames; redact and store notes
 9 load          write to DuckDB under the single-writer lock
10 ledger        append the DQ result and the row counts
```

Three rules shape the module.

**Nothing is dropped.** Every row a stage rejects is written to `quarantine`
with a reason the examiner can act on. A row that vanished is indistinguishable
from a row that was never submitted, and that ambiguity is precisely what the
negative-space indicators must not be built on.

**Nothing is imputed.** A missing timestamp stays missing. Each normaliser
records *why* a cell is absent, so the DQ report attributes a gap to a named
cause instead of reporting a bare null rate.

**Stage order is a privacy boundary.** Validation (6) runs *before*
pseudonymisation (8), so a rejected row is quarantined with its raw contents
for the examiner while a *retained* row never reaches DuckDB or `note_store`
with an un-pseudonymised analyst, IP or hostname. Quarantine is operator-facing
and stays inside the trust boundary; the evidence lake does not.

DuckDB is written only in stage 9, inside one `writer()` block for the whole
submission. Taking and releasing the lock per table would let a reader observe a
half-loaded submission.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl

from app.config import get_settings
from app.db.duckdb_client import get_duckdb
from app.db.sqlite import get_connection, transaction
from app.schemas.common import DataTier
from app.schemas.ingestion import (
    DQReportOut,
    JobStatus,
    MappingOut,
    MappingSuggestionOut,
    PipelineStage,
    QuarantineRowOut,
    SubmissionOut,
    UploadAccepted,
)
from app.services import dq_scoring
from app.services.format_loaders import infer_table_name, load
from app.services.ledger import LedgerAction, get_ledger
from app.services.mapping_assistant import (
    MappingProfileSpec,
    get_mapping_assistant,
    load_profiles,
    match_profile_table,
)
from app.services.normalisation import (
    PRIMARY_KEYS,
    QUARANTINE_ON_UNMAPPED,
    TABLE_CONTRACTS,
    ColumnRule,
    Normalised,
    NormaliseStats,
    clean,
    missing_result,
    normalise_severity,
    normalise_text,
)
from app.services.policy_profile import policy_profile
from app.services.pseudonymisation import get_pseudonymiser
from app.services.upload_security import (
    SanitisedFile,
    SecurityRejected,
    check_dimensions,
    compute_manifest_hash,
    detect_format,
    receive_uploads,
)

log = logging.getLogger(__name__)

# Rows per DuckDB insert. Bounded so a 40M-row submission does not build one
# enormous parameter list.
INSERT_CHUNK = 20_000

# Below this overlap score a profile match is not trusted to supply the mapping.
PROFILE_MATCH_FLOOR = 0.50

RULES_BY_TABLE: dict[str, dict[str, ColumnRule]] = {
    table: {rule.name: rule for rule in rules}
    for table, rules in TABLE_CONTRACTS.items()
}


class IngestionError(Exception):
    """Fatal pipeline failure. The job is marked `failed`; nothing is half-loaded."""


class MappingRequired(Exception):
    """No approved mapping covers the submission.

    Not a fault — a question for a human. The job stops at stage 4 and stays
    `queued` pending approval, rather than loading a table the pipeline guessed
    at. Carries the assistant's suggestions so the API can show them.
    """

    def __init__(
        self,
        submission_id: str,
        columns: list[str],
        unmapped_required: list[str],
        suggestions: list[MappingSuggestionOut],
        detected_profile: str | None,
    ) -> None:
        super().__init__(
            f"Submission {submission_id}: no approved mapping covers "
            f"{len(unmapped_required)} required column(s). "
            f"{len(suggestions)} suggestion(s) available for human approval."
        )
        self.submission_id = submission_id
        self.columns = columns
        self.unmapped_required = unmapped_required
        self.suggestions = suggestions
        self.detected_profile = detected_profile


@dataclass
class TablePlan:
    """One file's resolved destination and mapping."""

    table: str
    file: SanitisedFile
    columns: list[str]
    source_for_target: dict[str, str]  # canonical column -> submitted column
    profile_id: str | None
    vendor: str | None
    timezone: str
    severity_map: dict[str, str]
    status_map: dict[str, str]
    approved: bool


@dataclass
class StageOutcome:
    stage: PipelineStage
    ok: bool
    detail: str
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class IngestionResult:
    submission_id: str
    job_id: str
    entity_id: str
    version: int
    status: JobStatus
    dq: DQReportOut
    stage_log: list[StageOutcome]
    mapping: MappingOut
    n_loaded: int
    n_quarantined: int
    warnings: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def plan_label(f: SanitisedFile) -> str:
    return f.safe_name


def _staging_dir(data_dir: Path, entity_id: str, submission_id: str) -> Path:
    """Per-submission staging directory.

    Scoped per submission rather than per entity because a CSE submits the same
    filenames every period: `alerts.csv` is expected in period 2, so a shared
    directory would reject the resubmission as a duplicate upload, and a
    re-upload mid-load could overwrite bytes the running job is about to parse.
    """
    return data_dir / "staging" / entity_id / submission_id


def _split_column_map(
    column_map: dict[str, str] | dict[str, dict[str, str]],
    submission_id: str,
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Normalise an approval into per-file, canonical-direction columns.

    Returns `(columns_by_file, flat)` where `columns_by_file` is keyed by staged
    filename and each entry maps canonical column to source column, and `flat` is
    the original as given. A flat approval is expanded across every staged file,
    because that is what the approver meant: one reading of the same vendor
    headers for the whole submission.

    A per-file approval naming a filename the submission never received is
    rejected rather than ignored. Silently dropping it would leave the examiner
    believing they had mapped a file that in fact loaded with fields missing.
    """
    conn = get_connection()
    staged = [
        row["safe_name"]
        for row in conn.execute(
            "SELECT safe_name FROM submission_file WHERE submission_id = ? "
            "ORDER BY ordinal",
            (submission_id,),
        )
    ]

    if column_map and all(isinstance(v, str) for v in column_map.values()):
        canonical = {
            target: source for source, target in column_map.items()
        }
        return ({name: dict(canonical) for name in staged}, dict(column_map))

    by_file: dict[str, dict[str, str]] = {}
    unknown: list[str] = []
    for name, entries in column_map.items():
        if name not in staged:
            unknown.append(name)
            continue
        by_file[name] = {target: source for source, target in entries.items()}

    if unknown:
        raise IngestionError(
            f"Submission {submission_id} has no file(s) named "
            f"{', '.join(sorted(unknown))}. It received "
            f"{', '.join(staged) or '(nothing)'}. Approve a mapping only for "
            "files that were actually submitted."
        )
    if not by_file:
        raise IngestionError(
            "An empty column map cannot be approved. Approve at least the "
            "primary key and the fields the indicators need."
        )
    return by_file, {}


def _columns_for_file(definition: dict[str, Any], safe_name: str) -> dict[str, str]:
    """Canonical column to source column for one file, from an approval.

    Reads `columns_by_file` and falls back to the flat `columns` a vendor
    profile carries. The fallback is not nostalgia: an approval made before the
    per-file shape existed, or a profile edited by hand, has only `columns`, and
    refusing those would break real mappings rather than protect anything.
    """
    by_file = definition.get("columns_by_file")
    if isinstance(by_file, dict):
        entry = by_file.get(safe_name)
        if isinstance(entry, dict):
            return {target: str(source) for target, source in entry.items()}
        return {}

    columns = definition.get("columns") or {}
    return {
        target: str(meta["source"])
        for target, meta in columns.items()
        if isinstance(meta, dict) and meta.get("source")
    }


def _merge_flat(
    by_file: dict[str, dict[str, str]]
) -> dict[str, dict[str, str]]:
    """Flatten per-file columns for display, dropping genuine conflicts.

    A canonical column sourced from two different columns across two files has
    no single flat answer. Rather than let whichever file happened to be last
    win, it is omitted — `columns_by_file` stays authoritative.
    """
    merged: dict[str, dict[str, str]] = {}
    conflicting: set[str] = set()
    for columns in by_file.values():
        for target, source in columns.items():
            if target in merged and merged[target]["source"] != source:
                conflicting.add(target)
                continue
            merged[target] = {"source": source}
    for target in conflicting:
        merged.pop(target, None)
    return merged


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hashable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _aware(value: datetime) -> datetime:
    """Attach UTC to a naive datetime.

    Normalisation already localises every parsed timestamp, so this only fires
    for a value a caller constructed by hand. Assuming UTC rather than local
    time keeps the arithmetic reproducible on any host.
    """
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _safe_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Quarantine payload, clipped per cell.

    The rejected row is stored so an examiner can see what was sent, but a long
    note is truncated — an unbounded free-text field would let a submission write
    megabytes into the audit table.
    """
    return {
        key: (str(value)[:300] if value is not None else None)
        for key, value in raw.items()
    }


# ============================================================== service ==
class IngestionService:
    """Entry point for the API and the background worker."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.assistant = get_mapping_assistant()
        self.ledger = get_ledger()
        self.pseudonymiser = get_pseudonymiser()
        self.policy = policy_profile()

    # ------------------------------------------------------- stages 1-3 ----
    def accept_upload(
        self,
        entity_id: str,
        files: list[tuple[str, bytes]],
        period_start: date,
        period_end: date,
        actor: str = "system",
        declared_kpis: dict[str, float] | None = None,
        submission_id: str | None = None,
    ) -> UploadAccepted:
        """Stage and register a submission. The load itself is a background job.

        Returns an acknowledgement, not a result: the DuckDB write happens under
        the single-writer lock and must not occupy a request thread.
        """
        submission_id = submission_id or _new_id("sub")
        staging_dir = _staging_dir(self.settings.data_dir, entity_id, submission_id)
        try:
            staged = receive_uploads(files, staging_dir)
        except SecurityRejected as exc:
            raise IngestionError(f"Upload rejected: {exc}") from exc

        detected = {f.detected_format for f in staged}
        first = staged[0]

        profile, _table, score = match_profile_table(self._peek_columns(first))
        manifest = compute_manifest_hash([f.path for f in staged])

        version = self._next_version(entity_id)
        received = _now()
        job_id = _new_id("job")

        with transaction() as conn:
            conn.execute(
                "INSERT INTO submission (submission_id, entity_id, period_start, "
                "period_end, received_ts, manifest_hash, schema_profile, "
                "declared_kpis, version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    submission_id,
                    entity_id,
                    period_start.isoformat(),
                    period_end.isoformat(),
                    received.isoformat(),
                    manifest,
                    profile.profile_id if profile else None,
                    json.dumps(declared_kpis) if declared_kpis else None,
                    version,
                ),
            )
            conn.executemany(
                "INSERT INTO submission_file (submission_id, safe_name, "
                "original_name, detected_format, n_bytes, content_hash, ordinal) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        submission_id,
                        f.safe_name,
                        f.original_name,
                        f.detected_format,
                        f.n_bytes,
                        _hash_file(f.path),
                        ordinal,
                    )
                    for ordinal, f in enumerate(staged)
                ],
            )
            conn.execute(
                "INSERT INTO ingestion_job (job_id, submission_id, entity_id, "
                "status, stage, created_ts, started_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    submission_id,
                    entity_id,
                    JobStatus.QUEUED.value,
                    PipelineStage.RECEIVE.value,
                    received.isoformat(),
                    received.isoformat(),
                ),
            )

        self.ledger.append(
            actor=actor,
            action=LedgerAction.SUBMISSION_RECEIVED,
            payload={
                "submission_id": submission_id,
                "entity_id": entity_id,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "manifest_hash": manifest,
                "files": [
                    {"name": f.safe_name, "bytes": f.n_bytes,
                     "format": f.detected_format,
                     "sha256": _hash_file(f.path)}
                    for f in staged
                ],
                "profile": profile.profile_id if profile else None,
                "profile_match": round(score, 3),
                "version": version,
            },
            entity_id=entity_id,
        )

        if len(detected) > 1:
            log.warning(
                "submission %s mixes formats %s; each file is loaded independently",
                submission_id,
                sorted(detected),
            )

        return UploadAccepted(
            job_id=job_id,
            submission_id=submission_id,
            entity_id=entity_id,
            status=JobStatus.QUEUED,
            accepted_files=[f.safe_name for f in staged],
            total_bytes=sum(f.n_bytes for f in staged),
            detected_format=first.detected_format,
            detected_profile=profile.profile_id if profile else None,
            message=(
                f"Staged as {len(staged)} file(s). Stages 4-10 run as a background job "
                f"under the DuckDB single-writer lock. Poll GET /ingest/jobs/{job_id}; "
                f"if a mapping is required, approve it at "
                f"POST /submissions/{submission_id}/mapping/approve."
            ),
        )

    # ---------------------------------------------------- stages 4-10 -----
    def run_job(
        self,
        job_id: str,
        approved_column_maps: dict[str, dict[str, str]] | None = None,
        severity_overrides: dict[str, dict[str, str]] | None = None,
        warnings: list[str] | None = None,
    ) -> IngestionResult:
        """Run stages 4-10 for a queued job.

        `approved_column_maps` is keyed on the staged filename. A mapping is
        never inferred here; a file with no approved mapping raises
        `MappingRequired` and the job waits for a human.
        """
        conn = get_connection()
        job = conn.execute(
            "SELECT * FROM ingestion_job WHERE job_id = ?", (job_id,)
        ).fetchone()
        if job is None:
            raise IngestionError(f"No such ingestion job: {job_id}")

        submission_id: str = job["submission_id"]
        submission = conn.execute(
            "SELECT * FROM submission WHERE submission_id = ?", (submission_id,)
        ).fetchone()
        if submission is None:
            raise IngestionError(f"Job {job_id} references missing submission {submission_id}")

        entity_id: str = submission["entity_id"]
        self._set_stage(job_id, PipelineStage.MAP)

        stage_log: list[StageOutcome] = []
        collected_warnings: list[str] = warnings if warnings is not None else []
        stats = NormaliseStats()
        quarantine: dict[tuple[str, str, str], list[tuple[str, int, str]]] = {}
        tables_loaded: dict[str, int] = {}
        duplicates: dict[str, int] = {}

        try:
            # -- stage 4: map --------------------------------------------
            plans = self._resolve_mappings(
                submission_id, entity_id, approved_column_maps or {},
                severity_overrides or {}, collected_warnings,
            )
            mapped_columns = sum(len(p.source_for_target) for p in plans)
            stage_log.append(StageOutcome(
                PipelineStage.MAP, True,
                f"{len(plans)} file(s) mapped, {mapped_columns} column(s) resolved",
                {"files": len(plans), "columns": mapped_columns},
            ))
            self._set_stage(job_id, PipelineStage.NORMALISE)

            # -- stages 5-7: normalise, validate, quarantine ---------------
            prepared: dict[str, list[dict[str, Any]]] = {}
            for plan in plans:
                frame = load(plan.file.path, plan.file.detected_format)
                check_dimensions(frame.height, frame.width, plan.file.safe_name)

                rows, rejected = self._process_table(plan, frame, stats, entity_id)
                for key, hits in rejected.items():
                    quarantine.setdefault(key, []).extend(hits)

                n_dupes = self._count_duplicates(plan.table, rows)
                if n_dupes:
                    duplicates[plan.table] = n_dupes
                    collected_warnings.append(
                        f"{n_dupes} duplicate {plan.table} primary key(s); the first "
                        "occurrence of each was kept and the rest excluded"
                    )
                    rows = self._deduplicate(plan.table, rows)

                prepared[plan.table] = rows
                tables_loaded[plan.table] = len(rows)

            total_loaded = sum(tables_loaded.values())
            stage_log.append(StageOutcome(
                PipelineStage.NORMALISE, True,
                f"{total_loaded} row(s) normalised across {len(tables_loaded)} table(s)",
                dict(tables_loaded),
            ))

            n_quarantined = self._persist_quarantine(
                submission_id, entity_id, quarantine
            )
            stage_log.append(StageOutcome(
                PipelineStage.QUARANTINE, True,
                f"{n_quarantined} row(s) quarantined with reason",
                {"quarantined": n_quarantined},
            ))
            self._set_stage(job_id, PipelineStage.PSEUDONYMISE)

            # -- stage 8: pseudonymise --------------------------------------
            for plan in plans:
                self._pseudonymise_rows(prepared.get(plan.table, []), plan, entity_id)
            n_notes = self._count_notes()
            stage_log.append(StageOutcome(
                PipelineStage.PSEUDONYMISE, True,
                "analysts, IPs and hostnames pseudonymised; notes redacted and stored",
                {"notes": n_notes},
            ))
            self._set_stage(job_id, PipelineStage.LOAD)

            # -- stage 9: load ----------------------------------------------
            self._load_to_duckdb(prepared)
            n_versions = self._write_record_versions(submission_id, prepared)
            stage_log.append(StageOutcome(
                PipelineStage.LOAD, True,
                f"{total_loaded} row(s) written to the evidence lake, "
                f"{n_versions} record version(s) hashed",
                {"rows": total_loaded, "record_versions": n_versions},
            ))

            # -- cross-table integrity, feeding the consistency component -----
            dangling = self._check_dangling_refs(prepared)
            if dangling:
                collected_warnings.append(
                    "Some cross-table references did not resolve within this "
                    "submission: "
                    + "; ".join(f"{k} ({v})" for k, v in sorted(dangling.items()))
                )

            # -- DQ -----------------------------------------------------------
            dq = self._build_dq(
                submission, plans, stats, tables_loaded, quarantine,
                duplicates, dangling, collected_warnings,
            )
            self._persist_dq(submission_id, dq, tables_loaded)

            # -- stage 10: ledger ----------------------------------------------
            self.ledger.append(
                actor="ingestion_service",
                action=LedgerAction.RUN_COMPLETED,
                payload={
                    "kind": "ingestion",
                    "submission_id": submission_id,
                    "entity_id": entity_id,
                    "version": int(submission["version"]),
                    "rows_loaded": dq.n_rows_loaded,
                    "rows_quarantined": dq.n_rows_quarantined,
                    "dq_score": dq.dq_score,
                    "data_tier": dq.data_tier.value,
                    "dangling_references": dangling,
                },
                entity_id=entity_id,
            )

            status = JobStatus.COMPLETE if dq.n_rows_loaded else JobStatus.QUARANTINED
            stage_log.append(StageOutcome(
                PipelineStage.LEDGER, True,
                f"ledger appended; DQ {dq.dq_score:.3f}, tier {dq.data_tier.value}",
                {"dq_score": int(dq.dq_score * 1000)},
            ))
            self._finish_job(
                job_id, status, PipelineStage.LEDGER,
                n_loaded=dq.n_rows_loaded, n_quarantined=dq.n_rows_quarantined,
            )

            return IngestionResult(
                submission_id=submission_id,
                job_id=job_id,
                entity_id=entity_id,
                version=int(submission["version"]),
                status=status,
                dq=dq,
                stage_log=stage_log,
                mapping=self.mapping_for_submission(submission_id),
                n_loaded=dq.n_rows_loaded,
                n_quarantined=dq.n_rows_quarantined,
                warnings=collected_warnings,
            )

        except MappingRequired as exc:
            # A question for a human, not a failure. The job stays queued.
            self._finish_job(job_id, JobStatus.QUEUED, PipelineStage.MAP, error=str(exc))
            self.ledger.append(
                actor="ingestion_service",
                action=LedgerAction.MAPPING_PROPOSED,
                payload={
                    "submission_id": submission_id,
                    "columns": exc.columns,
                    "unmapped_required": exc.unmapped_required,
                    "detected_profile": exc.detected_profile,
                    "suggestions": [s.model_dump() for s in exc.suggestions],
                },
                entity_id=entity_id,
            )
            raise

        except Exception as exc:  # noqa: BLE001 - the outcome must be recorded
            log.exception("ingestion job %s failed", job_id)
            self._finish_job(
                job_id, JobStatus.FAILED, None,
                error=f"{type(exc).__name__}: {exc}",
            )
            self.ledger.append(
                actor="ingestion_service",
                action=LedgerAction.RUN_FAILED,
                payload={
                    "kind": "ingestion",
                    "submission_id": submission_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                entity_id=entity_id,
            )
            raise

    # ============================================== stage 4: mapping ==
    def _resolve_mappings(
        self,
        submission_id: str,
        entity_id: str,
        approved_column_maps: dict[str, dict[str, str]],
        severity_overrides: dict[str, dict[str, str]],
        warnings: list[str],
    ) -> list[TablePlan]:
        """Resolve an approved mapping for every staged file.

        Precedence: an explicit human approval, then a formally approved
        profile, then `MappingRequired`. There is no fuzzy fallback — the
        assistant proposes, a person decides.
        """
        files = self._staged_files(submission_id, entity_id)

        registered: dict[str, dict[str, Any]] = {}
        for row in self.assistant.approved_profiles():
            if row["active"]:
                try:
                    registered[row["profile_id"]] = json.loads(row["definition"])
                except (json.JSONDecodeError, TypeError):
                    log.warning(
                        "approved profile %s has an unreadable definition; ignoring it",
                        row["profile_id"],
                    )

        plans: list[TablePlan] = []
        pending_suggestions: list[MappingSuggestionOut] = []
        pending_missing: list[str] = []
        pending_columns: list[str] = []
        detected_profile_id: str | None = None

        # This submission's own approval, if it has one. Resolved once here
        # rather than per file: a submission approved yesterday must still
        # resolve today without the vendor profile happening to share its id.
        row = get_connection().execute(
            "SELECT approved_profile_id FROM submission WHERE submission_id = ?",
            (submission_id,),
        ).fetchone()
        submission_approval = (
            registered.get(row["approved_profile_id"])
            if row is not None and row["approved_profile_id"]
            else None
        )
        if (
            row is not None
            and row["approved_profile_id"]
            and submission_approval is None
        ):
            # The submission claims an approval that cannot be read back. Loading
            # the vendor proposal instead would quietly downgrade an examiner's
            # signed decision to an unapproved guess.
            raise IngestionError(
                f"Submission {submission_id} records approved mapping "
                f"{row['approved_profile_id']!r}, but that definition is missing or "
                "unreadable. Re-approve the mapping rather than loading on a guess."
            )

        for f in files:
            columns = self._peek_columns(f)
            if not columns:
                raise IngestionError(
                    f"{f.safe_name}: readable as {f.detected_format} but yielded no "
                    "columns, so nothing can be mapped"
                )
            pending_columns.extend(columns)

            table = self._table_for(f, columns)
            profile, profile_table, score = match_profile_table(columns)
            if detected_profile_id is None and profile is not None:
                detected_profile_id = profile.profile_id

            # When the profile match is confident, prefer its table block over
            # the filename/inference guess: a Splunk `cases` block tells us this
            # is a case export regardless of what the file is called.
            table_block = (
                profile_table
                if profile is not None and score >= PROFILE_MATCH_FLOOR
                and profile_table in TABLE_CONTRACTS
                else None
            )
            target_table = table_block or table

            source_for_target, definition, approved = self._mapping_for(
                f.safe_name, target_table, profile, score,
                approved_column_maps.get(f.safe_name), registered,
                submission_approval,
            )

            # A submission often carries several files, and a human approving
            # "this submission" naturally writes one map covering all of them.
            # So an entry that names a column this table lacks, or a source this
            # file lacks, is dropped with a warning rather than rejected -- the
            # dangerous case is a *required* column left unmapped, which
            # surfaces below as MappingRequired.
            known = set(RULES_BY_TABLE[target_table])
            present = set(columns)
            dropped: list[str] = []

            usable: dict[str, str] = {}
            for target, source in source_for_target.items():
                if target not in known:
                    dropped.append(f"{target} (not a column of {table})")
                    continue
                if source not in present:
                    dropped.append(f"{target} (source column {source!r} absent)")
                    continue
                usable[target] = source

            if dropped:
                warnings.append(
                    f"{plan_label(f)}: {len(dropped)} mapping entr(y/ies) not applicable "
                    f"to this file and were ignored: {'; '.join(dropped[:6])}"
                    + (" ..." if len(dropped) > 6 else "")
                )
            source_for_target = usable

            missing = [
                rule.name for rule in TABLE_CONTRACTS[target_table]
                if rule.required
                and not rule.injected
                and rule.name not in source_for_target
            ]

            if missing or not source_for_target:
                pending_missing.extend(missing)
                for suggestion in self.assistant.suggest(
                    columns, target_table, sample_values=self._sample_values(f)
                ):
                    pending_suggestions.append(MappingSuggestionOut(
                        source_column=suggestion.source_column,
                        suggested_target=suggestion.suggested_target,
                        confidence=suggestion.confidence,
                        sample_values=suggestion.sample_values,
                        reason=suggestion.reason,
                    ))

            # A file whose required columns are unmapped cannot load. A file
            # whose only unmapped columns are optional loads with the gaps
            # recorded, and assessability reports them per dimension.
            if missing:
                continue

            severity_map = dict(self.policy.severity_map)
            status_map = dict(self.policy.status_map)
            if definition:
                severity_map.update(definition.get("severity_map") or {})
                status_map.update(definition.get("status_map") or {})
            severity_map.update(severity_overrides.get(f.safe_name) or {})

            timezone_name = (
                (definition or {}).get("timezone")
                or (profile.timezone if profile else None)
                or str(self.policy.definition.get("timezone", "UTC"))
            )

            plans.append(TablePlan(
                table=target_table,
                file=f,
                columns=columns,
                source_for_target=source_for_target,
                profile_id=profile.profile_id if profile else None,
                vendor=profile.vendor if profile else None,
                timezone=timezone_name,
                severity_map=severity_map,
                status_map=status_map,
                approved=approved,
            ))

        if not plans:
            raise MappingRequired(
                submission_id,
                sorted(set(pending_columns)),
                sorted(set(pending_missing)),
                _dedupe_suggestions(pending_suggestions),
                detected_profile_id,
            )
        return plans

    def _mapping_for(
        self,
        safe_name: str,
        table: str,
        profile: MappingProfileSpec | None,
        score: float,
        explicit: dict[str, str] | None,
        registered: dict[str, dict[str, Any]],
        approved_definition: dict[str, Any] | None = None,
    ) -> tuple[dict[str, str], dict[str, Any] | None, bool]:
        """Resolve one file's mapping.

        Returns `(source_for_target, definition, approved)` where
        `source_for_target` is keyed by **canonical column name**. The API takes
        the opposite direction (`{source: target}`, which is what a human reads
        off the spreadsheet), so the flip happens here and nowhere else: one
        direction internally, one at the boundary.

        `approved_definition` is passed when the caller already knows which
        approval applies — a submission carries its own, keyed by a different
        id from the vendor profile it was detected as, so looking it up by
        profile id would miss it every time.
        """
        if explicit:
            # A human approval is authoritative, including where it contradicts
            # the vendor profile: CSEs rename columns without telling anyone,
            # and a profile edit must never rewrite an already-agreed mapping.
            return {target: source for source, target in explicit.items()}, None, True

        definition = approved_definition
        if definition is None and profile is not None and score >= PROFILE_MATCH_FLOOR:
            definition = registered.get(profile.profile_id)

        if definition is not None:
            source_for_target = _columns_for_file(definition, safe_name)
            # A profile usually covers several tables; keep only this one.
            return (
                {
                    target: source
                    for target, source in source_for_target.items()
                    if target in RULES_BY_TABLE[table]
                },
                definition,
                True,
            )

        # On disk but never formally approved. Its columns are a proposal, not
        # a decision, so they are offered and the approval is still required.
        if profile is None or score < PROFILE_MATCH_FLOOR:
            return {}, None, False
        try:
            proposal = {
                spec.target: spec.source
                for spec in profile.columns_for(table).values()
            }
        except KeyError as exc:
            # A profile block keyed something other than a canonical table name.
            # Refuse the file rather than load it with every column missing.
            raise IngestionError(str(exc).strip('"')) from exc
        return proposal, None, False

    def _table_for(self, f: SanitisedFile, columns: list[str]) -> str:
        """Destination table: filename hint, then inference from column names."""
        stem = f.safe_name.lower()
        # Longest name first, so `alert_record.csv` beats `alert_record_link.csv`.
        for table in sorted(TABLE_CONTRACTS, key=len, reverse=True):
            if table in stem:
                return table
        inferred = infer_table_name(columns)
        if inferred in TABLE_CONTRACTS:
            return inferred
        raise IngestionError(
            f"{f.safe_name}: cannot tell which evidence table this is. Columns were "
            f"{columns[:10]}. Name the file after its table (e.g. alerts.csv) or "
            "approve a mapping that names the table."
        )

    def _staged_files(self, submission_id: str, entity_id: str) -> list[SanitisedFile]:
        """Re-hydrate staged files from `submission_file`."""
        rows = get_connection().execute(
            "SELECT safe_name, detected_format, n_bytes FROM submission_file "
            "WHERE submission_id = ? ORDER BY ordinal",
            (submission_id,),
        ).fetchall()
        if not rows:
            raise IngestionError(
                f"No files recorded for submission {submission_id}; nothing to load"
            )

        staging_dir = _staging_dir(self.settings.data_dir, entity_id, submission_id)
        files: list[SanitisedFile] = []
        for row in rows:
            path = staging_dir / row["safe_name"]
            if not path.is_file():
                raise IngestionError(
                    f"Staged file {row['safe_name']} is missing from {staging_dir}. "
                    "The submission cannot be loaded; re-upload it."
                )
            files.append(SanitisedFile(
                original_name=row["safe_name"],
                safe_name=row["safe_name"],
                path=path,
                # Re-detected rather than trusted: the staged bytes are what will
                # actually be parsed, and `detected_format` is only a claim.
                detected_format=detect_format(path, declared=row["detected_format"]),
                n_bytes=path.stat().st_size,
            ))
        return files

    def _peek_columns(self, f: SanitisedFile) -> list[str]:
        try:
            return list(load(f.path, f.detected_format).columns)
        except Exception as exc:  # noqa: BLE001 - probing must never be fatal
            log.debug("column peek failed for %s: %s", f.safe_name, exc)
            return []

    def _sample_values(self, f: SanitisedFile) -> dict[str, list[str]]:
        try:
            frame = load(f.path, f.detected_format)
        except Exception:  # noqa: BLE001
            return {}
        return {
            column: [str(v) for v in frame[column].head(20).to_list()]
            for column in frame.columns
        }

    def _next_version(self, entity_id: str) -> int:
        row = get_connection().execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM submission WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        return int(row["v"]) + 1

    # -------------------------------------------- injection + derivation ==
    def _inject_and_derive(
        self, row: dict[str, Any], plan: TablePlan, entity_id: str
    ) -> list[str]:
        """Fill pipeline-owned columns. Mutates `row` in place.

        Three groups, and the distinction matters:

        * **injected** — the pipeline knows these and the submission cannot
          assert them. `entity_id` comes from the submission the row was
          accepted under; a row is not trusted to name its own entity.
        * **derived** — computed from values already present on the row, when
          those values are present. `severity_norm` is the canonical form of a
          severity the CSE *did* declare, and `investigation_duration_sec` is
          the gap between two timestamps it did declare. This is
          canonicalisation, not imputation: no new fact enters the data.
        * Everything else stays absent. A row with no closure time has no
          duration, and that absence is itself evidence.

        Returns the failure reasons found while deriving, so an unmapped
        severity reaches the quarantine stage instead of vanishing.
        """
        failures: list[str] = []

        if plan.table in {"alert_record", "case_record", "telemetry_daily",
                          "escalation"}:
            row["entity_id"] = entity_id
        if plan.table == "alert_record":
            row["source_tool"] = plan.vendor or "unknown"

        for rule in TABLE_CONTRACTS[plan.table]:
            if not rule.derived or row.get(rule.name) not in (None, ""):
                continue

            if rule.name == "timestamp":
                # v1 compatibility mirror of `event_ts`. Never the reverse.
                row["timestamp"] = row.get("event_ts")

            elif rule.name == "severity_norm":
                raw = row.get("severity_raw")
                if not raw:
                    continue
                mapped = normalise_severity(raw, plan.severity_map)
                row["severity_norm"] = mapped.value
                if mapped.failed:
                    # The severity was declared but is not in any vocabulary
                    # Orion knows. Quarantining beats defaulting it to MEDIUM,
                    # which would silently place the case in its peer cohort.
                    failures.append(mapped.reason or "unmapped severity")

            elif rule.name == "investigation_duration_sec":
                created, closed = row.get("created_at"), row.get("closed_at")
                if not (
                    isinstance(created, datetime) and isinstance(closed, datetime)
                ):
                    continue
                delta = (_aware(closed) - _aware(created)).total_seconds()
                # A negative duration means the export is wrong, not that the
                # case took no time. Refuse to invent a plausible positive one.
                row["investigation_duration_sec"] = int(delta) if delta >= 0 else None

        return failures

    # -------------------------------------------- stages 5-7: process a table --
    def _process_table(
        self,
        plan: TablePlan,
        frame: pl.DataFrame,
        stats: NormaliseStats,
        entity_id: str,
    ) -> tuple[list[dict[str, Any]], dict[tuple[str, str, str], list[tuple[str, int, str]]]]:
        """Normalise and validate one table into kept rows plus quarantine buckets.

        Per row, in order:
          1. normalise every mapped column (stage 5)
          2. inject pipeline-owned columns and derive computed ones
          3. check required fields, primary keys and fatal vocabularies (stage 6)

        Pseudonymous and redacted columns are carried through as *raw text*
        after step 1; stage 8 transforms them. That keeps a rejected row out of
        the pseudonymisation path entirely while still validating it.
        """
        rules = TABLE_CONTRACTS[plan.table]
        fatal_columns = QUARANTINE_ON_UNMAPPED.get(plan.table, frozenset())
        kept: list[dict[str, Any]] = []
        quarantine: dict[tuple[str, str, str], list[tuple[str, int, str]]] = {}

        for row_index, raw in enumerate(frame.iter_rows(named=True)):
            row: dict[str, Any] = {}
            failures: list[str] = []
            unmapped_failures: list[str] = []

            for rule in rules:
                # Injected columns are pipeline-owned and a mapping must not try
                # to supply them. Derived columns are usually pipeline-owned too,
                # but not always: `case_record.severity_norm` is derived from
                # `severity_raw` on an alert, yet a case export that has no raw
                # severity column supplies the canonical one directly. Honour an
                # explicit mapping when one exists, and derive only when it does
                # not.
                if rule.injected or (
                    rule.derived and rule.name not in plan.source_for_target
                ):
                    continue

                source_column = plan.source_for_target.get(rule.name)
                # Namespaced by table: `alert_record.case_id` and
                # `case_record.case_id` are different fields, and a shared
                # bucket made an alert's absent case_id look like a case's
                # empty one, blaming the CSE for a gap in their own export.
                stats_key = f"{plan.table}.{rule.name}"

                if source_column is None or source_column not in raw:
                    stats.observe(stats_key, missing_result())
                    if rule.required:
                        failures.append(
                            f"{rule.name} is required but is not present in this "
                            "submission's mapping"
                        )
                    continue

                value = raw[source_column]

                if rule.redact or rule.pseudonymous:
                    # Left raw for stage 8, but still counted for DQ so a gap in
                    # an analyst or note column is attributable.
                    result = normalise_text(value, stats, stats_key)
                    row[rule.name] = value if not result.absent else None
                    if rule.required and row[rule.name] is None:
                        failures.append(f"{rule.name} is required but empty")
                    continue

                normaliser = rule.normaliser(
                    plan.timezone, plan.severity_map, plan.status_map
                )
                result = self._apply(normaliser, value, stats, stats_key)
                row[rule.name] = result.value

                if result.failed:
                    reason = result.reason or f"invalid {rule.name}"
                    if rule.name in fatal_columns:
                        # An unmapped severity is fatal to the row: a case whose
                        # severity is unknown cannot be compared with its peers,
                        # and defaulting it would invent the comparison.
                        unmapped_failures.append(reason)
                    elif rule.name != "severity_norm":
                        stats.observe(stats_key, result)

            # -- stages 2: inject and derive -------------------------------
            derived_failures = self._inject_and_derive(row, plan, entity_id)
            unmapped_failures.extend(derived_failures)

            failures.extend(unmapped_failures)

            # -- stage 3: required fields ----------------------------------
            for rule in rules:
                if rule.injected or not rule.required:
                    continue
                if row.get(rule.name) in (None, ""):
                    message = f"{rule.name} is required but empty"
                    if message not in failures:
                        failures.append(message)

            # Primary keys.
            for key in PRIMARY_KEYS.get(plan.table, ()):
                if not row.get(key):
                    failures.append(f"primary key {key} is missing or empty")

            if failures:
                reason = "; ".join(dict.fromkeys(failures))[:500]
                stage = (
                    PipelineStage.NORMALISE.value
                    if any(
                        token in reason
                        for token in ("unmapped", "malformed", "non-finite", "ambiguous")
                    )
                    else PipelineStage.VALIDATE.value
                )
                quarantine.setdefault((plan.table, stage, reason), []).append(
                    (plan.file.safe_name, row_index,
                     json.dumps(_safe_raw(raw), default=str))
                )
                continue

            row["_source_row"] = row_index
            row["_source_file"] = plan.file.safe_name
            kept.append(row)

        return kept, quarantine

    @staticmethod
    def _apply(
        normaliser: Any, value: Any, stats: NormaliseStats, column: str
    ) -> Normalised:
        """Call a normaliser, threading `stats` only if it accepts them.

        The scalar normalisers in `normalisation.py` take optional stats; the
        per-rule closures do not. Inspecting once per table rather than once per
        cell keeps the hot loop free of reflection.
        """
        if _accepts_stats(normaliser):
            return normaliser(value, stats, column)
        return normaliser(value)

    # ============================================== stage 8: pseudonymise ==
    def _pseudonymise_rows(
        self, rows: list[dict[str, Any]], plan: TablePlan, entity_id: str
    ) -> None:
        """HMAC pseudonyms in, redacted notes out. Mutates `rows` in place.

        Runs after validation so a rejected row is never pseudonymised — it is
        quarantined with its raw contents for the examiner, inside the trust
        boundary, and only retained rows cross into the evidence lake.
        """
        ps = self.pseudonymiser
        rules = {rule.name: rule for rule in TABLE_CONTRACTS[plan.table]}

        # A hostname declared in this submission's own case notes is more
        # reliable than a generic pattern, so collect declared destinations first.
        declared_hosts = {
            str(row["destination"]).strip()
            for row in rows
            if row.get("destination") and clean(row["destination"])
        }

        for row in rows:
            for column, rule in rules.items():
                if rule.pseudonymous:
                    raw = row.get(column)
                    if raw is None or str(raw).strip() == "":
                        row[column] = None
                        continue
                    row[column] = ps.pseudonym(
                        str(raw), entity_id,
                        rule.pseudonymous,  # type: ignore[arg-type]
                    )
                elif rule.redact:
                    raw = row.get(column)
                    if raw is None or str(raw).strip() == "":
                        row[column] = None
                        continue
                    row[column] = self._store_note(
                        str(raw), entity_id, row, plan, declared_hosts
                    )

    def _store_note(
        self,
        raw: str,
        entity_id: str,
        row: dict[str, Any],
        plan: TablePlan,
        declared_hosts: set[str],
    ) -> str:
        """Redact, sign and store one note. Returns its `note_ref`.

        The shingle signature is computed on the *redacted* text. Computing it on
        the original would make the signature a fingerprint of text the
        evidence lake is not allowed to hold, which is the same leak with extra
        steps.
        """
        ps = self.pseudonymiser
        redaction = ps.redact_text(raw, entity_asset_names=declared_hosts or None)
        note_ref = _new_id("note")
        key_field = "case_id" if "case_id" in row else "alert_id"
        actor_value = (
            row.get("analyst_pseudo")
            or row.get("actor_pseudo")
            or row.get("closed_by_pseudo")
        )

        get_connection().execute(
            "INSERT OR REPLACE INTO note_store (note_ref, entity_id, analyst_pseudo, "
            "redacted_text, shingle_signature, length) VALUES (?, ?, ?, ?, ?, ?)",
            (
                note_ref,
                entity_id,
                actor_value,
                redaction.redacted_text,
                ps.shingle_signature(redaction.redacted_text),
                len(raw),
            ),
        )
        if redaction.redaction_count:
            log.debug(
                "note %s redacted %d item(s) (%s)",
                note_ref,
                redaction.redaction_count,
                ",".join(redaction.categories),
            )
        return note_ref

    def _count_notes(self) -> int:
        row = get_connection().execute(
            "SELECT COUNT(*) AS n FROM note_store"
        ).fetchone()
        return int(row["n"])

    # ================================================= stage 9: load ==
    def _load_to_duckdb(self, prepared: dict[str, list[dict[str, Any]]]) -> None:
        non_empty = {t: rows for t, rows in prepared.items() if rows}
        if not non_empty:
            return

        with get_duckdb().writer() as conn:
            for table, rows in non_empty.items():
                rules = TABLE_CONTRACTS[table]
                column_names = [rule.name for rule in rules]
                dtypes = {rule.name: _polars_dtype(rule) for rule in rules}
                select_list = ", ".join(f'"{name}"' for name in column_names)

                for start in range(0, len(rows), INSERT_CHUNK):
                    chunk = rows[start : start + INSERT_CHUNK]
                    frame = pl.DataFrame(
                        {
                            name: [_coerce(row.get(name), dtypes[name])
                                   for row in chunk]
                            for name in column_names
                        },
                        schema=dtypes,
                    )
                    conn.register("ingest_batch", frame)
                    try:
                        conn.execute(
                            f'INSERT INTO "{table}" ({select_list}) '
                            f"SELECT {select_list} FROM ingest_batch"
                        )
                    finally:
                        conn.unregister("ingest_batch")

    # ---------------------------------------------------- record versions --
    def _write_record_versions(
        self, submission_id: str, prepared: dict[str, list[dict[str, Any]]]
    ) -> int:
        """Per-record hash, backing EG-11 (retroactive edits) and EG-17 (deletions).

        The hash covers *canonical* values, not source bytes, so a resubmission
        that reorders columns or reformats a date hashes identically. That is
        what makes "did this record change since last period?" answerable, rather
        than merely "is this file byte-identical?".

        Columns whose stored value is *generated by the pipeline* are the
        exception, and getting this wrong makes EG-11 fire on everything.
        `case_record.note_ref` is a fresh `note_...` id on every load, so hashing
        it directly meant every case looked retroactively edited on every
        resubmission. An indicator that always fires is as useless as one that
        never fires, and worse, it teaches the examiner to ignore the card. The
        note's *content* is stable even though its reference is not, so the
        reference is replaced by a digest of the redacted text before hashing --
        which keeps the signal, since rewriting a justification still moves it,
        while a reload of unchanged text does not.
        """
        rows: list[tuple[str, str, str, str]] = []
        note_digests = self._note_content_digests(prepared)

        for table, records in prepared.items():
            keys = PRIMARY_KEYS.get(table, ())
            content_columns = [
                rule.name for rule in TABLE_CONTRACTS[table]
                if not rule.injected and not rule.pseudonymous and rule.name not in keys
            ]
            generated_columns = {
                rule.name for rule in TABLE_CONTRACTS[table] if rule.redact
            }
            for record in records:
                key_value = "|".join(str(record.get(k) or "") for k in keys)
                payload = json.dumps(
                    {
                        name: (
                            note_digests.get(str(record.get(name) or ""))
                            if name in generated_columns
                            else _hashable(record.get(name))
                        )
                        for name in content_columns
                    },
                    sort_keys=True,
                    default=str,
                )
                rows.append((
                    f"{table}:{key_value}",
                    submission_id,
                    hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                    json.dumps({k: record.get(k) for k in keys}, default=str),
                ))

        if rows:
            with transaction() as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO record_version "
                    "(record_key, submission_id, record_hash, key_field_snapshot) "
                    "VALUES (?, ?, ?, ?)",
                    rows,
                )
        return len(rows)

    def _note_content_digests(
        self, prepared: dict[str, list[dict[str, Any]]]
    ) -> dict[str, str]:
        """`note_ref -> digest of redacted text`, for stable record hashing.

        Returns a digest for every note referenced by this submission, including
        the empty one, so a record whose note reference is dangling hashes the
        same way every time instead of hashing the literal reference string.
        """
        refs: set[str] = set()
        for table, records in prepared.items():
            generated = {
                rule.name for rule in TABLE_CONTRACTS[table] if rule.redact
            }
            for record in records:
                for column in generated:
                    value = record.get(column)
                    if value:
                        refs.add(str(value))
        if not refs:
            return {}

        digests: dict[str, str] = {ref: "" for ref in refs}
        # Chunked to stay under SQLite's variable limit on a large submission.
        refs_sorted = sorted(refs)
        for start in range(0, len(refs_sorted), 400):
            chunk = refs_sorted[start : start + 400]
            placeholders = ", ".join("?" * len(chunk))
            for row in get_connection().execute(
                f"SELECT note_ref, redacted_text FROM note_store "
                f"WHERE note_ref IN ({placeholders})",
                chunk,
            ):
                digests[str(row["note_ref"])] = hashlib.sha256(
                    str(row["redacted_text"] or "").encode("utf-8")
                ).hexdigest()
        return digests

    def _check_dangling_refs(
        self, prepared: dict[str, list[dict[str, Any]]]
    ) -> dict[str, int]:
        """Referential integrity within the submission.

        Only references whose target is *expected* in this submission are
        checked. Absence alone is not a defect — an alert may legitimately point
        at a case from a previous period — so unresolved references are counted
        and fed to the consistency component, never used to reject a row.
        """
        case_ids = {
            r["case_id"] for r in prepared.get("case_record", []) if r.get("case_id")
        }
        alert_ids = {
            r["alert_id"] for r in prepared.get("alert_record", []) if r.get("alert_id")
        }

        dangling: dict[str, int] = {}
        if case_ids:
            unresolved = sum(
                1 for r in prepared.get("alert_record", [])
                if r.get("case_id") and r["case_id"] not in case_ids
            )
            if unresolved:
                dangling["alert_record.case_id absent from submitted case_record"] = unresolved

            unresolved = sum(
                1 for r in prepared.get("escalation", [])
                if r.get("case_id") and r["case_id"] not in case_ids
            )
            if unresolved:
                dangling["escalation.case_id absent from submitted case_record"] = unresolved

        if alert_ids:
            unresolved = sum(
                1 for r in prepared.get("alert_case", [])
                if r.get("alert_id") and r["alert_id"] not in alert_ids
            )
            if unresolved:
                dangling["alert_case.alert_id absent from submitted alert_record"] = unresolved

        return dangling

    # ============================================ stage 7: quarantine ==
    def _persist_quarantine(
        self,
        submission_id: str,
        entity_id: str,
        quarantine: dict[tuple[str, str, str], list[tuple[str, int, str]]],
    ) -> int:
        if not quarantine:
            return 0
        created = _now().isoformat()
        rows = [
            (
                _new_id("qtn"),
                submission_id,
                entity_id,
                table,
                source_file,
                source_row,
                stage,
                reason,
                raw,
                created,
            )
            for (table, stage, reason), hits in quarantine.items()
            for source_file, source_row, raw in hits
        ]
        with transaction() as conn:
            conn.executemany(
                "INSERT INTO quarantine (quarantine_id, submission_id, entity_id, "
                "source_table, source_file, source_row, failed_stage, reason, "
                "raw_payload, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    # ==================================================== DQ assembly ==
    def _build_dq(
        self,
        submission: Any,
        plans: list[TablePlan],
        stats: NormaliseStats,
        tables_loaded: dict[str, int],
        quarantine: dict[tuple[str, str, str], list[tuple[str, int, str]]],
        duplicates: dict[str, int],
        dangling: dict[str, int],
        warnings: list[str],
    ) -> DQReportOut:
        by_stage: dict[str, dict[str, int]] = {}
        samples: dict[str, list[str]] = {}
        for (table, stage, reason), hits in quarantine.items():
            by_stage.setdefault(stage, {})
            by_stage[stage][reason] = by_stage[stage].get(reason, 0) + len(hits)
            samples.setdefault(stage, []).extend(
                f"{table}:row-{row_index}" for _, row_index, _ in hits[:5]
            )

        dq_warnings = list(warnings)
        if stats.sanitised_cells:
            dq_warnings.append(
                f"{stats.sanitised_cells} cell(s) began with a spreadsheet formula "
                "character (=, +, -, @) and were prefixed with an apostrophe. If the "
                "original characters matter, read the raw file rather than an export."
            )

        required_columns = {
            # Injected and derived columns are excluded: the pipeline fills them
            # unconditionally, so counting them would report a permanently
            # absent column and depress completeness for a field the examiner
            # never had to supply.
            table: [
                rule.name for rule in TABLE_CONTRACTS[table]
                if rule.required and not rule.injected and not rule.derived
            ]
            for table in tables_loaded
        }

        total_columns = max(1, len(stats.per_column_seen))
        approved_columns = sum(
            len(plan.source_for_target) for plan in plans if plan.approved
        )

        inputs = dq_scoring.DQInputs(
            submission_id=submission["submission_id"],
            entity_id=submission["entity_id"],
            period_start=date.fromisoformat(submission["period_start"]),
            period_end=date.fromisoformat(submission["period_end"]),
            received_ts=datetime.fromisoformat(submission["received_ts"]),
            tables_loaded=tables_loaded,
            tables_present=set(tables_loaded),
            stats=stats,
            n_quarantined=sum(len(v) for v in quarantine.values()),
            quarantine_by_stage=by_stage,
            quarantine_samples=samples,
            duplicate_primary_keys=duplicates,
            dangling_references=dangling,
            latest_evidence_ts=self._latest_evidence_ts(plans, tables_loaded),
            mapping_profile_id=next(
                (p.profile_id for p in plans if p.approved and p.profile_id), None
            ),
            approved_column_count=min(total_columns, approved_columns),
            total_column_count=total_columns,
            warnings=dq_warnings,
        )
        return dq_scoring.build_dq_report(
            inputs, self.policy.definition["dq_components"], required_columns
        )

    def _latest_evidence_ts(
        self, plans: list[TablePlan], tables_loaded: dict[str, int]
    ) -> datetime | None:
        """Newest timestamp anywhere in the prepared rows.

        Read from the staged frames rather than the DuckDB lake: this runs
        before the load is committed conceptually, and querying the lake for
        "the newest thing in this submission" would need a submission tag the
        evidence tables do not carry.
        """
        latest: datetime | None = None
        for plan in plans:
            if not tables_loaded.get(plan.table):
                continue
            timestamp_columns = [
                rule.name for rule in TABLE_CONTRACTS[plan.table]
                if rule.kind in {"timestamp", "date"}
                and rule.name in plan.source_for_target
            ]
            if not timestamp_columns:
                continue
            try:
                frame = load(plan.file.path, plan.file.detected_format)
            except Exception:  # noqa: BLE001
                continue
            for column in timestamp_columns:
                source_column = plan.source_for_target[column]
                if source_column not in frame.columns:
                    continue
                from app.services.normalisation import normalise_timestamp

                for value in frame[source_column].to_list():
                    result = normalise_timestamp(value, plan.timezone)
                    if isinstance(result.value, datetime):
                        candidate = result.value
                        if candidate.tzinfo is None:
                            candidate = candidate.replace(tzinfo=timezone.utc)
                        if latest is None or candidate > latest:
                            latest = candidate
        return latest

    def _persist_dq(
        self,
        submission_id: str,
        dq: DQReportOut,
        tables_loaded: dict[str, int] | None = None,
    ) -> None:
        """Persist the DQ verdict and the per-table row counts.

        `tables_loaded` is stored because `GET /submissions/{id}/quarantine` and
        the DQ reconstruction both need it. Without it, a later read could report
        a quarantine count but not what survived it, which is exactly the
        question an examiner asks first.
        """
        with transaction() as conn:
            conn.execute(
                "UPDATE submission SET dq_score = ?, row_counts = ? "
                "WHERE submission_id = ?",
                (
                    dq.dq_score,
                    json.dumps({
                        "rows_loaded": dq.n_rows_loaded,
                        "rows_quarantined": dq.n_rows_quarantined,
                        "data_tier": dq.data_tier.value,
                        "tables_loaded": tables_loaded or {},
                        "components": {c.component: c.score for c in dq.components},
                    }),
                    submission_id,
                ),
            )

    # ===================================================== job state ==
    def _finish_job(
        self,
        job_id: str,
        status: JobStatus,
        stage: PipelineStage | None,
        error: str | None = None,
        n_loaded: int = 0,
        n_quarantined: int = 0,
    ) -> None:
        with transaction() as conn:
            conn.execute(
                "UPDATE ingestion_job SET status = ?, stage = ?, finished_ts = ?, "
                "error = ?, n_loaded = ?, n_quarantined = ? WHERE job_id = ?",
                (
                    status.value,
                    stage.value if stage else None,
                    _now().isoformat(),
                    error,
                    n_loaded,
                    n_quarantined,
                    job_id,
                ),
            )

    def _set_stage(self, job_id: str, stage: PipelineStage) -> None:
        with transaction() as conn:
            conn.execute(
                "UPDATE ingestion_job SET stage = ? WHERE job_id = ?",
                (stage.value, job_id),
            )

    def _count_duplicates(self, table: str, rows: list[dict[str, Any]]) -> int:
        keys = PRIMARY_KEYS.get(table, ())
        if not keys:
            return 0
        seen: set[tuple] = set()
        duplicates = 0
        for record in rows:
            key = tuple(record.get(k) for k in keys)
            if key in seen:
                duplicates += 1
            else:
                seen.add(key)
        return duplicates

    def _deduplicate(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep the first of each duplicate primary key.

        First-wins, not last-wins, so a resubmission that merely reorders rows
        does not change which record is canonical. The discarded rows are not
        silently dropped: they are counted in the DQ uniqueness component and
        surfaced as a warning.
        """
        keys = PRIMARY_KEYS.get(table, ())
        seen: set[tuple] = set()
        unique: list[dict[str, Any]] = []
        for record in rows:
            key = tuple(record.get(k) for k in keys)
            if key in seen:
                continue
            seen.add(key)
            unique.append(record)
        return unique

    # ====================================================== read side ==
    def mapping_for_submission(self, submission_id: str) -> MappingOut:
        """`GET /submissions/{id}/mapping` — the resolved mapping plus suggestions."""
        conn = get_connection()
        submission = conn.execute(
            "SELECT * FROM submission WHERE submission_id = ?", (submission_id,)
        ).fetchone()
        if submission is None:
            raise IngestionError(f"No such submission: {submission_id}")

        files = conn.execute(
            "SELECT safe_name FROM submission_file WHERE submission_id = ? "
            "ORDER BY ordinal",
            (submission_id,),
        ).fetchall()
        first = self._staged_files(submission_id, submission["entity_id"])[0] \
            if files else None

        suggestions: list[MappingSuggestionOut] = []
        column_map: dict[str, str] = {}
        unmapped: list[str] = []
        detected_profile = submission["schema_profile"]
        vendor: str | None = None
        version: str | None = None

        if first is not None:
            columns = self._peek_columns(first)
            table = self._table_for(first, columns) if columns else "alert_record"
            profile, profile_table, score = (
                match_profile_table(columns) if columns else (None, None, 0.0)
            )
            vendor = None
            version = None
            if profile is not None and score >= PROFILE_MATCH_FLOOR:
                detected_profile = profile.profile_id
                vendor = profile.vendor
                version = profile.version
                if profile_table in TABLE_CONTRACTS:
                    table = profile_table

            approved_by = None
            # This submission's own approval, whose id is deliberately not the
            # vendor profile id. Looking it up under the detected vendor profile
            # reported "not approved" for a mapping a supervisor signed off.
            approved_id = submission["approved_profile_id"]
            registered = self._registered_definitions()
            approval = (
                registered.get(approved_id) if approved_id else None
            )
            if approval is not None:
                approved_by = approval.get("approved_by")
                detected_profile = submission["schema_profile"] or detected_profile

            try:
                source_for_target, _definition, approved = self._mapping_for(
                    first.safe_name, table, profile, score, None,
                    registered, approval,
                )
            except IngestionError:
                source_for_target, approved = {}, False
            # `MappingOut.column_map` is `{source: target}` — the direction the
            # frontend renders and the approval endpoint accepts. Internally we
            # work the other way, so it is flipped back here.
            column_map = {source: target for target, source in source_for_target.items()}
            unmapped = [
                rule.name for rule in TABLE_CONTRACTS[table]
                if rule.required and not rule.injected
                and rule.name not in source_for_target
            ]
            if not approved:
                suggestions = [
                    MappingSuggestionOut(
                        source_column=s.source_column,
                        suggested_target=s.suggested_target,
                        confidence=s.confidence,
                        sample_values=s.sample_values,
                        reason=s.reason,
                    )
                    for s in self.assistant.suggest(
                        columns, table, sample_values=self._sample_values(first)
                    )
                ]

            self._last_approved_by = approved_by

        return MappingOut(
            submission_id=submission_id,
            detected_profile=detected_profile,
            vendor=vendor,
            profile_version=version,
            approved=bool(column_map) and not unmapped,
            approved_by=getattr(self, "_last_approved_by", None),
            timezone=self.policy.definition.get("timezone"),
            column_map=column_map,
            severity_map=self.policy.severity_map,
            status_map=self.policy.status_map,
            suggestions=suggestions,
            unmapped_columns=unmapped,
            warnings=(
                [f"{len(unmapped)} required column(s) are unmapped; this submission "
                 "cannot load until a mapping is approved"]
                if unmapped else []
            ),
        )

    def _registered_definitions(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in self.assistant.approved_profiles():
            if not row["active"]:
                continue
            try:
                out[row["profile_id"]] = json.loads(row["definition"])
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def approve_mapping(
        self,
        submission_id: str,
        column_map: dict[str, str] | dict[str, dict[str, str]],
        severity_map: dict[str, str] | None = None,
        status_map: dict[str, str] | None = None,
        timezone_name: str | None = None,
        actor: str = "unknown",
        notes: str | None = None,
        profile_id: str | None = None,
    ) -> str:
        """`POST /submissions/{id}/mapping/approve`. Ledgered.

        The approved mapping is stored against the *submission*, not against the
        vendor profile: two CSEs on the same vendor can and do export different
        column names, and a profile edit must never silently rewrite a mapping a
        human already agreed to.

        `column_map` may be flat (`{source: target}`, one mapping for the whole
        submission) or keyed by staged filename (`{filename: {source: target}}`).
        The per-file form exists because a flat map cannot express a real case:
        an alert export and a case export both carrying a `notes` column, where
        the alert's free-text rule description is not the same fact as the
        case's investigation note. Flattening those into one entry would have to
        pick one, and the other file would load with the field silently missing.
        When the flat form is used it is also stored per file, so the read side
        never has two shapes to reason about.
        """
        conn = get_connection()
        submission = conn.execute(
            "SELECT * FROM submission WHERE submission_id = ?", (submission_id,)
        ).fetchone()
        if submission is None:
            raise IngestionError(f"No such submission: {submission_id}")

        if not column_map:
            raise IngestionError(
                "An empty column map cannot be approved. Approve at least the "
                "primary key and the fields the indicators need."
            )

        by_file, flat = _split_column_map(column_map, submission_id)
        if not any(by_file.values()):
            raise IngestionError(
                "An empty column map cannot be approved. Approve at least the "
                "primary key and the fields the indicators need."
            )

        base_profile = submission["schema_profile"] or "adhoc"
        vendor = base_profile.split("_alerts_")[0].split("_")[0] or "adhoc"

        definition = {
            "submission_id": submission_id,
            "vendor": vendor,
            "version": "1",
            # The approver is carried inside the definition so the read side can
            # answer "who approved this?" without a second table join.
            "approved_by": actor,
            "approved_at": _now().isoformat(),
            # Canonical direction: `{canonical: {source: ...}}`.
            "columns_by_file": by_file,
            # Merged flat view for display. When the approver gave a per-file
            # map, a target present in two files under different sources cannot
            # be merged, so it is left out here rather than one file winning by
            # dict order. `columns_by_file` is authoritative.
            "columns": dict(_merge_flat(by_file)),
            "severity_map": severity_map or {},
            "status_map": status_map or {},
            "timezone": timezone_name or self.policy.definition.get("timezone", "UTC"),
        }
        profile_id = profile_id or f"{base_profile}__{submission_id}"

        # Recorded on the submission so the read side can find this approval
        # without guessing at the profile id, and so a later vendor-profile edit
        # cannot silently re-point a mapping a person already agreed to.
        with transaction() as conn:
            conn.execute(
                "UPDATE submission SET approved_profile_id = ? WHERE submission_id = ?",
                (profile_id, submission_id),
            )

        if notes:
            log.info("mapping %s approved by %s: %s", profile_id, actor, notes)

        # One ledger entry, carrying the full decision.
        return self.assistant.register_approved(
            profile_id,
            definition,
            approved_by=actor,
            ledger_payload={
                "submission_id": submission_id,
                "profile_id": profile_id,
                "vendor": vendor,
                "columns_by_file": by_file,
                "severity_map": severity_map or {},
                "status_map": status_map or {},
                "timezone": definition["timezone"],
                "notes": notes,
            },
        )

    def dq_report(self, submission_id: str) -> DQReportOut:
        """`GET /submissions/{id}` — reconstruct the DQ report from stored state."""
        conn = get_connection()
        submission = conn.execute(
            "SELECT * FROM submission WHERE submission_id = ?", (submission_id,)
        ).fetchone()
        if submission is None:
            raise IngestionError(f"No such submission: {submission_id}")

        quarantine_rows = conn.execute(
            "SELECT source_table, failed_stage, reason, source_row FROM quarantine "
            "WHERE submission_id = ?",
            (submission_id,),
        ).fetchall()

        by_stage: dict[str, dict[str, int]] = {}
        samples: dict[str, list[str]] = {}
        for row in quarantine_rows:
            by_stage.setdefault(row["failed_stage"], {})
            key = row["reason"]
            by_stage[row["failed_stage"]][key] = by_stage[row["failed_stage"]].get(key, 0) + 1
            samples.setdefault(row["failed_stage"], []).append(
                f"{row['source_table']}:row-{row['source_row']}"
            )

        row_counts = json.loads(submission["row_counts"] or "{}")
        tables_loaded = row_counts.get("tables_loaded") or {}

        stats = NormaliseStats()
        inputs = dq_scoring.DQInputs(
            submission_id=submission_id,
            entity_id=submission["entity_id"],
            period_start=date.fromisoformat(submission["period_start"]),
            period_end=date.fromisoformat(submission["period_end"]),
            received_ts=datetime.fromisoformat(submission["received_ts"]),
            tables_loaded=tables_loaded,
            tables_present=set(tables_loaded),
            stats=stats,
            n_quarantined=len(quarantine_rows),
            quarantine_by_stage=by_stage,
            quarantine_samples=samples,
            duplicate_primary_keys={},
            dangling_references={},
            latest_evidence_ts=None,
            mapping_profile_id=submission["schema_profile"],
            approved_column_count=0,
            total_column_count=0,
            warnings=[],
        )
        report = dq_scoring.build_dq_report(
            inputs,
            self.policy.definition["dq_components"],
            {
                t: [
                    r.name for r in TABLE_CONTRACTS[t]
                    if r.required and not r.injected and not r.derived
                ]
                for t in tables_loaded
            },
        )
        # The stored score is authoritative; reconstruction after the fact cannot
        # see the original frame, so per-cell counters are gone by this point.
        if submission["dq_score"] is not None:
            report = report.model_copy(update={"dq_score": float(submission["dq_score"])})
        return report

    def submission_out(self, submission_id: str) -> SubmissionOut:
        conn = get_connection()
        row = conn.execute(
            "SELECT s.*, e.name AS entity_name FROM submission s "
            "JOIN entity e ON e.entity_id = s.entity_id "
            "WHERE s.submission_id = ?",
            (submission_id,),
        ).fetchone()
        if row is None:
            raise IngestionError(f"No such submission: {submission_id}")
        counts = json.loads(row["row_counts"] or "{}")
        return SubmissionOut(
            submission_id=row["submission_id"],
            entity_id=row["entity_id"],
            entity_name=row["entity_name"],
            period_start=row["period_start"],
            period_end=row["period_end"],
            received_ts=row["received_ts"],
            manifest_hash=row["manifest_hash"],
            schema_profile=row["schema_profile"],
            row_counts=counts.get("tables_loaded") or {},
            declared_kpis=json.loads(row["declared_kpis"]) if row["declared_kpis"] else None,
            dq_score=row["dq_score"],
            data_tier=DataTier(counts["data_tier"]) if counts.get("data_tier") else None,
            version=int(row["version"]),
        )

    def quarantine_rows(
        self, submission_id: str, stage: PipelineStage | None = None, limit: int = 200
    ) -> list[QuarantineRowOut]:
        """`GET /submissions/{id}/quarantine` — rule 4, made visible."""
        clauses = ["submission_id = ?"]
        params: list[Any] = [submission_id]
        if stage is not None:
            clauses.append("failed_stage = ?")
            params.append(stage.value)
        params.append(limit)
        rows = get_connection().execute(
            f"SELECT * FROM quarantine WHERE {' AND '.join(clauses)} "
            "ORDER BY failed_stage, source_table, source_row LIMIT ?",
            params,
        ).fetchall()
        return [
            QuarantineRowOut(
                quarantine_id=row["quarantine_id"],
                source_table=row["source_table"],
                source_file=row["source_file"],
                source_row=row["source_row"],
                failed_stage=PipelineStage(row["failed_stage"]),
                reason=row["reason"],
                raw_payload=row["raw_payload"],
                created_ts=row["created_ts"],
            )
            for row in rows
        ]


# ================================================================= helpers ==
def _accepts_stats(fn: Any) -> bool:
    """Does this normaliser accept `(value, stats, column)`?

    Checked once per table and memoised by `_apply`'s callers, not per cell.
    """
    try:
        parameters = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return False
    positional = [
        p for p in parameters
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(p.kind is p.VAR_POSITIONAL for p in parameters)
    return has_varargs or len(positional) >= 3


def _dedupe_suggestions(
    suggestions: list[MappingSuggestionOut],
) -> list[MappingSuggestionOut]:
    """One suggestion per source column, highest confidence wins."""
    best: dict[str, MappingSuggestionOut] = {}
    for suggestion in suggestions:
        current = best.get(suggestion.source_column)
        if current is None or suggestion.confidence > current.confidence:
            best[suggestion.source_column] = suggestion
    return sorted(
        best.values(), key=lambda s: s.confidence, reverse=True
    )


_POLARS_CACHE: dict[str, Any] = {
    "pl.String": pl.String,
    "pl.Int64": pl.Int64,
    "pl.Float64": pl.Float64,
    "pl.Boolean": pl.Boolean,
    "pl.Date": pl.Date,
    'pl.Datetime("us")': pl.Datetime("us"),
}


def _polars_dtype(rule: ColumnRule) -> Any:
    return _POLARS_CACHE.get(rule.polars_type, pl.String)


def _coerce(value: Any, dtype: Any) -> Any:
    """Force one value into a declared polars type, or `None`.

    A strict schema with a wrong-typed value raises inside DuckDB, which aborts
    the entire load and quarantines nothing. Coercing to `None` loses the cell,
    but the load completes and the gap is counted rather than catastrophic.
    """
    if value is None:
        return None
    try:
        if dtype is pl.Boolean:
            return bool(value)
        if dtype is pl.Int64:
            return int(value)
        if dtype is pl.Float64:
            return float(value)
        if dtype is pl.Date:
            if isinstance(value, datetime):
                return value.date()
            return date.fromisoformat(str(value)[:10])
        if dtype == pl.Datetime("us"):
            if isinstance(value, datetime):
                aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
                return aware.astimezone(timezone.utc).replace(tzinfo=None)
            return datetime.fromisoformat(str(value))
        return str(value)
    except (TypeError, ValueError, OverflowError):
        return None


_service: IngestionService | None = None


def get_ingestion_service() -> IngestionService:
    global _service
    if _service is None:
        _service = IngestionService()
    return _service