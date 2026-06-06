"""Unit tests for design-time imagery system generation (mocked LLM)."""

# ruff: noqa: RUF001, RUF002, RUF003, E501, ANN001, ANN201

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bestseller.services import imagery_system_design as mod
from bestseller.services.imagery_system_design import (
    _resolve_premise,
    ensure_book_imagery_system,
    imagery_system_design_enabled,
)

_DESIGNER_JSON = json.dumps(
    {
        "theme_core": "借出去的运，记得清却偿不还",
        "images": [
            {"name": "黑纹账本", "carrier": "掌心顺腕骨蹿的黑线", "emotion_fn": "羞愧", "theme_fn": "代价可记账却偿不清"},
            {"name": "旧手机", "carrier": "裂痕里卡住的未发消息", "emotion_fn": "亏欠", "theme_fn": "想说的话迟一步"},
        ],
    },
    ensure_ascii=False,
)


def _settings(enabled: bool = True):
    return SimpleNamespace(pipeline=SimpleNamespace(enable_imagery_system_design=enabled))


def _project(**over):
    base = {"metadata_json": {"logline": "电工借运成神，借出去的运要记账偿还。"},
            "genre": "都市异能", "language": "zh-CN", "id": "p1"}
    base.update(over)
    return SimpleNamespace(**base)


class _FakeSession:
    async def flush(self) -> None:
        return None


def test_flag_default_and_resolution():
    assert imagery_system_design_enabled(_settings(True)) is True
    assert imagery_system_design_enabled(_settings(False)) is False


def test_resolve_premise_prefers_logline_then_book_spec():
    assert _resolve_premise(_project()).startswith("电工借运")
    p = _project(metadata_json={"book_spec": {"premise": "一句来自 book_spec 的前提"}})
    assert "book_spec 的前提" in _resolve_premise(p)
    assert _resolve_premise(_project(metadata_json={})) == ""


@pytest.mark.asyncio
async def test_disabled_flag_is_noop(monkeypatch):
    called = False

    async def _boom(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("LLM must not be called when disabled")

    monkeypatch.setattr(mod, "complete_text", _boom)
    out = await ensure_book_imagery_system(_FakeSession(), _settings(False), _project())
    assert out is None
    assert called is False


@pytest.mark.asyncio
async def test_english_project_is_noop(monkeypatch):
    monkeypatch.setattr(mod, "complete_text", _unexpected_llm)
    out = await ensure_book_imagery_system(_FakeSession(), _settings(True), _project(language="en"))
    assert out is None


@pytest.mark.asyncio
async def test_idempotent_skips_llm_when_already_present(monkeypatch):
    monkeypatch.setattr(mod, "complete_text", _unexpected_llm)
    existing = {"theme_core": "x", "images": [{"name": "镜子", "carrier": "裂了的镜面"}]}
    proj = _project(metadata_json={"logline": "...", "imagery_system": existing})
    out = await ensure_book_imagery_system(_FakeSession(), _settings(True), proj)
    assert out == existing  # returned without an LLM call


@pytest.mark.asyncio
async def test_happy_path_designs_and_persists(monkeypatch):
    async def _fake_complete(session, settings, req):
        return SimpleNamespace(content=_DESIGNER_JSON)

    monkeypatch.setattr(mod, "complete_text", _fake_complete)
    monkeypatch.setattr(mod, "flag_modified", lambda obj, key: None)
    proj = _project()
    out = await ensure_book_imagery_system(_FakeSession(), _settings(True), proj)
    assert out is not None
    assert len(out["images"]) == 2
    assert out["images"][0]["name"] == "黑纹账本"
    # persisted into metadata_json so the bible loader can expose it
    assert proj.metadata_json["imagery_system"]["images"][0]["carrier"].startswith("掌心")


@pytest.mark.asyncio
async def test_empty_premise_is_noop(monkeypatch):
    monkeypatch.setattr(mod, "complete_text", _unexpected_llm)
    out = await ensure_book_imagery_system(_FakeSession(), _settings(True), _project(metadata_json={}))
    assert out is None


async def _unexpected_llm(*a, **k):
    raise AssertionError("LLM must not be called in this path")
