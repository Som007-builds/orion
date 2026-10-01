"""Vendor mapping profiles and the fuzzy mapping assistant (plan §4).

Two responsibilities, deliberately separated:

* **`load_profiles`** reads the declarative YAML vendor profiles.
* **`suggest`** proposes column mappings for a submission whose profile is
  absent or incomplete.

The critical rule: **a human approves every mapping.** The assistant proposes;
it never applies. Auto-applying a fuzzy match is how `severity_raw` ends up
pointing at a column called "notes" and every alert quarantines for reasons
nobody can explain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from rapidfuzz import fuzz

from app.config import get_settings
from app.db.sqlite import get_connection
from app.services.ledger import LedgerAction, get_ledger

# Canonical target fields, grouped so a suggestion can say which table it fits.
CANONICAL_FIELDS: dict[str, tuple[str, ...]] = {
    "alert": (
        "alert_id", "event_ts", "title", "severity_raw", "rule_id",
        "source_ip", "destination", "mitre_tactic", "mitre_technique_id",
        "status", "auto_closed_flag", "case_id",
    ),
    "case": (
        "case_id", "created_at", "closed_at", "analyst", "closed_by",
        "severity_raw", "disposition", "root_cause_action", "actor_raw",
        "investigation_notes", "note_reference_id", "escalation_level",
    ),
}

# Type hints, used to check a proposal is even plausible before suggesting it.
_TYPE_HINTS: dict[str, tuple[str, ...]] = {
    "timestamp": ("ts", "time", "date", "_at", "created", "closed", "opened", "timestamp"),
    "ip": ("ip", "src", "source", "src_ip", "remote"),
    "hostname": ("host", "hostname", "dest", "device", "asset", "fqdn", "server"),
    "bool": ("is_", "auto", "flag", "enabled"),
    "string": ("id", "name", "title", "desc", "note", "status", "severity", "rule"),
}

# Below this, a suggestion is not worth showing: it would be noise an examiner
# has to dismiss, and too many bad suggestions train people to click through.
MIN_CONFIDENCE = 0.62


@dataclass(frozen=True)
class ColumnSpec:
    target: str
    source: str
    type: str
    required: bool = False
    pseudonymise: bool = False
    redact: bool = False


@dataclass(frozen=True)
class MappingProfileSpec:
    profile_id: str
    vendor: str
    version: str
    timezone: str
    tables: dict[str, dict[str, Any]]
    source_path: str | None = None

    def columns_for(self, table: str) -> dict[str, ColumnSpec]:
        """Column specs for one destination table.

        Raises on an unknown table rather than returning `{}`. An earlier
        version returned empty, which meant a profile block keyed `alerts`
        instead of `alert_record` resolved to no columns at all and the file
        loaded with every field missing — a silent data-loss path wearing a
        success message.
        """
        block = self.tables.get(table)
        if block is None:
            raise KeyError(
                f"Profile {self.profile_id} has no block for table {table!r}. "
                f"It declares: {sorted(self.tables)}. Block keys must be canonical "
                "table names from TABLE_CONTRACTS."
            )
        specs: dict[str, ColumnSpec] = {}
        for target, meta in (block.get("columns") or {}).items():
            meta = meta or {}
            specs[target] = ColumnSpec(
                target=target,
                source=meta.get("source", target),
                type=meta.get("type", "string"),
                required=bool(meta.get("required", False)),
                pseudonymise=bool(meta.get("pseudonymise", False)),
                redact=bool(meta.get("redact", False)),
            )
        return specs

    @property
    def severity_map(self) -> dict[str, str]:
        return self.tables.get("severity_map", {}) or {}

    @property
    def status_map(self) -> dict[str, str]:
        return self.tables.get("status_map", {}) or {}


@dataclass
class Suggestion:
    source_column: str
    suggested_target: str | None
    confidence: float
    sample_values: list[str] = field(default_factory=list)
    reason: str | None = None
    type_match: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_column": self.source_column,
            "suggested_target": self.suggested_target,
            "confidence": round(self.confidence, 3),
            "sample_values": self.sample_values[:5],
            "reason": self.reason,
        }


def load_profiles() -> dict[str, MappingProfileSpec]:
    """Every vendor profile on disk, keyed by `profile_id`."""
    settings = get_settings()
    profiles: dict[str, MappingProfileSpec] = {}

    if not settings.mapping_dir.is_dir():
        return profiles

    for path in sorted(settings.mapping_dir.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
        if not isinstance(raw, dict):
            continue

        tables = raw.get("tables") or {}
        if not tables:
            continue

        profiles[raw.get("profile_id", path.stem)] = MappingProfileSpec(
            profile_id=raw.get("profile_id", path.stem),
            vendor=raw.get("vendor", "unknown"),
            version=str(raw.get("version", "1")),
            timezone=raw.get("timezone", "UTC"),
            tables=tables,
            source_path=str(path),
        )
    return profiles


def get_profile(profile_id: str) -> MappingProfileSpec | None:
    return load_profiles().get(profile_id)


def _profile_source_columns(profile: MappingProfileSpec, table: str) -> set[str]:
    block = profile.tables.get(table) or {}
    return {
        str(meta["source"]).lower()
        for meta in (block.get("columns") or {}).values()
        if meta and meta.get("source")
    }


def match_profile_table(columns: list[str]) -> tuple[MappingProfileSpec | None, str | None, float]:
    """Best (profile, table block, score) for a set of submitted columns.

    Scoring is per *table block*, not per profile. A Splunk profile declares
    both an `alerts` and a `cases` block; a CSV carrying only the alert
    columns covers 11 of the profile's 23 sources, which scores 0.48 against
    the union — below any sane trust floor — even though it is an unambiguous
    Splunk alert export. Scoring against the matching block alone gives ~1.0.
    """
    lowered = {c.lower().strip() for c in columns}
    best: tuple[MappingProfileSpec | None, str | None, float] = (None, None, 0.0)

    for profile in load_profiles().values():
        for table in profile.tables:
            spec_columns = _profile_source_columns(profile, table)
            if not spec_columns:
                continue
            score = len(lowered & spec_columns) / len(spec_columns)
            if score > best[2]:
                best = (profile, table, score)

    return best


def match_profile(columns: list[str]) -> tuple[MappingProfileSpec | None, float]:
    """Best vendor profile for a set of columns, ignoring which table matched.

    Retained for callers that only need "which vendor is this?"; mapping
    resolution uses `match_profile_table`, which also needs the table.
    """
    profile, _table, score = match_profile_table(columns)
    return profile, score


class MappingAssistant:
    """Suggests column mappings. Never applies them without approval."""

    def suggest(
        self,
        columns: list[str],
        table: str,
        sample_values: dict[str, list[str]] | None = None,
    ) -> list[Suggestion]:
        """Rank candidate targets for each submitted column.

        Scoring blends name similarity with a value-shape check, because
        `timestamp` matching a column of free text is worse than no suggestion.
        """
        targets = CANONICAL_FIELDS.get(table, CANONICAL_FIELDS["alert"])
        samples = sample_values or {}
        suggestions: list[Suggestion] = []

        for column in columns:
            best_target: str | None = None
            best_score = 0.0
            best_reason: str | None = None
            best_type_match = False

            for target in targets:
                score = fuzz.ratio(column.lower(), target.lower()) / 100.0

                # Token overlap helps: "closed_at" vs "at_closed" scores poorly
                # on raw characters but well on tokens.
                column_tokens = set(column.lower().replace("-", "_").split("_"))
                target_tokens = set(target.split("_"))
                if column_tokens & target_tokens:
                    score = max(score, 0.55 + 0.1 * len(column_tokens & target_tokens))

                type_match = self._value_shape_matches(
                    samples.get(column, []), target
                )
                if type_match:
                    score = min(1.0, score + 0.12)

                if score > best_score:
                    best_score = score
                    best_target = target
                    best_type_match = type_match
                    best_reason = (
                        f"name similarity {score:.2f}"
                        + ("; value shape matches" if type_match else "")
                    )

            suggestions.append(
                Suggestion(
                    source_column=column,
                    suggested_target=best_target if best_score >= MIN_CONFIDENCE else None,
                    confidence=round(best_score, 3),
                    sample_values=samples.get(column, [])[:5],
                    reason=best_reason if best_score >= MIN_CONFIDENCE else
                           f"no confident match (best {best_score:.2f} < {MIN_CONFIDENCE})",
                    type_match=best_type_match,
                )
            )

        return sorted(suggestions, key=lambda s: s.confidence, reverse=True)

    def _value_shape_matches(self, values: list[str], target: str) -> bool:
        """Cheap check that a column's contents suit the target's type."""
        import re

        sample = [v for v in values if v][:20]
        if not sample:
            return False

        if any(hint in target for hint in _TYPE_HINTS["timestamp"]):
            pattern = re.compile(r"^\d{4}-\d{2}-\d{2}|T\d{2}:\d{2}|/\d{2}/\d{4}")
            return sum(bool(pattern.match(v.strip())) for v in sample) / len(sample) > 0.5

        if target in {"source_ip", "destination_ip"}:
            octet = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
            return sum(bool(octet.match(v.strip())) for v in sample) / len(sample) > 0.5

        if target in {"destination", "asset"}:
            return any("." in v for v in sample)

        if target == "auto_closed_flag":
            return all(v.strip().lower() in {"true", "false", "0", "1", "yes", "no"}
                       for v in sample)

        if target in {"disposition"}:
            return all(
                v.strip().upper().replace(" ", "_") in
                {"TRUE_POSITIVE", "FALSE_POSITIVE", "BENIGN_ANOMALY", "SUPPRESSED"}
                for v in sample
            )

        # A long free-text column is a note, not an identifier.
        if target.endswith("_id") or target in {"alert_id", "case_id"}:
            return not any(len(v) > 120 for v in sample)

        return False

    # -- approval ---------------------------------------------------------
    def register_approved(
        self,
        profile_id: str,
        definition: dict[str, Any],
        approved_by: str,
        ledger_payload: dict[str, Any] | None = None,
    ) -> str:
        """Persist an approved mapping. Ledgered.

        Exactly one `mapping_approved` entry is written per approval. Callers
        that know more than this function does (which submission, which
        columns, whose decision) pass `ledger_payload` so the richer detail
        lands in the audit trail rather than being lost.
        """
        import json

        get_connection().execute(
            "INSERT OR REPLACE INTO mapping_profile "
            "(profile_id, vendor, version, definition, created_ts, approved_by, active) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            (
                profile_id,
                definition.get("vendor") or "adhoc",
                str(definition.get("version", "1")),
                json.dumps(definition, sort_keys=True),
                datetime.now(timezone.utc).isoformat(),
                approved_by,
            ),
        )
        return get_ledger().append(
            actor=approved_by,
            action=LedgerAction.MAPPING_APPROVED,
            payload=ledger_payload or {
                "profile_id": profile_id,
                "version": definition.get("version"),
            },
        )

    def approved_profiles(self) -> list[dict[str, Any]]:
        rows = get_connection().execute(
            "SELECT profile_id, vendor, version, definition, created_ts, "
            "approved_by, active FROM mapping_profile ORDER BY created_ts DESC"
        ).fetchall()
        return [dict(row) for row in rows]


_assistant: MappingAssistant | None = None


def get_mapping_assistant() -> MappingAssistant:
    global _assistant
    if _assistant is None:
        _assistant = MappingAssistant()
    return _assistant