"""Add FK indexes needed for fast cascaded deletes.

Large projects can have thousands of workflow, review, score, draft, export,
LLM, and rewrite rows. PostgreSQL needs indexes on child FK columns to enforce
ON DELETE CASCADE / SET NULL without scanning large tables for every parent
row touched during project deletion.
"""

from __future__ import annotations

from alembic import op


revision = "0029_project_delete_fk_indexes"
down_revision = "0028_interpersonal_promises"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
          r record;
          idx_name text;
          col_list text;
        BEGIN
          FOR r IN
            WITH fks AS (
              SELECT
                c.conrelid::regclass AS table_name,
                c.conname,
                array_agg(att.attname ORDER BY u.ord) AS cols
              FROM pg_constraint c
              JOIN unnest(c.conkey) WITH ORDINALITY AS u(attnum, ord) ON true
              JOIN pg_attribute att
                ON att.attrelid = c.conrelid
               AND att.attnum = u.attnum
              WHERE c.contype = 'f'
              GROUP BY c.conrelid, c.conname
            ),
            idx AS (
              SELECT
                i.indrelid::regclass AS table_name,
                array_agg(a.attname ORDER BY u.ord) AS cols
              FROM pg_index i
              JOIN unnest(i.indkey) WITH ORDINALITY AS u(attnum, ord) ON true
              JOIN pg_attribute a
                ON a.attrelid = i.indrelid
               AND a.attnum = u.attnum
              WHERE i.indisvalid
              GROUP BY i.indexrelid, i.indrelid
            )
            SELECT f.table_name, f.cols
            FROM fks f
            WHERE NOT EXISTS (
              SELECT 1
              FROM idx
              WHERE idx.table_name = f.table_name
                AND idx.cols[1:array_length(f.cols, 1)] = f.cols
            )
          LOOP
            idx_name := 'ix_fk_'
              || replace(r.table_name::text, '.', '_')
              || '_'
              || array_to_string(r.cols, '_');
            IF length(idx_name) > 60 THEN
              idx_name := left(idx_name, 45) || '_' || substr(md5(idx_name), 1, 12);
            END IF;
            SELECT string_agg(format('%I', c), ', ')
              INTO col_list
              FROM unnest(r.cols) AS c;
            EXECUTE format(
              'CREATE INDEX IF NOT EXISTS %I ON %s (%s)',
              idx_name,
              r.table_name,
              col_list
            );
          END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
          r record;
        BEGIN
          FOR r IN
            SELECT schemaname, indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname LIKE 'ix_fk_%'
          LOOP
            EXECUTE format('DROP INDEX IF EXISTS %I.%I', r.schemaname, r.indexname);
          END LOOP;
        END $$;
        """
    )
