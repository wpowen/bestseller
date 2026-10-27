"""Opt-in quote-ID binding for a future planning submission protocol.

No dispatch, persistence, grade promotion or historical response reinterpretation.
The caller must introduce a distinct policy before using this future wire format.
"""
from copy import deepcopy
import json
import re

from bestseller.services.literal_quote_catalog import literal_quote_catalog, resolve_literal_quote
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services import planning_draft_admission as admission
from bestseller.services.planning_admission_submission import planning_admission_tool

SCHEMA = 'planning-literal-quote-selection.v1'
ROOT_FIELDS = ('schema','input_sha256','candidate_sha256','catalog_sha256',
               'draft_eligible','hard_violations','projection_checks','editorial_notes')


def quote_contract(supplied: dict) -> dict:
    # Recompute authority; a caller-supplied role map cannot manufacture authority.
    if supplied['source_authority'] != admission._source_authority(supplied):
        raise ValueError('quote_authority_changed')
    sources = sorted(admission._authority_roles(supplied['source_authority']))
    # JSON object order may change through JSONB and lossless transport;
    # list order and literal scalar spelling remain unchanged.
    canonical = json.loads(json.dumps(supplied, ensure_ascii=False, sort_keys=True, allow_nan=False))
    catalog = literal_quote_catalog(canonical, [*sources, '/candidate_payload'], json_scalars=True)
    return {'schema': SCHEMA, 'input_sha256': content_hash(supplied),
            'candidate_sha256': content_hash(supplied['candidate_payload']),
            'catalog_sha256': content_hash(catalog), 'catalog': catalog}


def quote_id_tool(grade_schema: dict, targets: list, supplied: dict) -> tuple[dict, dict]:
    """Prepare existing admission tool with IDs instead of model-copied quotes."""
    kind = admission._candidate_kind(supplied['candidate_payload'])
    tool = planning_admission_tool(grade_schema, targets, projection_kind=kind)
    contract = quote_contract(supplied)
    schema = tool['function']['parameters']
    for field in ('schema', 'input_sha256', 'candidate_sha256', 'catalog_sha256'):
        schema['properties'][field] = {'type': 'string', 'enum': [contract[field]]}
        schema['required'].append(field)
    fields = schema['properties']['hard_violations']['items']['properties']
    for field in ('evidence', 'source_requirement', 'source_refs'):
        fields.pop(field)
    candidate_ids = [key for key, row in contract['catalog'].items()
                     if row['pointer'].startswith('/candidate_payload/')]
    # Existing scene/continuity guards permit candidate_order sources.
    # Resolver still checks each code's permissions, never upgrades their role.
    source_ids = list(contract['catalog'])
    fields['candidate_quote_id'] = {'type': 'string', 'enum': candidate_ids}
    fields['source_quote_ids'] = {'type': 'array', 'minItems': 1, 'uniqueItems': True,
                                  'items': {'type': 'string', 'enum': source_ids}}
    schema['properties']['hard_violations']['items']['required'] = list(fields)
    # Keep human notes last so no required machine sibling follows them.
    # This changes future presentation, never repairs a historical response.
    schema['properties'] = {key:schema['properties'][key] for key in ROOT_FIELDS}
    schema['required'] = list(ROOT_FIELDS)
    schema['properties']['editorial_notes']['description'] = (
        'Root-level array of strings; use [] when empty. Never an object or a wrapper for projection_checks or binding hashes.')
    tool['function']['description'] += (' Quote IDs select original text only; semantic claims still require justification.'
        ' All eight required fields are siblings at the root. Emit binding fields first and editorial_notes last;'
        ' editorial_notes is only an array of strings, never a container for any other field.')
    return tool, contract


