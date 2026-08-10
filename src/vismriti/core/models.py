"""Domain models for the erasure agent.

Shared shapes passed between lineage traversal, planning, execution,
write-back, and reporting. A plan is data, not behaviour.
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
    dry_run: bool = Field(
        default=False,
        description=(
            "The statement was rendered and checked but never sent to the "
            "warehouse. `executed` records that the action reached the end of "
            "its lifecycle, not that any row changed."
        ),
    )
    advisory: bool = Field(
        default=False,
        description=(
            "Vismriti only records this action (dbt re-run, dashboard cache "
            "invalidation, ML retrain flag); the owning team performs it."
        ),
    )


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
    """Result of running an approved plan, including the DataHub write-back."""

    request_id: str
    subject_email: str
    started_at: datetime
    finished_at: datetime | None = None
    executed: list[PlannedAction] = Field(default_factory=list)
    failed: list[PlannedAction] = Field(default_factory=list)
    skipped_residual: list[PlannedAction] = Field(default_factory=list)

    # How this run was carried out. Both default to False so a report can
    # never imply real work by omission; the runner stamps them.
    dry_run: bool = Field(
        default=False,
        description="SQL was rendered but never sent to the warehouse.",
    )
    fixture_mode: bool = Field(
        default=False,
        description=(
            "Metadata reads and DataHub write-backs were served from canned "
            "fixture files, not from a DataHub deployment."
        ),
    )

    # Write-back outcome. writeback_urn is set only when DataHub actually
    # accepted the erasureRequest entity; it is never a placeholder.
    writeback_urn: str | None = None
    writeback_ok: bool = False
    writeback_error: str | None = None
    annotations_succeeded: list[str] = Field(default_factory=list)
    annotations_failed: list[str] = Field(default_factory=list)
    annotation_errors: dict[str, str] = Field(
        default_factory=dict,
        description="URN -> reason, for every annotation DataHub did not accept.",
    )

    def duration_seconds(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()

    def is_simulated(self) -> bool:
        """True when nothing in this run touched a real system."""
        return self.dry_run or self.fixture_mode

    def performed_actions(self) -> list[PlannedAction]:
        """Erasure actions Vismriti ran itself.

        In a dry run these were rendered and not sent; check `dry_run` before
        reading them as rows that changed.
        """
        return [a for a in self.executed if not a.advisory]

    def advisory_actions(self) -> list[PlannedAction]:
        """Advisory actions, whichever bucket the runner put them in."""
        return [a for a in (*self.executed, *self.failed) if a.advisory]

    def failed_actions(self) -> list[PlannedAction]:
        """Actions that were attempted and did not succeed."""
        return [a for a in self.failed if not a.advisory]
