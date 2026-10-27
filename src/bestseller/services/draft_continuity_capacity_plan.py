"""Pure, lossless source coverage planning. This module grants no qualification.

Every shard keeps exact typed source values at original JSON pointers. Every
unordered shard pair, including self pairs, is required: covering sources once
does not establish that interactions between sources were examined. Model and
physical receipt validation belongs to the consuming review protocol.
"""
from __future__ import annotations

from copy import deepcopy
from itertools import combinations_with_replacement
import hashlib
import json
from typing import Any, Callable

SCHEMA = 'draft-continuity-source-coverage-plan.v1'


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _need(value: Any, reason: str) -> None:
    if not value:
        raise ValueError('source_coverage_' + reason)


def _child(path: str, key: str | int) -> str:
    return path + '/' + str(key).replace('~', '~0').replace('/', '~1')


def _leaves(value: Any, path: str = '') -> dict[str, Any]:
    if isinstance(value, dict) and value:
        result = {}
        for key in sorted(value):
            _need(type(key) is str, 'non_string_key')
            result.update(_leaves(value[key], _child(path, key)))
        return result
    if isinstance(value, list) and value:
        result = {}
        for index, item in enumerate(value):
            result.update(_leaves(item, _child(path, index)))
        return result
    _need(value is None or type(value) in (str, int, float, bool, dict, list), 'non_json')
    return {path: value}


def _at(source: Any, pointer: str) -> Any:
    node = source
    if pointer == '':
        return node
    _need(pointer.startswith('/'), 'pointer')
    for part in pointer[1:].split('/'):
        key = part.replace('~1', '/').replace('~0', '~')
        node = node[int(key)] if isinstance(node, list) else node[key]
    return node


def _units(value: Any, *, max_chars: int, path: str = '') -> list[dict]:
    if len(_json(value)) <= max_chars:
        return [{'path': path, 'value': deepcopy(value)}]
    if isinstance(value, dict):
        rows = []
        for key in sorted(value):
            rows.extend(_units(value[key], max_chars=max_chars, path=_child(path, key)))
        return rows
    if isinstance(value, list):
        rows = []
        for index, item in enumerate(value):
            rows.extend(_units(item, max_chars=max_chars, path=_child(path, index)))
        return rows
    # Splitting a scalar would need an additional verified string-span codec.
    # Never silently cut a paragraph, scalar, or authored requirement to fit.
    raise ValueError('source_coverage_indivisible_value_exceeds_capacity:' + path)


def build_plan(*, body: str, sources: dict, max_unit_chars: int = 6000,
               max_shard_chars: int = 12000) -> dict:
    """Build a deterministic full-coverage candidate, without paying or passing it."""
    _need(type(body) is str and bool(body.strip()) and type(sources) is dict, 'input')
    _need(type(max_unit_chars) is int and type(max_shard_chars) is int
          and 0 < max_unit_chars <= max_shard_chars, 'limits')
    # Validate JSON before splitting, including nonfinite numbers.
    _json(sources)
    _leaves(sources)
    units = _units(sources, max_chars=max_unit_chars)
    groups: list[list[dict]] = []
    for unit in units:
        _need(len(_json([unit])) <= max_shard_chars, 'unit_envelope_exceeds_capacity')
        if not groups or len(_json(groups[-1] + [unit])) > max_shard_chars:
            groups.append([])
        groups[-1].append(unit)
    shards = [{'id': f'C{index + 1:04d}', 'units': group,
               'sha256': _sha(group)} for index, group in enumerate(groups)]
    ids = [row['id'] for row in shards]
    pairs = [list(pair) for pair in combinations_with_replacement(ids, 2)]
    core = {'schema': SCHEMA, 'body_sha256': hashlib.sha256(body.encode()).hexdigest(),
            'source_sha256': _sha(sources), 'max_unit_chars': max_unit_chars,
            'max_shard_chars': max_shard_chars, 'shards': shards,
            'required_pairs': pairs, 'qualification_granted': False}
    plan = {**core, 'plan_sha256': _sha(core)}
    verify_plan(plan, body=body, sources=sources)
    return plan


def verify_plan(plan: dict, *, body: str, sources: dict) -> None:
    """Verify complete disjoint typed leaves and the complete interaction matrix."""
    expected_keys = {'schema', 'body_sha256', 'source_sha256', 'max_unit_chars',
                     'max_shard_chars', 'shards', 'required_pairs',
                     'qualification_granted', 'plan_sha256'}
    _need(type(plan) is dict and set(plan) == expected_keys, 'plan_shape')
    _need(plan['schema'] == SCHEMA and plan['qualification_granted'] is False, 'plan_policy')
    _need(plan['body_sha256'] == hashlib.sha256(body.encode()).hexdigest()
          and plan['source_sha256'] == _sha(sources), 'plan_binding')
    _need(plan['plan_sha256'] == _sha({k: v for k, v in plan.items()
                                     if k != 'plan_sha256'}), 'plan_integrity')
    rows = plan['shards']
    _need(type(rows) is list and bool(rows), 'shards_missing')
    actual: dict[str, Any] = {}
    for index, row in enumerate(rows):
        _need(type(row) is dict and set(row) == {'id', 'units', 'sha256'}
              and row['id'] == f'C{index + 1:04d}', 'shard_shape')
        _need(type(row['units']) is list and bool(row['units'])
              and row['sha256'] == _sha(row['units']), 'shard_integrity')
        _need(len(_json(row['units'])) <= plan['max_shard_chars'], 'shard_capacity')
        for unit in row['units']:
            _need(type(unit) is dict and set(unit) == {'path', 'value'}
                  and type(unit['path']) is str, 'unit_shape')
            _need(len(_json(unit['value'])) <= plan['max_unit_chars'], 'unit_capacity')
            original = _at(sources, unit['path'])
            _need(_json(original) == _json(unit['value']), 'unit_source_changed')
            for path, value in _leaves(unit['value'], unit['path']).items():
                _need(path not in actual, 'source_overlap')
                actual[path] = value
    # Canonical JSON equality distinguishes False from 0, list from object,
    # and checks empty containers/nulls as well as every nonempty text leaf.
    _need(_json(actual) == _json(_leaves(sources)), 'source_coverage_incomplete')
    ids = [row['id'] for row in rows]
    _need(plan['required_pairs'] == [list(pair) for pair in
                                   combinations_with_replacement(ids, 2)], 'interaction_coverage')


def check_pair_capacity(plan: dict, *, body: str, sources: dict,
                        make_request: Callable[[list[dict]], dict],
                        measure_request: Callable[..., dict]) -> list[dict]:
    """Measure the actual consumer wire, including tools and output reserve.

    This refuses an oversized pair instead of dropping that pair or relaxing
    the shared request budget. A plan alone cannot be called model acceptance.
    """
    verify_plan(plan, body=body, sources=sources)
    shards = {row['id']: row for row in plan['shards']}
    receipts = []
    for pair in plan['required_pairs']:
        selected = [shards[key] for key in dict.fromkeys(pair)]
        request = make_request(deepcopy(selected))
        receipt = measure_request(**request)
        _need(receipt.get('budget_passed') is True, 'pair_request_capacity:' + ','.join(pair))
        receipts.append({'pair': pair, 'capacity': receipt})
    return receipts
