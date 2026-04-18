"""Regression tests for SQLAlchemy ORM cascade / passive_deletes configuration.

Why this matters
----------------
Some of our child tables (e.g. ``scene_cards``) declare ``chapter_id`` as
``NOT NULL`` with a database-level ``ON DELETE CASCADE``.  If the ORM
relationship on the parent side is declared without ``passive_deletes=True``,
SQLAlchemy will try to be "safe" and emit an
``UPDATE <child> SET parent_id=NULL WHERE parent_id=<id>`` *before* deleting
the parent.  That UPDATE immediately crashes into the ``NOT NULL`` constraint,
rolls back the transaction, and poisons the session — taking every autowrite
job running on the shared engine down with it.

The fix is to tell the ORM to trust the database CASCADE:

    scenes = relationship(
        back_populates="chapter",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

These tests assert the required options are still set on the relationships we
care about, so nobody accidentally regresses the fix and reintroduces the
production outage.
"""

from __future__ import annotations

import pytest

from bestseller.infra.db.models import ChapterModel, SceneCardModel

pytestmark = pytest.mark.unit


def test_chapter_scenes_relationship_has_passive_deletes() -> None:
    """``ChapterModel.scenes`` must trust the DB ON DELETE CASCADE.

    Without ``passive_deletes=True`` the ORM issues ``UPDATE scene_cards
    SET chapter_id=NULL`` before deleting the parent chapter, which violates
    the NOT NULL constraint and poisons the whole session.
    """
    rel = ChapterModel.__mapper__.relationships["scenes"]

    assert rel.passive_deletes is True, (
        "ChapterModel.scenes must set passive_deletes=True — otherwise "
        "session.delete(chapter) tries to NULL out scene_cards.chapter_id, "
        "which violates the NOT NULL constraint and crashes the pipeline."
    )

    cascade_tokens = set(rel.cascade)
    assert "delete" in cascade_tokens, (
        "ChapterModel.scenes must cascade delete so session.delete(chapter) "
        "also removes its scene_cards via the DB cascade."
    )
    assert "delete-orphan" in cascade_tokens, (
        "ChapterModel.scenes must cascade delete-orphan so detaching a scene "
        "from its chapter removes it rather than leaving a dangling row."
    )


def test_scene_card_chapter_id_is_not_nullable() -> None:
    """If somebody relaxes chapter_id to NULLABLE the passive_deletes fix
    becomes unnecessary — but so does the regression test above.  Keep the two
    invariants in sync."""
    chapter_id_column = SceneCardModel.__table__.c["chapter_id"]
    assert chapter_id_column.nullable is False, (
        "scene_cards.chapter_id is expected to be NOT NULL; if this changes, "
        "revisit the passive_deletes configuration on ChapterModel.scenes."
    )
    # And the DB-level ON DELETE CASCADE must be present so passive_deletes is safe.
    assert any(
        fk.ondelete and fk.ondelete.upper() == "CASCADE"
        for fk in chapter_id_column.foreign_keys
    ), (
        "scene_cards.chapter_id must have ON DELETE CASCADE at the DB level; "
        "passive_deletes=True on the ORM side relies on the DB to clean up."
    )
