"""Vismriti Agno agent package.

    - `build_agent(db)`: factory returning a configured Agno Agent
    - `VISMRITI_TOOLS`: the tool functions exposed to the model
    - `INSTRUCTIONS`: the system prompt string

This is the LLM-driven surface. The deterministic core is
`vismriti.services.orchestrator.ErasureOrchestrator`, which these tools wrap.
"""

from .agent import build_agent
from .prompt import INSTRUCTIONS
from .tools import VISMRITI_TOOLS

__all__ = ["INSTRUCTIONS", "VISMRITI_TOOLS", "build_agent"]
