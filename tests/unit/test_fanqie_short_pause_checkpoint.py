"""Real two-session SQLite operator intent, checkpoints and outer dispatch.

Only local writers/paid producers are replaced. Admission, adoption, knowledge,
production control and checkpoint commits execute their production code.
"""

from copy import deepcopy
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_fanqie_materialized_plan_resume import (
    integrated_modules as integrated_modules,
)
from test_fanqie_materialized_plan_resume import (
    materialized_case as materialized_case,
)
from test_fanqie_materialized_plan_resume import (
    plan_case as plan_case,
)
from test_fanqie_materialized_plan_resume import (
    store as store,
)
from test_fanqie_segment_resume import seed as seed_feedback_snapshot

from bestseller.domain.project import ProjectCreate
from bestseller.infra.db import models as db
from bestseller.services import continuity, feedback, llm
from bestseller.services import fanqie_short_pipeline as pipeline
from bestseller.services import production_control as control
from bestseller.services.adopted_knowledge_resume import read_adopted_knowledge
from bestseller.services.budget import bind_execution_scope
from bestseller.settings import load_settings
from support.adopted_bible_fixture import seed_empty_bible

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# Same SQLite table boundary as test_adopted_knowledge_lineage, with real
# independent transactions rather than that fixture's outer rollback/savepoint.
KNOWLEDGE_TABLES = (
    db.ProjectModel, db.PlanningArtifactVersionModel, db.ChapterModel,
    db.ChapterDraftVersionModel, db.ChapterStateSnapshotModel, db.CanonFactModel,
    db.TimelineEventModel, db.CharacterModel, db.CharacterStateSnapshotModel,
    db.PlotArcModel, db.ClueModel, db.AntagonistPlanModel, db.QualityScoreModel,
    db.ReviewReportModel, db.RelationshipEventModel, db.EmotionTrackModel,
    db.RelationshipModel, db.WorldRuleModel, db.WorkflowRunModel,
    db.BookProductionControlModel, db.DraftPromotionDecisionModel,
)


async def forbidden(*args, **kwargs):
    raise AssertionError("No paid provider, unexpected writer or publication allowed")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("SQLite pause tests forbid network/PG/provider access")

    monkeypatch.delenv("BESTSELLER_LINEAGE_PG", raising=False)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    for module in (llm, feedback, continuity):
        monkeypatch.setattr(module, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    monkeypatch.setattr(pipeline, "run_chapter_pipeline", forbidden)


async def knowledge_tables(engine):
    assert engine.dialect.name == "sqlite"
    assert "test" in Path(engine.url.database).stem
    async with engine.begin() as connection:
        for model in KNOWLEDGE_TABLES:
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
            await connection.exec_driver_sql(
                ddl.replace("::jsonb", "").replace(
                    "CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1,
                )
            )


@pytest.fixture
async def pause_case(tmp_path):
    path = tmp_path / "fanqie_pause_checkpoint_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    await knowledge_tables(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            project = db.ProjectModel(
                slug="pause-test-" + uuid4().hex,
                title="SQLite checkpoint fixture", genre="test", status="writing",
                project_type="fanqie_short", target_word_count=8000, target_chapters=2,
                metadata_json={"execution_scope_id": "fixture:" + uuid4().hex},
            )
            session.add(project)
            await session.flush()
            chapter = db.ChapterModel(
                project_id=project.id, chapter_number=1, chapter_goal="fixture",
                metadata_json={},
            )
            session.add(chapter)
            await session.flush()
            draft = db.ChapterDraftVersionModel(
                project_id=project.id, chapter_id=chapter.id, version_no=1,
                content_md="阿青把钥匙放在桌上。", promotion_state="candidate",
                is_current=True, promotion_metadata={"retained_raw": "local writer return"},
            )
            held = db.BookProductionControlModel(
                project_id=project.id, desired_state="run", command_serial=1,
                detail_json={"scope": "this book only"},
            )
            session.add_all([draft, held])
            await session.commit()
            settings = load_settings().model_copy(deep=True)
            assert settings.database.url.startswith("sqlite")
            settings.pipeline.enable_chapter_feedback = True
            settings.output.base_dir = str(tmp_path / "output")
            yield SimpleNamespace(
                session=session, factory=factory, settings=settings, project=project,
                chapter=chapter, draft=draft, held=held,
            )
    finally:
        await engine.dispose()


async def command(factory, project_id, intent):
    """A separate operator connection commits while the worker holds its ORM row."""
    async with factory() as operator:
        row = await operator.get(db.BookProductionControlModel, project_id)
        row.desired_state = intent
        row.command_serial += 1
        row.state_reason = "operator fixture command"
        row.requested_by = "pause-test-operator"
        await operator.commit()
        return row.command_serial


