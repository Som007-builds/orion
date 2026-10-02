"""P0 rules engine — execution-gap indicators (Phase 8, plan §4.5.1).

An indicator here is a **reason to look**, never a verdict. Each returns the
frozen `IndicatorResult` (interface #1) and carries three things an examiner
needs before they can act on it:

* a **baseline** — the comparison is peer-relative (leave-one-out median/MAD),
  never against a hardcoded expectation the CSE was told about in advance;
* **evidence** — the exact row ids that drove the observation, plus the SQL that
  reproduces them. `scripts/reproduce_finding.py` re-runs the stored query and
  must get byte-identical ids back, so every query here is fully ordered;
* **benign explanations** — the innocent reasons this pattern can arise. An
  indicator that cannot name them is asking the examiner to do the tool's work.

Two structural decisions are worth stating, because getting them wrong produces
numbers that look reasonable and mean nothing:

1. **One code path for the entity and its peers.** Every indicator computes a
   `dict[entity_id, Metric]` from a single read, and the scored entity's value
   is looked up in that same dict. Computing the entity separately from the
   cohort would let the two code paths drift, and a z-score against a baseline
   built by different arithmetic is not a z-score.

2. **Presence is measured, not declared.** Assessability reuses
   `assessability.snapshot_entity`, the same measurement Phase 6 uses for the
   §4.4 matrix, so an indicator cannot report a clean result on evidence that
   the dimension matrix has already recorded as absent.

Direction matters, so it is declared once per indicator and applied once, in
`_effect`. **Positive effect size always means adverse.** EG-01 is the only
indicator where the adverse end is the *low* end — rapid closure is a short
duration — and getting that flip wrong would make the fastest-closing CSE in
the cohort look like the safest one, which is the exact inversion this tool
exists to prevent.

Negative-space indicators (NS-01/02/04/05) are Dev 3's. Reference stubs are
shipped here so the pipeline runs end to end, and they are marked with
`NS_STUB_MARKER` so the read side can tell an unimplemented detector apart from
a genuine absence-of-evidence finding. See `ns_stub`.
"""

from __future__ import annotations

import json
import logging
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from app.db.duckdb_client import get_duckdb
from app.db.sqlite import get_connection
from app.schemas.common import DataTier
from app.schemas.indicator import (
    Assessability,
    Baseline,
    ConfidenceBreakdown,
    Dimension,
    FindingSource,
    IndicatorResult,
    Period,
)
from app.services.assessability import (
    EvidenceSnapshot,
    FieldRequirement,
    field_requirement,
    snapshot_entity,
)
from app.services.baseline_service import BaselineResult, get_baseline_service
from app.services.policy_profile import policy_profile

log = logging.getLogger(__name__)

SOURCE = FindingSource.RULES_ENGINE

#: Evidence rows are capped so one pathological submission cannot write a
#: finding card with a million ids. Truncation is recorded in `notes` — a
#: silently shortened evidence list reads as "this is all of them".
MAX_EVIDENCE_ROWS = 200

#: Marks a result produced by a placeholder rather than by a detector.
#: Deliberately greppable and stable: the API and scoring service filter on it
#: so an unimplemented detector is never presented to an examiner as though
#: the entity had been assessed and found wanting.
NS_STUB_MARKER = "REFERENCE STUB"

_TIER_ORDER = {DataTier.C: 0, DataTier.B: 1, DataTier.A: 2}

_HIGH_CRITICAL = ("CRITICAL", "HIGH")
_DISMISSIVE = ("FALSE_POSITIVE", "SUPPRESSED")


def _req(table: str, column: str | None, label: str | None = None) -> FieldRequirement:
    """A presence requirement, with the SQLite/DuckDB split filled in.

    Delegates to `assessability.field_requirement` rather than constructing the
    model directly, so `in_state_store` cannot be forgotten. When it is, the
    requirement is probed against DuckDB, `note_store` is never in
    `tables_present`, and EG-02 declines on notes that are plainly on file.
    """
    return field_requirement(table, column, label=label)


# --------------------------------------------------------------------- specs --
@dataclass(frozen=True)
class IndicatorSpec:
    """Static description of one indicator.

    Declared as data rather than buried in each method so the API can publish
    the catalogue, the scoring service can group by family, and a reviewer can
    read the whole P0 set on one screen.
    """

    indicator_id: str
    name: str
    description: str
    family: str
    primary: Dimension
    secondary: tuple[Dimension, ...]
    min_tier: DataTier
    required: tuple[FieldRequirement, ...]
    #: Which end of the peer distribution is adverse: +1 when high values are
    #: bad, -1 when low values are bad (EG-01 only).
    adverse_sign: int
    units: str
    benign: tuple[str, ...]
    #: Namespaced `table.column` keys this indicator attributes to. Used for
    #: the `Attribution` payload on model-sourced results and for family
    #: labelling, never for gating — gating uses measured presence.
    evidence_tables: tuple[str, ...]
    #: Required fields whose absence makes this indicator `Not assessable`
    #: outright rather than `Partial`.
    #:
    #: Partial means "the remaining evidence still supports an observation, at
    #: reduced confidence". It is the wrong answer when the absent field is the
    #: thing being measured. EG-06 asks what share of critical closures lacked
    #: escalation; if the escalation export was never submitted, every case
    #: looks unescalated and the indicator reports 100% — an accusation built
    #: entirely from the absence of the evidence needed to refute it. The same
    #: applies to EG-02, which cannot judge a note as thin when no note lengths
    #: were submitted at all.
    absence_disqualifies: tuple[str, ...] = ()


