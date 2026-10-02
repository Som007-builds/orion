"""Packs (Phase 14, plan §4.10): stage, shadow, promote, rollback.

The lifecycle is exercised against a cohort the scoring engine actually fires
on: nine peer banks at varied closure durations plus one fast-closing outlier,
each with in-period HIGH/CRITICAL closures (`closed_at` set), tier-A alerts, a
submission declaring its tables, an asset and note-store rows. The default
policy raises exactly one EG-01 finding (the outlier).

The fixture's live run is produced by running the scoring engine itself
(`score_period(persist=True)`), so the identical-policy shadow case is a
genuine reproducibility check — the shadow re-executes the same policy over the
same window and must diff against the stored findings as 0/0/0 — rather than a
comparison against hand-copied numbers that could drift from the engine.

Shadow diffs read the *stored* findings of the latest completed run, exactly as
the dashboard would. Tests that promote packs and then write fresh runs are
named `x_`-prefixed so they sort after every shadow test (unittest runs test
methods alphabetically) and never disturb the live side the shadows diff
against.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.duckdb_client import get_duckdb
from app.db.sqlite import get_connection, init_db
from app.main import create_app
from app.services.policy_profile import get_policy_service, load_from_yaml
from app.services.scoring_service import get_scoring_service
from tests import cleanup

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = str(BACKEND / "data" / "policies" / "policy_nccipc_default.yaml")

NOW = datetime.now(timezone.utc).isoformat()
PS, PE = "2026-01-01", "2026-01-31"

# Nine peers at varied closure durations + one outlier. Exact numbers are not
# asserted — relative properties are (identical-shadows diff 0/0/0, strict
# removes, loose adds) — but the durations must vary or the EG-01 peer baseline
# has no spread and the outlier cannot stand out.
PEER_DURATIONS = [285, 300, 315, 330, 345, 360, 395, 410, 280]
PEER_IDS = [f"ent_pk_{i:02d}" for i in range(len(PEER_DURATIONS))]
FAST_ID = "ent_pk_fast"
ALL_IDS = PEER_IDS + [FAST_ID]

PROFILE_ID = "nccipc_default"


def tearDownModule() -> None:  # noqa: N802
    cleanup()


def _policy_text(*, version: str, z0: float, z1: float, fdr_q: float) -> str:
    """The shipped default policy with the lifecycle knobs overridden."""
    text = Path(DEFAULT_POLICY).read_text(encoding="utf-8")
    text = text.replace('version: "1"', f'version: "{version}"')
    text = text.replace("z0: 2.0", f"z0: {z0}")
    text = text.replace("z1: 5.0", f"z1: {z1}")
    text = text.replace("fdr_q: 0.05", f"fdr_q: {fdr_q}")
    return text


class PackLifecycleFixture(unittest.TestCase):
    """Entities + lake for the pack scenarios and a live run the engine made.

    Guards mirror the review-pack fixture: the sqlite half survives per-module
    temp-tree cleanups (locked file on Windows) and is built once; the DuckDB
    lake is rebuilt idempotently whenever a cleanup wiped it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        init_db()
        get_duckdb().init()
        conn = get_connection()
        cls.client = TestClient(create_app(), raise_server_exceptions=False)

        if conn.execute(
            "SELECT 1 FROM entity WHERE entity_id = 'ent_pk_fast'"
        ).fetchone():
            cls._ensure_lake()
            cls.live_run_id = cls._latest_fixture_run()
            return

        cls._seed_sqlite(conn)
        cls._ensure_lake()
        # Live run: produced by the engine it is, so the identical-policy
        # shadow is a reproducibility assertion, not a copy of stored numbers.
        get_scoring_service().score_period(
            date(2026, 1, 1),
            date(2026, 1, 31),
            persist=True,
            actor="pack_manager:fixture:live",
        )
        cls.live_run_id = cls._latest_fixture_run()

    # ------------------------------------------------------------ seeding --
    @classmethod
    def _seed_sqlite(cls, conn) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO sector_ref (sector_ref, display_name) "
            "VALUES ('BANK', 'Banking')"
        )
        for eid in ALL_IDS:
            conn.execute(
                "INSERT OR IGNORE INTO entity (entity_id, name, sector, soc_model, "
                "coverage_type, declared_open, declared_close, size_tier, created_at) "
                "VALUES (?,?,'BANK','in-house','24x7','08:00','20:00','large',?)",
                (eid, f"Pack Bank {eid}", NOW),
            )
        tables = json.dumps(
            {"tables_loaded": ["case_record", "alert_record", "case_event", "escalation"]}
        )
        for i, eid in enumerate(ALL_IDS):
            conn.execute(
                "INSERT INTO submission (submission_id, entity_id, period_start, "
                "period_end, received_ts, manifest_hash, row_counts, dq_score, "
                "version) VALUES (?,?,?,?,?,'pk-manifest',?,0.7,1)",
                (f"sub_pk_{i}", eid, PS, PE, NOW, tables),
            )
            conn.execute(
                "INSERT INTO asset (asset_id_pseudo, entity_id, asset_class, "
                "criticality, environment, onboarded) "
                "VALUES (?,?,'workstation',2,'IT',?)",
                (f"ast_pk_{i}", eid, NOW),
            )
            conn.execute(
                "INSERT INTO note_store (note_ref, entity_id, length, redacted_text) "
                "VALUES (?,?,40,?)",
                (f"nt_pk_{i}", eid, f"note text {i} xxxx"),
            )
        conn.commit()

    @classmethod
    def _latest_fixture_run(cls) -> str:
        row = get_scoring_service()._latest_run(None, None)
        return str(row["run_id"]) if row else ""

    # ------------------------------------------------------------ the lake --
    @classmethod
    def _ensure_lake(cls) -> None:
        """(Re)build the cohort's lake rows if a cleanup wiped them."""
        with get_duckdb().writer() as lake:
            present = lake.execute(
                "SELECT COUNT(*) AS c FROM case_record WHERE case_id LIKE 'pk_ca_%'"
            ).fetchone()
            if int(present[0]) >= 600:
                return
            # case_record/case_event/alert_case key on case_id (case_event and
            # alert_case have no entity_id); alert_record keys on entity_id.
            for table in ("case_record", "case_event", "alert_case"):
                lake.execute(f"DELETE FROM {table} WHERE case_id LIKE 'pk_%'")
            lake.execute("DELETE FROM alert_record WHERE entity_id LIKE 'ent_pk_%'")

            k = 0
            for eid in ALL_IDS:
                dur = 45 if eid.endswith("fast") else PEER_DURATIONS[int(eid.rsplit("_", 1)[1])]
                for day in range(1, 31):
                    for _ in range(2):
                        opened = datetime(2026, 1, day, 8, 30) + timedelta(
                            minutes=(k % 20) * 7
                        )
                        closed = opened + timedelta(seconds=dur)
                        lake.execute(
                            "INSERT INTO case_record (case_id, entity_id, created_at, "
                            "closed_at, analyst_pseudo, disposition, severity_norm) "
                            "VALUES (?,?,?,?,?,'TRUE_POSITIVE','HIGH')",
                            (f"pk_ca_{eid}_{k}", eid, opened, closed, f"ana_{k % 4}"),
                        )
                        k += 1
            k2 = 0
            for eid in ALL_IDS:
                for day in (2, 9, 16, 23):
                    t = datetime(2026, 1, day, 13, 0) + timedelta(minutes=k2)
                    lake.execute(
                        "INSERT INTO alert_record (alert_id, entity_id, timestamp, "
                        "event_ts, severity_norm, rule_id, mitre_tactic) "
                        "VALUES (?,?,?,?,'MEDIUM','rule-1','T1078')",
                        (f"pk_al_{eid}_{k2}", eid, t, t),
                    )
                    k2 += 1
            k3 = 0
            for eid in ALL_IDS:
                for day in (3, 17):
                    lake.execute(
                        "INSERT INTO case_event (event_id, case_id, ts, event_type, "
                        "actor_pseudo) VALUES (?,?,?,'created',?)",
                        (
                            f"pk_ev_{eid}_{k3}",
                            f"pk_ca_{eid}_{k3 % 600}",
                            datetime(2026, 1, day, 9, 0),
                            "hum_1",
                        ),
                    )
                    k3 += 1

    # ------------------------------------------------------------- bundles --
    @classmethod
    def _bundle(
        cls,
        *,
        pack_id: str,
        version: str,
        z0: float = 2.0,
        z1: float = 5.0,
        fdr_q: float = 0.05,
    ) -> Path:
        """A pack bundle on disk: manifest.json + policy/<profile_id>.yaml."""
        bundle = Path(tempfile.mkdtemp(prefix=f"pk-bundle-{pack_id}-"))
        policy_dir = bundle / "policy"
        policy_dir.mkdir()
        (policy_dir / f"{PROFILE_ID}.yaml").write_text(
            _policy_text(version=version, z0=z0, z1=z1, fdr_q=fdr_q),
            encoding="utf-8",
        )
        (bundle / "manifest.json").write_text(
            json.dumps(
                {"policy_profile_id": PROFILE_ID, "pack_id": pack_id, "version": version}
            ),
            encoding="utf-8",
        )
        return bundle

    # -------------------------------------------------------------- client --
    def _stage(self, bundle: Path, version: str, *, pack_id: str | None = None):
        body: dict[str, object] = {"source_path": str(bundle), "version": version}
        if pack_id:
            body["pack_id"] = pack_id
        return self.client.post("/api/v1/packs/stage", json=body)

    def _stage_ok(self, bundle: Path, version: str, *, pack_id: str | None = None) -> dict:
        resp = self._stage(bundle, version, pack_id=pack_id)
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def _shadow(self, pack_id: str):
        return self.client.post(f"/api/v1/packs/{pack_id}/shadow")

    def _shadow_ok(self, pack_id: str) -> dict:
        resp = self._shadow(pack_id)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _promote_ok(self, pack_id: str) -> dict:
        resp = self.client.post(f"/api/v1/packs/{pack_id}/promote")
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _rollback(self, pack_id: str):
        return self.client.post(f"/api/v1/packs/{pack_id}/rollback")

    def _rollback_ok(self, pack_id: str) -> dict:
        resp = self._rollback(pack_id)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    # ---------------------------------------------------------------- tests --
    def test_stage_validates_and_signs(self) -> None:
        bundle = self._bundle(pack_id="pk_val", version="1")
        # Not a directory.
        resp = self.client.post(
            "/api/v1/packs/stage",
            json={"source_path": str(bundle / "manifest.json"), "version": "1"},
        )
        self.assertEqual(resp.status_code, 400)
        # No manifest.
        bare = Path(tempfile.mkdtemp(prefix="pk-bare-"))
        resp = self._stage(bare, "1")
        self.assertEqual(resp.status_code, 400)
        # Manifest without its policy file.
        no_policy = Path(tempfile.mkdtemp(prefix="pk-nopol-"))
        (no_policy / "manifest.json").write_text(
            json.dumps({"policy_profile_id": PROFILE_ID}), encoding="utf-8"
        )
        resp = self._stage(no_policy, "1")
        self.assertEqual(resp.status_code, 400)
        # Version mismatch between manifest and request.
        v2 = self._bundle(pack_id="pk_val", version="2")
        resp = self._stage(v2, "3")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("version", resp.json()["message"])
        # pack_id mismatch between manifest and request.
        resp = self._stage(v2, "2", pack_id="pk_other")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("pack_id", resp.json()["message"])

        # A valid stage: 201, signed, on disk, ledgered.
        resp = self.client.post(
            "/api/v1/packs/stage",
            json={
                "pack_id": "pk_val",
                "source_path": str(v2),
                "version": "2",
                "signed_by": "Supervisor G",
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertEqual(body["pack_id"], "pk_val")
        self.assertEqual(body["version"], "2")
        self.assertEqual(body["status"], "staged")
        self.assertEqual(body["signed_by"], "Supervisor G")
        self.assertEqual(len(body["content_hash"]), 64)
        self.assertNotEqual(body["signature"], "")
        self.assertIsNone(body["promoted_ts"])

        staged = get_settings().pack_dir / "staged" / "pk_val"
        self.assertTrue((staged / "manifest.json").is_file())
        self.assertTrue((staged / "policy" / "nccipc_default.yaml").is_file())

        row = get_connection().execute(
            "SELECT content_hash, status FROM pack WHERE pack_id = 'pk_val'"
        ).fetchone()
        self.assertEqual(row["content_hash"], body["content_hash"])
        self.assertEqual(row["status"], "staged")
        ledgered = get_connection().execute(
            "SELECT COUNT(*) AS c FROM ledger_entry "
            "WHERE action = 'pack_staged' AND entity_id = 'pk_val'"
        ).fetchone()
        self.assertGreater(int(ledgered["c"]), 0)

    def test_stage_refuses_duplicate(self) -> None:
        bundle = self._bundle(pack_id="pk_dup", version="1")
        self._stage_ok(bundle, "1", pack_id="pk_dup")
        resp = self._stage(bundle, "1", pack_id="pk_dup")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already registered", resp.json()["message"])

    def test_shadow_identical_policy_is_neutral(self) -> None:
        bundle = self._bundle(pack_id="pk_identical", version="1")
        self._stage_ok(bundle, "1", pack_id="pk_identical")
        body = self._shadow_ok("pk_identical")

        self.assertEqual(body["status"], "shadow")
        self.assertEqual(body["n_findings_added"], 0)
        self.assertEqual(body["n_findings_removed"], 0)
        self.assertEqual(body["n_findings_changed"], 0)
        self.assertEqual(body["recommendation"], "promote")
        self.assertNotEqual(body["ledger_entry_hash"], "")
        self.assertEqual(body["diff_report"]["counts"], {"changed": 0, "added": 0, "removed": 0})
        self.assertEqual(body["diff_report"]["live_run_id"], self.live_run_id)

        row = get_connection().execute(
            "SELECT status, diff_report FROM pack WHERE pack_id = 'pk_identical'"
        ).fetchone()
        self.assertEqual(row["status"], "shadow")
        report = json.loads(row["diff_report"])
        self.assertEqual(report["counts"]["added"], 0)
        self.assertEqual(report["pack_id"], "pk_identical")

    def test_shadow_strict_removes_findings(self) -> None:
        bundle = self._bundle(pack_id="pk_strict", version="2", z0=6.0, z1=9.0)
        self._stage_ok(bundle, "2", pack_id="pk_strict")
        body = self._shadow_ok("pk_strict")

        self.assertEqual(body["n_findings_added"], 0)
        self.assertGreaterEqual(body["n_findings_removed"], 1)
        self.assertEqual(body["recommendation"], "promote")
        self.assertTrue(body["diff_report"]["removed"])

    def test_shadow_loose_adds_findings(self) -> None:
        # Loose enough that the FDR gate lets marginal peers through AND low
        # |z| still clears the ramp: peers with p <= fdr_q survive BH (which is
        # per-entity), and a z0 of 0.5 gives them a positive signal.
        bundle = self._bundle(pack_id="pk_loose", version="3", z0=0.5, z1=3.5, fdr_q=0.5)
        self._stage_ok(bundle, "3", pack_id="pk_loose")
        body = self._shadow_ok("pk_loose")

        self.assertGreaterEqual(body["n_findings_added"], 1)
        self.assertEqual(body["recommendation"], "hold")
        self.assertTrue(body["diff_report"]["recommendation_reason"].startswith("hold"))
        self.assertTrue(body["diff_report"]["added"])

    def test_shadow_refuses_when_not_staged(self) -> None:
        # Unknown pack id -> 404.
        resp = self._shadow("pk_nope")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("does not exist", resp.json()["message"])

        # A staged pack shadows fine; the second shadow of the same pack is
        # refused (only 'staged' may shadow).
        bundle = self._bundle(pack_id="pk_unshadowed", version="1")
        self._stage_ok(bundle, "1", pack_id="pk_unshadowed")
        resp = self._shadow("pk_unshadowed")
        self.assertEqual(resp.status_code, 200)
        resp = self._shadow("pk_unshadowed")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("staged", resp.json()["message"])

        # Promote of an unknown pack -> 404.
        resp = self.client.post("/api/v1/packs/pk_nope/promote")
        self.assertEqual(resp.status_code, 404)

    def test_promote_requires_shadow(self) -> None:
        bundle = self._bundle(pack_id="pk_noshadow", version="1")
        self._stage_ok(bundle, "1", pack_id="pk_noshadow")
        resp = self.client.post("/api/v1/packs/pk_noshadow/promote")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("shadow", resp.json()["message"])

    def test_lifecycle_promote_and_rollback(self) -> None:
        # A: the current behaviour, promoted first.
        bundle_a = self._bundle(pack_id="pk_lc_a", version="1")
        self._stage_ok(bundle_a, "1", pack_id="pk_lc_a")
        self._shadow_ok("pk_lc_a")
        promoted = self._promote_ok("pk_lc_a")
        self.assertEqual(promoted["status"], "active")
        self.assertIsNone(promoted["previous_id"])
        self.assertNotEqual(promoted["ledger_entry_hash"], "")

        self.assertEqual(
            get_connection().execute(
                "SELECT status FROM pack WHERE pack_id = 'pk_lc_a'"
            ).fetchone()["status"],
            "active",
        )
        policy_a = load_from_yaml(bundle_a / "policy" / "nccipc_default.yaml")
        self.assertEqual(get_policy_service().active().content_hash, policy_a.content_hash)
        self.assertEqual(get_policy_service().active().version, "1")

        # B: a tightening policy promoted over A.
        bundle_b = self._bundle(pack_id="pk_lc_b", version="2", z0=6.0, z1=9.0)
        self._stage_ok(bundle_b, "2", pack_id="pk_lc_b")
        shadow_b = self._shadow_ok("pk_lc_b")
        self.assertGreaterEqual(shadow_b["n_findings_removed"], 1)
        promoted_b = self._promote_ok("pk_lc_b")
        self.assertEqual(promoted_b["previous_id"], "pk_lc_a")
        self.assertEqual(
            get_connection().execute(
                "SELECT status, promoted_ts FROM pack WHERE pack_id = 'pk_lc_b'"
            ).fetchone()["status"],
            "active",
        )
        row_a = get_connection().execute(
            "SELECT status, promoted_ts FROM pack WHERE pack_id = 'pk_lc_a'"
        ).fetchone()
        self.assertEqual(row_a["status"], "rolled_back")
        row_b = get_connection().execute(
            "SELECT promoted_ts FROM pack WHERE pack_id = 'pk_lc_b'"
        ).fetchone()
        self.assertGreater(row_b["promoted_ts"], row_a["promoted_ts"])
        policy_b = load_from_yaml(bundle_b / "policy" / "nccipc_default.yaml")
        self.assertEqual(get_policy_service().active().content_hash, policy_b.content_hash)
        self.assertEqual(get_policy_service().active().version, "2")
        ledgered = get_connection().execute(
            "SELECT COUNT(*) AS c FROM ledger_entry "
            "WHERE action = 'pack_promoted' AND entity_id = 'pk_lc_b'"
        ).fetchone()
        self.assertGreater(int(ledgered["c"]), 0)

        # Rollback A: one step, statuses swap, A's policy is re-adopted.
        rolled = self._rollback_ok("pk_lc_a")
        self.assertEqual(rolled["status"], "active")
        self.assertEqual(rolled["previous_id"], "pk_lc_b")
        self.assertEqual(
            get_connection().execute(
                "SELECT status FROM pack WHERE pack_id = 'pk_lc_a'"
            ).fetchone()["status"],
            "active",
        )
        self.assertEqual(
            get_connection().execute(
                "SELECT status FROM pack WHERE pack_id = 'pk_lc_b'"
            ).fetchone()["status"],
            "rolled_back",
        )
        self.assertEqual(get_policy_service().active().content_hash, policy_a.content_hash)
        self.assertEqual(get_policy_service().active().version, "1")
        ledgered = get_connection().execute(
            "SELECT COUNT(*) AS c FROM ledger_entry "
            "WHERE action = 'pack_rolled_back' AND entity_id = 'pk_lc_a'"
        ).fetchone()
        self.assertGreater(int(ledgered["c"]), 0)

        # A is live again: another rollback is refused.
        resp = self._rollback("pk_lc_a")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("superseded", resp.json()["message"])

    def test_rollback_refuses_non_immediate_predecessor(self) -> None:
        # Build an A -> B -> C chain, then try to leap A back over B.
        packs = [
            ("pk_chain_a", "1", 2.0, 5.0, 0.05),
            ("pk_chain_b", "2", 6.0, 9.0, 0.05),
            ("pk_chain_c", "3", 0.5, 3.5, 0.5),
        ]
        for pack_id, version, z0, z1, fdr_q in packs:
            bundle = self._bundle(
                pack_id=pack_id, version=version, z0=z0, z1=z1, fdr_q=fdr_q
            )
            self._stage_ok(bundle, version, pack_id=pack_id)
            self._shadow_ok(pack_id)
            self._promote_ok(pack_id)

        resp = self._rollback("pk_chain_a")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("immediate predecessor", resp.json()["message"])

        # The direct predecessor is restorable.
        rolled = self._rollback_ok("pk_chain_b")
        self.assertEqual(rolled["status"], "active")
        self.assertEqual(rolled["previous_id"], "pk_chain_c")

    def test_verify_pack_subprocess(self) -> None:
        bundle = self._bundle(pack_id="pk_signed", version="1")
        self._stage_ok(bundle, "1", pack_id="pk_signed")

        script = BACKEND / "scripts" / "verify_pack.py"
        proc = subprocess.run(
            [sys.executable, str(script), "pk_signed"],
            capture_output=True,
            text=True,
            env=os.environ,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("VERIFIED ok", proc.stdout)

        # Tamper with the staged bundle: the hash must no longer match.
        staged_policy = (
            get_settings().pack_dir
            / "staged"
            / "pk_signed"
            / "policy"
            / "nccipc_default.yaml"
        )
        with staged_policy.open("a", encoding="utf-8") as handle:
            handle.write("# tampered after staging\n")
        proc = subprocess.run(
            [sys.executable, str(script), "pk_signed"],
            capture_output=True,
            text=True,
            env=os.environ,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("FAILED", proc.stderr)

    def test_x_runs_record_pack_version(self) -> None:
        # A pack that was never promoted leaves no pack version on run lineage.
        row = get_connection().execute(
            "SELECT pack_version FROM run WHERE run_id = ?", (self.live_run_id,)
        ).fetchone()
        self.assertIsNone(row["pack_version"])

        # Promote a pack with a version no other bundle used, then score under
        # the now-active policy: the run must carry the pack's version.
        bundle = self._bundle(pack_id="pk_lin", version="9", z0=0.5, z1=3.5, fdr_q=0.5)
        self._stage_ok(bundle, "9", pack_id="pk_lin")
        self._shadow_ok("pk_lin")
        self._promote_ok("pk_lin")

        active = get_policy_service().active()
        self.assertEqual(active.version, "9")
        get_scoring_service(policy=active).score_period(
            date(2026, 1, 1), date(2026, 1, 31), persist=True, actor="pack_manager:test:lin"
        )
        latest = get_scoring_service()._latest_run(None, None)
        row = get_connection().execute(
            "SELECT pack_version FROM run WHERE run_id = ?", (latest["run_id"],)
        ).fetchone()
        self.assertEqual(row["pack_version"], "9")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()