def resolve_quote_submission(submission: dict, supplied: dict, contract: dict) -> dict:
    """Resolve all selectors atomically, then run the existing strict grade guard."""
    # Missing root bindings are a shape error, not evidence of source drift.
    # Never flatten editorial_notes or manufacture its missing [] value.
    if not isinstance(submission,dict) or set(submission) != set(ROOT_FIELDS):
        raise ValueError('quote_submission_shape')
    if not isinstance(submission['editorial_notes'],list):
        raise ValueError('quote_editorial_notes_shape')
    current = quote_contract(supplied)
    if current != contract or any(submission.get(key) != current[key]
                                  for key in ('schema', 'input_sha256', 'candidate_sha256', 'catalog_sha256')):
        raise ValueError('quote_contract_stale')
    expected = set(ROOT_FIELDS)
    normalized = {key: deepcopy(submission[key]) for key in expected
                  if key not in {'schema', 'input_sha256', 'candidate_sha256', 'catalog_sha256'}}
    proofs = []
    for row in normalized['hard_violations']:
        if set(row) != {'code', 'path', 'candidate_quote_id', 'source_quote_ids'}:
            raise ValueError('quote_violation_shape')
        ids = row.pop('source_quote_ids')
        if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
            raise ValueError('quote_source_ids_invalid')
        candidate = resolve_literal_quote(supplied, current['catalog'], row.pop('candidate_quote_id'))
        # Exact field ancestry, not substring membership in a different scene.
        parts = [match[1] or match[2] for match in re.finditer(
            r'([A-Za-z_][A-Za-z_0-9]*)|\[([0-9]+)\]', row['path'])]
        prefix = '/candidate_payload/' + '/'.join(parts)
        if candidate['pointer'] != prefix and not candidate['pointer'].startswith(prefix + '/'):
            raise ValueError('quote_candidate_scope')
        sources = [resolve_literal_quote(supplied, current['catalog'], key) for key in ids]
        roles = admission._authority_roles(supplied['source_authority'])
        if any(('candidate_order' if item['pointer'].startswith('/candidate_payload/')
                else roles.get(item['pointer'])) not in admission._SOURCE_ROLES_BY_CODE.get(row['code'], set())
               for item in sources):
            raise ValueError('quote_source_authority')
        row.update(evidence=candidate['quote'], source_requirement=sources[0]['quote'],
                   source_refs=[_source_ref(item['pointer']) for item in sources])
        proofs.append({'candidate': candidate, 'sources': sources})
    # Preserve the native tool's conditional projection shape as well as
    # Pydantic semantics (which supplies defaults for omitted fields).
    from bestseller.services.planning_admission_submission import _shape
    _shape(normalized)
    # This validates structural/literal consistency, not semantic entailment.
    grade = admission._grade(json.dumps(normalized, ensure_ascii=False),
                             supplied['candidate_payload'], supplied)
    return {'schema': SCHEMA, 'normalized_submission': grade.model_dump(mode='json'),
            'literal_bindings': proofs, 'input_sha256': current['input_sha256'],
            'catalog_sha256': current['catalog_sha256'], 'semantic_entailment': 'not_evaluated',
            'new_grade_or_adoption': False, 'provider_calls': 0}


def _source_ref(pointer: str) -> str:
    if not pointer.startswith('/candidate_payload/'):
        return pointer
    parts = pointer[len('/candidate_payload/'):].split('/')
    path = parts[0]
    for part in parts[1:]:
        path += '[' + part + ']' if part.isdigit() else '.' + part.replace('~1', '/').replace('~0', '~')
    return 'candidate:' + path


def quote_selection_view(contract: dict) -> dict:
    """Display existing IDs at their source paths, without duplicate text/hash.

    Full original text remains in input. The server retains and verifies the
    unchanged whole-value catalog including offsets and every value hash.
    This is an ordinary JSON tree sent through the existing lossless transport.
    """
    tree = {}
    for quote_id, row in contract['catalog'].items():
        node = tree
        parts = row['pointer'][1:].split('/')
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        if parts[-1] in node:
            raise ValueError('quote_catalog_pointer_duplicate')
        node[parts[-1]] = quote_id
    return {key: deepcopy(value) for key, value in contract.items() if key != 'catalog'} | {
        'catalog_view': 'literal-pointer-tree.v1', 'catalog_by_pointer': tree}
