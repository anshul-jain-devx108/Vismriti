"""Factory for the Vismriti Agno Agent.

Composes the resolved model, the system prompt, the four tools, and the DB
that `main.py` injects. Importing this module has no side effects; the agent
is only built when `build_agent(db)` is called.
"""

from __future__ import annotations

from typing import Any

from agno.agent import Agent

from ..utils.config import settings
from .prompt import INSTRUCTIONS
from .tools import VISMRITI_TOOLS


def _resolve_model() -> Any:
    """Return the model for the Agent.

    If `AZURE_OPENAI_ENDPOINT` is set, build an `OpenAILike` pointed at that
    endpoint with the deployment from `AZURE_OPENAI_DEPLOYMENT`. Otherwise
    return the `ERASURE_AGENT_MODEL` string and let Agno dispatch it.
    """

    endpoint = settings.azure_openai_endpoint or ""
    if endpoint:
        from agno.models.openai.like import OpenAILike

        deployment = settings.azure_openai_deployment or _model_id_from_string(settings.model)
        api_key = settings.azure_openai_api_key or settings.openai_api_key or "not-provided"
        # Azure OpenAI's OpenAI-compatible surface lives at /openai/v1, not the
        # root. Trim any trailing slash and append it if the user has not.
        base_url = endpoint.rstrip("/")
        if not base_url.endswith("/openai/v1"):
            base_url = f"{base_url}/openai/v1"
        # o1 and gpt-5 family deployments reject `max_tokens` and want
        # `max_completion_tokens`, so drop the field rather than send a 400.
        return OpenAILike(
            id=deployment,
            api_key=api_key,
            base_url=base_url,
            request_params={"parallel_tool_calls": False},
            max_tokens=None,
        )

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
