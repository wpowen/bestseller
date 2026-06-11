from __future__ import annotations

import pytest

from bestseller.services.acceptance_contract import (
    ENDING_HOOK_ANCHOR_TERMS,
    render_scene_acceptance_block,
    resolve_scene_duty,
)
from bestseller.services.deterministic_post_write_audit import _HOOK_TERMS

pytestmark = pytest.mark.unit


def test_audit_terms_are_single_sourced_from_contract() -> None:
    assert tuple(_HOOK_TERMS) == ENDING_HOOK_ANCHOR_TERMS


def test_first_scene_gets_opening_echo_duty_with_verbatim_tokens() -> None:
    block = render_scene_acceptance_block(
        scene_number=1,
        total_scenes=3,
        chapter_number=2,
        prev_hook_tokens=["镜片", "林渊", "门外", "罗盘", "尸体", "多余"],
    )
    assert "开篇呼应义务" in block
    for token in ("镜片", "林渊", "门外", "罗盘", "尸体"):
        assert token in block
    assert "多余" not in block  # capped at the 5-token pool
    assert "章末钩子" not in block


def test_last_scene_gets_ending_hook_duty_quoting_anchor_terms() -> None:
    block = render_scene_acceptance_block(
        scene_number=3,
        total_scenes=3,
        chapter_number=5,
    )
    assert "章末钩子义务" in block
    assert "突然" in block  # anchor terms quoted from the audit's list
    assert "开篇呼应" not in block


def test_middle_scene_gets_only_payoff_clause() -> None:
    block = render_scene_acceptance_block(
        scene_number=2,
        total_scenes=3,
        chapter_number=5,
        prev_hook_tokens=["镜片"],
    )
    assert "兑现义务" in block
    assert "开篇呼应" not in block
    assert "章末钩子" not in block


def test_middle_scene_without_payoff_clause_is_empty() -> None:
    assert (
        render_scene_acceptance_block(
            scene_number=2,
            total_scenes=3,
            chapter_number=5,
            prev_hook_tokens=["镜片"],
            include_payoff_clause=False,
        )
        == ""
    )


def test_single_scene_chapter_carries_both_duties() -> None:
    block = render_scene_acceptance_block(
        scene_number=1,
        total_scenes=1,
        chapter_number=2,
        prev_hook_tokens=["镜片", "林渊", "门外"],
    )
    assert "开篇呼应义务" in block
    assert "章末钩子义务" in block


def test_chapter_one_has_no_echo_duty() -> None:
    block = render_scene_acceptance_block(
        scene_number=1,
        total_scenes=1,
        chapter_number=1,
        prev_hook_tokens=["leak"],
    )
    assert "开篇呼应" not in block
    assert "章末钩子义务" in block


def test_unknown_total_scenes_never_guesses_last_scene() -> None:
    duty = resolve_scene_duty(scene_number=2, total_scenes=None)
    assert duty.is_last_scene is False
    assert duty.is_first_scene is False


def test_english_rendering() -> None:
    block = render_scene_acceptance_block(
        scene_number=2,
        total_scenes=2,
        chapter_number=3,
        prev_hook_tokens=None,
        language="en-US",
    )
    assert "ENDING HOOK DUTY" in block
