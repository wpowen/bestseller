"""Actual complete-answer allowance; old surgical wires remain unchanged."""
from copy import deepcopy
from uuid import uuid4

import pytest

from bestseller.services import llm, planning_surgical_budget as budget
from bestseller.settings import LLMRoleSettings

pytestmark = pytest.mark.unit


def role(**updates):
    return LLMRoleSettings(model='openai/MiniMax-M3.1-Flash',
        api_base='https://api.minimaxi.com/v1', max_tokens=32768,
        temperature=0.82, timeout_seconds=900, reasoning_effort=None).model_copy(update=updates)


def request():
    return llm.LLMCompletionRequest(logical_role='planner', project_id=uuid4(),
        workflow_run_id=uuid4(), system_prompt='Full source proposal.', user_prompt='{}',
        prompt_template=budget.CONSISTENCY_TEMPLATE, prompt_version=budget.CONSISTENCY_VERSION,
        fallback_response='{}')


def test_complete_projection_answer_and_thinking_fit_without_changing_role():
    original_role = role()
    before = original_role.model_dump()
    req = request()
    plan = budget.output_budget_for_request(req, original_role, answer_tokens=30208)
    assert plan['total_output_tokens'] == 54144
    assert plan['answer_tokens'] == 30208 and plan['reasoning_reserve_tokens'] == 16384
    req.metadata['planning_surgical_output_budget'] = deepcopy(plan)
    req.max_tokens_override = plan['total_output_tokens']
    actual_role = budget.apply_role(req, original_role)
    assert actual_role.reasoning_effort == 'medium'
    assert llm._effective_input_payload(req, actual_role)['max_tokens'] == 54144
    retained = req.model_copy(deep=True)
    retained.metadata = {}
    assert llm._effective_input_hash(retained, budget.apply_role(retained, original_role)) == llm._effective_input_hash(req, actual_role)
    assert original_role.model_dump() == before


@pytest.mark.parametrize('version,template', [
    (budget.PROMPT_VERSION, 'planner_volume_1_chapter_outline_commercial_surgical_repair'),
    (budget.DOMAIN_VERSION, budget.DOMAIN_TEMPLATE),
    (budget.FLAT_DOMAIN_VERSION, budget.FLAT_DOMAIN_TEMPLATE),
])
def test_original_route_budget_and_wire_do_not_gain_new_output_capacity(version, template):
    original_role = role(reasoning_effort='high')
    req = request().model_copy(update={'prompt_template': template, 'prompt_version': version})
    plan = budget.output_budget(original_role, answer_tokens=6000)
    assert budget.output_budget_for_request(req, original_role, answer_tokens=6000) == plan
    assert plan['total_output_tokens'] == 32768 and plan['schema'] == budget.SCHEMA
    req.metadata['planning_surgical_output_budget'] = plan
    req.max_tokens_override = 32768
    assert budget.apply_role(req, original_role) == original_role


@pytest.mark.parametrize('change', ['output', 'model', 'growth', 'answer', 'reserve'])
def test_budget_receipt_mutation_cannot_change_prepared_request(change):
    original_role = role()
    req = request()
    plan = budget.output_budget_for_request(req, original_role, answer_tokens=30208)
    req.max_tokens_override = plan['total_output_tokens']
    if change == 'output':
        plan['total_output_tokens'] += 1
    elif change == 'model':
        plan['model'] = 'unrelated'
    elif change == 'growth':
        plan['answer_growth_factor'] = 1
    elif change == 'answer':
        plan['answer_tokens'] += 1
    else:
        plan['reasoning_reserve_tokens'] = 0
    req.metadata['planning_surgical_output_budget'] = plan
    with pytest.raises(ValueError, match='request_binding_changed'):
        budget.apply_role(req, original_role)


@pytest.mark.parametrize('model', ['openai/MiniMax-M3', 'openai/unknown'])
def test_unknown_or_too_small_model_stops_before_calling_provider(model):
    with pytest.raises(ValueError):
        budget.output_budget_for_request(request(), role(model=model), answer_tokens=30208)
