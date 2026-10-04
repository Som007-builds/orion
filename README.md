<div align="center">

# 🛡️ Orion

### Supervisory Analytics Tool for SOC Assessment

**Turning SOC operational evidence into explainable supervisory hypotheses.**

**Smart India Hackathon 2026 · Problem Statement 26157 · NCIIPC**

<br>

Orion is an **air-gapped supervisory analytics platform** designed to help cyber supervisors and audit examiners analyze periodic batches of SOC operational data across Critical Sector Entities (CSEs).

It analyzes **alerts, cases, investigations, escalations, assets, telemetry, and investigation notes** to surface operational patterns that conventional dashboards and compliance reports can miss.

**Orion does not replace the examiner. It prioritizes where the examiner should look.**

<br>

🔒 **Air-Gapped** · 🔎 **Evidence-Driven** · 🧠 **Explainable Analytics** · 📊 **Supervisory Assessment** · 🧪 **Reproducible Validation**

<br>

[🤔 Why Orion](#why-orion) ·
[✨ What It Does](#what-it-does) ·
[🏗️ Architecture](#architecture) ·
[🧠 Analytics](#analytics) ·
[🧪 Validation](#validation) ·
[🚀 Quick Start](#quick-start) ·
[📚 Documentation](#documentation) ·
[🗺️ Roadmap](#roadmap)

</div>

---

<a id="why-orion"></a>

## 🤔 Why Orion

SOC environments generate enormous amounts of operational evidence.

The problem is that **important supervisory signals are often invisible in the reports built from that evidence**.

A SOC may report excellent response times, high closure rates, complete dashboards, and apparently healthy KPIs while the underlying operational data tells a different story.

For example:

- 🚨 Critical alerts may be closed unusually quickly.
- 🔎 Alerts may be acknowledged without meaningful investigation.
- 🔄 The same asset may repeatedly trigger alerts without evidence of root-cause remediation.
- 📉 Critical systems may generate unexpectedly little security telemetry.
- 🕒 Investigation activity may cluster suspiciously around reporting boundaries.
- 🧩 Records may contain gaps, inconsistencies, or patterns that ordinary KPI reporting does not expose.
- 📝 Investigation notes may exhibit suspicious repetition or template-driven behavior.
- 🌐 Multiple entities may respond similarly to a common operational event, creating signals that are difficult to spot manually.

These patterns do not automatically prove misconduct, non-compliance, or poor security.

They are **signals worth examining**.

That distinction is fundamental to Orion.

---

<a id="what-it-does"></a>

## ✨ What It Does

Orion ingests periodic SOC evidence and produces **prioritized analytical hypotheses backed by evidence**.

### Evidence sources

Orion can reason across multiple operational datasets:

| Evidence | Examples |
|---|---|
| **Alerts** | severity, timestamps, lifecycle, disposition |
| **Cases** | investigation state, closure, ownership |
| **Investigations** | analyst activity, notes, workflow evidence |
| **Escalations** | escalation decisions and timing |
| **Assets** | asset inventory and criticality |
| **Telemetry** | activity and coverage patterns |
| **Notes** | investigation narratives and repeated patterns |
| **Historical records** | trends, baselines, and change points |

### Analytical pillars

**Rules-based supervisory indicators**

Deterministic indicators identify measurable operational patterns such as unusual closure behavior, missing activity, integrity gaps, and temporal anomalies.

**ML-assisted analytics**

Machine-learning components provide additional signals such as:

- peer-relative anomaly detection
- deterministic feature attribution
- investigation-note similarity
- negative-space analysis
- anomaly scoring
- candidate clustering

ML output is treated as **evidence for investigation, not an automated decision**.

### Findings are hypotheses

A finding is intended to answer:

> **"What deserves the examiner's attention, and why?"**

Every finding is expected to retain its relationship to the underlying evidence.

Orion does **not** turn missing evidence into a zero-risk conclusion.

`NOT_ASSESSABLE` is a legitimate analytical outcome.

---

<a id="architecture"></a>

## 🏗️ Architecture

Orion is structured as an air-gapped backend and web interface with analytical, persistence, evaluation, and evidence layers.

```text
                         ┌──────────────────────────┐
                         │        SOC Records       │
                         │ alerts · cases · assets  │
                         │ telemetry · notes · etc. │
                         └────────────┬─────────────┘
                                      │
                                      ▼
                         ┌──────────────────────────┐
                         │       Ingestion          │
                         │ validation · quarantine  │
                         └────────────┬─────────────┘
                                      │
                         ┌────────────▼─────────────┐
                         │      Evidence Store      │
                         │ SQLite + DuckDB           │
                         └────────────┬─────────────┘
                                      │
                     ┌────────────────┴────────────────┐
                     │                                 │
                     ▼                                 ▼
          ┌─────────────────────┐          ┌─────────────────────┐
          │ RulesEngine         │          │ ML / Analytics      │
          │ deterministic       │          │ anomaly detection   │
          │ indicators          │          │ attribution         │
          │ evidence checks     │          │ NLP auditing        │
          └──────────┬──────────┘          └──────────┬──────────┘
                     │                                 │
                     └────────────────┬────────────────┘
                                      ▼
                         ┌──────────────────────────┐
                         │     Finding / Evidence   │
                         │          Cards           │
                         └────────────┬─────────────┘
                                      │
                                      ▼
                         ┌──────────────────────────┐
                         │    Supervisory Review    │
                         │ packs · trends · exports │
                         └──────────────────────────┘
```

### Design principles

**Air-gapped by design**

No external APIs, CDNs, web fonts, or online inference dependencies are required.

**Evidence-first**

Analytical output remains connected to the evidence that produced it.

**Deterministic where possible**

Seeds, aggregation, evaluation, and reproducibility paths are controlled explicitly.

**Human-in-the-loop**

Orion surfaces hypotheses. The examiner remains responsible for interpretation and action.

**No silent uncertainty**

Insufficient evidence produces `NOT_ASSESSABLE`, rather than silently becoming a favorable score.

**Reproducible findings**

Findings are designed to be independently reproduced from their underlying evidence.

---

<a id="analytics"></a>

## 🧠 Analytics

Orion's analytical layer combines deterministic supervisory rules with ML-assisted analysis.

### Negative-space detection

Negative-space indicators look for meaningful absences rather than only explicit events.

Current Dev 3 coverage includes:

- NS-01
- NS-02
- NS-03
- NS-04
- NS-05
- NS-06

The detectors explicitly handle insufficient evidence and return `NOT_ASSESSABLE` where the required evidence cannot support a conclusion.

### Isolation Forest anomaly analysis

The anomaly engine currently implements **exact leave-one-out peer-relative Isolation Forest evaluation**.

For a target entity:

1. The target's observations are excluded from its peer training set.
2. A deterministic Isolation Forest is fitted to the remaining peer data.
3. The target is evaluated against that peer model.
4. Feature-level attribution is generated.
5. The result is returned through the frozen `ModelOutput` contract.

The current implementation intentionally preserves exact LOO semantics.

The trade-off is computational cost: the current contract requires per-target model fitting, so very large detector workloads remain a known scalability limitation.

No semantics-changing optimization is silently introduced merely to manufacture a benchmark number. Humanity has enough benchmark fiction already.

### Explainable attributions

ML-derived findings expose deterministic feature attributions rather than returning an unexplained anomaly score.

The attribution layer preserves:

- feature identity
- direction
- contribution
- deterministic seed/context
- evidence relationships

### Investigation-note auditing

The NLP auditor uses deterministic candidate bucketing followed by text similarity analysis.

The implementation supports:

- deterministic candidate generation
- MinHash-style bucketing
- TF-IDF similarity
- legitimate-template suppression
- concentration controls
- evidence-linked outputs

A measured **100,000-note benchmark** currently executes at approximately:

**18,464 notes/sec**

on the development environment.

Larger NLP workloads remain limited by candidate-group size and similarity computation cost.

---

<a id="validation"></a>

## 🧪 Validation

Validation is treated as a first-class part of Orion rather than a decorative folder containing a benchmark nobody ran.

### Current verified state

| Validation | Result |
|---|---:|
| Full backend suite | **118 / 118 passing** |
| Dev 3 focused tests | **12 / 12 passing** |
| `git diff --check` | **Passing** |
| Streaming aggregation regression | **Passing** |
| Deterministic aggregation checks | **Passing** |
| Hard-negative FPR | **0 / 6** |
| Evidence traceability | **100 / 100** |
| NLP benchmark | **100,000 notes executed** |
| SOCSim generation benchmark | **Measured through ~50M rows** |

### SOCSim scale validation

The synthetic evaluation environment supports deterministic generation and controlled weakness injection.

Measured generation workloads include:

| Target | Rows | Throughput |
|---:|---:|---:|
| 1M | 993,964 | 568,395 rows/s |
| 10M | 9,942,462 | 558,651 rows/s |
| 50M | 49,735,130 | 549,484 rows/s |

These figures describe **SOCSim generation**, not detector throughput.

### Persisted RulesEngine validation

The evaluation pipeline now executes the real persisted path:

```text
SOCSim
   ↓
Injection
   ↓
Canonical database records
   ↓
ScoringService.score_period(..., persist=True)
   ↓
RulesEngine
   ↓
Persisted findings
```

The persisted validation path has confirmed real `EG-01` rapid-closure findings.

Current persisted dose-response coverage contains:

- **45 rows**
- **9 RulesEngine-owned families**
- **5 dose levels**

Adaptive validation currently contains **4 persisted scenarios**.

Families that lack the required canonical evidence structures remain explicitly `NOT_ASSESSABLE`.

### What is not claimed

The following are deliberately not presented as completed benchmarks:

- full 1M / 10M / 50M Isolation Forest detector throughput
- full 1M / 10M / 50M NLP throughput
- unsupported persisted RulesEngine scenarios
- detector throughput inferred from SOCSim generation throughput

The project reports what was actually executed.

---

<a id="quick-start"></a>

## 🚀 Quick Start

### Backend

```bash
cd backend

python -m venv .venv
```

Activate the environment:

```bash
# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

Install the pinned dependencies:

```bash
pip install -r requirements.txt
```

Start the service:

```bash
python run.py
```

Or:

```bash
uvicorn app.main:app --reload --port 8000
```

The backend exposes:

```text
API root:    http://localhost:8000/api/v1
OpenAPI:     /openapi.json
Health:      /health
```

### Backend tests

```bash
python -m unittest discover tests
```

### Verification utilities

Verify the append-only ledger:

```bash
python scripts/ledger_verify.py
```

Reproduce a finding:

```bash
python scripts/reproduce_finding.py --finding <id>
```

Run the air-gap verification:

```bash
python scripts/sovereignty_check.py
```

### Frontend

```bash
cd frontend
pnpm install
pnpm dev
```

Then run:

```bash
pnpm typecheck
pnpm lint
```

The frontend runs at:

```text
http://localhost:3000
```

---

<a id="documentation"></a>

## 📚 Documentation

| Document | Purpose |
|---|---|
| [`docs/AGENTS.md`](docs/AGENTS.md) | Project context and agent instructions |
| [`docs/implementation_plan_v2.md`](docs/implementation_plan_v2.md) | Authoritative implementation plan and changelog |
| [`docs/build-responsibility.md`](docs/build-responsibility.md) | Ownership matrix and frozen interfaces |
| [`docs/dev2-info-handover.md`](docs/dev2-info-handover.md) | Dev 1 / Dev 3 API and service contracts |
| [`docs/backend-architecture.md`](docs/backend-architecture.md) | Backend architecture |
| [`docs/frontend-architecture.md`](docs/frontend-architecture.md) | Frontend architecture |
| [`docs/project-overview.md`](docs/project-overview.md) | Mission, scope, and analytical pillars |
| [`docs/openapi-v1.json`](docs/openapi-v1.json) | Frozen v1 API contract |
| [`backend/eval/DEV3_HANDOFF.md`](backend/eval/DEV3_HANDOFF.md) | Dev 3 ML and validation handoff |
| [`progress.md`](progress.md) | Shared project progress and implementation status |

The original problem statement is maintained as an immutable project reference.

---

<a id="roadmap"></a>

## 🗺️ Roadmap

### Completed foundation

- [x] Frozen v1 API
- [x] Evidence and finding cards
- [x] Review packs
- [x] Signed exports
- [x] Signed pack lifecycle
- [x] Trends and change points
- [x] Negative-space detector integration
- [x] ML attribution layer
- [x] Deterministic SOCSim
- [x] Validation and benchmark harness
- [x] Persisted RulesEngine validation path
- [x] Full backend test suite
- [x] Dev 3 focused validation suite

### Remaining engineering work

- [ ] Complete broader persisted RulesEngine scenario coverage where canonical source evidence exists
- [ ] Improve scalable anomaly execution while preserving the exact LOO contract or explicitly separating any semantically different scalable mode
- [ ] Expand large-scale NLP benchmarking where computationally practical
- [ ] Offline bundle / SBOM / restore workflow
- [ ] Final integration and end-to-end demonstration
- [ ] Final evaluation and evidence package

The roadmap intentionally distinguishes **implemented functionality** from **benchmarks and scenarios that remain computationally or evidentially constrained**.

---

## 🔒 Ground Rules

Orion follows several non-negotiable principles.

1. **Air-gapped.** No external APIs, CDNs, or web dependencies.
2. **Hypotheses, never verdicts.** Orion does not declare an entity compliant, non-compliant, guilty, or safe.
3. **`NOT_ASSESSABLE` is not low risk.** Missing evidence remains missing evidence.
4. **No silent drops.** Rejected records enter quarantine and remain visible.
5. **Append-only ledger.** Ledger integrity is enforced through SQLite mechanisms rather than convention.
6. **DuckDB is single-writer.** Ingestion and analytical runs are handled outside the request thread.
7. **Findings are reproducible.** A finding can be independently reproduced from its evidence.
8. **No invented measurements.** Performance claims are reported only when the corresponding workload was actually executed.

---

## 📁 Repository Layout

```text
orion/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── db/
│   │   ├── schemas/
│   │   ├── services/
│   │   └── ml/
│   ├── eval/
│   │   ├── socsim.py
│   │   ├── injection.py
│   │   ├── runner.py
│   │   ├── metrics.py
│   │   ├── streaming_features.py
│   │   └── persisted_validation.py
│   ├── scripts/
│   ├── tests/
│   ├── data/
│   └── deploy/
│
├── frontend/
│   └── ...
│
├── docs/
│   ├── AGENTS.md
│   ├── implementation_plan_v2.md
│   ├── build-responsibility.md
│   ├── backend-architecture.md
│   ├── frontend-architecture.md
│   └── project-overview.md
│
└── progress.md
```

---

<div align="center">

### Orion

**Supervisory analytics for evidence that deserves a closer look.**

*The system surfaces the hypothesis. The examiner makes the judgment.*

</div>
