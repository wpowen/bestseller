"""Unit tests for reader-visible adjacent chapter logic."""

from __future__ import annotations

import pytest

from bestseller.services.reader_logic_gate import evaluate_reader_logic_seam

pytestmark = pytest.mark.unit


def test_flags_bare_room_entry_negation_after_prior_entry() -> None:
    prev = "王建业拿钥匙开门。林渊走进卧室，站在穿衣镜前。"
    current = "林渊没有推开303。\n\n门内那个像父亲的声音还在敲。"

    report = evaluate_reader_logic_seam(prev, current, prev_chapter=1, current_chapter=2)

    assert not report.passed
    assert {f.code for f in report.findings} == {"ambiguous_room_entry_negation"}


def test_qualified_mirror_door_negation_is_allowed() -> None:
    prev = "王建业拿钥匙开门。林渊走进卧室，站在穿衣镜前。"
    current = "林渊没有去开那道冒充父亲声音的镜门。\n\n铜钱滚到302门槛下。"

    report = evaluate_reader_logic_seam(prev, current, prev_chapter=1, current_chapter=2)

    assert report.passed


def test_flags_room_jump_without_visible_bridge() -> None:
    prev = "他冲过去，一脚踹开门。302的门板往里凹陷。门槛上的血水还在往外涌。"
    current = "林渊蹲在303室门口，罗盘平放在地砖上。"

    report = evaluate_reader_logic_seam(prev, current, prev_chapter=2, current_chapter=3)

    assert not report.passed
    assert {f.code for f in report.findings} == {"room_jump_without_reader_bridge"}


def test_room_jump_with_visible_bridge_is_allowed() -> None:
    prev = "他冲过去，一脚踹开门。302的门板往里凹陷。门槛上的血水还在往外涌。"
    current = "那声音不是从302里出来。罗盘指针拖着他退回303室门口。林渊蹲下身。"

    report = evaluate_reader_logic_seam(prev, current, prev_chapter=2, current_chapter=3)

    assert report.passed
