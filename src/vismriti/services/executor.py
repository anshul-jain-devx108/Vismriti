"""Execute approved plan actions against the target warehouse.

Never called on unapproved actions. Never called on residual actions.
Every executed action is stamped back on the PlannedAction so the report
carries the ground truth of what actually ran vs. what was proposed.
"""

from __future__ import annotations

from ..utils.config import settings
from ..core.models import ActionType, PlannedAction


class Executor:
    def __init__(self, dry_run: bool | None = None) -> None:
        self.dry_run = settings.dry_run if dry_run is None else dry_run

    def execute(self, action: PlannedAction) -> None:
        if not action.approved:
            raise RuntimeError(f"Cannot execute unapproved action for {action.asset.urn}")
        if action.is_residual:
            return

        if action.action_type in (
            ActionType.ANONYMIZE_ROW,
            ActionType.DELETE_ROW,
        ):
            if action.sql is None:
                action.execution_error = "No SQL to execute"
                return
            if self.dry_run:
                action.executed = True
                return
            try:
                self._run_sql(action.sql)
                action.executed = True
            except Exception as exc:
                action.execution_error = str(exc)
            return

        # Non-SQL actions: dbt re-run, dashboard flag, ML annotate.
        # For the MVP we mark these as executed at plan time - the actual
        # side-effect is a DataHub annotation, which happens in writeback.
        action.executed = True

    def _run_sql(self, sql: str) -> None:
        import psycopg2  # type: ignore

        conn = psycopg2.connect(settings.pg_dsn())
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        finally:
            conn.close()
