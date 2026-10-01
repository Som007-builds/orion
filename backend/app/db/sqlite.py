"""SQLite state store (plan §3.2).

SQLite holds **application state** and is the state of record:
  entity, asset, submission, record_version, note_store (metadata),
  run, finding, finding_evidence, dimension_score,
  review_pack, review_pack_item, verdict,
  ledger_entry, mapping_profile, policy_profile, pack,
  user, role_assignment, session

Evidence lives in DuckDB/Parquet (see `app.db.duckdb_client`).

Two properties are enforced here rather than left to convention:
  * WAL is always on (applied on every connection).
  * `ledger_entry` is physically append-only via triggers, so a well-meaning
    `UPDATE` fails loudly instead of silently breaking the audit chain.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import get_settings

# SQLite has no enum type; CHECK constraints carry canonical vocabularies.
# Vocabularies are fixed by plan §3.1 and are NOT vendor-configurable at the
# column level. Vendor drift is absorbed by the mapping profile's severity_map /
# status_map before load.

_DDL = """
-- ---------------------------------------------------------------- lookups --
CREATE TABLE IF NOT EXISTS sector_ref (
    sector_ref   TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1
);

-- ----------------------------------------------------------------- entity --
-- plan §3.4. soc_hours is flattened into coverage_type/declared_open/
-- declared_close/roster_ref: three nullable columns would be unqueryable, and
-- EG-03 / NS-03 both filter on declared hours.
CREATE TABLE IF NOT EXISTS entity (
    entity_id            TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    sector               TEXT NOT NULL REFERENCES sector_ref(sector_ref),
    soc_model            TEXT NOT NULL
                         CHECK (soc_model IN ('in-house','MSSP','hybrid')),
    coverage_type        TEXT NOT NULL
                         CHECK (coverage_type IN ('24x7','extended','business')),
    declared_open        TEXT NOT NULL,
    declared_close       TEXT NOT NULL,
    roster_ref           TEXT,
    size_tier            TEXT NOT NULL
                         CHECK (size_tier IN ('small','medium','large')),
    tooling_profile      TEXT,
    critical_asset_count INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL
);

-- ------------------------------------------------------------------ asset --
CREATE TABLE IF NOT EXISTS asset (
    asset_id_pseudo       TEXT PRIMARY KEY,
    entity_id             TEXT NOT NULL REFERENCES entity(entity_id),
    asset_class           TEXT NOT NULL,
    criticality           INTEGER NOT NULL CHECK (criticality BETWEEN 1 AND 4),
    environment           TEXT NOT NULL
                          CHECK (environment IN ('IT','OT','DMZ','cloud','unknown')),
    expected_log_sources  TEXT,
    onboarded             TEXT,
    decommissioned        TEXT
);
CREATE INDEX IF NOT EXISTS ix_asset_entity
    ON asset(entity_id, criticality);

-- ------------------------------------------------------------- submission --
CREATE TABLE IF NOT EXISTS submission (
    submission_id TEXT PRIMARY KEY,
    entity_id     TEXT NOT NULL REFERENCES entity(entity_id),
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    received_ts   TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    schema_profile TEXT,
    row_counts    TEXT,
    declared_kpis TEXT,
    dq_score      REAL CHECK (dq_score IS NULL OR (dq_score BETWEEN 0.0 AND 1.0)),
    version       INTEGER NOT NULL,
    UNIQUE (entity_id, version)
);

-- ---------------------------------------------------------- record_version --
-- Per-record hash across submissions. Backs EG-11 (retroactive edits) and
-- EG-17 (deletions/gaps) via cross-submission diffs.
CREATE TABLE IF NOT EXISTS record_version (
    record_key         TEXT NOT NULL,
    submission_id      TEXT NOT NULL REFERENCES submission(submission_id),
    record_hash        TEXT NOT NULL,
    key_field_snapshot TEXT,
    PRIMARY KEY (record_key, submission_id)
);
CREATE INDEX IF NOT EXISTS ix_record_version_sub ON record_version(submission_id);

