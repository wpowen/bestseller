"""SQLite restoration uses the real full admission reader and immutable marker."""
from copy import deepcopy
from hashlib import sha256

import pytest
from sqlalchemy import event, select
from sqlalchemy.schema import CreateTable
from test_materialized_outline_admission_read import rig, install  # noqa: F401

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db import models as db
from bestseller.services import workflows
from bestseller.services import chapter_plan_observation_recovery as service
from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash
from bestseller.services.materialized_outline_admission_read import admission_workflow_id
from bestseller.services.outline_source_entry import require_current_outline_source_admission
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def case(rig):  # noqa: F811
    engine = rig.session.bind
    # SQLite's legacy SELECT transaction behavior otherwise lets an outermost
    # SAVEPOINT release commit. Exercise real outer transactions, as PG does.
    @event.listens_for(engine.sync_engine, "begin")
    def explicit_begin(connection):
        connection.exec_driver_sql("BEGIN")

    async with engine.begin() as connection:
        ddl = str(CreateTable(db.ChapterDraftVersionModel.__table__).compile(dialect=engine.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    typed = ChapterOutlineBatchInput.model_validate(rig.artifact.content).chapters[0]
    chapter = db.ChapterModel(project_id=rig.project.id, chapter_number=1, metadata_json={})
    for key in ("title", "chapter_goal", "opening_situation", "main_conflict", "hook_type",
        "hook_description", "target_word_count"):
        setattr(chapter, key, getattr(typed, key))
    workflows._sync_chapter_causality_metadata(chapter, typed)
    workflows._sync_chapter_outline_semantics(chapter, typed)
    rig.session.add(chapter)
    await rig.session.flush()
    scene = db.SceneCardModel(project_id=rig.project.id, chapter_id=chapter.id, metadata_json={})
    for key in ("scene_number", "scene_type", "title", "time_label", "participants", "purpose",
        "entry_state", "exit_state", "key_dialogue_beats", "sensory_anchors", "forbidden_actions",
        "hook_requirement", "target_word_count"):
        setattr(scene, key, getattr(typed.scenes[0], key))
    workflows._sync_scene_methodology_metadata(scene, typed.scenes[0])
    rig.session.add(scene)
    draft = db.ChapterDraftVersionModel(project_id=rig.project.id, chapter_id=chapter.id,
        version_no=1, is_current=True, promotion_state="candidate", content_md="The harbour ledger opens.",
        word_count=5)
    rig.session.add(draft)
    rig.artifact.source_run_id = admission_workflow_id(rig.project.id, rig.artifact.id)
    await rig.session.commit()
    projection = outline_prefix_projection_hash(chapter, [scene])
    marker = {"artifact_id": str(rig.artifact.id), "workflow_run_id": str(rig.artifact.source_run_id),
        "source_content_sha256": content_hash(rig.artifact.content), "projection_hash": projection}
    chapter.metadata_json = {**chapter.metadata_json, "outline_source_admission": marker,
        "retained_history": [{"do_not_change": True}]}
    await rig.session.commit()
    workflow = await install(rig)
    workflow.metadata_json = {**workflow.metadata_json,
        "chapter_projection_hashes": {str(chapter.id): projection}}
    await rig.session.commit()
    assert await require_current_outline_source_admission(rig.session, rig.settings,
        project=rig.project, chapter=chapter, scenes=[scene])
    rig.chapter, rig.scene, rig.draft, rig.admission = chapter, scene, draft, workflow
    rig.original_metadata = deepcopy(chapter.metadata_json)
    rig.original_admission = deepcopy(workflow.metadata_json)
    chapter.hype_type, chapter.hype_intensity = "golden_finger_reveal", 1.0
    await rig.session.commit()
    return rig


async def restore(case, **overrides):
    args = dict(chapter_id=case.chapter.id, expected_draft_id=case.draft.id,
        expected_body_sha256=sha256(case.draft.content_md.encode()).hexdigest(),
        expected_current_values={"hype_type": "golden_finger_reveal", "hype_intensity": 1.0},
        original_values={"hype_type": None, "hype_intensity": None},
        actor="source-recovery", reason="Separate observations from the admitted harbour plan")
    args.update(overrides)
    return await service.restore_source_owned_hype_fields(case.session, case.settings, **args)


async def test_restore_and_replay_preserve_original_marker_body_and_evidence(case):
    body, marker = case.draft.content_md, deepcopy(case.original_metadata["outline_source_admission"])
    with pytest.raises(ValueError, match="full_revision_current_marker"):
        await require_current_outline_source_admission(case.session, case.settings,
            project=case.project, chapter=case.chapter, scenes=[case.scene])
    receipt = await restore(case)
    await case.session.commit()
    assert case.chapter.hype_type is case.chapter.hype_intensity is None
    assert case.chapter.metadata_json["outline_source_admission"] == marker
    assert {k:v for k,v in case.chapter.metadata_json.items() if k != service.AUDIT_KEY} == case.original_metadata
    assert case.admission.metadata_json == case.original_admission
    assert receipt["previous_values"]["hype_type"] == "golden_finger_reveal"
    assert receipt["adoption_authorized"] is receipt["paid_result_authority"] is False
    assert case.draft.content_md == body and case.draft.promotion_state == "candidate"
    assert await restore(case) == receipt
    assert len(case.chapter.metadata_json[service.AUDIT_KEY]) == 1
    assert await require_current_outline_source_admission(case.session, case.settings,
        project=case.project, chapter=case.chapter, scenes=[case.scene])


async def test_explicit_rollback_probe_retains_polluted_values_and_old_evidence(case):
    identity = case.chapter.id
    original_metadata = deepcopy(case.chapter.metadata_json)
    await restore(case)
    await case.session.rollback()
    restored = await case.session.get(db.ChapterModel, identity)
    assert restored.hype_type == "golden_finger_reveal" and restored.hype_intensity == 1.0
    assert restored.metadata_json == original_metadata


async def test_numeric_debt_adoption_is_also_an_adopted_source(case):
    from test_adopted_knowledge_lineage import make_accepted_debt
    from bestseller.services.adopted_chapter_source import select_adopted_chapter_source

    engine = case.session.bind
    async with engine.begin() as connection:
        for model in (db.ReviewReportModel, db.QualityScoreModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    metadata = deepcopy(case.chapter.metadata_json)
    await make_accepted_debt((case.session, case.project, case.chapter, case.draft))
    case.chapter.metadata_json = {**metadata, **case.chapter.metadata_json}
    await case.session.commit()
    adopted, debt = await select_adopted_chapter_source(case.session, chapter=case.chapter, lock=False)
    assert adopted.id == case.draft.id and debt is not None
    assert adopted.promotion_state == "under_review" and adopted.promoted_at is None
    with pytest.raises(ValueError, match="unadopted_source_required"):
        await restore(case)
    assert service.AUDIT_KEY not in case.chapter.metadata_json


async def test_failed_validation_rolls_back_only_its_fields_even_if_caller_commits(case):
    pid, cid = case.project.id, case.chapter.id
    case.project.title = "Outer caller work survives"
    case.canonical.status = "superseded"
    await case.session.flush()
    with pytest.raises(ValueError, match="canonical_source_changed"):
        await restore(case)
    await case.session.commit()
    title = await case.session.scalar(select(db.ProjectModel.title).where(db.ProjectModel.id == pid))
    row = (await case.session.execute(select(db.ChapterModel.hype_type, db.ChapterModel.hype_intensity,
        db.ChapterModel.metadata_json).where(db.ChapterModel.id == cid))).one()
    assert title == "Outer caller work survives"
    assert row.hype_type == "golden_finger_reveal" and row.hype_intensity == 1.0
    assert service.AUDIT_KEY not in row.metadata_json


@pytest.mark.parametrize("fault", ["body", "draft", "values", "other_plan", "scene", "canonical",
    "foundation", "marker", "forged_original", "active", "adopted", "receipt"])
async def test_ambiguity_and_unrelated_changes_cannot_be_accepted(case, fault):
    from uuid import uuid4
    args = {}
    if fault == "body": args["expected_body_sha256"] = "0" * 64
    elif fault == "draft": args["expected_draft_id"] = uuid4()
    elif fault == "values": args["expected_current_values"] = {"hype_type": None, "hype_intensity": None}
    elif fault == "other_plan": case.chapter.chapter_goal = "Invent a different transfer"
    elif fault == "scene": case.scene.exit_state = {"seal": "unapproved"}
    elif fault == "canonical": case.canonical.status = "superseded"
    elif fault == "foundation": case.sources["world_spec"].content = {"unexpected": True}
    elif fault == "marker": case.chapter.metadata_json = {**case.chapter.metadata_json,
        "outline_source_admission": {**case.original_metadata["outline_source_admission"],
            "source_content_sha256": "0" * 64}}
    elif fault == "forged_original": args["original_values"] = {"hype_type":"face_slap", "hype_intensity":9}
    elif fault == "active": case.session.add(db.WorkflowRunModel(project_id=case.project.id,
        workflow_type="chapter_pipeline", scope_type="chapter", scope_id=case.chapter.id, status="running"))
    elif fault == "adopted": case.draft.promotion_state = "promoted"
    else: case.chapter.metadata_json = {**case.chapter.metadata_json, service.AUDIT_KEY: {"forged": True}}
    await case.session.commit()
    before = (case.chapter.hype_type, case.chapter.hype_intensity)
    project_id = case.project.id
    with pytest.raises(ValueError):
        await restore(case, **args)
    await case.session.rollback()
    values = (await case.session.execute(select(db.ChapterModel.hype_type, db.ChapterModel.hype_intensity)
        .where(db.ChapterModel.project_id == project_id))).one()
    assert tuple(values) == before
