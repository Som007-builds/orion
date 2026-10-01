"""Shared schema primitives.

`extra="forbid"` everywhere: the frontend never invents fields (build-responsibility
rule 2), so a stray field must fail loudly at the boundary rather than be
silently absorbed.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class OrionModel(BaseModel):
    """Base for every request/response model."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Page(OrionModel, Generic[T]):
    """Paginated envelope. Every list endpoint returns one of these."""

    items: list[T]
    total: int = Field(..., ge=0)
    limit: int = Field(..., ge=1)
    offset: int = Field(..., ge=0)
    has_more: bool


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class CaseState(str, Enum):
    """Canonical case state machine (plan §3.1).

    NEW -> OPEN -> IN_PROGRESS -> (ESCALATED) -> RESOLVED -> CLOSED, plus REOPENED.
    """

    NEW = "NEW"
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"


class Disposition(str, Enum):
    TRUE_POSITIVE = "TRUE_POSITIVE"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    BENIGN_ANOMALY = "BENIGN_ANOMALY"
    SUPPRESSED = "SUPPRESSED"


class ActorType(str, Enum):
    HUMAN = "human"
    AUTOMATION = "automation"
    UNKNOWN = "unknown"


class SocModel(str, Enum):
    IN_HOUSE = "in-house"
    MSSP = "MSSP"
    HYBRID = "hybrid"


class CoverageType(str, Enum):
    H24x7 = "24x7"
    EXTENDED = "extended"
    BUSINESS = "business"


class SizeTier(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class AttentionTier(str, Enum):
    """SAP tiers (plan §6.3).

    `NOT_ASSESSABLE` is a separate state, not a low tier. An entity that cannot
    be assessed must not be ranked as if it were low risk.
    """

    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"
    NOT_ASSESSABLE = "not_assessable"


class DataTier(str, Enum):
    """Submission completeness tiers (plan §3.15)."""

    A = "A"  # alert + case core
    B = "B"  # workflow + governance
    C = "C"  # coverage + expectations


class Role(str, Enum):
    """RBAC roles (plan §8.3)."""

    SUPERVISOR = "Supervisor"
    EXAMINER = "Examiner"
    AUDITOR = "Auditor"
    ADMINISTRATOR = "Administrator"
    DATA_CUSTODIAN = "Data Custodian"


class PeriodOut(OrionModel):
    start: date
    end: date


class TimestampRange(OrionModel):
    start: datetime
    end: datetime


class RankInterval(OrionModel):
    """Point ranks imply false precision; intervals do not (plan §6.3)."""

    rank: int = Field(..., ge=1)
    low: int = Field(..., ge=1)
    high: int = Field(..., ge=1)
    method: str = Field(
        ..., description="Weight perturbation (Dirichlet draws) + bootstrap over peers"
    )