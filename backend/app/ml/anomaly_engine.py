"""Deterministic pooled leave-one-out anomaly detection."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math

from app.schemas.indicator import ModelOutput
from .attributions import feature_attributions


def pooled_loo_isolation_forest(reference: Sequence[Mapping[str, object]], target: Sequence[Mapping[str, object]] | Mapping[str, object], seed: int = 42) -> list[ModelOutput]:
    """Score targets against pooled reference rows, excluding each target.

    Rows need ``target_id`` and numeric feature columns; ``evidence_row_ids``
    may be supplied as a scalar or sequence. Small cohorts decline gracefully.
    """
    targets = [target] if isinstance(target, Mapping) else list(target)
    refs = list(reference)
    names = sorted({k for r in refs + targets for k, v in r.items() if k not in {"target_id", "evidence_row_ids", "entity_id"} and isinstance(v, (int, float)) and math.isfinite(float(v))})
    outputs: list[ModelOutput] = []
    for row in targets:
        rid = str(row.get("target_id", row.get("entity_id", "target")))
        peer = [r for r in refs if str(r.get("target_id", r.get("entity_id", ""))) != rid]
        usable = [r for r in peer if all(k in r and isinstance(r[k], (int, float)) and math.isfinite(float(r[k])) for k in names)]
        vals = {k: float(row[k]) for k in names if isinstance(row.get(k), (int, float)) and math.isfinite(float(row[k]))}
        ev = row.get("evidence_row_ids", [rid]); evidence = [str(x) for x in ev] if isinstance(ev, (list, tuple)) else [str(ev)]
        if len(usable) < 2 or not names or len(vals) != len(names):
            outputs.append(ModelOutput(model_id="isolation_forest_v1", target_type="entity", target_id=rid, features=vals, score=0.0, attributions=[], direction="within_peer", seed=seed, confidence=0.0, evidence_row_ids=evidence))
            continue
        import numpy as np
        from sklearn.ensemble import IsolationForest
        x = np.asarray([[float(r[k]) for k in names] for r in usable])
        model = IsolationForest(random_state=seed, n_estimators=100, contamination="auto").fit(x)
        score = float(-model.score_samples(np.asarray([[vals[k] for k in names]]))[0])
        attrs = feature_attributions(vals, usable)
        direction = "above_peer" if sum(a.effect_size for a in attrs) > 0 else "below_peer" if attrs else "within_peer"
        outputs.append(ModelOutput(model_id="isolation_forest_v1", target_type="entity", target_id=rid, features=vals, score=score, attributions=attrs, direction=direction, seed=seed, confidence=min(1.0, len(usable) / 10.0) if attrs and evidence else 0.0, evidence_row_ids=evidence))
    return outputs
