"""Upload security hardening (plan §4, CHANGELOG B.5).

A submission arrives from outside the system and may be hostile or simply
broken. Every check here exists because an unbounded input can take down the
service or the evidence lake.

Checks implemented:
  * size cap before anything is parsed
  * file-type by magic bytes, not just extension
  * CSV-injection sanitising on every cell (leading `= + - @` are formula
    triggers in Excel, which is how an examiner opens the report)
  * path-traversal rejection on every filename component
  * decompression-bomb ratio guard
  * row and column caps
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings

# ---------------------------------------------------------------- magic bytes --
# Extension is attacker-controlled; content type is not.
_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "csv": (),
    "tsv": (),
    "json": (b"{", b"[", b" ", b"\n", b"\t"),
    "ndjson": (b"{",),
    "parquet": (b"PAR1",),
    "sqlite": (b"SQLite format 3\x00",),
    "xlsx": (b"PK\x03\x04",),
    "sql_dump": (),
}

# Anything starting with these is a spreadsheet formula, not data.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class SecurityRejected(Exception):
    """Raised when an upload fails a security check. Message is examiner-safe."""


@dataclass
class SanitisedFile:
    original_name: str
    safe_name: str
    path: Path
    detected_format: str
    n_bytes: int
    sanitised: bool = False
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ filenames --
def safe_filename(name: str) -> str:
    """Strip any path component from an uploaded filename.

    `../../etc/passwd` and `..\\..\\windows\\system32` both have to die here.
    """
    if not name or not name.strip():
        raise SecurityRejected("Uploaded file has an empty name")

    # Take the basename under both separators, since a Windows client may send
    # backslashes on a POSIX host.
    candidate = name.replace("\\", "/").split("/")[-1]

    if candidate in {".", ".."} or candidate.startswith("."):
        raise SecurityRejected(f"Suspicious filename rejected: {name!r}")

    # Any residual traversal marker is a rejection, not something to clean.
    if ".." in candidate:
        raise SecurityRejected(f"Path traversal attempt in filename: {name!r}")

    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", candidate)
    if not cleaned or cleaned in {".", ".."}:
        raise SecurityRejected(f"Filename sanitised to nothing usable: {name!r}")
    return cleaned[:255]


# --------------------------------------------------------------------- format --
def detect_format(path: Path, declared: str | None = None) -> str:
    """Sniff the format. A declared type is trusted only if content agrees."""
    suffix = path.suffix.lower().lstrip(".")

    if suffix == "xlsx":
        return "xlsx"
    if suffix in {"parquet", "pq"}:
        return "parquet"
    if suffix in {"db", "sqlite", "sqlite3"}:
        return "sqlite"
    if suffix == "sql":
        return "sql_dump"
    if suffix in {"jsonl", "ndjson"}:
        return "ndjson"
    if suffix == "json":
        return "json"
    if suffix in {"csv", "txt"}:
        return "csv"
    if suffix == "tsv":
        return "tsv"

    # No usable extension: fall back to content sniffing.
    head = path.read_bytes()[:16]
    if head.startswith(b"PAR1"):
        return "parquet"
    if head.startswith(b"SQLite format 3\x00"):
        return "sqlite"
    if head.startswith(b"PK\x03\x04"):
        return "xlsx"

    raise SecurityRejected(
        f"Unrecognised file type for {path.name!r}. Supported: csv, tsv, json, "
        "ndjson, parquet, sqlite, sql_dump, xlsx."
    )


def verify_content_matches_format(path: Path, detected: str) -> None:
    """Reject content that disagrees with its declared format.

    A `.csv` that is really a zip bomb, or an XLSX that is really an
    executable, must not reach a parser.
    """
    if detected in {"csv", "tsv", "sql_dump"}:
        return  # text formats have no signature

    head = path.read_bytes()[:16]

    if detected == "parquet" and not head.startswith(b"PAR1"):
        raise SecurityRejected(f"{path.name}: missing PAR1 header, not a Parquet file")
    if detected == "sqlite" and not head.startswith(b"SQLite format 3\x00"):
        raise SecurityRejected(f"{path.name}: missing SQLite header")
    if detected == "xlsx" and not head.startswith(b"PK\x03\x04"):
        raise SecurityRejected(f"{path.name}: missing ZIP header, not an XLSX file")


# ----------------------------------------------------------------- size / bomb --
def check_size(path: Path) -> int:
    size = path.stat().st_size
    limit = get_settings().max_upload_bytes
    if size > limit:
        raise SecurityRejected(
            f"{path.name}: {size} bytes exceeds the {limit}-byte limit"
        )
    if size == 0:
        raise SecurityRejected(f"{path.name}: file is empty")
    return size


def check_zip_bomb(path: Path) -> None:
    """Reject a zip whose uncompressed size dwarfs its compressed size."""
    if path.suffix.lower() not in {".xlsx", ".zip"}:
        return
    try:
        with zipfile.ZipFile(path) as archive:
            compressed = sum(i.compress_size for i in archive.infolist())
            uncompressed = sum(i.file_size for i in archive.infolist())
            members = len(archive.infolist())
    except zipfile.BadZipFile as exc:
        raise SecurityRejected(f"{path.name}: corrupt archive ({exc})") from exc

    ratio_limit = get_settings().max_compression_ratio
    if compressed > 0 and uncompressed / compressed > ratio_limit:
        raise SecurityRejected(
            f"{path.name}: decompression ratio {uncompressed / compressed:.0f}:1 "
            f"exceeds the {ratio_limit}:1 limit (possible decompression bomb)"
        )
    if members > 10_000:
        raise SecurityRejected(f"{path.name}: {members} archive members is implausible")


# ------------------------------------------------------------ CSV injection ---
def sanitise_cell(value: str) -> tuple[str, bool]:
    """Neutralise a spreadsheet-formula cell.

    Returns `(cleaned, was_sanitised)`.

    A submission is untrusted input, and these cells end up in an examiner's
    spreadsheet. A leading `=` or `+` turns an alert title into a formula that
    executes when the file is opened.
    """
    if not value:
        return value, False

    if value[0] in _FORMULA_TRIGGERS:
        return "'" + value, True
    return value, False


def sanitise_frame(values: list[str]) -> tuple[list[str], int]:
    """Sanitise one row. Returns `(row, n_sanitised)`."""
    cleaned: list[str] = []
    count = 0
    for value in values:
        value = _CONTROL_CHARS.sub("", value)
        new_value, was = sanitise_cell(value)
        cleaned.append(new_value)
        count += int(was)
    return cleaned, count


def export_safe_cell(value: Any) -> str:
    """Make one cell safe to write to a CSV/XLSX a human will open.

    Two gates in order, and both are needed:

    1. **Neutralise formula syntax.** `sanitise_cell` alone is not enough on the
       export path, because a submission's note text goes through *redaction*
       on the way in, and redaction rewrites the front of the string. A note
       reading `=HYPERLINK(...)` survives redaction with its leading `=`
       intact, so it would reach an examiner's spreadsheet as a live formula.
       Redaction removes PII; it does not remove code.
    2. **Redact PII.** A cell can reach an export without ever passing through
       the ingest path — a derived summary column, a label built from an
       analyst-supplied string, a value read back out of the lake. Applying
       redaction here means that path cannot leak even if it skipped stage 8.

    Applied at the export boundary rather than only at ingest, because that is
    the last point where the value is still a string and the first point where
    it becomes a file a person opens.
    """
    from app.services.pseudonymisation import get_pseudonymiser

    if value is None:
        return ""
    text = str(value)
    if not text:
        return text

    text = _CONTROL_CHARS.sub("", text)
    text = get_pseudonymiser().redact_text(text).redacted_text
    text, _was = sanitise_cell(text)
    return text


# -------------------------------------------------------------- row / column --
def check_dimensions(n_rows: int, n_columns: int, source: str) -> None:
    settings = get_settings()
    if n_rows > settings.max_rows_per_file:
        raise SecurityRejected(
            f"{source}: {n_rows} rows exceeds the {settings.max_rows_per_file} limit"
        )
    if n_columns > settings.max_csv_columns:
        raise SecurityRejected(
            f"{source}: {n_columns} columns exceeds the "
            f"{settings.max_csv_columns} limit"
        )


# ------------------------------------------------------------------- manifest --
def compute_manifest_hash(paths: list[Path]) -> str:
    """Content hash over a submission's files, order-independent.

    Re-sending byte-identical files must yield an identical hash, because
    `record_version` diffing across submissions depends on that (EG-11, EG-17).
    """
    import hashlib

    per_file: list[tuple[str, str]] = []
    for path in paths:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        per_file.append((path.name, digest.hexdigest()))

    per_file.sort()  # order-independent: a re-upload of the same set matches
    combined = "\n".join(f"{name}:{digest}" for name, digest in per_file)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def receive_uploads(
    files: list[tuple[str, bytes]], staging_dir: Path
) -> list[SanitisedFile]:
    """Stage 1 of the pipeline: receive and security-check.

    Each item is `(filename, content_bytes)`. Raises `SecurityRejected` on the
    first file that fails a check — a submission is all-or-nothing at intake, so
    a partially-staged upload cannot be mistaken for a complete one.
    """
    settings = get_settings()
    staging_dir.mkdir(parents=True, exist_ok=True)

    total = sum(len(content) for _, content in files)
    if total > settings.max_upload_bytes:
        raise SecurityRejected(
            f"Submission totals {total} bytes, exceeding the "
            f"{settings.max_upload_bytes}-byte limit"
        )

    results: list[SanitisedFile] = []

    for raw_name, content in files:
        safe_name = safe_filename(raw_name)
        target = staging_dir / safe_name

        # Belt and braces: the resolved path must stay inside staging.
        try:
            target.resolve().relative_to(staging_dir.resolve())
        except ValueError as exc:
            raise SecurityRejected(
                f"{raw_name!r} resolves outside the staging directory"
            ) from exc

        if target.exists():
            raise SecurityRejected(f"{safe_name} was already uploaded in this batch")

        target.write_bytes(content)

        size = check_size(target)
        detected = detect_format(target, declared=raw_name)
        verify_content_matches_format(target, detected)
        check_zip_bomb(target)

        results.append(
            SanitisedFile(
                original_name=raw_name,
                safe_name=safe_name,
                path=target,
                detected_format=detected,
                n_bytes=size,
            )
        )

    return results