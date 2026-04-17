"""Repair corrupted role/age fields in planning artifacts.

This script fixes the specific corruption path where volume-level cast
expansion writes a full sentence into ``role`` and that sentence later gets
merged into ``cast_spec``. It repairs both:

1. ``cast_spec`` artifacts whose character ``role`` / ``age`` fields are no
   longer safe structured values.
2. ``volume_cast_expansion`` artifacts whose ``new_characters.role`` or
   ``character_evolutions[].changes.role`` fields contain sentence-shaped
   evolution text, or whose age fields contain prose like ``late 40s``.

Usage::

    .venv/bin/python -m scripts.repair_cast_spec_roles --dry-run
    .venv/bin/python -m scripts.repair_cast_spec_roles --apply
    .venv/bin/python -m scripts.repair_cast_spec_roles --apply --project-slug superhero-fiction-1776147970
"""

from __future__ import annotations

import argparse
import asyncio
import copy
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select

from bestseller.domain.enums import ArtifactType
from bestseller.domain.story_bible import (
    is_safe_character_role_label,
    normalize_character_age,
    normalize_character_role_label,
)
from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel
from bestseller.infra.db.session import create_session_factory
from bestseller.services.story_bible import parse_cast_spec_input
from bestseller.settings import load_settings


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _repair_note(current_metadata: Any, *, raw_role: str, repaired_role: str, normalized_role: str) -> dict[str, Any]:
    metadata = copy.deepcopy(_mapping(current_metadata))
    metadata["repaired_role_text"] = raw_role
    metadata["repaired_role_label"] = repaired_role
    if normalized_role and normalized_role != raw_role:
        metadata["repaired_role_normalized_label"] = normalized_role
    return metadata


def _repair_age_note(
    current_metadata: Any,
    *,
    raw_age: Any,
    normalized_age: int | None,
) -> dict[str, Any]:
    metadata = copy.deepcopy(_mapping(current_metadata))
    raw_text = str(raw_age).strip()
    if raw_text:
        metadata["repaired_age_text"] = raw_text
    if normalized_age is not None and raw_text != str(normalized_age):
        metadata["repaired_age_normalized"] = normalized_age
    return metadata


def _repair_age_field(
    payload: dict[str, Any],
    *,
    stats: Counter[str],
    stat_key: str,
) -> dict[str, Any]:
    repaired = copy.deepcopy(payload)
    raw_age = repaired.get("age")
    if raw_age is None or raw_age == "":
        repaired.pop("age", None)
        return repaired

    normalized_age = normalize_character_age(raw_age)
    if normalized_age is None:
        repaired["metadata"] = _repair_age_note(
            repaired.get("metadata"),
            raw_age=raw_age,
            normalized_age=None,
        )
        repaired.pop("age", None)
        stats[stat_key] += 1
        return repaired

    if isinstance(raw_age, str) and raw_age.strip() != str(normalized_age):
        repaired["metadata"] = _repair_age_note(
            repaired.get("metadata"),
            raw_age=raw_age,
            normalized_age=normalized_age,
        )
        stats[stat_key] += 1
    repaired["age"] = normalized_age
    return repaired


def _repair_supporting_cast_character(
    payload: dict[str, Any],
    *,
    previous_roles_by_name: dict[str, str],
    stats: Counter[str],
) -> dict[str, Any]:
    repaired = copy.deepcopy(payload)
    name = _string(repaired.get("name"))
    raw_role = _string(repaired.get("role"))

    if raw_role and is_safe_character_role_label(raw_role):
        repaired["role"] = normalize_character_role_label(raw_role, fallback="supporting")
    else:
        fallback_role = previous_roles_by_name.get(name) or "supporting"
        normalized_role = normalize_character_role_label(raw_role, fallback=fallback_role)
        if raw_role:
            repaired["metadata"] = _repair_note(
                repaired.get("metadata"),
                raw_role=raw_role,
                repaired_role=fallback_role,
                normalized_role=normalized_role,
            )
            stats["cast_spec_supporting_roles_repaired"] += 1
        repaired["role"] = fallback_role

    if name and isinstance(repaired.get("role"), str) and repaired["role"].strip():
        previous_roles_by_name[name] = repaired["role"].strip()
    return _repair_age_field(
        repaired,
        stats=stats,
        stat_key="cast_spec_character_ages_repaired",
    )


