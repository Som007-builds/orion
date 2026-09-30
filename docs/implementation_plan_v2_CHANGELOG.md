# Orion (SAT-SA) — Implementation Plan v2 Change Log & Gap Analysis

**Companion to**: `docs/implementation_plan_v2.md`
**Original (untouched)**: `docs/backend-architecture.md`
**Date**: 2026-10-01
**Repo state when written**: `docs/` = 6 Markdown files; `backend/` empty; `frontend/` = stock Next.js template. (A follow-up alignment pass then updated the other docs to v2 — see §6.)

---

## 1. Gap Analysis (task item → v1 document)

Legend: **Covered** = v1 addresses it; **Partial** = v1 gestures at it; **Missing** = not in v1. "v1 document" means `docs/backend-architecture.md` (backend plan) with support from `docs/project-overview.md` and `docs/problem-statement.md`.

| Task item | v1 state | Evidence in v1 |
|---|---|---|
| A.1 New tables (asset, case_event, escalation, telemetry_daily, submission, record_version, alert_case, note_store) | **Missing** | v1 §3 has only Entity, AlertRecord, CaseRecord; `associated_alert_ids` is a list column |
| A.2 Entity SOC profile fields | **Missing** | v1 Entity has sector enum + critical_asset_count only |
| A.3 AlertRecord severity_raw/norm, rule_id, source_tool, event_ts, auto_closed, closed_by, case_id | **Missing** | v1 AlertRecord has single `severity` enum |
| A.4 Data tiers A/B/C + unlock table | **Missing** | Not present |
| A.5 Privacy / HMAC pseudonymisation / note encryption / gated views | **Missing** | Not present |
| B.1 Formats (CSV, TSV, JSON/NDJSON, Parquet, SQLite/SQL dump, XLSX) + vendor batch profiles | **Partial** | v1 mentions "Splunk, Elastic, ServiceNow, QRadar dumps" and CSV/JSON only |
| B.2 Declarative vendor YAML mapping + fuzzy assistant | **Missing** | Not present |
| B.3 Pipeline stages (receive→verify→detect→map→normalise→validate→quarantine→pseudonymise→load→ledger) | **Partial** | v1 describes "normalizes incoming CSV/JSON"; no quarantine/ledger |
| B.4 Data-quality score + assessability matrix | **Missing** | Not present |
| B.5 Upload security + background job / single-writer | **Partial** | v1 says "Background processing"; no limits/file-type/CSV-injection/bomb protection |
| C.1 Indicators as hypotheses + standard result object | **Missing** | v1 calls rules "unambiguous regulatory breaches" |
| C.2 Versioned signed policy profiles + peer baselines + EB shrinkage | **Missing** | v1 hardcodes 180 s, 20 words, 25 cases, 0.88, 5 repeats |
| C.3 EG-03 name/definition fix; EG-04 severity-history fix | **Missing** | v1 EG-03 mismatched; EG-04 had no history source |
| C.4 Families mapped to 8 dimensions + P0 set | **Partial** | v1 has 5 EG + 3 NS; no dimension mapping |
| C.5 P1 indicators | **Missing** | Not present |
| C.6 Negative-space statistical fixes | **Partial** | v1 uses sector medians / raw zero counts; no Poisson/NB, no persistence, no holidays |
| C.7 Automation vs human, MSSP cohorts | **Missing** | Not present |
| D.1 Isolation Forest pooled LOO + attributions + ECOD + language rule | **Partial** | v1 fits per-feature IF but not pooled LOO, no attributions, no ECOD |
| D.2 MinHash-LSH, monoculture index, cluster share | **Missing** | v1 uses O(n²) pairwise TF-IDF cosine |
| D.3 Model inventory + six problem-statement AI/ML items | **Missing** | Not present |
| D.4 Signed pack update mechanism | **Missing** | Not present |
| D.5 Deterministic explanation philosophy | **Partial** | v1 §1.4 mentions it but pairs "deterministic + probabilistic" loosely |
| E.1 Replace 0–100 with EGI/NSI/DTS/8 dims/SAP | **Missing** | v1 stores a single composite 0–100 |
| E.2 Signal mapping, FDR, family max, capped noisy-OR | **Missing** | Not present |
| E.3 Rank intervals + T1–T4 + Not assessable | **Missing** | v1 emits point rankings + A–F grades |
| E.4 LOO peer baselines, cohorts, min-peer, fallback model | **Partial** | v1 has Z-score/IQR vs sector median; no LOO, cohorts, or fallback |
| E.5 Trend storage + change points | **Partial** | v1 mentions "trend analysis"; no store/detector |
| F. Review-pack generator (PPS, controls, HT estimates, exports, verdicts) | **Partial** | v1 has a ranked list + "Smart Sampler"; no inclusion probabilities/controls/estimates |
| G.1 Finding card spec + why-not-flagged + counterfactual | **Partial** | v1 has a "Transparent Rationale Card"; no missing-indicator view |
| G.2 Hash-chained ledger + verify + reproduce + signed exports | **Missing** | v1 says "audit logs in SQLite" |
| G.3 RBAC + Argon2 + sessions | **Missing** | Not present |
| H. New endpoints + findings router + rename dossier | **Partial** | v1 §5 lists a `findings` router with no endpoints; calls output "official NCIIPC dossier" |
| I.1 SOCSim 20–30 entities, archetypes, patterns | **Partial** | v1 §2/§6 + overview has 3 entities (Alpha/Beta/Gamma) |
| I.2 Injection with dose + hard negatives + truth CSVs | **Missing** | Not present |
| I.3 Circular-validation avoidance, splits by entity | **Missing** | Not present |
| I.4 Metric suite | **Missing** | Not present |
| I.5 Real-data protocol | **Missing** | Not present |
| I.6 Benchmarks replace/relabel with hardware | **Partial** | v1 asserts `<80ms` / `<4s` with no script or hardware |
| J. Goodhart / gaming resistance | **Missing** | Not present |
| K. Offline bundle, SBOM, sovereignty check, hardware, backup | **Partial** | v1 says "install cleanly offline"; no wheelhouse hashes/SBOM/egress proof |
| L.1 Mermaid id collision | **Missing** | v1 uses `MLEngine` as both node and subgraph id |
| L.2 2-page architecture doc outline | **Missing** | Not present |
| L.3 Priority table + compression rules | **Missing** | Not present |

