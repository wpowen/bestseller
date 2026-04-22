"""Preset hype-recipe-deck contract tests (Phase 3).

These tests enforce the plan's Phase 3 invariant:

  * Hot presets (apocalypse-supply / xianxia-upgrade / palace-revenge /
    horror-tycoon) must declare >= 10 hype recipes.
  * Every preset with a declared ``writing_profile_overrides.hype.recipe_deck``
    must deserialize cleanly through ``hype_scheme_from_preset_overrides``
    (keys unique, hype_type in the canonical enum, required fields present).
  * The four canonical 爽点 categories (face_slap / power_reveal /
    counterattack / status_jump) must all appear in hot decks so the
    rotation engine has at least one legal pick per tension band.

Plan reference: `/Users/owen/.claude/plans/twinkly-rolling-pnueli.md`
§Phase 3 "15+ preset 补完 deck; 热门 4 个各 10+ 条".
"""

from __future__ import annotations

import pytest

from bestseller.services import writing_presets as preset_services
from bestseller.services.hype_engine import (
    HypeRecipe,
    HypeScheme,
    HypeType,
    hype_scheme_from_preset_overrides,
)


pytestmark = pytest.mark.unit


# Hot presets — per plan, each must carry >= 10 hand-authored recipes.
_HOT_PRESET_KEYS: frozenset[str] = frozenset(
    {
        "apocalypse-supply",
        "xianxia-upgrade",
        "palace-revenge",
        "horror-tycoon",
    }
)

# Canonical 爽点 categories that every hot deck must cover. A deck that
# misses one of these would leave a tension band uncovered when the
# rotation engine is asked for a recipe of that type.
_HOT_REQUIRED_TYPES: frozenset[HypeType] = frozenset(
    {
        HypeType.FACE_SLAP,
        HypeType.POWER_REVEAL,
        HypeType.COUNTERATTACK,
        HypeType.STATUS_JUMP,
    }
)

# Generic-fallback deck size target (kept centralised — if we ever grow
# the fallback to 6 recipes, only this constant changes).
_FALLBACK_DECK_SIZE: int = 5


def _presets_with_hype() -> list[tuple[str, dict]]:
    """Return ``(preset_key, hype_overrides)`` pairs for every preset that
    declares a hype block."""

    out: list[tuple[str, dict]] = []
    for preset in preset_services.list_genre_presets():
        overrides = preset.writing_profile_overrides or {}
        hype = overrides.get("hype")
        if hype:
            out.append((preset.key, overrides))
    return out


# ---------------------------------------------------------------------------
# Catalog-level guarantees
# ---------------------------------------------------------------------------


class TestCatalogContract:
    def test_at_least_fifteen_presets_carry_hype_decks(self) -> None:
        presets = _presets_with_hype()

        # Plan calls for "15+ preset 补完 deck". Keep the assertion
        # strict so a regression (preset accidentally dropping its
        # ``hype`` key) fails loudly.
        assert len(presets) >= 15, (
            f"expected >= 15 presets with hype decks, got {len(presets)}: "
            f"{[k for k, _ in presets]}"
        )

    def test_every_hot_preset_is_present_in_catalog(self) -> None:
        preset_keys = {p.key for p in preset_services.list_genre_presets()}
        missing = _HOT_PRESET_KEYS - preset_keys
        assert not missing, f"hot presets missing from catalog: {missing}"

    def test_every_hot_preset_declares_a_hype_deck(self) -> None:
        with_hype = {key for key, _ in _presets_with_hype()}
        missing_deck = _HOT_PRESET_KEYS - with_hype
        assert not missing_deck, (
            f"hot presets without hype decks: {missing_deck}"
        )


# ---------------------------------------------------------------------------
# Per-preset contract tests (parametrised so each failure names a preset)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "preset_key,overrides",
    _presets_with_hype(),
    ids=lambda v: v if isinstance(v, str) else "",
)
class TestDeckContract:
    def test_deck_deserializes_to_non_empty_scheme(
        self, preset_key: str, overrides: dict
    ) -> None:
        scheme = hype_scheme_from_preset_overrides(overrides)

        assert isinstance(scheme, HypeScheme)
        assert scheme.recipe_deck, (
            f"{preset_key}: hype.recipe_deck deserialized to empty tuple — "
            "every recipe likely failed _recipe_from_dict()"
        )

    def test_deck_recipe_keys_are_unique_within_preset(
        self, preset_key: str, overrides: dict
    ) -> None:
        scheme = hype_scheme_from_preset_overrides(overrides)
        keys = [r.key for r in scheme.recipe_deck]

        duplicates = [k for k in keys if keys.count(k) > 1]
        assert not duplicates, (
            f"{preset_key}: duplicate recipe keys {set(duplicates)} "
            "(LRU rotation requires globally unique keys)"
        )

    def test_every_recipe_carries_trigger_keywords(
        self, preset_key: str, overrides: dict
    ) -> None:
        scheme = hype_scheme_from_preset_overrides(overrides)

        for recipe in scheme.recipe_deck:
            assert recipe.trigger_keywords, (
                f"{preset_key}: recipe '{recipe.key}' has no "
                "trigger_keywords — HypeOccurrenceCheck cannot detect it"
            )

    def test_every_recipe_carries_narrative_beats(
        self, preset_key: str, overrides: dict
    ) -> None:
        scheme = hype_scheme_from_preset_overrides(overrides)

        for recipe in scheme.recipe_deck:
            assert recipe.narrative_beats, (
                f"{preset_key}: recipe '{recipe.key}' has no narrative_beats "
                "— prompt_constructor cannot emit a concrete beat list"
            )

    def test_every_recipe_hype_type_is_canonical(
        self, preset_key: str, overrides: dict
    ) -> None:
        scheme = hype_scheme_from_preset_overrides(overrides)

        for recipe in scheme.recipe_deck:
            # HypeType(...) would have raised during
            # hype_scheme_from_preset_overrides — this assertion is the
            # belt-and-suspenders check that survives enum edits.
            assert isinstance(recipe.hype_type, HypeType)

    def test_intensity_floor_in_sane_range(
        self, preset_key: str, overrides: dict
    ) -> None:
        scheme = hype_scheme_from_preset_overrides(overrides)

        for recipe in scheme.recipe_deck:
            assert 0.0 <= recipe.intensity_floor <= 10.0, (
                f"{preset_key}: recipe '{recipe.key}' has intensity_floor "
                f"{recipe.intensity_floor} outside [0, 10]"
            )


