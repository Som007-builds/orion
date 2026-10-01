"""Canonical field normalisation (plan §3.1).

The pipeline's job at this stage is to turn a vendor's vocabulary into the
canonical one, and to be honest when it cannot.

Four rules from the plan, restated as code:

* **Time** — all timestamps stored UTC ISO-8601. A naive timestamp is localised
  using the profile's declared timezone, then converted. An *offset-aware*
  timestamp is converted as-is. A timestamp we cannot parse is missing, never
  guessed.
* **Severity** — canonical four levels. `INFO`/`INFORMATIONAL` fold into `LOW`.
  An unmapped value is a **quarantine**, not a guess. Guessing is how an
  entity ends up ranked on a severity the CSE never asserted.
* **State** — vendor statuses map through `status_map` into the canonical
  state machine.
* **Missing** — recorded, never imputed. Every normaliser returns `None` for
  absent input and a `reason` explaining why, so the DQ report can attribute a
  gap to a specific cause rather than reporting a bare null rate.

Every function is pure and total. Nothing here touches the database, so the
whole module is testable without fixtures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Sentinels that vendors use to mean "no value". Treated as missing, and
# attributed in the DQ report so "explicitly null" is distinguishable from
# "column absent".
_MISSING_TOKENS = frozenset(
    {"", "-", "--", "n/a", "na", "null", "none", "nil", "nan", "undefined", "tbd"}
)

_TRUE_TOKENS = frozenset({"true", "t", "yes", "y", "1", "on"})
_FALSE_TOKENS = frozenset({"false", "f", "no", "n", "0", "off"})

_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
_DISPOSITIONS = ("TRUE_POSITIVE", "FALSE_POSITIVE", "BENIGN_ANOMALY", "SUPPRESSED")
_ACTOR_TYPES = ("human", "automation", "unknown")
_CASE_EVENT_TYPES = (
    "created",
    "assigned",
    "note_added",
    "status_changed",
    "severity_changed",
    "escalated",
    "closed",
    "reopened",
)
_LINK_TYPES = ("explicit", "inferred")

# Formats seen in real exports, tried in order. Ordered most-specific first:
# "01/02/2026" is genuinely ambiguous and is only reached as a last resort, at
# which point it is flagged as ambiguous rather than silently resolved.
_TS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("iso8601", re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")),
    ("date_only", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("epoch_s", re.compile(r"^\d{9,10}$")),
    ("epoch_ms", re.compile(r"^\d{12,13}$")),
    ("us_style", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}")),
    ("dayfirst", re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}")),
)


class Missing:
    """Sentinel distinguishing "absent" from a legitimate null."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING = Missing()


@dataclass
class Normalised:
    """One normalised cell.

    `value` is the canonical value or `None`. `reason` is populated when the
    input could not be canonicalised, and distinguishes the three failure modes
    that matter to an examiner: absent, unrecognised vocabulary, or malformed.
    """

    value: Any = None
    reason: str | None = None
    absent: bool = False
    sanitised: bool = False

    @property
    def failed(self) -> bool:
        """Input was present but could not be canonicalised."""
        return not self.absent and self.reason is not None


@dataclass
class NormaliseStats:
    """Running tallies for the DQ report, accumulated during normalisation."""

    total_cells: int = 0
    missing_cells: int = 0
    malformed_cells: int = 0
    unmapped_vocabulary_cells: int = 0
    sanitised_cells: int = 0
    per_column_missing: dict[str, int] = field(default_factory=dict)
    per_column_seen: dict[str, int] = field(default_factory=dict)

    def observe(self, column: str, result: Normalised) -> None:
        self.total_cells += 1
        self.per_column_seen[column] = self.per_column_seen.get(column, 0) + 1
        if result.absent:
            self.missing_cells += 1
            self.per_column_missing[column] = self.per_column_missing.get(column, 0) + 1
        if result.failed and result.reason.startswith("malformed"):
            self.malformed_cells += 1
        if result.failed and result.reason.startswith("unmapped"):
            self.unmapped_vocabulary_cells += 1
        if result.sanitised:
            self.sanitised_cells += 1

    def missing_rate(self, column: str | None = None) -> float:
        if column is not None:
            seen = self.per_column_seen.get(column, 0)
            if not seen:
                return 0.0
            return self.per_column_missing.get(column, 0) / seen
        if not self.total_cells:
            return 0.0
        return self.missing_cells / self.total_cells


def clean(value: Any) -> str | None:
    """Trim and fold a raw cell to a string, or `None` if it means nothing."""
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _MISSING_TOKENS:
        return None
    return text


