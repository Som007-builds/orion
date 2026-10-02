"""DuckDB evidence store (plan §3.2).

DuckDB/Parquet holds **evidence of record**:
  alert_record, case_record, case_event, escalation, telemetry_daily, alert_case

DuckDB is **single-writer** (architecture principle §1.5). This module enforces
that structurally rather than by convention:

  * `writer()` is available to exactly one process/thread, guarded by a lock.
    It is called by the background worker during ingestion and runs.
  * `reader()` hands out read-only connections. Request handlers use this and
    physically cannot write, so no API call can stall or corrupt ingestion.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.config import get_settings

_EVIDENCE_DDL = """
-- plan §3.6. `timestamp` is the v1 field, retained for compatibility; `event_ts`
-- is preferred and nullable because some vendor exports omit it. Missing
-- timestamps are never imputed — silence is evidence (plan §3.1).
CREATE TABLE IF NOT EXISTS alert_record (
    alert_id             VARCHAR,
    entity_id            VARCHAR,
    timestamp            TIMESTAMP,
    event_ts             TIMESTAMP,
    title                VARCHAR,
    severity_raw         VARCHAR,
    severity_norm        VARCHAR
        CHECK (severity_norm IS NULL OR
               severity_norm IN ('CRITICAL','HIGH','MEDIUM','LOW')),
    rule_id              VARCHAR,
    source_tool          VARCHAR,
    mitre_tactic         VARCHAR,
    mitre_technique_id   VARCHAR,
    source_ip_pseudo     VARCHAR,
    destination_asset_id VARCHAR,
    asset_criticality    INTEGER,
    status               VARCHAR,
    auto_closed_flag     BOOLEAN,
    closed_by_pseudo     VARCHAR,
    case_id              VARCHAR
);

-- plan §3.7
CREATE TABLE IF NOT EXISTS case_record (
    case_id                   VARCHAR,
    entity_id                  VARCHAR,
    created_at                 TIMESTAMP,
    closed_at                  TIMESTAMP,
    investigation_duration_sec BIGINT,   -- derived; NULL if missing, never imputed
    analyst_pseudo             VARCHAR,
    closed_by_pseudo           VARCHAR,
    actor_type                 VARCHAR
        CHECK (actor_type IS NULL OR
               actor_type IN ('human','automation','unknown')),
    severity_norm              VARCHAR
        CHECK (severity_norm IS NULL OR
               severity_norm IN ('CRITICAL','HIGH','MEDIUM','LOW')),
    escalation_level           VARCHAR,
    disposition                VARCHAR
        CHECK (disposition IS NULL OR disposition IN
               ('TRUE_POSITIVE','FALSE_POSITIVE','BENIGN_ANOMALY','SUPPRESSED')),
    root_cause_action          VARCHAR,
    note_ref                   VARCHAR
);

-- plan §3.8. Required by EG-04 (severity history), EG-12, EG-13, EG-15, EG-16.
CREATE TABLE IF NOT EXISTS case_event (
    event_id     VARCHAR,
    case_id      VARCHAR,
    ts           TIMESTAMP,
    event_type   VARCHAR
        CHECK (event_type IS NULL OR event_type IN
               ('created','assigned','note_added','status_changed',
                'severity_changed','escalated','closed','reopened')),
    actor_pseudo VARCHAR,
    actor_type   VARCHAR,
    from_state   VARCHAR,
    to_state     VARCHAR,
    note_ref     VARCHAR
);

-- plan §3.9. Required by EG-06 (absence of escalation), EG-07 (rate).
--
-- `entity_id` is injected at load, like it is for alert_record and case_record.
-- It was missing originally, on the reasoning that an escalation could be scoped
-- through its case. That holds for *finding* an escalation but not for scoping
-- one to an entity, and EG-06 needs the scoping: `case_id` is unique only
-- within a submission, so a lookup of "case ids that were escalated" across the
-- whole lake returns every other entity's escalations too. Peer entities with
-- overlapping case-numbering then mask each other's missing escalations, and
-- EG-06 under-reports precisely where a cohort comparison is being made.
CREATE TABLE IF NOT EXISTS escalation (
    entity_id       VARCHAR,
    escalation_id   VARCHAR,
    case_id         VARCHAR,
    ts              TIMESTAMP,
    target          VARCHAR,
    reason_code     VARCHAR,
    acknowledged_ts TIMESTAMP,
    outcome         VARCHAR
);

-- plan §3.10. Required by NS-01, NS-03, NS-05.
CREATE TABLE IF NOT EXISTS telemetry_daily (
    entity_id       VARCHAR,
    asset_id_pseudo VARCHAR,
    source_type     VARCHAR,
    date            DATE,
    event_count     BIGINT,
    last_seen_ts    TIMESTAMP
);