def _repair_primary_character(
    payload: Any,
    *,
    stats: Counter[str],
) -> dict[str, Any]:
    repaired = copy.deepcopy(_mapping(payload))
    if not repaired:
        return repaired
    return _repair_age_field(
        repaired,
        stats=stats,
        stat_key="cast_spec_character_ages_repaired",
    )


def repair_cast_spec_content(
    content: Any,
    *,
    previous_roles_by_name: dict[str, str],
    stats: Counter[str],
) -> dict[str, Any]:
    repaired = copy.deepcopy(_mapping(content))
    if not repaired:
        return repaired

    if repaired.get("protagonist") is not None:
        repaired["protagonist"] = _repair_primary_character(
            repaired.get("protagonist"),
            stats=stats,
        )
    if repaired.get("antagonist") is not None:
        repaired["antagonist"] = _repair_primary_character(
            repaired.get("antagonist"),
            stats=stats,
        )
    repaired["supporting_cast"] = [
        _repair_supporting_cast_character(
            item,
            previous_roles_by_name=previous_roles_by_name,
            stats=stats,
        )
        for item in _mapping_list(repaired.get("supporting_cast"))
    ]

    # Re-run the normal model validation to prove the repaired payload is still
    # loadable, but preserve safe historical protagonist/antagonist role text
    # instead of rewriting every old version into a normalized dump.
    parse_cast_spec_input(repaired)
    previous_roles_by_name.clear()
    for item in _mapping_list(repaired.get("supporting_cast")):
        name = _string(item.get("name"))
        role = _string(item.get("role"))
        if name and role:
            previous_roles_by_name[name] = role
    return repaired


def repair_volume_cast_expansion_content(
    content: Any,
    *,
    stats: Counter[str],
) -> dict[str, Any]:
    repaired = copy.deepcopy(_mapping(content))
    if not repaired:
        return repaired

    new_characters: list[Any] = []
    for item in repaired.get("new_characters", []) if isinstance(repaired.get("new_characters"), list) else []:
        if not isinstance(item, dict):
            new_characters.append(item)
            continue
        character = copy.deepcopy(item)
        raw_role = _string(character.get("role"))
        if raw_role and is_safe_character_role_label(raw_role):
            character["role"] = normalize_character_role_label(raw_role, fallback="supporting")
        else:
            normalized_role = normalize_character_role_label(raw_role, fallback="supporting")
            if raw_role:
                character["metadata"] = _repair_note(
                    character.get("metadata"),
                    raw_role=raw_role,
                    repaired_role="supporting",
                    normalized_role=normalized_role,
                )
                stats["volume_cast_new_roles_repaired"] += 1
            character["role"] = "supporting"
        character = _repair_age_field(
            character,
            stats=stats,
            stat_key="volume_cast_new_ages_repaired",
        )
        new_characters.append(character)
    repaired["new_characters"] = new_characters

    character_evolutions: list[Any] = []
    for item in repaired.get("character_evolutions", []) if isinstance(repaired.get("character_evolutions"), list) else []:
        if not isinstance(item, dict):
            character_evolutions.append(item)
            continue
        evolution = copy.deepcopy(item)
        changes = _mapping(evolution.get("changes"))
        if not changes and isinstance(evolution.get("changes"), list):
            notes = [note for note in evolution.get("changes") if isinstance(note, str) and note.strip()]
            if notes:
                evolution["changes"] = {
                    "evolution_notes": notes,
                }
                stats["volume_cast_evolution_note_lists_repaired"] += 1
                character_evolutions.append(evolution)
                continue
        raw_role = _string(changes.get("role"))
        if raw_role and not is_safe_character_role_label(raw_role):
            normalized_role = normalize_character_role_label(raw_role, fallback="supporting")
            changes.pop("role", None)
            changes.setdefault("role_evolution", raw_role)
            if normalized_role and normalized_role != raw_role:
                changes.setdefault("role_evolution_normalized_label", normalized_role)
            stats["volume_cast_evolution_roles_repaired"] += 1
        elif raw_role:
            changes["role"] = normalize_character_role_label(raw_role, fallback="supporting")
        if changes:
            changes = _repair_age_field(
                changes,
                stats=stats,
                stat_key="volume_cast_evolution_ages_repaired",
            )
        if changes:
            evolution["changes"] = changes
        character_evolutions.append(evolution)
    repaired["character_evolutions"] = character_evolutions
    return repaired


