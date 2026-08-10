"""Streamlit demo UI for Vismriti.

The zero-dependency review + approve surface. Priya (DPO) can point Vismriti
at a subject email, walk through the deterministically-generated plan,
tick/untick individual actions, execute, and download the audit trail.

Bypasses the Agno LLM layer - talks directly to `ErasureOrchestrator` so
the UI runs even without an OpenAI key. Ideal for offline demos + hackathon
video capture. For the LLM-driven / Slack-driven flow see `vismriti/main.py`.

Run:
    streamlit run src/vismriti/ui/app.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import streamlit as st

from vismriti.services.orchestrator import ErasureOrchestrator
from vismriti.utils.config import settings
from vismriti.core.datahub_client import ClientMode, DataHubClient
from vismriti.core.models import ActionType, ErasurePlan
from vismriti.services.report import render_markdown, write_reports


def _request_id(email: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = email.split("@")[0].replace(".", "-")
    return f"{stamp}-{slug}"


def _action_badge(action_type: ActionType) -> str:
    """Short text tag rendered alongside the action_type value in the plan table."""
    palette = {
        ActionType.ANONYMIZE_ROW: "[anon]",
        ActionType.DELETE_ROW: "[del]",
        ActionType.DBT_RERUN: "[dbt]",
        ActionType.DASHBOARD_INVALIDATE: "[bi]",
        ActionType.ML_MODEL_ANNOTATE: "[ml]",
        ActionType.RESIDUAL_REVIEW: "[residual]",
    }
    return palette.get(action_type, "")


async def _make_plan(
    email: str, mode: str, fixture_id: int | None
) -> tuple[ErasurePlan, DataHubClient]:
    client = DataHubClient(mode=ClientMode(mode))
    await client.connect()
    agent = ErasureOrchestrator(client)
    plan = await agent.plan(email, _request_id(email), fixture_subject_id=fixture_id)
    return plan, client


async def _execute_plan(plan: ErasurePlan, client: DataHubClient, dry_run: bool):
    settings.dry_run = dry_run
    agent = ErasureOrchestrator(client)
    report = await agent.execute_plan(plan, auto_approve=False)
    md_path, json_path = write_reports(plan, report, settings.output_dir)
    return report, md_path, json_path


def _run_async(coro):
    return asyncio.run(coro)


def main() -> None:
    st.set_page_config(page_title="Vismriti - GDPR Erasure Agent", layout="wide")
    st.title("Vismriti")
    st.caption("The agent that helps your data forget - GDPR Article 17 automation via DataHub lineage.")

    with st.sidebar:
        st.header("Request")
        email = st.text_input("Subject email", value="priya.sharma@example.com")

        mode_label = st.radio(
            "DataHub backend",
            options=["Fixture (offline)", "Live REST (Azure)", "MCP stdio (local)"],
            index=0,
            help=(
                "Fixture:    pre-canned JSON, works offline.\n"
                "Live REST:  hits the deployed Azure GMS over HTTPS.\n"
                "MCP stdio:  spawns mcp-server-datahub as a subprocess (needs the package installed)."
            ),
        )
        mode = {
            "Fixture (offline)": "fixture",
            "Live REST (Azure)": "live-rest",
            "MCP stdio (local)": "mcp-stdio",
        }[mode_label]

        # Fixture-only knob: only shown in fixture mode, because in live mode
        # the subject_id is resolved by a real SQL lookup against the warehouse.
        if mode == "fixture":
            fixture_id = st.number_input(
                "Fixture subject id",
                value=48291,
                step=1,
                help=(
                    "Only used in fixture mode. Fixtures skip the real DB "
                    "lookup, so the subject's numeric id is passed in directly. "
                    "Default 48291 matches the seeded `priya.sharma@example.com`."
                ),
            )
        else:
            fixture_id = 48291  # still pass — live mode also uses it as fallback when SQL lookup fails

        dry_run = st.toggle("Dry-run SQL (no commit)", value=True)
        plan_btn = st.button("1. Plan erasure", type="primary")

        st.markdown("---")
        st.caption(
            "This agent reads DataHub via MCP, walks forward lineage from "
            "PII-tagged sources, generates per-asset actions, and writes an "
            "audit trail back to DataHub."
        )

    if plan_btn:
        with st.spinner(f"Resolving subject, traversing lineage, planning actions... [{mode}]"):
            plan, client = _run_async(_make_plan(email, mode, fixture_id))
            st.session_state["plan"] = plan
            st.session_state["client"] = client
            st.session_state["mode"] = mode
            st.session_state["approvals"] = {a.asset.urn: True for a in plan.actions}

    plan: ErasurePlan | None = st.session_state.get("plan")

    if plan is None:
        st.info("Enter a subject email in the sidebar and click **Plan erasure**.")
        return

    st.subheader(f"Plan: `{plan.request_id}`")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total assets", plan.total_assets())
    col2.metric("Actionable", len(plan.actions))
    col3.metric("Residual risk", len(plan.residual_actions))
    col4.metric("Subject id", plan.subject.primary_id or "?")

    st.markdown("### Actions (review + approve)")
    for action in plan.actions:
        cols = st.columns([1, 4, 2, 5])
        approved = cols[0].checkbox(
            "approve",
            key=f"appr_{action.asset.urn}",
            value=st.session_state["approvals"].get(action.asset.urn, True),
            label_visibility="collapsed",
        )
        st.session_state["approvals"][action.asset.urn] = approved
        action.approved = approved

        cols[1].markdown(f"**{action.asset.name}**\n\n`depth={action.asset.depth}`")
        cols[2].markdown(f"{_action_badge(action.action_type)} `{action.action_type.value}`")
        cols[3].markdown(f"_{action.reason}_")
        if action.sql:
            with cols[3].expander("SQL"):
                st.code(action.sql, language="sql")
        if action.command:
            with cols[3].expander("Command"):
                st.code(action.command, language="bash")

    if plan.residual_actions:
        st.markdown("### Residual risk (human review required)")
        for r in plan.residual_actions:
            st.warning(f"**{r.asset.name}** - {r.reason}")

    st.markdown("---")
    if st.button("2. Execute approved actions", type="primary"):
        with st.spinner("Executing, writing back to DataHub..."):
            report, md_path, json_path = _run_async(
                _execute_plan(plan, st.session_state["client"], dry_run)
            )
            st.success(
                f"Done. Executed: {len(report.executed)} | "
                f"Failed: {len(report.failed)} | "
                f"Residual: {len(report.skipped_residual)}"
            )
            st.markdown(f"**DataHub audit trail:** `{report.writeback_urn}`")
            st.download_button(
                "Download report (Markdown)",
                data=md_path.read_bytes(),
                file_name=md_path.name,
                mime="text/markdown",
            )
            st.download_button(
                "Download audit trail (JSON)",
                data=json_path.read_bytes(),
                file_name=json_path.name,
                mime="application/json",
            )
            with st.expander("View report"):
                st.markdown(render_markdown(plan, report))


if __name__ == "__main__":
    main()
