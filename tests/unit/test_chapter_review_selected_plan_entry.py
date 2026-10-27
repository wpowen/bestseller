"""Real SQLite review entry rejects a selected-plan failure before paid work.

Approval/grade fixtures are inert; these tests do not claim provider acceptance.
The entry loader, selected-plan verifier and persisted projection are real.
"""

from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.schema import CreateTable
from test_chapter_outline_readiness import _approve, _materialized
from test_chapter_outline_readiness import rig as rig  # noqa: F401
from test_chapter_review_commercial_checkpoint import review_db as review_db  # noqa: F401

from bestseller.infra.db.models import (
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    ReviewReportModel,
    SceneCardModel,
    WorkflowRunModel,
)
from bestseller.services import chapter_outline_readiness as readiness
from bestseller.services import chapter_llm_quality_judge, editable_plan_source, reviews

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _forbid_processing(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("selected-plan admission must precede context, duplication and paid work")

    for name in (
        "build_chapter_writer_context", "_compute_chapter_duplication_signal",
        "adjudicate_findings", "complete_text",
    ):
        monkeypatch.setattr(reviews, name, forbidden)
    monkeypatch.setattr(chapter_llm_quality_judge, "complete_text", forbidden)
    monkeypatch.setattr(chapter_llm_quality_judge, "judge_chapter_commercial_quality_stable", forbidden)


async def _run(db):
    return await reviews.review_chapter_draft(db.session, db.settings, db.project.slug, 2)


@pytest.mark.parametrize("marker", [None, {}, False, "approved", {"artifact_id": "invalid"}])
async def test_present_malformed_selection_stops_real_entry_before_any_processing(
    review_db, monkeypatch, marker,
):
    db = review_db
    db.chapter.metadata_json = {**db.chapter.metadata_json, "outline_prefix_readiness": marker}
    await db.session.commit()
    before = deepcopy(db.chapter.metadata_json)
    body = db.draft.content_md
    _forbid_processing(monkeypatch)
    with pytest.raises(ValueError, match="editable_plan_selection_invalid"):
        await _run(db)
    assert db.chapter.metadata_json == before and db.draft.content_md == body
    assert not list(await db.session.scalars(select(ReviewReportModel)))


@pytest.fixture
async def selected_db(review_db, rig):  # noqa: F811
    db = review_db
    # Reuse the existing bounded approval fixture, then persist its selected
    # artifact, original workflow and original grade run in the actual test DB.
    old_project_id = rig.project.id
    rig.project.id = db.project.id
    rig.store.rows.pop((ProjectModel, old_project_id), None)
    rig.store.rows[(ProjectModel, db.project.id)] = rig.project
    for foundation in rig.foundation.values():
        foundation.project_id = db.project.id
    for name in (
        "title", "genre", "sub_genre", "language", "target_chapters",
        "target_word_count", "reader_contract_json", "hype_scheme_json", "metadata_json",
    ):
        setattr(db.project, name, deepcopy(getattr(rig.project, name)))
    approved = await _approve(rig)
    artifact = await rig.store.get(PlanningArtifactVersionModel, UUID(approved["artifact_id"]))
    async with db.session.bind.begin() as connection:
        for model in (PlanningArtifactVersionModel, WorkflowRunModel, LlmRunModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    for (model, _), row in rig.store.rows.items():
        if model is ProjectModel:
            continue
        values = {
            attr.key: deepcopy(getattr(row, attr.key))
            for attr in inspect(model).column_attrs if hasattr(row, attr.key)
        }
        if model is PlanningArtifactVersionModel:
            values.update(version_no=1, schema_version="1")
        elif model is LlmRunModel:
            values.update(logical_role="critic", model_name="inert-approved-grade-fixture")
        db.session.add(model(**values))
    planned, scenes = _materialized(artifact.content["chapters"][1])
    for key, value in vars(planned).items():
        setattr(db.chapter, key, deepcopy(value))
    actual_scenes = list(await db.session.scalars(select(SceneCardModel)))
    assert len(actual_scenes) == len(scenes) == 1
    for actual, expected in zip(actual_scenes, scenes, strict=True):
        for key, value in vars(expected).items():
            if key in inspect(SceneCardModel).attrs:
                setattr(actual, key, deepcopy(value))
    db.chapter.metadata_json = {
        **db.chapter.metadata_json,
        "outline_prefix_readiness": {
            "artifact_id": str(artifact.id), "workflow_run_id": str(artifact.source_run_id),
            "projection_hash": readiness.outline_prefix_projection_hash(db.chapter, actual_scenes),
        },
    }
    await db.session.commit()
    # Prove the fixture really passes the unmodified full selected-plan guard.
    await editable_plan_source.build_editable_plan_source(
        db.session, project=db.project, chapter=db.chapter, scenes=actual_scenes,
    )
    db.selected_scenes = actual_scenes
    return db


@pytest.mark.parametrize("change", ["hype", "scene_entry", "scene_action", "missing_artifact"])
async def test_stale_selected_projection_stops_sqlite_entry_before_paid_or_duplication(
    selected_db, monkeypatch, change,
):
    db = selected_db
    if change == "hype":
        db.chapter.hype_type, db.chapter.hype_intensity = "face_slap", 1.0
    elif change == "scene_entry":
        db.selected_scenes[0].entry_state = {"summary": "Unapproved beginning"}
    elif change == "scene_action":
        scene = db.selected_scenes[0]
        scene.metadata_json = {**scene.metadata_json, "action_sequence": ["Unapproved action"]}
    else:
        db.chapter.metadata_json = {
            **db.chapter.metadata_json, "outline_prefix_readiness": {
                **db.chapter.metadata_json["outline_prefix_readiness"], "artifact_id": str(uuid4()),
            },
        }
    await db.session.commit()
    before = deepcopy(db.chapter.metadata_json)
    _forbid_processing(monkeypatch)
    code = "prefix_artifact_not_approved" if change == "missing_artifact" else "prefix_materialized_projection_changed"
    with pytest.raises(ValueError, match=code):
        await _run(db)
    assert db.chapter.metadata_json == before
    assert not list(await db.session.scalars(select(ReviewReportModel)))


async def _assert_continues(db, monkeypatch):
    class ExistingProcessingReached(RuntimeError):
        pass

    async def reached(*args, **kwargs):
        raise ExistingProcessingReached

    monkeypatch.setattr(reviews, "build_chapter_writer_context", reached)
    with pytest.raises(ExistingProcessingReached):
        await _run(db)


async def test_healthy_selected_entry_continues_after_full_actual_plan_verification(
    selected_db, monkeypatch,
):
    await _assert_continues(selected_db, monkeypatch)


async def test_ordinary_no_selection_entry_does_not_invent_plan_admission(
    review_db, monkeypatch,
):
    async def forbidden(*args, **kwargs):
        pytest.fail("ordinary entry must not acquire a new selection requirement")

    monkeypatch.setattr(editable_plan_source, "build_editable_plan_source", forbidden)
    await _assert_continues(review_db, monkeypatch)
