"""Exact reviewed producer pairs: original binding proof, never an owner/credit claim."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from bestseller.services.commercial_request_purpose import prove_known_noncommercial_request, _ROUTES

BASIS = json.loads((Path(__file__).parents[1] / 'fixtures' / 'request_purpose_producer_registry.json').read_text())
PAIRS = [(r['logical_role'], r['prompt_template']) for r in BASIS['routes']]


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def case(role, template, prose='An unrelated orbital botany chronicle / 潮汐观察记'):
    scope, key, h, model = 'retained-original-scope', sha('original operation'), sha(prose), 'openai/model-Z'
    attempts = [{'id': 'actual-'+str(i), 'scope_id': scope, 'operation_key': key,
                 'input_hash': h, 'model_name': model, 'status': 'completed',
                 'reserved_tokens': 500, 'actual_tokens': 50+i} for i in range(2)]
    declared = [{'attempt_id': a['id'], 'status': a['status'], 'model': model,
                 'effective_input_hash': h, 'wire_body_hash': sha('wire'+a['id']),
                 'reserved_tokens': a['reserved_tokens']} for a in attempts]
    origin = {'schema': 'paid-call-origin.v1', 'status': 'recorded', 'project_id': None,
              'workflow_run_id': None, 'step_run_id': None, 'logical_role': role,
              'prompt_template': template, 'prompt_version': None, 'prompt_hash': h}
    meta = {'paid_call_origin': origin,
            'effective_input_receipt': {'schema': 'effective-input.v1', 'hash': h, 'model': model},
            'execution_receipt': {'scope_id': scope, 'operation_key': key, 'attempts': declared}}
    paid = {'content': prose, 'model_name': model, 'metadata': meta,
            'tool_calls': None, 'raw_message': None}
    transport = [{'scope_id': scope, 'key': sha('transport-result.v1\n'+scope+'\n'+a['id']),
                  'input_hash': h, 'status': 'transport_result',
                  'result_json': {'schema': 'transport-result.v1', 'operation_key': key,
                                  'attempt_id': a['id'], 'input_hash': h, 'result': deepcopy(paid)}}
                 for a in attempts]
    run = {'id': 'original-row', 'project_id': None, 'workflow_run_id': None, 'step_run_id': None,
           'logical_role': role, 'prompt_template': template, 'prompt_version': None,
           'prompt_hash': h, 'model_name': model, 'metadata_json': deepcopy(meta)}
    return {'operation': {'scope_id': scope, 'key': key, 'input_hash': h, 'status': 'completed',
                          'result_json': paid}, 'actual_attempts': attempts,
            'original_runs': [run], 'transport_results': transport, 'failure_receipts': []}


@pytest.mark.parametrize('role,template', PAIRS)
@pytest.mark.parametrize('prose', ['潮汐档案中的新人物发现藻类', 'Orbital gardening book with a new device'])
def test_every_exact_producer_pair_full_original_physical_bindings(role, template, prose):
    value = case(role, template, prose)
    before = deepcopy(value)
    assert prove_known_noncommercial_request(**value) is True
    assert value == before
    assert value['operation']['result_json']['metadata']['paid_call_origin']['project_id'] is None


def test_registry_is_exactly_old_plus_thirty_nine_reviewed_pairs():
    before = {(role, template) for role, templates in BASIS['base_routes'].items()
              for template in templates}
    after = {(role, template) for role, templates in _ROUTES.items() for template in templates}
    assert len(PAIRS) == 39 == len(set(PAIRS))
    assert after - before == set(PAIRS)
    assert before <= after


@pytest.mark.parametrize('role,template', PAIRS)
def test_all_registered_routes_still_reject_competing_commercial_tool(role, template):
    value = case(role, template)
    value['transport_results'][-1]['result_json']['result']['tool_calls'] = [
        {'type': 'function', 'function': {'name': 'submit_chapter_review', 'arguments': '{}'}}]
    assert prove_known_noncommercial_request(**value) is False


@pytest.mark.parametrize('where', ['original_run', 'transport', 'paid_origin'])
def test_conflicting_purpose_never_falls_back_to_another_positive(where):
    value = case('planner', 'planner_world_spec')
    other = 'planner_cast_spec'
    if where == 'original_run':
        value['original_runs'][0]['prompt_template'] = other
        value['original_runs'][0]['metadata_json']['paid_call_origin']['prompt_template'] = other
    elif where == 'transport':
        value['transport_results'][-1]['result_json']['result']['metadata']['paid_call_origin']['prompt_template'] = other
    else:
        value['operation']['result_json']['metadata']['paid_call_origin']['prompt_template'] = other
    assert prove_known_noncommercial_request(**value) is False


@pytest.mark.parametrize('role,template', [('planner','planner_future_unregistered'),
    ('critic','critic_new_thing'), ('editor','editor_arbitrary'),
    ('writer','planner_world_spec'), ('critic','planner_world_spec'),
    ('planner','chapter_commercial_quality_judge')])
def test_unknown_or_wrong_role_is_not_allowed_by_prefix_or_role(role, template):
    assert prove_known_noncommercial_request(**case(role, template)) is False


@pytest.mark.parametrize('mutation', ['unknown_attempt', 'missing_attempt', 'wrong_scope', 'wrong_model',
                                   'effective_hash', 'transport_key', 'declared_extra'])
def test_full_actual_financial_binding_fences_stay_strict(mutation):
    value = case('editor', 'conception_finalize')
    if mutation == 'unknown_attempt':
        value['actual_attempts'][1]['status'] = 'unknown'
    elif mutation == 'missing_attempt':
        value['actual_attempts'].pop()
    elif mutation == 'wrong_scope':
        value['actual_attempts'][0]['scope_id'] = 'foreign'
    elif mutation == 'wrong_model':
        value['actual_attempts'][0]['model_name'] = 'other-model'
    elif mutation == 'effective_hash':
        value['transport_results'][0]['result_json']['result']['metadata']['effective_input_receipt']['hash'] = sha('changed')
    elif mutation == 'transport_key':
        value['transport_results'][0]['key'] = sha('wrong domain')
    else:
        value['original_runs'][0]['metadata_json']['execution_receipt']['attempts'].append({'attempt_id':'extra'})
    assert prove_known_noncommercial_request(**value) is False


def test_zero_actual_stopped_root_remains_unknown_with_no_purpose_evidence():
    value = case('planner', 'concept_methodology')
    value['operation'].update(status='stopped', result_json=None)
    value.update(actual_attempts=[], original_runs=[], transport_results=[])
    assert prove_known_noncommercial_request(**value) is False


def test_registered_producer_sources_still_match_reviewed_ast():
    import ast
    root = Path(__file__).parents[2]
    for route in BASIS['routes']:
        for evidence in route['producer_evidence']:
            tree = ast.parse((root / evidence['path']).read_text())
            nodes = [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == evidence['producer']]
            assert any(hashlib.sha256(ast.dump(n, include_attributes=False).encode()).hexdigest()
                       == evidence['producer_ast_sha256'] for n in nodes)
