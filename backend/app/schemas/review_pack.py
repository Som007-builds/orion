"""Review pack schemas (frozen interface #4).

Owner: Developer 2.

A review pack is a *sample*, not a ranked list. Two fields make it defensible:

* `inclusion_prob` — the known inclusion probability π_i. Without it, no
  prevalence estimate can be defended.
* the control slice. A pack containing only the top-scoring cases cannot
  distinguish "the tool ranked well" from "the pack was cherry-picked".

Inclusion probability is recorded per item rather than assumed uniform, because
PPS sampling makes it non-uniform by construction.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from app.schemas.common import OrionModel, Severity


class SliceType(str, Enum):
    TARGETED = "targeted"   # PPS by risk
    CONTROL = "control"     # random, severity-stratified


class Verdict(str, Enum):
    """Examiner verdict for calibration."""

    CONFIRMED = "confirmed"
    BENIGN = "benign"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class VerificationPrompt(OrionModel):
    question: str
    expected_evidence: str | None = None
    prompt_type: str = Field(
        ..., description="e.g. 'check_escalation_record', 'check_human_reviewer'"
    )


class ReviewPackItemOut(OrionModel):
    case_id: str
    entity_id: str
    slice_type: SliceType

    case_risk_score: float = Field(..., ge=0.0)
    inclusion_prob: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="π_i. Null only for deterministic-inclusion control items.",
    )

    severity: Severity | None = None
    selected_because: str = Field(
        ...,
        description="Prose explaining why this case is in the pack",
    )
    verification_prompts: list[VerificationPrompt] = Field(default_factory=list)

    contributing_indicators: list[str] = Field(default_factory=list)
    cluster_id: str | None = None
    analyst_pseudo: str | None = None

    finding_ids: list[str] = Field(default_factory=list)
    verdict: Verdict | None = None


class HTEstimate(OrionModel):
    """Horvitz-Thompson prevalence estimate with a confidence interval."""

    estimate: float
    ci_low: float
    ci_high: float
    n_sampled: int = Field(..., ge=0)
    n_population: int = Field(..., ge=0)
    confidence_level: float = Field(0.95, gt=0.0, lt=1.0)
    method: str = Field(..., description="Horvitz-Thompson with stratified variance")

    caveat: str | None = Field(
        None,
        description=(
            "Always present on stratified samples: the estimate is only as good "
            "as the severity stratification."
        ),
    )


class ReviewPackOut(OrionModel):
    """`GET /review-packs/{id}`."""

    pack_id: str
    created_ts: str
    created_by: str
    period_start: str
    period_end: str
    stratum: str | None = None

    n_target: int = Field(..., ge=0)
    n_control: int = Field(..., ge=0)
    n_selected: int = Field(..., ge=0)
    n_population: int = Field(..., ge=0)

    items: list[ReviewPackItemOut] = Field(default_factory=list)

    ht_estimate: HTEstimate | None = None
    diversity_caps_applied: dict[str, int] = Field(default_factory=dict)
    content_hash: str | None = None


class ReviewPackListItem(OrionModel):
    pack_id: str
    created_ts: str
    created_by: str
    period_start: str
    period_end: str
    n_selected: int = Field(..., ge=0)
    ht_estimate: float | None = None
    content_hash: str | None = None


class ReviewPackGenerateIn(OrionModel):
    """`POST /review-packs`."""

    period_start: str | None = None
    period_end: str | None = None
    entity_ids: list[str] | None = Field(
        None, description="None means all entities in scope"
    )
    n_target: int = Field(20, ge=1, le=500)
    n_control: int = Field(10, ge=0, le=500)
    control_severity_strata: list[Severity] = Field(
        default_factory=lambda: [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]
    )
    max_per_cluster: int = Field(3, ge=1, le=50)
    max_per_analyst: int = Field(3, ge=1, le=50)
    seed: int | None = Field(
        None, description="Recorded on the pack so the sample is reproducible"
    )


class VerdictIn(OrionModel):
    """`POST /verdicts`. Ledgered."""

    pack_id: str
    case_id: str
    verdict: Verdict
    notes: str | None = None
    evidence_seen: list[str] = Field(
        default_factory=list,
        description="Row IDs the examiner actually checked",
    )


class VerdictOut(OrionModel):
    verdict_id: str
    pack_id: str
    case_id: str
    verdict: Verdict
    notes: str | None = None
    examiner_pseudo: str
    recorded_ts: str
    ledger_entry_hash: str


class ReviewPackExportOut(OrionModel):
    """`GET /review-packs/{id}/export`."""

    pack_id: str
    formats: list[str] = Field(default_factory=list)
    content_hash: str
    ledger_head_hash: str
    signature: str | None = Field(
        None, description="Ed25519; null only for unsigned test packs"
    )
    signed_by: str | None = None
    export_paths: list[str] = Field(default_factory=list)