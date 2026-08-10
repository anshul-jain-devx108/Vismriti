"""Vismriti AgentOS runtime launcher.

One-command entrypoint that starts the FastAPI service (built on Agno's
AgentOS) with host/port/reload driven from `.env`. Equivalent to running:

    uvicorn vismriti.main:app --host $AGENTOS_HOST --port $AGENTOS_PORT

Usage:
    python run.py                  # dev, reload on
    python run.py --no-reload      # prod-style: no auto-reload
    AGENTOS_PORT=9000 python run.py   # override port via env
"""

from __future__ import annotations

import argparse

import uvicorn

from vismriti.utils.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Launch the Vismriti AgentOS service.",
    )
    parser.add_argument(
        "--host",
        default=settings.agentos_host,
        help=f"Bind host (default: {settings.agentos_host}, from AGENTOS_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.agentos_port,
        help=f"Bind port (default: {settings.agentos_port}, from AGENTOS_PORT)",
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable auto-reload (use in production).",
    )
    parser.add_argument(
        "--log-level",
        default=settings.log_level.lower(),
        help=f"Log level (default: {settings.log_level.lower()}, from LOG_LEVEL)",
    )
    args = parser.parse_args()

    uvicorn.run(
        "vismriti.main:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
