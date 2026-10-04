"""Pure negative-space detectors over row dictionaries.

The functions intentionally accept ordinary mappings so callers can pass a
DuckDB/Polars snapshot without giving this layer a database dependency.
"""
from __future__ import annotations
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
import math
from statistics import median
from datetime import datetime, time
from app.schemas.indicator import (Assessability, Baseline, ConfidenceBreakdown,
    Dimension, FindingSource, IndicatorResult, Period)

SRC = FindingSource.NEGATIVE_SPACE

def _period(start: date, end: date) -> Period: return Period(start=start, end=end)
def _na(i, entity, start, end, missing, dim, note):
    return IndicatorResult.not_assessable(i, entity, _period(start,end), list(missing), SRC, dim, note)
def _result(i, entity, start, end, value, unit, n, evidence, required, dim, family, baseline=None, effect=None, benign=()):
    trust = 1.0 if n else 0.0
    return IndicatorResult(indicator_id=i, entity_id=entity, period=_period(start,end), value=float(value) if math.isfinite(float(value)) else None, value_units=unit, n=n, confidence=min(1.0,n/5)*trust, confidence_breakdown=ConfidenceBreakdown(n_term=min(1.0,n/5), assessability_term=1.0, data_trust_term=trust), peer_baseline=baseline, effect_size=effect, evidence_row_ids=list(evidence), evidence_query=f"negative_space:{i}:{entity}:{start}:{end}", required_fields=list(required), assessability=Assessability.ASSESSABLE, family=family, primary_dimension=dim, source=SRC, benign_explanations=list(benign))

