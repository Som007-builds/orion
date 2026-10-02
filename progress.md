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

**Last update:** Dev 2 — Phase 15 (2.15) complete: `GET /trends` + `GET /trends/change-points` live (gap vs un-scored kept distinct; deterministic least-squares regime segmentation on per-period cohort medians — direction reports the measured shift, never a verdict). 106 tests green. Open for Dev 3: OQ-10 HMAC rotation, 2.11 `REVIEW_PACK_CREATED` sign-off, identity decision in `resolve_actor`.

---

## Dev 1 — Frontend Lead Hula

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

## Dev 2 — Core Backend Lead chom

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
| 2.5 | Ingestion | 10-stage pipeline, quarantine, DQ score | ✅ | 8 formats, 13-section smoke green; DQ tier A verified |
| 2.6 | Ingestion | Vendor mapping YAML + fuzzy assistant | ✅ | Splunk + Elastic profiles; suggests, never applies |
| 2.7 | Assessability | Per-dimension matrix | ✅ | 8 dims, §4.4 verbatim; never-submitted vs blank distinguished |
| 2.7 | Policy | Signed versioned policy profiles | ✅ | `policy_nccipc_default.yaml` |
| 2.7 | Baselines | LOO median/MAD + EB shrinkage + ranks | ✅ | MAD/IQR/stdev fallback added |
| 2.8 | Indicators | P0 EG-01…EG-11 | ✅ | 6 indicators in `app/services/rules_engine.py`; 14-section smoke green. **3 bugs fixed that would have shipped** — see notes below |
| 2.8 | Indicators | NS-01/02/04/05 **stubs** | ✅ | `rules_engine.ns_stub()`. `missing_fields` deliberately **empty** — an unwritten detector is not an absence of evidence. Drop real code in behind the same signature |
| 2.9 | Scoring | EGI/NSI/DTS/8 dims/SAP + tiers | ✅ | `app/services/scoring_service.py`, 13-section smoke green. **4 more bugs caught** — see notes below |
| 2.10 | Evidence | Finding cards + counterfactual | ✅ | findings materialised with scores: FDR-surviving, unsuppressed, signal > 0. Card answers why / why-not / counterfactual from stored rows; `note` keeps "measured but not comparable" honest; evidence re-runnable via `scripts/reproduce_finding.py`; +20 read-path tests; phase smoke 20/20 |
| 2.11 | Review packs | PPS + π + controls + HT | ✅ | five live routes: GET/POST `/review-packs`, GET `/review-packs/{id}`, POST/GET `/verdicts`. Every item carries its inclusion probability π; targeted slice is ordered systematic PPS (never selects a zero-risk case), controls are severity-stratified SRS from the complement; HT prevalence with 95% CI (Poisson PPS + exact SRS variance), seeded reproducibility, `review_pack_created` / `verdict_recorded` ledgered. `/export` still 503 → 2.13. **`LedgerAction` grew an additive member `REVIEW_PACK_CREATED` — needs Dev 3 sign-off (frozen interface #5).** +22 tests |
| 2.12 | API | All `/api/v1` routers + OpenAPI freeze | ✅ | 45 ops / 42 paths frozen in `docs/openapi-v1.json` + no-diff test; 28 tests. **Auth gap: header-derived actor labels but does NOT verify — see below** |
| 2.13 | Export | Supervisory brief, signed | ✅ | `GET /review-packs/{id}/export` live: one canonical brief (pack + per-item verdicts + **re-executed evidence rows** via `EvidenceService.get_evidence` — the same execution path as the screen) rendered to json/md/pdf with the pinned Jinja2/reportlab; signed with the host's Ed25519 key made on first use into `data/keys/` (**OQ-9 code shipped**), `pack_exported` ledgered, byte-reproducible (same key/bytes ⇒ same signature), `ledger_head_hash` = the export's own entry hash. `docx` shares the generator, renderer deliberate gap in 2.13 (typed 400 naming it). Verify: `backend/scripts/verify_export.py`. +12 tests; suite 82 green including the lake-wipe fixture self-heal |
| 2.14 | Packs | Stage/shadow/promote/rollback | ✅ | Signed pack lifecycle live: `POST /packs/stage` (201) copies bundle to `data/packs/staged/`, hashes deterministically, signs the hash with the host key (same `sign_bytes` as 2.13, `orion-pack:v1:` prefix); `shadow` re-executes the pack's policy over the live window *without persisting* and diffs against stored findings (0/0/0 identical-policy is a genuine engine reproducibility check) with a mechanical promote/hold recommendation; `promote` (requires shadow) installs to `active/`, re-adopts the bundled policy only when the content_hash differs, demotes the predecessor to `rolled_back`; `rollback` restores the *immediate* predecessor one step at a time. Every step ledgered (`pack_staged`/`pack_shadow_run`/`pack_promoted`/`pack_rolled_back`); runs record `pack_version` = the active pack's version (when its policy is live). `PackError`→400 via the shared classifier, `PackNotFound`→404. Verify: `backend/scripts/verify_pack.py`. +11 tests; suite 93 green. Also fixed a latent test-isolation trap: `packs.py` must not instantiate services at import time (module import warms `get_settings()` before `tests/__init__` redirects the temp env under `discover tests`); `.gitignore` now also covers `data/packs/active/` |
| 2.15 | Trends | Trends + change points | ✅ | `GET /trends` live: per-entity series (oldest period first) over `entity_score` — a **gap** (no row where the cohort scored: chart must break the line) is kept distinct from **un-scored** (row exists, metric NULL: "could not be scored" ≠ "scored nothing"), `n_scored` counts only computed values, lineage `run_id` per point; empty id → 400, unknown entity → 404, bad metric → 422. `GET /trends/change-points` live: least-squares binary segmentation over per-period **cohort median** — deterministic (no RNG, so "fixed seed" holds by construction), floors `MIN_SHIFT` 0.10 / `MIN_SSE_IMPROVEMENT` 0.20, `MIN_COHORT` 3 entities per period, `MIN_SEGMENT` 3 periods per side; `direction` is the sign of the measured shift, never a verdict (an improvement fires as reliably as a deterioration); sparse/too-short histories say so in `note`; optional `period_start/end` window (additive to the bare stub surface). New `app/schemas/trends.py` + `app/services/trend_service.py`. +13 tests; suite 106 green; fixture seeds a 2025 window so its cohort never collides with the 2026 fixtures |
| 2.16 | P1 | EG-12…EG-17, NS-03/06 | ⬜ | |
| 2.17 | Ops | Sovereignty check | ✅ | `backend/scripts/sovereignty_check.py` — zero outbound attempts over a full pipeline run, and the guard is proved able to deny. Bundle/SBOM/restore still ⬜ |
| 2.17b | Ops | Offline bundle, wheelhouse, SBOM, restore | ⬜ | ⛔ after 2.10/2.11; sizing targets only, no measured perf claims |
| 2.18 | Tests | Full suite | ⬜ | |
| 2.19 | Integrate | Dev 3 handoff + E2E demo | ⬜ | |

**Blocking others:** Dev 1 was unblocked at 2.12 — `/api/v1` frozen. Dev 3 needs nothing — unblocked since Phase 2.

**Labelled gaps (Dev 2, explicit so they are not silent):**

- **Authentication labels, it does not verify.** There is no identity provider in the offline deployment, so `X-Actor`/`X-Role` headers are resolved and gated but never checked against a credential. In dev the gate is permissive; outside dev an unattributed write is refused (401) and reads stay open. Dev 3 owns the identity decision — the seam is `resolve_actor` in `app/api/v1/deps.py`, one function to replace.
- **Perf figures are targets, not measured.** No bench script has run on stated hardware yet (2.18).

---

## Dev 3 — ML & Validation Lead  Joy

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
| **OQ-9** Ed25519 key custody | Dev 2 | 2.13, 2.14 signed exports & packs | ✅ **resolved** — host-local key, generated on first use into `backend/data/keys/` (gitignored), never leaves the machine. Signed artefacts are labelled *attributable to the signing host, not to an individual* — we do not claim non-repudiation we cannot back. Revisit if NCIIPC supplies a PKI or token. |
| **OQ-10** HMAC key rotation — needs Dev 3 agreement | Dev 2 + Dev 3 | rotation policy | ⛔ open |
| **OQ-6** Existing egress layer to reuse? | Dev 2 | 2.17 sovereignty check | ⛔ open |
| **OQ-8** Keep Alpha/Beta/Gamma as SOCSim presets? | Dev 3 | 3.2 | ⛔ open |
| Handle types for NS functions | Dev 2 proposes, Dev 3 confirms | 3.5–3.8 | 🟡 proposed in handover |
| `nlp_auditor` per-entity or per-cluster? | Dev 2 proposes per-entity, Dev 3 confirms | 3.11 | 🟡 proposed in handover |

---

## Schema changes Dev 3 should know about

Cross-cutting, so they're here and not buried in a commit. Anything below landed in Phase 8.

| # | Change | Why | What it means for Dev 3 |
|---|---|---|---|
| S-4 | **DuckDB `escalation` gained `entity_id`** (injected at load, like `alert_record`/`case_record`) | `case_id` is unique *within a submission*, not across the lake. EG-06 was looking up "which case ids were escalated" across all entities, so one entity's escalation vouched for another's case of the same number and peer cohorts masked each other's missing escalations. EG-06 under-reported exactly where it compares entities. | Query `escalation` **by `entity_id`**. If you write escalation joins, join on `(entity_id, case_id)` — never `case_id` alone. Added via an additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, so existing lakes migrate on next `init()`; no rebuild. |
| S-4b | `record_version.record_hash` now hashes a **digest of the redacted note text** instead of `note_ref` | `note_ref` is a fresh random id per load, so it was in the hash and *every* record moved on *every* resubmission — EG-11 would have reported 100% retroactive edits, forever. Rewriting a justification still moves the hash (signal preserved); reloading unchanged text does not. | Nothing. Just don't reintroduce generated identifiers into content hashes. |
| — | `assessability.field_requirement()` is the **only** supported way to build a `FieldRequirement` | `FieldRequirement.in_state_store` defaults to `False`, and forgetting it is silent: the field gets probed against DuckDB, `note_store` is never in `tables_present`, and the indicator declines on evidence that is sitting right there in SQLite. This bit EG-02, EG-09 and EG-11 at once. | Use `field_requirement(table, column, label=...)` rather than constructing the model directly. |

### Three bugs Phase 8 caught that would otherwise have shipped

Worth recording because the *shape* of each is the point — all three are indicators that would have lied confidently:

1. **EG-06 reported "100% of critical closures unescalated" from an entity that never sent an escalation export.** A finding built entirely out of the evidence needed to refute it. Fixed with `absence_disqualifies` on `IndicatorSpec` — a required field whose absence forces `NOT_ASSESSABLE` rather than `PARTIAL`, because `Partial` is only honest when the remaining evidence still supports an observation.
2. **EG-11 reported every record as retroactively edited**, for the hash reason above. An indicator that always fires is as useless as one that never fires, and worse, it teaches the examiner to ignore the card.
3. **Every SQLite-side requirement (`note_store`, `submission`, `record_version`) was probed as a DuckDB table**, so all three indicators declined on data that was present. Fixed at the source (S-4b row above), not at each call site.

Also fixed: stored evidence queries now `CAST(? AS TIMESTAMP)` their period bounds. The reproduction test caught this — a stored record is JSON, so bounds re-arrive as strings and DuckDB won't compare `TIMESTAMP` to `VARCHAR`. A re-runnable query has to stand alone.

### Four bugs Phase 9 caught that would otherwise have shipped

Same reason as above — each of these produces a *confident wrong answer*, which is the only kind of bug this tool cannot afford:

1. **Every compliant entity was reported `not_assessable`.** An indicator whose effect size came back null was dropped from the signal list, so a clean family had no score, so a clean dimension had no score, so `sap` was `None` and the tier defaulted to `not_assessable`. Ten of eleven entities in the smoke cohort were filed as *unexaminable* when they had in fact been examined and found fine — the exact mirror image of the error the tool exists to prevent, and it would have hit every well-run entity in the country. Fixed by separating **presence** from **magnitude**: a family whose detectors all ran and all came back null scores `0.0` rather than vanishing from the mapping. A zero is the answer "measured, and nothing was found", and it is the only answer that lets a clean entity be reported as clean.
2. **The most adverse entity in the cohort was graded `T4`, the lowest attention tier, while holding rank 1.** The tier cutpoints assume all eight dimensions answered. With two of eight measurable the ceiling is `0.16 x 0.3 = 0.05`, so `T1` (0.75) is unreachable *by construction* and everything collapses into `T4`. Fixed with `min_measurable_share` (0.50) in policy: below it the tier is withheld and reported `not_assessable`, while the SAP and every dimension score stay published. The number is real; it just cannot be graded yet.
3. **`rank_interval` could return an interval excluding its own point estimate**, and it broke ties by sort order inside the resampling draws while using competition ranking for the point rank. Nine entities at SAP 0.0 got nine different intervals. Both fixed: draws now use the same competition ranking, and the interval is clamped to contain the observed rank. A percentile interval that excludes the observed value is a mis-specified one, and it is worse than useless to someone deciding whether a rank is stable.
4. **All three service singletons ignored an explicitly passed policy.** `get_rules_engine(p)`, `get_baseline_service(p)` and `get_scoring_service(p)` returned the first-loaded instance no matter what the caller asked for, so a scoring pass could record one policy hash and compute under another. `content_hash` cannot detect this — it identifies the file a profile was loaded from, not the dict in hand. Now: no argument → shared instance; explicit policy → always a fresh service bound to exactly that policy.

Also: the ledger now has a distinct `scoring_completed` action rather than sharing `run_completed` with the ingestion jobs, so "when was this entity last scored" is answerable without also matching every load. And a scoring run that falls below the share floor now says so in `caveats` rather than leaving a bare null tier for the API to render.

### The sovereignty check polices itself

`backend/scripts/sovereignty_check.py` runs startup → ingest → indicators → scoring → export with `socket.connect`, `connect_ex`, `sendto`, `create_connection`, `getaddrinfo`, `gethostbyname` and `urllib.request.urlopen` all replaced by a recorder that **also raises**. An attempt that was merely logged could still have succeeded, so the guard refuses rather than observes. DNS is included because a resolver lookup is already the first half of every exfiltration path, and `sendto` because UDP has no handshake to notice afterwards.

Two things it does that a naive version would not, both because a check that cannot fail is not evidence:

- **It proves the guard denies.** Sections 5 and 1b make real `create_connection` / `getaddrinfo` / `urlopen` calls and planted import/attribute sites and require them to be caught. A zero only means something next to a demonstrated non-zero.
- **Its own scanner had a hole, and the self-test found it.** The static scan originally flagged `.connect` only when the *variable name* looked socket-y, which produced 42 false positives on this codebase's own `get_connection` and would have missed `s.connect(('10.0.0.1', 443))` entirely. Now every `.connect`/`.sendto` is collected and only demonstrably local bases (duckdb, sqlite) are classified out — the four real local sites are printed with file and line so the classification is reviewable. Recall stays at 100% and the judgement stays visible.

Scope is stated in the output rather than glossed: this covers the exercised paths plus an AST scan of `app/`. It is evidence for the air-gap design, not a substitute for network-segmentation policy at the deployment site. Current result: 15 pipeline stages, **zero outbound attempts**, none of 34 known egress modules imported.

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
| Data in (ingestion, DQ, assessability) | hour 8.5 | ✅ |
| Signal out (indicators, scoring) | hour 14 | ⬜ |
| **CUT LINE — demo-ready** | **hour 14** | ⬜ |
| OpenAPI frozen → Dev 1 data unblocked | hour 18 | ⬜ |
| Pack lifecycle, trends, P1, offline bundle | hour 26 | ⬜ |
| E2E demo | hour 30 | ⬜ |

> Above the cut line, scope is cut in this order: pack rollback → change-point detection → extra export formats → ECOD.
> **Never cut:** finding cards with evidence, negative-space indicators, sovereignty check, validation numbers.