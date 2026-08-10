"""Streamlit review-and-approve UI.

Lets a DPO plan an erasure for a subject email, tick or untick individual
actions, execute the approved ones, and download the audit trail. Talks
straight to `ErasureOrchestrator`, so it runs without an LLM key. The
LLM-driven and Slack-driven surfaces live in `vismriti/main.py`.

Run:
    streamlit run src/vismriti/ui/app.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import streamlit as st

from vismriti.core.datahub_client import ClientMode, DataHubClient
from vismriti.core.models import ActionType, ErasurePlan
from vismriti.services.orchestrator import ErasureOrchestrator
from vismriti.services.report import render_markdown, write_reports
from vismriti.utils.config import ConfigError, settings

MODE_LABELS = {
    "Fixture (offline)": ClientMode.FIXTURE.value,
    "Live REST": ClientMode.LIVE_REST.value,
    "MCP stdio (local)": ClientMode.MCP_STDIO.value,
}


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


def _check_mode(mode: str) -> None:
    """Refuse a live backend that has no GMS URL configured."""
    if mode != ClientMode.FIXTURE.value:
        settings.require_datahub_gms_url(f"DataHub backend '{mode}'")


async def _make_plan(email: str, mode: str, fixture_id: int | None) -> ErasurePlan:
    """Plan against a client that is opened and closed inside this event loop.

    The client is not kept in session state: a connection bound to a finished
    event loop cannot be reused by the next Streamlit rerun.
    """
    client = DataHubClient(mode=ClientMode(mode))
    await client.connect()
    try:
        agent = ErasureOrchestrator(client)
        return await agent.plan(email, _request_id(email), fixture_subject_id=fixture_id)
    finally:
        await client.close()


async def _execute_plan(plan: ErasurePlan, mode: str, dry_run: bool):
    settings.dry_run = dry_run
    client = DataHubClient(mode=ClientMode(mode))
    await client.connect()
    try:
        agent = ErasureOrchestrator(client)
        report = await agent.execute_plan(plan, auto_approve=False)
    finally:
        await client.close()
    md_path, json_path = write_reports(plan, report, settings.output_dir)
    return report, md_path, json_path


def _run_async(coro):
    return asyncio.run(coro)


def _render_sidebar() -> tuple[str, str, int, bool, bool]:
    st.header("Request")
    email = st.text_input("Subject email", value="priya.sharma@example.com")

    mode_label = st.radio(
        "DataHub backend",
        options=list(MODE_LABELS),
        index=0,
        help=(
            "Fixture:    pre-canned JSON, works offline.\n"
            "Live REST:  calls the DataHub GMS REST API over HTTPS.\n"
            "MCP stdio:  spawns mcp-server-datahub as a subprocess."
        ),
    )
    mode = MODE_LABELS[mode_label]

    if mode == ClientMode.FIXTURE.value:
        fixture_id = st.number_input(
            "Fixture subject id",
            value=48291,
            step=1,
            help=(
                "Fixtures skip the warehouse lookup, so the subject's numeric id "
                "is passed in directly. 48291 matches the seeded "
                "priya.sharma@example.com."
            ),
        )
    else:
        # Live modes resolve the id by SQL; this is only the fallback value.
        fixture_id = 48291

    dry_run = st.toggle("Dry-run SQL (no commit)", value=True)
    plan_btn = st.button("1. Plan erasure", type="primary")

    st.markdown("---")
    st.caption(
        "Vismriti reads DataHub, walks forward lineage from PII-tagged sources, "
        "generates per-asset actions, and writes an audit trail back to DataHub."
    )
    return email, mode, int(fixture_id), dry_run, plan_btn


def _render_actions(plan: ErasurePlan) -> None:
    approvals = st.session_state.setdefault("approvals", {})

    st.markdown("### Actions (review + approve)")
    for action in plan.actions:
        cols = st.columns([1, 4, 2, 5])
        approved = cols[0].checkbox(
            "approve",
            key=f"appr_{action.asset.urn}",
            value=approvals.get(action.asset.urn, True),
            label_visibility="collapsed",
        )
        approvals[action.asset.urn] = approved
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


def _render_report(report, md_path, json_path, plan: ErasurePlan) -> None:
    """Show what actually happened, including anything DataHub rejected."""
    if report.is_simulated():
        st.warning(
            "Simulation: nothing was erased. "
            + ("Metadata and the DataHub write-back came from fixture files. "
               if report.fixture_mode else "")
            + ("SQL was rendered but never sent to the warehouse."
               if report.dry_run else "")
        )

    verb = "Simulated" if report.is_simulated() else "Executed"
    st.info(
        f"{verb}: {len(report.performed_actions())} | "
        f"Advisory (owner must complete): {len(report.advisory_actions())} | "
        f"Failed: {len(report.failed_actions())} | "
        f"Residual: {len(report.skipped_residual)}"
    )
    for action in report.failed_actions():
        st.error(
            f"**{action.asset.name}** failed: "
            f"{action.execution_error or 'no reason recorded'}"
        )

    if report.fixture_mode:
        st.warning(
            f"DataHub write-back simulated. `{report.writeback_urn}` is a fixture "
            "value; no DataHub deployment was contacted."
        )
    elif report.writeback_ok and report.writeback_urn:
        st.success(f"DataHub audit trail written: `{report.writeback_urn}`")
    else:
        st.error(
            "DataHub write-back FAILED, so DataHub holds no record of this request: "
            f"{report.writeback_error or 'no reason recorded'}"
        )
    for urn, reason in report.annotation_errors.items():
        st.warning(f"Annotation not accepted for `{urn}`: {reason}")

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


def main() -> None:
    st.set_page_config(page_title="Vismriti - GDPR Erasure Agent", layout="wide")
    st.title("Vismriti")
    st.caption(
        "The agent that helps your data forget - GDPR Article 17 automation "
        "via DataHub lineage."
    )

    with st.sidebar:
        email, mode, fixture_id, dry_run, plan_btn = _render_sidebar()

    if plan_btn:
        st.session_state.pop("plan", None)
        st.session_state.pop("report", None)
        try:
            _check_mode(mode)
            with st.spinner(f"Resolving subject, traversing lineage, planning... [{mode}]"):
                plan_obj = _run_async(_make_plan(email, mode, fixture_id))
        except ConfigError as exc:
            st.error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - a DPO sees a message, not a traceback
            st.error(f"Planning failed ({type(exc).__name__}): {exc}")
            return
        st.session_state["plan"] = plan_obj
        st.session_state["mode"] = mode
        st.session_state["approvals"] = {a.asset.urn: True for a in plan_obj.actions}

    plan: ErasurePlan | None = st.session_state.get("plan")

    if plan is None:
        st.info("Enter a subject email in the sidebar and click **Plan erasure**.")
        return

    plan_mode = st.session_state.get("mode", ClientMode.FIXTURE.value)
    if plan_mode == ClientMode.FIXTURE.value:
        st.warning(
            "Fixture mode: this plan comes from canned JSON, not from DataHub, "
            "and executing it will not touch real data."
        )

    st.subheader(f"Plan: `{plan.request_id}`")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total assets", plan.total_assets())
    col2.metric("Actionable", len(plan.actions))
    col3.metric("Residual risk", len(plan.residual_actions))
    col4.metric("Subject id", plan.subject.primary_id or "?")

    _render_actions(plan)

    st.markdown("---")
    if st.button("2. Execute approved actions", type="primary"):
        try:
            with st.spinner("Executing, writing back to DataHub..."):
                st.session_state["report"] = _run_async(_execute_plan(plan, plan_mode, dry_run))
        except Exception as exc:  # noqa: BLE001 - a DPO sees a message, not a traceback
            st.session_state.pop("report", None)
            st.error(f"Execution failed ({type(exc).__name__}): {exc}")

    if st.session_state.get("report"):
        report, md_path, json_path = st.session_state["report"]
        _render_report(report, md_path, json_path, plan)


if __name__ == "__main__":
    main()
