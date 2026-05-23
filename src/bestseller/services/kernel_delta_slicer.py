from __future__ import annotations

from collections.abc import Iterable
import json
from uuid import UUID

from bestseller.domain.cultural_texture import CulturalTextureModule
from bestseller.domain.ensemble_arc import EnsembleArcKernel
from bestseller.domain.geography import GeographyKernel, Region, RouteEdge
from bestseller.domain.mystery_anchor import MysteryAnchorKernel
from bestseller.services.kernel_composer import NarrativeRichnessKernels


class KernelDeltaSlicer:
    """Trim narrative-richness kernels down to a chapter-local prompt slice."""

    def __init__(self, *, max_tokens: int = 800) -> None:
        self.max_tokens = max_tokens

    def slice_for_chapter(
        self,
        context: NarrativeRichnessKernels,
        *,
        chapter_no: int,
        current_region: str | None = None,
        active_character_ids: Iterable[str | UUID] = (),
        recent_palette_terms: Iterable[str] = (),
    ) -> NarrativeRichnessKernels:
        sliced = NarrativeRichnessKernels(
            geography_kernel=_slice_geography(context.geography_kernel, current_region),
            cultural_texture_module=_slice_culture(
                context.cultural_texture_module,
                chapter_no=chapter_no,
                recent_palette_terms=set(recent_palette_terms),
            ),
            ensemble_arc_kernel=_slice_ensemble(
                context.ensemble_arc_kernel,
                active_character_ids={str(item) for item in active_character_ids},
            ),
            mystery_anchor_kernel=_slice_mystery(
                context.mystery_anchor_kernel,
                chapter_no=chapter_no,
            ),
            ethical_dilemma_kernel=context.ethical_dilemma_kernel,
            lineage_kernel=context.lineage_kernel,
            crowd_scene=context.crowd_scene,
            zeitgeist_contract=context.zeitgeist_contract,
            meta_layer_contract=context.meta_layer_contract,
        )
        return _fit_token_budget(sliced, max_tokens=self.max_tokens)


def estimate_kernel_tokens(context: NarrativeRichnessKernels) -> int:
    payload = context.model_dump(mode="json", exclude_none=True)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return max(1, len(raw) // 4)


def _slice_geography(
    geography: GeographyKernel | None,
    current_region: str | None,
) -> GeographyKernel | None:
    if geography is None:
        return None
    focus = current_region or geography.protagonist_current
    selected_names = {focus}
    selected_names.update(region.name for region in geography.adjacent_regions(focus))
    for fallback in (
        geography.capital_region,
        geography.protagonist_origin,
        geography.protagonist_current,
    ):
        if fallback:
            selected_names.add(fallback)
        if len(selected_names) >= 3:
            break
    for region in geography.regions:
        selected_names.add(region.name)
        if len(selected_names) >= 3:
            break

    regions = [region for region in geography.regions if region.name in selected_names]
    routes = [
        route
        for route in geography.routes
        if route.region_a in selected_names and route.region_b in selected_names
    ]
    routes = _ensure_minimum_routes(routes, regions, geography.routes)
    return GeographyKernel(
        regions=regions,
        routes=routes,
        capital_region=(
            geography.capital_region
            if geography.capital_region in {region.name for region in regions}
            else None
        ),
        protagonist_origin=(
            geography.protagonist_origin
            if geography.protagonist_origin in {region.name for region in regions}
            else regions[0].name
        ),
        protagonist_current=(
            geography.protagonist_current
            if geography.protagonist_current in {region.name for region in regions}
            else regions[0].name
        ),
    )


def _ensure_minimum_routes(
    routes: list[RouteEdge],
    regions: list[Region],
    original_routes: list[RouteEdge],
) -> list[RouteEdge]:
    selected_names = {region.name for region in regions}
    for route in original_routes:
        if len(routes) >= 2:
            break
        if route.region_a in selected_names and route.region_b in selected_names:
            if route not in routes:
                routes.append(route)
    return routes


def _slice_culture(
    culture: CulturalTextureModule | None,
    *,
    chapter_no: int,
    recent_palette_terms: set[str],
) -> CulturalTextureModule | None:
    if culture is None:
        return None
    palette = [
        item for item in culture.palette if item.name not in recent_palette_terms
    ] or list(culture.palette)
    offset = (chapter_no - 1) % len(palette)
    rotated = [*palette[offset:], *palette[:offset]]
    for item in culture.palette:
        if len(rotated) >= 8:
            break
        if item not in rotated:
            rotated.append(item)
    return culture.model_copy(update={"palette": rotated[: max(8, min(len(rotated), 12))]})


def _slice_ensemble(
    ensemble: EnsembleArcKernel | None,
    *,
    active_character_ids: set[str],
) -> EnsembleArcKernel | None:
    if ensemble is None:
        return None
    if not active_character_ids:
        return ensemble.model_copy(update={"arcs": ensemble.arcs[:3]})
    arcs = [arc for arc in ensemble.arcs if str(arc.owner_id) in active_character_ids]
    return ensemble.model_copy(update={"arcs": arcs})


def _slice_mystery(
    mystery: MysteryAnchorKernel | None,
    *,
    chapter_no: int,
) -> MysteryAnchorKernel | None:
    if mystery is None:
        return None
    anchors = [
        anchor
        for anchor in mystery.anchors
        if anchor.final_payoff_chapter_range[0] - 5
        <= chapter_no
        <= anchor.final_payoff_chapter_range[1] + 5
    ]
    if not anchors:
        anchors = mystery.anchors[:1]
    return mystery.model_copy(update={"anchors": anchors[:3]})


def _fit_token_budget(
    context: NarrativeRichnessKernels,
    *,
    max_tokens: int,
) -> NarrativeRichnessKernels:
    out = context
    for field_name in (
        "meta_layer_contract",
        "zeitgeist_contract",
        "crowd_scene",
        "lineage_kernel",
        "ethical_dilemma_kernel",
        "mystery_anchor_kernel",
        "ensemble_arc_kernel",
    ):
        if estimate_kernel_tokens(out) <= max_tokens:
            break
        out = out.model_copy(update={field_name: None})
    return out
