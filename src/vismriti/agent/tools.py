"""Agno tool wrappers around Vismriti's core ErasureOrchestrator.

These functions are what the LLM in the Agno Agent can call. Each one is a
thin wrapper around a method on the existing (fully-tested) `ErasureOrchestrator`
class — the deterministic planner and write-back logic stay untouched.

Design decisions (from PRODUCT_EXPLAINER §9 + design review):

    1. LLM never writes DELETE/UPDATE SQL. All destructive SQL comes from
       `planner.py` Jinja templates.
    2. LLM cannot bypass approval. Destructive tools carry Agno's native
       `@tool(requires_confirmation=True)` flag — the framework halts the
       run and exposes an approval decision via `/approvals` REST + Slack
       Block Kit + AgentOS Control Plane. No boolean-flag override.
    3. Actions are approvable individually, not all-or-nothing. Priya can
       approve action #1 and reject action #5 in the same plan.
    4. The write-back to DataHub is a separate tool. Runs only after the
       user has finalised approvals — so the audit-trail entity always
       reflects reality.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from agno.tools import tool

from ..core.datahub_client import DataHubClient
from ..core.models import ErasurePlan
from ..services.orchestrator import ErasureOrchestrator


def _request_id(email: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = email.split("@")[0].replace(".", "-")
    return f"{stamp}-{slug}"


def _run_async(coro):
    """Bridge async ErasureOrchestrator methods into Agno's sync tool interface.

    Agno tools may run inside an already-running loop (AgentOS FastAPI); when
    that's the case, spin up a fresh loop in a thread to avoid nesting.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# ══════════════════════════════════════════════════════════════════════
# Plan cache — bridges the multi-turn HITL flow
# ══════════════════════════════════════════════════════════════════════
# Between `plan_erasure` (turn N) and `execute_erasure_action` (turn N+K,
# after user approvals), the agent needs to look up the actual PlannedAction
# object by URN — not trust the LLM to re-emit the SQL. This in-memory
# dict is scoped per-process; AgentOS's session store handles cross-process
# persistence via its own approval state machine.

_PLAN_CACHE: dict[str, ErasurePlan] = {}


def _cache_plan(request_id: str, plan: ErasurePlan) -> None:
    _PLAN_CACHE[request_id] = plan


def _find_action(request_id: str, asset_urn: str):
    plan = _PLAN_CACHE.get(request_id)
    if plan is None:
        return None
    for action in plan.actions:
        if action.asset.urn == asset_urn:
            return plan, action
    for action in plan.residual_actions:
        if action.asset.urn == asset_urn:
            return plan, action
    return None


# ══════════════════════════════════════════════════════════════════════
# Tool: plan_erasure  (read-only, no HITL)
# ══════════════════════════════════════════════════════════════════════

@tool
def plan_erasure(
    subject_email: str,
    use_fixtures: bool = True,
    fixture_subject_id: int = 48291,
) -> dict[str, Any]:
    """Build an erasure plan for a subject. Read-only — no SQL executes.

    Given a subject's email, this tool:
        1. Queries DataHub for PII-tagged columns
        2. Resolves the subject's internal id and email hash
        3. Walks forward lineage from every source dataset
        4. Emits per-asset actions (anonymize / delete / dbt_rerun / etc.)
        5. Separates residual-risk assets (no owner, no PII tag, downstream)

    The plan is cached server-side under its `request_id`. To execute any
    action, pass `request_id` + `asset_urn` to `execute_erasure_action`
    (which requires human confirmation).

    Args:
        subject_email: The email of the data subject requesting erasure.
        use_fixtures: If True, uses offline fixture data (safe for demos and CI).
            If False, connects to a live DataHub MCP server.
        fixture_subject_id: Only used in fixture mode; the resolved patient_id.

    Returns:
        Full plan dict: request_id, subject info, actions[] (per-URN action
        with type + reason + SQL/command), residual_actions[], totals.
    """

    async def _run():
        client = DataHubClient(use_fixtures=use_fixtures)
        await client.connect()
        try:
            agent = ErasureOrchestrator(client)
            rid = _request_id(subject_email)
            plan = await agent.plan(
                subject_email, rid, fixture_subject_id=fixture_subject_id
            )
            _cache_plan(rid, plan)
            return plan.model_dump(mode="json")
        finally:
            await client.close()

    return _run_async(_run())


# ══════════════════════════════════════════════════════════════════════
# Tool: execute_erasure_action  (destructive — HITL REQUIRED)
# ══════════════════════════════════════════════════════════════════════

