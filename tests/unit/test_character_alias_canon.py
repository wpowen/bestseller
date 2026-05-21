"""Unit tests for character_alias_canon."""

from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.character_alias_canon import (
    CharacterCanon,
    CharacterCanonEntry,
    build_name_canon_repair_prompt,
    load_character_canon,
    validate_chapter_name_canon,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_load_missing_file_returns_empty_canon(tmp_path: Path) -> None:
    canon = load_character_canon(tmp_path / "missing.yaml")
    assert canon.entries == ()
    assert canon.spelling_to_canonical == {}


def test_load_canon_indexes_aliases_and_forbidden(tmp_path: Path) -> None:
    yaml_path = tmp_path / "aliases.yaml"
    yaml_path.write_text(
        """
characters:
  - canonical: 周元青
    aliases: [周元青, 周公子]
    forbidden_collisions: [周元]
  - canonical: 周元
    aliases: [周元, 周师兄]
    forbidden_collisions: [周元青]
""",
        encoding="utf-8",
    )
    canon = load_character_canon(yaml_path)
    assert canon.canonical_of("周元青") == "周元青"
    assert canon.canonical_of("周公子") == "周元青"
    assert canon.canonical_of("周师兄") == "周元"
    assert canon.is_known("周元")
    # Forbidden pairs surfaced
    assert ("周元青", "周元") in canon.forbidden_pairs
    assert ("周元", "周元青") in canon.forbidden_pairs


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _canon_from_entries(*entries: CharacterCanonEntry) -> CharacterCanon:
    from bestseller.services.character_alias_canon import _build_index
    index, forbidden = _build_index(entries)
    return CharacterCanon(entries=entries, spelling_to_canonical=index, forbidden_pairs=forbidden)


def test_empty_canon_short_circuits() -> None:
    out = validate_chapter_name_canon("周元 走进 周元青 的院子", CharacterCanon.empty())
    assert out == []


def test_forbidden_collision_is_flagged() -> None:
    canon = _canon_from_entries(
        CharacterCanonEntry(
            canonical="周元青",
            aliases=("周元青", "周公子"),
            forbidden_collisions=("周元",),
        ),
    )
    text = (
        "周元青 抬头看着 宁尘。\n"
        "周元 在远处冷笑。\n"
        "周元 拔出剑。\n"
        "周元 又走了几步。\n"
    )
    violations = validate_chapter_name_canon(text, canon)
    kinds = {v.kind for v in violations}
    assert "forbidden_collision" in kinds
    weighted = [v for v in violations if v.kind == "forbidden_collision"]
    assert weighted[0].spelling == "周元"


def test_unknown_name_with_high_occurrence_is_flagged() -> None:
    canon = _canon_from_entries(
        CharacterCanonEntry(canonical="宁尘", aliases=("宁尘",)),
    )
    text = (
        "宁尘 在演武场。\n"
        "韩九 走过来，韩九 冷笑。\n"
        "韩九 又出拳。\n"
        "韩九 倒下了。\n"
    )
    violations = validate_chapter_name_canon(text, canon)
    unknowns = [v for v in violations if v.kind == "unknown_name"]
    assert any(v.spelling == "韩九" for v in unknowns)


def test_canonical_and_alias_spellings_do_not_flag() -> None:
    canon = _canon_from_entries(
        CharacterCanonEntry(
            canonical="周元青",
            aliases=("周元青", "周公子"),
        ),
    )
    text = (
        "周元青 走进院子。\n"
        "周元青 看着 宁尘。\n"
        "周公子 抚摸玉佩。\n"
        "周公子 又一次冷笑。\n"
        "周公子 抬手。\n"
    )
    violations = validate_chapter_name_canon(text, canon)
    # Both 周元青 and 周公子 are registered; should not trigger
    flagged_spellings = {v.spelling for v in violations}
    assert "周元青" not in flagged_spellings
    assert "周公子" not in flagged_spellings


def test_single_occurrence_below_alarm_threshold_is_ignored() -> None:
    canon = _canon_from_entries(
        CharacterCanonEntry(canonical="宁尘", aliases=("宁尘",)),
    )
    text = "宁尘 看见 韩九 一眼便走了。"
    violations = validate_chapter_name_canon(text, canon, min_occurrences_for_alarm=3)
    # 韩九 appears only once; should not be flagged
    assert not any(v.spelling == "韩九" for v in violations)


def test_build_repair_prompt_lists_each_violation() -> None:
    canon = _canon_from_entries(
        CharacterCanonEntry(
            canonical="周元青",
            aliases=("周元青",),
            forbidden_collisions=("周元",),
        ),
    )
    text = "\n".join(["周元 在场。"] * 4)
    violations = validate_chapter_name_canon(text, canon)
    prompt = build_name_canon_repair_prompt(violations)
    assert "人名 Canon 违规修复" in prompt
    assert "周元" in prompt
