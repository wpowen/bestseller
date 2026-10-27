"""Real SQLite consumers: validated retirement, retained audit, no PG/provider IO."""

from copy import deepcopy
import json
import socket
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.schema import CreateTable
from test_adopted_bible_lineage import db as db
from test_adopted_bible_lineage import knowledge_db as knowledge_db
from test_inspection_retired_world_rules import _overview_tables, _row_state
from test_world_rule_identity_recovery import actual_retained_case, retained_raw

from bestseller.infra.db.models import (
    RetrievalChunkModel,
    SceneCardModel,
    VolumeFrontierModel,
    VolumeModel,
    WorldRuleModel,
)
from bestseller.services import inspection, llm, retrieval, story_bible
from bestseller.services import story_bible_projection as projection
from bestseller.services import world_rule_current as current
from bestseller.services import world_rule_identity_recovery as recovery
from bestseller.services.feedback import validate_adopted_knowledge_source
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("World consumers forbid network/PG/provider IO")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)


async def _tables(session, *models):
    connection = await session.connection()
    assert connection.dialect.name == "sqlite"
    assert "test" in session.bind.engine.url.database
    for model in models:
        ddl = str(
            CreateTable(model.__table__, if_not_exists=True).compile(dialect=connection.dialect)
        )
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    await session.commit()


@pytest.fixture
async def shifted(db):
    """All wrong-row effects excluded, although their adopted source remains active."""
    session, project, chapter, draft = db
    assert session.bind.dialect.name == "sqlite"
    source, rejection = await validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md,
    )
    assert rejection is None
    payload = {
        "world_updates": [
            {"rule_or_location": "Current rule", "description": "A valid constraint."}
        ]
    }
    chapter.metadata_json = {
        "story_bible_update_receipts": [
            {
                "status": "ok",
                "source_lineage": source,
                "candidate_payload": payload,
                "raw_extractions": [json.dumps(payload)],
            }
        ]
    }
    old = WorldRuleModel(
        id=uuid4(),
        project_id=project.id,
        rule_code="000-shell",
        name="Retained shell",
        description="A valid constraint.",
        metadata_json={},
    )
    projection.capture_bible_baseline(old, created=True)
    old.metadata_json = {
        **old.metadata_json,
        "story_bible_update_history": [
            {
                "source_lineage": source,
                "description_applied": True,
                "update": deepcopy(payload["world_updates"][0]),
            }
        ],
    }
    session.add(old)
    await session.commit()
    plan = await recovery.plan_world_rule_identity_recovery(session, project_id=project.id)
    await recovery.apply_world_rule_identity_recovery(
        session,
        project_id=project.id,
        expected_plan_sha256=plan["plan_sha256"],
    )
    await session.commit()
    target = await session.scalar(
        select(WorldRuleModel).where(WorldRuleModel.name == "Current rule")
    )
    assert old.description == "" and old.metadata_json["story_bible_update_history"]
    assert (
        await recovery.world_rule_effective_history(session, project_id=project.id, row_id=old.id)
        == []
    )
    return db, old, target


async def test_empty_effective_history_retires_proved_shell_and_reads_only(shifted, monkeypatch):
    book, old, target = shifted
    session, project, chapter, draft = book
    before = [_row_state(row) for row in (project, chapter, draft, old, target)]
    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("current filter must not write")

    monkeypatch.setattr(session, "flush", forbidden)
    monkeypatch.setattr(session, "commit", forbidden)
    monkeypatch.setattr(projection, "reproject_adopted_bible", forbidden)
    event.listen(session.bind.engine.sync_engine, "before_cursor_execute", record)
    try:
        assert await current.current_world_rules(session, project_id=project.id) == [target]
        assert await inspection._world_rules_for_current_read(
            session, project.id, [old, target]
        ) == [target]
    finally:
        event.remove(session.bind.engine.sync_engine, "before_cursor_execute", record)
    assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert [_row_state(row) for row in (project, chapter, draft, old, target)] == before


