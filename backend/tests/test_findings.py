"""Read path of the finding card: hand-built fixture, service + endpoints.

The full pipeline (ingest -> score -> materialise) is exercised by the phase
10 smoke and by the 2.18 cohort fixture suite; what belongs here permanently is
the contract that the evidence service *reads* findings back correctly — every
card field populated, the three "why" answers distinguished, evidence
re-derived from the stored query, and the routes wired to the typed envelope.

The fixture is built directly in the tables the scorer writes, so the test
cannot drift from reality by constructing `IndicatorResult` objects the real
pipeline never produced. Rows here mirror `ScoringService._persist_indicator_results`.

Run: `python -m unittest discover tests`
"""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from app.db.duckdb_client import get_duckdb
from app.db.sqlite import get_connection, init_db
from app.main import create_app
from app.services.evidence_service import FindingNotFound, get_evidence_service
from app.services.rules_engine import _reproduce_evidence
from tests import cleanup

NOW = datetime.now(timezone.utc).isoformat()
PS, PE = "2026-01-01", "2026-01-31"

# The stored query reads the `entity` table (a table that exists in the SQLite
# store) rather than the evidence lake: the reproduction guarantee is about the
# execution path, not about which store the query targets. `_run_evidence`
# requires the sqlite id column to be named `record_key`.
EVIDENCE_QUERY = json.dumps(
    {
        "engine": "sqlite",
        "sql": (
            "SELECT entity_id AS record_key FROM entity "
            "WHERE size_tier = 'large' ORDER BY entity_id"
        ),
        "params": [],
        "table": "entity",
    }
)


def tearDownModule() -> None:  # noqa: N802
    cleanup()


