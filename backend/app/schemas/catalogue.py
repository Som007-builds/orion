"""Catalogue response schemas.

Separate from `app/schemas/indicator.py`, which is frozen. The indicator
*results* contract cannot change now that Phase 9 is committed; the catalogue is
a different thing — a description of what exists rather than a measurement — and
adding it to the frozen module would have meant either breaking the freeze or
returning an untyped dict. A new module keeps both promises.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import OrionModel


class IndicatorCatalogueItem(OrionModel):
    """`GET /indicators` — one indicator, described rather than measured."""

    indicator_id: str
    name: str
    description: str
    family: str
    primary_dimension: str
    secondary_dimensions: list[str] = Field(default_factory=list)
    min_tier: str | None = Field(
        None, description="Minimum data tier for this indicator to be assessable"
    )
    required_fields: list[str] = Field(
        default_factory=list, description="Fields whose absence blocks the indicator"
    )
    adverse_direction: str = Field(
        ..., description="'low' when a low value is adverse, 'high' when high is"
    )
    benign_explanations: list[str] = Field(
        default_factory=list,
        description=(
            "Non-adverse explanations carried on every finding card this "
            "indicator produces. Published here so the frontend can render them "
            "without having to read the rules engine."
        ),
    )
    source: str
    status: str = Field(
        ...,
        description=(
            "'implemented' where Orion has a real detector; "
            "'reference_stub' where the detector is Dev 3's and returns no score "
            "by design. A stub is published rather than hidden so that an entity "
            "scoring 0 on a negative-space dimension is distinguishable from one "
            "where the negative-space detectors never ran."
        ),
    )
