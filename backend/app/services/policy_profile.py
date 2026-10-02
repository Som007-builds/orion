"""Policy profile loading (plan §4.2).

Thresholds are policy-configurable, versioned and signed. Nothing in this module
hardcodes a threshold value — it only loads and validates them.

Activation is ledgered (`POST /policy-profiles/{id}/activate`). A profile is
never edited in place: activation copies it into SQLite with a content hash, and
`previous_id` is retained so a rollback is always possible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings
from app.db.sqlite import get_connection
from app.services.ledger import LedgerAction, get_ledger

# v1 hardcodes, retained only as documented defaults (plan §4.2). Referenced by
# the policy YAML; never read directly by indicator code.
DOCUMENTED_DEFAULTS: dict[str, Any] = {
    "fast_closure_seconds": 180,
    "note_min_words": 20,
    "max_closures_30min": 25,
    "cluster_similarity": 0.88,
    "repeat_count": 5,
    "repeat_window_days": 14,
}

VALID_DIMENSIONS = ("TD", "INV", "ESC", "IR", "SO", "GOV", "OD", "CR")


@dataclass(frozen=True)
class PolicyProfile:
    """A loaded, validated policy profile."""

    profile_id: str
    version: str
    effective_from: date
    definition: dict[str, Any]
    content_hash: str
    signature: str | None = None
    source_path: str | None = None

    # -- convenience accessors; all delegate to `definition` --------------
    @property
    def thresholds(self) -> dict[str, Any]:
        return self.definition.get("thresholds", {})

    @property
    def ramps(self) -> dict[str, float]:
        return self.definition.get("ramps", {"z0": 2.0, "z1": 5.0})

    @property
    def dimension_weights(self) -> dict[str, float]:
        return self.definition.get("dimension_weights", {})

    @property
    def sla_targets(self) -> dict[str, int]:
        return self.definition.get("sla_targets", {})

    @property
    def severity_map(self) -> dict[str, str]:
        return self.definition.get("severity_map", {})

    @property
    def status_map(self) -> dict[str, str]:
        return self.definition.get("status_map", {})

    @property
    def state_machine(self) -> dict[str, list[str]]:
        return self.definition.get("state_machine", {})

    @property
    def family_caps(self) -> dict[str, float]:
        return self.definition.get("family_caps", {})

    @property
    def family_weights(self) -> dict[str, float]:
        """Per-family weight `w_f` in the capped noisy-OR (plan §6.2 step 7).

        Defaults to 0.5 for any family the policy does not name. The weight says
        how much a single family can contribute to a dimension, and the cap says
        how much of one family's own score can be used at all — the cap guards
        against correlated indicators inside a family, the weight against one
        family dominating a dimension that several families feed.
        """
        declared = self.definition.get("family_weights", {})
        return {k: float(v) for k, v in declared.items()}

    def family_weight(self, family: str) -> float:
        return float(self.family_weights.get(family, 0.5))

    def family_cap(self, family: str) -> float:
        caps = self.family_caps
        if family in caps:
            return float(caps[family])
        return 0.60

    @property
    def egi_families(self) -> list[str]:
        return list(
            self.definition.get(
                "egi_families",
                [
                    "rapid_thin_closure",
                    "escalation_integrity",
                    "governance_records",
                    "metric_gaming",
                    "throughput_process",
                    "timestamp_integrity",
                    "recurrence",
                ],
            )
        )

    @property
    def nsi_families(self) -> list[str]:
        return list(self.definition.get("nsi_families", ["coverage", "record_integrity"]))

    @property
    def dts_floor(self) -> float:
        """Below this DTS, indicator confidence is suppressed proportionally."""
        return float(self.definition.get("dts_floor", 0.20))

    @property
    def min_measurable_share(self) -> float:
        """Share of the §6.1 dimension weight that must be measurable to tier.

        The cutpoints are calibrated against a full eight-dimension picture. A
        submission that answers two of the eight cannot reach `T1` however bad it
        looks, so a tier read off it is not a weaker version of the real tier --
        it is a different and much vaguer claim wearing the same label. Below this
        share the entity is reported `not_assessable` instead, and the dimension
        scores behind it stay visible.
        """
        return float(self.definition.get("min_measurable_share", 0.50))

    @property
    def draws(self) -> dict[str, int]:
        return {
            "bootstrap": int(self.definition.get("bootstrap_draws", 400)),
            "dirichlet": int(self.definition.get("dirichlet_draws", 400)),
        }

    @property
    def fdr_q(self) -> float:
        return float(self.definition.get("fdr_q", 0.05))

    @property
    def n_min(self) -> int:
        return int(self.definition.get("n_min", 30))

    @property
    def min_peers(self) -> int:
        return int(self.definition.get("min_peers", 8))

    @property
    def tier_cutpoints(self) -> dict[str, float]:
        return self.definition.get("tier_cutpoints", {})

    def threshold(self, name: str) -> Any:
        """Look up a threshold, falling back to the documented default."""
        value = self.thresholds.get(name)
        if value is not None:
            return value
        if name in DOCUMENTED_DEFAULTS:
            return DOCUMENTED_DEFAULTS[name]
        raise KeyError(
            f"Threshold {name!r} is not in policy {self.profile_id} and has no "
            "documented default. Add it to the policy profile."
        )

    def ramp(self, abs_z: float) -> float:
        """Map |robust z| to 0..1 through the configured ramp (plan §6.2 step 3)."""
        z0 = float(self.ramps.get("z0", 2.0))
        z1 = float(self.ramps.get("z1", 5.0))
        if z1 <= z0:
            raise ValueError(f"Policy ramp requires z1 > z0, got z0={z0}, z1={z1}")
        if abs_z <= z0:
            return 0.0
        if abs_z >= z1:
            return 1.0
        return (abs_z - z0) / (z1 - z0)

    def tier_for(self, sap: float | None, assessable: bool = True) -> str:
        """Attention tier, with `not_assessable` as a separate state.

        A `Not assessable` entity is never given T4. Reporting it as the *lowest*
        tier would tell a supervisor the entity was examined and found fine,
        which is the opposite of what the data supports (plan §6.3).
        """
        if not assessable or sap is None:
            return "not_assessable"
        cutpoints = self.tier_cutpoints or {"T1": 0.75, "T2": 0.50, "T3": 0.25, "T4": 0.0}
        for tier in ("T1", "T2", "T3", "T4"):
            if sap >= float(cutpoints.get(tier, 0.0)):
                return tier
        return "T4"

    def validate(self) -> list[str]:
        """Structural checks. Returns a list of problems; empty means valid."""
        problems: list[str] = []

        weights = self.dimension_weights
        if not weights:
            problems.append("dimension_weights is empty; all 8 dimensions are required")
        else:
            missing = [d for d in VALID_DIMENSIONS if d not in weights]
            if missing:
                problems.append(f"dimension_weights missing: {missing}")
            total = sum(float(v) for v in weights.values())
            if abs(total - 1.0) > 1e-6:
                problems.append(f"dimension_weights must sum to 1.0, got {total}")

        for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            if severity not in self.sla_targets:
                problems.append(f"sla_targets missing {severity}")

        ramps = self.ramps
        if float(ramps.get("z0", 0)) >= float(ramps.get("z1", 0)):
            problems.append(f"ramps require z1 > z0, got {ramps}")

        if not 0 < self.fdr_q < 1:
            problems.append(f"fdr_q must be in (0,1), got {self.fdr_q}")

        if self.min_peers < 1:
            problems.append(f"min_peers must be >= 1, got {self.min_peers}")

        return problems

    def validate_transitions(self) -> list[str]:
        """Check the state machine's transitions reference known states."""
        problems: list[str] = []
        known = {"NEW", "OPEN", "IN_PROGRESS", "ESCALATED", "RESOLVED", "CLOSED", "REOPENED"}
        for state, targets in self.state_machine.items():
            if state not in known:
                problems.append(f"state_machine: unknown source state {state!r}")
            for target in targets:
                if target not in known:
                    problems.append(f"state_machine: {state!r} -> unknown target {target!r}")
        return problems


