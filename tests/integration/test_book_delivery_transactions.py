"""Real PG settlement/savepoint and adopted-knowledge recovery; fixture transport only."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel, ChapterModel, ExportArtifactModel, ProjectModel, RewriteTaskModel,
)
from bestseller.infra.db.session import create_engine
from bestseller.services import book_closure, continuity, exports, feedback, llm
from bestseller.services.adopted_knowledge_resume import resume_project_adopted_knowledge
from bestseller.services.book_delivery_readiness import read_book_delivery_closure

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def pg_book(postgresql_test_settings, tmp_path, monkeypatch):
    settings = postgresql_test_settings.model_copy(deep=True)
    settings.output.base_dir = tmp_path / "output"
    settings.pipeline.enable_chapter_feedback = True
    engine = create_engine(settings)
    calls = []

    async def fixture_completion(session, settings, request):
        assert not session.in_nested_transaction()
        calls.append(request)
        return SimpleNamespace(content='{"facts": []}', fallback_used=False,
                               provider="offline", model_name="fixture", llm_run_id=uuid4())

    monkeypatch.setattr(feedback, "complete_text", fixture_completion)
    monkeypatch.setattr(continuity, "complete_text", fixture_completion)
    monkeypatch.setattr(llm, "complete_text", fixture_completion)
    try:
        async with engine.connect() as connection:
            assert "test" in await connection.scalar(text("select current_database()"))
            await connection.rollback()
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
            try:
                project = ProjectModel(slug=f"delivery-pg-test-{uuid4().hex}", title="test", genre="test",
                    status="writing", target_word_count=10000, target_chapters=1,
                    metadata_json={"execution_scope_id": "test-original-scope", "retained_charge": 123})
                session.add(project)
                await session.flush()
                chapter = ChapterModel(project_id=project.id, chapter_number=1, chapter_goal="test",
                    status="complete", production_state="ok", metadata_json={})
                session.add(chapter)
                await session.flush()
                draft = ChapterDraftVersionModel(project_id=project.id, chapter_id=chapter.id, version_no=1,
                    content_md="阿青把钥匙放在桌上。", promotion_state="promoted", is_current=True,
                    assembled_from_scene_draft_ids=[str(uuid4())], promotion_metadata={})
                task = RewriteTaskModel(project_id=project.id, trigger_type="chapter_review",
                    rewrite_strategy="targeted", instructions="retain until delivery", status="pending",
                    context_required=[], metadata_json={})
                session.add_all([draft, task])
                await session.commit()
                yield session, settings, project, chapter, draft, task, calls
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_pg_knowledge_resume_closure_export_failure_and_recovery(pg_book, monkeypatch):
    session, settings, project, chapter, draft, task, calls = pg_book
    original_body = draft.content_md
    first = await resume_project_adopted_knowledge(session, settings, project=project)
    assert first[1]["knowledge"]["closed"]
    paid = len(calls)
    assert paid == 3
    await resume_project_adopted_knowledge(session, settings, project=project)
    assert len(calls) == paid
    original = exports.export_project_markdown

    async def unavailable(*args, **kwargs):
        # Write inside settlement's savepoint before I/O fails; it must roll back.
        task.status = "cancelled"
        await session.flush()
        raise OSError("fixture disk unavailable")

    monkeypatch.setattr(exports, "export_project_markdown", unavailable)
    failed = await book_closure.settle_project_status_on_closure(
        session, project, settings=settings, fallback_status="writing", now_iso="2026-10-22T15:00:00Z")
    await session.commit()
    await session.refresh(task)
    assert not failed.is_complete and failed.body_complete
    assert task.status == "pending" and project.status == "writing"
    assert not list(await session.scalars(select(ExportArtifactModel).where(ExportArtifactModel.project_id == project.id)))
    monkeypatch.setattr(exports, "export_project_markdown", original)
    delivered = await book_closure.settle_project_status_on_closure(
        session, project, settings=settings, fallback_status="writing", now_iso="2026-10-22T15:01:00Z")
    await session.commit()
    assert delivered.is_complete, project.metadata_json
    await session.refresh(task)
    assert task.status == "cancelled"
    assert (await read_book_delivery_closure(session, project)).verdict.is_complete
    assert Path(project.metadata_json["completion_export_path"]).read_text().count(original_body) == 1
    assert project.metadata_json["retained_charge"] == 123
    assert project.metadata_json["execution_scope_id"] == "test-original-scope"
    assert len(calls) == paid and draft.content_md == original_body
    # Readback is bound to the actual file, not a stored success flag.
    Path(project.metadata_json["completion_export_path"]).write_text("corrupt mirror")
    assert not (await read_book_delivery_closure(session, project)).verdict.is_complete
