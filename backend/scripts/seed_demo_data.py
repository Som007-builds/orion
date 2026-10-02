"""Seed demonstration telemetry, entities, findings, and review packs into Orion SAT-SA.

Populates both SQLite state store (backend/data/sqlite/orion.db) and
DuckDB evidence store (backend/data/parquet/evidence.duckdb) with consistent,
realistic NCIIPC supervisory test data.

Usage:
    python scripts/seed_demo_data.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure backend root is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.db.sqlite import get_connection, init_db
from app.db.duckdb_client import get_duckdb

NOW = datetime.now(timezone.utc).isoformat()
PERIOD_START = "2026-09-01"
PERIOD_END = "2026-09-30"


def seed() -> None:
    print("==> Initializing SQLite state store...")
    init_db()
    conn = get_connection()

    print("==> Initializing DuckDB evidence store...")
    duck = get_duckdb()
    duck.init()

    # 1. Sector reference
    print("==> Seeding sector_ref...")
    sectors = [
        ("BANK", "Banking & Financial Services"),
        ("ENERGY", "Power & Energy Enclaves"),
        ("TELECOM", "Telecommunications"),
        ("TRANSPORT", "Civil Aviation & Rail"),
        ("GOV", "Government & Strategic Infrastructure"),
    ]
    for s_id, name in sectors:
        conn.execute(
            "INSERT OR REPLACE INTO sector_ref (sector_ref, display_name, active) VALUES (?, ?, 1)",
            (s_id, name),
        )

    # 2. Regulated CSE Entities
    print("==> Seeding regulated CSE entities...")
    entities = [
        ("CSE-POWER-01", "Northern Grid SCADA Enclave", "ENERGY", "in-house", "24x7", "large", 185),
        ("CSE-BANK-02", "National Clearing & Settlement System", "BANK", "in-house", "24x7", "large", 420),
        ("CSE-TELCO-01", "Central Core Routing Gateway", "TELECOM", "hybrid", "24x7", "medium", 94),
        ("CSE-TRANS-01", "Air Traffic Management Core Enclave", "TRANSPORT", "in-house", "24x7", "large", 62),
        ("CSE-DEF-03", "Strategic Defense Communications Hub", "GOV", "in-house", "24x7", "large", 310),
    ]
    for eid, name, sector, model, cov, size, assets in entities:
        conn.execute(
            """
            INSERT OR REPLACE INTO entity
            (entity_id, name, sector, soc_model, coverage_type, declared_open, declared_close,
             size_tier, critical_asset_count, created_at)
            VALUES (?, ?, ?, ?, ?, '00:00', '23:59', ?, ?, ?)
            """,
            (eid, name, sector, model, cov, size, assets, NOW),
        )

    # 3. Audit Run
    print("==> Seeding supervisory audit run...")
    run_id = "run_2026_09_cohort"
    conn.execute(
        """
        INSERT OR REPLACE INTO run
        (run_id, created_ts, status, input_manifest_hashes, pack_version, policy_hash,
         policy_profile_id, code_version, seed)
        VALUES (?, ?, 'complete', ?, 'pack_v2_stratified_pps', 'pol_nccipc_sha256_7f83b1',
                'policy_nccipc_default', 'orion-sat-sa:v2.12.0', 42)
        """,
        (run_id, NOW, json.dumps(["sub_hash_power_01", "sub_hash_bank_02", "sub_hash_telco_01"])),
    )

    # 4. Entity Scores
    print("==> Seeding entity scores...")
    scores = [
        ("CSE-POWER-01", 0.420, 0.180, 0.880, 0.380, "assessable", 2, 1, 3, "T2"),
        ("CSE-BANK-02", 0.280, 0.080, 0.940, 0.240, "assessable", 4, 3, 5, "T3"),
        ("CSE-TELCO-01", 0.680, 0.350, 0.650, 0.610, "assessable", 1, 1, 2, "T1"),
        ("CSE-TRANS-01", 0.150, 0.050, 0.910, 0.140, "assessable", 5, 4, 6, "T4"),
        ("CSE-DEF-03", None, None, 0.350, None, "not_assessable", None, None, None, "not_assessable"),
    ]
    for eid, egi, nsi, dts, sap, assess, rank, r_low, r_high, tier in scores:
        conn.execute(
            """
            INSERT OR REPLACE INTO entity_score
            (entity_id, period_start, period_end, egi, nsi, dts, sap, assessability,
             sap_rank, sap_rank_low, sap_rank_high, sap_tier, run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (eid, PERIOD_START, PERIOD_END, egi, nsi, dts, sap, assess, rank, r_low, r_high, tier, run_id),
        )

    # 5. 8-Dimension Scores
    print("==> Seeding 8-dimension scores...")
    dimensions = ["TD", "INV", "ESC", "IR", "SO", "GOV", "OD", "CR"]
    dim_profiles = {
        "CSE-POWER-01": [0.45, 0.58, 0.62, 0.38, 0.32, 0.28, 0.41, 0.52],
        "CSE-BANK-02": [0.22, 0.31, 0.25, 0.28, 0.21, 0.35, 0.29, 0.33],
        "CSE-TELCO-01": [0.72, 0.81, 0.74, 0.65, 0.58, 0.62, 0.69, 0.71],
        "CSE-TRANS-01": [0.14, 0.18, 0.12, 0.15, 0.19, 0.11, 0.16, 0.13],
    }
    for eid, vals in dim_profiles.items():
        for dim, val in zip(dimensions, vals):
            conn.execute(
                """
                INSERT OR REPLACE INTO dimension_score
                (entity_id, period_start, period_end, dimension, score, ci_low, ci_high,
                 assessability, dts, tier, run_id, pack_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, 0.88, 'T2', ?, 'pack_v2_stratified_pps')
                """,
                (eid, PERIOD_START, PERIOD_END, dim, val, max(0.0, val - 0.08), min(1.0, val + 0.08), run_id),
            )

    # 6. Detailed Findings
    print("==> Seeding finding cards and indicator status...")
    findings = [
        (
            "FINDING-PWR-001",
            "run_2026_09_cohort",
            "CSE-POWER-01",
            "EG-01",
            PERIOD_START,
            PERIOD_END,
            142.0,
            "seconds",
            1850.0,
            320.0,
            4.2,
            12,
            "loo_median_mad",
            "Power|large|in-house",
            4.85,
            24,
            0.88,
            1.0,
            1.0,
            0.88,
            "SELECT case_id, analyst_pseudo, investigation_duration_sec FROM case_record WHERE entity_id = 'CSE-POWER-01' AND investigation_duration_sec < 180",
            json.dumps([
                "Auto-enrichment SOAR playbook closed automated false positives",
                "Vendor routine health check trigger during scheduled maintenance window",
            ]),
            json.dumps(["case_record.investigation_duration_sec", "case_record.closed_by_pseudo"]),
            json.dumps([]),
            1.0,
            "rapid_thin_closure",
            "INV",
            json.dumps(["OD", "IR"]),
            "rules_engine",
            0,
            0,
            "Engine observed 24 critical substation cases resolved in under 180 seconds.",
        ),
        (
            "FINDING-PWR-002",
            "run_2026_09_cohort",
            "CSE-POWER-01",
            "EG-06",
            PERIOD_START,
            PERIOD_END,
            0.24,
            "ratio",
            0.02,
            0.01,
            96.5,
            12,
            "loo_median_mad",
            "Power|large|in-house",
            3.92,
            18,
            0.79,
            0.9,
            1.0,
            0.88,
            "SELECT case_id, severity_norm, escalation_status FROM case_record WHERE entity_id = 'CSE-POWER-01' AND severity_norm = 'CRITICAL'",
            json.dumps([
                "Known testing exercise on sub-network 10.42.0.0/16",
                "Analyst direct resolution under emergency SOP protocol 4A",
            ]),
            json.dumps(["escalation.target_level"]),
            json.dumps([]),
            1.0,
            "missing_escalation",
            "ESC",
            json.dumps(["GOV"]),
            "rules_engine",
            0,
            0,
            "Supervisory escalation was bypassed on 24% of critical OT boundary detections.",
        ),
        (
            "FINDING-TELCO-001",
            "run_2026_09_cohort",
            "CSE-TELCO-01",
            "EG-08",
            PERIOD_START,
            PERIOD_END,
            0.88,
            "similarity",
            0.32,
            0.09,
            98.9,
            10,
            "loo_median_mad",
            "Telecom|medium|hybrid",
            5.24,
            41,
            0.91,
            1.0,
            1.0,
            0.65,
            "SELECT case_id, note_text FROM case_record WHERE entity_id = 'CSE-TELCO-01'",
            json.dumps([
                "MSSP client-wide standard closure macro applied by contract analysts",
            ]),
            json.dumps(["note_store.text"]),
            json.dumps([]),
            1.0,
            "monoculture_closure",
            "OD",
            json.dumps(["INV"]),
            "rules_engine",
            0,
            0,
            "MinHash LSH shingle audit identified duplicate closure justifications.",
        ),
    ]

    for f in findings:
        conn.execute(
            """
            INSERT OR REPLACE INTO finding
            (finding_id, run_id, entity_id, indicator_id, period_start, period_end,
             value, value_units, peer_median, peer_mad, peer_percentile, n_peers,
             baseline_method, baseline_cohort, effect_size, n, confidence,
             conf_n_term, conf_assessability_term, conf_data_trust_term, evidence_query,
             benign_explanations, required_fields, missing_fields, assessability,
             family, primary_dimension, secondary_dimensions, source,
             is_low_confidence_lead, actor_type_inferred, notes, created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*f, NOW),
        )

    # 7. Finding Evidence Rows
    conn.execute(
        "INSERT OR REPLACE INTO finding_evidence (finding_id, table_name, row_id, ordinal) VALUES "
        "('FINDING-PWR-001', 'case_record', 'CASE-PWR-8801', 1), "
        "('FINDING-PWR-001', 'case_record', 'CASE-PWR-8802', 2), "
        "('FINDING-PWR-001', 'case_record', 'CASE-PWR-8805', 3), "
        "('FINDING-PWR-002', 'case_record', 'CASE-PWR-9104', 1), "
        "('FINDING-TELCO-001', 'case_record', 'CASE-TEL-4411', 1)"
    )

    # 8. Review Packs & Items
    print("==> Seeding review packs and systematic PPS sampled items...")
    pack_id = "pack_pwr_01_sep26"
    conn.execute(
        """
        INSERT OR REPLACE INTO review_pack
        (pack_id, created_ts, created_by, period_start, period_end, stratum,
         n_target, n_control, n_selected, n_population, seed, ht_estimate, ht_ci_low, ht_ci_high, content_hash)
        VALUES (?, ?, 'supervisor.sharma', ?, ?, 'critical', 4, 2, 6, 120, 42, 0.185, 0.112, 0.274, 'hash_pack_pwr_01')
        """,
        (pack_id, NOW, PERIOD_START, PERIOD_END),
    )

    pack_items = [
        (pack_id, "CASE-PWR-8801", "CSE-POWER-01", "targeted", 0.3842, 0.890, "Sampled because: Rapid closure (112s) of critical substation telemetry with high effect size.", json.dumps(["Inspect SCADA operator terminal logs", "Verify physical switchboard state"]), "CRITICAL", "FINDING-PWR-001"),
        (pack_id, "CASE-PWR-8802", "CSE-POWER-01", "targeted", 0.2915, 0.820, "Sampled because: Substation trip alarm resolved without tier-2 supervisory escalation.", json.dumps(["Review transmission log", "Confirm supervisory notification"]), "CRITICAL", "FINDING-PWR-001"),
        (pack_id, "CASE-PWR-8805", "CSE-POWER-01", "targeted", 0.2150, 0.760, "Sampled because: Unacknowledged protective relay communication fault.", json.dumps(["Verify relay handshake timestamp"]), "HIGH", "FINDING-PWR-001"),
        (pack_id, "CASE-PWR-9104", "CSE-POWER-01", "targeted", 0.1820, 0.710, "Sampled because: Critical OT boundary intrusion unescalated under SOP.", json.dumps(["Check firewall border session trace"]), "CRITICAL", "FINDING-PWR-002"),
        (pack_id, "CASE-PWR-7012", "CSE-POWER-01", "control", 0.0500, 0.120, "Sampled because: Randomly drawn from stratum Medium as unbiased calibration control slice.", json.dumps(["Standard procedural baseline verification"]), "MEDIUM", None),
        (pack_id, "CASE-PWR-7019", "CSE-POWER-01", "control", 0.0500, 0.090, "Sampled because: Randomly drawn from stratum Low as unbiased calibration control slice.", json.dumps(["Standard procedural baseline verification"]), "LOW", None),
    ]

    for item in pack_items:
        conn.execute(
            """
            INSERT OR REPLACE INTO review_pack_item
            (pack_id, case_id, entity_id, slice_type, inclusion_prob, case_risk_score,
             selected_because, verification_prompts, severity_norm, finding_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            item,
        )

    # 9. Submissions
    print("==> Seeding intake submissions...")
    submissions = [
        (
            "sub_pwr_2026_09",
            "CSE-POWER-01",
            PERIOD_START,
            PERIOD_END,
            NOW,
            "hash_manifest_pwr_2026_09",
            "builtin:splunk_es_v7",
            json.dumps({"tables_loaded": {"case_record": 120, "alert_record": 450}, "data_tier": "A"}),
            json.dumps({"mttd_seconds": 310, "mttr_seconds": 1820}),
            0.88,
            1,
            None,
        ),
        (
            "sub_bnk_2026_09",
            "CSE-BANK-02",
            PERIOD_START,
            PERIOD_END,
            NOW,
            "hash_manifest_bnk_2026_09",
            "builtin:qradar_v7_5",
            json.dumps({"tables_loaded": {"case_record": 84, "alert_record": 310}, "data_tier": "A"}),
            json.dumps({"mttd_seconds": 220, "mttr_seconds": 1450}),
            0.94,
            1,
            None,
        ),
        (
            "sub_tel_2026_09",
            "CSE-TELCO-01",
            PERIOD_START,
            PERIOD_END,
            NOW,
            "hash_manifest_tel_2026_09",
            "builtin:sentinel_v1",
            json.dumps({"tables_loaded": {"case_record": 210, "alert_record": 980}, "data_tier": "B"}),
            json.dumps({"mttd_seconds": 540, "mttr_seconds": 3200}),
            0.65,
            1,
            None,
        ),
        (
            "sub_trn_2026_09",
            "CSE-TRANS-01",
            PERIOD_START,
            PERIOD_END,
            NOW,
            "hash_manifest_trn_2026_09",
            "builtin:elastic_siem_v8",
            json.dumps({"tables_loaded": {"case_record": 95, "alert_record": 412}, "data_tier": "A"}),
            json.dumps({"mttd_seconds": 290, "mttr_seconds": 1640}),
            0.91,
            1,
            None,
        ),
    ]
    for sub in submissions:
        conn.execute(
            """
            INSERT OR REPLACE INTO submission
            (submission_id, entity_id, period_start, period_end, received_ts, manifest_hash,
             schema_profile, row_counts, declared_kpis, dq_score, version, approved_profile_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            sub,
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO submission_file
            (submission_id, safe_name, original_name, detected_format, n_bytes, content_hash, ordinal)
            VALUES (?, 'cases.csv', 'cases_2026_09.csv', 'csv', 245100, 'hash_file_cases', 1)
            """,
            (sub[0],),
        )

    conn.commit()

    # 10. Ledger Entries (Cryptographic Hash Chain via Ledger)
    print("==> Seeding append-only hash chain ledger via Ledger service...")
    from app.services.ledger import Ledger, LedgerAction
    ledger = Ledger()
    entry_count = conn.execute("SELECT COUNT(*) FROM ledger_entry").fetchone()[0]
    if entry_count == 0:
        ledger_events = [
            ("system.root", LedgerAction.RUN_COMPLETED, {"run_id": "run_2026_09_cohort", "entities": 5}, None),
            ("custodian.patel", LedgerAction.SUBMISSION_RECEIVED, {"submission_id": "sub_pwr_2026_09", "dq_score": 0.88}, "CSE-POWER-01"),
            ("supervisor.sharma", LedgerAction.REVIEW_PACK_CREATED, {"pack_id": "pack_pwr_01_sep26", "sampled_cases": 6}, "CSE-POWER-01"),
            ("examiner.singh", LedgerAction.VERDICT_RECORDED, {"case_id": "CASE-PWR-8801", "verdict": "confirmed"}, "CSE-POWER-01"),
        ]
        for actor, action, payload, entity_id in ledger_events:
            ledger.append(actor=actor, action=action, payload=payload, entity_id=entity_id)

    # 11. Populate DuckDB Evidence Store
    print("==> Populating DuckDB case_record and alert_record evidence...")
    with duck.writer() as dconn:
        # Case records
        cases = [
            ("CASE-PWR-8801", "CSE-POWER-01", "2026-09-12 14:02:10", "2026-09-12 14:04:32", 142, "analyst_p1", "analyst_p1", "human", "CRITICAL", "tier_1", "FALSE_POSITIVE", "auto_close_rule", "NOTE-PWR-01"),
            ("CASE-PWR-8802", "CSE-POWER-01", "2026-09-14 09:15:00", "2026-09-14 09:17:15", 135, "analyst_p2", "analyst_p2", "human", "CRITICAL", "tier_1", "BENIGN_ANOMALY", "operator_ack", "NOTE-PWR-02"),
            ("CASE-PWR-8805", "CSE-POWER-01", "2026-09-18 22:30:10", "2026-09-18 22:32:40", 150, "analyst_p1", "analyst_p1", "human", "HIGH", "tier_1", "FALSE_POSITIVE", "maintenance_suppress", "NOTE-PWR-03"),
            ("CASE-PWR-9104", "CSE-POWER-01", "2026-09-22 03:10:00", "2026-09-22 04:45:00", 5700, "analyst_p3", "analyst_p3", "human", "CRITICAL", "tier_2", "TRUE_POSITIVE", "quarantine_host", "NOTE-PWR-04"),
            ("CASE-TEL-4411", "CSE-TELCO-01", "2026-09-08 11:20:00", "2026-09-08 11:21:25", 85, "mssp_agent_4", "mssp_agent_4", "automation", "HIGH", "tier_1", "FALSE_POSITIVE", "template_macro", "NOTE-TEL-01"),
        ]
        for c in cases:
            dconn.execute(
                """
                INSERT INTO case_record
                (case_id, entity_id, created_at, closed_at, investigation_duration_sec,
                 analyst_pseudo, closed_by_pseudo, actor_type, severity_norm, escalation_level,
                 disposition, root_cause_action, note_ref)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                c,
            )

        # Alert records
        alerts = [
            ("ALT-PWR-101", "CSE-POWER-01", "2026-09-12 14:02:10", "2026-09-12 14:02:08", "Substation SCADA abnormal telemetry", "sev_1", "CRITICAL", "RULE-OT-01", "OT_IDS", "TA0001", "T1078", "192.168.10.4", "ASSET-PWR-SUB01", 5, "closed", True, "analyst_p1", "CASE-PWR-8801"),
            ("ALT-PWR-102", "CSE-POWER-01", "2026-09-14 09:15:00", "2026-09-14 09:14:55", "Protective relay trip signal anomaly", "sev_1", "CRITICAL", "RULE-OT-03", "OT_IDS", "TA0040", "T1529", "192.168.10.12", "ASSET-PWR-SUB02", 5, "closed", True, "analyst_p2", "CASE-PWR-8802"),
            ("ALT-TEL-301", "CSE-TELCO-01", "2026-09-08 11:20:00", "2026-09-08 11:19:50", "Core BGP gateway session drop", "sev_2", "HIGH", "RULE-BGP-09", "NET_FLOW", "TA0008", "T1090", "10.0.0.1", "ASSET-TEL-GW01", 4, "closed", True, "mssp_agent_4", "CASE-TEL-4411"),
        ]
        for a in alerts:
            dconn.execute(
                """
                INSERT INTO alert_record
                (alert_id, entity_id, timestamp, event_ts, title, severity_raw, severity_norm,
                 rule_id, source_tool, mitre_tactic, mitre_technique_id, source_ip_pseudo,
                 destination_asset_id, asset_criticality, status, auto_closed_flag,
                 closed_by_pseudo, case_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                a,
            )

    print("\n✅ Successfully seeded Orion SAT-SA database stores with realistic supervisory data!")
    print(f"   Entities: {len(entities)}")
    print(f"   Findings: {len(findings)}")
    print(f"   Review Pack Items: {len(pack_items)}")
    print(f"   Submissions: {len(submissions)}")
    print(f"   Ledger Chain Head: {ledger.head()[:16]}...")


if __name__ == "__main__":
    seed()
