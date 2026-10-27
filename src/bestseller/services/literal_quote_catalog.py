"""Shared whole-value quote IDs, extracted from foundation delta selection.

A locator proves literal identity only, never the meaning or authority of a claim.
"""
from collections.abc import Mapping, Sequence
from copy import deepcopy
import json

from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.retained_planning_clauses import resolve_pointer


def literal_quote_catalog(context: Mapping, paths: Sequence[str], *, json_scalars: bool = False) -> dict:
    rows = {}

    def walk(value, path):
        if isinstance(value, str) and value:
            rows['q' + str(len(rows))] = {
                'pointer': path, 'quote': value, 'start': 0, 'end': len(value),
                'value_sha256': snapshot_hash(value),
            }
        elif json_scalars and (value is None or type(value) in (bool, int, float)):
            quote = json.dumps(value, ensure_ascii=False, allow_nan=False)
            rows['q' + str(len(rows))] = {
                'pointer': path, 'quote': quote, 'start': 0, 'end': len(quote),
                'value_sha256': snapshot_hash(value), 'literal_type': 'json_scalar',
            }
        elif isinstance(value, dict):
            for key, child in value.items():
                walk(child, path + '/' + key.replace('~', '~0').replace('/', '~1'))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, path + '/' + str(index))

    for path in paths:
        try:
            value = resolve_pointer(context, path)
        except ValueError:
            continue
        walk(value, path)
    return rows


def resolve_literal_quote(context: Mapping, catalog: Mapping, quote_id: str) -> dict:
    item = catalog.get(quote_id)
    if not isinstance(item, Mapping):
        raise ValueError('literal_quote_id_unknown')
    value = resolve_pointer(context, item['pointer'])
    scalar = item.get('literal_type') == 'json_scalar'
    if scalar:
        if value is not None and type(value) not in (bool, int, float):
            raise ValueError('literal_quote_source_changed')
        quote = json.dumps(value, ensure_ascii=False, allow_nan=False)
    else:
        if not isinstance(value, str):
            raise ValueError('literal_quote_source_changed')
        quote = value
    if (item['value_sha256'] != snapshot_hash(value)
            or item['start'] != 0 or item['end'] != len(quote) or item['quote'] != quote):
        raise ValueError('literal_quote_source_changed')
    return deepcopy(dict(item))
