"""Unit tests for Phase 1 prompt_constructor + hype_engine integration.

Scope:
  * ``build_reader_contract_section`` — renders on first 10 chapters, then
    every 5th; empty scheme → empty string.
  * ``build_hype_constraints`` — includes assigned type + recipe beats +
    golden-three note when applicable + hype/cliffhanger separation.
  * ``build_chapter_prompt`` wiring — populates the new PromptPlan fields
    and renders them in the documented order between bible_slice and
    diversity_constraints.
  * Empty ``HypeScheme`` → hype_constraints / reader_contract are both
    empty, prompt renders without changes.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.services.diversity_budget import DiversityBudget
from bestseller.services.hype_engine import (
    HYPE_DENSITY_CURVE,
    GoldenFingerLadder,
    GoldenFingerRung,
    HypeRecipe,
    HypeScheme,
    HypeType,
    target_hype_for_chapter,
)
from bestseller.services.invariants import (
    LengthEnvelope,
    ProjectInvariants,
)
from bestseller.services.prompt_constructor import (
    EMPTY_HYPE_BLOCKS,
    build_chapter_hype_blocks,
    build_chapter_prompt,
    build_hype_constraints,
    build_reader_contract_section,
)


pytestmark = pytest.mark.unit


def _sample_recipe(
    key: str = "冥符拍脸-当众羞辱反转",
    hype_type: HypeType = HypeType.FACE_SLAP,
) -> HypeRecipe:
    return HypeRecipe(
        key=key,
        hype_type=hype_type,
        trigger_keywords=("冥符", "贴脸", "僵住"),
        narrative_beats=("挑衅升级", "主角收声", "冥符拍脸", "对手脸色铁青"),
        intensity_floor=8.0,
        cadence_hint="400-700 字，冥符亮相前必须有一段静",
    )


def _sample_scheme(
    *, recipes: tuple[HypeRecipe, ...] = (),
    selling_points: tuple[str, ...] = ("诡异复苏", "阴阳万亿资产"),
    promise: str = "第一章就要亮出钱不再是钱、冥符阴兵才是万亿资产的世界规则。",
    hook_keywords: tuple[str, ...] = ("冥符", "阴兵", "香火"),
    chapter_hook_strategy: str = "每章至少抛出一条新诡异资产或旧仇家以诡异形式归来。",
) -> HypeScheme:
    return HypeScheme(
        recipe_deck=recipes,
        selling_points=selling_points,
        reader_promise=promise,
        hook_keywords=hook_keywords,
        chapter_hook_strategy=chapter_hook_strategy,
    )


def _invariants_with_scheme(
    scheme: HypeScheme, language: str = "zh-CN"
) -> ProjectInvariants:
    return ProjectInvariants(
        project_id=uuid4(),
        language=language,
        length_envelope=LengthEnvelope(
            min_chars=2500, target_chars=3200, max_chars=4000
        ),
        hype_scheme=scheme,
    )


# ---------------------------------------------------------------------------
# build_reader_contract_section.
# ---------------------------------------------------------------------------


class TestReaderContractSection:
    def test_empty_scheme_returns_empty(self) -> None:
        inv = _invariants_with_scheme(HypeScheme())
        assert build_reader_contract_section(inv, chapter_no=1) == ""

    def test_renders_on_early_chapters(self) -> None:
        inv = _invariants_with_scheme(_sample_scheme())
        for ch in range(1, 11):
            block = build_reader_contract_section(inv, chapter_no=ch)
            assert block, f"chapter {ch} should render the contract"
            assert "读者契约" in block
            assert "诡异复苏" in block
            assert "冥符" in block

    def test_renders_every_fifth_chapter_after_head(self) -> None:
        inv = _invariants_with_scheme(_sample_scheme())
        # head=10, tail=5 → renders on 11, 16, 21, ...
        assert build_reader_contract_section(inv, chapter_no=11)
        assert build_reader_contract_section(inv, chapter_no=12) == ""
        assert build_reader_contract_section(inv, chapter_no=13) == ""
        assert build_reader_contract_section(inv, chapter_no=16)
        assert build_reader_contract_section(inv, chapter_no=21)

    def test_english_scheme_uses_english_headings(self) -> None:
        scheme = _sample_scheme(
            promise="The first chapter shows money is no longer currency.",
            chapter_hook_strategy="Introduce one new ghost asset per chapter.",
            selling_points=("ghost wealth", "supernatural capitalism"),
            hook_keywords=("incense", "paper talisman"),
        )
        inv = _invariants_with_scheme(scheme, language="en")
        block = build_reader_contract_section(inv, chapter_no=1)
        assert block
        assert "READER CONTRACT" in block
        assert "ghost wealth" in block


# ---------------------------------------------------------------------------
# build_hype_constraints.
# ---------------------------------------------------------------------------


class TestHypeConstraints:
    def test_includes_assigned_type_and_recipe_beats(self) -> None:
        inv = _invariants_with_scheme(_sample_scheme())
        band = HYPE_DENSITY_CURVE[1]  # min_count=1 mid-range
        recipe = _sample_recipe()
        block = build_hype_constraints(
            inv,
            band=band,
            hype_type=HypeType.FACE_SLAP,
            recipe=recipe,
            intensity_target=8.0,
            is_golden_three=False,
        )
        assert "【本章爽点约束】" in block
        assert "face_slap" in block
        assert "8.0" in block
        assert "冥符拍脸-当众羞辱反转" in block
        assert "挑衅升级" in block
        # Cadence hint surfaced.
        assert "400-700 字" in block

    def test_golden_three_injects_special_note(self) -> None:
        inv = _invariants_with_scheme(_sample_scheme())
        band = HYPE_DENSITY_CURVE[0]  # min_count=2
        block = build_hype_constraints(
            inv,
            band=band,
            hype_type=HypeType.POWER_REVEAL,
            recipe=_sample_recipe(
                key="阴兵列阵-当场亮牌", hype_type=HypeType.POWER_REVEAL
            ),
            intensity_target=8.5,
            is_golden_three=True,
        )
        assert "黄金三章特别约束" in block
        assert "前 1000 字内" in block

    def test_declares_hype_vs_cliffhanger_separation(self) -> None:
        inv = _invariants_with_scheme(_sample_scheme())
        band = HYPE_DENSITY_CURVE[1]
        block = build_hype_constraints(
            inv,
            band=band,
            hype_type=HypeType.LEVEL_UP,
            recipe=None,
            intensity_target=7.0,
        )
        assert "爽点 ≠ 章末悬念" in block

    def test_returns_empty_when_no_type_and_no_recipe(self) -> None:
        inv = _invariants_with_scheme(_sample_scheme())
        band = HYPE_DENSITY_CURVE[1]
        assert (
            build_hype_constraints(
                inv,
                band=band,
                hype_type=None,
                recipe=None,
                intensity_target=0.0,
            )
            == ""
        )

    def test_ladder_rung_injects_capability_and_anchor(self) -> None:
        inv = _invariants_with_scheme(_sample_scheme())
        band = HYPE_DENSITY_CURVE[1]
        rung = GoldenFingerRung(
            rung_index=3,
            unlock_percentile=(0.4, 0.6),
            capability="冥库 Level 3 - 阴兵结阵",
            signal_keywords=("结阵", "阴风"),
            hype_type_anchor=HypeType.LEVEL_UP,
        )
        block = build_hype_constraints(
            inv,
            band=band,
            hype_type=HypeType.LEVEL_UP,
            recipe=None,
            intensity_target=7.0,
            ladder_rung=rung,
        )
        assert "本章金手指阶梯" in block
        assert "第 3 级" in block
        assert "冥库 Level 3" in block
        assert "level_up" in block
        assert "结阵" in block

    def test_ladder_rung_english_heading_when_en_language(self) -> None:
        scheme = _sample_scheme(
            promise="Show the supernatural capitalism early.",
            chapter_hook_strategy="Introduce one ghost asset per chapter.",
            selling_points=("ghost wealth",),
            hook_keywords=("incense",),
        )
        inv = _invariants_with_scheme(scheme, language="en")
        band = HYPE_DENSITY_CURVE[1]
        rung = GoldenFingerRung(
            rung_index=2,
            unlock_percentile=(0.2, 0.4),
            capability="Ghost Vault tier 2 - spirit array",
            signal_keywords=("array", "banner"),
            hype_type_anchor=HypeType.POWER_REVEAL,
        )
        block = build_hype_constraints(
            inv,
            band=band,
            hype_type=HypeType.POWER_REVEAL,
            recipe=None,
            intensity_target=7.5,
            ladder_rung=rung,
        )
        assert "[CHAPTER GOLDEN-FINGER RUNG]" in block
        assert "Rung 2" in block
        assert "Ghost Vault tier 2" in block
        assert "power_reveal" in block

    def test_ladder_rung_without_signals_still_renders(self) -> None:
        """Engine-extracted rungs ship empty signal_keywords — guard the branch."""

        inv = _invariants_with_scheme(_sample_scheme())
        band = HYPE_DENSITY_CURVE[1]
        rung = GoldenFingerRung(
            rung_index=1,
            unlock_percentile=(0.0, 0.33),
            capability="现实落魄",
            signal_keywords=(),
            hype_type_anchor=HypeType.GOLDEN_FINGER_REVEAL,
        )
        block = build_hype_constraints(
            inv,
            band=band,
            hype_type=HypeType.GOLDEN_FINGER_REVEAL,
            recipe=None,
            intensity_target=6.0,
            ladder_rung=rung,
        )
        assert "本章金手指阶梯" in block
        assert "现实落魄" in block
        # No signals block when keywords tuple is empty.
        assert "关键信号" not in block


# ---------------------------------------------------------------------------
# build_chapter_prompt wiring.
# ---------------------------------------------------------------------------


class TestChapterPromptWiring:
    def test_empty_scheme_skips_hype_sections(self) -> None:
        inv = _invariants_with_scheme(HypeScheme())
        budget = DiversityBudget(project_id=uuid4())
        plan = build_chapter_prompt(
            inv,
            budget,
            chapter_no=1,
            total_chapters=60,
            system="SYSTEM",
            bible_slice="BIBLE",
            scene_spec="SCENE",
        )
        assert plan.hype_constraints == ""
        assert plan.reader_contract_section == ""
        assert plan.assigned_hype_type is None
        assert plan.assigned_hype_recipe is None

    def test_populated_scheme_injects_all_hype_metadata(self) -> None:
        scheme = _sample_scheme(recipes=(_sample_recipe(),))
        inv = _invariants_with_scheme(scheme)
        budget = DiversityBudget(project_id=uuid4())
        plan = build_chapter_prompt(
            inv,
            budget,
            chapter_no=1,
            total_chapters=60,
            pacing_profile="fast",
            system="SYSTEM",
            bible_slice="BIBLE",
            scene_spec="SCENE",
        )
        assert plan.assigned_hype_type is HypeType.FACE_SLAP
        assert plan.assigned_hype_recipe is not None
        assert plan.assigned_hype_recipe.key == "冥符拍脸-当众羞辱反转"
        # Chapter 1 is within the golden three.
        assert "黄金三章" in plan.hype_constraints
        assert "读者契约" in plan.reader_contract_section
        # Fast pacing bumps the target intensity by +0.5.
        band_0 = target_hype_for_chapter(1, 60, pacing_profile="fast")
        assert plan.assigned_hype_intensity is not None
        assert plan.assigned_hype_intensity >= band_0.intensity_target

    def test_render_order_places_hype_between_bible_and_diversity(self) -> None:
        scheme = _sample_scheme(recipes=(_sample_recipe(),))
        inv = _invariants_with_scheme(scheme)
        budget = DiversityBudget(project_id=uuid4())
        plan = build_chapter_prompt(
            inv,
            budget,
            chapter_no=1,
            total_chapters=60,
            system="SYSTEM_MARKER",
            bible_slice="BIBLE_MARKER",
            scene_spec="SCENE_MARKER",
        )
        rendered = plan.render()
        # Order sanity: bible before reader-contract before hype before
        # diversity before scene.
        bible_at = rendered.index("BIBLE_MARKER")
        reader_at = rendered.index("读者契约")
        hype_at = rendered.index("本章爽点约束")
        diversity_at = rendered.index("创作多样性约束")
        scene_at = rendered.index("SCENE_MARKER")
        assert bible_at < reader_at < hype_at < diversity_at < scene_at

    def test_golden_finger_ladder_threads_through_prompt(self) -> None:
        """Plan §Phase 3: ladder rung injects at the end of hype_constraints."""

        scheme = _sample_scheme(recipes=(_sample_recipe(),))
        inv = _invariants_with_scheme(scheme)
        budget = DiversityBudget(project_id=uuid4())
        ladder = GoldenFingerLadder(
            rungs=(
                GoldenFingerRung(
                    rung_index=1,
                    unlock_percentile=(0.0, 0.33),
                    capability="冥库 Level 1 - 冥符可随身",
                    signal_keywords=("冥符", "阴气"),
                    hype_type_anchor=HypeType.GOLDEN_FINGER_REVEAL,
                ),
                GoldenFingerRung(
                    rung_index=2,
                    unlock_percentile=(0.33, 1.0),
                    capability="冥库 Level 2 - 阴兵可结阵",
                    signal_keywords=("阴兵", "列阵"),
                    hype_type_anchor=HypeType.LEVEL_UP,
                ),
            ),
            source="preset_declared",
        )
        plan = build_chapter_prompt(
            inv,
            budget,
            chapter_no=1,
            total_chapters=60,
            system="",
            bible_slice="",
            scene_spec="",
            golden_finger_ladder=ladder,
        )
        assert "本章金手指阶梯" in plan.hype_constraints
        assert "冥库 Level 1" in plan.hype_constraints
        # Chapter 1 of 60 is at percentile ~0.017 → rung 1.
        assert "Level 2" not in plan.hype_constraints
        assert "冥符" in plan.hype_constraints

    def test_empty_ladder_is_noop(self) -> None:
        """An engine-extracted ladder from blank growth_curve is empty; no injection."""

        scheme = _sample_scheme(recipes=(_sample_recipe(),))
        inv = _invariants_with_scheme(scheme)
        budget = DiversityBudget(project_id=uuid4())
        ladder = GoldenFingerLadder(rungs=(), source="engine_extracted")
        plan = build_chapter_prompt(
            inv,
            budget,
            chapter_no=1,
            total_chapters=60,
            system="",
            bible_slice="",
            scene_spec="",
            golden_finger_ladder=ladder,
        )
        assert "本章金手指阶梯" not in plan.hype_constraints

    def test_hype_history_influences_recipe_choice(self) -> None:
        """After the FACE_SLAP recipe fires, diversity should push to a different type."""
        recipes = (
            _sample_recipe(),  # face_slap
            _sample_recipe(
                key="阴兵列阵-当场亮牌", hype_type=HypeType.POWER_REVEAL
            ),
        )
        scheme = _sample_scheme(recipes=recipes)
        inv = _invariants_with_scheme(scheme)
        budget = DiversityBudget(project_id=uuid4())
        plan1 = build_chapter_prompt(
            inv,
            budget,
            chapter_no=1,
            total_chapters=60,
            system="",
            bible_slice="",
            scene_spec="",
        )
        # Simulate pipeline registering what plan1 produced.
        assert plan1.assigned_hype_type is not None
        assert plan1.assigned_hype_recipe is not None
        budget.register_hype_moment(
            1,
            plan1.assigned_hype_type,
            plan1.assigned_hype_recipe.key,
            plan1.assigned_hype_intensity or 0.0,
        )
        plan2 = build_chapter_prompt(
            inv,
            budget,
            chapter_no=2,
            total_chapters=60,
            system="",
            bible_slice="",
            scene_spec="",
        )
        # Different recipe on the follow-up chapter.
        assert plan2.assigned_hype_recipe is not None
        assert plan2.assigned_hype_recipe.key != plan1.assigned_hype_recipe.key


# ---------------------------------------------------------------------------
# build_chapter_hype_blocks — lean picker used by the scene pipeline.
# ---------------------------------------------------------------------------


class TestChapterHypeBlocks:
    """End-to-end plumbing: verify the scene-pipeline helper that ships
    pre-rendered hype blocks through ``SceneWriterContextPacket`` shares
    the same assignment across every scene of one chapter and stays a
    safe no-op for legacy projects."""

    def test_empty_scheme_returns_empty_blocks(self) -> None:
        """Legacy projects with no HypeScheme must hit the no-op fast path."""

        inv = _invariants_with_scheme(HypeScheme())
        budget = DiversityBudget(project_id=uuid4())
        blocks = build_chapter_hype_blocks(
            inv,
            budget,
            chapter_no=1,
            total_chapters=60,
        )
        assert blocks is EMPTY_HYPE_BLOCKS
        assert blocks.is_empty
        assert blocks.reader_contract_block == ""
        assert blocks.hype_constraints_block == ""
        assert blocks.assigned_hype_type is None
        assert blocks.assigned_hype_recipe is None
        assert blocks.assigned_hype_intensity is None

    def test_populated_scheme_returns_rendered_blocks(self) -> None:
        scheme = _sample_scheme(recipes=(_sample_recipe(),))
        inv = _invariants_with_scheme(scheme)
        budget = DiversityBudget(project_id=uuid4())
        blocks = build_chapter_hype_blocks(
            inv,
            budget,
            chapter_no=1,
            total_chapters=60,
        )
        assert not blocks.is_empty
        assert "读者契约" in blocks.reader_contract_block
        assert "本章爽点约束" in blocks.hype_constraints_block
        assert blocks.assigned_hype_type is HypeType.FACE_SLAP
        assert blocks.assigned_hype_recipe is not None
        assert (
            blocks.assigned_hype_recipe.key == "冥符拍脸-当众羞辱反转"
        )
        assert blocks.assigned_hype_intensity is not None
        assert blocks.assigned_hype_intensity >= 6.0

    def test_deterministic_pick_for_same_chapter(self) -> None:
        """All scenes of one chapter share the same pick (key property:
        calling the helper twice without mutating the budget returns the
        same assignment, so scene 1 and scene 2 of chapter 3 agree)."""

        scheme = _sample_scheme(
            recipes=(
                _sample_recipe(),
                _sample_recipe(
                    key="阴兵列阵-当场亮牌", hype_type=HypeType.POWER_REVEAL
                ),
            )
        )
        inv = _invariants_with_scheme(scheme)
        budget = DiversityBudget(project_id=uuid4())
        first = build_chapter_hype_blocks(
            inv, budget, chapter_no=3, total_chapters=60
        )
        second = build_chapter_hype_blocks(
            inv, budget, chapter_no=3, total_chapters=60
        )
        assert first.assigned_hype_type == second.assigned_hype_type
        assert first.assigned_hype_recipe is not None
        assert second.assigned_hype_recipe is not None
        assert (
            first.assigned_hype_recipe.key
            == second.assigned_hype_recipe.key
        )

    def test_golden_three_flag_present_on_early_chapters(self) -> None:
        scheme = _sample_scheme(recipes=(_sample_recipe(),))
        inv = _invariants_with_scheme(scheme)
        budget = DiversityBudget(project_id=uuid4())
        for ch in (1, 2, 3):
            blocks = build_chapter_hype_blocks(
                inv, budget, chapter_no=ch, total_chapters=60
            )
            assert "黄金三章" in blocks.hype_constraints_block

    def test_ladder_rung_threads_into_hype_block(self) -> None:
        scheme = _sample_scheme(recipes=(_sample_recipe(),))
        inv = _invariants_with_scheme(scheme)
        budget = DiversityBudget(project_id=uuid4())
        ladder = GoldenFingerLadder(
            rungs=(
                GoldenFingerRung(
                    rung_index=1,
                    unlock_percentile=(0.0, 0.5),
                    capability="冥库 Level 1 - 冥符随身",
                    signal_keywords=("冥符", "阴气"),
                    hype_type_anchor=HypeType.GOLDEN_FINGER_REVEAL,
                ),
                GoldenFingerRung(
                    rung_index=2,
                    unlock_percentile=(0.5, 1.0),
                    capability="冥库 Level 2 - 阴兵结阵",
                    signal_keywords=("阴兵", "列阵"),
                    hype_type_anchor=HypeType.LEVEL_UP,
                ),
            ),
            source="preset_declared",
        )
        blocks = build_chapter_hype_blocks(
            inv,
            budget,
            chapter_no=1,
            total_chapters=60,
            golden_finger_ladder=ladder,
        )
        assert "本章金手指阶梯" in blocks.hype_constraints_block
        assert "冥库 Level 1" in blocks.hype_constraints_block
        assert "Level 2" not in blocks.hype_constraints_block

    def test_reader_contract_cadence_honored(self) -> None:
        """head=10 tail=5 → chapter 12 has no reader contract section."""

        scheme = _sample_scheme(recipes=(_sample_recipe(),))
        inv = _invariants_with_scheme(scheme)
        budget = DiversityBudget(project_id=uuid4())
        # Chapter within head: always rendered.
        early = build_chapter_hype_blocks(
            inv, budget, chapter_no=5, total_chapters=60
        )
        assert early.reader_contract_block
        # Chapter after head, not on 5-step cadence: empty.
        between = build_chapter_hype_blocks(
            inv, budget, chapter_no=12, total_chapters=60
        )
        assert between.reader_contract_block == ""
        # hype constraints stay populated regardless of contract cadence.
        assert between.hype_constraints_block

    def test_deck_exhausted_by_recent_keys_still_returns_structure(
        self,
    ) -> None:
        """Even if all recipes are recent, caller still receives a valid
        ChapterHypeBlocks so the scene pipeline has safe defaults."""

        scheme = _sample_scheme(recipes=(_sample_recipe(),))
        inv = _invariants_with_scheme(scheme)
        budget = DiversityBudget(project_id=uuid4())
        # Poison the budget so the one recipe is LRU-blocked.
        budget.register_hype_moment(
            1, HypeType.FACE_SLAP, "冥符拍脸-当众羞辱反转", 8.0
        )
        blocks = build_chapter_hype_blocks(
            inv, budget, chapter_no=2, total_chapters=60
        )
        # Helper must never raise: either falls back to same recipe or
        # returns None recipe — both are acceptable shapes downstream.
        assert isinstance(blocks.hype_constraints_block, str)
        assert isinstance(blocks.reader_contract_block, str)
