"""Ledger, run, policy profile, pack, submission and triage schemas."""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import OrionModel


# --------------------------------------------------------------------- ledger --
class LedgerEntryOut(OrionModel):
    seq: int = Field(..., ge=1)
    entry_hash: str
    prev_hash: str
    timestamp: str
    actor: str
    action: str
    payload_hash: str
    payload: dict | None = None
    entity_id: str | None = None


class LedgerVerifyOut(OrionModel):
    """`GET /ledger/verify`.

    `first_break_seq` is reported alongside `valid` because an operator needs to
    know *where* the chain diverges to distinguish tampering from a partial
    restore. A bare boolean is not actionable.
    """

    valid: bool
    head_hash: str
    entries_checked: int = Field(..., ge=0)
    first_break_seq: int | None = None
    break_reason: str | None = None
    verified_at: str


# ------------------------------------------------------------------------ run --
class RunTriggerIn(OrionModel):
    period_start: str | None = None
    period_end: str | None = None
    entity_ids: list[str] | None = None
    pack_id: str | None = None
    policy_profile_id: str | None = None
    indicators: list[str] | None = Field(
        None, description="None means the full P0 set"
    )


class RunOut(OrionModel):
    run_id: str
    created_ts: str
    started_ts: str | None = None
    finished_ts: str | None = None
    status: str
    input_manifest_hashes: list[str] = Field(default_factory=list)
    config_hash: str | None = None
    pack_version: str | None = None
    policy_hash: str | None = None
    code_version: str | None = None
    seed: int | None = None
    output_hash: str | None = None
    error: str | None = None
    n_findings: int = Field(0, ge=0)


# ------------------------------------------------------------ policy profile --
class PolicyProfileOut(OrionModel):
    profile_id: str
    version: str
    effective_from: str
    content_hash: str
    signature: str | None = None
    active: bool
    previous_id: str | None = None
    summary: dict = Field(default_factory=dict)


class PolicyActivateIn(OrionModel):
    effective_from: str | None = None
    notes: str | None = None


class PolicyActivateOut(OrionModel):
    profile_id: str
    version: str
    activated: bool
    previous_id: str | None = None
    ledger_entry_hash: str


# ----------------------------------------------------------------------- pack --
class PackStageIn(OrionModel):
    pack_id: str | None = None
    source_path: str
    version: str
    signed_by: str | None = None


class PackOut(OrionModel):
    pack_id: str
    version: str
    content_hash: str
    signature: str
    status: str
    staged_ts: str
    promoted_ts: str | None = None
    signed_by: str | None = None


class PackShadowOut(OrionModel):
    pack_id: str
    status: str
    n_findings_changed: int = Field(..., ge=0)
    n_findings_added: int = Field(..., ge=0)
    n_findings_removed: int = Field(..., ge=0)
    diff_report: dict = Field(default_factory=dict)
    recommendation: str | None = Field(
        None, description="'promote' or 'hold', with the reason stated"
    )
    ledger_entry_hash: str


class PackTransitionOut(OrionModel):
    pack_id: str
    status: str
    previous_id: str | None = None
    ledger_entry_hash: str


# --------------------------------------------------------------- compat alias --
class TriageOut(OrionModel):
    """`GET /triage` — compatibility alias for the latest review pack.

    Retained so the v1 frontend contract keeps resolving while review packs
    become the primary surface (plan §5).
    """

    pack_id: str
    period_start: str
    period_end: str
    n_selected: int = Field(..., ge=0)
    items: list[dict] = Field(default_factory=list)
    deprecation_note: str = (
        "Use /review-packs instead. This alias is retained for compatibility."
    )


# -------------------------------------------------------------------- report --
class BriefExportIn(OrionModel):
    include_evidence: bool = True
    include_not_assessable: bool = True
    formats: list[str] = Field(default_factory=lambda: ["pdf"])
    sign: bool = True


class BriefExportOut(OrionModel):
    entity_id: str
    period_start: str
    period_end: str
    formats: list[str] = Field(default_factory=list)
    content_hash: str
    ledger_head_hash: str
    signature: str | None = None
    export_paths: list[str] = Field(default_factory=list)
    ledger_entry_hash: str
    framing_note: str = (
        "This is a decision-support brief for human examiners. It contains "
        "prioritised hypotheses with evidence, not verdicts or compliance grades."
    )