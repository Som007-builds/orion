"""Hash-chained append-only audit ledger (plan §8.2).

    entry_hash = SHA256( prev_hash ‖ timestamp ‖ actor ‖ action ‖ payload_hash )

Properties enforced here:

  * **Append-only.** The SQLite triggers in `app.db.sqlite` abort any UPDATE or
    DELETE, so the rule holds even against a well-meaning service bug.
  * **Chain integrity.** `verify()` walks the chain and reports the *first*
    break, not just a boolean — a position is actionable, "invalid" is not.
  * **No concurrent forks.** The head read and the new insert happen inside one
    `BEGIN IMMEDIATE` transaction, so two simultaneous appends serialise.

This module is the owner of frozen interface #5 (`LedgerAction`). Changing the
enum requires agreement from all three developers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.db.sqlite import get_connection, transaction

GENESIS_HASH = "0" * 64


class LedgerAction(str, Enum):
    """Shared, versioned action vocabulary (frozen interface #5)."""

    SUBMISSION_RECEIVED = "submission_received"
    MAPPING_PROPOSED = "mapping_proposed"
    MAPPING_APPROVED = "mapping_approved"
    MAPPING_REJECTED = "mapping_rejected"
    RUN_TRIGGERED = "run_triggered"
    # Scored separately from RUN_COMPLETED on purpose. Both are "a run finished",
    # but an auditor filtering the ledger by action has to be able to ask "when
    # was this entity last *scored*" without also matching every load job, and
    # those two questions must not share a label.
    RUN_COMPLETED = "run_completed"
    SCORING_COMPLETED = "scoring_completed"
    RUN_FAILED = "run_failed"
    POLICY_ACTIVATED = "policy_activated"
    PACK_STAGED = "pack_staged"
    PACK_SHADOW_RUN = "pack_shadow_run"
    PACK_PROMOTED = "pack_promoted"
    PACK_ROLLED_BACK = "pack_rolled_back"
    BRIEF_EXPORTED = "brief_exported"
    PACK_EXPORTED = "pack_exported"
    NOTE_VIEWED = "note_viewed"
    VERDICT_RECORDED = "verdict_recorded"
    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_FAILED = "login_failed"
    ROLE_CHANGED = "role_changed"
    USER_CREATED = "user_created"


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    entry_hash: str
    prev_hash: str
    timestamp: str
    actor: str
    action: str
    payload_hash: str
    payload: dict[str, Any] | None
    entity_id: str | None


@dataclass(frozen=True)
class LedgerVerifyResult:
    valid: bool
    head_hash: str
    entries_checked: int
    first_break_seq: int | None = None
    break_reason: str | None = None


def _canonical_payload(payload: dict[str, Any] | None) -> str:
    """Deterministic serialisation.

    Keys sorted and separators pinned so the same logical payload always hashes
    identically — otherwise a dict ordering difference would break the chain on
    a replay.
    """
    if payload is None:
        return ""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_entry_hash(
    prev_hash: str, timestamp: str, actor: str, action: str, payload_hash: str
) -> str:
    """The chain function. Field order is part of the contract."""
    material = f"{prev_hash}{timestamp}{actor}{action}{payload_hash}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class Ledger:
    """Append-only ledger facade.

    Services call `append()` and never touch `ledger_entry` directly.
    """

    def append(
        self,
        actor: str,
        action: LedgerAction,
        payload: dict[str, Any] | None = None,
        entity_id: str | None = None,
    ) -> str:
        """Append one entry. Returns the new `entry_hash`.

        The head read and the insert share a single `BEGIN IMMEDIATE`
        transaction, so concurrent appends cannot fork the chain.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        action_value = action.value if isinstance(action, LedgerAction) else str(action)
        canonical = _canonical_payload(payload)
        payload_hash = (
            hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if canonical
            else ""
        )

        with transaction() as conn:
            row = conn.execute(
                "SELECT entry_hash FROM ledger_entry ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = row["entry_hash"] if row else GENESIS_HASH

            entry_hash = compute_entry_hash(
                prev_hash, timestamp, actor, action_value, payload_hash
            )
            conn.execute(
                "INSERT INTO ledger_entry "
                "(entry_hash, prev_hash, timestamp, actor, action, "
                " payload_hash, payload, entity_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry_hash,
                    prev_hash,
                    timestamp,
                    actor,
                    action_value,
                    payload_hash,
                    canonical or None,
                    entity_id,
                ),
            )
        return entry_hash

    def head(self) -> str:
        row = get_connection().execute(
            "SELECT entry_hash FROM ledger_entry ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["entry_hash"] if row else GENESIS_HASH

    def head_entry(self) -> LedgerEntry | None:
        row = get_connection().execute(
            "SELECT * FROM ledger_entry ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return LedgerEntry(**dict(row)) if row else None

    def verify(self, upto_seq: int | None = None) -> LedgerVerifyResult:
        """Walk the chain and report the first break.

        Returns a `first_break_seq` rather than a bare boolean: an operator needs
        to know *where* the chain diverged to decide whether this is tampering
        or a partial restore.
        """
        conn = get_connection()
        query = (
            "SELECT * FROM ledger_entry WHERE seq <= ? ORDER BY seq ASC"
            if upto_seq is not None
            else "SELECT * FROM ledger_entry ORDER BY seq ASC"
        )
        params = (upto_seq,) if upto_seq is not None else ()

        expected_prev = GENESIS_HASH
        checked = 0

        for row in conn.execute(query, params):
            seq = int(row["seq"])

            if row["prev_hash"] != expected_prev:
                return LedgerVerifyResult(
                    valid=False,
                    head_hash=expected_prev,
                    entries_checked=checked,
                    first_break_seq=seq,
                    break_reason=(
                        f"prev_hash mismatch: stored {row['prev_hash'][:16]}…, "
                        f"expected {expected_prev[:16]}…"
                    ),
                )

            recomputed = compute_entry_hash(
                row["prev_hash"],
                row["timestamp"],
                row["actor"],
                row["action"],
                row["payload_hash"],
            )
            if recomputed != row["entry_hash"]:
                return LedgerVerifyResult(
                    valid=False,
                    head_hash=expected_prev,
                    entries_checked=checked,
                    first_break_seq=seq,
                    break_reason="entry_hash does not match recomputed chain hash",
                )

            # The payload is stored alongside its hash; verify the two agree, so
            # an edit to the payload body is caught even if the hash column
            # alone were somehow rewritten.
            stored_payload = row["payload"]
            if stored_payload is not None:
                recomputed_payload_hash = hashlib.sha256(
                    stored_payload.encode("utf-8")
                ).hexdigest()
                if recomputed_payload_hash != row["payload_hash"]:
                    return LedgerVerifyResult(
                        valid=False,
                        head_hash=expected_prev,
                        entries_checked=checked,
                        first_break_seq=seq,
                        break_reason="payload no longer matches its recorded hash",
                    )

            expected_prev = row["entry_hash"]
            checked += 1

        return LedgerVerifyResult(
            valid=True, head_hash=expected_prev, entries_checked=checked
        )

    def page(
        self,
        limit: int = 50,
        offset: int = 0,
        action: str | None = None,
        actor: str | None = None,
        entity_id: str | None = None,
    ) -> list[LedgerEntry]:
        """Newest-first page of entries."""
        clauses: list[str] = []
        params: list[Any] = []
        if action:
            clauses.append("action = ?")
            params.append(action)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if entity_id:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        params.extend([limit, offset])
        rows = get_connection().execute(
            f"SELECT * FROM ledger_entry {where} ORDER BY seq DESC LIMIT ? OFFSET ?",
            params,
        )
        return [LedgerEntry(**dict(row)) for row in rows]


_ledger: Ledger | None = None


def get_ledger() -> Ledger:
    global _ledger
    if _ledger is None:
        _ledger = Ledger()
    return _ledger