class FindingFixture(unittest.TestCase):
    """Entities, a run, findings, status rows and evidence for one run."""

    @classmethod
    def setUpClass(cls) -> None:
        init_db()
        get_duckdb().init()
        conn = get_connection()
        if conn.execute(
            "SELECT 1 FROM run WHERE run_id = 'run_10'"
        ).fetchone():
            # Every subclass re-runs setUpClass against the shared suite DB;
            # the fixture is built once.
            return
        conn.execute(
            "INSERT OR IGNORE INTO sector_ref (sector_ref, display_name) "
            "VALUES ('BANK', 'Banking')"
        )
        for eid, name in (
            ("ent_a", "Alpha Bank"),
            ("ent_quiet", "Quiet Corp"),
            ("ent_p2", "Peer Two"),
        ):
            conn.execute(
                "INSERT OR IGNORE INTO entity (entity_id, name, sector, soc_model, "
                "coverage_type, declared_open, declared_close, size_tier, created_at) "
                "VALUES (?,?,'BANK','in-house','24x7','08:00','20:00','large',?)",
                (eid, name, NOW),
            )
        conn.execute(
            "INSERT INTO run (run_id, created_ts, status, input_manifest_hashes, "
            "pack_version, policy_hash, policy_profile_id, code_version, seed) "
            "VALUES ('run_10', ?, 'complete', ?, 'pack-10', 'pol-hash-1', "
            "'nccipc_default', 'test-1.0', 42)",
            (NOW, json.dumps(["sha-1", "sha-2"])),
        )
        conn.execute(
            "INSERT INTO entity_score (entity_id, period_start, period_end, egi, "
            "nsi, dts, sap, assessability, sap_rank, sap_rank_low, sap_rank_high, "
            "sap_tier, run_id) VALUES ('ent_a', ?, ?, 0.6, NULL, 0.8, 0.7, "
            "'assessable', 1, 0, 0, 'T1', 'run_10')",
            (PS, PE),
        )
        cls._finding(
            conn,
            "F-01",
            indicator="EG-01",
            family="rapid_thin_closure",
            dimension="INV",
            secondary='["IR","OD"]',
            value=120.0,
            units="seconds",
            effect=8.15,
            confidence=0.9,
            peer_median=25500.0,
            peer_mad=1800.0,
            peer_percentile=4.5,
            n_peers=9,
            notes="Engine caveat for the finding itself.",
            evidence_query=EVIDENCE_QUERY,
        )
        # Same-dimension, same-direction signal: corroboration, not independence.
        cls._finding(
            conn,
            "F-02",
            indicator="EG-08",
            family="sla_breach",
            dimension="INV",
            secondary="[]",
            value=0.0667,
            units=None,
            effect=2.5,
            confidence=0.7,
            peer_median=0.0,
            peer_mad=0.0,
            peer_percentile=98.0,
            n_peers=9,
            notes=None,
            evidence_query=None,
        )
        # A lead is not a finding: it must never surface in the list.
        cls._finding(
            conn,
            "F-LEAD",
            indicator="EG-02",
            family="rapid_thin_closure",
            dimension="INV",
            secondary="[]",
            value=1.0,
            units=None,
            effect=4.1,
            confidence=0.3,
            peer_median=0.0,
            peer_mad=0.0,
            peer_percentile=None,
            n_peers=0,
            notes=None,
            evidence_query=None,
            is_lead=True,
        )

        for ordinal, row_id in enumerate(("ent_a", "ent_p2", "ent_quiet")):
            conn.execute(
                "INSERT OR IGNORE INTO finding_evidence "
                "(finding_id, table_name, row_id, ordinal) VALUES (?, 'entity', ?, ?)",
                ("F-01", row_id, ordinal),
            )

        status = [
            # (indicator, value, effect, raised, suppressed, note, ncr, stub,
            #  missing_fields)
            ("EG-01", 120.0, 8.15, 1, None, None, None, 0, []),   # the finding itself
            ("EG-02", 1.0, None, 0, None,
             "The peer cohort shares a single value, so no robust z can be formed.",
             None, 0, []),
            ("EG-09", 0.0, 0.0, 0, None, None, None, 0, []),      # measured and quiet
            ("EG-08", 0.0667, 2.5, 1, None, None, None, 0, []),   # its own finding
            ("EG-06", None, None, 0, None, None,
             "needs escalation evidence not present in this submission", 0,
             ["escalation.csv"]),
            ("NS-01", None, None, 0, None, None,
             "REFERENCE STUB: Silent critical assets is owned by Developer 3",
             1, []),
        ]
        for indicator, value, effect, raised, suppressed, note, ncr, stub, missing in status:
            conn.execute(
                "INSERT INTO indicator_status (run_id, entity_id, indicator_id, "
                "period_start, period_end, value, value_units, effect_size, n, "
                "confidence, assessability, missing_fields, required_tier, raised, "
                "suppressed_reason, note, not_computable_reason, is_stub, created_ts) "
                "VALUES ('run_10', 'ent_a', ?, ?, ?, ?, ?, ?, 60, 0.9, 1.0, ?, "
                "'A', ?, ?, ?, ?, ?, ?)",
                (
                    indicator, PS, PE,
                    value, None if value is None else "seconds",
                    effect, json.dumps(missing), raised, suppressed, note,
                    ncr, stub, NOW,
                ),
            )
        conn.commit()

    @classmethod
    def _finding(cls, conn, fid, *, indicator, family, dimension, secondary,
                 value, units, effect, confidence, peer_median, peer_mad,
                 peer_percentile, n_peers, notes, evidence_query, is_lead=False):
        conn.execute(
            "INSERT INTO finding (finding_id, run_id, entity_id, indicator_id, "
            "period_start, period_end, value, value_units, peer_median, peer_mad, "
            "peer_percentile, n_peers, baseline_method, baseline_cohort, "
            "self_median, self_mad, self_periods, effect_size, n, confidence, "
            "conf_n_term, conf_assessability_term, conf_data_trust_term, "
            "evidence_query, benign_explanations, required_fields, missing_fields, "
            "assessability, family, primary_dimension, secondary_dimensions, source, "
            "is_low_confidence_lead, actor_type_inferred, notes, created_ts) "
            "VALUES (?, 'run_10', 'ent_a', ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "'loo_median_mad', 'BANK|large|in-house|24x7', NULL, NULL, 0, ?, 60, "
            "?, 1.0, 1.0, 0.9, ?, '[]', '[]', '[]', 1.0, ?, ?, ?, 'rules_engine', "
            "?, 1, ?, ?)",
            (
                fid, indicator, PS, PE, value, units, peer_median, peer_mad,
                peer_percentile, n_peers, effect, confidence, evidence_query,
                family, dimension, secondary, 1 if is_lead else 0, notes, NOW,
            ),
        )

    def test_findings_not_found_raises(self) -> None:
        with self.assertRaises(FindingNotFound):
            get_evidence_service().get_finding("F-NOPE")


