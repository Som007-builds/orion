# Developer 2 Handover — Contracts, Interfaces & Build State

**From:** Soham (Developer 2, Core Backend Lead)
**For:** Developer 3 (ML & Validation) and Developer 1 (Frontend)
**Authority:** `docs/implementation_plan_v2.md` is the plan of record. This document tells you what already exists, what you must code against, and what I still owe you.
**Last updated:** Phase 3 complete (foundation + frozen contracts)

> Read `docs/build-responsibility.md` first for the ownership matrix and the six frozen interfaces. This document is the *operational* companion: what is built, what the exact signatures are, and when each thing becomes available.

---

## 1. TL;DR — what you can do right now

| If you are | You can start **today** on | Blocked until |
|---|---|---|
| **Dev 3 (ML & Validation)** | `app/ml/negative_space.py`, `anomaly_engine.py`, `ecod.py`, `attributions.py`, `nlp_auditor.py`, and all of `backend/eval/**` | Nothing. The frozen contracts are written and tested. |
| **Dev 1 (Frontend)** | Shell, routing, design system, all component scaffolding, and every view typed against `app/schemas/**` | Live data at Phase 12 (~hour 18) |

**Dev 3 is fully unblocked. Dev 1 is unblocked for structure, not data.**

---

## 2. Build state as of now

### 2.1 Completed

| Phase | Deliverable | Verification |
|---|---|---|
| 0 | Repo skeleton, `requirements.txt` (pinned), `config.py`, `main.py`, `run.py` | App boots; `/health` returns `offline: true` |
| 1 | SQLite state schema — 25 tables | `journal_mode = wal` confirmed |
| 1 | DuckDB evidence schema — 6 tables | Created under single-writer lock |
| 2 | 8 Pydantic schema modules | All import; frozen invariants tested |
| 3 | Hash-chained append-only ledger | Chain verifies; tamper detected at `seq=1` |

### 2.2 Two tables I added beyond the §3.3 list

Both additive. Flagging so nobody duplicates the work:

| Table | Why it is not in the docs | Owner |
|---|---|---|
| `quarantine` (SQLite) | Rule 4 is "no silent drops", but §3.3 gives rejected rows nowhere to live. Without it, quarantine can only be a count. | **Me** |
| `entity_score` (SQLite) | `dimension_score` holds the 8 dimensions. EGI / NSI / DTS / SAP are entity-level and would pollute it. | **Me** |

**Dev 3:** do not create either table. Query `quarantine` freely — it is how you audit whether your indicators are seeing data you expect.

### 2.3 Where the stores live

```
backend/data/sqlite/orion.db          # state of record
backend/data/parquet/evidence.duckdb  # evidence of record
backend/.venv/                        # Python 3.11 environment, deps installed
```

Run from `backend/`. Activate with `.venv\Scripts\activate` (Windows) or `source .venv/bin/activate`.

---

## 3. THE CONTRACTS YOU CODE AGAINST

Everything in this section is in `backend/app/schemas/indicator.py` and is **frozen** (build-responsibility §2.1 and §2.3). Changing any field needs agreement from all three of us.

### 3.1 `IndicatorResult` — what every indicator returns

Rule or ML, P0 or P1, Core Backend or ML. Identical shape. There is no per-indicator variant.

```python
from app.schemas.indicator import (
    IndicatorResult, Period, Baseline, SelfBaseline,
    ConfidenceBreakdown, Assessability, Dimension, FindingSource,
)

period = Period(start=date(2026, 9, 1), end=date(2026, 9, 30))

return IndicatorResult(
    indicator_id="NS-01",
    entity_id="CSE-POWER-01",
    period=period,
    value=0.031,                      # probability of this zero-run occurring
    value_units="probability",

    peer_baseline=Baseline(
        median=0.42, mad=0.11, percentile=4.2, n_peers=11,
        method="loo_median_mad", cohort="Power|large|in-house|24x7",
    ),
    self_baseline=self_baseline,
    effect_size=-3.6,                 # robust z after EB shrinkage

    n=14,                             # assets observed, not a sample weight
    confidence=0.61,
    confidence_breakdown=ConfidenceBreakdown(
        n_term=1.0,                   # min(1, n / n_min)
        assessability_term=1.0,
        data_trust_term=0.61,         # DTS
    ),

    evidence_query="SELECT ...",      # stored, re-runnable
    evidence_row_ids=["ASSET-A1", "ASSET-A7"],

    benign_explanations=[
        "Asset may be in a scheduled maintenance window",
        "Maintenance-window silence is a hard negative, not a finding",
    ],
    required_fields=["telemetry_daily", "asset.criticality"],
    missing_fields=[],

    assessability=Assessability.ASSESSABLE,
    family="coverage",
    primary_dimension=Dimension.TD,
    secondary_dimensions=[Dimension.CR],

    source=FindingSource.NEGATIVE_SPACE,
    is_low_confidence_lead=False,
)
```

