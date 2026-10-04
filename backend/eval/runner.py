"""End-to-end reproducible validation command."""
from __future__ import annotations
import argparse,csv,json
from pathlib import Path
from .socsim import simulate
from .injection import inject,hard_negative
from .metrics import precision_recall,ndcg
from app.ml.nlp_auditor import audit_notes

def run(seed=42,out_dir="backend/eval/out"):
    base=simulate(seed=seed); injected,truth=inject(base,seed=seed,family="template_monoculture",dose=.75); neg,nt=hard_negative(base)
    results=audit_notes(injected.notes); scores=[(r.entity_id,r.monoculture_index) for r in results]
    positives=[x["entity_id"] for x in truth]; metrics=precision_recall(scores,positives); metrics["ndcg"]=ndcg(scores,positives)
    p=Path(out_dir); p.mkdir(parents=True,exist_ok=True)
    for name,rows in (("entity_truth.csv",truth+nt),("case_truth.csv",[])):
        with (p/name).open("w",newline="") as f:
            if rows:
                w=csv.DictWriter(f,fieldnames=sorted(rows[0])); w.writeheader(); w.writerows(rows)
    report={"seed":seed,"entities":len(base.entities),"alerts":len(base.alerts),"cases":len(base.cases),"hard_negatives":len(nt),"injections":truth,"detector":"nlp_auditor","metrics":metrics,"results":[r.__dict__ for r in results]}
    (p/"validation_summary.json").write_text(json.dumps(report,indent=2,sort_keys=True)); return report
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--seed",type=int,default=42); ap.add_argument("--out",default="backend/eval/out"); a=ap.parse_args(); print(json.dumps(run(a.seed,a.out),indent=2))
if __name__=="__main__": main()
