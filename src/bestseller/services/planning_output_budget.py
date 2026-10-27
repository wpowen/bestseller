"""Versioned reasoning-inclusive output allowance for chapter source admission.

This changes execution capacity, not the source/grade contract or repair count.
Legacy requests without this explicit receipt keep their original wire input.
"""

from bestseller.services import minimax_m31_protocol as m31
from bestseller.services.word_targets import model_output_token_ceiling

SCHEMA = "planning-reasoning-output-budget.v1"
TEMPLATE = "planning_draft_admission"
PROMPT_VERSION = "planning-draft-admission.v3"
LITERAL_FULL_SOURCE_VERSION = "v4-literal-full-source.v1"
LITERAL_ID_SOURCE_VERSION = "v5-literal-full-source.v1"
PROMPT_VERSIONS = {PROMPT_VERSION, "planning-draft-admission.v4", "planning-draft-admission.v5",
    LITERAL_FULL_SOURCE_VERSION, LITERAL_ID_SOURCE_VERSION}
ANSWER_TOKENS = 6000


def is_literal_full_source_request(request) -> bool:  # noqa: ANN001
    return (request.logical_role == "critic" and request.prompt_template == TEMPLATE
        and request.prompt_version in {LITERAL_FULL_SOURCE_VERSION, LITERAL_ID_SOURCE_VERSION})


def output_budget(role):
    if not m31.applies(role.model):
        return None
    ceiling = model_output_token_ceiling(role.model)
    total = max(ANSWER_TOKENS, int(role.max_tokens))
    if ceiling is not None:
        total = min(total, int(ceiling))
    if total < ANSWER_TOKENS:
        raise ValueError("planning_output_budget:model_capacity_below_answer_allowance")
    # Widening to the role allowance must not accidentally switch the M3.1
    # narrowed-cap default from low to max thinking. Explicit effort wins.
    return {
        "schema": SCHEMA,
        "model": role.model,
        "answer_tokens": ANSWER_TOKENS,
        "role_output_tokens": int(role.max_tokens),
        "total_output_tokens": total,
        "reasoning_effort": m31.reasoning_effort(role, max_completion_tokens=ANSWER_TOKENS),
    }


def apply_output_budget(request, role):
    receipt = request.metadata.get("planning_output_budget")
    if receipt is None:
        return role
    if (request.prompt_template != TEMPLATE or request.prompt_version not in PROMPT_VERSIONS
            or request.logical_role != "critic" or not m31.applies(role.model)
            or receipt != output_budget(role)
            or request.max_tokens_override != receipt["total_output_tokens"]):
        raise ValueError("planning_output_budget:request_binding_changed")
    return role.model_copy(update={"reasoning_effort": receipt["reasoning_effort"]})


def admission_output_budget(settings, project, *, schema, system):
    """Resolve the actual selected role before measuring the complete request."""
    if schema not in PROMPT_VERSIONS:
        return None
    from bestseller.services import llm, retained_review_input_proof as proof

    request = llm.LLMCompletionRequest(
        logical_role="critic", project_id=project.id,
        system_prompt=system, user_prompt="Output allowance preflight, never dispatched.",
        prompt_template=TEMPLATE, prompt_version=schema,
        max_tokens_override=ANSWER_TOKENS, fallback_response="{}",
    )
    catalog, _ = proof._fresh_catalog()
    role = proof._fresh_role(settings, project, request, catalog)
    from bestseller.services import model_catalog
    from bestseller.settings import apply_runtime_llm_profile

    if not model_catalog.selected_model_id(project.metadata_json):
        role = model_catalog.legacy_project_role(
            apply_runtime_llm_profile(settings), project.id, "critic") or role
    return output_budget(role)