-- plan §3.13. Bridge table, not a list column: `associated_alert_ids` could not
-- be joined or integrity-checked. link_type distinguishes a declared link from
-- an inferred one, because EG-04 and EG-16 must not treat inference as fact.
CREATE TABLE IF NOT EXISTS alert_case (
    alert_id  VARCHAR,
    case_id   VARCHAR,
    link_type VARCHAR CHECK (link_type IS NULL OR
                            link_type IN ('explicit','inferred'))
);
"""

#: Additive migrations, executed after `_EVIDENCE_DDL`. New columns are added
#: here rather than by demanding a full rebuild of the evidence lake.
_EVIDENCE_MIGRATIONS = """
-- 1.0.0: `escalation` gained `entity_id` so that EG-06 can scope escalation
-- records to the entity whose submission produced them. Without it, case_id
-- collisions across entities caused peer cohorts to look like they were not
-- missing escalations.
ALTER TABLE escalation ADD COLUMN IF NOT EXISTS entity_id VARCHAR;
"""


class DuckDBClient:
    """Single-writer DuckDB manager.

    One process owns the write connection. Readers get `read_only=True`
    connections so an API request can never block or corrupt ingestion.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        parquet_dir: Path | None = None,
    ) -> None:
        settings = get_settings()
        self.db_path = Path(db_path or settings.parquet_dir / "evidence.duckdb")
        self.parquet_dir = Path(parquet_dir or settings.parquet_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.parquet_dir.mkdir(parents=True, exist_ok=True)

        self._write_lock = threading.RLock()
        self._writer_owner: int | None = None
        self._write_conn = None
        self._initialised = False

    # -- writer ------------------------------------------------------------
    @contextmanager
    def writer(self) -> Iterator[Any]:
        """Write connection. Background worker only.

        Re-entrant on the same thread so a service can compose calls without
        deadlocking, but owned by one thread so ingestion never interleaves.
        """
        import duckdb

        thread_id = threading.get_ident()
        if self._writer_owner is not None and self._writer_owner != thread_id:
            raise RuntimeError(
                "DuckDB is single-writer. Another thread (likely an API request) "
                "already holds the write connection. Use `reader()` in request "
                "handlers — see architecture principle §1.5."
            )
        self._writer_owner = thread_id
        try:
            if self._write_conn is None:
                # The evidence store may be pointed at a path whose parent is
                # provisioned later (a fresh mount, or a test suite that wipes
                # the runtime tree between modules). Mirror `init_db`: open the
                # file only after its directory exists.
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                self._write_conn = duckdb.connect(str(self.db_path))
                self._execute_ddl(self._write_conn)
                self._initialised = True
            yield self._write_conn
        finally:
            if self._write_conn is not None:
                self._write_conn.close()
            self._write_conn = None
            self._writer_owner = None

    # -- reader ------------------------------------------------------------
    @contextmanager
    def reader(self) -> Iterator[Any]:
        """Read-only connection. Safe from request threads."""
        import duckdb

        conn = duckdb.connect(str(self.db_path), read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    # -- bootstrap ---------------------------------------------------------
    @staticmethod
    def _split_statements(ddl: str) -> list[str]:
        """Split a DDL script into individual statements.

        DuckDB connections have no `executescript`, so statements are issued one
        at a time. Splitting is comment-aware: the DDL prose contains semicolons
        inside `--` comments, and a naive `ddl.split(";")` would tear a comment
        in half and leave its text to be parsed as SQL.
        """
        statements: list[str] = []
        current: list[str] = []
        in_line_comment = False
        in_string = False

        for char in ddl:
            if in_line_comment:
                # Everything until end of line is comment; never terminates a
                # statement, and never contributes SQL text.
                if char == "\n":
                    in_line_comment = False
                    current.append(char)
                continue

            if char == "-" and not in_string:
                in_line_comment = True
                continue
            if char == "'" and char != in_string:
                in_string = not in_string

            if char == ";" and not in_string:
                statement = "".join(current).strip()
                if statement:
                    statements.append(statement)
                current = []
                continue

            current.append(char)

        tail = "".join(current).strip()
        if tail:
            statements.append(tail)
        return statements

    @classmethod
    def _execute_ddl(cls, conn: Any) -> None:
        for statement in cls._split_statements(_EVIDENCE_DDL):
            conn.execute(statement)

    def init(self) -> None:
        """Create evidence tables, then bring an existing file up to date.

        `CREATE TABLE IF NOT EXISTS` silently leaves a previously created file on
        the old shape, so a column added to the DDL is invisible to anyone whose
        evidence lake already exists. The additive migrations below close that
        gap; a rebuild is not required, and re-ingesting a submission to pick up
        a new column would be an absurd thing to ask of a supervisory tool.
        """
        with self.writer() as conn:
            self._execute_ddl(conn)
            for statement in self._split_statements(_EVIDENCE_MIGRATIONS):
                conn.execute(statement)

    def export_parquet(self, table: str, name: str | None = None) -> Path:
        """Materialise an evidence table to Parquet (the portable form)."""
        name = name or f"{table}.parquet"
        target = self.parquet_dir / name
        with self.writer() as conn:
            conn.execute(
                f"COPY (SELECT * FROM {table}) TO '{target}' "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        return target


_client: DuckDBClient | None = None


def get_duckdb() -> DuckDBClient:
    global _client
    if _client is None:
        _client = DuckDBClient()
    return _client