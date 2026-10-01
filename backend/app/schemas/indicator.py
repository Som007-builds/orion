"""Indicator result object — FROZEN INTERFACE #1.

Owner: Developer 2 (Core Backend).
Consumers: Developer 3 (`app/ml/**`), Developer 1 (renders it).

Defined in `docs/build-responsibility.md` §2.1. Changing any field here
requires agreement from all three developers.

Framing (plan §4.1): an indicator is a **reason to look**, expressed with a
baseline, effect size, confidence and counter-evidence. No indicator may assert
non-compliance or act as a regulatory finding.

The hard invariants below are load-bearing, not documentation:

* `assessability == NOT_ASSESSABLE` implies `value is None`.
  A non-assessable dimension never reports a number. Collapsing it to zero is
  how "we could not check this" silently becomes "this is fine".
* `extra="forbid"` everywhere. An unknown field is a bug, not an extension
  point — it hides contract drift between the three developers.
* Missing fields go in `missing_fields`. They never go in
  `benign_explanations`, which is for candidate *innocent* explanations.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class Assessability(str, Enum):
    """Per-dimension assessability (plan §4.4)."""

    ASSESSABLE = "assessable"
    PARTIAL = "partial"
    NOT_ASSESSABLE = "not_assessable"

    @property
    def multiplier(self) -> float:
        return {
            Assessability.ASSESSABLE: 1.0,
            Assessability.PARTIAL: 0.5,
            Assessability.NOT_ASSESSABLE: 0.0,
        }[self]


class Dimension(str, Enum):
    """The eight capability dimensions (plan §4.3). All are represented."""

    TD = "TD"    # Threat Detection
    INV = "INV"  # Investigation
    ESC = "ESC"  # Escalation
    IR = "IR"    # Incident Response
    SO = "SO"    # Security Operations
    GOV = "GOV"  # Governance and Oversight
    OD = "OD"    # Operational Discipline
    CR = "CR"    # Cyber Resilience


class FindingSource(str, Enum):
    """Which engine produced this result."""

    RULES_ENGINE = "rules_engine"
    ML = "ml"
    NEGATIVE_SPACE = "negative_space"


class ConfidenceBreakdown(BaseModel):
    """`confidence = min(1, n/n_min) × assessability × data_trust` (plan §6.2).

    Kept decomposed because an examiner asking "why is confidence 0.2?" needs
    to know which of the three factors drove it down.
    """

    model_config = ConfigDict(extra="forbid")

    n_term: float = Field(..., ge=0.0, le=1.0, description="min(1, n / n_min)")
    assessability_term: float = Field(..., ge=0.0, le=1.0)
    data_trust_term: float = Field(..., ge=0.0, le=1.0, description="DTS")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> float:
        return self.n_term * self.assessability_term * self.data_trust_term


class Baseline(BaseModel):
    """Peer baseline (leave-one-out median/MAD, plan §6.4)."""

    model_config = ConfigDict(extra="forbid")

    median: float | None = None
    mad: float | None = None
    percentile: float | None = Field(None, ge=0.0, le=100.0)
    n_peers: int = Field(..., ge=0)

    method: Literal["loo_median_mad", "eb_shrunk", "covariate_glm"] | None = None
    cohort: str | None = Field(
        None,
        description=(
            "Cohort key: sector x size_tier x soc_model x soc_hours. "
            "MSSP-run SOCs are a separate cohort (plan §4.7)."
        ),
    )


class SelfBaseline(BaseModel):
    """The entity's own history, for trend context."""

    model_config = ConfigDict(extra="forbid")

    median: float | None = None
    mad: float | None = None
    periods: int = Field(..., ge=0)


