"""Data-quality scoring and submission tiering (plan §3.15, §6).

DQ is not a compliment. A high score means "this submission can support the
indicators it claims to support"; a low one means the opposite. It is never
presented as a quality verdict on the entity, and it is deliberately kept
separate from the assessability matrix: **DQ describes the submission, and
assessability describes the dimension.**

Six components, weighted from the active policy profile:

| Component | Measures |
|---|---|
| `completeness` | required columns populated |
| `validity` | cells that parsed, and rows that survived validation |
| `timeliness` | latest evidence vs the period end, and submission lag |
| `consistency` | cross-table referential integrity |
| `uniqueness` | duplicate primary keys |
| `mapping_confidence` | share of columns from an approved profile |

Every component returns a score in [0, 1] **and** a plain-language detail
string. The detail matters as much as the number: an examiner looking at
`timeliness 0.4` learns nothing, while "latest telemetry is 34 days before the
period end" tells them exactly what they can and cannot conclude.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.schemas.common import DataTier
from app.schemas.ingestion import DQComponentOut, DQReportOut, QuarantineSummaryOut
from app.schemas.ingestion import PipelineStage
from app.services.normalisation import NormaliseStats

# Timeliness is scored against the period end, not "now": a Q1 submission
# reviewed in Q2 is on time, and scoring it against the wall clock would punish
# every late-arriving historical submission.
STALE_AFTER = timedelta(days=7)
VERY_STALE_AFTER = timedelta(days=30)
MAX_SUBMISSION_LAG = timedelta(days=45)

# Tables required for each data tier (plan §3.15). Tier is the *highest* tier
# whose full requirement set is satisfied, so a submission with alerts, cases
# and escalations is tier A, not "A because it had alerts".
TIER_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "A": ("alert_record", "case_record"),
    "B": ("case_event", "escalation"),
    "C": ("telemetry_daily",),
}


@dataclass
class DQInputs:
    """Everything the scorer needs, gathered once during the run."""

    submission_id: str
    entity_id: str
    period_start: date
    period_end: date
    received_ts: datetime
    tables_loaded: dict[str, int]
    tables_present: set[str]
    stats: NormaliseStats
    n_quarantined: int
    quarantine_by_stage: dict[str, dict[str, int]] = field(default_factory=dict)
    quarantine_samples: dict[str, list[str]] = field(default_factory=dict)
    duplicate_primary_keys: dict[str, int] = field(default_factory=dict)
    dangling_references: dict[str, int] = field(default_factory=dict)
    latest_evidence_ts: datetime | None = None
    mapping_profile_id: str | None = None
    approved_column_count: int = 0
    total_column_count: int = 0
    warnings: list[str] = field(default_factory=list)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


# ------------------------------------------------------------------ components --
def score_completeness(inp: DQInputs, required_columns: dict[str, list[str]]) -> tuple[float, str]:
    """Mean per-column fill rate over the required columns.

    Each required column is scored on its own units — filled cells over cells
    seen — and the columns are averaged. An earlier version summed filled cells
    and divided by the *number of columns*, which mixed units and could report a
    negative percentage; averaging per column keeps the ratio meaningful no
    matter how the tables differ in size.

    Keys are namespaced `table.column` because the same canonical name means
    different things on different tables: an alert's `case_id` is routinely
    absent, and crediting that against the case table's `case_id` would report
    a gap the CSE never had.

    A column never seen at all scores zero rather than being excluded. That is
    the difference between "this CSE left a field empty" and "this CSE does not
    submit this field", and the two warrant different conclusions.
    """
    seen = inp.stats.per_column_seen
    missing = inp.stats.per_column_missing

    scores: list[float] = []
    absent_columns: list[str] = []
    thin_columns: list[tuple[str, float]] = []

    for table, columns in required_columns.items():
        if table not in inp.tables_loaded:
            continue
        for column in columns:
            key = f"{table}.{column}"
            observed = seen.get(key, 0)
            if observed == 0:
                absent_columns.append(key)
                scores.append(0.0)
                continue
            rate = 1.0 - (missing.get(key, 0) / observed)
            scores.append(rate)
            if rate < 0.9:
                thin_columns.append((key, rate))

    if not scores:
        return 1.0, "no required fields declared for this submission"

    score = _clamp(sum(scores) / len(scores))

    if absent_columns:
        detail = (
            f"{len(absent_columns)} of {len(scores)} required column(s) were entirely "
            f"absent: {', '.join(sorted(absent_columns)[:6])}"
            + (" ..." if len(absent_columns) > 6 else "")
        )
    elif thin_columns:
        worst = sorted(thin_columns, key=lambda item: item[1])[:4]
        detail = "required column(s) mostly empty: " + ", ".join(
            f"{name} {(1 - rate) * 100:.0f}% empty" for name, rate in worst
        )
    else:
        detail = (
            f"all {len(scores)} required column(s) fully populated across "
            f"{sum(inp.tables_loaded.values())} row(s)"
        )
    return score, detail


def score_validity(inp: DQInputs) -> tuple[float, str]:
    """Cells that parsed and rows that survived validation.

    Quarantine counts against validity twice over, because a quarantined row
    costs the examiner both the row and the trust that it was counted: the row
    is missing from every downstream median, and nobody knows unless they read
    the quarantine table.
    """
    stats = inp.stats
    total_rows = sum(inp.tables_loaded.values())
    if total_rows == 0:
        return 0.0, "no rows were loaded at all"

    quarantine_rate = inp.n_quarantined / (total_rows + inp.n_quarantined)
    malformed_rate = (
        stats.malformed_cells / stats.total_cells if stats.total_cells else 0.0
    )
    unmapped_rate = (
        stats.unmapped_vocabulary_cells / stats.total_cells if stats.total_cells else 0.0
    )

    score = _clamp(1.0 - (quarantine_rate + malformed_rate + unmapped_rate) / 2.0)

    parts = [f"{inp.n_quarantined} of {total_rows + inp.n_quarantined} rows quarantined"]
    if malformed_rate:
        parts.append(f"{malformed_rate * 100:.1f}% of cells were malformed")
    if unmapped_rate:
        parts.append(f"{unmapped_rate * 100:.1f}% used vocabulary absent from the map")
    if stats.sanitised_cells:
        parts.append(f"{stats.sanitised_cells} cells sanitised for spreadsheet formulas")

    return score, "; ".join(parts)


def score_timeliness(inp: DQInputs) -> tuple[float, str]:
    """Evidence recency against the declared period end, plus submission lag."""
    period_end_dt = datetime.combine(
        inp.period_end, datetime.min.time(), tzinfo=timezone.utc
    )

    parts: list[str] = []
    penalties: list[float] = []

    if inp.latest_evidence_ts is None:
        penalties.append(1.0)
        parts.append("no timestamped evidence in the submission")
    else:
        lag = period_end_dt - inp.latest_evidence_ts
        if lag <= timedelta(0):
            penalties.append(0.0)
            parts.append("evidence extends to or past the declared period end")
        elif lag <= STALE_AFTER:
            penalties.append(0.15)
            parts.append(f"latest evidence is {lag.days}d before period end")
        elif lag <= VERY_STALE_AFTER:
            penalties.append(0.5)
            parts.append(f"latest evidence is {lag.days}d stale")
        else:
            penalties.append(1.0)
            parts.append(f"latest evidence is {lag.days}d stale — over a month")

    submission_lag = inp.received_ts - period_end_dt
    if submission_lag <= timedelta(0):
        parts.append("submitted on or before the period end")
    elif submission_lag <= MAX_SUBMISSION_LAG:
        penalties.append(0.2)
        parts.append(f"submitted {submission_lag.days}d after period end")
    else:
        penalties.append(0.6)
        parts.append(
            f"submitted {submission_lag.days}d after period end, well past the "
            f"{MAX_SUBMISSION_LAG.days}d expectation"
        )

    score = _clamp(1.0 - sum(penalties) / 2.0)
    return score, "; ".join(parts)


def score_consistency(inp: DQInputs) -> tuple[float, str]:
    """Cross-table referential integrity.

    An alert pointing at a case that was never submitted is not a harmless
    gap: EG-04 (severity history) and EG-16 (severity drift) both join through
    that link, so a dangling reference silently shrinks the cohort those
    indicators can compare against.
    """
    total_dangling = sum(inp.dangling_references.values())
    total_refs = total_dangling + sum(inp.tables_loaded.get(t, 0) for t in
                                      ("alert_record", "case_record", "alert_case"))
    if total_refs == 0:
        return 1.0, "no cross-table references declared in this submission"

    score = _clamp(1.0 - total_dangling / total_refs)
    if total_dangling == 0:
        return 1.0, "every cross-table reference resolves"
    detail = ", ".join(
        f"{count} dangling {table} reference(s)" for table, count in
        sorted(inp.dangling_references.items()) if count
    )
    return score, detail


def score_uniqueness(inp: DQInputs) -> tuple[float, str]:
    """Duplicate primary keys within the submission.

    A duplicate primary key means two rows claim the same identity. Either the
    submission contains an export artefact or the CSE reused ids across
    periods; both break per-record versioning, so it must be visible.
    """
    total_rows = sum(inp.tables_loaded.values())
    total_dupes = sum(inp.duplicate_primary_keys.values())
    if total_rows == 0:
        return 1.0, "no rows loaded"

    score = _clamp(1.0 - total_dupes / total_rows)
    if total_dupes == 0:
        return 1.0, f"all {total_rows} primary keys unique"
    detail = ", ".join(
        f"{count} duplicate {table} key(s)" for table, count in
        sorted(inp.duplicate_primary_keys.items()) if count
    )
    return score, detail


def score_mapping_confidence(inp: DQInputs) -> tuple[float, str]:
    """How much of the submission was understood by an approved mapping."""
    if inp.total_column_count == 0:
        return 0.0, "no columns were mapped, so nothing in this submission is interpretable"

    ratio = inp.approved_column_count / inp.total_column_count
    if inp.mapping_profile_id:
        detail = (
            f"{inp.approved_column_count}/{inp.total_column_count} columns resolved by "
            f"approved profile {inp.mapping_profile_id}"
        )
    else:
        detail = (
            f"{inp.approved_column_count}/{inp.total_column_count} columns resolved by "
            "ad-hoc approval with no named vendor profile"
        )
    return _clamp(ratio), detail


# -------------------------------------------------------------------- tiering --
def determine_data_tier(
    tables_present: set[str], tables_loaded: dict[str, int]
) -> DataTier:
    """Highest tier whose full requirement set is loaded with rows.

    Deliberately all-or-nothing per tier: alerts without cases is not "tier A
    with a gap", it is tier C, because every case-dependent indicator is
    uncomputable and the examiner needs to be told so by the tier rather than
    discovering it per-finding.
    """
    populated = {t for t, n in tables_loaded.items() if n > 0}
    for tier in ("A", "B", "C"):
        if set(TIER_REQUIREMENTS[tier]) <= (tables_present & populated):
            return DataTier(tier)
    # Nothing satisfied a full tier: report the lowest, since there is evidence,
    # and let assessability carry the detail.
    return DataTier("C")


def interpret(dq: float, tier: DataTier, n_quarantined: int, n_loaded: int) -> str:
    """Plain-language statement of what this submission can support.

    Written to be read by a supervisor, and deliberately free of anything that
    sounds like a judgement of the CSE.
    """
    if n_loaded == 0:
        return (
            "No rows were loaded from this submission. Nothing can be assessed from it. "
            "The rejected rows are listed in the quarantine table so the submission can "
            "be corrected and resent."
        )

    parts = [
        f"Tier {tier.value} ({', '.join(TIER_REQUIREMENTS[tier.value])}).",
        f"{n_loaded} row(s) loaded.",
    ]
    if n_quarantined:
        parts.append(
            f"{n_quarantined} row(s) quarantined and excluded from every calculation "
            f"({n_quarantined / (n_loaded + n_quarantined) * 100:.1f}% of the submission)."
        )

    if dq >= 0.85:
        parts.append(
            "The submission is largely complete and self-consistent, and can support "
            "most of the P0 indicators for its declared dimensions."
        )
    elif dq >= 0.6:
        parts.append(
            "The submission supports a subset of indicators. Dimensions depending on the "
            "gaps above will report as not assessable rather than as low risk."
        )
    elif dq >= 0.35:
        parts.append(
            "The submission is substantially incomplete. Treat any score derived from it "
            "as provisional; a resubmission would materially change what can be assessed."
        )
    else:
        parts.append(
            "The submission is too incomplete to support scoring. It is retained in full "
            "for the audit record, and no indicator should be read from it."
        )

    return " ".join(parts)


# --------------------------------------------------------------------- facade --
def build_dq_report(
    inp: DQInputs,
    weights: dict[str, float],
    required_columns: dict[str, list[str]],
) -> DQReportOut:
    """Compute all six components, the weighted score, and the tier."""
    components: list[tuple[str, float, str]] = [
        ("completeness", *score_completeness(inp, required_columns)),
        ("validity", *score_validity(inp)),
        ("timeliness", *score_timeliness(inp)),
        ("consistency", *score_consistency(inp)),
        ("uniqueness", *score_uniqueness(inp)),
        ("mapping_confidence", *score_mapping_confidence(inp)),
    ]

    total_weight = sum(weights.get(name, 0.0) for name, _, _ in components)
    if total_weight <= 0:
        raise ValueError("Policy profile declares no DQ component weights")

    dq_score = sum(
        score * weights.get(name, 0.0) for name, score, _ in components
    ) / total_weight

    tier = determine_data_tier(inp.tables_present, inp.tables_loaded)

    quarantine_summary = [
        QuarantineSummaryOut(
            stage=PipelineStage(stage),
            reason=reason,
            count=count,
            sample_row_refs=inp.quarantine_samples.get(stage, [])[:5],
        )
        for stage, reasons in sorted(inp.quarantine_by_stage.items())
        for reason, count in sorted(reasons.items())
    ]

    return DQReportOut(
        submission_id=inp.submission_id,
        entity_id=inp.entity_id,
        dq_score=round(_clamp(dq_score), 4),
        components=[
            DQComponentOut(
                component=name,
                score=round(score, 4),
                weight=weights.get(name, 0.0),
                detail=detail,
            )
            for name, score, detail in components
        ],
        data_tier=tier,
        n_rows_loaded=sum(inp.tables_loaded.values()),
        n_rows_quarantined=inp.n_quarantined,
        quarantine_summary=quarantine_summary,
        warnings=list(inp.warnings),
        interpretation=interpret(
            _clamp(dq_score), tier, inp.n_quarantined, sum(inp.tables_loaded.values())
        ),
    )