---

## 2. Change Log

Each entry: what changed and why. `[CHANGED]`/`[NEW]` match the markers in `implementation_plan_v2.md`.

### A — Schema (§3 of v2)

| # | Change | Why |
|---|---|---|
| A.1 | Added `asset`, `case_event`, `escalation`, `telemetry_daily`, `submission`, `record_version`, `alert_case`, `note_store` `[NEW]` | v1 could not represent worked examples without joinable state history, coverage heartbeats, submission lineage, or note provenance |
| A.2 | Extended `entity` with `soc_model`, `soc_hours`, `size_tier`, `tooling_profile`; sector via `sector_ref` lookup `[CHANGED]` | Needed for MSSP cohorts, declared-hours indicators (EG-03), and covariate peer models; fixed enum blocks new sectors |
| A.3 | Extended `alert_record` with `severity_raw`/`severity_norm`, `rule_id`, `source_tool`, `event_ts`, `auto_closed_flag`, `closed_by_pseudo`, `case_id` `[CHANGED]` | 4-level normalisation, automation separation, recurrence signatures, and direct case linkage |
| A.4 | Added Tier A/B/C definition + per-indicator unlock table `[NEW]` | Makes assessability explicit and tells CSEs what data raises coverage |
| A.5 | Added privacy section: HMAC pseudonyms, note redaction, encrypted originals, role-gated logged views `[NEW]` | Minimise sensitive data per problem statement; support audit without exposing PII |
| A.6 | Replaced `associated_alert_ids` list with `alert_case` bridge `[CHANGED]` | Lists cannot be joined or validated; a bridge is queryable and integrity-checkable |
| A.7 | Added state tables `run`, `finding`, `finding_evidence`, `dimension_score`, `review_pack`, `review_pack_item`, `verdict`, `ledger_entry`, `mapping_profile`, `policy_profile`, `pack`, `user`/`role_assignment`/`session` `[NEW]` | Required to persist the v2 outputs and every cross-reference the spec promises |
| A.8 | Declared source of truth: Parquet/DuckDB = evidence, SQLite = state `[CHANGED]` | Removes ambiguity in v1's "dual-tier" description |

