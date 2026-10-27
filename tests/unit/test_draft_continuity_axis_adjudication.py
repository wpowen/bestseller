"""Independent operator actions retain original invalid citations and fail closed."""
# ruff: noqa: F811
from copy import deepcopy
from uuid import UUID
import pytest
from sqlalchemy import select, func
from bestseller.infra.db import models as m
from bestseller.services import draft_continuity as dc, draft_continuity_adjudication as legacy
from bestseller.services import draft_continuity_axis_adjudication as service
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.quality_report_claim import graded_text_fingerprint
from bestseller.settings import load_settings
from test_draft_continuity import BODY, response, sql  # noqa: F401

pytestmark = pytest.mark.unit


def case():
    sources = {'chapter_sources': {'chapter': {'chapter_number': 2}, 'acceptance_contract': {'must_deliver': [], 'source_authority_channels': {'policy': 'Planning and AI proposals are not occurred events or mandatory prose'}}},
               'foundations': {'book_spec': {'content': {'series_engine': {'first_three_chapter_hook': 'Previously unknown readers remain unknown; do not open the sealed case.'}}}}}
    raw = response(sources=sources)
    axis = next(x for x in raw['checks'] if x['axis'] == 'explicit_plan_delivery')
    axis['body_ids'].append('unresolvable-original-ID')
    axis['explanation'] *= 50
    raw['unknown_issues'] = ['The archive history is explicitly unknown; this is not a new historical fact.']
    anchor = dc.build_critic_body_anchors(BODY)[0]
    source = next(x for x in dc.source_catalog(sources) if x['path'].endswith('/first_three_chapter_hook'))
    evidence = {'reason': 'Independently compare actual unopened-case action with the applicable source, preserving historical uncertainty.',
                'unresolved_hard_items': [], 'body_evidence': [{'id': anchor['id'], 'quote': anchor['text']}],
                'source_evidence': [{'id': source['id'], 'quote': source['text']}]}
    action = {'axis': axis['axis'], 'original_axis_sha256': content_hash(axis), 'body_sha256': dc.body_hash(BODY), 'source_sha256': content_hash(sources),
              'original_invalid_body_ids': ['unresolvable-original-ID'], 'unresolved_hard_items': [],
              'operator_conclusion': 'no_unresolved_hard_delivery_item', 'claims': [{**deepcopy(evidence), 'disposition': 'textually_supported'}]}
    decisions = [{**deepcopy(evidence), 'index': 0, 'raw_issue_sha256': dc.body_hash(raw['unknown_issues'][0]), 'category': 'preserved_epistemic_boundary'}]
    return sources, raw, action, decisions


def derive(sources, raw, action, decisions):
    return service.derive_actions(raw, body=BODY, sources=sources, axis_action=action, decisions=decisions)


def test_separate_action_never_normalizes_or_maps_original_invalid_submission():
    sources, raw, action, decisions = case()
    before = deepcopy((raw, action, decisions))
    with pytest.raises(ValueError, match='adjudication_body_citation'):
        legacy.derive_axis_evidence(raw, body=BODY, sources=sources, decisions=decisions)
    result = derive(sources, raw, action, decisions)
    assert result['original_raw'] == raw and result['original_invalid_ids_unmapped'] == ['unresolvable-original-ID']
    assert (raw, action, decisions) == before and result['raw_model_grade_modified'] is False
    quote = result['independent_axis_action']['claims'][0]['body_evidence'][0]
    assert BODY[quote['start']:quote['end']] == quote['quote']
    assert derive(sources, raw, action, decisions) == result


@pytest.mark.parametrize('fault', ['body', 'source', 'axis_hash', 'unknown_axis', 'failed_axis', 'other_bad_id', 'other_bad_source', 'hard_blocker', 'missing_coverage',
    'missing_claims', 'claim_body', 'claim_source', 'offset', 'quote_hash', 'unresolved', 'claim_unresolved', 'diagnostic_unresolved', 'unknown_decision', 'issue_hash',
    'explicit_requirement', 'policy_missing', 'invalid_inventory', 'unrelated_axis', 'dropped_valid_id', 'empty_claim_evidence', 'not_applicable'])
