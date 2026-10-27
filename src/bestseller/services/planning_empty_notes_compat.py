"""Lossless presentation compatibility; never supplies comments or decisions.

Only the exact exhausted machine-field wrapper is recognized. Original bytes
and the inverse mapping remain in the submission receipt. Binding/grade checks
are mandatory downstream, including for a normalized false decision.
"""
from copy import deepcopy

from bestseller.services.prompt_source_policy import content_hash

ADAPTER = 'exhausted-machine-wrapper-empty-notes.v1'
MOVED = ('schema', 'input_sha256', 'candidate_sha256', 'catalog_sha256', 'projection_checks')


def normalize_empty_notes(payload, completion):
    if not isinstance(payload, dict) or not isinstance(payload.get('editorial_notes'), dict):
        return payload, None
    # No broad dict-to-array coercion: even a native empty object is rejected.
    if (set(payload) != {'draft_eligible', 'hard_violations', 'editorial_notes'}
            or set(payload['editorial_notes']) != set(MOVED)
            or completion.get('finish_reason') != 'tool_calls'
            or completion.get('content') != ''):
        raise ValueError('empty_notes_compat_shape')
    normalized = deepcopy(payload)
    for key in MOVED:
        normalized[key] = normalized['editorial_notes'].pop(key)
    if normalized['editorial_notes'] != {}:
        raise ValueError('empty_notes_compat_nonempty')
    normalized['editorial_notes'] = []
    # Prove the inverse on values, without discarding the original raw bytes.
    inverse = deepcopy(normalized)
    inverse['editorial_notes'] = {key: inverse.pop(key) for key in MOVED}
    if inverse != payload or content_hash(inverse) != content_hash(payload):
        raise ValueError('empty_notes_compat_inverse')
    return normalized, {
        'schema': ADAPTER, 'original_payload_sha256': content_hash(payload),
        'normalized_payload_sha256': content_hash(normalized),
        'field_moves': [{'from': '/editorial_notes/' + key, 'to': '/' + key} for key in MOVED],
        'container_diff': {'pointer': '/editorial_notes', 'before': {}, 'after': []},
        'inverse_payload_sha256': content_hash(inverse), 'submitted_comment_count': 0,
        'comments_added': 0, 'comments_removed': 0, 'decision_changed': False,
    }
