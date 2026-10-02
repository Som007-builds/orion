# Orion — Supervisory Analytics Tool for SOC Assessment (SAT-SA)

**Problem Statement ID:** 26157 (Smart India Hackathon 2026)
**Stakeholder:** National Critical Information Infrastructure Protection Centre (NCIIPC), Government of India
**Plan of record:** [`docs/implementation_plan_v2.md`](docs/implementation_plan_v2.md)

Orion is an **air-gapped** supervisory analytics tool that lets NCIIPC cyber supervisors and audit examiners periodically review **batches** of operational SOC records — alerts, case-management entries, asset inventories, investigation workflows — across Critical Sector Entities (CSEs).

It is **not** a SOC, SIEM, SOAR, or real-time monitor. It does not grade or sentence entities. **It emits prioritised hypotheses with evidence and confidence, never verdicts or compliance grades** — and **`Not assessable` is a first-class outcome, distinct from "low risk".**

---

## Repo layout

| Path | What lives here |
|---|---|
| `backend/` | Python 3.11+ FastAPI service: `app/` (api, db, schemas, services, ml), `eval/` (SOCSim + validation), `data/`, `scripts/`, `deploy/` |
| `frontend/` | Next.js 16 / React 19 / Shadcn UI / Tailwind v4 web client |
| `docs/` | Architecture, ownership, handover, plan + changelog; **`problem-statement.md` is immutable** |
| `progress.md` | The shared team whiteboard — every dev updates their own rows and commits |

## Key documents

| Doc | What it is |
|---|---|
| [`docs/AGENTS.md`](docs/AGENTS.md) | Project context + agent instructions — read this first |
| [`docs/implementation_plan_v2.md`](docs/implementation_plan_v2.md) | Authoritative build plan (+ `_CHANGELOG`) |
| [`docs/build-responsibility.md`](docs/build-responsibility.md) | Ownership matrix and the six frozen interfaces |
| [`docs/dev2-info-handover.md`](docs/dev2-info-handover.md) | Exact API/service contracts for Dev 1 & Dev 3 |
| [`docs/backend-architecture.md`](docs/backend-architecture.md) · [`docs/frontend-architecture.md`](docs/frontend-architecture.md) | Layer/layout specifications v2 |
| [`docs/project-overview.md`](docs/project-overview.md) | Mission, scope, dual analytical pillars |

---

## Quick start

### Backend (`backend/`)

```bash
cd backend
python -m venv .venv                 # Python 3.11+
.venv\Scripts\activate               # (Windows)  ·  source .venv/bin/activate (Unix)
pip install -r requirements.txt      # pinned exactly; offline deploys use the hashed wheelhouse
python run.py                        # or: uvicorn app.main:app --reload --port 8000
```

- API root: `http://localhost:8000/api/v1` · OpenAPI: `/openapi.json` · health: `/health`
- The published v1 contract is frozen in `docs/openapi-v1.json`; a no-diff test guards it, so regenerating must reproduce the committed document.
- Tests: `python -m unittest discover tests`
- Ledger verify: `python scripts/ledger_verify.py`
- Reproduce a finding: `python scripts/reproduce_finding.py --finding <id>`
- Air-gap proof: `python scripts/sovereignty_check.py`

### Frontend (`frontend/`)

```bash
cd frontend
pnpm install
pnpm dev            # http://localhost:3000
pnpm typecheck
pnpm lint
```

---

## Ground rules (abridged — full list in `progress.md`)

1. **Air-gapped.** No external APIs, no CDNs, no web fonts. `sovereignty_check.py` must prove it.
2. **Hypotheses, never verdicts.** No "non-compliant", "violation", or grade.
3. **`Not assessable` ≠ low risk.** A distinct state, never coerced to `0`.
4. **No silent drops.** Rejected rows go to `quarantine` and stay visible.
5. **Ledger is append-only** — enforced by SQLite triggers, not convention.
6. **DuckDB is single-writer.** Ingestion and runs are background jobs, never in the request thread.
7. **Every finding is reproducible** — `reproduce_finding` returns identical row IDs.
8. **No invented measurements.** Perf figures are "target (to be benchmarked)" unless produced by a bench script with hardware stated.

Everything below the cut line of the build plan is cut *in order*; the never-cut list is finding cards with evidence, negative-space indicators, the sovereignty check, and validation numbers.

## Where the build stands

Tracked live on the [`progress.md`](progress.md) whiteboard. As of this writing the backend has shipped through **Phase 2.15** (frozen v1 API, evidence/finding cards, review packs, signed exports, signed pack lifecycle, trends + change points). Remaining phases: **2.16** (P1 indicators — partially awaiting Dev 3's negative-space detectors), **2.17b** (offline bundle/SBOM/restore), **2.18** (full evaluation suite — gated on Dev 3 validation data), **2.19** (integration + E2E demo). Dev 3 (ML & validation) has been unblocked since Phase 2.

---

*Orion is a supervised analytics capability, not an automated adjudicator — the examiner stays in the loop.*