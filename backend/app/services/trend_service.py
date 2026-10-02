"""Per-period trends and regime-shift detection (plan §6.5).

Both read `entity_score`, the persisted per-entity/per-period aggregates, so a
trend never disagrees with the row an auditor can read. Two structural choices:

* A **series is per entity** — the supervisory question is "did *this*
  entity's gap picture get better or worse" — and the route was registered
  that way. A period the entity has no row for is reported as a gap, never
  bridged.
* A **change point is a cohort property** — it marks where the *distribution*
  of a metric shifted across the peers. The same detector flags an improvement
  exactly as reliably as a deterioration, so callers must read `direction` as
  the sign of the measured shift, never as a verdict.

Detection is least-squares binary segmentation over the per-period cohort
median. It is deterministic — there are no random draws, so no seed is needed
and results reproduce exactly across hosts, which is what the "fixed seed" of
plan §6.5 is for.
"""

from __future__ import annotations

from datetime import date
from statistics import fmean, median

from app.db.sqlite import get_connection
from app.schemas.trends import (
    ChangePointOut,
    ChangePointsOut,
    TrendMetric,
    TrendPointOut,
    TrendSeriesOut,
)

#: The metric columns on `entity_score`. A whitelist, so the column is never
#: interpolated from caller input even though the API already constrains it.
_METRICS: tuple[str, ...] = ("egi", "nsi", "dts", "sap")

#: A period needs at least this many scored entities to stand for the cohort's
#: distribution. With fewer, one entity carries the whole "regime" reading,
#: which is a single score, not a distribution.
MIN_COHORT = 3

#: A regime on each side of a candidate change point needs at least this many
#: periods; below twice this, the series is too short to say anything honest.
MIN_SEGMENT = 3

#: The cohort median must move at least this far (absolute, on the 0..1 metric
#: scale) for a split to count as a regime shift at all.
MIN_SHIFT = 0.10

#: A split must remove at least this fraction of its segment's variance to be
#: kept — an ordinary wobble inside a stable regime must not qualify.
MIN_SSE_IMPROVEMENT = 0.20


class TrendError(Exception):
    """Bad request for the trends API — surfaced as 400."""


class TrendEntityNotFound(Exception):
    """No such entity in a trend read — surfaced as 404 by the route."""


def _sse(values: list[float]) -> float:
    """Within-segment sum of squared deviations from the segment mean."""
    mean = fmean(values)
    return sum((v - mean) ** 2 for v in values)


