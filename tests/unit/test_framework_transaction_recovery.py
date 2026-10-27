import pytest

from bestseller.infra.db import session as db_session
from bestseller.infra.db.models import RewriteTaskModel, ChapterModel
from bestseller.services import reviews
from test_wp4_integration_closure import db, seed, SQLiteSession  # noqa: F401 - shared real SQLite fixture

pytestmark = pytest.mark.unit


class ClosableSQLiteSession(SQLiteSession):
    async def close(self):
        self.orm.close()


@pytest.mark.asyncio
async def test_shared_transaction_owner_preserves_only_rejection_after_rollback(db, monkeypatch):
    chapter, _, draft, task = await seed(db)
    sample = db()
    engine = sample.orm.get_bind()
    sample.orm.close()
    monkeypatch.setattr(db_session, "_global_session_factory", lambda: ClosableSQLiteSession(engine))
    with pytest.raises(reviews.RewriteSourceRejected):
        async with db_session.get_server_session() as session:
            row = await session.get(RewriteTaskModel, task.id)
            other = await session.get(ChapterModel, chapter.id)
            other.chapter_goal = "This unrelated change must roll back"
            raise reviews._reject_rewrite_source(row,"invalid source binding",reason="test")
    async with db() as session:
        row = await session.get(RewriteTaskModel, task.id)
        assert row.status == "stale" and row.attempts == 2
        assert len(row.metadata_json["source_binding_rejections"]) == 1
        assert (await session.get(ChapterModel, chapter.id)).chapter_goal == "Original goal"
