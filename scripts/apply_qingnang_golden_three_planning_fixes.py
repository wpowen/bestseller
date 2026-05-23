"""Repair golden-three planning metadata for 《青囊不语问阴阳》 only."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from sqlalchemy import select

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.models import ChapterModel, ProjectModel  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402

PROJECT_SLUG = "exorcist-detective-1778051012"
GOLDEN_THREE_HYPE = {
    1: ("reversal", 8.0),
    2: ("face_slap", 8.0),
    3: ("golden_finger_reveal", 8.0),
}


async def _apply(*, dry_run: bool) -> dict[str, object]:
    settings = load_settings()
    results: list[dict[str, object]] = []
    async with session_scope(settings) as session:
        project = (
            await session.scalars(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
        ).one()
        for chapter_number, (hype_type, intensity) in GOLDEN_THREE_HYPE.items():
            chapter = (
                await session.scalars(
                    select(ChapterModel).where(
                        ChapterModel.project_id == project.id,
                        ChapterModel.chapter_number == chapter_number,
                    )
                )
            ).one()
            before = {
                "hype_type": chapter.hype_type,
                "hype_intensity": chapter.hype_intensity,
            }
            changed = chapter.hype_type != hype_type or float(chapter.hype_intensity or 0.0) < intensity
            if changed and not dry_run:
                chapter.hype_type = hype_type
                chapter.hype_intensity = intensity
            results.append(
                {
                    "chapter_number": chapter_number,
                    "changed": changed,
                    "before": before,
                    "after": {
                        "hype_type": hype_type if changed else chapter.hype_type,
                        "hype_intensity": intensity if changed else chapter.hype_intensity,
                    },
                }
            )
    return {"project_slug": PROJECT_SLUG, "dry_run": dry_run, "results": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    import json

    print(json.dumps(asyncio.run(_apply(dry_run=not args.apply)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
