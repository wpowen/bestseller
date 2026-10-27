"""Ordinary selection consumers with native SQLite source and paid-proof readers.

The shared plan_case uses an offline transport at the physical model boundary;
assessment, custody rows, retained request and subsequent verification are real.
No admission/source verifier is replaced here. Only this file is collected.
"""

from collections.abc import Mapping
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update
from test_fanqie_materialized_plan_resume import (
    evidence,
)
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
from test_selected_prefix_hype_observation import HIT, KEY
from test_selected_prefix_hype_observation import budget_io as budget_io

from bestseller.infra.db import models as db
from bestseller.services import drafts
from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash
from bestseller.services.chapter_scene_contract_materializer import (
    materialize_chapter_contract_from_chapter,
    materialize_chapter_scene_contracts,
)
from bestseller.services.chapter_writer_delivery import load_delivery_blocks
from bestseller.services.editable_plan_source import (
    build_editable_plan_source,
    load_selected_plan_source,
)
from bestseller.services.outline_source_entry import (
    current_outline_artifact,
    require_current_outline_source_admission,
)
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
ORDINARY = "fanqie_outline_source_selection"
SELECTIONS = ("outline_source_admission", "outline_prefix_readiness", ORDINARY)


async def current(case, session):
    project = await session.get(db.ProjectModel, case.pid)
    chapter = await session.get(db.ChapterModel, case.original.chapters[0].id)
    scenes = list(
        await session.scalars(
            select(db.SceneCardModel)
            .where(
                db.SceneCardModel.chapter_id == chapter.id,
            )
            .order_by(db.SceneCardModel.scene_number)
        )
    )
    return project, chapter, scenes


@pytest.fixture
async def ordinary_case(plan_case):
    case = plan_case
    async with case.factory() as session:
        source = await session.get(db.PlanningArtifactVersionModel, case.original.outline.id)
        producer = db.WorkflowRunModel(
            project_id=case.pid,
            workflow_type="fanqie_outline_field_completion",
            scope_type="planning_artifact",
            scope_id=source.id,
            status="completed",
            metadata_json={"fixture": "actual ordinary producer, not admission owner"},
        )
        session.add(producer)
        await session.flush()
        source.source_run_id = producer.id
        case.producer_id = producer.id
        chapters = list(
            await session.scalars(
                select(db.ChapterModel).where(
                    db.ChapterModel.project_id == case.pid,
                )
            )
        )
        assert len(chapters) == 4
        for chapter in chapters:
            scenes = list(
                await session.scalars(
                    select(db.SceneCardModel)
                    .where(
                        db.SceneCardModel.chapter_id == chapter.id,
                    )
                    .order_by(db.SceneCardModel.scene_number)
                )
            )
            chapter.metadata_json = {
                **chapter.metadata_json,
                ORDINARY: {
                    "artifact_id": str(source.id),
                    "workflow_run_id": str(case.stable_id),
                    "projection_hash": outline_prefix_projection_hash(chapter, scenes),
                    "source_content_sha256": content_hash(source.content),
                },
            }
        # A newer approved version must not win over the exact selected source.
        decoy = db.PlanningArtifactVersionModel(
            project_id=case.pid,
            artifact_type="chapter_outline_batch",
            version_no=99,
            status="approved",
            schema_version="1.0",
            content=deepcopy(source.content),
        )
        session.add(decoy)
        await session.execute(
            delete(db.ChapterDraftVersionModel).where(
                db.ChapterDraftVersionModel.project_id == case.pid,
            )
        )
        await session.commit()
        case.decoy_id = decoy.id
    return case


