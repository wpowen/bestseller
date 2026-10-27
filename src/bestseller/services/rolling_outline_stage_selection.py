"""Conservative rolling-stage selection within the existing prompt compiler.

No IO, grades, authority creation or token-budget choice. Unknown and mixed
story text is retained, never classified by keywords or by the book's names.
The caller retains the complete original document and this path/hash audit.
"""
from copy import deepcopy
import hashlib
import json
import re

POLICY = 'rolling-outline-stage-selection.v1'
_PRODUCER_META = frozenset({
    'derived_from_candidate', 'foundation_consumer_kind', 'methodology_lineage',
    'name_drift_repair', 'original_paid_generation_aliased', 'quality_profile',
    'repair_attempts', 'reviewed_admission_workflow_id', 'source_snapshot_hash',
    'source_snapshot_id', 'source_snapshot_version', 'workflow_run_id', 'reused',
    'reused_artifact_id', 'version_no', 'artifact_id', 'source_artifact_id',
    'source_bound_expansion_review_receipt',
})
_FUTURE_CARD = frozenset({'chapter', 'chapter_number', 'actual_action',
    'actual_information', 'location', 'present', 'trigger_account'})


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _segment(value):
    return str(value).replace('~', '~0').replace('/', '~1')


def _at(value, path):
    for token in path.lstrip('/').split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def verified_selection_inputs(project, source):
    """Use the caller's existing DB-verified canonical binding, never source claims.

    Only an existing World producer proof and the locked intent's exact default
    markers supply non-author evidence here. Cast indexes and other asset meta
    have no comparable per-field witness yet: retain them and block selection.
    """
    binding = getattr(project, '_source_bound_planning_canonical', None)
    metadata = getattr(project, 'metadata_json', None) or {}
    if (not isinstance(binding, dict) or binding.get('id') != metadata.get('creation_canonical_snapshot_id')
            or binding.get('content_hash') != metadata.get('creation_canonical_snapshot_hash')):
        return {}
    from bestseller.services.conception_snapshots import snapshot_hash
    if not isinstance(binding.get('content'), dict) or snapshot_hash(binding['content']) != binding['content_hash']:
        return {}
    from bestseller.services.retained_planning_clauses import selected_clauses
    protected = ['/story_assets/' + kind + row['target_pointer']
        for kind in ('book_spec', 'world_spec', 'cast_spec', 'volume_plan')
        for row in selected_clauses(project, kind)]
    proofs = {}
    authority = source.get('source_authority', {})
    world = source['story_assets'].get('world_spec', {})
    world_authority = authority.get('approved_world_authority', {})
    witness = world_authority.get('source_proof', {})
    body = {key: value for key, value in world.items() if key != '_meta'}
    if (world_authority.get('source') == 'current_planning_artifact'
            and world_authority.get('authority_status') == 'current'
            and witness.get('source_payload_sha256') == _hash(world)
            and witness.get('story_payload_sha256') == _hash(body)
            and world_authority.get('full_story_source') == body
            and witness.get('producer_meta_role') == 'provenance_not_story_or_author_instructions'):
        for key, value in world.get('_meta', {}).items():
            if key in _PRODUCER_META:
                proofs['/story_assets/world_spec/_meta/' + _segment(key)] = {
                    'source_role': 'producer_provenance', 'value_sha256': _hash(value)}
    snapshot = authority.get('book_design_snapshot', {})
    if authority.get('book_design_source_proof', {}).get('source_payload_sha256') == _hash(snapshot):
        intent = snapshot.get('creation_intent', {})
        markers = intent.get('field_sources', {})
        if isinstance(markers, dict):
            for key, value in intent.items():
                if markers.get(key) == 'default':
                    proofs['/source_authority/book_design_snapshot/creation_intent/' + _segment(key)] = {
                        'source_role': 'default_reference', 'value_sha256': _hash(value)}
    return {'author_binding': {'canonical_id': binding['id'], 'canonical_sha256': binding['content_hash']},
        'protected_paths': protected, 'non_author_proofs': proofs}


