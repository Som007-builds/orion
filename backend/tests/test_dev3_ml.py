"""Focused Dev 3 regression tests for frozen ML and negative-space contracts."""
from __future__ import annotations

import unittest
from datetime import date

from app.ml.anomaly_engine import pooled_loo_isolation_forest
from app.ml.attributions import feature_attributions
from app.ml.negative_space import (
    ns01_silent_critical_assets,
    ns02_absent_alert_categories,
    ns03_temporal_inactivity,
    ns04_orphan_records,
    ns05_implausibly_low_activity,
    ns06_common_shock_nonresponse,
)
from app.ml.nlp_auditor import audit_notes
from backend.eval.socsim import simulate, iter_simulate
from backend.eval.streaming_features import aggregate_chunks
from app.schemas.indicator import Assessability


class Dev3AnomalyTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"target_id": "n1", "volume": 10.0, "duration": 100.0, "evidence_row_ids": ["n1-r"]},
            {"target_id": "n2", "volume": 11.0, "duration": 98.0, "evidence_row_ids": ["n2-r"]},
            {"target_id": "n3", "volume": 9.0, "duration": 103.0, "evidence_row_ids": ["n3-r"]},
            {"target_id": "outlier", "volume": 100.0, "duration": 900.0, "evidence_row_ids": ["o-r"]},
        ]

    def test_isolation_forest_is_real_deterministic_and_outlier_scores_high(self):
        first = pooled_loo_isolation_forest(self.rows, self.rows, seed=17)
        second = pooled_loo_isolation_forest(self.rows, self.rows, seed=17)
        self.assertEqual([x.model_dump() for x in first], [x.model_dump() for x in second])
        scores = {x.target_id: x.score for x in first}
        self.assertGreater(scores["outlier"], max(scores[x] for x in ("n1", "n2", "n3")))
        self.assertTrue(all(x.seed == 17 and x.explainable for x in first))

    def test_loo_excludes_target_from_reference(self):
        target = {"target_id": "n1", "volume": 1000.0, "duration": 1000.0, "evidence_row_ids": ["n1-r"]}
        with_target = pooled_loo_isolation_forest(self.rows, target, seed=3)[0]
        without_target = pooled_loo_isolation_forest(self.rows[1:], target, seed=3)[0]
        self.assertEqual(with_target.model_dump(), without_target.model_dump())

    def test_small_constant_and_missing_cohorts_are_not_false_findings(self):
        small = pooled_loo_isolation_forest(self.rows[:1], self.rows[:1], seed=1)[0]
        self.assertEqual(small.confidence, 0.0)
        constant = pooled_loo_isolation_forest(
            [{"target_id": "a", "x": 1.0}, {"target_id": "b", "x": 1.0}],
            {"target_id": "a", "x": 1.0}, seed=1,
        )[0]
        self.assertEqual(constant.attributions, [])
        missing = pooled_loo_isolation_forest(
            [{"target_id": "a", "x": 1.0}, {"target_id": "b", "x": 2.0}],
            {"target_id": "a", "evidence_row_ids": ["a"]}, seed=1,
        )[0]
        self.assertEqual(missing.confidence, 0.0)


class Dev3NegativeSpaceTests(unittest.TestCase):
    def test_ns01_suspicious_silence_and_maintenance(self):
        rows = [{"row_id": f"r{i}", "asset_id": "a", "criticality": 4, "activity": 0 if i < 4 else 2} for i in range(6)]
        self.assertEqual(len(ns01_silent_critical_assets(rows, entity_id="e")), 1)
        maintained = [dict(r, maintenance=True) for r in rows]
        self.assertEqual(ns01_silent_critical_assets(maintained, entity_id="e"), [])
        self.assertEqual(ns01_silent_critical_assets([], entity_id="e")[0].assessability, Assessability.NOT_ASSESSABLE)

    def test_ns02_absence_and_insufficient_baseline(self):
        rows = [{"row_id": str(i), "category": "auth", "period": str(i), "count": 4 if i < 3 else 0} for i in range(5)]
        self.assertEqual(len(ns02_absent_alert_categories(rows, entity_id="e")), 1)
        self.assertEqual(ns02_absent_alert_categories(rows[-1:], entity_id="e")[0].assessability, Assessability.NOT_ASSESSABLE)

    def test_ns03_hours_holiday_maintenance_and_missing(self):
        rows = [
            {"row_id": "in", "timestamp": "2026-01-05T10:00:00+00:00", "activity": 0},
            {"row_id": "maint", "timestamp": "2026-01-06T10:00:00+00:00", "activity": 0, "maintenance": True},
            {"row_id": "out", "timestamp": "2026-01-05T22:00:00+00:00", "activity": 0},
            {"row_id": "holiday", "timestamp": "2026-01-26T10:00:00+00:00", "activity": 0},
        ]
        result = ns03_temporal_inactivity(rows, entity_id="e", soc_hours={"declared_open": "08:00", "declared_close": "20:00"}, holidays=[date(2026, 1, 26)])
        self.assertEqual(result.assessability, Assessability.ASSESSABLE)
        self.assertEqual(result.value, 0.5)
        self.assertEqual(ns03_temporal_inactivity(rows, entity_id="e").assessability, Assessability.NOT_ASSESSABLE)

    def test_ns04_ns05_ns06(self):
        orphan = [{"row_id": "c", "record_type": "case", "case_id": "c"}, {"row_id": "a", "record_type": "alert", "case_id": "missing"}]
        self.assertEqual(len(ns04_orphan_records(orphan, entity_id="e")), 1)
        self.assertEqual(len(ns05_implausibly_low_activity([{"row_id": "1", "activity": 1}, {"row_id": "2", "activity": 1}], baseline=10, entity_id="e")), 1)
        shock = [{"row_id": "p", "entity_id": x, "period": "2", "activity": 10 if x != "target" else 0} for x in ("target", "p1", "p2", "p3", "p4")]
        self.assertEqual(ns06_common_shock_nonresponse(shock, entity_id="target").assessability, Assessability.ASSESSABLE)


