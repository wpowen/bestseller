"""Lossless coverage and omitted-interaction regressions; no DB/model access."""
from copy import deepcopy
import hashlib
import json

import pytest

from bestseller.services.draft_continuity_capacity_plan import (
    build_plan, check_pair_capacity, verify_plan,
)

pytestmark = pytest.mark.unit


def source():
    return {'foundation': {'name': '无关测试人物', 'creator/a~b': '禁止把旧传闻当已知。' * 10,
                           'facts': [False, 0, None, [], {}, '当前发生的可创作动作。' * 12]},
            'previous': [{'body': '她昨日已经交还账簿。' * 10}],
            'unknown_authored_field': '这一条不能因为键名未知被丢掉。' * 10}


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def reseal(plan):
    for shard in plan['shards']:
        shard['sha256'] = sha(shard['units'])
    plan['plan_sha256'] = sha({k: v for k, v in plan.items() if k != 'plan_sha256'})


def plan_for(sources):
    return build_plan(body='完整当前正文。', sources=sources,
                      max_unit_chars=160, max_shard_chars=300)


def test_every_typed_leaf_unknown_field_and_escaped_path_is_retained_without_mutation():
    sources = source()
    before = deepcopy(sources)
    plan = plan_for(sources)
    verify_plan(plan, body='完整当前正文。', sources=sources)
    assert sources == before
    assert plan == plan_for(sources)
    assert plan['qualification_granted'] is False
    paths = {u['path'] for s in plan['shards'] for u in s['units']}
    assert '/foundation/creator~1a~0b' in paths
    assert '/unknown_authored_field' in paths
    n = len(plan['shards'])
    assert len(plan['required_pairs']) == n * (n + 1) // 2


@pytest.mark.parametrize('mutation', ['drop_source', 'duplicate_source', 'change_type',
                                     'drop_cross_pair', 'drop_self_pair'])
def test_resealed_but_incomplete_or_type_changed_plan_is_rejected(mutation):
    sources = source()
    plan = plan_for(sources)
    if mutation == 'drop_source':
        row = next(s for s in plan['shards'] if len(s['units']) > 1)
        row['units'].pop()
    elif mutation == 'duplicate_source':
        plan['shards'][-1]['units'].append(deepcopy(plan['shards'][0]['units'][0]))
    elif mutation == 'change_type':
        unit = next(u for s in plan['shards'] for u in s['units']
                    if u['path'] == '/foundation/facts')
        unit['value'][0] = 0
    elif mutation == 'drop_cross_pair':
        plan['required_pairs'].remove(next(p for p in plan['required_pairs'] if p[0] != p[1]))
    else:
        plan['required_pairs'].remove(next(p for p in plan['required_pairs'] if p[0] == p[1]))
    reseal(plan)
    with pytest.raises(ValueError):
        verify_plan(plan, body='完整当前正文。', sources=sources)


@pytest.mark.parametrize('changed', ['body', 'source'])
def test_exact_body_or_source_change_invalidates_plan(changed):
    sources = source()
    plan = plan_for(sources)
    body = '完整当前正文。'
    if changed == 'body':
        body += '新增一段。'
    else:
        sources['unknown_authored_field'] += '作者新增约束。'
    with pytest.raises(ValueError, match='plan_binding'):
        verify_plan(plan, body=body, sources=sources)


def test_oversized_indivisible_source_is_never_cut_to_fit():
    with pytest.raises(ValueError, match='indivisible_value_exceeds_capacity'):
        build_plan(body='全文。', sources={'author': '甲' * 200},
                   max_unit_chars=100, max_shard_chars=200)


def test_every_pair_is_measured_with_consumer_tools_and_output_and_no_payment():
    sources = source()
    plan = plan_for(sources)
    seen = []

    def request(selected):
        seen.append([s['id'] for s in selected])
        return {'messages': [{'role': 'user', 'content': json.dumps(selected)}],
                'tools': [{'type': 'function'}], 'output_tokens': 6500}

    def measure(**kwargs):
        assert kwargs['tools'] == [{'type': 'function'}]
        assert kwargs['output_tokens'] == 6500
        return {'budget_passed': True, 'provider_usage_claimed': False}

    receipts = check_pair_capacity(plan, body='完整当前正文。', sources=sources,
                                   make_request=request, measure_request=measure)
    assert [r['pair'] for r in receipts] == plan['required_pairs']
    assert seen == [list(dict.fromkeys(pair)) for pair in plan['required_pairs']]
    assert all(r['capacity']['provider_usage_claimed'] is False for r in receipts)


def test_oversized_interaction_pair_is_not_silently_omitted():
    sources = source()
    plan = plan_for(sources)
    with pytest.raises(ValueError, match='pair_request_capacity'):
        check_pair_capacity(plan, body='完整当前正文。', sources=sources,
                            make_request=lambda selected: {},
                            measure_request=lambda **kwargs: {'budget_passed': False})