def test_unresolved_missing_or_foreign_evidence_has_no_qualification_route(fault):
    sources, raw, action, decisions = case()
    if fault == 'body': action['body_sha256'] = 'wrong'
    if fault == 'source': action['source_sha256'] = 'wrong'
    if fault == 'axis_hash': action['original_axis_sha256'] = 'wrong'
    if fault in ('unknown_axis', 'failed_axis'): raw['checks'][0]['status'] = 'unknown' if fault == 'unknown_axis' else 'fail'
    if fault == 'other_bad_id': raw['checks'][0]['body_ids'] = ['another-invalid-ID']
    if fault == 'other_bad_source': raw['checks'][0]['source_ids'] = ['another-source']
    if fault == 'hard_blocker': raw['blocking_issues'] = ['Actual hard prohibition violated.']
    if fault == 'missing_coverage': raw['checked_body_ids'].pop()
    if fault == 'missing_claims': action['claims'] = []
    if fault == 'claim_body': action['claims'][0]['body_evidence'][0]['id'] = 'invented'
    if fault == 'claim_source': action['claims'][0]['source_evidence'][0]['quote'] = 'Invented source permission.'
    if fault == 'offset': action['claims'][0]['body_evidence'][0]['start'] = 999
    if fault == 'quote_hash': action['claims'][0]['body_evidence'][0]['quote_sha256'] = 'wrong'
    if fault == 'unresolved': action['unresolved_hard_items'] = ['Unproven historical witness.']
    if fault == 'claim_unresolved': action['claims'][0]['unresolved_hard_items'] = ['Unverified.']
    if fault == 'diagnostic_unresolved': decisions[0]['unresolved_hard_items'] = ['Unverified.']
    if fault == 'unknown_decision': decisions = []
    if fault == 'issue_hash': decisions[0]['raw_issue_sha256'] = 'wrong'
    if fault == 'explicit_requirement': sources['chapter_sources']['acceptance_contract']['must_deliver'] = ['Must open the case.']
    if fault == 'policy_missing': sources['chapter_sources']['acceptance_contract']['source_authority_channels']['policy'] = 'Unproven policy.'
    if fault == 'invalid_inventory': action['original_invalid_body_ids'] = []
    if fault == 'unrelated_axis': action['axis'] = 'physical_causality'
    if fault == 'dropped_valid_id':
        other = dc.build_critic_body_anchors(BODY)[1]
        next(c for c in raw['checks'] if c['axis'] == 'explicit_plan_delivery')['body_ids'].append(other['id'])
        action['original_axis_sha256'] = content_hash(next(c for c in raw['checks'] if c['axis'] == 'explicit_plan_delivery'))
    if fault == 'empty_claim_evidence': action['claims'][0]['body_evidence'] = []
    if fault == 'not_applicable': raw['checks'][0]['status'] = 'not_applicable'
    with pytest.raises(ValueError): derive(sources, raw, action, decisions)


