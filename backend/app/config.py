"""Application configuration.

Air-gapped by design: no outbound hosts appear anywhere in this file, and no
secret material is stored in the repo. Secrets are referenced by *handle* and
resolved at runtime from outside the working tree (see `resolve_secret`).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- paths -------------------------------------------------------------
    data_dir: Path = BACKEND_ROOT / "data"
    sqlite_path: Path = BACKEND_ROOT / "data" / "sqlite" / "orion.db"
    parquet_dir: Path = BACKEND_ROOT / "data" / "parquet"
    policy_dir: Path = BACKEND_ROOT / "data" / "policies"
    mapping_dir: Path = BACKEND_ROOT / "data" / "mappings"
    pack_dir: Path = BACKEND_ROOT / "data" / "packs"
    reference_dir: Path = BACKEND_ROOT / "data" / "reference"
    seed_dir: Path = BACKEND_ROOT / "data" / "seed"
    pubkey_dir: Path = BACKEND_ROOT / "data" / "keys"

    # --- secrets -----------------------------------------------------------
    # Handles, never literal keys. `resolve_secret()` maps a handle to material
    # held outside the repository (env var or operator-supplied key file).
    hmac_key_handle: str = "HMAC_KEY_HANDLE"
    hmac_key_ref: str = "HMAC_KEY_REF"
    note_key_handle: str = "NOTE_KEY_HANDLE"
    session_key_handle: str = "SESSION_KEY_HANDLE"

    # --- upload security limits (CHANGELOG B.5) ----------------------------
    max_upload_bytes: int = 2 * 1024**3  # 2 GiB
    max_rows_per_file: int = 50_000_000
    max_csv_columns: int = 5_000
    max_compression_ratio: int = 100  # zip-bomb guard
    session_timeout_min: int = 30

    # --- analysis ----------------------------------------------------------
    seed: int = 42  # fixed; recorded on every run for reproducibility

    @property
    def sqlite_wal_enabled(self) -> bool:
        """Always on. SQLite is the state of record; WAL is not optional."""
        return True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    for directory in (
        settings.data_dir,
        settings.parquet_dir,
        settings.pack_dir / "staged",
        settings.pack_dir / "active",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return settings


def resolve_secret(handle: str) -> bytes:
    """Resolve a secret handle to key material held outside the repo.

    Lookup order:
      1. Environment variable named after the handle
      2. Operator key file at ``$ORION_SECRET_DIR/<handle>`` (default: unset)
      3. Development-only deterministic derivation, **refused** when
         ``ORION_ENV`` is anything other than ``dev``.

    The development fallback exists so a fresh clone can boot for a demo. It is
    explicitly not production behaviour and loudly fails closed otherwise.
    """
    import os

    material = os.environ.get(handle)
    if material:
        return material.encode("utf-8")

    secret_dir = os.environ.get("ORION_SECRET_DIR")
    if secret_dir:
        key_file = Path(secret_dir) / handle
        if key_file.is_file():
            return key_file.read_bytes().strip()

    if os.environ.get("ORION_ENV", "dev") == "dev":
        # Deterministic so pseudonymisation is stable across restarts in dev.
        # Never reachable in a non-dev deployment.
        import hashlib

        return hashlib.sha256(f"orion-dev::{handle}".encode()).digest()

    raise RuntimeError(
        f"Secret handle {handle!r} is unresolvable and ORION_ENV is not 'dev'. "
        "Provide it via environment variable or ORION_SECRET_DIR. Orion does not "
        "ship secret material in the repository."
    )