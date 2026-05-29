"""Mark completed finale chapters (217-230) as 【新】 for frontend identification.

For each COMPLETE chapter in range:
  - prefix the DB title with 【新】 (idempotent)
  - prefix the exported markdown H1 with 【新】 (idempotent)
  - set metadata flag is_finale_new=True (so the frontend / queries can filter)

Run inside worker container:
    python /app/scripts/mark_finale_new.py
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from bestseller.domain.enums import ChapterStatus
from bestseller.infra.db.models import ChapterModel, ProjectModel
from bestseller.infra.db.session import session_scope

SLUG = "xianxia-upgrade-1776137730"
RANGE = list(range(217, 231))
MARK = "【新】"
OUTPUT_DIR = Path("/app/output") / SLUG


def _mark_md(chapter_number: int) -> bool:
    md = OUTPUT_DIR / f"chapter-{chapter_number:03d}.md"
    if not md.exists():
        # try non-padded
        md = OUTPUT_DIR / f"chapter-{chapter_number}.md"
    if not md.exists():
        return False
    text = md.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            if MARK in line:
                return True  # already marked
            # insert 【新】 right after the chapter number colon, or after "# "
            lines[i] = re.sub(r"^(#\s*第\d+章[:：])", r"\1" + MARK, line)
            if lines[i] == line:  # pattern didn't match -> just prefix title text
                lines[i] = "# " + MARK + line[2:]
            md.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    return False


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

        marked = 0
        for c in sorted(chapters, key=lambda x: x.chapter_number):
            done = c.status == ChapterStatus.COMPLETE.value
            md_ok = _mark_md(c.chapter_number)
            if not done:
                print(f"  ch{c.chapter_number}: status={c.status} (skip title mark; md_marked={md_ok})")
                continue
            title = c.title or ""
            if MARK not in title:
                c.title = MARK + title
            meta = dict(c.metadata_json or {})
            meta["is_finale_new"] = True
            c.metadata_json = meta
            flag_modified(c, "metadata_json")
            marked += 1
            print(f"  ch{c.chapter_number}: marked (title={c.title!r}, md_marked={md_ok})")

        await session.commit()
        print(f"done; {marked} complete chapters marked 新")


if __name__ == "__main__":
    asyncio.run(main())