class Dev3NLPTests(unittest.TestCase):
    def test_nlp_duplicates_templates_empty_and_malformed(self):
        notes = [
            {"row_id": "1", "entity_id": "e", "severity": "HIGH", "text": "reviewed alert and validated context"},
            {"row_id": "2", "entity_id": "e", "severity": "HIGH", "text": "reviewed alert and validated context"},
            {"row_id": "3", "entity_id": "e", "severity": "LOW", "text": "different investigation result"},
            {"row_id": "4", "entity_id": "mssp", "severity": "HIGH", "text": "shared template wording", "legitimate_template": True},
            {"row_id": "5", "entity_id": "mssp", "severity": "HIGH", "text": "shared template wording", "legitimate_template": True},
            {"row_id": "6", "entity_id": "e", "severity": "LOW", "text": ""},
        ]
        result = audit_notes(notes)
        self.assertTrue(any(x.entity_id == "e" for x in result))
        self.assertFalse(any(x.entity_id == "mssp" for x in result))
        self.assertEqual(audit_notes(notes), audit_notes(notes))

    def test_attribution_contract(self):
        attrs = feature_attributions({"volume": 10.0}, [{"volume": 1.0}, {"volume": 2.0}])
        self.assertEqual(attrs[0].feature, "volume")
        self.assertEqual(attrs[0].direction, "increases_risk")
        self.assertEqual(feature_attributions({}, [{"x": 1.0}]), [])


class Dev3SOCSimTests(unittest.TestCase):
    def test_chunk_generation_matches_small_api(self):
        whole = simulate(seed=11, entity_count=7, days=4)
        chunks = list(iter_simulate(seed=11, entity_count=7, days=4, entity_chunk_size=2))
        combined = simulate(seed=11, entity_count=7, days=4)
        self.assertEqual([x["entity_id"] for x in whole.entities], [x["entity_id"] for c in chunks for x in c.entities])
        self.assertEqual(whole.alerts, [x for c in chunks for x in c.alerts])
        self.assertEqual(whole.cases, [x for c in chunks for x in c.cases])
        self.assertEqual(whole.telemetry, [x for c in chunks for x in c.telemetry])
        self.assertEqual(whole.alerts, combined.alerts)

    def test_chunk_seed_changes_data(self):
        a = list(iter_simulate(seed=1, entity_count=3, days=2, entity_chunk_size=1))
        b = list(iter_simulate(seed=2, entity_count=3, days=2, entity_chunk_size=1))
        self.assertNotEqual([x.alerts for x in a], [x.alerts for x in b])

    def test_streaming_features_preserve_entity_counts_and_evidence(self):
        whole = simulate(seed=42, entity_count=4, days=3)
        result = aggregate_chunks(iter_simulate(seed=42, entity_count=4, days=3, entity_chunk_size=2))
        self.assertEqual([x["entity_id"] for x in result["entities"]], [x["entity_id"] for x in whole.entities])
        for feature in result["entities"]:
            eid = feature["entity_id"]
            self.assertEqual(feature["alert_count"], sum(x["entity_id"] == eid for x in whole.alerts))
            self.assertEqual({x["row_id"] for x in feature["notes"]}, {x["row_id"] for x in whole.notes if x["entity_id"] == eid})


if __name__ == "__main__":
    unittest.main()
