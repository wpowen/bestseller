"""Run real entry functions against retained SQLite state before paid work."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.schema import CreateTable
from test_materialized_outline_admission_read import artifact, install
from test_materialized_outline_admission_read import rig as source_rig  # noqa: F401

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db import models as db
from bestseller.services import (
    context,
    drafts,
    editable_plan_source,
    llm,
    pipelines,
    review_source_context,
    workflows,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class ForbiddenWork(BaseException):
    """Cannot be swallowed by the optional optimizer's Exception handler."""


class ReachedAfterAdmission(BaseException):
    pass


@pytest.fixture
async def entry(source_rig, monkeypatch):  # noqa: F811
    rig = source_rig
    engine = rig.session.bind
    async with engine.begin() as connection:
        for model in (db.ChapterDraftVersionModel, db.ReviewReportModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))

    typed = ChapterOutlineBatchInput.model_validate(rig.artifact.content).chapters[0]
    chapter = db.ChapterModel(project_id=rig.project.id, chapter_number=1, metadata_json={})
    for key in (
        "title",
        "chapter_goal",
        "opening_situation",
        "main_conflict",
        "hook_type",
        "hook_description",
        "target_word_count",
    ):
        setattr(chapter, key, getattr(typed, key))
    workflows._sync_chapter_causality_metadata(chapter, typed)
    workflows._sync_chapter_outline_semantics(chapter, typed)
    rig.session.add(chapter)
    await rig.session.flush()
    scene = db.SceneCardModel(project_id=rig.project.id, chapter_id=chapter.id, metadata_json={})
    for key in (
        "scene_number",
        "scene_type",
        "title",
        "time_label",
        "participants",
        "purpose",
        "entry_state",
        "exit_state",
        "key_dialogue_beats",
        "sensory_anchors",
        "forbidden_actions",
        "hook_requirement",
        "target_word_count",
    ):
        setattr(scene, key, getattr(typed.scenes[0], key))
    workflows._sync_scene_methodology_metadata(scene, typed.scenes[0])
    rig.session.add(scene)
    old_draft = db.ChapterDraftVersionModel(
        project_id=rig.project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="A retained older candidate.",
        word_count=5,
        promotion_state="candidate",
        is_current=False,
    )
    current = db.ChapterDraftVersionModel(
        project_id=rig.project.id,
        chapter_id=chapter.id,
        version_no=3,
        content_md="The clerk kept the brass seal beside the signed cargo receipt.",
        word_count=13,
        promotion_state="candidate",
        is_current=True,
        promotion_metadata={"retained_provenance": "unchanged"},
    )
    reports = [
        db.ReviewReportModel(
            project_id=rig.project.id,
            target_type="chapter",
            target_id=chapter.id,
            reviewer_type="critic",
            verdict=verdict,
            structured_output={
                "verdict": verdict,
                "retained_evidence": {"raw": verdict + " original"},
            },
        )
        for verdict in ("unknown", "rewrite")
    ]
    rig.session.add_all([old_draft, current, *reports])
    chapter.metadata_json = {
        **chapter.metadata_json,
        "chapter_review_active_cycle": {"cycle_id": "retained-cycle", "state": "completed"},
        "chapter_critic_cycles": {
            "retained-cycle": {
                "state": "completed",
                "attempts": [{"draft_version": 3, "old_unknown_retained": True}],
            }
        },
    }
    await rig.session.commit()
    rig.chapter, rig.scene, rig.current, rig.reports = chapter, scene, current, reports
    rig.forbidden = []

    def deny(label):
        def call(*args, **kwargs):
            rig.forbidden.append(label)
            raise ForbiddenWork(label)

        return call

    def deny_async(label):
        async def call(*args, **kwargs):
            rig.forbidden.append(label)
            raise ForbiddenWork(label)

        return call

    for module, names in (
        (
            pipelines,
            (
                "_ensure_emotion_kernel_backfill_for_pipeline",
                "_ensure_public_emotion_kernel_backfill_for_pipeline",
                "_ensure_entry_system_backfill_for_pipeline",
                "generate_chapter_draft_once",
                "generate_scene_draft",
            ),
        ),
        (
            context,
            (
                "optimize_project_character_profiles",
                "load_scene_story_bible_context",
                "search_retrieval_for_project",
            ),
        ),
        (editable_plan_source, ("build_editable_plan_source",)),
        (review_source_context, ("build_planning_continuity_context",)),
        (drafts, ("complete_text",)),
        (llm, ("complete_text",)),
    ):
        for name in names:
            monkeypatch.setattr(module, name, deny_async(module.__name__ + "." + name))
    monkeypatch.setattr(pipelines, "_chapter_first_requested", deny("pipeline_after_admission"))
    monkeypatch.setattr(
        review_source_context,
        "build_chapter_generation_input_bundle",
        deny("review_bundle_after_admission"),
    )
    return rig