async def test_retained_real_snapshot_uses_effective_not_raw_active_sources(db):
    session, project, _ = await actual_retained_case(db)
    plan = await recovery.plan_world_rule_identity_recovery(session, project_id=project.id)
    await recovery.apply_world_rule_identity_recovery(
        session,
        project_id=project.id,
        expected_plan_sha256=plan["plan_sha256"],
    )
    await session.commit()
    before = await retained_raw(session, project), deepcopy(project.metadata_json)
    all_rules = list(
        await session.scalars(select(WorldRuleModel).where(WorldRuleModel.project_id == project.id))
    )
    visible = await current.current_world_rules(
        session, project_id=project.id, world_rules=all_rules
    )
    # Exact evidence fixture names are assertions, never routing special cases.
    retired_names = {"南城东路二十七号", "旧收音机临时播放限制"}
    assert retired_names <= {r.name for r in all_rules}
    assert retired_names.isdisjoint({r.name for r in visible})
    assert len(all_rules) == 17 and len(visible) == 13
    assert all(
        r in visible
        for r in all_rules
        if not r.metadata_json.get(projection.BASE, {}).get("created")
    )
    assert (await retained_raw(session, project), project.metadata_json) == before


def _chunk_state(chunk):
    state = _row_state(chunk)
    state["embedding"] = list(map(float, chunk.embedding))
    return state


async def _seed_chunks(shifted):
    book, old, target = shifted
    session, project, *_ = book
    await _tables(session, RetrievalChunkModel, VolumeModel)
    settings = load_settings()
    for source_type, source_id, text, pid in [
        ("world_rule", old.id, "Retained shell obsolete", project.id),
        ("world_rule", target.id, "Current rule constraint", project.id),
        ("canon_fact", uuid4(), "Retained shell audit canon", project.id),
        ("world_rule", uuid4(), "Foreign project's index", uuid4()),
    ]:
        await retrieval._upsert_source_chunks(
            session,
            settings,
            project_id=pid,
            source_type=source_type,
            source_id=source_id,
            text=text,
            metadata={"retained": True},
        )
    await session.commit()
    return settings


async def test_existing_index_search_filters_shell_without_erasing_audit(shifted):
    settings = await _seed_chunks(shifted)
    book, old, target = shifted
    session, project, *_ = book
    retained = await retained_raw(session, project)
    unrelated = {
        c.id: _chunk_state(c)
        for c in await session.scalars(select(RetrievalChunkModel))
        if c.project_id != project.id or c.source_type != "world_rule"
    }
    # Legacy derived rows lack a current-source receipt. The first configured
    # entry repairs those rows; subsequent searches must be stable and retain
    # raw audit plus unrelated index entries.
    await retrieval.ensure_project_retrieval_index(session, settings, project.id)
    assert await retained_raw(session, project) == retained
    assert {
        c.id: _chunk_state(c)
        for c in await session.scalars(select(RetrievalChunkModel))
        if c.id in unrelated
    } == unrelated
    before = [_chunk_state(c) for c in await session.scalars(select(RetrievalChunkModel))]
    for query in ("Retained shell", "unmatched lookup"):
        result = await retrieval.search_retrieval_for_project(
            session, settings, project, query, top_k=50
        )
        assert all(c.source_id != old.id for c in result.chunks)
    result = await retrieval.search_retrieval_for_project(
        session, settings, project, "Current rule", top_k=50
    )
    assert target.id in {c.source_id for c in result.chunks}
    audit_result = await retrieval.search_retrieval_for_project(
        session,
        settings,
        project,
        "Retained shell audit canon",
        top_k=50,
    )
    assert any(c.source_type == "canon_fact" for c in audit_result.chunks)
    assert [_chunk_state(c) for c in await session.scalars(select(RetrievalChunkModel))] == before