async def test_exact_ordinary_source_and_native_paid_proof_ignore_newer_approved(ordinary_case):
    case = ordinary_case
    before = await evidence(case)
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        artifact = await current_outline_artifact(session, project=project, chapter=chapter)
        assert artifact.id == case.original.outline.id != case.decoy_id
        assert artifact.source_run_id == case.producer_id != case.stable_id
        receipt = await require_current_outline_source_admission(
            session,
            case.settings,
            project=project,
            chapter=chapter,
            scenes=scenes,
        )
        assert receipt["workflow_run_id"] == str(case.stable_id)
        assert receipt["source_admission"]["draft_eligible"] is True
        assert (
            receipt["source_plan_projection"] == chapter.metadata_json[ORDINARY]["projection_hash"]
        )
        assert "outline_source_admission" not in chapter.metadata_json
        assert not session.new and not session.dirty and not session.deleted
    assert await evidence(case) == before
    assert len(case.sent) == 1  # Only the fixture's offline physical assessment.


async def test_ordinary_editable_plan_and_full_delivery_use_verified_source(ordinary_case):
    case = ordinary_case
    before = await evidence(case)
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        result = await build_editable_plan_source(
            session,
            project=project,
            chapter=chapter,
            scenes=scenes,
            settings=case.settings,
        )
        assert result["status"] == "available"
        assert result["binding"]["artifact_id"] == str(case.original.outline.id)
        blocks = await load_delivery_blocks(
            session,
            case.settings,
            project=project,
            chapter=chapter,
            scenes=scenes,
        )
        assert len(blocks) == 1 and blocks[0].required and blocks[0].trim_policy == "preserve"
        payload = json.loads(blocks[0].text.split("\n", 2)[2])
        approved = case.original.outline.content["chapters"][0]
        assert payload["artifact_id"] == str(case.original.outline.id)
        assert payload["chapter_contract"] == {
            key: value for key, value in approved.items() if key not in {"scenes", "_meta"}
        }
        assert payload["approved_chapter_sha256"] == content_hash(approved)
        for actual, source in zip(payload["scenes"], approved["scenes"], strict=True):
            for key, value in actual.items():
                assert value == source[key]
        assert not session.new and not session.dirty
    assert await evidence(case) == before and len(case.sent) == 1


async def test_delivery_rejects_cached_absence_of_persisted_ordinary_selection(ordinary_case):
    case = ordinary_case
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        # Detached cached state is supplied without overwriting the durable row.
        cached = SimpleNamespace(
            **{
                field.key: deepcopy(getattr(chapter, field.key))
                for field in db.ChapterModel.__mapper__.column_attrs
            }
        )
        cached.metadata_json.pop(ORDINARY)
        with pytest.raises(ValueError, match="current_marker_changed"):
            await load_delivery_blocks(
                session,
                case.settings,
                project=project,
                chapter=cached,
                scenes=scenes,
            )


async def test_unselected_delivery_keeps_native_empty_result(plan_case):
    case = plan_case
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        assert not any(key in chapter.metadata_json for key in SELECTIONS)
        assert (
            await load_delivery_blocks(
                session,
                case.settings,
                project=project,
                chapter=chapter,
                scenes=scenes,
            )
            == []
        )
        assert not session.dirty and not session.new


