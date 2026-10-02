"""Evidence service — finding cards, per plan §8.1 and Phase 10.

Read side only. Findings are written by `ScoringService._persist_indicator_results`
in the same transaction as the scores, so a card can never describe a signal
that a run did not produce.

Every card answers three questions an examiner must not have to ask:

  1. Why was this flagged?        -> `why_flagged`
  2. Why was this NOT flagged?    -> `why_not_flagged` (incl. what was not computable)
  3. What would change it?        -> `counterfactual`

Gap rule that holds here as everywhere in this codebase: a field is null when
the value is unknown, never filled with an estimate presented as a fact.
`peer_sensitivity` is deliberately empty — peer-count sensitivity needs the peer
values each baseline was built from, and those are not stored, so it is omitted
rather than approximated.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from typing import Any

from app.db.sqlite import get_connection
from app.schemas.common import AttentionTier, PeriodOut
from app.schemas.finding import (
    BaselineView,
    ConfidenceView,
    CorroboratingSignal,
    Counterfactual,
    CounterfactualOut,
    EvidenceOut,
    EvidenceRowOut,
    FindingCardOut,
    FindingListItem,
    IndicatorNotComputable,
    IndicatorStatus,
    LineageOut,
    SuggestedAction,
    WhyFlagged,
    WhyNotFlagged,
)
from app.schemas.indicator import (
    Assessability,
    Baseline,
    ConfidenceBreakdown,
    Dimension,
)
from app.services.baseline_service import MAD_SCALE
from app.services.policy_profile import PolicyProfileService, policy_profile
from app.services.rules_engine import evidence_rows, get_rules_engine


class FindingNotFound(Exception):
    """Raised when a finding id does not exist."""


def _norm_inv(p: float) -> float:
    """Inverse standard-normal CDF, Acklam's rational approximation.

    `math.erfcinv` (which would give `sqrt(2)*erfcinv(q)` directly) is only
    available on Python >= 3.12; this stack targets 3.11, and adding scipy for
    one constant would break the air-gap discipline. Acklam's approximation is
    deterministic and accurate to ~1e-9 over the range this code uses (two-sided
    critical values for FDR bounds in (0, 1)).
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0, 1), got {p}")

    a = (-3.969683028665376e01, 2.209460984245205e02,
         -2.759285104469687e02, 1.383577518672690e02,
         -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02,
         -1.556989798598866e02, 6.680131188771972e01,
         -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e00, -2.549732539343734e00,
         4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e00, 3.754408661907416e00)

    if p < 0.5:
        return -_norm_inv(1.0 - p)
    if p < 0.02425:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q
                + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r
            + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r
                            + b[4]) * r + 1.0)


def _assessability(mult: float | None) -> Assessability:
    """Map the stored 0/0.5/1 multiplier back to the enum.

    Stored from `result.assessability.multiplier`. A missing value maps to
    NOT_ASSESSABLE rather than to nothing, because a row in the finding table
    was assessed the moment it was written.
    """
    return {
        1.0: Assessability.ASSESSABLE,
        0.5: Assessability.PARTIAL,
        0.0: Assessability.NOT_ASSESSABLE,
    }.get(mult, Assessability.NOT_ASSESSABLE)


def _json_list(value: str | None, default: list[Any] | None = None) -> list[Any]:
    if not value:
        return list(default or [])
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else list(default or [])
    except (TypeError, ValueError):
        return list(default or [])


