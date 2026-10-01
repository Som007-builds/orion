"""Orion Core Backend — entry point.

Loopback only. Nothing in this module opens an outbound connection.
"""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    # Loopback-only bind: Orion is air-gapped and must not be network-reachable.
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info",
    )