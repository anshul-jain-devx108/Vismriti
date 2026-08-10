"""Vismriti services — the business logic that makes the erasure workflow work.

Composition (used by both `vismriti.agent` tools and direct callers):

    resolve_subject  →  find_pii_columns  →  collect_downstream  →
    build_plan       →  Executor.execute  →  write_back           →
    render_markdown / write_reports

The top-level `ErasureOrchestrator` in `orchestrator.py` sequences all of these
into `plan()` / `execute_plan()` / `run()` for callers that want the whole
pipeline in one call.

Bundled resources:
    - sql_templates/ : Jinja2 SQL templates for the deterministic planner
    - fixtures/      : offline demo data consumed by DataHubClient
"""

from .executor import Executor
from .lineage import collect_downstream
from .orchestrator import ErasureOrchestrator
from .planner import build_plan, plan_action
from .report import render_markdown, write_reports
from .subject_resolver import resolve_subject
from .writeback import write_back

__all__ = [
    "ErasureOrchestrator",
    "Executor",
    "build_plan",
    "plan_action",
    "collect_downstream",
    "resolve_subject",
    "write_back",
    "render_markdown",
    "write_reports",
]
