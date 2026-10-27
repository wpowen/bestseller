"""Actual SQLite producers/readers; no provider or production database access."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import importlib.util
import io
from pathlib import Path
import socket
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db as db
from test_adopted_knowledge_lineage import stub_llm

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    CharacterModel,
    CharacterStateSnapshotModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    RelationshipModel,
    SceneCardModel,
    SceneDraftVersionModel,
)
from bestseller.services import context, feedback, knowledge, story_bible
from bestseller.services.character_snapshot_sources import (
    CharacterSnapshotView,
    load_eligible_character_snapshots,
    read_character_snapshot_knowledge,
    seal_snapshot_binding,
    snapshot_binding,
    snapshot_source_status,
)
from bestseller.services.drafts import render_chapter_draft_markdown
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def local_only(monkeypatch):
    monkeypatch.delenv("BESTSELLER_LINEAGE_PG", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("snapshot source tests forbid network/providers")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
async def configured(db):
    session, project, chapter, draft = db
    conn = await session.connection()
    assert conn.dialect.name == "sqlite"
    for model in (
        CharacterStateSnapshotModel,
        SceneCardModel,
        SceneDraftVersionModel,
        PlanningArtifactVersionModel,
    ):
        ddl = str(CreateTable(model.__table__, if_not_exists=True).compile(dialect=conn.dialect))
        await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    char = CharacterModel(
        project_id=project.id,
        name="阿青",
        role="protagonist",
        knowledge_state_json={},
        metadata_json={},
    )
    session.add(char)
    await session.commit()
    return session, project, chapter, draft, char


async def extract(configured, monkeypatch, payload, draft=None):
    session, project, chapter, original, _ = configured
    draft = draft or original
    calls = stub_llm(monkeypatch, feedback, payload)
    result = await feedback.extract_chapter_feedback(
        session,
        load_settings(),
        project_id=project.id,
        chapter=chapter,
        chapter_md=draft.content_md,
        source_chapter_draft_version_id=draft.id,
    )
    await session.commit()
    assert len(calls) == 1
    return result


async def replace_body(configured, old, *, body="这是新采用正文。"):
    session, project, chapter, _, _ = configured
    old.is_current = False
    old.promotion_state = "superseded"
    await session.flush()
    new = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=old.version_no + 1,
        content_md=body,
        promotion_state="promoted",
        is_current=True,
        assembled_from_scene_draft_ids=list(old.assembled_from_scene_draft_ids or []),
    )
    session.add(new)
    await session.commit()
    return new


async def effective(configured):
    session, project, _, _, char = configured
    return await story_bible.get_effective_character_state(
        session,
        project_id=project.id,
        character=char,
        before_chapter_number=2,
        before_scene_number=1,
    )


async def status(configured, row, **kwargs):
    return await snapshot_source_status(
        configured[0], row, project_id=configured[1].id, character_id=row.character_id, **kwargs
    )


@pytest.mark.parametrize("kind", ["empty", "partial", "full"])
async def test_actual_adoption_switch_excludes_a_before_fallback_and_history(
    configured, monkeypatch, kind
):
    session, project, _, a, char = configured
    await extract(
        configured,
        monkeypatch,
        {
            "character_states": [
                {
                    "character_name": char.name,
                    "emotional_state": "A情绪",
                    "arc_state": "A弧线",
                    "power_tier": "A境界",
                    "knowledge_gained": ["A知识"],
                    "beliefs_gained": ["A信念"],
                }
            ]
        },
    )
    a_row = await session.scalar(select(CharacterStateSnapshotModel))
    assert a_row.source_binding["payload_sha256"]
    b = await replace_body(configured, a)
    payload = (
        {}
        if kind == "empty"
        else {
            "character_states": [
                {
                    "character_name": char.name,
                    "knowledge_gained": ["B知识"],
                    **(
                        {"emotional_state": "B情绪", "beliefs_gained": ["B信念"]}
                        if kind == "full"
                        else {}
                    ),
                }
            ]
        }
    )
    await extract(configured, monkeypatch, payload, b)
    assert (await status(configured, a_row))["status"] == "rejected"
    value = await effective(configured)
    assert value.emotional_state == ("B情绪" if kind == "full" else None)
    assert value.previous_emotional_state is None
    assert value.arc_state is None and value.power_tier is None
    assert value.knowledge_state.get("knows", []) == ([] if kind == "empty" else ["B知识"])
    assert "A信念" not in repr(value.knowledge_state)
    assert "B信念" not in value.knowledge_state.get("knows", [])
    assert await session.get(CharacterStateSnapshotModel, a_row.id) is a_row
    # The raw CharacterModel was mutated by A. It cannot restore retired A.
    assert char.arc_state == "A弧线"
    writer = await context._load_participant_knowledge_states(
        session,
        project=project,
        chapter=type("Chapter", (), {"chapter_number": 2})(),
        participant_names=[char.name],
    )
    assert "A知识" not in repr(writer) and "A信念" not in repr(writer)


@pytest.mark.parametrize("mode", ["rejected_death", "rejected_stance", "historical_alive"])
async def test_actual_rejected_claims_are_raw_audit_only_and_historical_alive_is_valid(
    configured, monkeypatch, mode
):
    session, _, _, _, char = configured
    char.stance = "ally"
    char.alive_status = "deceased" if mode == "historical_alive" else "alive"
    char.death_chapter_number = 10
    if mode == "historical_alive":
        char.metadata_json = {"last_seen_chapter_number": 20}
    await session.commit()
    claim = {
        "character_name": char.name,
        "alive_status": "alive"
        if mode == "historical_alive"
        else "deceased"
        if mode == "rejected_death"
        else None,
        "stance": "enemy" if mode == "rejected_stance" else None,
        "power_tier": "unchanged-policy",
    }
    await extract(configured, monkeypatch, {"character_states": [claim]})
    raw = await session.scalar(select(CharacterStateSnapshotModel))
    views = await load_eligible_character_snapshots(
        session, project_id=configured[1].id, character_id=char.id
    )
    assert len(views) == 1 and isinstance(views[0], CharacterSnapshotView)
    assert views[0].raw is raw and views[0].power_tier == "unchanged-policy"
    result = await effective(configured)
    if mode == "rejected_death":
        assert raw.alive_status == "deceased" and views[0].alive_status is None
        assert result.alive_status is None
    elif mode == "rejected_stance":
        assert raw.stance == "enemy" and views[0].stance is None and result.stance is None
    else:
        assert char.alive_status == "deceased"
        assert views[0].alive_status == result.alive_status == "alive"


@pytest.mark.parametrize(
    "field", ["emotional_state", "alive_status", "stance", "beliefs", "binding"]
)
async def test_raw_or_binding_tamper_rejects_without_mutating_history(
    configured, monkeypatch, field
):
    session, _, _, _, char = configured
    await extract(
        configured,
        monkeypatch,
        {
            "character_states": [
                {
                    "character_name": char.name,
                    "knowledge_gained": ["确证知识"],
                    "emotional_state": "平静",
                }
            ]
        },
    )
    row = await session.scalar(select(CharacterStateSnapshotModel))
    if field == "binding":
        binding = deepcopy(row.source_binding)
        binding["accepted_fields"]["stance"] = False
        row.source_binding = binding
    else:
        setattr(row, field, ["篡改"] if field == "beliefs" else "篡改")
    await session.commit()
    assert (await status(configured, row))["reason"] == "snapshot_payload_changed"
    assert not (await effective(configured)).knowledge_state
    assert await session.get(CharacterStateSnapshotModel, row.id) is row


async def scene_fixture(configured):
    session, project, chapter, draft, char = configured
    await session.delete(draft)
    scene = SceneCardModel(
        project_id=project.id,
        chapter_id=chapter.id,
        scene_number=1,
        scene_type="dialogue",
        participants=[char.name],
    )
    session.add(scene)
    await session.flush()
    source = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md="场景正文。",
        promotion_state="promoted",
        is_current=True,
    )
    session.add(source)
    await session.commit()
    row = await knowledge._upsert_character_state_snapshot(
        session,
        project_id=project.id,
        character=char,
        chapter=chapter,
        scene=scene,
        source_scene_draft=source,
        state_payload={
            "emotional_state": "场景情绪",
            "beliefs": ["场景信念"],
            "knowledge_state": {"knows": ["场景知识"]},
        },
        summary_text="scene audit",
    )
    await session.commit()
    return scene, source, row


async def test_scene_continuation_known_late_adoption_and_copied_rewrite_ids(configured):
    session, project, chapter, _, char = configured
    scene, source, row = await scene_fixture(configured)
    binding = deepcopy(row.source_binding)
    assert binding["payload_sha256"]
    assert (await status(configured, row, before_chapter_number=1, before_scene_number=2))[
        "status"
    ] == "eligible"
    assert (await status(configured, row, before_chapter_number=2, before_scene_number=1))[
        "status"
    ] == "unknown"
    late = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md=render_chapter_draft_markdown(chapter, [source], language=project.language),
        promotion_state="promoted",
        is_current=True,
        assembled_from_scene_draft_ids=[str(source.id)],
    )
    session.add(late)
    await session.commit()
    assert (await status(configured, row, before_chapter_number=2, before_scene_number=1))[
        "status"
    ] == "eligible"
    assert (await effective(configured)).knowledge_state["knows"] == ["场景知识"]
    # Capture a known exact chapter binding at the producer too.
    known = await knowledge._upsert_character_state_snapshot(
        session,
        project_id=project.id,
        character=char,
        chapter=chapter,
        scene=scene,
        source_scene_draft=source,
        state_payload={"emotional_state": "场景情绪"},
        summary_text="known chapter",
    )
    await session.commit()
    assert known.source_binding["chapter_source"]
    await replace_body(configured, late)
    assert (await status(configured, known))["status"] == "rejected"
    assert (await status(configured, row, before_chapter_number=2, before_scene_number=1))[
        "status"
    ] != "eligible"
    assert row.source_binding == binding  # no lazy binding/upgrade on read


async def test_scene_body_and_snapshot_payload_tamper_are_fenced(configured):
    session = configured[0]
    _, source, row = await scene_fixture(configured)
    row.emotional_state = "raw tamper"
    await session.commit()
    assert (await status(configured, row, before_chapter_number=1, before_scene_number=2))[
        "reason"
    ] == "snapshot_payload_changed"
    row.emotional_state = "场景情绪"
    source.content_md += "改写。"
    await session.commit()
    assert (await status(configured, row, before_chapter_number=1, before_scene_number=2))[
        "reason"
    ] == "scene_source_changed"


async def test_scene_idempotence_preserves_prior_rows_without_relabeling_current_fields(configured):
    session, project, chapter, _, char = configured
    char.arc_state, char.power_tier = "retired arc", "future tier"
    scene, draft, row = await scene_fixture(configured)
    assert row.arc_state is None and row.power_tier is None
    again = await knowledge._upsert_character_state_snapshot(
        session,
        project_id=project.id,
        character=char,
        chapter=chapter,
        scene=scene,
        source_scene_draft=draft,
        state_payload={
            "emotional_state": "场景情绪",
            "beliefs": ["场景信念"],
            "knowledge_state": {"knows": ["场景知识"]},
        },
        summary_text="scene audit",
    )
    assert again.id == row.id
    original = deepcopy(row.source_binding)
    draft.is_current, draft.promotion_state = False, "superseded"
    await session.flush()
    new = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=2,
        content_md="改写后的场景正文。",
        promotion_state="promoted",
        is_current=True,
    )
    session.add(new)
    await session.flush()
    replacement = await knowledge._upsert_character_state_snapshot(
        session,
        project_id=project.id,
        character=char,
        chapter=chapter,
        scene=scene,
        source_scene_draft=new,
        state_payload={"emotional_state": "新情绪"},
        summary_text="new source",
    )
    await session.commit()
    assert replacement.id != row.id and row.source_binding == original
    assert (await status(configured, row, before_chapter_number=1, before_scene_number=2))[
        "status"
    ] == "rejected"
    assert (await status(configured, replacement, before_chapter_number=1, before_scene_number=2))[
        "status"
    ] == "eligible"


@pytest.mark.parametrize("key", feedback.KNOWLEDGE_SOURCE_KEYS)
async def test_all_six_source_keys_are_checked_even_when_payload_is_resealed(
    configured, monkeypatch, key
):
    session, _, _, _, char = configured
    await extract(
        configured,
        monkeypatch,
        {"character_states": [{"character_name": char.name, "emotional_state": "确证情绪"}]},
    )
    row = await session.scalar(select(CharacterStateSnapshotModel))
    changed = deepcopy(row.source_binding)
    value = changed["adopted_source"][key]
    changed["adopted_source"][key] = value + 1 if type(value) is int else str(uuid4())
    row.source_binding = seal_snapshot_binding(row, changed)
    await session.commit()
    assert (await status(configured, row))["reason"] == "adopted_source_changed"


async def test_same_draft_id_body_mutation_is_not_current_evidence(configured, monkeypatch):
    session, _, _, draft, char = configured
    await extract(
        configured,
        monkeypatch,
        {"character_states": [{"character_name": char.name, "emotional_state": "确证情绪"}]},
    )
    row = await session.scalar(select(CharacterStateSnapshotModel))
    draft.content_md += "原版本正文已改变。"
    await session.commit()
    assert (await status(configured, row))["reason"] == "adopted_source_changed"
    assert (await effective(configured)).emotional_state is None


async def test_previous_field_comes_from_still_adopted_prior_chapter(configured, monkeypatch):
    session, project, _, _, char = configured
    await extract(
        configured,
        monkeypatch,
        {"character_states": [{"character_name": char.name, "emotional_state": "先前有效情绪"}]},
    )
    second = ChapterModel(project_id=project.id, chapter_number=2, chapter_goal="next")
    session.add(second)
    await session.flush()
    draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=second.id,
        version_no=1,
        content_md="第二章的实际正文。",
        promotion_state="promoted",
        is_current=True,
    )
    session.add(draft)
    await session.commit()
    await extract(
        (session, project, second, draft, char),
        monkeypatch,
        {"character_states": [{"character_name": char.name, "emotional_state": "当前有效情绪"}]},
    )
    value = await story_bible.get_effective_character_state(
        session,
        project_id=project.id,
        character=char,
        before_chapter_number=3,
    )
    assert value.emotional_state == "当前有效情绪"
    assert value.previous_emotional_state == "先前有效情绪"
    assert value.previous_state_chapter_number == 2


async def test_unknown_kind_and_foreign_owner_fail_before_source_queries(configured):
    session, project, _, _, char = configured
    row = CharacterStateSnapshotModel(
        project_id=project.id,
        character_id=char.id,
        chapter_number=0,
        trust_map={},
        beliefs=[],
    )
    row.source_binding = seal_snapshot_binding(
        row,
        snapshot_binding(
            "unrecognized",
            project_id=project.id,
            character_id=char.id,
            chapter_number=0,
        ),
    )
    session.add(row)
    await session.commit()
    connection = await session.connection()
    queries = []

    def track(_conn, _cursor, sql, *_):
        queries.append(sql)

    event.listen(connection.sync_connection, "before_cursor_execute", track)
    try:
        assert (await status(configured, row))["reason"] == "unknown_producer"
        assert (
            await snapshot_source_status(
                session,
                row,
                project_id=uuid4(),
                character_id=char.id,
            )
        )["reason"] == "owner_mismatch"
        assert (
            await load_eligible_character_snapshots(
                session,
                project_id=project.id,
                character_id=char.id,
                limit=0,
            )
            == []
        )
        assert queries == []
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", track)


async def test_foreign_character_owner_cannot_be_bound_to_this_project(configured):
    session, project, _, _, _ = configured
    foreign = ProjectModel(
        slug=f"unrelated-{uuid4().hex}",
        title="Unrelated",
        genre="cozy",
        target_word_count=1000,
        target_chapters=2,
    )
    session.add(foreign)
    await session.flush()
    outsider = CharacterModel(
        project_id=foreign.id, name="Observer", knowledge_state_json={}, metadata_json={}
    )
    session.add(outsider)
    await session.flush()
    row = CharacterStateSnapshotModel(
        project_id=project.id, character_id=outsider.id, chapter_number=0, trust_map={}, beliefs=[]
    )
    row.source_binding = seal_snapshot_binding(
        row,
        snapshot_binding(
            "initial",
            project_id=project.id,
            character_id=outsider.id,
            chapter_number=0,
        ),
    )
    session.add(row)
    await session.commit()
    assert (await status(configured, row))["reason"] == "owner_mismatch"


async def test_actual_cast_materialization_keeps_unscoped_plot_knowledge_unknown(configured):
    session, project, _, _, char = configured
    cast = {
        "protagonist": {
            "name": char.name,
            "arc_state": "planned arc",
            "power_tier": "planned tier",
            "knowledge_state": {"knows": ["unscoped plot knowledge"]},
        }
    }
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="cast_spec",
        version_no=1,
        status="approved",
        schema_version="test",
        content=cast,
    )
    session.add(artifact)
    await session.flush()
    project.metadata_json = {
        "cast_spec": cast,
        "active_foundation_selection": {
            "schema": "foundation-selection.v1",
            "project_id": str(project.id),
            "artifact_ids": {"cast_spec": str(artifact.id)},
            "artifact_content_hashes": {"cast_spec": content_hash(cast)},
        },
    }
    await session.commit()
    counts = await story_bible.upsert_cast_spec(session, project, cast)
    assert counts["state_snapshots_created"] == 1
    await session.commit()
    initial = await session.scalar(select(CharacterStateSnapshotModel))
    assert (await status(configured, initial))["status"] == "eligible"
    assert initial.source_binding["knowledge_state"] == {}
    assert initial.beliefs == [] and not (await effective(configured)).knowledge_state
    repeat = await story_bible.upsert_cast_spec(session, project, cast)
    assert repeat["state_snapshots_created"] == 0


async def test_initial_explicit_planning_binding_keeps_legacy_unknown(configured):
    session, project, _, _, char = configured
    old = CharacterStateSnapshotModel(
        project_id=project.id,
        character_id=char.id,
        chapter_number=0,
        scene_number=0,
        beliefs=["旧未归因"],
        trust_map={},
        notes="legacy",
    )
    session.add(old)
    cast = {"protagonist": {"name": char.name}}
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="cast_spec",
        version_no=1,
        status="approved",
        schema_version="test",
        content=cast,
    )
    session.add(artifact)
    await session.flush()
    project.metadata_json = {
        "cast_spec": cast,
        "active_foundation_selection": {
            "schema": "foundation-selection.v1",
            "project_id": str(project.id),
            "artifact_ids": {"cast_spec": str(artifact.id)},
            "artifact_content_hashes": {"cast_spec": content_hash(cast)},
        },
    }
    char.knowledge_state_json = {"knows": ["初始知识"]}
    await session.commit()
    assert await story_bible._ensure_initial_character_state_snapshot(
        session, project_id=project.id, character=char
    )
    await session.commit()
    rows = list(await session.scalars(select(CharacterStateSnapshotModel)))
    initial = next(row for row in rows if row.source_binding is not None)
    assert initial.source_binding["payload_sha256"]
    assert (await status(configured, old))["status"] == "unknown"
    assert (await status(configured, initial))["status"] == "eligible"
    assert (await effective(configured)).knowledge_state["knows"] == ["初始知识"]
    initial.emotional_state = "tamper"
    await session.commit()
    assert (await status(configured, initial))["reason"] == "snapshot_payload_changed"
    initial.emotional_state = None
    artifact.content = {"protagonist": {"name": "不同角色"}}
    await session.commit()
    assert (await status(configured, initial))["reason"] == "planning_source_changed"
    assert old.source_binding is None


async def test_death_derived_actual_feedback_trigger_is_sealed_and_superseded(
    configured, monkeypatch
):
    session, project, _, a, char = configured
    survivor = CharacterModel(
        project_id=project.id, name="阿白", knowledge_state_json={}, metadata_json={}
    )
    session.add(survivor)
    await session.flush()
    session.add(
        RelationshipModel(
            project_id=project.id,
            character_a_id=char.id,
            character_b_id=survivor.id,
            relationship_type="ally",
            strength=0.9,
            metadata_json={},
        )
    )
    await session.commit()
    await extract(
        configured,
        monkeypatch,
        {"character_states": [{"character_name": char.name, "alive_status": "deceased"}]},
    )
    rows = list(await session.scalars(select(CharacterStateSnapshotModel)))
    derived = next(row for row in rows if row.character_id == survivor.id)
    assert derived.source_binding["kind"] == "death_derived"
    assert derived.source_binding["payload_sha256"]
    assert (await status(configured, derived))["status"] == "eligible"
    derived.emotional_state = "tamper"
    await session.commit()
    assert (await status(configured, derived))["reason"] == "snapshot_payload_changed"
    derived.emotional_state = "grieving"
    await replace_body(configured, a)
    assert (await status(configured, derived))["status"] == "rejected"


async def test_unknown_has_no_owner_query_and_read_is_no_autoflush(configured, monkeypatch):
    session, project, _, _, char = configured
    legacy = CharacterStateSnapshotModel(
        project_id=project.id,
        character_id=char.id,
        chapter_number=0,
        trust_map={},
        beliefs=["future?"],
        notes="feedback_extraction:ch0",
    )
    session.add(legacy)
    await session.commit()
    calls = []

    def track(_conn, _cursor, sql, *_):
        calls.append(sql)
        assert not sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))

    connection = await session.connection()
    event.listen(connection.sync_connection, "before_cursor_execute", track)
    try:
        assert (await status(configured, legacy))["status"] == "unknown"
        assert calls == []
        project.title = "pending authored change"
        assert not (await effective(configured)).knowledge_state
        assert project in session.dirty and project.title == "pending authored change"
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", track)


async def test_stale_rows_do_not_consume_64_limit_and_no_future_current_row(
    configured, monkeypatch
):
    session, project, chapter, a, char = configured
    await extract(
        configured,
        monkeypatch,
        {
            "character_states": [
                {"character_name": char.name, "emotional_state": "A", "knowledge_gained": ["A"]}
            ]
        },
    )
    original = await session.scalar(select(CharacterStateSnapshotModel))
    for i in range(70):
        row = CharacterStateSnapshotModel(
            project_id=project.id,
            character_id=char.id,
            chapter_id=chapter.id,
            chapter_number=1,
            emotional_state="A",
            trust_map={},
            beliefs=[],
            created_at=datetime(2030, 1, 1, tzinfo=UTC) + timedelta(seconds=i),
        )
        row.source_binding = seal_snapshot_binding(row, original.source_binding)
        session.add(row)
    await session.commit()
    b = await replace_body(configured, a)
    await extract(
        configured,
        monkeypatch,
        {
            "character_states": [
                {"character_name": char.name, "emotional_state": "B", "knowledge_gained": ["B"]}
            ]
        },
        b,
    )
    char.knowledge_state_json = {"knows": ["未来未归因知识"]}
    await session.commit()
    rows = await load_eligible_character_snapshots(
        session,
        project_id=project.id,
        character_id=char.id,
        before_chapter_number=2,
        before_scene_number=1,
        limit=1,
    )
    assert len(rows) == 1 and rows[0].emotional_state == "B"
    assert (await effective(configured)).knowledge_state["knows"] == ["B"]
    assert (
        await snapshot_source_status(session, rows[0], project_id=uuid4(), character_id=char.id)
    )["status"] == "rejected"
    # A genuinely bound later contribution is excluded by historical cutoff.
    future = ChapterModel(project_id=project.id, chapter_number=3, chapter_goal="future")
    session.add(future)
    await session.flush()
    fd = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=future.id,
        version_no=1,
        content_md="未来正文。",
        promotion_state="promoted",
        is_current=True,
    )
    session.add(fd)
    await session.commit()
    lineage, _ = await feedback.validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=future.id,
        source_chapter_draft_version_id=fd.id,
        chapter_md=fd.content_md,
        lock=False,
    )
    from test_adopted_bible_lineage import stub_llm as stub_bible_transport
    from bestseller.services import story_bible
    # A later contribution must have a successful real updater receipt, not
    # merely a copied adopted-source tuple on arbitrary character history.
    stub_bible_transport(monkeypatch, response={
        "character_updates": [{"name": char.name, "knowledge_gained": ["未来已绑定知识"]}],
        "relationship_updates": [], "world_updates": [],
    })
    outcome = await story_bible.update_story_bible_from_chapter(
        session, load_settings(), project=project, chapter=future,
        chapter_text=fd.content_md, source_chapter_draft_version_id=fd.id,
    )
    assert outcome["extraction_status"] == "ok"
    await session.commit()
    assert (await effective(configured)).knowledge_state["knows"] == ["B"]
    assert (
        "未来已绑定知识"
        in (
            await read_character_snapshot_knowledge(session, project_id=project.id, character=char)
        )["knows"]
    )


async def test_owned_migration_is_additive_sqlite_and_exact_postgres_ddl():
    path = (
        Path(__file__).resolve().parents[2] / "migrations/versions/0044_character_state_binding.py"
    )
    spec = importlib.util.spec_from_file_location("snapshot_binding_migration", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    assert revision.down_revision == "0043_execution_budget_ledger"
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE character_state_snapshots (id INTEGER PRIMARY KEY, notes TEXT)")
        )
        conn.execute(
            text("INSERT INTO character_state_snapshots(id, notes) VALUES (:id, :notes)"),
            [{"id": i, "notes": f"legacy-{i}"} for i in range(27)],
        )
        op = revision.op
        revision.op = Operations(MigrationContext.configure(conn))
        try:
            revision.upgrade()
        finally:
            revision.op = op
        rows = conn.execute(text("SELECT * FROM character_state_snapshots ORDER BY id")).all()
        assert len(rows) == 27 and all(row.source_binding is None for row in rows)
        assert [row.notes for row in rows] == [f"legacy-{i}" for i in range(27)]
        column = next(
            c
            for c in inspect(conn).get_columns("character_state_snapshots")
            if c["name"] == "source_binding"
        )
        assert column["nullable"] and column["default"] is None
    engine.dispose()
    output = io.StringIO()
    revision.op = Operations(
        MigrationContext.configure(
            dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
        )
    )
    try:
        revision.upgrade()
    finally:
        revision.op = op
    assert (
        output.getvalue().strip()
        == "ALTER TABLE character_state_snapshots ADD COLUMN source_binding JSONB;"
    )