### 3.2 The invariants — these are enforced in code, not conventions

| Rule | Enforcement |
|---|---|
| `assessability == NOT_ASSESSABLE` ⟹ `value is None` | `model_post_init` raises `ValueError`. Tested. |
| Unknown fields rejected | `model_config = ConfigDict(extra="forbid")` on every model. Tested. |
| `missing_fields` ≠ `benign_explanations` | Convention, but load-bearing — see §3.3 |
| `n` is an observation count | Not a weight, not a sample size |
| `confidence` in `[0, 1]` | Field constraint |

### 3.3 The `missing_fields` vs `benign_explanations` distinction

This one gets confused, so stating it plainly:

- **`missing_fields`** — fields that were *absent from the submission*. "There is no `case_event` table, so I cannot check severity-change history." This drives assessability.
- **`benign_explanations`** — *candidate innocent reasons the observed value is fine*. "Maintenance window," "MSSP shared template," "legitimate auto-enrichment close."

Putting a missing field in `benign_explanations` tells the examiner "we know why this is OK", which is a completely different and unsupported claim. Do not do it.

### 3.4 Declining to compute — use the helper

Never fabricate a value to fill the field. `IndicatorResult.not_assessable()` builds the correct result:

```python
return IndicatorResult.not_assessable(
    indicator_id="NS-03",
    entity_id=entity_id,
    period=period,
    missing_fields=["entity.soc_hours", "telemetry_daily"],
    source=FindingSource.NEGATIVE_SPACE,
    primary_dimension=Dimension.SO,
    notes="Declared SOC hours absent; cannot assess coverage lapse.",
)
```

Result: `value=None`, `n=0`, `confidence=0.0`, `assessability=not_assessable`.

### 3.5 `ModelOutput` — frozen interface #3

You emit this. I persist it verbatim into `finding`.

```python
from app.schemas.indicator import ModelOutput, Attribution

return ModelOutput(
    model_id="isolation_forest_v1",
    target_type="case",              # "case" | "entity"
    target_id=case_id,

    features={"closure_sec": 42.0, "note_words": 6, "escalated": 0.0},
    score=0.71,
    attributions=[
        Attribution(
            feature="closure_sec", value=42.0,
            peer_median=1800.0, effect_size=-4.1,
            direction="increases_risk",
        ),
    ],
    direction="above_peer",          # "above_peer" | "below_peer" | "within_peer"
    seed=42,                         # your fixed seed
    confidence=0.44,
    evidence_row_ids=["CASE-8812"],
)
```

### 3.6 The language rule — this is a hard gate, not advice

`ModelOutput.explainable` returns `True` only when **both** attributions and evidence rows are present:

```python
mo.explainable   # bool attributions AND bool evidence_row_ids
```

- `explainable == True` → I surface it as a **finding**.
- `explainable == False` → forced to `is_low_confidence_lead=True`, shown as a **lead only**, never a finding.

Per plan §4.8.1, every ML flag must be restatable as *"these features deviate in this direction versus this peer group"* with evidence rows. No attribution, no finding. This is why `explainable` is a computed property rather than a field you can set optimistically.

---

## 4. Dev 3 — your scope

Ownership per build-responsibility §3. You own `backend/app/ml/**` and `backend/eval/**`.

### 4.1 Expected function signatures

I call these from `scoring_service` and the pipeline. Pure functions, typed in/out, **no SQLite writes** (merge rule 3).

