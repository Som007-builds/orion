# Orion (SAT-SA) — Frontend Architecture Document

**System**: Supervisory Analytics Tool for SOC Assessment (SAT-SA) for NCIIPC  
**Application**: Orion Web Console  
**Tech Stack**: Next.js 16 (App Router) • React 19 • Tailwind CSS v4 • Shadcn UI • Hugeicons / Lucide React • Recharts

---

## 1. Architectural Principles

1. **Air-Gapped & Offline Independence**: Zero external CDN scripts, remote fonts, or internet APIs. All fonts (Inter / Geist Mono) and icon SVGs are bundled locally.
2. **Supervisory-First Information Density**: Built specifically for national cyber supervisors and audit examiners. High contrast, sleek dark cybersecurity palette, scannable data grids, and instant contextual drill-downs.
3. **Traceability & Evidence First**: Every score, badge, or warning card links directly to underlying empirical evidence (alert timestamp, analyst ID, disposition rationale, MITRE ATT&CK technique).
4. **Resilient Data Fetching & Optimistic UX**: SWR or TanStack Query caching for snappy navigation between CSE entity profiles, triage queues, and deep audit drawers.

---

## 2. Directory Structure & App Router Hierarchy

```text
frontend/
├── app/
│   ├── layout.tsx                     # Root layout: ThemeProvider, QueryClient, global navbar
│   ├── page.tsx                       # Redirects to /dashboard
│   ├── (dashboard)/
│   │   ├── layout.tsx                 # Dashboard Shell: Top supervisory bar, air-gapped status, entity quick-switcher
│   │   ├── dashboard/
│   │   │   └── page.tsx               # Executive View: Multi-Entity Resilience Matrix & Peer Benchmark
│   │   ├── entities/
│   │   │   ├── page.tsx               # All CSE Entities directory & comparative risk scores
│   │   │   └── [entityId]/
│   │   │       └── page.tsx           # Entity Deep-Audit: Execution Gaps, Negative Space & MITRE coverage
│   │   ├── triage/
│   │   │   └── page.tsx               # Supervisory Triage Queue: Smart sampling & prioritized manual review list
│   │   ├── ingestion/
│   │   │   └── page.tsx               # Batch Ingestion Hub: CSV/JSON upload, validation logs & seed switcher
│   │   └── reports/
│   │       └── page.tsx               # Supervisory Dossier Generator & PDF/Print Export
├── components/
│   ├── ui/                            # Shadcn UI primitives (Button, Card, Dialog, Drawer, Badge, Table, Tooltip)
│   ├── supervisory/
│   │   ├── executive/
│   │   │   ├── risk-matrix.tsx        # Scatter plot / quadrant chart of Execution Gap vs Negative Space
│   │   │   ├── peer-benchmark-radar.tsx # Multi-metric radar comparing CSE against peer sector average
│   │   │   └── kpi-stat-cards.tsx     # High-level resilience metrics & anomaly count
│   │   ├── triage/
│   │   │   ├── triage-table.tsx       # Paginated, sortable sample list with risk priority badges
│   │   │   ├── filter-toolbar.tsx     # Filter by entity, threat severity, gap type, anomaly confidence
│   │   │   └── smart-sampler-modal.tsx # Statistical sampling config (e.g. sample top 5% suspicious closures)
│   │   ├── evidence/
│   │   │   ├── evidence-drawer.tsx    # Slide-out sheet displaying complete alert/case audit trail
│   │   │   ├── timeline-view.tsx      # Chronological progression: Ingest -> Alert -> Investigation -> Closure
│   │   │   ├── explainability-card.tsx # Visual breakdown: "Why Orion flagged this alert"
│   │   │   └── template-inspector.tsx # NLP similarity highlighting canned/copy-pasted notes
│   │   ├── negative-space/
│   │   │   ├── mitre-coverage-matrix.tsx # ATT&CK heatmap showing expected vs observed telemetry
│   │   │   └── asset-blindspot-list.tsx  # Crown Jewel assets with zero logging activity
│   │   └── layout/
│   │       ├── top-nav.tsx            # NCIIPC header with air-gapped badge & live timestamp
│   │       └── entity-selector.tsx    # Dropdown to filter across CSE-Alpha, CSE-Beta, CSE-Gamma
├── hooks/
│   ├── use-entities.ts                # SWR/Query hooks for entity lists & metrics
│   ├── use-triage-samples.ts          # Hooks for prioritized review samples
│   ├── use-supervisory-stats.ts       # Aggregated stats & benchmark data
│   └── use-evidence-drawer.ts         # Global state for opening/closing the inspection drawer
├── lib/
│   ├── api.ts                         # Type-safe Fetch client pointing to FastAPI (/api/v1)
│   ├── types.ts                       # Shared TypeScript interfaces (Entity, AlertSample, Finding, Report)
│   └── utils.ts                       # Formatting helpers (duration, risk scoring colors, dates)
└── styles/
    └── globals.css                    # Tailwind CSS v4 design tokens and custom dark cyber themes
```

---

## 3. Core Page Specifications & UX Workflows

