"""场景接续性修复的 prompt 渲染测试（2026-07-07 真机 ch1 重演病根治）。

真机病灶：ch1 场景2把场景1结尾的『墨字入掌』事件换措辞、换人物重演一遍
（家属男变女），整章逻辑断裂冷读者弃书。词法查重被标定证伪（边界 Jaccard
坏样本 0.019 vs 正常 0.006-0.014 无分离度），根治=写手 prompt 纪律 +
场景评审员第 6 轴（接续性硬轴，喂上一场结尾）+ 场景卡 participants 列全。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services import reviews as review_services
from bestseller.services.drafts import (
    _render_chapter_first_opening_contract,
    _render_chapter_first_scene_cards,
    _render_recent_scene_section,
)
from bestseller.services.reviews import SceneReviewFinding, SceneReviewResult, SceneReviewScores

pytestmark = pytest.mark.unit


# ── 写手侧：上一场结尾续写铁律 ─────────────────────────────────────────────


def test_recent_scene_extended_tail_bans_redramatization() -> None:
    text = _render_recent_scene_section(
        [
            {
                "chapter_number": 1,
                "scene_number": 1,
                "scene_title": "攥腕",
                "summary": "家属攥住纪蘅手腕，墨字入掌",
                "extended_tail": "墨迹最末那个字从他腕骨上剥下来，顺着旧疤往掌根走。",
            }
        ]
    )
    assert "续写铁律" in text
    assert "换一套措辞" in text  # 禁换皮重演
    assert "身份、性别、位置就此锁定" in text  # 无名配角身份锁定
    assert "下一秒写起" in text


def test_recent_scene_without_tail_keeps_summary_only() -> None:
    text = _render_recent_scene_section(
        [{"chapter_number": 1, "scene_number": 1, "summary": "抢救开始"}]
    )
    assert "抢救开始" in text
    assert "续写铁律" not in text


# ── 写手侧：场景卡入场状态=既成事实 ────────────────────────────────────────


def _scene_card(number: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        scene_number=number,
        title="代价入掌",
        scene_type="pressure",
        time_label="凌晨三点",
        participants=["纪蘅", "家属(男,五十多岁)"],
        purpose={"story": "读出规则并付出代价", "emotion": "怕"},
        entry_state={"summary": "墨字钻进纪蘅掌心的瞬间"},
        exit_state={"summary": "纪蘅意识到愤怒迟了一格"},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        hook_requirement="尾钩",
        target_word_count=1200,
        metadata_json={},
        rewrite_hint="",
    )


def test_scene_cards_mark_entry_state_as_already_done() -> None:
    text = _render_chapter_first_scene_cards([_scene_card()])
    assert "既成事实" in text
    assert "严禁把它当剧情在本场重演一遍" in text
    assert "下一拍写起" in text


# ── 写手侧：冷读者定位授权 ────────────────────────────────────────────────


def test_opening_contract_authorizes_cold_reader_orientation() -> None:
    chapter = SimpleNamespace(
        chapter_number=1, opening_situation="急诊抢救台前家属攥住医生手腕"
    )
    text = _render_chapter_first_opening_contract(chapter, [_scene_card(1)])
    assert "冷读者定位" in text
    assert "视角人物是谁" in text
    assert "连接组织授权" in text
    assert "专有名词预算" in text


def test_opening_contract_skips_late_chapters() -> None:
    chapter = SimpleNamespace(chapter_number=11, opening_situation="x")
    assert _render_chapter_first_opening_contract(chapter, [_scene_card(1)]) == ""


# ── 评审侧：第 6 轴接续性 + 上一场结尾注入 ─────────────────────────────────


def _review_fixtures() -> tuple[SimpleNamespace, SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    project = SimpleNamespace(
        title="加班怪谈",
        genre="规则怪谈",
        sub_genre="都市异能",
        language="zh-CN",
        metadata_json={"writing_profile": {"market": {"platform_target": "番茄小说"}}},
    )
    chapter = SimpleNamespace(chapter_number=1)
    scene = SimpleNamespace(
        scene_number=2,
        title="代价入掌",
        purpose={"story": "付出第一格代价", "emotion": "怕"},
    )
    draft = SimpleNamespace(content_md="家属坐在折叠椅上，开始念报告。")
    return project, chapter, scene, draft


def _review_result() -> SceneReviewResult:
    required = {
        name: 0.5
        for name, field in SceneReviewScores.model_fields.items()
        if field.is_required() and field.annotation is float
    }
    return SceneReviewResult(
        verdict="rewrite",
        severity_max="high",
        scores=SceneReviewScores(**required),
        findings=[
            SceneReviewFinding(category="conflict", severity="high", message="冲突弱")
        ],
    )


def test_scene_review_prompt_has_continuity_axis_and_prev_tail() -> None:
    project, chapter, scene, draft = _review_fixtures()
    scene_context = SimpleNamespace(
        previous_scene_summaries=[
            {
                "chapter_number": 1,
                "scene_number": 1,
                "summary": "墨字入掌",
                "extended_tail": "墨迹最末那个字从他腕骨上剥下来。",
            }
        ]
    )
    system_prompt, user_prompt = review_services.build_scene_review_prompts(
        project, chapter, scene, draft, _review_result(), scene_context=scene_context
    )
    assert "接续性（硬轴）" in system_prompt
    assert "第 6 项 FAIL" in system_prompt
    assert "上一场结尾原文" in user_prompt
    assert "墨迹最末那个字" in user_prompt


def test_scene_review_prompt_without_context_still_has_axis() -> None:
    project, chapter, scene, draft = _review_fixtures()
    system_prompt, user_prompt = review_services.build_scene_review_prompts(
        project, chapter, scene, draft, _review_result()
    )
    assert "接续性（硬轴）" in system_prompt
    assert "上一场结尾原文" not in user_prompt