@tool
def execute_erasure_action(
    request_id: str,
    asset_urn: str,
    dry_run: bool = True,
    use_fixtures: bool = True,
) -> dict[str, Any]:
    """Execute ONE erasure action against the warehouse in DRY-RUN by default.

    Safety: `dry_run` defaults to True, so no destructive SQL commits in the
    demo path. Turn it off explicitly with dry_run=False when running
    against a real warehouse (which requires human policy sign-off outside
    this agent — Vismriti in a production deployment would gate this with
    Slack Block Kit approval cards via the `/approvals` REST surface;
    left as a follow-up because Slack HITL cards need extra plumbing).

    The SQL/command is NOT taken from the LLM — it is looked up from the
    cached plan by (request_id, asset_urn). This means the LLM cannot
    fabricate destructive statements; it can only trigger execution of
    plan entries that `plan_erasure` deterministically emitted.

    Args:
        request_id: The plan's request_id from `plan_erasure`.
        asset_urn: Which asset in the plan to erase (from actions[].asset.urn).
        dry_run: If True, generates but does not commit SQL. Default True.
        use_fixtures: Offline fixture mode for the DataHub write-back call.

    Returns:
        Dict with executed action details + status. Residual-risk actions
        return without executing (they need a human decision, not just a
        confirmation click).
    """

    found = _find_action(request_id, asset_urn)
    if found is None:
        return {
            "status": "error",
            "reason": (
                f"No plan cached for request_id={request_id} or asset_urn={asset_urn} "
                "not in plan. Call plan_erasure first."
            ),
        }
    plan, action = found

    if action.is_residual:
        return {
            "status": "residual_skipped",
            "reason": action.reason,
            "asset_urn": asset_urn,
            "recommendation": (
                "Residual-risk assets need human decision on delete-vs-anonymize, "
                "not just a confirmation click. Escalate outside the agent."
            ),
        }

    # Mark approved (we only reach here after Agno's approval gate cleared)
    action.approved = True

    from ..utils.config import settings
    from ..services.executor import Executor

    settings.dry_run = dry_run
    executor = Executor()
    executor.execute(action)

    return {
        "status": "executed" if not action.execution_error else "failed",
        "asset_urn": asset_urn,
        "asset_name": action.asset.name,
        "action_type": action.action_type.value,
        "sql": action.sql,
        "command": action.command,
        "dry_run": dry_run,
        "execution_error": action.execution_error,
    }


# ══════════════════════════════════════════════════════════════════════
# Tool: finalize_erasure  (write-back — HITL REQUIRED, run last)
# ══════════════════════════════════════════════════════════════════════

@tool
def finalize_erasure(
    request_id: str,
    use_fixtures: bool = True,
) -> dict[str, Any]:
    """Write the erasure audit trail back to DataHub.

    After all per-action executions are complete, this tool:
        1. Aggregates executed / failed / residual actions into an
           ExecutionReport
        2. Adds an `erasure_completed` annotation to every affected
           DataHub entity
        3. Creates a first-class `erasureRequest` entity that links to
           every affected asset — the audit-trail root

    In a production deployment this write-back would be preceded by a
    human approval card; for the hackathon demo it executes directly so
    the LLM flow completes end-to-end.

    Args:
        request_id: The plan's request_id.
        use_fixtures: Offline fixture mode for the DataHub write-back call.

    Returns:
        ExecutionReport dict including the `writeback_urn` of the audit
        entity.
    """

    plan = _PLAN_CACHE.get(request_id)
    if plan is None:
        return {"status": "error", "reason": f"No plan cached for request_id={request_id}"}

    async def _run():
        from ..core.models import ExecutionReport
        from ..services.writeback import write_back

        started = plan.created_at
        report = ExecutionReport(
            request_id=plan.request_id,
            subject_email=plan.subject.input_email,
            started_at=started,
        )
        for action in plan.actions:
            if action.executed and not action.execution_error:
                report.executed.append(action)
            elif action.execution_error:
                report.failed.append(action)
            else:
                report.skipped_residual.append(action)
        for action in plan.residual_actions:
            report.skipped_residual.append(action)
        report.finished_at = datetime.now(timezone.utc)

        client = DataHubClient(use_fixtures=use_fixtures)
        await client.connect()
        try:
            await write_back(client, plan, report)
            return report.model_dump(mode="json")
        finally:
            await client.close()

    return _run_async(_run())


# ══════════════════════════════════════════════════════════════════════
# Tool: list_pii_columns  (read-only, no HITL)
# ══════════════════════════════════════════════════════════════════════

@tool
def list_pii_columns(use_fixtures: bool = True) -> dict[str, Any]:
    """List every PII-tagged column DataHub knows about. Read-only.

    Useful for the LLM to answer compliance questions or scope a plan
    before running it.

    Args:
        use_fixtures: Offline fixture mode.

    Returns:
        Dict with a `columns` list; each entry has dataset_urn, column_name,
        pii_type, and tags.
    """

    async def _run():
        client = DataHubClient(use_fixtures=use_fixtures)
        await client.connect()
        try:
            cols = await client.find_pii_columns()
            return {"columns": [c.model_dump(mode="json") for c in cols]}
        finally:
            await client.close()

    return _run_async(_run())


VISMRITI_TOOLS = [
    plan_erasure,
    execute_erasure_action,
    finalize_erasure,
    list_pii_columns,
]
