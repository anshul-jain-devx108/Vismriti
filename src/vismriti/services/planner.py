"""Per-asset action planner.

Maps each discovered asset to exactly one erasure action and renders the SQL
for the destructive ones. The rules are deterministic so an auditor can trace
every generated statement back to the rule that produced it.

Identifiers reaching a template come from DataHub metadata and are therefore
untrusted: they are validated and quoted here, or the asset is downgraded to
a residual-review item instead of emitting SQL.
"""

from __future__ import annotations

import os
import re
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

# Only bare, optionally dotted SQL identifiers are accepted. Everything else
# (quotes, spaces, semicolons, comment markers, unicode) is rejected.
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

# A segment Postgres will fold to exactly itself, so quoting is a no-op.
_FOLD_SAFE_SEGMENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# Hash we are willing to use as a row predicate. A short or non-hex value is
# not a digest and could match rows belonging to a different subject.
_HASH_RE = re.compile(r"^[0-9a-fA-F]{32,128}$")

# Postgres keywords that must be quoted to be usable as identifiers.
_RESERVED_WORDS = frozenset({
    "all", "analyse", "analyze", "and", "any", "array", "as", "asc", "asymmetric",
    "authorization", "binary", "both", "case", "cast", "check", "collate", "collation", "column",
    "concurrently", "constraint", "create", "cross", "current_catalog", "current_date",
    "current_role", "current_schema", "current_time", "current_timestamp", "current_user",
    "default", "deferrable", "desc", "distinct", "do", "else", "end", "except", "false", "fetch",
    "for", "foreign", "freeze", "from", "full", "grant", "group", "having", "ilike", "in",
    "initially", "inner", "intersect", "into", "is", "isnull", "join", "lateral", "leading",
    "left", "like", "limit", "localtime", "localtimestamp", "natural", "not", "notnull", "null",
    "offset", "on", "only", "or", "order", "outer", "overlaps", "placing", "primary",
    "references", "returning", "right", "select", "session_user", "similar", "some", "symmetric",
    "system_user", "table", "tablesample", "then", "to", "trailing", "true", "union", "unique",
    "user", "using", "variadic", "verbose", "when", "where", "window", "with",
})

_DEFAULT_ID_COLUMN_MAP = "patient:patient_id"
_FALLBACK_ID_COLUMN = "user_id"


def is_safe_identifier(name: object, *, allow_qualified: bool = True) -> bool:
    """True if name can be safely placed in generated SQL."""
    if not isinstance(name, str) or IDENTIFIER_RE.match(name) is None:
        return False
    return allow_qualified or "." not in name


