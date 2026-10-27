"""A paid feedback receipt alone cannot close adopted-body knowledge."""
from copy import deepcopy
from asyncio import CancelledError
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterStateSnapshotModel,
    WorkflowRunModel,
)
from bestseller.services import continuity, execution_windows as windows, feedback
from bestseller.settings import load_settings
from test_adopted_knowledge_lineage import db as db, stub_llm

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def configured(db, monkeypatch):
    session, project, chapter, draft = db
    from bestseller.services.creation_configuration import ExecutionWindow
    monkeypatch.setattr(windows, 'read_window', lambda _: ExecutionWindow(start=1, end=1))
    async def runnable(*_):
        return None
    monkeypatch.setattr(windows, 'assert_window_runnable', runnable)
    connection = await session.connection()
    for model in (WorkflowRunModel, BookProductionControlModel):
        await connection.exec_driver_sql(str(CreateTable(model.__table__).compile(
            dialect=connection.dialect)).replace('::jsonb', ''))
    project.status = 'writing'
    settings = load_settings().model_copy(deep=True)
    settings.pipeline.enable_chapter_feedback = True
    await session.commit()
    return session, settings, project, chapter, draft


async def feedback_receipt(configured, monkeypatch, *, unresolved=False):
    session, settings, project, chapter, draft = configured
    payload = {'canon_facts': [{'subject': '钥匙', 'predicate': 'location',
        'value': '桌上', 'evidence': '未出现在正文里的转述', 'confidence': .8}]} if unresolved else {}
    calls = stub_llm(monkeypatch, feedback, payload)
    await feedback.extract_chapter_feedback(session, settings, project_id=project.id,
        chapter=chapter, chapter_md=draft.content_md, source_chapter_draft_version_id=draft.id)
    await session.commit()
    return calls


async def snapshot(configured, monkeypatch):
    session, settings, project, chapter, draft = configured
    calls = stub_llm(monkeypatch, continuity, {'facts': [], 'time_anchor': None})
    row = await continuity.extract_chapter_state_snapshot(session, settings,
        project_id=project.id, chapter=chapter, chapter_md=draft.content_md,
        source_chapter_draft_version_id=draft.id)
    await session.commit()
    assert row.extraction_status == 'ok_promoted'
    return row, calls


async def test_feedback_without_snapshot_remains_pending(configured, monkeypatch):
    await feedback_receipt(configured, monkeypatch)
    session, _, project, _, _ = configured
    state = await windows.window_progress(session, project)
    assert state['adopted'] == [1]
    assert state['knowledge_closed'] == []
    assert state['pending'] == [1] and not state['window_complete']


async def test_unresolved_feedback_with_snapshot_remains_pending(configured, monkeypatch):
    await feedback_receipt(configured, monkeypatch, unresolved=True)
    await snapshot(configured, monkeypatch)
    session, _, project, _, _ = configured
    assert not (await windows.window_progress(session, project))['window_complete']


async def test_snapshot_only_resume_reuses_feedback_and_body(configured, monkeypatch):
    await feedback_receipt(configured, monkeypatch)
    from support.adopted_bible_fixture import seed_empty_bible
    await seed_empty_bible(configured, monkeypatch)
    session, settings, project, chapter, draft = configured
    prior = deepcopy(chapter.metadata_json['knowledge_feedback_receipts'])
    body = draft.content_md
    async def forbidden(*_, **__):
        raise AssertionError('completed feedback must not be re-extracted')
    monkeypatch.setattr(feedback, 'extract_chapter_feedback', forbidden)
    calls = stub_llm(monkeypatch, continuity, {'facts': []})
    state = await windows.window_progress(session, project)
    await windows.resume_window_knowledge(session, settings, project, state)
    state = await windows.window_progress(session, project)
    assert state['knowledge_closed'] == [1] and state['window_complete']
    assert len(calls) == 1 and draft.content_md == body
    assert chapter.metadata_json['knowledge_feedback_receipts'] == prior
    await windows.resume_window_knowledge(session, settings, project, state)
    assert len(calls) == 1
    workflows = list(await session.scalars(select(WorkflowRunModel)))
    assert len(workflows) == 1 and workflows[0].status == 'completed'


