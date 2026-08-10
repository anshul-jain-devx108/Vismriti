"""Domain models for the erasure agent.

These are the shared shapes passed between lineage traversal, planning,
execution, write-back, and reporting. Keep them plain — a plan is data,
not behaviour.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    """What the agent proposes to do with a single downstream asset."""

    ANONYMIZE_ROW = "anonymize_row"          # UPDATE source row, null out PII
    DELETE_ROW = "delete_row"                # DELETE FROM derived table
    DBT_RERUN = "dbt_rerun"                  # Re-run dbt model after source erase
    DASHBOARD_INVALIDATE = "dashboard_invalidate"   # Flag BI cache refresh
    ML_MODEL_ANNOTATE = "ml_model_annotate"  # Annotate model for retrain queue
    RESIDUAL_REVIEW = "residual_review"      # Cannot handle - human decision


class AssetType(str, Enum):
    """Kind of DataHub entity we're acting on."""

    DATASET = "dataset"
    DASHBOARD = "dashboard"
    CHART = "chart"
    ML_MODEL = "ml_model"
    ML_FEATURE_TABLE = "ml_feature_table"
    UNKNOWN = "unknown"


class PIIColumn(BaseModel):
    """A column in DataHub tagged with a PII glossary term."""

    dataset_urn: str
    column_name: str
    pii_type: str = Field(description="e.g. 'email', 'phone', 'name', 'ssn'")
    tags: list[str] = Field(default_factory=list)


class SubjectIdentifiers(BaseModel):
    """Resolved identifiers for one erasure subject across sources."""

    input_email: str
    primary_id: int | str | None = None
    email_hash: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Asset(BaseModel):
    """A DataHub entity discovered in lineage traversal."""

    urn: str
    name: str
    asset_type: AssetType
    platform: str | None = None
    owners: list[str] = Field(default_factory=list)
    pii_columns: list[PIIColumn] = Field(default_factory=list)
    upstreams: list[str] = Field(default_factory=list)
    depth: int = 0


class PlannedAction(BaseModel):
    """One row of the erasure plan."""

    asset: Asset
    action_type: ActionType
    sql: str | None = None
    command: str | None = None
    reason: str
    is_residual: bool = False
    approved: bool = False
    executed: bool = False
    execution_error: str | None = None


class ErasurePlan(BaseModel):
    """Full plan for one erasure request."""

    request_id: str
    subject: SubjectIdentifiers
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actions: list[PlannedAction] = Field(default_factory=list)
    residual_actions: list[PlannedAction] = Field(default_factory=list)

    def total_assets(self) -> int:
        return len(self.actions) + len(self.residual_actions)


class ExecutionReport(BaseModel):
    """Result of running an approved plan."""

    request_id: str
    subject_email: str
    started_at: datetime
    finished_at: datetime | None = None
    executed: list[PlannedAction] = Field(default_factory=list)
    failed: list[PlannedAction] = Field(default_factory=list)
    skipped_residual: list[PlannedAction] = Field(default_factory=list)
    writeback_urn: str | None = None

    def duration_seconds(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()
