"""Deterministic, peer-relative feature explanations."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from statistics import median

from app.schemas.indicator import Attribution


def feature_attributions(
    observed: Mapping[str, float], reference: Sequence[Mapping[str, float]], *, limit: int = 5
) -> list[Attribution]:
    """Return strongest finite deviations from peer medians.

    This is deliberately not model importance: it explains observed feature
    deviation, which is the stable language the supervisory UI needs.
    """
    out: list[Attribution] = []
    for name, raw in observed.items():
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        peers = []
        for row in reference:
            try:
                v = float(row[name])
                if math.isfinite(v): peers.append(v)
            except (KeyError, TypeError, ValueError):
                pass
        if not math.isfinite(value) or not peers:
            continue
        med = median(peers)
        mad = median([abs(v - med) for v in peers])
        scale = 1.4826 * mad
        if scale <= 1e-12:
            scale = max(abs(med) * 0.1, 1.0)
        effect = (value - med) / scale
        if abs(effect) <= 1e-12:
            continue
        out.append(Attribution(feature=name, value=value, peer_median=med,
                               effect_size=effect,
                               direction="increases_risk" if effect > 0 else "decreases_risk"))
    return sorted(out, key=lambda a: (-abs(a.effect_size), a.feature))[:limit]


def attributions(model: object, reference: Sequence[Mapping[str, float]], target: Mapping[str, float]) -> list[Attribution]:
    """Compatibility adapter for the frozen handover signature."""
    return feature_attributions(target, reference)