SPECS: tuple[IndicatorSpec, ...] = (
    IndicatorSpec(
        indicator_id="EG-01",
        name="Rapid closure of high/critical",
        description=(
            "Median time to close a high or critical case, against peers in the "
            "same cohort. Short is adverse, so the effect size is sign-flipped."
        ),
        family="rapid_thin_closure",
        primary=Dimension.INV,
        secondary=(Dimension.IR, Dimension.OD),
        min_tier=DataTier.A,
        required=(
            _req("case_record", "created_at", label="case.created_at"),
            _req("case_record", "closed_at", label="case.closed_at"),
            _req("case_record", "severity_norm", label="case.severity"),
        ),
        adverse_sign=-1,
        units="seconds",
        benign=(
            "A well-drilled SOC triaging on known playbooks will genuinely close "
            "faster than its peers. That is the capability being measured, not a "
            "fault, and it needs corroboration from EG-02 before it means anything.",
            "Cohorts differ in alert volume, so some face fewer hard cases per "
            "analyst and close them faster for that reason alone.",
            "Severity is often assigned after triage; a case escalated later in "
            "its life is indistinguishable here from one dismissed outright.",
        ),
        evidence_tables=("case_record",),
    ),
    IndicatorSpec(
        indicator_id="EG-02",
        name="Disposition implausibility",
        description=(
            "Share of high/critical cases dismissed as false positive or "
            "suppressed whose written justification is below the policy floor."
        ),
        family="rapid_thin_closure",
        primary=Dimension.INV,
        secondary=(Dimension.ESC,),
        min_tier=DataTier.A,
        required=(
            _req("case_record", "disposition", label="case.disposition"),
            _req("case_record", "severity_norm", label="case.severity"),
            _req("note_store", "length", label="case notes"),
        ),
        absence_disqualifies=("case notes",),
        adverse_sign=1,
        units="fraction of dismissals",
        benign=(
            "Notes may live in the SIEM's own field rather than the exported note "
            "column, leaving the export thin while the reasoning exists.",
            "Redaction strips IPs, hostnames and URLs, which shortens every note "
            "mechanically and says nothing about how much investigation happened. "
            "Compare note length against other entities in the same cohort before "
            "reading anything into it.",
            "Bulk dismissal of a known false-positive rule is routine maintenance, "
            "not concealment.",
        ),
        evidence_tables=("case_record", "note_store"),
    ),
    IndicatorSpec(
        indicator_id="EG-06",
        name="Critical closed without escalation",
        description=(
            "Share of closed critical cases with no escalation evidence of any "
            "kind — neither a row in the escalation export nor a level on the case."
        ),
        family="escalation_integrity",
        primary=Dimension.ESC,
        secondary=(Dimension.IR,),
        min_tier=DataTier.A,
        required=(
            _req("case_record", "severity_norm", label="case.severity"),
            _req("case_record", "closed_at", label="case.closed_at"),
            _req("escalation", "case_id", label="the escalation records"),
        ),
        # Without the escalation export every critical case reads as
        # unescalated. That is the shape of a finding built out of missing
        # evidence, so this declines instead of reporting 100%.
        absence_disqualifies=("the escalation records",),
        adverse_sign=1,
        units="fraction of critical closures",
        benign=(
            "Escalation may be recorded by phone or ticket outside the SIEM and "
            "never reach this export at all.",
            "Some cohorts escalate on assessed impact rather than raw severity, so "
            "a genuinely low-impact critical closes without a formal escalation.",
            "Severity is frequently revised upward after triage, so a closure "
            "classified critical in the export may never have been treated that way.",
        ),
        evidence_tables=("case_record", "escalation"),
    ),
    IndicatorSpec(
        indicator_id="EG-08",
        name="SLA threshold bunching",
        description=(
            "Share of closures landing in the window immediately beneath the "
            "policy SLA target for their severity."
        ),
        family="metric_gaming",
        primary=Dimension.OD,
        secondary=(Dimension.GOV,),
        min_tier=DataTier.A,
        required=(
            _req("case_record", "investigation_duration_sec",
                 label="case investigation duration"),
            _req("case_record", "severity_norm", label="case.severity"),
        ),
        adverse_sign=1,
        units="fraction of closures",
        benign=(
            "The target is a policy artefact. A team told to meet it will cluster "
            "near it as ordinary compliance pressure, and this indicator cannot "
            "distinguish that from gaming.",
            "Duration derived from open/close timestamps includes queue time, so "
            "clustering may reflect when a case was picked up rather than how long "
            "it was actually worked.",
            "End-of-shift batch processing produces the same clustering with no "
            "reference to the target at all.",
        ),
        evidence_tables=("case_record",),
    ),
    IndicatorSpec(
        indicator_id="EG-09",
        name="Backlog washing",
        description=(
            "Closures concentrated at the end of the reporting period, expressed "
            "as a multiple of the share expected if closures were spread evenly."
        ),
        family="metric_gaming",
        primary=Dimension.OD,
        secondary=(Dimension.GOV,),
        min_tier=DataTier.A,
        required=(
            _req("case_record", "closed_at", label="case.closed_at"),
            _req("submission", None, label="the submission period"),
        ),
        adverse_sign=1,
        units="x expected share",
        benign=(
            "Period and quarter ends are genuinely busier, so real work landing at "
            "a boundary is expected rather than suspicious.",
            "Cases discovered late in the period get closed quickly, concentrating "
            "closures at the edge for reasons that have nothing to do with the "
            "metric.",
            "Short or unusual reporting windows make the expected share small and "
            "the ratio volatile; check `period_span_hours` in the notes before "
            "reading a high ratio as behaviour.",
        ),
        evidence_tables=("case_record", "submission"),
    ),
    IndicatorSpec(
        indicator_id="EG-11",
        name="Retroactive edits across submissions",
        description=(
            "Share of records whose canonical content hash differs between "
            "successive submissions — evidence rewritten after first receipt."
        ),
        family="governance_records",
        primary=Dimension.GOV,
        secondary=(Dimension.OD,),
        min_tier=DataTier.B,
        required=(
            _req("submission", None, label="the submission history"),
            _req("record_version", None, label="record version history"),
        ),
        adverse_sign=1,
        units="fraction of versioned records",
        benign=(
            "Corrections are legitimate and expected. A resubmission fixing a "
            "detected error moves exactly these hashes, and that is the system "
            "working.",
            "Late-arriving enrichment (asset tags, owner names) changes content "
            "without changing any finding.",
            "Reordering columns or reformatting a date does not move the hash — the "
            "hash covers canonical values, so a move means content really changed.",
        ),
        evidence_tables=("record_version", "submission"),
    ),
)

SPECS_BY_ID = {s.indicator_id: s for s in SPECS}

#: Negative-space indicators owned by Dev 3 (`app/ml/negative_space.py`).
NS_INDICATOR_IDS = ("NS-01", "NS-02", "NS-04", "NS-05")

NS_SPECS: dict[str, tuple[str, Dimension, str, str]] = {
    "NS-01": (
        "Silent critical assets",
        Dimension.TD,
        "coverage",
        "asset inventory and telemetry_daily event counts",
    ),
    "NS-02": (
        "Absent alert categories",
        Dimension.TD,
        "coverage",
        "alert MITRE tactics and a negative-binomial cohort expectation",
    ),
    "NS-04": (
        "Orphan records",
        Dimension.INV,
        "record_integrity",
        "alert/case/escalation referential integrity checks",
    ),
    "NS-05": (
        "Implausibly low activity",
        Dimension.SO,
        "coverage",
        "telemetry_daily and alert volumes against cohort expectation",
    ),
}


# -------------------------------------------------------------------- metrics --
@dataclass
class Metric:
    """One entity's observation for one indicator.

    `value` is the statistic compared against peers, already oriented so high
    is adverse. `n` is the **denominator** — the population actually examined —
    because that is what `min(1, n/n_min)` in the confidence formula is meant to
    measure. Using the numerator would make an entity look well-evidenced
    precisely when it had almost nothing to look at.
    """

    value: float | None = None
    n: int = 0
    row_ids: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def computable(self) -> bool:
        return self.value is not None and self.n > 0


def _duration(row: dict[str, Any]) -> float | None:
    """Closure duration in seconds.

    Prefers the pipeline's derived column but falls back to the timestamps. The
    derived column is NULL when the source lacked one of the two, and treating
    that as "duration unknown" would silently shrink the population whenever a
    vendor exported open and close under different names.
    """
    derived = row.get("investigation_duration_sec")
    if derived is not None:
        try:
            seconds = float(derived)
        except (TypeError, ValueError):
            seconds = None
        # A negative duration is impossible and was already coerced to NULL on
        # load; one appearing here is a data defect, not a measurement.
        if seconds is not None and seconds >= 0:
            return seconds

    created = row.get("created_at")
    closed = row.get("closed_at")
    if created is None or closed is None:
        return None
    delta = (closed - created).total_seconds()
    return delta if delta >= 0 else None