# ---------------------------------------------------------------------------
# Hot-deck-specific guarantees
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("preset_key", sorted(_HOT_PRESET_KEYS))
class TestHotDeck:
    def _scheme_for(self, preset_key: str) -> HypeScheme:
        preset = preset_services.get_genre_preset(preset_key)
        assert preset is not None, f"missing hot preset {preset_key}"
        return hype_scheme_from_preset_overrides(
            preset.writing_profile_overrides
        )

    def test_hot_deck_has_at_least_ten_recipes(self, preset_key: str) -> None:
        scheme = self._scheme_for(preset_key)
        assert len(scheme.recipe_deck) >= 10, (
            f"{preset_key}: hot preset must carry >= 10 recipes per plan "
            f"(got {len(scheme.recipe_deck)})"
        )

    def test_hot_deck_covers_four_canonical_hype_types(
        self, preset_key: str
    ) -> None:
        scheme = self._scheme_for(preset_key)
        types_present = {r.hype_type for r in scheme.recipe_deck}

        missing = _HOT_REQUIRED_TYPES - types_present
        assert not missing, (
            f"{preset_key}: hot deck missing canonical types "
            f"{[t.value for t in missing]} — rotation engine would run out "
            "of legal picks for tension bands that demand these types"
        )


# ---------------------------------------------------------------------------
# Generic-fallback sanity — non-hot presets use the 5-recipe shared deck.
# ---------------------------------------------------------------------------


def test_generic_fallback_deck_shape() -> None:
    """The shared fallback is what non-hot presets embed; its shape
    governs the minimum floor."""

    from bestseller.services.writing_presets import (
        _GENERIC_FALLBACK_HYPE_DECK,
    )

    assert len(_GENERIC_FALLBACK_HYPE_DECK) == _FALLBACK_DECK_SIZE

    keys = {entry["key"] for entry in _GENERIC_FALLBACK_HYPE_DECK}
    assert len(keys) == _FALLBACK_DECK_SIZE, "fallback keys must be unique"

    types = {entry["hype_type"] for entry in _GENERIC_FALLBACK_HYPE_DECK}
    required = {
        HypeType.FACE_SLAP.value,
        HypeType.POWER_REVEAL.value,
        HypeType.COUNTERATTACK.value,
        HypeType.UNDERDOG_WIN.value,
        HypeType.STATUS_JUMP.value,
    }
    assert types == required, (
        f"fallback deck types drifted from plan: {types} vs {required}"
    )


def test_non_hot_preset_deck_size_matches_fallback() -> None:
    """Presets that wire in the shared fallback should all end up with
    the same deck size. If a preset diverges, either it earned a bespoke
    deck (move it to the hot list) or something is wrong."""

    non_hot_sizes: dict[str, int] = {}
    for preset_key, overrides in _presets_with_hype():
        if preset_key in _HOT_PRESET_KEYS:
            continue
        scheme = hype_scheme_from_preset_overrides(overrides)
        non_hot_sizes[preset_key] = len(scheme.recipe_deck)

    # Flag any non-hot preset that doesn't match the fallback — that's a
    # "should be promoted to hot" signal.
    non_matching = {
        k: v for k, v in non_hot_sizes.items() if v != _FALLBACK_DECK_SIZE
    }
    assert not non_matching, (
        f"non-hot presets with non-fallback deck sizes: {non_matching} — "
        "either move them to _HOT_PRESET_KEYS or use _GENERIC_FALLBACK_HYPE_DECK"
    )


# ---------------------------------------------------------------------------
# Round-trip hardening
# ---------------------------------------------------------------------------


def test_every_preset_deck_recipe_is_a_frozen_hype_recipe() -> None:
    """Immutability safety net — ``HypeRecipe`` is frozen, so mutation
    via scheme.recipe_deck[0].key = ... should raise."""

    for preset_key, overrides in _presets_with_hype():
        scheme = hype_scheme_from_preset_overrides(overrides)
        assert scheme.recipe_deck, f"{preset_key}: empty deck"
        for recipe in scheme.recipe_deck:
            assert isinstance(recipe, HypeRecipe)
            with pytest.raises(Exception):
                recipe.key = "mutated"  # type: ignore[misc]
