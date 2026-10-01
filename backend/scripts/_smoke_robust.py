import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db.sqlite import init_db, get_connection
init_db()
from app.services.baseline_service import BaselineService, Cohort

conn = get_connection()
conn.execute("INSERT OR IGNORE INTO sector_ref VALUES ('Fin','Financial Services',1)")
ids = [f"CSE-P{i:02d}" for i in range(1,13)]
for i, eid in enumerate(ids):
    conn.execute(
        "INSERT OR IGNORE INTO entity (entity_id,name,sector,soc_model,coverage_type,"
        "declared_open,declared_close,size_tier,critical_asset_count,created_at)"
        " VALUES (?,?,'Fin','in-house','24x7','00:00','23:59','large',10,'2026-01-01')",
        (eid, eid))

bl = BaselineService()
cohort = bl.cohort_for(ids[0])
print("cohort:", cohort.key)
print("n_peers (excl self):", cohort.n_peers, ">= min_peers 8 ->", cohort.n_peers >= 8)

# one entity is a wild outlier; it must not drag the baseline
vals = {e: 1800.0 for e in cohort.members}
vals[cohort.members[0]] = 20.0
res = bl.peer_baseline(ids[0], "closure_sec", cohort=cohort, peer_values=vals)
print(f"method            : {res.method}")
print(f"fell_back         : {res.fell_back}")
print(f"median            : {res.median}   <- robust to the 20.0 outlier")
print(f"mad               : {res.mad}")
z = bl.effect_size(20.0, res)
print(f"effect_size(20)   : {z:.3f}  <- outlier still detected against a clean baseline")

import statistics
print(f"mean would be     : {statistics.fmean(vals.values()):.1f}  <- a mean baseline would have collapsed to ~1655")
