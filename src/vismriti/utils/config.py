"""Runtime configuration — single source of truth for every knob.

Values are loaded from environment variables (`.env` file is read at import
time via python-dotenv). Every field has a sensible default so a bare
`from vismriti.utils.config import settings` never fails — or the shorter
`from vismriti.utils import settings` via the package re-export.

Organisation mirrors `.env.example` — keep the two in sync when adding a
new setting.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Settings(BaseModel):
    """All runtime configuration in one immutable object."""

    # ── 1. Branding ────────────────────────────────────────────────────
    app_id: str = Field(default_factory=lambda: os.getenv("APP_ID", "vismriti"))
    app_name: str = Field(default_factory=lambda: os.getenv("APP_NAME", "Vismriti"))
    app_version: str = Field(default_factory=lambda: os.getenv("APP_VERSION", "0.1.0"))

    # ── 2. LLM ─────────────────────────────────────────────────────────
    model: str = Field(default_factory=lambda: os.getenv("ERASURE_AGENT_MODEL", "openai:gpt-5.6"))

    openai_api_key: str = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))

    azure_openai_api_key: str = Field(default_factory=lambda: os.getenv("AZURE_OPENAI_API_KEY", ""))
    azure_openai_endpoint: str = Field(default_factory=lambda: os.getenv("AZURE_OPENAI_ENDPOINT", ""))
    azure_openai_api_version: str = Field(default_factory=lambda: os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"))
    azure_openai_deployment: str = Field(default_factory=lambda: os.getenv("AZURE_OPENAI_DEPLOYMENT", ""))

    # ── 3. DataHub ────────────────────────────────────────────────────
    datahub_gms_url: str = Field(default_factory=lambda: os.getenv(
        "DATAHUB_GMS_URL",
        "https://datahub-gms.happyhill-72aa3202.centralindia.azurecontainerapps.io",
    ))
    datahub_gms_token: str = Field(default_factory=lambda: os.getenv("DATAHUB_GMS_TOKEN", ""))

    # Client mode: "fixture" | "live-rest" | "mcp-stdio"
    # When unset, DataHubClient auto-picks:
    #   - LIVE_REST if GMS URL is https://...
    #   - MCP_STDIO otherwise
    datahub_client_mode: str = Field(default_factory=lambda: os.getenv("DATAHUB_CLIENT_MODE", ""))

    # ── 4. DataHub MCP Server ─────────────────────────────────────────
    datahub_mcp_command: str = Field(default_factory=lambda: os.getenv("DATAHUB_MCP_COMMAND", "uvx"))
    datahub_mcp_args: str = Field(default_factory=lambda: os.getenv("DATAHUB_MCP_ARGS", "mcp-server-datahub"))
    datahub_mcp_url: str = Field(default_factory=lambda: os.getenv("DATAHUB_MCP_URL", ""))
    datahub_mcp_auth_token: str = Field(default_factory=lambda: os.getenv("DATAHUB_MCP_AUTH_TOKEN", ""))

    # ── 5. Warehouse ──────────────────────────────────────────────────
    pg_host: str = Field(default_factory=lambda: os.getenv("PG_HOST", "localhost"))
    pg_port: int = Field(default_factory=lambda: _int("PG_PORT", 5432))
    pg_database: str = Field(default_factory=lambda: os.getenv("PG_DATABASE", "healthcare"))
    pg_user: str = Field(default_factory=lambda: os.getenv("PG_USER", "datahub"))
    pg_password: str = Field(default_factory=lambda: os.getenv("PG_PASSWORD", "datahub"))
    warehouse_dsn: str = Field(default_factory=lambda: os.getenv("WAREHOUSE_DSN", ""))

    # ── 6. AgentOS runtime ────────────────────────────────────────────
    agentos_host: str = Field(default_factory=lambda: os.getenv("AGENTOS_HOST", "127.0.0.1"))
    agentos_port: int = Field(default_factory=lambda: _int("AGENTOS_PORT", 8000))

    agentos_mcp_server: bool = Field(default_factory=lambda: _bool("AGENTOS_MCP_SERVER", True))
    agentos_tracing: bool = Field(default_factory=lambda: _bool("AGENTOS_TRACING", True))
    agentos_telemetry: bool = Field(default_factory=lambda: _bool("AGENTOS_TELEMETRY", False))
    agentos_a2a: bool = Field(default_factory=lambda: _bool("AGENTOS_A2A", False))

    agentos_db_url: str = Field(default_factory=lambda: os.getenv("AGENTOS_DB_URL", "sqlite:///./runs/vismriti.db"))

    # ── 7. Vismriti agent behaviour ───────────────────────────────────
    dry_run: bool = Field(default_factory=lambda: _bool("ERASURE_AGENT_DRY_RUN", True))
    output_dir: Path = Field(default_factory=lambda: Path(os.getenv("ERASURE_AGENT_OUTPUT_DIR", "./runs")))
    max_lineage_depth: int = Field(default_factory=lambda: _int("ERASURE_AGENT_MAX_LINEAGE_DEPTH", 5))

    # ── 8. Slack interface (opt-in) ───────────────────────────────────
    slack_enabled: bool = Field(default_factory=lambda: _bool("SLACK_ENABLED", False))
    slack_bot_token: str = Field(default_factory=lambda: os.getenv("SLACK_BOT_TOKEN", ""))
    slack_signing_secret: str = Field(default_factory=lambda: os.getenv("SLACK_SIGNING_SECRET", ""))
    slack_reply_to_mentions_only: bool = Field(default_factory=lambda: _bool("SLACK_REPLY_TO_MENTIONS_ONLY", True))

    # ── 9. Observability ──────────────────────────────────────────────
    appinsights_connection_string: str = Field(default_factory=lambda: os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", ""))
    otel_endpoint: str = Field(default_factory=lambda: os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""))
    log_level: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    # ── 10. Fixture / demo mode ───────────────────────────────────────
    use_fixtures: bool = Field(default_factory=lambda: _bool("VISMRITI_USE_FIXTURES", True))
    fixture_subject_id: int = Field(default_factory=lambda: _int("VISMRITI_FIXTURE_SUBJECT_ID", 48291))

    # ── Helpers ───────────────────────────────────────────────────────
    def pg_dsn(self) -> str:
        """Legacy libpq-style DSN for psycopg2 (still used by executor)."""
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={self.pg_database} "
            f"user={self.pg_user} password={self.pg_password}"
        )

    # AgentOS occasionally toggles dry_run at request time, so keep mutable.
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=False)


settings = Settings()
