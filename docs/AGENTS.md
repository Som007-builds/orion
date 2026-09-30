# AGENTS.md — Orion Project Context & Agent Instructions

**Project**: Orion (Supervisory Analytics Tool for SOC Assessment — SAT-SA)
**Organization / Stakeholder**: National Critical Information Infrastructure Protection Centre (NCIIPC), India
**Problem Statement ID**: 26157 (Smart India Hackathon 2026)
**Plan of record**: `docs/implementation_plan_v2.md`

---

## 1. Project Overview & Core Mission

Orion is a **Supervisory Analytics Tool for SOC Assessment (SAT-SA)**. It is **NOT** a Security Operations Centre (SOC), SIEM, or real-time monitoring tool.

Orion is designed for **NCIIPC cyber supervisors and audit examiners** to periodically review **batches** of operational SOC alerts, case-management records, asset inventories, and investigation workflows across **Critical Sector Entities (CSEs)**.

### The Core Supervisory Duality

1. **Execution Gaps** — documented policies and metrics claim resilience, but operational evidence reveals weakness:
   - Critical/high alerts closed unusually quickly to satisfy SLA metrics.
   - Critical alerts closed without escalation, or with implausible dispositions.
   - Repetitive, template-driven (monoculture) investigation notes.
   - Backlog washing **and** shift-boundary bunching.
   - Recurrence without root-cause remediation.
   - Retroactive edits across submissions.
2. **Negative Space** — the suspicious *absence* of expected evidence:
   - Critical assets generating no telemetry ("silent since").
   - Missing expected alert categories (persistent, not one month).
   - Orphan records and implausibly low activity.
   - Coverage lapses against declared SOC hours.
   - Non-response to a common shock that peers detected.

### Output rule
Orion emits **prioritised hypotheses with evidence and confidence**, never verdicts or compliance grades. **`Not assessable` is a first-class outcome, distinct from "low risk".**

---

## 2. Air-Gapped & Offline Deployment Rules

> **CRITICAL RULE**: Orion must function in a **strictly air-gapped, offline environment** with **zero internet connectivity**.

- **No external AI APIs**: no OpenAI, Anthropic, Gemini, or any externally hosted model/API. No generative AI in the decision path.
- **Local ML only**: scikit-learn, NumPy, MinHash-LSH, DuckDB, Polars.
- **No remote CDNs**: the frontend bundles all fonts, styles and icons locally.
- **Provable**: `backend/scripts/sovereignty_check.py` must demonstrate no outbound network during startup, ingest, run and export.
- **Packaged**: offline bundle uses a **hashed wheelhouse** (`pip install --require-hashes`), a **CycloneDX SBOM**, and a detached signature.

---

## 3. Technology Stack & Monorepo Structure

```text
orion/
├── frontend/                        # Next.js 16, React 19, Shadcn UI, Tailwind CSS v4
├── backend/                         # Python 3.11+, FastAPI, SQLite (WAL), DuckDB, Polars, scikit-learn
│   ├── app/                         # api, db, schemas, services, ml
│   ├── eval/                        # SOCSim, injection, metrics, runner, adversarial
│   ├── data/                        # seed, sqlite, parquet, policies, mappings, packs, reference, keys
│   ├── scripts/                     # sovereignty_check, build_offline_bundle, ledger_verify, reproduce_finding, bench
│   └── deploy/
└── docs/
    ├── implementation_plan_v2.md         # AUTHORITATIVE build plan
    ├── implementation_plan_v2_CHANGELOG.md
    ├── backend-architecture.md           # architecture v2
    ├── frontend-architecture.md          # UI v2
    ├── build-responsibility.md           # team ownership v2
    ├── project-overview.md
    ├── problem-statement.md              # IMMUTABLE source requirement — do not edit
    └── AGENTS.md
```

---

## 4. Team Division & Code Boundaries

See `docs/build-responsibility.md` for the full ownership matrix and frozen interfaces.

* **Developer 1 (Frontend)**: owns `frontend/`. Executive view (EGI/NSI/DTS + 8-dim radar), Review-Pack Workbench, Finding Card & evidence, submissions/assessability, governance views, supervisory brief.
* **Developer 2 (Core Backend)**: owns `backend/app/api/`, `app/db/`, `app/services/`, `app/schemas/`. Schema, ingestion/mapping/privacy, assessability, policy + baselines, indicators, scoring, review packs, ledger, RBAC, API.
* **Developer 3 (ML & Validation)**: owns `backend/app/ml/` and `backend/eval/`. SOCSim + injection + truth CSVs, negative-space NB/Poisson models, pooled LOO Isolation Forest + ECOD, MinHash-LSH auditor, metric suite.

**Frozen interfaces** (change needs all three to agree): indicator result object, finding card, model output, review pack, ledger event, signed pack.

---

## 5. Development & Running Commands

### Frontend (`/frontend`)
- Package manager: `pnpm`
- Dev server: `pnpm dev` (http://localhost:3000)
- Type check: `pnpm typecheck`
- Lint: `pnpm lint`

### Backend (`/backend`)
- Python environment: Python 3.11+
- Install (offline): `pip install --require-hashes -r requirements.txt`
- Run dev server: `python run.py` or `uvicorn app.main:app --reload --port 8000`
- Tests: `python -m unittest discover tests`
- Validation: `python -m eval.runner --config eval/configs/*.yaml`
- Ledger verify: `python scripts/ledger_verify.py`
- Reproduce a finding: `python scripts/reproduce_finding.py --finding <id>`
- Sovereignty check: `python scripts/sovereignty_check.py`

---

## 6. Guidelines for AI Agents Working on Orion

1. **Follow the plan of record.** Check `docs/implementation_plan_v2.md` before adding endpoints, schemas, indicators, or services. Check `docs/backend-architecture.md` for layer boundaries and `docs/frontend-architecture.md` for UI.
2. **Preserve air-gapped compatibility.** Never inject calls to external endpoints (fonts, CDNs, webhooks, cloud telemetry).
3. **Hypotheses, not verdicts.** Never phrase output as a regulatory finding or compliance grade.
4. **Every finding is explainable.** A finding must have a card with baseline, effect size, confidence breakdown, evidence row IDs, a stored re-runnable query, and lineage. `reproduce_finding` must return identical evidence.
5. **No invented measurements.** Performance numbers are labelled **target (to be benchmarked)** unless produced by a script under `backend/scripts/bench/` with hardware stated.
6. **Do not edit `docs/problem-statement.md`.** It is the immutable requirement source.
7. **Keep schema, indicators, and API in sync.** Every indicator must have its required tables/fields and a minimum data tier; every endpoint must map to a service.
8. **DuckDB is single-writer.** Ingestion and runs execute as background jobs, never in the request thread.
9. **Ledger is append-only.** No service updates or deletes `ledger_entry`.
10. **Validation is independent.** Injection scenarios are written by someone who does not author the indicator logic; splits are **by entity, never by time**.
