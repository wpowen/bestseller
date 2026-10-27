"""Real NOT NULL flush failures must survive ORM expiration and outer rollback."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData, event, func, inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.domain.enums import WorkflowStatus
from bestseller.infra.db import session as db_session
from bestseller.infra.db.models import ChapterModel, ProjectModel, WorkflowRunModel
from bestseller.services.execution_stage import attach_workflow_failure
from bestseller.services.workflows import create_workflow_run

pytestmark = pytest.mark.unit


@pytest.fixture
async def database(tmp_path):
    path = tmp_path / "workflow_flush_failure_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def create_tables(connection):
        for model in (ProjectModel, ChapterModel, WorkflowRunModel):
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))

    async with engine.begin() as connection:
        await connection.run_sync(create_tables)
    project_id, other_project_id, chapter_id, other_run_id = (uuid4() for _ in range(4))
    original_metadata = {"author_fact": "preserved", "retry_chain": {"used": 3}}
    async with factory() as session:
        for project, slug in ((project_id, "quiet-harbor"), (other_project_id, "distant-orbit")):
            session.add(
                ProjectModel(
                    id=project,
                    slug=slug,
                    title=slug,
                    genre="custom",
                    target_word_count=7800,
                    target_chapters=3,
                    status="drafting",
                    metadata_json=deepcopy(original_metadata),
                )
            )
        session.add(
            ChapterModel(
                id=chapter_id,
                project_id=project_id,
                chapter_number=2,
                chapter_goal="Return the map",
                title="Original chapter",
                production_state="ok",
                metadata_json={"author_note": "preserved"},
            )
        )
        session.add(
            WorkflowRunModel(
                id=other_run_id,
                project_id=project_id,
                workflow_type="chapter_pipeline",
                status="running",
                current_step="unrelated",
                metadata_json={"keep": True},
            )
        )
        await session.commit()
    yield SimpleNamespace(
        engine=engine,
        factory=factory,
        project_id=project_id,
        other_project_id=other_project_id,
        chapter_id=chapter_id,
        other_run_id=other_run_id,
        original_metadata=original_metadata,
    )
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["server", "one_shot"])
@pytest.mark.parametrize("existing", [False, True], ids=["new", "existing-authoritative-owner"])
async def test_real_flush_failure_is_automatically_persisted(database, monkeypatch, kind, existing):
    rig = database
    if kind == "server":
        monkeypatch.setattr(db_session, "_global_session_factory", rig.factory)
        context = db_session.get_server_session
    else:
        monkeypatch.setattr(db_session, "create_engine", lambda _settings: rig.engine)
        monkeypatch.setattr(db_session, "create_session_factory", lambda **_kw: rig.factory)
        context = db_session.session_scope
    target_id = uuid4()
    if existing:
        async with rig.factory() as session:
            session.add(
                WorkflowRunModel(
                    id=target_id,
                    project_id=rig.project_id,
                    workflow_type="chapter_pipeline",
                    status="running",
                    current_step="rewrite_chapter_v1",
                    idempotency_key="existing-test-key",
                    metadata_json={"keep": "original"},
                )
            )
            await session.commit()

    original_error = None
    with pytest.raises(IntegrityError) as caught:
        async with context() as session:
            # A reused row's identity must not come from these incoming values.
            run = await create_workflow_run(
                session,
                project_id=rig.other_project_id if existing else rig.project_id,
                workflow_type="chapter_pipeline",
                status=WorkflowStatus.RUNNING,
                scope_type="chapter",
                scope_id=rig.chapter_id,
                requested_by="offline-test",
                current_step="creation_step_must_not_replace_committed_step",
                metadata={"keep": "incoming"},
                idempotency_key="existing-test-key" if existing else None,
            )
            if existing:
                assert run.id == target_id
            else:
                target_id = run.id
                run.current_step = "rewrite_chapter_v1"
            snapshot = deepcopy(run.__dict__["_failure_identity"])
            assert snapshot == {
                "id": target_id,
                "project_id": rig.project_id,
                "workflow_type": "chapter_pipeline",
            }
            assert "current_step" not in snapshot
            assert "_failure_identity" not in run.metadata_json
            await session.commit()  # The actual durable checkpoint before failure.
            chapter = await session.get(ChapterModel, rig.chapter_id)
            project = await session.get(ProjectModel, rig.project_id)
            project.title = "Uncommitted business edit"
            chapter.production_state = None
            try:
                await session.flush()  # Real database constraint failure, not a fake exception.
            except IntegrityError as error:
                original_error = error
                assert "production_state" in str(error.orig)
                assert not session.is_active
                assert inspect(run).expired_attributes
                assert (
                    not {"id", "project_id", "workflow_type", "current_step"} & run.__dict__.keys()
                )
                assert run.__dict__["_failure_identity"] == snapshot
                statements = []

                def observe(*_args):
                    statements.append(True)

                event.listen(rig.engine.sync_engine, "before_cursor_execute", observe)
                try:
                    attach_workflow_failure(error, run)
                finally:
                    event.remove(rig.engine.sync_engine, "before_cursor_execute", observe)
                assert not statements  # No ORM reload from the broken session.
                assert error.workflow_failures == (
                    {
                        "workflow_id": str(target_id),
                        "project_id": str(rig.project_id),
                        "workflow_type": "chapter_pipeline",
                        "step": None,
                        "code": "workflow_failed",
                        "error_type": "IntegrityError",
                        "recoverable": True,
                    },
                )
                raise
    assert caught.value is original_error
    # First independent read: no manual recovery call may hide a broken automatic chain.
    async with rig.factory() as reader:
        saved = await reader.get(WorkflowRunModel, target_id)
        assert saved.status == "failed" and saved.current_step == "rewrite_chapter_v1"
        assert saved.error_message == "workflow_failed"
        assert saved.metadata_json["execution_failure_receipts"] == list(
            original_error.workflow_failures
        )
        assert "_failure_identity" not in saved.__dict__
        assert "_failure_identity" not in saved.metadata_json
        book = await reader.get(ProjectModel, rig.project_id)
        assert book.title == "quiet-harbor" and book.status == "drafting"
        assert book.metadata_json == rig.original_metadata
        other_book = await reader.get(ProjectModel, rig.other_project_id)
        assert other_book.metadata_json == rig.original_metadata
        other = await reader.get(WorkflowRunModel, rig.other_run_id)
        assert other.status == "running" and other.current_step == "unrelated"
        assert other.metadata_json == {"keep": True}
        preserved = await reader.get(ChapterModel, rig.chapter_id)
        assert preserved.production_state == "ok"
        assert preserved.title == "Original chapter"
        assert preserved.metadata_json == {"author_note": "preserved"}
        assert await reader.scalar(select(func.count()).select_from(WorkflowRunModel)) == 2


def test_known_plain_workflow_identity_remains_compatible():
    workflow = SimpleNamespace(
        id=uuid4(), project_id=uuid4(), workflow_type="plain-test", current_step="original-step"
    )
    error = RuntimeError("ordinary failure")
    attach_workflow_failure(error, workflow)
    attach_workflow_failure(error, workflow)
    assert len(error.workflow_failures) == 1
    assert error.workflow_failures[0]["step"] == "original-step"


@pytest.mark.parametrize(
    "snapshot", [None, {}, [], "invalid", {"id": uuid4(), "workflow_type": "missing-project"}]
)
def test_missing_snapshot_fields_do_not_guess_a_project(snapshot):
    error = RuntimeError("unknown identity")
    attach_workflow_failure(error, SimpleNamespace(_failure_identity=snapshot))
    assert not hasattr(error, "workflow_failures")


@pytest.mark.asyncio
async def test_expired_snapshot_must_match_its_own_orm_identity(database):
    async with database.factory() as session:
        run = await create_workflow_run(
            session,
            project_id=database.project_id,
            workflow_type="chapter_pipeline",
            status=WorkflowStatus.RUNNING,
            scope_type="chapter",
            scope_id=database.chapter_id,
            requested_by="offline-test",
        )
        await session.commit()
        snapshot = deepcopy(run.__dict__["_failure_identity"])
        session.expire(run)
        run.__dict__["_failure_identity"] = {**snapshot, "id": uuid4()}
        error = RuntimeError("foreign snapshot")
        attach_workflow_failure(error, run)
        assert not hasattr(error, "workflow_failures")
