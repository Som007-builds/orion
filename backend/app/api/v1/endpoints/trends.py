"""Trend series and change-point detection.

503 until Phase 15 (2.15), which is the second thing to cut if the deadline
bites. The route is in the contract now so the frontend's time-series view can be
built against the final shape.

The description says what a change point is *not*, because the name invites the
wrong reading: a change point is a point where the *distribution* of a measured
value shifted. It is not evidence that anything improper happened, and it is not
a verdict about the entity.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.api.v1.deps import ActorDep, PeriodDep
from app.api.v1.errors import NotImplementedResponse, pending, pending_meta

router = APIRouter(tags=["trends"])

PENDING = {"model": NotImplementedResponse, "description": "Not built yet"}
PHASE = "Phase 15 (2.15)"


@router.get(
    "/trends",
    response_model=NotImplementedResponse,
    summary="A scored metric over successive periods",
    description=(
        "503 until Phase 15 (2.15). A series is only meaningful alongside its "
        "assessability per period: an entity that stopped submitting data has a "
        "gap in its series, and that gap must not be drawn as a line through it."
    ),
    responses={503: PENDING},
    openapi_extra=pending_meta("trend_service", PHASE),
)
def trends(
    actor: ActorDep,
    period: PeriodDep,
    entity_id: Annotated[str, Query(description="Entity to trace")] = "",
    metric: Annotated[str, Query(description="egi, nsi, dts or sap")] = "egi",
) -> Any:
    return pending("GET /api/v1/trends", "trend_service", "Phase 15 (2.15)")


@router.get(
    "/trends/change-points",
    response_model=NotImplementedResponse,
    summary="Periods where a metric's distribution shifted",
    description=(
        "503 until Phase 15 (2.15). A change point marks where the shape of the "
        "values changed across the cohort. It is a prompt to look, not a finding: "
        "a genuine improvement produces one exactly as reliably as a deterioration, "
        "and the response must be able to say which direction it moved."
    ),
    responses={503: PENDING},
    openapi_extra=pending_meta("trend_service", PHASE),
)
def change_points(
    actor: ActorDep,
    metric: Annotated[str, Query(description="egi, nsi, dts or sap")] = "egi",
) -> Any:
    return pending(
        "GET /api/v1/trends/change-points", "trend_service", "Phase 15 (2.15)"
    )
