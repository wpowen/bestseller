from __future__ import annotations

from uuid import uuid4

from bestseller.domain.cultural_texture import CulturalTextureModule, MaterialPaletteItem
from bestseller.domain.ensemble_arc import EnsembleArcKernel, EnsembleCharacterArc
from bestseller.domain.geography import GeographyKernel, Region, RouteEdge
from bestseller.domain.mystery_anchor import MysteryAnchor, MysteryAnchorKernel, RevealMilestone
from bestseller.services.diversity_budget import DiversityBudget
from bestseller.services.invariants import seed_invariants
from bestseller.services.kernel_composer import KernelComposer, NarrativeRichnessKernels
from bestseller.services.kernel_delta_slicer import KernelDeltaSlicer, estimate_kernel_tokens
from bestseller.services.prompt_constructor import build_chapter_l3_blocks


def _geography() -> GeographyKernel:
    regions = [
        Region(
            name=name,
            climate="温带",
            terrain="河网",
            demographics="商旅",
            surface_economy=["稻米"],
            cultural_signature=f"{name}水路繁忙。",
        )
        for name in ("江宁", "青崖", "北渡", "南港")
    ]
    return GeographyKernel(
        regions=regions,
        routes=[
            RouteEdge(
                region_a="江宁",
                region_b="青崖",
                kind="水路",
                days_typical=2,
                hazard_level=1,
            ),
            RouteEdge(
                region_a="青崖",
                region_b="北渡",
                kind="官道",
                days_typical=3,
                hazard_level=2,
            ),
            RouteEdge(
                region_a="北渡",
                region_b="南港",
                kind="海路",
                days_typical=4,
                hazard_level=3,
            ),
        ],
        capital_region="江宁",
        protagonist_origin="江宁",
        protagonist_current="青崖",
    )


def _culture() -> CulturalTextureModule:
    categories = [
        "food",
        "clothing",
        "tool",
        "ornament",
        "music",
        "vehicle",
        "food",
        "tool",
        "music",
    ]
    return CulturalTextureModule(
        palette=[
            MaterialPaletteItem(
                category=category,  # type: ignore[arg-type]
                name=f"物件{i}",
                sensory_hook=f"物件{i}的气味",
                class_signal="市井",
            )
            for i, category in enumerate(categories)
        ],
        daily_rituals=["晨起净手"],
        aesthetic_zeitgeist="重礼。",
    )


def _ensemble(active_id) -> EnsembleArcKernel:
    return EnsembleArcKernel(
        arcs=[
            EnsembleCharacterArc(
                owner_id=active_id,
                arc_kind="loyalty",
                private_goal="守住旧账",
                private_obstacle="身份被疑",
                private_payoff="证明忠诚",
                standalone_value="能单独推动支线",
            ),
            EnsembleCharacterArc(
                owner_id=uuid4(),
                arc_kind="fall",
                private_goal="夺权",
                private_obstacle="证据不足",
                private_payoff="短暂得势",
                standalone_value="制造外部压力",
            ),
        ]
    )


def _mystery() -> MysteryAnchorKernel:
    def anchor(question: str, payoff: tuple[int, int]) -> MysteryAnchor:
        return MysteryAnchor(
            question=question,
            stake_if_solved="主线真相推进",
            reveal_milestones=[
                RevealMilestone(
                    volume=1,
                    fraction_revealed=0.2,
                    reveal_kind="hint",
                    description="线索",
                ),
                RevealMilestone(
                    volume=1,
                    fraction_revealed=0.8,
                    reveal_kind="partial_truth",
                    description="半真相",
                ),
            ],
            final_payoff_chapter_range=payoff,
        )

    return MysteryAnchorKernel(
        anchors=[anchor("近端谜题", (10, 12)), anchor("远端谜题", (90, 95))]
    )


def test_kernel_delta_slicer_keeps_chapter_local_subset() -> None:
    active_id = uuid4()
    context = NarrativeRichnessKernels(
        geography_kernel=_geography(),
        cultural_texture_module=_culture(),
        ensemble_arc_kernel=_ensemble(active_id),
        mystery_anchor_kernel=_mystery(),
    )

    sliced = KernelDeltaSlicer(max_tokens=800).slice_for_chapter(
        context,
        chapter_no=11,
        current_region="青崖",
        active_character_ids=(active_id,),
        recent_palette_terms=("物件0",),
    )

    assert sliced.geography_kernel is not None
    assert {region.name for region in sliced.geography_kernel.regions} <= {
        "江宁",
        "青崖",
        "北渡",
    }
    assert sliced.ensemble_arc_kernel is not None
    assert [arc.owner_id for arc in sliced.ensemble_arc_kernel.arcs] == [active_id]
    assert sliced.mystery_anchor_kernel is not None
    assert [anchor.question for anchor in sliced.mystery_anchor_kernel.anchors] == [
        "近端谜题"
    ]
    assert sliced.cultural_texture_module is not None
    assert sliced.cultural_texture_module.palette[0].name != "物件0"
    assert estimate_kernel_tokens(sliced) <= 800


def test_kernel_composer_exposes_slice_for_chapter(tmp_path) -> None:
    composer = KernelComposer(tmp_path / "story-bible")
    composer.compose(
        NarrativeRichnessKernels(
            geography_kernel=_geography(),
            cultural_texture_module=_culture(),
        )
    )

    sliced = composer.slice_for_chapter(chapter_no=2, current_region="青崖")

    assert sliced.geography_kernel is not None
    assert len(sliced.geography_kernel.regions) <= 3


def test_prompt_constructor_slices_richness_context_before_rendering() -> None:
    invariants = seed_invariants(
        project_id=uuid4(),
        language="zh-CN",
        words_per_chapter={"min": 1000, "target": 1500, "max": 2000},
        pov="close_third",
    )
    blocks = build_chapter_l3_blocks(
        invariants,
        DiversityBudget(project_id=uuid4()),
        chapter_no=2,
        current_region="青崖",
        narrative_richness_context=NarrativeRichnessKernels(
            geography_kernel=_geography(),
            cultural_texture_module=_culture(),
        ),
    )

    assert "Narrative Richness Kernel" in blocks.narrative_richness_section
    assert "南港" not in blocks.narrative_richness_section
