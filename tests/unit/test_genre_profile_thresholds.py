"""Unit tests for Phase A2 genre profile thresholds."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bestseller.services import genre_profile_thresholds as gpt
from bestseller.services.genre_profile_thresholds import (
    DEFAULT_FALLBACK_GENRE,
    GenreProfileThresholds,
    HookConfig,
    KNOWN_GENRE_IDS,
    MicropayoffConfig,
    PacingThresholds,
    OverrideConfig,
    _reset_cache,
    load_thresholds,
    parse_thresholds,
    resolve_thresholds,
)


@pytest.fixture(autouse=True)
def _reset_loader_cache() -> None:
    _reset_cache()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestParser:
    def test_minimal_input_uses_defaults(self) -> None:
        thresholds = parse_thresholds({"id": "minimal", "name": "Minimal"})
        assert thresholds.id == "minimal"
        assert thresholds.name == "Minimal"
        assert thresholds.hook_config == HookConfig()
        assert thresholds.pacing_config.strand_max_gap["overt"] == 5

    def test_partial_pacing_merges_with_defaults(self) -> None:
        raw = {
            "id": "x",
            "pacing_config": {
                "strand_max_gap": {"overt": 3},  # only override one key
            },
        }
        t = parse_thresholds(raw)
        gap = t.pacing_config.strand_max_gap
        assert gap["overt"] == 3
        assert gap["undercurrent"] == 10  # default preserved
        assert gap["hidden"] == 15
        assert gap["core_axis"] == 20

    def test_unknown_strand_key_ignored(self) -> None:
        raw = {
            "id": "x",
            "pacing_config": {
                "strand_max_gap": {"bogus_line": 99, "overt": 7},
            },
        }
        t = parse_thresholds(raw)
        assert t.pacing_config.strand_max_gap["overt"] == 7
        assert "bogus_line" not in t.pacing_config.strand_max_gap

    def test_invalid_strength_falls_back(self) -> None:
        t = parse_thresholds({"id": "x", "hook_config": {"strength_baseline": "EXTREME"}})
        assert t.hook_config.strength_baseline == "medium"

    def test_invalid_density_falls_back(self) -> None:
        t = parse_thresholds({"id": "x", "coolpoint_config": {"density_per_chapter": "INSANE"}})
        assert t.coolpoint_config.density_per_chapter == "medium"

    def test_override_parsed_full(self) -> None:
        raw = {
            "id": "x",
            "override_config": {
                "allowed_rationale_types": ["LOGIC_INTEGRITY", "ARC_TIMING"],
                "debt_multiplier": 1.5,
                "payback_window_default": 7,
                "interest_rate_per_chapter": 0.15,
            },
        }
        t = parse_thresholds(raw)
        assert t.override_config.allowed_rationale_types == ("LOGIC_INTEGRITY", "ARC_TIMING")
        assert t.override_config.debt_multiplier == 1.5
        assert t.override_config.payback_window_default == 7
        assert t.override_config.interest_rate_per_chapter == 0.15


# ---------------------------------------------------------------------------
# Round-trip via to_dict
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_to_dict_includes_all_sections(self) -> None:
        t = GenreProfileThresholds(id="t", name="T")
        d = t.to_dict()
        assert set(d.keys()) == {
            "id",
            "name",
            "hook_config",
            "coolpoint_config",
            "micropayoff_config",
            "pacing_config",
            "override_config",
        }

    def test_roundtrip_through_dict(self) -> None:
        original = GenreProfileThresholds(
            id="custom",
            name="Custom",
            pacing_config=PacingThresholds(
                stagnation_threshold=7,
                strand_max_gap={"overt": 3, "undercurrent": 6, "hidden": 10, "core_axis": 15},
                transition_max_consecutive=1,
            ),
            override_config=OverrideConfig(
                allowed_rationale_types=("ARC_TIMING",),
                debt_multiplier=2.0,
                payback_window_default=10,
                interest_rate_per_chapter=0.20,
            ),
        )
        restored = parse_thresholds(original.to_dict())
        assert restored.id == original.id
        assert restored.pacing_config.stagnation_threshold == 7
        assert restored.override_config.debt_multiplier == 2.0


# ---------------------------------------------------------------------------
# YAML file parsing — every known genre must parse
# ---------------------------------------------------------------------------


class TestYAMLFiles:
    @pytest.mark.parametrize("genre_id", list(KNOWN_GENRE_IDS))
    def test_each_yaml_parses(self, genre_id: str) -> None:
        t = load_thresholds(genre_id)
        assert t.id == genre_id
        assert t.name  # non-empty
        # Sanity: every known rationale ends up as a non-empty tuple of strings.
        assert len(t.override_config.allowed_rationale_types) >= 1
        for rt in t.override_config.allowed_rationale_types:
            assert isinstance(rt, str) and rt.isupper()

    @pytest.mark.parametrize("genre_id", list(KNOWN_GENRE_IDS))
    def test_pacing_has_all_four_lines(self, genre_id: str) -> None:
        t = load_thresholds(genre_id)
        keys = set(t.pacing_config.strand_max_gap.keys())
        assert keys == {"overt", "undercurrent", "hidden", "core_axis"}

    def test_action_progression_is_high_density(self) -> None:
        t = load_thresholds("action-progression")
        assert t.coolpoint_config.density_per_chapter == "high"
        assert t.hook_config.strength_baseline == "strong"

    def test_eastern_aesthetic_tolerates_quieter_pacing(self) -> None:
        action = load_thresholds("action-progression")
        eastern = load_thresholds("eastern-aesthetic")
        assert eastern.pacing_config.stagnation_threshold > action.pacing_config.stagnation_threshold
        assert eastern.override_config.debt_multiplier < action.override_config.debt_multiplier

    def test_suspense_mystery_primary_rationale_is_logic(self) -> None:
        t = load_thresholds("suspense-mystery")
        assert t.override_config.allowed_rationale_types[0] == "LOGIC_INTEGRITY"

    def test_relationship_driven_undercurrent_tighter_than_default(self) -> None:
        t = load_thresholds("relationship-driven")
        default = load_thresholds("action-progression")
        assert (
            t.pacing_config.strand_max_gap["undercurrent"]
            <= default.pacing_config.strand_max_gap["undercurrent"]
        )


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


class TestFallback:
    def test_unknown_genre_falls_back(self) -> None:
        t = resolve_thresholds("completely-made-up-genre")
        assert t.id == DEFAULT_FALLBACK_GENRE

    def test_none_input_falls_back(self) -> None:
        t = resolve_thresholds(None)
        assert t.id == DEFAULT_FALLBACK_GENRE

    def test_empty_string_falls_back(self) -> None:
        t = resolve_thresholds("   ")
        assert t.id == DEFAULT_FALLBACK_GENRE

    def test_fallback_genre_is_valid(self) -> None:
        assert DEFAULT_FALLBACK_GENRE in KNOWN_GENRE_IDS


# ---------------------------------------------------------------------------
# Loader cache (behavioral)
# ---------------------------------------------------------------------------


class TestLoaderCache:
    def test_same_id_returns_cached_instance(self) -> None:
        a = load_thresholds("action-progression")
        b = load_thresholds("action-progression")
        assert a is b  # lru_cache returns the same instance

    def test_reset_cache_clears(self) -> None:
        a = load_thresholds("action-progression")
        _reset_cache()
        b = load_thresholds("action-progression")
        assert a is not b


# ---------------------------------------------------------------------------
# genre_review_profiles re-export
# ---------------------------------------------------------------------------


def test_re_export_from_genre_review_profiles() -> None:
    from bestseller.services.genre_review_profiles import load_thresholds as rv_load

    t = rv_load("action-progression")
    assert t.id == "action-progression"
