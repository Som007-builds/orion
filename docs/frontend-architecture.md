# Orion (SAT-SA) — Frontend Architecture (v2)

**System**: Supervisory Analytics Tool for SOC Assessment (SAT-SA) for NCIIPC
**Application**: Orion Web Console
**Stack**: Next.js 16 (App Router) • React 19 • Tailwind CSS v4 • Shadcn UI • Hugeicons / Lucide React • Recharts
**Aligned with**: `docs/implementation_plan_v2.md` and `docs/backend-architecture.md`
**Visual language**: `frontend/DESIGN.md` (see §4 note on the design-system conflict)

---

## 1. Architectural Principles

1. **Air-gapped and offline.** Zero external CDN scripts, remote fonts, or internet APIs. Fonts and icons are bundled locally.
2. **Supervisory-first information density.** Built for NCIIPC supervisors and examiners: high contrast, scannable data grids, instant drill-down.
3. **Hypotheses, not verdicts.** The UI must never present a score as a compliance grade. `Not assessable` is shown as its own state, distinct from "low risk".
4. **Evidence first.** Every score, badge and warning links to finding cards, evidence row IDs and lineage.
5. **Honest uncertainty.** Rank intervals and confidence breakdowns are shown; point ranks are treated as provisional.
6. **Contract-driven.** Types are generated/maintained from the backend OpenAPI. The frontend never invents fields.

---

## 2. Directory Structure & App Router Hierarchy

```text
frontend/
├── app/
│   ├── layout.tsx                     # ThemeProvider, QueryClient, global shell
│   ├── page.tsx                       # Redirects to /dashboard
│   └── (dashboard)/
│       ├── layout.tsx                 # Top supervisory bar, air-gapped badge, entity switcher
│       ├── dashboard/page.tsx         # Executive: EGI/NSI/DTS + 8-dimension peer radar
│       ├── entities/
│       │   ├── page.tsx               # CSE directory (SAP tiers + rank intervals)
│       │   └── [entityId]/page.tsx    # Deep audit: dimensions, assessability, findings
│       ├── review-packs/
│       │   ├── page.tsx               # Pack list + builder
│       │   └── [packId]/page.tsx      # Pack items, inclusion probabilities, verdicts
│       ├── findings/[findingId]/page.tsx  # Finding card, evidence, counterfactual
│       ├── submissions/
│       │   ├── page.tsx               # Submission list + DQ scores
│       │   └── [submissionId]/page.tsx# DQ report, mapping review/approve, assessability
│       ├── trends/page.tsx            # Per-period dimension trends + change points
│       ├── governance/
│       │   ├── ledger/page.tsx        # Ledger viewer + verify
│       │   ├── packs/page.tsx         # Stage / shadow / promote / rollback
│       │   └── policy/page.tsx        # Policy profiles (read-only unless admin)
│       ├── ingestion/page.tsx         # Compatibility: upload + seed switcher
│       └── reports/page.tsx           # Supervisory brief generator & export
├── components/
│   ├── ui/                            # Shadcn primitives
│   ├── supervisory/
│   │   ├── executive/
│   │   │   ├── score-cards.tsx        # EGI / NSI / DTS (with intervals)
│   │   │   ├── peer-radar.tsx         # 8-dimension radar vs cohort
│   │   │   └── sap-badge.tsx          # T1-T4 + Not assessable
│   │   ├── review-pack/
│   │   │   ├── pack-builder.tsx       # PPS controls, strata, size
│   │   │   ├── pack-table.tsx         # Items, pi, "selected because", verdict actions
│   │   │   └── prevalence-panel.tsx   # Horvitz-Thompson estimate + CI
│   │   ├── finding/
│   │   │   ├── finding-card.tsx       # Summary, baseline, effect size, confidence
│   │   │   ├── why-not-flagged.tsx    # Indicators run, values, not-computable
│   │   │   ├── counterfactual.tsx     # What would clear/raise the finding
│   │   │   ├── evidence-table.tsx     # Evidence row IDs + stored query
│   │   │   ├── lineage-panel.tsx      # Submission hashes, pack, policy, code version
│   │   │   └── template-inspector.tsx # Monoculture cluster highlighting
│   │   ├── submissions/
│   │   │   ├── dq-report.tsx          # Data-quality score breakdown
│   │   │   ├── mapping-review.tsx     # Fuzzy suggestions + approve
│   │   │   └── assessability-matrix.tsx # Dimension x Assessable/Partial/Not assessable
│   │   ├── governance/
│   │   │   ├── trend-chart.tsx        # Per-period dimensions + change points
│   │   │   ├── ledger-table.tsx       # Hash chain + verify button
│   │   │   └── pack-admin.tsx         # Stage/shadow/promote/rollback + diff
│   │   └── layout/
│   │       ├── top-nav.tsx            # NCIIPC header, air-gapped badge, role indicator
│   │       └── entity-selector.tsx
├── hooks/                             # use-entities, use-findings, use-review-packs,
│                                      # use-submissions, use-trends, use-ledger
├── lib/
│   ├── api.ts                         # Type-safe client for /api/v1
│   ├── types.ts                       # Shared interfaces (generated from OpenAPI where possible)
│   └── utils.ts                       # Formatting, interval rendering, tier colors
└── styles/globals.css
```