-- -------------------------------------------------------------- note_store --
-- Metadata only. Originals are encrypted blobs referenced by
-- encrypted_original_ref; `redacted_text` is what detectors and APIs read.
CREATE TABLE IF NOT EXISTS note_store (
    note_ref              TEXT PRIMARY KEY,
    entity_id             TEXT NOT NULL REFERENCES entity(entity_id),
    analyst_pseudo        TEXT,
    redacted_text         TEXT NOT NULL,
    shingle_signature     TEXT,
    length                INTEGER,
    encrypted_original_ref TEXT
);

-- -------------------------------------------------------------- quarantine --
-- plan: "no silent drops" (build-responsibility rule 4). Anything rejected by
-- normalisation or validation lands here with its reason, and the DQ report
-- exposes it to the examiner rather than only counting it.
CREATE TABLE IF NOT EXISTS quarantine (
    quarantine_id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL REFERENCES submission(submission_id),
    entity_id     TEXT,
    source_table  TEXT,
    source_file   TEXT,
    source_row    INTEGER,
    failed_stage  TEXT NOT NULL,
    reason        TEXT NOT NULL,
    raw_payload   TEXT,
    created_ts    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_quarantine_submission
    ON quarantine(submission_id, failed_stage);

-- -------------------------------------------------------------------- run --
CREATE TABLE IF NOT EXISTS run (
    run_id                TEXT PRIMARY KEY,
    created_ts            TEXT NOT NULL,
    started_ts            TEXT,
    finished_ts           TEXT,
    status                TEXT NOT NULL
                          CHECK (status IN ('queued','running','complete','failed')),
    input_manifest_hashes TEXT NOT NULL,
    config_hash           TEXT,
    pack_version          TEXT,
    policy_hash           TEXT,
    code_version          TEXT,
    seed                  INTEGER,
    output_hash           TEXT,
    error                 TEXT
);

-- ---------------------------------------------------------------- finding --
-- Persists the frozen indicator result object (plan §4.1).
CREATE TABLE IF NOT EXISTS finding (
    finding_id             TEXT PRIMARY KEY,
    run_id                 TEXT NOT NULL REFERENCES run(run_id),
    entity_id              TEXT NOT NULL REFERENCES entity(entity_id),
    indicator_id           TEXT NOT NULL,
    period_start           TEXT NOT NULL,
    period_end             TEXT NOT NULL,
    value                  REAL,
    value_units            TEXT,
    peer_median            REAL,
    peer_mad               REAL,
    peer_percentile        REAL,
    n_peers                INTEGER,
    baseline_method        TEXT,
    baseline_cohort        TEXT,
    self_median            REAL,
    self_mad               REAL,
    self_periods           INTEGER,
    effect_size            REAL,
    n                      INTEGER NOT NULL DEFAULT 0,
    confidence             REAL CHECK (confidence IS NULL OR
                                        (confidence BETWEEN 0.0 AND 1.0)),
    conf_n_term            REAL,
    conf_assessability_term REAL,
    conf_data_trust_term   REAL,
    evidence_query         TEXT,
    benign_explanations    TEXT,
    required_fields        TEXT,
    missing_fields         TEXT,
    assessability          REAL CHECK (assessability IS NULL OR assessability IN
                                      (0.0, 0.5, 1.0)),
    family                 TEXT,
    primary_dimension      TEXT,
    secondary_dimensions   TEXT,
    source                 TEXT NOT NULL
                           CHECK (source IN ('rules_engine','ml','negative_space')),
    is_low_confidence_lead INTEGER NOT NULL DEFAULT 0,
    actor_type_inferred    INTEGER NOT NULL DEFAULT 0,
    created_ts             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_finding_entity
    ON finding(entity_id, period_start);
CREATE INDEX IF NOT EXISTS ix_finding_indicator
    ON finding(indicator_id);
CREATE INDEX IF NOT EXISTS ix_finding_run ON finding(run_id);

-- ------------------------------------------------------- finding_evidence --
CREATE TABLE IF NOT EXISTS finding_evidence (
    finding_id TEXT NOT NULL REFERENCES finding(finding_id),
    table_name TEXT NOT NULL,
    row_id     TEXT NOT NULL,
    ordinal    INTEGER NOT NULL,
    PRIMARY KEY (finding_id, table_name, row_id)
);

-- --------------------------------------------------------- dimension_score --
-- plan §6.5. Persisted per period so trends and change points have history.
CREATE TABLE IF NOT EXISTS dimension_score (
    entity_id     TEXT NOT NULL REFERENCES entity(entity_id),
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    dimension     TEXT NOT NULL,
    score         REAL,
    ci_low        REAL,
    ci_high       REAL,
    assessability REAL CHECK (assessability IS NULL OR assessability IN
                              (0.0, 0.5, 1.0)),
    dts           REAL,
    tier          TEXT,
    run_id        TEXT NOT NULL REFERENCES run(run_id),
    pack_version  TEXT,
    PRIMARY KEY (entity_id, period_start, dimension)
);

-- -------------------------------------------------------- entity aggregates --
-- EGI / NSI / DTS / SAP per entity per period. Kept separate from
-- dimension_score because they are entity-level, not dimension-level.
CREATE TABLE IF NOT EXISTS entity_score (
    entity_id       TEXT NOT NULL REFERENCES entity(entity_id),
    period_start    TEXT NOT NULL,
    period_end      TEXT NOT NULL,
    egi             REAL,
    nsi             REAL,
    dts             REAL,
    sap             REAL,
    sap_rank        INTEGER,
    sap_rank_low    INTEGER,
    sap_rank_high   INTEGER,
    sap_tier        TEXT CHECK (sap_tier IS NULL OR sap_tier IN
                        ('T1','T2','T3','T4','not_assessable')),
    run_id          TEXT NOT NULL REFERENCES run(run_id),
    PRIMARY KEY (entity_id, period_start)
);

-- --------------------------------------------------------- review packs ----
CREATE TABLE IF NOT EXISTS review_pack (
    pack_id       TEXT PRIMARY KEY,
    created_ts    TEXT NOT NULL,
    created_by    TEXT NOT NULL,
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    stratum       TEXT,
    n_target      INTEGER NOT NULL,
    n_control     INTEGER NOT NULL,
    n_selected    INTEGER NOT NULL,
    ht_estimate   REAL,
    ht_ci_low     REAL,
    ht_ci_high    REAL,
    content_hash  TEXT
);

CREATE TABLE IF NOT EXISTS review_pack_item (
    pack_id             TEXT NOT NULL REFERENCES review_pack(pack_id),
    case_id             TEXT NOT NULL,
    slice_type          TEXT NOT NULL
                        CHECK (slice_type IN ('targeted','control')),
    inclusion_prob      REAL,
    case_risk_score     REAL NOT NULL,
    selected_because    TEXT NOT NULL,
    verification_prompts TEXT,
    cluster_id          TEXT,
    analyst_pseudo      TEXT,
    PRIMARY KEY (pack_id, case_id)
);

-- ---------------------------------------------------------------- verdict --
CREATE TABLE IF NOT EXISTS verdict (
    verdict_id      TEXT PRIMARY KEY,
    pack_id         TEXT NOT NULL REFERENCES review_pack(pack_id),
    case_id         TEXT NOT NULL,
    verdict         TEXT NOT NULL
                    CHECK (verdict IN ('confirmed','benign','insufficient_information')),
    notes           TEXT,
    examiner_pseudo TEXT NOT NULL,
    recorded_ts     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_verdict_case ON verdict(case_id);

-- ------------------------------------------------------------ ledger entry --
-- APPEND ONLY. plan §8.2. The triggers below make the rule physical: SQLite
-- rejects UPDATE and DELETE before any service code is consulted.
CREATE TABLE IF NOT EXISTS ledger_entry (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_hash   TEXT NOT NULL UNIQUE,
    prev_hash    TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload      TEXT,
    entity_id    TEXT
);
CREATE INDEX IF NOT EXISTS ix_ledger_action ON ledger_entry(action);
CREATE INDEX IF NOT EXISTS ix_ledger_ts ON ledger_entry(timestamp);

CREATE TRIGGER IF NOT EXISTS ledger_no_update
BEFORE UPDATE ON ledger_entry
BEGIN
    SELECT RAISE(ABORT, 'ledger_entry is append-only');
END;

CREATE TRIGGER IF NOT EXISTS ledger_no_delete
BEFORE DELETE ON ledger_entry
BEGIN
    SELECT RAISE(ABORT, 'ledger_entry is append-only');
END;

-- -------------------------------------------------------- mapping profile --
CREATE TABLE IF NOT EXISTS mapping_profile (
    profile_id  TEXT PRIMARY KEY,
    vendor      TEXT NOT NULL,
    version     TEXT NOT NULL,
    definition  TEXT NOT NULL,
    created_ts  TEXT NOT NULL,
    approved_by TEXT,
    active      INTEGER NOT NULL DEFAULT 1
);

-- --------------------------------------------------------- policy profile --
CREATE TABLE IF NOT EXISTS policy_profile (
    profile_id     TEXT NOT NULL,
    version        TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    definition     TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    signature      TEXT,
    active         INTEGER NOT NULL DEFAULT 0,
    previous_id    TEXT,
    PRIMARY KEY (profile_id, version)
);

-- ------------------------------------------------------------------- pack --
CREATE TABLE IF NOT EXISTS pack (
    pack_id      TEXT PRIMARY KEY,
    version      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    signature    TEXT NOT NULL,
    status       TEXT NOT NULL
                 CHECK (status IN ('staged','shadow','active','rolled_back')),
    staged_ts    TEXT NOT NULL,
    promoted_ts  TEXT,
    diff_report  TEXT,
    signed_by    TEXT
);

-- ------------------------------------------------------------------- RBAC --
-- plan §8.3
CREATE TABLE IF NOT EXISTS role (
    role         TEXT PRIMARY KEY,
    description  TEXT,
    permissions  TEXT NOT NULL  -- JSON list of permission strings
);

CREATE TABLE IF NOT EXISTS user (
    user_id        TEXT PRIMARY KEY,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,          -- Argon2id
    active         INTEGER NOT NULL DEFAULT 1,
    created_ts     TEXT NOT NULL,
    last_login_ts  TEXT
);

CREATE TABLE IF NOT EXISTS role_assignment (
    user_id     TEXT NOT NULL REFERENCES user(user_id),
    role        TEXT NOT NULL REFERENCES role(role),
    granted_ts  TEXT NOT NULL,
    granted_by  TEXT,
    revoked_ts  TEXT,
    PRIMARY KEY (user_id, role)
);

CREATE TABLE IF NOT EXISTS session (
    session_id  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user(user_id),
    created_ts  TEXT NOT NULL,
    expires_ts  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_session_user ON session(user_id);

-- ------------------------------------------------------------ migrations --
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_ts TEXT NOT NULL,
    note       TEXT
);

-- ------------------------------------------------------------- ingestion ---
CREATE TABLE IF NOT EXISTS ingestion_job (
    job_id        TEXT PRIMARY KEY,
    submission_id TEXT,
    entity_id     TEXT,
    status        TEXT NOT NULL
                  CHECK (status IN ('queued','running','complete','failed','quarantined')),
    stage         TEXT,
    created_ts    TEXT NOT NULL,
    started_ts    TEXT,
    finished_ts   TEXT,
    error         TEXT,
    n_loaded      INTEGER NOT NULL DEFAULT 0,
    n_quarantined INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_job_status ON ingestion_job(status, created_ts);
"""


class _ConnectionFactory:
    """Thread-local SQLite connections with the mandated PRAGMAs."""

    def __init__(self) -> None:
        self._local = threading.local()

    @property
    def path(self) -> Path:
        return get_settings().sqlite_path

    def get(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self.path,
                detect_types=sqlite3.PARSE_DECLTYPES,
                timeout=10.0,
                isolation_level=None,  # explicit transactions via `with`
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


_factory = _ConnectionFactory()


def get_connection() -> sqlite3.Connection:
    """Return the thread-local connection with WAL and FK enforcement applied."""
    return _factory.get()


def close_connection() -> None:
    _factory.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Explicit transaction.

    The ledger appends read the current head and insert the new entry inside one
    of these, so concurrent appends serialise rather than fork the chain.
    """
    conn = get_connection()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_db() -> None:
    """Create the schema. Idempotent."""
    settings = get_settings()
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    conn.executescript(_DDL)
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version, applied_ts, note) "
        "VALUES (1, datetime('now'), 'initial v2 schema')"
    )


def schema_version() -> int:
    row = get_connection().execute(
        "SELECT MAX(version) AS v FROM schema_version"
    ).fetchone()
    return int(row["v"] or 0)