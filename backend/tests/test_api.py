"""End-to-end checks on a throwaway database: wiring, gates, envelopes.

These need no submission fixture. They check the parts of the API that are true
regardless of what data is loaded — that the app boots, that the declared surface
is the registered surface, that the gates fail closed, that errors are typed, and
that the append-only ledger verifies. Anything that needs a cohort belongs with
the cohort, in the Phase 2.18 fixture suite.

Run: `python -m unittest discover tests`

The suite prints `LEDGER CHAIN BROKEN at seq=900001` to stderr partway through.
That is the lifespan's boot-time verification doing its job, not a failure: the
ledger test forges a row, and every TestClient that starts afterwards refuses to
come up quietly on a broken chain. Test classes run alphabetically, so `Ledger`
comes before `Wiring` and `WriteGate`.
"""

from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient

from app.main import create_app
from tests import cleanup

# Bodies that satisfy each pending route's declared request model. A pending route
# still validates its body before answering, so `{}` would draw a 422 rather than
# the 503 that says "not built yet". Asserted separately in the contract tests.
PENDING_BODIES = {
    "POST /api/v1/verdicts": {
        "pack_id": "p_x", "case_id": "c_x", "verdict": "confirmed",
    },
    "POST /api/v1/packs/stage": {"source_path": "data/packs/x", "version": "1"},
    "POST /api/v1/review-packs": {"n_target": 3},
    "POST /api/v1/exports/brief": {},
}


def tearDownModule() -> None:  # noqa: N802
    cleanup()


class TestClientCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # As a context manager, so the lifespan runs and the schema is created.
        # A bare TestClient skips it, and then every read fails on a database that
        # does not exist -- which is a confusing way to learn that.
        cls._ctx = TestClient(create_app())
        cls.client = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._ctx.__exit__(None, None, None)


class Wiring(TestClientCase):
    def test_health_reports_offline_as_a_constant_not_a_claim(self) -> None:
        body = self.client.get("/health").json()
        self.assertEqual("ok", body["status"])
        self.assertTrue(body["offline"])
        # Nothing at runtime can prove the air gap; the sovereignty script can.
        self.assertEqual("hypotheses_with_evidence", body["outputs"])

    def test_contract_index_splits_live_from_pending(self) -> None:
        body = self.client.get("/api/v1").json()
        self.assertTrue(body["live"], "no live routes registered")
        self.assertTrue(body["pending"], "no pending routes registered")
        for entry in body["live"] + body["pending"]:
            self.assertTrue(entry["path"].startswith("/api/v1/"), entry["path"])
            self.assertTrue(entry["methods"], entry["path"])
        for entry in body["pending"]:
            self.assertTrue(entry["service"], entry["path"])
            self.assertIn("Phase", entry["phase"], entry["path"])

    def test_contract_index_and_openapi_agree_on_the_surface(self) -> None:
        index = self.client.get("/api/v1").json()
        spec = self.client.get("/openapi.json").json()
        declared = {
            f"{method.upper()} {path}"
            for path, ops in spec["paths"].items()
            for method in ops
            if method in ("get", "post", "put", "patch", "delete")
        }
        indexed = {
            f"{method} {entry['path']}"
            for entry in index["live"] + index["pending"]
            for method in entry["methods"]
        }
        # Every route in the index must be in the document. The document may hold
        # more, because `GET /api/v1` and `/health` are declared on the app rather
        # than on the v1 router and so are outside its route list.
        self.assertEqual(set(), indexed - declared)

    def test_pending_routes_return_a_typed_503(self) -> None:
        for entry in self.client.get("/api/v1").json()["pending"]:
            target = (
                entry["path"]
                .replace("{entity_id}", "ent_x")
                .replace("{finding_id}", "f_x")
                .replace("{pack_id}", "p_x")
                .replace("{seed_id}", "demo")
            )
            for method in entry["methods"]:
                r = self.client.request(
                    method,
                    target,
                    headers={"X-Actor": "test", "X-Role": "Supervisor"},
                    json=PENDING_BODIES.get(f"{method} {target}", {}),
                )
                self.assertEqual(503, r.status_code, f"{method} {target}")
                body = r.json()
                self.assertLessEqual(
                    {"endpoint", "service", "phase", "status"},
                    set(body),
                    f"{method} {target}: {body}",
                )
                # A `reason` in plain words is what an operator reads when a
                # feature they were told exists turns out not to.
                self.assertTrue(body.get("reason"), f"{method} {target}: {body}")


