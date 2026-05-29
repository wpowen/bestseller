"""Reset ch217-230 status back to PLANNED so materialize-outline can overwrite.

We can't delete the chapter rows (canon_facts FK), so instead we just downgrade
status to PLANNED — the materialize path treats PLANNED chapters as mutable and
will overwrite the outline fields.

Run inside worker container:
    python /app/scripts/reset_finale_chapters.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select, update

from bestseller.domain.enums import ChapterStatus, SceneStatus
from bestseller.infra.db.models import ChapterModel, ProjectModel, SceneCardModel
from bestseller.infra.db.session import session_scope

SLUG = "xianxia-upgrade-1776137730"
RANGE = list(range(217, 231))


async def main() -> None:
    async with session_scope() as session:
        project = (
            await session.execute(
                select(ProjectModel).where(ProjectModel.slug == SLUG)
            )
        ).scalar_one()

        # Find chapter rows
        chapters = (
            await session.execute(
                select(ChapterModel).where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number.in_(RANGE),
                )
            )
        ).scalars().all()
        print(f"found {len(chapters)} chapter rows for ch217-230")

        if not chapters:
            print("nothing to reset")
            return

        chapter_ids = [c.id for c in chapters]

        # Set status -> planned for chapters
        res = await session.execute(
            update(ChapterModel)
            .where(ChapterModel.id.in_(chapter_ids))
            .values(status=ChapterStatus.PLANNED.value)
        )
        print(f"chapters set to PLANNED: {res.rowcount}")

        # Set status -> planned for all scenes under those chapters
        res2 = await session.execute(
            update(SceneCardModel)
            .where(SceneCardModel.chapter_id.in_(chapter_ids))
            .values(status=SceneStatus.PLANNED.value)
        )
        print(f"scenes set to PLANNED: {res2.rowcount}")

        await session.commit()
        print("done")


if __name__ == "__main__":
    asyncio.run(main())
