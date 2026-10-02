"""Ed25519 host signing key (open question OQ-9, resolved for 2.13).

The key is generated on first use into ``data/keys/`` (gitignored) and never
leaves the host: there is no key material in the repo, none in ``.env`` and none
in any test. ``signed_by`` therefore labels an artefact as attributable to the
*signing host* (``host:<hostname>``), never to an individual — we do not claim
non-repudiation we cannot back, which is the OQ-9 record in progress.md.

What that buys is integrity with a custody story an operator can actually check:

  * the keypair is created once and reused, so artefacts exported months apart
    verify against the same public key (``orion_signing.pub`` sits next to the
    private key on the host);
  * re-exporting an unchanged pack reproduces the same bytes and the same Ed25519
    signature (Ed25519 is deterministic), so "the artefact did not change" is
    checkable by re-exporting, without needing a timestamp oracle.

The bytes that get signed are not the artefact bytes themselves but a labelled
envelope — ``orion-export:v1:`` plus the SHA-256 of the artefact — turned into an
ASCII hex digest. The label names the scheme version so a future scheme change
cannot be mistaken for the same scheme; hashing keeps the signature compact and
makes ``verify_bytes`` accept the artefact itself on both sides of the wire.

Signing is a *server* fault when it fails: a report that exists unsigned is
worse than no report, so failures raise ``SigningFailure``. The class name
deliberately does not end in ``Error`` — the API layer's convention maps
``*Error`` to a 400 (client fault), and a key that cannot be loaded or created is
not the caller's fault. It falls through to 500.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.config import get_settings

#: The signed envelope label. Bumped only when the signing scheme changes; the
#: label is part of the signed bytes, so a bump invalidates old signatures on
#: purpose rather than silently.
_SIGNING_LABEL = b"orion-export:v1:"

PRIVATE_KEY_FILE = "orion_signing.key"
PUBLIC_KEY_FILE = "orion_signing.pub"


class SigningFailure(Exception):
    """The host signing key could not be loaded or created (maps to 500)."""


def host_label() -> str:
    """Attribution label: the signing host, never a person."""
    return f"host:{socket.gethostname()}"


def signing_input(payload: bytes) -> bytes:
    """The bytes that are signed for a given artefact payload."""
    return _SIGNING_LABEL + hashlib.sha256(payload).hexdigest().encode("ascii")


def _key_paths() -> tuple[Path, Path]:
    settings = get_settings()
    d = settings.pubkey_dir
    return d / PRIVATE_KEY_FILE, d / PUBLIC_KEY_FILE


def _private_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _public_bytes(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _load_private(data: bytes) -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(data, password=None)


def _load_public(data: bytes) -> Ed25519PublicKey:
    return serialization.load_pem_public_key(data)


def signing_key() -> Ed25519PrivateKey:
    """Load the host signing key, generating it atomically on first use."""
    priv_path, pub_path = _key_paths()
    try:
        priv_path.parent.mkdir(parents=True, exist_ok=True)
        if priv_path.exists():
            key = _load_private(priv_path.read_bytes())
        else:
            # O_EXCL: two processes generating at once must not overwrite each
            # other; the loser re-reads what the winner wrote.
            try:
                fd = os.open(str(priv_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                key = _load_private(priv_path.read_bytes())
            else:
                with os.fdopen(fd, "wb") as fh:
                    key = Ed25519PrivateKey.generate()
                    fh.write(_private_bytes(key))
        # The public key always derives from the private key we hold, so a
        # stale or partial pub file can never disagree with it.
        pub_path.write_bytes(_public_bytes(key.public_key()))
        try:
            os.chmod(priv_path, 0o600)
        except OSError:  # Windows: permissions are advisory; gitignore is the guard
            pass
        return key
    except (OSError, ValueError, TypeError) as exc:  # pragma: no cover - env specific
        raise SigningFailure(
            f"cannot load or create the host signing key at {priv_path}: {exc}"
        ) from exc


def public_key() -> Ed25519PublicKey:
    try:
        return signing_key().public_key()
    except SigningFailure:
        raise
    except Exception as exc:  # pragma: no cover - env specific
        raise SigningFailure(f"cannot derive the public signing key: {exc}") from exc


def sign_bytes(payload: bytes) -> tuple[str, str]:
    """Sign an artefact. Returns ``(signature_base64, signed_by)``."""
    try:
        signature = signing_key().sign(signing_input(payload))
    except SigningFailure:
        raise
    except Exception as exc:  # pragma: no cover - env specific
        raise SigningFailure(f"signing failed: {exc}") from exc
    return (
        base64.b64encode(signature).decode("ascii"),
        host_label(),
    )


def verify_bytes(payload: bytes, signature_b64: str) -> bool:
    """Verify an artefact against the host public key. False on any failure."""
    try:
        signature = base64.b64decode(signature_b64, validate=True)
        public_key().verify(signature, signing_input(payload))
        return True
    except (InvalidSignature, ValueError, SigningFailure, OSError):
        return False


def key_fingerprint() -> str:
    """Short, stable identifier of the host public key, for operator checks."""
    digest = hashlib.sha256(_public_bytes(public_key())).hexdigest()
    return digest[:16]