from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.domain.cultural_texture import CulturalTextureModule, MaterialPaletteItem
from bestseller.domain.ethical_dilemma import EthicalDilemmaKernel, EthicalDilemmaSlot
from bestseller.domain.geography import GeographyKernel, Region, RouteEdge
from bestseller.services.cultural_texture_density_gate import (
    scan_cultural_texture_density,
)
from bestseller.services.diversity_budget import DiversityBudget
from bestseller.services.invariants import seed_invariants
from bestseller.services.kernel_composer import (
    KernelComposer,
    KernelNotPersistedError,
    NarrativeRichnessKernels,
    narrative_richness_context_from_metadata,
    render_narrative_richness_prompt_block,
)
from bestseller.services.prompt_constructor import build_chapter_l3_blocks, build_chapter_prompt

pytestmark = pytest.mark.unit


def _geography() -> GeographyKernel:
    def region(name: str) -> Region:
        return Region(
            name=name,
            climate="温带",
            terrain="河网",
            demographics="商旅",
            dominant_faction=None,
            surface_economy=["稻米"],
            cultural_signature=f"{name}有水路烟火气。",
        )

    return GeographyKernel(
        regions=[region("江宁"), region("青崖"), region("北渡")],
        routes=[
            RouteEdge(
                region_a="江宁",
                region_b="青崖",
                kind="水路",
                days_typical=2,
                hazard_level=1,
                hazard_kinds=[],
            ),
            RouteEdge(
                region_a="青崖",
                region_b="北渡",
                kind="官道",
                days_typical=3,
                hazard_level=2,
                hazard_kinds=[],
            ),
        ],
        capital_region="江宁",
        protagonist_origin="江宁",
        protagonist_current="青崖",
    )


def _culture() -> CulturalTextureModule:
    cats = ["food", "clothing", "tool", "ornament", "music", "vehicle", "food", "tool"]
    return CulturalTextureModule(
        palette=[
            MaterialPaletteItem(
                category=cat,  # type: ignore[arg-type]
                name=f"物件{i}",
                sensory_hook=f"物件{i}的气味",
                class_signal="市井",
            )
            for i, cat in enumerate(cats)
        ],
        daily_rituals=["晨起净手"],
        taboo_behaviors=["直呼长辈名讳"],
        aesthetic_zeitgeist="重礼。",
    )


def _ethical() -> EthicalDilemmaKernel:
    return EthicalDilemmaKernel(
        slots=[
            EthicalDilemmaSlot(
                chapter_window=(2, 2),
                dilemma_kind="law_vs_compassion",
                competing_values=("律法", "怜悯"),
                involved_characters=[uuid4()],
                intended_choice="open",
                consequence_for_unchosen="无论不选哪边,都要有人承担后果。",
            )
        ],
        minimum_cadence_chapters=12,
    )


def test_composer_renders_narrative_richness_block() -> None:
    block = render_narrative_richness_prompt_block(
        NarrativeRichnessKernels(
            geography_kernel=_geography(),
            cultural_texture_module=_culture(),
            ethical_dilemma_kernel=_ethical(),
        ),
        chapter_no=2,
    )
    assert "Narrative Richness Kernel" in block
    assert "青崖" in block
    assert "物件" in block
    assert "伦理两难" in block


def test_kernel_composer_persists_and_loads_from_story_bible(tmp_path) -> None:
    composer = KernelComposer(tmp_path / "story-bible")

    composer.compose(
        NarrativeRichnessKernels(
            geography_kernel=_geography(),
            cultural_texture_module=_culture(),
        )
    )

    geography_path = tmp_path / "story-bible/kernels/geography-kernel.json"
    assert geography_path.exists()
    assert "schema_version" in geography_path.read_text(encoding="utf-8")

    loaded = composer.load_for_chapter(
        required_kernel_names=("geography_kernel", "cultural_texture_module")
    )
    assert loaded.geography_kernel is not None
    assert loaded.cultural_texture_module is not None


