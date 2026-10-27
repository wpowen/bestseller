import pytest

from bestseller.settings import load_settings


@pytest.mark.parametrize("word", ["off", "on", "yes", "no"])
def test_yaml_legacy_boolean_words_remain_valid_string_fields(word):
    settings = load_settings(env={"BESTSELLER__GENERATION__GENRE": word})
    assert settings.generation.genre == word


def test_off_mode_and_false_boolean_can_coexist_in_environment():
    settings = load_settings(env={
        "BESTSELLER__LLM__INDEPENDENT_JUDGE_MODE": "off",
        "BESTSELLER__LLM__RETRY__RATE_LIMIT_FALLBACK_ENABLED": "false",
    })
    assert settings.llm.independent_judge_mode == "off"
    assert settings.llm.retry.rate_limit_fallback_enabled is False


@pytest.mark.parametrize("word,wanted", [("off", False), ("on", True)])
def test_legacy_boolean_words_still_validate_for_typed_boolean_fields(word, wanted):
    settings = load_settings(env={"BESTSELLER__LLM__MOCK": word})
    assert settings.llm.mock is wanted