class Period(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: date
    end: date


class IndicatorResult(BaseModel):
    """The standard result object. Every indicator, rule or ML, returns this."""

    model_config = ConfigDict(extra="forbid")

    # --- identity ---
    indicator_id: str = Field(..., examples=["EG-01", "NS-04"])
    entity_id: str
    period: Period

    # --- observation ---
    value: float | None = Field(
        None, description="None when not computable; never imputed"
    )
    value_units: str | None = None

    # --- context ---
    peer_baseline: Baseline | None = None
    self_baseline: SelfBaseline | None = None
    effect_size: float | None = Field(
        None,
        description="Robust z after EB shrinkage, or a rate ratio",
    )

    # --- support ---
    n: int = Field(..., ge=0, description="Supporting observation count")
    confidence: float = Field(..., ge=0.0, le=1.0)
    confidence_breakdown: ConfidenceBreakdown | None = None

    # --- explainability ---
    evidence_query: str | None = Field(
        None,
        description="Stored, re-runnable query id. `reproduce_finding` re-executes it.",
    )
    evidence_row_ids: list[str] = Field(default_factory=list)

    benign_explanations: list[str] = Field(
        default_factory=list,
        description="Candidate innocent explanations the examiner should check",
    )
    required_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Fields absent from the submission; these drive assessability",
    )

    # --- assessability ---
    assessability: Assessability = Assessability.ASSESSABLE

    # --- classification ---
    family: str | None = None
    primary_dimension: Dimension | None = None
    secondary_dimensions: list[Dimension] = Field(default_factory=list)

    # --- provenance ---
    source: FindingSource
    is_low_confidence_lead: bool = Field(
        False,
        description=(
            "True when a statistical detector fired without per-feature "
            "attribution or evidence rows. Such results are surfaced as leads, "
            "never as findings (plan §4.8.1)."
        ),
    )
    actor_type_inferred: bool = Field(
        False,
        description="True when actor_type was inferred rather than declared (plan §4.7)",
    )
    notes: str | None = None

    # --- post-construction invariants ---
    def model_post_init(self, __context: Any) -> None:
        if self.assessability is Assessability.NOT_ASSESSABLE and self.value is not None:
            raise ValueError(
                f"{self.indicator_id}: assessability is NOT_ASSESSABLE, so `value` "
                "must be None. 'Not assessable' is a first-class outcome and must "
                "never be reported as a number (plan §4.4)."
            )

    @property
    def is_computable(self) -> bool:
        return self.assessability is not Assessability.NOT_ASSESSABLE

    @classmethod
    def not_assessable(
        cls,
        indicator_id: str,
        entity_id: str,
        period: Period,
        missing_fields: list[str],
        source: FindingSource,
        primary_dimension: Dimension | None = None,
        notes: str | None = None,
    ) -> IndicatorResult:
        """Construct a proper `Not assessable` result.

        This exists so every indicator has one obvious way to decline to compute,
        and so a decline can never be mistaken for a low score.
        """
        return cls(
            indicator_id=indicator_id,
            entity_id=entity_id,
            period=period,
            value=None,
            n=0,
            confidence=0.0,
            assessability=Assessability.NOT_ASSESSABLE,
            required_fields=list(missing_fields),
            missing_fields=list(missing_fields),
            source=source,
            primary_dimension=primary_dimension,
            benign_explanations=[],
            notes=notes,
        )


class Attribution(BaseModel):
    """Per-feature contribution to a model score (frozen interface #3)."""

    model_config = ConfigDict(extra="forbid")

    feature: str
    value: float
    peer_median: float | None = None
    effect_size: float
    direction: Literal["increases_risk", "decreases_risk"]


class ModelOutput(BaseModel):
    """Statistical detector output. Dev 3 emits, Dev 2 persists verbatim.

    The language rule (plan §4.8.1): any ML flag must be restatable as *"these
    features deviate in this direction versus this peer group"*. Without
    attributions and evidence rows, it is a low-confidence lead and nothing more.
    """

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(..., examples=["isolation_forest_v1", "ecod_v1"])
    target_type: Literal["case", "entity"]
    target_id: str

    features: dict[str, float]
    score: float
    attributions: list[Attribution] = Field(default_factory=list)
    direction: Literal["above_peer", "below_peer", "within_peer"]
    seed: int
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_row_ids: list[str] = Field(default_factory=list)

    @property
    def explainable(self) -> bool:
        """Whether this can be presented as a finding rather than a lead."""
        return bool(self.attributions) and bool(self.evidence_row_ids)