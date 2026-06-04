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
