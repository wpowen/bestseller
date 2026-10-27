"""Public window controls and workflow registration use real offline SQLite."""

from asyncio import CancelledError
from copy import deepcopy
import socket
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import stub_llm
from test_fanqie_segment_resume import forbid, resume, seed

from bestseller.infra.db.models import (
    AntagonistPlanModel,
    BookProductionControlModel,
    CanonFactModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
    CharacterModel,
    ClueModel,
    EmotionTrackModel,
    PlotArcModel,
    ProjectModel,
    QualityScoreModel,
    RelationshipEventModel,
    ReviewReportModel,
    TimelineEventModel,
    WorkflowRunModel,
)
from bestseller.services import continuity, feedback
from bestseller.services import execution_windows as windows
from bestseller.services.creation_configuration import ExecutionWindow
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def configured(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("offline SQLite regression cannot use the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(feedback, "complete_text", forbid)
    monkeypatch.setattr(continuity, "complete_text", forbid)
    path = tmp_path / "adopted_resume_control_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    assert engine.dialect.name == "sqlite" and "test" in path.name
    async with engine.begin() as connection:
        for model in (
            ProjectModel, ChapterModel, ChapterDraftVersionModel,
            ChapterStateSnapshotModel, CanonFactModel, TimelineEventModel,
            CharacterModel, PlotArcModel, ClueModel, AntagonistPlanModel,
            QualityScoreModel, ReviewReportModel, RelationshipEventModel,
            EmotionTrackModel, WorkflowRunModel, BookProductionControlModel,
        ):
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            project = ProjectModel(
                slug=f"resume-control-test-{uuid4().hex}", title="test", genre="test",
                status="writing", target_word_count=10000, target_chapters=1,
                metadata_json={"execution_scope_id": "project:preserved-test-scope"},
            )
            session.add(project)
            await session.flush()
            chapter = ChapterModel(
                project_id=project.id, chapter_number=1, chapter_goal="test", metadata_json={},
            )
            session.add(chapter)
            await session.flush()
            draft = ChapterDraftVersionModel(
                project_id=project.id, chapter_id=chapter.id, version_no=1,
                content_md="阿青把钥匙放在桌上。", promotion_state="promoted",
                is_current=True, promotion_metadata={},
            )
            session.add(draft)
            await session.commit()
            settings = load_settings().model_copy(deep=True)
            settings.pipeline.enable_chapter_feedback = True
            yield session, settings, project, chapter, draft
    finally:
        await engine.dispose()


@pytest.mark.parametrize("halted_status", ["paused", "cancelled", "archived"])
async def test_public_window_two_session_halt_preserves_feedback_and_stops_snapshot(
    configured, monkeypatch, halted_status,
):
    session, settings, project, chapter, draft = configured
    project_id, chapter_id, body = project.id, chapter.id, draft.content_md
    prior_scope = deepcopy(project.metadata_json)
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    monkeypatch.setattr(windows, "read_window", lambda _: ExecutionWindow(start=1, end=1))

    async def pause_in_other_session(current):
        # Match the real gateway's transaction release before another worker writes.
        await current.commit()
        async with factory() as operator:
            other = await operator.get(ProjectModel, project_id)
            other.status = halted_status
            await operator.commit()
        assert project.status == "writing", "reproduce the caller's stale ORM object"

    feedback_calls = stub_llm(monkeypatch, feedback, {}, pause_in_other_session)
    snapshot_calls = stub_llm(monkeypatch, continuity, {"facts": []})
    with pytest.raises(ValueError, match="adopted_knowledge_halted"):
        await windows.completed_window_resume(session, settings, project)
    async with factory() as observer:
        stored_project = await observer.get(ProjectModel, project_id)
        stored_chapter = await observer.get(ChapterModel, chapter_id)
        receipts = deepcopy(stored_chapter.metadata_json["knowledge_feedback_receipts"])
        runs = list(await observer.scalars(select(WorkflowRunModel)))
        assert stored_project.status == halted_status
        assert stored_project.metadata_json == prior_scope
        assert receipts and len(feedback_calls) == 1 and snapshot_calls == []
        assert len(runs) == 1 and runs[0].status == "machine_blocked"
        assert runs[0].workflow_type == "execution_window_knowledge_resume"
        assert runs[0].metadata_json["knowledge_resume_progress"]["feedback_complete"]
        assert not list(await observer.scalars(select(ChapterStateSnapshotModel)))
    for row in (project, chapter, draft):
        await session.refresh(row)
    assert chapter.metadata_json["knowledge_feedback_receipts"] == receipts
    assert draft.content_md == body
    state = await windows.window_progress(session, project)
    assert not state["window_complete"] and state["knowledge_closed"] == []


async def test_caller_guard_remains_additional_and_propagates_original_error(configured):
    error = ValueError("window_scope_guard")
    calls = []

    async def caller_guard():
        calls.append(True)
        raise error

    with pytest.raises(ValueError) as caught:
        await resume(configured, assert_runnable=caller_guard)
    assert caught.value is error and calls == [True]
    assert not list(await configured[0].scalars(select(WorkflowRunModel)))


async def test_run_control_cannot_be_bypassed_by_noop_caller_guard(configured):
    session, _, project, _, _ = configured
    session.add(BookProductionControlModel(project_id=project.id, desired_state="stop"))
    await session.commit()
    calls = []

    async def caller_guard():
        calls.append(True)

    with pytest.raises(ValueError, match="adopted_knowledge_halted"):
        await resume(configured, assert_runnable=caller_guard)
    assert calls == [] and not list(await session.scalars(select(WorkflowRunModel)))


@pytest.mark.parametrize("failure", ["cancel", "error"])
@pytest.mark.parametrize(
    "boundary", ["before_flush", "after_flush", "before_commit", "after_commit"],
)
async def test_registration_failure_only_updates_exact_durable_owned_workflow(
    configured, monkeypatch, failure, boundary,
):
    await seed(configured, monkeypatch, snapshot_done=False)
    session, _, project, chapter, _ = configured
    project_id, chapter_id = project.id, chapter.id
    prior_receipts = deepcopy(chapter.metadata_json["knowledge_feedback_receipts"])
    prior_scope = deepcopy(project.metadata_json)
    old_id = uuid4()
    old_metadata = {"old_receipt": {"status": "completed", "raw": "preserve"}}
    session.add(WorkflowRunModel(
        id=old_id, project_id=project_id, scope_type="chapter", scope_id=chapter_id,
        workflow_type="adopted_knowledge_resume", status="completed", metadata_json=old_metadata,
    ))
    await session.commit()
    monkeypatch.setattr(feedback, "complete_text", forbid)
    monkeypatch.setattr(continuity, "complete_text", forbid)
    error = (
        CancelledError("registration cancelled")
        if failure == "cancel" else RuntimeError("registration failed")
    )
    phase, method = boundary.split("_")
    original = getattr(session, method)
    interrupted = []

    async def interrupt_once(*args, **kwargs):
        if interrupted:
            return await original(*args, **kwargs)
        interrupted.append(True)
        if phase == "before":
            raise error
        await original(*args, **kwargs)
        raise error

    monkeypatch.setattr(session, method, interrupt_once)
    with pytest.raises(type(error)) as caught:
        await resume(configured)
    assert caught.value is error and interrupted == [True]
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    async with factory() as observer:
        runs = list(await observer.scalars(select(WorkflowRunModel)))
        old = next(row for row in runs if row.id == old_id)
        assert old.status == "completed" and old.metadata_json == old_metadata
        new = [row for row in runs if row.id != old_id]
        if boundary == "after_commit":
            assert len(new) == 1
            assert new[0].status == ("cancelled" if failure == "cancel" else "machine_blocked")
            assert new[0].metadata_json["error_type"] == type(error).__name__
            assert new[0].project_id == project_id and new[0].scope_id == chapter_id
        else:
            assert new == []
        stored_chapter = await observer.get(ChapterModel, chapter_id)
        stored_project = await observer.get(ProjectModel, project_id)
        assert stored_chapter.metadata_json["knowledge_feedback_receipts"] == prior_receipts
        assert stored_project.metadata_json == prior_scope
        assert not list(await observer.scalars(select(ChapterStateSnapshotModel)))
    # Failed uncommitted registration must be rolled back, not left pending.
    assert not session.new
    assert len(list(await session.scalars(select(WorkflowRunModel)))) == len(runs)
