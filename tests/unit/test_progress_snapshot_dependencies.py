"""The actual SQL progress projection rejects stale predecessor snapshots."""
from copy import deepcopy

import pytest
from test_adopted_knowledge_lineage import db as db
from test_adopted_knowledge_lineage import snapshot, stub_llm
from test_knowledge_downstream_source_dependencies import _next_chapter, _replace_source

from bestseller.services import continuity, feedback
from bestseller.settings import load_settings
from bestseller.web.server import _load_adopted_chapter_progress
from support.adopted_bible_fixture import seed_empty_bible

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def populate(db, monkeypatch):
    session, project, chapter, draft = db
    stub_llm(monkeypatch, continuity, {'facts': []})
    row = await snapshot(db)
    stub_llm(monkeypatch, feedback, {})
    await feedback.extract_chapter_feedback(session, load_settings(), project_id=project.id,
        chapter=chapter, chapter_md=draft.content_md, source_chapter_draft_version_id=draft.id)
    await seed_empty_bible((session, load_settings(), project, chapter, draft), monkeypatch)
    await session.flush()
    return row


async def test_actual_progress_invalidates_later_snapshot_after_prior_replacement(db, monkeypatch):
    await populate(db, monkeypatch)
    later = await _next_chapter(db, '另一个观测员收到巡检通知。')
    later_snapshot = await populate(later, monkeypatch)
    original = deepcopy(later_snapshot.facts)
    before = (await _load_adopted_chapter_progress(db[0], [db[1].id]))[db[1].id]
    assert before['knowledge']['closed_chapters'] == 2
    replacement = await _replace_source(db, '观测员检查了另一处封签。')
    await populate(replacement, monkeypatch)
    state = (await _load_adopted_chapter_progress(db[0], [db[1].id]))[db[1].id]
    assert state['adopted_chapters'] == 2
    assert state['knowledge']['snapshot_chapters'] == 1
    assert state['knowledge']['closed_chapter_numbers'] == [1]
    assert state['knowledge']['pending_chapters'] == 1
    assert later_snapshot.facts == original  # Projection never repairs history.
    await populate(later, monkeypatch)
    repaired = (await _load_adopted_chapter_progress(db[0], [db[1].id]))[db[1].id]
    assert repaired['knowledge']['closed_chapters'] == 2
    assert (
        later_snapshot.facts['snapshot_history'][-1]['facts']['snapshot_contract']
        == original['snapshot_contract']
    )


async def test_legacy_later_snapshot_is_pending_while_original_record_survives(db, monkeypatch):
    await populate(db, monkeypatch)
    later = await _next_chapter(db, '巡检继续进行。')
    row = await populate(later, monkeypatch)
    facts = deepcopy(row.facts)
    facts['snapshot_contract'].pop('snapshot_dependencies')
    row.facts = facts
    await db[0].flush()
    state = (await _load_adopted_chapter_progress(db[0], [db[1].id]))[db[1].id]
    assert state['adopted_chapters'] == 2
    assert state['knowledge']['closed_chapter_numbers'] == [1]
    assert row.facts == facts
