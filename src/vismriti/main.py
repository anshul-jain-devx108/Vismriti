"""AgentOS runtime entrypoint.

Wires the Vismriti Agno agent into a FastAPI app that exposes the agent over
REST, an optional MCP server endpoint, and an optional Slack interface.
Everything is driven by `vismriti.utils.config.settings`; see `.env.example`.

Run:
    python -m vismriti.main
    uvicorn vismriti.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from agno.db.sqlite import SqliteDb
from agno.os import AgentOS
from agno.utils.log import log_error, log_info, log_warning
from fastapi import FastAPI

from .agent import build_agent
from .utils.config import settings

# Storage backend is driven by AGENTOS_DB_URL:
#   sqlite:///./runs/vismriti.db          -> SqliteDb
#   postgresql+psycopg://user:pw@host/db  -> PostgresDb
# The same db instance backs the Agno Agent and the AgentOS runtime, so
# sessions, HITL approvals and traces all land in one store for auditing.


def _build_db():
    url = settings.agentos_db_url.strip()
    if url.startswith(("postgresql", "postgres://")):
        from agno.db.postgres import PostgresDb  # imported only when configured

        return PostgresDb(db_url=url)

    # sqlite: strip the scheme, resolve relative paths against output_dir's parent
    if url.startswith("sqlite:///"):
        raw = url[len("sqlite:///"):]
    elif url.startswith("sqlite://"):
        raw = url[len("sqlite://"):]
    else:
        raw = url or str(Path(settings.output_dir).parent / "vismriti.db")

    db_path = Path(raw)
    if not db_path.is_absolute():
        db_path = (Path(settings.output_dir).parent / db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteDb(db_file=str(db_path))


_shared_db = _build_db()

vismriti_agent = build_agent(db=_shared_db)


def _build_interfaces() -> list:
    """Slack is opt-in via SLACK_ENABLED plus token and signing secret."""
    interfaces: list = []

    if not settings.slack_enabled:
        return interfaces

    missing = [
        name
        for name, value in (
            ("SLACK_BOT_TOKEN", settings.slack_bot_token),
            ("SLACK_SIGNING_SECRET", settings.slack_signing_secret),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"SLACK_ENABLED=true but the following env vars are empty: "
            f"{', '.join(missing)}. Fill them in .env or unset SLACK_ENABLED."
        )

    try:
        from agno.os.interfaces.slack import Slack
    except ImportError as exc:
        raise RuntimeError(
            f"SLACK_ENABLED=true but the Agno Slack interface failed to import: {exc}. "
            "Install the Slack extra (`pip install slack-sdk`) or set SLACK_ENABLED=false."
        ) from exc

    interfaces.append(
        Slack(
            agent=vismriti_agent,
            token=settings.slack_bot_token,
            signing_secret=settings.slack_signing_secret,
            reply_to_mentions_only=settings.slack_reply_to_mentions_only,
            streaming=True,
            task_display_mode="plan",
            loading_text="Traversing lineage...",
            suggested_prompts=[
                {
                    "title": "Erase a subject",
                    "text": "Erase priya.sharma@example.com",
                },
                {
                    "title": "List PII columns",
                    "text": "Show me every PII-tagged column DataHub knows about.",
                },
            ],
        )
    )

    return interfaces


def _mcp_server_enabled() -> bool:
    """True when AGENTOS_MCP_SERVER is on and its dependencies actually import.

    A broken fastmcp/mcp pairing would otherwise abort the whole process at
    import time. The MCP endpoint is not on any deletion path, so the service
    still starts without it, but the failure is logged as an error and the
    startup banner reports the endpoint as unavailable.
    """
    if not settings.agentos_mcp_server:
        return False
    try:
        from agno.os.mcp import get_mcp_server  # noqa: F401
    except ImportError as exc:
        log_error(
            f"AGENTOS_MCP_SERVER=true but the MCP server could not be loaded: {exc}. "
            "This usually means the installed fastmcp and mcp packages are an "
            "incompatible pair. Reinstall a matching pair, or set "
            "AGENTOS_MCP_SERVER=false. Starting WITHOUT the /mcp endpoint."
        )
        return False
    return True


_mcp_server_active = _mcp_server_enabled()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Log the resolved configuration and refuse to serve a misconfigured runtime."""
    log_info(f"{settings.app_name} v{settings.app_version} starting")
    log_info(f"  model:        {settings.model}")
    log_info(f"  fixture_mode: {settings.use_fixtures}")
    log_info(f"  dry_run:      {settings.dry_run}")
    log_info(f"  slack:        {'ENABLED' if settings.slack_enabled else 'disabled'}")
    if settings.agentos_mcp_server and not _mcp_server_active:
        log_info("  mcp_server:   UNAVAILABLE (requested, dependencies failed to load)")
    else:
        log_info(f"  mcp_server:   {'ENABLED' if _mcp_server_active else 'disabled'}")

    if settings.use_fixtures:
        log_warning(
            "FIXTURE MODE IS ON (VISMRITI_USE_FIXTURES=true). Every plan is built "
            "from canned JSON in vismriti/services/fixtures, not from DataHub, and "
            "no erasure it reports has touched real data. Set "
            "VISMRITI_USE_FIXTURES=false before pointing anyone at this instance."
        )
    else:
        # Live mode with no metadata backend cannot do anything safely, and
        # guessing a URL is worse than refusing to start.
        settings.require_datahub_gms_url("Live mode (VISMRITI_USE_FIXTURES=false)")
        log_info(f"  datahub_gms:  {settings.datahub_gms_url}")

    # LLM key check: Azure OpenAI endpoint takes precedence; only warn if
    # neither Azure nor OpenAI credentials are available for an openai model.
    if settings.model.startswith("openai:"):
        azure_ok = bool(settings.azure_openai_endpoint and settings.azure_openai_api_key)
        openai_ok = bool(settings.openai_api_key)
        if not (azure_ok or openai_ok):
            log_warning(
                f"Model is {settings.model} but neither OPENAI_API_KEY nor "
                "AZURE_OPENAI_(ENDPOINT+API_KEY) is set, so LLM calls will fail."
            )
        elif azure_ok:
            log_info(f"  llm_backend:  Azure OpenAI @ {settings.azure_openai_endpoint}")
        else:
            log_info("  llm_backend:  OpenAI direct")

    yield

    log_info(f"{settings.app_name} shutting down.")


# checkpoint="tool-batch" makes HITL flows resumable: after a restart, or when
# an approver returns hours later, Agno replays state from the db and continues
# at the last completed tool boundary.
agent_os = AgentOS(
    id=settings.app_id,
    name=settings.app_name,
    description=f"{settings.app_name} - DataHub Erasure Agent runtime",
    version=settings.app_version,
    db=_shared_db,
    agents=[vismriti_agent],
    interfaces=_build_interfaces(),
    mcp_server=_mcp_server_active,
    a2a_interface=settings.agentos_a2a,
    tracing=settings.agentos_tracing,
    telemetry=settings.agentos_telemetry,
    checkpoint="tool-batch",
    lifespan=_lifespan,
)

app = agent_os.get_app()


def main() -> None:
    """Serve the AgentOS app locally, using host/port from settings."""
    agent_os.serve(
        app="vismriti.main:app",
        reload=True,
        host=settings.agentos_host,
        port=settings.agentos_port,
    )


if __name__ == "__main__":
    main()