def ns01_silent_critical_assets(evidence: Iterable[Mapping[str, object]], policy=None, baseline=None, *, entity_id: str = "", period_start: date | None = None, period_end: date | None = None) -> list[IndicatorResult]:
    """Detect critical assets with a sustained zero run; maintenance is a hard negative."""
    rows=list(evidence); s=period_start or date.min; e=period_end or date.max
    if not rows: return [_na("NS-01",entity_id,s,e,["telemetry_daily","asset.criticality"],Dimension.TD,"No telemetry rows supplied")]
    if not all("asset_id" in r and "criticality" in r for r in rows): return [_na("NS-01",entity_id,s,e,["telemetry_daily.asset_id/criticality"],Dimension.TD,"Asset identity and criticality are required")]
    valid_rows=[r for r in rows if "activity" in r and r.get("activity") is not None]
    if len(valid_rows) < 3:
        return [_na("NS-01",entity_id,s,e,["telemetry_daily.activity"],Dimension.TD,"Insufficient valid telemetry after missingness")]
    rows=valid_rows
    groups=defaultdict(list)
    for r in rows: groups[str(r["asset_id"])].append(r)
    out=[]
    for aid, rs in sorted(groups.items()):
        assessable=[r for r in rs if not bool(r.get("maintenance", False)) and not bool(r.get("legitimate_quiet", False))]
        silence=sum(1 for r in assessable if float(r["activity"] or 0)==0); critical=max(int(r["criticality"]) for r in rs)
        expected=sum(float(r["activity"] or 0) for r in assessable)/max(1,len(assessable));
        if critical >= 3 and silence >= max(3, len(assessable)//2) and expected > 0:
            out.append(_result("NS-01",entity_id,s,e,min(1.0,silence/max(1,len(assessable))),"silent_fraction",len(assessable),[str(r.get("row_id",aid)) for r in assessable if float(r["activity"] or 0)==0],["telemetry_daily","asset.criticality"],Dimension.TD,"coverage",benign=("Scheduled maintenance or decommissioning can create legitimate silence",)))
    return out

def ns02_absent_alert_categories(evidence: Iterable[Mapping[str, object]], policy=None, baseline=None, *, entity_id: str = "", period_start: date | None = None, period_end: date | None = None) -> list[IndicatorResult]:
    """Find categories absent for persistent periods relative to peer activity."""
    rows=list(evidence); s=period_start or date.min; e=period_end or date.max
    if not rows or not all(k in r for r in rows for k in ("category","period","count")): return [_na("NS-02",entity_id,s,e,["alert_record.category/period/count"],Dimension.TD,"Category history is unavailable")]
    by=defaultdict(list)
    for r in rows: by[str(r["category"])].append(float(r["count"] or 0))
    if any(len(vals) < 2 for vals in by.values()):
        return [_na("NS-02",entity_id,s,e,["alert category baseline"],Dimension.TD,"Insufficient category history for persistence")]
    out=[]
    for cat, vals in sorted(by.items()):
        if len(vals)>=2 and vals[-1]==0 and vals[-2]==0 and median(vals[:-2])>0:
            out.append(_result("NS-02",entity_id,s,e,1.0,"persistent_absence",len(vals),[str(r.get("row_id",cat)) for r in rows if r["category"]==cat],["alert_record.category/period/count"],Dimension.TD,"coverage",benign=("A retired rule or changed tooling can legitimately remove a category",)))
    return out

def ns04_orphan_records(evidence: Iterable[Mapping[str, object]], policy=None, baseline=None, *, entity_id: str = "", period_start: date | None = None, period_end: date | None = None) -> list[IndicatorResult]:
    """Deterministically report broken alert/case/event references."""
    rows=list(evidence); s=period_start or date.min; e=period_end or date.max
    if not rows: return [_na("NS-04",entity_id,s,e,["alert_record","case_record","case_event"],Dimension.GOV,"No reference data supplied")]
    cases={str(r["case_id"]) for r in rows if r.get("record_type")=="case" and r.get("case_id") is not None}; bad=[]
    for r in rows:
        ref=r.get("case_id")
        if ref is not None and r.get("record_type") in {"alert","event","escalation"} and str(ref) not in cases: bad.append(str(r.get("row_id",ref)))
    if not bad: return []
    return [_result("NS-04",entity_id,s,e,len(bad)/max(1,len(rows)),"orphan_fraction",len(rows),bad,["alert_record","case_record","case_event"],Dimension.GOV,"referential_integrity")]

def ns05_implausibly_low_activity(evidence: Iterable[Mapping[str, object]], policy=None, baseline=None, *, entity_id: str = "", period_start: date | None = None, period_end: date | None = None) -> list[IndicatorResult]:
    """Flag low activity only when a peer/context baseline is supplied."""
    rows=list(evidence); s=period_start or date.min; e=period_end or date.max
    if not rows or not all("activity" in r for r in rows): return [_na("NS-05",entity_id,s,e,["activity","peer baseline"],Dimension.SO,"Activity or peer context is unavailable")]
    vals=[float(r["activity"]) for r in rows]; peer=float(baseline if isinstance(baseline,(int,float)) else median(vals))
    current=vals[-1]
    if peer <= 0 or current >= peer*0.25: return []
    return [_result("NS-05",entity_id,s,e,current/peer,"relative_activity",len(vals),[str(r.get("row_id",i)) for i,r in enumerate(rows)],["activity","peer baseline"],Dimension.SO,"operational_activity",effect=(current-peer)/max(peer,1e-9),benign=("A seasonal workload or scope change may explain low activity",))]

def ns03_temporal_inactivity(evidence: Iterable[Mapping[str, object]], policy=None, baseline=None, *, entity_id: str = "", period_start: date | None = None, period_end: date | None = None, soc_hours: Mapping[str, object] | None = None, holidays: Sequence[date] = ()) -> IndicatorResult:
    """Measure unexplained inactivity during declared operating hours.

    Weekends/holidays and rows explicitly marked maintenance are excluded from
    the adverse denominator. Missing declared hours or telemetry is a real
    assessability gap, never a zero activity result.
    """
    s=period_start or date.min; e=period_end or date.max; rows=list(evidence)
    if not soc_hours: return _na("NS-03",entity_id,s,e,["entity.soc_hours"],Dimension.SO,"Declared SOC operating hours are required")
    if not rows or not all("timestamp" in r and "activity" in r for r in rows): return _na("NS-03",entity_id,s,e,["telemetry_daily.timestamp/activity"],Dimension.SO,"Telemetry timestamps and activity are required")
    try: op, close = time.fromisoformat(str(soc_hours["declared_open"])), time.fromisoformat(str(soc_hours["declared_close"]))
    except (KeyError, ValueError): return _na("NS-03",entity_id,s,e,["entity.soc_hours.declared_open/close"],Dimension.SO,"Declared hours are invalid")
    holiday_set=set(holidays); expected=[]; unexplained=[]
    for r in rows:
        try: dt=datetime.fromisoformat(str(r["timestamp"]).replace("Z","+00:00"))
        except ValueError: continue
        if dt.date() in holiday_set or dt.weekday()>=5: continue
        in_hours=(op<=dt.time()<close) if op<close else (dt.time()>=op or dt.time()<close)
        if in_hours:
            expected.append(r)
            if float(r["activity"] or 0)<=0 and not bool(r.get("maintenance")): unexplained.append(r)
    if not expected: return _na("NS-03",entity_id,s,e,["assessable operating-hour telemetry"],Dimension.SO,"No operating-hour observations are available")
    return _result("NS-03",entity_id,s,e,len(unexplained)/len(expected),"unexplained_inactivity_fraction",len(expected),[str(r.get("row_id",i)) for i,r in enumerate(unexplained)], ["entity.soc_hours","telemetry_daily"],Dimension.SO,"coverage",benign=("Indian public holidays and declared maintenance are excluded",))

def ns06_common_shock_nonresponse(evidence: Iterable[Mapping[str, object]], policy=None, baseline=None, *, entity_id: str = "", period_start: date | None = None, period_end: date | None = None, min_peers: int = 3) -> IndicatorResult:
    """Flag a target that does not respond to a statistically meaningful peer shock."""
    s=period_start or date.min; e=period_end or date.max; rows=list(evidence)
    if not rows or not all(k in r for r in rows for k in ("entity_id","period","activity")):
        return _na("NS-06",entity_id,s,e,["peer activity history"],Dimension.TD,"Peer activity history is required")
    periods=defaultdict(dict)
    for r in rows: periods[str(r["period"])][str(r["entity_id"])]=float(r["activity"] or 0)
    candidates=[]; evidence_ids=[]
    for per, vals in sorted(periods.items()):
        peers=[v for k,v in vals.items() if k!=entity_id]
        if len(peers)<min_peers: continue
        prior=[]
        for p2,v2 in periods.items():
            if p2<per: prior.extend([x for k,x in v2.items() if k!=entity_id])
        base=median(prior) if prior else median(peers)
        peer_avg=sum(peers)/len(peers); target=vals.get(entity_id)
        if target is not None and peer_avg>=max(1.0,base*1.5) and target<=max(0.0,base*.5):
            candidates.append((peer_avg-target)/max(peer_avg,1.0)); evidence_ids.extend(str(r.get("row_id",per)) for r in rows if str(r["period"])==per)
    if not candidates: return _result("NS-06",entity_id,s,e,0.0,"shock_nonresponse",len(rows),[],["peer activity history"],Dimension.TD,"common_shock",benign=("Low-volume entities are not flagged without a measured peer shock",))
    value=sum(candidates)/len(candidates)
    return _result("NS-06",entity_id,s,e,value,"mean_nonresponse_gap",len(candidates),evidence_ids,["peer activity history"],Dimension.TD,"common_shock",baseline=Baseline(median=1.0,mad=None,percentile=None,n_peers=min_peers,method="loo_median_mad",cohort="peer_activity"),effect=value,benign=("A peer shock may reflect sector-wide reporting or tooling changes",))
