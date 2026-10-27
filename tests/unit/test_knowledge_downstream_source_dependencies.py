"""Downstream source isolation, using isolated SQL and local model stubs.

Source replacements create new draft versions; all assertions must pass.
"""

from __future__ import annotations

import socket

import pytest
from sqlalchemy import select

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    CharacterModel,
    RelationshipModel,
)
from bestseller.services import continuity, llm
from test_adopted_bible_lineage import (
    db as bible_db,
    payload,
    stub_llm as stub_bible,
    update,
)
from test_adopted_knowledge_lineage import (
    db as knowledge_db,
    snapshot,
    stub_llm as stub_snapshot,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class DownstreamSourceDependencyAssertion(AssertionError):
    """An observed source-isolation violation, never an infrastructure error."""


def _require_isolation(condition, message):
    if not condition:
        raise DownstreamSourceDependencyAssertion(message)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    # The reused fixture otherwise permits an opt-in PostgreSQL connection.
    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")

    def forbidden_network(*_args, **_kwargs):
        raise RuntimeError("Downstream source tests forbid network connections")

    async def forbidden_gateway(*_args, **_kwargs):
        raise RuntimeError("Install a local deterministic stub before extraction")

    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    monkeypatch.setattr(llm, "complete_text", forbidden_gateway)
    monkeypatch.setattr(continuity, "complete_text", forbidden_gateway)


@pytest.fixture
async def db(bible_db):
    assert bible_db[0].bind.dialect.name == "sqlite"
    assert "test" in bible_db[0].bind.engine.url.database
    return bible_db


async def _replace_source(db, body):
    session, project, chapter, old = db
    old.promotion_state, old.is_current = "superseded", False
    await session.flush()
    new = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=old.version_no + 1,
        content_md=body,
        promotion_state="promoted",
        is_current=True,
        promotion_metadata={},
    )
    session.add(new)
    await session.flush()
    return session, project, chapter, new


async def _next_chapter(db, body):
    session, project, previous, _draft = db
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=previous.chapter_number + 1,
        chapter_goal="downstream source isolation test",
        metadata_json={},
    )
    session.add(chapter)
    await session.flush()
    draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md=body,
        promotion_state="promoted",
        is_current=True,
        promotion_metadata={},
    )
    session.add(draft)
    await session.flush()
    return session, project, chapter, draft


async def test_bible_replacement_retires_superseded_source_knowledge(db, monkeypatch):
    calls = stub_bible(monkeypatch)
    first = await update(db)
    assert first["extraction_status"] == "ok" and len(calls) == 1
    character = await db[0].scalar(
        select(CharacterModel).where(CharacterModel.name == "阿青")
    )
    assert "钥匙在桌上" in character.knowledge_state_json["knows"]

    replacement = await _replace_source(db, "阿青独自离开，未获得钥匙。")
    calls = stub_bible(monkeypatch, {})
    outcome = await update(replacement)
    assert outcome["extraction_status"] == "empty" and len(calls) == 1
    assert db[3].promotion_state == "superseded"
    assert outcome["source_lineage"]["source_chapter_draft_version_id"] == str(
        replacement[3].id
    )
    await db[0].refresh(character)
    _require_isolation(
        "钥匙在桌上" not in character.knowledge_state_json.get("knows", []),
        "A superseded draft's exclusive knowledge survived a successful empty B update",
    )


async def test_older_chapter_repair_preserves_later_relationship_projection(db, monkeypatch):
    stub_bible(monkeypatch)
    assert (await update(db))["extraction_status"] == "ok"
    later = await _next_chapter(db, "阿青与阿白决裂。")
    later_payload = payload()
    later_payload["relationship_updates"][0].update(
        change="决裂", affinity_direction="down"
    )
    stub_bible(monkeypatch, later_payload)
    assert (await update(later))["extraction_status"] == "ok"
    relationship = await db[0].scalar(select(RelationshipModel))
    assert relationship.last_changed_chapter_no == 2
    assert relationship.relationship_type == "决裂"

    repaired = await _replace_source(db, "阿青把钥匙放在桌上，又检查了门锁。")
    stub_bible(monkeypatch)
    repair_result = await update(repaired)
    stub_bible(monkeypatch, later_payload)
    retry_result = await update(later)
    await db[0].refresh(relationship)
    _require_isolation(
        relationship.last_changed_chapter_no == 2
        and relationship.relationship_type == "决裂",
        f"Later relationship regressed to chapter {relationship.last_changed_chapter_no}: "
        f"{relationship.relationship_type}; repair={repair_result['extraction_status']}, "
        f"later retry={retry_result['extraction_status']}",
    )


def _countdown(value, quote=None):
    return {
        "facts": [
            {
                "name": "倒计时",
                "value": value,
                "unit": "小时",
                "kind": "countdown",
                "source_quote": quote,
            }
        ]
    }


@pytest.mark.parametrize("cache_path", ["current", "history"])
async def test_snapshot_cache_tracks_predecessor_source(db, monkeypatch, cache_path):
    first = await _replace_source(db, "计时器显示二十小时。")
    stub_snapshot(monkeypatch, continuity, _countdown("20", "二十小时"))
    assert (await snapshot(first)).extraction_status == "ok_promoted"
    later = await _next_chapter(first, "阿青静坐不语。")
    stub_snapshot(monkeypatch, continuity, _countdown("20"))
    original = await snapshot(later)
    assert original.facts["facts"][0]["value"] == "20"

    changed = await _replace_source(first, "计时器显示十小时。")
    stub_snapshot(monkeypatch, continuity, _countdown("10", "十小时"))
    assert (await snapshot(changed)).extraction_status == "ok_promoted"
    predecessor = await continuity.load_previous_chapter_snapshot(
        db[0], project_id=db[1].id, current_chapter_number=2
    )
    assert predecessor is not None and predecessor.facts[0].value == "10"

    if cache_path == "history":
        newer = await _replace_source(later, "阿青站起来，仍未查看计时器。")
        stub_snapshot(monkeypatch, continuity, _countdown("10"))
        assert (await snapshot(newer)).facts["facts"][0]["value"] == "10"
        # Re-adopt the original version; do not mutate either version's body.
        newer[3].promotion_state, newer[3].is_current = "superseded", False
        await db[0].flush()
        later[3].promotion_state, later[3].is_current = "promoted", True
        await db[0].flush()

    calls = stub_snapshot(monkeypatch, continuity, _countdown("10"))
    await snapshot(later)
    authority = await continuity.load_previous_chapter_snapshot(
        db[0], project_id=db[1].id, current_chapter_number=3
    )
    # Safe behavior may invalidate the dependent snapshot or recompute it.
    _require_isolation(
        authority is None or authority.facts[0].value == "10",
        f"{cache_path} cache exposed countdown 20 after predecessor changed to 10; "
        f"refresh stub calls={len(calls)}",
    )
