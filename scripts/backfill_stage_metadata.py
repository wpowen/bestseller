"""Backfill Stage 0 (+A/B/C/D) metadata into existing chapters / scenes / characters.

Runs the lightweight keyword classifiers from `stage_seed` over the actual
generated text (ChapterDraftVersionModel / SceneDraftVersionModel) and over
character bible fields, and writes the derived structured signatures into
`metadata_json`. Purely **additive** — keys already present in metadata_json
are never overwritten.

This lets Stage A-D diversity blocks consume history from a project whose
chapters were generated before Stage 0 landed.

Usage::

    .venv/bin/python -m scripts.backfill_stage_metadata \\
        --project-slug female-no-cp-1776303225

    .venv/bin/python -m scripts.backfill_stage_metadata \\
        --project-slug female-no-cp-1776303225 --dry-run

Idempotent: safe to re-run. A second run is a no-op for rows already enriched.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from typing import Any

from sqlalchemy import select

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    CharacterModel,
    SceneCardModel,
    SceneDraftVersionModel,
)
from bestseller.infra.db.session import create_session_factory
from bestseller.services.projects import get_project_by_slug
from bestseller.services.stage_seed import (
    seed_character_inner_structure,
    seed_chapter_metadata,
    seed_scene_metadata,
)
from bestseller.services.pacing_engine import (
    target_beat_for_chapter,
    target_tension_for_chapter,
)
from bestseller.settings import load_settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _load_current_chapter_text(session, chapter_id) -> str:
    row = await session.scalar(
        select(ChapterDraftVersionModel)
        .where(
            ChapterDraftVersionModel.chapter_id == chapter_id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
    )
    return (row.content_md if row else "") or ""


async def _load_current_scene_text(session, scene_id) -> str:
    row = await session.scalar(
        select(SceneDraftVersionModel)
        .where(
            SceneDraftVersionModel.scene_card_id == scene_id,
            SceneDraftVersionModel.is_current.is_(True),
        )
    )
    return (row.content_md if row else "") or ""


def _only_fill_missing(existing: dict[str, Any] | None, seed: dict[str, Any]) -> dict[str, Any]:
    """Return a merged dict; keys present (and non-None/non-empty) in existing win."""
    merged: dict[str, Any] = dict(existing or {})
    added: dict[str, Any] = {}
    for key, value in seed.items():
        if key in merged and merged[key] not in (None, "", [], {}):
            continue
        merged[key] = value
        added[key] = value
    return merged if added else dict(existing or {})


def _added_keys(before: dict[str, Any] | None, after: dict[str, Any]) -> list[str]:
    before = before or {}
    return [k for k in after if k not in before or before.get(k) != after.get(k)]


# ---------------------------------------------------------------------------
# Chapter backfill
# ---------------------------------------------------------------------------

async def _backfill_chapters(
    session,
    project_id,
    total_chapters: int,
    dry_run: bool,
) -> Counter:
    stats: Counter = Counter()
    chapters = list(await session.scalars(
        select(ChapterModel)
        .where(ChapterModel.project_id == project_id)
        .order_by(ChapterModel.chapter_number)
    ))
    stats["chapters_scanned"] = len(chapters)
    if not chapters:
        return stats

    for chapter in chapters:
        text = await _load_current_chapter_text(session, chapter.id)
        card: dict[str, Any] = {}
        meta = chapter.metadata_json or {}
        if isinstance(meta.get("if_card"), dict):
            card = dict(meta["if_card"])
        # Enrich card with actual tail of chapter text — hook usually last paragraph.
        if text:
            card.setdefault("next_chapter_hook", text[-800:])

        seed = seed_chapter_metadata(card, chapter.chapter_number, total_chapters)
        # Always ensure tension_score + beat_id reflect the target curve.
        if total_chapters > 0 and "tension_score" not in (meta or {}):
            seed.setdefault(
                "tension_score",
                float(target_tension_for_chapter(chapter.chapter_number, total_chapters)),
            )
            seed.setdefault(
                "beat_id",
                target_beat_for_chapter(chapter.chapter_number, total_chapters).beat_name,
            )

        merged = _only_fill_missing(meta, seed)
        if merged == (meta or {}):
            stats["chapters_skipped"] += 1
            continue

        stats["chapters_updated"] += 1
        for k in _added_keys(meta, merged):
            stats[f"chapter_field::{k}"] += 1
        if not dry_run:
            chapter.metadata_json = merged

    return stats


# ---------------------------------------------------------------------------
# Scene backfill
# ---------------------------------------------------------------------------

async def _backfill_scenes(
    session,
    project_id,
    dry_run: bool,
) -> Counter:
    stats: Counter = Counter()
    scenes = list(await session.scalars(
        select(SceneCardModel).where(SceneCardModel.project_id == project_id)
    ))
    stats["scenes_scanned"] = len(scenes)
    if not scenes:
        return stats

    for scene in scenes:
        text = await _load_current_scene_text(session, scene.id)
        meta = scene.metadata_json or {}
        card: dict[str, Any] = {}
        if isinstance(meta.get("if_card"), dict):
            card = dict(meta["if_card"])
        # Enrich card with the scene text so the classifier sees real prose,
        # not just the planning stub.
        card.setdefault("title", scene.title or card.get("title"))
        if text:
            card["chapter_goal"] = (card.get("chapter_goal") or "") + "\n" + text[:3000]

        seed = seed_scene_metadata(card)
        if not seed:
            stats["scenes_skipped"] += 1
            continue

        merged = _only_fill_missing(meta, seed)
        if merged == (meta or {}):
            stats["scenes_skipped"] += 1
            continue

        stats["scenes_updated"] += 1
        for k in _added_keys(meta, merged):
            stats[f"scene_field::{k}"] += 1
        if not dry_run:
            scene.metadata_json = merged

    return stats


# ---------------------------------------------------------------------------
# Character backfill
# ---------------------------------------------------------------------------

async def _backfill_characters(session, project_id, dry_run: bool) -> Counter:
    stats: Counter = Counter()
    characters = list(await session.scalars(
        select(CharacterModel).where(CharacterModel.project_id == project_id)
    ))
    stats["characters_scanned"] = len(characters)
    if not characters:
        return stats

    for character in characters:
        meta = character.metadata_json or {}
        if meta.get("inner_structure"):
            stats["characters_skipped"] += 1
            continue

        lta = meta.get("lie_truth_arc") if isinstance(meta.get("lie_truth_arc"), dict) else None
        # Character bible fields live as columns, not inside metadata_json.
        proxy = {
            "goal": character.goal,
            "fear": character.fear,
            "flaw": character.flaw,
            "secret": character.secret,
            "arc_trajectory": character.arc_trajectory,
            "arc_state": character.arc_state,
        }
        structure = seed_character_inner_structure(proxy, lie_truth_arc=lta)
        if structure is None:
            stats["characters_skipped"] += 1
            continue

        merged = dict(meta)
        merged["inner_structure"] = structure
        stats["characters_updated"] += 1
        if not dry_run:
            character.metadata_json = merged

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(project_slug: str, dry_run: bool) -> None:
    settings = load_settings()
    session_factory = create_session_factory(settings)

    async with session_factory() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise SystemExit(f"Project '{project_slug}' not found.")

        total_chapters = int(
            getattr(project, "target_chapters", None)
            or (project.metadata_json or {}).get("target_chapter_count")
            or 100
        )

        print(f"→ Backfilling project '{project_slug}' (target_chapters={total_chapters})")
        print(f"  dry_run={dry_run}")

        chapter_stats = await _backfill_chapters(
            session, project.id, total_chapters, dry_run
        )
        scene_stats = await _backfill_scenes(session, project.id, dry_run)
        character_stats = await _backfill_characters(session, project.id, dry_run)

        if not dry_run:
            await session.commit()

    def _print_stats(label: str, counter: Counter) -> None:
        print(f"\n[{label}]")
        for key in sorted(counter):
            print(f"  {key}: {counter[key]}")

    _print_stats("chapters", chapter_stats)
    _print_stats("scenes", scene_stats)
    _print_stats("characters", character_stats)

    if dry_run:
        print("\n(dry-run: nothing was committed)")
    else:
        print("\n✓ Committed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-slug", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.project_slug, args.dry_run))


if __name__ == "__main__":
    main()
