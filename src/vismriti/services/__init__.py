"""Vismriti services: the erasure workflow.

Pipeline order:

    resolve_subject -> find_pii_columns -> collect_downstream ->
    build_plan -> Executor.execute -> write_back -> render_markdown

`ErasureOrchestrator` in `orchestrator.py` sequences all of it behind
`plan()`, `execute_plan()` and `run()`.

Bundled resources:
    - sql_templates/ : Jinja2 SQL templates used by the planner
    - fixtures/      : offline data consumed by DataHubClient in fixture mode
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
    "collect_downstream",
    "plan_action",
    "render_markdown",
    "resolve_subject",
    "write_back",
    "write_reports",
]
