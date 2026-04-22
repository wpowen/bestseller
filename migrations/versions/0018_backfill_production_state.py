"""Backfill ``chapters.production_state`` for pre-Phase-1 rows.

Migration 0016 added ``production_state`` with ``server_default='pending'``.
Every chapter inserted before Phase 1 therefore reports ``pending`` even
when a current draft exists — which makes dashboards misread finished
novels as untouched.

This migration updates those rows in place using evidence already in the
database:

* ``ok``     — at least one ``chapter_draft_versions`` row with
               ``is_current=true`` and non-empty ``content_md``
* ``blocked``— a ``chapter_quality_reports.blocks_write=true`` row exists
               for the chapter (picks the most recent)
* ``pending``— otherwise (untouched)

The update runs in a single SQL statement per target state so it stays
fast even for projects with hundreds of chapters; transactional so a
partial run leaves no half-migrated data.
"""

from __future__ import annotations

from alembic import op


revision = "0018_backfill_production_state"
down_revision = "0017_commercial_anchors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pre-Phase-1 novels: chapters that already shipped content to a
    # current draft version are considered "ok" — the write-gate didn't
    # exist yet, but by definition they didn't get blocked either.
    op.execute(
        """
        UPDATE chapters
        SET production_state = 'ok'
        WHERE production_state = 'pending'
          AND id IN (
              SELECT chapter_id
              FROM chapter_draft_versions
              WHERE is_current = TRUE
                AND content_md IS NOT NULL
                AND char_length(content_md) > 0
          )
        """
    )

    # Any chapter with a persisted blocking quality report outranks the
    # draft-exists signal — those really were blocked by a later run.
    op.execute(
        """
        UPDATE chapters
        SET production_state = 'blocked'
        WHERE id IN (
            SELECT DISTINCT chapter_id
            FROM chapter_quality_reports
            WHERE blocks_write = TRUE
        )
        """
    )


def downgrade() -> None:
    # Reset every chapter back to 'pending' — the column existed before this
    # migration (0016) so we only revert the derived values, not the schema.
    op.execute(
        "UPDATE chapters SET production_state = 'pending'"
    )
