"""Entity and scoring response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import (
    AttentionTier,
    CoverageType,
    OrionModel,
    RankInterval,
    SizeTier,
    SocModel,
)
from app.schemas.indicator import Assessability, Dimension


class SocHours(OrionModel):
    """Declared coverage (plan §3.4). Backs EG-03 and NS-03."""

    coverage_type: CoverageType
    declared_open: str = Field(..., description="HH:MM local")
    declared_close: str
    roster_ref: str | None = None


class DimensionScoreOut(OrionModel):
    dimension: Dimension
    score: float | None = Field(
        None, description="None when Not assessable — never 0.0 as a substitute"
    )
    assessability: Assessability
    missing_fields: list[str] = Field(default_factory=list)
    ci_low: float | None = None
    ci_high: float | None = None


class EntityOut(OrionModel):
    """Row in `GET /entities`."""

    entity_id: str
    name: str
    sector: str
    soc_model: SocModel
    size_tier: SizeTier
    coverage_type: CoverageType
    critical_asset_count: int = Field(..., ge=0)


class EntityListItem(EntityOut):
    """Entity plus its supervisory scores, for the executive view."""

    egi: float | None = Field(None, description="Execution Gap Index 0..1")
    nsi: float | None = Field(None, description="Negative Space Index 0..1")
    dts: float | None = Field(
        None, description="Data Trust Score 0..1 — confidence the submission supports assessment"
    )
    sap: float | None = Field(None, description="Supervisory Attention Priority")
    sap_tier: AttentionTier
    sap_rank_interval: RankInterval | None = None
    period_start: str | None = None
    period_end: str | None = None
    overall_assessability: Assessability = Assessability.ASSESSABLE


class EntitySummaryOut(OrionModel):
    """`GET /entities/{id}/summary` — the executive scorecard."""

    entity: EntityOut
    period_start: str
    period_end: str

    egi: float | None = None
    nsi: float | None = None
    dts: float | None = None

    dimensions: list[DimensionScoreOut] = Field(default_factory=list)

    sap: float | None = None
    sap_tier: AttentionTier
    sap_rank_interval: RankInterval | None = None

    n_findings: int = Field(0, ge=0)
    n_not_assessable_dimensions: int = Field(0, ge=0)

    caveats: list[str] = Field(
        default_factory=list,
        description="Plain-language reasons the result is incomplete or uncertain",
    )

    model_config = {**OrionModel.model_config, "extra": "forbid"}


class BenchmarkOut(OrionModel):
    """`GET /benchmarks` — cohort baselines."""

    cohort: str
    metric: str
    n_peers: int = Field(..., ge=0)
    peer_median: float
    peer_mad: float | None = None
    method: str
    fell_back_to_covariate_model: bool = False


class SocrHypothesisNote(OrionModel):
    """Helper for the brief: a hypothesis in prose, never a verdict."""

    indicator_id: str
    statement: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    supporting_row_ids: list[str] = Field(default_factory=list)
    caveats: dict[str, Any] | None = None