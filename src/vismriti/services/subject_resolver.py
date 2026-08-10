"""Resolve an input identifier (usually email) into internal IDs.

email -> primary id + email_hash. The primary id comes from a parameterised
lookup against the PII-tagged source table; the hash is deterministic so
hash-keyed derived tables can be matched without the plaintext address.
"""

from __future__ import annotations

import hashlib
import logging

from ..core.models import PIIColumn, SubjectIdentifiers
from ..utils.config import settings
from .planner import is_safe_identifier, quote_identifier, resolve_id_column

logger = logging.getLogger(__name__)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.lower().strip().encode("utf-8")).hexdigest()


def resolve_subject(
    email: str,
    pii_columns: list[PIIColumn],
    fixture_id: int | None = None,
) -> SubjectIdentifiers:
    """Resolve subject identifiers.

    Live path: pick the first email-tagged column, query its dataset for the
    matching primary key. Fixture path: return a canned id for repeatability.
    A failed lookup leaves primary_id as None, which the planner treats as
    "not safe to generate a destructive statement".
    """
    email_hash = _sha256_hex(email)

    if fixture_id is not None:
        return SubjectIdentifiers(
            input_email=email,
            primary_id=fixture_id,
            email_hash=email_hash,
        )

    email_col = next((c for c in pii_columns if c.pii_type == "email"), None)
    if email_col is None:
        logger.warning("No email-tagged PII column found; subject id unresolved")
        return SubjectIdentifiers(input_email=email, email_hash=email_hash)

    return SubjectIdentifiers(
        input_email=email,
        primary_id=_lookup_primary_id(email_col, email),
        email_hash=email_hash,
    )


def table_from_urn(dataset_urn: str) -> str:
    """Extract the table name from a DataHub dataset URN.

    urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.raw.patients,PROD)
    -> healthcare.raw.patients
    """
    if "," in dataset_urn:
        return dataset_urn.split(",")[-2].strip()
    return dataset_urn.strip()


def _lookup_primary_id(pii_col: PIIColumn, email: str) -> int | str | None:
    """Look up the subject's primary id in Postgres.

    Returns None when the subject is absent, the identifiers are unsafe, or
    the database is unreachable. The last two are logged so an outage is not
    silently reported as "subject not found".
    """
    table = table_from_urn(pii_col.dataset_urn)
    id_column = resolve_id_column(table)
    email_column = pii_col.column_name

    if not is_safe_identifier(table):
        logger.error("Subject lookup skipped: unsafe table identifier %r", table)
        return None
    for column in (id_column, email_column):
        if not is_safe_identifier(column, allow_qualified=False):
            logger.error("Subject lookup skipped: unsafe column identifier %r", column)
            return None

    sql = (
        f"SELECT {quote_identifier(id_column)} FROM {quote_identifier(table)} "
        f"WHERE {quote_identifier(email_column)} = %s LIMIT 1"
    )

    try:
        import psycopg2  # type: ignore

        conn = psycopg2.connect(settings.pg_dsn())
    except Exception:
        logger.exception("Subject lookup failed: cannot connect to Postgres")
        return None

    try:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        logger.exception("Subject lookup query failed against %s", table)
        return None
    finally:
        conn.close()
