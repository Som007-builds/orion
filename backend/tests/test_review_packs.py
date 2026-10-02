"""Review packs (Phase 11): sampling properties, persistence, verdicts.

What belongs here permanently is the *sampling contract* — the properties that
make a pack usable as evidence about the population rather than a ranked list:

* known inclusion probabilities (π), recorded per item, with the PPS slice never
  selecting a zero-risk case;
* stratified controls drawn from the complement of the targeted slice, whose
  stated draw counts match their recorded π;
* HT estimate bounds that actually bound (`ci_low <= estimate <= ci_high`);
* reproducibility: the same seed and inputs produce the same pack and the same
  content hash;
* the ledger trail — `review_pack_created` on generation, `verdict_recorded`
  on a supervisor's conclusion — extending the chain without a new break point
  (the shared suite DB carries test_api's deliberately forged break, so global
  validity is never asserted here);
* the routes wired to the typed envelope, with `/export` still honestly 503.

The fixture is hand-built in the tables the scorer writes (mirroring
`ScoringService._persist_indicator_results`) plus `case_record` rows in the
DuckDB lake, because no other test writes `case_record` — the pack population
is isolated. Rows use distinct ids (`run_rp_1`, `ent_rp`...) so nothing here can
collide with the suite's shared runtime DB.

Run: `python -m unittest discover tests`
"""

from __future__ import annotations

import re
import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.db.duckdb_client import get_duckdb
from app.db.sqlite import get_connection, init_db
from app.main import create_app
from app.schemas.review_pack import ReviewPackGenerateIn, Verdict, VerdictIn
from app.services.ledger import get_ledger, LedgerAction
from app.services.review_pack_service import (
    PackNotFound,
    ReviewPackError,
    get_review_pack_service,
)
from tests import cleanup

NOW = datetime.now(timezone.utc).isoformat()
PS, PE = "2026-01-01", "2026-01-31"

# ent_rp risks (from findings below): rp_ca1=2.4, rp_ca2=2.4, rp_ca3=1.0,
# rp_ca4=0.5, rp_ca5=0, rp_ca6=0  ->  W = 6.3, N = 6.
RISK = {
    "rp_ca1": 2.4,
    "rp_ca2": 2.4,
    "rp_ca3": 1.0,
    "rp_ca4": 0.5,
    "rp_ca5": 0.0,
    "rp_ca6": 0.0,
}
W_ENT_RP = sum(RISK.values())  # 6.3


def tearDownModule() -> None:  # noqa: N802
    cleanup()


