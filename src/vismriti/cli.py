"""CLI entry point.

Usage:
    erase plan --email priya.sharma@example.com
    erase run --email priya.sharma@example.com --approve
    erase run --email priya.sharma@example.com --fixtures
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import NoReturn

import typer
from rich.console import Console
from rich.table import Table

from .core.datahub_client import DataHubClient
from .core.models import ErasurePlan
from .services.orchestrator import ErasureOrchestrator
from .services.report import write_reports
from .utils.config import ConfigError, settings

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
    """Build a connected client, refusing to run live without a GMS URL."""
    if not use_fixtures:
        # Raises with an actionable message rather than falling back to a
        # built-in URL or an MCP subprocess pointed at nothing.
        settings.require_datahub_gms_url("Live mode (no --fixtures)")
    client = DataHubClient(use_fixtures=use_fixtures)
    await client.connect()
    return client


def _fail(exc: Exception) -> NoReturn:
    console.print(f"[red]Configuration error:[/red] {exc}")
    raise typer.Exit(code=1)


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
            console.print(
                f"\n[bold]Total assets:[/bold] {plan_obj.total_assets()}  "
                f"[bold]Residual:[/bold] {len(plan_obj.residual_actions)}"
            )
            if fixtures:
                console.print("[yellow]Fixture mode: plan built from canned data.[/yellow]")
        finally:
            await client.close()

    try:
        asyncio.run(_run())
    except ConfigError as exc:
        _fail(exc)


@app.command()
def run(
    email: str = typer.Option(..., help="Subject email address"),
    approve: bool = typer.Option(False, help="Auto-approve all non-residual actions"),
    fixtures: bool = typer.Option(False, help="Use fixture data instead of live DataHub"),
    fixture_subject_id: int = typer.Option(48291, help="Fixture subject id"),
    dry_run: bool = typer.Option(True, help="Dry-run SQL execution (no commit)"),
) -> None:
    """Plan, (optionally auto-approve), execute, and write back to DataHub."""

    async def _run() -> bool:
        """Run the pipeline. Returns True only if everything it reported happened."""
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

            # Advisory actions are counted separately: Vismriti records them,
            # the owning team performs them.
            performed = report.performed_actions()
            advisory = report.advisory_actions()
            verb = "simulated" if report.is_simulated() else "executed"
            console.print(
                f"\n{verb}={len(performed)} advisory={len(advisory)} "
                f"failed={len(report.failed_actions())} "
                f"residual={len(report.skipped_residual)}"
            )
            if report.failed_actions():
                console.print(
                    f"[red]{len(report.failed_actions())} action(s) failed.[/red] "
                    "See the audit trail."
                )
            if fixtures:
                console.print(
                    "[yellow]Fixture mode: metadata and the DataHub write-back were "
                    "canned. Nothing was erased.[/yellow]"
                )
            if dry_run:
                console.print(
                    "[yellow]Dry run: SQL was rendered, not sent. No row changed.[/yellow]"
                )
            console.print(f"[blue]Report:[/blue] {md_path}")
            console.print(f"[blue]Audit:[/blue]  {json_path}")

            # Report the write-back result as it happened. A missing URN means
            # DataHub has no record of this request.
            if fixtures:
                console.print(
                    "[yellow]DataHub write-back: simulated.[/yellow] "
                    f"{report.writeback_urn} is a fixture value; no DataHub "
                    "deployment was contacted."
                )
            elif report.writeback_ok and report.writeback_urn:
                console.print(f"[blue]DataHub:[/blue] {report.writeback_urn}")
            else:
                console.print(
                    "[red]DataHub write-back FAILED:[/red] "
                    f"{report.writeback_error or 'no reason recorded'}"
                )
            if report.annotations_failed:
                console.print(
                    f"[red]{len(report.annotations_failed)} DataHub annotation(s) "
                    "were not accepted.[/red]"
                )

            ok = (
                not report.failed_actions()
                and report.writeback_ok
                and not report.annotations_failed
            )
            if ok and report.is_simulated():
                console.print(
                    "[yellow]Simulation complete.[/yellow] No personal data was "
                    "erased. The request is still open."
                )
            elif ok:
                console.print("[green]Run complete.[/green]")
            else:
                console.print(
                    "[red]Run incomplete:[/red] part of this erasure did not happen. "
                    "Do not close the request."
                )
            return ok
        finally:
            await client.close()

    try:
        completed = asyncio.run(_run())
    except ConfigError as exc:
        _fail(exc)
    # Non-zero exit so a scheduler or CI job cannot read a partial erasure as done.
    raise typer.Exit(code=0 if completed else 1)


if __name__ == "__main__":
    app()
