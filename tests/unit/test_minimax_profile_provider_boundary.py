from __future__ import annotations

import pytest

from bestseller.services.llm import LLMCompletionRequest, _effective_input_hash
from bestseller.settings import apply_runtime_llm_profile, load_settings, set_runtime_llm_profile


@pytest.mark.parametrize("role_name", ["planner", "writer", "critic", "summarizer", "editor"])
def test_minimax_selection_clears_inherited_foreign_fallback(role_name: str) -> None:
    settings = load_settings()
    original = getattr(settings.llm, role_name)
    original.rate_limit_fallback_model = "openai/z-ai/glm-5.1"
    original.rate_limit_fallback_api_base = "https://foreign.example.invalid/v1"
    original.rate_limit_fallback_api_key_env = "SYNTHETIC_FOREIGN_KEY"
    original.rate_limit_fallback_stream = True
    set_runtime_llm_profile(settings, "minimax")

    effective = apply_runtime_llm_profile(settings)
    selected = getattr(effective.llm, role_name)
    assert selected.model == "openai/MiniMax-M3.1-Flash"
    assert selected.api_base == "https://api.minimaxi.com/v1"
    assert selected.rate_limit_fallback_model is None
    assert selected.rate_limit_fallback_api_base is None
    assert selected.rate_limit_fallback_api_key_env is None
    assert selected.rate_limit_fallback_stream is False
    # Applying a profile must not mutate the caller's cached settings.
    assert original.rate_limit_fallback_model == "openai/z-ai/glm-5.1"

    request = LLMCompletionRequest(
        logical_role=role_name, system_prompt="system", user_prompt="test",
        fallback_response="{}", prompt_template="conception_source_effect_contract",
    )
    prior_primary = selected.model_copy(update={
        "rate_limit_fallback_model": original.rate_limit_fallback_model,
        "rate_limit_fallback_api_base": original.rate_limit_fallback_api_base,
        "rate_limit_fallback_api_key_env": original.rate_limit_fallback_api_key_env,
        "rate_limit_fallback_stream": True,
    })
    assert _effective_input_hash(request, selected) == _effective_input_hash(request, prior_primary)


def test_other_profile_fallback_policy_is_preserved() -> None:
    settings = load_settings()
    settings.llm.writer.rate_limit_fallback_model = "synthetic-existing-fallback"
    set_runtime_llm_profile(settings, "deepseek")
    assert apply_runtime_llm_profile(settings).llm.writer.rate_limit_fallback_model == (
        "synthetic-existing-fallback"
    )