class TrendService:
    """Read-side queries behind `GET /trends` and `GET /trends/change-points`."""

    def entity_trend(
        self,
        entity_id: str,
        metric: TrendMetric,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> TrendSeriesOut:
        """An entity's series for one metric, oldest period first.

        Every period the cohort scored within the entity's own scored span is
        answered: scored points carry the value, periods the entity has no row
        for are `gap=True` (charts must break the line), and a period whose row
        lacks the metric is `assessable=False` — "could not be scored" is not
        the same fact as "scored nothing".
        """
        if not entity_id:
            raise TrendError("entity_id is required: a trend series is per entity.")
        if metric not in _METRICS:
            raise TrendError(
                f"Unknown metric '{metric}'. Known metrics: {', '.join(_METRICS)}."
            )
        conn = get_connection()
        if conn.execute(
            "SELECT 1 FROM entity WHERE entity_id = ?", (entity_id,)
        ).fetchone() is None:
            raise TrendEntityNotFound(
                f"No such entity: {entity_id}. Registered entities are listed at "
                "GET /api/v1/entities."
            )

        window = (
            period_start.isoformat() if period_start else None,
            period_end.isoformat() if period_end else None,
        )
        own = {
            str(row["period_start"]): row
            for row in conn.execute(
                f"SELECT period_start, period_end, run_id, {metric} AS value "
                "FROM entity_score WHERE entity_id = ? "
                "AND (? IS NULL OR period_start >= ?) AND (? IS NULL OR period_end <= ?) "
                "ORDER BY period_start",
                (entity_id, window[0], window[0], window[1], window[1]),
            ).fetchall()
        }
        if not own:
            return TrendSeriesOut(
                entity_id=entity_id, metric=metric, points=[], n_scored=0, n_gaps=0
            )

        timeline = conn.execute(
            "SELECT DISTINCT period_start, MIN(period_end) AS period_end "
            "FROM entity_score "
            "WHERE (? IS NULL OR period_start >= ?) AND (? IS NULL OR period_end <= ?) "
            "GROUP BY period_start ORDER BY period_start",
            (window[0], window[0], window[1], window[1]),
        ).fetchall()

        # The entity's own scored span bounds the series: periods before its
        # first score or after its last one say nothing about *this* entity and
        # must not be rendered as gaps it "stopped" filling.
        span_start, span_end = min(own), max(own)
        points: list[TrendPointOut] = []
        for slot in timeline:
            ps = str(slot["period_start"])
            if not (span_start <= ps <= span_end):
                continue
            row = own.get(ps)
            if row is None:
                points.append(
                    TrendPointOut(
                        period_start=date.fromisoformat(ps),
                        period_end=date.fromisoformat(str(slot["period_end"])),
                        value=None,
                        assessable=False,
                        gap=True,
                    )
                )
            else:
                points.append(
                    TrendPointOut(
                        period_start=date.fromisoformat(ps),
                        period_end=date.fromisoformat(str(row["period_end"])),
                        value=row["value"],
                        assessable=row["value"] is not None,
                        gap=False,
                        run_id=str(row["run_id"]),
                    )
                )
        n_gaps = sum(1 for p in points if p.gap)
        return TrendSeriesOut(
            entity_id=entity_id,
            metric=metric,
            points=points,
            n_scored=sum(1 for p in points if p.value is not None),
            n_gaps=n_gaps,
        )

    def change_points(
        self,
        metric: TrendMetric,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> ChangePointsOut:
        """Regime-shift candidates across the cohort.

        The per-period cohort median (median of every entity that scored the
        metric that period) is segmented with the least-squares criterion: the
        split that removes the most within-segment variance, recursing on both
        sides. A split is kept only when the cohort median moves by at least
        `MIN_SHIFT` and the split removes a material share of variance. The
        detector is deterministic by construction.
        """
        if metric not in _METRICS:
            raise TrendError(
                f"Unknown metric '{metric}'. Known metrics: {', '.join(_METRICS)}."
            )
        window = (
            period_start.isoformat() if period_start else None,
            period_end.isoformat() if period_end else None,
        )
        rows = get_connection().execute(
            f"SELECT period_start, period_end, {metric} AS value FROM entity_score "
            "WHERE (? IS NULL OR period_start >= ?) AND (? IS NULL OR period_end <= ?) "
            f"AND {metric} IS NOT NULL ORDER BY period_start, entity_id",
            (window[0], window[0], window[1], window[1]),
        ).fetchall()

        values_by_period: dict[str, list[float]] = {}
        period_end_by: dict[str, str] = {}
        for row in rows:
            ps = str(row["period_start"])
            values_by_period.setdefault(ps, []).append(float(row["value"]))
            period_end_by.setdefault(ps, str(row["period_end"]))

        series: list[tuple[str, float]] = []
        dropped = 0
        for ps, values in values_by_period.items():
            if len(values) < MIN_COHORT:
                dropped += 1
                continue
            series.append((ps, median(values)))

        notes: list[str] = []
        if dropped:
            notes.append(
                f"{dropped} period(s) had fewer than {MIN_COHORT} scored entities "
                "and were not used."
            )
        n_periods = len(series)
        if n_periods < 2 * MIN_SEGMENT:
            notes.append(
                f"fewer than {2 * MIN_SEGMENT} scored periods — too short for a "
                "regime test."
            )
            return ChangePointsOut(
                metric=metric,
                n_periods=n_periods,
                change_points=[],
                note=" ".join(notes) or None,
            )

        medians = [value for _, value in series]
        detected = self._segment(medians, 0, len(medians))
        change_points = [
            ChangePointOut(
                period_start=date.fromisoformat(series[k][0]),
                period_end=date.fromisoformat(period_end_by[series[k][0]]),
                direction="up" if after > before else "down",
                cohort_median_before=before,
                cohort_median_after=after,
                shift=after - before,
                ss_reduction=impr,
                n_periods_before=k - lo,
                n_periods_after=hi - k,
            )
            for k, lo, hi, after, before, impr in detected
        ]
        return ChangePointsOut(
            metric=metric,
            n_periods=n_periods,
            change_points=change_points,
            note=" ".join(notes) or None,
        )

    def _segment(
        self, medians: list[float], lo: int, hi: int
    ) -> list[tuple[int, int, int, float, float, float]]:
        """Least-squares regime splits in `medians[lo:hi]`.

        Returns one record per split: `(mid, lo, hi, median_after,
        median_before, ss_reduction)` where `mid` is the first period of the
        new regime. Chosen splits are exhaustive over the segment — no sampling,
        hence deterministic.
        """
        segment = medians[lo:hi]
        sse_full = _sse(segment)
        if sse_full <= 0:
            # Constant series: there is no within-segment variance for a split
            # to explain, so no regime shift to report.
            return []
        best: tuple[int, float, float, float] | None = None  # (mid, impr, med_before, med_after)
        for mid in range(lo + MIN_SEGMENT, hi - MIN_SEGMENT + 1):
            left, right = medians[lo:mid], medians[mid:hi]
            med_before, med_after = median(left), median(right)
            if abs(med_after - med_before) < MIN_SHIFT:
                continue
            impr = (sse_full - (_sse(left) + _sse(right))) / sse_full
            if impr < MIN_SSE_IMPROVEMENT:
                continue
            if best is None or impr > best[1]:
                best = (mid, impr, med_before, med_after)
        if best is None:
            return []
        mid, impr, med_before, med_after = best
        return [
            (mid, lo, hi, med_after, med_before, impr)
        ] + self._segment(medians, lo, mid) + self._segment(medians, mid, hi)


def get_trend_service() -> TrendService:
    """Per-request service factory (endpoints never hold one at import time)."""
    return TrendService()