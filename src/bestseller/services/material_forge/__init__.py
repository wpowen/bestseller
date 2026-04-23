"""Material Forge — 5 forge agents that produce project-specific materials.

Each Forge reads from the global ``material_library``, runs an LLM
differentiation pass, and writes project-unique entries to
``project_materials``.

Forge pipeline (run in order so later forges can reference earlier outputs):
  1. WorldForge       — world_settings, factions, locale_templates
  2. PowerSystemForge — power_systems
  3. CharacterForge   — character_archetypes, character_templates
  4. PlotForge        — plot_patterns, scene_templates
  5. DeviceForge      — device_templates, thematic_motifs

Feature gate: ``settings.pipeline.enable_forge_pipeline``.

Usage::

    from bestseller.services.material_forge import forge_all_materials
    results = await forge_all_materials(session, project_id, genre, settings)
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.material_forge.base import ForgeResult, ProjectMaterial
from bestseller.services.material_forge.character_forge import CharacterForge
from bestseller.services.material_forge.device_forge import DeviceForge
from bestseller.services.material_forge.plot_forge import PlotForge
from bestseller.services.material_forge.power_forge import PowerSystemForge
from bestseller.services.material_forge.world_forge import WorldForge
from bestseller.settings import AppSettings

__all__ = [
    "forge_all_materials",
    "WorldForge",
    "PowerSystemForge",
    "CharacterForge",
    "PlotForge",
    "DeviceForge",
    "ForgeResult",
    "ProjectMaterial",
]

logger = logging.getLogger(__name__)

# Forge execution order — later forges receive earlier forges' outputs so
# they can maintain cross-forge consistency (e.g. characters reference the
# world, devices reference the power system).
_FORGE_ORDER = [
    WorldForge,
    PowerSystemForge,
    CharacterForge,
    PlotForge,
    DeviceForge,
]


async def forge_all_materials(
    session: AsyncSession,
    project_id: str,
    genre: str,
    settings: AppSettings,
    *,
    sub_genre: str | None = None,
    max_rounds_per_dimension: int = 10,
) -> list[ForgeResult]:
    """Run all 5 forges in order for *project_id*.

    Each forge receives the accumulated ``existing_materials`` dict from
    prior forges so it can maintain cross-forge consistency.

    Parameters
    ----------
    session:
        Active async session (used for library reads and project_materials writes).
    project_id:
        Project slug or UUID string — becomes the FK in ``project_materials``.
    genre:
        Primary genre string (e.g. "仙侠", "都市修仙").
    settings:
        Global :class:`AppSettings` — drives LLM role config.
    sub_genre:
        Optional sub-genre refinement forwarded to library queries.
    max_rounds_per_dimension:
        Hard cap on LLM tool-loop rounds per dimension.

    Returns
    -------
    list[ForgeResult]
        One :class:`ForgeResult` per dimension across all forges (14 dimensions
        total if all forges run to completion).
    """
    all_results: list[ForgeResult] = []
    # Accumulated cross-forge materials keyed by dimension
    existing: dict[str, list[ProjectMaterial]] = {}

    for forge_cls in _FORGE_ORDER:
        forge = forge_cls()
        logger.info(
            "forge_all_materials: running %s for project=%s genre=%s",
            forge_cls.__name__,
            project_id,
            genre,
        )
        try:
            results = await forge.run(
                session,
                project_id=project_id,
                genre=genre,
                settings=settings,
                sub_genre=sub_genre,
                existing_materials=existing,
                max_rounds=max_rounds_per_dimension,
            )
        except Exception:  # noqa: BLE001 — one forge failure must not abort entire pipeline
            logger.exception(
                "forge_all_materials: %s raised — skipping dimension(s) %s",
                forge_cls.__name__,
                forge.dimensions,
            )
            continue

        for result in results:
            all_results.append(result)
            # Accumulate for later forges
            existing.setdefault(result.dimension, []).extend(result.emitted)

    total_emitted = sum(r.emitted_count for r in all_results)
    logger.info(
        "forge_all_materials: done project=%s — %d results, %d total entries",
        project_id,
        len(all_results),
        total_emitted,
    )
    return all_results
