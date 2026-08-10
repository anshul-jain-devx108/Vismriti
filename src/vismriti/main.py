"""Vismriti as an AgentOS service — the runtime entrypoint.

This module wires the Vismriti Agno agent (from `vismriti.agent`) into an
AgentOS FastAPI runtime that provides:

    • REST endpoint at /agents/vismriti/runs      (production trigger)
    • MCP server endpoint at /mcp                 (Vismriti IS an MCP server)
    • Slack interface at /slack/*                 (opt-in via SLACK_ENABLED)
    • /approvals + Slack Block Kit HITL           (framework-level gates)
    • Sessions, memories, traces, checkpoints     (persisted to _shared_db)
    • Startup config validation                   (lifespan hook)

All behaviour is driven by `vismriti.config.settings` — one source of truth
for every knob. See `.env.example` for the full list.

Run:
    python -m vismriti.main                                     # dev, reload on
    uvicorn vismriti.main:app --host 0.0.0.0 --port 8000        # prod
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from agno.db.sqlite import SqliteDb
from agno.os import AgentOS
from agno.utils.log import log_info, log_warning
from fastapi import FastAPI

from .agent import build_agent
from .utils.config import settings


# ── Storage ────────────────────────────────────────────────────────────────
# Backend is driven by AGENTOS_DB_URL:
#   sqlite:///./runs/vismriti.db          → SqliteDb (default; single-node demos)
#   postgresql+psycopg://user:pw@host/db  → PostgresDb (Azure Postgres, prod)
#
# The db instance is shared across the Agno Agent AND the AgentOS runtime,
# so sessions/HITL-approvals/traces all persist to one store — that's the
# single source of truth an auditor can query.

def _build_db():
    url = settings.agentos_db_url.strip()
    if url.startswith("postgresql") or url.startswith("postgres://"):
        from agno.db.postgres import PostgresDb  # lazy import — only when needed

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


# ── Agent ──────────────────────────────────────────────────────────────────
vismriti_agent = build_agent(db=_shared_db)


# ── Interfaces (Slack, Telegram, …) ────────────────────────────────────────
# Slack is opt-in: enable by setting SLACK_ENABLED=true plus filling
# SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET. When disabled, we don't even
# import the Slack module — keeps startup fast and avoids peer-dep pressure.

def _build_interfaces() -> list:
    interfaces: list = []

    if settings.slack_enabled:
        # Fail loud on missing credentials — better than a silently broken bot.
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

        from agno.os.interfaces.slack import Slack

        interfaces.append(
            Slack(
                agent=vismriti_agent,
                token=settings.slack_bot_token,
                signing_secret=settings.slack_signing_secret,
                reply_to_mentions_only=settings.slack_reply_to_mentions_only,
                streaming=True,
                task_display_mode="plan",
                loading_text="Traversing lineage…",
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


# ── Lifespan — startup + shutdown hooks ───────────────────────────────────
# On boot: verify config sanity, log warnings for obvious mistakes so ops
# can catch them from the first log line rather than after the first failed
# request. On shutdown: nothing custom — AgentOS handles its own cleanup.

@asynccontextmanager
async def _lifespan(app: FastAPI):
    log_info(f"{settings.app_name} v{settings.app_version} starting…")
    log_info(f"  model:        {settings.model}")
    log_info(f"  fixture_mode: {settings.use_fixtures}")
    log_info(f"  dry_run:      {settings.dry_run}")
    log_info(f"  slack:        {'ENABLED' if settings.slack_enabled else 'disabled'}")
    log_info(f"  mcp_server:   {'ENABLED' if settings.agentos_mcp_server else 'disabled'}")

    if not settings.use_fixtures and not settings.datahub_gms_url:
        log_warning(
            "VISMRITI_USE_FIXTURES=false but DATAHUB_GMS_URL is empty — live "
            "DataHub calls will fail. Set the env var or flip to fixture mode."
        )
    # LLM key check: Azure OpenAI endpoint takes precedence; only warn if
    # NEITHER Azure nor OpenAI key is available for an openai-family model.
    if settings.model.startswith("openai:"):
        azure_ok = bool(settings.azure_openai_endpoint and settings.azure_openai_api_key)
        openai_ok = bool(settings.openai_api_key)
        if not (azure_ok or openai_ok):
            log_warning(
                f"Model is {settings.model} but neither OPENAI_API_KEY nor "
                "AZURE_OPENAI_(ENDPOINT+API_KEY) is set — LLM calls will fail."
            )
        elif azure_ok:
            log_info(
                f"  llm_backend:  Azure OpenAI @ {settings.azure_openai_endpoint}"
            )
        else:
            log_info("  llm_backend:  OpenAI direct")

    yield

    log_info(f"{settings.app_name} shutting down.")


# ── AgentOS ────────────────────────────────────────────────────────────────
# checkpoint="tool-batch" makes HITL flows resumable — if the service
# restarts (or the user comes back 2 hours later), Agno replays state
# from the DB and picks up at the last completed tool boundary. Critical
# for a real DPO workflow where approvals stretch over hours.

agent_os = AgentOS(
    id=settings.app_id,
    name=settings.app_name,
    description=f"{settings.app_name} — DataHub Erasure Agent runtime",
    version=settings.app_version,
    db=_shared_db,
    agents=[vismriti_agent],
    interfaces=_build_interfaces(),
    mcp_server=settings.agentos_mcp_server,
    a2a_interface=settings.agentos_a2a,
    tracing=settings.agentos_tracing,
    telemetry=settings.agentos_telemetry,
    checkpoint="tool-batch",
    lifespan=_lifespan,
)

app = agent_os.get_app()


# ── Entrypoint ────────────────────────────────────────────────────────────
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