class FindingsList(FindingFixture):
    def test_list_excludes_leads_and_orders_most_adverse_first(self) -> None:
        items = get_evidence_service().list_findings(entity_id="ent_a")
        ids = [i.finding_id for i in items]
        self.assertEqual(["F-01", "F-02"], ids)  # F-LEAD is a lead, not a finding
        self.assertLess(0.0, items[0].effect_size)  # type: ignore[operator]
        self.assertGreater(items[0].effect_size, items[1].effect_size)  # type: ignore[operator]
        self.assertEqual("Alpha Bank", items[0].entity_name)
        self.assertEqual("T1", items[0].sap_tier.value)  # type: ignore[union-attr]

    def test_list_period_window_filters(self) -> None:
        items = get_evidence_service().list_findings(
            entity_id="ent_a",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
        )
        self.assertEqual(["F-01", "F-02"], [i.finding_id for i in items])
        # Non-overlapping window: no findings.
        items = get_evidence_service().list_findings(
            entity_id="ent_a",
            period_start=date(2027, 1, 1),
            period_end=date(2027, 1, 31),
        )
        self.assertEqual([], items)


class FindingCard(FindingFixture):
    def test_card_populates_every_field(self) -> None:
        card = get_evidence_service().get_finding("F-01")
        self.assertEqual("EG-01", card.indicator_id)
        self.assertEqual("Alpha Bank", card.entity_name)
        self.assertEqual(120.0, card.value)
        self.assertEqual(8.15, card.effect_size)
        self.assertEqual("loo_median_mad", card.baseline.method)
        self.assertEqual(9, card.baseline.n_peers)
        self.assertEqual(25500.0, card.baseline.peer_median)
        self.assertTrue(card.confidence.breakdown is not None)
        # Why-flagged is a hypothesis, never a verdict word.
        self.assertIn("reason to look", card.why_flagged.statement)
        self.assertNotIn("verdict", card.why_flagged.statement.lower())
        self.assertIn("non-compliance", card.why_flagged.statement)
        # Lineage carries the run's identity.
        self.assertEqual("run_10", card.lineage.run_id)
        self.assertEqual(["sha-1", "sha-2"], card.lineage.submission_manifest_hashes)
        self.assertEqual("pol-hash-1", card.lineage.policy_hash)
        self.assertEqual("nccipc_default", card.lineage.policy_profile_id)
        self.assertEqual(42, card.lineage.seed)
        # Benign explanations and caveats come from the stored row.
        self.assertIsInstance(card.benign_explanations, list)
        self.assertIn("Engine caveat for the finding itself.", card.caveats)

    def test_card_counterfactual_has_a_conservative_threshold(self) -> None:
        card = get_evidence_service().get_finding("F-01")
        cf = card.counterfactual
        # Two-sided critical value at q=0.05 (the nccipc_default / policy default).
        self.assertAlmostEqual(1.96, cf.would_clear_at_effect_size, places=2)
        self.assertIsNotNone(cf.would_clear_at_value)
        self.assertEqual(120.0, cf.current_value)
        self.assertEqual(8.15, cf.current_effect_size)
        self.assertTrue(cf.explanation)

    def test_why_not_flagged_distinguishes_all_five_cases(self) -> None:
        why = get_evidence_service().get_finding("F-01").why_not_flagged
        run = {s.indicator_id: s for s in why.indicators_run}
        # Measured and quiet: no note.
        self.assertTrue(run["EG-09"].raised is False and run["EG-09"].note is None)
        # Measured but no comparison could be formed: the caveat is labelled.
        self.assertTrue(run["EG-02"].note.startswith(
            "Measured but not comparable:"))
        # Its own raised sibling is listed as raised.
        self.assertTrue(run["EG-08"].raised)
        # Cannot compute at all: reasons, never a gap.
        nc = {n.indicator_id: n for n in why.indicators_not_computable}
        self.assertIn("EG-06", nc)
        self.assertTrue(nc["EG-06"].missing_fields)
        self.assertIn("REFERENCE STUB", nc["NS-01"].reason)
        self.assertIn("1 other indicator(s) raised", why.statement)

    def test_corroborating_signals_same_dimension(self) -> None:
        signals = get_evidence_service().get_finding("F-01").corroborating_signals
        self.assertEqual(1, len(signals))
        self.assertEqual("EG-08", signals[0].indicator_id)
        self.assertEqual("same_dimension", signals[0].relationship)
        # The lead is never corroboration.
        self.assertNotIn("EG-02", {s.indicator_id for s in signals})