@pytest.mark.parametrize(
    "fault",
    [
        "paid_model",
        "paid_owner",
        "missing_grade",
        "blocked_owner",
        "projection",
        "marker_owner",
        "marker_hash",
        "marker_projection",
        "foreign_source",
        "unapproved",
    ],
)
async def test_ordinary_tamper_never_falls_back_to_latest_or_unavailable(ordinary_case, fault):
    case = ordinary_case
    async with case.factory() as session:
        _project, chapter, scenes = await current(case, session)
        owner = await session.get(db.WorkflowRunModel, case.stable_id)
        source = await session.get(db.PlanningArtifactVersionModel, case.original.outline.id)
        metadata = deepcopy(chapter.metadata_json)
        marker = metadata[ORDINARY]
        if fault in {"paid_model", "paid_owner"}:
            run = await session.get(
                db.LlmRunModel, UUID(owner.metadata_json["source_admission"]["llm_run_id"])
            )
            if fault == "paid_model":
                run.model_name = "tampered-physical-route"
            else:
                run.workflow_run_id = uuid4()
        elif fault == "missing_grade":
            owner.metadata_json = {**owner.metadata_json, "source_admission": None}
        elif fault == "blocked_owner":
            owner.status = "machine_blocked"
        elif fault == "projection":
            scenes[0].exit_state = {"summary": "unapproved outcome"}
        elif fault == "marker_owner":
            marker["workflow_run_id"] = str(uuid4())
        elif fault == "marker_hash":
            marker["source_content_sha256"] = "0" * 64
        elif fault == "marker_projection":
            marker["projection_hash"] = "0" * 64
        elif fault == "foreign_source":
            source.project_id = uuid4()
        else:
            source.status = "draft"
        chapter.metadata_json = metadata
        await session.commit()
    before = await evidence(case)
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        with pytest.raises(ValueError):
            await load_selected_plan_source(
                session,
                case.settings,
                project=project,
                chapter=chapter,
                scenes=scenes,
            )
        assert not session.new and not session.dirty
    assert await evidence(case) == before and len(case.sent) == 1


@pytest.mark.parametrize("marker", [None, {}, [], False, "bad", {"artifact_id": "bad"}])
async def test_malformed_ordinary_marker_fails_closed(ordinary_case, marker):
    case = ordinary_case
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        chapter.metadata_json = {**chapter.metadata_json, ORDINARY: deepcopy(marker)}
        await session.commit()
        with pytest.raises(ValueError):
            await load_delivery_blocks(
                session,
                case.settings,
                project=project,
                chapter=chapter,
                scenes=scenes,
            )


@pytest.mark.parametrize("fault", ["extra_field", "uuid_case", "hash_case", "short_hash"])
async def test_ordinary_marker_keeps_exact_four_field_schema(ordinary_case, fault):
    case = ordinary_case
    async with case.factory() as session:
        project, chapter, _scenes = await current(case, session)
        metadata = deepcopy(chapter.metadata_json)
        marker = metadata[ORDINARY]
        if fault == "extra_field":
            marker["draft_eligible"] = True
        elif fault == "uuid_case":
            marker["artifact_id"] = marker["artifact_id"].upper()
        elif fault == "hash_case":
            marker["source_content_sha256"] = "A" * 64
        else:
            marker["projection_hash"] = "0" * 63
        chapter.metadata_json = metadata
        await session.commit()
        with pytest.raises(ValueError, match="marker_invalid"):
            await current_outline_artifact(session, project=project, chapter=chapter)


@pytest.mark.parametrize("other", ["outline_source_admission", "outline_prefix_readiness"])
@pytest.mark.parametrize(
    "field", ["artifact_id", "workflow_run_id", "projection_hash", "source_content_sha256"]
)
async def test_conflicting_old_full_prefix_ordinary_markers_are_rejected(
    ordinary_case, other, field
):
    case = ordinary_case
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        conflict = deepcopy(chapter.metadata_json[ORDINARY])
        conflict[field] = str(uuid4()) if field.endswith("_id") else "0" * 64
        chapter.metadata_json = {**chapter.metadata_json, other: conflict}
        await session.commit()
        with pytest.raises(ValueError):
            await current_outline_artifact(session, project=project, chapter=chapter)
        with pytest.raises(ValueError):
            await load_selected_plan_source(
                session,
                case.settings,
                project=project,
                chapter=chapter,
                scenes=scenes,
            )


