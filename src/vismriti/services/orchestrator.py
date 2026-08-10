"""Deterministic core of the erasure workflow.

Sequences resolve -> discover -> traverse -> plan -> (approve) -> execute ->
write-back. Contains no LLM calls: traversal, action selection and SQL
rendering stay in code so destructive operations are auditable line by line.
The Agno layer, CLI and Streamlit UI all call into this.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..core.datahub_client import DataHubClient
from ..core.models import ErasurePlan, ExecutionReport
from .executor import Executor
from .lineage import collect_downstream
from .planner import build_plan
from .subject_resolver import resolve_subject
from .writeback import write_back


class ErasureOrchestrator:
    """Stateless orchestrator - all state lives on the plan and the report."""

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

        # Stamp how this run was carried out before anything executes, so the
        # report cannot describe simulated work as real.
        report = ExecutionReport(
            request_id=plan.request_id,
            subject_email=plan.subject.input_email,
            started_at=datetime.now(timezone.utc),
            dry_run=self.executor.dry_run,
            fixture_mode=self.client.use_fixtures,
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
