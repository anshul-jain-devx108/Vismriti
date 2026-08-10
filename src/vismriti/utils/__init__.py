"""Vismriti utilities — generic helpers with no domain dependencies.

Currently:
    - config: env-driven Settings singleton (the source of truth for every knob)

New helpers here should stay decoupled from `core.models` and
`services.*` — anything with domain knowledge belongs in `services/`.
"""

from .config import Settings, settings

__all__ = ["Settings", "settings"]
