"""Test suite. Run: `python -m unittest discover tests`

The writable runtime paths are redirected to a temp directory **here**, at package
import, rather than in a test module. `app.config.get_settings()` is cached and
reads the environment at first use, so a test module that set those variables in
its own body would be doing it after another module had already imported `app` --
and which module wins would depend on alphabetical discovery order.

`data/policies`, `data/mappings` and `data/reference` are deliberately NOT
redirected. They hold tracked assets rather than runtime state, and pointing at a
temp copy makes `activate("nccipc_default")` raise KeyError for the wrong reason.
"""

import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="orion-tests-"))

os.environ.setdefault("ORION_ENV", "dev")
os.environ["ORION_DATA_DIR"] = str(_TMP / "data")
os.environ["ORION_SQLITE_PATH"] = str(_TMP / "data" / "sqlite" / "orion.db")
os.environ["ORION_PARQUET_DIR"] = str(_TMP / "data" / "parquet")
os.environ["ORION_PACK_DIR"] = str(_TMP / "data" / "packs")


def cleanup() -> None:
    """Remove the temp runtime tree. Called from each module's `tearDownModule`."""
    shutil.rmtree(_TMP, ignore_errors=True)