---

## 3. Core Page Specifications

### 3.1 Executive Overview (`/dashboard`)
* **Purpose**: answer *"Which CSEs require supervisory attention, and how confident are we?"*
* **Components**: EGI / NSI / DTS cards with rank intervals; **8-dimension peer radar** (Threat Detection, Investigation, Escalation, Incident Response, Security Operations, Governance and Oversight, Operational Discipline, Cyber Resilience); SAP badge (T1–T4 or **Not assessable**); entity leaderboard with DTS shown so low-trust submissions are not over-ranked.

### 3.2 Review-Pack Workbench (`/review-packs`)
* Replaces the v1 plain ranked triage list.
* **Builder**: targeted PPS size, diversity caps per template cluster and analyst, control-slice size by severity stratum.
* **Table**: case risk score, **inclusion probability π**, stratum, **"selected because"** text and verification prompts.
* **Prevalence panel**: Horvitz-Thompson prevalence estimate with confidence interval.
* **Verdict capture**: confirmed / benign / insufficient information (stored for calibration).

### 3.3 Finding Card & Evidence (`/findings/[findingId]`)
* **Finding card**: summary, baseline, effect size, confidence breakdown, corroborating signals, benign explanations to check, suggested examiner actions.
* **"Why flagged"** and **"Why not flagged"** (which indicators ran, values, which were not computable and why).
* **Counterfactual**: what value change would clear or raise the finding.
* **Evidence table**: evidence row IDs + stored, re-runnable query.
* **Lineage**: submission manifest hashes, pack version, policy hash, code version.

### 3.4 Submissions & Assessability (`/submissions`)
* Submission list with DQ score; detail shows data-quality breakdown, **mapping review/approve** with fuzzy suggestions, and the **assessability matrix** (dimension × Assessable / Partial / Not assessable, with the missing fields that caused it).
* Quarantined rows are visible and never hidden.

### 3.5 Governance (`/trends`, `/governance/*`)
* **Trends**: per-period dimension scores with change-point markers.
* **Ledger**: paged hash chain with a **Verify** action showing the head hash.
* **Packs**: stage, shadow-run diff, promote, rollback (admin role).
* **Policy**: active policy profiles (read-only unless Administrator).

### 3.6 Ingestion Hub (`/ingestion`) and Supervisory Brief (`/reports`)
* **Ingestion**: batch upload (CSV/TSV/JSON/NDJSON/Parquet/SQL dump/XLSX) and SOCSim seed switcher; progress reflects the background job.
* **Brief**: the **supervisory brief** (renamed from "official dossier") with PDF/print export; the exported file is signed and carries the ledger head hash.

---

## 4. Design System & Theming Tokens

**Unchanged from the original specification.** The theme is not modified by the v2 plan.

