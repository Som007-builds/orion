"""Bounded streaming aggregation for SOCSim evaluation.

The aggregate deliberately emits ordinary mappings, matching the existing ML
contracts. Raw rows are discarded after each chunk; evidence identifiers and
entity ordering are retained in bounded per-entity state.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from .socsim import Dataset


@dataclass
class EntityAggregate:
    entity_id: str
    alert_count: int = 0
    case_count: int = 0
    severity_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    activity_total: int = 0
    activity_rows: list[dict] = field(default_factory=list)
    assets: dict[str, dict] = field(default_factory=dict)
    cases: list[dict] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)


class StreamingFeatureAggregator:
    def __init__(self) -> None:
        self.entities: list[str] = []
        self._seen: set[str] = set()
        self.by_entity: dict[str, EntityAggregate] = {}

    def consume(self, chunk: Dataset) -> None:
        for entity in chunk.entities:
            eid = str(entity["entity_id"])
            if eid not in self._seen:
                self._seen.add(eid)
                self.entities.append(eid)
                self.by_entity[eid] = EntityAggregate(eid)
        for row in chunk.assets:
            self.by_entity[str(row["entity_id"])].assets[str(row["asset_id"])] = dict(row)
        for row in chunk.alerts:
            agg = self.by_entity[str(row["entity_id"])]
            agg.alert_count += 1
            agg.severity_counts[str(row.get("severity", "UNKNOWN"))] += 1
            agg.alerts.append(dict(row))
        for row in chunk.cases:
            agg = self.by_entity[str(row["entity_id"])]
            agg.case_count += 1
            agg.cases.append(dict(row))
        for row in chunk.telemetry:
            agg = self.by_entity[str(row["entity_id"])]
            agg.activity_total += int(row.get("activity", 0) or 0)
            agg.activity_rows.append(dict(row))
        for row in chunk.notes:
            self.by_entity[str(row["entity_id"])].notes.append(dict(row))

    def finalize(self) -> dict:
        """Return deterministic entity-level feature mappings."""
        return {
            "entities": [self.features(eid) for eid in self.entities],
            "by_entity": {eid: self.features(eid) for eid in self.entities},
        }

    def features(self, entity_id: str) -> dict:
        agg = self.by_entity[entity_id]
        return {
            "entity_id": entity_id,
            "alert_count": agg.alert_count,
            "case_count": agg.case_count,
            "activity_total": agg.activity_total,
            "severity_counts": dict(sorted(agg.severity_counts.items())),
            "assets": [agg.assets[k] for k in sorted(agg.assets)],
            "telemetry": list(agg.activity_rows),
            "alerts": list(agg.alerts),
            "cases": list(agg.cases),
            "notes": list(agg.notes),
        }


def aggregate_chunks(chunks: Iterable[Dataset]) -> dict:
    aggregate = StreamingFeatureAggregator()
    for chunk in chunks:
        aggregate.consume(chunk)
    return aggregate.finalize()
