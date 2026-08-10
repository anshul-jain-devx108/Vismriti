"""CLI entry point.

Usage:
    erase plan --email priya.sharma@example.com
    erase run --email priya.sharma@example.com --approve
    erase run --email priya.sharma@example.com --fixtures  # demo mode
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import typer
from rich.console import Console
from rich.table import Table

from .services.orchestrator import ErasureOrchestrator
from .utils.config import settings
from .core.datahub_client import DataHubClient
from .core.models import ErasurePlan
from .services.report import write_reports

app = typer.Typer(help="Vismriti - GDPR Article 17 automation via DataHub lineage.")
console = Console()


def _request_id(email: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = email.split("@")[0].replace(".", "-")
    return f"{stamp}-{slug}"


def _render_plan_table(plan: ErasurePlan) -> Table:
    table = Table(title=f"Erasure plan: {plan.request_id}")
    table.add_column("Depth", justify="right")
    table.add_column("Asset")
    table.add_column("Action")
    table.add_column("Reason", overflow="fold")

    for a in plan.actions:
        table.add_row(str(a.asset.depth), a.asset.name, a.action_type.value, a.reason)
    for a in plan.residual_actions:
        table.add_row(
            str(a.asset.depth),
            f"[yellow]{a.asset.name}[/yellow]",
            f"[yellow]{a.action_type.value}[/yellow]",
            a.reason,
        )
    return table


async def _make_client(use_fixtures: bool) -> DataHubClient:
    client = DataHubClient(use_fixtures=use_fixtures)
    await client.connect()
    return client


@app.command()
def plan(
    email: str = typer.Option(..., help="Subject email address"),
    fixtures: bool = typer.Option(False, help="Use fixture data instead of live DataHub"),
    fixture_subject_id: int = typer.Option(48291, help="Fixture subject id"),
) -> None:
    """Generate and print an erasure plan without executing it."""

    async def _run() -> None:
        client = await _make_client(fixtures)
        try:
            agent = ErasureOrchestrator(client)
            rid = _request_id(email)
            plan_obj = await agent.plan(email, rid, fixture_subject_id=fixture_subject_id)
            console.print(_render_plan_table(plan_obj))
            console.print(f"\n[bold]Total assets:[/bold] {plan_obj.total_assets()}  "
                          f"[bold]Residual:[/bold] {len(plan_obj.residual_actions)}")
        finally:
            await client.close()

    asyncio.run(_run())


@app.command()
def run(
    email: str = typer.Option(..., help="Subject email address"),
    approve: bool = typer.Option(False, help="Auto-approve all non-residual actions"),
    fixtures: bool = typer.Option(False, help="Use fixture data instead of live DataHub"),
    fixture_subject_id: int = typer.Option(48291, help="Fixture subject id"),
    dry_run: bool = typer.Option(True, help="Dry-run SQL execution (no commit)"),
) -> None:
    """Plan, (optionally auto-approve), execute, and write back to DataHub."""

    async def _run() -> None:
        settings.dry_run = dry_run
        client = await _make_client(fixtures)
        try:
            agent = ErasureOrchestrator(client)
            rid = _request_id(email)
            plan_obj, report = await agent.run(
                email, rid, auto_approve=approve, fixture_subject_id=fixture_subject_id
            )
            md_path, json_path = write_reports(plan_obj, report, settings.output_dir)
            console.print(_render_plan_table(plan_obj))
            console.print(f"\n[green]OK[/green] executed={len(report.executed)} "
                          f"failed={len(report.failed)} residual={len(report.skipped_residual)}")
            console.print(f"[blue]Report:[/blue] {md_path}")
            console.print(f"[blue]Audit:[/blue]  {json_path}")
            if report.writeback_urn:
                console.print(f"[blue]DataHub:[/blue] {report.writeback_urn}")
        finally:
            await client.close()

    asyncio.run(_run())


if __name__ == "__main__":
    app()
