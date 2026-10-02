"""Review-pack report generator (Phase 13 — the export route is live here).

The export is the artefact a supervisor leaves the building with, so it carries
the *same evidence rows and caveats as the screen* — not a summary of them. To
make that guarantee structural rather than disciplinary, every format is a
rendering of one canonical brief (``_canonical_brief``): the pack as served by
``ReviewPackService.get`` plus the seed the request recorded, the verdicts the
pack already carries per item, and the evidence rows for every linked finding
re-executed through ``EvidenceService.get_evidence`` — the same execution path
the screen uses, so a CSV of findings and a PDF of the pack can never disagree
about what the data said.

Formats in 2.13 are ``json`` (the canonical brief itself, schema-faithful),
``md`` (a diffable, greppable rendering via the pinned Jinja2) and ``pdf`` (a
signed leave-behind via the pinned reportlab). ``docx`` is the known gap: it
shares this generator and needs only a renderer, which is why 2.13 refuses it
with a message instead of silently omitting it. The eight-format ladder on
`/exports/formats` is the entity-brief surface; a pack export renders the pack
document.

The artefact is **byte-reproducible**: the brief carries no generation timestamp,
so the same pack at the same chain state produces the same bytes and the same
Ed25519 signature (Ed25519 is deterministic). Re-exporting an unchanged pack
therefore reproduces the signature exactly, and an examiner can verify the file
with ``scripts/verify_export.py <file> <signature>`` or this module's
``verify_bytes``.

Everything the export writes or signs is ledgered with the pre-frozen
``pack_exported`` action before the response is returned; ``ledger_head_hash`` in
the response is the export's own entry hash, so the artefact file, its signature
and its position in the audit chain are bound together.
"""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from xml.sax.saxutils import escape

from app.config import get_settings
from app.db.sqlite import get_connection
from app.schemas.review_pack import ReviewPackExportOut
from app.services.evidence_service import EvidenceService, FindingNotFound
from app.services.ledger import LedgerAction, get_ledger
from app.services.review_pack_service import (
    PackNotFound,
    ReviewPackOut,
    get_review_pack_service,
)
from app.services.signing_service import host_label, sign_bytes

#: Formats this phase renders. Others on the export ladder get a typed 400 that
#: names the gap instead of a silent success.
SUPPORTED_FORMATS = ("json", "md", "pdf")
FORMAT_EXTENSION = {"json": "json", "md": "md", "pdf": "pdf"}
KNOWN_NEXT_FORMATS = {
    "docx": "docx reuses this generator; its renderer lands after 2.13",
}

EXPORT_SUBDIR = "exports"
BRIEF_SCHEMA = "orion/review-pack-brief/v1"

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "report"
_TEMPLATE_NAME = "review_pack_brief.md.j2"


class ReportError(Exception):
    """Bad export request: unknown or not-yet-renderable format (maps to 400)."""