async def close_knowledge(session, settings, project, chapter, draft, monkeypatch):
    configured = (session, settings, project, chapter, draft)
    # Restore the deny stubs after the existing local transport fixture returns.
    with monkeypatch.context() as patch:
        await seed_feedback_snapshot(configured, patch)
        await seed_empty_bible(configured, patch)
    _, knowledge = await read_adopted_knowledge(
        session, project_id=project.id, chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id, chapter_md=draft.content_md,
        require_bible=True,
    )
    assert knowledge["closed"]
    await session.commit()


def assert_halt(caught, events, *, intent, slug, next_segment):
    assert caught.value.intent == intent
    assert str(caught.value) == f"production_{intent}_by_operator"
    assert caught.value.control["intent"] == intent
    assert caught.value.control["command_serial"] == 2
    halts = [payload for event, payload in events if event == "fanqie_halted_by_operator"]
    assert len(halts) == 1
    assert halts[0] == {
        "project_slug": slug, "next_segment_number": next_segment,
        **caught.value.control,
    }


@pytest.mark.parametrize("intent", ["pause", "stop"])
async def test_other_session_initial_halt_refreshes_strong_run_reference(pause_case, intent):
    c = pause_case
    held = c.held
    assert (await control.load_control_state(c.session, c.project.id)).intent is control.ProductionIntent.RUN
    await c.session.commit()
    await command(c.factory, c.project.id, intent)
    assert held.desired_state == "run"  # Strong identity-map reference really is stale.
    pending = db.ProjectModel(
        slug="pending-test-" + uuid4().hex, title="Uncommitted caller work", genre="other",
        target_word_count=8000, target_chapters=1,
    )
    c.session.add(pending)
    events, fallthrough = [], []
    with pytest.raises(control.ProductionControlHaltedError) as caught:
        await pipeline._run_fanqie_segments(
            c.session, c.settings, c.project, requested_by="test",
            progress=lambda event, payload: events.append((event, payload)),
        )
        fallthrough.append("whole-book export")
    assert_halt(caught, events, intent=intent, slug=c.project.slug, next_segment=1)
    assert held.desired_state == intent
    assert pending in c.session.new  # Control reads must also suppress autoflush.
    assert not fallthrough
    assert [event for event, _ in events] == ["fanqie_halted_by_operator"]
    assert c.draft.promotion_state == "candidate"


@pytest.mark.parametrize("segment_count", [1, 2], ids=["last-boundary", "first-boundary"])
@pytest.mark.parametrize("intent", ["pause", "stop"])
async def test_completed_checkpoint_survives_external_halt_and_explicit_run_resume(
    pause_case, monkeypatch, segment_count, intent,
):
    c = pause_case
    first_draft_id, first_chapter_id = c.draft.id, c.chapter.id
    c.project.target_chapters = segment_count
    await c.session.commit()
    calls, events, observed, fallthrough = [], [], [], []

    async def local_writer(session, settings, slug, number, **kwargs):
        assert session is c.session and slug == c.project.slug
        calls.append(number)
        if number == 1:
            chapter, draft = c.chapter, c.draft
        else:
            chapter = db.ChapterModel(
                project_id=c.project.id, chapter_number=number, chapter_goal="next fixture",
                metadata_json={},
            )
            session.add(chapter)
            await session.flush()
            draft = db.ChapterDraftVersionModel(
                project_id=c.project.id, chapter_id=chapter.id, version_no=1,
                content_md="阿青拿起钥匙，推开门。", promotion_state="promoted",
                is_current=True,
            )
            session.add(draft)
        draft.promotion_state = "promoted"
        await session.commit()
        await close_knowledge(session, settings, c.project, chapter, draft, monkeypatch)

    monkeypatch.setattr(pipeline, "run_chapter_pipeline", local_writer)
    real_checkpoint = pipeline._checkpoint_commit

    def progress(event, payload):
        events.append((event, payload))
        if event == "fanqie_segment_writing_completed" and not observed:
            # Written after knowledge services commit: only the actual completed
            # segment checkpoint can make this marker visible to the operator.
            c.chapter.metadata_json = {**c.chapter.metadata_json, "pause_checkpoint": "durable"}

    async def checkpoint_then_operator(session):
        await real_checkpoint(session)
        if observed or not events or events[-1][0] != "fanqie_segment_writing_completed":
            return
        async with c.factory() as operator:
            chapter = await operator.get(db.ChapterModel, c.chapter.id)
            draft = await operator.get(db.ChapterDraftVersionModel, c.draft.id)
            snapshot = await operator.scalar(select(db.ChapterStateSnapshotModel).where(
                db.ChapterStateSnapshotModel.chapter_id == chapter.id,
            ))
            assert chapter.metadata_json["pause_checkpoint"] == "durable"
            assert draft.promotion_state == "promoted" and draft.content_md == c.draft.content_md
            assert snapshot.raw_extraction
            observed.append((draft.content_md, deepcopy(draft.promotion_metadata),
                             snapshot.id, snapshot.raw_extraction, deepcopy(chapter.metadata_json)))
        await command(c.factory, c.project.id, intent)

    # Transparent scheduling hook, never a replacement for commit or a guard.
    monkeypatch.setattr(pipeline, "_checkpoint_commit", checkpoint_then_operator)
    with pytest.raises(control.ProductionControlHaltedError) as caught:
        await pipeline._run_fanqie_segments(
            c.session, c.settings, c.project, requested_by="test", progress=progress,
        )
        fallthrough.append("whole-book export")
    assert_halt(caught, events, intent=intent, slug=c.project.slug, next_segment=2)
    assert calls == [1] and len(observed) == 1 and not fallthrough
    assert c.held.desired_state == intent
    assert [p["segment_number"] for e, p in events if e == "fanqie_segment_writing_started"] == [1]

    async def retained_first():
        async with c.factory() as reader:
            draft = await reader.get(db.ChapterDraftVersionModel, first_draft_id)
            snapshot = await reader.get(db.ChapterStateSnapshotModel, observed[0][2])
            chapter = await reader.get(db.ChapterModel, first_chapter_id)
            assert (draft.content_md, draft.promotion_metadata, snapshot.id,
                    snapshot.raw_extraction, chapter.metadata_json) == observed[0]
            assert len(list(await reader.scalars(select(db.ChapterDraftVersionModel).where(
                db.ChapterDraftVersionModel.chapter_id == chapter.id,
            )))) == 1

    await c.session.rollback()  # A caller exiting on halt cannot undo its checkpoint.
    await retained_first()
    await c.session.refresh(c.project)
    await c.session.refresh(c.held)
    assert await command(c.factory, c.project.id, "run") == 3
    await pipeline._run_fanqie_segments(
        c.session, c.settings, c.project, requested_by="explicit-run-resume",
    )
    assert calls == ([1, 2] if segment_count == 2 else [1])
    assert c.held.desired_state == "run"
    await retained_first()


