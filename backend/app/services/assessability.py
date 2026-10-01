"""Per-dimension assessability matrix (plan §4.4).

The rule this module exists to enforce: **`Not assessable` is its own outcome,
never a low score.** A CSE that submitted no escalation records and one that
submitted zero escalations must not look the same in the output, because the
first tells an examiner nothing about escalation behaviour and the second tells
them a great deal. Collapsing them is how an analytics tool launders missing
evidence into a clean-looking dashboard.

So every dimension resolves to `Assessable` / `Partial` / `Not assessable`, and
every non-`Assessable` outcome carries the *specific fields* whose absence caused
it. The examiner's next question is always "what would you need me to send?",
and the answer has to be a list of export fields, not a shrug.

Two design commitments worth stating before the matrix:

* **Presence is measured, not declared.** Every check runs a count against the
  evidence lake or the state store. Nothing here trusts a profile, a policy, or
  a data tier to imply that a table exists, because the whole point is to find
  the submissions that claim one thing and contain another.
* **Absence is scoped to the period.** "No escalation records" means none for
  this submission's window. An escalation table that is empty because the period
  has no escalations yet, and one empty because the export omitted it, are
  distinguished by looking at whether the submission declared escalation fields at
  all — recorded in `submission.row_counts` / the mapping.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.db.duckdb_client import get_duckdb
from app.db.sqlite import get_connection
from app.schemas.assessability import (
    AssessabilityReport,
    DimensionAssessability,
)
from app.schemas.common import DataTier
from app.schemas.indicator import Assessability, Dimension

log = logging.getLogger(__name__)


# --------------------------------------------------------------- requirements --
@dataclass(frozen=True)
class FieldRequirement:
    """One piece of evidence a dimension needs, and what its absence means.

    `table` is the evidence table the field lives in. `column` is the canonical
    column name — always checked against the DDL, because plan §3's field names
    are the contract and a typo here would report a gap that does not exist.
    """

    table: str
    column: str | None = None
    #: State DB tables are checked for row presence instead of column fill.
    in_state_store: bool = False
    #: Human-readable field name for the examiner-facing gap list. Defaults to
    #: `table.column`, which is what the examiner sees in the export spec.
    label: str | None = None

    def display(self) -> str:
        if self.label:
            return self.label
        return f"{self.table}.{self.column}" if self.column else self.table


@dataclass(frozen=True)
class DimensionSpec:
    """The §4.4 matrix, one row per dimension.

    `required` is what makes a dimension `Assessable`; `optional` downgrades to
    `Partial` when missing but does not block assessment outright. Keeping the
    two lists separate is what stops "some data is absent" from collapsing into
    "no data is present".
    """

    dimension: Dimension
    name: str
    required: tuple[FieldRequirement, ...]
    optional: tuple[FieldRequirement, ...]
    min_tier: DataTier
    #: Indicators this dimension gates. Used to tell the examiner which of their
    #: questions go unanswered, which is far more useful than "ESC is partial".
    indicators: tuple[str, ...] = ()
    #: Conditions that make the dimension Partial even with every field present.
    #: For GOV, a single submission is structurally unable to evidence change
    #: over time, so it is capped at Partial no matter how rich the one period is.
    soft_cap: tuple[str, ...] = ()


# The §4.4 matrix, verbatim, with the field names resolved against the DDL in
# `app/db/duckdb_client.py` and `app/db/sqlite.py`.
DIMENSION_SPECS: tuple[DimensionSpec, ...] = (
    DimensionSpec(
        dimension=Dimension.TD,
        name="Threat Detection",
        required=(
            FieldRequirement("alert_record", "event_ts", label="alert.event_ts"),
            FieldRequirement("alert_record", "mitre_tactic",
                             label="alert category / MITRE tactic"),
        ),
        optional=(
            FieldRequirement("alert_record", "mitre_technique_id",
                             label="alert MITRE technique"),
            FieldRequirement("telemetry_daily", label="telemetry"),
            FieldRequirement("asset", in_state_store=True, label="asset inventory"),
        ),
        min_tier=DataTier.B,
        indicators=("NS-01", "NS-02", "NS-05", "EG-11"),
        soft_cap=(
            "no telemetry, so NS-01 and NS-05 (silent critical assets, "
            "implausibly low activity) cannot be assessed",
        ),
    ),
    DimensionSpec(
        dimension=Dimension.INV,
        name="Investigation",
        required=(
            FieldRequirement("case_record", "disposition"),
            FieldRequirement("note_store", "length", in_state_store=True,
                             label="case notes"),
        ),
        optional=(
            FieldRequirement("case_record", "closed_at"),
            FieldRequirement("note_store", "redacted_text", in_state_store=True),
        ),
        min_tier=DataTier.A,
        indicators=("EG-01", "EG-02", "EG-17"),
        soft_cap=("closed_at missing, so closure timing is unmeasurable",),
    ),
    DimensionSpec(
        dimension=Dimension.ESC,
        name="Escalation",
        required=(
            FieldRequirement("case_record", "severity_norm"),
            FieldRequirement("escalation", "case_id"),
        ),
        optional=(
            FieldRequirement("escalation", "acknowledged_ts"),
            FieldRequirement("case_event", "event_type"),
        ),
        min_tier=DataTier.A,
        indicators=("EG-06", "EG-07", "EG-04"),
        soft_cap=(
            "escalation records carry no acknowledgement, so an escalation "
            "that was raised and ignored is indistinguishable from one never "
            "seen by a responder",
        ),
    ),
    DimensionSpec(
        dimension=Dimension.IR,
        name="Incident Response",
        required=(
            FieldRequirement("case_record", "closed_at"),
        ),
        optional=(
            FieldRequirement("case_record", "root_cause_action"),
            FieldRequirement("alert_case", label="alert-to-case links (recurrence)"),
        ),
        min_tier=DataTier.A,
        indicators=("EG-05", "EG-06"),
        soft_cap=(
            "root_cause_action absent, so recurrence without remediation "
            "(EG-05) cannot be assessed",
        ),
    ),
    DimensionSpec(
        dimension=Dimension.SO,
        name="Security Operations",
        required=(
            FieldRequirement("case_event", "ts"),
        ),
        optional=(
            FieldRequirement("case_event", "event_type"),
            FieldRequirement("entity", "roster_ref", in_state_store=True,
                             label="analyst roster / SOC hours"),
        ),
        min_tier=DataTier.B,
        indicators=("EG-03", "EG-08", "EG-12", "EG-13"),
        soft_cap=(
            "no workflow events, so throughput and shift-boundary behaviour "
            "(EG-12, EG-13) cannot be assessed",
        ),
    ),
    DimensionSpec(
        dimension=Dimension.GOV,
        name="Governance and Oversight",
        required=(
            # A submission row is the period definition itself; without one
            # there is no period to assess against.
            FieldRequirement("submission", in_state_store=True),
            # Record versions are the retroactive-edit trail. Absent versions
            # mean EG-11 cannot detect a rewrite at all, so this is a
            # requirement rather than a nicety.
            FieldRequirement("record_version", in_state_store=True,
                             label="per-record version history"),
        ),
        optional=(
            FieldRequirement("submission", "declared_kpis", in_state_store=True,
                             label="declared KPIs"),
        ),
        min_tier=DataTier.B,
        indicators=("EG-11", "EG-14"),
        soft_cap=(
            "single submission: change over time cannot be evidenced, so "
            "EG-14 (trend vs declaration) is not assessable",
            "no declared KPIs, so EG-14 is not assessable",
        ),
    ),
    DimensionSpec(
        dimension=Dimension.OD,
        name="Operational Discipline",
        required=(
            FieldRequirement("case_event", "ts"),
        ),
        optional=(
            FieldRequirement("case_record", "created_at"),
        ),
        min_tier=DataTier.A,
        indicators=("EG-12", "EG-13"),
        soft_cap=(
            "only case-level timestamps: sub-case workflow granularity is "
            "absent, so EG-12 and EG-13 are only partly assessable",
        ),
    ),
    DimensionSpec(
        dimension=Dimension.CR,
        name="Cyber Resilience",
        required=(
            FieldRequirement("asset", in_state_store=True, label="asset inventory"),
        ),
        optional=(
            FieldRequirement("telemetry_daily", label="telemetry"),
            FieldRequirement("alert_record", "destination_asset_id"),
            FieldRequirement("alert_case", label="alert-to-case links (recurrence)"),
        ),
        min_tier=DataTier.B,
        indicators=("EG-05", "NS-01", "NS-03"),
        soft_cap=(
            "alerts only, no telemetry: resilience cannot be separated from "
            "blindness",
        ),
    ),
)


SPECS_BY_DIMENSION: dict[Dimension, DimensionSpec] = {
    spec.dimension: spec for spec in DIMENSION_SPECS
}


# ------------------------------------------------------------------- probing --
@dataclass
class EvidenceSnapshot:
    """What the evidence lake actually holds for one submission's period.

    Field presence is measured as `(rows, non_null_cells)`. Both matter and
    they answer different questions: zero rows means the table was never
    submitted, while rows with a NULL column mean it was submitted and the field
    was left empty. The examiner-facing gap list has to say which.
    """

    entity_id: str
    period_start: date
    period_end: date
    #: `table -> {column: (n_rows, n_non_null)}`
    columns: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)
    #: Tables that exist in the DDL and are queryable for this window.
    tables_present: set[str] = field(default_factory=set)
    #: Tables that returned no rows for this window. Distinct from absent.
    tables_empty: set[str] = field(default_factory=set)
    #: Tables the submission's mapping actually loaded, from
    # `submission.row_counts`. This is the only honest way to tell "the CSE
    # never sent escalation records" from "the CSE sent them and none fired":
    # the evidence lake looks identical in both cases, because a table with no
    # rows and a table never written both read as zero. An examiner told
    # "no rows in this period" about a table that was never submitted would
    # draw the wrong conclusion about the CSE.
    tables_declared: set[str] = field(default_factory=set)
    #: True when a submission row exists, so `tables_declared` means something.
    has_submission: bool = False
    #: State-store facts, gathered separately because they are not period-scoped.
    n_assets: int = 0
    n_notes: int = 0
    n_submissions: int = 1
    n_declared_kpis: int = 0
    has_roster: bool = False
    n_record_versions: int = 0

    def field_state(self, req: FieldRequirement) -> str:
        """One of `present`, `empty_column`, `no_rows`, `never_submitted`.

        The distinction between `never_submitted` and `empty_column` is the whole
        reason this returns a word instead of a boolean: the first is a mapping
        or template problem the CSE can fix, the second is a question about how
        the work was done, and they point at completely different conversations
        with the CSE.
        """
        if req.in_state_store:
            return self._state_field_state(req)

        if req.table not in self.tables_present:
            return "never_submitted"

        # A table the submission never loaded is reported as never submitted even
        # if the DDL has it, because that is what happened from the CSE's side.
        # Only when the submission did claim the table does an empty result mean
        # "sent but nothing in this window".
        if self.has_submission and req.table not in self.tables_declared:
            return "never_submitted"

        counts = self.columns.get(req.table, {})
        if req.column is None:
            n_rows = next((c[0] for c in counts.values()), 0)
            return "present" if n_rows else "no_rows"

        if req.column not in counts:
            return "empty_column"

        n_rows, n_non_null = counts[req.column]
        if n_rows == 0:
            return "no_rows"
        return "present" if n_non_null > 0 else "empty_column"

    def _state_field_state(self, req: FieldRequirement) -> str:
        n = {
            "asset": self.n_assets,
            "note_store": self.n_notes,
            "submission": self.n_submissions,
            "record_version": self.n_record_versions,
            "entity": 1 if self.has_roster else 0,
        }.get(req.table, 0)
        if req.table == "submission" and req.column == "declared_kpis":
            return "present" if self.n_declared_kpis else "empty_column"
        return "present" if n else "never_submitted"

    def n_rows(self, table: str) -> int:
        """Rows in the table for this period.

        Taken as the maximum of the recorded per-column counts, not their sum.
        Every column carries the same row count, so summing multiplied the total
        by the column count -- which reported 85 rows for a 5-row table and made
        tier B reachable with no evidence at all.
        """
        counts = self.columns.get(table, {})
        return max((c[0] for c in counts.values()), default=0)


def _table_columns() -> dict[str, set[str]]:
    """Columns per evidence table, read from the live DDL.

    Read rather than hardcoded, so a column added to `duckdb_client.py` is
    immediately probeable and a column renamed there cannot leave this module
    reporting a phantom gap.
    """
    with get_duckdb().reader() as conn:
        rows = conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "ORDER BY table_name, ordinal_position"
        ).fetchall()
    out: dict[str, set[str]] = {}
    for row in rows:
        out.setdefault(str(row[0]), set()).add(str(row[1]))
    return out


def snapshot_entity(
    entity_id: str,
    period_start: date,
    period_end: date,
) -> EvidenceSnapshot:
    """Measure what exists for one entity over one period.

    One `count(*)` per table plus a single `count(col)` sweep over every column,
    rather than a query per field. Assessability runs on every entity list view,
    so the round-trip count is kept low; eight dimensions x up to five fields
    would otherwise mean forty queries per entity.

    A period-scoped count is not enough on its own. Rows from a previous period
    would make every dimension read `Assessable` while the examiner is looking at
    a matrix backed by evidence nobody submitted for the window in question, so
    the window is always applied and recorded.
    """
    snap = EvidenceSnapshot(entity_id=entity_id, period_start=period_start,
                            period_end=period_end)
    known = _table_columns()

    # Timestamp column per table, used to scope counts to the period.
    time_col = {
        "alert_record": "event_ts",
        "case_record": "created_at",
        "case_event": "ts",
        "escalation": "ts",
        "telemetry_daily": "date",
    }

    with get_duckdb().reader() as conn:
        for table, columns in known.items():
            if table not in time_col:
                continue
            tcol = time_col[table]
            if tcol not in columns:
                continue

            if "entity_id" in columns:
                where = f"WHERE entity_id = ? AND {tcol} >= ? AND {tcol} < ?"
                params: list[Any] = [
                    entity_id, period_start, period_end + _one_day()
                ]
            else:
                where = f"WHERE {tcol} >= ? AND {tcol} < ?"
                params = [period_start, period_end + _one_day()]
            row = conn.execute(
                f"SELECT COUNT(*) FROM {table} {where}", params
            ).fetchone()
            n_rows = int(row[0]) if row else 0

            # `entity_id` is the filter, so counting it adds nothing. The time
            # column *is* counted: a dimension can require `alert.event_ts`
            # (EG-11) or `case_event.ts` (SO, OD), and excluding it made those
            # requirements report as absent on submissions that had them.
            counted = [c for c in sorted(columns) if c != "entity_id"]
            snap.tables_present.add(table)
            if n_rows == 0:
                snap.tables_empty.add(table)
                # Columns are still recorded, at zero. Leaving the dict empty
                # made every column read as "not in the schema" rather than
                # "present but unpopulated", so a table with no rows in the
                # period and a table whose column was never mapped became
                # indistinguishable.
                snap.columns[table] = {col: (0, 0) for col in counted}
                continue

            projection = ", ".join(f"COUNT({c})" for c in counted)
            counts = conn.execute(
                f"SELECT {projection} FROM {table} {where}", params
            ).fetchone()
            snap.columns[table] = {
                col: (n_rows, int(counts[i] or 0))
                for i, col in enumerate(counted)
            }

    _probe_alert_case(snap)
    _fill_state_store(snap)
    return snap


def _probe_alert_case(snap: EvidenceSnapshot) -> None:
    """Count the alert-to-case bridge for this entity's period.

    The bridge carries no `entity_id` and no timestamp of its own, so it cannot
    be scoped by a plain filter. It is scoped through the cases it links to:
    a link row is in-period exactly when its case is. Treating it as absent
    because it has no `entity_id` would have quietly made EG-05 (recurrence
    without root cause) unassessable for every CSE.
    """
    with get_duckdb().reader() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM alert_case ac WHERE ac.case_id IN ("
            "  SELECT case_id FROM case_record "
            "  WHERE entity_id = ? AND created_at >= ? AND created_at < ?"
            ")",
            (snap.entity_id, snap.period_start, snap.period_end + _one_day()),
        ).fetchone()
    n = int(row[0]) if row else 0
    snap.tables_present.add("alert_case")
    snap.columns["alert_case"] = {"case_id": (n, n)}
    if n == 0:
        snap.tables_empty.add("alert_case")


def _one_day() -> Any:
    from datetime import timedelta
    return timedelta(days=1)


def _fill_state_store(snap: EvidenceSnapshot) -> None:
    """Period-agnostic facts from SQLite. Assets, notes, roster, versions."""
    conn = get_connection()

    row = conn.execute(
        "SELECT row_counts FROM submission WHERE entity_id = ? "
        "ORDER BY version DESC LIMIT 1",
        (snap.entity_id,),
    ).fetchone()
    if row is not None and row[0]:
        import json as _json
        try:
            declared = _json.loads(row[0]) or {}
        except (TypeError, ValueError):
            declared = {}
        snap.tables_declared = set(declared.get("tables_loaded") or {})
        snap.has_submission = True

    snap.n_assets = int(
        conn.execute(
            "SELECT COUNT(*) FROM asset WHERE entity_id = ? AND "
            "decommissioned IS NULL",
            (snap.entity_id,),
        ).fetchone()[0]
    )
    snap.n_notes = int(
        conn.execute(
            "SELECT COUNT(*) FROM note_store WHERE entity_id = ?",
            (snap.entity_id,),
        ).fetchone()[0]
    )
    snap.n_submissions = int(
        conn.execute(
            "SELECT COUNT(*) FROM submission WHERE entity_id = ?",
            (snap.entity_id,),
        ).fetchone()[0]
    )
    if snap.n_submissions:
        snap.has_submission = True
    snap.n_record_versions = int(
        conn.execute(
            "SELECT COUNT(*) FROM record_version rv JOIN submission s "
            "ON s.submission_id = rv.submission_id WHERE s.entity_id = ?",
            (snap.entity_id,),
        ).fetchone()[0]
    )
    entity_row = conn.execute(
        "SELECT roster_ref FROM entity WHERE entity_id = ?",
        (snap.entity_id,),
    ).fetchone()
    snap.has_roster = bool(entity_row and entity_row[0])
    snap.n_declared_kpis = int(
        conn.execute(
            "SELECT COUNT(*) FROM submission WHERE entity_id = ? "
            "AND declared_kpis IS NOT NULL AND declared_kpis <> '' "
            "AND declared_kpis <> '{}'",
            (snap.entity_id,),
        ).fetchone()[0]
    )


# ------------------------------------------------------------------- resolver --
def _resolve(spec: DimensionSpec, snap: EvidenceSnapshot) -> DimensionAssessability:
    """Apply one matrix row to a snapshot.

    Precedence: missing *required* → `Not assessable`; missing *optional* →
    `Partial`; a triggered `soft_cap` → `Partial`; otherwise `Assessable`. Data
    tier can only lower the verdict, never raise it, since tier B cannot evidence
    what tier A requires.
    """
    missing_required: list[str] = []
    missing_optional: list[str] = []
    cap_reasons: list[str] = []
    missing_fields: list[str] = []

    for req in spec.required:
        state = snap.field_state(req)
        if state == "present":
            continue
        missing_fields.append(req.display())
        # The distinction is carried in the *reason*, not just the gap list,
        # because the fix differs: a column never exported is a mapping
        # conversation, a column exported blank is a process question.
        missing_required.append(_GAP_WORDING[state].format(field=req.display()))

    for req in spec.optional:
        state = snap.field_state(req)
        if state != "present":
            missing_optional.append(_GAP_WORDING[state].format(field=req.display()))
            missing_fields.append(req.display())

    triggered = [c for c in spec.soft_cap if _soft_cap_fires(c, snap)]

    if missing_required:
        verdict = Assessability.NOT_ASSESSABLE
        reason = "required evidence absent: " + ", ".join(missing_required)
    elif missing_optional or triggered:
        verdict = Assessability.PARTIAL
        parts: list[str] = []
        if missing_optional:
            parts.append("optional evidence absent: " + ", ".join(missing_optional))
        if triggered:
            parts.extend(triggered)
        reason = "; ".join(parts)
    else:
        verdict = Assessability.ASSESSABLE
        reason = None

    # Data tier is a floor on trust, applied last so it can only restrict.
    tier_ok = _tier_at_least(_achieved_tier(snap), spec.min_tier)
    if verdict is Assessability.ASSESSABLE and not tier_ok:
        verdict = Assessability.PARTIAL
        reason = (
            f"data tier {_achieved_tier(snap).value} is below the {spec.min_tier.value} "
            "this dimension requires"
        )
        missing_fields.append(f"data tier {spec.min_tier.value} (currently "
                              f"{_achieved_tier(snap).value})")

    return DimensionAssessability(
        dimension=spec.dimension,
        assessability=verdict,
        missing_fields=sorted(set(missing_fields)),
        required_fields=[r.display() for r in spec.required],
        min_tier=spec.min_tier,
        achieved_tier=_achieved_tier(snap),
        reason=reason,
        affected_indicators=list(spec.indicators) if verdict is not Assessability.ASSESSABLE else [],
    )


# How each absence is described to the examiner. Wording is part of the
# contract: "never submitted" is an accusation about the export, "left empty on
# every row" is an observation about the data, and only the second one is safe
# to read as a statement about how the SOC worked.
_GAP_WORDING = {
    "never_submitted": "{field} was not part of this submission",
    "no_rows": "{field} was submitted but has no rows in this period",
    "empty_column": "{field} was submitted but left empty on every row",
    "absent": "{field} is not available",
}


_SOFT_CAP_PROBES: dict[str, str] = {
    "no telemetry, so NS-01 and NS-05 (silent critical assets, implausibly low activity) cannot be assessed":
        "telemetry_daily",
    "escalation records carry no acknowledgement, so an escalation that was raised and ignored is indistinguishable from one never seen by a responder":
        "escalation.acknowledged_ts",
    "no workflow events, so throughput and shift-boundary behaviour (EG-12, EG-13) cannot be assessed":
        "case_event.ts",
    "single submission: change over time cannot be evidenced, so EG-14 (trend vs declaration) is not assessable":
        "__single_submission__",
    "no declared KPIs, so EG-14 is not assessable":
        "__declared_kpis__",
    "only case-level timestamps: sub-case workflow granularity is absent, so EG-12 and EG-13 are only partly assessable":
        "__no_subcase__",
    "alerts only, no telemetry: resilience cannot be separated from blindness":
        "telemetry_daily",
    "root_cause_action absent, so recurrence without remediation (EG-05) cannot be assessed":
        "case_record.root_cause_action",
    "closed_at missing, so closure timing is unmeasurable":
        "case_record.closed_at",
}


def _soft_cap_fires(cap: str, snap: EvidenceSnapshot) -> bool:
    probe = _SOFT_CAP_PROBES.get(cap)
    if probe is None:
        # An unrecognised cap is treated as *not* firing rather than silently
        # downgrading every dimension. A new cap that has not been wired to a
        # probe should show up as a missing-verdict question, not as a verdict.
        log.warning("soft cap has no probe wired: %s", cap[:70])
        return False

    if probe == "__single_submission__":
        return snap.n_submissions < 2
    if probe == "__declared_kpis__":
        return snap.n_declared_kpis == 0
    if probe == "__no_subcase__":
        return snap.field_state(FieldRequirement("case_event", "ts")) != "present"

    table, _, column = probe.partition(".")
    return snap.field_state(
        FieldRequirement(table, column or None, in_state_store=table in _STATE_TABLES)
    ) != "present"


# Tables that live in SQLite. Presence is a row count there, not a column fill,
# and the two are checked differently — conflating them made a telemetry export
# look absent because the SQLite branch had never heard of it.
_STATE_TABLES = frozenset(
    {"asset", "note_store", "submission", "record_version", "entity"}
)


_TIER_ORDER = {DataTier.C: 0, DataTier.B: 1, DataTier.A: 2}


def _tier_at_least(achieved: DataTier, required: DataTier) -> bool:
    return _TIER_ORDER[achieved] >= _TIER_ORDER[required]


def field_requirement(
    table: str, column: str | None = None, label: str | None = None
) -> FieldRequirement:
    """Build a `FieldRequirement` with `in_state_store` set correctly.

    `FieldRequirement` defaults `in_state_store` to False because a caller that
    forgets to set it is only wrong for the five SQLite tables. That failure is
    silent: the requirement is then probed as a DuckDB table, the table is not in
    `tables_present`, and the field reports `never_submitted` no matter what the
    entity actually sent. An indicator built that way declines on evidence that
    is sitting in the database in front of it.

    The set of SQLite tables is knowledge this module already owns, so callers
    outside it build requirements through here rather than repeating the list.
    """
    return FieldRequirement(
        table, column, label=label, in_state_store=table in _STATE_TABLES
    )


def _achieved_tier(snap: EvidenceSnapshot) -> DataTier:
    """Highest tier the evidence actually reaches.

    Derived from what is present rather than from the DQ tier, because the two
    answer different questions: DQ tier summarises submission quality as a whole,
    while this asks only "does this submission reach the structural bar this
    dimension needs".
    """
    if snap.n_rows("case_record") and snap.n_rows("alert_record"):
        return DataTier.A
    if snap.n_rows("case_record") or snap.n_rows("case_event") \
            or snap.n_rows("escalation"):
        return DataTier.B
    return DataTier.C


# ------------------------------------------------------------------ guidance --
def _guidance(dims: Iterable[DimensionAssessability],
              snap: EvidenceSnapshot) -> list[str]:
    """What to ask the CSE for, in the examiner's language.

    Grouped by the *export* that would fix it rather than by dimension, because
    that is how a data custodian actually acts — nobody exports "ESC". One line
    per blocked dimension is noise; "export case_event history" is an
    instruction.
    """
    gaps: dict[tuple[str, ...], set[str]] = {}
    for dim in dims:
        if dim.assessability is Assessability.ASSESSABLE:
            continue
        spec = SPECS_BY_DIMENSION[dim.dimension]
        missing = set(dim.missing_fields)
        tables: set[str] = set()
        for req in list(spec.required) + list(spec.optional):
            if req.display() in missing:
                tables.add(req.table)
        if not tables:
            # A soft cap fired with no field gap, e.g. a single submission. The
            # fix is a different period, not a different export.
            tables.add("submission")
        gaps.setdefault(tuple(sorted(tables)), set()).add(
            f"{dim.dimension.value} ({spec.name})"
        )

    out: list[str] = []
    for tables, dims_hit in sorted(gaps.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        parts = [_READABLE_TABLE.get(t, t) for t in tables]
        out.append(
            f"Provide {', '.join(parts)} to enable "
            f"{', '.join(sorted(dims_hit))}"
        )
    return out


# What a data custodian is actually asked to send. The point is to name the
# *export*, not the table: "Export case_event workflow history" is an action
# someone can take, "export case_event" reads like a database instruction and
# leaves them guessing which columns matter.
_READABLE_TABLE = {
    "alert_record": "the alert export",
    "case_record": "the case export",
    "case_event": "the case_event workflow history",
    "escalation": "the escalation records",
    "telemetry_daily": "telemetry",
    "asset": "the asset inventory",
    "note_store": "the case notes",
    "alert_case": "alert-to-case links",
    "submission": "a second period's submission",
    "record_version": "record version history",
    "entity": "the analyst roster and declared SOC hours",
}


def _interpretation(dims: list[DimensionAssessability],
                    overall: Assessability) -> str:
    """Plain language. No grades, no verdicts, no implication of compliance."""
    assessable = [d for d in dims if d.assessability is Assessability.ASSESSABLE]
    partial = [d for d in dims if d.assessability is Assessability.PARTIAL]
    blocked = [d for d in dims if d.assessability is Assessability.NOT_ASSESSABLE]

    def names(items: list[DimensionAssessability]) -> str:
        return ", ".join(SPECS_BY_DIMENSION[d.dimension].name for d in items)

    if not dims:
        return "No dimensions were evaluated for this submission."

    parts = [
        f"{len(assessable)} of {len(dims)} capability dimensions can be fully "
        f"assessed from this submission"
    ]
    if assessable:
        parts[0] += f": {names(assessable)}."

    if blocked:
        parts.append(
            f"{len(blocked)} cannot be assessed at all because the required "
            f"evidence was not submitted ({names(blocked)}). This is an absence "
            "of evidence, not evidence of low capability, and no score should be "
            "read across these dimensions."
        )
    if partial:
        parts.append(
            f"{len(partial)} are only partly assessable ({names(partial)}); "
            "indicators depending on the missing fields will carry reduced "
            "confidence or report not-assessable."
        )
    parts.append(
        "Each gap above names the specific export fields involved, so the CSE "
        "can be asked for precisely what is missing."
    )
    return " ".join(parts)


# -------------------------------------------------------------------- service --
def build_report(
    entity_id: str | None = None,
    submission_id: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
) -> AssessabilityReport:
    """`GET /entities/{id}/assessability`.

    With a `submission_id`, the period comes from that submission. Without one,
    the most recent submission is used; with neither, the entity is reported
    against an empty period, which correctly returns mostly `Not assessable` —
    an entity with no submitted evidence has not demonstrated anything, and that
    is the honest answer rather than a default of `Assessable`.
    """
    conn = get_connection()
    if submission_id and not entity_id:
        row = conn.execute(
            "SELECT entity_id, period_start, period_end FROM submission "
            "WHERE submission_id = ?",
            (submission_id,),
        ).fetchone()
        if row is None:
            raise AssessabilityError(f"No such submission: {submission_id}")
        entity_id = row["entity_id"]
        period_start = date.fromisoformat(str(row["period_start"])[:10])
        period_end = date.fromisoformat(str(row["period_end"])[:10])
    elif entity_id:
        # No submission named: fall back to the entity's latest period, so the
        # endpoint answers for a bare entity id rather than 400-ing.
        row = conn.execute(
            "SELECT period_start, period_end FROM submission "
            "WHERE entity_id = ? ORDER BY version DESC LIMIT 1",
            (entity_id,),
        ).fetchone()
        if row is not None:
            period_start = date.fromisoformat(str(row["period_start"])[:10])
            period_end = date.fromisoformat(str(row["period_end"])[:10])
    else:
        raise AssessabilityError(
            "assessability needs an entity_id or a submission_id; neither was given"
        )

    if period_start is None or period_end is None:
        # No submission yet. Report against a one-day empty window so the shape
        # of the response is identical and the examiner sees a real matrix with
        # real gaps, rather than an error they have to interpret.
        log.info("assessability for %s: no submission on record", entity_id)
        period_start = period_end = date(1970, 1, 1)

    snap = snapshot_entity(entity_id, period_start, period_end)
    dims = [_resolve(spec, snap) for spec in DIMENSION_SPECS]

    n_a = sum(1 for d in dims if d.assessability is Assessability.ASSESSABLE)
    n_p = sum(1 for d in dims if d.assessability is Assessability.PARTIAL)
    n_n = sum(1 for d in dims if d.assessability is Assessability.NOT_ASSESSABLE)

    overall = _overall(n_a, n_p, n_n)

    return AssessabilityReport(
        entity_id=entity_id,
        submission_id=submission_id,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        overall=overall,
        data_tier=_achieved_tier(snap),
        dimensions=dims,
        n_assessable=n_a,
        n_partial=n_p,
        n_not_assessable=n_n,
        upgrade_guidance=_guidance(dims, snap),
        interpretation=_interpretation(dims, overall),
    )


def _overall(n_a: int, n_p: int, n_n: int) -> Assessability:
    """Overall state.

    Weighted toward the *weakest* evidence: a report where five dimensions are
    perfect and three have no data at all should not read as "assessable".
    Deliberately not the mean multiplier — that is exactly the averaging that
    turns missing evidence into a middling score.
    """
    if n_n == 0 and n_p == 0:
        return Assessability.ASSESSABLE
    if n_a == 0:
        return Assessability.NOT_ASSESSABLE
    if n_p >= n_a:
        return Assessability.PARTIAL
    return Assessability.ASSESSABLE


class AssessabilityError(Exception):
    """Raised for a request assessability cannot answer."""


_assessability_singleton: Any = None


def get_assessability_service() -> Any:
    """Module-level accessor for symmetry with the other services."""
    global _assessability_singleton
    if _assessability_singleton is None:
        _assessability_singleton = AssessabilityService()
    return _assessability_singleton


class AssessabilityService:
    """Thin service wrapper so the API layer has one thing to call."""

    def report(
        self,
        entity_id: str,
        submission_id: str | None = None,
    ) -> AssessabilityReport:
        return build_report(entity_id, submission_id)

    def for_submission(self, submission_id: str) -> AssessabilityReport:
        return build_report(submission_id=submission_id)