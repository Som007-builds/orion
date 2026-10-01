"""Pseudonymisation and note redaction (plan §3.16, §4).

Four rules from the plan:

1. Analyst IDs, source IPs and hostnames are HMAC-SHA-256 pseudonymised at
   ingestion with a key held outside the repo.
2. Notes are redacted for IPs, emails and hostnames before storage; originals
   are encrypted at rest and referenced only.
3. Full-text and note views are role-gated and every view is a ledger entry.
4. Pseudonyms are **stable within an entity** so baselines work, but cannot be
   reversed without the key.

Design note on rule 4: the HMAC input is salted with the entity id, so the same
analyst appearing in two CSEs produces two different pseudonyms. Without that,
an adversary holding two submissions could link one analyst across entities,
which the plan is trying to prevent. Stability is per-entity, not global.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
from dataclasses import dataclass
from typing import Literal

from app.config import get_settings, resolve_secret

PseudonymKind = Literal["analyst", "ip", "hostname", "asset"]

# Redaction order matters: emails before bare hostnames, IPv6 before IPv4, or
# the first pattern eats part of the second.
_REDACTION_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    ("ipv6", re.compile(r"\b(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\b"), "[REDACTED_IP]"),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[REDACTED_IP]"),
    ("fqdn", re.compile(r"\b(?:[A-Za-z0-9-]+\.)+(?:com|net|org|io|co\.in|gov\.in|local|lan|corp|int)\b", re.IGNORECASE), "[REDACTED_HOST]"),
    ("hostname", re.compile(r"\b(?:HOST|SERVER|SRVR|DC|DB|WEB)-[A-Za-z0-9-]{2,}\b"), "[REDACTED_HOST]"),
    ("url", re.compile(r"\b(?:https?|ftp)://[^\s]+", re.IGNORECASE), "[REDACTED_URL]"),
)

# CVE-style identifiers and long hex blobs are worth redacting: they are
# attacker-controlled strings that end up in notes verbatim.
_CVE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)


@dataclass(frozen=True)
class RedactionResult:
    redacted_text: str
    redaction_count: int
    categories: tuple[str, ...]


class Pseudonymiser:
    """HMAC-SHA-256 pseudonymiser, salted per entity.

    The key comes from a handle resolved outside the repo. The same input
    yields the same pseudonym within an entity (so baselines are meaningful)
    and a different one across entities (so analysts cannot be linked).
    """

    def __init__(self, key_handle: str | None = None) -> None:
        settings = get_settings()
        handle = key_handle or settings.hmac_key_handle
        self._key = resolve_secret(handle)
        self._key_ref = settings.hmac_key_ref

    def pseudonym(self, raw: str, entity_id: str, kind: PseudonymKind) -> str:
        """Stable per (entity, kind, raw). Not reversible without the key."""
        material = f"{entity_id}|{kind}|{raw}|{self._key_ref}"
        digest = hmac.new(self._key, material.encode("utf-8"), hashlib.sha256)
        return f"{kind}_{digest.hexdigest()[:32]}"

    def pseudonymise_field(
        self, value: str | None, entity_id: str, kind: PseudonymKind
    ) -> str | None:
        """Null-safe wrapper. Empty and None pass through unchanged."""
        if value is None or value == "":
            return value
        return self.pseudonym(value, entity_id, kind)

    def pseudonymise_asset_id(self, hostname: str, entity_id: str) -> str:
        """Asset IDs are HMACs of hostname/FQDN (plan §3.5)."""
        return self.pseudonym(hostname.lower().strip(), entity_id, "asset")

    def redact_text(self, text: str, entity_asset_names: set[str] | None = None) -> RedactionResult:
        """Strip PII from a note before storage.

        `redacted_text` is what detectors and APIs read; the original is
        encrypted and referenced only.
        """
        if not text:
            return RedactionResult("", 0, ())

        redacted = text
        count = 0
        categories: list[str] = []

        # Entity asset names first — a hostname the submission itself declared
        # is more reliable than a generic pattern.
        if entity_asset_names:
            for name in sorted(entity_asset_names, key=len, reverse=True):
                if name and name in redacted:
                    occurrences = redacted.count(name)
                    redacted = redacted.replace(name, "[REDACTED_HOST]")
                    count += occurrences
                    categories.append("asset_name")

        for category, pattern, replacement in _REDACTION_PATTERNS:
            redacted, n = pattern.subn(replacement, redacted)
            if n:
                count += n
                categories.append(category)

        redacted, n = _CVE.subn("[REDACTED_CVE]", redacted)
        if n:
            count += n
            categories.append("cve")

        return RedactionResult(redacted, count, tuple(sorted(set(categories))))

    def shingle_signature(self, text: str, n: int = 5) -> str:
        """Order-sensitive shingle hash for note-cluster comparison.

        EG-10 and the MinHash auditor use this to group templated notes without
        storing or comparing raw text.
        """
        normalised = re.sub(r"\s+", " ", text.lower().strip())
        words = normalised.split()
        if len(words) < n:
            shingles = [" ".join(words)] if words else [""]
        else:
            shingles = [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]
        joined = "|".join(sorted(shingles))
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]

    def classify_actor_type(self, value: str | None) -> tuple[str, bool]:
        """Infer `actor_type` from a free-text or numeric actor field.

        Returns `(actor_type, was_inferred)`. The plan (§4.7) requires that an
        inferred actor type be *labelled as inferred* on the finding, because a
        wrong inference silently changes which indicators apply.
        """
        if value is None or str(value).strip() == "":
            return "unknown", True

        token = str(value).strip().lower()
        automation_tokens = {
            "automation", "auto", "automated", "bot", "system", "svc",
            "service", "script", "engine", "playbook", "soar", "correlation",
        }
        human_tokens = {
            "human", "user", "analyst", "analyst_pseudo", "manual", "person",
            "operator", "engineer", "l1", "l2", "l3", "tier1", "tier2", "tier3",
        }
        if token in automation_tokens:
            return "automation", True
        if token in human_tokens:
            return "human", True
        # Numeric pseudonyms are HMAC outputs, so a real actor type is
        # unknowable — that is exactly the "unknown" case.
        return "unknown", True


_pseudonymiser: Pseudonymiser | None = None


def get_pseudonymiser() -> Pseudonymiser:
    global _pseudonymiser
    if _pseudonymiser is None:
        _pseudonymiser = Pseudonymiser()
    return _pseudonymiser


def is_valid_ip(value: str) -> bool:
    """Strict IP check, so `999.1.1.1` is not silently treated as an IP."""
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False