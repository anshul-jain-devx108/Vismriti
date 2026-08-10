"""Agno tool wrappers around the ErasureOrchestrator.

Four tools are exposed to the LLM: two read-only (plan_erasure,
list_pii_columns) and two destructive (execute_erasure_action,
finalize_erasure) that carry requires_confirmation=True so Agno halts the run
until a human confirms. The LLM never supplies SQL; destructive statements are
looked up from the stored plan by (request_id, asset_urn).
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agno.tools import tool

from ..core.datahub_client import DataHubClient
from ..core.models import ErasurePlan
from ..services.orchestrator import ErasureOrchestrator
from ..utils.config import settings


def _request_id(email: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = email.split("@")[0].replace(".", "-")
    return f"{stamp}-{slug}"


def _run_async(coro):
    """Bridge async orchestrator methods into Agno's sync tool interface.

    Agno tools may run inside an already-running loop (AgentOS FastAPI); when
    that is the case, run the coroutine on a fresh loop in a worker thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# Plan store. Approvals in a DPO workflow span hours and AgentOS may run
# several uvicorn workers, so plans are persisted as JSON under
# <output_dir>/plans/<request_id>.json. The dict below is only a read cache,
# keyed by file mtime so a plan written by another worker is picked up.

_PLAN_CACHE: dict[str, tuple[float, ErasurePlan]] = {}

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def _plan_path(request_id: str) -> Path | None:
    """Path for a request id, or None if the id is not a safe file name."""
    if not request_id or not _SAFE_REQUEST_ID.match(request_id):
        return None
    return Path(settings.output_dir) / "plans" / f"{request_id}.json"