class ReviewPackFixture(unittest.TestCase):
    """Entities, one complete run, findings with case-level evidence, and the
    DuckDB case population for three pack scenarios."""

    @classmethod
    def setUpClass(cls) -> None:
        init_db()
        get_duckdb().init()
        conn = get_connection()
        if conn.execute(
            "SELECT 1 FROM run WHERE run_id = 'run_rp_1'"
        ).fetchone():
            # Subclasses re-run setUpClass against the shared suite DB; the
            # fixture is built once, in sqlite and the lake together.
            return
        conn.execute(
            "INSERT OR IGNORE INTO sector_ref (sector_ref, display_name) "
            "VALUES ('BANK', 'Banking')"
        )
        for eid, name in (
            ("ent_rp", "Review Pack Bank"),
            ("ent_cap_rp", "Cap Bank"),
            ("ent_flat_rp", "Flat Bank"),
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
            "VALUES ('run_rp_1', ?, 'complete', '[]', 'pack-rp', 'pol-hash-rp', "
            "'nccipc_default', 'test-1.0', 42)",
            (NOW,),
        )
        for eid in ("ent_rp", "ent_cap_rp", "ent_flat_rp"):
            conn.execute(
                "INSERT INTO entity_score (entity_id, period_start, period_end, "
                "egi, nsi, dts, sap, assessability, run_id) "
                "VALUES (?, ?, ?, 0.5, 0.5, 0.5, 0.5, 'assessable', 'run_rp_1')",
                (eid, PS, PE),
            )
        # Findings: risk = confidence * max(0, effect_size), summed per case.
        cls._finding(conn, "F-RP-A", "ent_rp", "EG-01", 3.0, 0.8,
                     ["rp_ca1", "rp_ca2"])   # 2.4 per case
        cls._finding(conn, "F-RP-B", "ent_rp", "EG-08", 2.0, 0.5,
                     ["rp_ca3"])             # 1.0
        cls._finding(conn, "F-RP-C", "ent_rp", "EG-02", 1.0, 0.5,
                     ["rp_ca4"])             # 0.5
        cls._finding(conn, "F-RP-CAP", "ent_cap_rp", "EG-01", 2.0, 1.0,
                     ["rp_cb1", "rp_cb2"])   # 2.0 per case, capped scenario
        conn.commit()

        with get_duckdb().writer() as lake:
            for case_id, entity, day, analyst, severity in (
                ("rp_ca1", "ent_rp", 5, "ana1", "HIGH"),
                ("rp_ca2", "ent_rp", 6, "ana1", "HIGH"),
                ("rp_ca3", "ent_rp", 7, "ana2", "MEDIUM"),
                ("rp_ca4", "ent_rp", 8, "ana3", "LOW"),
                ("rp_ca5", "ent_rp", 9, "ana2", "CRITICAL"),
                ("rp_ca6", "ent_rp", 10, "ana4", "LOW"),
                ("rp_cb1", "ent_cap_rp", 11, "anaA", "HIGH"),
                ("rp_cb2", "ent_cap_rp", 12, "anaA", "HIGH"),
                ("rp_fd1", "ent_flat_rp", 13, "anaF", "LOW"),
                ("rp_fd2", "ent_flat_rp", 14, "anaF", "MEDIUM"),
                ("rp_fd3", "ent_flat_rp", 15, "anaG", "HIGH"),
            ):
                lake.execute(
                    "INSERT INTO case_record (case_id, entity_id, created_at, "
                    "analyst_pseudo, severity_norm) VALUES (?, ?, ?, ?, ?)",
                    (
                        case_id,
                        entity,
                        f"2026-01-{day:02d} 09:00:00",
                        analyst,
                        severity,
                    ),
                )

    @classmethod
    def _finding(cls, conn, fid, entity, indicator, effect, confidence, cases):
        conn.execute(
            "INSERT INTO finding (finding_id, run_id, entity_id, indicator_id, "
            "period_start, period_end, value, value_units, peer_median, peer_mad, "
            "peer_percentile, n_peers, baseline_method, baseline_cohort, "
            "self_median, self_mad, self_periods, effect_size, n, confidence, "
            "conf_n_term, conf_assessability_term, conf_data_trust_term, "
            "evidence_query, benign_explanations, required_fields, missing_fields, "
            "assessability, family, primary_dimension, secondary_dimensions, source, "
            "is_low_confidence_lead, actor_type_inferred, notes, created_ts) "
            "VALUES (?, 'run_rp_1', ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 0, "
            "'loo_median_mad', 'BANK|large|in-house|24x7', NULL, NULL, 0, ?, 60, "
            "?, 1.0, 1.0, 0.9, NULL, '[]', '[]', '[]', 1.0, 'rapid_thin_closure', "
            "'INV', '[]', 'rules_engine', 0, 1, NULL, ?)",
            (
                fid, entity, indicator, PS, PE, 1.0, effect, confidence, NOW,
            ),
        )
        for ordinal, case in enumerate(cases):
            conn.execute(
                "INSERT OR IGNORE INTO finding_evidence "
                "(finding_id, table_name, row_id, ordinal) "
                "VALUES (?, 'case_record', ?, ?)",
                (fid, case, ordinal),
            )

    # ------------------------------------------------------------ helpers ----
    @classmethod
    def _body(cls, **overrides) -> ReviewPackGenerateIn:
        kwargs = dict(
            period_start=PS,
            period_end=PE,
            entity_ids=["ent_rp"],
            n_target=2,
            n_control=4,
            seed=42,
            max_per_analyst=3,
            max_per_cluster=3,
        )
        kwargs.update(overrides)
        return ReviewPackGenerateIn(**kwargs)

    @staticmethod
    def _expected_pi(case: str) -> float:
        """Hartley-Rao π for the ent_rp targeted slice: min(1, n*w/W)."""
        n_target = 2
        return min(1.0, n_target * RISK[case] / W_ENT_RP)


