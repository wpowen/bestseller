from __future__ import annotations

import pytest

from bestseller.services import model_catalog as mc


@pytest.fixture(autouse=True)
def _clear_cache():
    mc._load_catalog_raw.cache_clear()
    yield
    mc._load_catalog_raw.cache_clear()


def test_catalog_loads_known_entries():
    entries = mc.load_model_catalog()
    ids = {e.id for e in entries}
    assert "minimax-m3" in ids
    assert "minimax-m2.7-highspeed" in ids
    by_id = {e.id: e for e in entries}
    hs = by_id["minimax-m2.7-highspeed"]
    assert hs.model == "openai/MiniMax-M2.7-highspeed"
    assert hs.api_base == "https://api.minimaxi.com/v1"
    assert hs.api_key_env == "MINIMAX_API_KEY"
    assert hs.vendor == "MiniMax"


def test_availability_follows_env_key(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    by_id = {e.id: e for e in mc.load_model_catalog()}
    assert by_id["minimax-m2.7-highspeed"].available is True
    assert by_id["claude-opus-4-5"].available is False


def test_availability_uses_dotenv_merged_runtime_env(monkeypatch):
    monkeypatch.delenv("QWEN_CODING_PLAN_API_KEY", raising=False)
    monkeypatch.setattr(
        mc,
        "get_runtime_env_value",
        lambda name: "dotenv-key" if name == "QWEN_CODING_PLAN_API_KEY" else None,
    )

    by_id = {e.id: e for e in mc.load_model_catalog()}

    assert by_id["qwen3.7-plus-coding-plan"].available is True


def test_get_entry_by_id():
    assert mc.get_model_catalog_entry("minimax-m3").id == "minimax-m3"
    assert mc.get_model_catalog_entry("nope") is None
    assert mc.get_model_catalog_entry(None) is None


def test_resolve_project_entry(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "x")
    entry = mc.resolve_project_model_entry({"llm_model_id": "minimax-m2.7-highspeed"})
    assert entry is not None and entry.model == "openai/MiniMax-M2.7-highspeed"
    # No selection -> None
    assert mc.resolve_project_model_entry({}) is None
    assert mc.resolve_project_model_entry(None) is None


def test_resolve_skips_unavailable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # selected but key missing -> not resolvable
    assert mc.resolve_project_model_entry({"llm_model_id": "claude-opus-4-5"}) is None


def test_apply_model_override_to_role_settings(monkeypatch):
    from bestseller.services.llm import _apply_model_override
    from bestseller.settings import LLMRoleSettings

    rs = LLMRoleSettings(
        model="openai/MiniMax-M3",
        temperature=0.7,
        max_tokens=8192,
        timeout_seconds=120,
        api_base="https://api.minimaxi.com/v1",
        api_key_env="MINIMAX_API_KEY",
        model_override="openai/MiniMax-M3",
    )
    entry = mc.get_model_catalog_entry("deepseek-v4-flash")
    out = _apply_model_override(rs, entry)
    assert out.model == "deepseek/deepseek-v4-flash"
    assert out.api_base == "https://api.deepseek.com"
    assert out.api_key_env == "DEEPSEEK_API_KEY"
    assert out.model_override == "deepseek/deepseek-v4-flash"
    # original untouched (immutability)
    assert rs.model == "openai/MiniMax-M3"


def test_mimo_entry_has_custom_api_key_header(monkeypatch):
    monkeypatch.setenv("XIAOMI_MIMO_API_KEY", "x")
    entry = mc.get_model_catalog_entry("xiaomi-mimo-v2.5-pro")
    assert entry is not None
    assert entry.model == "openai/mimo-v2.5-pro"
    assert entry.api_base == "https://token-plan-cn.xiaomimimo.com/v1"
    assert entry.api_key_env == "XIAOMI_MIMO_API_KEY"
    assert entry.api_key_header == "api-key"


def test_apply_override_sets_custom_header_for_mimo():
    from bestseller.services.llm import _apply_model_override
    from bestseller.settings import LLMRoleSettings

    rs = LLMRoleSettings(
        model="openai/MiniMax-M3", temperature=0.7, max_tokens=8192,
        timeout_seconds=120, api_base="https://api.minimaxi.com/v1",
        api_key_env="MINIMAX_API_KEY", api_key_header=None,
    )
    mimo = mc.get_model_catalog_entry("xiaomi-mimo-v2.5-pro")
    out = _apply_model_override(rs, mimo)
    assert out.model == "openai/mimo-v2.5-pro"
    assert out.api_key_header == "api-key"
    assert out.api_key_env == "XIAOMI_MIMO_API_KEY"


def test_apply_override_clears_header_when_switching_to_bearer():
    # Switching from a header-auth model (MiMo) to a Bearer model must clear
    # the custom header, else auth breaks.
    from bestseller.services.llm import _apply_model_override
    from bestseller.settings import LLMRoleSettings

    rs = LLMRoleSettings(
        model="openai/mimo-v2.5-pro", temperature=0.7, max_tokens=8192,
        timeout_seconds=120, api_base="https://token-plan-cn.xiaomimimo.com/v1",
        api_key_env="XIAOMI_MIMO_API_KEY", api_key_header="api-key",
    )
    nim = mc.get_model_catalog_entry("nim-mistral-large-3")
    out = _apply_model_override(rs, nim)
    assert out.model == "openai/mistralai/mistral-large-3-675b-instruct-2512"
    assert out.api_key_header is None
    assert out.api_key_env == "NVIDIA_API_KEY"


def test_catalog_has_expanded_vendors():
    ids = {e.id for e in mc.load_model_catalog()}
    for expected in (
        "xiaomi-mimo-v2.5-pro", "nim-deepseek-v4-pro", "nim-kimi-k2.6",
        "nim-mistral-large-3", "nim-llama-3.3-70b", "minimax-m3",
    ):
        assert expected in ids, expected


def test_retired_entries_unavailable_even_with_key(monkeypatch):
    # Upstream removed these models (410 Gone / 404 Function Not Found); the
    # shared NVIDIA_API_KEY being present must NOT make them available.
    monkeypatch.setenv("NVIDIA_API_KEY", "x")
    by_id = {e.id: e for e in mc.load_model_catalog()}
    for dead in ("nim-deepseek-v4-pro", "nim-kimi-k2.6"):
        entry = by_id[dead]
        assert entry.retired is True, dead
        assert entry.available is False, dead
        assert entry.unavailable_reason, dead
        assert "已下线" in entry.unavailable_reason, dead
    # Live NVIDIA entries on the same key stay available.
    assert by_id["nim-mistral-large-3"].available is True
    assert by_id["nim-mistral-large-3"].unavailable_reason is None


def test_resolve_skips_retired_selection(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "x")
    assert mc.resolve_project_model_entry({"llm_model_id": "nim-kimi-k2.6"}) is None


def test_missing_key_reason_names_the_env_var(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(mc, "get_runtime_env_value", lambda name: None)
    by_id = {e.id: e for e in mc.load_model_catalog()}
    entry = by_id["claude-opus-4-5"]
    assert entry.available is False
    assert "ANTHROPIC_API_KEY" in (entry.unavailable_reason or "")


def test_runtime_dead_registry_flips_availability(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "x")
    mc.clear_runtime_dead_models()
    try:
        by_id = {e.id: e for e in mc.load_model_catalog()}
        assert by_id["nim-mistral-large-3"].available is True
        mc.mark_model_runtime_dead(
            "openai/mistralai/mistral-large-3-675b-instruct-2512",
            "NotFoundError: 404 Function Not Found",
        )
        by_id = {e.id: e for e in mc.load_model_catalog()}
        entry = by_id["nim-mistral-large-3"]
        assert entry.available is False
        assert "404" in (entry.unavailable_reason or "")
        # Registry keys are model strings; other entries are untouched.
        assert by_id["nim-llama-3.3-70b"].available is True
    finally:
        mc.clear_runtime_dead_models()


def test_mark_model_runtime_dead_ignores_empty_model():
    mc.clear_runtime_dead_models()
    mc.mark_model_runtime_dead(None, "reason")
    mc.mark_model_runtime_dead("", "reason")
    assert mc.runtime_dead_reason(None) is None
    assert mc.runtime_dead_reason("") is None