### B — Ingestion (`services/ingestion_service.py`)

| # | Change | Why |
|---|---|---|
| B.1 | Formats expanded to CSV, TSV, JSON/NDJSON, Parquet, SQLite/SQL dump, XLSX; batch-export profiles for Splunk, Elastic, QRadar, Sentinel, TheHive, ServiceNow, Wazuh `[CHANGED]` | Real CSE exports are heterogeneous; no live pulls preserves the batch-only constraint |
| B.2 | Declarative YAML vendor mapping profiles + fuzzy mapping assistant `[NEW]` | Column/severity/status/timezone/ID drift across tools; a human confirms every suggestion |
| B.3 | Explicit 10-stage pipeline with quarantine and ledger registration `[CHANGED]` | v1 normalised but could silently drop; quarantine keeps errors visible |
| B.4 | `dq_score` + per-dimension assessability matrix `[NEW]` | Must separate "Not assessable" from "low risk" |
| B.5 | Upload security: size limits, file-type checks, CSV-injection sanitising, path-traversal and decompression-bomb protection, resource limits; ingestion as background job under DuckDB single-writer `[CHANGED]` | Hostile/broken submissions must not take down the service or the evidence lake |

### C — Rules & engines

| # | Change | Why |
|---|---|---|
| C.1 | Reframed rules as hypothesis indicators; standard result object `[CHANGED]` | Problem statement forbids verdicts; a uniform object makes every finding comparable and explainable |
| C.2 | Replaced hardcoded thresholds with versioned/signed policy profiles + peer baselines with EB shrinkage `[CHANGED]` | Entity-relative and cohort-relative decisions are defensible; hardcodes become documented defaults only |
| C.3 | Fixed EG-03 (name vs definition; now declared SOC hours/roster) and EG-04 (now requires `case_event` severity history) `[CHANGED]` | Removes the v1 mismatch and the unsupported inference |
| C.4 | Added indicator families mapped to all 8 capability dimensions + explicit P0 set `[NEW]` | Directly addresses the problem statement's 8 capabilities; shows coverage |
| C.5 | Added P1 indicators EG-12…EG-17 and NS-06 `[NEW]` | Differentiators the task requires |
| C.6 | NS-01 Poisson/NB zero-run + multiple-testing correction + criticality weight + "silent since"; NS-02 negative-binomial persistence across months; NS-03 declared hours + Indian holiday calendar `[CHANGED]` | v1's raw zero-count and sector-median comparisons would flag legitimate silence |
| C.7 | `actor_type` automation/human separation; "inferred" labelling; MSSP separate cohorts `[NEW]` | Prevent false positives on automation and on MSSP shared templates |

### D — ML & NLP