class PackGeneration(ReviewPackFixture):
    def _generate(self, **overrides):
        return get_review_pack_service().generate(
            self._body(**overrides), actor="pack-tester"
        )

    def test_main_pack_records_known_inclusion_probabilities(self) -> None:
        pack = self._generate()
        self.assertEqual(6, pack.n_population)
        self.assertEqual(6, pack.n_selected)
        self.assertEqual(PS, pack.period_start)
        self.assertEqual(PE, pack.period_end)
        # The two PPS picks: every π is the Hartley-Rao value for its risk.
        targeted = [i for i in pack.items if i.slice_type.value == "targeted"]
        controls = [i for i in pack.items if i.slice_type.value == "control"]
        self.assertEqual(2, len(targeted))
        self.assertEqual(4, len(controls))
        for item in targeted:
            self.assertAlmostEqual(
                self._expected_pi(item.case_id), item.inclusion_prob, places=6
            )
            self.assertIn("risk-proportional", item.selected_because)
            self.assertGreater(item.case_risk_score, 0.0)
        # Controls: recorded π must match the ratio stated in their own prose.
        t_keyed = {i.case_id for i in targeted}
        for item in controls:
            self.assertNotIn(item.case_id, t_keyed, "controls disjoint of targeted")
            match = re.search(r"(\d+) of (\d+) available cases", item.selected_because)
            self.assertIsNotNone(match, item.selected_because)
            n_s, n_avail = int(match.group(1)), int(match.group(2))
            self.assertGreater(n_avail, 0)
            self.assertAlmostEqual(n_s / n_avail, item.inclusion_prob, places=3)
            self.assertEqual("control", item.slice_type.value)
        self.assertTrue(
            all(i.inclusion_prob is not None for i in pack.items),
            "every item records π; a pack without known probabilities is not defensible",
        )
        self.assertTrue(all(i.verification_prompts for i in pack.items))
        self.assertEqual(64, len(pack.content_hash))

    def test_targeted_never_selects_a_zero_risk_case(self) -> None:
        pack = self._generate()
        targeted = [i for i in pack.items if i.slice_type.value == "targeted"]
        self.assertTrue(targeted)
        for item in targeted:
            self.assertGreater(item.case_risk_score, 0.0)
            self.assertNotIn(item.case_id, ("rp_ca5", "rp_ca6"))

    def test_ht_estimates_bound_themselves(self) -> None:
        pack = self._generate()
        ht = pack.ht_estimate
        self.assertIsNotNone(ht)
        self.assertEqual(0.95, ht.confidence_level)
        self.assertEqual(6, ht.n_population)
        self.assertEqual(6, ht.n_sampled)
        self.assertLessEqual(0.0, ht.estimate)
        self.assertLessEqual(ht.ci_low, ht.estimate)
        self.assertLessEqual(ht.estimate, ht.ci_high)
        self.assertLessEqual(ht.ci_high, 1.0 + 1e-9)
        self.assertTrue(ht.caveat)

    def test_same_seed_reproduces_the_pack_and_hash(self) -> None:
        first = self._generate(seed=7)
        second = self._generate(seed=7)
        self.assertEqual(first.content_hash, second.content_hash)
        first_slice = {
            (i.slice_type.value, i.case_id, i.inclusion_prob)
            for i in first.items
        }
        second_slice = {
            (i.slice_type.value, i.case_id, i.inclusion_prob)
            for i in second.items
        }
        self.assertEqual(first_slice, second_slice)
        # The seed travels on the request, so two *bodies* with equal seed
        # produce the same TARGETED draw even across distinct requests.
        self.assertEqual(first.n_selected, second.n_selected)

    def test_diversity_cap_drops_the_lower_risk_excess_per_analyst(self) -> None:
        # ent_cap_rp: two HIGH cases, same analyst, each risk 2.0 (W=4.0).
        # n_target=2 draws both; max_per_analyst=1 must keep one and count the drop.
        pack = get_review_pack_service().generate(
            self._body(
                entity_ids=["ent_cap_rp"],
                n_target=2,
                n_control=0,
                max_per_analyst=1,
                seed=42,
            ),
            actor="pack-tester",
        )
        caps = pack.diversity_caps_applied
        self.assertEqual(1, caps["targeted_kept"])
        self.assertEqual(1, caps["targeted_analyst_capped_excluded"])
        self.assertEqual(1, caps["max_per_analyst"])
        targeted = [i for i in pack.items if i.slice_type.value == "targeted"]
        self.assertEqual(1, len(targeted))
        self.assertEqual(1, pack.n_selected)
        # The kept case: risk 2.0 of W 4.0 with n_target 2 -> π = 1.0 (certainty).
        self.assertEqual(1.0, targeted[0].inclusion_prob)
        # y=1, π=1 over a population of 2 -> HT point estimate 0.5, point CI.
        self.assertAlmostEqual(0.5, pack.ht_estimate.estimate, places=6)
        self.assertAlmostEqual(
            pack.ht_estimate.ci_low, pack.ht_estimate.ci_high, places=6
        )

    def test_flat_population_builds_controls_only_with_point_zero(self) -> None:
        pack = get_review_pack_service().generate(
            self._body(
                entity_ids=["ent_flat_rp"],
                n_target=3,
                n_control=2,
                seed=42,
            ),
            actor="pack-tester",
        )
        self.assertEqual(3, pack.n_target)   # requested, not effective
        self.assertEqual(0, pack.diversity_caps_applied["targeted_kept"])
        self.assertTrue(
            all(i.slice_type.value == "control" for i in pack.items)
        )
        self.assertEqual(2, len(pack.items))
        # No case carries a hypothesis: prevalence must be 0, stated as a point.
        self.assertAlmostEqual(0.0, pack.ht_estimate.estimate, places=6)
        self.assertAlmostEqual(pack.ht_estimate.ci_low, pack.ht_estimate.ci_high, places=6)

    def test_empty_or_zero_risk_population_refuses_an_empty_pack(self) -> None:
        service = get_review_pack_service()
        # Nothing to review at all: no cases in the window.
        with self.assertRaises(ReviewPackError):
            service.generate(
                self._body(
                    period_start="2020-01-01", period_end="2020-01-31"
                ),
                actor="pack-tester",
            )
        # Cases exist but none carries a hypothesis and no controls were asked for.
        with self.assertRaises(ReviewPackError):
            service.generate(
                self._body(
                    entity_ids=["ent_flat_rp"], n_target=3, n_control=0
                ),
                actor="pack-tester",
            )

    def test_unknown_entity_is_an_explicit_error(self) -> None:
        with self.assertRaises(ReviewPackError):
            self._generate(entity_ids=["not_an_entity"])

    def test_periods_are_dates_not_free_text(self) -> None:
        with self.assertRaises(ReviewPackError):
            self._generate(period_start="January 2026", period_end="2026-01-31")


