from __future__ import annotations

import pytest

from bestseller.services.golden_rules import render_golden_three_rules

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("chapter_number", [1, 2, 3])
@pytest.mark.parametrize(
    ("language", "path_mode", "marker"),
    [
        ("zh-CN", "chapter_first", "黄金三章"),
        ("zh-CN", "scene", "开篇硬指标"),
        ("en", "chapter_first", "GOLDEN THREE"),
        ("en", "scene", "OPENING METRICS"),
    ],
)
def test_golden_three_rules_apply_only_to_chapters_one_through_three(
    chapter_number: int,
    language: str,
    path_mode: str,
    marker: str,
) -> None:
    assert marker in render_golden_three_rules(
        chapter_number,
        language,
        path_mode=path_mode,
    )


@pytest.mark.parametrize("chapter_number", [4, 10])
@pytest.mark.parametrize(
    ("language", "marker"),
    [("zh-CN", "前十章留存硬规则"), ("en", "FRONT-TEN RETENTION RULES")],
)
def test_front_ten_rules_apply_only_to_chapters_four_through_ten(
    chapter_number: int,
    language: str,
    marker: str,
) -> None:
    assert marker in render_golden_three_rules(chapter_number, language)


@pytest.mark.parametrize("chapter_number", [0, 11, 50])
@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_special_opening_rules_are_empty_outside_supported_ranges(
    chapter_number: int,
    language: str,
) -> None:
    assert render_golden_three_rules(chapter_number, language) == ""
