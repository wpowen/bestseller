"""Append-only exact-source fragments; actual unsent input and hostile evidence."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from bestseller.services import lossless_source_encoding as pointers
from bestseller.services import primary_review_transport as t
from bestseller.services.prompt_compiler import PromptBudgetError

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def arguments(name):
    c = json.loads((ROOT / 'tests/fixtures/primary_review_capacity' / name).read_text())
    return dict(source_context=c['source_context'], system_prompt=c['system'],
                user_prompt=c['user'], tools=c['tools'], output_tokens=c['output_tokens'])


@pytest.fixture(scope='module')
def actual():
    args = arguments('original_four_v11.json')
    before = deepcopy(args)
    result = t.prepare_source_complete_prompt(**args)
    assert args == before
    return args, result


def verify(args, result):
    sy, us, receipt = result
    return t.verify_primary_review_transport(
        sy, us, source_context=args['source_context'], receipt=receipt,
        tools=args['tools'], output_tokens=args['output_tokens'],
    )


def test_real_unsent_source_fits_full_original_task_and_budgets(actual):
    args, result = actual
    sy, us, receipt = result
    proof = receipt['primary_source_transport']
    cap = proof['capacity']
    assert proof['encoding'] == t._FRAGMENT_ENCODING
    assert cap['budget_passed'] and cap['truncated'] is False
    assert cap['total_token_budget'] == 65536 and cap['token_safety_factor'] == 1.5
    assert cap['output_reserve_tokens'] == 8192 and cap['combined_characters'] <= 120000
    assert cap['estimated_input_tokens'] <= cap['input_allowance_tokens'] == 37717
    assert cap['reserved_input_tokens'] + 8192 <= 65536
    assert us[proof['packet_end']:] == '\n\n' + args['user_prompt']
    packet = pointers.decode_lossless_source(json.loads(us[:proof['packet_end']]))
    assert t._fragment_decode(packet, args['user_prompt']) == args['source_context']
    assert verify(args, (sy, us, receipt)) is None


def test_every_old_actual_wire_byte_remains_stable():
    path = ROOT / 'tests/fixtures/primary_review_capacity/primary_transport_before_task_fragments.txt'
    from importlib.machinery import SourceFileLoader
    spec = importlib.util.spec_from_loader('primary_before_task_fragments', SourceFileLoader('primary_before_task_fragments', str(path)))
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    args = arguments('original_four_v10.json')
    assert t.prepare_source_complete_prompt(**args) == old.prepare_source_complete_prompt(**args)
    for name, value in old._INSTRUCTIONS.items():
        assert t._INSTRUCTIONS[name] == value and t._notice(name) == old._notice(name)


@pytest.mark.parametrize('part', ['task', 'source', 'tools', 'output', 'packet', 'decoder',
                                  'encoding', 'notice', 'receipt'])
def test_actual_wire_cannot_borrow_changed_inputs_or_receipts(actual, part):
    args, result = deepcopy(actual)
    sy, us, receipt = result
    if part == 'task':
        us += 'changed'
    elif part == 'source':
        args['source_context']['authority'] += 'changed'
    elif part == 'tools':
        args['tools'][0]['function']['name'] += '_other'
    elif part == 'output':
        args['output_tokens'] = 8191
    elif part == 'packet':
        us = ' ' + us
    elif part == 'decoder':
        sy = sy.replace('index len(corpus_paths)', 'index zero')
    elif part == 'encoding':
        receipt['primary_source_transport']['encoding'] = t._COMPACT_ENCODING
    elif part == 'notice':
        sy += 'changed'
    else:
        receipt['primary_source_transport']['logical_source_sha256'] = '0' * 64
    with pytest.raises((ValueError, PromptBudgetError)):
        verify(args, (sy, us, receipt))


def simple():
    body = '首晚已交钥匙，原始带保存在防潮袋里。' * 10
    task = 'CURRENT_DRAFT\n' + body + '\n目录与反馈逐字保留。'
    source = {'adopted_continuity': {'recent_adopted_prose': [{'body': body}]},
              'parent': body[:80] + '后来有一处变化。' + body[100:],
              'task_copy': task, 'literal/键~': {'$review_span': [999, False, -1]},
              'literal_fragments': {'$review_fragments': [{'nested': {'$review_span': []}}]},
              'values': [None, False, 4, 2.25, '§0 §§1 😀 e\u0301']}
    return source, task


def test_whole_and_partial_strings_unicode_and_authored_markers_remain_data():
    source, task = simple()
    before = deepcopy(source)
    packet = t._fragment_encode(source, task)
    assert packet['integrity']['span_count'] > 0
    assert packet['integrity']['literal_marker_count'] == 2
    assert t._fragment_decode(packet, task) == source == before
    with pytest.raises(ValueError):
        t._fragment_decode(packet, task + 'changed')


def rehash(packet):
    rendered = t._json({k: v for k, v in packet.items() if k != 'integrity'})
    packet['integrity']['encoded_payload_sha256'] = t._sha(rendered)
    packet['integrity']['encoded_payload_characters'] = len(rendered)


@pytest.mark.parametrize('fault', ['bool', 'negative', 'short', 'overflow', 'bad_index',
                                   'chunk_type', 'literal_inventory', 'extra_field',
                                   'count', 'source_hash', 'task_hash', 'task_length'])
def test_malformed_packet_still_rejects_after_attacker_rehashes(fault):
    source, task = simple()
    packet = t._fragment_encode(source, task)
    # Direct complete task reference always exists and is unambiguous here.
    assert '$review_span' in packet['source_context']['task_copy']
    ref = packet['source_context']['task_copy']['$review_span']
    if fault == 'bool':
        ref[0] = True
    elif fault == 'negative':
        ref[1] = -1
    elif fault == 'short':
        ref[2] = ref[1] + 1
    elif fault == 'overflow':
        ref[2] = len(task) + 1
    elif fault == 'bad_index':
        ref[0] = 999
    elif fault == 'chunk_type':
        packet['source_context']['task_copy'] = {'$review_fragments': [None]}
    elif fault == 'literal_inventory':
        packet['literal_marker_paths'].append('/missing')
        packet['literal_marker_paths'].sort()
        packet['integrity']['literal_marker_count'] += 1
    elif fault == 'extra_field':
        packet['extra'] = 'unreviewed'
    elif fault == 'count':
        packet['integrity']['span_count'] += 1
    elif fault == 'source_hash':
        packet['integrity']['original_source_sha256'] = '0' * 64
    elif fault == 'task_hash':
        packet['original_task_sha256'] = '0' * 64
    else:
        packet['original_task_characters'] = True
    rehash(packet)
    with pytest.raises(ValueError):
        t._fragment_decode(packet, task)


def test_oversized_task_cannot_be_admitted_by_source_compression(monkeypatch):
    def forbidden(*_args):
        raise AssertionError('should not index an already oversized original task')
    monkeypatch.setattr(t, '_fragment_encode', forbidden)
    with pytest.raises(PromptBudgetError):
        t.prepare_source_complete_prompt(
            system_prompt='strict review', user_prompt='x' * 200000,
            source_context={'fact': 'original'}, tools=None, output_tokens=8192,
        )
