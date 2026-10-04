"""Small persisted-path evaluator using the repository's real test fixture.

This intentionally reuses the PackLifecycleFixture instead of creating a second
database schema. It is a harness for the real RulesEngine/ScoringService path.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.db.duckdb_client import get_duckdb
from app.db.sqlite import get_connection
from app.services.scoring_service import get_scoring_service


PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 1, 31)


def _finding_rows(entity_id: str) -> list[dict[str, Any]]:
    rows = get_connection().execute(
            "SELECT indicator_id, value, confidence, assessability, evidence_query "
            "FROM finding WHERE entity_id = ? ORDER BY indicator_id",
            (entity_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def run_fixture_case(*, entity_id: str = "ent_pk_fast", family: str = "rapid_closure", dose: float = 1.0) -> dict:
    """Run one deterministic mutation through persisted scoring.

    The fixture is initialized by the existing backend test class. Unsupported
    families are returned as NOT_ASSESSABLE rather than being simulated.
    """
    from backend.tests.test_packs import PackLifecycleFixture
    PackLifecycleFixture.setUpClass()
    supported = {"rapid_closure": "EG-01"}
    if family not in supported:
        return {"injection": family, "owner": "Dev2 RulesEngine", "dose": dose,
                "classification": "NOT_ASSESSABLE", "indicator_ids": [],
                "explanation": "This fixture does not contain the source records required for this injection family."}
    with get_duckdb().writer() as conn:
        conn.execute("UPDATE case_record SET closed_at = created_at WHERE entity_id = ? AND case_id LIKE 'pk_ca_%'", [entity_id])
    scores = get_scoring_service().score_period(PERIOD_START, PERIOD_END, persist=True, actor="persisted_dev3_eval")
    findings = _finding_rows(entity_id)
    own = [x for x in findings if x["indicator_id"] == supported[family]]
    detected = any(x["value"] is not None for x in own)
    return {"injection": family, "owner": "Dev2 RulesEngine", "dose": dose,
            "truth_count": 1, "detected_count": int(detected), "detection_rate": float(detected),
            "indicator_ids": [x["indicator_id"] for x in own], "evidence_ids": [],
            "confidence": max((x["confidence"] or 0 for x in own), default=None),
            "assessability": [x["assessability"] for x in own],
            "classification": "DETECTED" if detected else "NOT_DETECTED",
            "score_count": len(scores), "findings": findings}
