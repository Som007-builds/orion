"""Trend series and change-point models (plan §6.5).

Read-only views over `entity_score`. New in 2.15: the v1 contract froze only
the *routes* (as 503s), so these response shapes are defined by this phase,
not inherited from a frozen module.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from app.schemas.common import OrionModel

#: The entity-level metrics `entity_score` carries; a metric is one column.
TrendMetric = Literal["egi", "nsi", "dts", "sap"]


class TrendPointOut(OrionModel):
    """One period in an entity's series.

    `value` is `None` in two situations the models keep distinct:

    * `gap=True` — the entity has **no score row** for a period the cohort
      scored in (the submission stopped or never covered it). A chart must
      break the line here, not bridge it.
    * `assessable=False` — the row exists but the metric was not computed for
      the entity that period. "Scored nothing" and "could not be scored" are
      different facts, and the series holds both.
    """

    period_start: date
    period_end: date
    value: float | None = None
    assessable: bool = Field(
        False, description="Whether the metric was computed for this period"
    )
    gap: bool = Field(
        False,
        description="No score row for this period: submission missing or never covered it",
    )
    run_id: str | None = Field(
        None, description="The run that wrote this period's score (lineage)"
    )


class TrendSeriesOut(OrionModel):
    """One entity's series for one metric, oldest period first."""

    entity_id: str
    metric: TrendMetric
    points: list[TrendPointOut]
    n_scored: int = Field(
        ..., ge=0, description="Periods with a computed value (neither gap nor unscored)"
    )
    n_gaps: int = Field(..., ge=0)


class ChangePointOut(OrionModel):
    """A period where the cohort's distribution of a metric shifted.

    A prompt to look, not a finding: the same detector flags a genuine
    improvement as reliably as a deterioration, so `direction` reports the sign
    of the measured shift only — never a judgement about the entity.
    """

    period_start: date
    period_end: date
    direction: Literal["up", "down"]
    cohort_median_before: float
    cohort_median_after: float
    shift: float = Field(..., description="median_after - median_before")
    ss_reduction: float = Field(
        ...,
        description="Fraction of within-segment variance the split removes "
        "(least-squares criterion)",
    )
    n_periods_before: int = Field(..., ge=1)
    n_periods_after: int = Field(..., ge=1)


class ChangePointsOut(OrionModel):
    """Regime-shift candidates for a metric across the cohort."""

    metric: TrendMetric
    n_periods: int = Field(
        ..., ge=0, description="Scored periods the detector could reason over"
    )
    change_points: list[ChangePointOut]
    note: str | None = Field(
        None, description="Why the answer is weaker than it looks, when it is"
    )