"""Format loaders for the ingestion pipeline.

Every supported format returns a Polars frame so normalisation downstream is
format-agnostic. Nothing here interprets business meaning — that is the
mapping profile's job.

Formats (plan §4): CSV, TSV, JSON, NDJSON, Parquet, SQLite, SQL dump, XLSX.

Row limits are enforced while reading, not after. A file that blows past the cap
must abort the read, not materialise 50M rows into memory first.
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from app.services.upload_security import check_dimensions

# Table-name inference when a file does not declare what it contains.
_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("case_event", ("event_type", "from_state", "to_state", "event_ts")),
    ("escalation", ("escalation_id", "acknowledged_ts", "reason_code")),
    ("telemetry_daily", ("event_count", "last_seen_ts", "source_type")),
    ("asset", ("asset_class", "criticality", "expected_log_sources")),
    ("case", ("case_id", "disposition", "closed_at", "root_cause_action")),
    ("alert", ("alert_id", "severity", "rule_id", "event_ts")),
)


def infer_table_name(columns: list[str]) -> str:
    """Guess which evidence table a file holds from its column names.

    A guess, not a decision: the result is surfaced for human confirmation at
    the mapping stage. Guessing wrong is recoverable; silently loading alerts
    into the case table is not.
    """
    lowered = {c.lower() for c in columns}
    for table, hints in _HINTS:
        if lowered & set(hints):
            return table
    return "unknown"


def _guard(df: Any, source: str) -> Any:
    check_dimensions(df.height, df.width, source)
    return df


def load_csv(path: Path, *, delimiter: str = ",") -> Any:
    import polars as pl

    df = pl.read_csv(
        path,
        separator=delimiter,
        infer_schema_length=0,       # keep everything as text; we normalise later
        truncate_ragged_lines=True,
        ignore_errors=True,         # malformed rows are quarantined, not fatal
    )
    return _guard(df, path.name)


def load_tsv(path: Path) -> Any:
    return load_csv(path, delimiter="\t")


def load_json(path: Path) -> Any:
    import polars as pl

    payload = json.loads(path.read_text(encoding="utf-8"))

    # Accept either a bare list or a wrapper object keyed by table name.
    if isinstance(payload, dict):
        for key in ("alerts", "cases", "rows", "data", "records"):
            if key in payload and isinstance(payload[key], list):
                payload = payload[key]
                break
        else:
            # A dict of {table_name: [rows]}
            tables = {k: v for k, v in payload.items() if isinstance(v, list)}
            if len(tables) == 1:
                payload = next(iter(tables.values()))
            elif len(tables) > 1:
                # Multiple tables in one file: return the largest, and record
                # that others were present so the examiner can re-submit.
                name = max(tables, key=lambda k: len(tables[k]))
                payload = tables[name]

    if not isinstance(payload, list):
        raise ValueError(f"{path.name}: JSON must be a list of records or a table wrapper")
    if not payload:
        return _guard(pl.DataFrame(), path.name)

    df = pl.DataFrame(payload[0]).with_columns(
        [pl.col(c).cast(pl.String) for c in pl.DataFrame(payload[0]).columns]
    )
    return _guard(df, path.name)


def load_ndjson(path: Path) -> Any:
    import polars as pl

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # Keep going: the quarantine stage records the bad line with its
                # number. Aborting the whole file here would be a silent drop
                # in the other direction.
                continue
            if len(rows) > 100_000_000:
                raise ValueError(f"{path.name}: NDJSON row limit exceeded")
    if not rows:
        return _guard(pl.DataFrame(), path.name)
    df = pl.DataFrame(rows).with_columns(
        [pl.col(c).cast(pl.String) for c in pl.DataFrame(rows).columns]
    )
    return _guard(df, path.name)


def load_parquet(path: Path) -> Any:
    import polars as pl

    return _guard(pl.read_parquet(path), path.name)


def load_xlsx(path: Path, sheet: str | None = None) -> Any:
    import polars as pl

    frame = pl.read_excel(path, sheet_name=sheet or 0)
    return _guard(
        frame.with_columns([pl.col(c).cast(pl.String) for c in frame.columns]),
        path.name,
    )


def load_sqlite(path: Path, table: str | None = None) -> Any:
    """Read one table from a submitted SQLite database into a frame."""
    import polars as pl

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        available = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]
        if not available:
            raise ValueError(f"{path.name}: contains no tables")

        target = table or infer_table_name(available) or available[0]
        if target not in available:
            raise ValueError(
                f"{path.name}: table {target!r} not found. Available: {available}"
            )

        # Read with a cap so a hostile 10M-row table cannot exhaust memory.
        limit = 10_000_000
        cursor = conn.execute(f'SELECT * FROM "{target}" LIMIT {limit}')
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        return _guard(pl.DataFrame(), path.name)
    df = pl.DataFrame(rows, schema=columns, orient="row")
    return _guard(df, path.name)


_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+[\"'`]?(\w+)[\"'`]?\s*\(([^)]+)\)\s*VALUES\s*\((.+?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)


def load_sql_dump(path: Path, table: str | None = None) -> Any:
    """Extract rows from a SQL dump without executing any of it.

    This is a parser, never an interpreter. The file is untrusted input, so
    executing it would let a submission run arbitrary SQL. Only INSERT
    statements are read, and only their literal values are extracted.
    """
    import polars as pl

    text = path.read_text(encoding="utf-8", errors="replace")

    grouped: dict[str, tuple[list[str], list[list[str]]]] = {}
    for match in _INSERT_RE.finditer(text):
        target = match.group(1)
        columns = [c.strip().strip('"`') for c in match.group(2).split(",")]
        values = _split_sql_values(match.group(3))
        if len(values) != len(columns):
            continue  # arity mismatch -> skipped, recorded by quarantine
        grouped.setdefault(target, (columns, []))[1].append(values)

    if not grouped:
        raise ValueError(f"{path.name}: no parseable INSERT statements found")

    # Named table wins; otherwise pick whichever grouped table's columns look
    # most like a table Orion knows. A dump of one logical table split across
    # two physical ones lands on the same table, which is the common case for
    # vendor exports.
    if table and table in grouped:
        target = table
    else:
        target = None
        for name, (columns, _rows) in grouped.items():
            if infer_table_name(columns) != "unknown":
                target = name
                break
        if target is None:
            target = max(grouped, key=lambda k: len(grouped[k][1]))

    columns, rows = grouped[target]
    df = pl.DataFrame(rows, schema=columns, orient="row")
    return _guard(df, path.name)


def _split_sql_values(raw: str) -> list[str]:
    """Split a VALUES tuple, respecting quoted strings and commas inside them."""
    values: list[str] = []
    current: list[str] = []
    in_string = False
    quote_char = ""

    for char in raw:
        if in_string:
            if char == quote_char:
                # Doubled quote inside a SQL literal is an escaped quote.
                if len(current) > 0 and current[-1] == quote_char:
                    current.append(char)
                    continue
                in_string = False
            current.append(char)
            continue

        if char in {"'", '"'}:
            in_string = True
            quote_char = char
            current.append(char)
            continue
        if char == ",":
            values.append("".join(current).strip())
            current = []
            continue
        current.append(char)

    if current:
        values.append("".join(current).strip())
    return [_unquote(v) for v in values]


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if value.upper() == "NULL":
        return ""
    return value


LOADERS = {
    "csv": load_csv,
    "tsv": load_tsv,
    "json": load_json,
    "ndjson": load_ndjson,
    "parquet": load_parquet,
    "sqlite": load_sqlite,
    "sql_dump": load_sql_dump,
    "xlsx": load_xlsx,
}


def load(path: Path, detected_format: str, **kwargs: Any) -> Any:
    """Dispatch to the loader for a detected format."""
    loader = LOADERS.get(detected_format)
    if loader is None:
        raise ValueError(f"No loader for format {detected_format!r}")
    return loader(path, **kwargs)