@pytest.mark.parametrize("key", SELECTIONS)
@pytest.mark.parametrize("change", ["add", "remove", "replace"])
async def test_all_current_and_cached_selection_markers_are_compared(ordinary_case, key, change):
    case = ordinary_case
    # Seed other keys before loading the caller, so removal/replacement has a value.
    if change != "add" and key != ORDINARY:
        async with case.factory() as setup:
            _project, chapter, _scenes = await current(case, setup)
            chapter.metadata_json = {
                **chapter.metadata_json,
                key: deepcopy(chapter.metadata_json[ORDINARY]),
            }
            await setup.commit()
    elif change == "add" and key == ORDINARY:
        async with case.factory() as setup:
            _project, chapter, _scenes = await current(case, setup)
            metadata = deepcopy(chapter.metadata_json)
            metadata.pop(ORDINARY)
            chapter.metadata_json = metadata
            await setup.commit()
    async with case.factory() as session:
        project, chapter, _scenes = await current(case, session)
        cached = deepcopy(chapter.metadata_json)
        async with case.factory() as other:
            row = await other.get(db.ChapterModel, chapter.id)
            metadata = deepcopy(row.metadata_json)
            if change == "remove":
                metadata.pop(key)
            else:
                metadata[key] = {"artifact_id": str(uuid4())}
            await other.execute(
                update(db.ChapterModel)
                .where(db.ChapterModel.id == row.id)
                .values(
                    metadata_json=metadata,
                )
            )
            await other.commit()
        with pytest.raises(ValueError, match="current_marker_changed"):
            await current_outline_artifact(session, project=project, chapter=chapter)
        assert chapter.metadata_json == cached and not session.dirty


async def test_ordinary_key_cannot_downgrade_existing_full_revision_producer_rule(ordinary_case):
    case = ordinary_case
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        chapter.metadata_json = {
            **chapter.metadata_json,
            "outline_source_admission": deepcopy(chapter.metadata_json[ORDINARY]),
        }
        await session.commit()
        with pytest.raises(
            ValueError, match=r"full_revision_actual_producer|conflicting_selection_kinds"
        ):
            await load_selected_plan_source(
                session,
                case.settings,
                project=project,
                chapter=chapter,
                scenes=scenes,
            )


async def test_ordinary_selection_requires_settings_and_real_shared_mapping(ordinary_case):
    case = ordinary_case
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        with pytest.raises(ValueError, match="settings_required"):
            await load_selected_plan_source(
                session, project=project, chapter=chapter, scenes=scenes
            )
        # A native no-proof return is not success. No verifier is monkeypatched.
        materializer = await session.get(db.WorkflowRunModel, case.original.owner.id)
        materializer.metadata_json = {"source_artifact_id": str(case.original.outline.id)}
        await session.commit()
        from bestseller.services import materialized_outline_admission_read as admission

        artifact = await session.get(db.PlanningArtifactVersionModel, case.original.outline.id)
        # MAIN's updated reader may reject changed materializer observations
        # immediately. The original ordinary reader returns None. Both must
        # remain failures at this selected consumer, without a stubbed verifier.
        try:
            native = await admission.require_materialized_outline_source_admission(
                session,
                case.settings,
                project=project,
                artifact=artifact,
                chapter=chapter,
                scenes=scenes,
            )
        except ValueError:
            with pytest.raises(ValueError):
                await load_selected_plan_source(
                    session,
                    case.settings,
                    project=project,
                    chapter=chapter,
                    scenes=scenes,
                )
        else:
            if native is None:
                with pytest.raises(ValueError, match="admission_required"):
                    await load_selected_plan_source(
                        session,
                        case.settings,
                        project=project,
                        chapter=chapter,
                        scenes=scenes,
                    )
            else:
                assert isinstance(native, Mapping)
                assert native["source_admission"]["draft_eligible"] is True
                result = await load_selected_plan_source(
                    session,
                    case.settings,
                    project=project,
                    chapter=chapter,
                    scenes=scenes,
                )
                assert result[0].id == artifact.id and result[2] == native