```python
# app/ml/negative_space.py
def ns01_silent_critical_assets(evidence, policy, baseline) -> list[IndicatorResult]: ...
def ns02_absent_alert_categories(evidence, policy, baseline) -> list[IndicatorResult]: ...
def ns04_orphan_records(evidence, policy, baseline) -> list[IndicatorResult]: ...
def ns05_implausibly_low_activity(evidence, policy, baseline) -> list[IndicatorResult]: ...

# app/ml/anomaly_engine.py
def pooled_loo_isolation_forest(reference, target, seed: int) -> list[ModelOutput]: ...
def attributions(model, reference, target) -> list[Attribution]: ...

# app/ml/nlp_auditor.py
def monoculture_audit(notes, policy) -> list[IndicatorResult]: ...   # EG-10

# app/ml/pipeline.py  — orchestration; you own, I integrate
```

`evidence` and `baseline` are duck-typed read handles I pass in. I will confirm exact types before Phase 8 (hour 12).

### 4.2 Statistical methodology I am relying on

These are **not** optional — they are why v2 exists. Please implement as specified.

**NS-01 — silent critical assets** (plan §4.6.1)
- Model expected event rate by `asset_class` × `environment`
- Zero-run probability of length *L* under **Poisson or negative-binomial**
- Correct for multiple testing (**Benjamini–Hochberg**)
- Weight by `criticality`
- Report a **"silent since" date**, not a boolean
- **Skip** `decommissioned` assets and dates before `onboarded`

**NS-02 — absent alert categories** (plan §4.6.2)
- **Negative-binomial** expectation, overdispersion-aware. Not a sector median.
- Require **persistence across months** before elevating. One quiet month is not a finding.

**NS-03 / EG-03 — temporal** (plan §4.6.3)
- Use **declared SOC hours** and the bundled Indian holiday calendar (`data/reference/india_holidays.csv` — I will add it)
- Legitimate quiet periods must be **hard negatives, not findings**

**Isolation Forest** (plan §4.8.1)
- Fit on a **pooled reference population with leave-one-out** — never on the scored entity's own data
- Fixed seed, recorded in `run.seed` (default `42` from `config.Settings.seed`)

**MinHash-LSH** (plan §4.8.2)
- **LSH on word shingles**, blocked by entity **and** analyst
- TF-IDF cosine **only inside candidate clusters**, for scoring
- Report **monoculture index** = `1 − normalised entropy of cluster distribution`
- Report **cluster share among High/Critical closures**
- O(n²) pairwise cosine will not scale and is explicitly the v1 defect being fixed

**Cohorts (plan §4.7, §6.4)** — MSSP-run SOCs are a **separate cohort**. Shared templates across MSSP clients are normal; the same pattern in-house is a signal.

### 4.3 Automation vs human (plan §4.7)

`actor_type` separates `human` / `automation` / `unknown`.

1. If absent, **infer** it and set `actor_type_inferred=True` on the result.
2. Automation-generated template work is **not itself a weakness**. The question is whether a **human reviewed high-severity closures** (EG-01/EG-02/EG-06).
3. Never report an automation pattern as a finding without the human-review question attached.

### 4.4 Stubs I am shipping, and why — **replace these**

Stubs for NS-01/02/04/05 are now live in `rules_engine.py`, so the pipeline runs end to end without waiting on you. `rules_engine.ns_stub(indicator_id, entity_id, period_start, period_end)` returns:

```python
assessability = Assessability.NOT_ASSESSABLE
value          = None
missing_fields = []          # <- deliberately empty, see below
required_fields = ["..."]    # <- what your detector will need
notes          = "REFERENCE STUB: ..."   # greppable prefix
```

`RulesEngine.is_stub(result)` detects them via that marker, so a real detector declining for a real gap is never confused with "we never wrote this". Keep the marker until you have real code, then drop it.

**They never return a plausible placeholder number.** A stub returning a fake statistic is worse than an honest gap, because it flows into EGI/NSI and silently corrupts scoring. Swap the bodies, keep the signatures.

**Why `missing_fields` is empty and `required_fields` is not.** `missing_fields` means *we measured and this entity did not supply it*. A stub measures nothing, so it cannot claim anything is missing — as far as anyone knows, that entity submitted a perfect telemetry export and we simply never looked. Putting anything in `missing_fields` would read on the card as *this CSE sent us nothing*, which is an accusation we have no basis for. Populate `required_fields` with what your detector needs, and only populate `missing_fields` once you are actually probing presence.