class PackReadsAndLedger(ReviewPackFixture):
    def _generate(self, **overrides):
        return get_review_pack_service().generate(
            self._body(**overrides), actor="pack-tester"
        )

    def test_creation_is_ledgered_with_the_sampling_decision(self) -> None:
        before = get_ledger().head()
        before_state = get_ledger().verify()
        pack = self._generate()
        entries = get_connection().execute(
            "SELECT action, payload FROM ledger_entry WHERE action = ? "
            "ORDER BY seq",
            (LedgerAction.REVIEW_PACK_CREATED.value,),
        ).fetchall()
        self.assertTrue(entries)
        latest = entries[-1]
        import json as _json

        payload = _json.loads(latest["payload"])
        self.assertEqual(pack.pack_id, payload["pack_id"])
        self.assertEqual(pack.content_hash, payload["content_hash"])
        self.assertEqual(42, payload["seed"])
        self.assertEqual(6, payload["n_population"])
        self.assertNotEqual(before, get_ledger().head())
        # The suite's shared DB carries the deliberately forged break from
        # test_api (seq 900001, unrepairable by design), so the assertions here
        # are the properties this module owns: the append chained onto the
        # head we observed and introduced no *new* break point.
        after_state = get_ledger().verify()
        self.assertEqual(
            before_state.first_break_seq, after_state.first_break_seq
        )
        head = get_ledger().head_entry()
        self.assertEqual(before, head.prev_hash)

    def test_get_reads_items_back_with_entity_and_no_verdict(self) -> None:
        pack = self._generate()
        read = get_review_pack_service().get(pack.pack_id)
        self.assertEqual(pack.pack_id, read.pack_id)
        self.assertEqual(pack.content_hash, read.content_hash)
        self.assertTrue(all(i.entity_id == "ent_rp" for i in read.items))
        self.assertTrue(all(i.verdict is None for i in read.items))
        # The drawn targeted case carries its finding linkage.
        targeted = [i for i in read.items if i.slice_type.value == "targeted"]
        self.assertTrue(all(i.finding_ids for i in targeted))
        self.assertEqual(["EG-01"], targeted[0].contributing_indicators)

    def test_missing_pack_raises_pack_not_found(self) -> None:
        with self.assertRaises(PackNotFound):
            get_review_pack_service().get("pack_nope")


