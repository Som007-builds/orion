"""Reproduce a finding's evidence from its stored query.

Every finding stores a re-runnable `evidence_query` (plan §8.6). This script
re-executes it and asserts that the row ids it returns are identical to the
`finding_evidence` rows persisted when the finding was materialised. A pass is
evidence; a mismatch names the first difference and exits 1.

Usage:
    python scripts/reproduce_finding.py --finding F-<id>
    python scripts/reproduce_finding.py --finding F-<id> --json

Exit 0 on identical row ids, 1 on any mismatch or error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.sqlite import get_connection  # noqa: E402
from app.services.rules_engine import _reproduce_evidence  # noqa: E402


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finding", required=True, help="Finding id, e.g. F-<hex>")
    parser.add_argument(
        "--json", action="store_true", help="Emit the result as one JSON object"
    )
    args = parser.parse_args()

    row = get_connection().execute(
        "SELECT finding_id, evidence_query, entity_id, indicator_id, run_id "
        "FROM finding WHERE finding_id = ?",
        (args.finding,),
    ).fetchone()
    if row is None:
        print(f"finding {args.finding}: not found", file=sys.stderr)
        return 1

    persisted = [
        str(r["row_id"])
        for r in get_connection().execute(
            "SELECT row_id FROM finding_evidence WHERE finding_id = ? ORDER BY ordinal",
            (args.finding,),
        )
    ]

    query = row["evidence_query"]
    if not query:
        result = {
            "finding_id": args.finding,
            "status": "no_evidence_query",
            "persisted_row_ids": persisted,
            "reproduced_row_ids": [],
            "message": "The finding stores no evidence query, so there is "
            "nothing to reproduce. A lead without rows cannot claim evidence.",
            "match": False,
        }
        print(json.dumps(result) if args.json else (
            f"{args.finding}: NO stored evidence query — {result['message']}"
        ))
        return 0

    reproduced = _reproduce_evidence(str(query))

    match = reproduced == persisted
    result = {
        "finding_id": args.finding,
        "run_id": row["run_id"],
        "entity_id": row["entity_id"],
        "indicator_id": row["indicator_id"],
        "stored_query": str(query),
        "status": "match" if match else "mismatch",
        "persisted_row_ids": persisted,
        "reproduced_row_ids": reproduced,
        "match": match,
    }
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif match:
        print(
            f"{args.finding} ({row['indicator_id']}): OK — "
            f"{len(reproduced)} evidence row ids reproduced identically"
        )
    else:
        print(
            f"{args.finding} ({row['indicator_id']}): MISMATCH — "
            f"{len(persisted)} persisted, {len(reproduced)} reproduced",
            file=sys.stderr,
        )
        for i, (a, b) in enumerate(zip(persisted, reproduced)):
            if a != b:
                print(f"  first difference at ordinal {i}: stored {a!r}, "
                      f"re-run {b!r}", file=sys.stderr)
                break
    return 0 if match else 1


if __name__ == "__main__":
    raise SystemExit(_main())