| # | Change | Why |
|---|---|---|
| D.1 | Isolation Forest on pooled LOO reference population, fixed seed, TreeSHAP/permutation attributions; ECOD option; ML restated as peer-deviation with evidence or shown as low-confidence lead `[CHANGED]` | Per-entity fitting leaks and over-flags; attributions are required for explanation |
| D.2 | Pairwise TF-IDF cosine replaced by MinHash-LSH on shingles blocked by entity and analyst; TF-IDF kept within clusters; monoculture index; High/Critical cluster share `[CHANGED]` | O(n²) does not scale; blocked LSH both scales and finds copy-paste within a writer's own corpus |
| D.3 | Added model inventory + the six problem-statement AI/ML items `[NEW]` | Deployment requirement lists exactly these six |
| D.4 | Signed pack lifecycle with shadow-run diff, promote, rollback `[NEW]` | Offline model/rule updates need verifiable provenance and reversibility |
| D.5 | Philosophy line fixed: deterministic, reproducible explanation even where detectors are statistical `[CHANGED]` | Removes the v1 ambiguity; makes `reproduce-finding` a hard requirement |

### E — Scoring

| # | Change | Why |
|---|---|---|
| E.1 | Replaced single 0–100 index with EGI, NSI, DTS, 8 dimension indicators, SAP `[CHANGED]` | A single number overstates precision and collapses unrelated capabilities |
| E.2 | Signal mapping: robust z after shrinkage → 0–1 ramp; confidence formula; BH FDR per entity per cycle; family max; capped noisy-OR `[CHANGED]` | Controls false discovery and prevents correlated indicators double-counting |
| E.3 | Rank intervals (weight perturbation + bootstrap) + T1–T4 + separate Not assessable `[CHANGED]` | Point ranks imply false precision; Not assessable is a real supervisory outcome |
| E.4 | LOO median/MAD cohorts by sector × size × SOC model × hours; minimum-peer rule; covariate-adjusted fallback `[CHANGED]` | Robust to outliers and small cohorts |
| E.5 | Persisted `dimension_score` per period + change-point detection + trends API `[NEW]` | Enables regime-shift visibility across cycles |

### F — Sampling & triage

| # | Change | Why |
|---|---|---|
| F.1 | Plain ranked list replaced by review-pack generator: PPS sampling with known π, diversity caps, stratified random control, Horvitz-Thompson estimates with CIs, "selected because" text, verified prompts, hash-lined PDF/XLSX/JSON exports, stored verdicts `[CHANGED]` | A ranked list cannot support defensible sampling inference or examiner calibration |

### G — Explainability & audit

| # | Change | Why |
|---|---|---|
| G.1 | Finding card spec incl. "why flagged", "why not flagged", counterfactual, lineage `[NEW]` | v1 had a rationale card but no missing-indicator or counterfactual view |
| G.2 | "Audit logs in SQLite" replaced by hash-chained append-only ledger + ledger verify + reproduce-finding + Ed25519-signed exports with head hash `[CHANGED]` | Tamper-evidence and reproducibility are explicit deliverables |
| G.3 | RBAC: Supervisor, Examiner, Auditor, Administrator, Data Custodian; Argon2; sessions; least privilege `[NEW]` | Required for handling sensitive submissions and audit separation of duties |

### H — API

| # | Change | Why |
|---|---|---|
| H.1 | Added submissions/DQ, runs, findings list/detail/evidence/counterfactual, review-packs, verdicts, trends, ledger/verify, packs stage/shadow/promote/rollback, policy-profiles, assessability endpoints `[NEW]` | Every v2 output needs a route; each maps to a service |
| H.2 | Specified real `findings` endpoints (v1 listed the router with none) `[CHANGED]` | v1 gap |
| H.3 | Renamed "official NCIIPC dossier" → "supervisory brief" `[CHANGED]` | Avoid overstating authority; it is a decision-support artefact, not a verdict |
| H.4 | Kept `/triage` and `/triage/{id}/evidence` as compatibility aliases `[CHANGED]` | Preserve frontend contracts while review packs become primary |

### I — Validation

