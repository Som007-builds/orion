# Dev 3 ML and validation handoff

## Status

NS-01 through NS-06, Isolation Forest, deterministic attribution, NLP auditing,
SOCSim chunk generation, traceability checks, and the Dev 2 RulesEngine boundary
are implemented. No frontend or frozen schema files were changed. Python 3.11.15
is used with working NumPy 2.2.6, SciPy 1.14.1, and sklearn 1.6.0 imports.

## Detector ownership

The authoritative machine-readable matrix is `detector_ownership.json`; its source
is `backend/eval/detector_ownership.py`. Rapid closure, SLA bunching, backlog
washing, timestamp fabrication, critical-without-escalation, ID gaps, and
retroactive edits are Dev 2 RulesEngine responsibilities. NS-01/02/04/05/06 and
the NLP auditor are Dev 3 responsibilities. Metric gaming is composite.

## Validation

The corrected missingness harness uses clean negative controls and counts
assessable findings. At 0%, 10%, 25%, and 50% activity-field deletion, assessable
rate was 1.0, confidence mean 1.0, precision 1.0, and recall 1.0. The target
remained above the detector's three-valid-row minimum, so no NOT_ASSESSABLE case
occurred in this particular sweep; insufficient evidence still returns
NOT_ASSESSABLE and is never treated as safe.

Hard-negative FPR was 0.6667 (4/6) before ownership correction. After applying
the correct owner and explicit legitimate-quiet context, Dev 3 FPR is 0.0 (0/6).
Per-scenario evidence is in `hard_negative_results.json`.

The dose-response matrix contains 70 rows: all 14 required injection families at
five doses. Dev 3-owned families include measured detection, scores,
confidence/assessability, and evidence IDs. RulesEngine/composite families are
independently truth-labelled and owner-mapped, but explicitly marked as requiring
the persisted Core path when the offline evaluator cannot run that component.

Adaptive evaluation contains six scenarios with baseline/manipulated metrics,
owner, detector, truth, classification, and explanation. Repetitive-note gaming
is detected by NLP; RulesEngine-owned scenarios are `NOT_ASSESSABLE` in the
offline Dev3-only runner rather than being misreported as Dev3 failures.

All dose levels (0, .25, .50, .75, 1.0) are in `dose_response_results.json`.
Direct Dev 3 coverage exists for silent critical asset, low volume, template
monoculture, and common-shock non-response. Rapid closure, critical-without-
escalation, and metric gaming are recorded as RulesEngine/composite paths, with
no unsupported detection metrics claimed. Repetitive-note gaming is detected by
NLP; other adaptive scenarios are not detected by Dev 3 and are correctly
reported as owned by other paths.

## Scale benchmarks

Streamed generation on macOS 27 ARM64 / Python 3.11.15 measured:

| target | rows | seconds | rows/sec |
|---:|---:|---:|---:|
| 1M | 993,964 | 1.75 | 568,395 |
| 10M | 9,942,462 | 17.80 | 558,651 |
| 50M | 49,735,130 | 90.51 | 549,484 |

Full target-scale detector/NLP throughput remains partial because current APIs
require in-memory entity features and NLP similarity is quadratic. Bounded
incremental aggregation hooks and bounded NLP sampling are implemented, but no
unmeasured full-target throughput is claimed.

`backend/eval/streaming_features.py` preserves small-fixture entity ordering,
counts, detector rows, and evidence IDs across chunked input. The current
adapter retains raw rows for exact small-fixture equivalence, so it is not yet a
bounded-memory full-scale detector adapter. Accordingly, the 1M/10M/50M artifact
contains measured generation throughput only; detector throughput is not claimed.

`backend/eval/persisted_validation.py` reuses the real PackLifecycleFixture,
SQLite/DuckDB initialization, `ScoringService.score_period(persist=True)`, and
persisted findings. A rapid-closure run produced persisted EG-01 findings. The
persisted dose artifact contains 45 rows across nine RulesEngine-owned families
and five doses; unsupported fixture families are explicitly `NOT_ASSESSABLE`.
The adaptive artifact contains four persisted RulesEngine scenarios.

## Traceability, E2E, and tests

Traceability checked 100 note evidence IDs: all resolved and all checked features
were observed. Offline ten-scenario E2E schema, evidence, confidence, and
determinism checks pass. Focused Dev 3 tests: 12 passed, including the streaming
regression. The full backend suite now passes: 118 tests, 0 failures, 0 errors.
The root cause was test cleanup
unlinking the temporary SQLite database while the application retained a
thread-local cached connection. The fixture now closes that connection before
cleanup, allowing the next module to apply the complete DDL including `pack`.

## Reproduction

```bash
PYTHONPATH=backend:. .venv/bin/python -m unittest backend.tests.test_dev3_ml -q
PYTHONPATH=backend:. .venv/bin/python -m backend.eval.dev3_validation --out backend/eval/results-final
PYTHONPATH=backend:. .venv/bin/python -m unittest discover -s backend/tests -p 'test*.py' -q
git diff --check
git status --short
```

## Final classification

- **DONE:** persisted RulesEngine wiring, real rapid-closure execution,
  ownership-aware persisted artifacts, streaming small-fixture equivalence,
  regression tests, and full backend suite.
- **PARTIAL:** full target-scale detector/NLP benchmark. Generation through 50M
  is measured, but the current adapter is not yet bounded-memory for full raw
  detector/NLP workloads, so detector throughput is not claimed.
- **PARTIAL:** persisted dose/adaptive coverage. The real path executes, but the
  reusable fixture contains complete source data for rapid closure only; other
  families are explicitly `NOT_ASSESSABLE`, not fabricated detections.
