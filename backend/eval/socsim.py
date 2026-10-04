"""Deterministic, offline SOCSim fixture generator."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
import random

ARCHETYPES=("mature","average","understaffed","MSSP","metric-gaming","blind-spot","data-fabricating","small-quiet")

@dataclass
class Dataset:
    entities:list[dict]; alerts:list[dict]; cases:list[dict]; telemetry:list[dict]; assets:list[dict]; notes:list[dict]; truth:list[dict]

def simulate(seed:int=42, entity_count:int=24, days:int=30) -> Dataset:
    chunks = list(iter_simulate(seed=seed, entity_count=entity_count, days=days, entity_chunk_size=max(1, entity_count)))
    return _merge(chunks)


def _merge(chunks):
    merged=Dataset([],[],[],[],[],[],[])
    for chunk in chunks:
        merged.entities.extend(chunk.entities); merged.alerts.extend(chunk.alerts); merged.cases.extend(chunk.cases)
        merged.telemetry.extend(chunk.telemetry); merged.assets.extend(chunk.assets); merged.notes.extend(chunk.notes); merged.truth.extend(chunk.truth)
    return merged


def iter_simulate(seed:int=42, entity_count:int=24, days:int=30, entity_chunk_size:int=1):
    """Yield deterministic entity chunks without retaining the full dataset.

    The RNG is shared across chunks, so concatenating chunks is equivalent to
    ``simulate`` for the same seed and configuration.
    """
    rng=random.Random(seed); start=datetime(2026,1,1,tzinfo=timezone.utc)
    for begin in range(0, entity_count, max(1, entity_chunk_size)):
        entities=[]; alerts=[]; cases=[]; telemetry=[]; assets=[]; notes=[]
        for ei in range(begin, min(entity_count, begin+max(1, entity_chunk_size))):
            arch=ARCHETYPES[ei%len(ARCHETYPES)]; eid=f"CSE-{ei+1:03d}"; entities.append({"entity_id":eid,"archetype":arch,"sector":"energy" if ei%2==0 else "finance"})
            nassets=2 if arch=="small-quiet" else 5
            own_assets=[]
            for ai in range(nassets):
                asset={"entity_id":eid,"asset_id":f"{eid}-A{ai+1}","criticality":4 if ai==0 else (2+ai%2)}; assets.append(asset); own_assets.append(asset)
            volume=2 if arch=="small-quiet" else (18 if arch in {"mature","MSSP"} else 10)
            for day in range(days):
                weekday=(start+timedelta(days=day)).weekday(); factor=0.35 if weekday>=5 else 1.0
                for asset in own_assets:
                    telemetry.append({"entity_id":eid,"asset_id":asset["asset_id"],"period":str(day),"activity":0 if (arch=="blind-spot" and asset["criticality"]==4) else int(rng.random()*factor*3)})
                for j in range(max(0,int(rng.gauss(volume*factor, max(1,volume*.2))))):
                    aid=f"{eid}-AL-{day:03d}-{j:03d}"; ts=start+timedelta(days=day,hours=rng.randrange(8,22),minutes=rng.randrange(60)); sev=rng.choices(["LOW","MEDIUM","HIGH","CRITICAL"],[.55,.25,.15,.05])[0]
                    cid=f"{eid}-CA-{day:03d}-{j:03d}"; close=ts+timedelta(minutes=max(2,int(rng.expovariate(1/240))))
                    if arch=="metric-gaming": close=ts+timedelta(minutes=rng.randint(55,89))
                    alerts.append({"row_id":aid,"entity_id":eid,"alert_id":aid,"case_id":cid,"severity":sev,"timestamp":ts.isoformat()})
                    cases.append({"row_id":cid,"entity_id":eid,"case_id":cid,"created_at":ts.isoformat(),"closed_at":close.isoformat(),"severity":sev,"analyst_id":f"{eid}-AN-{j%3}"})
                    text="Investigation completed and resolved per playbook." if arch in {"MSSP","metric-gaming"} else f"Reviewed {sev.lower()} alert for {cid}; validated context and response path {j%5}."
                    notes.append({"row_id":f"N-{cid}","entity_id":eid,"analyst_id":f"{eid}-AN-{j%3}","case_id":cid,"severity":sev,"text":text,"legitimate_template":arch=="MSSP"})
        yield Dataset(entities,alerts,cases,telemetry,assets,notes,[])
