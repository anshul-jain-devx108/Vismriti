"""Vismriti orchestrator — the deterministic core of the erasure workflow.

Sequences the phases:

    resolve -> discover -> traverse -> plan -> (approve) -> execute -> write-back -> report

Distinct from ``agno.agent.Agent``: this class contains ZERO LLM calls and is
the substrate the Agno-wrapping layer (`vismriti.agent.tools` +
`vismriti.main`) calls into. Direct users (CLI, Streamlit, DataHub Skill,
pytest) instantiate `ErasureOrchestrator` without any Agno / LLM dependency —
that's the whole point of the separation.

Deterministic logic (traversal, action selection, SQL rendering) stays
in code because destructive operations should be auditable line-by-line,
not "the model decided."
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..core.datahub_client import DataHubClient
from .executor import Executor
from .lineage import collect_downstream
from ..core.models import ErasurePlan, ExecutionReport
from .planner import build_plan
from .subject_resolver import resolve_subject
from .writeback import write_back


class ErasureOrchestrator:
    """Stateless orchestrator - all state is on the plan/report.

    Not an "agent" in the LLM sense — no reasoning, no tool-choice. It is
    the linear pipeline that the Agno layer (or CLI / Streamlit) calls into.
    """

    def __init__(
        self,
        client: DataHubClient,
        executor: Executor | None = None,
    ) -> None:
        self.client = client
        self.executor = executor or Executor()

    async def plan(
        self,
        email: str,
        request_id: str,
        fixture_subject_id: int | None = None,
    ) -> ErasurePlan:
        pii_columns = await self.client.find_pii_columns()
        subject = resolve_subject(email, pii_columns, fixture_id=fixture_subject_id)

        source_urns = sorted({c.dataset_urn for c in pii_columns})
        downstream = await collect_downstream(self.client, source_urns)

        return build_plan(request_id, subject, downstream)

    async def execute_plan(
        self,
        plan: ErasurePlan,
        auto_approve: bool = False,
    ) -> ExecutionReport:
        if auto_approve:
            for action in plan.actions:
                action.approved = True

        report = ExecutionReport(
            request_id=plan.request_id,
            subject_email=plan.subject.input_email,
            started_at=datetime.now(timezone.utc),
        )

        for action in plan.actions:
            if not action.approved:
                report.skipped_residual.append(action)
                continue
            self.executor.execute(action)
            if action.execution_error:
                report.failed.append(action)
            else:
                report.executed.append(action)

        for action in plan.residual_actions:
            report.skipped_residual.append(action)

        report.finished_at = datetime.now(timezone.utc)

        await write_back(self.client, plan, report)
        return report

    async def run(
        self,
        email: str,
        request_id: str,
        auto_approve: bool = False,
        fixture_subject_id: int | None = None,
    ) -> tuple[ErasurePlan, ExecutionReport]:
        plan = await self.plan(email, request_id, fixture_subject_id=fixture_subject_id)
        report = await self.execute_plan(plan, auto_approve=auto_approve)
        return plan, report
