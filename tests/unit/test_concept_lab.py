from __future__ import annotations

import re

import pytest

from bestseller.services.concept_lab import (
    build_concept_lab_catalog,
    concept_lab_listing_overrides,
    concept_lab_to_user_hints,
    render_concept_lab_material_brief_block,
    render_concept_lab_prompt_block,
    select_concept_lab_bundle,
)
from bestseller.services.concept_title_formulas import (
    clamp_title_length,
    load_title_cores,
    load_title_formulas,
    render_title,
)

pytestmark = pytest.mark.unit

_CJK_PATTERN = re.compile(r"[一-鿿]")


def _cjk_length(text: str) -> int:
    return len(_CJK_PATTERN.findall(text))


def test_build_concept_lab_catalog_combines_core_contracts() -> None:
    catalog = build_concept_lab_catalog("apocalypse-supply", count=3)

    assert catalog.genre_key == "apocalypse-supply"
    assert catalog.default_bundle_id
    assert len(catalog.bundles) == 3

    bundle = catalog.bundles[0]
    assert bundle.genre_key == "apocalypse-supply"
    assert bundle.creative_key
    assert bundle.hook_spec["one_liner"]
    assert bundle.reader_promise
    assert bundle.title_seeds
    assert bundle.listing_seeds
    assert "material_combination_trace" in bundle.methodology_targets
    assert "反常识爽点库" in bundle.source_mix
    assert bundle.material_brief.query_terms
    assert bundle.story_loop.per_chapter_contract


def test_build_concept_lab_catalog_can_return_full_batch() -> None:
    catalog = build_concept_lab_catalog("apocalypse-supply", count=12)

    assert len(catalog.bundles) == 12
    assert len({bundle.bundle_id for bundle in catalog.bundles}) == 12
    assert all(
        bundle.title_seeds and bundle.story_loop.per_chapter_contract
        for bundle in catalog.bundles
    )
    assert len({bundle.hook_spec["mechanism_key"] for bundle in catalog.bundles}) >= 6
    assert all(bundle.hook_spec.get("llm_design_brief") for bundle in catalog.bundles)
    assert not all(bundle.one_liner.startswith("主角想") for bundle in catalog.bundles)


def test_title_seeds_comply_with_golden_length() -> None:
    """All produced title seeds must fit 6-25 CJK characters."""

    catalog = build_concept_lab_catalog("apocalypse-supply", count=12)
    for bundle in catalog.bundles:
        for seed in bundle.title_seeds:
            length = _cjk_length(seed.text)
            assert 6 <= length <= 25, (
                f"Title {seed.text!r} length {length} out of golden range "
                f"(bundle={bundle.bundle_id})"
            )


def test_title_seeds_use_chinese_title_cores_for_legacy_eight() -> None:
    """The 8 legacy mechanisms each get their 4-8 char Chinese core."""

    cores = load_title_cores()
    for legacy in (
        "death_grows",
        "forced_loss",
        "emotion_value",
        "hide_anti_trope",
        "misunderstanding",
        "fourth_disaster",
        "rule_horror",
        "profession_reversal",
    ):
        assert legacy in cores
        assert cores[legacy]


def test_title_cores_cover_at_least_40_mechanisms() -> None:
    cores = load_title_cores()
    assert len(cores) >= 40


def test_clamp_title_length_truncates_overlong_titles() -> None:
    out = clamp_title_length("一" * 30, low=6, high=25)
    assert _cjk_length(out) == 25


def test_render_title_substitutes_slots() -> None:
    formulas = load_title_formulas()
    assert formulas
    contrarian = next(f for f in formulas if f.id == "contrarian_truth")
    out = render_title(
        contrarian,
        title_core="死线越近，底牌越真",
        genre_label="末日",
        reward="资源",
        cost="代价",
        direction_title="情绪轴",
        hook_type="悬疑",
        n=7,
    )
    assert "死线越近，底牌越真" in out


def test_seed_shuffles_bundle_order() -> None:
    """Different seeds should produce different first-page bundle sets."""
    default = build_concept_lab_catalog("apocalypse-supply", count=4, seed=None)
    seeded1 = build_concept_lab_catalog("apocalypse-supply", count=4, seed=11)
    seeded2 = build_concept_lab_catalog("apocalypse-supply", count=4, seed=12)
    ids_default = [b.bundle_id for b in default.bundles]
    ids_1 = [b.bundle_id for b in seeded1.bundles]
    ids_2 = [b.bundle_id for b in seeded2.bundles]
    # At least one of the seeded variants should differ from default.
    assert ids_default != ids_1 or ids_default != ids_2 or ids_1 != ids_2
    assert set(ids_1) != set(ids_2)
    # And the same seed is deterministic.
    again = build_concept_lab_catalog("apocalypse-supply", count=4, seed=11)
    assert [b.bundle_id for b in again.bundles] == ids_1


def test_select_concept_lab_bundle_accepts_valid_ui_payload() -> None:
    bundle = build_concept_lab_catalog("apocalypse-supply", count=1).bundles[0]

    selected = select_concept_lab_bundle(
        genre_key="apocalypse-supply",
        bundle_payload=bundle.model_dump(mode="json"),
    )

    assert selected == bundle


def test_concept_lab_user_hints_include_story_loop_and_material_brief() -> None:
    bundle = build_concept_lab_catalog("apocalypse-supply", count=1).bundles[0]

    hints = concept_lab_to_user_hints(bundle)

    assert hints["concept_lab"]["bundle_id"] == bundle.bundle_id
    assert hints["reader_promise"] == bundle.reader_promise
    assert hints["material_brief"]["query_terms"]
    assert hints["story_loop"]["per_chapter_contract"]


def test_concept_lab_prompt_blocks_render_selected_contract() -> None:
    bundle = build_concept_lab_catalog("apocalypse-supply", count=1).bundles[0]
    source = {"concept_lab": bundle.model_dump(mode="json")}

    prompt_block = render_concept_lab_prompt_block(source)
    material_block = render_concept_lab_material_brief_block(source)

    assert "已选脑洞组合合同" in prompt_block
    assert bundle.reader_promise in prompt_block
    assert "hook_design" in prompt_block
    assert "llm_design_brief" in prompt_block
    assert "per_chapter_contract" in prompt_block
    assert "已选脑洞物料合同" in material_block
    assert bundle.material_brief.query_terms[0] in material_block


def test_concept_lab_listing_overrides_seed_market_profile() -> None:
    bundle = build_concept_lab_catalog("apocalypse-supply", count=1).bundles[0]

    overrides = concept_lab_listing_overrides({"concept_lab": bundle.model_dump(mode="json")})

    assert overrides["logline"] == bundle.one_liner
    assert overrides["short_intro"]
    assert bundle.reader_promise in overrides["promo_copy"]
    assert overrides["title_candidates"][0]["title"] == bundle.title_seeds[0].text
