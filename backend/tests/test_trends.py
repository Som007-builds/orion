"""Trends (Phase 2.15, plan §6.5): per-entity series and regime shifts.

The fixture scores nine synthetic entities across eight monthly periods in 2025
— a window no other fixture occupies — with a deliberate cohort regime shift at
the fifth period: EGI steps up ~0.21 -> ~0.60, NSI steps down ~0.49 -> ~0.22,
SAP stays flat, and DTS is only scored for the first four periods (too short a
history for a regime test).

`ent_tr` itself carries both ways a value can be absent, and the routes must
keep them distinct: a *gap* at period three (no row at all, while the cohort
scored it — charts must break the line) and an *un-scored* NSI at period two
(the row exists, the value is NULL — "could not be scored", not "scored
nothing").

Run: `python -m unittest discover tests`
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.db.sqlite import get_connection, init_db
from app.main import create_app
from tests import cleanup

NOW = "2026-01-01T00:00:00+00:00"

# 2025 window: deliberately clear of every other fixture's 2026 periods, so a
# windowed trend read sees only this fixture's cohort.
PERIODS = [
    ("2025-01-01", "2025-01-31"),
    ("2025-02-01", "2025-02-28"),
    ("2025-03-01", "2025-03-31"),
    ("2025-04-01", "2025-04-30"),
    ("2025-05-01", "2025-05-31"),
    ("2025-06-01", "2025-06-30"),
    ("2025-07-01", "2025-07-31"),
    ("2025-08-01", "2025-08-31"),
]
PEERS = [f"tr_p{i:02d}" for i in range(8)]
TR = "ent_tr"
EMPTY = "ent_tr_empty"
ALL_IDS = PEERS + [TR, EMPTY]

RUN = "run_tr"


def tearDownModule() -> None:  # noqa: N802
    cleanup()


def _egi(p_idx: int, offset: float) -> float:
    base = 0.19 if p_idx < 4 else 0.58
    return round(base + offset, 3)


def _nsi(p_idx: int, offset: float) -> float:
    base = 0.47 if p_idx < 4 else 0.20
    return round(base + offset, 3)


def _dts(p_idx: int, offset: float) -> float | None:
    return round(0.30 + offset, 3) if p_idx < 4 else None


class TrendFixture(unittest.TestCase):
    """Nine entities across eight 2025 periods with a regime shift at P5.

    Built once; guarded so the shared suite DB is never double-seeded.
    """

    @classmethod
    def setUpClass(cls) -> None:
        init_db()
        conn = get_connection()
        cls.client = TestClient(create_app(), raise_server_exceptions=False)
        if conn.execute("SELECT 1 FROM run WHERE run_id = ?", (RUN,)).fetchone():
            return

        conn.execute(
            "INSERT OR IGNORE INTO sector_ref (sector_ref, display_name) "
            "VALUES ('BANK', 'Banking')"
        )
        for eid in ALL_IDS:
            conn.execute(
                "INSERT OR IGNORE INTO entity (entity_id, name, sector, soc_model, "
                "coverage_type, declared_open, declared_close, size_tier, created_at) "
                "VALUES (?,?,'BANK','in-house','24x7','08:00','20:00','large',?)",
                (eid, f"Trend Bank {eid}", NOW),
            )
        # finished_ts deliberately ancient: this fixture's run must never be the
        # "latest run" a read in another module resolves (the shared suite DB).
        conn.execute(
            "INSERT INTO run (run_id, created_ts, finished_ts, status, "
            "input_manifest_hashes, code_version, seed) "
            "VALUES (?, ?, '2000-01-01T00:00:00+00:00', 'complete', '[\"t\"]', "
            "'test-trends-1', 7)",
            (RUN, NOW),
        )

        rows = []
        for eid in PEERS:
            offset = (PEERS.index(eid) % 5) * 0.01
            for p_idx, (ps, pe) in enumerate(PERIODS):
                rows.append(
                    (eid, ps, pe, _egi(p_idx, offset), _nsi(p_idx, offset),
                     _dts(p_idx, offset), 0.50)
                )
        for p_idx, (ps, pe) in enumerate(PERIODS):
            if ps == PERIODS[2][0]:
                continue  # ent_tr gap: no row for period three.
            rows.append(
                (TR, ps, pe, _egi(p_idx, 0.04),
                 None if p_idx == 1 else _nsi(p_idx, 0.04),
                 _dts(p_idx, 0.04), 0.50)
            )
        conn.executemany(
            "INSERT INTO entity_score (entity_id, period_start, period_end, egi, "
            "nsi, dts, sap, assessability, sap_rank, sap_rank_low, sap_rank_high, "
            "sap_tier, run_id) VALUES (?,?,?,?,?,?,?,'assessable',NULL,NULL,NULL,"
            "'T4',?)",
            [r + (RUN,) for r in rows],
        )

    # ---------------------------------------------------------- /trends ----
    def test_series_is_chronological_and_marks_the_gap(self) -> None:
        body = self.client.get(
            "/api/v1/trends", params={"entity_id": TR, "metric": "egi"}
        ).json()
        self.assertEqual(TR, body["entity_id"])
        self.assertEqual("egi", body["metric"])
        self.assertEqual(8, len(body["points"]))
        self.assertEqual("2025-01-01", body["points"][0]["period_start"])
        self.assertAlmostEqual(0.23, body["points"][0]["value"], places=3)
        # Period three has a cohort-scored row for everyone but ent_tr.
        gap = body["points"][2]
        self.assertTrue(gap["gap"])
        self.assertIsNone(gap["value"])
        self.assertFalse(gap["assessable"])
        # Lineage travels with each scored point.
        self.assertEqual(RUN, body["points"][0]["run_id"])
        self.assertEqual(7, body["n_scored"])
        self.assertEqual(1, body["n_gaps"])

    def test_unscored_metric_is_not_a_gap(self) -> None:
        body = self.client.get(
            "/api/v1/trends", params={"entity_id": TR, "metric": "nsi"}
        ).json()
        # P2: the row exists, the metric was not computed.
        unscored = body["points"][1]
        self.assertFalse(unscored["gap"])
        self.assertFalse(unscored["assessable"])
        self.assertIsNone(unscored["value"])
        # P3: still a genuine gap.
        self.assertTrue(body["points"][2]["gap"])
        self.assertEqual(1, body["n_gaps"])
        self.assertEqual(6, body["n_scored"])

    def test_period_window_slices_the_series(self) -> None:
        body = self.client.get(
            "/api/v1/trends",
            params={
                "entity_id": TR,
                "metric": "egi",
                "period_start": "2025-02-01",
                "period_end": "2025-06-30",
            },
        ).json()
        self.assertEqual(5, len(body["points"]))
        self.assertEqual("2025-02-01", body["points"][0]["period_start"])
        self.assertTrue(body["points"][1]["gap"])  # the middle month stays a gap.

    def test_entity_with_no_scores_is_an_empty_series(self) -> None:
        body = self.client.get(
            "/api/v1/trends", params={"entity_id": EMPTY}
        ).json()
        self.assertEqual([], body["points"])
        self.assertEqual(0, body["n_scored"])
        self.assertEqual(0, body["n_gaps"])

    def test_unknown_entity_is_404(self) -> None:
        r = self.client.get("/api/v1/trends", params={"entity_id": "ent_gone"})
        self.assertEqual(404, r.status_code)
        self.assertEqual("not_found", r.json()["error"])

    def test_empty_entity_id_is_400(self) -> None:
        r = self.client.get("/api/v1/trends", params={"entity_id": ""})
        self.assertEqual(400, r.status_code)
        body = r.json()
        self.assertEqual("bad_request", body["error"])
        self.assertIn("entity_id", body["message"])

    def test_unknown_metric_is_422(self) -> None:
        r = self.client.get(
            "/api/v1/trends", params={"entity_id": TR, "metric": "energy"}
        )
        self.assertEqual(422, r.status_code)
        self.assertEqual("validation_error", r.json()["error"])

    # ---------------------------------------------------- change points ----
    def test_change_point_flags_the_up_shift(self) -> None:
        r = self.client.get(
            "/api/v1/trends/change-points",
            params={
                "metric": "egi",
                "period_start": "2025-01-01",
                "period_end": "2025-08-31",
            },
        )
        self.assertEqual(200, r.status_code)
        body = r.json()
        self.assertEqual(8, body["n_periods"])
        self.assertEqual(1, len(body["change_points"]))
        cp = body["change_points"][0]
        self.assertEqual("2025-05-01", cp["period_start"])
        self.assertEqual("2025-05-31", cp["period_end"])
        self.assertEqual("up", cp["direction"])
        self.assertAlmostEqual(0.21, cp["cohort_median_before"], places=3)
        self.assertAlmostEqual(0.60, cp["cohort_median_after"], places=3)
        self.assertGreaterEqual(cp["shift"], 0.10)
        self.assertEqual(4, cp["n_periods_before"])
        self.assertEqual(4, cp["n_periods_after"])
        self.assertGreater(cp["ss_reduction"], 0.5)

    def test_change_point_reports_a_drop_as_down(self) -> None:
        body = self.client.get(
            "/api/v1/trends/change-points",
            params={
                "metric": "nsi",
                "period_start": "2025-01-01",
                "period_end": "2025-08-31",
            },
        ).json()
        self.assertEqual(1, len(body["change_points"]))
        cp = body["change_points"][0]
        self.assertEqual("2025-05-01", cp["period_start"])
        self.assertEqual("down", cp["direction"])
        self.assertLess(cp["shift"], -0.10)

    def test_flat_series_flags_nothing(self) -> None:
        body = self.client.get(
            "/api/v1/trends/change-points",
            params={
                "metric": "sap",
                "period_start": "2025-01-01",
                "period_end": "2025-08-31",
            },
        ).json()
        self.assertEqual(8, body["n_periods"])
        self.assertEqual([], body["change_points"])
        self.assertIsNone(body["note"])

    def test_too_short_history_says_so_instead_of_guessing(self) -> None:
        body = self.client.get(
            "/api/v1/trends/change-points",
            params={
                "metric": "dts",
                "period_start": "2025-01-01",
                "period_end": "2025-08-31",
            },
        ).json()
        self.assertEqual(4, body["n_periods"])
        self.assertEqual([], body["change_points"])
        self.assertIsNotNone(body["note"])
        self.assertIn("too short", body["note"])

    def test_change_points_reject_unknown_metric(self) -> None:
        r = self.client.get(
            "/api/v1/trends/change-points", params={"metric": "energy"}
        )
        self.assertEqual(422, r.status_code)

    def test_change_points_unwindowed_still_shape_checks(self) -> None:
        # Without a window the whole shared history is read (other fixtures'
        # rows included), so assert the envelope, not exact contents.
        r = self.client.get("/api/v1/trends/change-points", params={"metric": "egi"})
        self.assertEqual(200, r.status_code)
        body = r.json()
        self.assertIn("n_periods", body)
        self.assertIn("change_points", body)


if __name__ == "__main__":
    unittest.main()