class Verdicts(ReviewPackFixture):
    def _generate(self, **overrides):
        return get_review_pack_service().generate(
            self._body(**overrides), actor="pack-tester"
        )

    def test_verdict_is_recorded_ledgered_and_reflected_on_the_item(self) -> None:
        pack = self._generate()
        case = pack.items[0].case_id
        before_state = get_ledger().verify()
        before_head = get_ledger().head()
        out = get_review_pack_service().record_verdict(
            VerdictIn(
                pack_id=pack.pack_id,
                case_id=case,
                verdict=Verdict.BENIGN,
                notes="Checked the closure record; timeline is genuine.",
                evidence_seen=["rp_evidence_1", "rp_note_2"],
            ),
            actor="examiner-sam",
        )
        self.assertTrue(out.verdict_id.startswith("V-"))
        self.assertEqual("examiner-sam", out.examiner_pseudo)
        self.assertTrue(out.ledger_entry_hash)
        self.assertEqual(Verdict.BENIGN, out.verdict)

        # Ledgered under the examiner's own action.
        entries = get_connection().execute(
            "SELECT payload FROM ledger_entry WHERE action = ? ORDER BY seq",
            (LedgerAction.VERDICT_RECORDED.value,),
        ).fetchall()
        self.assertTrue(entries)
        import json as _json

        self.assertEqual(
            case, _json.loads(entries[-1]["payload"])["case_id"]
        )
        # Same shared-DB caveat as the pack test: assert the append chained
        # onto the head we observed with no *new* break point.
        after_state = get_ledger().verify()
        self.assertEqual(
            before_state.first_break_seq, after_state.first_break_seq
        )
        head = get_ledger().head_entry()
        self.assertEqual(before_head, head.prev_hash)

        # Reading the pack back surfaces the supervisor's conclusion on that item.
        read = get_review_pack_service().get(pack.pack_id)
        by_case = {i.case_id: i for i in read.items}
        self.assertEqual(Verdict.BENIGN, by_case[case].verdict)
        others = [i for c, i in by_case.items() if c != case]
        self.assertTrue(all(i.verdict is None for i in others))

        # List filter by pack and by case.
        listed = get_review_pack_service().list_verdicts(pack_id=pack.pack_id)
        self.assertEqual(1, len(listed))
        self.assertEqual(case, listed[0].case_id)
        scoped = get_review_pack_service().list_verdicts(
            pack_id=pack.pack_id, case_id=case
        )
        self.assertEqual(1, len(scoped))

    def test_verdict_on_an_unselected_case_is_refused(self) -> None:
        pack = self._generate()  # ent_rp only; rp_cb1 is not in it
        with self.assertRaises(ReviewPackError):
            get_review_pack_service().record_verdict(
                VerdictIn(
                    pack_id=pack.pack_id,
                    case_id="rp_cb1",
                    verdict=Verdict.CONFIRMED,
                ),
                actor="examiner-sam",
            )


