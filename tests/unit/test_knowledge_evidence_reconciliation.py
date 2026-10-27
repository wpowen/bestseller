from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_adopted_knowledge_lineage import db as db

from bestseller.infra.db.models import CanonFactModel, TimelineEventModel
from bestseller.services import feedback
from bestseller.services import knowledge_evidence_reconciliation as reconciliation
from bestseller.services.critic_evidence_gate import build_critic_body_anchors
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings


def test_incomplete_candidates_do_not_claim_closed_and_cross_body_ids_fail():
    body = '甲把信交给乙。'
    receipt = {'candidate_payload': {'timeline_events': [{'event_name': '交接', 'evidence': '不存在的引文'}]}}
    chapter = SimpleNamespace(metadata_json={})
    assert not reconciliation.feedback_evidence_complete(chapter, receipt, body)
    pending = reconciliation.unresolved_evidence(receipt, body)
    invalid = build_critic_body_anchors('另一个版本。')[0]['id']
    with pytest.raises(ValueError, match='unknown_body_anchor'):
        reconciliation.resolve_decisions(json.dumps({'decisions': [{'item_id': 'timeline_events:0',
            'supported': True, 'anchor_ids': [invalid], 'reason': '有证据'}]}), pending, body)


async def test_real_sql_reconciliation_adds_only_supported_items_and_replay_has_no_increments(db, monkeypatch):
    session, project, chapter, draft = db
    lineage, rejection = await feedback.validate_adopted_knowledge_source(session,
        project_id=project.id, chapter_id=chapter.id, source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md)
    assert rejection is None
    payload = {'canon_facts': [
        {'subject': '钥匙', 'predicate': 'location', 'value': '桌上', 'evidence': '错误摘录'},
        {'subject': '甲', 'predicate': 'skill', 'value': '读心', 'evidence': '猜测'}],
        'timeline_events': [{'event_name': '放下钥匙', 'evidence': '错误引用'}]}
    receipt = {'status': 'ok', 'source_lineage': lineage, 'candidate_payload': payload}
    chapter.metadata_json = {'knowledge_feedback_receipts': [receipt], 'other_history': [1, 2]}
    await session.flush()
    before = deepcopy(receipt)
    calls = []
    async def complete(s, settings, request):
        assert not s.in_nested_transaction()
        calls.append(request)
        anchor = build_critic_body_anchors(draft.content_md)[0]['id']
        return LLMCompletionResult(provider='test', model_name='test', llm_run_id=uuid4(),
            content=json.dumps({'decisions': [
                {'item_id': 'canon_facts:0', 'supported': True, 'anchor_ids': [anchor], 'reason': '原文明确放下钥匙'},
                {'item_id': 'canon_facts:1', 'supported': False, 'anchor_ids': [], 'reason': '正文没有读心能力'},
                {'item_id': 'timeline_events:0', 'supported': True, 'anchor_ids': [anchor], 'reason': '交接行为已发生'}]}))
    monkeypatch.setattr(reconciliation, 'complete_text', complete)
    kwargs = dict(chapter=chapter, chapter_md=draft.content_md,
        source_chapter_draft_version_id=draft.id, workflow_run_id=uuid4())
    result = await reconciliation.reconcile_feedback_evidence(session, load_settings(env={}), **kwargs)
    assert result['status'] == 'applied'
    assert result['canon_facts_created'] == 1 and result['timeline_events_created'] == 1
    assert chapter.metadata_json['knowledge_feedback_receipts'] == [before]
    assert chapter.metadata_json['other_history'] == [1, 2]
    assert reconciliation.feedback_evidence_complete(chapter, receipt, draft.content_md)
    replay = await reconciliation.reconcile_feedback_evidence(session, load_settings(env={}), **kwargs)
    assert replay['status'] == 'already_complete' and len(calls) == 1
    facts = list(await session.scalars(select(CanonFactModel).where(CanonFactModel.project_id == project.id)))
    events = list(await session.scalars(select(TimelineEventModel).where(TimelineEventModel.project_id == project.id)))
    assert len(facts) == len(events) == 1
    assert facts[0].value_json['evidence'] in draft.content_md
    assert events[0].metadata_json['source_lineage']['source_chapter_draft_version_id'] == str(draft.id)


