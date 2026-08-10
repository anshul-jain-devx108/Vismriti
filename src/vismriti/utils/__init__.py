"""Vismriti utilities: generic helpers with no domain dependencies.

    - config: env-driven Settings singleton
"""

from .config import ConfigError, Settings, settings

__all__ = ["ConfigError", "Settings", "settings"]