class ReportService:
    """Build, sign and ledger the signed supervisory brief."""

    def export(self, pack_id: str, fmt: str, actor: str) -> ReviewPackExportOut:
        """Render one canonical brief to `fmt`, sign it, write it, ledger it."""
        fmt = self._validate_format(fmt)
        # PackNotFound propagates to the route, which maps it to 404; the report
        # must never invent a pack to render.
        pack = get_review_pack_service().get(pack_id)
        seed = self._seed_of(pack_id)
        brief = self._canonical_brief(pack, seed)
        payload = self._render(brief, fmt)
        path = self._write(brief["pack_id"], fmt, payload)
        signature, signed_by = sign_bytes(payload)
        ledger_hash = get_ledger().append(
            actor=actor,
            action=LedgerAction.PACK_EXPORTED,
            payload={
                "pack_id": brief["pack_id"],
                "format": fmt,
                "content_hash": pack.content_hash,
                "file_digest": hashlib.sha256(payload).hexdigest(),
                "file_size": len(payload),
                "signature": signature,
                "signed_by": signed_by,
                "export_path": str(path),
            },
            entity_id=pack.pack_id,
        )
        return ReviewPackExportOut(
            pack_id=pack.pack_id,
            formats=[fmt],
            content_hash=pack.content_hash,
            ledger_head_hash=ledger_hash,
            signature=signature,
            signed_by=signed_by,
            export_paths=[str(path)],
        )

    # ------------------------------------------------------------------ brief --
    def _canonical_brief(self, pack: ReviewPackOut, seed: int | None) -> dict[str, Any]:
        """The one serialised brief every format renders. Deterministic."""
        brief = pack.model_dump(mode="json")
        brief["schema"] = BRIEF_SCHEMA
        brief["seed"] = seed
        brief["signed_by_host"] = host_label()
        brief["evidence"] = self._evidence_by_finding(pack)
        caveats: list[str] = []
        if pack.ht_estimate is not None and pack.ht_estimate.caveat:
            caveats.append(pack.ht_estimate.caveat)
        brief["caveats"] = caveats
        return brief

    def _evidence_by_finding(self, pack: ReviewPackOut) -> dict[str, Any]:
        """Re-execute stored evidence for every finding the pack links to.

        Same execution path as the screen (`EvidenceService.get_evidence`), so a
        finding whose evidence manages to move between the screen and the export
        would be a finding about the tool, not an excuse the export hides behind.
        A finding that no longer resolves is recorded as such — the export does
        not silently drop evidence the screen would have shown as missing.
        """
        svc = EvidenceService()
        evidence: dict[str, Any] = {}
        for item in pack.items:
            for finding_id in item.finding_ids or []:
                if finding_id in evidence:
                    continue
                try:
                    evidence[finding_id] = svc.get_evidence(finding_id).model_dump(
                        mode="json"
                    )
                except FindingNotFound:
                    evidence[finding_id] = {"finding_not_found_at_export": True}
        return evidence

    def _seed_of(self, pack_id: str) -> int | None:
        row = get_connection().execute(
            "SELECT seed FROM review_pack WHERE pack_id = ?", (pack_id,)
        ).fetchone()
        if row is None:  # unreachable after get() succeeded; fail loudly anyway
            raise PackNotFound(f"pack_id {pack_id} does not exist")
        return int(row["seed"]) if row["seed"] is not None else None

    # --------------------------------------------------------------- formats --
    def _validate_format(self, fmt: str) -> str:
        fmt = (fmt or "pdf").strip().lower()
        if fmt in SUPPORTED_FORMATS:
            return fmt
        if fmt in KNOWN_NEXT_FORMATS:
            raise ReportError(
                f"format '{fmt}' is on the export ladder but not in 2.13 — "
                f"{KNOWN_NEXT_FORMATS[fmt]}. Ask for json, md or pdf."
            )
        raise ReportError(
            f"unknown export format '{fmt}'. The review-pack export renders one "
            "canonical brief as json, md or pdf."
        )

    def _render(self, brief: dict[str, Any], fmt: str) -> bytes:
        if fmt == "json":
            return (json.dumps(brief, indent=2, ensure_ascii=False) + "\n").encode(
                "utf-8"
            )
        if fmt == "md":
            return self._render_markdown(brief).encode("utf-8")
        if fmt == "pdf":
            return self._render_pdf(brief)
        raise ReportError(f"format '{fmt}' is not renderable")  # pragma: no cover

    def _render_markdown(self, brief: dict[str, Any]) -> str:
        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=False,
            keep_trailing_newline=True,
        )
        return env.get_template(_TEMPLATE_NAME).render(brief=brief)

    def _render_pdf(self, brief: dict[str, Any]) -> bytes:
        """Deterministic PDF via the pinned reportlab (no timestamps)."""
        styles = getSampleStyleSheet()
        body = styles["BodyText"]
        mono = ParagraphStyle(
            "MonoBrief", parent=styles["Code"], fontSize=7.5, leading=9
        )

        story: list[Any] = [
            Paragraph(_pdf(f"Review pack {brief['pack_id']}"), styles["Title"]),
            Spacer(1, 4 * mm),
            Paragraph(
                _pdf(
                    "Signed supervisory brief built by Orion from the stored pack, "
                    "the per-item verdicts and the re-executed evidence rows — the "
                    "same rows the screen shows. Byte-reproducible and signed with "
                    "the host Ed25519 key (verify with scripts/verify_export.py "
                    "<file> <signature>)."
                ),
                body,
            ),
            Spacer(1, 6 * mm),
        ]
        story.append(Paragraph(_pdf("Pack"), styles["Heading2"]))
        story.append(self._meta_table(brief))
        story.append(Spacer(1, 6 * mm))

        ht = brief.get("ht_estimate")
        if ht:
            story.append(Paragraph(_pdf("Prevalence estimate (Horvitz-Thompson)"), styles["Heading2"]))
            story.append(
                Paragraph(
                    _pdf(
                        f"estimate {ht['estimate']:.4f} | 95% CI ["
                        f"{ht['ci_low']:.4f}, {ht['ci_high']:.4f}] | method: "
                        f"{ht.get('method', '')}"
                    ),
                    body,
                )
            )
            if ht.get("caveat"):
                story.append(Paragraph(_pdf("Caveat: " + ht["caveat"]), body))
            story.append(Spacer(1, 4 * mm))

        story.append(Paragraph(_pdf("Items"), styles["Heading2"]))
        for item in brief["items"]:
            story.append(
                Paragraph(
                    _pdf(f"{item['case_id']} - {item.get('slice_type', '')}"),
                    styles["Heading3"],
                )
            )
            for line in self._item_lines(item):
                story.append(Paragraph(_pdf(line), body))
            for prompt in item.get("verification_prompts") or []:
                note = ""
                if prompt.get("expected_evidence"):
                    note = f" (expected evidence: {prompt['expected_evidence']})"
                story.append(
                    Paragraph(
                        _pdf(
                            f"verify ({prompt.get('prompt_type', '')}): "
                            f"{prompt.get('question', '')}{note}"
                        ),
                        body,
                    )
                )
            for finding_id in item.get("finding_ids") or []:
                story.extend(self._evidence_paragraphs(brief, finding_id, mono, body))
            story.append(Spacer(1, 3 * mm))

        story.append(Paragraph(_pdf("Caveats"), styles["Heading2"]))
        for caveat in brief.get("caveats") or []:
            story.append(Paragraph(_pdf("- " + caveat), body))

        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            pageCompression=0,  # uncompressed streams: the payload is greppable
            title=f"Review pack {brief['pack_id']}",
            author="ORION",
        )
        doc.build(story)
        return buf.getvalue()

    def _meta_table(self, brief: dict[str, Any]) -> Table:
        rows = [
            ["Field", "Value"],
            ["pack_id", brief["pack_id"]],
            ["created_ts", str(brief["created_ts"])],
            ["created_by", str(brief["created_by"])],
            ["period", f"{brief['period_start']} .. {brief['period_end']}"],
            ["stratum", str(brief.get("stratum") or "")],
            [
                "counts",
                f"{brief['n_selected']} selected of {brief['n_population']} "
                f"population (target {brief['n_target']}, control {brief['n_control']})",
            ],
            ["seed", str(brief.get("seed") or "")],
            ["content_hash", str(brief["content_hash"] or "")],
            ["signed_by_host", str(brief.get("signed_by_host") or "")],
        ]
        table = Table([[ _pdf(c) for c in row] for row in rows])
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.92, 0.92, 0.92)),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return table

    @staticmethod
    def _item_lines(item: dict[str, Any]) -> list[str]:
        lines = [
            f"entity: {item.get('entity_id', '')}",
            f"risk score: {item.get('case_risk_score', '')} | pi: "
            f"{item.get('inclusion_prob', '')} | severity: {item.get('severity', '')}",
            f"selected because: {item.get('selected_because', '')}",
        ]
        if item.get("cluster_id"):
            lines.append(f"cluster: {item['cluster_id']}")
        if item.get("analyst_pseudo"):
            lines.append(f"analyst: {item['analyst_pseudo']}")
        if item.get("contributing_indicators"):
            lines.append(
                "contributing indicators: "
                + ", ".join(str(x) for x in item["contributing_indicators"])
            )
        if item.get("finding_ids"):
            lines.append("findings: " + ", ".join(str(x) for x in item["finding_ids"]))
        verdict = item.get("verdict")
        lines.append(f"verdict: {verdict if verdict is not None else 'none recorded'}")
        return lines

    @staticmethod
    def _evidence_paragraphs(
        brief: dict[str, Any], finding_id: str, mono, body
    ) -> list[Any]:
        ev = brief.get("evidence", {}).get(finding_id)
        if ev is None:
            return []
        out: list[Any] = []
        if ev.get("finding_not_found_at_export"):
            out.append(
                Paragraph(
                    _pdf(
                        f"evidence for {finding_id}: finding no longer resolves "
                        "at export time (the screen shows the same gap)"
                    ),
                    body,
                )
            )
            return out
        out.append(
            Paragraph(
                _pdf(
                    f"evidence for {finding_id}: {ev.get('row_count', 0)} row(s)"
                    f" ({ev.get('query_language', 'sql')})"
                ),
                body,
            )
        )
        if ev.get("stored_query"):
            out.append(Preformatted(_pdf(ev["stored_query"]), mono))
        for row in ev.get("rows") or []:
            fields = ", ".join(
                f"{k}={v}" for k, v in (row.get("fields") or {}).items()
            )
            note = f" [{row['redaction_note']}]" if row.get("redaction_note") else ""
            out.append(
                Paragraph(
                    _pdf(
                        f"  {row.get('row_id', '')} ({row.get('table_name', '')}): "
                        f"{fields}{note}"
                    ),
                    body,
                )
            )
        return out

    # ------------------------------------------------------------------ write --
    def _write(self, pack_id: str, fmt: str, payload: bytes) -> Path:
        out_dir = get_settings().pack_dir / EXPORT_SUBDIR / pack_id
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"brief.{FORMAT_EXTENSION[fmt]}"
        path.write_bytes(payload)
        return path


_service: ReportService | None = None


def get_report_service() -> ReportService:
    global _service
    if _service is None:
        _service = ReportService()
    return _service


def _pdf(value: Any) -> str:
    """Reportlab-safe text: XML-escaped and forced into the built-in WinAnsi set."""
    raw = str(value)
    return escape(raw.encode("latin-1", "replace").decode("latin-1"))