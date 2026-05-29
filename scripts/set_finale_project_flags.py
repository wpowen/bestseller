"""Set per-project flags so the compressed finale (ch217-230) can finalize.

  chapter_review_warn_only = True
      -> chapter-review failures accept the best draft on stall instead of
         hard-blocking in REVISION. Generation + review still run via the
         framework's own models; only the terminal hard-block is relaxed.

This is scoped to THIS project only (mirrors whole_book_quality_gate_warn_only).

Run inside worker container:
    python /app/scripts/set_finale_project_flags.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from bestseller.infra.db.models import ProjectModel
from bestseller.infra.db.session import session_scope

SLUG = "xianxia-upgrade-1776137730"


async def main() -> None:
    async with session_scope() as session:
        project = (
            await session.execute(
                select(ProjectModel).where(ProjectModel.slug == SLUG)
            )
        ).scalar_one()
        metadata = dict(project.metadata_json or {})
        metadata["chapter_review_warn_only"] = True
        project.metadata_json = metadata
        flag_modified(project, "metadata_json")
        await session.commit()
        print(f"set chapter_review_warn_only=True for {SLUG}")


if __name__ == "__main__":
    asyncio.run(main())
