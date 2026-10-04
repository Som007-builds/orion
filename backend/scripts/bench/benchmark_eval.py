"""Measured synthetic benchmark; no numbers are embedded."""
from __future__ import annotations
import argparse,json,time,platform
from pathlib import Path
from backend.eval.socsim import simulate
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--entities",type=int,default=24); ap.add_argument("--days",type=int,default=30); ap.add_argument("--seed",type=int,default=42); ap.add_argument("--out",default="backend/eval/out/benchmark.json"); a=ap.parse_args()
    t=time.perf_counter(); d=simulate(a.seed,a.entities,a.days); elapsed=time.perf_counter()-t
    out={"seed":a.seed,"entities":a.entities,"days":a.days,"alerts":len(d.alerts),"cases":len(d.cases),"generation_seconds":elapsed,"rows_per_second":(len(d.alerts)+len(d.cases))/max(elapsed,1e-12),"python":platform.python_version(),"platform":platform.platform()}
    Path(a.out).parent.mkdir(parents=True,exist_ok=True); Path(a.out).write_text(json.dumps(out,indent=2,sort_keys=True)); print(json.dumps(out,indent=2))
if __name__=="__main__": main()
