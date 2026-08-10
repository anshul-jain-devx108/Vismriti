"""End-to-end test using fixture DataHub responses.

This is what runs in CI and what the demo script relies on when DataHub
Docker isn't available.
"""

from __future__ import annotations

import asyncio

import pytest

from vismriti.services.orchestrator import ErasureOrchestrator
from vismriti.utils.config import settings
from vismriti.core.datahub_client import DataHubClient


@pytest.fixture(autouse=True)
def _dry_run():
    settings.dry_run = True
    yield


def test_end_to_end_fixture_run():
    async def _run():
        client = DataHubClient(use_fixtures=True)
        await client.connect()
        try:
            agent = ErasureOrchestrator(client)
            plan, report = await agent.run(
                "priya.sharma@example.com",
                "test-req-1",
                auto_approve=True,
                fixture_subject_id=48291,
            )
            return plan, report
        finally:
            await client.close()

    plan, report = asyncio.run(_run())

    # At least the two source tables + several derived assets should be planned.
    assert plan.total_assets() >= 5

    # Sandbox asset without owner or tags must land in residual.
    residual_names = [a.asset.name for a in plan.residual_actions]
    assert any("sandbox" in n for n in residual_names)

    # Every non-residual action should be approved and executed in dry-run mode.
    assert all(a.approved for a in plan.actions)
    assert len(report.executed) == len(plan.actions)

    # Write-back should have produced an audit-trail URN.
    assert report.writeback_urn is not None
    assert "erasureRequest" in report.writeback_urn
