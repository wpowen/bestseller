"""Monotonic structure-repair counts with an explicit protocol migration boundary.

A legacy spent count remains spent. Only a separately verified checkpoint
migration can identify its v1 draw and expose the new protocol's single draw.
This module does not authorize that migration, release charges or invoke models.
"""
from collections.abc import Mapping
from typing import Any

KEY = 'structure_repair_protocol_attempts'


def structure_repair_budget(state: Mapping[str, Any], *, protocol: str) -> tuple[int, int]:
    total = state.get('structure_repair_attempts', 0)
    if type(total) is not int or total < 0 or not protocol or protocol == 'v1':
        raise ValueError('invalid conception structure repair budget')
    counts = state.get(KEY)
    if counts is None:
        if total not in (0, 1):
            raise ValueError('invalid conception structure repair budget')
        # Missing provenance never replenishes a previously spent draw.
        return total, total
    if (not isinstance(counts, dict) or not counts or set(counts) - {'v1', protocol}
            or any(type(v) is not int or v not in (0, 1) for v in counts.values())
            or sum(counts.values()) != total):
        raise ValueError('invalid conception structure repair budget')
    return total, counts.get(protocol, 0)


def reserve_structure_repair(state: dict[str, Any], *, protocol: str) -> None:
    total, used = structure_repair_budget(state, protocol=protocol)
    if used:
        raise ValueError('conception structure repair protocol exhausted')
    counts = dict(state.get(KEY) or {})
    counts[protocol] = 1
    state[KEY] = counts
    state['structure_repair_attempts'] = total + 1
