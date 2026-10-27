"""Canonical adoption hydrates a planning lock without regenerating identity."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import PlanningArtifactVersionModel
from bestseller.services.conception_approval_bridge import prepare_adopted_design_snapshot
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services import planner
from bestseller.services import planning_concurrency
from bestseller.settings import load_settings
from test_adopted_knowledge_lineage import db  # noqa: F401
from test_conception_approval_bridge import _snapshot

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def seed(db, name):
    session, project, _, _ = db
    conn = await session.connection()
    # The shared lineage fixture also creates this table. Keep local seeding
    # valid both alone and with the complete fixture dependency corpus.
    ddl = str(CreateTable(PlanningArtifactVersionModel.__table__, if_not_exists=True).compile(dialect=conn.dialect))
    await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    content = _snapshot()["content"]
    content["premise"] = f"{name}，一名接线员，追查明日来电。"
    snapshot = PlanningArtifactVersionModel(
        id=uuid4(), project_id=project.id, artifact_type="conception_snapshot",
        version_no=1, status="canonical", content=content,
        schema_version="conception-snapshot.v1", idempotency_key="test-adoption",
    )
    session.add(snapshot)
    project.target_chapters = 12
    project.target_word_count = 31200
    project.metadata_json = {
        "conception_approved": True,
        "creation_canonical_snapshot_id": str(snapshot.id),
        "creation_canonical_snapshot_hash": snapshot_hash(content),
        "legacy_intent_provenance": {"complete_original_contract_available": False},
    }
    await session.commit()
    return session, project, snapshot


@pytest.mark.parametrize("name", ["林杉", "苏棠"])
async def test_adoption_reuses_named_canonical_design_and_preserves_unknown_intent(db, name):
    session, project, snapshot = await seed(db, name)
    assert not planner._source_bound_cast_enabled(project)
    original_content = deepcopy(snapshot.content)
    assert await prepare_adopted_design_snapshot(session, project)
    assert planner._source_bound_cast_enabled(project)
    design = deepcopy(project.metadata_json["book_design_snapshot"])
    assert design["protagonist"]["name"] == name
    assert "creation_intent_contract" not in project.metadata_json
    assert not project.metadata_json["legacy_intent_provenance"]["complete_original_contract_available"]
    assert await prepare_adopted_design_snapshot(session, project)
    assert project.metadata_json["book_design_snapshot"] == design
    assert snapshot.content == original_content
    book = planner._compile_source_bound_book_spec(project, project.metadata_json["premise"])
    assert book["protagonist"]["name"] == name


@pytest.mark.parametrize("failure", ["hash", "status", "missing", "different_adoption", "stale_design"])
async def test_bad_adoption_evidence_never_locks_or_regenerates(db, failure):
    session, project, snapshot = await seed(db, "林杉")
    if failure == "hash":
        snapshot.content = {**snapshot.content, "premise": "changed"}
    elif failure == "status":
        snapshot.status = "superseded"
    elif failure == "missing":
        project.metadata_json = {**project.metadata_json, "creation_canonical_snapshot_id": str(uuid4())}
    elif failure == "different_adoption":
        project.metadata_json = {**project.metadata_json, "book_design_adoption_source": {"content_hash": "old"}}
    else:
        project.metadata_json = {**project.metadata_json, "book_design_snapshot": {
            "protagonist": {"name": "另一位主角"},
            "approved_story_design": {"source_contract_hash": "old"},
        }}
    await session.flush()
    before = deepcopy(project.metadata_json)
    with pytest.raises(ValueError):
        await prepare_adopted_design_snapshot(session, project)
    assert project.metadata_json == before
    assert not planner._source_bound_cast_enabled(project)


async def test_cached_canonical_object_cannot_mask_changed_database_content(db):
    session, project, snapshot = await seed(db, "林杉")
    old_content = deepcopy(snapshot.content)
    await session.execute(update(PlanningArtifactVersionModel).where(
        PlanningArtifactVersionModel.id == snapshot.id
    ).values(content={**old_content, "premise": "已被另一次写入改变"}).execution_options(
        synchronize_session=False
    ))
    assert snapshot.content == old_content
    with pytest.raises(ValueError, match="hash is stale"):
        await prepare_adopted_design_snapshot(session, project)
    assert not planner._source_bound_cast_enabled(project)


async def test_unrelated_cached_name_cannot_be_promoted_by_canonical_handoff(db):
    session, project, _ = await seed(db, "林杉")
    project.metadata_json = {**project.metadata_json,
        "creation_protagonist_name": "苏棠",
        "creation_protagonist_source": "explicit_user_choice"}
    before = deepcopy(project.metadata_json)
    with pytest.raises(ValueError, match="cached protagonist"):
        await prepare_adopted_design_snapshot(session, project)
    assert project.metadata_json == before


async def test_inferred_cache_cannot_override_named_canonical_premise(db):
    session, project, _ = await seed(db, "林杉")
    project.metadata_json = {**project.metadata_json,
        "creation_protagonist_name": "苏棠",
        "creation_protagonist_source": "llm_premise_identity_resolution"}
    assert await prepare_adopted_design_snapshot(session, project)
    assert project.metadata_json["book_design_snapshot"]["protagonist"]["name"] == "林杉"


@pytest.mark.parametrize("entry", ["generate_foundation_plan", "generate_novel_plan"])
async def test_planning_entry_never_sends_stale_envelope_premise(db, monkeypatch, entry):
    session, project, snapshot = await seed(db, "林杉")
    settings = load_settings()
    settings.pipeline.enable_rolling_outline = False
    settings.hook_engine.enabled = True

    class ObservedSource(Exception):
        pass

    async def project_lookup(*args, **kwargs):
        return project

    async def no_op(*args, **kwargs):
        pass

    async def workflow(*args, **kwargs):
        return SimpleNamespace(id=uuid4(), metadata_json={}, current_step="store_premise")

    async def observe(settings, *, project, premise):
        assert premise == snapshot.content["premise"]
        assert planner._source_bound_cast_enabled(project)
        raise ObservedSource()

    monkeypatch.setattr(planner, "_assert_plan_writer_not_locked", project_lookup)
    monkeypatch.setattr(planning_concurrency, "cancel_stale_planning_workflows", no_op)
    monkeypatch.setattr(planner, "create_workflow_run", workflow)
    monkeypatch.setattr(planner, "_run_hook_strength_gate", observe)
    monkeypatch.setattr(planner, "_record_planner_failure_step_once", no_op)
    with pytest.raises(ObservedSource):
        await getattr(planner, entry)(session, settings, project.slug, "旧任务缓存带来的另一个故事")
