from copy import deepcopy

import pytest

from bestseller.services import planning_draft_admission as a
from bestseller.services.literal_quote_catalog import literal_quote_catalog, resolve_literal_quote
from bestseller.services.planning_quote_evidence import quote_contract, quote_id_tool, resolve_quote_submission


def fixture():
    supplied = {'approved_world_spec': {'limits': '航站不得开启外舱。', 'other': '返航须登记。'},
                'approved_book_spec': {'outline': '先返航。'},
                'candidate_payload': {'volumes': [{'information_flow_scene_cards': [
                    {'actual_action': '检修员开启外舱。'}, {'actual_action': '驾驶员核对仪表。'}]}]}}
    supplied['source_authority'] = a._source_authority(supplied)
    contract = quote_contract(supplied)
    ids = {row['pointer']: key for key, row in contract['catalog'].items()}
    path = 'volumes[0].information_flow_scene_cards[0]'
    wire = {key: contract[key] for key in ('schema', 'input_sha256', 'candidate_sha256', 'catalog_sha256')}
    wire.update(draft_eligible=False, editorial_notes=[], hard_violations=[{
        'code': 'ABILITY_RULE_CONFLICT', 'path': path,
        'candidate_quote_id': ids['/candidate_payload/volumes/0/information_flow_scene_cards/0/actual_action'],
        'source_quote_ids': [ids['/approved_world_spec/limits']]}], projection_checks=[
            {**target, 'status': 'conflict', 'explanation': '检修行为与禁止开舱规则冲突。',
             'related_violation_paths': [path]} for target in a.projection_audit_targets(supplied['candidate_payload'])])
    return supplied, contract, wire, ids


def test_resolves_whole_originals_without_copying_or_promoting():
    supplied, contract, wire, _ = fixture()
    before = deepcopy((supplied, contract, wire))
    result = resolve_quote_submission(wire, supplied, contract)
    assert result['normalized_submission']['draft_eligible'] is False
    row = result['normalized_submission']['hard_violations'][0]
    assert row['evidence'] == '检修员开启外舱。'
    assert row['source_requirement'] == '航站不得开启外舱。'
    assert result['semantic_entailment'] == 'not_evaluated'
    assert (supplied, contract, wire) == before


@pytest.mark.parametrize('change', ['unknown', 'source_as_candidate', 'candidate_as_source',
    'other_scene', 'advisory_source', 'stale_input', 'stale_candidate', 'stale_catalog',
    'modified_input', 'forged_catalog', 'forged_authority', 'copied_quote', 'duplicate_source'])
def test_mutations_fail_closed(change):
    supplied, contract, wire, ids = fixture()
    row = wire['hard_violations'][0]
    if change == 'unknown': row['candidate_quote_id'] = 'q999999'
    elif change == 'source_as_candidate': row['candidate_quote_id'] = ids['/approved_world_spec/limits']
    elif change == 'candidate_as_source': row['source_quote_ids'] = [row['candidate_quote_id']]
    elif change == 'other_scene': row['candidate_quote_id'] = ids['/candidate_payload/volumes/0/information_flow_scene_cards/1/actual_action']
    elif change == 'advisory_source': row['source_quote_ids'] = [ids['/approved_book_spec/outline']]
    elif change.startswith('stale_'): wire[{'stale_input': 'input_sha256', 'stale_candidate': 'candidate_sha256', 'stale_catalog': 'catalog_sha256'}[change]] = '0' * 64
    elif change == 'modified_input': supplied['approved_world_spec']['limits'] += '已变更'
    elif change == 'forged_catalog': contract['catalog'][row['candidate_quote_id']]['quote'] = '伪造'
    elif change == 'forged_authority': supplied['source_authority']['world_rule']['/approved_book_spec'] = ['/outline']
    elif change == 'copied_quote': row['evidence'] = '复制文本绕过ID'
    elif change == 'duplicate_source': row['source_quote_ids'] *= 2
    with pytest.raises(ValueError): resolve_quote_submission(wire, supplied, contract)


