"""Review-pack export (Phase 13): signed supervisory brief, formats, ledgering.

The export is the artefact a supervisor leaves the building with, so this module
owns the contract points that make it defensible:

* every format is a rendering of ONE canonical brief — the JSON export *is* the
  canonical brief, and re-exporting an unchanged pack reproduces the same bytes
  and the same Ed25519 signature (Ed25519 is deterministic), so "the artefact
  did not change" is checkable without a timestamp oracle;
* the payload carries the same evidence rows as the screen — re-executed through
  `EvidenceService.get_evidence`, which is why the fixture here adds a finding
  with a real DuckDB evidence query over `case_record`;
* the signature verifies against the host public key (tamper detection), the
  rotation of nothing is asserted (key created once, reused), and the private key
  is operator-readable only where POSIX enforces it;
* the export is ledgered with the pre-frozen `pack_exported` action and
  `ledger_head_hash` in the response is the export's own entry hash.

The shared suite DB carries test_api's deliberately forged break (seq 900001),
so ledger assertions follow the house rule: assert head chaining and that the
append introduced no *new* break point, never global validity.

Run: `python -m unittest discover tests`
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.sqlite import get_connection
from app.main import create_app
from app.schemas.review_pack import Verdict, VerdictIn
from app.services.ledger import LedgerAction, get_ledger
from app.services.review_pack_service import get_review_pack_service
from app.services.signing_service import (
    host_label,
    key_fingerprint,
    sign_bytes,
    signing_input,
    verify_bytes,
)
from tests import cleanup
from tests.test_review_packs import NOW, PS, PE, ReviewPackFixture

#: A real stored evidence query over the DuckDB lake, so the export's evidence
#: section contains the same rows the screen would show for the finding.
RPE_EV_QUERY = json.dumps(
    {
        "engine": "duckdb",
        "sql": (
            "SELECT case_id, entity_id, severity_norm, analyst_pseudo "
            "FROM case_record WHERE entity_id = 'ent_rp' ORDER BY case_id"
        ),
        "params": [],
        "table": "case_record",
    }
)


def tearDownModule() -> None:  # noqa: N802
    cleanup()


class ReviewPackExport(ReviewPackFixture):
    """Export contract tests. Reuses the review-pack fixture; the evidence
    finding below is added here with a real DuckDB evidence query."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._ensure_evidence_finding()
        # Service `*Error`s become 400 via the app handlers, but this Starlette
        # re-raises after the handler writes; without the flag, TestClient
        # surfaces the exception instead of the 400 body (house rule from 2.11).
        cls._ctx = TestClient(create_app(), raise_server_exceptions=False)
        cls.client = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._ctx.__exit__(None, None, None)

    @classmethod
    def _ensure_evidence_finding(cls) -> None:
        """Add F-RPE-EV under the shared run, linked to case rp_ca1, carrying a
        real DuckDB evidence query. Idempotent against the shared suite DB."""
        conn = get_connection()
        if conn.execute(
            "SELECT 1 FROM finding WHERE finding_id = 'F-RPE-EV'"
        ).fetchone():
            return
        conn.execute(
            "INSERT INTO finding (finding_id, run_id, entity_id, indicator_id, "
            "period_start, period_end, value, value_units, peer_median, peer_mad, "
            "peer_percentile, n_peers, baseline_method, baseline_cohort, "
            "self_median, self_mad, self_periods, effect_size, n, confidence, "
            "conf_n_term, conf_assessability_term, conf_data_trust_term, "
            "evidence_query, benign_explanations, required_fields, missing_fields, "
            "assessability, family, primary_dimension, secondary_dimensions, source, "
            "is_low_confidence_lead, actor_type_inferred, notes, created_ts) "
            "VALUES (?, 'run_rp_1', 'ent_rp', 'EG-01', ?, ?, 1.0, NULL, NULL, "
            "NULL, NULL, 0, 'loo_median_mad', 'BANK|large|in-house|24x7', NULL, "
            "NULL, 0, 1.5, 60, 0.8, 1.0, 1.0, 0.9, ?, '[]', '[]', '[]', 1.0, "
            "'rapid_thin_closure', 'INV', '[]', 'rules_engine', 0, 1, NULL, ?)",
            ("F-RPE-EV", PS, PE, RPE_EV_QUERY, NOW),
        )
        conn.execute(
            "INSERT OR IGNORE INTO finding_evidence "
            "(finding_id, table_name, row_id, ordinal) "
            "VALUES (?, 'case_record', 'rp_ca1', 0)",
            ("F-RPE-EV",),
        )
        conn.commit()

    # ------------------------------------------------------------ helpers ----
    def _generate(self, **overrides):
        return get_review_pack_service().generate(
            self._body(**overrides), actor="export-tester"
        )

    def _export(self, pack_id: str, fmt: str, actor: str = "supervisor-export"):
        return self.client.get(
            f"/api/v1/review-packs/{pack_id}/export",
            params={"fmt": fmt},
            headers={"X-Actor": actor, "X-Role": "Supervisor"},
        )

    # ---------------------------------------------------------------- tests --
    def test_json_export_is_signed_written_and_verifiable(self) -> None:
        pack = self._generate()
        resp = self._export(pack.pack_id, "json")
        self.assertEqual(200, resp.status_code, resp.text)
        body = resp.json()
        self.assertEqual(pack.pack_id, body["pack_id"])
        self.assertEqual(["json"], body["formats"])
        self.assertEqual(pack.content_hash, body["content_hash"])
        self.assertTrue(body["signed_by"].startswith("host:"))
        # 64-byte Ed25519 signature, base64: 88 chars, padded.
        self.assertEqual(88, len(body["signature"]))
        self.assertTrue(body["signature"].endswith("="))
        path = Path(body["export_paths"][0])
        self.assertTrue(path.exists())
        self.assertEqual("brief.json", path.name)
        raw = path.read_bytes()
        self.assertTrue(verify_bytes(raw, body["signature"]))

        parsed = json.loads(raw.decode("utf-8"))
        self.assertEqual("orion/review-pack-brief/v1", parsed["schema"])
        self.assertEqual(pack.pack_id, parsed["pack_id"])
        self.assertIn("items", parsed)
        self.assertIn("evidence", parsed)
        self.assertEqual(pack.content_hash, parsed["content_hash"])
        # Same item set as the screen.
        read = get_review_pack_service().get(pack.pack_id)
        self.assertEqual(
            {i.case_id for i in read.items},
            {i["case_id"] for i in parsed["items"]},
        )

    def test_export_is_reproducible_and_tamper_detectable(self) -> None:
        pack = self._generate()
        first = self._export(pack.pack_id, "json").json()
        second = self._export(pack.pack_id, "json").json()
        raw_a = Path(first["export_paths"][0]).read_bytes()
        raw_b = Path(second["export_paths"][0]).read_bytes()
        # Byte-reproducible brief -> identical signature (Ed25519 is deterministic).
        self.assertEqual(raw_a, raw_b)
        self.assertEqual(first["signature"], second["signature"])
        self.assertTrue(verify_bytes(raw_a, first["signature"]))

        corrupted = bytearray(raw_a)
        mid = len(corrupted) // 2
        corrupted[mid] = ord("X") if corrupted[mid] != ord("X") else ord("Y")
        self.assertFalse(verify_bytes(bytes(corrupted), first["signature"]))

    def test_markdown_export_carries_evidence_rows_and_caveats(self) -> None:
        # Full coverage so rp_ca1 (linked to F-RPE-EV) is definitely drawn.
        pack = self._generate(n_target=6, n_control=0)
        item = next(i for i in pack.items if i.case_id == "rp_ca1")
        self.assertIn("F-RPE-EV", item.finding_ids)

        resp = self._export(pack.pack_id, "md")
        self.assertEqual(200, resp.status_code, resp.text)
        body = resp.json()
        self.assertEqual(["md"], body["formats"])
        md = Path(body["export_paths"][0]).read_text("utf-8")
        self.assertIn("# Review pack", md)
        self.assertIn("selected because", md)
        self.assertIn("rp_ca1", md)
        self.assertIn("F-RPE-EV", md)
        # The evidence section shows the same rows as the screen: re-executed
        # from the stored DuckDB query over case_record.
        self.assertIn("case_record", md)
        self.assertIn("severity_norm=HIGH", md)
        self.assertIn("severity_norm=MEDIUM", md)
        self.assertTrue(verify_bytes(md.encode("utf-8"), body["signature"]))

    def test_pdf_export_is_a_signed_greppable_pdf(self) -> None:
        pack = self._generate(n_target=6, n_control=0)
        resp = self._export(pack.pack_id, "pdf")
        self.assertEqual(200, resp.status_code, resp.text)
        body = resp.json()
        self.assertEqual(["pdf"], body["formats"])
        raw = Path(body["export_paths"][0]).read_bytes()
        self.assertTrue(raw.startswith(b"%PDF-"))
        self.assertTrue(raw.rstrip().endswith(b"%%EOF"))
        self.assertGreater(len(raw), 1000)
        # Uncompressed streams make the leave-behind greppable.
        self.assertIn(b"Review pack", raw)
        self.assertIn(b"case_record", raw)
        self.assertIn(b"rp_ca1", raw)
        self.assertTrue(verify_bytes(raw, body["signature"]))

    def test_docx_names_the_gap_as_a_typed_400(self) -> None:
        pack = self._generate()
        resp = self._export(pack.pack_id, "docx")
        self.assertEqual(400, resp.status_code)
        self.assertEqual("bad_request", resp.json()["error"])
        self.assertIn("docx", resp.json()["message"])

    def test_unknown_format_is_a_typed_400(self) -> None:
        pack = self._generate()
        resp = self._export(pack.pack_id, "quark")
        self.assertEqual(400, resp.status_code)
        self.assertEqual("bad_request", resp.json()["error"])
        self.assertIn("unknown export format", resp.json()["message"])

    def test_missing_pack_is_a_typed_404(self) -> None:
        resp = self._export("pack_nope", "json")
        self.assertEqual(404, resp.status_code)
        self.assertEqual("not_found", resp.json()["error"])

    def test_export_is_ledgered_with_pack_exported(self) -> None:
        pack = self._generate()
        before = get_ledger().head()
        before_state = get_ledger().verify()
        body = self._export(pack.pack_id, "json").json()

        head = get_ledger().head_entry()
        self.assertEqual(LedgerAction.PACK_EXPORTED.value, head.action)
        self.assertEqual(before, head.prev_hash)
        self.assertEqual(pack.pack_id, head.entity_id)
        self.assertEqual(body["ledger_head_hash"], head.entry_hash)
        payload = json.loads(head.payload)
        self.assertEqual(pack.pack_id, payload["pack_id"])
        self.assertEqual("json", payload["format"])
        self.assertEqual(body["signature"], payload["signature"])
        self.assertEqual(body["signed_by"], payload["signed_by"])
        # House rule: the append introduced no *new* break point.
        after_state = get_ledger().verify()
        self.assertEqual(
            before_state.first_break_seq, after_state.first_break_seq
        )

    def test_a_supervisors_verdict_travels_into_the_export(self) -> None:
        pack = self._generate()
        case = pack.items[0].case_id
        get_review_pack_service().record_verdict(
            VerdictIn(
                pack_id=pack.pack_id,
                case_id=case,
                verdict=Verdict.BENIGN,
                notes="Closure record checks out.",
            ),
            actor="examiner-sam",
        )
        body = self._export(pack.pack_id, "json").json()
        parsed = json.loads(Path(body["export_paths"][0]).read_text("utf-8"))
        item = next(i for i in parsed["items"] if i["case_id"] == case)
        self.assertEqual("benign", item["verdict"])

    def test_signing_key_is_created_once_and_reused(self) -> None:
        first = key_fingerprint()
        self.assertTrue(first)
        second = key_fingerprint()
        self.assertEqual(first, second)
        self.assertEqual("host", host_label().split(":")[0])

        keys_dir = get_settings().pubkey_dir
        priv, pub = keys_dir / "orion_signing.key", keys_dir / "orion_signing.pub"
        self.assertTrue(priv.exists())
        self.assertTrue(pub.exists())
        self.assertGreater(priv.stat().st_size, 100)

        # Deterministic envelope: same payload, same signed input.
        self.assertEqual(signing_input(b"x"), signing_input(b"x"))
        self.assertNotEqual(signing_input(b"x"), signing_input(b"y"))
        sig, by = sign_bytes(b"hello")
        self.assertTrue(verify_bytes(b"hello", sig))
        self.assertFalse(verify_bytes(b"hello!", sig))
        self.assertEqual(by, host_label())

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not enforced on Windows")
    def test_private_key_file_is_operator_readable_only(self) -> None:
        priv = get_settings().pubkey_dir / "orion_signing.key"
        self.assertEqual(0o600, priv.stat().st_mode & 0o777)

    def test_verify_script_checks_the_artefact(self) -> None:
        pack = self._generate()
        body = self._export(pack.pack_id, "json").json()
        script = (
            Path(__file__).resolve().parent.parent / "scripts" / "verify_export.py"
        )
        cwd = Path(__file__).resolve().parent.parent
        ok = subprocess.run(
            [sys.executable, str(script), body["export_paths"][0], body["signature"]],
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, ok.returncode, ok.stderr)
        self.assertIn("VERIFIED", ok.stdout)
        bad = subprocess.run(
            [sys.executable, str(script), body["export_paths"][0], "AAAA"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, bad.returncode)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()