def quote_identifier(name: str) -> str:
    """Quote a possibly dotted identifier for Postgres, one pair per segment.

    Segments that Postgres already folds to themselves (plain lowercase, not a
    keyword) are left bare so the generated SQL stays readable; the result is
    the same relation either way.
    """
    if not is_safe_identifier(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return ".".join(_quote_segment(segment) for segment in name.split("."))


def _quote_segment(segment: str) -> str:
    if _FOLD_SAFE_SEGMENT_RE.match(segment) and segment not in _RESERVED_WORDS:
        return segment
    return '"' + segment.replace('"', '""') + '"'


def sql_literal(value: object) -> str:
    """Render a Python value as a Postgres literal."""
    if value is None:
        raise ValueError("refusing to render NULL as a subject identifier")
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _id_column_rules() -> list[tuple[str, str]]:
    """Parse VISMRITI_ID_COLUMN_MAP ("substring:column,substring:column")."""
    raw = os.getenv("VISMRITI_ID_COLUMN_MAP")
    if raw is None or not raw.strip():
        raw = _DEFAULT_ID_COLUMN_MAP
    rules: list[tuple[str, str]] = []
    for entry in raw.split(","):
        substring, sep, column = entry.partition(":")
        if not sep:
            continue
        substring, column = substring.strip().lower(), column.strip()
        if substring and column:
            rules.append((substring, column))
    return rules


def resolve_id_column(asset_name: str) -> str:
    """Pick the column holding the subject id for an asset.

    First matching substring in VISMRITI_ID_COLUMN_MAP wins; otherwise
    VISMRITI_DEFAULT_ID_COLUMN (default "user_id") is used.
    """
    lowered = (asset_name or "").lower()
    for substring, column in _id_column_rules():
        if substring in lowered:
            return column
    return os.getenv("VISMRITI_DEFAULT_ID_COLUMN", "").strip() or _FALLBACK_ID_COLUMN


def _usable_hash(value: str | None) -> str | None:
    """Return the hash only if it is a hex digest of at least 32 characters."""
    if isinstance(value, str) and _HASH_RE.match(value.strip()):
        return value.strip()
    return None


def _first_unsafe(names: list[str], *, allow_qualified: bool = True) -> str | None:
    for name in names:
        if not is_safe_identifier(name, allow_qualified=allow_qualified):
            return name
    return None


def _render(template_name: str, **ctx: object) -> str:
    return _env.get_template(template_name).render(**ctx).strip()


def _residual(asset: Asset, reason: str) -> PlannedAction:
    return PlannedAction(
        asset=asset,
        action_type=ActionType.RESIDUAL_REVIEW,
        reason=reason,
        is_residual=True,
    )


def _is_source(asset: Asset) -> bool:
    """A source asset carries PII columns itself (depth=0 in traversal)."""
    return asset.depth == 0 and bool(asset.pii_columns)


def _is_derived_dataset(asset: Asset) -> bool:
    return asset.asset_type == AssetType.DATASET and asset.depth > 0


def _is_orphan(asset: Asset) -> bool:
    """No owner + no PII tag + downstream of source = residual risk.

    This is the class of asset a static PII catalog misses: derived tables an
    analyst forked into a sandbox, no tags propagated, no owner set.
    """
    return not asset.owners and not asset.pii_columns and asset.depth > 0


def _plan_anonymize(asset: Asset, subject: SubjectIdentifiers) -> PlannedAction:
    pii_columns = list(dict.fromkeys(c.column_name for c in asset.pii_columns))
    id_column = resolve_id_column(asset.name)

    unsafe = _first_unsafe([asset.name]) or _first_unsafe(
        [id_column, *pii_columns], allow_qualified=False
    )
    if unsafe is not None:
        return _residual(
            asset,
            f"Refusing to build SQL: {unsafe!r} is not a valid SQL identifier. "
            "Fix the name in DataHub or handle this asset manually.",
        )

    if subject.primary_id is None:
        return _residual(
            asset,
            "Subject id could not be resolved, so the UPDATE has no safe row filter. "
            "Resolve the subject id or anonymize this table manually.",
        )

    sql = _render(
        "anonymize_source.sql.j2",
        table=quote_identifier(asset.name),
        pii_columns=[quote_identifier(c) for c in pii_columns],
        id_column=quote_identifier(id_column),
        subject_id=sql_literal(subject.primary_id),
    )
    return PlannedAction(
        asset=asset,
        action_type=ActionType.ANONYMIZE_ROW,
        sql=sql,
        reason=(
            f"Source table with {len(pii_columns)} PII column(s). "
            "Null out PII, retain row for FK integrity."
        ),
    )


def _plan_delete(asset: Asset, subject: SubjectIdentifiers) -> PlannedAction:
    id_column = resolve_id_column(asset.name)
    hash_column = f"{id_column}_hash"

    unsafe = _first_unsafe([asset.name]) or _first_unsafe(
        [id_column, hash_column], allow_qualified=False
    )
    if unsafe is not None:
        return _residual(
            asset,
            f"Refusing to build SQL: {unsafe!r} is not a valid SQL identifier. "
            "Fix the name in DataHub or handle this asset manually.",
        )

    predicates: list[str] = []
    if subject.primary_id is not None:
        predicates.append(f"{quote_identifier(id_column)} = {sql_literal(subject.primary_id)}")
    subject_hash = _usable_hash(subject.email_hash)
    if subject_hash is not None:
        predicates.append(f"{quote_identifier(hash_column)} = {sql_literal(subject_hash)}")

    if not predicates:
        return _residual(
            asset,
            "Neither a subject id nor a usable email hash is available, so a DELETE would "
            "have no safe row filter. Resolve the subject before deleting.",
        )

    sql = _render(
        "delete_derived.sql.j2",
        table=quote_identifier(asset.name),
        predicates=predicates,
    )
    return PlannedAction(
        asset=asset,
        action_type=ActionType.DELETE_ROW,
        sql=sql,
        reason="Derived table containing subject row - delete directly.",
    )


def plan_action(asset: Asset, subject: SubjectIdentifiers) -> PlannedAction:
    """Map one asset to one action.

    Order matters: more-specific rules before more-general ones.
    """

    # 1. Residual: no owner AND no tags AND downstream. Human must decide.
    if _is_orphan(asset):
        return _residual(
            asset,
            "No owner and no PII tag, but appears downstream of tagged sources. "
            "Static classification would miss this asset; agent flags for manual review.",
        )

    # 2. Source dataset with PII columns: anonymize in place.
    if _is_source(asset):
        return _plan_anonymize(asset, subject)

    # 3. Derived dataset: delete the subject's rows or (if dbt-managed) re-run.
    if _is_derived_dataset(asset):
        if "dbt" in (asset.platform or "").lower():
            if not is_safe_identifier(asset.name):
                return _residual(
                    asset,
                    f"Refusing to build a dbt command: {asset.name!r} is not a valid "
                    "model selector. Re-run this model manually.",
                )
            return PlannedAction(
                asset=asset,
                action_type=ActionType.DBT_RERUN,
                command=f"dbt run --select {asset.name}",
                reason="Derived dbt model - re-run after source anonymization propagates.",
            )
        return _plan_delete(asset, subject)

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

    return _residual(asset, f"Unhandled asset type '{asset.asset_type}' - manual review required.")


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