def test_existing_admission_tool_uses_selector_enums():
    supplied, contract, _, _ = fixture()
    tool, result = quote_id_tool(a._Grade.model_json_schema(),
                               a.projection_audit_targets(supplied['candidate_payload']), supplied)
    assert result == contract
    fields = tool['function']['parameters']['properties']['hard_violations']['items']['properties']
    assert set(fields) == {'code', 'path', 'candidate_quote_id', 'source_quote_ids'}
    assert set(fields['candidate_quote_id']['enum']) <= set(fields['source_quote_ids']['items']['enum'])


def test_shared_catalog_keeps_unicode_offsets_and_escaped_paths():
    payload = {'a/b': {'~声': '甲😀乙'}}
    rows = literal_quote_catalog(payload, ['/a~1b'])
    assert rows['q0']['end'] == 3
    assert rows['q0']['pointer'] == '/a~1b/~0声'
    assert resolve_literal_quote(payload, rows, 'q0')['quote'] == '甲😀乙'
    payload['a/b']['~声'] = '甲乙'
    with pytest.raises(ValueError): resolve_literal_quote(payload, rows, 'q0')


def test_bad_late_item_rejects_entire_submission_without_partial_mutation():
    supplied, contract, wire, _ = fixture()
    wire['hard_violations'].append({**deepcopy(wire['hard_violations'][0]), 'candidate_quote_id': 'unknown'})
    original = deepcopy(wire)
    with pytest.raises(ValueError): resolve_quote_submission(wire, supplied, contract)
    assert wire == original


def test_other_request_catalog_cannot_be_reused_even_with_same_local_ids():
    supplied, contract, wire, _ = fixture()
    other = deepcopy(supplied)
    other['candidate_payload']['volumes'][0]['information_flow_scene_cards'][0]['actual_action'] = '另一场检修。'
    other_contract = quote_contract(other)
    assert set(other_contract['catalog']) == set(contract['catalog'])
    with pytest.raises(ValueError): resolve_quote_submission(wire, other, other_contract)


def test_literal_binding_does_not_claim_semantic_entailment():
    supplied, contract, wire, ids = fixture()
    # This is a real authorized rule but unrelated to opening an outer hatch.
    # The literal adapter must not pretend to have judged its causal relevance.
    wire['hard_violations'][0]['source_quote_ids'] = [ids['/approved_world_spec/other']]
    result = resolve_quote_submission(wire, supplied, contract)
    assert result['semantic_entailment'] == 'not_evaluated'
    assert result['normalized_submission']['draft_eligible'] is False
    assert result['new_grade_or_adoption'] is False


@pytest.mark.parametrize('code', ['CONTINUITY_CONFLICT', 'SCENE_NOT_EXECUTABLE'])
def test_existing_candidate_order_authority_is_preserved(code):
    supplied, contract, wire, ids = fixture()
    wire['hard_violations'][0]['code'] = code
    wire['hard_violations'][0]['source_quote_ids'] = [
        ids['/candidate_payload/volumes/0/information_flow_scene_cards/1/actual_action']]
    result = resolve_quote_submission(wire, supplied, contract)
    row = result['normalized_submission']['hard_violations'][0]
    assert row['source_refs'] == ['candidate:volumes[0].information_flow_scene_cards[1].actual_action']
    assert row['source_requirement'] == '驾驶员核对仪表。'
    assert result['new_grade_or_adoption'] is False


def test_pointer_tree_view_preserves_every_original_id_and_full_catalog_binding():
    from bestseller.services.planning_quote_evidence import quote_selection_view
    supplied, catalog, _, _ = fixture()
    view = quote_selection_view(catalog)
    assert view['input_sha256'] == catalog['input_sha256']
    assert view['catalog_sha256'] == catalog['catalog_sha256']
    assert 'catalog' not in view
    found = {}
    def walk(node, pointer):
        if isinstance(node, str): found[node] = pointer
        else:
            for key, child in node.items(): walk(child, pointer + '/' + key)
    walk(view['catalog_by_pointer'], '')
    assert found == {key: row['pointer'] for key, row in catalog['catalog'].items()}
    for key, pointer in found.items():
        assert resolve_literal_quote(supplied, catalog['catalog'], key)['pointer'] == pointer