async def rows_before(entry):
    result = {}
    for model in (
        db.ProjectModel,
        db.PlanningArtifactVersionModel,
        db.WorkflowRunModel,
        db.ChapterModel,
        db.SceneCardModel,
        db.ChapterDraftVersionModel,
        db.ReviewReportModel,
    ):
        fields = [
            getattr(model, field.key).label(field.key) for field in inspect(model).column_attrs
        ]
        rows = (await entry.session.execute(select(*fields).order_by(model.id))).mappings().all()
        result[model.__tablename__] = deepcopy([dict(row) for row in rows])
    return result


async def execute(entry, target):
    if target == "pipeline":
        # Actual function body, bypassing only the outer operation-stage decorator.
        return await pipelines.run_chapter_pipeline.__wrapped__(
            entry.session,
            entry.settings,
            entry.project.slug,
            1,
            resume_existing_draft_only=True,
            chapter_first=True,
        )
    if target == "scene":
        return await context.build_scene_writer_context_from_models(
            entry.session, entry.settings, entry.project, entry.chapter, entry.scene
        )
    if target == "chapter":
        return await context.build_chapter_writer_context(
            entry.session, entry.settings, entry.project.slug, 1
        )
    assert target == "review"
    return await review_source_context.load_review_source_context(
        entry.session,
        settings=entry.settings,
        project=entry.project,
        chapter=entry.chapter,
        scenes=[entry.scene],
        context_packet=SimpleNamespace(),
        content_md=entry.current.content_md,
    )


@pytest.mark.parametrize("target", ["pipeline", "scene", "chapter", "review"])
async def test_negative_legacy_blocks_real_entry_before_any_downstream_work(entry, target):
    before = await rows_before(entry)
    with pytest.raises(
        ValueError, match="outline_source_admission_required:current_hard_admission_missing"
    ):
        await execute(entry, target)
    assert entry.forbidden == [] and entry.verified == []
    assert await rows_before(entry) == before
    assert entry.current.is_current and entry.current.promotion_state == "candidate"
    assert [r.verdict for r in entry.reports] == ["unknown", "rewrite"]


async def test_verified_admission_crosses_real_pipeline_guard_until_next_stage(entry, monkeypatch):
    workflow = await install(entry)
    before = await rows_before(entry)
    reached = []

    def stop_after_guard(*args, **kwargs):
        reached.append("chapter_first_selection")
        raise ReachedAfterAdmission

    monkeypatch.setattr(pipelines, "_chapter_first_requested", stop_after_guard)
    with pytest.raises(ReachedAfterAdmission):
        await execute(entry, "pipeline")
    assert reached == ["chapter_first_selection"] and len(entry.verified) == 1
    candidate = deepcopy(entry.artifact.content)
    candidate.pop("_meta", None)
    assert entry.verified[0]["candidate_payload"] == candidate
    assert entry.verified[0]["workflow_run_id"] == workflow.id
    assert entry.forbidden == [] and await rows_before(entry) == before


@pytest.mark.parametrize("selection", ["no_artifact", "unselected_negative"])
async def test_existing_scene_path_survives_without_a_selected_negative(
    entry, monkeypatch, selection
):
    if selection == "no_artifact":
        entry.artifact.status = "needs_review"
    else:
        entry.session.add(
            artifact(entry.project, "chapter_outline_batch", entry.artifact.content, version_no=2)
        )
    await entry.session.commit()
    before = await rows_before(entry)
    reached = []

    async def stop_after_guard(*args, **kwargs):
        reached.append("optimizer")
        raise ReachedAfterAdmission

    monkeypatch.setattr(context, "optimize_project_character_profiles", stop_after_guard)
    with pytest.raises(ReachedAfterAdmission):
        await execute(entry, "scene")
    assert reached == ["optimizer"] and entry.verified == [] and entry.forbidden == []
    assert await rows_before(entry) == before
