"""Verify a signed review-pack export artefact against the host signing key.

The export endpoint signs the artefact with the host's Ed25519 key (generated on
first use into ``data/keys/``, gitignored) and returns the base64 signature in
the `GET /review-packs/{id}/export` response. This script checks a file on disk
against that signature using the public key that sits next to the private key on
the same host — the custody claim is *attributable to the signing host*, never
to an individual (see progress.md, open question OQ-9).

Usage:  python scripts/verify_export.py <artefact_file> <signature_base64>

Exit status 0 means the signature matches this exact file; anything else prints a
reason and exits 1.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.signing_service import verify_bytes  # noqa: E402


def _main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: python scripts/verify_export.py <artefact_file> "
            "<signature_base64>",
            file=sys.stderr,
        )
        return 2
    artefact = Path(sys.argv[1])
    signature = sys.argv[2].strip()
    if not artefact.exists():
        print(f"FAILED - {artefact} does not exist", file=sys.stderr)
        return 1
    payload = artefact.read_bytes()
    if verify_bytes(payload, signature):
        print(f"VERIFIED ok - {artefact.name} matches the signature")
        return 0
    print(
        "FAILED - the signature does not match this file (tampered, or the "
        "host signing key changed since export)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())