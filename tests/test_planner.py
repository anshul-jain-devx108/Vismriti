"""Unit tests for the deterministic action planner."""

from __future__ import annotations

from vismriti.core.models import Asset, AssetType, PIIColumn, SubjectIdentifiers
from vismriti.services.planner import build_plan, plan_action


def _subject() -> SubjectIdentifiers:
    return SubjectIdentifiers(
        input_email="priya.sharma@example.com",
        primary_id=48291,
        email_hash="deadbeef",
    )


def test_source_dataset_anonymizes():
    asset = Asset(
        urn="urn:li:dataset:(pg,healthcare.raw.patients,PROD)",
        name="patients",
        asset_type=AssetType.DATASET,
        pii_columns=[
            PIIColumn(dataset_urn="x", column_name="email", pii_type="email"),
            PIIColumn(dataset_urn="x", column_name="name", pii_type="name"),
        ],
        depth=0,
    )
    action = plan_action(asset, _subject())
    assert action.action_type.value == "anonymize_row"
    assert "UPDATE patients" in (action.sql or "")
    assert "email = NULL" in (action.sql or "")


def test_derived_postgres_deletes():
    asset = Asset(
        urn="urn:li:dataset:(pg,healthcare.marts.patient_360,PROD)",
        name="patient_360",
        asset_type=AssetType.DATASET,
        platform="postgres",
        owners=["analytics@example.com"],
        depth=2,
    )
    action = plan_action(asset, _subject())
    assert action.action_type.value == "delete_row"
    assert "DELETE FROM patient_360" in (action.sql or "")


def test_derived_dbt_reruns():
    asset = Asset(
        urn="urn:li:dataset:(dbt,healthcare.staging.patients_clean,PROD)",
        name="patients_clean",
        asset_type=AssetType.DATASET,
        platform="dbt",
        owners=["data-eng@example.com"],
        depth=1,
    )
    action = plan_action(asset, _subject())
    assert action.action_type.value == "dbt_rerun"
    assert "dbt run" in (action.command or "")


def test_dashboard_invalidates():
    asset = Asset(
        urn="urn:li:dashboard:(tableau,x)",
        name="exec_dashboard",
        asset_type=AssetType.DASHBOARD,
        owners=["exec@example.com"],
        depth=3,
    )
    action = plan_action(asset, _subject())
    assert action.action_type.value == "dashboard_invalidate"


def test_ml_model_annotates():
    asset = Asset(
        urn="urn:li:mlModel:(mlflow,churn_v3,PROD)",
        name="churn_v3",
        asset_type=AssetType.ML_MODEL,
        owners=["ml@example.com"],
        depth=3,
    )
    action = plan_action(asset, _subject())
    assert action.action_type.value == "ml_model_annotate"


def test_orphan_downstream_is_residual():
    asset = Asset(
        urn="urn:li:dataset:(pg,healthcare.analytics_sandbox.priya_analysis_2024,PROD)",
        name="priya_analysis_2024",
        asset_type=AssetType.DATASET,
        owners=[],
        pii_columns=[],
        depth=3,
    )
    action = plan_action(asset, _subject())
    assert action.action_type.value == "residual_review"
    assert action.is_residual is True


def test_build_plan_separates_residual():
    assets = [
        Asset(
            urn="urn:li:dataset:(pg,healthcare.raw.patients,PROD)",
            name="patients",
            asset_type=AssetType.DATASET,
            owners=["data-eng@example.com"],
            pii_columns=[PIIColumn(dataset_urn="x", column_name="email", pii_type="email")],
            depth=0,
        ),
        Asset(
            urn="urn:li:dataset:(pg,healthcare.sandbox.orphan,PROD)",
            name="orphan",
            asset_type=AssetType.DATASET,
            owners=[],
            depth=2,
        ),
    ]
    plan = build_plan("req-1", _subject(), assets)
    assert len(plan.actions) == 1
    assert len(plan.residual_actions) == 1
    assert plan.total_assets() == 2
