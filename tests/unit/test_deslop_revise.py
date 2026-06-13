"""Unit tests for the production deslop self-review rewrite loop."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

import bestseller.services.deslop_revise as deslop
from bestseller.services.deslop_revise import revise_prose_deslop
from bestseller.services.llm import LLMCompletionResult

pytestmark = pytest.mark.unit

# A draft the detector flags (info_narration + 不是X variant), and a clean rewrite.
_DIRTY = "他翻开旧册。没人看见他三年来每夜的功课。这不是寻常的册子，是命册。"
_CLEAN = "他翻开旧册，指腹压在卷边那道折痕上，压了三年，折痕里还嵌着干墨。"


def _fake_result(content: str) -> LLMCompletionResult:
    return LLMCompletionResult(
        content=content, provider="mock", model_name="mock", llm_run_id=uuid4(),
        input_tokens=1, output_tokens=1, finish_reason="stop",
    )


def _run(content, **kw):
    return asyncio.run(
        revise_prose_deslop(None, object(), content=content, target_chars=40, **kw)
    )


def test_dirty_draft_gets_rewritten_clean(monkeypatch) -> None:
    calls = []

    async def fake_complete(_s, _set, req):
        calls.append(req)
        return _fake_result(_CLEAN)

    monkeypatch.setattr(deslop, "complete_text", fake_complete)
    out = _run(_DIRTY, rounds=2)
    assert out == _CLEAN
    assert len(calls) == 1  # round 1 rewrote to clean; round 2 detected clean, stopped


def test_clean_draft_skips_rewrite(monkeypatch) -> None:
    calls = []

    async def fake_complete(_s, _set, req):
        calls.append(req)
        return _fake_result("should not be used")

    monkeypatch.setattr(deslop, "complete_text", fake_complete)
    out = _run(_CLEAN, rounds=2)
    assert out == _CLEAN
    assert calls == []  # detector clean → no model call


def test_short_rewrite_is_rejected(monkeypatch) -> None:
    async def fake_complete(_s, _set, req):
        return _fake_result("太短")  # < 60% of original → rejected

    monkeypatch.setattr(deslop, "complete_text", fake_complete)
    out = _run(_DIRTY, rounds=2)
    assert out == _DIRTY  # kept original, not the truncated rewrite


def test_empty_input_noop(monkeypatch) -> None:
    out = _run("", rounds=3)
    assert out == ""
