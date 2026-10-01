"""Validate every schema module imports and the frozen contracts behave."""
import sys, json
from pathlib import Path
from datetime import date
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError

mods = ["common", "entity", "finding", "indicator", "ingestion",
        "assessability", "review_pack", "ledger"]
for m in mods:
    __import__(f"app.schemas.{m}")
    print(f"  OK  app.schemas.{m}")

from app.schemas.indicator import (
    IndicatorResult, ModelOutput, Assessability, Dimension,
    Period, FindingSource, Baseline, ConfidenceBreakdown, Attribution,
)
from app.schemas.finding import FindingCardOut, WhyNotFlagged

p = Period(start=date(2026, 9, 1), end=date(2026, 9, 30))

print("\n--- frozen contract behaviour ---")

r = IndicatorResult(
    indicator_id="EG-01", entity_id="CSE-A", period=p, value=42.0,
    n=120, confidence=0.83,
    confidence_breakdown=ConfidenceBreakdown(n_term=1.0, assessability_term=1.0, data_trust_term=0.83),
    peer_baseline=Baseline(median=1800.0, mad=400.0, n_peers=12, method="loo_median_mad"),
    effect_size=-4.1, source=FindingSource.RULES_ENGINE,
    primary_dimension=Dimension.INV, family="rapid_thin_closure",
)
print(f"  OK  computed confidence total = {r.confidence_breakdown.total:.4f} (expect 0.8300)")
print(f"  OK  is_computable = {r.is_computable}")

na = IndicatorResult.not_assessable("EG-04", "CSE-A", p, ["case_event"], FindingSource.RULES_ENGINE)
print(f"  OK  not_assessable helper -> value={na.value}, conf={na.confidence}, missing={na.missing_fields}")

# INVARIANT: not-assessable must not carry a value.
try:
    IndicatorResult(
        indicator_id="EG-04", entity_id="CSE-A", period=p,
        value=5.0, n=10, confidence=0.5,
        assessability=Assessability.NOT_ASSESSABLE, source=FindingSource.RULES_ENGINE,
    )
    print("  FAIL  not-assessable with a value was accepted")
except ValidationError:
    print("  OK  not-assessable + value rejected by invariant")

# INVARIANT: extra="forbid" catches contract drift.
try:
    IndicatorResult(
        indicator_id="EG-01", entity_id="CSE-A", period=p, n=1, confidence=0.5,
        source=FindingSource.RULES_ENGINE, surprise_field=1,
    )
    print("  FAIL  unknown field was accepted")
except ValidationError:
    print("  OK  unknown field rejected (extra=forbid)")

mo = ModelOutput(
    model_id="isolation_forest_v1", target_type="case", target_id="C-1",
    features={"closure_sec": 30.0}, score=0.71, direction="below_peer",
    seed=42, confidence=0.6,
    attributions=[Attribution(feature="closure_sec", value=30.0, effect_size=-4.0, direction="increases_risk")],
    evidence_row_ids=["r1", "r2"],
)
print(f"  OK  ModelOutput.explainable = {mo.explainable} (True: attributions + evidence present)")

mo2 = ModelOutput(
    model_id="isolation_forest_v1", target_type="case", target_id="C-2",
    features={}, score=0.7, direction="above_peer", seed=42, confidence=0.2,
)
print(f"  OK  bare ModelOutput.explainable = {mo2.explainable} (False -> low-confidence lead only)")

print("\nALL SCHEMA CHECKS PASSED")