async def test_failed_snapshot_cannot_leave_completed_recovery_workflow(configured, monkeypatch):
    await feedback_receipt(configured, monkeypatch)
    session, settings, project, chapter, _ = configured
    prior = deepcopy(chapter.metadata_json['knowledge_feedback_receipts'])
    stub_llm(monkeypatch, continuity, {'not_facts': []})
    with pytest.raises(ValueError, match='knowledge_resume_incomplete'):
        await windows.resume_window_knowledge(session, settings, project,
            await windows.window_progress(session, project))
    workflows = list(await session.scalars(select(WorkflowRunModel)))
    assert len(workflows) == 1 and workflows[0].status == 'machine_blocked'
    assert chapter.metadata_json['knowledge_feedback_receipts'] == prior
    assert not (await windows.window_progress(session, project))['window_complete']


@pytest.mark.parametrize('fault', ['project_id', 'chapter_id', 'source_version_no',
                                  'source_body_sha256', 'is_usable', 'status'])
async def test_snapshot_must_bind_exact_owner_version_body_and_status(configured, monkeypatch, fault):
    await feedback_receipt(configured, monkeypatch)
    row, _ = await snapshot(configured, monkeypatch)
    session, _, project, chapter, draft = configured
    if fault == 'status':
        row.extraction_status = 'failed:fallback'
    else:
        facts = deepcopy(row.facts)
        facts['snapshot_contract'][fault] = False if fault == 'is_usable' else 'wrong-source'
        row.facts = facts
    await session.flush()
    state = await windows.window_progress(session, project)
    assert not state['window_complete'] and state['knowledge_closed'] == []
    from bestseller.web.server import _adopted_knowledge_evidence
    assert _adopted_knowledge_evidence(chapter, draft, row, 'promoted')[:2] == (True, False)


async def test_recovery_rejects_changed_source_before_model(configured, monkeypatch):
    await feedback_receipt(configured, monkeypatch)
    session, settings, project, _, draft = configured
    state = await windows.window_progress(session, project)
    draft.content_md += '采用正文已变化。'
    await session.flush()
    calls = stub_llm(monkeypatch, continuity, {'facts': []})
    with pytest.raises(ValueError, match='knowledge_source_changed'):
        await windows.resume_window_knowledge(session, settings, project, state)
    assert calls == []
    assert not list(await session.scalars(select(WorkflowRunModel)))


async def test_cancellation_preserves_partial_success_and_closes_workflow(configured, monkeypatch):
    await feedback_receipt(configured, monkeypatch)
    session, settings, project, chapter, _ = configured
    prior = deepcopy(chapter.metadata_json['knowledge_feedback_receipts'])
    async def cancelled(*_, **__):
        raise CancelledError
    monkeypatch.setattr(continuity, 'extract_chapter_state_snapshot', cancelled)
    with pytest.raises(CancelledError):
        await windows.resume_window_knowledge(session, settings, project,
            await windows.window_progress(session, project))
    await session.refresh(chapter)
    assert chapter.metadata_json['knowledge_feedback_receipts'] == prior
    runs = list(await session.scalars(select(WorkflowRunModel)))
    assert len(runs) == 1 and runs[0].status == 'cancelled'
    assert not list(await session.scalars(select(ChapterStateSnapshotModel)))


async def test_other_chapter_receipt_cannot_close_identical_body(configured, monkeypatch):
    await feedback_receipt(configured, monkeypatch)
    await snapshot(configured, monkeypatch)
    session, _, project, chapter, _ = configured
    metadata = deepcopy(chapter.metadata_json)
    metadata['knowledge_feedback_receipts'][-1]['source_lineage']['chapter_id'] = str(uuid4())
    chapter.metadata_json = metadata
    await session.flush()
    assert not (await windows.window_progress(session, project))['window_complete']
