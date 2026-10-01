# Orion — Team Progress Board

**This is the shared whiteboard. Every dev updates their own rows as they go, and commits.**

| | |
|---|---|
| **Plan of record** | `docs/implementation_plan_v2.md` |
| **Who owns what** | `docs/build-responsibility.md` |
| **Contracts for devs 1 & 3** | `docs/dev2-info-handover.md` |

> **How to use this board**
> - Update your own table. Move one task at a time — ⬜ → 🟡 → ✅ as you work.
> - Mark anything waiting on another dev as `⛔` and name them in the Notes column.
> - Don't batch updates to the end of the day. If you die mid-task, this board is what tells the team what to pick up.
> - Keep it honest. A blocked task that looks done costs far more than a task left red.
> - Commit it. It's shared state, not a private scratch file.

**Status key:** `⬜` not started · `🟡` in progress · `✅` complete · `⛔` blocked by another dev

**Last update:** Dev 2 — Phase 4 & 7 complete (see commit history for who changed what)

---

## Dev 1 — Frontend Lead

Owns `frontend/**`. Blocked on Dev 2's `/openapi.json` **for data only** — scaffolding is unblocked now.

| # | Task | Status | Notes |
|---|---|---|---|
| 1.0 | **Claim your rows** | ⬜ | Read `docs/dev2-info-handover.md` §5, claim 1.1–1.9 here before starting |
| 1.1 | Design system + app shell | ⬜ | `frontend/DESIGN.md`; **local fonts/icons only — no CDN** |
| 1.2 | Routing + typed API client | ⬜ | Type against `app/schemas/**` (frozen fields) |
| 1.3 | Executive view (EGI/NSI/DTS + 8-dim radar) | ⬜ | `entity.py` · unblocked |
| 1.4 | Review-Pack Workbench | ⬜ | `review_pack.py` · unblocked |
| 1.5 | Finding Card + evidence drawer | ⬜ | `finding.py` · unblocked |
| 1.6 | Submissions + DQ + assessability | ⬜ | `ingestion.py`, `assessability.py` · unblocked |
| 1.7 | Governance views (ledger, packs, policy) | ⬜ | `ledger.py` |
| 1.8 | Supervisory brief export | ⬜ | ⛔ blocked by Dev 2 export endpoint |
| 1.9 | Print / PDF styling | ⬜ | |

**Dependencies on Dev 2:** `/openapi.json` frozen at Phase 12 (~hour 18). All response schemas already written — column "schema to type against" in the handover.

---

## Dev 2 — Core Backend Lead *(me)*

Owns `backend/app/{api,db,schemas,services}`, `backend/scripts/`, `backend/deploy/`.

| # | Phase | Task | Status | Notes |
|---|---|---|---|---|
| 2.0 | Foundation | Skeleton, `requirements.txt`, `config.py`, `main.py` | ✅ | deps installed in `.venv` |
| 2.0 | Handover | `docs/dev2-info-handover.md` for devs 1 & 3 | ✅ | ⛔ awaiting Dev 3 contract review |
| 2.1 | Schema | SQLite state store (25 tables, WAL) | ✅ | `journal_mode=wal` verified |
| 2.1 | Schema | DuckDB evidence store (6 tables) | ✅ | single-writer enforced structurally |
| 2.2 | Schema | 8 Pydantic modules incl. **frozen** `indicator.py` | ✅ | invariants tested |
| 2.3 | Audit | Hash-chained append-only ledger | ✅ | tamper detected at `seq=1` |
| 2.4 | Privacy | HMAC pseudonymisation + note redaction | ✅ | per-entity salted; zero PII leakage |
| 2.5 | Ingestion | 10-stage pipeline, quarantine, DQ score | 🟡 | **next** |
| 2.6 | Ingestion | Vendor mapping YAML + fuzzy assistant | ⬜ | blocked by 2.5 |
| 2.7 | Assessability | Per-dimension matrix | ⬜ | needs 2.5 |
| 2.7 | Policy | Signed versioned policy profiles | ✅ | `policy_nccipc_default.yaml` |
| 2.7 | Baselines | LOO median/MAD + EB shrinkage + ranks | ✅ | MAD/IQR/stdev fallback added |
| 2.8 | Indicators | P0 EG-01…EG-11 | ⬜ | needs 2.5 |
| 2.8 | Indicators | NS-01/02/04/05 **stubs** | ⬜ | unblocks Dev 3 integration |
| 2.9 | Scoring | EGI/NSI/DTS/8 dims/SAP + tiers | ⬜ | needs 2.7, 2.8 |
| 2.10 | Evidence | Finding cards + counterfactual | ⬜ | |
| 2.11 | Review packs | PPS + π + controls + HT | ⬜ | |
| 2.12 | API | All `/api/v1` routers + OpenAPI freeze | ⬜ | **unblocks Dev 1** |
| 2.13 | Export | Supervisory brief, signed | ⬜ | ⛔ needs OQ-9 key custody |
| 2.14 | Packs | Stage/shadow/promote/rollback | ⬜ | ⛔ needs OQ-9 |
| 2.15 | Trends | Trends + change points | ⬜ | |
| 2.16 | P1 | EG-12…EG-17, NS-03/06 | ⬜ | |
| 2.17 | Ops | Sovereignty check, bundle, SBOM | ⬜ | high value, keep early |
| 2.18 | Tests | Full suite | ⬜ | |
| 2.19 | Integrate | Dev 3 handoff + E2E demo | ⬜ | |

**Blocking others:** Dev 1 needs 2.12. Dev 3 needs nothing — unblocked since Phase 2.