def missing_result(reason: str = "absent") -> Normalised:
    return Normalised(value=None, reason=reason, absent=True)


# --------------------------------------------------------------------- time --
def _zone(name: str) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        # A profile with a bad timezone must not lose its timestamps; fall back
        # to UTC and let the DQ report carry a warning.
        return timezone.utc


def normalise_timestamp(
    value: Any, tz_name: str = "UTC", stats: NormaliseStats | None = None,
    column: str | None = None,
) -> Normalised:
    """Parse a timestamp to timezone-aware UTC.

    Naive input is localised to `tz_name` first, so a CSE exporting local time
    lands on the right instant rather than being shifted by a fixed offset that
    would be wrong across a DST boundary.
    """
    text = clean(value)
    if text is None:
        result = missing_result()
    else:
        result = _parse_timestamp(text, tz_name)

    if stats is not None and column:
        stats.observe(column, result)
    return result


def _parse_timestamp(text: str, tz_name: str) -> Normalised:
    # ISO-8601, with or without offset.
    try:
        iso = text.replace(" ", "T", 1) if " " in text[:11] else text
        if iso.endswith("Z"):
            iso = iso[:-1] + "+00:00"
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        parsed = None

    if parsed is None:
        for kind, pattern in _TS_PATTERNS:
            if not pattern.match(text):
                continue
            try:
                if kind == "iso8601":
                    iso = text.replace(" ", "T", 1)
                    parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                elif kind == "date_only":
                    parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
                elif kind == "epoch_s":
                    parsed = datetime.fromtimestamp(int(text), tz=timezone.utc)
                elif kind == "epoch_ms":
                    parsed = datetime.fromtimestamp(int(text) / 1000.0, tz=timezone.utc)
                elif kind == "us_style":
                    # Genuinely ambiguous. Resolved day-first because the CSE
                    # context is India, and flagged so the examiner can correct
                    # it rather than inherit a silent misreading.
                    day, month, year = (int(p) for p in text.split("/")[:3])
                    parsed = datetime(year, month, day)
                    return Normalised(
                        value=_to_utc(parsed, tz_name),
                        reason="ambiguous_day_first_assumed",
                        absent=False,
                    )
                elif kind == "dayfirst":
                    parsed = datetime.strptime(text, "%d-%b-%Y")
            except (ValueError, OverflowError, OSError):
                parsed = None
                break
            break

    if parsed is None:
        # A trailing "Z"-less fraction the stdlib rejects, e.g. 2026-01-01T00:00:00.1234567
        trimmed = re.sub(r"\.\d{7,}", "", text)
        try:
            parsed = datetime.fromisoformat(trimmed.replace(" ", "T", 1))
        except ValueError:
            return Normalised(
                value=None,
                reason=f"malformed timestamp {text[:40]!r}",
                absent=False,
            )

    return Normalised(value=_to_utc(parsed, tz_name), absent=False)


def _to_utc(parsed: datetime, tz_name: str) -> datetime:
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_zone(tz_name))
    return parsed.astimezone(timezone.utc)


def normalise_date(
    value: Any, tz_name: str = "UTC", stats: NormaliseStats | None = None,
    column: str | None = None,
) -> Normalised:
    result = normalise_timestamp(value, tz_name)
    if result.value is None:
        result.reason = result.reason or "absent"
    else:
        result = Normalised(value=result.value.date(), absent=False, reason=result.reason)
    if stats is not None and column:
        stats.observe(column, result)
    return result


# -------------------------------------------------------------------- scalars --
def normalise_bool(
    value: Any, stats: NormaliseStats | None = None, column: str | None = None
) -> Normalised:
    text = clean(value)
    if text is None:
        result = missing_result()
    else:
        token = text.lower()
        if token in _TRUE_TOKENS:
            result = Normalised(value=True)
        elif token in _FALSE_TOKENS:
            result = Normalised(value=False)
        else:
            result = Normalised(
                value=None,
                reason=f"malformed boolean {text!r}",
                absent=False,
            )
    if stats is not None and column:
        stats.observe(column, result)
    return result


def normalise_int(
    value: Any, stats: NormaliseStats | None = None, column: str | None = None
) -> Normalised:
    text = clean(value)
    if text is None:
        result = missing_result()
    else:
        try:
            result = Normalised(value=int(float(text.replace(",", ""))))
        except (ValueError, OverflowError):
            result = Normalised(
                value=None, reason=f"malformed integer {text!r}", absent=False
            )
    if stats is not None and column:
        stats.observe(column, result)
    return result


