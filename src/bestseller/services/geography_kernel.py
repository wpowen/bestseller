from __future__ import annotations

from bestseller.domain.geography import GeographyKernel


def render_geography_prompt_block(
    kernel: GeographyKernel | dict | None,
    *,
    current_region: str | None = None,
    max_adjacent: int = 3,
) -> str:
    if kernel is None:
        return ""
    if isinstance(kernel, dict):
        kernel = GeographyKernel.model_validate(kernel)

    region_name = current_region or kernel.protagonist_current
    region = kernel.region_by_name(region_name)
    if region is None:
        return ""

    lines = [
        "### Geography Kernel",
        f"- Current region: {region.name}",
        f"- Climate/terrain: {region.climate} / {region.terrain}",
        f"- Demographics: {region.demographics}",
        f"- Economy: {', '.join(region.surface_economy)}",
        f"- Cultural signature: {region.cultural_signature}",
    ]
    adjacent = kernel.adjacent_regions(region.name)[:max_adjacent]
    if adjacent:
        lines.append("- Adjacent regions:")
        for item in adjacent:
            route = kernel.route_between(region.name, item.name)
            route_text = ""
            if route is not None:
                hazards = f"; hazards={', '.join(route.hazard_kinds)}" if route.hazard_kinds else ""
                route_text = (
                    f" via {route.kind}, {route.days_typical} days, "
                    f"hazard={route.hazard_level}{hazards}"
                )
            lines.append(f"  - {item.name}: {item.cultural_signature}{route_text}")
    return "\n".join(lines)

