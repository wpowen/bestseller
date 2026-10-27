"""Actual feedback -> Bible composition under adopted-body replacement."""

from copy import deepcopy

import pytest
from sqlalchemy import select
from story_bible_read_views_test_support import create_story_bible_read_view_tables
from test_character_snapshot_sources import (
    configured as configured,
    db as db,
    extract,
    local_only as local_only,
)
from test_knowledge_downstream_source_dependencies import _replace_source
from test_knowledge_downstream_source_dependencies import _next_chapter
from test_adopted_bible_lineage import stub_llm, update

from bestseller.infra.db.models import CharacterStateSnapshotModel
from bestseller.services.character_snapshot_sources import read_character_snapshot_knowledge

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
async def native_read_view_schema(db):
    session = db[0]
    assert session.bind.dialect.name == "sqlite"
    await create_story_bible_read_view_tables(session)


@pytest.mark.parametrize("replacement_knowledge", [[], ["替换稿的观测"]])
async def test_feedback_is_not_captured_as_permanent_bible_baseline(
    configured, monkeypatch, replacement_knowledge,
):
    session, project, chapter, original, character = configured
    character.knowledge_state_json = {"knows": ["初始观测"], "private": {"keep": None}}
    await session.commit()
    await extract(configured, monkeypatch, {"character_states": [{
        "character_name": character.name, "knowledge_gained": ["旧稿独有观测"],
    }]})
    original_snapshot = await session.scalar(select(CharacterStateSnapshotModel))
    raw_before = (deepcopy(original_snapshot.source_binding), original_snapshot.beliefs,
                  original_snapshot.emotional_state, original_snapshot.notes)
    stub_llm(monkeypatch, {})
    assert (await update(configured[:4]))["extraction_status"] == "empty"
    await session.commit()
    assert character.knowledge_state_json["knows"] == ["初始观测", "旧稿独有观测"]

    replacement = await _replace_source(configured[:4], "替换后的正文没有旧稿观测。")
    await extract(configured, monkeypatch, {"character_states": [{
        "character_name": character.name, "knowledge_gained": replacement_knowledge,
    }]} if replacement_knowledge else {}, replacement[3])
    stub_llm(monkeypatch, {})
    assert (await update(replacement))["extraction_status"] == "empty"
    await session.refresh(character)
    assert character.knowledge_state_json["knows"] == ["初始观测", *replacement_knowledge]
    assert character.knowledge_state_json.get("falsely_believes", []) == []
    assert character.knowledge_state_json["private"] == {"keep": None}
    current = await read_character_snapshot_knowledge(
        session, project_id=project.id, character=character,
    )
    assert current.get("knows", []) == replacement_knowledge
    await session.refresh(original_snapshot)
    assert (original_snapshot.source_binding, original_snapshot.beliefs,
            original_snapshot.emotional_state, original_snapshot.notes) == raw_before
    receipts = deepcopy(chapter.metadata_json)
    calls = stub_llm(monkeypatch, {})
    assert (await update(replacement))["extraction_status"] == "already_applied"
    assert calls == [] and chapter.metadata_json == receipts


async def test_feedback_beliefs_and_later_observations_keep_chapter_order(configured, monkeypatch):
    session, project, chapter, original, character = configured
    character.knowledge_state_json = {"knows": ["初始观测"]}
    await session.commit()
    await extract(configured, monkeypatch, {"character_states": [{
        "character_name": character.name, "knowledge_gained": ["旧稿观测"],
        "beliefs_gained": ["待证实推测"],
    }]})
    stub_llm(monkeypatch, {})
    await update(configured[:4])
    await session.commit()
    assert "待证实推测" not in character.knowledge_state_json["knows"]
    assert character.knowledge_state_json["beliefs"] == ["待证实推测"]

    later = await _next_chapter(configured[:4], "下一章的观测员核对了推测。")
    later_case = (*later, character)
    await extract(later_case, monkeypatch, {"character_states": [{
        "character_name": character.name, "knowledge_gained": ["后章独立观测"],
        "beliefs_invalidated": ["待证实推测"],
    }]})
    stub_llm(monkeypatch, {})
    await update(later)
    await session.commit()
    assert character.knowledge_state_json["beliefs"] == []

    replacement = await _replace_source(configured[:4], "第一章的新稿没有观测。")
    await extract(configured, monkeypatch, {}, replacement[3])
    stub_llm(monkeypatch, {})
    await update(replacement)
    await session.refresh(character)
    assert character.knowledge_state_json["knows"] == ["初始观测", "后章独立观测"]
    assert character.knowledge_state_json.get("beliefs", []) == []
    assert len(list(await session.scalars(select(CharacterStateSnapshotModel)))) == 2