def test_catalog_ids_stay_exact_after_jsonb_order_and_lossless_transport_sorting():
    import json
    from bestseller.services.lossless_source_encoding import encode_lossless_source,decode_lossless_source
    supplied, _, _, _ = fixture()
    expected = quote_contract(supplied)
    sorted_payload = json.loads(json.dumps(supplied, ensure_ascii=False, sort_keys=True))
    assert quote_contract(sorted_payload) == expected
    # Match the actual serialization used by planning review transport v3.
    encoded = encode_lossless_source(supplied, version=3)
    restored = decode_lossless_source(json.loads(json.dumps(encoded, ensure_ascii=False, sort_keys=True)))
    assert quote_contract(restored) == expected


@pytest.mark.parametrize('value,quote', [(2, '2'), (False, 'false'), (True, 'true'), (2.5, '2.5')])
def test_numeric_and_boolean_hard_sources_remain_citeable_with_typed_binding(value, quote):
    supplied, _, wire, _ = fixture()
    supplied['approved_world_spec']['typed_limit'] = value
    supplied['source_authority'] = a._source_authority(supplied)
    contract = quote_contract(supplied)
    ids = {row['pointer']: key for key,row in contract['catalog'].items()}
    selected = resolve_literal_quote(supplied,contract['catalog'],ids['/approved_world_spec/typed_limit'])
    assert selected['quote'] == quote
    assert selected['literal_type'] == 'json_scalar'
    wire.update({key:contract[key] for key in ('schema','input_sha256','candidate_sha256','catalog_sha256')})
    wire['hard_violations'][0]['source_quote_ids'] = [ids['/approved_world_spec/typed_limit']]
    wire['hard_violations'][0]['candidate_quote_id'] = ids['/candidate_payload/volumes/0/information_flow_scene_cards/0/actual_action']
    resolved = resolve_quote_submission(wire,supplied,contract)
    assert resolved['normalized_submission']['hard_violations'][0]['source_requirement'] == quote
    assert resolved['normalized_submission']['draft_eligible'] is False
    # A textual lookalike has a different source identity.
    supplied['approved_world_spec']['typed_limit'] = quote
    with pytest.raises(ValueError):
        resolve_literal_quote(supplied,contract['catalog'],ids['/approved_world_spec/typed_limit'])


def test_historical_string_only_catalog_default_has_no_scalar_ids():
    payload = {'sources': {'limit': 2, 'enabled': False, 'text': 'old literal'}}
    assert literal_quote_catalog(payload, ['/sources']) == {
        'q0': {'pointer': '/sources/text', 'quote':'old literal','start':0,'end':11,
               'value_sha256': literal_quote_catalog(payload, ['/sources'])['q0']['value_sha256']}}


def test_tool_keeps_all_machine_bindings_and_projections_at_root_before_notes():
    from bestseller.services.planning_quote_evidence import ROOT_FIELDS
    supplied, _, _, _ = fixture()
    tool, _ = quote_id_tool(a._Grade.model_json_schema(),a.projection_audit_targets(supplied['candidate_payload']),supplied)
    schema = tool['function']['parameters']
    assert tuple(schema['properties']) == ROOT_FIELDS == tuple(schema['required'])
    assert ROOT_FIELDS[-1] == 'editorial_notes'
    assert schema['properties']['editorial_notes']['type'] == 'array'
    assert schema['properties']['editorial_notes']['items']['type'] == 'string'
    assert schema['additionalProperties'] is False