| # | Change | Why |
|---|---|---|
| I.1 | `dataset_generator.py` → SOCSim: YAML-configured, 20–30 entities, 8 archetypes, realistic arrival patterns, lognormal service times, assets and telemetry `[CHANGED]` | 3 entities cannot exercise peer baselines, cohorts, or dose-response |
| I.2 | Injection with dose + hard negatives + `entity_truth.csv`/`case_truth.csv` `[NEW]` | Ground truth is needed to compute recall, FPR and dose-response |
| I.3 | Circular-validation avoidance; splits by entity (never time) `[NEW]` | Prevents the generator and detectors co-adapting; time splits leak |
| I.4 | Metric suite (entity/case/effort/power/safety/calibration/robustness/gaming/scale) `[NEW]` | Directly answers the validation requirement |
| I.5 | Real-data protocol with kappa/alpha ceiling and proposed acceptance criteria `[NEW]` | Human review is the ceiling; criteria must be labelled proposals |
| I.6 | Performance section replaced: all numbers targets unless a repo bench script produces them with hardware `[CHANGED]` | v1 asserted timings with no script or hardware, violating the no-invented-measurements rule |

### J — Goodhart / integrity

| # | Change | Why |
|---|---|---|
| J.1 | Added gaming-resistance section: triangulation, displacement detection, rotating subsets with fixed core, cross-submission hash diffs, timestamp forensics, robust stats, adversarial harness `[NEW]` | Indicators published in a supervisory tool become targets |

### K — Deployment & ops

| # | Change | Why |
|---|---|---|
| K.1 | Offline bundle with hashed wheelhouse (`--require-hashes`), CDN-free frontend build, CycloneDX SBOM, detached signature, install + self-test `[NEW]` | Air-gap deployment must be reproducible and verifiable |
| K.2 | `sovereignty_check.py` egress-deny verifier `[NEW]` | Proves the offline claim instead of asserting it |
| K.3 | Hardware profiles (pilot, production) labelled sizing targets `[NEW]` | Problem statement requires hardware requirements without invented precision |
| K.4 | Backup/restore notes + ledger verification after restore `[NEW]` | Preserves the audit chain across disaster recovery |

### L — Small fixes

| # | Change | Why |
|---|---|---|
| L.1 | Mermaid: node `MLMod` vs subgraph `MLEngine` `[CHANGED]` | v1 reused the id and would not render |
| L.2 | Added Appendix A: 2-page architecture doc outline; stated this is a backend spec `[NEW]` | Submission requires a separate 2-page architecture document |
| L.3 | Added P0/P1/P2 priority table and compression rules `[NEW]` | Scope in v2 far exceeds a short build window; the never-cut list protects the demo |

---

## 3. Consistency Pass

| Check | Method | Result |
|---|---|---|
| Every indicator has its tables/fields in §3 | Cross-read §4.5 against §3.3–3.14 for EG-01…EG-17, NS-01…NS-06 | Pass. Notable: EG-04/EG-11/EG-12/EG-13/EG-15/EG-17 require `case_event` (NEW) and EG-03/NS-03 require `entity.soc_hours` (CHANGED) — both present |
| Every indicator has a minimum tier | §3.15 unlock table vs §4.5 Min tier column | Pass (after correcting stray NS-07/NS-08 ids to the real NS-01…NS-06 set) |
| Every endpoint maps to a service | §5 table | Pass. Non-`[NEW]` legacy endpoints are mapped too |
| `findings` router has real endpoints | §5 | Pass |
| Every performance number is a target or benchmark-backed | §9.6, §11.5 | Pass. v1 `<80 ms`/`<4 s` relabelled targets; bench scripts named |
| "Not assessable" distinct from "low risk" | §4.4, §6.3 | Pass |
| Deterministic explanation over statistical detectors | §1.5, §8.1, §8.2 | Pass |
| Mermaid id collision fixed | §11.6 | Pass |
| 8 capability dimensions all reachable from indicators | §4.3 | Pass (TD, INV, ESC, IR, SO, GOV, OD, CR each appear) |
| Thresholds no longer hardcoded | §4.2 | Pass — defaults only, policy-configurable |

