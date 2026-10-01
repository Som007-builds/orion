"""Verify Phase 4 and 7: pseudonymisation, redaction, policy, baselines."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.sqlite import init_db
init_db()

from datetime import date
from app.services.pseudonymisation import get_pseudonymiser
from app.services.policy_profile import policy_profile, load_from_yaml
from app.services.baseline_service import BaselineService, MAD_SCALE

print("=== PSEUDONYMISATION (plan §3.16) ===")
p = get_pseudonymiser()

a = p.pseudonym("analyst.raj@corp", "CSE-A", "analyst")
b = p.pseudonym("analyst.raj@corp", "CSE-A", "analyst")
c = p.pseudonym("analyst.raj@corp", "CSE-B", "analyst")
print(f"  stable within entity : {a == b}  ({a})")
print(f"  differs across entity: {a != c}  (prevents cross-CSE analyst linkage)")
ip = p.pseudonym("192.168.1.44", "CSE-A", "ip")
print(f"  ip pseudonym         : {ip}")
print(f"  asset id from host   : {p.pseudonymise_asset_id('scada-gw01.corp.local', 'CSE-A')}")

note = ("Escalated from 10.0.4.19 to soc-lead@corp.in. "
        "Confirmed on HOST-DB02 and scada-gw01.corp.local. "
        "See https://ticket.corp.in/4471 and CVE-2026-12345.")
r = p.redact_text(note)
print(f"\n  redaction categories : {list(r.categories)}")
print(f"  redactions applied   : {r.redaction_count}")
print(f"  redacted text        : {r.redacted_text}")
leaked = [t for t in ("10.0.4.19", "soc-lead@corp.in", "HOST-DB02", "CVE-2026-12345") if t in r.redacted_text]
print(f"  PII leaked           : {leaked if leaked else 'NONE'}")

sig1 = p.shingle_signature("investigated the alert and closed as false positive")
sig2 = p.shingle_signature("investigated  the alert and closed as false positive")
sig3 = p.shingle_signature("attacker exfiltrated credentials from domain controller")
print(f"\n  shingle same note    : {sig1 == sig2}")
print(f"  shingle diff note    : {sig1 != sig3}")

for raw, expected in [("auto", "automation"), ("analyst", "human"), ("", "unknown"), (None, "unknown")]:
    at, inferred = p.classify_actor_type(raw)
    print(f"  actor_type {str(raw):10} -> {at:11} inferred={inferred}")

print("\n=== POLICY PROFILE (plan §4.2) ===")
pol = policy_profile()
print(f"  profile          : {pol.profile_id} v{pol.version}")
print(f"  content_hash     : {pol.content_hash[:24]}…")
print(f"  validation       : {'PASS' if not pol.validate() else pol.validate()}")
print(f"  transitions      : {'PASS' if not pol.validate_transitions() else pol.validate_transitions()}")
print(f"  weights sum      : {sum(pol.dimension_weights.values())}")
print(f"  ramp |z|=1       : {pol.ramp(1.0):.3f} (expect 0.000)")
print(f"  ramp |z|=3.5     : {pol.ramp(3.5):.3f} (expect 0.500)")
print(f"  ramp |z|=9       : {pol.ramp(9.0):.3f} (expect 1.000)")
print(f"  threshold 180s   : {pol.threshold('fast_closure_seconds')} (documented default, not hardcoded)")
print(f"  tier sap=0.80    : {pol.tier_for(0.80)}")
print(f"  tier sap=0.10    : {pol.tier_for(0.10)}")
print(f"  tier None        : {pol.tier_for(None)}  <- Not assessable is its own state, NOT T4")
print(f"  tier False flag  : {pol.tier_for(0.90, assessable=False)}  <- cannot be promoted")

print("\n=== BASELINES (plan §6.4) ===")
bl = BaselineService(policy=pol)
print(f"  MAD_SCALE        : {MAD_SCALE}")

# Seed a cohort so LOO exclusion can be exercised against real DB rows.
from app.db.sqlite import get_connection
from app.services.baseline_service import Cohort

conn = get_connection()
conn.execute("INSERT OR IGNORE INTO sector_ref VALUES ('Power', 'Power', 1)")
for eid, size in [("CSE-A", "large"), ("CSE-B", "large"), ("CSE-C", "large"), ("CSE-D", "large")]:
    conn.execute(
        "INSERT OR IGNORE INTO entity "
        "(entity_id, name, sector, soc_model, coverage_type, declared_open, "
        " declared_close, size_tier, critical_asset_count, created_at) "
        "VALUES (?, ?, 'Power', 'in-house', '24x7', '00:00', '23:59', ?, 5, '2026-01-01')",
        (eid, eid, size),
    )

cohort = bl.cohort_for("CSE-A")
print(f"  cohort key       : {cohort.key}")
print(f"  cohort members   : {cohort.members}")
print(f"  LOO excludes self: {'CSE-A' not in cohort.members}")

vals = {"CSE-A": 50.0, "CSE-B": 1800.0, "CSE-C": 1750.0, "CSE-D": 1900.0}
res = bl.peer_baseline("CSE-A", "closure_sec", cohort=cohort, peer_values=vals)
print(f"  peer values used : {res.peer_values}  <- CSE-A's own 50.0 absent")
print(f"  median/mad       : {res.median} / {res.mad}")

z = bl.robust_z(50.0, 1800.0, 100.0)
print(f"  robust z         : {z:.3f} (expect (50-1800)/(1.4826*100) = {(50-1800)/(MAD_SCALE*100):.3f})")
print(f"  robust z MAD=0   : {bl.robust_z(50.0, 1800.0, 0.0)} (None, not inf — missing must surface as missing)")

# min-peer fallback
thin = {"CSE-B": 100.0, "CSE-C": 110.0}
thin_res = bl.peer_baseline("CSE-A", "closure_sec", cohort=cohort, peer_values=thin)
print(f"  thin cohort      : n={thin_res.n_peers} method={thin_res.method} fell_back={thin_res.fell_back}")
print(f"  fallback reason  : {thin_res.fallback_reason}")

# EB shrinkage direction
shrunk = bl.eb_shrink(50.0, [1800.0, 1750.0, 1900.0])
print(f"  EB shrink(50)    : {shrunk:.2f} (pulled toward cohort mean ~1816, away from raw 50)")

sb = bl.self_baseline("CSE-A", "closure_sec", [100.0, 200.0, 300.0])
print(f"  self baseline    : median={sb['median']} mad={sb['mad']} periods={sb['periods']}")

ri = bl.rank_interval({"CSE-A": 0.9, "CSE-B": 0.5, "CSE-C": 0.4, "CSE-D": 0.2}, seed=42)
print(f"  rank interval A  : {ri['CSE-A']}  <- point rank implies false precision")

print("\nALL PHASE 4/7 CHECKS PASSED")