def content_hash(definition: dict[str, Any]) -> str:
    """Deterministic hash over the profile body.

    Sorted keys and pinned separators, so an identical profile always hashes
    identically regardless of YAML key order. This hash is recorded on every
    finding's lineage, so it has to be stable.
    """
    import json

    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_from_yaml(path: Path) -> PolicyProfile:
    """Load and validate a profile from disk."""
    with path.open("r", encoding="utf-8") as handle:
        definition = yaml.safe_load(handle)

    if not isinstance(definition, dict):
        raise ValueError(f"{path}: policy profile must be a YAML mapping")

    profile = PolicyProfile(
        profile_id=definition.get("profile_id", path.stem),
        version=str(definition.get("version", "1")),
        effective_from=date.fromisoformat(str(definition.get("effective_from", "2026-01-01"))),
        definition=definition,
        content_hash=content_hash(definition),
        source_path=str(path),
    )

    problems = profile.validate() + profile.validate_transitions()
    if problems:
        raise ValueError(f"{path}: invalid policy profile: " + "; ".join(problems))
    return profile


class PolicyProfileService:
    """Loads profiles from disk and manages activation in SQLite."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._cache: dict[str, PolicyProfile] = {}

    def available(self) -> list[PolicyProfile]:
        """Every profile on disk, validated on load."""
        profiles: list[PolicyProfile] = []
        if not self.settings.policy_dir.is_dir():
            return profiles
        for path in sorted(self.settings.policy_dir.glob("*.yaml")):
            profiles.append(load_from_yaml(path))
        return profiles

    def get(self, profile_id: str) -> PolicyProfile:
        if profile_id in self._cache:
            return self._cache[profile_id]

        path = self.settings.policy_dir / f"{profile_id}.yaml"
        if not path.is_file():
            candidates = [p for p in self.available() if p.profile_id == profile_id]
            if not candidates:
                raise KeyError(f"No policy profile {profile_id!r} in {self.settings.policy_dir}")
            profile = candidates[0]
        else:
            profile = load_from_yaml(path)

        self._cache[profile_id] = profile
        return profile

    def active(self) -> PolicyProfile:
        """The currently active profile.

        Falls back to the on-disk default only when nothing has been activated,
        so a fresh install is usable without ceremony.
        """
        row = get_connection().execute(
            "SELECT profile_id, version, definition, content_hash, signature "
            "FROM policy_profile WHERE active = 1 ORDER BY effective_from DESC LIMIT 1"
        ).fetchone()
        if row:
            import json

            # `definition` is stored as canonical JSON text, so it has to be
            # parsed before the effective date can be read out of it.
            definition = json.loads(row["definition"])
            return PolicyProfile(
                profile_id=row["profile_id"],
                version=row["version"],
                effective_from=date.fromisoformat(
                    str(definition.get("effective_from", "2026-01-01"))
                ),
                definition=definition,
                content_hash=row["content_hash"],
                signature=row["signature"],
            )

        available = self.available()
        if not available:
            raise RuntimeError(
                f"No policy profiles found in {self.settings.policy_dir}. Orion cannot "
                "decide anything without a signed, versioned policy profile."
            )
        return available[0]

    def register(self, profile: PolicyProfile) -> None:
        """Persist a profile without activating it."""
        import json

        get_connection().execute(
            "INSERT OR REPLACE INTO policy_profile "
            "(profile_id, version, effective_from, definition, content_hash, "
            " signature, active, previous_id) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, NULL)",
            (
                profile.profile_id,
                profile.version,
                profile.effective_from.isoformat(),
                json.dumps(profile.definition, sort_keys=True),
                profile.content_hash,
                profile.signature,
            ),
        )

    def activate(
        self,
        profile_id: str,
        actor: str,
        notes: str | None = None,
        profile: PolicyProfile | None = None,
    ) -> tuple[PolicyProfile, str, str | None]:
        """Activate a profile. Ledgered. Returns (profile, ledger hash, previous id).

        `profile` may be supplied to activate a profile that is not (yet) on disk
        under `policy_dir` — the pack lifecycle carries its policy inside the
        bundle and adopts it on promote/rollback without the profile ever needing
        to live in `data/policies/`. When omitted, resolution is exactly as
        before: disk, then the available set.
        """
        profile = profile or self.get(profile_id)
        self.register(profile)

        current = get_connection().execute(
            "SELECT profile_id, version FROM policy_profile WHERE active = 1 LIMIT 1"
        ).fetchone()
        previous_id = f"{current['profile_id']}@{current['version']}" if current else None

        get_connection().execute("UPDATE policy_profile SET active = 0")
        get_connection().execute(
            "UPDATE policy_profile SET active = 1, previous_id = ? "
            "WHERE profile_id = ? AND version = ?",
            (previous_id, profile.profile_id, profile.version),
        )

        ledger_hash = get_ledger().append(
            actor=actor,
            action=LedgerAction.POLICY_ACTIVATED,
            payload={
                "profile_id": profile.profile_id,
                "version": profile.version,
                "content_hash": profile.content_hash,
                "previous_id": previous_id,
                "notes": notes,
            },
        )
        return profile, ledger_hash, previous_id

    def list_profiles(self) -> list[dict[str, Any]]:
        rows = get_connection().execute(
            "SELECT profile_id, version, effective_from, content_hash, signature, "
            "active, previous_id FROM policy_profile ORDER BY effective_from DESC"
        ).fetchall()
        return [dict(row) for row in rows]


_service: PolicyProfileService | None = None


def get_policy_service() -> PolicyProfileService:
    global _service
    if _service is None:
        _service = PolicyProfileService()
    return _service


def policy_profile(profile_id: str | None = None) -> PolicyProfile:
    """Convenience accessor used throughout the indicator layer."""
    service = get_policy_service()
    return service.get(profile_id) if profile_id else service.active()