@pytest.mark.asyncio
@pytest.mark.parametrize('tamper', [None, 'approval', 'body', 'source', 'receipt', 'no_authorization', 'no_actor'])
async def test_actual_sqlite_qualification_reopen_idempotence_and_denials(sql, monkeypatch, tamper):
    from bestseller.services import drafts, llm
    sources, raw, action, decisions = case()
    async def current(*a, **k): return deepcopy(sources)
    async def physical(*a, **k): return deepcopy(raw), {'physical_verified_fixture': True}
    async def gate(*, session, project, chapter_number, content, extra_report_payload, **k):
        chapter = await session.scalar(select(m.ChapterModel).where(m.ChapterModel.project_id == project.id, m.ChapterModel.chapter_number == chapter_number))
        session.add(m.ChapterQualityReportModel(chapter_id=chapter.id, blocks_write=False, regen_attempts=0, report_json={**extra_report_payload, 'graded': graded_text_fingerprint(content), 'blocking_codes': []}))
        await session.flush()
        return 'ok'
    async def duplicates(*a, **k): return []
    async def denied(*a, **k): raise AssertionError('No provider or ledger operation is permitted')
    monkeypatch.setattr(dc, 'current_sources', current)
    monkeypatch.setattr(dc, 'hard_policy_hash', lambda: 'a' * 64)
    monkeypatch.setattr(legacy, 'verify_paid_evidence', physical)  # Scope of fixture: real SQL lifecycle, not physical integration.
    monkeypatch.setattr(drafts, '_evaluate_chapter_quality_gate', gate)
    monkeypatch.setattr(drafts, '_collect_post_assembly_duplicate_findings', duplicates)
    monkeypatch.setattr(llm, 'complete_text', denied)
    settings = load_settings(env={})
    async with sql() as s:
        p = m.ProjectModel(slug='unrelated-lunar-archive', title='Lunar archive', genre='science-fiction', target_chapters=3, target_word_count=6000, metadata_json={})
        s.add(p); await s.flush()
        c = m.ChapterModel(project_id=p.id, chapter_number=2, chapter_goal='Keep the sealed case closed.', production_state='blocked', metadata_json={'chapter_first_local_repair_count':2, 'chapter_first_full_regeneration_count':1})
        s.add(c); await s.flush()
        d = m.ChapterDraftVersionModel(project_id=p.id, chapter_id=c.id, version_no=6, content_md=BODY, is_current=True, promotion_state='candidate')
        s.add(d); await s.commit()
        kwargs = dict(project=p, chapter=c, draft=d, evidence={'retained': 'original'}, axis_action=action, decisions=decisions, actor='' if tamper == 'no_actor' else 'trusted-fixture-operator', authorization_ref='fixture direct instruction', explicit_user_approval=tamper != 'no_authorization')
        if tamper in ('no_authorization', 'no_actor'):
            with pytest.raises(ValueError, match='explicit_authorization'): await service.qualify_reference(s, settings, **kwargs)
            return
        result = await service.qualify_reference(s, settings, **kwargs)
        again = await service.qualify_reference(s, settings, **kwargs)
        assert again['receipt_id'] == result['receipt_id'] and again['already_qualified']
        await s.commit(); ids = p.id, c.id, d.id, UUID(result['receipt_id'])
    async with sql() as s:
        p, c, d = await s.get(m.ProjectModel, ids[0]), await s.get(m.ChapterModel, ids[1]), await s.get(m.ChapterDraftVersionModel, ids[2])
        row = await s.get(m.WorkflowRunModel, ids[3]); original = deepcopy(row.metadata_json['proof']['source_adjudication']['original_raw'])
        if tamper == 'approval':
            approval = await s.get(m.WorkflowRunModel, UUID(row.metadata_json['approval_id'])); approval.status = 'cancelled'
        if tamper == 'body': d.content_md += '篡改正文。'
        if tamper == 'source': sources['chapter_sources']['chapter']['title'] = 'Changed source'
        if tamper == 'receipt':
            value = deepcopy(row.metadata_json); value['proof']['source_adjudication']['original_raw']['checks'][0]['status'] = 'fail'; row.metadata_json = value
        await s.commit()
        if tamper:
            with pytest.raises(ValueError): await dc.select_reference(s, settings, project=p, chapter=c)
        else:
            selected = await dc.select_reference(s, settings, project=p, chapter=c)
            assert selected[0].id == d.id and selected[1]['formal_adoption'] is False
            assert row.metadata_json['proof']['source_adjudication']['original_raw'] == original == raw
            assert c.production_state == 'blocked' and c.metadata_json['chapter_first_local_repair_count'] == 2
            for model in (m.CanonFactModel, m.TimelineEventModel, m.ExportArtifactModel):
                assert await s.scalar(select(func.count()).select_from(model)) == 0
