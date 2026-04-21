#!/usr/bin/env python3
"""Retroactive repair: restore per-volume antagonist routing.

Root cause this script addresses
--------------------------------
Across every existing multi-volume project in the database we observed:

  * Every antagonist_plan row carries the same ``antagonist_label`` — the
    primary antagonist's name — regardless of which volume the plan scopes.
  * No antagonist-role ``character`` row has ``active_volumes`` populated
    in ``metadata_json``.

This happens because ``persist_cast_spec`` never cross-referenced
``cast_spec.antagonist_forces[].character_ref`` → ``character.metadata_json``,
so ``narrative._build_antagonist_plan_specs`` read an empty
``metadata.active_volumes`` for every non-primary antagonist and fell
through to the primary. The fix that now lives in code
(``services/story_bible.py``) applies going forward; this script is the
retroactive half — it walks the saved ``cast_spec`` artifact for each
project, backfills character ``active_volumes``, then regenerates the
``antagonist_plans`` routing so each volume resolves to the correct
character and label.

Usage
-----

    # Dry run across every project
    scripts/repair_antagonist_routing.py --all --dry-run

    # Apply to one project
    scripts/repair_antagonist_routing.py --project-id <uuid>

    # Apply to every project
    scripts/repair_antagonist_routing.py --all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from bestseller.infra.db.models import (  # noqa: E402
    AntagonistPlanModel,
    ChapterModel,
    CharacterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    VolumeModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402


async def _load_latest_cast_spec(
    session: AsyncSession, project_id: UUID
) -> dict | None:
    stmt = (
        select(PlanningArtifactVersionModel)
        .where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type == "cast_spec",
        )
        .order_by(PlanningArtifactVersionModel.version_no.desc())
        .limit(1)
    )
    row = (await session.scalars(stmt)).first()
    if row is None:
        return None
    content = row.content
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return None
    return content if isinstance(content, dict) else None


def _build_force_map(cast_spec: dict) -> dict[str, list[int]]:
    """name -> sorted active_volumes from antagonist_forces.character_ref."""
    out: dict[str, set[int]] = {}
    for force in cast_spec.get("antagonist_forces") or []:
        if not isinstance(force, dict):
            continue
        ref = (force.get("character_ref") or "").strip()
        if not ref:
            continue
        bucket = out.setdefault(ref, set())
        for vol in force.get("active_volumes") or []:
            if isinstance(vol, int) and vol > 0:
                bucket.add(vol)
    return {name: sorted(vols) for name, vols in out.items() if vols}


def _cast_spec_is_pathological(cast_spec: dict, volume_count: int) -> bool:
    """Detect the 'cloned force' failure mode — every antagonist_forces entry
    shares a single character_ref and/or a single name, which means a prior
    repair pass cloned one force N times instead of generating distinct forces.

    In that case ``_build_force_map`` would collapse to one key covering all
    volumes, so backfilling alone doesn't help. We need to redistribute
    volumes across the supporting_cast's antagonist entries.
    """

    forces = [f for f in (cast_spec.get("antagonist_forces") or []) if isinstance(f, dict)]
    if len(forces) < 2:
        return False
    refs = {(f.get("character_ref") or "").strip() for f in forces}
    names = {(f.get("name") or "").strip() for f in forces}
    cloned = (len(refs - {""}) <= 1) or (len(names - {""}) <= 1)
    if not cloned:
        return False
    # Only redistribute if we actually have multiple antagonist-role chars
    # in supporting_cast to redistribute to.
    antags_in_cast = [
        c for c in (cast_spec.get("supporting_cast") or [])
        if isinstance(c, dict) and "antag" in (str(c.get("role") or "").lower())
    ]
    return len(antags_in_cast) + 1 >= 2 and volume_count >= 2


def _redistribute_forces(
    cast_spec: dict, *, volume_count: int
) -> dict[str, list[int]]:
    """Split 1..volume_count evenly across supporting_cast antagonists.

    The primary antagonist (``cast_spec.antagonist``) gets the final slice
    so the book's narrative arc still converges on them at the climax.
    Supporting-cast antagonists fill the earlier slices in listed order.
    """

    supporting = [
        c for c in (cast_spec.get("supporting_cast") or [])
        if isinstance(c, dict) and "antag" in (str(c.get("role") or "").lower())
    ]
    primary_name = ""
    primary = cast_spec.get("antagonist")
    if isinstance(primary, dict):
        primary_name = (primary.get("name") or "").strip()

    # Roster ordered: supporting antagonists first, primary last.
    roster: list[str] = []
    for s in supporting:
        name = (s.get("name") or "").strip()
        if name and name != primary_name and name not in roster:
            roster.append(name)
    if primary_name and primary_name not in roster:
        roster.append(primary_name)
    if not roster:
        return {}

    # Even partition of 1..volume_count across roster. Primary keeps tail.
    n = len(roster)
    base = volume_count // n
    rem = volume_count % n
    out: dict[str, list[int]] = {}
    start = 1
    for idx, name in enumerate(roster):
        size = base + (1 if idx < rem else 0)
        if size <= 0:
            continue
        vols = list(range(start, start + size))
        start += size
        out[name] = vols
    return out


def _rewrite_cast_spec_forces(
    cast_spec: dict, distribution: dict[str, list[int]]
) -> dict:
    """Produce a new cast_spec with antagonist_forces rebuilt from the
    distribution. Each character gets exactly one force entry named after
    them (distinct names, distinct character_refs, disjoint active_volumes)."""

    old_forces = cast_spec.get("antagonist_forces") or []
    first_force = old_forces[0] if old_forces and isinstance(old_forces[0], dict) else {}
    threat_desc = first_force.get("threat_description") or ""
    escalation = first_force.get("escalation_path") or ""
    relationship = first_force.get("relationship_to_protagonist") or ""
    new_forces: list[dict] = []
    for name, vols in distribution.items():
        new_forces.append(
            {
                "name": f"{name}·对抗线",
                "force_type": "character",
                "character_ref": name,
                "active_volumes": vols,
                "threat_description": threat_desc
                or f"{name} 在第 {vols[0]}–{vols[-1]} 卷的主要威胁来源。",
                "escalation_path": escalation
                or f"{name} 的压迫手段随主角成长同步升级。",
                "relationship_to_protagonist": relationship
                or f"主角必须先应对 {name} 的阶段性压力，才能推进主线。",
            }
        )
    new_spec = dict(cast_spec)
    new_spec["antagonist_forces"] = new_forces
    return new_spec


async def _persist_repaired_cast_spec(
    session: AsyncSession,
    *,
    project_id: UUID,
    new_spec: dict,
) -> None:
    """Append a new cast_spec artifact version recording the repair."""

    latest = (
        await session.scalars(
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.project_id == project_id,
                PlanningArtifactVersionModel.artifact_type == "cast_spec",
            )
            .order_by(PlanningArtifactVersionModel.version_no.desc())
            .limit(1)
        )
    ).first()
    next_version = (latest.version_no if latest is not None else 0) + 1
    session.add(
        PlanningArtifactVersionModel(
            project_id=project_id,
            artifact_type="cast_spec",
            scope_ref_id=None,
            version_no=next_version,
            status="active",
            schema_version=(latest.schema_version if latest else "1"),
            content=new_spec,
            source_run_id=None,
            notes="repair_antagonist_routing: redistributed antagonist_forces",
            created_by="script:repair_antagonist_routing",
        )
    )


async def _backfill_character_active_volumes(
    session: AsyncSession,
    *,
    project_id: UUID,
    force_map: dict[str, list[int]],
    dry_run: bool,
) -> int:
    if not force_map:
        return 0
    chars = await session.scalars(
        select(CharacterModel).where(CharacterModel.project_id == project_id)
    )
    updated = 0
    for char in chars:
        target = force_map.get(char.name)
        if not target:
            continue
        meta = dict(char.metadata_json or {})
        existing = meta.get("active_volumes") or []
        merged = sorted(set(existing) | set(target)) if isinstance(existing, list) else list(target)
        # Promote supporting-cast entries referenced by antagonist_forces to
        # role='antagonist' so narrative._build_antagonist_plan_specs can
        # discover them. Without this, only the primary antagonist is
        # routable and every plan collapses onto it.
        needs_role_promotion = char.role not in ("antagonist", "protagonist")
        if merged == existing and not needs_role_promotion:
            continue
        if not dry_run:
            meta["active_volumes"] = merged
            char.metadata_json = meta
            if needs_role_promotion:
                char.role = "antagonist"
        updated += 1
    return updated


def _label_by_volume(
    volumes: list[VolumeModel],
    antagonists: list[CharacterModel],
    primary: CharacterModel | None,
) -> dict[int, tuple[str, UUID | None]]:
    """Resolve (label, character_id) for each volume from character metadata."""

    out: dict[int, tuple[str, UUID | None]] = {}
    extras = [c for c in antagonists if primary is None or c.id != primary.id]
    for volume in volumes:
        resolved_char: CharacterModel | None = None
        for extra in extras:
            meta = extra.metadata_json if isinstance(extra.metadata_json, dict) else {}
            if volume.volume_number in (meta.get("active_volumes") or []):
                resolved_char = extra
                break
        if resolved_char is None and primary is not None:
            prim_meta = primary.metadata_json if isinstance(primary.metadata_json, dict) else {}
            prim_active = prim_meta.get("active_volumes") or []
            if not prim_active or volume.volume_number in prim_active:
                resolved_char = primary
        if resolved_char is None:
            resolved_char = primary
        if resolved_char is None:
            out[volume.volume_number] = ("未知反派", None)
        else:
            out[volume.volume_number] = (resolved_char.name, resolved_char.id)
    return out


async def _regen_antagonist_plans(
    session: AsyncSession,
    *,
    project_id: UUID,
    dry_run: bool,
) -> dict[str, int]:
    antagonists = (
        await session.scalars(
            select(CharacterModel)
            .where(
                CharacterModel.project_id == project_id,
                CharacterModel.role == "antagonist",
            )
        )
    ).all()
    if not antagonists:
        return {"plans_updated": 0, "plans_scanned": 0}

    primary = antagonists[0]
    volumes = (
        await session.scalars(
            select(VolumeModel)
            .where(VolumeModel.project_id == project_id)
            .order_by(VolumeModel.volume_number.asc())
        )
    ).all()

    resolved = _label_by_volume(list(volumes), list(antagonists), primary)

    plans = (
        await session.scalars(
            select(AntagonistPlanModel).where(
                AntagonistPlanModel.project_id == project_id
            )
        )
    ).all()

    plans_updated = 0
    for plan in plans:
        if plan.scope_volume_number is None:
            continue
        new_label, new_id = resolved.get(plan.scope_volume_number, (None, None))
        if not new_label:
            continue
        changed = False
        if plan.antagonist_label != new_label:
            changed = True
            if not dry_run:
                plan.antagonist_label = new_label
        if plan.antagonist_character_id != new_id:
            changed = True
            if not dry_run:
                plan.antagonist_character_id = new_id
        meta = dict(plan.metadata_json or {})
        if meta.get("antagonist_label") != new_label or meta.get("antagonist_character_id") != (
            str(new_id) if new_id else None
        ):
            changed = True
            if not dry_run:
                meta["antagonist_label"] = new_label
                meta["antagonist_character_id"] = str(new_id) if new_id else None
                plan.metadata_json = meta
        if changed:
            plans_updated += 1
    return {"plans_updated": plans_updated, "plans_scanned": len(plans)}


async def _count_volumes(session: AsyncSession, project_id: UUID) -> int:
    vols = await session.scalars(
        select(VolumeModel.volume_number).where(VolumeModel.project_id == project_id)
    )
    return len(list(vols))


async def repair_project(
    session: AsyncSession,
    project: ProjectModel,
    *,
    dry_run: bool,
) -> dict[str, object]:
    cast_spec = await _load_latest_cast_spec(session, project.id)
    if not cast_spec:
        return {
            "project_id": str(project.id),
            "slug": project.slug,
            "skipped": "no cast_spec artifact",
        }

    volume_count = await _count_volumes(session, project.id)
    redistributed = False
    redistribution: dict[str, list[int]] = {}
    if _cast_spec_is_pathological(cast_spec, volume_count):
        redistribution = _redistribute_forces(cast_spec, volume_count=volume_count)
        if redistribution:
            new_spec = _rewrite_cast_spec_forces(cast_spec, redistribution)
            if not dry_run:
                await _persist_repaired_cast_spec(
                    session, project_id=project.id, new_spec=new_spec
                )
            cast_spec = new_spec
            redistributed = True

    force_map = _build_force_map(cast_spec)
    chars_updated = await _backfill_character_active_volumes(
        session, project_id=project.id, force_map=force_map, dry_run=dry_run
    )
    if not dry_run:
        await session.flush()
    plan_stats = await _regen_antagonist_plans(
        session, project_id=project.id, dry_run=dry_run
    )
    if not dry_run:
        await session.flush()
    return {
        "project_id": str(project.id),
        "slug": project.slug,
        "title": project.title,
        "volume_count": volume_count,
        "redistributed": redistributed,
        "new_force_roster": list(redistribution.keys()) if redistributed else None,
        "force_map_size": len(force_map),
        "characters_updated": chars_updated,
        **plan_stats,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--project-id", type=str, help="UUID of one project to repair")
    group.add_argument("--all", action="store_true", help="Repair every project")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results: list[dict[str, object]] = []
    # Resolve the project list up-front in a short-lived session.
    async with session_scope() as scan_session:
        if args.all:
            projects = (
                await scan_session.scalars(
                    select(ProjectModel).order_by(ProjectModel.created_at.asc())
                )
            ).all()
        else:
            try:
                pid = UUID(args.project_id)
            except ValueError:
                print(f"Invalid UUID: {args.project_id}", file=sys.stderr)
                return 2
            project = await scan_session.get(ProjectModel, pid)
            if project is None:
                print(f"Project {pid} not found", file=sys.stderr)
                return 2
            projects = [project]
        project_ids = [(p.id, p.slug, p.title) for p in projects]

    # Each project commits independently so a lock timeout on one running
    # project (e.g. an active worker transaction) does not roll back repairs
    # on others.
    for pid, slug, title in project_ids:
        try:
            async with session_scope() as session:
                # Short statement timeout so we fail fast if a worker is
                # holding character/plan locks, rather than blocking.
                await session.execute(text("SET LOCAL lock_timeout = '3s'"))
                await session.execute(text("SET LOCAL statement_timeout = '15s'"))
                project = await session.get(ProjectModel, pid)
                if project is None:
                    print(
                        json.dumps(
                            {
                                "project_id": str(pid),
                                "slug": slug,
                                "skipped": "project disappeared",
                            },
                            ensure_ascii=False,
                        )
                    )
                    continue
                report = await repair_project(session, project, dry_run=args.dry_run)
                if not args.dry_run:
                    await session.commit()
                else:
                    await session.rollback()
        except Exception as exc:  # noqa: BLE001
            report = {
                "project_id": str(pid),
                "slug": slug,
                "title": title,
                "error": f"{type(exc).__name__}: {exc}",
            }
        results.append(report)
        print(json.dumps(report, ensure_ascii=False))

    print(json.dumps({"summary": {"projects": len(results)}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