**Known deliberate omissions / bounds:** no source-code changes; the missing `Full Solution Plan.md` was not invented (OQ-1); the empty `backend/` was not guessed at (OQ-2).

---

## 4. Prioritised Build Checklist

Paths are relative to `backend/`.

### P0 — must ship

| # | Deliverable | Modules / paths |
|---|---|---|
| 1 | Schema + migrations | `app/db/models.py`, `app/db/sqlite.py`, `app/db/duckdb_client.py` |
| 2 | Pseudonymisation + note redaction | `app/services/pseudonymisation.py` |
| 3 | Ingestion pipeline + quarantine + DQ score | `app/services/ingestion_service.py`, `app/schemas/ingestion.py`, `app/schemas/submission.py` |
| 4 | Vendor mapping profiles + fuzzy assistant | `app/services/mapping_assistant.py`, `data/mappings/*.yaml` |
| 5 | Policy profile loader + defaults | `app/services/policy_profile.py`, `data/policies/*.yaml` |
| 6 | Peer baselines + EB shrinkage | `app/services/baseline_service.py` |
| 7 | P0 indicators | `app/services/rules_engine.py`, `app/ml/negative_space.py` |
| 8 | Assessability matrix | `app/services/assessability.py`, `app/schemas/assessability.py` |
| 9 | Scoring EGI/NSI/DTS/dims/SAP | `app/services/scoring_service.py` |
| 10 | Finding card + evidence + counterfactual | `app/services/evidence_service.py`, `app/api/v1/endpoints/findings.py` |
| 11 | Review-pack generator + verdicts | `app/services/review_pack.py`, `app/api/v1/endpoints/review_packs.py`, `app/api/v1/endpoints/verdicts.py` |
| 12 | Hash-chained ledger + verify | `app/services/ledger.py`, `scripts/ledger_verify.py`, `scripts/reproduce_finding.py` |
| 13 | RBAC | `app/services/auth.py`, `app/api/v1/deps.py` |
| 14 | SOCSim + injection + core metrics | `eval/socsim.py`, `eval/injection.py`, `eval/metrics.py`, `eval/archetypes.yaml` |
| 15 | Offline bundle + CDN-free frontend build | `scripts/build_offline_bundle.py`, `deploy/` |
| 16 | Sovereignty check | `scripts/sovereignty_check.py` |
| 17 | API wiring for all P0 routes | `app/api/v1/router.py`, `app/main.py` |

### P1 — differentiators

| # | Deliverable | Modules / paths |
|---|---|---|
| 1 | P1 indicators EG-12…EG-17, NS-03, NS-06 | `app/services/rules_engine.py`, `app/ml/negative_space.py` |
| 2 | ECOD + attributions | `app/ml/ecod.py`, `app/ml/attributions.py` |
| 3 | MinHash-LSH + monoculture index | `app/ml/nlp_auditor.py` |
| 4 | Signed packs stage/shadow/promote/rollback | `app/services/pack_manager.py`, `app/api/v1/endpoints/packs.py`, `data/keys/` |
| 5 | Trends + change points | `app/services/trend_service.py`, `app/api/v1/endpoints/trends.py` |
| 6 | RBAC role/session admin UI hooks | `app/services/auth.py`, `app/api/v1/endpoints/` |
| 7 | Adversarial harness | `eval/adversarial.py` |
| 8 | CycloneDX SBOM | `scripts/build_offline_bundle.py`, `deploy/` |
| 9 | Benchmark scripts | `scripts/bench/bench_duckdb.py`, `bench_pipeline.py`, `bench_ingest.py` |
| 10 | Real-data protocol | `eval/real_data_protocol.md` |
| 11 | Supervisory brief export (PDF/XLSX/JSON, signed) | `app/services/report_generator.py` |

### P2 — design-only

| # | Deliverable | Notes |
|---|---|---|
| 1 | Optional note embeddings | Off by default; no generative AI in the decision path |
| 2 | Covariate-adjusted peer model as primary (not just fallback) | Only if cohort sizes justify |
| 3 | Additional export formats / scheduling | Nice-to-have |

