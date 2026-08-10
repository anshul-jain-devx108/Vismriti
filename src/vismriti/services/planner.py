"""Per-asset action planner.

Given a resolved subject and the set of downstream assets, decide what
action to take on each one and emit SQL / commands via Jinja templates.

The rules are intentionally simple and explicit rather than LLM-driven
because:
    (a) auditors need to see deterministic logic behind an erasure decision
    (b) LLM SQL for destructive ops is a footgun for a demo

The LLM's role is elsewhere: subject-identifier resolution across schemas
and the human-facing summary in the report. The plan itself is code.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..core.models import (
    ActionType,
    Asset,
    AssetType,
    ErasurePlan,
    PlannedAction,
    SubjectIdentifiers,
)

TEMPLATE_DIR = Path(__file__).parent / "sql_templates"

_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(disabled_extensions=("sql", "j2"), default=False),
    keep_trailing_newline=True,
)


def _render(template_name: str, **ctx: object) -> str:
    return _env.get_template(template_name).render(**ctx).strip()


def _is_source(asset: Asset) -> bool:
    """A source asset carries PII columns itself (depth=0 in traversal)."""
    return asset.depth == 0 and bool(asset.pii_columns)


def _is_derived_dataset(asset: Asset) -> bool:
    return asset.asset_type == AssetType.DATASET and asset.depth > 0


def _is_orphan(asset: Asset) -> bool:
    """No owner + no PII tag + downstream of source = residual risk.

    This is the class of asset a static PII catalog misses: derived tables
    an analyst forked into a sandbox, no tags propagated, no owner set.
    """
    return not asset.owners and not asset.pii_columns and asset.depth > 0


def plan_action(asset: Asset, subject: SubjectIdentifiers) -> PlannedAction:
    """Map one asset to one action.

    Order matters: more-specific rules before more-general ones.
    """

    # 1. Residual: no owner AND no tags AND downstream. Human must decide.
    if _is_orphan(asset):
        return PlannedAction(
            asset=asset,
            action_type=ActionType.RESIDUAL_REVIEW,
            reason=(
                "No owner and no PII tag, but appears downstream of tagged sources. "
                "Static classification would miss this asset; agent flags for manual review."
            ),
            is_residual=True,
        )

    # 2. Source dataset with PII columns: anonymize in place.
    if _is_source(asset):
        pii_cols = [c.column_name for c in asset.pii_columns]
        sql = _render(
            "anonymize_source.sql.j2",
            table=asset.name,
            pii_columns=pii_cols,
            id_column="patient_id" if "patient" in asset.name.lower() else "user_id",
            subject_id=subject.primary_id,
        )
        return PlannedAction(
            asset=asset,
            action_type=ActionType.ANONYMIZE_ROW,
            sql=sql,
            reason=f"Source table with {len(pii_cols)} PII column(s). Null out PII, retain row for FK integrity.",
        )

    # 3. Derived dataset: delete the subject's rows or (if dbt-managed) re-run.
    if _is_derived_dataset(asset):
        if any("dbt" in (p or "").lower() for p in [asset.platform]):
            return PlannedAction(
                asset=asset,
                action_type=ActionType.DBT_RERUN,
                command=f"dbt run --select {asset.name}",
                reason="Derived dbt model - re-run after source anonymization propagates.",
            )
        sql = _render(
            "delete_derived.sql.j2",
            table=asset.name,
            id_column="patient_id" if "patient" in asset.name.lower() else "user_id",
            subject_id=subject.primary_id,
            subject_hash=subject.email_hash,
        )
        return PlannedAction(
            asset=asset,
            action_type=ActionType.DELETE_ROW,
            sql=sql,
            reason="Derived table containing subject row - delete directly.",
        )

    # 4. Dashboard / chart: flag for cache invalidation.
    if asset.asset_type in (AssetType.DASHBOARD, AssetType.CHART):
        return PlannedAction(
            asset=asset,
            action_type=ActionType.DASHBOARD_INVALIDATE,
            command=f"# invalidate cache for {asset.urn}",
            reason="BI asset - flag for cache/extract refresh so stale PII doesn't render.",
        )

    # 5. ML model / feature table: annotate for retrain queue.
    if asset.asset_type in (AssetType.ML_MODEL, AssetType.ML_FEATURE_TABLE):
        return PlannedAction(
            asset=asset,
            action_type=ActionType.ML_MODEL_ANNOTATE,
            command=f"# annotate {asset.urn} training_data_erasure=pending",
            reason="ML asset trained on data containing subject - flag for retrain per policy.",
        )

    # Fallback: residual.
    return PlannedAction(
        asset=asset,
        action_type=ActionType.RESIDUAL_REVIEW,
        reason=f"Unhandled asset type '{asset.asset_type}' - manual review required.",
        is_residual=True,
    )


def build_plan(
    request_id: str,
    subject: SubjectIdentifiers,
    assets: list[Asset],
) -> ErasurePlan:
    plan = ErasurePlan(request_id=request_id, subject=subject)
    for asset in assets:
        action = plan_action(asset, subject)
        if action.is_residual:
            plan.residual_actions.append(action)
        else:
            plan.actions.append(action)
    return plan
