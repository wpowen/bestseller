"""Tests for the scene-plan richness gate (services.scene_plan_richness)."""

from __future__ import annotations

import pytest

from bestseller.services.scene_plan_richness import (
    GENERIC_EMOTION_PATTERNS,
    GENERIC_STATE_PATTERNS,
    GENERIC_STORY_PATTERNS,
    RichnessIssue,
    RichnessReport,
    validate_scene_card_richness,
    validate_scene_model,
)


# ---------------------------------------------------------------------------
# Fixtures — baseline "rich" card used as a mutation starting point
# ---------------------------------------------------------------------------

def _rich_card() -> dict:
    """A scene card that should pass every check."""
    return {
        "scene_type": "confrontation",
        "purpose": {
            "story": "林风破解浮标封锁并夺走核心灵石，触发巡守阵眼",
            "emotion": "从侥幸转为背水一战的决绝",
        },
        "entry_state": {
            "location": "禁地外围浮标阵",
            "linfeng": "受伤但藏匿中，持伪造通行符",
        },
        "exit_state": {
            "location": "阵眼激活，禁地中心",
            "linfeng": "失去通行符，身份暴露，被巡守包围",
        },
        "participants": ["林风", "巡守长老"],
        "language": "zh-CN",
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_rich_card_passes():
    report = validate_scene_card_richness(**_rich_card())
    assert report.is_rich_enough
    assert report.severity == "pass"
    assert report.issues == ()


# ---------------------------------------------------------------------------
# purpose.story checks
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_missing_story_purpose_is_critical():
    card = _rich_card()
    card["purpose"] = {"emotion": card["purpose"]["emotion"]}
    report = validate_scene_card_richness(**card)
    assert not report.is_rich_enough
    assert report.severity == "critical"
    assert any(i.code == "missing_story_purpose" for i in report.issues)


@pytest.mark.unit
def test_short_story_purpose_is_critical():
    card = _rich_card()
    card["purpose"] = {"story": "打架", "emotion": card["purpose"]["emotion"]}
    report = validate_scene_card_richness(**card)
    assert not report.is_rich_enough
    assert any(i.code == "story_purpose_too_short" for i in report.issues)


@pytest.mark.unit
@pytest.mark.parametrize("phrase", [
    "推进真相揭示",
    "推进剧情",
    "展开冲突",
    "承接上文",
    "advance the chapter spine",
    "push the story forward",
])
def test_generic_story_template_is_critical(phrase: str):
    card = _rich_card()
    card["purpose"] = {"story": phrase, "emotion": card["purpose"]["emotion"]}
    report = validate_scene_card_richness(**card)
    assert not report.is_rich_enough
    assert any(i.code == "story_purpose_generic_template" for i in report.issues)


@pytest.mark.unit
def test_generic_templates_are_matched_case_insensitive_with_punctuation():
    """'Advance the Chapter Spine.' should still be caught."""
    card = _rich_card()
    card["purpose"] = {"story": "Advance the Chapter Spine.", "emotion": "tension"}
    report = validate_scene_card_richness(**card)
    assert any(i.code == "story_purpose_generic_template" for i in report.issues)


# ---------------------------------------------------------------------------
# purpose.emotion checks (warning-level only)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_missing_emotion_purpose_is_warning_not_critical():
    card = _rich_card()
    card["purpose"] = {"story": card["purpose"]["story"]}
    report = validate_scene_card_richness(**card)
    # Still rich enough — only a warning
    assert report.is_rich_enough
    assert report.severity == "warning"
    assert any(
        i.code == "missing_emotion_purpose" and i.severity == "warning"
        for i in report.issues
    )


@pytest.mark.unit
def test_generic_emotion_template_is_warning():
    card = _rich_card()
    card["purpose"] = {"story": card["purpose"]["story"], "emotion": "提升紧张感"}
    report = validate_scene_card_richness(**card)
    assert report.is_rich_enough  # emotion generic = warning only
    assert any(
        i.code == "emotion_purpose_generic_template" and i.severity == "warning"
        for i in report.issues
    )


# ---------------------------------------------------------------------------
# entry/exit state checks
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_empty_exit_state_is_critical():
    card = _rich_card()
    card["exit_state"] = {}
    report = validate_scene_card_richness(**card)
    assert not report.is_rich_enough
    assert any(
        i.code == "exit_state_empty_or_generic" and i.severity == "critical"
        for i in report.issues
    )


@pytest.mark.unit
def test_empty_entry_state_is_warning():
    card = _rich_card()
    card["entry_state"] = {}
    report = validate_scene_card_richness(**card)
    # Entry-only empty = warning (some scenes are legitimately "world-entry" beats)
    assert report.is_rich_enough
    assert any(
        i.code == "entry_state_empty_or_generic" and i.severity == "warning"
        for i in report.issues
    )


@pytest.mark.unit
def test_no_state_delta_is_critical():
    """entry_state == exit_state means scene advances nothing."""
    card = _rich_card()
    same_state = {"location": "禁地外围", "林风": "藏匿中"}
    card["entry_state"] = same_state
    card["exit_state"] = same_state
    report = validate_scene_card_richness(**card)
    assert not report.is_rich_enough
    assert any(i.code == "no_state_delta" for i in report.issues)


@pytest.mark.unit
@pytest.mark.parametrize("generic", ["待定", "TBD", "unknown", "N/A", "状态不变"])
def test_generic_state_placeholder_is_flagged(generic: str):
    card = _rich_card()
    card["exit_state"] = {"status": generic}
    report = validate_scene_card_richness(**card)
    assert any(i.code == "exit_state_empty_or_generic" for i in report.issues)


# ---------------------------------------------------------------------------
# participants + scene_type checks
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_missing_scene_type_is_critical():
    card = _rich_card()
    card["scene_type"] = None
    report = validate_scene_card_richness(**card)
    assert not report.is_rich_enough
    assert any(i.code == "missing_scene_type" for i in report.issues)


@pytest.mark.unit
def test_confrontation_scene_needs_two_participants():
    card = _rich_card()
    card["scene_type"] = "confrontation"
    card["participants"] = ["林风"]
    report = validate_scene_card_richness(**card)
    assert not report.is_rich_enough
    assert any(i.code == "interactive_needs_two" for i in report.issues)


@pytest.mark.unit
def test_inner_monologue_allows_zero_participants():
    card = _rich_card()
    card["scene_type"] = "inner_monologue"
    card["participants"] = []
    report = validate_scene_card_richness(**card)
    # 0 participants on a non-interactive scene is a warning, not critical
    assert any(
        i.code == "no_participants" and i.severity == "warning"
        for i in report.issues
    )
    # Still rich_enough unless other critical issues exist
    assert report.is_rich_enough


@pytest.mark.unit
def test_dialogue_with_empty_participants_is_critical():
    card = _rich_card()
    card["scene_type"] = "dialogue"
    card["participants"] = []
    report = validate_scene_card_richness(**card)
    assert not report.is_rich_enough
    assert any(
        i.code == "no_participants" and i.severity == "critical"
        for i in report.issues
    )


# ---------------------------------------------------------------------------
# Integration-style scenarios
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_ch181_style_thin_card_is_rejected():
    """Reproduce the ch181 failure mode: generic purpose + empty states."""
    report = validate_scene_card_richness(
        scene_type="confrontation",
        purpose={"story": "推进真相揭示", "emotion": "提升紧张感"},
        entry_state={},
        exit_state={},
        participants=["林风"],
        language="zh-CN",
    )
    assert not report.is_rich_enough
    assert report.severity == "critical"
    codes = {i.code for i in report.issues}
    # All four fatal conditions should be caught
    assert "story_purpose_generic_template" in codes
    assert "exit_state_empty_or_generic" in codes
    assert "interactive_needs_two" in codes


@pytest.mark.unit
def test_english_scene_card_passes():
    report = validate_scene_card_richness(
        scene_type="confrontation",
        purpose={
            "story": "Lin breaches the buoy lockdown and seizes the core spirit stone",
            "emotion": "reluctant relief giving way to desperate resolve",
        },
        entry_state={"location": "outer buoy array", "lin": "wounded, hiding"},
        exit_state={"location": "inner array eye", "lin": "exposed, surrounded"},
        participants=["Lin Feng", "Warden Elder"],
        language="en",
    )
    assert report.is_rich_enough
    assert report.severity == "pass"


# ---------------------------------------------------------------------------
# Prompt block rendering
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_prompt_block_chinese_format():
    report = validate_scene_card_richness(
        scene_type="dialogue",
        purpose={"story": "推进剧情"},
        entry_state={},
        exit_state={},
        participants=[],
    )
    block = report.to_prompt_block(language="zh-CN")
    assert "场景卡片稠密度不足" in block
    assert "❗关键" in block or "⚠️提示" in block
    # Must mention at least one field path
    assert "purpose.story" in block or "exit_state" in block


@pytest.mark.unit
def test_prompt_block_english_format():
    report = validate_scene_card_richness(
        scene_type="dialogue",
        purpose={"story": "advance the plot"},
        entry_state={},
        exit_state={},
        participants=[],
        language="en",
    )
    block = report.to_prompt_block(language="en")
    assert "Scene card richness insufficient" in block
    assert "CRITICAL" in block or "WARN" in block


@pytest.mark.unit
def test_empty_report_produces_empty_prompt_block():
    report = validate_scene_card_richness(**_rich_card())
    assert report.to_prompt_block() == ""


# ---------------------------------------------------------------------------
# validate_scene_model wrapper
# ---------------------------------------------------------------------------

class _FakeScene:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


@pytest.mark.unit
def test_validate_scene_model_wrapper():
    scene = _FakeScene(
        scene_type="dialogue",
        purpose={"story": "林风与巡守长老在阵眼处对峙并交换情报", "emotion": "猜忌与试探"},
        entry_state={"location": "禁地阵眼"},
        exit_state={"location": "禁地阵眼", "info": "长老泄露关键线索"},
        participants=["林风", "巡守长老"],
    )
    report = validate_scene_model(scene, language="zh-CN")
    assert report.is_rich_enough


@pytest.mark.unit
def test_validate_scene_model_missing_attrs_handled():
    """Defensive: model-like object with missing attrs shouldn't crash."""
    scene = _FakeScene()
    report = validate_scene_model(scene)
    # Should produce many critical issues but not raise
    assert not report.is_rich_enough
    assert report.severity == "critical"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_validator_is_deterministic():
    """Same input → same report. Critical for CI reliability."""
    card = _rich_card()
    r1 = validate_scene_card_richness(**card)
    r2 = validate_scene_card_richness(**card)
    assert r1 == r2


@pytest.mark.unit
def test_generic_pattern_lists_are_non_empty():
    """Guard against accidentally emptying the blacklist."""
    assert len(GENERIC_STORY_PATTERNS) >= 5
    assert len(GENERIC_EMOTION_PATTERNS) >= 5
    assert len(GENERIC_STATE_PATTERNS) >= 5