class EvidenceRows(FindingFixture):
    def test_evidence_re_derives_the_persisted_row_ids(self) -> None:
        evidence = get_evidence_service().get_evidence("F-01")
        self.assertIsNotNone(evidence.stored_query)
        self.assertEqual(["ent_a", "ent_p2", "ent_quiet"],
                         [r.row_id for r in evidence.rows])
        self.assertEqual("entity", evidence.rows[0].table_name)
        self.assertEqual(3, evidence.row_count)
        self.assertEqual(64, len(evidence.row_id_hash))

        persisted = [str(r["row_id"]) for r in get_connection().execute(
            "SELECT row_id FROM finding_evidence WHERE finding_id = 'F-01' "
            "ORDER BY ordinal")]
        self.assertEqual(persisted, [r.row_id for r in evidence.rows])

    def test_reproduce_matches_evidence_rows_byte_for_byte(self) -> None:
        # The identical execution path the CLI uses (scripts/reproduce_finding.py).
        reproduced = _reproduce_evidence(EVIDENCE_QUERY)
        self.assertEqual(["ent_a", "ent_p2", "ent_quiet"], reproduced)

    def test_a_finding_without_a_stored_query_reports_none(self) -> None:
        evidence = get_evidence_service().get_evidence("F-02")
        self.assertIsNone(evidence.stored_query)
        self.assertEqual([], evidence.rows)
        self.assertEqual(0, evidence.row_count)
        self.assertIsNone(evidence.row_id_hash)


class CounterfactualEndpoint(FindingFixture):
    def test_counterfactual_carries_baseline_and_omits_peer_sensitivity(self) -> None:
        cf = get_evidence_service().get_counterfactual("F-01")
        self.assertEqual("F-01", cf.finding_id)
        self.assertEqual("EG-01", cf.indicator_id)
        self.assertEqual(25500.0, cf.baseline.median)
        self.assertEqual(1800.0, cf.baseline.mad)
        self.assertEqual(9, cf.baseline.n_peers)
        self.assertEqual("BANK|large|in-house|24x7", cf.baseline.cohort)
        # Peer values are not stored, so sensitivity is omitted rather than
        # approximated — an empty list plus the counterfactual's explanation.
        self.assertEqual([], cf.peer_sensitivity)
        self.assertTrue(cf.counterfactual.explanation)


class FindingsEndpoints(FindingFixture):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # Lifespan context, exactly as the API suite does it.
        cls._ctx = TestClient(create_app())
        cls.client = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._ctx.__exit__(None, None, None)

    def test_list_is_a_paginated_envelope(self) -> None:
        body = self.client.get("/api/v1/findings?entity_id=ent_a").json()
        self.assertIn("items", body)
        self.assertEqual(2, body["total"])
        self.assertEqual(["F-01", "F-02"], [i["finding_id"] for i in body["items"]])
        self.assertFalse(body["has_more"])

    def test_card_route_returns_every_field(self) -> None:
        body = self.client.get("/api/v1/findings/F-01").json()
        self.assertEqual("run_10", body["lineage"]["run_id"])
        self.assertTrue(body["why_flagged"]["statement"])
        self.assertTrue(body["why_not_flagged"]["statement"])
        self.assertIsNotNone(body["counterfactual"]["would_clear_at_effect_size"])

    def test_evidence_and_counterfactual_routes(self) -> None:
        ev = self.client.get("/api/v1/findings/F-01/evidence").json()
        self.assertEqual(3, ev["row_count"])
        cf = self.client.get("/api/v1/findings/F-01/counterfactual").json()
        self.assertEqual([], cf["peer_sensitivity"])

    def test_unknown_finding_is_a_typed_404(self) -> None:
        body = self.client.get("/api/v1/findings/F-NOPE")
        self.assertEqual(404, body.status_code)
        self.assertEqual("not_found", body.json()["error"])