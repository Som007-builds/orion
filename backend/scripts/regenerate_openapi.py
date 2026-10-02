"""Regenerate the frozen OpenAPI document.

`docs/openapi-v1.json` is the published contract Dev 1 types against and
`tests/test_contract.py` guards it with a byte-for-byte regeneration check.
When an intended change alters the surface (a route goes live, a schema gains a
field), regenerate the document in the same commit — never edit it by hand.

Usage:  python scripts/regenerate_openapi.py [--write]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.main import create_app  # noqa: E402

FROZEN_SPEC = BACKEND.parent / "docs" / "openapi-v1.json"


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true",
        help="Write the document to docs/openapi-v1.json",
    )
    args = parser.parse_args()

    serialised = json.dumps(create_app().openapi(), sort_keys=True, indent=2)

    if not args.write:
        print(serialised)
        return 0

    FROZEN_SPEC.write_text(serialised + "\n", encoding="utf-8")
    print(f"wrote {FROZEN_SPEC} ({len(serialised)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())