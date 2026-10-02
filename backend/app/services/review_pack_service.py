"""Review-pack generator (plan §7, Phase 11).

A pack is a *sample for human review*, not a ranked list. The two properties
that make its conclusions usable as evidence about the population:

* **Known inclusion probabilities.** Every item carries `inclusion_prob`
  (`π_i`), recorded rather than assumed uniform, because PPS makes it
  non-uniform by construction. Without it, no prevalence estimate can be
  defended.
* **A control slice.** A pack containing only the top-scoring cases cannot
  distinguish "the tool ranked well" from "the pack was cherry-picked".

Sampling design, chosen so every number on the pack is reproducible and
stated rather than asserted:

1. **Population** — every case in `case_record` for the entities in scope with
   `created_at` inside the pack's period. `case_id` is unique only within a
   submission, so cases are keyed internally by `(entity_id, case_id)`.
2. **Case risk score** — from the findings of each entity's *most recent
   completed scoring run* overlapping the period. A finding contributes to
   every case that appears in its evidence (`finding_evidence` rows in
   case-level tables), weighted by `confidence * max(0, effect_size)`. The
   effect size is a robust z after shrinkage, so contributions are comparable
   across indicators. A case in no finding's evidence scores 0.
3. **Targeted slice** — ordered systematic PPS by risk: one random start,
   `n_target` picks at step `W / n_target`, inclusion probability
   `π_i = min(1, n_target * w_i / W)`. This is the classical Hartley–Rao
   scheme: known first-order probabilities, deterministic given the seed.
4. **Diversity caps** — post-selection, at most `max_per_analyst` targeted
   items per analyst (highest risk kept, the excess excluded and counted in
   `diversity_caps_applied`). Template-cluster caps are configured but inert
   until Dev 3's `nlp_auditor` produces cluster ids.
5. **Control slice** — severity-stratified simple random sample, drawn from the
   *complement* of the targeted selection so the two slices are disjoint.
   Within stratum `s`, `π = n_s / N_s`. Cases whose severity is not in the
   requested stratum set (or is unknown) form a residual stratum rather than
   being silently dropped.
6. **Prevalence estimate** — Horvitz–Thompson over the combined sample:
   `τ̂ = Σ y_i / π_i`, `p̂ = τ̂ / N`, where `y_i = 1` iff the case carries a
   working hypothesis (risk > 0). Variance is summed per stratum: the
   independent-selection (Poisson) approximation for the PPS slice, exact
   SRS-without-replacement variance for each control stratum; the caveat says
   what each approximation assumes.

Persistence and ledger: the pack and its items are written in one transaction,
then a `review_pack_created` ledger entry records the sampling decision with
the content hash, seed and counts. Verdicts are the *supervisor's own*
conclusions (`verdict_recorded`), never Orion's.
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.config import get_settings
from app.db.duckdb_client import get_duckdb
from app.db.sqlite import get_connection, transaction
from app.schemas.common import Severity
from app.schemas.review_pack import (
    HTEstimate,
    ReviewPackGenerateIn,
    ReviewPackItemOut,
    ReviewPackListItem,
    ReviewPackOut,
    SliceType,
    VerificationPrompt,
    Verdict,
    VerdictIn,
    VerdictOut,
)
from app.services.evidence_service import _norm_inv
from app.services.ledger import LedgerAction, get_ledger

#: Findings whose evidence rows sit in one of these tables are case-attached:
#: the row id *is* a case id. Other tables (`record_version`, `alert_record`)
#: are record- or alert-level and do not grade a case — a case risk score must
#: be traceable to the case it grades, not to a row that merely resembles one.
CASE_EVIDENCE_TABLES = frozenset({"case_record", "case_event", "escalation"})

#: Severity discipline lands, for an examiner to verify — the vocabulary is
#: hypotheses and evidence, never determinations. Each entry is
#: (prompt_type, question, expected_evidence).
INDICATOR_VERIFY: dict[str, tuple[str, str, str]] = {
    "EG-01": (
        "check_closure_record",
        "The case was closed unusually quickly for its severity. Verify the "
        "closure record exists and that the investigation timeline matches the "
        "case events.",
        "case_record closed_at/created_at plus case_event rows for the case",
    ),
    "EG-02": (
        "check_documentation_quality",
        "Verify whether the closure note records a genuine human review or a "
        "thin dismissal template.",
        "note_store redacted text for the case's note_refs",
    ),
    "EG-06": (
        "check_escalation_record",
        "Verify whether a separate escalation record exists outside the case "
        "file for this critical closure.",
        "escalation rows for the case",
    ),
    "EG-08": (
        "check_sla_timeline",
        "Verify the case created/closed timestamps against its severity's SLA "
        "target rather than the summary.",
        "case_record created_at and closed_at",
    ),
    "EG-09": (
        "check_closure_sequence",
        "Verify the closure timestamps against the submission time — was a "
        "backlog closed inside one narrow window?",
        "case_record closed_at versus submission received_ts",
    ),
    "EG-11": (
        "check_record_history",
        "Verify the record was edited across submissions and whether a "
        "documented justification exists for the change.",
        "record_version hashes across submissions",
    ),
}


class ReviewPackError(Exception):
    """A pack cannot be built or read for the requested inputs."""


class PackNotFound(ReviewPackError):
    """A pack id does not exist (or the caller has no access to it)."""


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _severity(value: str | None) -> Severity | None:
    if not value:
        return None
    try:
        return Severity(value)
    except ValueError:
        return None


class ReviewPackService:
    """Build, read and ledger review packs and the supervisor's verdicts."""

    # ------------------------------------------------------------ generation --
    def generate(self, body: ReviewPackGenerateIn, actor: str) -> ReviewPackOut:
        """Build a pack, persist it, and ledger the sampling decision.

        `actor` names who requested the pack; the ledger entry makes the
        *sampling decision* attributable, which is the whole reason the pack —
        not just its export — is audit-trailed.
        """
        seed = body.seed if body.seed is not None else get_settings().seed
        rng = random.Random(seed)

        try:
            ps_date = date.fromisoformat(body.period_start) if body.period_start else None
            pe_date = date.fromisoformat(body.period_end) if body.period_end else None
        except ValueError as exc:
            raise ReviewPackError(
                "period_start and period_end must be YYYY-MM-DD; a malformed "
                f"period is a sampling-input error, not something to guess about: {exc}"
            ) from exc
        if ps_date and pe_date and ps_date > pe_date:
            raise ReviewPackError(
                f"period_start {ps_date} is after period_end {pe_date}."
            )
        period_start, period_end = self._resolve_period(ps_date, pe_date)

        population = self._population(period_start, period_end, body.entity_ids)
        if not population:
            raise ReviewPackError(
                f"No cases in case_record between {period_start.isoformat()} and "
                f"{period_end.isoformat()} for the entities in scope, so there "
                "is nothing to sample. This is not the same as 'no findings' — "
                "it means no case evidence exists for the window."
            )

        findings = self._scoring_findings(period_start, period_end)
        attached = self._attached_findings(findings)
        risk_by_case = {
            cid: self._case_risk(attached.get(cid, [])) for cid in population
        }

        n_target = min(body.n_target, len(population))
        kept_targeted, analyst_excluded = self._targeted_slice(
            population, risk_by_case, n_target, rng, body.max_per_analyst
        )
        controls, control_counts = self._control_slice(
            population,
            kept_targeted,
            body.n_control,
            body.control_severity_strata,
            rng,
        )
        self._stamp_control_text(controls, control_counts)

        items = kept_targeted + controls
        if not items:
            raise ReviewPackError(
                "The targeted slice came up empty (no case carries a working "
                "hypothesis — every case risk is 0) and no controls were "
                "requested, so the pack would contain nothing to review. "
                "Request a non-zero control count, or run scoring first."
            )

        risk_sorted = sorted(
            population, key=lambda cid: (-risk_by_case[cid], cid)
        )
        rank = {cid: i + 1 for i, cid in enumerate(risk_sorted)}

        ht = self._ht_estimate(
            items,
            risk_by_case,
            len(population),
            control_counts,
            kept=len(kept_targeted),
        )

        strata_used = self._stratum_text(control_counts)
        stratum = (
            f"PPS targeted ({len(kept_targeted)} of {n_target} kept) + "
            f"severity-stratified controls ({len(controls)} of {body.n_control} "
            f"requested) over {len(population)} cases"
            + (f"; {strata_used}" if strata_used else "")
        )

        diversity_caps = {
            "targeted_requested": n_target,
            "targeted_kept": len(kept_targeted),
            "targeted_analyst_capped_excluded": analyst_excluded,
            "max_per_analyst": body.max_per_analyst,
            "max_per_cluster": body.max_per_cluster,
            "cluster_cap_active": 0,
            "control_drawn": len(controls),
        }

        pack_rows = [
            self._item_out(
                item, rank, risk_by_case, attached.get(item["key"], [])
            )
            for item in items
        ]

        content_hash = self._content_hash(
            period_start,
            period_end,
            body,
            seed,
            stratum,
            diversity_caps,
            pack_rows,
        )

        now = datetime.now(timezone.utc).isoformat()
        pack_id = f"pack_{uuid.uuid4().hex[:16]}"
        with transaction() as conn:
            conn.execute(
                "INSERT INTO review_pack (pack_id, created_ts, created_by, "
                " period_start, period_end, stratum, n_target, n_control, "
                " n_selected, n_population, seed, ht_estimate, ht_ci_low, "
                " ht_ci_high, diversity_caps, content_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pack_id,
                    now,
                    actor,
                    period_start.isoformat(),
                    period_end.isoformat(),
                    stratum,
                    body.n_target,
                    body.n_control,
                    len(pack_rows),
                    len(population),
                    seed,
                    ht.estimate if ht else None,
                    ht.ci_low if ht else None,
                    ht.ci_high if ht else None,
                    json.dumps(diversity_caps),
                    content_hash,
                ),
            )
            for row in pack_rows:
                conn.execute(
                    "INSERT INTO review_pack_item (pack_id, case_id, entity_id, "
                    " slice_type, inclusion_prob, case_risk_score, "
                    " selected_because, verification_prompts, severity_norm, "
                    " contributing_indicators, finding_ids, cluster_id, "
                    " analyst_pseudo) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        pack_id,
                        row.case_id,
                        row.entity_id,
                        row.slice_type.value,
                        row.inclusion_prob,
                        row.case_risk_score,
                        row.selected_because,
                        json.dumps([p.model_dump() for p in row.verification_prompts]),
                        row.severity.value if row.severity else None,
                        json.dumps(row.contributing_indicators),
                        json.dumps(row.finding_ids),
                        row.cluster_id,
                        row.analyst_pseudo,
                    ),
                )

        get_ledger().append(
            actor=actor,
            action=LedgerAction.REVIEW_PACK_CREATED,
            payload={
                "pack_id": pack_id,
                "content_hash": content_hash,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "n_target": body.n_target,
                "n_control": body.n_control,
                "n_selected": len(pack_rows),
                "n_population": len(population),
                "seed": seed,
            },
        )
        return self.get(pack_id)

    # ------------------------------------------------------------- population --
    def _resolve_period(
        self, period_start: date | None, period_end: date | None
    ) -> tuple[date, date]:
        """The pack's window.

        Absent means the most recent completed scoring run's window — same rule
        as the entity reads, so a request without a period cannot silently pick
        a different quarter than the one the frontend is showing.
        """
        if period_start and period_end:
            return period_start, period_end
        run_row = get_connection().execute(
            "SELECT run_id FROM run WHERE status = 'complete' "
            "ORDER BY COALESCE(finished_ts, created_ts) DESC, run_id DESC LIMIT 1"
        ).fetchone()
        if run_row is None:
            raise ReviewPackError(
                "No period was given and no completed scoring run exists to derive "
                "one from. Pass period_start/period_end, or run scoring first."
            )
        row = get_connection().execute(
            "SELECT MIN(period_start) AS ps, MAX(period_end) AS pe "
            "FROM entity_score WHERE run_id = ?",
            (run_row["run_id"],),
        ).fetchone()
        if row and row["ps"] and row["pe"]:
            return date.fromisoformat(str(row["ps"])), date.fromisoformat(str(row["pe"]))
        raise ReviewPackError(
            f"Completed run {run_row['run_id']} has no entity scores to derive "
            "a period from. Pass period_start/period_end explicitly."
        )

    def _population(
        self,
        period_start: date,
        period_end: date,
        entity_ids: list[str] | None,
    ) -> dict[str, dict[str, Any]]:
        """Every case inside the period, keyed by (entity_id, case_id)."""
        with get_duckdb().reader() as conn:
            cursor = conn.execute(
                "SELECT case_id, entity_id, "
                "       COALESCE(analyst_pseudo, closed_by_pseudo, '') AS analyst, "
                "       severity_norm AS severity "
                "FROM case_record "
                "WHERE created_at >= CAST(? AS TIMESTAMP) "
                "  AND created_at < CAST(? AS TIMESTAMP)",
                [
                    period_start.isoformat(),
                    (period_end + timedelta(days=1)).isoformat(),
                ],
            )
            names = [d[0] for d in cursor.description]
            rows = [dict(zip(names, r)) for r in cursor.fetchall()]

        if entity_ids:
            wanted = set(entity_ids)
            present = {str(r["entity_id"]) for r in rows}
            unknown = sorted(wanted - present)
            if unknown:
                raise ReviewPackError(
                    "No cases in this period for: "
                    + ", ".join(unknown)
                    + ". Known entities with cases are on GET /entities."
                )
            rows = [r for r in rows if str(r["entity_id"]) in wanted]

        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            key = f"{r['entity_id']}\u0000{r['case_id']}"
            out[key] = {
                "key": key,
                "entity_id": str(r["entity_id"]),
                "case_id": str(r["case_id"]),
                "analyst": str(r["analyst"]) or None,
                "severity": str(r["severity"]) if r["severity"] else None,
            }
        return out

    # --------------------------------------------------------------- findings --
    def _scoring_findings(
        self, period_start: date, period_end: date
    ) -> list[sqlite3.Row]:
        """Findings of each entity's most recent completed run in the window.

        A re-run of the same period produces new finding rows under a new run
        id, so joining every run would double-count the same signal. Only the
        newest completed pass per entity feeds the risk scores.
        """
        conn = get_connection()
        runs = conn.execute(
            "SELECT s.entity_id, s.run_id, r.created_ts "
            "FROM entity_score s JOIN run r ON r.run_id = s.run_id "
            "AND r.status = 'complete' "
            "WHERE s.period_start <= ? AND s.period_end >= ? "
            "ORDER BY s.entity_id, r.created_ts DESC, s.run_id DESC",
            (period_end.isoformat(), period_start.isoformat()),
        ).fetchall()
        latest: dict[str, str] = {}
        for row in runs:
            latest.setdefault(str(row["entity_id"]), str(row["run_id"]))
        if not latest:
            return []
        marks = ",".join("?" * len(latest))
        return conn.execute(
            f"SELECT * FROM finding WHERE run_id IN ({marks}) "
            "AND is_low_confidence_lead = 0",
            list(latest.values()),
        ).fetchall()

    def _attached_findings(
        self, findings: list[sqlite3.Row]
    ) -> dict[str, list[sqlite3.Row]]:
        """Map case key -> findings whose evidence includes that case."""
        conn = get_connection()
        by_key: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for frow in findings:
            rows = conn.execute(
                "SELECT table_name, row_id FROM finding_evidence "
                "WHERE finding_id = ?",
                (frow["finding_id"],),
            ).fetchall()
            for ev in rows:
                if str(ev["table_name"]) not in CASE_EVIDENCE_TABLES:
                    continue
                key = f"{frow['entity_id']}\u0000{ev['row_id']}"
                by_key[key].append(frow)
        return dict(by_key)

    @staticmethod
    def _case_risk(findings: list[sqlite3.Row]) -> float:
        """A case's risk score from its attached findings.

        Every contribution is `confidence * max(0, effect_size)`, so a finding
        can only add risk, never subtract it, and a robust z of 8 with
        confidence 0.9 contributes more than a z of 2 with confidence 0.5.
        """
        total = 0.0
        for frow in findings:
            confidence = frow["confidence"] if frow["confidence"] is not None else 0.0
            effect = frow["effect_size"] if frow["effect_size"] is not None else 0.0
            total += float(confidence) * max(0.0, float(effect))
        return round(total, 6)

    # ----------------------------------------------------------- targeted PPS --
    def _targeted_slice(
        self,
        population: dict[str, dict[str, Any]],
        risk_by_case: dict[str, float],
        n_target: int,
        rng: random.Random,
        max_per_analyst: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Ordered systematic PPS, then the analyst diversity cap.

        Returns (kept items, number excluded by the analyst cap). Ordering is
        risk-descending and fully deterministic, so the same seed reproduces
        the same slice byte-for-byte.
        """
        total_weight = sum(risk_by_case.values())
        if total_weight <= 0.0:
            return [], 0
        step = total_weight / n_target
        start = rng.random() * step

        ordered = sorted(
            population.values(),
            key=lambda c: (-risk_by_case[c["key"]], c["entity_id"], c["case_id"]),
        )
        cum = 0.0
        j = 0
        picked: list[dict[str, Any]] = []
        for idx in range(n_target):
            target = start + idx * step
            if j >= len(ordered):
                break
            while j < len(ordered) and not (
                target < cum + risk_by_case[ordered[j]["key"]]
            ):
                cum += risk_by_case[ordered[j]["key"]]
                j += 1
            if j < len(ordered):
                picked.append(ordered[j])

        # De-duplicate: a unit wide enough to contain several picks is a
        # certainty unit and appears multiple times.
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for c in picked:
            if c["key"] not in seen:
                seen.add(c["key"])
                selected.append(c)

        for c in selected:
            c["inclusion_prob"] = min(
                1.0, n_target * risk_by_case[c["key"]] / total_weight
            )

        # Analyst diversity cap, applied post-selection: keep the highest-risk
        # items per analyst, drop the excess, and count what was dropped so the
        # pack states its own distortion instead of hiding it.
        by_analyst: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        for c in selected:
            by_analyst[c["analyst"]].append(c)
        kept: list[dict[str, Any]] = []
        excluded = 0
        for analyst, items in by_analyst.items():
            if analyst is None or len(items) <= max_per_analyst:
                kept.extend(items)
                continue
            ordered_items = sorted(
                items,
                key=lambda c: (-risk_by_case[c["key"]], c["entity_id"], c["case_id"]),
            )
            kept.extend(ordered_items[:max_per_analyst])
            excluded += len(ordered_items) - max_per_analyst
        kept.sort(key=lambda c: (c["entity_id"], c["case_id"]))
        return kept, excluded

    # ----------------------------------------------------------- control SRS --
    def _control_slice(
        self,
        population: dict[str, dict[str, Any]],
        targeted: list[dict[str, Any]],
        n_control: int,
        strata: list[Severity],
        rng: random.Random,
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[int, int]]]:
        """Severity-stratified SRS from the complement of the targeted slice.

        Returns (control items, {stratum: (selected, available)}). A case whose
        severity is not in the requested strata (or is unknown) forms a residual
        stratum so nothing is silently dropped from the frame.
        """
        if n_control <= 0:
            return [], {}
        targeted_keys = {c["key"] for c in targeted}
        allowed = {s.value for s in strata}
        by_stratum: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for key, c in population.items():
            if key in targeted_keys:
                continue
            sev = c["severity"]
            stratum = sev if sev in allowed else "UNKNOWN"
            by_stratum[stratum].append(c)

        for items in by_stratum.values():
            items.sort(key=lambda c: (c["entity_id"], c["case_id"]))

        allocation = self._allocate(by_stratum, n_control)
        controls: list[dict[str, Any]] = []
        counts: dict[str, tuple[int, int]] = {}
        for stratum, n_s in sorted(allocation.items()):
            pool = by_stratum[stratum]
            if not pool or n_s <= 0:
                counts[stratum] = (0, len(pool))
                continue
            rng.shuffle(pool)
            for c in pool[:n_s]:
                c["inclusion_prob"] = n_s / len(pool)
                c["stratum"] = stratum
                controls.append(c)
            counts[stratum] = (n_s, len(pool))
        controls.sort(key=lambda c: (c["entity_id"], c["case_id"]))
        return controls, counts

    def _stamp_control_text(
        self, controls: list[dict[str, Any]], counts: dict[str, tuple[int, int]]
    ) -> None:
        """Attach stratum draw counts to control items for the prose."""
        for c in controls:
            stratum = c.get("stratum", "UNKNOWN")
            n_s, n_avail = counts.get(stratum, (0, 0))
            c["n_stratum"] = n_s
            c["n_avail"] = n_avail

    @staticmethod
    def _allocate(
        by_stratum: dict[str, list[dict[str, Any]]], n: int
    ) -> dict[str, int]:
        """Largest-remainder proportional allocation across non-empty strata."""
        sizes = {s: len(pool) for s, pool in by_stratum.items() if pool}
        total = sum(sizes.values())
        if total == 0:
            return {}
        base = {s: sizes[s] * n // total for s in sizes}
        remainder = n - sum(base.values())
        if remainder > 0:
            keyed = sorted(
                sizes,
                key=lambda s: (
                    sizes[s] * n / total - sizes[s] * n // total,
                    -sizes[s],
                    s,
                ),
                reverse=True,
            )
            for s in keyed[:remainder]:
                base[s] += 1
        return {
            s: min(cnt, sizes[s])
            for s, cnt in base.items()
            if sizes.get(s, 0) > 0
        }

    # ------------------------------------------------------------- estimate --
    def _ht_estimate(
        self,
        items: list[dict[str, Any]],
        risk_by_case: dict[str, float],
        n_population: int,
        control_counts: dict[str, tuple[int, int]],
        kept: int,
    ) -> HTEstimate | None:
        """Horvitz-Thompson prevalence with per-stratum variance.

        `y_i = 1` iff the case carries a working hypothesis (risk > 0). The
        point estimate is the HT total over the population size, truncated to
        the [0,1] parameter space (the estimator can overshoot on small
        samples). Variance is summed per stratum: independent-selection
        (Poisson) approximation for the PPS slice, exact SRS variance for each
        control stratum. Both assumptions are stated in the caveat rather than
        hidden.
        """
        if not items:
            return None

        ys = [1.0 if risk_by_case.get(c["key"], 0.0) > 0.0 else 0.0 for c in items]
        pis = [float(c["inclusion_prob"]) for c in items]

        estimate_total = sum(y / pi for y, pi in zip(ys, pis) if pi > 0.0)
        estimate_raw = estimate_total / n_population if n_population else 0.0
        # The HT total/N estimator is unbiased but can overshoot the [0,1]
        # parameter space on small samples. Report it truncated (the CI is
        # clamped the same way) and say so — a prevalence of 1.2 is not an
        # answer a supervisor can act on.
        estimate = max(0.0, min(1.0, estimate_raw))

        variance = 0.0
        # PPS slice: Poisson/independent approximation (joint inclusion
        # probabilities of the systematic design are not modelled).
        for c, y in zip(items[:kept], ys[:kept]):
            pi = float(c["inclusion_prob"])
            if pi > 0.0:
                variance += (1.0 - pi) / (pi * pi) * y * y

        # Control strata: exact SRS-without-replacement variance where the
        # stratum drew more than one item; a single-item stratum falls back to
        # the same independent approximation so it cannot claim zero
        # uncertainty.
        y_by_key = {c["key"]: y for c, y in zip(items[kept:], ys[kept:])}
        for stratum, (n_s, n_avail) in control_counts.items():
            keys = [
                c["key"]
                for c in items[kept:]
                if c.get("stratum") == stratum
            ]
            if not keys or n_s <= 0:
                continue
            if n_s == 1:
                c = next(c for c in items[kept:] if c["key"] in keys)
                pi = float(c["inclusion_prob"])
                if pi > 0.0:
                    variance += (1.0 - pi) / (pi * pi) * y_by_key[c["key"]] ** 2
                continue
            stratum_ys = [y_by_key[k] for k in keys]
            mean = sum(stratum_ys) / len(stratum_ys)
            s2 = sum((v - mean) ** 2 for v in stratum_ys) / (len(stratum_ys) - 1)
            variance += (n_avail**2) * (1.0 - n_s / n_avail) * s2 / n_s

        caveat = (
            "Always present on stratified samples: the estimate is only as "
            "good as the severity stratification. The PPS slice's variance "
            "treats selections as independent (systematic joint probabilities "
            "are not modelled) and ignores the effect of diversity-cap "
            "exclusions; control strata use exact SRS variance."
        )
        if variance <= 0.0:
            ci_low = ci_high = estimate
            caveat += " No variance could be formed, so the confidence interval is a point."
        else:
            z = _norm_inv(1.0 - 0.05 / 2.0)  # 95% two-sided
            delta = z * (variance**0.5) / n_population
            ci_low = max(0.0, estimate - delta)
            ci_high = min(1.0, estimate + delta)
        if estimate_raw != estimate:
            caveat += (
                f" The raw HT estimate {estimate_raw:.4f} fell outside the "
                "[0, 1] parameter space and is reported truncated; with a "
                "small sample the estimator can overshoot."
            )

        return HTEstimate(
            estimate=round(estimate, 6),
            ci_low=round(ci_low, 6),
            ci_high=round(ci_high, 6),
            n_sampled=len(items),
            n_population=n_population,
            confidence_level=0.95,
            method="Horvitz-Thompson with stratified variance",
            caveat=caveat,
        )

    # ---------------------------------------------------------------- text -- -
    @staticmethod
    def _stratum_text(control_counts: dict[str, tuple[int, int]]) -> str:
        if not control_counts:
            return ""
        bits = []
        for stratum in sorted(control_counts):
            n_s, n_avail = control_counts[stratum]
            bits.append(f"{stratum}: {n_s}/{n_avail}")
        return "controls by stratum = " + ", ".join(bits)

    def _item_out(
        self,
        item: dict[str, Any],
        rank: dict[str, int],
        risk_by_case: dict[str, float],
        findings: list[sqlite3.Row],
    ) -> ReviewPackItemOut:
        key = item["key"]
        risk = risk_by_case.get(key, 0.0)
        indicators = sorted({str(f["indicator_id"]) for f in findings})
        finding_ids = sorted({str(f["finding_id"]) for f in findings})
        prompts = self._prompts(indicators, findings)

        if item.get("stratum") is None:
            selected_because = (
                f"Selected by risk-proportional sampling: case risk {risk:.4g} "
                f"(rank {rank[key]} of {len(rank)} by risk), inclusion "
                f"probability {item['inclusion_prob']:.4f} under the pack's "
                "PPS targeted slice."
            )
            slice_type = SliceType.TARGETED
        else:
            selected_because = (
                f"Selected at random as a control in stratum "
                f"{item['stratum']}: {item['n_stratum']} of {item['n_avail']} "
                f"available cases, inclusion probability "
                f"{item['inclusion_prob']:.4f}. Controls keep the pack's "
                "conclusions about the population independent of its risk "
                "ranking."
            )
            slice_type = SliceType.CONTROL

        return ReviewPackItemOut(
            case_id=item["case_id"],
            entity_id=item["entity_id"],
            slice_type=slice_type,
            case_risk_score=risk,
            inclusion_prob=item["inclusion_prob"],
            severity=_severity(item.get("severity")),
            selected_because=selected_because,
            verification_prompts=prompts,
            contributing_indicators=indicators,
            cluster_id=None,
            analyst_pseudo=item.get("analyst"),
            finding_ids=finding_ids,
            verdict=None,
        )

    def _prompts(
        self,
        indicators: list[str],
        findings: list[sqlite3.Row],
    ) -> list[VerificationPrompt]:
        if not findings:
            return [
                VerificationPrompt(
                    question=(
                        "This case was drawn at random as a control. Verify it "
                        "in full — investigation trail, notes, disposition — "
                        "without reference to any risk score, because the "
                        "control slice is what keeps the pack's conclusions "
                        "about the population checkable."
                    ),
                    expected_evidence=(
                        "case_record, case_event and escalation rows for the case"
                    ),
                    prompt_type="full_case_review",
                )
            ]
        prompts: list[VerificationPrompt] = []
        seen_types: set[str] = set()
        for indicator_id in indicators:
            spec = INDICATOR_VERIFY.get(indicator_id)
            if not spec or spec[0] in seen_types:
                continue
            seen_types.add(spec[0])
            prompts.append(
                VerificationPrompt(
                    question=spec[1],
                    expected_evidence=spec[2],
                    prompt_type=spec[0],
                )
            )
        prompts.append(
            VerificationPrompt(
                question=(
                    "Review the evidence rows and benign explanations on the "
                    "attached finding cards for this case before forming any "
                    "conclusion."
                ),
                expected_evidence="the stored evidence queries behind the finding cards",
                prompt_type="review_evidence_rows",
            )
        )
        return prompts

    def _content_hash(
        self,
        period_start: date,
        period_end: date,
        body: ReviewPackGenerateIn,
        seed: int,
        stratum: str,
        diversity_caps: dict[str, int],
        pack_rows: list[ReviewPackItemOut],
    ) -> str:
        canonical = {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "n_target": body.n_target,
            "n_control": body.n_control,
            "seed": seed,
            "stratum": stratum,
            "diversity_caps": diversity_caps,
            "items": sorted(
                (
                    {
                        "case_id": r.case_id,
                        "entity_id": r.entity_id,
                        "slice": r.slice_type.value,
                        "pi": r.inclusion_prob,
                        "risk": r.case_risk_score,
                        "indicators": r.contributing_indicators,
                    }
                    for r in pack_rows
                ),
                key=lambda d: (d["entity_id"], d["case_id"]),
            ),
        }
        return hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    # ----------------------------------------------------------------- reads --
    def _load_pack(self, pack_id: str) -> sqlite3.Row:
        row = get_connection().execute(
            "SELECT * FROM review_pack WHERE pack_id = ?", (pack_id,)
        ).fetchone()
        if row is None:
            raise PackNotFound(
                f"pack_id {pack_id} does not exist. Packs are created by POST "
                "/api/v1/review-packs; a missing id means no such sampling "
                "decision was recorded."
            )
        return row

    def get(self, pack_id: str) -> ReviewPackOut:
        row = self._load_pack(pack_id)
        item_rows = get_connection().execute(
            "SELECT * FROM review_pack_item WHERE pack_id = ? "
            "ORDER BY case_id",
            (pack_id,),
        ).fetchall()

        verdicts = {
            str(v["case_id"]): str(v["verdict"])
            for v in get_connection().execute(
                "SELECT case_id, verdict FROM verdict WHERE pack_id = ? "
                "ORDER BY recorded_ts DESC",
                (pack_id,),
            )
        }

        items = [
            self._read_item(r, verdicts.get(str(r["case_id"]))) for r in item_rows
        ]
        ht = None
        if row["ht_estimate"] is not None:
            ht = HTEstimate(
                estimate=float(row["ht_estimate"]),
                ci_low=float(row["ht_ci_low"] or row["ht_estimate"]),
                ci_high=float(row["ht_ci_high"] or row["ht_estimate"]),
                n_sampled=int(row["n_selected"] or 0),
                n_population=int(row["n_population"] or 0),
                confidence_level=0.95,
                method="Horvitz-Thompson with stratified variance",
                caveat=(
                    "Always present on stratified samples: the estimate is only "
                    "as good as the severity stratification. The PPS slice's "
                    "variance treats selections as independent (systematic "
                    "joint probabilities are not modelled) and ignores the "
                    "effect of diversity-cap exclusions; control strata use "
                    "exact SRS variance."
                ),
            )
        return ReviewPackOut(
            pack_id=str(row["pack_id"]),
            created_ts=str(row["created_ts"]),
            created_by=str(row["created_by"]),
            period_start=str(row["period_start"]),
            period_end=str(row["period_end"]),
            stratum=row["stratum"],
            n_target=int(row["n_target"] or 0),
            n_control=int(row["n_control"] or 0),
            n_selected=int(row["n_selected"] or 0),
            n_population=int(row["n_population"] or 0),
            items=items,
            ht_estimate=ht,
            diversity_caps_applied={
                k: int(v) for k, v in _json_dict(row["diversity_caps"]).items()
            },
            content_hash=row["content_hash"],
        )

    @staticmethod
    def _read_item(row: sqlite3.Row, verdict: str | None) -> ReviewPackItemOut:
        return ReviewPackItemOut(
            case_id=str(row["case_id"]),
            entity_id=str(row["entity_id"]),
            slice_type=SliceType(str(row["slice_type"])),
            case_risk_score=float(row["case_risk_score"] or 0.0),
            inclusion_prob=row["inclusion_prob"],
            severity=_severity(row["severity_norm"]),
            selected_because=str(row["selected_because"]),
            verification_prompts=[
                VerificationPrompt(**p)
                for p in _json_list(row["verification_prompts"])
                if isinstance(p, dict)
            ],
            contributing_indicators=_json_list(row["contributing_indicators"]),
            cluster_id=row["cluster_id"],
            analyst_pseudo=row["analyst_pseudo"],
            finding_ids=_json_list(row["finding_ids"]),
            verdict=verdict,
        )

    def list(self, limit: int = 100, offset: int = 0) -> list[ReviewPackListItem]:
        rows = get_connection().execute(
            "SELECT * FROM review_pack "
            "ORDER BY created_ts DESC, pack_id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [
            ReviewPackListItem(
                pack_id=str(r["pack_id"]),
                created_ts=str(r["created_ts"]),
                created_by=str(r["created_by"]),
                period_start=str(r["period_start"]),
                period_end=str(r["period_end"]),
                n_selected=int(r["n_selected"] or 0),
                ht_estimate=r["ht_estimate"],
                content_hash=r["content_hash"],
            )
            for r in rows
        ]

    def count(self) -> int:
        row = get_connection().execute(
            "SELECT COUNT(*) AS c FROM review_pack"
        ).fetchone()
        return int(row["c"] or 0) if row else 0

    # -------------------------------------------------------------- verdicts --
    def record_verdict(self, body: VerdictIn, actor: str) -> VerdictOut:
        """Record a supervisor's own conclusion about a selected case.

        `actor` becomes `examiner_pseudo` — the attribution is part of the
        record, not an audit detail. The verdict is ledgered with
        `verdict_recorded`; a recorded conclusion is never edited or withdrawn.
        """
        conn = get_connection()
        item = conn.execute(
            "SELECT 1 FROM review_pack_item WHERE pack_id = ? AND case_id = ?",
            (body.pack_id, body.case_id),
        ).fetchone()
        if item is None:
            raise ReviewPackError(
                f"case {body.case_id} is not an item of pack {body.pack_id}. "
                "A verdict may only be recorded for a case the pack actually "
                "selected — recording conclusions about unselected cases would "
                "make the pack's calibration untraceable."
            )

        recorded_ts = datetime.now(timezone.utc).isoformat()
        verdict_id = f"V-{uuid.uuid4().hex[:16]}"
        evidence_seen = json.dumps(body.evidence_seen or [])
        with transaction() as conn:
            conn.execute(
                "INSERT INTO verdict (verdict_id, pack_id, case_id, verdict, "
                " notes, evidence_seen, examiner_pseudo, recorded_ts) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    verdict_id,
                    body.pack_id,
                    body.case_id,
                    body.verdict.value,
                    body.notes,
                    evidence_seen,
                    actor,
                    recorded_ts,
                ),
            )
        ledger_hash = get_ledger().append(
            actor=actor,
            action=LedgerAction.VERDICT_RECORDED,
            payload={
                "verdict_id": verdict_id,
                "pack_id": body.pack_id,
                "case_id": body.case_id,
                "verdict": body.verdict.value,
                "notes": body.notes,
                "evidence_seen": body.evidence_seen or [],
                "recorded_ts": recorded_ts,
            },
        )
        return VerdictOut(
            verdict_id=verdict_id,
            pack_id=body.pack_id,
            case_id=body.case_id,
            verdict=body.verdict,
            notes=body.notes,
            examiner_pseudo=actor,
            recorded_ts=recorded_ts,
            ledger_entry_hash=ledger_hash,
        )

    def list_verdicts(
        self,
        pack_id: str | None = None,
        case_id: str | None = None,
    ) -> list[VerdictOut]:
        clauses: list[str] = []
        params: list[Any] = []
        if pack_id:
            clauses.append("pack_id = ?")
            params.append(pack_id)
        if case_id:
            clauses.append("case_id = ?")
            params.append(case_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = get_connection().execute(
            f"SELECT * FROM verdict {where} ORDER BY recorded_ts DESC",
            params,
        ).fetchall()
        return [
            VerdictOut(
                verdict_id=str(r["verdict_id"]),
                pack_id=str(r["pack_id"]),
                case_id=str(r["case_id"]),
                verdict=Verdict(str(r["verdict"])),
                notes=r["notes"],
                examiner_pseudo=str(r["examiner_pseudo"]),
                recorded_ts=str(r["recorded_ts"]),
                ledger_entry_hash="",
            )
            for r in rows
        ]


_service: ReviewPackService | None = None


def get_review_pack_service() -> ReviewPackService:
    global _service
    if _service is None:
        _service = ReviewPackService()
    return _service