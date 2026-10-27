"""Scene checkpoint durability through real entrypoints and file-backed SQLite.

Writer/reviewer/knowledge services emit deterministic fixture rows without committing.
Context, planning backfills and provider transport are excluded. Workflow creation,
exact-version promotion, both pipeline checkpoints and resume selection are real.
Fresh connections after rollback/close establish durability, not ORM identity-map state.
This does not establish PostgreSQL locking, real-model quality, or chapter completion.
"""

from collections import Counter
import hashlib
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb  # noqa: F401

from bestseller.domain.knowledge import SceneKnowledgeRefreshResult
from bestseller.infra.db.models import (
    CanonFactModel,
    ChapterModel,
    CharacterModel,
    DraftPromotionDecisionModel,
    LlmRunModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
    SceneCardModel,
    SceneDraftVersionModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.services import llm, pipelines, planner, story_bible
from bestseller.services import outline_source_entry
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class ImmediatelyAfterScene(BaseException):
    """Simulate termination without letting normal exception handlers commit anything."""


@pytest.fixture
async def scene_checkpoint(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Checkpoint tests forbid network/provider access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setenv("LITSTYLE_LONGFORM_ADVISORY", "0")
    path = tmp_path / "scene_completion_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def explicit_transactions(connection, _record):
        connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def begin(connection):
        # A released SAVEPOINT must not masquerade as the final outer COMMIT.
        connection.exec_driver_sql("BEGIN")

    models = (
        ProjectModel, ChapterModel, SceneCardModel, SceneDraftVersionModel,
        LlmRunModel, WorkflowRunModel, WorkflowStepRunModel, ReviewReportModel,
        QualityScoreModel, DraftPromotionDecisionModel, CharacterModel, CanonFactModel,
    )
    async with engine.begin() as connection:
        assert connection.dialect.name == "sqlite"
        assert connection.engine.url.database == str(path)
        for model in models:
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = ProjectModel(
            slug=f"checkpoint-test-{uuid4().hex}", title="Checkpoint fixture", genre="test",
            target_chapters=1, target_word_count=2000, metadata_json={},
        )
        session.add(project)
        await session.flush()
        chapter = ChapterModel(
            project_id=project.id, chapter_number=1, chapter_goal="test", status="planned",
        )
        session.add(chapter)
        await session.flush()
        scenes = [SceneCardModel(
            project_id=project.id, chapter_id=chapter.id, scene_number=number,
            scene_type="dialogue", status="planned", participants=[], purpose={},
        ) for number in (1, 2)]
        session.add_all(scenes)
        await session.commit()
        ids = SimpleNamespace(
            project=project.id, chapter=chapter.id, scenes=[scene.id for scene in scenes],
            slug=project.slug,
        )

    settings = load_settings()
    settings.pipeline.resume_enabled = True
    settings.quality.draft_mode = False
    settings.pipeline.require_pre_draft_scene_contract = False
    settings.pipeline.enable_scene_plan_richness_gate = False
    settings.pipeline.enable_contradiction_checks = False
    settings.pipeline.enable_story_bible_write_gate = False
    settings.pipeline.enable_outline_semantic_gate = False
    calls = Counter()

    async def nothing(*args, **kwargs):
        return None

    # Unrelated planning/model/context dependencies; no transaction helper is patched.
    for name in (
        "_ensure_emotion_kernel_backfill_for_pipeline",
        "_ensure_public_emotion_kernel_backfill_for_pipeline",
        "_ensure_entry_system_backfill_for_pipeline", "_enforce_truth_version_guard",
        "_ensure_project_invariants", "build_scene_writer_context_from_models",
        "propagate_scene_discoveries",
    ):
        monkeypatch.setattr(pipelines, name, nothing)
    monkeypatch.setattr(outline_source_entry, "current_outline_artifact", nothing)
    monkeypatch.setattr(planner, "_assert_planning_capabilities_available", lambda *a, **k: None)
    monkeypatch.setattr(story_bible, "is_bible_incremental_enabled", lambda: False)

    async def fixture_run(session, role, workflow_id):
        row = LlmRunModel(
            project_id=ids.project, workflow_run_id=workflow_id,
            logical_role=role, provider="offline-fixture", model_name="offline-fixture",
            prompt_hash="f" * 64, metadata_json={"fixture": True},
        )
        session.add(row)
        await session.flush()
        return row

    async def write(session, slug, chapter_number, scene_number, **kw):
        calls["writer", scene_number] += 1
        run = await fixture_run(session, "writer", kw["workflow_run_id"])
        draft = SceneDraftVersionModel(
            project_id=ids.project, scene_card_id=ids.scenes[scene_number - 1],
            version_no=1, content_md=f"Scene {scene_number}: the sealed letter stayed on the desk.",
            word_count=12, is_current=True, promotion_state="candidate", llm_run_id=run.id,
        )
        session.add(draft)
        await session.flush()
        return draft

    async def review(session, settings, slug, chapter_number, scene_number, **kw):
        calls["reviewer", scene_number] += 1
        draft = await pipelines._load_current_scene_draft(session, ids.scenes[scene_number - 1])
        run = await fixture_run(session, "critic", kw["workflow_run_id"])
        report = ReviewReportModel(
            project_id=ids.project, target_type="scene_card", target_id=draft.scene_card_id,
            reviewer_type="offline-fixture", verdict="pass", severity_max="low",
            llm_run_id=run.id,
            structured_output={
                "draft_id": str(draft.id),
                "body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest(),
                "fixture": True,
            },
        )
        session.add(report)
        await session.flush()
        score = QualityScoreModel(
            project_id=ids.project, target_type="scene_card", target_id=draft.scene_card_id,
            scene_draft_version_id=draft.id, review_report_id=report.id,
            judge_key="checkpoint-fixture", evaluation_round=1, is_current=True,
            score_overall=0.95, score_goal=0.95, score_conflict=0.95,
            score_emotion=0.95, score_style=0.95,
            evidence_summary={"hard_gates_passed": True, "blocking_codes": [], "fixture": True},
        )
        session.add(score)
        await session.flush()
        return SimpleNamespace(verdict="pass", severity_max="low"), report, score, None

    async def knowledge(session, settings, slug, chapter_number, scene_number, **kw):
        calls["knowledge", scene_number] += 1
        draft = await pipelines._load_current_scene_draft(session, ids.scenes[scene_number - 1])
        assert draft.promotion_state == "promoted"  # Real promotion, not a score-object shortcut.
        fact = CanonFactModel(
            project_id=ids.project, subject_type="scene_card", subject_id=draft.scene_card_id,
            subject_label="fixture", predicate="scene_summary", fact_type="scene_summary",
            value_json={"summary": draft.content_md, "source_draft_version_id": str(draft.id)},
            source_scene_id=draft.scene_card_id, source_chapter_id=ids.chapter,
            valid_from_chapter_no=1, is_current=True,
        )
        session.add(fact)
        await session.flush()
        return SceneKnowledgeRefreshResult(
            project_id=ids.project, chapter_id=ids.chapter, scene_id=draft.scene_card_id,
            chapter_number=1, scene_number=scene_number, canon_fact_ids=[fact.id],
            canon_facts_created=1, summary_text=draft.content_md,
        )

    monkeypatch.setattr(pipelines, "generate_scene_draft", write)
    monkeypatch.setattr(pipelines, "review_scene_draft", review)
    monkeypatch.setattr(pipelines, "refresh_scene_knowledge", knowledge)
    try:
        yield SimpleNamespace(factory=factory, ids=ids, settings=settings, calls=calls)
    finally:
        await engine.dispose()


async def assert_durable_scene(session, ids, number):
    scene = await session.get(SceneCardModel, ids.scenes[number - 1])
    assert scene.status == "approved"
    draft = await pipelines._load_current_scene_draft(session, scene.id)
    assert draft is not None and draft.promotion_state == "promoted"
    assert draft.content_md == f"Scene {number}: the sealed letter stayed on the desk."
    score = await session.scalar(select(QualityScoreModel).where(
        QualityScoreModel.scene_draft_version_id == draft.id,
    ))
    report = await session.get(ReviewReportModel, score.review_report_id)
    assert report.verdict == "pass"
    assert report.structured_output["draft_id"] == str(draft.id)
    assert report.structured_output["body_sha256"] == hashlib.sha256(draft.content_md.encode()).hexdigest()
    workflow = await session.scalar(select(WorkflowRunModel).where(
        WorkflowRunModel.scope_id == scene.id,
    ))
    assert workflow.status == workflow.current_step == "completed"
    assert workflow.metadata_json["scene_promoted"] is True
    steps = {row.step_name: row for row in await session.scalars(select(WorkflowStepRunModel).where(
        WorkflowStepRunModel.workflow_run_id == workflow.id,
    ))}
    assert steps["generate_scene_draft"].output_ref["draft_id"] == str(draft.id)
    assert steps["review_scene_v1"].output_ref["report_id"] == str(report.id)
    assert steps["review_scene_v1"].output_ref["quality_score_id"] == str(score.id)
    fact_id = steps["refresh_scene_knowledge"].output_ref["canon_fact_ids"][0]
    fact = await session.scalar(select(CanonFactModel).where(
        CanonFactModel.source_scene_id == scene.id,
    ))
    assert str(fact.id) == fact_id
    assert fact.value_json["source_draft_version_id"] == str(draft.id)
    assert await session.get(LlmRunModel, draft.llm_run_id) is not None
    assert await session.get(LlmRunModel, report.llm_run_id) is not None
    decisions = list(await session.scalars(select(DraftPromotionDecisionModel).where(
        DraftPromotionDecisionModel.scene_draft_version_id == draft.id,
    )))
    assert any(row.to_state == "promoted" for row in decisions)
    assert all(row.status == "completed" for row in steps.values())
    return draft.id, workflow.id


async def test_scene_success_survives_immediate_caller_interruption_and_resume(scene_checkpoint):
    fixture = scene_checkpoint
    async with fixture.factory() as session:
        with pytest.raises(ImmediatelyAfterScene):
            result = await pipelines.run_scene_pipeline(
                session, fixture.settings, fixture.ids.slug, 1, 1,
            )
            assert result.final_verdict == "pass" and not result.requires_human_review
            raise ImmediatelyAfterScene()
        await session.rollback()  # Exactly what the interrupted caller loses.
    async with fixture.factory() as reopened:
        draft_id, workflow_id = await assert_durable_scene(reopened, fixture.ids, 1)
        assert result.current_draft_id == draft_id and result.workflow_run_id == workflow_id
        resumed = await pipelines.run_scene_pipeline(
            reopened, fixture.settings, fixture.ids.slug, 1, 1,
        )
        assert resumed.current_draft_id == draft_id
        assert resumed.review_iterations == resumed.rewrite_iterations == 0
        assert fixture.calls == Counter({("writer", 1): 1, ("reviewer", 1): 1, ("knowledge", 1): 1})
        assert await reopened.scalar(select(func.count()).select_from(SceneDraftVersionModel)) == 1
        assert await reopened.scalar(select(func.count()).select_from(ReviewReportModel)) == 1
        assert await reopened.scalar(select(func.count()).select_from(WorkflowRunModel)) == 1


async def test_chapter_scene_step_survives_interruption_and_resume_skips_completed_writer(
    scene_checkpoint,
):
    fixture = scene_checkpoint

    def stop_after(number):
        def progress(stage, payload):
            if stage == "chapter_scene_pipeline_completed" and payload["scene_number"] == number:
                raise ImmediatelyAfterScene()
        return progress

    async with fixture.factory() as session:
        with pytest.raises(ImmediatelyAfterScene):
            await pipelines.run_chapter_pipeline(
                session, fixture.settings, fixture.ids.slug, 1,
                chapter_first=False, progress=stop_after(1),
            )
        await session.rollback()
    async with fixture.factory() as reopened:
        first_draft, first_workflow = await assert_durable_scene(reopened, fixture.ids, 1)
        parent_step = await reopened.scalar(select(WorkflowStepRunModel).where(
            WorkflowStepRunModel.step_name == "scene_pipeline_1",
        ))
        assert parent_step is not None and parent_step.status == "completed"
        assert parent_step.output_ref["scene_workflow_run_id"] == str(first_workflow)
        assert (await reopened.get(SceneCardModel, fixture.ids.scenes[1])).status == "planned"
        # Continue through the same public chapter entry, then interrupt after scene 2.
        with pytest.raises(ImmediatelyAfterScene):
            await pipelines.run_chapter_pipeline(
                reopened, fixture.settings, fixture.ids.slug, 1,
                chapter_first=False, progress=stop_after(2),
            )
        await reopened.rollback()
    async with fixture.factory() as final:
        assert (await assert_durable_scene(final, fixture.ids, 1)) == (first_draft, first_workflow)
        await assert_durable_scene(final, fixture.ids, 2)
        steps = list(await final.scalars(select(WorkflowStepRunModel).where(
            WorkflowStepRunModel.step_name.in_(["scene_pipeline_1", "scene_pipeline_2"]),
        )))
        assert len(steps) == 2 and all(row.status == "completed" for row in steps)
        assert {row.step_name for row in steps} == {"scene_pipeline_1", "scene_pipeline_2"}
        assert fixture.calls == Counter({
            (role, number): 1 for role in ("writer", "reviewer", "knowledge") for number in (1, 2)
        })
        assert await final.scalar(select(func.count()).select_from(SceneDraftVersionModel)) == 2
        assert await final.scalar(select(func.count()).select_from(ReviewReportModel)) == 2
