"""Assessability schema (plan §4.4).

`Not assessable` is a first-class outcome. The `Not assessable` state is
reported as its own value and never collapsed into a low score.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import DataTier, OrionModel
from app.schemas.indicator import Assessability, Dimension


class DimensionAssessability(OrionModel):
    """Assessability for one capability dimension."""

    dimension: Dimension
    assessability: Assessability
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Always populated when not fully assessable — never omitted",
    )
    required_fields: list[str] = Field(default_factory=list)
    min_tier: DataTier | None = None
    achieved_tier: DataTier | None = None
    reason: str | None = None
    affected_indicators: list[str] = Field(
        default_factory=list,
        description="Indicators blocked by this dimension's gaps",
    )


class AssessabilityReport(OrionModel):
    """`GET /entities/{id}/assessability`."""

    entity_id: str
    submission_id: str | None = None
    period_start: str | None = None
    period_end: str | None = None

    overall: Assessability
    data_tier: DataTier

    dimensions: list[DimensionAssessability] = Field(default_factory=list)

    n_assessable: int = Field(0, ge=0)
    n_partial: int = Field(0, ge=0)
    n_not_assessable: int = Field(0, ge=0)

    upgrade_guidance: list[str] = Field(
        default_factory=list,
        description=(
            "What data would raise coverage — e.g. 'export case_event history "
            "to enable EG-04, EG-12, EG-13'."
        ),
    )
    interpretation: str = Field(
        ...,
        description="Plain-language statement of what can and cannot be assessed",
    )


class AssessabilityWeights(OrionModel):
    """Multiplier applied to `confidence` (plan §6.2 step 4)."""

    assessable: float = 1.0
    partial: float = 0.5
    not_assessable: float = 0.0

    def multiplier(self, assessability: Assessability) -> float:
        return {
            Assessability.ASSESSABLE: self.assessable,
            Assessability.PARTIAL: self.partial,
            Assessability.NOT_ASSESSABLE: self.not_assessable,
        }[assessability]