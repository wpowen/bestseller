"""Backfill missing/placeholder volume titles for a project.

Volumes whose title matches the generic ``第N卷`` / ``Volume N`` pattern
(inherited from the old fallback plan) are re-titled using the same
phase-based pool the planner now uses for fresh generations. The script
updates both the ``volumes`` table and the project's stored
``metadata.volume_plan`` JSON so future reads are consistent.

Usage::

    .venv/bin/python -m scripts.fix_volume_titles --project-slug xianxia-upgrade-1776137730
    .venv/bin/python -m scripts.fix_volume_titles --project-slug xianxia-upgrade-1776137730 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import re
from typing import Any

from sqlalchemy import select

from bestseller.infra.db.models import ProjectModel, VolumeModel
from bestseller.infra.db.session import create_session_factory
from bestseller.services.planner import _resolve_fallback_volume_title
from bestseller.settings import load_settings


_PLACEHOLDER_ZH = re.compile(r"^第\s*\d+\s*卷$")
_PLACEHOLDER_EN = re.compile(r"^Volume\s+\d+$", re.IGNORECASE)


def _is_placeholder_title(title: str | None) -> bool:
    if not title:
        return True
    stripped = title.strip()
    if not stripped:
        return True
    return bool(_PLACEHOLDER_ZH.match(stripped) or _PLACEHOLDER_EN.match(stripped))


def _new_titles(volumes_meta: list[dict[str, Any]], *, is_en: bool) -> dict[int, str]:
    """Return {volume_number: new_title} for volumes that need a fix."""
    phase_occurrence: dict[str, int] = {}
    used: set[str] = set()
    fixes: dict[int, str] = {}
    for entry in sorted(volumes_meta, key=lambda e: int(e.get("volume_number") or 0)):
        vn_raw = entry.get("volume_number")
        if not isinstance(vn_raw, int):
            continue
        phase = str(entry.get("conflict_phase") or "").strip() or "survival"
        current_title = str(entry.get("volume_title") or "").strip()
        occ = phase_occurrence.get(phase, 0)
        phase_occurrence[phase] = occ + 1
        if _is_placeholder_title(current_title):
            candidate = _resolve_fallback_volume_title(phase, occ, vn_raw, is_en=is_en)
            # Disambiguate against existing non-placeholder titles.
            while candidate in used:
                occ += 1
                candidate = _resolve_fallback_volume_title(phase, occ, vn_raw, is_en=is_en)
                if occ > 100:  # pragma: no cover - safety stop
                    candidate = (
                        f"Volume {vn_raw}" if is_en else f"第{vn_raw}卷"
                    )
                    break
            fixes[vn_raw] = candidate
            used.add(candidate)
        else:
            used.add(current_title)
    return fixes


async def _apply(project_slug: str, *, dry_run: bool) -> None:
    settings = load_settings()
    session_factory = create_session_factory(settings)
    async with session_factory() as session:
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == project_slug)
        )
        if project is None:
            raise SystemExit(f"project not found: {project_slug}")

        is_en = (project.language or "zh-CN").lower().startswith("en")
        metadata = dict(project.metadata_json or {})
        plan_payload = metadata.get("volume_plan")
        volumes_meta: list[dict[str, Any]]
        if isinstance(plan_payload, list):
            volumes_meta = [dict(e) for e in plan_payload if isinstance(e, dict)]
        elif isinstance(plan_payload, dict):
            volumes_meta = [
                dict(e)
                for e in (plan_payload.get("volumes") or [])
                if isinstance(e, dict)
            ]
        else:
            volumes_meta = []

        # Load DB rows so we can also fix volumes missing from metadata.
        volume_rows = (
            await session.scalars(
                select(VolumeModel)
                .where(VolumeModel.project_id == project.id)
                .order_by(VolumeModel.volume_number)
            )
        ).all()

        # Seed from DB when metadata has no volume_plan (still provides
        # volume_number + conflict_phase via metadata_json).
        if not volumes_meta and volume_rows:
            volumes_meta = [
                {
                    "volume_number": row.volume_number,
                    "volume_title": row.title,
                    "conflict_phase": (row.metadata_json or {}).get("conflict_phase", ""),
                }
                for row in volume_rows
            ]

        fixes = _new_titles(volumes_meta, is_en=is_en)
        if not fixes:
            print("nothing to fix")
            return

        print(f"{len(fixes)} volume title(s) will be updated:")
        for vn in sorted(fixes):
            print(f"  Volume {vn}: -> {fixes[vn]}")

        if dry_run:
            print("(dry run — no changes written)")
            return

        # Update volumes table rows.
        for row in volume_rows:
            new_title = fixes.get(row.volume_number)
            if new_title:
                row.title = new_title

        # Update project.metadata.volume_plan (array or {"volumes": [...]}).
        if isinstance(plan_payload, list):
            new_payload = [
                {**e, "volume_title": fixes.get(int(e.get("volume_number") or 0), e.get("volume_title"))}
                if isinstance(e, dict) else e
                for e in plan_payload
            ]
            metadata["volume_plan"] = new_payload
        elif isinstance(plan_payload, dict):
            new_volumes = [
                {**e, "volume_title": fixes.get(int(e.get("volume_number") or 0), e.get("volume_title"))}
                if isinstance(e, dict) else e
                for e in (plan_payload.get("volumes") or [])
            ]
            metadata["volume_plan"] = {**plan_payload, "volumes": new_volumes}
        project.metadata_json = metadata

        await session.commit()
        print("update committed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-slug", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(_apply(args.project_slug, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
