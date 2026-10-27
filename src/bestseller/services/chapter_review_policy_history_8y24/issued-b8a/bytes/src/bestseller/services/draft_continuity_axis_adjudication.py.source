"""Privileged independent axis actions; original malformed submissions stay immutable.

This is a separate reference policy, never a parser adapter or a model grade.
Only a positive explicit-delivery axis may be independently re-evidenced. All
other native axes, full coverage, physical payment and explicit approval remain
mandatory. Unresolved hard items and real blockers have no passage here.
"""
from copy import deepcopy
from pathlib import Path
import hashlib
import re
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from sqlalchemy import select
from bestseller.infra.db import models as m
from bestseller.services import draft_continuity as dc
from bestseller.services import draft_continuity_adjudication as legacy
from bestseller.services.prompt_source_policy import content_hash

POLICY = 'source-adjudicated-axis-continuity.v1'


def policy_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def source_class(path):
    if path == '/chapter_sources/acceptance_contract/source_authority_channels/policy':
        return 'input_authority_contract'
    if re.fullmatch(r'/chapter_sources/scenes/[0-9]+/(purpose/story|exit_state/summary|ending_hook_payload)', path):
        return 'planning_reference_not_hard_fact'
    if re.fullmatch(r'/prior_manuscript_continuity/sources/[0-9]+/body', path):
        return 'guarded_prior_prose_not_canon'
    if legacy.applicable_action_source(path):
        return 'selected_applicable_source'
    if path == '/foundations/book_spec/content/protagonist/weakness':
        return 'selected_POV_tendency_not_other_mind_evidence'
    return None