def _median(values: Sequence[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _share(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _fmt_detail(detail: dict[str, Any]) -> str:
    """Render metric detail for the examiner-facing `notes` field."""
    parts: list[str] = []
    for key, value in detail.items():
        parts.append(f"{key}={value:.4g}" if isinstance(value, float) else f"{key}={value}")
    return ", ".join(parts)


def _period_bounds(period_start: date, period_end: date) -> tuple[Any, Any]:
    """Half-open window `[start, end+1day)`.

    Half-open so a case closed at 23:59 on the final day of the period is
    inside it. A closed upper bound would drop the last day's work from every
    closure-based indicator — which is exactly where backlog washing shows up,
    so the error would suppress the very finding it was meant to enable.
    """
    return period_start, period_end + timedelta(days=1)


def _query_record(engine: str, sql: str, params: Sequence[Any]) -> str:
    """Serialise a re-runnable query as JSON.

    Stored with its parameters attached. A query stored without them cannot be
    re-run, and an evidence query that cannot be re-run is a claim rather than
    evidence.
    """
    return json.dumps(
        {"engine": engine, "sql": " ".join(sql.split()), "params": list(params)},
        default=str,
        sort_keys=True,
    )


# ---------------------------------------------------------------- evidence SQL --
# One query per indicator, returning that entity's evidence rows in a fixed
# order. Every one filters on `entity_id` so a stored query returns this
# entity's rows and nothing else, and every one ends in `ORDER BY` so a second
# run returns the ids in the same sequence — which is what `reproduce_finding`
# asserts byte-for-byte.
#
# EG-02 needs a post-filter rather than more SQL. `note_store` lives in SQLite
# and `case_record` in DuckDB; DuckDB cannot join to it, and attaching SQLite
# tables across two live connections would couple indicator reads to the
# ingestion store's lock. So the query returns the dismissals with their note
# refs and the record carries `post_filter`, which `reproduce_evidence()` applies.
# The filter is still declared in the stored record, so a reproduction run uses
# the floor that was in force when the finding was produced.
#
# Every date parameter is wrapped in an explicit `CAST(... AS TIMESTAMP)`. The
# stored record is JSON, so the bounds reach a reproduction run as strings, and
# DuckDB will not compare a TIMESTAMP column to a VARCHAR. The casts make the
# query stand alone, which is the property a re-runnable query needs to have.
_EVIDENCE_SQL: dict[str, tuple[str, str]] = {
    "EG-01": (
        "duckdb",
        "SELECT case_id FROM case_record "
        "WHERE entity_id = ? "
        "AND created_at >= CAST(? AS TIMESTAMP) "
        "AND created_at < CAST(? AS TIMESTAMP) "
        "AND severity_norm IN ('CRITICAL', 'HIGH') "
        "AND closed_at IS NOT NULL AND created_at IS NOT NULL "
        "AND closed_at >= created_at "
        "ORDER BY case_id",
    ),
    "EG-02": (
        "duckdb",
        "SELECT case_id, note_ref FROM case_record "
        "WHERE entity_id = ? "
        "AND created_at >= CAST(? AS TIMESTAMP) "
        "AND created_at < CAST(? AS TIMESTAMP) "
        "AND severity_norm IN ('CRITICAL', 'HIGH') "
        "AND disposition IN ('FALSE_POSITIVE', 'SUPPRESSED') "
        "ORDER BY case_id",
    ),
    # The join is on `entity_id` as well as `case_id`. Joining on `case_id`
    # alone lets one entity's escalation vouch for another's case of the same
    # number, so the reproduction would return a shorter row list than the
    # result claims -- and a reproduction that disagrees with the finding is
    # worse than no reproduction at all.
    "EG-06": (
        "duckdb",
        "SELECT c.case_id FROM case_record c "
        "LEFT JOIN escalation e ON e.case_id = c.case_id "
        "AND e.entity_id = c.entity_id "
        "WHERE c.entity_id = ? "
        "AND c.created_at >= CAST(? AS TIMESTAMP) "
        "AND c.created_at < CAST(? AS TIMESTAMP) "
        "AND c.severity_norm = 'CRITICAL' AND c.closed_at IS NOT NULL "
        "AND c.escalation_level IS NULL AND e.case_id IS NULL "
        "ORDER BY c.case_id",
    ),
    # The SLA target is selected per row from the case's own severity via a CASE
    # expression, so each row is tested against its own target. A single shared
    # bound would apply one severity's target to all four and silently compare
    # low-severity closures against the critical SLA. The targets come from the
    # policy profile, so `{sla_case}` is filled in at assembly time.
    "EG-08": (
        "duckdb",
        "SELECT case_id FROM case_record "
        "WHERE entity_id = ? "
        "AND created_at >= CAST(? AS TIMESTAMP) "
        "AND created_at < CAST(? AS TIMESTAMP) "
        "AND severity_norm IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW') "
        "AND closed_at IS NOT NULL AND created_at IS NOT NULL "
        "AND closed_at >= created_at "
        "AND EXTRACT(EPOCH FROM (closed_at - created_at)) >= "
        "    (CASE severity_norm {sla_case} END) * ? "
        "AND EXTRACT(EPOCH FROM (closed_at - created_at)) <= "
        "    (CASE severity_norm {sla_case} END) "
        "ORDER BY case_id",
    ),
    "EG-09": (
        "duckdb",
        "SELECT case_id FROM case_record "
        "WHERE entity_id = ? "
        "AND created_at >= CAST(? AS TIMESTAMP) "
        "AND created_at < CAST(? AS TIMESTAMP) "
        "AND closed_at IS NOT NULL "
        "AND closed_at <= CAST(? AS TIMESTAMP) "
        "AND EXTRACT(EPOCH FROM (CAST(? AS TIMESTAMP) - closed_at)) <= ? * 3600 "
        "ORDER BY case_id",
    ),
    # DISTINCT, because the join to `submission` yields one row per version and
    # a record that changed appears once per submission it appears in. The
    # result's own `evidence_row_ids` is deduplicated, so without this the
    # reproduction returns each changed key twice and disagrees with the
    # finding it is supposed to reproduce.
    "EG-11": (
        "sqlite",
        "SELECT DISTINCT rv.record_key FROM record_version rv "
        "JOIN submission s ON s.submission_id = rv.submission_id "
        "WHERE s.entity_id = ? AND rv.record_key IN ("
        "  SELECT rv2.record_key FROM record_version rv2 "
        "  JOIN submission s2 ON s2.submission_id = rv2.submission_id "
        "  WHERE s2.entity_id = ? "
        "  GROUP BY rv2.record_key HAVING COUNT(DISTINCT rv2.record_hash) > 1) "
        "ORDER BY rv.record_key",
    ),
}

#: `WHEN <severity> THEN <target_seconds>`, built from the policy profile when
#: the query is assembled. Kept as a format string here because the targets are
#: policy data, not code.
_SLA_CASE = "WHEN 'CRITICAL' THEN 900 WHEN 'HIGH' THEN 3600 WHEN 'MEDIUM' THEN 14400 WHEN 'LOW' THEN 43200 "


def _reproduce_evidence(record: str) -> list[str]:
    """Re-execute a stored evidence record and return its row ids.

    Part of the rules engine rather than the evidence service because a stored
    record is only trustworthy if the module that wrote it can read it back. If
    this lives in a separate service, the two can disagree about what a record
    means and nothing catches it until an examiner tries to reproduce a finding.

    Ordered rows make the output comparable byte-for-byte across runs, which is
    what the reproduction check asserts.
    """
    payload = json.loads(record)
    engine = payload["engine"]
    sql = payload["sql"]
    params = payload.get("params", [])
    post_filter = payload.get("post_filter")

    if engine == "duckdb":
        with get_duckdb().reader() as conn:
            cursor = conn.execute(sql, params)
            names = [d[0] for d in cursor.description]
            records = [dict(zip(names, row)) for row in cursor.fetchall()]
    else:
        records = [dict(r) for r in get_connection().execute(sql, params)]

    if post_filter == "note_length_below":
        # A dismissal with no note at all counts as thin; requiring a note to be
        # present would make the indicator silent where evidence is weakest.
        floor = payload["params_extra"]["note_word_floor"]
        lengths = {
            str(r["note_ref"]): int(r["length"])
            for r in get_connection().execute(
                "SELECT note_ref, length FROM note_store WHERE length IS NOT NULL"
            )
        }
        records = [
            r for r in records
            if lengths.get(str(r.get("note_ref"))) is None
            or int(lengths[str(r["note_ref"])]) < floor
        ]

    id_column = "record_key" if engine == "sqlite" else "case_id"
    return [str(r[id_column]) for r in records]


# ------------------------------------------------------------------- engine --
class RulesEngine:
    """Computes the P0 execution-gap indicators.

    Read-only with respect to the evidence lake. Persisting results is the
    scoring service's job (Phase 9); this module's contract is that every
    indicator returns the frozen object with reproducible evidence attached.
    """

    def __init__(self, policy=None, baseline=None) -> None:
        self.policy = policy or policy_profile()
        self.bsl = baseline or get_baseline_service(self.policy)

    # -- shared plumbing ---------------------------------------------------
    def _snapshot(self, entity_id: str, period: Period) -> EvidenceSnapshot:
        return snapshot_entity(entity_id, period.start, period.end)

    @staticmethod
    def _achieved_tier(snap: EvidenceSnapshot) -> DataTier:
        """Highest structural tier the evidence reaches.

        Same rule as the §4.4 matrix: tier A needs both alerts and cases,
        because a submission with alerts and no cases cannot evidence anything
        about investigation.
        """
        if snap.n_rows("case_record") and snap.n_rows("alert_record"):
            return DataTier.A
        if (
            snap.n_rows("case_record")
            or snap.n_rows("case_event")
            or snap.n_rows("escalation")
        ):
            return DataTier.B
        return DataTier.C

    def _presence(
        self, spec: IndicatorSpec, snap: EvidenceSnapshot
    ) -> tuple[Assessability, list[str]]:
        """Assessability for one indicator, from measured presence.

        All required fields absent is `Not assessable`; some absent is `Partial`.
        Gap labels are the same `table.column` strings the dimension matrix
        uses, so an examiner reading both sees one vocabulary.
        """
        missing = [r.display() for r in spec.required if snap.field_state(r) != "present"]
        if not missing:
            return Assessability.ASSESSABLE, []
        # A field this indicator measures the absence of cannot be downgraded to
        # Partial: see `absence_disqualifies`.
        if len(missing) == len(spec.required) or any(
            field in spec.absence_disqualifies for field in missing
        ):
            return Assessability.NOT_ASSESSABLE, missing
        return Assessability.PARTIAL, missing

    def _dts(
        self, entity_id: str, period: Period
    ) -> tuple[float, Assessability, str | None]:
        """Data-trust term for the confidence breakdown, and what it cost.

        Uses the submission's own `dq_score`. Absent means the submission never
        went through ingestion scoring, which is itself a trust signal. Reported
        as `Partial` with the DQ score named as the gap, reusing the existing
        1.0/0.5/0.0 assessability vocabulary rather than inventing a second,
        conflicting confidence penalty.
        """
        row = get_connection().execute(
            "SELECT dq_score FROM submission WHERE entity_id = ? "
            "AND period_start <= ? AND period_end >= ? "
            "ORDER BY version DESC LIMIT 1",
            (entity_id, period.end.isoformat(), period.start.isoformat()),
        ).fetchone()
        if row is None or row["dq_score"] is None:
            return (
                0.5,
                Assessability.PARTIAL,
                "no data-quality score on record for this period, so data trust "
                "is treated as partial",
            )
        return max(0.0, min(1.0, float(row["dq_score"]))), Assessability.ASSESSABLE, None

    def _baseline_and_effect(
        self,
        spec: IndicatorSpec,
        entity_id: str,
        cohort_members: Iterable[str],
        metrics: dict[str, Metric],
        value: float | None,
        n_obs: int = 0,
    ) -> tuple[Baseline, float | None, BaselineResult]:
        """Leave-one-out peer baseline plus the adverse-oriented robust z.

        The entity is excluded from its own peer set here as well as in
        `BaselineService.cohort_for`, because `peer_values` is built by the
        caller and a caller mistake must not be able to reintroduce it.

        Steps 1 and 2 of plan §6.2 live here rather than in `scoring_service`,
        because this is the only place the peer values exist. `effect_size` on
        the result is therefore the robust z of the **EB-shrunk** estimate, not
        of the raw one — which is the number an examiner should see, since a
        single-case month genuinely carries less evidence than a sixty-case one.
        `scoring_service` consumes it from step 3 onward.
        """
        members = set(cohort_members)
        peers = {
            eid: m.value
            for eid, m in metrics.items()
            if eid in members and eid != entity_id and m.value is not None
        }
        baseline = self.bsl.peer_baseline(entity_id, spec.indicator_id, peer_values=peers)
        peer_baseline = Baseline(
            median=baseline.median,
            mad=baseline.mad,
            percentile=baseline.percentile,
            n_peers=baseline.n_peers,
            method=baseline.method,
            cohort=baseline.cohort,
        )

        if value is None or not peers:
            return peer_baseline, None, baseline

        z = self.bsl.effect_size(value, baseline, n_obs=n_obs, shrink=True)
        if z is None:
            # The cohort is a single repeated value. A deviation from it is real
            # but cannot be expressed as a z; the raw value against the peer
            # median still reads, and `None` says so rather than inventing one.
            return peer_baseline, None, baseline

        # The one place the sign is applied, for every indicator: a positive
        # effect size always means adverse.
        return peer_baseline, round(spec.adverse_sign * z, 6), baseline

    def _assemble(
        self,
        spec: IndicatorSpec,
        entity_id: str,
        period: Period,
        metric: Metric,
        metrics: dict[str, Metric],
        cohort_members: Iterable[str],
        snap: EvidenceSnapshot,
        evidence_query: str | None = None,
    ) -> IndicatorResult:
        """Build the frozen result. The single construction site for P0.

        Every indicator goes through here so the tier gate, the assessability
        rules, the confidence arithmetic and the truncation notice cannot drift
        apart between indicators.
        """
        achieved = self._achieved_tier(snap)
        if _TIER_ORDER[achieved] < _TIER_ORDER[spec.min_tier]:
            return IndicatorResult.not_assessable(
                indicator_id=spec.indicator_id,
                entity_id=entity_id,
                period=period,
                missing_fields=[f"data tier {spec.min_tier.value} (currently {achieved.value})"],
                source=SOURCE,
                primary_dimension=spec.primary,
                notes=(
                    f"{spec.name} needs data tier {spec.min_tier.value}; this "
                    f"submission reaches {achieved.value}, which cannot evidence it."
                ),
            )

        presence, missing_fields = self._presence(spec, snap)
        if presence is Assessability.NOT_ASSESSABLE:
            return IndicatorResult.not_assessable(
                indicator_id=spec.indicator_id,
                entity_id=entity_id,
                period=period,
                missing_fields=missing_fields,
                source=SOURCE,
                primary_dimension=spec.primary,
                notes=(
                    f"{spec.name} cannot be assessed: "
                    f"{', '.join(missing_fields)} not present in this submission. "
                    "This is an absence of evidence, not evidence of good practice."
                ),
            )

        # Fields present but population empty — no critical cases closed this
        # period, say. That is a real, reportable answer, not a gap, so it
        # reports zero with a note saying what was examined rather than
        # declining.
        value = 0.0 if metric.value is None else float(metric.value)
        n = metric.n

        peer_baseline, effect, baseline = self._baseline_and_effect(
            spec, entity_id, cohort_members, metrics, metric.value, n_obs=metric.n
        )

        dts_term, dts_assessability, dts_note = self._dts(entity_id, period)
        assess_term = presence.multiplier
        if dts_assessability is Assessability.PARTIAL:
            assess_term = min(assess_term, Assessability.PARTIAL.multiplier)

        n_min = max(1, int(self.policy.n_min))
        n_term = min(1.0, float(n) / float(n_min))
        confidence = round(min(1.0, n_term * assess_term * dts_term), 6)

        row_ids = list(metric.row_ids)
        notes = [spec.description]
        if metric.detail:
            notes.append(_fmt_detail(metric.detail))
        if presence is Assessability.PARTIAL:
            notes.append("Partial assessability — absent evidence: " + ", ".join(missing_fields))
        if dts_note:
            notes.append(dts_note)
        if baseline.fell_back:
            notes.append(
                f"Peer baseline fell back to a covariate-adjusted model "
                f"({baseline.fallback_reason}). This is not a direct peer comparison."
            )
        if effect is None and baseline.median is not None:
            notes.append(
                "The peer cohort shares a single value, so no robust z can be formed; "
                "the comparison is shown as the raw value against the peer median."
            )
        if len(row_ids) > MAX_EVIDENCE_ROWS:
            notes.append(
                f"Evidence truncated to the first {MAX_EVIDENCE_ROWS} of "
                f"{len(row_ids)} matching rows."
            )
            row_ids = row_ids[:MAX_EVIDENCE_ROWS]

        return IndicatorResult(
            indicator_id=spec.indicator_id,
            entity_id=entity_id,
            period=period,
            value=value,
            value_units=spec.units,
            peer_baseline=peer_baseline,
            # Trend context is computed across prior periods by the trend
            # service (Phase 15). Left unset rather than zeroed — a self-baseline
            # claiming zero observed periods would read as a statement that the
            # entity has no history, which is not something this module knows.
            self_baseline=None,
            effect_size=effect,
            n=n,
            confidence=confidence,
            confidence_breakdown=ConfidenceBreakdown(
                n_term=round(n_term, 6),
                assessability_term=round(assess_term, 6),
                data_trust_term=round(dts_term, 6),
            ),
            evidence_query=evidence_query,
            evidence_row_ids=row_ids,
            benign_explanations=list(spec.benign),
            required_fields=[r.display() for r in spec.required],
            missing_fields=missing_fields,
            assessability=presence,
            family=spec.family,
            primary_dimension=spec.primary,
            secondary_dimensions=list(spec.secondary),
            source=SOURCE,
            # Every detector here supplies evidence rows, so nothing is a lead.
            # Kept derived rather than hardcoded False so a future indicator that
            # fires without rows is surfaced as a lead instead of being silently
            # promoted to a finding.
            is_low_confidence_lead=not row_ids,
            actor_type_inferred=False,
            notes=" ".join(notes),
        )

    # -- evidence readers --------------------------------------------------
    def _cases(self, period: Period) -> list[dict[str, Any]]:
        """Every case row in the period, across all entities.

        One read for the whole cohort. Peers and the scored entity come from the
        same result set, so there is no path by which they can be computed
        differently.
        """
        start, end = _period_bounds(period.start, period.end)
        sql = (
            "SELECT case_id, entity_id, created_at, closed_at, "
            "investigation_duration_sec, analyst_pseudo, closed_by_pseudo, "
            "actor_type, severity_norm, escalation_level, disposition, "
            "root_cause_action, note_ref "
            "FROM case_record "
            "WHERE created_at >= ? AND created_at < ? "
            "ORDER BY entity_id, case_id"
        )
        with get_duckdb().reader() as conn:
            cursor = conn.execute(sql, [start, end])
            names = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
        # DuckDB hands back plain tuples; column names come from the cursor, not
        # the row. Building dicts positionally without them would silently shift
        # every field by one.
        return [dict(zip(names, row)) for row in rows]

    def _escalated_case_ids(self, period: Period) -> dict[str, set[str]]:
        """`entity_id -> case ids` carrying any escalation evidence.

        A row in the escalation export and a non-null level on the case are
        treated as equivalent evidence that escalation was recorded. Being
        generous here costs a little sensitivity and buys real protection
        against telling a CSE they did not escalate when their export simply
        records escalation on the case instead of in a side table.

        Scoped per entity, which is the whole reason `escalation` grew an
        `entity_id` column. `case_id` is unique within a submission, not across
        the lake, and peer cohorts use overlapping case numbering. An unscoped
        lookup therefore returns *every* entity's escalations, so a peer that
        properly escalated a case numbered 0004 makes the subject's identically
        numbered, unescalated case look escalated too — and EG-06 quietly
        under-reports for exactly the entities it is comparing.
        """
        start, end = _period_bounds(period.start, period.end)
        with get_duckdb().reader() as conn:
            rows = conn.execute(
                "SELECT entity_id, case_id FROM escalation "
                "WHERE ts >= ? AND ts < ? AND case_id IS NOT NULL",
                [start, end],
            ).fetchall()
        # DuckDB returns tuples here, so both values are positional.
        out: dict[str, set[str]] = {}
        for entity_id, case_id in rows:
            if case_id is None:
                continue
            out.setdefault(str(entity_id), set()).add(str(case_id))
        return out

    def _note_lengths(self) -> dict[str, int]:
        """`note_ref -> length` for every note.

        Read whole rather than per entity because the peer metric needs the same
        lookup, and `note_store` holds metadata only — no note text crosses this
        boundary.
        """
        return {
            str(r["note_ref"]): int(r["length"])
            for r in get_connection().execute(
                "SELECT note_ref, length FROM note_store WHERE length IS NOT NULL"
            )
        }

    def _submissions_for(self, entity_id: str, period: Period) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in get_connection().execute(
                "SELECT submission_id, entity_id, period_start, period_end, "
                "received_ts, version, dq_score FROM submission "
                "WHERE entity_id = ? AND period_start <= ? AND period_end >= ? "
                "ORDER BY version",
                (entity_id, period.end.isoformat(), period.start.isoformat()),
            )
        ]

    def _reporting_boundary(self, entity_id: str, period: Period) -> datetime:
        """End of the reporting window, as a timestamp.

        Taken from the submission that covers the period, so EG-09 measures
        against the boundary the CSE was actually reporting to rather than an
        assumed calendar month. Falls back to the requested period end.
        """
        rows = self._submissions_for(entity_id, period)
        end_date = period.end
        if rows:
            end_date = date.fromisoformat(str(rows[-1]["period_end"])[:10])
        return datetime.combine(end_date, time.max)

    # -- metrics ----------------------------------------------------------
    def _metric_eg01(self, period: Period) -> dict[str, Metric]:
        """Median high/critical closure duration per entity. Short is adverse."""
        cases = self._cases(period)
        by_entity: dict[str, list[float]] = {}
        case_ids: dict[str, list[str]] = {}
        for case in cases:
            if case["severity_norm"] not in _HIGH_CRITICAL:
                continue
            if case["closed_at"] is None or case["created_at"] is None:
                continue
            seconds = _duration(case)
            if seconds is None:
                continue
            entity_id = str(case["entity_id"])
            by_entity.setdefault(entity_id, []).append(seconds)
            case_ids.setdefault(entity_id, []).append(str(case["case_id"]))

        floor = self.policy.thresholds["fast_closure_seconds"]
        out: dict[str, Metric] = {}
        for entity_id, durations in by_entity.items():
            # The evidence rows are every case examined, not only the fast ones.
            # An examiner checking EG-01 needs to see the comparison set to judge
            # whether the median is representative; a row list containing only
            # the extreme cases would present the median's own inputs as though
            # every one of them were a finding.
            out[entity_id] = Metric(
                value=_median(durations),
                n=len(durations),
                row_ids=tuple(sorted(case_ids.get(entity_id, []))),
                detail={
                    "high_critical_cases_examined": len(durations),
                    "median_closure_seconds": _median(durations),
                    "faster_than_policy_floor": sum(1 for d in durations if d < floor),
                    "fast_closure_floor_seconds": floor,
                },
            )
        return out

    def _metric_eg02(self, period: Period) -> dict[str, Metric]:
        """Share of dismissals whose written justification is below the floor."""
        cases = self._cases(period)
        lengths = self._note_lengths()
        floor = int(self.policy.thresholds["note_min_words"])

        totals: dict[str, int] = {}
        thin: dict[str, list[str]] = {}
        for case in cases:
            if case["severity_norm"] not in _HIGH_CRITICAL:
                continue
            if case["disposition"] not in _DISMISSIVE:
                continue
            entity_id = str(case["entity_id"])
            totals[entity_id] = totals.get(entity_id, 0) + 1
            note_ref = case["note_ref"]
            # A dismissal with no exported note counts as thin. Requiring a note
            # to be present would make the indicator go silent exactly where the
            # evidence is weakest, which is the wrong direction to fail.
            length = lengths.get(str(note_ref)) if note_ref else None
            if length is None or length < floor:
                thin.setdefault(entity_id, []).append(str(case["case_id"]))

        return {
            entity_id: Metric(
                value=_share(len(rows), total),
                n=total,
                row_ids=tuple(rows),
                detail={"dismissals_examined": total, "note_word_floor": floor},
            )
            for entity_id, total in totals.items()
            for rows in [sorted(thin.get(entity_id, []))]
        }

    def _metric_eg06(self, period: Period) -> dict[str, Metric]:
        """Share of closed critical cases with no escalation evidence."""
        cases = self._cases(period)
        escalated_by_entity = self._escalated_case_ids(period)

        totals: dict[str, int] = {}
        unescalated: dict[str, list[str]] = {}
        for case in cases:
            if case["severity_norm"] != "CRITICAL" or case["closed_at"] is None:
                continue
            entity_id = str(case["entity_id"])
            totals[entity_id] = totals.get(entity_id, 0) + 1
            case_id = str(case["case_id"])
            if (case_id in escalated_by_entity.get(entity_id, set())
                    or case["escalation_level"] is not None):
                continue
            unescalated.setdefault(entity_id, []).append(case_id)

        return {
            entity_id: Metric(
                value=_share(len(rows), total),
                n=total,
                row_ids=tuple(rows),
                detail={
                    "critical_closures_examined": total,
                    "escalation_rows_in_period": len(
                        escalated_by_entity.get(entity_id, set())
                    ),
                },
            )
            for entity_id, total in totals.items()
            for rows in [sorted(unescalated.get(entity_id, []))]
        }

    def _metric_eg08(self, period: Period) -> dict[str, Metric]:
        """Share of closures landing just under the severity's SLA target."""
        cases = self._cases(period)
        targets = self.policy.sla_targets
        window = float(self.policy.thresholds["sla_bunch_window_ratio"])

        totals: dict[str, int] = {}
        bunched: dict[str, list[str]] = {}
        for case in cases:
            if case["closed_at"] is None:
                continue
            severity = str(case["severity_norm"]) if case["severity_norm"] else None
            target = targets.get(severity) if severity else None
            if target is None:
                continue
            seconds = _duration(case)
            if seconds is None:
                continue
            entity_id = str(case["entity_id"])
            totals[entity_id] = totals.get(entity_id, 0) + 1
            if float(target) * (1.0 - window) <= seconds <= float(target):
                bunched.setdefault(entity_id, []).append(str(case["case_id"]))

        return {
            entity_id: Metric(
                value=_share(len(rows), total),
                n=total,
                row_ids=tuple(rows),
                detail={
                    "closures_examined": total,
                    "sla_window_ratio": window,
                    "sla_targets_seconds": dict(sorted(targets.items())),
                },
            )
            for entity_id, total in totals.items()
            for rows in [sorted(bunched.get(entity_id, []))]
        }

    def _metric_eg09(self, period: Period) -> dict[str, Metric]:
        """Period-boundary closure concentration, as a multiple of uniform.

        Expressed as a ratio rather than a raw share so it stays comparable
        across reporting windows of different lengths: a 48-hour tail is 29% of a
        one-week period and 4% of a three-month one, and a raw share would make
        every short window look like backlog washing.
        """
        window_hours = float(self.policy.thresholds["backlog_window_hours"])
        cases = self._cases(period)
        entity_ids = sorted({str(c["entity_id"]) for c in cases})

        boundaries = {eid: self._reporting_boundary(eid, period) for eid in entity_ids}
        spans = {
            eid: max(
                (boundary - datetime.combine(period.start, time.min)).total_seconds() / 3600.0,
                1.0,
            )
            for eid, boundary in boundaries.items()
        }

        totals: dict[str, int] = {}
        late: dict[str, int] = {}
        at_boundary: dict[str, list[str]] = {}
        for case in cases:
            closed = case["closed_at"]
            if closed is None:
                continue
            entity_id = str(case["entity_id"])
            totals[entity_id] = totals.get(entity_id, 0) + 1
            boundary = boundaries[entity_id]
            if closed > boundary:
                # Closed after the reporting window closed. Counted in the
                # denominator and reported separately, because a late closure is
                # exactly the sort of thing an examiner needs to see rather than
                # have quietly dropped from the ratio.
                late[entity_id] = late.get(entity_id, 0) + 1
                continue
            if (boundary - closed).total_seconds() / 3600.0 <= window_hours:
                at_boundary.setdefault(entity_id, []).append(str(case["case_id"]))

        out: dict[str, Metric] = {}
        for entity_id, total in totals.items():
            rows = sorted(at_boundary.get(entity_id, []))
            observed = _share(len(rows), total) or 0.0
            expected = min(1.0, window_hours / spans[entity_id])
            detail = {
                "closures_examined": total,
                "share_in_boundary_window": round(observed, 4),
                "share_expected_if_uniform": round(expected, 4),
                "boundary_window_hours": window_hours,
                "period_span_hours": round(spans[entity_id], 2),
            }
            if late.get(entity_id):
                detail["closures_after_window_closed"] = late[entity_id]
            out[entity_id] = Metric(
                value=observed / expected if expected > 0 else None,
                n=total,
                row_ids=tuple(rows),
                detail=detail,
            )
        return out

    def _metric_eg11(self) -> dict[str, Metric]:
        """Share of records whose hash moved between successive submissions.

        `n` is the count of records carrying at least one version, which is the
        population the change fraction is measured over — not the number of
        submissions and not the number of changed records.
        """
        totals: dict[str, int] = {}
        changed: dict[str, list[str]] = {}
        for row in get_connection().execute(
            "SELECT s.entity_id AS entity_id, rv.record_key AS record_key, "
            "COUNT(DISTINCT rv.record_hash) AS n_hashes "
            "FROM record_version rv JOIN submission s "
            "ON s.submission_id = rv.submission_id "
            "GROUP BY s.entity_id, rv.record_key"
        ):
            entity_id = str(row["entity_id"])
            totals[entity_id] = totals.get(entity_id, 0) + 1
            if int(row["n_hashes"]) > 1:
                changed.setdefault(entity_id, []).append(str(row["record_key"]))

        submissions = {
            str(row["entity_id"]): int(row["n"])
            for row in get_connection().execute(
                "SELECT entity_id, COUNT(*) AS n FROM submission GROUP BY entity_id"
            )
        }

        return {
            entity_id: Metric(
                value=_share(len(rows), total),
                n=total,
                row_ids=tuple(rows),
                detail={
                    "records_with_versions": total,
                    "records_changed": len(rows),
                    "submissions_on_record": submissions.get(entity_id, 0),
                },
            )
            for entity_id, total in totals.items()
            for rows in [sorted(changed.get(entity_id, []))]
        }

    # -- evidence query assembly -------------------------------------------
    def evidence_query(
        self, indicator_id: str, entity_id: str, period: Period
    ) -> str | None:
        """The stored, re-runnable query for one indicator and entity.

        Parameters are filled in from the policy profile, so the stored query
        embeds the thresholds actually used rather than leaving the reader to
        reconstruct them. Changing the policy therefore changes the query, and
        the reproduction run reflects the policy in force when the finding was
        produced.
        """
        if indicator_id not in _EVIDENCE_SQL:
            return None
        engine, sql = _EVIDENCE_SQL[indicator_id]
        start, end = _period_bounds(period.start, period.end)

        if indicator_id == "EG-11":
            return _query_record(engine, sql, [entity_id, entity_id])

        if indicator_id == "EG-02":
            record = _query_record(engine, sql, [entity_id, start, end])
            # Declared in the record rather than baked into SQL, because the
            # thinness test reads note lengths from SQLite.
            payload = json.loads(record)
            payload["post_filter"] = "note_length_below"
            payload["params_extra"] = {
                "note_word_floor": int(self.policy.thresholds["note_min_words"])
            }
            return json.dumps(payload, default=str, sort_keys=True)

        if indicator_id == "EG-08":
            targets = self.policy.sla_targets
            sla_case = " ".join(
                f"WHEN '{severity}' THEN {int(targets[severity])}"
                for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
                if severity in targets
            )
            if not sla_case:
                return None
            return _query_record(
                engine,
                sql.format(sla_case=sla_case),
                [
                    entity_id,
                    start,
                    end,
                    1.0 - float(self.policy.thresholds["sla_bunch_window_ratio"]),
                ],
            )

        if indicator_id == "EG-09":
            boundary = self._reporting_boundary(entity_id, period)
            return _query_record(
                engine,
                sql,
                [
                    entity_id,
                    start,
                    end,
                    boundary.isoformat(sep=" "),
                    boundary.isoformat(sep=" "),
                    float(self.policy.thresholds["backlog_window_hours"]),
                ],
            )

        return _query_record(engine, sql, [entity_id, start, end])

    # -- public API --------------------------------------------------------
    def run(
        self, indicator_id: str, entity_id: str, period_start: date, period_end: date
    ) -> IndicatorResult:
        """Run one indicator for one entity over one period."""
        spec = SPECS_BY_ID.get(indicator_id)
        if spec is None:
            raise KeyError(f"Unknown indicator {indicator_id!r}")
        period = Period(start=period_start, end=period_end)
        snap = self._snapshot(entity_id, period)
        cohort = self.bsl.cohort_for(entity_id)

        if indicator_id == "EG-11":
            return self._run_eg11(spec, entity_id, period, snap, cohort.members)

        readers: dict[str, Any] = {
            "EG-01": self._metric_eg01,
            "EG-02": self._metric_eg02,
            "EG-06": self._metric_eg06,
            "EG-08": self._metric_eg08,
            "EG-09": self._metric_eg09,
        }
        metrics = readers[indicator_id](period)
        metric = metrics.get(entity_id, Metric())

        return self._assemble(
            spec,
            entity_id,
            period,
            metric,
            metrics,
            cohort.members,
            snap,
            evidence_query=self.evidence_query(indicator_id, entity_id, period),
        )

    def _run_eg11(
        self,
        spec: IndicatorSpec,
        entity_id: str,
        period: Period,
        snap: EvidenceSnapshot,
        cohort_members: Sequence[str],
    ) -> IndicatorResult:
        """EG-11, which needs two submissions before it can say anything."""
        if snap.n_record_versions == 0:
            return IndicatorResult.not_assessable(
                indicator_id=spec.indicator_id,
                entity_id=entity_id,
                period=period,
                missing_fields=["record version history"],
                source=SOURCE,
                primary_dimension=spec.primary,
                notes=(
                    f"{spec.name} needs per-record hashes across submissions; this "
                    "entity has no record version history on file."
                ),
            )

        n_submissions = snap.n_submissions
        if n_submissions < 2:
            return IndicatorResult.not_assessable(
                indicator_id=spec.indicator_id,
                entity_id=entity_id,
                period=period,
                missing_fields=["a second submission to compare against"],
                source=SOURCE,
                primary_dimension=spec.primary,
                notes=(
                    f"{spec.name} compares consecutive submissions. One submission "
                    "is on record, so there is no earlier version for a record to "
                    "have changed from. Nothing can be asserted either way — a zero "
                    "here would claim nothing was rewritten, which is not what "
                    "one submission can tell us."
                ),
            )

        metrics = self._metric_eg11()
        return self._assemble(
            spec,
            entity_id,
            period,
            metrics.get(entity_id, Metric()),
            metrics,
            cohort_members,
            snap,
            evidence_query=self.evidence_query(spec.indicator_id, entity_id, period),
        )

    def run_all(
        self,
        entity_id: str,
        period_start: date,
        period_end: date,
        only: Sequence[str] | None = None,
    ) -> dict[str, IndicatorResult]:
        """Every P0 indicator plus the NS reference stubs, keyed by id.

        `only` restricts the set to named indicator ids. It is a scoping filter
        for a targeted run, not a selection mechanism: an id that does not exist
        is an error rather than a silent omission, because a request naming an
        indicator Orion does not have should not come back looking like a
        successful narrower run.

        An exception in one indicator becomes a marked not-assessable result
        rather than aborting the set. One faulty detector must not cost the
        examiner the other five.
        """
        period = Period(start=period_start, end=period_end)
        wanted = set(only) if only is not None else None

        out: dict[str, IndicatorResult] = {}
        for spec in SPECS:
            if wanted is not None and spec.indicator_id not in wanted:
                continue
            try:
                out[spec.indicator_id] = self.run(
                    spec.indicator_id, entity_id, period_start, period_end
                )
            except Exception as exc:
                log.exception("%s failed for %s", spec.indicator_id, entity_id)
                out[spec.indicator_id] = IndicatorResult.not_assessable(
                    indicator_id=spec.indicator_id,
                    entity_id=entity_id,
                    period=period,
                    missing_fields=[],
                    source=SOURCE,
                    primary_dimension=spec.primary,
                    notes=(
                        f"{NS_STUB_MARKER}: {spec.name} could not be evaluated "
                        f"({type(exc).__name__}: {exc}). This is a fault in Orion, "
                        "not a statement about the entity."
                    ),
                )
        for indicator_id in NS_INDICATOR_IDS:
            if wanted is not None and indicator_id not in wanted:
                continue
            out[indicator_id] = self.ns_stub(indicator_id, entity_id, period_start, period_end)

        if wanted is not None:
            missing = sorted(wanted - set(out))
            if missing:
                raise KeyError(
                    f"Unknown indicator id(s): {', '.join(missing)}. "
                    f"Known ids: {', '.join(sorted(self.catalogue_ids()))}."
                )
        return out

    def catalogue_ids(self) -> list[str]:
        """Every indicator id this engine can produce, implemented and stub."""
        return [spec.indicator_id for spec in SPECS] + list(NS_INDICATOR_IDS)

    def implemented_evidence(self) -> list[str]:
        """Indicators with a real detector behind them, in catalogue order."""
        return [spec.indicator_id for spec in SPECS]

    # -- Dev 3 integration point -------------------------------------------
    def ns_stub(
        self, indicator_id: str, entity_id: str, period_start: date, period_end: date
    ) -> IndicatorResult:
        """Reference stub for a Dev 3 negative-space indicator.

        Returns `NOT_ASSESSABLE` with `value=None`, which the frozen model
        enforces. `missing_fields` is deliberately **empty**, and that is the
        load-bearing decision here: the honest reading of an unwritten detector
        is "nothing was measured", not "the CSE submitted no telemetry". A
        populated gap list would tell an examiner their export was deficient
        when in fact `app/ml/negative_space.py` does not exist yet — the single
        most damaging thing this component could do.

        The note carries `NS_STUB_MARKER` so the read side can exclude these
        from assessability summaries and from every count of gaps. Real Dev 3
        code drops in behind this signature.
        """
        if indicator_id not in NS_SPECS:
            raise KeyError(f"Unknown negative-space indicator {indicator_id!r}")
        name, dimension, _family, needs = NS_SPECS[indicator_id]
        # Built directly rather than via `not_assessable`, because that helper
        # sets `required_fields` and `missing_fields` to the same list. Here they
        # must differ: we can say what the detector will need, but we have not
        # measured whether this entity has it, so nothing is missing — as far as
        # anyone knows, this entity submitted a perfect telemetry export and we
        # simply never looked.
        return IndicatorResult(
            indicator_id=indicator_id,
            entity_id=entity_id,
            period=Period(start=period_start, end=period_end),
            value=None,
            value_units="n/a",
            n=0,
            confidence=0.0,
            assessability=Assessability.NOT_ASSESSABLE,
            required_fields=[needs],
            missing_fields=[],
            source=FindingSource.NEGATIVE_SPACE,
            primary_dimension=dimension,
            family=_family,
            benign_explanations=[],
            is_low_confidence_lead=False,
            notes=(
                f"{NS_STUB_MARKER}: {name} is owned by Developer 3 "
                f"(`app/ml/negative_space.py`) and is not implemented. It will need "
                f"{needs}. Nothing here is a finding, and nothing here should be "
                "read as an absence of evidence in this entity's submission."
            ),
        )

    @staticmethod
    def is_stub(result: IndicatorResult) -> bool:
        """Whether a result came from a placeholder rather than a detector."""
        return NS_STUB_MARKER in (result.notes or "")

    def catalogue(self) -> list[dict[str, Any]]:
        """The published P0 catalogue, for `GET /indicators` and the UI."""
        out: list[dict[str, Any]] = []
        for spec in SPECS:
            out.append(
                {
                    "indicator_id": spec.indicator_id,
                    "name": spec.name,
                    "description": spec.description,
                    "family": spec.family,
                    "primary_dimension": spec.primary.value,
                    "secondary_dimensions": [d.value for d in spec.secondary],
                    "min_tier": spec.min_tier.value,
                    "required_fields": [r.display() for r in spec.required],
                    "adverse_direction": "low" if spec.adverse_sign < 0 else "high",
                    "benign_explanations": list(spec.benign),
                    "source": SOURCE.value,
                    "status": "implemented",
                }
            )
        for indicator_id, (name, dimension, family, needs) in sorted(NS_SPECS.items()):  # noqa: E501
            out.append(
                {
                    "indicator_id": indicator_id,
                    "name": name,
                    "description": "Owned by Developer 3; shipped as a reference stub.",
                    "family": family,
                    "primary_dimension": dimension.value,
                    "secondary_dimensions": [],
                    "min_tier": None,
                    "required_fields": [needs],
                    "adverse_direction": "high",
                    "benign_explanations": [],
                    "source": FindingSource.NEGATIVE_SPACE.value,
                    "status": "reference_stub",
                }
            )
        return out


_engine: RulesEngine | None = None


def get_rules_engine(policy=None) -> RulesEngine:
    """Module-level accessor, matching the other services.

    A passed policy is honoured rather than folded into the singleton, for the
    same reason as the other services: a detector calibrated against one policy
    profile must not be silently reused under another.
    """
    global _engine
    if policy is not None:
        return RulesEngine(policy=policy)
    if _engine is None:
        _engine = RulesEngine()
    return _engine