def normalise_float(
    value: Any, stats: NormaliseStats | None = None, column: str | None = None
) -> Normalised:
    text = clean(value)
    if text is None:
        result = missing_result()
    else:
        try:
            parsed = float(text.replace(",", ""))
        except ValueError:
            result = Normalised(
                value=None, reason=f"malformed number {text!r}", absent=False
            )
        else:
            # NaN and inf survive float() and poison every downstream median.
            if parsed != parsed or parsed in (float("inf"), float("-inf")):
                result = Normalised(
                    value=None, reason=f"non-finite number {text!r}", absent=False
                )
            else:
                result = Normalised(value=parsed)
    if stats is not None and column:
        stats.observe(column, result)
    return result


def normalise_text(
    value: Any, stats: NormaliseStats | None = None, column: str | None = None
) -> Normalised:
    """Plain text, with CSV-injection neutralisation.

    The `sanitised` flag is carried through so the DQ report can tell an
    examiner that a cell was altered on the way in, rather than the examiner
    finding `'=SUM(...)` in their export and wondering what Orion changed.
    """
    from app.services.upload_security import sanitise_cell

    text = clean(value)
    if text is None:
        result = missing_result()
    else:
        cleaned, was = sanitise_cell(text)
        result = Normalised(value=cleaned, sanitised=was, absent=False)
    if stats is not None and column:
        stats.observe(column, result)
    return result


# --------------------------------------------------------------- vocabularies --
def normalise_severity(
    value: Any,
    severity_map: dict[str, str],
    stats: NormaliseStats | None = None,
    column: str | None = None,
) -> Normalised:
    """Map a vendor severity to the canonical four levels.

    An unmapped value is reported, never guessed. The caller quarantines it.
    """
    text = clean(value)
    if text is None:
        result = missing_result()
    else:
        key = text.lower().strip()
        canonical = severity_map.get(key)
        if canonical is None and key.upper() in _SEVERITIES:
            canonical = key.upper()
        if canonical is None:
            result = Normalised(
                value=None,
                reason=(
                    f"unmapped severity {text!r}; add it to the profile's severity_map "
                    "rather than defaulting it"
                ),
                absent=False,
            )
        elif canonical not in _SEVERITIES:
            result = Normalised(
                value=None,
                reason=f"unmapped severity {text!r} -> {canonical!r} is not canonical",
                absent=False,
            )
        else:
            result = Normalised(value=canonical)
    if stats is not None and column:
        stats.observe(column, result)
    return result


def normalise_status(
    value: Any,
    status_map: dict[str, str],
    stats: NormaliseStats | None = None,
    column: str | None = None,
) -> Normalised:
    """Map a vendor case status into the canonical state machine.

    Unlike severity, a missing case status is *not* fatal: the record still has
    a timestamp and an id, and a downstream indicator may only need those. The
    unmapped case is reported so the gap is visible, and quarantine is the
    caller's decision based on whether the column is required.
    """
    text = clean(value)
    if text is None:
        result = missing_result()
    else:
        key = text.lower().strip().replace(" ", "_").replace("-", "_")
        canonical = status_map.get(key) or status_map.get(key.replace("_", ""))
        if canonical is None:
            result = Normalised(
                value=None,
                reason=f"unmapped status {text!r}",
                absent=False,
            )
        else:
            result = Normalised(value=canonical)
    if stats is not None and column:
        stats.observe(column, result)
    return result


def normalise_enum(
    value: Any,
    allowed: tuple[str, ...],
    field_name: str,
    stats: NormaliseStats | None = None,
    column: str | None = None,
) -> Normalised:
    """Map to a canonical enum, tolerating separator and case drift."""
    text = clean(value)
    if text is None:
        result = missing_result()
    else:
        key = text.strip().upper().replace(" ", "_").replace("-", "_")
        result = (
            Normalised(value=key)
            if key in allowed
            else Normalised(
                value=None,
                reason=f"unmapped {field_name} {text!r}; expected one of {list(allowed)}",
                absent=False,
            )
        )
    if stats is not None and column:
        stats.observe(column, result)
    return result


def normalise_case_event_type(
    value: Any, stats: NormaliseStats | None = None, column: str | None = None
) -> Normalised:
    """Vendor event verbs folded onto the eight canonical transition types."""
    text = clean(value)
    if text is None:
        result = missing_result()
    else:
        key = text.strip().lower().replace(" ", "_").replace("-", "_")
        alias = _EVENT_TYPE_ALIASES.get(key, key)
        result = (
            Normalised(value=alias)
            if alias in _CASE_EVENT_TYPES
            else Normalised(
                value=None,
                reason=f"unmapped case_event type {text!r}",
                absent=False,
            )
        )
    if stats is not None and column:
        stats.observe(column, result)
    return result


