"""Scenario-based mutations and independently authored truth labels."""
from __future__ import annotations
from dataclasses import dataclass
from copy import deepcopy
import hashlib

@dataclass(frozen=True)
class Injection:
    family:str; entity_id:str; dose:float; hard_negative:bool=False

def inject(dataset, *, seed:int=42, family:str="silent_critical_asset", entity_id:str|None=None, dose:float=.75):
    """Apply a dose-scaled scenario and return (dataset, labels)."""
    d=deepcopy(dataset); eid=entity_id or d.entities[0]["entity_id"]; dose=max(0.,min(1.,dose)); labels=[]
    labels.append({"entity_id":eid,"weakness_family":family,"dose":dose,"hard_negative":False,"expected_detection_level":"high" if dose>=.75 else "medium"})
    if family=="silent_critical_asset":
        rows=[r for r in d.telemetry if r["entity_id"]==eid and any(a["asset_id"]==r["asset_id"] and a["criticality"]>=4 for a in d.assets)]
        for r in rows[:max(1,int(len(rows)*dose))]: r["activity"]=0
    elif family in {"rapid_closure","SLA_bunching","backlog_washing","timestamp_fabrication"}:
        for r in d.cases:
            if r["entity_id"]==eid and int(r["case_id"].split('-')[-1]) % max(1,int(4/max(dose,0.01))) == 0:
                if family=="SLA_bunching": r["closed_at"]=r["created_at"]
                elif family=="backlog_washing": r["closed_at"]=r["created_at"] if dose>.5 else r["closed_at"]
                elif family=="timestamp_fabrication": r["closed_at"]="2099-01-01T00:00:00+00:00"
                else: r["closed_at"]=r["created_at"]
    elif family=="template_monoculture":
        for r in d.notes:
            if r["entity_id"]==eid and int(hashlib.sha256(r["row_id"].encode()).hexdigest()[:8],16)%100 < int(100*dose): r["text"]="Investigation completed and resolved per playbook."
    elif family in {"orphan_records","missing_alert_category","critical_without_escalation","id_gaps","retroactive_edits"}:
        for r in d.alerts:
            if r["entity_id"]==eid and int(hashlib.sha256(r["row_id"].encode()).hexdigest()[:8],16)%100 < int(100*dose):
                if family in {"orphan_records","critical_without_escalation"}: r["case_id"]="MISSING-CASE"
                elif family=="id_gaps": r["alert_id"]="GAP-"+r["alert_id"]
                else: r["timestamp"]="2020-01-01T00:00:00+00:00"
    elif family in {"low_volume","common_shock_nonresponse"}:
        d.alerts[:]=[r for i,r in enumerate(d.alerts) if r["entity_id"]!=eid or i%max(1,int(2+8*dose))==0]
    elif family=="metric_gaming":
        for r in d.cases:
            if r["entity_id"]==eid: r["closed_at"]=r["created_at"] if dose>.25 else r["closed_at"]
    return d, labels

def hard_negative(dataset, scenario:str="legitimate_fast_closure"):
    d=deepcopy(dataset); eid=d.entities[-1]["entity_id"]
    if scenario=="legitimate_fast_closure":
        for r in d.cases:
            if r["entity_id"]==eid: r["closed_at"]=r["created_at"]
    elif scenario in {"legitimate_mssp_template", "legitimate_automation"}:
        for r in d.notes:
            if r["entity_id"] == eid:
                r["text"] = "Standard managed-service investigation completed under approved automation."
                r["legitimate_template"] = True
        for r in d.cases:
            if r["entity_id"] == eid:
                r["actor_type"] = "automation"
    elif scenario == "maintenance_window_silence":
        for r in d.telemetry:
            if r["entity_id"] == eid and r["asset_id"].endswith("A1"):
                r["activity"] = 0
                r["maintenance"] = True
    elif scenario == "legitimately_quiet_small_cse":
        for r in d.entities:
            if r["entity_id"] == eid:
                r["archetype"] = "small-quiet"
        d.alerts[:] = [r for r in d.alerts if r["entity_id"] != eid or int(r["timestamp"][8:10]) % 3 == 0]
        for r in d.telemetry:
            if r["entity_id"] == eid:
                r["legitimate_quiet"] = True
    elif scenario == "legitimate_workload_difference":
        for r in d.alerts:
            if r["entity_id"] == eid:
                r["severity"] = "LOW"
    return d,[{"entity_id":eid,"weakness_family":scenario,"dose":0.0,"hard_negative":True,"expected_detection_level":"none"}]
