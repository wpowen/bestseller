"""Show generation progress for finale chapters 217-230.

Run inside worker container:
    python /app/scripts/monitor_finale.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

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
        chapters = (
            await session.execute(
                select(ChapterModel).where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number.in_(RANGE),
                )
            )
        ).scalars().all()
        done = 0
        for c in sorted(chapters, key=lambda x: x.chapter_number):
            scs = (
                await session.execute(
                    select(SceneCardModel).where(SceneCardModel.chapter_id == c.id)
                )
            ).scalars().all()
            sc_str = ",".join(
                f"{sc.scene_number}:{sc.status[:4]}"
                for sc in sorted(scs, key=lambda x: x.scene_number)
            )
            flag = (c.metadata_json or {}).get("is_finale_new")
            if c.status == "complete":
                done += 1
            print(
                f"ch{c.chapter_number}: {c.status:10s} ps={c.production_state or '-':12s} "
                f"新={'Y' if flag else '-'} [{sc_str}]"
            )
        print(f"\ncomplete: {done}/{len(chapters)}")


if __name__ == "__main__":
    asyncio.run(main())
