"""The Vismriti LLM agent - Agno Agent instance.

Composes:
    - the model (resolved via `_resolve_model()` below)
    - the system prompt from `prompt.py`
    - the 4 tools from `tools.py` (2 HITL-gated, 2 read-only)
    - the shared DB (injected at runtime by `main.py` via `build_agent(db)`)

Kept as a plain factory function (not a module-level `Agent(...)` call) so
that:
    (a) importing this module has zero side effects (safer for tests + evals)
    (b) `main.py` can inject the same DB instance into both the Agent and
        the AgentOS runtime

# Model resolution

Two paths are supported, both driven from `.env`:

    1. OPENAI-COMPATIBLE ENDPOINT (Azure OpenAI, LiteLLM, vLLM, etc.)
       If `AZURE_OPENAI_ENDPOINT` (or `OPENAI_BASE_URL`) is set, we build
       an `OpenAILike` model pointed at that base URL. Model id is taken
       from `AZURE_OPENAI_DEPLOYMENT` (falls back to the deployment name
       parsed out of ERASURE_AGENT_MODEL if you used `openai:<name>`).

    2. AGNO MODEL STRING
       Otherwise we pass `ERASURE_AGENT_MODEL` (e.g. `openai:gpt-5.6`,
       `anthropic:claude-sonnet-5`) directly to Agno, which handles the
       provider dispatch via its 30+ built-in adapters.
"""

from __future__ import annotations

from typing import Any

from agno.agent import Agent

from ..utils.config import settings
from .prompt import INSTRUCTIONS
from .tools import VISMRITI_TOOLS


def _resolve_model() -> Any:
    """Return either an `OpenAILike` instance (Azure / custom endpoint) or a
    plain Agno model string that Agno will dispatch itself.

    Ordered by specificity:
      1. Azure OpenAI Service (endpoint + deployment)
      2. Generic OpenAI-compatible base URL (OPENAI_BASE_URL env)
      3. Agno model string (`ERASURE_AGENT_MODEL`)
    """

    endpoint = settings.azure_openai_endpoint or ""
    if endpoint:
        from agno.models.openai.like import OpenAILike

        deployment = settings.azure_openai_deployment or _model_id_from_string(settings.model)
        api_key = settings.azure_openai_api_key or settings.openai_api_key or "not-provided"
        # Azure OpenAI's OpenAI-compatible surface lives at /openai/v1
        # (not the root). Trim trailing slash and append if user hasn't.
        base_url = endpoint.rstrip("/")
        if not base_url.endswith("/openai/v1"):
            base_url = f"{base_url}/openai/v1"
        # Newer Azure OpenAI models (o1, gpt-5 family) reject `max_tokens` —
        # they need `max_completion_tokens`. Drop the field so Agno's default
        # doesn't trigger a 400.
        return OpenAILike(
            id=deployment,
            api_key=api_key,
            base_url=base_url,
            request_params={"parallel_tool_calls": False},
            max_tokens=None,
        )

    # Fallback: Agno model string (e.g. "openai:gpt-5.6", "anthropic:claude-sonnet-5")
    return settings.model or "openai:gpt-5.6"


def _model_id_from_string(model_str: str) -> str:
    """Extract the deployment name from an Agno model string.

    "openai:gpt-5.6" -> "gpt-5.6"
    "gpt-5.6"        -> "gpt-5.6"
    """
    return model_str.split(":", 1)[1] if ":" in model_str else model_str


def build_agent(db) -> Agent:
    """Return a fresh Vismriti Agno Agent bound to the given DB backend."""
    return Agent(
        name=settings.app_id,
        model=_resolve_model(),
        description=f"{settings.app_name} - GDPR Article 17 erasure agent for DataHub.",
        instructions=INSTRUCTIONS,
        tools=list(VISMRITI_TOOLS),
        db=db,
        markdown=True,
        add_history_to_context=True,
        num_history_runs=5,
    )