def _save_plan(plan: ErasurePlan) -> None:
    """Persist a plan atomically and refresh the read cache."""
    path = _plan_path(plan.request_id)
    if path is None:
        raise ValueError(f"Unsafe request_id for a plan file: {plan.request_id!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)
    _PLAN_CACHE[plan.request_id] = (path.stat().st_mtime, plan)


def _load_plan(request_id: str) -> ErasurePlan | None:
    """Return the stored plan, or None if it is missing or unreadable."""
    path = _plan_path(request_id)
    if path is None:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _PLAN_CACHE.pop(request_id, None)
        return None

    cached = _PLAN_CACHE.get(request_id)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        plan = ErasurePlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    _PLAN_CACHE[request_id] = (mtime, plan)
    return plan


def _find_action(request_id: str, asset_urn: str):
    """Return (plan, action) for an asset in a stored plan, or None."""
    plan = _load_plan(request_id)
    if plan is None:
        return None
    for action in plan.actions:
        if action.asset.urn == asset_urn:
            return plan, action
    for action in plan.residual_actions:
        if action.asset.urn == asset_urn:
            return plan, action
    return None


@tool
def plan_erasure(
    subject_email: str,
    use_fixtures: bool = True,
    fixture_subject_id: int = 48291,
) -> dict[str, Any]:
    """Build an erasure plan for a subject. Read-only, no SQL executes.

    Given a subject's email, this tool:
        1. Finds the PII-tagged columns DataHub reports
        2. Resolves the subject's internal id and email hash
        3. Walks forward lineage from every source dataset
        4. Emits per-asset actions (anonymize / delete / dbt_rerun / etc.)
        5. Separates residual-risk assets (no owner, no PII tag, downstream)

    The plan is stored on disk under its `request_id`. To execute any action,
    pass `request_id` + `asset_urn` to `execute_erasure_action` (which requires
    human confirmation).

    Args:
        subject_email: The email of the data subject requesting erasure.
        use_fixtures: If True, uses offline fixture data. If False, queries the
            configured live DataHub deployment.
        fixture_subject_id: Only used in fixture mode; the resolved patient_id.

    Returns:
        Full plan dict: request_id, subject info, actions[] (per-URN action
        with type + reason + SQL/command), residual_actions[], totals.
    """

    async def _run():
        client = DataHubClient(use_fixtures=use_fixtures)
        await client.connect()
        try:
            orchestrator = ErasureOrchestrator(client)
            rid = _request_id(subject_email)
            plan = await orchestrator.plan(
                subject_email, rid, fixture_subject_id=fixture_subject_id
            )
            _save_plan(plan)
            return plan.model_dump(mode="json")
        finally:
            await client.close()

    return _run_async(_run())


@tool(requires_confirmation=True)
def execute_erasure_action(
    request_id: str,
    asset_urn: str,
    dry_run: bool = True,
    use_fixtures: bool = True,
) -> dict[str, Any]:
    """Execute ONE erasure action against the warehouse, dry-run by default.

    Agno halts the run before this tool executes and waits for a human to
    confirm the call and its arguments, including `dry_run`.

    The SQL/command is not taken from the LLM; it is looked up from the stored
    plan by (request_id, asset_urn). The LLM can only trigger execution of
    entries that `plan_erasure` deterministically emitted.

    Args:
        request_id: The plan's request_id from `plan_erasure`.
        asset_urn: Which asset in the plan to erase (from actions[].asset.urn).
        dry_run: If True, generates but does not commit SQL. Default True.
        use_fixtures: Offline fixture mode for the DataHub calls.

    Returns:
        Dict with the executed action details + status. Residual-risk actions
        return without executing; they need a human decision on
        delete-vs-anonymize, not a confirmation click.
    """

    found = _find_action(request_id, asset_urn)
    if found is None:
        return {
            "status": "error",
            "reason": (
                f"No stored plan for request_id={request_id}, or asset_urn={asset_urn} "
                "is not in that plan. Call plan_erasure first."
            ),
        }
    plan, action = found

    if action.is_residual:
        return {
            "status": "residual_skipped",
            "reason": action.reason,
            "asset_urn": asset_urn,
            "recommendation": (
                "Residual-risk assets need a human decision on delete-vs-anonymize. "
                "Escalate outside the agent."
            ),
        }

    from ..services.executor import Executor

    # Reached only after Agno's confirmation gate cleared.
    action.approved = True

    Executor(dry_run=dry_run).execute(action)

    if action.execution_error:
        status = "failed"
    elif action.executed:
        # An advisory action is only recorded here; the owning team performs it.
        status = "recorded_advisory" if action.advisory else "executed"
    else:
        status = "not_executed"

    # Persist the mutated action so finalize_erasure aggregates real state.
    _save_plan(plan)

    return {
        "status": status,
        "asset_urn": asset_urn,
        "asset_name": action.asset.name,
        "action_type": action.action_type.value,
        "sql": action.sql,
        "command": action.command,
        "dry_run": dry_run,
        "execution_error": action.execution_error,
    }


@tool(requires_confirmation=True)
def finalize_erasure(
    request_id: str,
    use_fixtures: bool = True,
) -> dict[str, Any]:
    """Write the erasure audit trail back to DataHub.

    Agno halts the run before this tool executes and waits for human
    confirmation. It then aggregates executed / failed / residual actions from
    the stored plan into an ExecutionReport and attempts the DataHub write-back
    (per-asset annotations plus one erasureRequest audit entity).

    Args:
        request_id: The plan's request_id.
        use_fixtures: Offline fixture mode for the DataHub write-back call.

    Returns:
        The ExecutionReport fields plus a `status`. status="finalized" only if
        DataHub accepted the write-back; otherwise status="writeback_failed"
        with the reason, and `writeback_urn` stays null.
    """

    plan = _load_plan(request_id)
    if plan is None:
        return {"status": "error", "reason": f"No stored plan for request_id={request_id}"}

    async def _run():
        from ..core.models import ExecutionReport
        from ..services.writeback import write_back

        report = ExecutionReport(
            request_id=plan.request_id,
            subject_email=plan.subject.input_email,
            started_at=plan.created_at,
            fixture_mode=use_fixtures,
            dry_run=any(a.dry_run for a in plan.actions if a.executed),
        )
        for action in plan.actions:
            if action.execution_error:
                report.failed.append(action)
            elif action.executed or action.advisory:
                # Advisory actions are unexecuted by design. They belong in the
                # executed bucket so write_back annotates them as advisory
                # rather than as residual risk.
                report.executed.append(action)
            else:
                report.skipped_residual.append(action)
        for action in plan.residual_actions:
            report.skipped_residual.append(action)
        report.finished_at = datetime.now(timezone.utc)

        client = DataHubClient(use_fixtures=use_fixtures)
        await client.connect()
        try:
            await write_back(client, plan, report)
        except Exception as exc:  # noqa: BLE001 - any client failure is reported, not raised
            report.writeback_ok = False
            report.writeback_error = f"{type(exc).__name__}: {exc}"
        finally:
            await client.close()

        payload = report.model_dump(mode="json")
        if report.writeback_ok:
            return {"status": "finalized", **payload}
        return {
            "status": "writeback_failed",
            "reason": report.writeback_error or "DataHub did not accept the write-back",
            **payload,
        }

    return _run_async(_run())


@tool
def list_pii_columns(use_fixtures: bool = True) -> dict[str, Any]:
    """List every PII-tagged column DataHub reports. Read-only.

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