def derive_actions(raw, *, body, sources, axis_action, decisions):
    need = dc.need
    need(isinstance(raw, dict) and set(raw) == {'schema', 'body_sha256', 'source_sha256', 'checked_body_ids', 'checks', 'blocking_issues', 'unknown_issues', 'submission_complete'}
         and raw['schema'] == dc.POLICY and raw['submission_complete'] == 'all_applicable_checks_examined', 'axis_action_native_protocol')
    body_sha, source_sha = dc.body_hash(body), content_hash(sources)
    need(raw['body_sha256'] == body_sha and raw['source_sha256'] == source_sha, 'axis_action_native_binding')
    anchors = {x['id']: x for x in dc.build_critic_body_anchors(body)}
    catalog = {x['id']: x for x in dc.source_catalog(sources)}
    need(isinstance(raw['checked_body_ids'], list) and all(isinstance(x, str) for x in raw['checked_body_ids'])
         and set(raw['checked_body_ids']) == set(anchors), 'axis_action_full_coverage')
    checks = raw['checks']
    need(isinstance(checks, list) and len(checks) == len(dc.AXES) and {c.get('axis') for c in checks} == set(dc.AXES), 'axis_action_axes')
    need(isinstance(axis_action, dict) and axis_action.get('axis') == 'explicit_plan_delivery'
         and axis_action.get('body_sha256') == body_sha and axis_action.get('source_sha256') == source_sha
         and axis_action.get('unresolved_hard_items') == [] and axis_action.get('operator_conclusion') == 'no_unresolved_hard_delivery_item', 'axis_action_binding_or_unresolved')
    target = next(c for c in checks if c['axis'] == axis_action['axis'])
    need(axis_action.get('original_axis_sha256') == content_hash(target), 'axis_action_original_changed')
    # This narrow policy supports an empty explicit obligation set. Nonempty
    # creator obligations require a distinct complete obligation proof, not a
    # general waiver based on matching a few planning excerpts.
    contract = sources.get('chapter_sources', {}).get('acceptance_contract', {})
    need(contract.get('must_deliver') == [], 'axis_action_explicit_obligations_unsupported')
    authority = contract.get('source_authority_channels', {}).get('policy', '')
    need('Planning and AI proposals are not occurred events or mandatory prose' in authority, 'axis_action_authority_contract')
    need(isinstance(raw['blocking_issues'], list) and all(isinstance(x, str) and not x.strip() for x in raw['blocking_issues']), 'axis_action_hard_blocker')
    unknowns = raw['unknown_issues']
    need(isinstance(unknowns, list) and all(isinstance(x, str) and x.strip() for x in unknowns), 'axis_action_unknown_shape')
    for check in checks:
        need(set(check) == {'axis', 'status', 'coverage', 'body_ids', 'source_ids', 'explanation'}, 'axis_action_axis_shape')
        need(check['coverage'] == 'complete' and check['status'] in ('pass', 'not_applicable'), 'axis_action_nonpositive_axis')
        if check['status'] == 'not_applicable':
            need(check['axis'] == 'prior_continuity' and sources['chapter_sources']['chapter']['chapter_number'] == 1
                 and sources.get('prior_manuscript_continuity', {}).get('sources') == [], 'axis_action_invalid_na')
        need(isinstance(check['body_ids'], list) and check['body_ids'] and all(isinstance(x, str) for x in check['body_ids']), 'axis_action_body_citation_shape')
        if check['axis'] != axis_action['axis']:
            need(set(check['body_ids']) <= set(anchors), 'axis_action_other_axis_bad_body_citation')
        need(isinstance(check['source_ids'], list) and check['source_ids'] and set(check['source_ids']) <= set(catalog), 'axis_action_source_citation')
        need(isinstance(check['explanation'], str) and check['explanation'].strip(), 'axis_action_axis_explanation')
    invalid = sorted(set(target['body_ids']) - set(anchors))
    need(invalid and axis_action.get('original_invalid_body_ids') == invalid, 'axis_action_invalid_inventory')

    def evidence(item):
        need(isinstance(item.get('reason'), str) and item['reason'].strip() and item.get('unresolved_hard_items') == [], 'axis_action_reason_or_unresolved')
        bproof, sproof = [], []
        for citation in item.get('body_evidence', []):
            anchor = anchors.get(citation.get('id'))
            quote = citation.get('quote')
            need(anchor is not None and isinstance(quote, str) and len(quote) >= 8 and quote in anchor['text'], 'axis_action_body_quote')
            start = anchor['start'] + anchor['text'].index(quote)
            normalized = {'id': anchor['id'], 'quote': quote, 'start': start, 'end': start + len(quote), 'offset_unit': 'unicode-codepoint', 'quote_sha256': dc.body_hash(quote), 'body_sha256': body_sha}
            for key in ('start', 'end', 'offset_unit', 'quote_sha256', 'body_sha256'):
                need(key not in citation or citation[key] == normalized[key], 'axis_action_supplied_offset_or_hash')
            need(body[start:start + len(quote)] == quote, 'axis_action_exact_body_slice')
            bproof.append(normalized)
        for citation in item.get('source_evidence', []):
            selected = catalog.get(citation.get('id'))
            quote = citation.get('quote')
            need(selected is not None and isinstance(quote, str) and len(quote) >= 4 and quote in selected['text'], 'axis_action_source_quote')
            kind = source_class(selected['path'])
            need(kind is not None, 'axis_action_inapplicable_source')
            normalized = {'id': selected['id'], 'path': selected['path'], 'quote': quote, 'quote_sha256': dc.body_hash(quote), 'source_value_sha256': dc.body_hash(selected['text']), 'source_sha256': source_sha, 'source_class': kind}
            for key in ('path', 'quote_sha256', 'source_value_sha256', 'source_sha256'):
                need(key not in citation or citation[key] == normalized[key], 'axis_action_supplied_source_hash')
            sproof.append(normalized)
        need(bproof and sproof, 'axis_action_missing_positive_evidence')
        return {**deepcopy(item), 'body_evidence': bproof, 'source_evidence': sproof}

    claims = axis_action.get('claims')
    need(isinstance(claims, list) and bool(claims), 'axis_action_missing_claims')
    for claim in claims:
        need(claim.get('disposition') in ('textually_supported', 'editorial_deviation_not_hard'), 'axis_action_unresolved_claim')
    independent_claims = [evidence(item) for item in claims]
    need(isinstance(decisions, list) and len(decisions) == len(unknowns)
         and {x.get('index') for x in decisions} == set(range(len(unknowns))), 'axis_action_unresolved_diagnostic')
    actions = []
    for item in decisions:
        need(item.get('raw_issue_sha256') == dc.body_hash(unknowns[item['index']]), 'axis_action_issue_changed')
        need(item.get('category') in ('preserved_epistemic_boundary', 'current_plan_delivered_without_past_invention', 'retained_editorial_boundary', 'exact_prior_prose_continuation'), 'axis_action_diagnostic_category')
        actions.append(evidence(item))
    cited = {c['id'] for item in independent_claims + actions for c in item['body_evidence']}
    native_valid = {x for x in target['body_ids'] if x in anchors and not anchors[x]['text'].lstrip().startswith('#')}
    need(native_valid <= cited, 'axis_action_valid_original_citations_not_reexamined')
    return {'schema': POLICY, 'raw_submission_sha256': content_hash(raw), 'original_raw': deepcopy(raw),
            'independent_axis_action': {**deepcopy(axis_action), 'claims': independent_claims}, 'diagnostic_actions': actions,
            'original_invalid_ids_unmapped': invalid, 'raw_model_grade_modified': False,
            'qualification_evidence': 'positive_native_axes_plus_separately_approved_exact_source_axis_action'}


