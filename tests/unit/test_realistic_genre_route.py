"""Realistic fiction must keep its own reader contract and review rubric."""

from bestseller.services.genre_review_profiles import resolve_genre_review_profile
from bestseller.services.genre_taxonomy import resolve_selection
from bestseller.services.hype_engine import hype_scheme_from_preset_overrides
from bestseller.services.diversity_budget import DiversityBudget
from bestseller.services.hype_engine import target_hype_for_chapter
from bestseller.services.invariants import LengthEnvelope, ProjectInvariants
from bestseller.services.prompt_constructor import build_chapter_hype_blocks
from uuid import uuid4
from bestseller.services.prompt_packs import infer_default_prompt_pack_key, resolve_prompt_pack
from bestseller.services.writing_presets import get_genre_preset


def test_realistic_taxonomy_does_not_route_to_power_reversal() -> None:
    selected = resolve_selection("general", "现实", "现实百态")
    assert selected.pack == "realistic-life"
    assert selected.category == "urban-contemporary"
    assert infer_default_prompt_pack_key("现实", "现实百态") == "realistic-life"


def test_explicit_urban_psionic_keeps_power_route() -> None:
    selected = resolve_selection("male", "都市", "都市异能")
    assert selected.pack == "urban-power-reversal"
    assert infer_default_prompt_pack_key("都市", "都市异能") == "urban-power-reversal"


def test_realistic_route_has_choice_and_consequence_contract() -> None:
    pack = resolve_prompt_pack("realistic-life", genre="现实", sub_genre="现实百态")
    assert pack is not None
    assert "选择" in (pack.fragments.conflict_stakes or "")
    assert "超能力" in "".join(pack.anti_patterns)
    profile = resolve_genre_review_profile("现实", "现实百态", "realistic-life")
    assert profile.category_key == "realistic-life"
    assert "choice_consequence_loop" in profile.plan_rubric.required_checks
    assert not profile.plan_rubric.require_power_system_tiers


def test_realistic_hype_scheme_does_not_require_two_opening_peaks() -> None:
    preset = get_genre_preset("realistic-life")
    assert preset is not None
    scheme = hype_scheme_from_preset_overrides(preset.writing_profile_overrides)
    assert scheme.golden_three_peak_count == 1
    assert all("打脸" not in "".join(recipe.narrative_beats) for recipe in scheme.recipe_deck)


def test_realistic_density_and_chapter_blocks_use_one_native_payoff() -> None:
    preset = get_genre_preset("realistic-life")
    assert preset is not None
    scheme = hype_scheme_from_preset_overrides(preset.writing_profile_overrides)
    invariants = ProjectInvariants(
        project_id=uuid4(),
        language="zh-CN",
        length_envelope=LengthEnvelope(min_chars=1800, target_chars=2200, max_chars=3500),
        hype_scheme=scheme,
    )
    band = target_hype_for_chapter(1, 30, pacing_profile="fast", hype_scheme=scheme)
    assert band.min_count_per_chapter == 1
    assert set(band.expected_types) <= {recipe.hype_type for recipe in scheme.recipe_deck}
    blocks = build_chapter_hype_blocks(
        invariants, DiversityBudget(uuid4()), chapter_no=1, total_chapters=30, pacing_profile="fast"
    )
    assert blocks.hype_constraints_block
    assert "黄金三章特别约束" not in blocks.hype_constraints_block
    assert "金手指" not in blocks.hype_constraints_block
