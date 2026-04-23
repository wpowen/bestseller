"""Material Reference Renderer — §slug reference blocks for Planner / Drafter.

When ``enable_reference_style_generation`` is on, the Planner no longer
receives raw prompt-pack fragments (B-class script injection).  Instead it
receives a structured block listing all project materials by §slug so it
can reference them symbolically:

    ## 可引用物料（本书 Material Forge 已生成）
    §world_settings/blood-twins/yunhe-town：云和镇 — 荒山脚下的破败矿镇，暗藏…
    §power_systems/blood-twins/blood-vein-system：血脉三阶 — 以血液共鸣为根基…
    §character_templates/blood-twins/wang-qingfeng：王青峰 — 出身落魄武将世家…
    ※ 大纲、角色表、场景设计必须引用以上 slug，不得自创同类新名称

The "§dimension/project_id/slug" URN format is intentional:
- Planner outputs that contain §slugs can be validated against the DB
- Batch 3 novelty_critic will flag sections that *bypass* the reference system

Public API
----------
``render_material_reference_block(session, project_id, dimensions=None)``
    → str: the formatted block to inject into Planner/Drafter prompts.

``parse_material_refs(text)``
    → list[str]: extract all §dim/proj/slug tokens from LLM output.
"""

from __future__ import annotations

import logging
import re
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# URN regex: §dimension/project_id/slug  (dimension and slug are kebab-case;
# project_id may contain hyphens and digits from the UUID prefix)
_SLUG_URN_RE = re.compile(
    r"§([a-z][a-z0-9_]*)/([a-z0-9][a-z0-9-]*)/([a-z0-9][a-z0-9-]*)"
)

# Dimensions that Planner most needs (shown first in the reference block)
_PLANNER_PRIORITY_DIMS = [
    "world_settings",
    "power_systems",
    "factions",
    "character_archetypes",
    "character_templates",
    "plot_patterns",
    "scene_templates",
    "device_templates",
    "locale_templates",
    "thematic_motifs",
]


async def render_material_reference_block(
    session: AsyncSession,
    project_id: str,
    *,
    dimensions: Sequence[str] | None = None,
    max_per_dimension: int = 8,
    include_content_preview: bool = False,
) -> str:
    """Return the reference block to inject into Planner/Drafter prompts.

    Parameters
    ----------
    session:
        Active async session for ``project_materials`` reads.
    project_id:
        The project being planned/drafted.
    dimensions:
        If given, only include entries from these dimensions.
        Default: ``_PLANNER_PRIORITY_DIMS``.
    max_per_dimension:
        Cap on entries shown per dimension (to keep prompts from bloating).
    include_content_preview:
        If True, include a short content_json preview (first 120 chars).
        Useful for the Drafter; the Planner usually doesn't need full detail.
    """
    from bestseller.infra.db.models import ProjectMaterialModel  # noqa: PLC0415

    dims_to_show = list(dimensions) if dimensions else _PLANNER_PRIORITY_DIMS

    entries_by_dim: dict[str, list[tuple[str, str, str]]] = {}
    for dim in dims_to_show:
        result = await session.execute(
            select(ProjectMaterialModel)
            .where(
                ProjectMaterialModel.project_id == project_id,
                ProjectMaterialModel.material_type == dim,
                ProjectMaterialModel.status == "active",
            )
            .limit(max_per_dimension)
            .order_by(ProjectMaterialModel.id)
        )
        rows = result.scalars().all()
        if rows:
            items: list[tuple[str, str, str]] = []
            for row in rows:
                urn = f"§{dim}/{project_id}/{row.slug}"
                content_hint = ""
                if include_content_preview:
                    try:
                        import json  # noqa: PLC0415
                        preview = json.dumps(row.content_json, ensure_ascii=False)[:120]
                        content_hint = f" [{preview}]"
                    except Exception:  # noqa: BLE001
                        pass
                items.append((urn, row.name, row.narrative_summary))
            entries_by_dim[dim] = items

    if not entries_by_dim:
        return ""

    lines = [
        "## 可引用物料（Material Forge 已为本书生成，勿另创同类）",
        "",
    ]
    for dim in dims_to_show:
        items = entries_by_dim.get(dim)
        if not items:
            continue
        lines.append(f"### {dim}")
        for urn, name, summary in items:
            summary_short = summary[:80] + ("…" if len(summary) > 80 else "")
            lines.append(f"  {urn}：{name} — {summary_short}")
        lines.append("")

    lines += [
        "※ 大纲/人物表/场景方案中凡涉及以上条目，**必须引用对应 §slug**，",
        "   不得另行创造同功能的新名称。未列出的内容可自由创作。",
    ]
    return "\n".join(lines)


def parse_material_refs(text: str) -> list[str]:
    """Extract all §dim/project/slug URN tokens from LLM output.

    Returns a deduplicated list preserving order of first occurrence.
    """
    seen: set[str] = set()
    result: list[str] = []
    for m in _SLUG_URN_RE.finditer(text):
        urn = m.group(0)
        if urn not in seen:
            seen.add(urn)
            result.append(urn)
    return result


async def list_project_materials(
    session: AsyncSession,
    project_id: str,
    *,
    material_type: str | None = None,
    status: str = "active",
) -> list[dict[str, object]]:
    """Return a minimal list-of-dicts for all project materials.

    Useful for Forges that want to check what's already been forged, and
    for the reference block renderer.
    """
    from bestseller.infra.db.models import ProjectMaterialModel  # noqa: PLC0415

    q = select(ProjectMaterialModel).where(
        ProjectMaterialModel.project_id == project_id,
        ProjectMaterialModel.status == status,
    )
    if material_type:
        q = q.where(ProjectMaterialModel.material_type == material_type)

    result = await session.execute(q.order_by(ProjectMaterialModel.id))
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "material_type": r.material_type,
            "slug": r.slug,
            "name": r.name,
            "narrative_summary": r.narrative_summary,
            "source_library_ids": r.source_library_ids_json or [],
            "variation_notes": r.variation_notes,
            "status": r.status,
        }
        for r in rows
    ]
