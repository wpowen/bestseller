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


def test_final_round_regression_reverts_to_best(monkeypatch) -> None:
    """2026-07-04: the last round's rewrite was historically accepted on
    length alone (never re-detected). A final rewrite that ADDS AI-flavor
    must be dropped in favor of the cleanest draft seen."""
    # One-round loop: dirty input → rewrite comes back with MORE tells.
    _WORSE = (
        "他翻开旧册。没人看见他三年来每夜的功课。这不是寻常的册子，是命册。"
        "这不是结束，是开始。他心头一紧，眼瞳一缩。没人知道他为什么这样做。"
    )

    async def fake_complete(_s, _set, req):
        return _fake_result(_WORSE)

    monkeypatch.setattr(deslop, "complete_text", fake_complete)
    out = _run(_DIRTY, rounds=1)
    assert out == _DIRTY  # regressed rewrite discarded


def test_rewrite_below_target_floor_is_rejected(monkeypatch) -> None:
    """A rewrite may not drag an on-target draft below ~70% of target_chars
    even when it clears the 60%-of-source ratio (LENGTH-gate loop guard)."""
    long_dirty = _DIRTY * 6  # well above target

    async def fake_complete(_s, _set, req):
        # 65% of source length → passes the old ratio guard, but far below
        # 70% of the (implied) target when target ≈ source length.
        return _fake_result(long_dirty[: int(len(long_dirty) * 0.65)])

    monkeypatch.setattr(deslop, "complete_text", fake_complete)
    out = asyncio.run(
        revise_prose_deslop(
            None, object(), content=long_dirty,
            target_chars=len(long_dirty), rounds=1,
        )
    )
    assert out == long_dirty


def test_self_check_covers_staccato_and_system_ladder() -> None:
    """Regression: the deslop rewrite prompt must explicitly target the two
    structural tells the span patcher cannot touch and that staccato/repetition
    detectors route here for — single-sentence-paragraph saturation (碎句独段)
    and templated escalating system spam (系统刷屏). Without these clauses the
    rewrite fires but leaves the dominant defect untouched (real ch11 evidence).
    """
    chk = deslop._EXTRA_SELF_CHECK
    assert "单句独段饱和" in chk, "staccato-merge self-check missing"
    assert "系统刷屏" in chk and "数字递增" in chk, "system-ladder self-check missing"
    # The closing instruction must re-count to the new total so the model
    # actually re-scans the added items (12 = +具身动词词族纪律
    # +无来源修辞体系纪律, 2026-08-01).
    assert "上面 12 条" in chk
    assert "具身动词词族" in chk, "verb-tic family discipline missing"
    assert "无来源的修辞体系" in chk, "source-bound imagery self-check missing"


def test_staccato_saturation_routes_to_deslop() -> None:
    """A chapter whose dominant tell is single-sentence-paragraph saturation
    must trip ``needs_deslop_revise`` (it is in the deslop trigger set) so the
    now-staccato-aware rewrite actually runs. Proven separately on real ch11."""
    from bestseller.services.ai_flavor_gate import (
        AiFlavorGateConfig,
        needs_deslop_revise,
        run_ai_flavor_gate,
    )

    solo = [
        "他坐起来。", "天还黑着。", "手机又响。", "他没去接。", "心里发紧。",
        "门外有声。", "脚步停了。", "他屏住气。", "数字跳了。", "又跳一格。",
        "他攥紧手。", "窗帘晃了。", "灯灭了一下。", "他站起来。",
    ]
    filler = "他走到窗边，把窗帘拨开一条缝，楼下的路灯在雨里晕成一团昏黄，街角那家便利店还亮着。"
    paragraphs: list[str] = []
    for i, line in enumerate(solo):
        paragraphs.append(line)
        if i % 5 == 4:
            paragraphs.append(filler)
    content = "\n\n".join(paragraphs)

    cfg = AiFlavorGateConfig(llm_rewrite_enabled=False, write_audit_file=False)
    out = run_ai_flavor_gate(
        chapter_number=1, content_md=content, language="zh-CN", config=cfg,
        llm_rewriter=None, project_output_dir=None,
    )
    issue_ids = {i.id for i in (out.report.issues if out.report else [])}
    assert "AI_FLAVOR_STACCATO_SATURATION" in issue_ids, "staccato not detected"
    assert needs_deslop_revise(out), "saturated staccato must route to deslop"
    # Must NOT hard-block (that would stall an autonomous run); the cure is a
    # rewrite, not a wall.
    assert out.decision != "block"


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
