"""One-off: retarget xianxia-upgrade-1776137730 from 1200 chapters to 230.

Run with the same env as the main service (so DB url + settings load correctly):

    python scripts/retarget_xianxia_to_200.py

(File name kept as-is for git history continuity; the actual target value is
defined by NEW_TARGET below.)

It updates exactly one column on the projects row and prints before/after.
Quality bar and other settings are intentionally untouched.

Revision log:
- 2026-05-28 early: NEW_TARGET=200 (based on assumption ch186 was the last published).
- 2026-05-28 late : NEW_TARGET=230 (actual last published is ch216; finale is ch217-230).
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from bestseller.infra.db.models import ProjectModel
from bestseller.infra.db.session import session_scope

SLUG = "xianxia-upgrade-1776137730"
NEW_TARGET = 230


async def main() -> None:
    async with session_scope() as session:
        result = await session.execute(
            select(ProjectModel).where(ProjectModel.slug == SLUG)
        )
        project = result.scalar_one_or_none()
        if project is None:
            raise SystemExit(f"Project '{SLUG}' not found in DB")

        before = project.target_chapters
        if before == NEW_TARGET:
            print(f"target_chapters already {NEW_TARGET}, no change")
            return

        project.target_chapters = NEW_TARGET
        await session.commit()
        print(f"target_chapters: {before} -> {NEW_TARGET}  (slug={SLUG})")


if __name__ == "__main__":
    asyncio.run(main())