async def qualify_reference(session, settings, *, project, chapter, draft, evidence, axis_action, decisions, actor, authorization_ref, explicit_user_approval=False):
    from bestseller.services.operator_action_approval import record_operator_action_approval
    from bestseller.services.drafts import _evaluate_chapter_quality_gate, _collect_post_assembly_duplicate_findings
    dc.need(explicit_user_approval is True and actor and authorization_ref, 'axis_action_explicit_authorization')
    dc.need(chapter.project_id == project.id and draft.project_id == project.id and draft.chapter_id == chapter.id, 'axis_action_scope')
    await session.execute(select(m.ChapterModel.id).where(m.ChapterModel.id == chapter.id).with_for_update())
    await session.refresh(project)
    await session.refresh(chapter)
    await session.refresh(draft)
    sources = await dc.current_sources(session, settings, project=project, chapter=chapter)
    raw, physical = await legacy.verify_paid_evidence(session, settings, project=project, chapter=chapter, draft=draft, sources=sources, evidence=evidence)
    actions = derive_actions(raw, body=draft.content_md, sources=sources, axis_action=axis_action, decisions=decisions)
    prior = chapter.metadata_json.get(dc.POINTER)
    if prior:
        row = await session.get(m.WorkflowRunModel, UUID(prior), populate_existing=True)
        proof = (row.metadata_json or {}).get('proof') if row is not None else None
        if proof and proof.get('schema') == POLICY and proof.get('native_evidence') == evidence and proof.get('source_adjudication') == actions and proof.get('draft_id') == str(draft.id):
            selected = await dc.select_reference(session, settings, project=project, chapter=chapter)
            dc.need(selected is not None and selected[0].id == draft.id, 'axis_action_existing_reference_changed')
            return {'receipt_id': str(row.id), 'draft_id': str(draft.id), 'body_sha256': dc.body_hash(draft.content_md), 'state': 'pending_literary_review', 'already_qualified': True, 'formal_adoption': False}
        dc.need(False, 'axis_action_existing_reference_conflict')
    tag = {'evaluation_id': str(uuid4()), 'draft_id': str(draft.id), 'body_sha256': dc.body_hash(draft.content_md), 'source_sha256': content_hash(sources)}
    outcome = await _evaluate_chapter_quality_gate(session=session, project=project, chapter_number=chapter.chapter_number, content=draft.content_md, register_diversity=False, extra_report_payload={'draft_continuity_hard_safety': tag})
    duplicates = await _collect_post_assembly_duplicate_findings(session, project=project, chapter=chapter, content_md=draft.content_md)
    dc.need(outcome == 'ok' and not duplicates, 'axis_action_current_deterministic_not_positive')
    reports = list(await session.scalars(select(m.ChapterQualityReportModel).where(m.ChapterQualityReportModel.chapter_id == chapter.id).order_by(m.ChapterQualityReportModel.created_at.desc()).limit(10)))
    qr = next(r for r in reports if r.report_json.get('draft_continuity_hard_safety') == tag)
    proof = {'schema': POLICY, 'adjudicator_source_sha256': policy_hash(), 'project_id': str(project.id), 'chapter_id': str(chapter.id), 'draft_id': str(draft.id),
             'body_sha256': dc.body_hash(draft.content_md), 'source_sha256': content_hash(sources), 'hard_policy_sha256': dc.hard_policy_hash(),
             'deterministic_outcome': outcome, 'quality_report_id': str(qr.id), 'deterministic_binding': tag,
             'native_evidence': evidence, 'physical_evidence': physical, 'source_adjudication': actions,
             'axis_action': deepcopy(axis_action), 'decisions': deepcopy(decisions), 'raw_model_status_changed': False,
             'formal_adoption': False, 'canon_authority': False, 'final_export_eligible': False}
    identity = uuid5(NAMESPACE_URL, POLICY + ':' + content_hash(proof))
    binding = {'project_id': str(project.id), 'target_id': str(identity), 'proof_sha256': content_hash(proof), 'draft_id': str(draft.id), 'body_sha256': proof['body_sha256'], 'source_sha256': proof['source_sha256'], 'formal_adoption': False}
    approval = await record_operator_action_approval(session, purpose='draft_continuity_reference', binding=binding, capabilities=['reference_pending_manuscript'], actor=actor, authorization_ref=authorization_ref, explicit_user_approval=True)
    session.add(m.WorkflowRunModel(id=identity, project_id=project.id, workflow_type='draft_continuity_reference', scope_type='chapter', scope_id=chapter.id, status='completed', current_step='pending_literary_review', requested_by=actor,
                                  metadata_json={'binding': binding, 'approval_id': str(approval), 'proof': proof, 'authority': 'Explicit independent exact-source axis action; original native submission remains invalid.'}))
    chapter.metadata_json = {**chapter.metadata_json, dc.POINTER: str(identity)}
    await session.flush()
    return {'receipt_id': str(identity), 'draft_id': str(draft.id), 'body_sha256': proof['body_sha256'], 'state': 'pending_literary_review', 'evidence_kind': POLICY, 'raw_model_status_changed': False, 'formal_adoption': False}


async def validate_reference(session, settings, *, project, chapter, draft, proof, _memo=None):
    dc.need(proof['schema'] == POLICY and proof['adjudicator_source_sha256'] == policy_hash(), 'axis_action_policy_changed')
    sources = await dc.current_sources(session, settings, project=project, chapter=chapter, _memo=_memo)
    dc.need(proof['source_sha256'] == content_hash(sources) and proof['body_sha256'] == dc.body_hash(draft.content_md), 'axis_action_sources_changed')
    raw, physical = await legacy.verify_paid_evidence(session, settings, project=project, chapter=chapter, draft=draft, sources=sources, evidence=proof['native_evidence'])
    dc.need(physical == proof['physical_evidence'] and derive_actions(raw, body=draft.content_md, sources=sources, axis_action=proof['axis_action'], decisions=proof['decisions']) == proof['source_adjudication'], 'axis_action_evidence_changed')
    await dc._verify_deterministic_receipt(session, draft=draft, proof=proof)
