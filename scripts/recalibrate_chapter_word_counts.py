"""Recalibrate all stored chapter word counts to use the unified count_words().

Before the fix, chapter.current_word_count and draft.word_count were computed
with a narrow CJK range and no markdown stripping.  The frontend displayed
those values, which didn't match the real content.  After the fix, new
chapters get the correct count, but existing rows still carry old values.

This script backfills every current chapter draft and its parent chapter row
so the quickstart dashboard and reader TOC show consistent numbers.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.chdir(Path(__file__).resolve().parents[1])

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from bestseller.settings import load_settings
from bestseller.services.drafts import count_words


async def main() -> int:
    settings = load_settings()
    engine = create_async_engine(settings.database.url, echo=False)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as session:
        # Find all current chapter drafts
        from bestseller.infra.db.models import (
            ChapterDraftVersionModel,
            ChapterModel,
        )

        draft_rows = list(
            await session.scalars(
                select(ChapterDraftVersionModel).where(
                    ChapterDraftVersionModel.is_current.is_(True),
                )
            )
        )

        updated_drafts = 0
        updated_chapters = 0
        skipped_empty = 0

        for draft in draft_rows:
            if not draft.content_md:
                skipped_empty += 1
                continue
            new_wc = count_words(draft.content_md)
            if new_wc == int(draft.word_count or 0):
                continue  # already correct

            old_wc = int(draft.word_count or 0)
            draft.word_count = new_wc
            updated_drafts += 1

            # Also update the parent chapter row
            chapter = await session.get(ChapterModel, draft.chapter_id)
            if chapter is not None:
                chapter.current_word_count = new_wc
                updated_chapters += 1

            print(
                f"  ch-{draft.chapter_id}: {old_wc} → {new_wc} "
                f"(Δ {new_wc - old_wc:+d})"
            )

        await session.commit()

    print(
        f"\nDone: {updated_drafts} drafts updated, "
        f"{updated_chapters} chapters updated, "
        f"{skipped_empty} skipped (empty content)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