**Compression rules.** If time is short, demote P1 items in this order: pack rollback → change-point detection → extra export formats → ECOD. **Never cut:** finding cards with evidence, negative-space indicators, offline proof (sovereignty check), validation numbers.

---

## 5. Open Questions (duplicated from v2 §13)

| ID | Question |
|---|---|
| OQ-1 | **Resolved (accepted):** `Full Solution Plan.md` is absent and will not be added; the remaining docs are the reference. |
| OQ-2 | **Resolved:** `backend/` stays empty; backend is greenfield, so all `[NEW]` items are built from scratch. |
| OQ-3 | **Partially resolved:** team = 3 (1 frontend + 2 backend). Wall-clock duration still to confirm. |
| OQ-4 | **Resolved:** theme unchanged from the original spec; `frontend/DESIGN.md` untouched. |
| OQ-5 | Include note embeddings (P2)? Default off. |
| OQ-6 | Is there an existing offline-sovereignty/egress layer to reuse? |
| OQ-7 | All docs now adopt EGI/NSI/DTS/8 dims/SAP; confirm the frontend build follows (0–100 score of record removed from all docs). |
| OQ-8 | All docs now specify SOCSim 20–30 entities; confirm whether to retain Alpha/Beta/Gamma names as demo presets. |
| OQ-9 | Ed25519 private-key custody: which offline signer, and is a hardware token available? |
| OQ-10 | HMAC pseudonymisation key custody and rotation policy. |

---

## 6. Documentation Alignment Pass

After the plan v2 was written, every project doc was brought in line with it. Change summary:

| Doc | Change | Reason |
|---|---|---|
| `docs/backend-architecture.md` | Rewritten as architecture v2; v1 content replaced | Align module layout, schema, engines, scoring, API, audit and deployment with the plan |
| `docs/build-responsibility.md` | Recreated as v2 | Rebalance 3 workstreams around P0/P1/P2; freeze cross-module interfaces; add validation-independence rule |
| `docs/project-overview.md` | Updated to v2 | Remove single 0–100 index, 3-entity demo, O(n²) TF-IDF and v1 rule names; add data tiers, privacy, validation, Goodhart, offline deployment |
| `docs/frontend-architecture.md` | Updated to v2 | Replace `supervisoryRiskScore` with EGI/NSI/DTS/8 dims/SAP; add review packs, finding cards, submissions/assessability, governance views; rename dossier→brief; **original theme preserved unchanged** (OQ-4) |
| `docs/AGENTS.md` | Updated to v2 | New doc set/paths, offline bundle + sovereignty, hypothesis-not-verdict rule, evidence/explainability rule, single-writer and ledger rules |
| `frontend/DESIGN.md` | **Unchanged** | Theme is not to be changed; export naming change is documented in `frontend-architecture.md` instead |
| `docs/problem-statement.md` | **Unchanged** | Immutable requirement source; must not be edited |
| `docs/implementation_plan_v2.md` / `_CHANGELOG.md` | Plan v2 created; this pass records resolutions for OQ-1…OQ-4 | Reflects the confirmed decisions (Q1–Q4) |

**Post-alignment consistency checks**

| Check | Result |
|---|---|
| No doc still presents a 0–100 score of record | Pass (removed from AGENTS, overview, frontend; kept only as explanatory "v1 had X" notes) |
| All docs name EGI/NSI/DTS/8 dimensions/SAP | Pass |
| All docs use SOCSim (20–30 entities) | Pass |
| All docs use "supervisory brief" (not "official dossier") | Pass |
| All docs state evidence=Parquet/DuckDB, state=SQLite | Pass |
| Theme unchanged from the original specification | Pass (`frontend-architecture.md` §4 restores the original tokens; `frontend/DESIGN.md` untouched) |
| `problem-statement.md` untouched | Pass |