class EvidenceService:
    """Read side of the finding tables."""

    def __init__(self) -> None:
        self._catalogue_cache: dict[str, dict[str, Any]] | None = None
        self._policy_service = PolicyProfileService()

    # ------------------------------------------------------------------ cache --
    def _catalogue(self) -> dict[str, dict[str, Any]]:
        if self._catalogue_cache is None:
            self._catalogue_cache = {
                c["indicator_id"]: c for c in get_rules_engine().catalogue()
            }
        return self._catalogue_cache

    def _name(self, indicator_id: str) -> str:
        entry = self._catalogue().get(indicator_id, {})
        return str(entry.get("name") or indicator_id)

    # ------------------------------------------------------------------ loads --
    def _load_finding(self, finding_id: str) -> Any:
        row = get_connection().execute(
            "SELECT f.*, e.name AS entity_name FROM finding f "
            "JOIN entity e ON e.entity_id = f.entity_id "
            "WHERE f.finding_id = ?",
            (finding_id,),
        ).fetchone()
        if row is None:
            raise FindingNotFound(
                f"finding_id {finding_id} does not exist. Findings are created "
                "by a completed scoring run, so a missing id means 'no such "
                "run result', not 'a finding was lost'."
            )
        return row

    def _load_run(self, run_id: str) -> Any:
        return get_connection().execute(
            "SELECT * FROM run WHERE run_id = ?", (run_id,)
        ).fetchone()

    def _fdr_q(self, run_id: str) -> float | None:
        """The FDR q the finding was subjected to.

        Read from the policy profile the *run* named, not from today's active
        policy: a counterfactual computed under one q and displayed under a
        later one would tell the examiner what a finding would do under rules it
        was never judged by. Falls back to the active profile only when the
        named one is gone.
        """
        run = self._load_run(run_id)
        profile_id = str(run["policy_profile_id"]) if run and run["policy_profile_id"] else None
        if profile_id:
            try:
                return float(self._policy_service.get(profile_id).fdr_q)
            except (KeyError, ValueError, TypeError):
                pass
        return float(policy_profile().fdr_q)

    # ------------------------------------------------------------------ list --
    def list_findings(
        self,
        entity_id: str | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> list[FindingListItem]:
        """Surviving findings, most adverse first.

        Low-confidence leads are excluded: a lead is a candidate that lacks
        evidence or attribution (plan §4.8.1) and is not presented as a finding.
        """
        where = ["f.is_low_confidence_lead = 0"]
        params: list[Any] = []
        if entity_id:
            where.append("f.entity_id = ?")
            params.append(entity_id)
        if period_start and period_end:
            where.append("f.period_start <= ? AND f.period_end >= ?")
            params += [period_end.isoformat(), period_start.isoformat()]
        elif period_start:
            where.append("f.period_end >= ?")
            params.append(period_start.isoformat())
        elif period_end:
            where.append("f.period_start <= ?")
            params.append(period_end.isoformat())

        rows = get_connection().execute(
            "SELECT f.*, e.name AS entity_name, "
            "es.sap_tier AS sap_tier FROM finding f "
            "JOIN entity e ON e.entity_id = f.entity_id "
            "LEFT JOIN entity_score es "
            "ON es.run_id = f.run_id AND es.entity_id = f.entity_id "
            "WHERE " + " AND ".join(where)
            + " ORDER BY f.effect_size DESC NULLS LAST, f.period_start DESC",
            params,
        ).fetchall()
        return [self._list_item(r) for r in rows]

    def _list_item(self, row: Any) -> FindingListItem:
        entry = self._catalogue().get(str(row["indicator_id"]), {})
        indicator_id = str(row["indicator_id"])
        name = self._name(indicator_id)
        return FindingListItem(
            finding_id=str(row["finding_id"]),
            entity_id=str(row["entity_id"]),
            entity_name=row["entity_name"],
            indicator_id=indicator_id,
            indicator_name=name,
            period=PeriodOut(start=row["period_start"], end=row["period_end"]),
            value=row["value"],
            effect_size=row["effect_size"],
            confidence=float(row["confidence"] or 0.0),
            primary_dimension=Dimension(row["primary_dimension"])
            if row["primary_dimension"]
            else None,
            family=row["family"],
            assessability=_assessability(row["assessability"]),
            is_low_confidence_lead=bool(row["is_low_confidence_lead"]),
            sap_tier=AttentionTier(str(row["sap_tier"])) if row["sap_tier"] else None,
            summary=self._summary(
                name,
                str(row["entity_name"] or row["entity_id"]),
                row["value"],
                row["value_units"],
                row["peer_median"],
                int(row["n_peers"] or 0),
                row["effect_size"],
                str(entry.get("adverse_direction") or "high"),
            ),
        )

    def _summary(
        self,
        name: str,
        entity_name: str,
        value: float | None,
        units: str | None,
        median: float | None,
        n_peers: int,
        effect: float | None,
        adverse: str,
    ) -> str:
        """One line, hypothesis-framed. Never a verdict."""
        parts: list[str] = []
        if value is not None:
            rendered = f"{value:g}"
            if units:
                rendered += f" {units}"
            parts.append(f"{name} placed {entity_name} at {rendered}")
        if median is not None and n_peers:
            relation = "below" if adverse == "low" else "above"
            parts.append(f"the peer median of {median:g} among {n_peers} peers")
        if effect is not None:
            parts.append(f"a robust z of {effect:+.2f} after shrinkage")
        if not parts:
            parts.append(f"{name} for {entity_name} was flagged")
        return "; ".join(parts) + " — a reason to look, never a determination."

    # ------------------------------------------------------------------ card --
    def get_finding(self, finding_id: str) -> FindingCardOut:
        row = self._load_finding(finding_id)
        run = self._load_run(str(row["run_id"]))
        entry = self._catalogue().get(str(row["indicator_id"]), {})
        indicator_id = str(row["indicator_id"])
        name = self._name(indicator_id)
        entity_name = str(row["entity_name"] or row["entity_id"])

        baseline_view = BaselineView(
            peer_median=row["peer_median"],
            peer_mad=row["peer_mad"],
            peer_percentile=row["peer_percentile"],
            n_peers=int(row["n_peers"] or 0),
            method=row["baseline_method"],
            cohort=row["baseline_cohort"],
        )
        confidence = self._confidence_view(row)
        why_not = self._why_not_flagged(row)

        return FindingCardOut(
            finding_id=str(row["finding_id"]),
            entity_id=str(row["entity_id"]),
            entity_name=entity_name,
            period=PeriodOut(start=row["period_start"], end=row["period_end"]),
            indicator_id=indicator_id,
            indicator_name=name,
            summary=self._summary(
                name, entity_name, row["value"], row["value_units"],
                row["peer_median"], int(row["n_peers"] or 0),
                row["effect_size"], str(entry.get("adverse_direction") or "high"),
            ),
            value=row["value"],
            value_units=row["value_units"],
            baseline=baseline_view,
            effect_size=row["effect_size"],
            n=int(row["n"] or 0),
            confidence=confidence,
            family=row["family"],
            primary_dimension=Dimension(row["primary_dimension"])
            if row["primary_dimension"]
            else None,
            secondary_dimensions=[
                Dimension(d) for d in _json_list(row["secondary_dimensions"])
                if d in {x.value for x in Dimension}
            ],
            source=str(row["source"]),
            is_low_confidence_lead=bool(row["is_low_confidence_lead"]),
            actor_type_inferred=bool(row["actor_type_inferred"]),
            why_flagged=self._why_flagged(row, name, entity_name,
                                          str(entry.get("adverse_direction") or "high"),
                                          baseline_view),
            why_not_flagged=why_not,
            counterfactual=self._counterfactual(row, entry),
            corroborating_signals=self._corroborating(finding_id, row),
            benign_explanations=_json_list(row["benign_explanations"]),
            suggested_actions=self._suggested_actions(finding_id, row),
            assessability=_assessability(row["assessability"]),
            missing_fields=_json_list(row["missing_fields"]),
            lineage=self._lineage(run),
            caveats=self._caveats(row),
        )

    def _confidence_view(self, row: Any) -> ConfidenceView:
        has_breakdown = all(
            row[k] is not None
            for k in ("conf_n_term", "conf_assessability_term", "conf_data_trust_term")
        )
        breakdown = None
        explanation: str | None = None
        if has_breakdown:
            breakdown = ConfidenceBreakdown(
                n_term=float(row["conf_n_term"]),
                assessability_term=float(row["conf_assessability_term"]),
                data_trust_term=float(row["conf_data_trust_term"]),
            )
            explanation = self._confidence_explanation(row)
        elif row["confidence"] is None:
            explanation = (
                "Confidence was not recorded for this finding (no evidence "
                "volume, assessability or data-trust terms were stored)."
            )
        return ConfidenceView(total=float(row["confidence"] or 0.0),
                              breakdown=breakdown, explanation=explanation)

    @staticmethod
    def _confidence_explanation(row: Any) -> str | None:
        terms = {
            "evidence volume": row["conf_n_term"],
            "assessability": row["conf_assessability_term"],
            "data trust": row["conf_data_trust_term"],
        }
        present = {k: v for k, v in terms.items() if v is not None}
        if not present:
            return None
        limiting = min(present, key=present.get)
        return (
            f"Confidence is capped by the smallest of the three multipliers; "
            f"{limiting} (factor {present[limiting]:.2f}) limited it most."
        )

    def _why_flagged(
        self, row: Any, name: str, entity_name: str, adverse: str,
        baseline_view: BaselineView,
    ) -> WhyFlagged:
        value = row["value"]
        median = row["peer_median"]
        n_peers = int(row["n_peers"] or 0)
        effect = row["effect_size"]
        parts: list[str] = []
        if value is not None:
            rendered = f"{value:g}"
            if row["value_units"]:
                rendered += f" {row['value_units']}"
            parts.append(f"reported {rendered}")
        if median is not None and n_peers:
            relation = "below" if adverse == "low" else "above"
            parts.append(f"the peer median of {median:g} ({n_peers} peers)")
        if effect is not None:
            parts.append(f"robust z {effect:+.2f} after shrinkage")
        sentence = ""
        if parts:
            sentence = f"{name} for {entity_name} " + ", ".join(parts) + ". "
        sentence += (
            "This is a reason to look, not a finding of non-compliance or a "
            "determination about this entity's capability."
        )
        return WhyFlagged(
            indicator_id=str(row["indicator_id"]),
            indicator_name=name,
            value=value,
            value_units=row["value_units"],
            baseline=baseline_view,
            effect_size=effect,
            statement=sentence,
        )

    def _why_not_flagged(self, row: Any) -> WhyNotFlagged:
        status_rows = get_connection().execute(
            "SELECT * FROM indicator_status WHERE run_id = ? AND entity_id = ?",
            (str(row["run_id"]), str(row["entity_id"])),
        ).fetchall()
        indicators_run: list[IndicatorStatus] = []
        not_computable: list[IndicatorNotComputable] = []
        other_raised = 0
        for s in status_rows:
            indicator_id = str(s["indicator_id"])
            if indicator_id == str(row["indicator_id"]):
                # The finding's own indicator is described by its card, not by
                # this list — which exists to explain the silences around it.
                continue
            name = self._name(indicator_id)
            raised = bool(s["raised"])
            if raised:
                other_raised += 1
            if s["value"] is not None:
                note: str | None = None
                if not raised and s["suppressed_reason"]:
                    note = (
                        "Signalled but suppressed: "
                        + str(s["suppressed_reason"])
                    )
                elif not raised and s["note"]:
                    # Measured but no comparison could be formed — the engine's
                    # own caveat (e.g. a single-valued cohort) must not read as
                    # "ran and stayed quiet".
                    note = "Measured but not comparable: " + str(s["note"])
                indicators_run.append(
                    IndicatorStatus(
                        indicator_id=indicator_id,
                        indicator_name=name,
                        value=s["value"],
                        effect_size=s["effect_size"],
                        raised=raised,
                        note=note,
                    )
                )
            else:
                not_computable.append(
                    IndicatorNotComputable(
                        indicator_id=indicator_id,
                        indicator_name=name,
                        missing_fields=_json_list(s["missing_fields"]),
                        required_tier=s["required_tier"],
                        assessability=_assessability(s["assessability"]),
                        reason=str(
                            s["not_computable_reason"]
                            or "could not be computed for this entity"
                        ),
                    )
                )

        entity_name = str(row["entity_name"] or row["entity_id"])
        statement = f"For {entity_name}, beyond this finding: "
        clauses: list[str] = []
        if other_raised:
            clauses.append(
                f"{other_raised} other indicator(s) raised in the same run and "
                "are listed as separate findings"
            )
        if indicators_run:
            clauses.append(
                f"{len(indicators_run)} indicator(s) ran and returned no "
                "adverse signal"
            )
        if not_computable:
            clauses.append(
                f"{len(not_computable)} indicator(s) could not be computed — "
                "reasons are listed, because a silence that was never measured "
                "is not the same as a measured silence"
            )
        if not clauses:
            statement += "no other indicator was examined for this entity in this run."
        else:
            statement += "; ".join(clauses) + "."
        return WhyNotFlagged(
            indicators_run=indicators_run,
            indicators_not_computable=not_computable,
            statement=statement,
        )

    def _corroborating(self, finding_id: str, row: Any) -> list[CorroboratingSignal]:
        """Other findings in the same run pointing the same way."""
        others = get_connection().execute(
            "SELECT * FROM finding WHERE run_id = ? AND entity_id = ? "
            "AND finding_id != ? AND is_low_confidence_lead = 0",
            (str(row["run_id"]), str(row["entity_id"]), finding_id),
        ).fetchall()
        this_effect = row["effect_size"]
        out: list[CorroboratingSignal] = []
        for o in others:
            other_effect = o["effect_size"]
            if (
                this_effect is not None
                and other_effect is not None
                and this_effect * other_effect < 0
            ):
                # Opposite direction: a cross-check, not corroboration.
                continue
            if o["family"] and o["family"] == row["family"]:
                relationship = "same_family"
            elif o["primary_dimension"] and o["primary_dimension"] == row["primary_dimension"]:
                relationship = "same_dimension"
            else:
                relationship = "independent_signal"
            out.append(
                CorroboratingSignal(
                    indicator_id=str(o["indicator_id"]),
                    indicator_name=self._name(str(o["indicator_id"])),
                    dimension=Dimension(o["primary_dimension"])
                    if o["primary_dimension"]
                    else None,
                    effect_size=other_effect,
                    confidence=float(o["confidence"] or 0.0),
                    relationship=relationship,
                )
            )
        return out

    def _suggested_actions(self, finding_id: str, row: Any) -> list[SuggestedAction]:
        evidence_ids = [
            str(r["row_id"])
            for r in get_connection().execute(
                "SELECT row_id FROM finding_evidence "
                "WHERE finding_id = ? ORDER BY ordinal", (finding_id,)
            )
        ]
        actions = [
            SuggestedAction(
                action="Examine the evidence rows",
                rationale=(
                    "Start at the rows behind this signal, not at the score. "
                    "Every card's evidence is a stored query that reproduce_finding "
                    "re-executes."
                ),
                evidence_row_ids=evidence_ids,
                priority="high",
            ),
            SuggestedAction(
                action="Verify reproducibility",
                rationale=(
                    f"Run scripts/reproduce_finding.py --finding {finding_id}; "
                    "identical row ids are part of the review."
                ),
                evidence_row_ids=[],
                priority="normal",
            ),
        ]
        benign = _json_list(row["benign_explanations"])
        if benign:
            actions.insert(
                1,
                SuggestedAction(
                    action="Rule out the benign explanations",
                    rationale=(
                        "Check each candidate innocent cause before acting: "
                        + ", ".join(str(b) for b in benign)
                    ),
                    evidence_row_ids=[],
                    priority="normal",
                ),
            )
        return actions

    def _lineage(self, run: Any) -> LineageOut:
        if run is None:
            return LineageOut(run_id="")
        return LineageOut(
            run_id=str(run["run_id"]),
            submission_manifest_hashes=_json_list(run["input_manifest_hashes"]),
            pack_version=run["pack_version"],
            policy_hash=run["policy_hash"],
            policy_profile_id=run["policy_profile_id"],
            code_version=run["code_version"],
            seed=run["seed"],
        )

    @staticmethod
    def _caveats(row: Any) -> list[str]:
        caveats: list[str] = []
        if row["notes"]:
            caveats.append(str(row["notes"]))
        if row["is_low_confidence_lead"]:
            caveats.append(
                "This is a low-confidence lead, not a finding: it lacks the "
                "evidence or attribution required to be presented as one."
            )
        return caveats

    # ------------------------------------------------------------ counterfactual --
    def _counterfactual(self, row: Any, entry: dict[str, Any]) -> Counterfactual:
        value = row["value"]
        median = row["peer_median"]
        mad = row["peer_mad"]
        effect = row["effect_size"]
        adverse = str(entry.get("adverse_direction") or "high")
        units = row["value_units"]
        missing = _json_list(row["missing_fields"])

        fdr_q = self._fdr_q(str(row["run_id"]))
        scale = MAD_SCALE * float(mad) if mad not in (None, 0) else None

        would_clear_at_value: float | None = None
        would_clear_at_effect_size: float | None = None
        would_raise_at_value: float | None = None
        if fdr_q and scale and median is not None:
            # Conservative bound: every kept signal satisfies p <= q*rank/n <= q,
            # so dropping below |z| where the two-sided p equals q guarantees a
            # clear under any rank the run could have assigned. Two-sided, so
            # z = Phi^-1(1 - q/2) == sqrt(2)*erfcinv(q).
            z_clear = abs(_norm_inv(1.0 - float(fdr_q) / 2.0))
            would_clear_at_effect_size = z_clear
            sign = 1.0 if adverse == "high" else -1.0
            would_clear_at_value = float(median + sign * z_clear * scale)
        if value is not None and median is not None:
            would_raise_at_value = float(median + 2.0 * (value - median))

        explanation: str
        if fdr_q and scale and median is not None:
            unit = f" {units}" if units else ""
            explanation = (
                f"A finding clears when its robust z falls below the adjusted "
                f"threshold in magnitude. At the {fdr_q:.2f} FDR bound of the "
                f"run's policy this is |z| < {would_clear_at_effect_size:.2f}, "
                f"approximately a value of {would_clear_at_value:.3g}{unit}. The "
                f"value figure treats the shrinkage applied at run time as "
                f"constant — the exact number depends on re-estimating the "
                f"cohort, which reproduce_finding does."
            )
            if would_raise_at_value is not None:
                explanation += (
                    f" Moving the value to {would_raise_at_value:.3g}{unit} "
                    "would double the current deviation from the peer median."
                )
        else:
            explanation = (
                "The clearing threshold cannot be computed from the stored "
                "baseline (the median or MAD needed for the effect-size scale "
                "is missing). Re-run the indicator via reproduce_finding to "
                "recover it."
            )
        if missing:
            explanation += (
                " The finding also rests on partially assessable evidence; "
                "supplying the missing fields and re-running may clear or "
                "strengthen it: "
                + ", ".join(str(m) for m in missing)
                + "."
            )
        return Counterfactual(
            would_clear_at_value=would_clear_at_value,
            current_value=value,
            would_clear_at_effect_size=would_clear_at_effect_size,
            current_effect_size=effect,
            required_fields_if_missing=missing,
            would_raise_at_value=would_raise_at_value,
            explanation=explanation,
        )

    # ---------------------------------------------------------------- evidence --
    def get_evidence(self, finding_id: str) -> EvidenceOut:
        row = self._load_finding(finding_id)
        query = row["evidence_query"]
        if not query:
            return EvidenceOut(
                stored_query=None, query_language="sql",
                rows=[], row_count=0, row_id_hash=None,
            )
        records, id_column = evidence_rows(str(query))
        table = ""
        try:
            table = str(json.loads(query).get("table") or "")
        except (TypeError, ValueError):
            pass
        rows = [
            EvidenceRowOut(
                table_name=table,
                row_id=str(r[id_column]),
                fields={k: v for k, v in r.items() if k != id_column},
                # The store already holds pseudonymised rows, so the fields here
                # are what the detector saw, nothing more to redact on read.
                redaction_note=None,
            )
            for r in records
        ]
        ids = [str(r[id_column]) for r in records]
        digest = hashlib.sha256(json.dumps(ids).encode("utf-8")).hexdigest()
        return EvidenceOut(
            stored_query=str(query),
            query_language="sql",
            rows=rows,
            row_count=len(rows),
            row_id_hash=digest,
        )

    def get_counterfactual(self, finding_id: str) -> CounterfactualOut:
        row = self._load_finding(finding_id)
        entry = self._catalogue().get(str(row["indicator_id"]), {})
        baseline = Baseline(
            median=row["peer_median"],
            mad=row["peer_mad"],
            percentile=row["peer_percentile"],
            n_peers=int(row["n_peers"] or 0),
            method=row["baseline_method"],
            cohort=row["baseline_cohort"],
        )
        return CounterfactualOut(
            finding_id=str(row["finding_id"]),
            indicator_id=str(row["indicator_id"]),
            counterfactual=self._counterfactual(row, entry),
            baseline=baseline,
            # Deliberately empty: peer-count sensitivity needs the peer values
            # each baseline was built from, which are not stored. Filling this
            # with an estimate would violate the never-fabricated rule.
            peer_sensitivity=[],
        )


_service: EvidenceService | None = None


def get_evidence_service() -> EvidenceService:
    global _service
    if _service is None:
        _service = EvidenceService()
    return _service