"""Submission reads, the mapping approval gate, and quarantined rows.

The mapping approval route is the only place in the ingestion path where a human
decides something the machine will act on. It is ledgered, it is attributed, and
it is the one write on this module that a supervisor performs rather than a
pipeline. That is why the actor is required rather than defaulted.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.v1.deps import WriterDep, ingestion_service
from app.schemas.common import Page
from app.schemas.ingestion import (
    DQReportOut,
    MappingApprovalIn,
    MappingApprovalOut,
    MappingOut,
    PipelineStage,
    QuarantineRowOut,
    SubmissionDetail,
    SubmissionOut,
)
from app.services.ingestion_service import IngestionError

router = APIRouter(tags=["submissions"])


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.get(
    "/submissions",
    response_model=Page[SubmissionOut],
    summary="Registered submissions, newest first",
    description="Filter by `entity_id` to see one entity's submission history.",
)
def list_submissions(
    entity_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[SubmissionOut]:
    rows = ingestion_service().list_submissions(entity_id, limit + offset, 0)
    return Page[SubmissionOut](
        items=rows[offset : offset + limit],
        total=len(rows),
        limit=limit,
        offset=offset,
        has_more=offset + limit < len(rows),
    )


@router.get(
    "/submissions/{submission_id}",
    response_model=SubmissionDetail,
    summary="One submission with its full data-quality decomposition",
    description=(
        "Returns the submission record and its DQ report together, because an "
        "examiner asking 'what arrived' is always also asking 'what can be done "
        "with it'. `interpretation` states plainly what the submission can and "
        "cannot support — it is never a quality verdict on the entity."
    ),
    responses={404: {"description": "No such submission"}},
)
def get_submission(submission_id: str) -> SubmissionDetail:
    try:
        service = ingestion_service()
        return SubmissionDetail(
            submission=service.submission_out(submission_id),
            data_quality=service.dq_report(submission_id),
        )
    except IngestionError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/submissions/{submission_id}/dq",
    response_model=DQReportOut,
    summary="Data-quality report for one submission",
    description=(
        "Field-level completeness, validity and timeliness for one submission. The "
        "same figures appear inside `GET /submissions/{id}`; this route exists so a "
        "UI can load them on their own.\n\n"
        "`interpretation` is the part to show a submitter: it names what the quality "
        "figures mean for what can be scored, in words rather than as a number to "
        "be interpreted."
    ),
    responses={404: {"description": "No such submission"}},
)
def get_dq(submission_id: str) -> DQReportOut:
    try:
        return ingestion_service().dq_report(submission_id)
    except IngestionError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/submissions/{submission_id}/mapping",
    response_model=MappingOut,
    summary="The resolved column mapping plus the suggestions for it",
    description=(
        "Suggestions are advisory. Nothing in this response has been applied — a "
        "mapping takes effect only through `POST .../mapping/approve`, and the "
        "`approved` flag here says whether that has happened."
    ),
    responses={404: {"description": "No such submission"}},
)
def get_mapping(submission_id: str) -> MappingOut:
    try:
        return ingestion_service().mapping_for_submission(submission_id)
    except IngestionError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/submissions/{submission_id}/mapping/approve",
    response_model=MappingApprovalOut,
    summary="Approve a column mapping. Ledgered and attributed.",
    description=(
        "The human gate in the ingestion pipeline. A file with no approved mapping "
        "does not load — the pipeline waits here rather than guessing, so an "
        "unapproved submission sitting at this route is the expected state, not a "
        "stuck job."
    ),
    responses={404: {"description": "No such submission"}},
)
def approve_mapping(
    submission_id: str, body: MappingApprovalIn, writer: WriterDep
) -> MappingApprovalOut:
    from app.services.mapping_assistant import get_mapping_assistant

    service = ingestion_service()
    try:
        entry_hash = service.approve_mapping(
            submission_id=submission_id,
            column_map=body.column_map,
            severity_map=body.severity_map or None,
            status_map=body.status_map or None,
            timezone_name=body.timezone,
            actor=writer.name,
            notes=body.notes,
            profile_id=body.profile_id,
        )
    except IngestionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Read back what was stored rather than echoing the request. The profile id
    # is derived inside the service and the timestamp is the ledger's, not the
    # caller's clock — a response that reported its own request as the outcome
    # would be reporting an intention.
    stored = service.mapping_for_submission(submission_id)
    profile_id = body.profile_id or _approved_profile_id(service, submission_id)
    try:
        record = get_mapping_assistant().approved_profile(profile_id)
        approved_at = str(record["created_ts"])
        approved_by = record.get("approved_by")
    except KeyError:  # pragma: no cover - defensive
        approved_at, approved_by = "", None

    return MappingApprovalOut(
        submission_id=submission_id,
        profile_id=profile_id,
        approved=stored.approved,
        ledger_entry_hash=entry_hash,
        approved_at=approved_at,
        approved_by=approved_by,
    )


def _approved_profile_id(service, submission_id: str) -> str:
    """The profile id the service derived for this submission.

    Mirrors the service's own derivation rather than being told: if the two ever
    disagreed, the response would name a profile that does not exist, which is
    worse than a slightly redundant read.
    """
    from app.db.sqlite import get_connection

    row = get_connection().execute(
        "SELECT approved_profile_id FROM submission WHERE submission_id = ?",
        (submission_id,),
    ).fetchone()
    return str(row["approved_profile_id"]) if row and row["approved_profile_id"] else ""


@router.get(
    "/submissions/{submission_id}/quarantine",
    response_model=Page[QuarantineRowOut],
    summary="Rows this submission lost, and at which stage",
    description=(
        "Rule 4 made visible: a rejected row is never dropped silently, and this "
        "is where a quarantined row is accounted for. An examiner reconciling "
        "'you say 1.2 million alerts arrived' needs this to be able to say where "
        "they went."
    ),
    responses={404: {"description": "No such submission"}},
)
def get_quarantine(
    submission_id: str,
    stage: Annotated[PipelineStage | None, Query(description="Filter by stage")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[QuarantineRowOut]:
    rows = ingestion_service().quarantine_rows(submission_id, stage, limit + offset)
    return Page[QuarantineRowOut](
        items=rows[offset : offset + limit],
        total=len(rows),
        limit=limit,
        offset=offset,
        has_more=offset + limit < len(rows),
    )