_EVENT_TYPE_ALIASES: dict[str, str] = {
    "new": "created",
    "opened": "created",
    "open": "created",
    "create": "created",
    "reopen": "reopened",
    "re_open": "reopened",
    "comment": "note_added",
    "note": "note_added",
    "note_added_to_case": "note_added",
    "add_note": "note_added",
    "assigned_to": "assigned",
    "assign": "assigned",
    "owner_changed": "assigned",
    "state_changed": "status_changed",
    "status_change": "status_changed",
    "changed_status": "status_changed",
    "severity": "severity_changed",
    "changed_severity": "severity_changed",
    "escalation": "escalated",
    "close": "closed",
    "resolve": "resolved",
}


# ------------------------------------------------------------ column contracts --
@dataclass(frozen=True)
class ColumnRule:
    """One canonical column's normalisation contract.

    Kept declarative so the polars schema, the INSERT, the validation pass and
    the DQ report are all generated from one source. A column that exists in
    the DuckDB DDL but has no rule here is a bug, not a passthrough.
    """

    name: str
    kind: str  # string|int|float|bool|timestamp|date|ip|hostname|enum
    required: bool = False
    enum: tuple[str, ...] = ()
    enum_field: str = ""
    mapper: str = ""  # severity|status|disposition|actor|link|event_type
    pseudonymous: str = ""  # analyst|ip|hostname|asset
    redact: bool = False  # free text routed to note_store
    injected: bool = False  # supplied by the pipeline, never by the submission
    derived: bool = False  # computed by the pipeline from other columns
    polars_type: str = "pl.String"

    def normaliser(
        self, tz_name: str, severity_map: dict[str, str], status_map: dict[str, str]
    ) -> Callable[[Any], Normalised]:
        if self.kind == "timestamp":
            return lambda v: normalise_timestamp(v, tz_name)
        if self.kind == "date":
            return lambda v: normalise_date(v, tz_name)
        if self.kind == "bool":
            return normalise_bool
        if self.kind == "int":
            return normalise_int
        if self.kind == "float":
            return normalise_float
        if self.mapper == "severity":
            return lambda v: normalise_severity(v, severity_map)
        if self.mapper == "status":
            return lambda v: normalise_status(v, status_map)
        if self.mapper == "disposition":
            return lambda v: normalise_enum(v, _DISPOSITIONS, "disposition")
        if self.mapper == "actor":
            from app.services.pseudonymisation import get_pseudonymiser

            ps = get_pseudonymiser()

            def classify(v: Any) -> Normalised:
                text = clean(v)
                if text is None:
                    return missing_result()
                actor_type, _inferred = ps.classify_actor_type(text)
                return Normalised(value=actor_type, absent=False)

            return classify
        if self.mapper == "link":
            return lambda v: normalise_enum(v, _LINK_TYPES, "link_type")
        if self.mapper == "event_type":
            return normalise_case_event_type
        if self.redact:
            return normalise_text
        return normalise_text


def _c(
    name: str,
    kind: str,
    *,
    required: bool = False,
    enum: tuple[str, ...] = (),
    mapper: str = "",
    pseudonymous: str = "",
    redact: bool = False,
    injected: bool = False,
    derived: bool = False,
    polars_type: str = "pl.String",
) -> ColumnRule:
    return ColumnRule(
        name=name,
        kind=kind,
        required=required,
        enum=enum,
        enum_field=name,
        mapper=mapper,
        pseudonymous=pseudonymous,
        redact=redact,
        injected=injected,
        derived=derived,
        polars_type=polars_type,
    )


_INT = "pl.Int64"
_BOOL = "pl.Boolean"
_TS = "pl.Datetime(\"us\")"
_DATE = "pl.Date"