**When you build real requirements, use `assessability.field_requirement(table, column, label=...)` — do not construct `FieldRequirement` directly.** `in_state_store` defaults to `False`, and getting it wrong is completely silent: the requirement is probed against DuckDB, `note_store` is never in `tables_present`, and your detector declines on evidence sitting in SQLite right in front of it. This bit three of my indicators at once before I found it.

**Use `absence_disqualifies` on `IndicatorSpec`** when a required field's absence must force `NOT_ASSESSABLE` rather than `PARTIAL`. `Partial` is only honest when the remaining evidence still supports an observation. NS-02 needs it: absent alert categories are exactly the claim, so without the missing categories being present-then-gone there is nothing to observe.

### 4.5 Your validation harness — independence requirement

`eval/injection.py` **must not** be written by the author of the indicator logic (plan §9.3, build-responsibility rule 3). Practically, for this project:

- **You author the injection scenarios.** I authored the P0 indicators, so I do not write or tune injection parameters.
- **I do not see dose values while tuning indicators.** If I do, the validation stops being independent and the recall numbers become self-fulfilling.
- **Splits are by entity, never by time.** A time split leaks, because SOCSim's archetypes carry temporal structure.

Deliverables: `entity_truth.csv`, `case_truth.csv`, plus hard negatives (legit fast auto-enrichment closes, legit MSSP templates, small genuinely quiet entities, maintenance-window silence).

### 4.6 Model inventory (plan §4.9)

Maintain this table for the supervisory brief. All classical/statistical — **no generative AI in the decision path**.

| Model | Architecture | Learned | Training data | Purpose |
|---|---|---|---|---|
| Isolation Forest | random-tree ensemble | yes | pooled LOO reference population | case/entity outlier lead |
| ECOD | empirical CDF | no | reference population | per-dimension entity outlier |
| MinHash-LSH | locality-sensitive hashing | no | notes corpus | template cluster discovery |
| TF-IDF + cosine | bag-of-words | no | notes within cluster | within-cluster score |
| NB / Poisson | generalised linear | estimated | entity + peer counts | expected-rate / zero-run |
| EB shrinkage | empirical Bayes | estimated | cohort statistics | baseline stabilisation |

Plus the six problem-statement items: architecture, hardware, offline training/inference, update mechanism, explainability controls, auditability controls.

---

## 5. Dev 1 — your scope

### 5.1 Start now

Shell, routing, design system, all components — per `frontend/DESIGN.md`. **Air-gap rule: local fonts and icons only, no CDN.**

You can also build every view against my typed response models, because the field names are now locked:

| Your view | Schema to type against |
|---|---|
| Executive (EGI/NSI/DTS + 8-dim radar) | `app/schemas/entity.py` → `EntityListItem`, `EntitySummaryOut`, `DimensionScoreOut` |
| Finding Card + evidence drawer | `app/schemas/finding.py` → `FindingCardOut`, `WhyFlagged`, `WhyNotFlagged`, `Counterfactual`, `EvidenceOut` |
| Review-Pack Workbench | `app/schemas/review_pack.py` → `ReviewPackOut`, `ReviewPackItemOut`, `HTEstimate`, `Verdict` |
| Submissions + DQ | `app/schemas/ingestion.py` → `SubmissionOut`, `DQReportOut`, `QuarantineSummaryOut`, `MappingOut` |
| Assessability matrix | `app/schemas/assessability.py` → `AssessabilityReport`, `DimensionAssessability` |
| Ledger + verify | `app/schemas/ledger.py` → `LedgerEntryOut`, `LedgerVerifyOut` |

### 5.2 Four rendering rules that will bite you

**1. `Not assessable` is a state, not a zero.**
`assessability: "not_assessable"` with `score: null`. It must have distinct visual treatment. A `Not assessable` dimension rendered as `0` tells the examiner "we checked and found nothing", which is a false claim. Plan §6.3 is explicit that a not-assessable dimension cannot raise an entity's tier.

**2. `is_low_confidence_lead: true` must look different.**
An ML lead without attributions is not a finding. Render it as a lead.

**3. Rank intervals, not point ranks.**
`sap_rank_interval` is `{rank, low, high, method}`. Render the range. A bare integer implies precision the method does not have.

**4. Language: hypotheses, not verdicts.**
No "non-compliant", "violation", "failed", "grade". Say "the evidence suggests…", "prioritised for review". We are decision support for human examiners, and the brief export is not an official NCIIPC document.

### 5.3 API surface — all of it (plan §5)

