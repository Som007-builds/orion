"""Typed error responses and the handlers that produce them.

Every failure a client can provoke comes back as the same JSON shape, so the
frontend writes one error renderer instead of one per status code. The shape
carries a `hint` because the most common client-side problem with this API is
procedural: not knowing that a mapping needs a human before the load can proceed,
or that a score is absent because the dimension is not assessable rather than
because it is zero.

Nothing here decides *what* failed. Services raise; this module only translates.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.common import OrionModel

log = logging.getLogger("orion.api")


class ErrorDetail(OrionModel):
    """One reason, in the vocabulary of whoever has to act on it."""

    code: str = Field(
        ...,
        description=(
            "Stable machine-readable code. Clients branch on this, not on the "
            "message: message text is for humans and may be reworded."
        ),
    )
    message: str
    field: str | None = Field(None, description="Request field, when the fault is in one")
    hint: str | None = Field(
        None,
        description="What the caller can do about it. Omitted rather than "
        "filler text when there is nothing actionable to say.",
    )


class ErrorResponse(OrionModel):
    """The single error envelope for the whole API."""

    error: str = Field(..., description="Error family, e.g. 'not_found'")
    message: str = Field(..., description="Human-readable summary")
    details: list[ErrorDetail] = Field(default_factory=list)
    request_id: str | None = None


class NotImplementedResponse(OrionModel):
    """Body returned by a route whose backing service is not built yet.

    These routes are registered rather than omitted so that the OpenAPI document
    is the complete v1 surface: the frontend can be written against the whole API
    from day one and build only the parts that answer. The alternative —
    publishing only the built half — means the contract changes under the
    frontend halfway through the project, which is the failure mode a contract
    freeze exists to prevent.

    `service` names what is missing and `phase` names when it lands, so nobody has
    to guess whether a 503 is a bug or a scheduled gap.
    """

    endpoint: str
    service: str = Field(..., description="Service module not yet written")
    phase: str = Field(..., description="Plan phase that delivers it")
    reason: str = "This endpoint is part of the frozen v1 contract but its backing "
    "service has not been built yet. It is listed here so the contract is complete, "
    "not to suggest the capability exists."
    status: int = 503


#: Service error classes are mapped here rather than being caught by name, so a
#: service gaining a new exception type forces a decision about what a client sees
#: instead of silently surfacing as a 500.
_STATUS_BY_ERROR: dict[type[Exception], tuple[int, str]] = {
    FileNotFoundError: (404, "not_found"),
    KeyError: (404, "not_found"),
}


def _classify(exc: Exception) -> tuple[int, str]:
    for exc_type, mapped in _STATUS_BY_ERROR.items():
        if isinstance(exc, exc_type):
            return mapped
    # Service modules each define their own error class. Rather than importing
    # every one of them here (which would make this module a second registry of
    # what exists), match on the naming convention they all follow: `<Thing>Error`.
    # An unknown `*Error` is a bad request about data the caller supplied; it is
    # not a server fault, and reporting it as 500 would train operators to ignore
    # the 5xx column.
    if type(exc).__name__.endswith("Error"):
        return 400, "bad_request"
    return 500, "internal_error"


def install_handlers(app: FastAPI) -> None:
    """Register the exception handlers on the app."""

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            400: "bad_request",
            401: "unauthenticated",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            409: "conflict",
            413: "payload_too_large",
            422: "unprocessable",
            429: "rate_limited",
            503: "unavailable",
        }.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error=code,
                message=str(exc.detail),
                request_id=getattr(request.state, "request_id", None),
            ).model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Surfaced as 422 rather than the default because a validation failure is
        # the client's input being wrong, not the request being unprocessable in
        # some deeper sense — and the field list is the useful part.
        details = [
            ErrorDetail(
                code="validation_error",
                message=str(err.get("msg", "invalid value")),
                field=".".join(str(p) for p in err.get("loc", ())) or None,
                hint=(
                    "Response models forbid unknown fields; a field Orion does not "
                    "define will be rejected here rather than ignored."
                    if err.get("type") == "extra_forbidden"
                    else None
                ),
            )
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error="validation_error",
                message=f"{len(details)} field(s) rejected.",
                details=details,
                request_id=getattr(request.state, "request_id", None),
            ).model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        code, family = _classify(exc)
        log.exception(
            "unhandled error on %s %s: %s", request.method, request.url.path, exc
        )
        # A 500 body never carries the exception text. It is echoed into the log
        # instead: the message may contain a file path, a fragment of submitted
        # data, or a secret handle, and this response goes to a browser.
        return JSONResponse(
            status_code=code,
            content=ErrorResponse(
                error=family,
                message=(
                    str(exc)
                    if code < 500
                    else "The request could not be completed. The failure has been "
                    "logged with a request id."
                ),
                details=[
                    ErrorDetail(
                        code=family,
                        message=str(exc),
                        hint=(
                            "Check the submission's mapping and declared period."
                            if code == 400
                            else None
                        ),
                    )
                ] if code < 500 else [],
                request_id=getattr(request.state, "request_id", None),
            ).model_dump(mode="json"),
        )


def pending_meta(service: str, phase: str) -> dict[str, Any]:
    """The `openapi_extra` marker for a route whose service is not built yet.

    A machine-readable extension rather than a sentence a client has to parse.
    `GET /api/v1` and any generated client read `x-orion.status` to tell a route
    that will work from one that will answer 503, and the phase string travels
    with it so a client can show *when* rather than leaving someone to file a
    question.
    """
    return {"x-orion": {"status": "pending", "service": service, "phase": phase}}


def is_pending(route: Any) -> bool:
    """Whether a route was registered with `pending_meta`."""
    extra = getattr(route, "openapi_extra", None) or {}
    return (extra.get("x-orion") or {}).get("status") == "pending"


def pending(endpoint: str, service: str, phase: str) -> JSONResponse:
    """A typed 503 for a contract endpoint whose service is not built yet."""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=NotImplementedResponse(
            endpoint=endpoint, service=service, phase=phase
        ).model_dump(mode="json"),
    )


def detail_of(exc: Exception) -> dict[str, Any]:
    """Normalise a service exception into the error envelope."""
    code, family = _classify(exc)
    return ErrorResponse(
        error=family, message=str(exc), details=[ErrorDetail(code=family, message=str(exc))]
    ).model_dump(mode="json")
