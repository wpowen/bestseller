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


def test_production_discourse_flavor_triggers_deslop_then_clears(monkeypatch) -> None:
    """The production guarantee (exact compose pipelines.py runs): a chapter
    carrying discourse-level AI flavor (旁白解释来历 + 不是X而是Y) trips
    ``needs_deslop_revise`` even though the span patcher can't touch it; after
    the deslop rewrite the recheck no longer needs revise. Proven end-to-end on
    real ch4 (gate block 88→pass); this is the fast deterministic regression."""
    from bestseller.services.ai_flavor_gate import (
        AiFlavorGateConfig,
        needs_deslop_revise,
        run_ai_flavor_gate,
    )

    cfg = AiFlavorGateConfig(llm_rewrite_enabled=False, write_audit_file=False)
    # Discourse tells (advisory, no static fix → patcher leaves them in):
    # info_narration (那是…的后遗症 / 没人看见) + negated_definition (不是X而是Y).
    dirty = (
        "那是他替师父挡刀留下的后遗症。没人看见他三年来每夜的苦练。"
        "这不是一柄普通的剑，而是斩断因果的凶器。"
    )
    g0 = run_ai_flavor_gate(
        chapter_number=1, content_md=dirty, language="zh-CN", config=cfg,
        llm_rewriter=None, project_output_dir=None,
    )
    assert needs_deslop_revise(g0), "discourse flavor must trigger deslop"

    clean = "他左肩一沉，刀尖在石上磕出一点火星。剑还在鞘里，鞘口缠着三圈旧布。"

    async def fake_complete(_s, _set, req):
        return _fake_result(clean)

    monkeypatch.setattr(deslop, "complete_text", fake_complete)
    revised = asyncio.run(
        revise_prose_deslop(None, object(), content=dirty, target_chars=40, rounds=3)
    )
    g1 = run_ai_flavor_gate(
        chapter_number=1, content_md=revised, language="zh-CN", config=cfg,
        llm_rewriter=None, project_output_dir=None,
    )
    assert not needs_deslop_revise(g1), "after deslop the chapter must be clean"
