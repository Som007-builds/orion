"""Authoritative evaluation ownership map for injected supervisory weaknesses."""
from __future__ import annotations

OWNERSHIP = {
    "rapid_closure": {"owner": "Dev2 RulesEngine", "component": "EG-01", "mechanism": "case closure timing against SLA", "evidence": ["case.created_at", "case.closed_at"], "output": "IndicatorResult", "validation": "covered by RulesEngine; no standalone Dev3 detector"},
    "SLA_bunching": {"owner": "Dev2 RulesEngine", "component": "EG-01", "mechanism": "closure-time concentration", "evidence": ["case.created_at", "case.closed_at"], "output": "IndicatorResult", "validation": "covered by RulesEngine; no standalone Dev3 detector"},
    "backlog_washing": {"owner": "Dev2 RulesEngine", "component": "EG-01", "mechanism": "backlog/closure trend", "evidence": ["case.created_at", "case.closed_at"], "output": "IndicatorResult", "validation": "RulesEngine-owned"},
    "timestamp_fabrication": {"owner": "Dev2 RulesEngine", "component": "EG-11", "mechanism": "temporal/integrity inconsistency", "evidence": ["alert.timestamp", "case timestamps"], "output": "IndicatorResult", "validation": "RulesEngine-owned"},
    "critical_without_escalation": {"owner": "Dev2 RulesEngine", "component": "EG-06", "mechanism": "critical case without escalation", "evidence": ["alert.case_id", "escalation records"], "output": "IndicatorResult", "validation": "RulesEngine-owned"},
    "orphan_records": {"owner": "Dev3 negative-space", "component": "NS-04", "mechanism": "referential integrity", "evidence": ["alert_record", "case_record", "case_event"], "output": "IndicatorResult", "validation": "tested"},
    "id_gaps": {"owner": "Dev2 RulesEngine", "component": "EG-11", "mechanism": "identifier/integrity consistency", "evidence": ["alert.alert_id"], "output": "IndicatorResult", "validation": "RulesEngine-owned"},
    "retroactive_edits": {"owner": "Dev2 RulesEngine", "component": "EG-11", "mechanism": "retroactive timestamp/edit integrity", "evidence": ["alert.timestamp", "audit history"], "output": "IndicatorResult", "validation": "RulesEngine-owned"},
    "low_volume": {"owner": "Dev3 negative-space", "component": "NS-05", "mechanism": "peer-relative activity baseline", "evidence": ["activity", "peer baseline"], "output": "IndicatorResult", "validation": "tested"},
    "common_shock_nonresponse": {"owner": "Dev3 negative-space", "component": "NS-06", "mechanism": "peer shock and target response", "evidence": ["peer activity history"], "output": "IndicatorResult", "validation": "tested"},
    "metric_gaming": {"owner": "Composite", "component": "EG-01 + NS-05/NLP", "mechanism": "cross-signal inconsistency", "evidence": ["case timing", "activity", "notes"], "output": "IndicatorResult/ModelOutput", "validation": "no standalone detector; evaluate through owning signals"},
    "template_monoculture": {"owner": "Dev3 NLP", "component": "NLP auditor", "mechanism": "investigation-note concentration", "evidence": ["note.text", "note.row_id"], "output": "ModelOutput", "validation": "tested"},
    "silent_critical_asset": {"owner": "Dev3 negative-space", "component": "NS-01", "mechanism": "sustained critical-asset silence", "evidence": ["telemetry_daily", "asset.criticality"], "output": "IndicatorResult", "validation": "tested"},
    "missing_alert_category": {"owner": "Dev3 negative-space", "component": "NS-02", "mechanism": "persistent category absence", "evidence": ["alert.category", "period", "count"], "output": "IndicatorResult", "validation": "tested"},
}

def ownership_rows():
    return [{"injection": key, **value} for key, value in OWNERSHIP.items()]
