from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from bestseller.domain.crowd_dynamics import CrowdScene
from bestseller.domain.cultural_texture import CulturalTextureModule
from bestseller.domain.ensemble_arc import EnsembleArcKernel
from bestseller.domain.ethical_dilemma import EthicalDilemmaKernel
from bestseller.domain.geography import GeographyKernel
from bestseller.domain.lineage_system import LineageKernel
from bestseller.domain.meta_layer import MetaLayerContract
from bestseller.domain.mystery_anchor import MysteryAnchorKernel
from bestseller.domain.zeitgeist import ZeitgeistContract
from bestseller.services.crowd_scene_planner import render_crowd_scene_prompt_block
from bestseller.services.cultural_texture_density_gate import pick_palette_items_for_chapter
from bestseller.services.ensemble_arc_kernel import render_ensemble_arc_prompt_block
from bestseller.services.ethical_dilemma_kernel import render_ethical_dilemma_prompt_block
from bestseller.services.geography_kernel import render_geography_prompt_block
from bestseller.services.lineage_kernel import render_lineage_prompt_block
from bestseller.services.meta_layer_composer import render_meta_layer_prompt_block
from bestseller.services.mystery_anchor_kernel import render_mystery_anchor_prompt_block


class NarrativeRichnessKernels(BaseModel, frozen=True):
    geography_kernel: GeographyKernel | None = None
    cultural_texture_module: CulturalTextureModule | None = None
    ensemble_arc_kernel: EnsembleArcKernel | None = None
    mystery_anchor_kernel: MysteryAnchorKernel | None = None
    ethical_dilemma_kernel: EthicalDilemmaKernel | None = None
    lineage_kernel: LineageKernel | None = None
    crowd_scene: CrowdScene | None = None
    zeitgeist_contract: ZeitgeistContract | None = None
    meta_layer_contract: MetaLayerContract | None = None


class KernelNotPersistedError(RuntimeError):
    """Raised when a kernel exists in memory but not in story-bible/kernels."""


_KERNEL_FILE_MAP: dict[str, str] = {
    "geography_kernel": "geography-kernel.json",
    "cultural_texture_module": "cultural-texture-module.json",
    "ensemble_arc_kernel": "ensemble-arc-kernel.json",
    "mystery_anchor_kernel": "mystery-anchor-kernel.json",
    "ethical_dilemma_kernel": "ethical-dilemma-kernel.json",
    "lineage_kernel": "lineage-kernel.json",
    "crowd_scene": "crowd-scene.json",
    "zeitgeist_contract": "zeitgeist-contract.json",
    "meta_layer_contract": "meta-layer-contract.json",
}


class KernelComposer:
    """Persist and load narrative-richness kernels from story-bible/kernels."""

    def __init__(self, story_bible_dir: str | Path) -> None:
        self.story_bible_dir = Path(story_bible_dir)
        self.kernels_dir = self.story_bible_dir / "kernels"

    def compose(self, context: NarrativeRichnessKernels | dict) -> NarrativeRichnessKernels:
        """Validate the context and persist every populated kernel."""

        kernels = NarrativeRichnessKernels.model_validate(context)
        self.kernels_dir.mkdir(parents=True, exist_ok=True)
        for field_name, filename in _KERNEL_FILE_MAP.items():
            value = getattr(kernels, field_name)
            if value is None:
                continue
            payload = _kernel_to_payload(value)
            path = self.kernels_dir / filename
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
                encoding="utf-8",
            )
        return kernels

    def load_for_chapter(
        self,
        *,
        required_kernel_names: tuple[str, ...] = (),
        memory_context: NarrativeRichnessKernels | dict | None = None,
    ) -> NarrativeRichnessKernels:
        """Load kernels from disk; never silently fall back to memory."""

        memory = _coerce_context(memory_context)
        payload: dict[str, object] = {}
        fields = required_kernel_names or tuple(_KERNEL_FILE_MAP)
        for field_name in fields:
            filename = _KERNEL_FILE_MAP.get(field_name)
            if filename is None:
                continue
            path = self.kernels_dir / filename
            if path.exists():
                payload[field_name] = json.loads(path.read_text(encoding="utf-8"))
                continue
            if memory is not None and getattr(memory, field_name, None) is not None:
                raise KernelNotPersistedError(
                    f"ERROR_KERNEL_NOT_PERSISTED: {field_name} missing at {path}"
                )
        return NarrativeRichnessKernels.model_validate(payload)

    def slice_for_chapter(
        self,
        *,
        chapter_no: int,
        current_region: str | None = None,
        active_character_ids: tuple[str, ...] = (),
        recent_palette_terms: tuple[str, ...] = (),
        max_tokens: int = 800,
        required_kernel_names: tuple[str, ...] = (),
        memory_context: NarrativeRichnessKernels | dict | None = None,
    ) -> NarrativeRichnessKernels:
        """Load persisted kernels and return a chapter-local delta slice."""

        from bestseller.services.kernel_delta_slicer import KernelDeltaSlicer

        context = self.load_for_chapter(
            required_kernel_names=required_kernel_names,
            memory_context=memory_context,
        )
        return KernelDeltaSlicer(max_tokens=max_tokens).slice_for_chapter(
            context,
            chapter_no=chapter_no,
            current_region=current_region,
            active_character_ids=active_character_ids,
            recent_palette_terms=recent_palette_terms,
        )