Every endpoint maps to exactly one service. OpenAPI freezes at Phase 12 (~hour 18).

```
GET  /entities                                GET  /review-packs
GET  /entities/{id}/summary                   POST /review-packs
GET  /entities/{id}/assessability             GET  /review-packs/{id}
POST /ingest/upload                           GET  /review-packs/{id}/export
POST /ingest/seed/{seed_id}                   POST /verdicts
GET  /submissions                             GET  /trends
GET  /submissions/{id}                        GET  /trends/change-points
GET  /submissions/{id}/mapping                GET  /ledger
POST /submissions/{id}/mapping/approve        GET  /ledger/verify
POST /runs                                    POST /packs/stage
GET  /runs            GET  /runs/{id}        POST /packs/{id}/shadow
GET  /findings                                 POST /packs/{id}/promote
GET  /findings/{id}                           POST /packs/{id}/rollback
GET  /findings/{id}/evidence                  GET  /policy-profiles
GET  /findings/{id}/counterfactual            POST /policy-profiles/{id}/activate
GET  /triage                    (compat)      GET  /benchmarks
GET  /triage/{id}/evidence      (compat)      POST /reports/{entity_id}/export
```

`/triage` and `/triage/{id}/evidence` are **compatibility aliases** for the v1 contract. New work goes on `/review-packs`.

Exports are **supervisory briefs**, not "official dossiers". The rename is deliberate.

### 5.4 RBAC — what each role sees

| Role | Rights |
|---|---|
| Supervisor | Full supervisory read, run approval, brief export |
| Examiner | Scoped read, verdict entry, review packs |
| Auditor | Read-only, **including ledger and verify** |
| Administrator | User/role/pack administration |
| Data Custodian | Submission intake, mapping approval, **no finding verdicts** |

Least privilege. An Examiner cannot read the ledger. A Data Custodian cannot record a verdict. Your nav should reflect the role, not just hide buttons.

---

## 6. Interfaces I own that you consume

### 6.1 Policy profiles (Phase 7)

Thresholds are **not hardcoded**. They live in `data/policies/*.yaml`, versioned, signed, and activated through a ledgered endpoint. Plan §4.2 lists the v1 hardcodes now demoted to *documented defaults*:

| v1 hardcode | v2 default | Primary v2 mechanism |
|---|---|---|
| 180 s fast closure | `fast_closure_seconds: 180` | robust z vs peer/self baseline |
| < 20 note words | `note_min_words: 20` | note substance + monoculture cluster share |
| > 25 cases / 30 min | `max_closures_30min: 25` | throughput vs `soc_hours` + human plausibility |
| 0.88 similarity | `cluster_similarity: 0.88` | LSH cluster membership, then TF-IDF score |
| > 5 repeats / 14 days | `repeat_count: 5`, `repeat_window_days: 14` | recurrence vs base rate + root-cause absence |

Your models must read thresholds from the policy object, not define their own.

### 6.2 Scoring pipeline (Phase 9)

Your indicator values feed step 1. I own the rest.

```
1  robust z          z = (x - median_peer) / (1.4826 * MAD_peer)
2  EB shrinkage      toward cohort mean, weighted by n
3  ramp to 0..1      configurable z0..z1
4  confidence        min(1, n/n_min) × assessability × DTS
5  BH FDR            per entity per cycle, at fdr_q
6  family max        aggregate indicator families by MAXIMUM
7  capped noisy-OR   1 - Π(1 - w_f × min(family_f, cap))
```

Step 7's cap is what stops correlated indicators double-counting. Your models should not pre-aggregate anything — give me raw observations and let the pipeline aggregate.

---

## 7. Constraints that will fail review

| Constraint | Consequence of breaking it |
|---|---|
| No generative AI in the decision path | Submission invalidated |
| Any external API / CDN / web font | Air-gap claim becomes false |
| `app/ml` writing SQLite directly | Merge rule 3 violation; corrupts single-writer invariant |
| `ledger_entry` UPDATE or DELETE | Physically rejected by SQLite triggers; chain breaks |
| Invented performance numbers | Violates "no invented measurements". Targets must say "target (to be benchmarked)" unless `scripts/bench/` produced it with hardware stated |
| Stubs returning fake values | Silently corrupts scoring; worse than an honest gap |
| `Not assessable` collapsed to 0 | Reports "no risk" where the truth is "unknown" |