async def test_unrelated_paused_book_does_not_contaminate_running_project(pause_case, monkeypatch):
    c = pause_case
    other = db.ProjectModel(
        slug="unrelated-test-" + uuid4().hex, title="Unrelated book", genre="other",
        target_word_count=8000, target_chapters=1, metadata_json={"keep": "unrelated"},
    )
    c.session.add(other)
    await c.session.flush()
    other_control = db.BookProductionControlModel(
        project_id=other.id, desired_state="run", command_serial=1,
    )
    c.session.add(other_control)
    c.project.target_chapters = 1
    c.draft.promotion_state = "promoted"
    await c.session.commit()
    await close_knowledge(c.session, c.settings, c.project, c.chapter, c.draft, monkeypatch)
    before = deepcopy(other.metadata_json)
    await command(c.factory, other.id, "pause")
    await pipeline._run_fanqie_segments(c.session, c.settings, c.project, requested_by="test")
    assert c.held.desired_state == "run" and c.held.command_serial == 1
    async with c.factory() as reader:
        assert (await reader.get(db.ProjectModel, other.id)).metadata_json == before
        assert (await reader.get(db.BookProductionControlModel, other.id)).desired_state == "pause"
        assert not list(await reader.scalars(select(db.ChapterModel).where(
            db.ChapterModel.project_id == other.id,
        )))


async def test_real_control_read_failure_propagates_before_writer(pause_case):
    c = pause_case
    await c.session.commit()
    async with c.factory() as operator:
        await operator.execute(text("DROP TABLE book_production_control"))
        await operator.commit()
    events = []
    with pytest.raises(OperationalError, match="no such table"):
        await pipeline._run_fanqie_segments(
            c.session, c.settings, c.project, requested_by="test",
            progress=lambda event, payload: events.append((event, payload)),
        )
    assert not events and c.held.desired_state == "run"


