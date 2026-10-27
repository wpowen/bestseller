"""Receipt success cannot conceal stale current knowledge in any progress reader."""
from copy import deepcopy
import socket

import pytest
from sqlalchemy import select

from bestseller.infra.db.models import CharacterModel
from bestseller.services import continuity, execution_windows, feedback, llm, story_bible_projection
from bestseller.services.adopted_knowledge_resume import read_adopted_knowledge
from bestseller.services.creation_configuration import ExecutionWindow
from bestseller.settings import load_settings
from bestseller.web.server import _load_adopted_chapter_progress
from test_adopted_bible_lineage import db as db, knowledge_db as knowledge_db
from test_adopted_bible_lineage import stub_llm as bible_stub, update
from test_adopted_knowledge_lineage import stub_llm
from test_knowledge_downstream_source_dependencies import _next_chapter

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('BESTSELLER_LINEAGE_PG', '0')

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Closure readback forbids network/provider calls')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(llm, '_call_litellm', forbidden)


async def seed(db, monkeypatch):
    session, project, chapter, draft = db
    settings = load_settings()
    stub_llm(monkeypatch, feedback, {})
    await feedback.extract_chapter_feedback(session, settings, project_id=project.id,
        chapter=chapter, chapter_md=draft.content_md, source_chapter_draft_version_id=draft.id)
    stub_llm(monkeypatch, continuity, {'facts': []})
    snapshot = await continuity.extract_chapter_state_snapshot(session, settings, project_id=project.id,
        chapter=chapter, chapter_md=draft.content_md, source_chapter_draft_version_id=draft.id)
    original_facts = deepcopy(snapshot.facts)
    original_raw, original_body = snapshot.raw_extraction, draft.content_md
    witness = continuity.snapshot_epistemic_witness(snapshot)
    source = {'project_id': project.id, 'chapter_id': chapter.id,
        'source_chapter_draft_version_id': draft.id, 'chapter_md': draft.content_md}
    proof = await continuity.validate_snapshot_epistemic_view(session, **source)
    assert proof['snapshot_sha256'] == witness
    assert continuity.snapshot_epistemic_witness(snapshot) == witness
    applied = await continuity.apply_snapshot_epistemic_view(session, **source,
        expected_snapshot_sha256=proof['snapshot_sha256'], admission_receipt=proof['admission_receipt'],
        actor='bible-projection-fixture', reason='Admit retained synthetic source without model calls')
    assert applied['status'] == 'applied' and applied['receipt']['provider_calls'] == 0
    assert all(snapshot.facts[key] == value for key, value in original_facts.items()
        if key not in {'epistemic_admitted_view', 'epistemic_admission_history'})
    assert snapshot.facts['epistemic_admission_history'][:-1] == original_facts['epistemic_admission_history']
    assert snapshot.raw_extraction == original_raw and draft.content_md == original_body
    assert draft.is_current and draft.promotion_state == 'promoted'
    assert await continuity.snapshot_evidence_current(session, project_id=project.id,
        chapter_number=chapter.chapter_number)
    bible_stub(monkeypatch)
    await update(db)
    await session.commit()


async def read(db):
    session, project, chapter, draft = db
    return (await read_adopted_knowledge(session, project_id=project.id, chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id, chapter_md=draft.content_md))[1]


async def test_same_source_receipt_cannot_green_stale_bible_and_cached_repair_closes_it(db, monkeypatch):
    await seed(db, monkeypatch)
    session, project, chapter, _ = db
    monkeypatch.setattr(execution_windows, 'read_window', lambda _: ExecutionWindow(start=1, end=1))
    assert (await read(db))['closed']
    character = await session.scalar(select(CharacterModel).where(
        CharacterModel.project_id == project.id, CharacterModel.name == '阿青'))
    character.knowledge_state_json = {'knows': ['来自旧投影的错误知识']}
    await session.commit()
    receipts = deepcopy(chapter.metadata_json)
    status = await read(db)
    assert status['feedback_complete'] and status['snapshot_complete']
    assert not status['bible_complete'] and not status['closed']
    window = await execution_windows.window_progress(session, project)
    page = (await _load_adopted_chapter_progress(session, [project.id]))[project.id]
    assert window['adopted'] == [1] and window['pending'] == [1]
    assert window['knowledge_closed'] == [] and not window['window_complete']
    assert page['adopted_chapters'] == 1 and page['knowledge']['closed_chapters'] == 0
    assert page['knowledge']['bible']['pending_chapters'] == 1
    assert chapter.metadata_json == receipts  # Readback never repairs.
    calls = bible_stub(monkeypatch)
    result = await update(db)
    assert result['projection_reconciled'] is True and not calls
    await session.commit()
    assert (await read(db))['closed']
    assert (await execution_windows.window_progress(session, project))['window_complete']
    assert (await _load_adopted_chapter_progress(session, [project.id]))[project.id]['knowledge']['closed_chapters'] == 1


async def test_window_and_page_plan_each_project_once_for_multiple_chapters(db, monkeypatch):
    await seed(db, monkeypatch)
    later = await _next_chapter(db, '观测员在下一站签收记录。')
    await seed(later, monkeypatch)
    monkeypatch.setattr(execution_windows, 'read_window', lambda _: ExecutionWindow(start=1, end=2))
    calls = []
    original = story_bible_projection._plan

    async def counted(*args, **kwargs):
        calls.append(1)
        return await original(*args, **kwargs)

    monkeypatch.setattr(story_bible_projection, '_plan', counted)
    window = await execution_windows.window_progress(db[0], db[1])
    assert window['knowledge_closed'] == [1, 2] and len(calls) == 1
    calls.clear()
    page = (await _load_adopted_chapter_progress(db[0], [db[1].id]))[db[1].id]
    assert page['knowledge']['closed_chapter_numbers'] == [1, 2] and len(calls) == 1