async def test_refresh_removes_only_noncurrent_project_rule_chunks(shifted):
    settings = await _seed_chunks(shifted)
    book, old, target = shifted
    session, project, *_ = book
    stale_current = await session.scalar(
        select(RetrievalChunkModel).where(RetrievalChunkModel.source_id == target.id)
    )
    stale_current.chunk_text = "Old wrong-name and wrong-body index payload"
    await session.commit()
    preserved = {
        c.id: _chunk_state(c)
        for c in await session.scalars(select(RetrievalChunkModel))
        if c.project_id != project.id or c.source_type != "world_rule"
    }
    audit = await retained_raw(session, project), deepcopy(project.metadata_json)
    assert await retrieval.refresh_story_bible_retrieval_index(session, settings, project.id) > 0
    await session.commit()
    chunks = list(await session.scalars(select(RetrievalChunkModel)))
    assert not any(c.source_id == old.id for c in chunks)
    assert any(c.source_id == target.id for c in chunks)
    current_chunks = [c for c in chunks if c.source_id == target.id]
    assert len(current_chunks) == 1
    assert current_chunks[0].chunk_text == (
        f"世界规则 {target.rule_code} {target.name}。"
        f"{target.description}。冲突后果\uff1a{target.story_consequence or '未定义'}。"
    )
    assert "Old wrong-name" not in current_chunks[0].chunk_text
    assert {c.id: _chunk_state(c) for c in chunks if c.id in preserved} == preserved
    assert (await retained_raw(session, project), project.metadata_json) == audit


async def test_context_and_inventory_share_filter_before_cap_and_keep_frontier(shifted):
    book, old, _target = shifted
    session, project, chapter, *_ = book
    await _overview_tables(session)
    for i in range(8):
        session.add(
            WorldRuleModel(
                project_id=project.id,
                rule_code=f"100-{i}",
                name=f"Authored {i}",
                description="An authored constraint.",
                metadata_json={},
            )
        )
    await session.commit()
    scene = SceneCardModel(
        chapter_id=chapter.id, project_id=project.id, scene_number=1, participants=[]
    )
    raw = await story_bible.load_scene_story_bible_context(
        session, project=project, chapter=chapter, scene=scene
    )
    payload = await current.load_scene_story_bible_context(
        session, project=project, chapter=chapter, scene=scene
    )
    expected = await current.current_world_rules(session, project_id=project.id)
    assert [r["rule_code"] for r in payload["world_rules"]] == [r.rule_code for r in expected[:8]]
    assert len(payload["world_rules"]) == 8
    assert old.rule_code in {r["rule_code"] for r in raw["world_rules"]}
    assert payload["volume_frontier"] == raw["volume_frontier"]
    assert {k: v for k, v in payload.items() if k != "world_rules"} == {
        k: v for k, v in raw.items() if k != "world_rules"
    }
    overview = await inspection.build_story_bible_overview(session, project.slug)
    assert old.rule_code not in {r.rule_code for r in overview.world_rules}
    assert len(overview.world_rules) == 9


