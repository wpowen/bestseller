"""Guard against catastrophic bulk project deletion.

The book library was wiped three times (2026-07-14, 07-21, 07-23), every time
with the same signature: a cascade ``DELETE FROM projects`` against the
production database that deleted every project + chapter and orphaned all
``llm_runs`` (their FK is ``ON DELETE SET NULL``). The application never bulk-
deletes projects — the real delete flow removes exactly one project by slug —
so any statement that removes several at once is an accident (a test or script
pointed at production, a stray ``DELETE FROM projects``).

This installs a database-level backstop that no client can bypass by accident:
a statement-level trigger that aborts a DELETE removing more than
``_MAX_PROJECTS_PER_DELETE`` rows, and a BEFORE TRUNCATE trigger that blocks
truncation outright. A legitimate bulk operation must opt in explicitly with
``SET LOCAL bestseller.allow_bulk_delete = 'on'`` — so intent is required, and
an accident (which never sets it) is stopped and rolled back.

Postgres-only: SQLite test runs skip it (no PL/pgSQL). Real bulk cleanups in
integration tests set the GUC or stay under the threshold.
"""

from __future__ import annotations

from alembic import op

revision = "0037_guard_bulk_project_delete"
down_revision = "0036_conception_snapshot_idempotency"
branch_labels = None
depends_on = None

# The single-project delete flow removes 1 row; small test fixtures remove a
# handful. A real wipe removes the whole library. 5 clears normal use with
# margin and still stops any mass delete.
_MAX_PROJECTS_PER_DELETE = 5

_UPGRADE_SQL = f"""
CREATE OR REPLACE FUNCTION bestseller_guard_bulk_project_delete()
RETURNS trigger AS $$
DECLARE
    deleted_count integer;
BEGIN
    -- Explicit opt-in escape hatch for intentional bulk operations.
    IF current_setting('bestseller.allow_bulk_delete', true) = 'on' THEN
        RETURN NULL;
    END IF;
    SELECT count(*) INTO deleted_count FROM deleted_projects;
    IF deleted_count > {_MAX_PROJECTS_PER_DELETE} THEN
        RAISE EXCEPTION
            'Bulk project delete blocked: % projects in one statement exceeds '
            'the safety limit of {_MAX_PROJECTS_PER_DELETE}. This is the '
            'signature of the 2026-07 library wipes. If this is intentional, '
            'run: SET LOCAL bestseller.allow_bulk_delete = ''on''; first.',
            deleted_count;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS guard_bulk_project_delete ON projects;
CREATE TRIGGER guard_bulk_project_delete
    AFTER DELETE ON projects
    REFERENCING OLD TABLE AS deleted_projects
    FOR EACH STATEMENT
    EXECUTE FUNCTION bestseller_guard_bulk_project_delete();

CREATE OR REPLACE FUNCTION bestseller_guard_truncate_projects()
RETURNS trigger AS $$
BEGIN
    IF current_setting('bestseller.allow_bulk_delete', true) = 'on' THEN
        RETURN NULL;
    END IF;
    RAISE EXCEPTION
        'TRUNCATE projects blocked: this wipes the whole library. If '
        'intentional, run: SET bestseller.allow_bulk_delete = ''on''; first.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS guard_truncate_projects ON projects;
CREATE TRIGGER guard_truncate_projects
    BEFORE TRUNCATE ON projects
    FOR EACH STATEMENT
    EXECUTE FUNCTION bestseller_guard_truncate_projects();
"""

_DOWNGRADE_SQL = """
DROP TRIGGER IF EXISTS guard_bulk_project_delete ON projects;
DROP TRIGGER IF EXISTS guard_truncate_projects ON projects;
DROP FUNCTION IF EXISTS bestseller_guard_bulk_project_delete();
DROP FUNCTION IF EXISTS bestseller_guard_truncate_projects();
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # PL/pgSQL triggers are Postgres-only; SQLite test DBs skip the guard.
        return
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(_DOWNGRADE_SQL)