def test_nested_machine_fields_are_shape_failure_never_source_stale_or_repaired():
    supplied, contract, wire, _ = fixture()
    notes = {key:wire.pop(key) for key in ('projection_checks','schema','input_sha256','candidate_sha256','catalog_sha256')}
    wire['editorial_notes'] = notes
    before = deepcopy(wire)
    with pytest.raises(ValueError, match='quote_submission_shape'):
        resolve_quote_submission(wire,supplied,contract)
    assert wire == before


def test_actual_paid_misnested_tool_return_remains_invalid_and_byte_exact():
    import hashlib
    import json
    from pathlib import Path
    from bestseller.services.planning_admission_submission import PlanningAdmissionSubmissionError
    raw = (Path(__file__).parents[1]/'fixtures/planning_admission/misnested_editorial_notes_2040da20.json').read_text()
    assert hashlib.sha256(raw.encode()).hexdigest() == 'c98e6e3515fb774692e02f2a22d1b582e113e13f49cfec39d31e295c27db63d9'
    actual = json.loads(raw)
    assert actual['draft_eligible'] is True and actual['hard_violations'] == []
    assert isinstance(actual['editorial_notes'],dict)
    supplied, _, _, _ = fixture()
    calls = [{'id':'actual-paid-shape-regression','type':'function','function':{
        'name':'submit_planning_admission','arguments':raw}}]
    completion = {'content':'','tool_calls':calls,'raw_message':{
        'role':'assistant','content':None,'tool_calls':deepcopy(calls)}}
    before = deepcopy(completion)
    with pytest.raises(PlanningAdmissionSubmissionError, match='empty_notes_compat_shape') as error:
        a._submitted_grade(completion,supplied=supplied,schema=a.VOLUME_QUOTE_SCHEMA)
    assert error.value.receipt['raw_arguments'] == raw
    assert completion == before


def test_v3_replaces_legacy_four_field_example_without_changing_chapter_or_v2():
    from bestseller.services.planning_quote_evidence import ROOT_FIELDS
    legacy = '返回且仅返回{"draft_eligible":bool'
    assert legacy in a.SYSTEM and legacy in a.VOLUME_SYSTEM
    assert legacy not in a.VOLUME_QUOTE_SYSTEM
    assert a.VOLUME_SYSTEM.startswith(a.SYSTEM)
    example = a.VOLUME_QUOTE_WIRE_EXAMPLE
    assert tuple(example) == ROOT_FIELDS
    assert example['editorial_notes'] == []
    assert set(example['hard_violations'][0]) == {'code','path','candidate_quote_id','source_quote_ids'}
    assert all(key in a.VOLUME_QUOTE_SYSTEM for key in ROOT_FIELDS)
    supplied, _, _, _ = fixture()
    tool, _ = quote_id_tool(a._Grade.model_json_schema(),a.projection_audit_targets(supplied['candidate_payload']),supplied)
    actual = tool['function']['parameters']
    assert tuple(actual['properties']) == tuple(actual['required']) == tuple(example)
    assert actual['properties']['editorial_notes']['type'] == 'array'
    assert actual['properties']['editorial_notes']['items']['type'] == 'string'


def test_actual_layout_only_moves_leave_no_original_editorial_notes_array():
    import json
    from pathlib import Path
    from bestseller.services.planning_admission_submission import _unique
    raw = (Path(__file__).parents[1]/'fixtures/planning_admission/misnested_editorial_notes_2040da20.json').read_text()
    original = json.loads(raw,object_pairs_hook=_unique)
    layout = deepcopy(original)
    for key in tuple(layout['editorial_notes']):
        assert key not in layout
        layout[key] = layout['editorial_notes'].pop(key)
    from bestseller.services.planning_quote_evidence import ROOT_FIELDS
    assert set(layout) == set(ROOT_FIELDS)
    assert layout['editorial_notes'] == {}  # Preserve container; never invent [].
    supplied, contract, _, _ = fixture()
    with pytest.raises(ValueError,match='quote_editorial_notes_shape'):
        resolve_quote_submission(layout,supplied,contract)
    assert json.loads(raw)==original
