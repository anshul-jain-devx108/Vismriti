"""Execute approved plan actions against the target warehouse.

Only approved, non-residual actions are touched. Whatever happens is stamped
back on the PlannedAction, so the report carries what actually ran rather than
what was proposed. Actions Vismriti cannot perform itself are marked advisory
and left unexecuted; nothing here reports work it did not do.
"""

from __future__ import annotations

import logging

from ..core.models import ActionType, PlannedAction
from ..utils.config import settings

logger = logging.getLogger(__name__)

SQL_ACTIONS = (ActionType.ANONYMIZE_ROW, ActionType.DELETE_ROW)

# Vismriti has no dbt runner, BI client or model registry client, so these
# actions are proposals for an operator or a downstream pipeline.
ADVISORY_ACTIONS = (
    ActionType.DBT_RERUN,
    ActionType.DASHBOARD_INVALIDATE,
    ActionType.ML_MODEL_ANNOTATE,
)

ADVISORY_NOTE = (
    "advisory action - requires external system (dbt/BI/ML platform); not executed by Vismriti"
)


class Executor:
    def __init__(self, dry_run: bool | None = None) -> None:
        self.dry_run = settings.dry_run if dry_run is None else dry_run

    def execute(self, action: PlannedAction) -> None:
        if not action.approved:
            raise RuntimeError(f"Cannot execute unapproved action for {action.asset.urn}")
        if action.is_residual:
            return

        if action.action_type in SQL_ACTIONS:
            self._execute_sql(action)
            return

        if action.action_type in ADVISORY_ACTIONS:
            action.advisory = True
            action.executed = False
            action.execution_error = None
            logger.info("%s: %s", action.asset.urn, ADVISORY_NOTE)
            return

        action.executed = False
        action.execution_error = f"no executor for action type '{action.action_type.value}'"

    def _execute_sql(self, action: PlannedAction) -> None:
        if action.sql is None:
            action.executed = False
            action.execution_error = "No SQL to execute"
            return
        if self.dry_run:
            # Nothing is sent. dry_run is stamped so no reader of this action
            # can mistake a rendered statement for a changed row.
            action.dry_run = True
            action.executed = True
            return
        try:
            self._run_sql(action.sql)
            action.dry_run = False
            action.executed = True
        except Exception as exc:
            action.executed = False
            action.execution_error = str(exc)
            logger.exception("SQL execution failed for %s", action.asset.urn)

    def _run_sql(self, sql: str) -> None:
        import psycopg2  # type: ignore

        conn = psycopg2.connect(settings.pg_dsn())
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        finally:
            conn.close()