async def _apply(*, project_slugs: set[str], apply: bool, all_versions: bool) -> None:
    settings = load_settings()
    session_factory = create_session_factory(settings)
    stats: Counter[str] = Counter()
    repaired_rows: list[tuple[str, str, int]] = []
    previous_roles_by_project: dict[str, dict[str, str]] = defaultdict(dict)

    async with session_factory() as session:
        stmt = (
            select(PlanningArtifactVersionModel, ProjectModel.slug)
            .join(ProjectModel, ProjectModel.id == PlanningArtifactVersionModel.project_id)
            .where(
                PlanningArtifactVersionModel.artifact_type.in_(
                    (
                        ArtifactType.CAST_SPEC.value,
                        ArtifactType.VOLUME_CAST_EXPANSION.value,
                    )
                )
            )
            .order_by(
                ProjectModel.slug.asc(),
                PlanningArtifactVersionModel.artifact_type.asc(),
                PlanningArtifactVersionModel.version_no.asc(),
            )
        )
        if project_slugs:
            stmt = stmt.where(ProjectModel.slug.in_(sorted(project_slugs)))

        rows = (await session.execute(stmt)).all()
        latest_version_by_key: dict[tuple[str, str], int] = {}
        for artifact, slug in rows:
            key = (slug, artifact.artifact_type)
            latest_version_by_key[key] = max(latest_version_by_key.get(key, 0), artifact.version_no)

        for artifact, slug in rows:
            original = copy.deepcopy(_mapping(artifact.content))
            repaired = original
            if artifact.artifact_type == ArtifactType.CAST_SPEC.value:
                repaired = repair_cast_spec_content(
                    repaired,
                    previous_roles_by_name=previous_roles_by_project[slug],
                    stats=stats,
                )
            elif artifact.artifact_type == ArtifactType.VOLUME_CAST_EXPANSION.value:
                repaired = repair_volume_cast_expansion_content(repaired, stats=stats)

            should_write = all_versions or artifact.version_no == latest_version_by_key[(slug, artifact.artifact_type)]
            if repaired != original and should_write:
                repaired_rows.append((slug, artifact.artifact_type, artifact.version_no))
                stats["artifacts_repaired"] += 1
                if apply:
                    artifact.content = repaired

        if apply and repaired_rows:
            await session.commit()
        else:
            await session.rollback()

    if not repaired_rows:
        print("nothing to repair")
        return

    action = "applied" if apply else "would repair"
    print(f"{action} {len(repaired_rows)} artifact(s)")
    for slug, artifact_type, version_no in repaired_rows:
        print(f"  {slug} [{artifact_type} v{version_no}]")
    print("summary:")
    for key in sorted(stats):
        print(f"  {key}: {stats[key]}")
    if not apply:
        print("(dry run — rerun with --apply to write changes)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-slug",
        action="append",
        default=[],
        help="Limit repair to one or more project slugs.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write repaired artifact content back to Postgres.",
    )
    parser.add_argument(
        "--all-versions",
        action="store_true",
        help="Repair every historical version instead of only the latest live artifact per type.",
    )
    args = parser.parse_args()
    asyncio.run(
        _apply(
            project_slugs=set(args.project_slug),
            apply=args.apply,
            all_versions=args.all_versions,
        )
    )


if __name__ == "__main__":
    main()