---

## 8. Timeline

Roughly 2 days wall clock. Current position and what unblocks whom:

| Now | Dev 3 fully unblocked. Dev 1 unblocked for structure. |
|---|---|
| +~4 h | I add `data/reference/india_holidays.csv`, policy YAML, vendor mapping YAMLs |
| +~6 h | Ingestion pipeline live; you can run your models against loaded evidence |
| +~12 h | **Dev 3 integration point.** I call your NS-01/02/04/05 and IF. Stubs get swapped here. |
| +~14 h | **CUT LINE** — demo-ready: scores, findings, cards, packs, brief |
| +~18 h | **Dev 1 data unblock.** OpenAPI frozen. |
| +~26 h | Packs, trends, P1 indicators, offline bundle |

If a deliverable of mine slips, the cut line at +14 h is where I stop cutting scope and you will be told immediately rather than discovering it at integration.

---

## 9. Open questions — your input needed

| ID | Question | Blocks |
|---|---|---|
| **OQ-9** | Ed25519 private-key custody. Who is the offline signer? Is a hardware token available? | Me — signed exports and pack lifecycle |
| **OQ-10** | HMAC pseudonymisation key rotation. My position: deployment-scoped, long-lived, no routine rotation — because rotating requires re-pseudonymising the whole evidence lake, which is **your** data. Needs your agreement. | Me + you |
| **OQ-6** | Is there an existing egress layer to reuse for `sovereignty_check.py`, or build fresh? | Me |
| **OQ-8** | SOCSim is 20–30 entities now. Retain Alpha/Beta/Gamma as demo presets, or drop them? | Dev 3 |
| **New** | Exact types for the `evidence` and `baseline` handles I pass to your NS functions. I propose read-only DuckDB connection + a `BaselineService` result object. Confirm or counter. | Me + you |
| **New** | Should `nlp_auditor` return one `IndicatorResult` per entity (EG-10), or one per cluster with entity attribution? I lean per-entity with `evidence_row_ids` naming the cluster's notes, because scoring aggregates per entity. | Me + you |

---

## 10. What I owe you next

| Deliverable | By |
|---|---|
| `data/reference/india_holidays.csv` | +~1 h |
| `data/policies/policy_nccipc_default.yaml` with all thresholds | +~4 h |
| `data/mappings/*.yaml` vendor profiles | +~5 h |
| Ingestion pipeline so you have real evidence to run against | +~6 h |
| Stubs for NS-01/02/04/05 | +~12 h |
| Frozen `/openapi.json` for Dev 1 | +~18 h |
| Signed-export and pack lifecycle code (pending OQ-9) | +~21 h |

---

## Quick start for Dev 3

```bash
cd backend
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -c "from app.schemas.indicator import IndicatorResult; print('contract OK')"

# Load a seed and inspect what is available
python -c "
from app.db.sqlite import init_db; init_db()
from app.db.duckdb_client import DuckDBClient
d = DuckDBClient()
with d.reader() as c:
    print([r[0] for r in c.execute('SHOW TABLES').fetchall()])
"
```

Evidence tables you read: `alert_record`, `case_record`, `case_event`, `escalation`, `telemetry_daily`, `alert_case`.

### ⚠️ `escalation` now carries `entity_id` (Phase 8)

**Query `escalation` by `entity_id`. Join on `(entity_id, case_id)` — never `case_id` alone.**

`case_id` is unique *within a submission*, not across the lake. Two CSEs both numbering their cases `0001`…`0060` is the normal case, not an edge case. Without `entity_id`, EG-06 looked up "which case ids were escalated" across every entity, so a peer that properly escalated its case 0004 made the subject's identically-numbered, unescalated case look escalated too. The indicator under-reported precisely in the peer cohorts it exists to compare against.

`entity_id` is injected at load, same as `alert_record` and `case_record`, so no mapping is needed for it. Added with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` — an existing evidence lake migrates on next `init()`, no rebuild required.

`alert_case` has no `entity_id` and does not need one: it is a bridge whose endpoints already carry it, so it is scoped through its cases. `escalation` had no such endpoint that identifies the *submitting* entity.

---

**Questions on anything above — ping me. If a contract looks wrong, say so now.** Rewriting a frozen interface after integration costs all three of us; disagreeing before you write a line costs nothing.