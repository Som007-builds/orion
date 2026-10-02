"""Entity reads: the list, the per-entity scorecard, and assessability.

Every score field on these routes is nullable, and null is never rendered as zero
by this layer. A dimension that could not be assessed has no number, and the
routes above pass that absence through rather than substituting something the UI
would then chart as a real value.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.v1.deps import ConnectionDep, PeriodDep
from app.schemas.assessability import AssessabilityReport
from app.schemas.common import Page
from app.schemas.entity import EntityListItem, EntitySummaryOut
from app.services.assessability import AssessabilityService
from app.services.scoring_service import ScoringError, get_scoring_service

router = APIRouter(tags=["entities"])


@router.get(
    "/entities",
    response_model=Page[EntityListItem],
    summary="Every registered entity with its supervisory scores",
    description=(
        "Entities with no scoring data are included with null scores and the "
        "`not_assessable` tier. They are not omitted: the executive view has to "
        "be able to distinguish *registered, nothing submitted* from *submitted, "
        "nothing found*, and a list that only ever shows scored entities cannot."
    ),
)
def list_entities(
    period: PeriodDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[EntityListItem]:
    rows = get_scoring_service().entity_list(period.start, period.end)
    return Page[EntityListItem](
        items=rows[offset : offset + limit],
        total=len(rows),
        limit=limit,
        offset=offset,
        has_more=offset + limit < len(rows),
    )


@router.get(
    "/entities/{entity_id}/summary",
    response_model=EntitySummaryOut,
    summary="The executive scorecard for one entity",
    description=(
        "The drill-down behind a row of `GET /entities`, and it must agree with "
        "that row: both read `overall_assessability` off the persisted score, so "
        "the list and the scorecard cannot state different assessability for the "
        "same entity.\n\n"
        "Two fields answer different questions and are read together.\n"
        "`overall_assessability` is about coverage — whether Orion could examine "
        "the entity at all. `not_assessable` there means *nothing* was measurable, "
        "and is reserved for that: a well-run entity whose detectors find nothing "
        "is `assessable`, never `not_assessable`. `sap_tier` is about calibration "
        "— whether what was measured can be placed against cutpoints, which "
        "requires more of the dimension set than coverage alone. So `partial` "
        "alongside a withheld tier is the ordinary, correct reading today: "
        "measured in part, and not enough measured to prioritise.\n\n"
        "`caveats` states what Orion could not see. `run_id` names the run these "
        "figures came from, so a scorecard and the ledger can be tied to one "
        "another.\n\n"
        "Every dimension with `assessability != assessable` has `score: null`. "
        "Render the absence; do not coerce it to 0.0.\n\n"
        "If no run covers this entity for the period, the figures are computed on "
        "demand and `caveats` says so — they will not match a later run."
    ),
    responses={404: {"description": "No such entity"}},
)
def entity_summary(entity_id: str, period: PeriodDep) -> EntitySummaryOut:
    try:
        return get_scoring_service().entity_summary(entity_id, period.start, period.end)
    except ScoringError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get(
    "/entities/{entity_id}/assessability",
    response_model=AssessabilityReport,
    summary="What can and cannot be assessed for this entity, and why",
    description=(
        "This is the route that answers 'why is this number missing'. It resolves "
        "the period from the entity's most recent submission; pass `period` to "
        "gate against a different window."
    ),
    responses={404: {"description": "No such entity"}},
)
def entity_assessability(entity_id: str, conn: ConnectionDep) -> AssessabilityReport:
    # The service deliberately answers for an unknown entity rather than raising —
    # an entity with no submissions really is a report full of gaps, and that is
    # a useful answer. But here the id came from a URL, so "no such entity" and
    # "no submissions yet" have to stay distinguishable: the first is a typo to fix,
    # the second is a gap to chase. Checking existence is what separates them.
    known = conn.execute(
        "SELECT 1 FROM entity WHERE entity_id = ?", (entity_id,)
    ).fetchone()
    if known is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No such entity: {entity_id}. Registered entities are listed at "
            "GET /api/v1/entities.",
        )
    return AssessabilityService().report(entity_id=entity_id)
