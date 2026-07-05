"""Unit tests for the qimao opening inline-revise closure helper.

Guards the safety contract: the helper never raises and never returns an empty
or drastically-truncated draft, so wiring it into the autonomous forward-writing
hot path cannot break generation. The gate re-eval / keep-better decision lives
in the caller.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

import bestseller.services.opening_revise as opening
from bestseller.services.llm import LLMCompletionResult

pytestmark = pytest.mark.unit

_ORIGINAL = (
    "门板上三下闷响。抽屉里那页黄纸自己渗出一行字。"
    "他还没抬头，门外的人已经趴在门槛上，念着同一句话。"
)
_REWRITTEN = (
    "我盯着那页黄纸，指节抵着抽屉沿——门外第三声闷响刚落，"
    "趴在门槛上的人抬起眼，冲我念出那句只有我听得懂的话。我得在天亮前决定开不开门。"
)


def _fake_result(content: str) -> LLMCompletionResult:
    return LLMCompletionResult(
        content=content, provider="mock", model_name="mock", llm_run_id=uuid4(),
        input_tokens=1, output_tokens=1, finish_reason="stop",
    )


def _run(content: str, **kw) -> str:
    return asyncio.run(
        opening.revise_opening_qimao(
            None, object(), content=content, instructions="【七猫开篇门禁重写任务】", **kw
        )
    )


def test_opening_gets_rewritten(monkeypatch) -> None:
    async def fake_complete(_s, _set, req):
        return _fake_result(_REWRITTEN)

    monkeypatch.setattr(opening, "complete_text", fake_complete)
    assert _run(_ORIGINAL) == _REWRITTEN


def test_short_rewrite_is_rejected(monkeypatch) -> None:
    async def fake_complete(_s, _set, req):
        return _fake_result("太短")  # < 70% of original → rejected

    monkeypatch.setattr(opening, "complete_text", fake_complete)
    assert _run(_ORIGINAL) == _ORIGINAL  # kept original


def test_empty_rewrite_is_rejected(monkeypatch) -> None:
    async def fake_complete(_s, _set, req):
        return _fake_result("")

    monkeypatch.setattr(opening, "complete_text", fake_complete)
    assert _run(_ORIGINAL) == _ORIGINAL


def test_exception_keeps_original(monkeypatch) -> None:
    async def fake_complete(_s, _set, req):
        raise RuntimeError("provider down")

    monkeypatch.setattr(opening, "complete_text", fake_complete)
    assert _run(_ORIGINAL) == _ORIGINAL  # never raises, keeps draft


def test_empty_input_noop() -> None:
    assert _run("") == ""
    assert _run("   ") == "   "