@pytest.mark.parametrize("pause_during", ["deslop", "litstyle"])
async def test_actual_outer_run_retains_advisory_return_then_halts_before_publish(
    plan_case, monkeypatch, pause_during,
):
    """Use existing real hard-admission fixture, never fake its gate green."""
    c = plan_case
    engine = c.factory.kw["bind"]
    await knowledge_tables(engine)
    c.settings.pipeline.enable_chapter_feedback = True
    events, publish_calls = [], []
    async with c.factory() as session:
        project = await session.get(db.ProjectModel, c.pid)
        chapters = list(await session.scalars(select(db.ChapterModel).where(
            db.ChapterModel.project_id == c.pid,
        ).order_by(db.ChapterModel.chapter_number)))
        for chapter in chapters:
            chapter.status, chapter.production_state = "complete", "ok"
            draft = await session.scalar(select(db.ChapterDraftVersionModel).where(
                db.ChapterDraftVersionModel.chapter_id == chapter.id,
            ))
            if draft is None:
                draft = db.ChapterDraftVersionModel(
                    project_id=c.pid, chapter_id=chapter.id, version_no=1,
                    content_md=f"Mira records the winter signal at station {chapter.chapter_number}.",
                    promotion_state="promoted", is_current=True,
                )
                session.add(draft)
            draft.promotion_state = "promoted"
            await session.commit()
            await close_knowledge(session, c.settings, project, chapter, draft, monkeypatch)

        # The caller keeps the original RUN row alive across all fresh reads.
        held = await session.get(db.BookProductionControlModel, c.pid)
        assert held.desired_state == "run"
        payload = ProjectCreate(
            slug=project.slug, title=project.title, genre=project.genre,
            language=project.language, project_type="fanqie_short",
            target_chapters=4, target_word_count=8000,
            metadata=deepcopy(project.metadata_json),
        )
        await session.commit()
        candidate = "  A local unadopted whole-story advisory return.  \n"
        advisory_returns = []

        async def deslop(session, settings, full_text, *, observe_candidate, **kwargs):
            assert (await control.load_control_state(session, c.pid)).intent is control.ProductionIntent.RUN
            await session.commit()
            await observe_candidate({
                "content": candidate, "llm_run_id": None, "round": 1,
                "parent_content": full_text,
            })
            advisory_returns.append("deslop")
            if pause_during == "deslop":
                await command(c.factory, c.pid, "pause")
            return candidate

        async def litstyle(session, settings, **kwargs):
            assert [p["segment_number"] for e, p in events
                    if e == "fanqie_segment_writing_completed"] == [1, 2, 3, 4]
            assert (await control.load_control_state(session, c.pid)).intent is control.ProductionIntent.RUN
            await session.commit()  # Model gateway's real transaction release boundary.
            await command(c.factory, c.pid, "pause")
            advisory_returns.append("litstyle")
            return SimpleNamespace(final_score=37, level="fixture-return-retained")

        async def publish(*args, **kwargs):
            publish_calls.append(kwargs)
            raise AssertionError("operator pause must prevent publication entry")

        from bestseller.services import fanqie_short_source_export as source_export

        monkeypatch.setattr(pipeline, "_deslop_whole_short_story", deslop)
        monkeypatch.setattr(pipeline, "judge_chapter_litstyle_stable", litstyle)
        monkeypatch.setattr(source_export, "publish_adopted_assembly", publish)
        with bind_execution_scope(c.original.scope):
            with pytest.raises(control.ProductionControlHaltedError) as caught:
                await pipeline.run_fanqie_short_pipeline(
                    session, c.settings, project_payload=payload,
                    premise=project.metadata_json["premise"],
                    progress=lambda event, data: events.append((event, data)),
                )
        assert_halt(caught, events, intent="pause", slug=project.slug, next_segment=5)
        assert held.desired_state == "pause" and not publish_calls
        assert advisory_returns == (["deslop", "litstyle"] if pause_during == "litstyle" else ["deslop"])
        assert [p["segment_number"] for e, p in events
                if e == "fanqie_segment_writing_completed"] == [1, 2, 3, 4]
        assert not any(e == "fanqie_short_pipeline_completed" for e, _ in events)
        assert not any(e == "fanqie_export_completed" for e, _ in events)
        style_returns = [p for e, p in events if e == "fanqie_litstyle_advisory_completed"]
        assert style_returns == ([{
            "project_slug": project.slug, "litstyle_score": 37,
            "litstyle_level": "fixture-return-retained",
        }] if pause_during == "litstyle" else [])
        assert not any(e == "fanqie_litstyle_advisory_skipped" for e, _ in events)
        output = Path(c.settings.output.base_dir) / project.slug
        candidates = list(output.glob("candidates/whole-story/*/candidate.md"))
        assert candidates and candidate in [p.read_text() for p in candidates]
        assert not (output / "full-story.md").exists()
        assert project.status != "completed"
        await session.rollback()
    async with c.factory() as reader:
        assert (await reader.get(db.BookProductionControlModel, c.pid)).desired_state == "pause"
        assert (await reader.get(db.ProjectModel, c.pid)).status != "completed"
        assert len(list(await reader.scalars(select(db.ChapterDraftVersionModel).where(
            db.ChapterDraftVersionModel.project_id == c.pid,
            db.ChapterDraftVersionModel.promotion_state == "promoted",
        )))) == 4
    assert len(c.sent) == 1  # Existing fixture's one local hard-admission wire reply.
