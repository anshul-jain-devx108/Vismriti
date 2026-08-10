"""Vismriti AgentOS runtime launcher.

Starts the FastAPI service with host, port and log level taken from `.env`.
Equivalent to: uvicorn vismriti.main:app --host $AGENTOS_HOST --port $AGENTOS_PORT

Usage:
    python run.py                     # dev, reload on
    python run.py --no-reload         # no auto-reload
    AGENTOS_PORT=9000 python run.py   # override port via env
"""

from __future__ import annotations

import argparse

import uvicorn

from vismriti.utils.config import settings

LOG_LEVELS = ("critical", "error", "warning", "info", "debug", "trace")


def _default_log_level() -> str:
    level = settings.log_level.strip().lower()
    return level if level in LOG_LEVELS else "info"


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
        default=_default_log_level(),
        choices=LOG_LEVELS,
        help=f"Log level (default: {_default_log_level()}, from LOG_LEVEL)",
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
