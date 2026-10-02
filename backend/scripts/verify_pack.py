"""Verify a staged pack against its registered hash and the host signing key.

The pack endpoint signs the bundle's deterministic content hash with the host's
Ed25519 key (generated on first use into ``data/keys/``, gitignored) and stores
the signature on the pack row. This script recomputes the hash from the bundle
on disk in ``data/packs/staged/<pack_id>/``, checks it against the registered
hash, and verifies the stored signature with the public key that sits next to
the private key on the same host — the custody claim is *attributable to the
signing host*, never to an individual (see progress.md, open question OQ-9).

Usage:  python scripts/verify_pack.py <pack_id>

Exit status 0 means the bundle is intact and its signature verifies; anything
else prints a reason and exits 1.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.config import get_settings  # noqa: E402
from app.db.sqlite import get_connection, init_db  # noqa: E402
from app.services.signing_service import verify_bytes  # noqa: E402

_PACK_LABEL = b"orion-pack:v1:"


def _bundle_hash(bundle: Path) -> str:
    entries = {}
    for path in sorted(bundle.rglob("*")):
        if path.is_file():
            rel = path.relative_to(bundle).as_posix()
            entries[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/verify_pack.py <pack_id>", file=sys.stderr)
        return 2
    pack_id = sys.argv[1].strip()

    init_db()
    settings = get_settings()
    row = get_connection().execute(
        "SELECT version, content_hash, signature, status, signed_by "
        "FROM pack WHERE pack_id = ?",
        (pack_id,),
    ).fetchone()
    if row is None:
        print(f"FAILED - pack {pack_id} is not registered", file=sys.stderr)
        return 1

    bundle = settings.pack_dir / "staged" / pack_id
    if not bundle.is_dir():
        print(f"FAILED - bundle for {pack_id} missing at {bundle}", file=sys.stderr)
        return 1

    actual = _bundle_hash(bundle)
    if actual != row["content_hash"]:
        print(
            f"FAILED - bundle content hash changed (registered "
            f"{row['content_hash'][:16]}..., on disk {actual[:16]}...) — the "
            "bundle was altered after staging",
            file=sys.stderr,
        )
        return 1

    payload = _PACK_LABEL + row["content_hash"].encode("ascii")
    if verify_bytes(payload, row["signature"]):
        print(
            f"VERIFIED ok - pack {pack_id} ({row['status']}, version "
            f"{row['version']}, signed by {row['signed_by']}) matches its "
            "registered signature"
        )
        return 0
    print(
        "FAILED - the signature does not verify (tampered, or the host signing "
        "key changed since staging)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(_main())