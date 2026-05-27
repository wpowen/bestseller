from bestseller.services.llm import LLMCompletionRequest, _build_messages


def test_build_messages_cache_system_for_anthropic():
    request = LLMCompletionRequest(
        logical_role="writer",
        system_prompt="stable",
        user_prompt="volatile",
        fallback_response="fallback",
        cache_system=True,
    )
    messages = _build_messages(request, "anthropic")
    assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_build_messages_cache_system_ignored_for_other_provider():
    request = LLMCompletionRequest(
        logical_role="writer",
        system_prompt="stable",
        user_prompt="volatile",
        fallback_response="fallback",
        cache_system=True,
    )
    messages = _build_messages(request, "openai")
    assert messages[0]["content"] == "stable"