class PackEndpoints(ReviewPackFixture):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # Service `*Error`s are converted to the typed 400 envelope by the
        # app's handlers, but this Starlette version re-raises after the
        # handler writes the response; without this flag TestClient surfaces
        # the exception instead of the 400 body.
        cls._ctx = TestClient(create_app(), raise_server_exceptions=False)
        cls.client = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._ctx.__exit__(None, None, None)

    def _post_pack(self, **overrides) -> dict:
        body = {
            "period_start": PS,
            "period_end": PE,
            "entity_ids": ["ent_rp"],
            "n_target": 2,
            "n_control": 4,
            "seed": 42,
        }
        body.update(overrides)
        resp = self.client.post("/api/v1/review-packs", json=body)
        self.assertEqual(200, resp.status_code, resp.text)
        return resp.json()

    def test_create_then_get_round_trips(self) -> None:
        created = self._post_pack()
        self.assertIn("pack_id", created)
        self.assertTrue(created["items"])
        self.assertIsNotNone(created["ht_estimate"])
        fetched = self.client.get(
            f"/api/v1/review-packs/{created['pack_id']}"
        ).json()
        self.assertEqual(created["content_hash"], fetched["content_hash"])
        self.assertEqual(created["n_selected"], fetched["n_selected"])
        item = fetched["items"][0]
        self.assertTrue(item["entity_id"])
        self.assertIsNotNone(item["inclusion_prob"])
        self.assertTrue(item["selected_because"])

    def test_create_without_a_period_uses_the_last_completed_run(self) -> None:
        created = self._post_pack(period_start=None, period_end=None)
        self.assertEqual(PS, created["period_start"])
        self.assertEqual(PE, created["period_end"])

    def test_pack_list_is_a_paginated_envelope(self) -> None:
        self._post_pack()
        body = self.client.get("/api/v1/review-packs").json()
        self.assertIn("items", body)
        self.assertGreaterEqual(body["total"], 1)
        first = body["items"][0]
        for key in ("pack_id", "created_ts", "period_start", "n_selected"):
            self.assertIn(key, first)

    def test_missing_pack_is_a_typed_404(self) -> None:
        resp = self.client.get("/api/v1/review-packs/pack_nope")
        self.assertEqual(404, resp.status_code)
        self.assertEqual("not_found", resp.json()["error"])

    def test_empty_population_is_a_typed_400(self) -> None:
        resp = self.client.post(
            "/api/v1/review-packs",
            json={
                "period_start": "2020-01-01",
                "period_end": "2020-01-31",
                "entity_ids": ["ent_rp"],
                "n_target": 2,
                "n_control": 1,
            },
        )
        self.assertEqual(400, resp.status_code)
        self.assertEqual("bad_request", resp.json()["error"])

    def test_export_is_still_an_honest_503(self) -> None:
        created = self._post_pack()
        resp = self.client.get(
            f"/api/v1/review-packs/{created['pack_id']}/export"
        )
        self.assertEqual(503, resp.status_code)
        body = resp.json()
        self.assertEqual("report_generator", body["service"])
        self.assertEqual("Phase 13 (2.13)", body["phase"])

    def test_verdicts_route_round_trip(self) -> None:
        created = self._post_pack()
        case = created["items"][0]["case_id"]
        resp = self.client.post(
            "/api/v1/verdicts",
            json={
                "pack_id": created["pack_id"],
                "case_id": case,
                "verdict": "confirmed",
                "notes": "Independently confirmed.",
                "evidence_seen": ["rp_evidence_9"],
            },
        )
        self.assertEqual(200, resp.status_code, resp.text)
        verdict = resp.json()
        self.assertTrue(verdict["verdict_id"].startswith("V-"))
        self.assertTrue(verdict["ledger_entry_hash"])
        listed = self.client.get(
            f"/api/v1/verdicts?pack_id={created['pack_id']}"
        ).json()
        self.assertEqual(1, listed["total"])
        self.assertEqual(case, listed["items"][0]["case_id"])

    def test_verdict_on_an_unselected_case_is_a_typed_400(self) -> None:
        created = self._post_pack()
        resp = self.client.post(
            "/api/v1/verdicts",
            json={
                "pack_id": created["pack_id"],
                "case_id": "rp_cb1",
                "verdict": "benign",
            },
        )
        self.assertEqual(400, resp.status_code)
        self.assertEqual("bad_request", resp.json()["error"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()