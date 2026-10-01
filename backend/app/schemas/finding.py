"""Finding card and evidence schemas (frozen interface #2).

Owner: Developer 2. Dev 1 renders; Dev 3 supplies attributions.

Every card answers three questions an examiner must not have to ask:
  1. Why was this flagged?      -> `why_flagged`
  2. Why was this NOT flagged?  -> `why_not_flagged` (incl. what was not computable)
  3. What would change it?      -> `counterfactual`
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import (
    AttentionTier,
    OrionModel,
    PeriodOut,
)
from app.schemas.indicator import (
    Assessability,
    Baseline,
    ConfidenceBreakdown,
    Dimension,
    FindingSource,
    SelfBaseline,
)


class BaselineView(OrionModel):
    """Human-readable baseline for the card."""

    peer_median: float | None = None
    peer_mad: float | None = None
    peer_percentile: float | None = None
    n_peers: int = Field(..., ge=0)
    method: str | None = None
    cohort: str | None = None
    self_baseline: SelfBaseline | None = None


class ConfidenceView(OrionModel):
    total: float = Field(..., ge=0.0, le=1.0)
    breakdown: ConfidenceBreakdown | None = None
    explanation: str | None = Field(
        None,
        description="Which of n / assessability / data trust limited confidence most",
    )


class WhyFlagged(OrionModel):
    indicator_id: str
    indicator_name: str
    value: float | None
    value_units: str | None = None
    baseline: BaselineView
    effect_size: float | None = None
    statement: str = Field(
        ..., description="Hypothesis-framed sentence, never a verdict"
    )


class WhyNotFlagged(OrionModel):
    """The view most systems omit, and the one examiners rely on.

    Must distinguish indicators that ran and stayed quiet from indicators that
    could not run at all. "We found nothing" and "we could not look" are
    different answers.
    """

    indicators_run: list["IndicatorStatus"] = Field(default_factory=list)
    indicators_not_computable: list["IndicatorNotComputable"] = Field(
        default_factory=list
    )
    statement: str


class IndicatorStatus(OrionModel):
    indicator_id: str
    indicator_name: str
    value: float | None = None
    effect_size: float | None = None
    raised: bool
    note: str | None = None


class IndicatorNotComputable(OrionModel):
    indicator_id: str
    indicator_name: str
    missing_fields: list[str] = Field(default_factory=list)
    required_tier: str | None = None
    assessability: Assessability
    reason: str


class Counterfactual(OrionModel):
    """What value or field change would clear or raise the finding."""

    would_clear_at_value: float | None = None
    current_value: float | None = None
    would_clear_at_effect_size: float | None = None
    current_effect_size: float | None = None
    required_fields_if_missing: list[str] = Field(default_factory=list)
    would_raise_at_value: float | None = None
    explanation: str


class EvidenceRowOut(OrionModel):
    """One evidence row. Row IDs are explicit, never an opaque blob."""

    table_name: str
    row_id: str
    fields: dict[str, Any] = Field(default_factory=dict)
    redaction_note: str | None = Field(
        None, description="Set when a field was redacted or pseudonymised"
    )


class EvidenceOut(OrionModel):
    stored_query: str | None = Field(
        None, description="Re-runnable query; `reproduce_finding` executes this"
    )
    query_language: str = "sql"
    rows: list[EvidenceRowOut] = Field(default_factory=list)
    row_count: int = Field(..., ge=0)
    row_id_hash: str | None = Field(
        None, description="Digest over the ordered row IDs, for reproducibility checks"
    )


class LineageOut(OrionModel):
    """Where this finding came from. Never fabricated — null means unknown."""

    run_id: str
    submission_manifest_hashes: list[str] = Field(default_factory=list)
    pack_version: str | None = None
    policy_hash: str | None = None
    policy_profile_id: str | None = None
    code_version: str | None = None
    seed: int | None = None


class CorroboratingSignal(OrionModel):
    indicator_id: str
    indicator_name: str
    dimension: Dimension | None = None
    effect_size: float | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    relationship: str = Field(
        ..., description="e.g. 'same_family', 'same_dimension', 'independent_signal'"
    )


class SuggestedAction(OrionModel):
    action: str
    rationale: str
    evidence_row_ids: list[str] = Field(default_factory=list)
    priority: str = "normal"


class FindingCardOut(OrionModel):
    """`GET /findings/{id}` — the full card."""

    finding_id: str
    entity_id: str
    entity_name: str | None = None
    period: PeriodOut

    indicator_id: str
    indicator_name: str
    summary: str = Field(
        ..., description="One line, hypothesis-framed. Never a regulatory finding."
    )

    value: float | None = None
    value_units: str | None = None

    baseline: BaselineView
    effect_size: float | None = None
    n: int = Field(..., ge=0)
    confidence: ConfidenceView

    family: str | None = None
    primary_dimension: Dimension | None = None
    secondary_dimensions: list[Dimension] = Field(default_factory=list)
    source: FindingSource
    is_low_confidence_lead: bool = False
    actor_type_inferred: bool = False

    why_flagged: WhyFlagged
    why_not_flagged: WhyNotFlagged
    counterfactual: Counterfactual

    corroborating_signals: list[CorroboratingSignal] = Field(default_factory=list)
    benign_explanations: list[str] = Field(
        default_factory=list,
        description="Candidate innocent explanations the examiner should check first",
    )
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)

    assessability: Assessability
    missing_fields: list[str] = Field(default_factory=list)

    lineage: LineageOut
    caveats: list[str] = Field(default_factory=list)


class FindingListItem(OrionModel):
    """Row in `GET /findings` — deliberately lighter than the card."""

    finding_id: str
    entity_id: str
    entity_name: str | None = None
    indicator_id: str
    indicator_name: str
    period: PeriodOut
    value: float | None = None
    effect_size: float | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    primary_dimension: Dimension | None = None
    family: str | None = None
    assessability: Assessability
    is_low_confidence_lead: bool = False
    sap_tier: AttentionTier | None = None
    summary: str


# Resolve the forward reference in WhyNotFlagged.
WhyNotFlagged.model_rebuild()


class CounterfactualOut(OrionModel):
    """`GET /findings/{id}/counterfactual`."""

    finding_id: str
    indicator_id: str
    counterfactual: Counterfactual
    baseline: Baseline
    peer_sensitivity: list[dict[str, Any]] = Field(
        default_factory=list,
        description="How the effect size moves as peer count varies",
    )