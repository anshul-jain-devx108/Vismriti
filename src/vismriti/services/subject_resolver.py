"""Resolve an input identifier (usually email) into internal IDs.

For the healthcare demo:
    email -> patient_id, email_hash

Strategy:
    1. Ask DataHub for PII-tagged email columns.
    2. For each matching table, run a lookup query to find the internal id.
    3. Compute deterministic hash for hash-keyed derived tables.
"""

from __future__ import annotations

import hashlib

from ..utils.config import settings
from ..core.models import PIIColumn, SubjectIdentifiers


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.lower().strip().encode("utf-8")).hexdigest()


def resolve_subject(
    email: str,
    pii_columns: list[PIIColumn],
    fixture_id: int | None = None,
) -> SubjectIdentifiers:
    """Resolve subject identifiers.

    Live path: pick the first email-tagged column, query its dataset for
    the matching primary key.
    Fixture path: return a canned id for demo repeatability.
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
        return SubjectIdentifiers(input_email=email, email_hash=email_hash)

    primary_id = _lookup_primary_id(email_col, email)
    return SubjectIdentifiers(
        input_email=email,
        primary_id=primary_id,
        email_hash=email_hash,
    )


def _lookup_primary_id(pii_col: PIIColumn, email: str) -> int | None:
    """Best-effort lookup against Postgres. Returns None if unreachable."""
    try:
        import psycopg2  # type: ignore

        conn = psycopg2.connect(settings.pg_dsn())
        try:
            with conn.cursor() as cur:
                table = pii_col.dataset_urn.split(",")[-2] if "," in pii_col.dataset_urn else "patients"
                id_col = "patient_id" if "patient" in table.lower() else "user_id"
                cur.execute(
                    f"SELECT {id_col} FROM {table} WHERE {pii_col.column_name} = %s LIMIT 1",
                    (email,),
                )
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None
