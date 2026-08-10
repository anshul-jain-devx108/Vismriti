"""Write erasure results back to DataHub.

Two kinds of write happen here: one annotation per affected asset, and one
erasureRequest entity that links them together as the audit-trail root.
Either can be rejected by the target deployment, so every outcome is
recorded on the ExecutionReport instead of being discarded.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..core.datahub_client import DataHubClient
from ..core.models import ErasurePlan, ExecutionReport

_UNKNOWN_ERROR = "DataHub rejected the write (no reason given)"


def _outcome(result: Any) -> tuple[bool, str | None]:
    """Read (accepted, error) from an annotate_entity result.

    The client returns a payload dict; a bare bool is the older shape. Anything
    that does not positively report acceptance counts as a failure.
    """
    if isinstance(result, bool):
        return result, None if result else _UNKNOWN_ERROR
    if not isinstance(result, dict):
        return False, f"unusable write result: {type(result).__name__}"

    error = result.get("error") or result.get("reason") or result.get("message")
    if "success" in result:
        ok = bool(result["success"])
    else:
        # No explicit flag: accept only a non-empty, error-free payload.
        ok = bool(result) and error is None
    return ok, None if ok else (str(error) if error else _UNKNOWN_ERROR)


def _create_outcome(result: Any) -> tuple[bool, str | None, str | None]:
    """Read (accepted, urn, error) from a create_erasure_request result.

    The URN is returned only when DataHub accepted the entity, so a rejected
    write can never surface a URN that does not exist.
    """
    if isinstance(result, str):
        return bool(result), result or None, None if result else _UNKNOWN_ERROR

    ok, error = _outcome(result)
    urn = result.get("urn") if isinstance(result, dict) else None
    if ok and not urn:
        return False, None, "DataHub returned no erasureRequest URN"
    return ok, str(urn) if ok else None, error


async def _annotate(
    client: DataHubClient,
    report: ExecutionReport,
    urn: str,
    key: str,
    value: str,
) -> bool:
    """Annotate one entity and record the outcome on the report."""
    try:
        ok, error = _outcome(await client.annotate_entity(urn, key, value))
    except Exception as exc:  # noqa: BLE001 - a transport failure is an outcome, not a crash
        ok, error = False, f"{type(exc).__name__}: {exc}"

    if ok:
        report.annotations_succeeded.append(urn)
    else:
        report.annotations_failed.append(urn)
        report.annotation_errors[urn] = error or _UNKNOWN_ERROR
    return ok


async def write_back(
    client: DataHubClient,
    plan: ErasurePlan,
    report: ExecutionReport,
) -> str | None:
    """Annotate every affected asset and create the audit-trail entity.

    Records per-URN annotation outcomes, the erasureRequest outcome, and the
    reason for any rejection on the report. Returns the erasureRequest URN, or
    None when DataHub did not accept it.
    """
    now = datetime.now(timezone.utc).isoformat()

    report.annotations_succeeded.clear()
    report.annotations_failed.clear()
    report.annotation_errors.clear()

    affected_urns: list[str] = []
    for action in report.performed_actions():
        annotation = (
            f"erasure_request={plan.request_id};"
            f"action={action.action_type.value};"
            f"completed_at={now};"
            f"executed=true"
        )
        await _annotate(client, report, action.asset.urn, "erasure_completed", annotation)
        affected_urns.append(action.asset.urn)

    for action in report.advisory_actions():
        annotation = (
            f"erasure_request={plan.request_id};"
            f"action={action.action_type.value};"
            f"flagged_at={now};"
            f"executed=false;"
            f"performed_by=asset_owner"
        )
        await _annotate(client, report, action.asset.urn, "erasure_advisory", annotation)
        affected_urns.append(action.asset.urn)

    for action in report.failed_actions():
        annotation = (
            f"erasure_request={plan.request_id};"
            f"action={action.action_type.value};"
            f"failed_at={now};"
            f"error={action.execution_error or 'unknown'}"
        )
        await _annotate(client, report, action.asset.urn, "erasure_failed", annotation)

    for action in report.skipped_residual:
        if action.is_residual:
            key = "erasure_residual"
            annotation = (
                f"erasure_request={plan.request_id};"
                f"residual=true;"
                f"reason={action.reason}"
            )
        else:
            # Planned, approved by nobody, never run. Recording it as residual
            # risk would describe a judgement call that was never made.
            key = "erasure_not_performed"
            annotation = (
                f"erasure_request={plan.request_id};"
                f"action={action.action_type.value};"
                f"executed=false;"
                f"reason=planned but not executed"
            )
        await _annotate(client, report, action.asset.urn, key, annotation)

    try:
        raw = await client.create_erasure_request(
            request_id=plan.request_id,
            subject_email_hash=plan.subject.email_hash or "",
            affected_urns=affected_urns,
        )
        entity_ok, urn, entity_error = _create_outcome(raw)
    except Exception as exc:  # noqa: BLE001 - a transport failure is an outcome, not a crash
        entity_ok, urn, entity_error = False, None, f"{type(exc).__name__}: {exc}"

    report.writeback_urn = urn
    attempted = len(report.annotations_succeeded) + len(report.annotations_failed)

    if not entity_ok:
        report.writeback_ok = False
        report.writeback_error = entity_error or _UNKNOWN_ERROR
    elif report.annotations_failed:
        first = report.annotations_failed[0]
        report.writeback_ok = False
        report.writeback_error = (
            f"audit-trail entity created, but {len(report.annotations_failed)} of "
            f"{attempted} annotations were rejected "
            f"(first: {first}: {report.annotation_errors.get(first, _UNKNOWN_ERROR)})"
        )
    else:
        report.writeback_ok = True
        report.writeback_error = None

    return report.writeback_urn
