"""Vismriti Agno agent package.

Public API:
    - `build_agent(db)` — factory that returns a configured Agno Agent
    - `VISMRITI_TOOLS` — the 4 tool functions (for eval / registration)
    - `INSTRUCTIONS` — the raw system prompt string

Note: this is the *LLM-driven* agent. The deterministic core is in
`vismriti.services.orchestrator.ErasureOrchestrator` — this package's tools
wrap it and add the LLM + HITL layer on top.
"""

from .agent import build_agent
from .prompt import INSTRUCTIONS
from .tools import VISMRITI_TOOLS

__all__ = ["build_agent", "INSTRUCTIONS", "VISMRITI_TOOLS"]
