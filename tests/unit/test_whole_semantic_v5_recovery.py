"""Synthetic protocol/retention evidence only; no DB, network or real critic."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine

from bestseller.services import fanqie_short_semantic_review as semantic
from test_fanqie_short_semantic_review import (
    kwargs, row, response, events, span, title_row, SOURCE,
)

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / 'reports/project-reliability-review-2026-10-24/implementation/whole-semantic-stage5-recovery/preimage-stage4'


@pytest.fixture(autouse=True)
def prohibit_io(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError('DB/network forbidden in protocol tests')
    monkeypatch.setattr(Engine, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(semantic, '_native_complete_text', forbidden)


def checked(p, rows):
    completion, physical = response(p, rows)
    report = semantic.validate_response(p, completion, current_binding=p['binding'], retained_payloads=physical)
    assert report['raw_response'] == completion['content']
    assert report['original_report'] == p['original_report']
    assert 'passed' not in report
    return report


def literal_events(p, count=1):
    result = deepcopy(events(p)[:count])
    for event in result:
        for key in ('goal', 'action', 'result'):
            event[key] = {k: event[key][k] for k in ('source', 'quote')}
    return result


def descriptor():
    return dict(artifact_id=str(uuid4()), workflow_id=str(uuid4()), content_sha256='a'*64,
                error='quote_not_in_scope', raw_response_sha256='b'*64)


def test_permanent_paid_preimage_and_raw_bytes_are_intact():
    proof = json.loads((ARCHIVE/'SHA256.json').read_text())
    for name, key in [('fanqie_short_semantic_review.py', 'source_sha256'), ('raw_response.txt', 'raw_response_sha256')]:
        assert hashlib.sha256((ARCHIVE/name).read_bytes()).hexdigest() == proof[key]
    assert proof['source_sha256'] == 'f80c3580dc89dcbb8e2fb65fb6462ed032809b4dbca2dc720bfa472b22023bb2'
    assert proof['calls_before'] == 286 and proof['calls_after'] == 287


def test_fresh_review_default_has_all_response_rows_and_no_recovery_authorization():
    p = semantic.prepare_review(**kwargs())
    assert p['recovery_parent'] is None
    assert all(i['response_required'] is True for i in p['reviews_requested'])
    assert checked(p, [row(p, i) for i in p['reviews_requested']])['error'] is None


def test_native_five_field_descriptor_binds_locally_without_archive_or_frozen_verdicts():
    values = kwargs()
    values.update(operation_namespace='fanqie-whole-semantic-v5', recovery_parent=descriptor())
    p = semantic.prepare_review(**values)
    assert p['recovery_parent'] == values['recovery_parent']
    assert p['binding']['recovery_parent_sha256'] == semantic.fingerprint(values['recovery_parent'])
    assert all(i['response_required'] for i in p['reviews_requested'])
    wire = json.loads(semantic.native_request_fields(p)['user_prompt'])
    assert 'recovery_parent' not in wire
    assert 'frozen_parent_reviews' not in p
    fields = semantic.native_request_fields(p)
    assert fields['operation_id'].startswith('fanqie-whole-semantic-v5:')
    assert fields['prompt_version'] == 'stage.5'
    assert fields['max_tokens_override'] == 12000
    assert not fields.get('tools') and not fields.get('tool_choice')


@pytest.mark.parametrize('mutation', ['extra', 'hash', 'error', 'same_workflow', 'v4_namespace'])
def test_descriptor_is_not_approved_flag_or_reusable_v4_root(mutation):
    values = kwargs(); parent = descriptor()
    values.update(operation_namespace='fanqie-whole-semantic-v5', recovery_parent=parent)
    if mutation == 'extra': parent['approved'] = True
    if mutation == 'hash': parent['content_sha256'] = 'A'*64
    if mutation == 'error': parent['error'] = 'provider_incomplete'
    if mutation == 'same_workflow': parent['workflow_id'] = values['workflow_run_id']
    if mutation == 'v4_namespace': values['operation_namespace'] = 'fanqie-whole-semantic-v4'
    with pytest.raises(semantic.ReviewInputError): semantic.prepare_review(**values)


def test_every_scope_text_is_full_literal_original_window_and_bound():
    codes = ('weak_hook', 'first_10k_loop_missing', 'opening_pressure_missing', 'title_abstract_setting_only')
    values = kwargs(codes, text='  \n'+SOURCE*10+'\n ')
    p = semantic.prepare_review(**values)
    for item in p['reviews_requested']:
        scope = item['scope']
        assert item['scope_text'] == p[scope['source']][scope['start']:scope['end']]
        if item['target'] == 'opening_30_percent':
            assert scope['end'] - scope['start'] == max(50, int(len(p['full_text'].strip())*.30))
    assert p['original_report'] == values['original_report']
    p['reviews_requested'][0]['scope_text'] += '改写'
    with pytest.raises(semantic.ReviewInputError, match='scope_text_mismatch'):
        semantic.native_request_fields(p)


def test_jsonb_key_sort_roundtrip_has_identical_request_and_valid_consumer_shape():
    p = semantic.prepare_review(**kwargs())
    clone = json.loads(semantic._json(p))
    assert semantic.native_request_fields(clone) == semantic.native_request_fields(p)
    assert checked(clone, [row(clone, clone['reviews_requested'][0])])['error'] is None


def test_complete_example_has_typed_status_ordered_literal_spans_and_actual_wire_delivery():
    p = semantic.prepare_review(**kwargs())
    wire = json.loads(semantic._user_prompt(p))
    example = wire['reply_format_example']
    assert wire['example_is_synthetic_not_source_evidence'] is True
    assert example == semantic.REPLY_EXAMPLE
    event = example['reviews'][0]['events'][0]
    assert event['status'] == 'completed'
    for key in ('goal', 'action', 'result'): assert set(event[key]) == {'source', 'quote'}
    # The illustrated story is explicitly synthetic; do not count it as evidence.
    assert example['binding_sha256'] != p['binding_sha256']
    for phrase in ('goal 起点', 'action 引文结束', '覆盖区间不能重叠', '完整分句', 'N=4时提供4个'):
        assert phrase in semantic.SYSTEM_PROMPT


@pytest.mark.parametrize('code', ['weak_hook', 'first_10k_loop_missing'])
def test_added_opening_rows_use_only_original_window_and_completed_events_for_loop(code):
    p = semantic.prepare_review(**kwargs((code,), SOURCE+'尾声。'*400))
    item = p['reviews_requested'][0]; r = row(p, item)
    r['evidence'] = [span(p, '我装好新腿后坐稳了。')]
    if code == 'first_10k_loop_missing': r['events'] = literal_events(p)
    assert checked(p, [r])['error'] is None
    r['evidence'] = [dict(source='full_text', quote='尾声。尾声。')]
    assert checked(p, [r])['error'] is not None
    if code == 'first_10k_loop_missing':
        r = row(p, item)
        assert checked(p, [r])['error'] == 'completed_event_count'


@pytest.mark.parametrize('mutation,expected', [
    ('paraphrase', 'span_schema'), ('missing_status', 'event_schema'),
    ('reversed', 'event_order'), ('touch_result', 'event_order'),
    ('shared_goal', 'overlapping_events'), ('duplicate', 'overlapping_events'),
])
def test_protocol_errors_do_not_get_repaired_into_valid_completed_events(mutation, expected):
    p = semantic.prepare_review(**kwargs(('opening_fast_payoff_missing',)))
    r = row(p, p['reviews_requested'][0]); r['events'] = literal_events(p, 2)
    e = r['events'][0]
    if mutation == 'paraphrase': e['action'] = '主角完成了修理'
    if mutation == 'missing_status': del e['status']
    if mutation == 'reversed': e['goal'], e['action'] = e['action'], e['goal']
    if mutation == 'touch_result': e['action'] = deepcopy(e['result'])
    if mutation == 'shared_goal': r['events'][1]['goal'] = deepcopy(e['goal'])
    if mutation == 'duplicate': r['events'][1] = deepcopy(e)
    report = checked(p, [r])
    assert report['error'] == expected
    assert all(i['verdict'] == 'uncertain' for i in report['reviews'])


def test_four_independent_events_satisfy_unchanged_n_four_without_extra_eight():
    # Synthetic Chinese padding reaches the original N=4 density threshold.
    p = semantic.prepare_review(**kwargs(('payoff_density_too_low',), SOURCE+'静静的街巷。'*2000))
    item = p['reviews_requested'][0]
    assert item['expected_payoff_count'] == 4
    r = row(p, item); r['events'] = literal_events(p, 4)
    assert checked(p, [r])['error'] is None
    r['events'].pop()
    assert checked(p, [r])['error'] == 'completed_event_count'


def test_title_paths_are_exact_validator_requirements_not_same_value_guesses():
    p = semantic.prepare_review(**kwargs(('title_abstract_setting_only',)))
    item = p['reviews_requested'][0]
    exact = '/approval_evidence/canonical_snapshot/content/writing_profile/market/reader_promise'
    assert exact in item['required_contract_paths']
    assert p['title_source_path'] in item['required_contract_paths']
    paths = semantic._projected_contract_strings(p)
    assert {x for x in paths if x.rsplit('/',1)[-1] in {'title_pattern', 'title_strategy'}} <= set(item['required_contract_paths'])
    assert '/creation_intent_contract/concept_seed' in item['explicit_creator_any_of_paths']
    r = title_row(p, item)
    assert checked(p, [r])['error'] is None
    r['contract_evidence'] = [v for v in r['contract_evidence'] if v['path'] != exact]
    assert checked(p, [r])['error'] == 'title_contract_unresolved'
    p['reviews_requested'][0]['required_contract_paths'].remove(exact)
    with pytest.raises(semantic.ReviewInputError, match='title_contract_paths_mismatch'):
        semantic.native_request_fields(p)


@pytest.mark.parametrize('verdict', ['confirmed', 'uncertain'])
def test_title_assessment_forbidden_on_other_verdicts_stays_strict(verdict):
    p = semantic.prepare_review(**kwargs(('title_abstract_setting_only',)))
    r = row(p, p['reviews_requested'][0], verdict)
    assert checked(p, [r])['error'] is None
    r['title_review'] = title_row(p, p['reviews_requested'][0])['title_review']
    assert checked(p, [r])['error'] == 'unexpected_title_assessment'


def test_title_explicit_contract_priority_is_prompted_without_automatic_acceptance():
    p = semantic.prepare_review(**kwargs(('title_abstract_setting_only',)))
    assert '不是新的作者硬指令' in semantic.SYSTEM_PROMPT
    assert '不得仅因没有公开羞辱' in semantic.SYSTEM_PROMPT
    assert '也不得仅因现实题材或元数据已批准' in semantic.SYSTEM_PROMPT
    r = row(p, p['reviews_requested'][0])
    assert checked(p, [r])['error'] == 'explicit_creator_evidence_missing'


def test_original_paid_seven_scopes_are_unchanged_under_v5_additive_protocol():
    # Frozen history is read, never regenerated or overwritten. v5 adds two
    # allowed rows and row protocol hints; the original seven windows stay exact.
    from test_fanqie_short_semantic_review import paid_source_payload
    old, p = paid_source_payload()
    current = {i['id']: i for i in p['reviews_requested']}
    assert len(p['reviews_requested']) == len(old['reviews_requested']) + 2
    assert {i['code'] for i in p['reviews_requested']} - {i['code'] for i in old['reviews_requested']} == {'weak_hook', 'first_10k_loop_missing'}
    for item in old['reviews_requested']:
        assert all(current[item['id']][k] == value for k, value in item.items())
    for key in ('full_text', 'title', 'manifest', 'approved_contract', 'original_report', 'title_revision_status'):
        assert p[key] == old[key]
    assert len(semantic._user_prompt(p)) < semantic.POLICY['max_prompt_chars']
    assert semantic.native_request_fields(p)['max_tokens_override'] == 12000


def test_actual_failed_v4_all_seven_rows_still_fail_original_rules_without_rewriting():
    path = ROOT/'reports/project-reliability-review-2026-10-24/implementation/original-four-whole-v4-execution.json'
    saved = json.loads(path.read_text())
    payload = saved['result']['request_payload']
    answer = json.loads(saved['result']['raw_response'])
    rows = {r['id']: r for r in answer['reviews']}
    errors = []
    original = semantic._json(answer)
    for item in payload['reviews_requested']:
        try:
            resolved, _ = semantic._resolve_response_row(rows[item['id']], item, payload)
            semantic._validate_row(resolved, item, payload)
        except semantic.ReviewInputError as exc:
            errors.append(str(exc))
    assert len(errors) == len(payload['reviews_requested']) == 7
    assert errors[0] == saved['result']['error'] == 'quote_not_in_scope'
    assert semantic._json(answer) == original


def test_full_parent_archive_is_not_accepted_as_descriptor_or_authorization():
    values = kwargs(); values['operation_namespace'] = 'fanqie-whole-semantic-v5'
    with pytest.raises(semantic.ReviewInputError, match='recovery_descriptor_schema'):
        semantic.prepare_review(**values, recovery_parent=dict(artifact_id=str(uuid4()), report={}, recovery_ordinal=1))