@pytest.mark.parametrize(
    "defect", ["receipt_signature", "raw", "source", "stale_fields", "baseline"]
)
@pytest.mark.parametrize("consumer", ["filter", "search", "refresh", "project_refresh", "context"])
async def test_unknown_proof_fails_closed_before_index_mutation(shifted, defect, consumer):
    settings = await _seed_chunks(shifted)
    book, old, target = shifted
    session, project, chapter, *_ = book
    if defect == "receipt_signature":
        metadata = deepcopy(project.metadata_json)
        metadata[recovery.LEDGER][0]["schema"] = "unknown"
        project.metadata_json = metadata
    elif defect in {"raw", "source"}:
        metadata = deepcopy(chapter.metadata_json)
        receipt = metadata["story_bible_update_receipts"][0]
        if defect == "raw":
            receipt["raw_extractions"] = []
        else:
            receipt["source_lineage"]["source_body_sha256"] = "0" * 64
        chapter.metadata_json = metadata
    elif defect == "stale_fields":
        old.description = "Unproved stale field"
    else:
        metadata = deepcopy(target.metadata_json)
        metadata[projection.BASE]["fields"]["description"] = "Tampered baseline"
        target.metadata_json = metadata
    await session.commit()
    before = [_chunk_state(c) for c in await session.scalars(select(RetrievalChunkModel))]
    retained = await retained_raw(session, project), deepcopy(project.metadata_json)
    with pytest.raises(projection.BibleProjectionAttributionError):
        if consumer == "filter":
            await current.current_world_rules(session, project_id=project.id)
        elif consumer == "search":
            await retrieval.search_retrieval_for_project(
                session, settings, project, "Retained shell"
            )
        elif consumer == "project_refresh":
            await retrieval.refresh_project_retrieval_index(session, settings, project.slug)
        elif consumer == "refresh":
            await retrieval.refresh_story_bible_retrieval_index(session, settings, project.id)
        else:
            scene = SceneCardModel(
                chapter_id=chapter.id, project_id=project.id, scene_number=1, participants=[]
            )
            await current.load_scene_story_bible_context(
                session, project=project, chapter=chapter, scene=scene
            )
    assert [_chunk_state(c) for c in await session.scalars(select(RetrievalChunkModel))] == before
    assert (await retained_raw(session, project), project.metadata_json) == retained


async def test_writer_entrypoints_bind_current_adapter():
    from bestseller.services import context, drafts

    assert context.load_scene_story_bible_context is current.load_scene_story_bible_context
    assert drafts.load_scene_story_bible_context is current.load_scene_story_bible_context


async def test_context_frontier_excludes_hidden_rule_after_retirement(shifted):
    book, old, target = shifted
    session, project, chapter, *_ = book
    await _overview_tables(session)
    hidden = WorldRuleModel(
        project_id=project.id,
        rule_code="hidden",
        name="Hidden authored",
        description="Keep behind the existing frontier.",
        metadata_json={},
    )
    frontier = VolumeFrontierModel(
        project_id=project.id,
        volume_number=1,
        title="Test frontier",
        frontier_summary="Only the adopted visible constraint.",
        start_chapter_number=1,
        end_chapter_number=4,
        visible_rule_codes=[old.rule_code, target.rule_code],
        metadata_json={},
    )
    session.add_all([hidden, frontier])
    await session.commit()
    scene = SceneCardModel(
        chapter_id=chapter.id,
        project_id=project.id,
        scene_number=1,
        participants=[],
    )
    payload = await current.load_scene_story_bible_context(
        session,
        project=project,
        chapter=chapter,
        scene=scene,
    )
    assert [r["rule_code"] for r in payload["world_rules"]] == [target.rule_code]
    assert set(payload["volume_frontier"]["visible_rule_codes"]) == {
        old.rule_code,
        target.rule_code,
    }
    assert await session.get(WorldRuleModel, hidden.id) is hidden


async def test_refresh_with_no_current_rules_clears_only_project_world_index(shifted):
    from test_inspection_retired_world_rules import _withdraw

    settings = await _seed_chunks(shifted)
    book, _old, _target = shifted
    session, project, *_ = book
    await _withdraw(book)
    assert await current.current_world_rules(session, project_id=project.id) == []
    preserved = {
        c.id: _chunk_state(c)
        for c in await session.scalars(select(RetrievalChunkModel))
        if c.project_id != project.id or c.source_type != "world_rule"
    }
    retained = await retained_raw(session, project), deepcopy(project.metadata_json)
    await retrieval.refresh_story_bible_retrieval_index(session, settings, project.id)
    await session.commit()
    chunks = list(await session.scalars(select(RetrievalChunkModel)))
    assert not any(c.project_id == project.id and c.source_type == "world_rule" for c in chunks)
    assert {c.id: _chunk_state(c) for c in chunks} == preserved
    assert (await retained_raw(session, project), project.metadata_json) == retained