def _kernel_to_payload(value: object) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        payload = {"value": value}
    if "schema_version" not in payload:
        payload = {"schema_version": "kernel.v1", **payload}
    return payload


def _coerce_context(context: object) -> NarrativeRichnessKernels | None:
    if context is None:
        return None
    if isinstance(context, NarrativeRichnessKernels):
        return context
    if isinstance(context, dict):
        return NarrativeRichnessKernels.model_validate(context)
    return None


def narrative_richness_context_from_metadata(
    metadata: dict | None,
) -> NarrativeRichnessKernels | None:
    data = metadata if isinstance(metadata, dict) else {}
    explicit = data.get("narrative_richness_context") or data.get(
        "narrative_richness_kernels"
    )
    if isinstance(explicit, dict):
        return NarrativeRichnessKernels.model_validate(explicit)

    payload: dict[str, object] = {}
    for key in (
        "geography_kernel",
        "cultural_texture_module",
        "ensemble_arc_kernel",
        "mystery_anchor_kernel",
        "ethical_dilemma_kernel",
        "lineage_kernel",
        "crowd_scene",
        "zeitgeist_contract",
        "meta_layer_contract",
    ):
        if data.get(key) is not None:
            payload[key] = data[key]

    worldview = data.get("worldview_kernel")
    story_design = data.get("story_design_kernel")
    if isinstance(story_design, dict) and not isinstance(worldview, dict):
        worldview = story_design.get("worldview_kernel")
    if isinstance(worldview, dict):
        for key in (
            "cultural_texture_module",
            "calendar_module",
            "religious_organization_module",
            "honorific_system_module",
        ):
            if worldview.get(key) is not None and key not in payload:
                payload[key] = worldview[key]

    if not payload:
        return None
    return NarrativeRichnessKernels.model_validate(payload)


def render_narrative_richness_prompt_block(
    context: NarrativeRichnessKernels | dict | None,
    *,
    chapter_no: int | None = None,
    current_region: str | None = None,
    palette_count: int = 3,
) -> str:
    kernels = _coerce_context(context)
    if kernels is None:
        return ""

    parts: list[str] = ["## Narrative Richness Kernel"]
    geography = render_geography_prompt_block(
        kernels.geography_kernel,
        current_region=current_region,
    )
    if geography:
        parts.append(geography)
    if kernels.cultural_texture_module is not None and chapter_no is not None:
        picks = pick_palette_items_for_chapter(
            kernels.cultural_texture_module,
            chapter_no=chapter_no,
            count=palette_count,
        )
        if picks:
            lines = ["### Cultural Texture Module", "- Required sensory anchors:"]
            lines.extend(
                f"  - {item.name} ({item.category}): {item.sensory_hook}; {item.class_signal}"
                for item in picks
            )
            if kernels.cultural_texture_module.daily_rituals:
                lines.append(
                    "- Daily ritual candidates: "
                    + ", ".join(kernels.cultural_texture_module.daily_rituals[:3])
                )
            if kernels.cultural_texture_module.aesthetic_zeitgeist:
                lines.append(
                    f"- Aesthetic zeitgeist: {kernels.cultural_texture_module.aesthetic_zeitgeist}"
                )
            parts.append("\n".join(lines))
    ensemble = render_ensemble_arc_prompt_block(kernels.ensemble_arc_kernel)
    if ensemble:
        parts.append(ensemble)
    mystery = render_mystery_anchor_prompt_block(kernels.mystery_anchor_kernel)
    if mystery:
        parts.append(mystery)
    ethical = render_ethical_dilemma_prompt_block(
        kernels.ethical_dilemma_kernel,
        chapter_no=chapter_no,
    )
    if ethical:
        parts.append(ethical)
    lineage = render_lineage_prompt_block(kernels.lineage_kernel)
    if lineage:
        parts.append(lineage)
    crowd = render_crowd_scene_prompt_block(kernels.crowd_scene)
    if crowd:
        parts.append(crowd)
    if kernels.zeitgeist_contract is not None:
        volume = 1 if chapter_no is None else max(1, ((chapter_no - 1) // 20) + 1)
        zeitgeist = kernels.zeitgeist_contract
        parts.append(
            "\n".join(
                [
                    "### Zeitgeist Contract",
                    f"- Label: {zeitgeist.label}",
                    f"- Core anxiety: {zeitgeist.core_anxiety}",
                    f"- Dominant aspiration: {zeitgeist.dominant_aspiration}",
                    f"- Volume injection: {zeitgeist.injection_for_volume(volume)}",
                ]
            )
        )
    meta = render_meta_layer_prompt_block(kernels.meta_layer_contract)
    if meta:
        parts.append(meta)

    return "\n\n".join(part for part in parts if part.strip()) if len(parts) > 1 else ""