class WriteGate(TestClientCase):
    """The gate is permissive in dev and fail-closed everywhere else."""

    def tearDown(self) -> None:
        os.environ["ORION_ENV"] = "dev"

    def test_an_unattributed_write_is_refused_outside_dev(self) -> None:
        os.environ["ORION_ENV"] = "production"
        r = self.client.post("/api/v1/runs", json={})
        self.assertEqual(401, r.status_code, r.text)
        self.assertIn("X-Actor", r.json()["message"])

    def test_reads_still_work_outside_dev_with_no_identity(self) -> None:
        """An auditor who has not been issued credentials must still be able to
        look. Looking changes nothing, so refusing it buys no safety."""
        os.environ["ORION_ENV"] = "production"
        self.assertEqual(200, self.client.get("/api/v1/entities").status_code)

    def test_an_auditor_may_not_write(self) -> None:
        r = self.client.post(
            "/api/v1/runs",
            headers={"X-Actor": "auditor", "X-Role": "Auditor"},
            json={},
        )
        self.assertEqual(403, r.status_code, r.text)
        self.assertIn("Supervisor", r.json()["message"])

    def test_an_unknown_role_is_a_400_not_a_403(self) -> None:
        """A role the system does not recognise is a malformed request. Reporting
        it as forbidden would tell an operator their role is wrong, when the
        header is. Checked on a route that resolves an actor -- a read with no
        actor dependency ignores the header entirely, which is correct, since
        reads are open to everyone."""
        r = self.client.post(
            "/api/v1/runs",
            headers={"X-Actor": "someone", "X-Role": "overlord"},
            json={},
        )
        self.assertEqual(400, r.status_code, r.text)
        self.assertIn("overlord", r.json()["message"])

    def test_a_read_with_an_unused_role_header_still_works(self) -> None:
        """An X-Role on a route that never reads it must not fail the request.
        Refusing would make a harmless header a denial-of-service."""
        self.assertEqual(
            200,
            self.client.get("/api/v1/entities", headers={"X-Role": "overlord"}).status_code,
        )

    def test_a_narrower_gate_still_holds_for_policy_activation(self) -> None:
        """An Examiner may write, but may not reinterpret every stored score."""
        r = self.client.post(
            "/api/v1/policy-profiles/nccipc_default/activate",
            headers={"X-Actor": "examiner", "X-Role": "Examiner"},
            json={},
        )
        self.assertEqual(403, r.status_code, r.text)


class Errors(TestClientCase):
    def test_validation_errors_are_typed_and_name_the_field(self) -> None:
        r = self.client.get("/api/v1/entities?limit=99999")
        self.assertEqual(422, r.status_code)
        body = r.json()
        self.assertEqual("validation_error", body["error"])
        self.assertTrue(any(d.get("field") for d in body["details"]), body)

    def test_an_inverted_period_is_a_400(self) -> None:
        r = self.client.get("/api/v1/entities?period_start=2026-02-01&period_end=2026-01-01")
        self.assertEqual(400, r.status_code, r.text)

    def test_an_unknown_entity_is_a_404_in_the_typed_envelope(self) -> None:
        r = self.client.get("/api/v1/entities/NOPE/summary")
        self.assertEqual(404, r.status_code)
        self.assertEqual("not_found", r.json()["error"])


class Ledger(TestClientCase):
    def test_verify_reports_a_valid_chain_and_catches_an_unchained_row(self) -> None:
        """Verify, then forge, then verify again.

        One test rather than two because the second state cannot be undone: the
        append-only triggers refuse the cleanup, so a separate test would leave a
        broken chain behind for whatever ran next. Forging last keeps that true.
        """
        import sqlite3

        from app.db.sqlite import get_connection

        clean = self.client.get("/api/v1/ledger/verify").json()
        # An empty chain is valid. A brand-new database reporting `valid: false`
        # would train operators to ignore the field.
        self.assertTrue(clean["valid"], clean)
        self.assertIsNone(clean["first_break_seq"])
        self.assertIsNone(clean["break_reason"])
        self.assertEqual(clean["entries_checked"], 0)

        forged_prev = "e" * 64
        forged_hash = "f" * 64
        conn = get_connection()
        conn.execute(
            "INSERT INTO ledger_entry (seq, entry_hash, prev_hash, timestamp, "
            "actor, action, payload_hash, payload, entity_id) "
            "VALUES (?, ?, ?, datetime('now'), 'forger', 'forged', ?, '{}', NULL)",
            # Bound, not interpolated: `'e' * 64` inside a SQL string is SQL, and
            # SQLite reads `e * 64` as the number 0.
            (900001, forged_hash, forged_prev, "d" * 64),
        )
        conn.commit()

        broken = self.client.get("/api/v1/ledger/verify").json()
        self.assertFalse(broken["valid"], "the verifier accepted an unchained row")
        self.assertEqual(900001, broken["first_break_seq"])
        # Full hashes, not 16-character prefixes: this string is what an examiner
        # quotes when the chain breaks.
        self.assertIn(forged_prev, broken["break_reason"])
        self.assertIn("prev_hash mismatch", broken["break_reason"])

        # Cannot be cleaned up in place -- the chain is not repairable, only
        # extensible. That is the property, so it is asserted rather than worked
        # around.
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM ledger_entry WHERE actor = 'forger'")
        conn.rollback()

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE ledger_entry SET actor = 'nobody' WHERE seq = 900001")
        conn.rollback()

        # Still broken, because it cannot be made otherwise.
        self.assertFalse(self.client.get("/api/v1/ledger/verify").json()["valid"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()