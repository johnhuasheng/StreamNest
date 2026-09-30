"""Entrypoint for the bundled, loopback-only StreamNest resolver."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("STREAMNEST_RESOLVER_PORT", "8788"))
    if not 1 <= port <= 65535:
        raise ValueError("Invalid resolver port")
    # Import after the launcher has supplied its per-installation output path.
    from streamnest_api.main import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
