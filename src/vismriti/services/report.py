"""Human-readable and machine-readable erasure reports.

Both artifacts stand on their own: if the DataHub write-back failed, they say
so and say why, so the file itself remains the audit record.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.models import ErasurePlan, ExecutionReport, PlannedAction


def _mode_label(report: ExecutionReport) -> str:
    """Short description of how much of this run was real."""
    if report.fixture_mode and report.dry_run:
        return "SIMULATION - fixture metadata, no SQL sent"
    if report.fixture_mode:
        return "SIMULATION - fixture metadata"
    if report.dry_run:
        return "DRY RUN - no SQL sent"
    return "LIVE"


def _mode_lines(report: ExecutionReport) -> list[str]:
    """Banner stating plainly when a run changed nothing."""
    if not report.is_simulated():
        return []

    reasons = []
    if report.fixture_mode:
        reasons.append(
            "metadata and the DataHub write-back were served from canned "
            "fixture files, so nothing was read from or written to a real "
            "DataHub deployment"
        )
    if report.dry_run:
        reasons.append(
            "the SQL below was rendered but never sent to the warehouse, so "
            "no row was changed or deleted"
        )
    return [
        f"> **{_mode_label(report)}. No personal data was erased.**",
        ">",
        f"> This run is not evidence of an erasure: {'; and '.join(reasons)}.",
        "> Every count and status in this file describes the simulation.",
        "",
    ]


def _writeback_status(report: ExecutionReport) -> str:
    if report.fixture_mode:
        # The fixture backend returns canned acceptances; reporting that as a
        # successful write would claim a record DataHub does not hold.
        return "SIMULATED - fixture backend, nothing reached DataHub"
    if report.writeback_ok:
        return "succeeded"
    if report.annotations_succeeded or report.writeback_urn:
        return "PARTIAL - some writes were rejected"
    return "FAILED - nothing was recorded in DataHub"


def _writeback_lines(report: ExecutionReport) -> list[str]:
    """Markdown section stating exactly what reached DataHub."""
    lines = ["## DataHub write-back", ""]
    attempted = len(report.annotations_succeeded) + len(report.annotations_failed)
    status = _writeback_status(report)

    real_success = report.writeback_ok and not report.fixture_mode
    lines.append(f"Status: {status}" if real_success else f"Status: **{status}**")
    lines.append("")
    if report.fixture_mode:
        lines.append(
            f"- Audit-trail entity: `{report.writeback_urn}` (canned fixture "
            "value, not an entity in any DataHub deployment)"
        )
        lines.append(
            f"- Annotations accepted by the fixture backend: "
            f"{len(report.annotations_succeeded)}/{attempted}"
        )
        lines.append("")
        return lines

    if report.writeback_urn:
        lines.append(f"- Audit-trail entity: `{report.writeback_urn}`")
    else:
        lines.append("- Audit-trail entity: not created")
    lines.append(f"- Annotations accepted: {len(report.annotations_succeeded)}/{attempted}")
    if not report.writeback_ok:
        lines.append(f"- Reason: {report.writeback_error or 'unknown'}")
        if report.dry_run:
            lines.append(
                "- Nothing was erased in this run either, so there is no gap "
                "between the warehouse and DataHub to reconcile."
            )
        else:
            lines.append(
                "- The warehouse actions listed below still happened; DataHub does "
                "not fully reflect them. Keep this file as the audit record."
            )

    if report.annotations_failed:
        lines.append("")
        lines.append("Rejected annotations:")
        lines.append("")
        for urn in report.annotations_failed:
            lines.append(f"- `{urn}` - {report.annotation_errors.get(urn, 'unknown')}")
    lines.append("")
    return lines


def render_markdown(plan: ErasurePlan, report: ExecutionReport) -> str:
    performed: list[PlannedAction] = report.performed_actions()
    advisory: list[PlannedAction] = report.advisory_actions()
    failed: list[PlannedAction] = report.failed_actions()
    finished = report.finished_at.isoformat() if report.finished_at else "(in progress)"

    simulated = report.is_simulated()
    performed_label = (
        "Erasures simulated (nothing changed)" if simulated else "Erasures executed"
    )

    lines: list[str] = []
    lines.append(f"# Erasure Report - {plan.request_id}")
    lines.append("")
    lines.extend(_mode_lines(report))
    lines.append(f"- Run mode: {_mode_label(report)}")
    lines.append(f"- Subject email: `{report.subject_email}`")
    lines.append(f"- Subject hash: `{plan.subject.email_hash}`")
    lines.append(f"- Started:  {report.started_at.isoformat()}")
    lines.append(f"- Finished: {finished}")
    lines.append(f"- Duration: {report.duration_seconds():.1f}s")
    lines.append(f"- Assets planned: {plan.total_assets()}")
    lines.append(f"- {performed_label}: {len(performed)}")
    lines.append(f"- Advisory (not performed, owner must complete): {len(advisory)}")
    lines.append(f"- Failed: {len(failed)}")
    lines.append(f"- Residual (manual review): {len(report.skipped_residual)}")
    lines.append(f"- DataHub write-back: {_writeback_status(report)}")
    lines.append("")

    lines.extend(_writeback_lines(report))

    lines.append("## Simulated actions" if simulated else "## Executed actions")
    lines.append("")
    if simulated and performed:
        lines.append(
            "These statements were generated and validated. None of them ran; "
            "the rows they name still contain the subject's data."
        )
        lines.append("")
    if performed:
        lines.append("| Asset | Action | Ran | Reason |")
        lines.append("|---|---|---|---|")
        for a in performed:
            ran = "no" if (a.dry_run or report.fixture_mode) else "yes"
            lines.append(
                f"| `{a.asset.name}` | {a.action_type.value} | {ran} | {a.reason} |"
            )
    else:
        lines.append("None.")
    lines.append("")

    if advisory:
        recorded = set(report.annotations_succeeded)
        lines.append("## Advisory actions - owner must complete")
        lines.append("")
        lines.append(
            "Vismriti did not run these. The erasure is not complete until the "
            "owning team does. The last column says whether the flag reached DataHub; "
            "where it did not, notify the owner directly."
        )
        lines.append("")
        lines.append("| Asset | Action | Owners | Next step | Flagged in DataHub |")
        lines.append("|---|---|---|---|---|")
        for a in advisory:
            owners = ", ".join(a.asset.owners) or "unassigned"
            step = a.command or a.reason
            if report.fixture_mode:
                flagged = "n/a (fixture)"
            else:
                flagged = "yes" if a.asset.urn in recorded else "**no**"
            lines.append(
                f"| `{a.asset.name}` | {a.action_type.value} | {owners} | `{step}` | {flagged} |"
            )
        lines.append("")

    if failed:
        lines.append("## Failed actions")
        lines.append("")
        for a in failed:
            lines.append(f"- `{a.asset.name}` - {a.action_type.value} - error: {a.execution_error}")
        lines.append("")

    if report.skipped_residual:
        lines.append("## Residual risk - human review required")
        lines.append("")
        for a in report.skipped_residual:
            lines.append(f"- `{a.asset.name}` ({a.asset.urn}) - {a.reason}")
        lines.append("")

    return "\n".join(lines)


def _action_dict(action: PlannedAction) -> dict[str, object]:
    return {
        "urn": action.asset.urn,
        "asset_name": action.asset.name,
        "action_type": action.action_type.value,
        "sql": action.sql,
        "command": action.command,
        "reason": action.reason,
        "approved": action.approved,
        "executed": action.executed,
        "dry_run": action.dry_run,
        "advisory": action.advisory,
        "execution_error": action.execution_error,
        "is_residual": action.is_residual,
    }


def write_reports(
    plan: ErasurePlan,
    report: ExecutionReport,
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"{plan.request_id}.md"
    json_path = output_dir / f"{plan.request_id}.json"

    md_path.write_text(render_markdown(plan, report), encoding="utf-8")

    audit = {
        "request_id": plan.request_id,
        "subject_email_hash": plan.subject.email_hash,
        "started_at": report.started_at.isoformat(),
        "finished_at": report.finished_at.isoformat() if report.finished_at else None,
        "mode": {
            "dry_run": report.dry_run,
            "fixture_mode": report.fixture_mode,
            "simulated": report.is_simulated(),
            "label": _mode_label(report),
        },
        "counts": {
            "planned": plan.total_assets(),
            "executed": len(report.performed_actions()),
            "advisory": len(report.advisory_actions()),
            "failed": len(report.failed_actions()),
            "residual": len(report.skipped_residual),
        },
        "writeback": {
            "ok": report.writeback_ok,
            # ok reports what the configured backend answered. In fixture mode
            # that answer is canned, so state separately whether a real
            # deployment was ever contacted.
            "reached_datahub": report.writeback_ok and not report.fixture_mode,
            "urn": report.writeback_urn,
            "error": report.writeback_error,
            "annotations_succeeded": report.annotations_succeeded,
            "annotations_failed": report.annotations_failed,
            "annotation_errors": report.annotation_errors,
        },
        "writeback_urn": report.writeback_urn,
        "actions": [
            _action_dict(a)
            for a in [*report.executed, *report.failed, *report.skipped_residual]
        ],
    }
    json_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return md_path, json_path
