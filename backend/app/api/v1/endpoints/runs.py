"""Scoring runs and the indicator catalogue.

`POST /runs` is the heaviest route in the API: it executes the whole rules and
scoring pipeline and holds the DuckDB single-writer lock for the duration. It is
synchronous here because a caller that fires it needs the run id back to poll,
and because the lock is held either way — running it in the background would free
the request thread but not the lock, and would only add a job table to wait on.
The cost is stated in the route description rather than hidden.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from app.api.v1.deps import ActorDep, PeriodDep, WriterDep
from app.api.v1.errors import NotImplementedResponse, pending, pending_meta
from app.schemas.catalogue import IndicatorCatalogueItem
from app.schemas.common import Page
from app.schemas.indicator import Dimension
from app.schemas.ledger import RunOut, RunTriggerIn
from app.services.rules_engine import get_rules_engine
from app.services.scoring_service import ScoringError, get_scoring_service

router = APIRouter(tags=["runs"])


@router.post(
    "/runs",
    response_model=RunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Execute a scoring run",
    description=(
        "Executes the full rules and scoring pipeline synchronously. Expect "
        "seconds, not milliseconds — it reads every entity's evidence and writes "
        "`finding`, `dimension_score` and `entity_score` under the DuckDB "
        "single-writer lock. One run at a time; a concurrent call waits for the "
        "lock rather than failing.\n\n"
        "`entity_ids` filters the **response**, not the cohort. Ranking is "
        "cohort-relative, so a subset ranked against itself would produce ranks "
        "incomparable with any other run's. The full cohort is scored and stored; "
        "the subset asked for is what comes back, and it is recorded in the "
        "ledger.\n\n"
        "`indicators` narrows the detector set. Every dimension is still scored, "
        "but one with no indicators behind it comes out not-assessable rather "
        "than zero — a targeted run must not look like a full one that found "
        "nothing in the dimensions it skipped."
    ),
    responses={400: {"description": "Run could not be attempted"}},
)
def create_run(body: RunTriggerIn, period: PeriodDep, writer: WriterDep) -> RunOut:
    service = get_scoring_service()
    start = date.fromisoformat(body.period_start) if body.period_start else (
        period.start or date.today().replace(day=1)
    )
    end = date.fromisoformat(body.period_end) if body.period_end else (
        period.end or date.today()
    )
    if start > end:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"period_start {start} is after period_end {end}."
        )

    try:
        scores = service.score_period(
            start,
            end,
            persist=True,
            actor=writer.name,
            indicators=body.indicators,
            only_entity_ids=body.entity_ids,
        )
    except ScoringError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except KeyError as exc:
        # An unknown indicator id from `run_all`. Named as a 400 rather than a
        # 500: the request asked for something that does not exist.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            str(exc).strip("'"),
        ) from exc

    if not scores:  # pragma: no cover - score_period raises before this
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The run scored no entities.")
    return service.get_run(str(scores[0].run_id))


@router.get(
    "/runs",
    response_model=Page[RunOut],
    summary="Scoring runs, newest first",
    description=(
        "A run is the reproducibility unit. `policy_hash`, `code_version`, `seed` "
        "and `input_manifest_hashes` are what make a stored score interpretable "
        "later; `output_hash` is what proves two runs over the same inputs agreed. "
        "Any score shown in the UI should be traceable to one `run_id` here."
    ),
)
def list_runs(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[RunOut]:
    rows = get_scoring_service().list_runs(limit + offset, 0)
    return Page[RunOut](
        items=rows[offset : offset + limit],
        total=len(rows),
        limit=limit,
        offset=offset,
        has_more=offset + limit < len(rows),
    )


@router.get(
    "/runs/{run_id}",
    response_model=RunOut,
    summary="One run, with its code, policy and pack identities",
    description=(
        "Note what a run does not carry: a period. The `run` row records that a "
        "computation happened over a set of submission hashes, but the window it "
        "covered lives on the score rows it wrote and is derived from them. A run "
        "that wrote no scores therefore reports no period and no entity — that is "
        "a real state (it scored nothing), not a missing record."
    ),
    responses={404: {"description": "No such run"}},
)
def get_run(run_id: str) -> RunOut:
    try:
        return get_scoring_service().get_run(run_id)
    except ScoringError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get(
    "/indicators",
    response_model=list[IndicatorCatalogueItem],
    summary="The indicator catalogue — what is computed, and what is a stub",
    description=(
        "Every P0 indicator with its dimension, family and assessability "
        "requirement. Indicators with `status: stub` return no score by design; "
        "they are published rather than hidden because an entity scoring 0 on a "
        "negative-space dimension must be distinguishable from one where the "
        "negative-space detectors never ran."
    ),
)
def indicators() -> list[IndicatorCatalogueItem]:
    return [IndicatorCatalogueItem(**item) for item in get_rules_engine().catalogue()]


@router.get(
    "/dimensions",
    response_model=list[str],
    summary="The eight capability dimensions",
    description=(
        "Ordered, and the order is the reporting order, not alphabetical. The §6.1 "
        "weights behind these names are on `GET /policy-profiles/active` under "
        "`dimension_weights`; read the weight from there rather than assuming equal "
        "contribution, because the dimension scores behind a SAP are not summed "
        "evenly."
    ),
)
def dimensions() -> list[str]:
    return [d.value for d in Dimension]


@router.get(
    "/findings",
    response_model=NotImplementedResponse,
    summary="Surviving findings for an entity, with evidence",
    description=(
        "In the frozen v1 contract. Returns 503 until Phase 10 (2.10) lands the "
        "evidence service. The route is published now so the frontend can be "
        "written against the final shape; building a findings list from raw "
        "indicator signals and then rewriting it once finding cards exist would "
        "be the same list twice, with the second version contradicting the first."
    ),
    responses={503: {"model": NotImplementedResponse, "description": "Not built yet"}},
    openapi_extra=pending_meta("evidence_service", "Phase 10 (2.10)"),
)
def list_findings(actor: ActorDep) -> Any:
    return pending("GET /api/v1/findings", "evidence_service", "Phase 10 (2.10)")


@router.get(
    "/findings/{finding_id}",
    response_model=NotImplementedResponse,
    summary="One finding card",
    description=(
        "In the frozen v1 contract. Returns 503 until Phase 10 (2.10) lands the "
        "evidence service. A card is not a score: it is one surviving indicator "
        "with the rows behind it, the counterfactual for the cutpoint that did not "
        "fire, and the reason it was not suppressed. The response shape is "
        "frozen in `app/schemas/finding.py` and will not be renegotiated once the "
        "service exists."
    ),
    responses={503: {"model": NotImplementedResponse, "description": "Not built yet"}},
    openapi_extra=pending_meta("evidence_service", "Phase 10 (2.10)"),
)
def get_finding(finding_id: str, actor: ActorDep) -> Any:
    return pending("GET /api/v1/findings/{id}", "evidence_service", "Phase 10 (2.10)")


@router.get(
    "/findings/{finding_id}/evidence",
    response_model=NotImplementedResponse,
    summary="The rows behind a finding, re-derived",
    description=(
        "Re-runs the stored `evidence_query` so the rows on screen are the rows "
        "the indicator saw, not a snapshot that can drift from them."
    ),
    responses={503: {"model": NotImplementedResponse, "description": "Not built yet"}},
    openapi_extra=pending_meta("evidence_service", "Phase 10 (2.10)"),
)
def get_finding_evidence(finding_id: str, actor: ActorDep) -> Any:
    return pending(
        "GET /api/v1/findings/{id}/evidence", "evidence_service", "Phase 10 (2.10)"
    )


@router.get(
    "/findings/{finding_id}/counterfactual",
    response_model=NotImplementedResponse,
    summary="What the peer cohort looks like on the same measure",
    description=(
        "A comparison against peers, not a verdict. States what the cohort's "
        "distribution is on this measure so a supervisor can see whether the "
        "entity is an outlier or simply in a differently-shaped population."
    ),
    responses={503: {"model": NotImplementedResponse, "description": "Not built yet"}},
    openapi_extra=pending_meta("evidence_service", "Phase 10 (2.10)"),
)
def get_counterfactual(finding_id: str, actor: ActorDep) -> Any:
    return pending(
        "GET /api/v1/findings/{id}/counterfactual",
        "evidence_service",
        "Phase 10 (2.10)",
    )
