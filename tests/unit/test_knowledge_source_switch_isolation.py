"""Exact-source knowledge switching with real isolated SQL and no model access."""

from copy import deepcopy
import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_adopted_knowledge_lineage import db as db

from bestseller.domain.feedback import ChapterFeedbackPayload, ChapterFeedbackResult
from bestseller.infra.db.models import (
    CanonFactModel, ChapterDraftVersionModel, ChapterModel, ProjectModel, TimelineEventModel,
)
from bestseller.services import continuity, feedback, llm, story_bible
from bestseller.services import knowledge_evidence_reconciliation as reconciliation
from bestseller.services.critic_evidence_gate import build_critic_body_anchors
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def no_models(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("No model gateway may run in knowledge isolation tests")

    for module in (llm, feedback, continuity, reconciliation):
        monkeypatch.setattr(module, "complete_text", forbidden)


def unresolved_payload():
    return ChapterFeedbackPayload.model_validate({
        "canon_facts": [{"subject": "钥匙", "predicate": "location", "value": "猜测",
                         "evidence": "不在正文的旧引文"}],
        "timeline_events": [{"event_name": "放下钥匙", "evidence": "另一个错误引文"}],
    })


async def apply_base(db, payload):
    session, project, chapter, draft = db
    source, rejection = await feedback.validate_adopted_knowledge_source(
        session, project_id=project.id, chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id, chapter_md=draft.content_md,
    )
    assert rejection is None
    operation = {
        "operation_id": f"isolated-feedback:{draft.id}:{source['source_body_sha256']}",
        "fallback_used": False, "llm_run_id": str(uuid4()),
    }
    result = ChapterFeedbackResult(
        project_id=project.id, chapter_id=chapter.id, chapter_number=chapter.chapter_number,
        source_lineage=source,
    )
    await feedback._apply_verified_feedback(
        session, chapter=chapter, chapter_md=draft.content_md, result=result,
        payload=payload, llm_receipts=[operation],
    )
    return deepcopy(chapter.metadata_json["knowledge_feedback_receipts"][-1])


async def reconcile(db):
    session, _, chapter, draft = db
    return await reconciliation.reconcile_feedback_evidence(
        session, load_settings(), chapter=chapter, chapter_md=draft.content_md,
        source_chapter_draft_version_id=draft.id, workflow_run_id=uuid4(),
    )


async def seed_cached_evidence(db):
    session, _, chapter, draft = db
    receipt = await apply_base(db, unresolved_payload())
    pending = reconciliation.unresolved_evidence(receipt, draft.content_md)
    anchor = build_critic_body_anchors(draft.content_md)[0]["id"]
    # Synthetic retained decision fixture, not a provider call or production receipt.
    previous = {
        "status": "applied", "source_payload_sha256": reconciliation.payload_digest(receipt),
        "source_body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest(),
        "source_draft_id": str(draft.id),
        "source_lineage": {**receipt["source_lineage"], "operation_id": "unit-retained-selection"},
        "dispositions": {key: {"item_id": key, "supported": True, "anchor_ids": [anchor],
                               "reason": "保留的隔离测试段落选择"} for key in pending},
        "raw_model_response": "synthetic retained fixture",
    }
    chapter.metadata_json = {
        **chapter.metadata_json, "knowledge_feedback_evidence_receipts": [previous],
    }
    await session.flush()
    assert (await reconcile(db))["status"] == "applied"
    return receipt


async def current_projection(db):
    session, project, chapter, _ = db
    facts = list(await session.scalars(select(CanonFactModel).where(
        CanonFactModel.project_id == project.id, CanonFactModel.source_chapter_id == chapter.id,
        CanonFactModel.is_current.is_(True),
    )))
    events = [event for event in await session.scalars(select(TimelineEventModel).where(
        TimelineEventModel.project_id == project.id, TimelineEventModel.chapter_id == chapter.id,
    )) if event.metadata_json.get("knowledge_lineage_status") == "current"]
    return facts, events


async def test_cached_reconciliation_restores_a_after_b_without_model_or_history_loss(db):
    session, project, chapter, draft = db
    await seed_cached_evidence(db)
    body_a = draft.content_md
    original_receipts = deepcopy(chapter.metadata_json["knowledge_feedback_evidence_receipts"])
    assert tuple(map(len, await current_projection(db))) == (1, 1)
    draft.content_md = "阿青把钥匙放在柜里。"
    await session.flush()
    await apply_base(db, ChapterFeedbackPayload())
    assert tuple(map(len, await current_projection(db))) == (0, 0)
    draft.content_md = body_a
    await session.flush()
    result = await feedback.extract_chapter_feedback(
        session, load_settings(), project_id=project.id, chapter=chapter,
        chapter_md=body_a, source_chapter_draft_version_id=draft.id, workflow_run_id=uuid4(),
    )
    assert result.extraction_status in {"ok", "already_applied"}
    facts, events = await current_projection(db)
    assert len(facts) == len(events) == 1
    assert facts[0].value_json["value"] == body_a
    assert events[0].event_name in body_a
    assert chapter.metadata_json["knowledge_feedback_evidence_receipts"][:len(original_receipts)] == original_receipts
    counts = tuple(map(len, await current_projection(db)))
    assert (await reconcile(db))["status"] == "already_complete"
    assert tuple(map(len, await current_projection(db))) == counts


async def test_historical_feedback_receipt_cannot_close_a_before_base_a_is_restored(db):
    session, _, chapter, draft = db
    receipt_a = await seed_cached_evidence(db)
    body_a = draft.content_md
    draft.content_md = "阿青把钥匙放在柜里。"
    await session.flush()
    await apply_base(db, ChapterFeedbackPayload())
    assert not reconciliation.feedback_evidence_complete(chapter, receipt_a, body_a)
    draft.content_md = body_a
    await session.flush()
    assert await reconcile(db) == {"status": "not_applicable", "reason": "no_matching_feedback_receipt"}
    assert tuple(map(len, await current_projection(db))) == (0, 0)


@pytest.mark.parametrize("field", ["project_id", "chapter_id", "source_version_no"])
async def test_feedback_cache_requires_full_owner_and_version_binding(db, field):
    _, _, chapter, _ = db
    receipt = await apply_base(db, ChapterFeedbackPayload())
    source = deepcopy(receipt["source_lineage"])
    receipt["source_lineage"][field] = 999 if field == "source_version_no" else str(uuid4())
    chapter.metadata_json = {"knowledge_feedback_receipts": [receipt]}
    assert feedback._matching_feedback_receipt(chapter, source) is None
    assert feedback._cached_feedback_receipt(chapter, source) is None


async def test_latest_feedback_application_rechecked_before_projection_writes(db, monkeypatch):
    session, _, chapter, draft = db
    await seed_cached_evidence(db)
    # Force reapplication of retained selections to a new base receipt epoch.
    metadata = deepcopy(chapter.metadata_json)
    metadata['knowledge_feedback_receipts'][-1]['recorded_at'] = 'unit-new-application'
    chapter.metadata_json = metadata
    await session.flush()
    original = reconciliation.resolve_decisions

    def switch_receipt(*args):
        decisions = original(*args)
        current = deepcopy(chapter.metadata_json)
        current['knowledge_feedback_receipts'][-1]['candidate_payload']['timeline_events'] = []
        chapter.metadata_json = current
        return decisions

    monkeypatch.setattr(reconciliation, 'resolve_decisions', switch_receipt)
    prior_evidence = deepcopy(chapter.metadata_json['knowledge_feedback_evidence_receipts'])
    before = tuple(map(len, await current_projection(db)))
    with pytest.raises(ValueError, match='feedback_evidence_receipt_changed'):
        await reconcile(db)
    assert tuple(map(len, await current_projection(db))) == before
    assert chapter.metadata_json['knowledge_feedback_evidence_receipts'] == prior_evidence


async def another_source(db, *, same_project=False):
    session, original, _, draft = db
    project = original
    if not same_project:
        project = ProjectModel(slug=f'isolation-test-{uuid4().hex}', title='另一项目',
                               genre='test', target_word_count=10000, target_chapters=4)
        session.add(project)
        await session.flush()
    chapter = ChapterModel(project_id=project.id, chapter_number=2 if same_project else 1,
                           chapter_goal='独立来源', metadata_json={})
    session.add(chapter)
    await session.flush()
    other_draft = ChapterDraftVersionModel(
        project_id=project.id, chapter_id=chapter.id, version_no=1,
        content_md=draft.content_md, promotion_state='promoted', is_current=True,
    )
    session.add(other_draft)
    await session.flush()
    return session, project, chapter, other_draft


@pytest.mark.parametrize('service', ['feedback', 'snapshot', 'bible', 'reconciliation'])
@pytest.mark.parametrize('mismatch', ['project', 'chapter', 'draft', 'body'])
async def test_real_service_rejects_wrong_source_before_any_gateway(db, service, mismatch):
    session, project, chapter, draft = db
    other = await another_source(db, same_project=mismatch == 'chapter')
    source = draft.id
    body = draft.content_md
    if mismatch == 'project':
        project = other[1]
    elif mismatch == 'chapter':
        chapter = other[2]
    elif mismatch == 'draft':
        source = other[3].id
    else:
        body += '不属于已采用版本的附加句。'
    if service == 'feedback':
        result = await feedback.extract_chapter_feedback(
            session, load_settings(), project_id=project.id, chapter=chapter,
            chapter_md=body, source_chapter_draft_version_id=source,
        )
        assert result.extraction_status == 'rejected_source'
    elif service == 'snapshot':
        result = await continuity.extract_chapter_state_snapshot(
            session, load_settings(), project_id=project.id, chapter=chapter,
            chapter_md=body, source_chapter_draft_version_id=source,
        )
        assert result.extraction_status == 'rejected_source'
    elif service == 'bible':
        result = await story_bible.update_story_bible_from_chapter(
            session, load_settings(), project=project, chapter=chapter,
            chapter_text=body, source_chapter_draft_version_id=source,
        )
        assert result['extraction_status'] == 'rejected_source'
    else:
        # Reconciliation derives ownership from chapter, not a caller project.
        if mismatch == 'project':
            chapter = other[2]
        with pytest.raises(ValueError, match='feedback_evidence_source_rejected'):
            await reconciliation.reconcile_feedback_evidence(
                session, load_settings(), chapter=chapter, chapter_md=body,
                source_chapter_draft_version_id=source, workflow_run_id=uuid4(),
            )
    assert not list(await session.scalars(select(CanonFactModel)))
    assert not list(await session.scalars(select(TimelineEventModel)))


async def test_continuous_chapters_and_earlier_rewrite_preserve_later_canon(db):
    session, _, first, first_draft = db
    payload = ChapterFeedbackPayload.model_validate({
        'canon_facts': [{'subject': '钥匙', 'predicate': 'location', 'value': '桌上',
                         'evidence': first_draft.content_md}],
        'timeline_events': [{'event_name': '第一章放钥匙', 'evidence': first_draft.content_md}],
    })
    await apply_base(db, payload)
    second_db = await another_source(db, same_project=True)
    second_db[3].content_md = '阿青把钥匙收进柜里。'
    await session.flush()
    later_payload = ChapterFeedbackPayload.model_validate({
        'canon_facts': [{'subject': '钥匙', 'predicate': 'location', 'value': '柜里',
                         'evidence': second_db[3].content_md}],
        'timeline_events': [{'event_name': '第二章收钥匙', 'evidence': second_db[3].content_md}],
    })
    await apply_base(second_db, later_payload)
    facts = list(await session.scalars(select(CanonFactModel).order_by(CanonFactModel.valid_from_chapter_no)))
    assert len(facts) == 2 and not facts[0].is_current and facts[1].is_current
    assert facts[0].valid_to_chapter_no == 2 and facts[1].source_chapter_id == second_db[2].id
    later_id = facts[1].id
    first_draft.content_md = '阿青把钥匙留在窗边。'
    await session.flush()
    changed = ChapterFeedbackPayload.model_validate({
        'canon_facts': [{'subject': '钥匙', 'predicate': 'location', 'value': '窗边',
                         'evidence': first_draft.content_md}],
        'timeline_events': [{'event_name': '第一章新稿', 'evidence': first_draft.content_md}],
    })
    result = await apply_base(db, changed)
    assert result['result']['candidate_canon_facts'][0]['reason'] == 'historical_writeback'
    current = list(await session.scalars(select(CanonFactModel).where(CanonFactModel.is_current.is_(True))))
    assert len(current) == 1 and current[0].id == later_id
    assert current[0].value_json['value'] == '柜里'
    assert facts[0].valid_to_chapter_no == 2
    later_facts, later_events = await current_projection(second_db)
    assert len(later_facts) == len(later_events) == 1
    assert later_events[0].event_name == '第二章收钥匙'
    first_events = (await current_projection(db))[1]
    assert len(first_events) == 1 and first_events[0].event_name == '第一章新稿'


@pytest.mark.parametrize('field', ['project_id', 'chapter_id', 'source_chapter_draft_version_id'])
async def test_reconciliation_cache_rejects_copied_foreign_lineage(db, field):
    session, _, chapter, draft = db
    receipt = await seed_cached_evidence(db)
    current = deepcopy(chapter.metadata_json)
    current['knowledge_feedback_evidence_receipts'][-1]['source_lineage'][field] = str(uuid4())
    current['knowledge_feedback_evidence_receipts'] = current['knowledge_feedback_evidence_receipts'][-1:]
    chapter.metadata_json = current
    await session.flush()
    assert reconciliation._cached_evidence_receipt(chapter, receipt, draft.content_md) is None
    assert not reconciliation.feedback_evidence_complete(chapter, receipt, draft.content_md)


async def test_legacy_v2_binding_is_append_only_and_idempotent_without_projection_writes(db):
    from sqlalchemy import event

    session, _, chapter, draft = db
    receipt = await seed_cached_evidence(db)
    metadata = deepcopy(chapter.metadata_json)
    metadata['knowledge_feedback_evidence_receipts'][-1].pop('source_feedback_receipt_sha256')
    chapter.metadata_json = metadata
    await session.flush()
    before = deepcopy(chapter.metadata_json)
    before_ids = [[row.id for row in rows] for rows in await current_projection(db)]
    writes = []
    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
            writes.append(statement.lower())
    event.listen(session.bind.sync_engine, 'before_cursor_execute', record)
    try:
        result = await reconciliation.bind_legacy_feedback_evidence_receipt(
            session, chapter=chapter, chapter_md=draft.content_md,
            source_chapter_draft_version_id=draft.id,
        )
        assert result['status'] == 'bound'
        assert result['canon_rows_changed'] == result['timeline_rows_changed'] == 0
        assert not any('canon_facts' in sql or 'timeline_events' in sql for sql in writes)
        assert reconciliation.feedback_evidence_complete(chapter, receipt, draft.content_md)
        assert chapter.metadata_json['knowledge_feedback_evidence_receipts'][:-1] == before['knowledge_feedback_evidence_receipts']
        assert chapter.metadata_json['knowledge_feedback_receipts'] == before['knowledge_feedback_receipts']
        assert [[row.id for row in rows] for rows in await current_projection(db)] == before_ids
        again = await reconciliation.bind_legacy_feedback_evidence_receipt(
            session, chapter=chapter, chapter_md=draft.content_md,
            source_chapter_draft_version_id=draft.id,
        )
        assert again['status'] == 'already_complete'
        assert len(chapter.metadata_json['knowledge_feedback_evidence_receipts']) == len(before['knowledge_feedback_evidence_receipts']) + 1
    finally:
        event.remove(session.bind.sync_engine, 'before_cursor_execute', record)