* **Theme**: Deep Dark Mode (`#090d16` background, `#0f172a` card surfaces, `#1e293b` borders).
* **Accent Colors**:
  - **Critical / Execution Gap**: Amber/Rose (`#f43f5e` / `#fb7185`)
  - **Negative Space / Blindspot**: Cyan/Electric Blue (`#06b6d4` / `#38bdf8`)
  - **Resilient / Verified**: Emerald (`#10b981`)
  - **Supervisory Attention / NCIIPC Accent**: Violet/Indigo (`#6366f1`)
* **Typography**: Clean sans-serif (`Inter`) with monospace numerals (`Geist Mono`) for financial/timestamps/IP addresses to maintain scannability.
* **Data colours**: risk emphasis must pair colour with an icon and a text tag (never colour alone). Execution Gap and Negative Space are visually distinct.
* **Fonts/icons**: bundled locally; no remote CDN.
* **Note**: `frontend/DESIGN.md` separately specifies a light-first token set. That conflict is **not resolved here** and remains open (OQ-4); this document does not change either theme.

The only UI naming change in v2 is the export action: "Export Dossier" becomes **"Export Brief"** (supervisory brief). This is copy, not theme.

---

## 5. API Client & Frontend State Management

The client calls `/api/v1` using `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000/api/v1`) with a shared typed fetcher. RBAC is server-enforced; the UI hides actions the role cannot perform but never relies on hiding for security.

```typescript
// lib/types.ts (shape; keep in sync with backend OpenAPI)
export type Dimension = 'td' | 'inv' | 'esc' | 'ir' | 'so' | 'gov' | 'od' | 'cr';
export type Assessability = 'assessable' | 'partial' | 'not_assessable';
export type SapTier = 'T1' | 'T2' | 'T3' | 'T4' | 'NOT_ASSESSABLE';

export interface EntityResilience {
  id: string;
  name: string;
  sector: string;
  socModel: 'in-house' | 'MSSP' | 'hybrid';
  sizeTier: 'small' | 'medium' | 'large';
  egi: number;                       // Execution Gap Index 0..1
  nsi: number;                       // Negative Space Index 0..1
  dts: number;                       // Data Trust Score 0..1
  dimensions: Record<Dimension, number>;
  sapTier: SapTier;
  rankInterval: { low: number; high: number };
  assessability: Record<Dimension, Assessability>;
}

export interface Finding {
  id: string;
  indicatorId: string;               // e.g. 'EG-01', 'NS-01'
  entityId: string;
  period: { start: string; end: string };
  summary: string;
  value: unknown;
  peerBaseline: { median: number; mad: number; percentile: number; nPeers: number };
  selfBaseline?: { median: number; mad: number; periods: number };
  effectSize: number;
  confidence: number;
  confidenceBreakdown: { n: number; assessability: Assessability; dataTrust: number };
  benignExplanations: string[];
  evidenceRowIds: string[];
  evidenceQuery: string;
  lineage: { submissionHashes: string[]; packVersion: string; policyHash: string; codeVersion: string };
  notFlagged?: { indicatorId: string; reason: string }[];
}

export interface ReviewPackItem {
  caseId: string;
  entityId: string;
  riskScore: number;
  inclusionProbability: number;      // pi
  stratum: string;
  selectedBecause: string;
  verificationPrompts: string[];
}

export interface Submission {
  submissionId: string;
  entityId: string;
  periodStart: string;
  periodEnd: string;
  receivedTs: string;
  dqScore: number;
  version: number;
  assessability: Record<Dimension, Assessability>;
}
```

The v1 `supervisoryRiskScore: number` (0–100) field is **removed**; it is replaced by `egi`, `nsi`, `dts`, `dimensions` and `sapTier`. Hooks are named after the v2 resources (`use-findings`, `use-review-packs`, `use-submissions`, `use-trends`, `use-ledger`).

---

## 6. Accessibility & Guardrails

1. **High contrast** per `DESIGN.md`; risk is always colour + icon + text.
2. **Keyboard navigation** for tables, pack builder and drawer.
3. **Reduced motion** respected.
4. **No em-dashes/en-dashes** in product copy (per `DESIGN.md` pre-flight checklist).
5. **Offline build** fails if any external font/script/CDN reference is introduced.