### 3.1 Executive Overview (`/dashboard`)
* **Purpose**: NCIIPC Leadership & Lead Examiner command center. Instant answer to: *"Which Critical Sector Entities require urgent supervisory intervention?"*
* **Key Components**:
  - **Supervisory Risk Quadrant**: Plots entities on a 2D matrix: X-axis (*Execution Gap Risk*), Y-axis (*Negative Space Risk*). Critical outliers immediately pop in the top-right quadrant.
  - **Entity Leaderboard**: Ranked table showing CSE Name, Sector (Banking, Energy, Telecom), Alert Volume, Investigation Velocity Index, Negative Space Score, and Overall Resilience Grade (A to F).
  - **Sector Peer Benchmark Radar**: Overlays the selected CSE's capabilities against the national sector median across 6 dimensions: Detection Diversity, Escalation Integrity, Investigation Rigor, Telemetry Coverage, Closure Authenticity, and Asset Visibility.

### 3.2 Supervisory Triage & Smart Sampling Queue (`/triage`)
* **Purpose**: Replaces arbitrary manual sampling with ML-assisted risk-prioritized sampling for audit examiners.
* **Key Components**:
  - **Priority Sample Table**: Ranked by `supervisory_priority_score` (0.00 – 1.00).
  - Columns:
    - *Flag / Tag*: `#RapidClosure`, `#UnescalatedCritical`, `#CannedNote`, `#OffHoursBulk`, `#CrownJewelBlindspot`.
    - *Entity & Asset*: CSE identifier and target host/service (marked with Crown Jewel badge if critical).
    - *Investigation Duration*: Highlighted in red if < 180 seconds for High/Critical alerts.
    - *Anomaly Confidence*: ML-derived score from Isolation Forest.
    - *Actions*: "Inspect Evidence" (triggers Evidence Drawer) and "Mark Verified / Add Examiner Note".
  - **Smart Sampler Controls**: Allows examiner to filter: *"Show me top 50 suspicious closures from CSE-Beta during Q3"*.

### 3.3 Evidence Drill-Down Drawer (Global Flyout)
* **Purpose**: Provides bulletproof explainability and auditability. Supervisors can verify exactly why an item was flagged without trusting a "black box".
* **Key Sections**:
  1. **Supervisory Explanation Banner**:
     > *"Flagged: Case closed in 47 seconds. Rule: Rapid Case Closure for Critical Asset. NLP Analysis: 96% text similarity to 312 other closed cases by Analyst ID #409."*
  2. **Audit Timeline**: Visual step-by-step timeline of raw events:
     - `14:02:10` Alert Triggered (Ransomware Artifact Detected on Core Payment DB)
     - `14:02:45` Case Created & Assigned to Analyst #409
     - `14:03:32` Status changed to 'Closed - False Positive' without Tier-2 escalation.
  3. **Investigation Note Text Diff / Highlighter**: Renders the investigation narrative with canned boilerplate phrases highlighted.
  4. **Examiner Action Bar**: One-click actions: "Escalate to CSE Inquiry", "Attach to Supervisory Dossier", "Mark False Flag".

### 3.4 Ingestion Hub & Seed Data Switcher (`/ingestion`)
* **Purpose**: Manage periodic offline batch uploads.
* **Key Features**:
  - Drag-and-drop zone for CSV/JSON files.
  - Schema mapping status & ingestion progress bar.
  - **SIH Demonstration One-Click Pre-loaders**: Instant buttons to reset/load:
    - `Load CSE-Alpha (Power Grid: High Negative Space Demo)`
    - `Load CSE-Beta (Bank: Metric Gaming & Execution Gaps Demo)`
    - `Load CSE-Gamma (Telecom: Resilient Baseline)`

### 3.5 Supervisory Dossier & Report Export (`/reports`)
* **Purpose**: Formats a formal supervisory report ready for printing or exporting as an NCIIPC assessment dossier.
* **Key Sections**:
  - Entity Profile, Executive Summary, Score Breakdown.
  - Documented vs Operational Reality Gap Analysis.
  - Top 10 Evidence Samples with Examiner Notes.
  - Regulatory Recommendations & Corrective Action Plan.

---

## 4. Design System & Theming Tokens

* **Theme**: Deep Dark Mode (`#090d16` background, `#0f172a` card surfaces, `#1e293b` borders).
* **Accent Colors**:
  - **Critical / Execution Gap**: Amber/Rose (`#f43f5e` / `#fb7185`)
  - **Negative Space / Blindspot**: Cyan/Electric Blue (`#06b6d4` / `#38bdf8`)
  - **Resilient / Verified**: Emerald (`#10b981`)
  - **Supervisory Attention / NCIIPC Accent**: Violet/Indigo (`#6366f1`)
* **Typography**: Clean sans-serif (`Inter`) with monospace numerals (`Geist Mono`) for financial/timestamps/IP addresses to maintain scannability.

---

## 5. API Client & Frontend State Management

```typescript
// lib/types.ts
export interface EntityResilience {
  id: string;
  name: string;
  sector: 'Banking' | 'Energy' | 'Telecom' | 'Defence';
  supervisoryRiskScore: number; // 0 - 100
  executionGapScore: number;     // 0 - 100
  negativeSpaceScore: number;    // 0 - 100
  totalAlertsReviewed: number;
  criticalCasesSampled: number;
  status: 'Critical Attention' | 'Elevated Concern' | 'Resilient';
}

export interface SupervisorySample {
  id: string;
  entityId: string;
  entityName: string;
  caseId: string;
  alertTitle: string;
  severity: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';
  assetName: string;
  isCrownJewel: boolean;
  closureDurationSec: number;
  analystId: string;
  flags: string[];
  anomalyScore: number;
  nlpSimilarityScore?: number;
  explainabilityRationale: string;
  timestamp: string;
}
```
All API interactions run against standard Next.js environment configuration `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000/api/v1`), using a shared `fetcher` with type-safe schemas.
