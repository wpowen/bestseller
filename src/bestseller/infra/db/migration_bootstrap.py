"""Greenfield bootstrap helpers for the Alembic migration chain.

Migration ``0001`` renders the *entire current* SQLAlchemy metadata (every
table that exists in the models today) via ``render_schema_statements``. Later
migrations (0002+) then ``op.create_table`` some of those same tables, so on a
brand-new database ``alembic upgrade head`` builds everything in 0001 and then
aborts at the first later ``create_table`` with a DuplicateTable error.

The chain is therefore only valid for *evolving an existing* database. For a
greenfield database we build the current schema directly and stamp it at head,
which is the standard Alembic baseline pattern and keeps a fresh deploy working
while existing managed databases continue to upgrade incrementally.

These helpers are deliberately import-safe (no side effects) and free of the
Alembic ``config`` object so they can be unit-tested with a fake connection.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text


def database_is_greenfield(connection: Any) -> bool:
    """True for a fresh PostgreSQL DB with no app schema and no stamped revision.

    Uses ``information_schema`` (rather than the SQLAlchemy inspector) so the
    branching logic stays unit-testable with a simple fake connection.
    """

    if connection.dialect.name != "postgresql":
        return False
    has_projects = connection.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'projects' LIMIT 1"
        )
    ).scalar()
    if has_projects:
        return False
    current = connection.execute(
        text("SELECT version_num FROM alembic_version LIMIT 1")
    ).scalar()
    return current is None


def baseline_to_head(connection: Any, *, head_revision: str | None) -> None:
    """Create the full current schema and stamp it at ``head_revision``.

    Reuses the exact statements migration 0001 runs (extensions + tables +
    indexes), so the resulting schema is identical to replaying the chain —
    minus the self-collision. Data backfills in later migrations are no-ops on
    an empty database, so skipping them is safe.
    """

    from bestseller.infra.db.schema import render_schema_statements

    for statement in render_schema_statements():
        connection.execute(text(statement.rstrip(";")))
    if head_revision is not None:
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
            {"rev": head_revision},
        )
