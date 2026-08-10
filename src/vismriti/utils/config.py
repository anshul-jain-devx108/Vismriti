"""Runtime configuration for Vismriti.

Every knob is read from the environment (a `.env` file is loaded at import
time via python-dotenv) into a single `settings` object. Keep the field list
in sync with `.env.example`.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

load_dotenv()


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or contradictory."""


def _str(*names: str, default: str = "") -> str:
    """First non-empty value among `names`, else `default`.

    Several names are accepted so a misspelled key in an existing .env still
    resolves instead of silently falling back to the default.
    """
    for name in names:
        raw = os.getenv(name)
        if raw is not None and raw.strip() != "":
            return raw.strip()
    return default


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
    """All runtime configuration in one object."""

    # Branding
    app_id: str = Field(default_factory=lambda: _str("APP_ID", default="vismriti"))
    app_name: str = Field(default_factory=lambda: _str("APP_NAME", default="Vismriti"))
    app_version: str = Field(default_factory=lambda: _str("APP_VERSION", default="0.1.0"))

    # LLM
    model: str = Field(
        default_factory=lambda: _str("ERASURE_AGENT_MODEL", default="openai:gpt-5.6")
    )

    openai_api_key: str = Field(default_factory=lambda: _str("OPENAI_API_KEY"))

    azure_openai_api_key: str = Field(default_factory=lambda: _str("AZURE_OPENAI_API_KEY"))
    azure_openai_endpoint: str = Field(default_factory=lambda: _str("AZURE_OPENAI_ENDPOINT"))
    # "Azure_API_VERSION" is the spelling used by some existing .env files.
    azure_openai_api_version: str = Field(
        default_factory=lambda: _str(
            "AZURE_OPENAI_API_VERSION", "AZURE_API_VERSION", "Azure_API_VERSION",
            default="2024-10-21",
        )
    )
    azure_openai_deployment: str = Field(default_factory=lambda: _str("AZURE_OPENAI_DEPLOYMENT"))

    # DataHub. No default: pointing at someone else's metadata server by
    # accident is worse than refusing to run, so live mode demands an explicit
    # DATAHUB_GMS_URL (see require_datahub_gms_url).
    datahub_gms_url: str = Field(default_factory=lambda: _str("DATAHUB_GMS_URL"))
    datahub_gms_token: str = Field(default_factory=lambda: _str("DATAHUB_GMS_TOKEN"))

    # Client mode: "fixture" | "live-rest" | "mcp-stdio"
    # When unset, DataHubClient auto-picks:
    #   - LIVE_REST if GMS URL is https://...
    #   - MCP_STDIO otherwise
    datahub_client_mode: str = Field(default_factory=lambda: _str("DATAHUB_CLIENT_MODE"))

    # DataHub MCP server
    datahub_mcp_command: str = Field(
        default_factory=lambda: _str("DATAHUB_MCP_COMMAND", default="uvx")
    )
    datahub_mcp_args: str = Field(
        default_factory=lambda: _str("DATAHUB_MCP_ARGS", default="mcp-server-datahub")
    )
    datahub_mcp_url: str = Field(default_factory=lambda: _str("DATAHUB_MCP_URL"))
    datahub_mcp_auth_token: str = Field(default_factory=lambda: _str("DATAHUB_MCP_AUTH_TOKEN"))

    # Warehouse
    pg_host: str = Field(default_factory=lambda: _str("PG_HOST", default="localhost"))
    pg_port: int = Field(default_factory=lambda: _int("PG_PORT", 5432))
    pg_database: str = Field(default_factory=lambda: _str("PG_DATABASE", default="healthcare"))
    pg_user: str = Field(default_factory=lambda: _str("PG_USER", default="datahub"))
    pg_password: str = Field(default_factory=lambda: _str("PG_PASSWORD"))
    warehouse_dsn: str = Field(default_factory=lambda: _str("WAREHOUSE_DSN"))

    # AgentOS runtime
    agentos_host: str = Field(default_factory=lambda: _str("AGENTOS_HOST", default="127.0.0.1"))
    agentos_port: int = Field(default_factory=lambda: _int("AGENTOS_PORT", 8000))

    agentos_mcp_server: bool = Field(default_factory=lambda: _bool("AGENTOS_MCP_SERVER", True))
    agentos_tracing: bool = Field(default_factory=lambda: _bool("AGENTOS_TRACING", True))
    agentos_telemetry: bool = Field(default_factory=lambda: _bool("AGENTOS_TELEMETRY", False))
    agentos_a2a: bool = Field(default_factory=lambda: _bool("AGENTOS_A2A", False))

    agentos_db_url: str = Field(
        default_factory=lambda: _str("AGENTOS_DB_URL", default="sqlite:///./runs/vismriti.db")
    )

    # Agent behaviour
    dry_run: bool = Field(default_factory=lambda: _bool("ERASURE_AGENT_DRY_RUN", True))
    output_dir: Path = Field(
        default_factory=lambda: Path(_str("ERASURE_AGENT_OUTPUT_DIR", default="./runs"))
    )
    max_lineage_depth: int = Field(
        default_factory=lambda: _int("ERASURE_AGENT_MAX_LINEAGE_DEPTH", 5)
    )

    # Slack interface (opt-in)
    slack_enabled: bool = Field(default_factory=lambda: _bool("SLACK_ENABLED", False))
    slack_bot_token: str = Field(default_factory=lambda: _str("SLACK_BOT_TOKEN"))
    slack_signing_secret: str = Field(default_factory=lambda: _str("SLACK_SIGNING_SECRET"))
    slack_reply_to_mentions_only: bool = Field(
        default_factory=lambda: _bool("SLACK_REPLY_TO_MENTIONS_ONLY", True)
    )

    # Observability
    appinsights_connection_string: str = Field(
        default_factory=lambda: _str("APPLICATIONINSIGHTS_CONNECTION_STRING")
    )
    otel_endpoint: str = Field(default_factory=lambda: _str("OTEL_EXPORTER_OTLP_ENDPOINT"))
    log_level: str = Field(default_factory=lambda: _str("LOG_LEVEL", default="INFO"))

    # Fixture mode
    use_fixtures: bool = Field(default_factory=lambda: _bool("VISMRITI_USE_FIXTURES", True))
    fixture_subject_id: int = Field(
        default_factory=lambda: _int("VISMRITI_FIXTURE_SUBJECT_ID", 48291)
    )

    def pg_dsn(self) -> str:
        """libpq-style DSN for psycopg2. Empty values are omitted so libpq can
        fall back to PGPASSWORD or ~/.pgpass instead of sending an empty one."""
        parts = [
            ("host", self.pg_host),
            ("port", str(self.pg_port)),
            ("dbname", self.pg_database),
            ("user", self.pg_user),
            ("password", self.pg_password),
        ]
        return " ".join(f"{key}={value}" for key, value in parts if value)

    def require_datahub_gms_url(self, context: str = "live DataHub mode") -> str:
        """Return the configured GMS URL, or raise if it is missing.

        Call this on every path that talks to a real DataHub. Refusing to start
        beats guessing a URL, which would aim a deletion workflow at whatever
        metadata server happened to be baked into the source.
        """
        url = self.datahub_gms_url.strip()
        if not url:
            raise ConfigError(
                f"{context} requires DATAHUB_GMS_URL, which is not set. "
                "Set it in .env (for example DATAHUB_GMS_URL=https://datahub-gms.example.com) "
                "or run in fixture mode instead (VISMRITI_USE_FIXTURES=true, or --fixtures "
                "on the CLI)."
            )
        return url

    # AgentOS toggles dry_run at request time, so this stays mutable.
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=False)


settings = Settings()
