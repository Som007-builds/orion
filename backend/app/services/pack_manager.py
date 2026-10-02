"""Signed pack lifecycle (plan §4.10, Phase 14): stage, shadow, promote, rollback.

A pack is a self-contained, signed directory bundle:

    manifest.json                 must declare `policy_profile_id`
    policy/<profile_id>.yaml      the policy definition the pack carries

Staging copies the bundle into `data/packs/staged/<pack_id>/`, hashes its
contents deterministically (per-file sha256 over sorted relative paths), and
signs that hash with the host's Ed25519 key — the same signing as the 2.13
exports (`sign_bytes`), domain-tagged with the ``b"orion-pack:v1:"`` payload
prefix. Nothing staged is reachable at runtime.

The two live steps are the point of the lifecycle:

* **shadow** — execute the pack's policy over the same window the latest
  completed run covered, *without persisting anything*, and diff its raised
  findings against the stored findings of that run. A raised finding is
  exactly the rule `_persist_indicator_results` writes with (signal present,
  unsuppressed, positive), so the shadow side of the diff and the live side
  are the same predicate: the diff is between two result sets, never between
  two code paths.
* **promote / rollback** — the only steps that change anything live. Promote
  makes the bundled policy the active policy (skipped when its content hash
  already matches the active one), swaps the pack statuses, and copies the
  bundle into `data/packs/active/`. Rollback restores the *immediate*
  predecessor one step at a time.

Every step is ledgered (`pack_staged`, `pack_shadow_run`, `pack_promoted`,
`pack_rolled_back`).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from app.config import get_settings
from app.db.sqlite import get_connection, transaction
from app.schemas.ledger import PackShadowOut, PackStageIn, PackOut, PackTransitionOut
from app.services.ledger import LedgerAction, get_ledger
from app.services.policy_profile import PolicyProfile, get_policy_service, load_from_yaml
from app.services.rules_engine import get_rules_engine
from app.services.scoring_service import ScoringError, ScoringService, get_scoring_service
from app.services.signing_service import host_label, sign_bytes

#: The signed payload for a pack is ``label + content_hash`` — the label keeps
#: pack signatures apart from other artefacts signed with the same host key.
PACK_LABEL = b"orion-pack:v1:"


class PackError(Exception):
    """A pack lifecycle step was refused by a rule (maps to 400)."""


class PackNotFound(PackError):
    """The named pack does not exist (routes map this to 404)."""


def _finding_key(entry: tuple[str, str]) -> tuple[str, str]:
    return entry


class PackManager:
    """Stage, shadow, promote and roll back signed packs."""

    def __init__(self) -> None:
        self.settings = get_settings()

    # ------------------------------------------------------------ staging --
    def stage(self, body: PackStageIn, actor: str) -> PackOut:
        """Validate, copy, hash, sign and register a pack bundle."""
        source = Path(body.source_path)
        if not source.is_dir():
            raise PackError(
                f"source_path {body.source_path} is not a directory — a pack is a "
                "directory bundle (manifest.json + policy/<profile_id>.yaml)"
            )

        manifest = self._read_manifest(source)
        profile_id = str(manifest["policy_profile_id"])
        policy_path = source / "policy" / f"{profile_id}.yaml"
        if not policy_path.is_file():
            raise PackError(
                f"manifest declares policy_profile_id {profile_id!r} but "
                f"{policy_path.relative_to(source.parent)} is missing — a pack must "
                "carry the policy it proposes"
            )
        profile = self._load_policy(policy_path, profile_id)

        # Stated version must match the declared one: the version an operator
        # stages is the version the run lineage records, and drift between the
        # request and the bundle would make the pack undecidable to audit.
        declared_version = manifest.get("version")
        if declared_version is not None and str(declared_version) != str(body.version):
            raise PackError(
                f"manifest declares version {declared_version!r} but the stage "
                f"request says {body.version!r}"
            )

        declared_pack_id = manifest.get("pack_id")
        if (
            body.pack_id
            and declared_pack_id is not None
            and body.pack_id != str(declared_pack_id)
        ):
            raise PackError(
                f"manifest declares pack_id {declared_pack_id!r} but the stage "
                f"request says {body.pack_id!r}"
            )

        content_hash = self._bundle_hash(source)
        if body.pack_id or declared_pack_id is not None:
            pack_id = body.pack_id or str(declared_pack_id)
        else:
            pack_id = f"pack_{content_hash[:8]}"

        if self._pack_row(pack_id) is not None:
            raise PackError(
                f"pack {pack_id} is already registered — stage a changed bundle "
                "under a new id or version"
            )
        staged_dir = self.settings.pack_dir / "staged" / pack_id
        if staged_dir.exists():
            raise PackError(
                f"a bundle already exists at {staged_dir} — refuse to overwrite "
                "candidate evidence"
            )

        shutil.copytree(source, staged_dir)

        signature, signing_host = sign_bytes(PACK_LABEL + content_hash.encode("ascii"))
        signed_by = body.signed_by or signing_host
        staged_ts = self._now()
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pack (pack_id, version, content_hash, signature, "
                "status, staged_ts, signed_by) "
                "VALUES (?,?,?,?,'staged',?,?)",
                (pack_id, body.version, content_hash, signature, staged_ts, signed_by),
            )

        get_ledger().append(
            actor=actor,
            action=LedgerAction.PACK_STAGED,
            payload={
                "pack_id": pack_id,
                "version": body.version,
                "content_hash": content_hash,
                "policy_profile_id": profile_id,
                "policy_version": profile.version,
                "policy_content_hash": profile.content_hash,
                "signed_by": signed_by,
                "n_files": len(self._bundle_entries(source)),
            },
            entity_id=pack_id,
        )
        return PackOut(
            pack_id=pack_id,
            version=body.version,
            content_hash=content_hash,
            signature=signature,
            status="staged",
            staged_ts=staged_ts,
            signed_by=signed_by,
        )

    # ------------------------------------------------------------ shadow --
    def shadow(self, pack_id: str, actor: str) -> PackShadowOut:
        """Run the staged policy over the live window and diff it against it."""
        row = self._get_pack_row(pack_id)
        if row["status"] != "staged":
            raise PackError(
                f"pack {pack_id} is '{row['status']}'; only a staged pack can be "
                "shadow-run — an active pack is already live and a rolled-back "
                "one was superseded"
            )
        bundle = self.settings.pack_dir / "staged" / pack_id
        if not bundle.is_dir():
            raise PackError(
                f"the staged bundle for {pack_id} is missing at {bundle} — "
                "stage it again"
            )
        profile = self._bundle_policy(bundle)

        # Live side: the stored findings of the latest completed run — the same
        # read path the dashboard serves, so the shadow never disagrees with
        # what an auditor can see.
        live = get_scoring_service()._latest_run(None, None)
        if live is None:
            raise PackError(
                "no completed run to shadow against — execute a run first; a "
                "shadow diff needs a live result set to compare with"
            )
        window = (
            date.fromisoformat(live["period_start"]),
            date.fromisoformat(live["period_end"]),
        )

        # Candidate side: a fresh ScoringService bound to the pack's policy,
        # executed without persistence.
        candidate_svc = ScoringService(policy=profile, rules=get_rules_engine(profile))
        try:
            scores = candidate_svc.score_period(
                window[0], window[1], persist=False, actor=f"pack_manager:shadow:{pack_id}"
            )
        except ScoringError as exc:
            raise PackError(
                f"shadow run refused: the pack's policy could not score the live "
                f"window {live['period_start']}..{live['period_end']}: {exc}"
            ) from exc

        stored = self._stored_findings(str(live["run_id"]))
        candidate = self._raised_findings(scores)
        report = self._diff_report(
            pack_row=row,
            profile=profile,
            live_run_id=str(live["run_id"]),
            window_start=live["period_start"],
            window_end=live["period_end"],
            stored=stored,
            candidate=candidate,
        )

        with transaction() as conn:
            conn.execute(
                "UPDATE pack SET status = 'shadow', diff_report = ? "
                "WHERE pack_id = ?",
                (json.dumps(report, sort_keys=True), pack_id),
            )

        ledger_hash = get_ledger().append(
            actor=actor,
            action=LedgerAction.PACK_SHADOW_RUN,
            payload={
                "pack_id": pack_id,
                "version": row["version"],
                "content_hash": row["content_hash"],
                "policy_profile_id": profile.profile_id,
                "live_run_id": live["run_id"],
                "window": {"period_start": live["period_start"], "period_end": live["period_end"]},
                "counts": report["counts"],
                "recommendation": report["recommendation"],
            },
            entity_id=pack_id,
        )
        return PackShadowOut(
            pack_id=pack_id,
            status="shadow",
            n_findings_changed=report["counts"]["changed"],
            n_findings_added=report["counts"]["added"],
            n_findings_removed=report["counts"]["removed"],
            diff_report=report,
            recommendation=report["recommendation"],
            ledger_entry_hash=ledger_hash,
        )

    # ----------------------------------------------------------- promote --
    def promote(self, pack_id: str, actor: str) -> PackTransitionOut:
        """Make a shadow-run pack the live pack — the first live step."""
        row = self._get_pack_row(pack_id)
        if row["status"] != "shadow":
            raise PackError(
                f"pack {pack_id} is '{row['status']}'; promotion is refused "
                "without a shadow run that has been compared against the live "
                "result set"
            )
        bundle = self.settings.pack_dir / "staged" / pack_id
        if not bundle.is_dir():
            raise PackError(
                f"the staged bundle for {pack_id} is missing at {bundle} — "
                "stage it again"
            )
        profile = self._bundle_policy(bundle)

        previous = get_connection().execute(
            "SELECT pack_id FROM pack WHERE status = 'active' "
            "ORDER BY promoted_ts DESC LIMIT 1"
        ).fetchone()
        previous_id = str(previous["pack_id"]) if previous else None

        # Live steps: bundle first, then policy, then the status flip, so a
        # failure never leaves a pack half-live.
        self._install_bundle(pack_id, bundle)
        self._activate_policy_if_needed(profile, actor, notes=f"promote pack {pack_id}")
        promoted_ts = self._now()
        with transaction() as conn:
            if previous_id:
                conn.execute(
                    "UPDATE pack SET status = 'rolled_back' WHERE pack_id = ?",
                    (previous_id,),
                )
            conn.execute(
                "UPDATE pack SET status = 'active', promoted_ts = ? "
                "WHERE pack_id = ?",
                (promoted_ts, pack_id),
            )

        ledger_hash = get_ledger().append(
            actor=actor,
            action=LedgerAction.PACK_PROMOTED,
            payload={
                "pack_id": pack_id,
                "version": row["version"],
                "content_hash": row["content_hash"],
                "policy_profile_id": profile.profile_id,
                "policy_version": profile.version,
                "policy_content_hash": profile.content_hash,
                "previous_id": previous_id,
            },
            entity_id=pack_id,
        )
        return PackTransitionOut(
            pack_id=pack_id,
            status="active",
            previous_id=previous_id,
            ledger_entry_hash=ledger_hash,
        )

    # ---------------------------------------------------------- rollback --
    def rollback(self, pack_id: str, actor: str) -> PackTransitionOut:
        """Restore the immediately previous live pack, one step at a time."""
        row = self._get_pack_row(pack_id)
        if row["status"] != "rolled_back":
            raise PackError(
                f"pack {pack_id} is '{row['status']}'; only a superseded pack "
                "(status 'rolled_back') can be restored"
            )
        active = get_connection().execute(
            "SELECT pack_id, promoted_ts FROM pack WHERE status = 'active' "
            "ORDER BY promoted_ts DESC LIMIT 1"
        ).fetchone()
        if active is None:
            raise PackError("no live pack to roll back from")

        # Only the immediate predecessor may be restored: a rollback is a
        # rewind of the last promote, not a leap across history. One step per
        # call, so a chain collapses back in the order it was built.
        if (
            not row["promoted_ts"]
            or not active["promoted_ts"]
            or row["promoted_ts"] >= active["promoted_ts"]
        ):
            raise PackError(
                f"pack {pack_id} was not live before {active['pack_id']} — a "
                "rollback restores the immediately previous live pack"
            )
        between = get_connection().execute(
            "SELECT COUNT(*) AS c FROM pack WHERE status = 'rolled_back' "
            "AND promoted_ts IS NOT NULL AND promoted_ts > ? AND promoted_ts < ?",
            (row["promoted_ts"], active["promoted_ts"]),
        ).fetchone()
        if int(between["c"]) > 0:
            raise PackError(
                f"pack {pack_id} is not the immediate predecessor of "
                f"{active['pack_id']} — roll back one step at a time"
            )

        bundle = self.settings.pack_dir / "staged" / pack_id
        if not bundle.is_dir():
            raise PackError(
                f"the staged bundle for {pack_id} is missing at {bundle} — "
                "a rollback cannot restore a bundle that was deleted"
            )
        profile = self._bundle_policy(bundle)

        previous_id = str(active["pack_id"])
        self._install_bundle(pack_id, bundle)
        self._activate_policy_if_needed(profile, actor, notes=f"rollback to pack {pack_id}")
        promoted_ts = self._now()
        with transaction() as conn:
            conn.execute(
                "UPDATE pack SET status = 'rolled_back' WHERE pack_id = ?",
                (previous_id,),
            )
            conn.execute(
                "UPDATE pack SET status = 'active', promoted_ts = ? "
                "WHERE pack_id = ?",
                (promoted_ts, pack_id),
            )

        ledger_hash = get_ledger().append(
            actor=actor,
            action=LedgerAction.PACK_ROLLED_BACK,
            payload={
                "pack_id": pack_id,
                "version": row["version"],
                "content_hash": row["content_hash"],
                "policy_profile_id": profile.profile_id,
                "policy_version": profile.version,
                "policy_content_hash": profile.content_hash,
                "previous_id": previous_id,
            },
            entity_id=pack_id,
        )
        return PackTransitionOut(
            pack_id=pack_id,
            status="active",
            previous_id=previous_id,
            ledger_entry_hash=ledger_hash,
        )

    # ------------------------------------------------------------ helpers --
    def _install_bundle(self, pack_id: str, bundle: Path) -> None:
        """Copy the staged bundle into the active tree (idempotent)."""
        active_dir = self.settings.pack_dir / "active" / pack_id
        if active_dir.exists():
            shutil.rmtree(active_dir)
        shutil.copytree(bundle, active_dir)

    def _activate_policy_if_needed(
        self, profile: PolicyProfile, actor: str, notes: str
    ) -> None:
        """Make the pack's policy the active policy, unless it already is.

        A pack that is 'live' but whose policy is not the active one would be a
        label with no effect — scoring reads the active policy, so promotion has
        to adopt the bundled policy for the pack to mean anything at runtime.
        The profile is activated through the same ledgered path as the
        `POST /policy-profiles/{id}/activate` route.
        """
        current = get_policy_service().active()
        if current.content_hash == profile.content_hash:
            return
        get_policy_service().activate(
            profile.profile_id, actor=actor, notes=notes, profile=profile
        )

    def _bundle_policy(self, bundle: Path) -> PolicyProfile:
        manifest = self._read_manifest(bundle)
        profile_id = str(manifest["policy_profile_id"])
        policy_path = bundle / "policy" / f"{profile_id}.yaml"
        return self._load_policy(policy_path, profile_id)

    def _load_policy(self, policy_path: Path, profile_id: str) -> PolicyProfile:
        try:
            profile = load_from_yaml(policy_path)
        except ValueError as exc:
            raise PackError(
                f"the pack's policy is not a valid policy profile: {exc}"
            ) from exc
        if profile.profile_id != profile_id:
            raise PackError(
                f"manifest declares policy_profile_id {profile_id!r} but the "
                f"policy YAML declares {profile.profile_id!r}"
            )
        return profile

    def _read_manifest(self, bundle: Path) -> dict[str, Any]:
        manifest_path = bundle / "manifest.json"
        if not manifest_path.is_file():
            raise PackError(
                f"{bundle} has no manifest.json — a pack bundle is a directory "
                "with manifest.json and policy/<profile_id>.yaml"
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise PackError(f"{manifest_path} is not valid JSON: {exc}") from exc
        if not isinstance(manifest, dict) or not manifest.get("policy_profile_id"):
            raise PackError(
                f"{manifest_path} must declare 'policy_profile_id' — a pack that "
                "does not say which policy it carries cannot be audited"
            )
        return manifest

    # ------------------------------------------------------------ hashing --
    def _bundle_entries(self, bundle: Path) -> dict[str, str]:
        """Relative path -> sha256(file bytes), sorted by path."""
        entries: dict[str, str] = {}
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                rel = path.relative_to(bundle).as_posix()
                entries[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        return entries

    def _bundle_hash(self, bundle: Path) -> str:
        """Deterministic hash over the bundle's file map.

        Sorted keys and pinned separators, so an identical bundle always hashes
        identically regardless of copy order. The hash is what the stage step
        signs, and `scripts/verify_pack.py` recomputes it from the stored copy.
        """
        canonical = json.dumps(
            self._bundle_entries(bundle), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ---------------------------------------------------------- diffing ----
    def _raised_findings(
        self, scores: Sequence[Any]
    ) -> dict[tuple[str, str], dict[str, float | None]]:
        """The candidate's findings, keyed by (entity_id, indicator_id).

        The predicate is identical to the one `_persist_indicator_results` uses
        to decide a stored finding — a signal that exists, is not suppressed,
        and is positive — so "removed" and "added" describe the same thing on
        both sides of the diff.
        """
        out: dict[tuple[str, str], dict[str, float | None]] = {}
        for score in scores:
            signal_by_id = {s.indicator_id: s for s in score.signals}
            for indicator_id, result in score.indicator_results.items():
                signal = signal_by_id.get(indicator_id)
                raised = (
                    signal is not None
                    and signal.suppressed_reason is None
                    and signal.signal > 0.0
                )
                if not raised:
                    continue
                out[(str(score.entity_id), indicator_id)] = {
                    "effect_size": result.effect_size,
                    "confidence": result.confidence,
                }
        return out

    def _stored_findings(
        self, run_id: str
    ) -> dict[tuple[str, str], dict[str, float | None]]:
        rows = get_connection().execute(
            "SELECT entity_id, indicator_id, effect_size, confidence "
            "FROM finding WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        return {
            (str(r["entity_id"]), str(r["indicator_id"])): {
                "effect_size": r["effect_size"],
                "confidence": r["confidence"],
            }
            for r in rows
        }

    def _diff_report(
        self,
        *,
        pack_row,
        profile: PolicyProfile,
        live_run_id: str,
        window_start: str,
        window_end: str,
        stored: dict[tuple[str, str], dict[str, float | None]],
        candidate: dict[tuple[str, str], dict[str, float | None]],
    ) -> dict[str, Any]:
        stored_keys = set(stored)
        candidate_keys = set(candidate)

        removed_keys = sorted(stored_keys - candidate_keys, key=_finding_key)
        added_keys = sorted(candidate_keys - stored_keys, key=_finding_key)
        changed_keys = [
            key
            for key in sorted(stored_keys & candidate_keys, key=_finding_key)
            if self._changed(stored[key], candidate[key])
        ]

        def entry(
            key: tuple[str, str], data: dict[str, float | None]
        ) -> dict[str, Any]:
            return {
                "entity_id": key[0],
                "indicator_id": key[1],
                "effect_size": data["effect_size"],
                "confidence": data["confidence"],
            }

        removed = [entry(k, stored[k]) for k in removed_keys]
        added = [entry(k, candidate[k]) for k in added_keys]
        changed = [
            {
                "entity_id": key[0],
                "indicator_id": key[1],
                "effect_before": stored[key]["effect_size"],
                "effect_after": candidate[key]["effect_size"],
                "confidence_before": stored[key]["confidence"],
                "confidence_after": candidate[key]["confidence"],
            }
            for key in changed_keys
        ]

        recommendation, reason = self._recommend(added, removed, changed)
        return {
            "window": {"period_start": window_start, "period_end": window_end},
            "live_run_id": live_run_id,
            "pack_id": str(pack_row["pack_id"]),
            "policy": {
                "profile_id": profile.profile_id,
                "version": profile.version,
                "content_hash": profile.content_hash,
            },
            "counts": {
                "changed": len(changed),
                "added": len(added),
                "removed": len(removed),
            },
            "added": added,
            "removed": removed,
            "changed": changed,
            "recommendation": recommendation,
            "recommendation_reason": reason,
        }

    @staticmethod
    def _changed(
        before: dict[str, float | None], after: dict[str, float | None]
    ) -> bool:
        return not (
            _same(before.get("effect_size"), after.get("effect_size"))
            and _same(before.get("confidence"), after.get("confidence"))
        )

    @staticmethod
    def _intensified(change: dict[str, Any]) -> bool:
        """A change that makes an existing finding worse, never better.

        Effect up, or the same effect asserted with more confidence. The
        inverse (weakening or de-asserting) is what a promotion is for.
        """
        before = change["effect_before"] or 0.0
        after = change["effect_after"] or 0.0
        if after > before + _EPS:
            return True
        if abs(after - before) <= _EPS:
            return (change["confidence_after"] or 0.0) > (
                change["confidence_before"] or 0.0
            ) + _EPS
        return False

    def _recommend(
        self,
        added: list[dict[str, Any]],
        removed: list[dict[str, Any]],
        changed: list[dict[str, Any]],
    ) -> tuple[str, str]:
        """The mechanical promote/hold rule, with the reason always stated.

        Promote unless the candidate adds findings or intensifies existing
        ones: a candidate that only removes or weakens findings is a
        tightening policy, which is the normal reason to make a pack live.
        """
        if added:
            worst = max(added, key=lambda c: c["effect_size"] or 0.0)
            reason = (
                f"hold: the candidate introduces {len(added)} new finding(s) not "
                f"on the live run — worst is {worst['entity_id']}/"
                f"{worst['indicator_id']} (effect {worst['effect_size']:g})"
            )
            return "hold", reason
        intensified = [c for c in changed if self._intensified(c)]
        if intensified:
            worst = max(
                intensified,
                key=lambda c: (c["effect_after"] or 0.0) - (c["effect_before"] or 0.0),
            )
            reason = (
                f"hold: the candidate intensifies {len(intensified)} finding(s) "
                f"already live — worst is {worst['entity_id']}/"
                f"{worst['indicator_id']} (effect {worst['effect_before']:g} -> "
                f"{worst['effect_after']:g})"
            )
            return "hold", reason
        if removed or changed:
            reason = (
                f"the candidate introduces no new findings and intensifies none; "
                f"it removes {len(removed)} and adjusts {len(changed)} on the "
                "live window"
            )
        else:
            reason = (
                "the candidate changes nothing on the live window — "
                "behaviourally identical here"
            )
        return "promote", reason

    # ----------------------------------------------------------- registry --
    def _pack_row(self, pack_id: str) -> sqlite3.Row | None:
        return get_connection().execute(
            "SELECT * FROM pack WHERE pack_id = ?", (pack_id,)
        ).fetchone()

    def _get_pack_row(self, pack_id: str) -> sqlite3.Row:
        row = self._pack_row(pack_id)
        if row is None:
            raise PackNotFound(f"pack_id {pack_id} does not exist")
        return row

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


_EPS = 1e-9


def _same(a: float | None, b: float | None) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= _EPS


_service: PackManager | None = None


def get_pack_manager() -> PackManager:
    """The shared pack lifecycle manager."""
    global _service
    if _service is None:
        _service = PackManager()
    return _service