def select_rolling_stage_source(source, *, author_binding=None, protected_paths=(),
                                non_author_proofs=None):
    """Select only provably nonstory diagnostics or explicitly future staging.

    Every retained original leaf remains at its original path, with its exact
    type/value. A removed parent is recorded atomically with its full hash and
    reason. Mixed character objects (including voice and psychology prose),
    unknown fields, creation clauses, World rules and prior facts stay intact.
    """
    if not isinstance(source, dict) or not isinstance(source.get('story_assets'), dict):
        raise ValueError('rolling_stage_source_object_required')
    from bestseller.services.source_bound_rolling_outline_prompt import _scope
    _, start, end = _scope(source['story_assets']['volume_entry'])
    selected = deepcopy(source)
    decisions = []
    uncertain = []
    author_coverage = (isinstance(author_binding, dict)
        and set(author_binding) == {'canonical_id', 'canonical_sha256'}
        and bool(author_binding.get('canonical_id'))
        and isinstance(author_binding.get('canonical_sha256'), str)
        and re.fullmatch(r'[a-f0-9]{64}', author_binding['canonical_sha256']) is not None)
    protected_paths = tuple(protected_paths)
    non_author_proofs = deepcopy(non_author_proofs or {})
    if any(not isinstance(path, str) or not path.startswith('/') for path in protected_paths):
        raise ValueError('rolling_stage_author_clause_path_invalid')

    def remove(parent, key, path, reason, role):
        protected = any(path == other or path.startswith(other + '/') or other.startswith(path + '/')
                        for other in protected_paths)
        proof = non_author_proofs.get(path)
        proven = (isinstance(proof, dict) and set(proof) == {'source_role', 'value_sha256'}
                  and proof['source_role'] == role and proof['value_sha256'] == _hash(parent[key]))
        if not author_coverage or protected or not proven:
            uncertain.append(path)
            return
        value = parent.pop(key)
        decisions.append({'path': path, 'value_sha256': _hash(value),
            'action': 'deferred', 'reason': reason, 'source_role': role,
            'phase': 'rolling_outline', 'output_range': [start, end]})

    def producer_meta(obj, path):
        meta = obj.get('_meta') if isinstance(obj, dict) else None
        if not isinstance(meta, dict):
            return
        for key in list(meta):
            if key in _PRODUCER_META:
                remove(meta, key, path + '/_meta/' + key,
                    'producer bookkeeping retained in original-source audit, not story law',
                    'producer_provenance')
            elif key != 'planned_frontiers':
                uncertain.append(path + '/_meta/' + _segment(key))
        # planned_frontiers can contain epistemic/Unknown boundaries. Retain
        # it whole, even when marked planned. Never equate all _meta with noise.
        if not meta:
            obj.pop('_meta')

    assets = selected['story_assets']
    for key, value in assets.items():
        for index, item in enumerate(value if isinstance(value, list) else [value]):
            producer_meta(item, '/story_assets/' + key + ('/' + str(index) if isinstance(value, list) else ''))
    authority = selected.get('source_authority', {})
    for key in ('genre_taxonomy_reference', 'deterministic_semantic_candidates',
                'deterministic_semantic_contract'):
        if key in authority:
            remove(authority, key, '/source_authority/' + key,
                'routing/detector diagnostics are not plot prerequisites or author obligations',
                'diagnostic_reference')

    # Preserve every explicit intent field, including ones not recognized by
    # an older selector. Defaults and absence of provenance never become law.
    snapshot = authority.get('book_design_snapshot', {})
    intent = snapshot.get('creation_intent', {}) if isinstance(snapshot, dict) else {}
    provenance = intent.get('field_sources') if isinstance(intent, dict) else None
    if isinstance(intent, dict) and intent:
        if not isinstance(provenance, dict):
            uncertain.append('/source_authority/book_design_snapshot/creation_intent')
            provenance = {}
    if isinstance(provenance, dict):
        for key in list(intent):
            if key != 'field_sources' and provenance.get(key) == 'default':
                remove(intent, key, '/source_authority/book_design_snapshot/creation_intent/' + key,
                    'recorded default provenance; explicit clauses retained separately and verbatim',
                    'default_reference')
            elif key not in {'field_sources', 'schema_version'} and provenance.get(key) not in {
                    'explicit', 'default', 'derived', 'legacy'}:
                uncertain.append('/source_authority/book_design_snapshot/creation_intent/' + _segment(key))

    unresolved_provenance = list(uncertain)

    cast = assets.get('cast_spec', {})
    people = []
    for key in ('protagonist', 'antagonist'):
        if isinstance(cast.get(key), dict):
            people.append((cast[key], '/story_assets/cast_spec/' + key))
    for index, person in enumerate(cast.get('supporting_cast', [])):
        if isinstance(person, dict):
            people.append((person, '/story_assets/cast_spec/supporting_cast/' + str(index)))
    for person, path in people:
        psych = person.get('psych_profile')
        if not isinstance(psych, dict):
            continue
        for key, pattern in [('mbti', r'[IE][NS][FT][JP]'), ('enneagram', r'[1-9](?:w[1-9])?')]:
            if key in psych and isinstance(psych[key], str) and re.fullmatch(pattern, psych[key]):
                remove(psych, key, path + '/psych_profile/' + key,
                    'typed personality index deferred to characterization, not an event/ability statement',
                    'characterization_index')
        scale = psych.get('big_five')
        if isinstance(scale, dict) and set(scale) <= {
                'openness', 'conscientiousness', 'extraversion', 'agreeableness', 'neuroticism'} and all(
                type(value) in (int, float) and 0 <= value <= 100 for value in scale.values()):
            remove(psych, 'big_five', path + '/psych_profile/big_five',
                'typed psychological scale; narrative history and behavioral prose retained',
                'characterization_index')

    # Requests to omit a typed index also need verified author coverage, not
    # just a regex match. Keep failures as dispatch blockers, not silent cuts.
    unresolved_provenance = list(dict.fromkeys([*unresolved_provenance, *uncertain]))

    # A future card's action/presence/location may contain a CURRENT negative
    # constraint, privilege boundary or temporal prerequisite. Chapter position
    # alone proves none of those leaves are dispensable. Retain every byte and
    # record refused exclusions until a structured per-field scope proves it.
    entries = [('/story_assets/volume_entry', assets.get('volume_entry'))]
    plan = assets.get('volume_plan')
    if isinstance(plan, list):
        entries.extend(('/story_assets/volume_plan/' + str(i), row) for i, row in enumerate(plan))
    elif isinstance(plan, dict):
        entries.append(('/story_assets/volume_plan', plan))
    for path, entry in entries:
        if not isinstance(entry, dict):
            continue
        cards = entry.get('information_flow_scene_cards', [])
        if not isinstance(cards, list):
            uncertain.append(path + '/information_flow_scene_cards')
            continue
        for index, card in enumerate(cards):
            if not isinstance(card, dict):
                uncertain.append(path + '/information_flow_scene_cards/' + str(index))
                continue
            chapter = card.get('chapter', card.get('chapter_number'))
            if type(chapter) is not int or chapter <= end or set(card) - _FUTURE_CARD:
                continue  # No scope/role proof: retain the entire card.
            for key in ('actual_action', 'location', 'present'):
                if key in card:
                    uncertain.append(path + '/information_flow_scene_cards/' + str(index) + '/' + key)

    # Original constraints stay in order. In particular, do not silently remove
    # long critic quotations just because they resemble a familiar producer.
    receipt = {'schema': POLICY, 'phase': 'rolling_outline', 'output_range': [start, end],
        'original_source_sha256': _hash(source), 'selected_source_sha256': _hash(selected),
        'decisions': decisions, 'retained_unclassified_paths': uncertain,
        'unresolved_provenance_paths': unresolved_provenance,
        'selection_dispatchable': not unresolved_provenance,
        'classification_blockers': [dict(path=path, action='retained_required',
            value_sha256=_hash(_at(source, path)),
            reason='no structured proof that this mixed field has no current/global prerequisite')
            for path in uncertain],
        'global_creation_contracts_exact': selected.get('source_authority', {}).get('approved_creation_contracts')
            == source.get('source_authority', {}).get('approved_creation_contracts'),
        'original_document': deepcopy(source), 'budget_increased': False,
        'author_binding': deepcopy(author_binding), 'protected_paths': list(protected_paths),
        'non_author_proofs': non_author_proofs,
        'author_authority_created': False, 'grade_asserted': False}
    receipt['receipt_sha256'] = _hash(receipt)
    return selected, receipt


def stage_selected_outline_prompt(system, user, *, author_binding=None, protected_paths=(),
                                  non_author_proofs=None):
    from bestseller.services.rolling_outline_source_transport import _split, _json
    prefix, _, suffix, original = _split(system, user)
    selected, receipt = select_rolling_stage_source(original, author_binding=author_binding,
        protected_paths=protected_paths, non_author_proofs=non_author_proofs)
    system += ('\nThis request contains phase-selected source data. Original sources and '
        'exact excluded paths/hashes are retained by the caller. Selection grants no '
        'author authority, grade, permission, new powers or occurred events. Retained '
        'information/trigger boundaries still apply even where later scene staging is absent.')
    return system, prefix + _json(selected, ordered=True) + suffix, receipt


def verify_stage_selection(original, selected, receipt, *, author_binding=None, protected_paths=(),
                           non_author_proofs=None):
    expected, expected_receipt = select_rolling_stage_source(original,
        author_binding=author_binding, protected_paths=protected_paths, non_author_proofs=non_author_proofs)
    if selected != expected or receipt != expected_receipt:
        raise ValueError('rolling_stage_selection_changed')
    return True
