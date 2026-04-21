"""Tests for the fallback-volume-title cycle composer (B8).

Locks in the contract that eliminates the "·二" / "·III" sequel-tag
suffix from xianxia (and every other) volume-title fallback path.

Before the fix:
  * A 24-volume novel with a single dominant phase would see
    ``_resolve_fallback_volume_title`` emit titles like
    ``绝境求生`` → ``绝境求生·二`` → ``绝境求生·三`` …
After the fix:
  * Pools are ≥ 12 entries so 24-volume books usually avoid cycling.
  * When cycles do occur, ``_compose_cycle_title`` mixes a neutral
    prefix (重帷, 余烬) or suffix (重演, 新篇) with the base — never an
    ordinal.
"""

from __future__ import annotations

import pytest

from bestseller.services.planner import (
    _PHASE_TITLE_VARIATIONS_EN,
    _PHASE_TITLE_VARIATIONS_ZH,
    _compose_cycle_title,
    _resolve_fallback_volume_title,
)


# ---------------------------------------------------------------------------
# Pool expansion
# ---------------------------------------------------------------------------

def test_phase_pools_zh_have_at_least_twelve_entries() -> None:
    """Each phase must expose enough titles that a 12-volume book with a
    single dominant phase never cycles past the pool."""

    for phase, pool in _PHASE_TITLE_VARIATIONS_ZH.items():
        assert len(pool) >= 12, (
            f"Chinese phase pool '{phase}' only has {len(pool)} entries; "
            "need ≥ 12 to avoid ordinal-suffix fallback in typical books."
        )


def test_phase_pools_en_have_at_least_twelve_entries() -> None:
    for phase, pool in _PHASE_TITLE_VARIATIONS_EN.items():
        assert len(pool) >= 12, (
            f"English phase pool '{phase}' only has {len(pool)} entries; "
            "need ≥ 12."
        )


def test_phase_pools_contain_no_ordinal_suffixes() -> None:
    """Canonical pool entries must not themselves contain sequel-tag
    suffixes."""

    forbidden = ("·二", "·三", "· II", "· III", "·第", " · II", " · III")
    for pool in _PHASE_TITLE_VARIATIONS_ZH.values():
        for entry in pool:
            for tag in forbidden:
                assert tag not in entry, (
                    f"Chinese pool entry '{entry}' contains forbidden "
                    f"sequel tag '{tag}'."
                )
    for pool in _PHASE_TITLE_VARIATIONS_EN.values():
        for entry in pool:
            for tag in forbidden:
                assert tag not in entry, (
                    f"English pool entry '{entry}' contains forbidden "
                    f"sequel tag '{tag}'."
                )


# ---------------------------------------------------------------------------
# Compose cycle
# ---------------------------------------------------------------------------

def test_compose_cycle_zero_returns_base_unchanged() -> None:
    assert _compose_cycle_title("血路初开", 0, is_en=False) == "血路初开"
    assert _compose_cycle_title("Bare Survival", 0, is_en=True) == "Bare Survival"


def test_compose_cycle_never_emits_ordinal_suffix_zh() -> None:
    for cycle in range(1, 25):
        out = _compose_cycle_title("血路初开", cycle, is_en=False)
        assert "·二" not in out
        assert "·三" not in out
        assert "·四" not in out
        assert "·第" not in out


def test_compose_cycle_never_emits_ordinal_suffix_en() -> None:
    for cycle in range(1, 25):
        out = _compose_cycle_title("Bare Survival", cycle, is_en=True)
        assert " · II" not in out
        assert " · III" not in out
        assert " · IV" not in out
        assert "Volume " not in out


def test_compose_cycle_is_deterministic() -> None:
    """Same (base, cycle) always produces the same composed title so
    volume plans regenerate stably."""

    a = _compose_cycle_title("绝境求生", 3, is_en=False)
    b = _compose_cycle_title("绝境求生", 3, is_en=False)
    assert a == b

    a_en = _compose_cycle_title("Bare Survival", 3, is_en=True)
    b_en = _compose_cycle_title("Bare Survival", 3, is_en=True)
    assert a_en == b_en


def test_compose_cycle_alternates_prefix_suffix() -> None:
    """Odd cycles prepend a prefix; even cycles append a suffix — the
    two styles alternate so adjacent cycles feel distinct."""

    base_zh = "血路初开"
    cyc1 = _compose_cycle_title(base_zh, 1, is_en=False)
    cyc2 = _compose_cycle_title(base_zh, 2, is_en=False)
    assert cyc1.endswith(base_zh)  # prefix form
    assert cyc2.startswith(base_zh)  # suffix form

    base_en = "Bare Survival"
    cyc1_en = _compose_cycle_title(base_en, 1, is_en=True)
    cyc2_en = _compose_cycle_title(base_en, 2, is_en=True)
    assert cyc1_en.endswith(base_en)
    assert cyc2_en.startswith(base_en)


# ---------------------------------------------------------------------------
# Resolve fallback volume title end-to-end
# ---------------------------------------------------------------------------

def test_twenty_four_volume_xianxia_produces_no_ordinal_suffixes() -> None:
    """The direct root-cause test for the user's complaint: 24-volume
    xianxia titles must never emit ·二 / ·III tags even when a single
    phase dominates."""

    for occurrence in range(24):
        title = _resolve_fallback_volume_title(
            "individual_survival", occurrence, occurrence + 1, is_en=False
        )
        assert "·二" not in title
        assert "·三" not in title
        assert "·第" not in title
        assert title  # non-empty


def test_twenty_four_volume_english_produces_no_ordinal_suffixes() -> None:
    for occurrence in range(24):
        title = _resolve_fallback_volume_title(
            "survival", occurrence, occurrence + 1, is_en=True
        )
        assert " · II" not in title
        assert " · III" not in title
        assert "Volume " not in title
        assert title


def test_twenty_four_volume_xianxia_titles_are_unique() -> None:
    """With 12-entry pools + cycle compose, 24 fallback titles must all
    be distinct so the volume plan never ships two volumes with the
    same title."""

    titles = {
        _resolve_fallback_volume_title(
            "individual_survival", occurrence, occurrence + 1, is_en=False
        )
        for occurrence in range(24)
    }
    assert len(titles) == 24


def test_legacy_survival_phase_produces_no_suffixes() -> None:
    """The legacy ``survival`` phase key also triggers fallback in
    older books — verify it too."""

    for occurrence in range(20):
        title = _resolve_fallback_volume_title(
            "survival", occurrence, occurrence + 1, is_en=False
        )
        assert "·二" not in title
        assert "·三" not in title


def test_all_phase_keys_resist_ordinal_suffix_cycle() -> None:
    """Every phase pool — not just survival — must produce clean titles
    through at least 18 repetitions."""

    for phase in _PHASE_TITLE_VARIATIONS_ZH:
        for occurrence in range(18):
            title = _resolve_fallback_volume_title(
                phase, occurrence, occurrence + 1, is_en=False
            )
            assert "·二" not in title
            assert "·三" not in title
            assert "·第" not in title


def test_unknown_phase_falls_back_to_volume_number() -> None:
    """An unknown phase key still falls through to '第N卷' — this is
    allowed (the milestone naming system normally provides a real
    title). It must NOT emit a '·二' suffix."""

    title = _resolve_fallback_volume_title(
        "nonexistent_phase", 5, 6, is_en=False
    )
    assert "·二" not in title
    assert title  # non-empty