---

## Dev 3 — ML & Validation Lead

Owns `backend/app/ml/**`, `backend/eval/**`. **Fully unblocked** — frozen contracts shipped and tested.

| # | Task | Status | Notes |
|---|---|---|---|
| 3.0 | **Review contracts** | Read `docs/dev2-info-handover.md` §3–4 | ⬜ | **raise objections now** — cheaper than after you write code |
| 3.1 | Claim your rows | ⬜ | Mark 3.2–3.15 as claimed here |
| 3.1 | Contract review | Confirm `IndicatorResult` / `ModelOutput` fit | ⬜ | blocking others if a field is wrong |
| 3.2 | SOCSim | 20–30 entities, 8 archetypes, `archetypes.yaml` | ⬜ | diurnal/weekly/holiday arrivals |
| 3.3 | Injection | Dose parameter + hard negatives | ⬜ | ⛔ **Dev 3 only** — anti-circularity |
| 3.4 | Truth data | `entity_truth.csv`, `case_truth.csv` | ⬜ | |
| 3.5 | NS-01 | Silent critical assets (NB zero-run + BH) | ⬜ | stub ready at 2.8 |
| 3.6 | NS-02 | Absent alert categories (NB + persistence) | ⬜ | stub ready at 2.8 |
| 3.7 | NS-04 | Orphan records | ⬜ | stub ready at 2.8 |
| 3.8 | NS-05 | Implausibly low activity | ⬜ | stub ready at 2.8 |
| 3.9 | Outlier | Pooled LOO Isolation Forest, fixed seed | ⬜ | never fit on scored entity |
| 3.10 | Attribution | TreeSHAP / permutation | ⬜ | **required** or result = lead only |
| 3.11 | NLP | MinHash-LSH monoculture auditor | ⬜ | blocked by entity **and** analyst |
| 3.12 | ECOD | Entity-level per-dimension | ⬜ | P1 |
| 3.13 | Metrics | Entity/case/effort/power/safety/calibration | ⬜ | |
| 3.14 | Adversarial | Adaptive-gaming harness | ⬜ | |
| 3.15 | Bench | `scripts/bench/*` | ⬜ | hardware must be stated |

**Blocking others:** nothing. Dev 2 needs 3.5–3.8 at Phase 8 (~hour 12) to compute NSI. Until then honest stubs return `not_assessable`.

---

## Cross-dev blockers

| Blocker | Owner | Blocks | Status |
|---|---|---|---|
| **OQ-9** Ed25519 key custody — offline signer? hardware token? | Dev 2 | 2.13, 2.14 signed exports & packs | ⛔ open |
| **OQ-10** HMAC key rotation — needs Dev 3 agreement | Dev 2 + Dev 3 | rotation policy | ⛔ open |
| **OQ-6** Existing egress layer to reuse? | Dev 2 | 2.17 sovereignty check | ⛔ open |
| **OQ-8** Keep Alpha/Beta/Gamma as SOCSim presets? | Dev 3 | 3.2 | ⛔ open |
| Handle types for NS functions | Dev 2 proposes, Dev 3 confirms | 3.5–3.8 | 🟡 proposed in handover |
| `nlp_auditor` per-entity or per-cluster? | Dev 2 proposes per-entity, Dev 3 confirms | 3.11 | 🟡 proposed in handover |

---

## Claiming work

Two conventions so three people don't collide:

1. **Dev 3 should review the frozen contracts (3.0) before writing any model code.** If `IndicatorResult` or `ModelOutput` is missing a field your model needs, say so now — changing a frozen interface after integration costs all three of us, disagreeing before you write a line costs nothing.
2. **Each dev owns their own table.** Cross-owner edits need a PR note referencing `docs/build-responsibility.md` (rule 6: one owner per file).

---

## Ground rules — nobody breaks these

1. Air-gapped. No external APIs, no CDN, no web fonts. `sovereignty_check.py` must prove it.
2. **Hypotheses, never verdicts.** No "non-compliant", "violation", "grade".
3. **`Not assessable` ≠ low risk.** Distinct state. Never `0`.
4. No silent drops. Rejected rows go to `quarantine` and are visible to the examiner.
5. Ledger is append-only — enforced by SQLite triggers, not convention.
6. DuckDB is single-writer. Ingestion/runs are background jobs, never in a request thread.
7. Every finding reproducible — `reproduce_finding` returns identical row IDs.
8. **No invented numbers.** Perf figures are "target (to be benchmarked)" unless a bench script produced them with hardware stated.
9. **Injection scenarios belong to Dev 3.** Dev 2 does not tune against dose values.
10. Splits are **by entity, never by time**.
11. `extra="forbid"` on every schema. Unknown field = bug, not extension point.

---

## Milestones

| Milestone | Target | Status |
|---|---|---|
| Foundation + frozen contracts | hour 0–3 | ✅ |
| Data in (ingestion, DQ, assessability) | hour 8.5 | 🟡 |
| Signal out (indicators, scoring) | hour 14 | ⬜ |
| **CUT LINE — demo-ready** | **hour 14** | ⬜ |
| OpenAPI frozen → Dev 1 data unblocked | hour 18 | ⬜ |
| Pack lifecycle, trends, P1, offline bundle | hour 26 | ⬜ |
| E2E demo | hour 30 | ⬜ |

> Above the cut line, scope is cut in this order: pack rollback → change-point detection → extra export formats → ECOD.
> **Never cut:** finding cards with evidence, negative-space indicators, sovereignty check, validation numbers.