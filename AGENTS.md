# AGENTS.md — Orion Project Context & Agent Instructions

**Project**: Orion (Supervisory Analytics Tool for SOC Assessment — SAT-SA)  
**Organization / Stakeholder**: National Critical Information Infrastructure Protection Centre (NCIIPC), India  
**Problem Statement ID**: 26157 (Smart India Hackathon 2026)

---

## 1. Project Overview & Core Mission

Orion is a **Supervisory Analytics Tool for SOC Assessment (SAT-SA)**. It is **NOT** a Security Operations Centre (SOC), SIEM, or real-time monitoring tool.

Instead, Orion is designed for **NCIIPC cyber supervisors and audit examiners** to periodically review batches of operational SOC alerts, case management records, asset inventories, and investigation workflows across **Critical Sector Entities (CSEs)** (e.g., Power Grids, Banks, Telecoms, Transport).

### The Core Supervisory Duality:
1. **Execution Gaps**: Documented policies and metrics claim high cyber resilience, but operational evidence reveals weakness:
   - Critical alerts closed in seconds (< 3 mins) to satisfy SLA metrics (*Metric Gaming*).
   - Critical/P1 alerts closed as "False Positive" with no tier-2/tier-3 escalation.
   - Repetitive, template-driven, copy-pasted investigation notes.
   - Bulk case closures during shift handovers.
2. **Negative Space**: The suspicious *absence* of expected operational evidence:
   - High-value "Crown Jewel" assets generating zero security alerts or telemetry (*Blind Spots*).
   - Missing expected MITRE ATT&CK categories (e.g. lots of phishing alerts, zero lateral movement or ransomware detection).
   - Major statistical divergence from peer entities in the same sector.

---

## 2. Air-Gapped & Offline Deployment Rules

> **CRITICAL RULE**: Orion must function in a **strictly air-gapped, offline environment** with **zero internet connectivity**.
- **No external AI APIs**: Do NOT use OpenAI, Anthropic, Gemini, or any externally hosted model APIs.
- **Local Machine Learning**: All ML/NLP algorithms must run locally via Python (`scikit-learn`, `TF-IDF`, `Isolation Forest`, `Polars`, `DuckDB`).
- **No Remote CDNs**: Frontend must bundle all fonts, styles, and icon libraries locally.

---

## 3. Technology Stack & Monorepo Structure

```text
orion/
├── frontend/                  # Next.js 16, React 19, Shadcn UI, Tailwind CSS v4, Lucide/Hugeicons
├── backend/                   # Python 3.11+, FastAPI, SQLite (WAL mode), DuckDB, Polars, Scikit-learn
├── problem-statement.md       # NCIIPC Problem Statement reference
├── build-responsibility.md    # 3-Developer task division (Frontend, Core Backend, ML Backend)
├── frontend-architecture.md   # Detailed UI component & view architecture
├── backend-architecture.md    # Detailed backend API, database, and analytics architecture
└── AGENTS.md                  # This file
```

---

## 4. Team Division & Code Boundaries

* **Developer 1 (Frontend)**:
  - Owns `/frontend/`.
  - Next.js 16 App Router, dark-theme supervisory console, Executive Peer Benchmark Radar, Triage Queue, Evidence Inspection Drawer, and Report Export.
* **Developer 2 (Core Backend)**:
  - Owns `backend/app/api/`, `backend/app/db/`, `backend/app/services/`.
  - SQLite metadata + DuckDB OLAP queries, CSV/JSON canonical ingestion normalizers, deterministic rule engine for Execution Gaps, and FastAPI endpoints.
* **Developer 3 (ML & Analytics Backend)**:
  - Owns `backend/app/ml/`.
  - Synthetic multi-entity dataset generator (`CSE-Alpha`, `CSE-Beta`, `CSE-Gamma`), Negative Space statistical detector, Isolation Forest outlier engine, and offline NLP template detector.

---

## 5. Development & Running Commands

### Frontend (`/frontend`)
- Package manager: `pnpm`
- Dev server: `pnpm dev` (Runs on `http://localhost:3000`)
- Type check: `pnpm typecheck`
- Lint: `pnpm lint`

### Backend (`/backend`)
- Python environment: Python 3.11+
- Virtual environment: `python3 -m venv .venv && source .venv/bin/activate`
- Install dependencies: `pip install -r requirements.txt`
- Run dev server: `python run.py` or `uvicorn app.main:app --reload --port 8000`
- Run ML test suite: `python -m unittest discover tests`

---

## 6. Guidelines for AI Agents Working on Orion

1. **Adhere to the Architecture Documents**:
   - Always check [frontend-architecture.md](/frontend-architecture.md) before creating new UI components.
   - Always check [backend-architecture.md](/backend-architecture.md) before writing new API endpoints or schemas.
   - Reference [build-responsibility.md](/build-responsibility.md) to keep code modular and aligned with developer ownership.
2. **Preserve Air-Gapped Compatibility**:
   - Never inject calls to external endpoints (e.g. `fonts.googleapis.com`, external webhooks, cloud telemetry).
3. **Data Integrity & Explainability**:
   - Every supervisory risk score (0–100) must be explainable through traceable evidence records (case ID, alert ID, rule tag, or anomaly metric).