def test_kernel_composer_rejects_memory_fallback_when_disk_missing(tmp_path) -> None:
    composer = KernelComposer(tmp_path / "story-bible")

    with pytest.raises(KernelNotPersistedError, match="ERROR_KERNEL_NOT_PERSISTED"):
        composer.load_for_chapter(
            required_kernel_names=("geography_kernel",),
            memory_context=NarrativeRichnessKernels(geography_kernel=_geography()),
        )


def test_kernel_composer_missing_disk_without_memory_is_safe_noop(tmp_path) -> None:
    composer = KernelComposer(tmp_path / "story-bible")

    loaded = composer.load_for_chapter(
        required_kernel_names=("geography_kernel",),
    )

    assert loaded.geography_kernel is None


def test_prompt_constructor_accepts_richness_context() -> None:
    invariants = seed_invariants(
        project_id=uuid4(),
        language="zh-CN",
        words_per_chapter={"min": 1000, "target": 1500, "max": 2000},
        pov="close_third",
    )
    plan = build_chapter_prompt(
        invariants,
        DiversityBudget(project_id=uuid4()),
        chapter_no=2,
        system="你是畅销小说作者。",
        scene_spec="【本章任务】进城。",
        narrative_richness_context=NarrativeRichnessKernels(
            geography_kernel=_geography(),
            cultural_texture_module=_culture(),
            ethical_dilemma_kernel=_ethical(),
        ),
    )
    assert "Narrative Richness Kernel" in plan.render()


def test_metadata_context_feeds_current_workflow_shape() -> None:
    context = narrative_richness_context_from_metadata(
        {
            "narrative_richness_kernels": {
                "geography_kernel": _geography().model_dump(mode="json"),
                "cultural_texture_module": _culture().model_dump(mode="json"),
                "ethical_dilemma_kernel": _ethical().model_dump(mode="json"),
            }
        }
    )
    assert context is not None
    block = render_narrative_richness_prompt_block(context, chapter_no=2)
    assert "Narrative Richness Kernel" in block
    assert "Required sensory anchors" in block


def test_metadata_context_can_extract_worldview_cultural_module() -> None:
    context = narrative_richness_context_from_metadata(
        {
            "story_design_kernel": {
                "worldview_kernel": {
                    "cultural_texture_module": _culture().model_dump(mode="json")
                }
            }
        }
    )
    assert context is not None
    block = render_narrative_richness_prompt_block(context, chapter_no=1)
    assert "Cultural Texture Module" in block


def test_five_chapter_prompt_pilot_lands_rotating_palette() -> None:
    culture = _culture()
    context = NarrativeRichnessKernels(
        geography_kernel=_geography(),
        cultural_texture_module=culture,
        ethical_dilemma_kernel=_ethical(),
    )
    prompts = [
        render_narrative_richness_prompt_block(context, chapter_no=chapter_no)
        for chapter_no in range(1, 6)
    ]
    expected_hooks = [item.sensory_hook for item in culture.palette[:5]]
    assert all(hook in prompt for hook, prompt in zip(expected_hooks, prompts, strict=True))

    landing_reports = [
        scan_cultural_texture_density(
            culture,
            chapter_text=f"本章场景落到{hook}, 人物借此推进冲突。",
            chapter_no=chapter_no,
            category="历史架空",
        )
        for chapter_no, hook in enumerate(expected_hooks, start=1)
    ]
    assert all(not report.findings for report in landing_reports)


def test_l3_blocks_accept_richness_context_for_scene_workflow() -> None:
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
        narrative_richness_context=NarrativeRichnessKernels(
            geography_kernel=_geography(),
            cultural_texture_module=_culture(),
            ethical_dilemma_kernel=_ethical(),
        ),
    )
    assert "Narrative Richness Kernel" in blocks.as_prompt_block()
