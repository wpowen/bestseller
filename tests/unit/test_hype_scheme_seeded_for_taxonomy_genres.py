"""Persist explicit payoff choices without turning taxonomy into consent.

An unselected effect scheme may be empty. Explicit curated decks still retain
their recipes through the invariant seeding and storage round-trip.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.services.hype_engine import hype_scheme_from_preset_overrides
from bestseller.services.invariants import invariants_from_dict, invariants_to_dict, seed_invariants
from bestseller.services.writing_presets import (
    get_genre_preset,
    infer_genre_preset,
    synthesize_genre_preset,
)


def _resolve_preset_overrides(
    genre: str, sub_genre: str, pack_key: str | None = None,
    *, selected_preset_key: str | None = None,
):
    """Build test inputs with author adoption distinct from genre labels."""

    if selected_preset_key:
        preset = get_genre_preset(selected_preset_key)
        assert preset is not None
        source = "explicit"
    else:
        preset = synthesize_genre_preset(
            str(pack_key or sub_genre or genre or "").strip() or "light-novel",
            genre=genre or None,
            sub_genre=sub_genre or None,
        )
        source = "synthesized"
    return dict(preset.writing_profile_overrides or {}), source


def _deck(overrides) -> list:
    return list((overrides.get("hype") or {}).get("recipe_deck") or [])


def test_curated_genre_still_uses_its_own_deck():
    """玄幻在 curated 表里，兜底绝不能顶掉它手工调过的配方。"""

    inferred = infer_genre_preset("东方玄幻", "东方玄幻")
    assert inferred is not None
    overrides, source = _resolve_preset_overrides(
        "东方玄幻", "东方玄幻", selected_preset_key=inferred.key
    )
    assert source == "explicit"
    assert len(_deck(overrides)) >= 10, "curated 配方被兜底覆盖了"


@pytest.mark.parametrize(
    "genre, sub_genre, pack_key",
    [
        ("搞笑沙雕", "搞笑沙雕", "light-novel"),
        ("搞笑沙雕", "搞笑沙雕", None),
        ("", "", None),  # Missing genre is also neutral.
    ],
)
def test_taxonomy_genre_leaves_unselected_deck_empty(genre, sub_genre, pack_key):
    """Neither a genre nor a technique pack opts an author into a deck."""

    overrides, source = _resolve_preset_overrides(genre, sub_genre, pack_key)
    assert source == "synthesized"
    assert _deck(overrides) == []
    assert hype_scheme_from_preset_overrides(overrides).is_empty


@pytest.mark.parametrize(
    "genre, sub_genre, selected_preset_key",
    [
        ("东方玄幻", "东方玄幻", "xianxia-upgrade"),
        ("搞笑沙雕", "搞笑沙雕", None),
        ("", "", None),
    ],
)
def test_hype_scheme_roundtrip_preserves_adopted_or_empty_deck(
    genre, sub_genre, selected_preset_key,
):
    """Storage must neither discard selected recipes nor invent missing ones."""

    overrides, _ = _resolve_preset_overrides(
        genre, sub_genre, selected_preset_key=selected_preset_key
    )
    scheme = hype_scheme_from_preset_overrides(overrides)
    inv = seed_invariants(
        project_id=uuid4(), language="zh-CN", words_per_chapter=None,
        overrides={"preset_overrides": overrides},
    )
    restored = invariants_from_dict(invariants_to_dict(inv))
    assert restored.hype_scheme == scheme
    if selected_preset_key:
        assert not scheme.is_empty
        assert len(scheme.recipe_deck) >= 10
    else:
        assert scheme.is_empty
        assert scheme.recipe_deck == ()


def test_infer_returns_none_for_taxonomy_genre():
    """Keep the uncovered taxonomy route represented without auto-adoption."""

    assert infer_genre_preset("搞笑沙雕", "搞笑沙雕") is None
