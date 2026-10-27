"""Prospective full-source patch allowance; legacy wire inputs remain unchanged."""

import math

from bestseller.services import minimax_m31_protocol as m31
from bestseller.services.word_targets import model_output_token_ceiling

SCHEMA = 'planning-surgical-output-budget.v1'
PROMPT_VERSION = '3.5-source-complete-repair'
DOMAIN_VERSION = '3.6-domain-fields'
DOMAIN_TEMPLATE = 'planner_outline_domain_field_corrections'
FLAT_DOMAIN_VERSION = '3.7-domain-fields-flat'
FLAT_DOMAIN_TEMPLATE = 'planner_outline_domain_flat_field_corrections'
CONSISTENCY_VERSION = '3.8-source-consistency'
CONSISTENCY_TEMPLATE = 'planner_outline_source_consistency'
FANQIE_FIELDS_TEMPLATE = 'planner_structured_field_correction'
FANQIE_FIELDS_VERSION = 'fanqie-plan-fields-v1'


def applies(request):
    if (request.logical_role == 'planner'
            and request.prompt_template == FANQIE_FIELDS_TEMPLATE
            and request.prompt_version == FANQIE_FIELDS_VERSION):
        return True
    if request.logical_role=='planner' and request.prompt_version==CONSISTENCY_VERSION:
        return request.prompt_template==CONSISTENCY_TEMPLATE
    if request.logical_role=='planner' and request.prompt_version==FLAT_DOMAIN_VERSION:
        return request.prompt_template==FLAT_DOMAIN_TEMPLATE
    if request.logical_role=='planner' and request.prompt_version==DOMAIN_VERSION:
        return request.prompt_template==DOMAIN_TEMPLATE
    return (request.logical_role == 'planner' and request.prompt_version == PROMPT_VERSION
        and request.prompt_template.startswith('planner_')
        and request.prompt_template.endswith('_commercial_surgical_repair'))


def output_budget(role, *, answer_tokens):
    if type(answer_tokens) is not int or answer_tokens < 1:
        raise ValueError('planning_surgical_budget:answer_allowance_invalid')
    total = int(role.max_tokens)
    ceiling = model_output_token_ceiling(role.model)
    if ceiling is not None:
        total = min(total, int(ceiling))
    if total < answer_tokens:
        raise ValueError('planning_surgical_budget:role_below_answer_allowance')
    effort = (role.reasoning_effort or 'medium') if m31.applies(role.model) else role.reasoning_effort
    return {'schema': SCHEMA, 'model': role.model, 'answer_tokens': answer_tokens,
        'role_output_tokens': int(role.max_tokens), 'total_output_tokens': total,
        'reasoning_effort': effort}


def output_budget_for_request(request, role, *, answer_tokens):
    """Reserve a complete projection answer and thinking without changing old routes."""
    if not (request.logical_role == 'planner' and request.prompt_template == CONSISTENCY_TEMPLATE
            and request.prompt_version == CONSISTENCY_VERSION):
        return output_budget(role, answer_tokens=answer_tokens)
    if type(answer_tokens) is not int or answer_tokens < 1:
        raise ValueError('planning_surgical_budget:answer_allowance_invalid')
    configured = int(role.max_tokens)
    needed = math.ceil(answer_tokens * 1.25) + max(8192, configured // 2)
    total = max(configured, needed)
    ceiling = model_output_token_ceiling(role.model)
    if ceiling is None:
        if needed > configured:
            raise ValueError('planning_surgical_budget:unknown_model_output_capacity')
    elif needed > ceiling:
        raise ValueError('planning_surgical_budget:model_below_complete_answer_allowance')
    else:
        total = min(total, int(ceiling))
    effort = (role.reasoning_effort or 'medium') if m31.applies(role.model) else role.reasoning_effort
    return {'schema': 'planning-consistency-output-budget.v1', 'model': role.model,
        'answer_tokens': answer_tokens, 'role_output_tokens': configured,
        'total_output_tokens': total, 'reasoning_effort': effort,
        'answer_growth_factor': 1.25, 'reasoning_reserve_tokens': max(8192, configured // 2)}


def apply_role(request, role):
    if not applies(request):
        if request.metadata.get('planning_surgical_output_budget') is not None:
            raise ValueError('planning_surgical_budget:wrong_request_contract')
        return role
    receipt = request.metadata.get('planning_surgical_output_budget')
    if receipt is not None:
        if not isinstance(receipt, dict):
            raise ValueError('planning_surgical_budget:receipt_invalid')
        expected = output_budget_for_request(request, role, answer_tokens=receipt.get('answer_tokens'))
        if receipt != expected or request.max_tokens_override != expected['total_output_tokens']:
            raise ValueError('planning_surgical_budget:request_binding_changed')
    # The immutable prospective version also reconstructs its execution settings
    # during read-only physical proof, where story metadata is never trusted.
    if m31.applies(role.model) and role.reasoning_effort is None:
        return role.model_copy(update={'reasoning_effort': 'medium'})
    return role


def resolve_output_budget(settings, project, *, logical_name, answer_tokens):
    from bestseller.services import llm

    request = llm.LLMCompletionRequest(logical_role='planner', project_id=project.id,
        system_prompt='Output allowance preflight; never dispatched.', user_prompt='{}',
        fallback_response='{}', prompt_template=f'planner_{logical_name}_commercial_surgical_repair',
        prompt_version=PROMPT_VERSION, max_tokens_override=answer_tokens)
    role = resolve_request_role(settings, project, request)
    return output_budget(role, answer_tokens=answer_tokens)


def resolve_request_role(settings, project, request):
    from bestseller.services import llm, model_catalog, retained_review_input_proof as proof
    from bestseller.settings import apply_runtime_llm_profile

    catalog, _ = proof._fresh_catalog()
    role = proof._fresh_role(settings, project, request, catalog)
    if not model_catalog.selected_model_id(project.metadata_json):
        role = model_catalog.legacy_project_role(
            apply_runtime_llm_profile(settings), project.id, request.logical_role) or role
    return llm._apply_conception_phase_settings(request, role)
