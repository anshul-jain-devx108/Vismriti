"""Human-readable + machine-readable erasure reports."""

from __future__ import annotations

import json
from pathlib import Path

from ..core.models import ErasurePlan, ExecutionReport


def render_markdown(plan: ErasurePlan, report: ExecutionReport) -> str:
    lines: list[str] = []
    lines.append(f"# Erasure Report - {plan.request_id}")
    lines.append("")
    lines.append(f"- Subject email: `{report.subject_email}`")
    lines.append(f"- Subject hash: `{plan.subject.email_hash}`")
    lines.append(f"- Started:  {report.started_at.isoformat()}")
    lines.append(f"- Finished: {report.finished_at.isoformat() if report.finished_at else '(in progress)'}")
    lines.append(f"- Duration: {report.duration_seconds():.1f}s")
    lines.append(f"- Assets planned: {plan.total_assets()}")
    lines.append(f"- Executed OK:   {len(report.executed)}")
    lines.append(f"- Failed:        {len(report.failed)}")
    lines.append(f"- Residual (manual review): {len(report.skipped_residual)}")
    if report.writeback_urn:
        lines.append(f"- DataHub audit trail: `{report.writeback_urn}`")
    lines.append("")

    lines.append("## Executed actions")
    lines.append("")
    lines.append("| Asset | Action | Reason |")
    lines.append("|---|---|---|")
    for a in report.executed:
        lines.append(f"| `{a.asset.name}` | {a.action_type.value} | {a.reason} |")
    lines.append("")

    if report.failed:
        lines.append("## Failed actions")
        lines.append("")
        for a in report.failed:
            lines.append(f"- `{a.asset.name}` - {a.action_type.value} - error: {a.execution_error}")
        lines.append("")

    if report.skipped_residual:
        lines.append("## Residual risk - human review required")
        lines.append("")
        for a in report.skipped_residual:
            lines.append(f"- `{a.asset.name}` ({a.asset.urn}) - {a.reason}")
        lines.append("")

    return "\n".join(lines)


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
        "writeback_urn": report.writeback_urn,
        "actions": [
            {
                "urn": a.asset.urn,
                "asset_name": a.asset.name,
                "action_type": a.action_type.value,
                "sql": a.sql,
                "command": a.command,
                "reason": a.reason,
                "approved": a.approved,
                "executed": a.executed,
                "execution_error": a.execution_error,
                "is_residual": a.is_residual,
            }
            for a in [*report.executed, *report.failed, *report.skipped_residual]
        ],
    }
    json_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return md_path, json_path
