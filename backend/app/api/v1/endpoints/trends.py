"""Trend series and change-point detection (plan §6.5).

Live since 2.15. A series is per entity and only meaningful alongside its
assessability per period: an entity that stopped submitting data has a gap in
its series, and the response marks it so a chart never draws a line through it.

A change point marks where the *distribution* of a measured value shifted
across the cohort. It is not evidence that anything improper happened and it is
not a verdict about the entity: the same detector flags a genuine improvement
exactly as reliably as a deterioration, and the response says which direction
the measured value moved.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.v1.deps import ActorDep, PeriodDep
from app.schemas.trends import ChangePointsOut, TrendMetric, TrendSeriesOut
from app.services.trend_service import (
    TrendEntityNotFound,
    get_trend_service,
)

router = APIRouter(tags=["trends"])


@router.get(
    "/trends",
    response_model=TrendSeriesOut,
    summary="A scored metric over successive periods",
    description=(
        "One entity's series for one metric, oldest period first. A series is "
        "only meaningful alongside its assessability per period: an entity that "
        "stopped submitting data has a gap in its series, and that gap is "
        "flagged (`gap: true`) rather than bridged — a chart must break the "
        "line, not draw straight through it. A period whose row exists but "
        "whose metric was not computed is `assessable: false`, which is a "
        "different fact from a gap.\n\n"
        "`entity_id` is required; pass `period_start`/`period_end` to window "
        "the series."
    ),
    responses={404: {"description": "No such entity"}},
)
def trends(
    _actor: ActorDep,
    period: PeriodDep,
    entity_id: Annotated[str, Query(description="Entity to trace")] = "",
    metric: Annotated[TrendMetric, Query(description="egi, nsi, dts or sap")] = "egi",
) -> TrendSeriesOut:
    try:
        return get_trend_service().entity_trend(
            entity_id, metric, period.start, period.end
        )
    except TrendEntityNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get(
    "/trends/change-points",
    response_model=ChangePointsOut,
    summary="Periods where a metric's distribution shifted",
    description=(
        "A change point marks where the shape of a metric shifted across the "
        "cohort: the per-period cohort median is segmented with a least-squares "
        "criterion, and each split that clears the shift and variance floors is "
        "reported at the period the new regime begins. It is a prompt to look, "
        "not a finding: a genuine improvement produces one exactly as reliably "
        "as a deterioration, so `direction` only says which way the measured "
        "value moved.\n\n"
        "Pass `period_start`/`period_end` to reason over a window; without one "
        "the whole scored history is used. Periods too sparsely scored to stand "
        "for the cohort are skipped, and `note` says so — the answer must never "
        "look stronger than the data behind it."
    ),
)
def change_points(
    _actor: ActorDep,
    period: PeriodDep,
    metric: Annotated[TrendMetric, Query(description="egi, nsi, dts or sap")] = "egi",
) -> ChangePointsOut:
    return get_trend_service().change_points(metric, period.start, period.end)