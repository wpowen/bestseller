"""正文层反债务化 + 比喻逻辑自洽测试（2026-07-08 用户真机终审二次根治）。

真机原文（project=guize-shuohuang-fw ch1）：
- 构思层金手指干干净净（"污染值/协议区共生绑定"，不含一个"账"字），写手
  描述"签字接受代价"时却自己长出"但写了他就是认下这笔账……白板上的字就是
  给协议区的欠条，第一条欠条"——反债务化闸门此前只在 conception.py 生效，
  没接到正文写手层。
- 同一场景反复出现"购物筐拎在手里晃，像拎着一只空了的胃"——喻体（空了的
  胃）和本体（晃动的购物筐）毫无可感知的相似点，跨越至少 3 轮场景重写都
  没被改掉；场景评审七轴里没有一条判断"比喻讲不讲得通"。
"""

from __future__ import annotations

import pytest

from bestseller.services import reviews
from bestseller.services.ai_flavor.detector import detect, _detect_debt_metaphor_leak
from bestseller.services.drafts import render_anti_debt_prose_guardrail

pytestmark = pytest.mark.unit


# ── 检测器：正文债务化比喻回流 ────────────────────────────────────────────


def test_debt_metaphor_leak_detector_is_retired() -> None:
    """2026-08-02: flagging every 债/账/欠 in finished prose deleted the story.

    The deslop pass rewrote the flagged sentences out of the chapter. In a book
    about an unpaid bill, a sect's resource accounts, or a favour owed, that is
    the plot being edited away.
    """
    text = (
        "但写了他就是认下这笔账：协议区以'写下副作用'完成对宿主的握手确认，"
        "白板上的字就是给协议区的欠条，第一条欠条。"
    )
    assert _detect_debt_metaphor_leak(text, lang="zh") == []
    assert _detect_debt_metaphor_leak("", lang="zh") == []

    report = detect(text * 3, language="zh-CN", chapter_number=1)
    assert not any(s.category == "debt_metaphor_leak" for s in report.spans)


def test_anti_debt_prose_guardrail_is_retired() -> None:
    """The writer's job is to invent imagery; the guardrail forbade exactly that."""
    premise = "夜班便利店店员凌晨两点照镜子，看见规则在空气中显形。"
    assert render_anti_debt_prose_guardrail(premise, is_en=False) == ""
    assert render_anti_debt_prose_guardrail(premise, is_en=True) == ""
    assert render_anti_debt_prose_guardrail(None, is_en=False) == ""


# ── 场景评审：比喻/意象逻辑自洽（逻辑自洽硬轴第⑤条） ──────────────────────


def _review_fixtures() -> tuple:
    from types import SimpleNamespace

    from bestseller.services.reviews import SceneReviewFinding, SceneReviewResult, SceneReviewScores

    project = SimpleNamespace(
        title="听见规则在撒谎",
        genre="都市异能",
        sub_genre="规则怪谈",
        language="zh-CN",
        metadata_json={"writing_profile": {"market": {"platform_target": "番茄小说"}}},
    )
    chapter = SimpleNamespace(chapter_number=2)
    scene = SimpleNamespace(
        scene_number=1,
        title="便利店的顾客",
        purpose={"story": "第一次目击规则显形", "emotion": "惊"},
    )
    draft = SimpleNamespace(content_md="男顾客挑完酸奶走回来，购物筐拎在手里晃。")
    required = {
        name: 0.5
        for name, field in SceneReviewScores.model_fields.items()
        if field.is_required() and field.annotation is float
    }
    review_result = SceneReviewResult(
        verdict="rewrite",
        severity_max="high",
        scores=SceneReviewScores(**required),
        findings=[SceneReviewFinding(category="conflict", severity="high", message="冲突弱")],
    )
    return project, chapter, scene, draft, review_result


def test_scene_review_prompt_has_simile_coherence_subcheck() -> None:
    project, chapter, scene, draft, review_result = _review_fixtures()
    system_prompt, _ = reviews.build_scene_review_prompts(project, chapter, scene, draft, review_result)
    assert "比喻/意象不通" in system_prompt
    assert "空了的胃" in system_prompt  # 真机反例原样出现在判断标准里
    # 轴 7 仍是"逻辑自洽（硬轴）"且第 7 项 FAIL 仍强制 rewrite —— 不破坏既有契约
    assert "逻辑自洽（硬轴）" in system_prompt
    assert "第 7 项 FAIL" in system_prompt