def test_schema_rejections_remain_auditable_candidates():
    payload = feedback._validate_payload({'canon_facts': [{'subject': '不完整事实'}],
        'character_states': ['无法解析的角色记录']})
    assert payload.canon_facts == []
    diagnostics = payload.validation_diagnostics
    assert {d['field'] for d in diagnostics} == {'canon_facts', 'character_states'}
    receipt = {'candidate_payload': payload.model_dump(mode='json')}
    pending = reconciliation.unresolved_evidence(receipt, '现有正文。')
    assert len(pending) == 1
    assert next(iter(pending.values()))['claim'] == {'subject': '不完整事实'}


async def test_legacy_semantic_projection_superseded_without_repaying_or_deleting(db, monkeypatch):
    session, project, chapter, draft = db
    from bestseller.domain.feedback import CanonFactExtraction, TimelineEventExtraction
    lineage, rejection = await feedback.validate_adopted_knowledge_source(session,
        project_id=project.id, chapter_id=chapter.id, source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md)
    assert rejection is None
    body = draft.content_md
    payload = {'canon_facts': [{'subject': '钥匙', 'predicate': 'location', 'value': '模型猜测的解释', 'evidence': '错引'}],
        'timeline_events': [{'event_name': '意图推测的事件名', 'evidence': '错引', 'consequences': ['假想的后果']}]}
    receipt = {'status': 'ok', 'source_lineage': lineage, 'candidate_payload': payload}
    pending = reconciliation.unresolved_evidence(receipt, body)
    anchor = build_critic_body_anchors(body)[0]['id']
    decisions = {k: {'item_id': k, 'supported': True, 'anchor_ids': [anchor], 'reason': '原文存在相应动作',
        'resolved_evidence': body} for k in pending}
    paid = {**lineage, 'operation_id': 'historical-paid:v1', 'llm_run_id': str(uuid4()), 'fallback_used': False}
    await feedback._apply_canon_fact_extractions(session, project.id, chapter,
        [CanonFactExtraction(**{**payload['canon_facts'][0], 'evidence': body})], chapter_md=body, source_lineage=paid)
    await feedback._apply_timeline_extractions(session, project.id, chapter,
        [TimelineEventExtraction(**{**payload['timeline_events'][0], 'evidence': body})], chapter_md=body, source_lineage=paid)
    old = {'status': 'applied', 'source_payload_sha256': reconciliation.payload_digest(receipt),
        'source_body_sha256': lineage['source_body_sha256'], 'source_draft_id': str(draft.id),
        'source_lineage': paid, 'dispositions': decisions, 'raw_model_response': 'retained original'}
    chapter.metadata_json = {'knowledge_feedback_receipts': [receipt], 'knowledge_feedback_evidence_receipts': [old]}
    await session.flush()
    async def forbidden(*args, **kwargs):
        raise AssertionError('Migration must reuse valid paid paragraph selections')
    monkeypatch.setattr(reconciliation, 'complete_text', forbidden)
    result = await reconciliation.reconcile_feedback_evidence(session, load_settings(env={}),
        chapter=chapter, chapter_md=body, source_chapter_draft_version_id=draft.id, workflow_run_id=uuid4())
    assert result['projection_policy'] == 'literal-source.v2'
    facts = list(await session.scalars(select(CanonFactModel).where(CanonFactModel.project_id == project.id)))
    events = list(await session.scalars(select(TimelineEventModel).where(TimelineEventModel.project_id == project.id)))
    assert len(facts) == len(events) == 2
    current = [f for f in facts if f.is_current]
    assert len(current) == 1 and current[0].value_json['value'] == body
    usable = [v for v in events if v.metadata_json.get('knowledge_lineage_status') == 'current']
    assert len(usable) == 1 and usable[0].event_name in body and not usable[0].consequences
    assert len(result['superseded_projections']) == 2
    assert chapter.metadata_json['knowledge_feedback_evidence_receipts'][0] == old