# Column order matches the DuckDB DDL exactly. Positional INSERTs depend on it.
TABLE_CONTRACTS: dict[str, tuple[ColumnRule, ...]] = {
    "alert_record": (
        _c("alert_id", "string", required=True),
        _c("entity_id", "string", required=True, injected=True),
        # `timestamp` is the v1 field, retained for compatibility. Mirrored from
        # `event_ts` so old readers keep working; never the reverse, and never
        # invented when `event_ts` is absent.
        _c("timestamp", "timestamp", derived=True, polars_type=_TS),
        _c("event_ts", "timestamp", polars_type=_TS),
        _c("title", "string"),
        _c("severity_raw", "string"),
        _c("severity_norm", "string", mapper="severity", derived=True),
        _c("rule_id", "string"),
        _c("source_tool", "string", injected=True),
        _c("mitre_tactic", "string"),
        _c("mitre_technique_id", "string"),
        _c("source_ip_pseudo", "string", pseudonymous="ip"),
        _c("destination_asset_id", "string", pseudonymous="asset"),
        _c("asset_criticality", "int", polars_type=_INT),
        _c("status", "string", mapper="status"),
        _c("auto_closed_flag", "bool", polars_type=_BOOL),
        _c("closed_by_pseudo", "string", pseudonymous="analyst"),
        _c("case_id", "string"),
    ),
    "case_record": (
        _c("case_id", "string", required=True),
        _c("entity_id", "string", required=True, injected=True),
        _c("created_at", "timestamp", polars_type=_TS),
        _c("closed_at", "timestamp", polars_type=_TS),
        # Derived only when both timestamps are present. A case with no closure
        # time has no duration; that is a fact about the data, not a gap to fill.
        _c("investigation_duration_sec", "int", derived=True, polars_type=_INT),
        _c("analyst_pseudo", "string", pseudonymous="analyst"),
        _c("closed_by_pseudo", "string", pseudonymous="analyst"),
        _c("actor_type", "string", mapper="actor"),
        _c("severity_norm", "string", mapper="severity", derived=True),
        _c("escalation_level", "int", polars_type=_INT),
        _c("disposition", "string", mapper="disposition"),
        _c("root_cause_action", "string"),
        # Not injected: the submission supplies the note *text* into this column,
        # and stage 8 replaces it with a `note_ref` after redacting and storing
        # the text. Marking it injected would skip it entirely, and the notes
        # would be silently discarded.
        _c("note_ref", "string", redact=True),
    ),
    "case_event": (
        _c("event_id", "string", required=True),
        _c("case_id", "string", required=True),
        _c("ts", "timestamp", polars_type=_TS),
        _c("event_type", "string", mapper="event_type"),
        _c("actor_pseudo", "string", pseudonymous="analyst"),
        _c("actor_type", "string", mapper="actor"),
        _c("from_state", "string", mapper="status"),
        _c("to_state", "string", mapper="status"),
        # See `case_record.note_ref` — redact-only, not injected.
        _c("note_ref", "string", redact=True),
    ),
    "escalation": (
        # See `duckdb_client.escalation`: injected, because an escalation is
        # only attributable once the pipeline says whose submission it came
        # from. `case_id` alone cannot scope it -- case numbers are unique per
        # submission, not across the lake, and peer cohorts routinely overlap.
        _c("entity_id", "string", required=True, injected=True),
        _c("escalation_id", "string", required=True),
        _c("case_id", "string", required=True),
        _c("ts", "timestamp", polars_type=_TS),
        _c("target", "string"),
        _c("reason_code", "string"),
        _c("acknowledged_ts", "timestamp", polars_type=_TS),
        _c("outcome", "string"),
    ),
    "telemetry_daily": (
        _c("entity_id", "string", required=True, injected=True),
        _c("asset_id_pseudo", "string", required=True, pseudonymous="asset"),
        _c("source_type", "string", required=True),
        _c("date", "date", required=True, polars_type=_DATE),
        _c("event_count", "int", polars_type=_INT),
        _c("last_seen_ts", "timestamp", polars_type=_TS),
    ),
    "alert_case": (
        _c("alert_id", "string", required=True),
        _c("case_id", "string", required=True),
        _c("link_type", "string", mapper="link"),
    ),
}

# Primary keys, used for uniqueness checking and record_version keys.
PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "alert_record": ("alert_id",),
    "case_record": ("case_id",),
    "case_event": ("event_id",),
    "escalation": ("escalation_id",),
    "telemetry_daily": ("asset_id_pseudo", "source_type", "date"),
    "alert_case": ("alert_id", "case_id"),
}

# Columns whose unmapped value is fatal to the row rather than merely reported.
# Severity is fatal because a case with an unknown severity cannot be compared
# to its peers; status is not, because the timestamp carries the indicator.
QUARANTINE_ON_UNMAPPED: dict[str, frozenset[str]] = {
    "alert_record": frozenset({"severity_norm", "severity_raw"}),
    "case_record": frozenset({"severity_norm"}),
    "case_event": frozenset({"event_type"}),
    "escalation": frozenset(),
    "telemetry_daily": frozenset(),
    "alert_case": frozenset({"link_type"}),
}

DATA_TIER_TABLES: dict[str, str] = {
    "A": "alert_record + case_record",
    "B": "case_event + escalation",
    "C": "telemetry_daily + asset",
}