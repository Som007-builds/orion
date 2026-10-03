"""Request-scoped dependencies: actor identity, role gate, pagination, periods.

**Authentication is not implemented here.** There is no identity provider in the
offline deployment and none was built in Phase 3, so this module does the part
that can be done correctly without one — it establishes *where the actor string
comes from* and makes it impossible for a write to reach the ledger without one.

The gate below is deliberately asymmetric:

  * in development it is permissive and says so in every response it gates;
  * outside development a write without an explicit actor is refused.

That mirrors `resolve_secret()`'s fail-closed rule in `pseudonymisation.py`. A
supervisory tool whose audit trail records `unknown` as the actor for a mapping
approval is worse than one that refuses the approval, because the refusal is
visible and the bad audit entry is not. What this does *not* do is authenticate —
it labels, it does not verify. Deployments that need real identity must replace
`resolve_actor` with something that checks a credential; the seam is here so that
is a single function to change.

Dev 3 owns the identity decision. Until then this is a labelled gap, not a
silent one.

**No `from __future__ import annotations` in this module.** FastAPI evaluates
these annotations at runtime to work out what to inject, and under postponed
evaluation a bare `Depends()` cannot see the class it is supposed to depend on.
The `Depends(Class)` calls below are explicit for the same reason.
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Iterator

from fastapi import Depends, Header, HTTPException, Query, status

from app.config import is_development
from app.schemas.common import Role
from app.services.policy_profile import PolicyProfileService
from app.services.scoring_service import ScoringService, get_scoring_service

log = logging.getLogger("orion.api")

#: Actor recorded when a dev request carries none. Deliberately obviously fake so
#: it is greppable in an audit export rather than looking like a real name.
DEV_ACTOR = "dev:unauthenticated"

#: Roles that may change stored state. Reads are open to every role — including
#: Auditor, who must never write — but a write needs one of these.
_WRITE_ROLES = frozenset(
    {Role.SUPERVISOR, Role.EXAMINER, Role.ADMINISTRATOR, Role.DATA_CUSTODIAN}
)

#: Roles that may activate a policy profile. Narrower than write access: changing
#: what "high risk" means retroactively reinterprets every score already stored,
#: so it is a supervisor-or-admin act and nothing else.
_POLICY_ROLES = frozenset({Role.SUPERVISOR, Role.ADMINISTRATOR})


@dataclass(frozen=True)
class Actor:
    """Who is making this request, as far as the application knows."""

    name: str
    role: Role
    authenticated: bool

    def require(self, allowed: frozenset[Role], action: str) -> None:
        if self.role not in allowed:
            # Named what was required and what was held. A 403 that says only
            # "forbidden" sends an operator to file a support ticket.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{self.role.value}' may not {action}. "
                    f"Requires one of: {', '.join(sorted(r.value for r in allowed))}."
                ),
            )


def resolve_actor(
    x_actor: Annotated[str | None, Header(alias="X-Actor")] = None,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
) -> Actor:
    """Establish the actor for a request.

    Header-named rather than token-verified, because there is nothing to verify
    against. Both headers are recorded in the ledger on a write, so an operator
    reviewing who approved a mapping sees what was claimed at the time.
    """
    role = Role.EXAMINER
    if x_role:
        try:
            role = Role(x_role)
        except ValueError:
            matched = False
            for r in Role:
                if (
                    r.value.lower() == x_role.lower()
                    or r.name.lower() == x_role.lower()
                    or r.value.lower().replace(" ", "_") == x_role.lower()
                ):
                    role = r
                    matched = True
                    break
            if not matched:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Unknown role '{x_role}'. Known roles: "
                        f"{', '.join(r.value for r in Role)}."
                    ),
                ) from None

    if x_actor:
        return Actor(name=x_actor.strip()[:128] or DEV_ACTOR, role=role, authenticated=True)

    if is_development():
        return Actor(name=DEV_ACTOR, role=role, authenticated=False)

    # Fail closed on an unattributable write. Reads still pass: an auditor who has
    # not yet been given an identity should still be able to look, and looking
    # changes nothing.
    return Actor(name="", role=role, authenticated=False)


ActorDep = Annotated[Actor, Depends(resolve_actor)]


def require_write(actor: ActorDep) -> Actor:
    """Gate for any state-changing route."""
    if not actor.name:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "This write cannot be attributed to anyone. Send an X-Actor "
                "header. A state change with no actor is refused rather than "
                "recorded as 'unknown', because an unattributable entry in a "
                "supervisory audit trail cannot be relied on later."
            ),
        )
    actor.require(_WRITE_ROLES, action="modify stored state")
    if not actor.authenticated:
        log.warning(
            "write attributed to %s with no credential — identity is asserted, "
            "not verified", actor.name
        )
    return actor


WriterDep = Annotated[Actor, Depends(require_write)]


def require_policy_admin(actor: ActorDep) -> Actor:
    """Gate for activating a policy profile."""
    if not actor.name:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Activating a policy profile must be attributed. Send X-Actor.",
        )
    actor.require(_POLICY_ROLES, action="activate a policy profile")
    return actor


PolicyAdminDep = Annotated[Actor, Depends(require_policy_admin)]


# ------------------------------------------------------------------ period --
class PeriodQuery:
    """Optional period filter.

    Absent means "the most recent window that has data", not "the current
    calendar month". A tool that silently reported March when asked in October
    would be reporting the wrong quarter without saying so.
    """

    def __init__(
        self,
        period_start: Annotated[
            date | None, Query(description="Inclusive start, YYYY-MM-DD")
        ] = None,
        period_end: Annotated[date | None, Query(description="Inclusive end, YYYY-MM-DD")] = None,
    ) -> None:
        if period_start and period_end and period_start > period_end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"period_start {period_start} is after period_end {period_end}.",
            )
        self.start = period_start
        self.end = period_end


PeriodDep = Annotated[PeriodQuery, Depends(PeriodQuery)]


# ---------------------------------------------------------------- services --
def scoring(policy: str | None = None) -> ScoringService:
    """A scoring service bound to a named policy profile, or the shared one.

    Naming a profile gets exactly that profile — never the active one — because a
    run recorded under a policy hash must have been computed under that policy.
    """
    if policy:
        return get_scoring_service(policy=PolicyProfileService().get(policy))
    return get_scoring_service()


def ingestion_service():
    from app.services.ingestion_service import IngestionService

    return IngestionService()


def db_connection() -> Iterator[Any]:
    """Per-request SQLite handle, closed afterwards.

    Request handlers only ever read through this. Writes go through a service,
    which takes the transaction — an endpoint that wrote directly would bypass the
    ledger, and the ledger is the point of the tool.
    """
    from app.db.sqlite import get_connection

    yield get_connection()


ConnectionDep = Annotated[Any, Depends(db_connection)]