async def test_pending_cached_selection_is_not_flushed_or_replaced(ordinary_case):
    case = ordinary_case
    async with case.factory() as session:
        project, chapter, _scenes = await current(case, session)
        original = deepcopy(chapter.metadata_json)
        pending = deepcopy(original)
        pending[ORDINARY]["workflow_run_id"] = str(uuid4())
        chapter.metadata_json = pending
        with pytest.raises(ValueError, match="current_marker_changed"):
            await current_outline_artifact(session, project=project, chapter=chapter)
        assert chapter.metadata_json == pending and chapter in session.dirty
        async with case.factory() as other:
            persisted = await other.get(db.ChapterModel, chapter.id)
            assert persisted.metadata_json == original


@pytest.mark.parametrize("marker", [None, {}, "broken"])
async def test_ordinary_presence_protects_both_contract_materializers(ordinary_case, marker):
    case = ordinary_case
    async with case.factory() as session:
        _project, chapter, scenes = await current(case, session)
        chapter.metadata_json = {**chapter.metadata_json, ORDINARY: marker}
        before = outline_prefix_projection_hash(chapter, scenes)
        report = materialize_chapter_scene_contracts(
            chapter=chapter,
            scenes=scenes,
            verified_adoption=False,
            methodology_selected=True,
        )
        assert not report.changed and not report.scene_changes
        contract = SimpleNamespace(metadata_json={}, opening_state={})
        frozen = deepcopy(vars(contract))
        aligned = materialize_chapter_contract_from_chapter(
            chapter=chapter,
            chapter_contract=contract,
            verified_adoption=False,
        )
        assert not aligned.changed and vars(contract) == frozen
        assert outline_prefix_projection_hash(chapter, scenes) == before


@pytest.mark.parametrize("refresh", [False, True])
async def test_native_stamp_keeps_ordinary_projection_and_grade(ordinary_case, budget_io, refresh):
    case = ordinary_case
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        before = outline_prefix_projection_hash(chapter, scenes)
        planned = tuple(
            getattr(chapter, key) for key in ("hype_type", "hype_intensity", "hype_recipe_key")
        )
        await drafts.stamp_chapter_hype(
            session,
            chapter=chapter,
            chapter_number=chapter.chapter_number,
            content_md=HIT,
            project=project,
            refresh=refresh,
            scene_drafts=[
                SimpleNamespace(
                    generation_params={
                        "assigned_hype_type": "face_slap",
                        "assigned_hype_intensity": 9.0,
                        "assigned_hype_recipe_key": "body-observation-must-not-rewrite-plan",
                    }
                )
            ],
        )
        assert (
            tuple(
                getattr(chapter, key) for key in ("hype_type", "hype_intensity", "hype_recipe_key")
            )
            == planned
        )
        assert outline_prefix_projection_hash(chapter, scenes) == before
        assert chapter.metadata_json[KEY]["adoption_authorized"] is False
        await session.commit()
        receipt = await require_current_outline_source_admission(
            session,
            case.settings,
            project=project,
            chapter=chapter,
            scenes=scenes,
        )
        assert receipt["source_admission"]["draft_eligible"] is True
    assert len(case.sent) == 1


async def test_ordinary_repair_verifies_paid_plan_before_mutating(ordinary_case):
    case = ordinary_case
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        before = outline_prefix_projection_hash(chapter, scenes)
        with pytest.raises(ValueError, match="source_plan_repair_settings_required"):
            await drafts.maybe_prepare_chapter_auto_repair(
                session,
                project=project,
                chapter=chapter,
                repairable_codes=("BLOCK_LOW",),
            )
        owner = await session.get(db.WorkflowRunModel, case.stable_id)
        owner.metadata_json = {**owner.metadata_json, "source_admission": None}
        await session.commit()
        with pytest.raises(ValueError, match="outline_source_admission_required"):
            await drafts.maybe_prepare_chapter_auto_repair(
                session,
                project=project,
                chapter=chapter,
                repairable_codes=("BLOCK_LOW",),
                settings=case.settings,
            )
        assert outline_prefix_projection_hash(chapter, scenes) == before
