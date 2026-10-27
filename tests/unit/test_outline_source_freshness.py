"""SQLite source freshness; existing inert grade proof is not a new real grade."""

from copy import deepcopy
import socket

import pytest
from sqlalchemy import update
from test_materialized_outline_admission_read import artifact, install
from test_materialized_outline_admission_read import (
    rig as source_rig,  # noqa: F401 - entry dependency
)
from test_outline_source_entry_guards import entry as entry
from test_outline_source_entry_guards import execute, rows_before

from bestseller.infra.db import models as db
from bestseller.services import materialized_outline_admission_read as reader
from bestseller.services import outline_source_entry as source_entry
from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Outline freshness tests forbid PG/provider/network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def marker(case, workflow_id):
    return {
        "artifact_id": str(case.artifact.id),
        "workflow_run_id": str(workflow_id),
        "projection_hash": outline_prefix_projection_hash(case.chapter, [case.scene]),
        "source_content_sha256": content_hash(case.artifact.content),
    }


async def bind_existing_grade_boundary(case):
    """Reuse the existing test verifier boundary; source/projection reads are real SQL."""
    workflow = await install(case)
    case.artifact.source_run_id = workflow.id
    binding = marker(case, workflow.id)
    case.chapter.metadata_json = {**case.chapter.metadata_json, "outline_source_admission": binding}
    workflow.metadata_json = {
        **workflow.metadata_json,
        "chapter_projection_hashes": {str(case.chapter.id): binding["projection_hash"]},
    }
    await case.session.commit()
    return binding


async def call(case, target, *, scenes=None):
    if target == "pipeline":
        return await execute(case, "pipeline")
    if target == "reader":
        return await reader.require_materialized_outline_source_admission(
            case.session,
            case.settings,
            project=case.project,
            artifact=case.artifact,
            chapter=case.chapter,
            scenes=[case.scene] if scenes is None else scenes,
        )
    assert target in {"entry", "entry_auto"}
    return await source_entry.require_current_outline_source_admission(
        case.session,
        case.settings,
        project=case.project,
        chapter=case.chapter,
        scenes=None if target == "entry_auto" else ([case.scene] if scenes is None else scenes),
    )


async def assert_rejected_without_work_or_writes(case, target, *, scenes=None):
    before = await rows_before(case)
    with pytest.raises(ValueError, match="outline_source_admission_required:"):
        await call(case, target, scenes=scenes)
    assert case.forbidden == []
    assert await rows_before(case) == before


@pytest.mark.parametrize("target", ["entry", "pipeline"])
async def test_database_marker_cannot_hide_behind_cached_unmarked_chapter(entry, target):
    case = entry
    later = artifact(case.project, "chapter_outline_batch", case.artifact.content, version_no=2)
    case.session.add(later)
    await case.session.commit()
    cached = deepcopy(case.chapter.metadata_json)
    binding = marker(case, reader.admission_workflow_id(case.project.id, case.artifact.id))
    await case.session.execute(
        update(db.ChapterModel)
        .where(db.ChapterModel.id == case.chapter.id)
        .values(
            metadata_json={**cached, "outline_source_admission": binding},
        )
        .execution_options(synchronize_session=False)
    )
    await case.session.commit()
    assert "outline_source_admission" not in case.chapter.metadata_json
    # Neither the marked source nor the unrelated later import has a current
    # hard receipt. The old cache must not select the latter and return None.
    await assert_rejected_without_work_or_writes(case, target)
    assert case.chapter.metadata_json == cached and case.verified == []


@pytest.mark.parametrize("target", ["reader", "entry", "entry_auto", "pipeline"])
@pytest.mark.parametrize("changed", ["scene", "chapter"])
async def test_changed_database_projection_rejects_cached_approved_view(entry, target, changed):
    case = entry
    binding = await bind_existing_grade_boundary(case)
    before = await call(case, "entry")
    assert before["source_plan_projection"] == binding["projection_hash"]
    assert before["source_admission"]["draft_eligible"] is True
    cached = deepcopy(
        (case.chapter.chapter_goal, case.scene.exit_state, case.chapter.metadata_json)
    )
    if changed == "scene":
        await case.session.execute(
            update(db.SceneCardModel)
            .where(db.SceneCardModel.id == case.scene.id)
            .values(
                exit_state={"seal": "an unapproved changed outcome"},
            )
            .execution_options(synchronize_session=False)
        )
    else:
        await case.session.execute(
            update(db.ChapterModel)
            .where(db.ChapterModel.id == case.chapter.id)
            .values(
                chapter_goal="An unapproved replacement chapter goal.",
            )
            .execution_options(synchronize_session=False)
        )
    await case.session.commit()
    assert (case.chapter.chapter_goal, case.scene.exit_state, case.chapter.metadata_json) == cached
    await assert_rejected_without_work_or_writes(case, target)
    assert (case.chapter.chapter_goal, case.scene.exit_state, case.chapter.metadata_json) == cached


@pytest.mark.parametrize("target", ["entry", "reader"])
async def test_removed_database_marker_is_not_restored_from_caller_cache(entry, target):
    case = entry
    await bind_existing_grade_boundary(case)
    cached = deepcopy(case.chapter.metadata_json)
    without_marker = {
        key: deepcopy(value) for key, value in cached.items() if key != "outline_source_admission"
    }
    await case.session.execute(
        update(db.ChapterModel)
        .where(db.ChapterModel.id == case.chapter.id)
        .values(
            metadata_json=without_marker,
        )
        .execution_options(synchronize_session=False)
    )
    await case.session.commit()
    assert case.chapter.metadata_json == cached
    await assert_rejected_without_work_or_writes(case, target)
    assert case.chapter.metadata_json == cached


@pytest.mark.parametrize("target", ["entry", "reader"])
async def test_other_chapter_scene_cannot_borrow_identical_plan_fields(entry, target):
    case = entry
    await bind_existing_grade_boundary(case)
    other = db.ChapterModel(
        project_id=case.project.id,
        chapter_number=2,
        chapter_goal="Retained independent chapter.",
        metadata_json={},
    )
    case.session.add(other)
    await case.session.flush()
    copied = case.scene
    foreign = db.SceneCardModel(
        project_id=case.project.id,
        chapter_id=other.id,
        scene_number=1,
        scene_type=copied.scene_type,
        title=copied.title,
        time_label=copied.time_label,
        participants=deepcopy(copied.participants),
        purpose=deepcopy(copied.purpose),
        entry_state=deepcopy(copied.entry_state),
        exit_state=deepcopy(copied.exit_state),
        target_word_count=copied.target_word_count,
        metadata_json=deepcopy(copied.metadata_json),
    )
    case.session.add(foreign)
    await case.session.commit()
    await assert_rejected_without_work_or_writes(case, target, scenes=[foreign])


async def test_unchanged_current_view_remains_read_only_with_pending_caller_metadata(entry):
    case = entry
    binding = await bind_existing_grade_boundary(case)
    before = await rows_before(case)
    case.project.title = "A caller-owned pending title"
    result = await call(case, "entry")
    assert result["source_plan_projection"] == binding["projection_hash"]
    assert (
        case.project in case.session.dirty and case.project.title == "A caller-owned pending title"
    )
    with case.session.no_autoflush:
        assert await rows_before(case) == before
    assert case.forbidden == []
