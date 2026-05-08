"""Mark out-of-range 《道种破虚》 chapters as blocked.

This is a state repair, not prose repair. It makes the database reflect the
truth exposed by the structural audit: chapters whose current draft is outside
the hard length envelope must not remain ``production_state=ok``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select  # noqa: E402

from bestseller.domain.enums import ChapterStatus  # noqa: E402
from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.drafts import count_words  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402


PROJECT_SLUG = "xianxia-upgrade-1776137730"
DEFAULT_START = 51
DEFAULT_END = 550


def _effective_chars(text: str) -> int:
    return len("".join(str(text or "").split()))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=DEFAULT_START)
    parser.add_argument("--end", type=int, default=DEFAULT_END)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    budget = settings.generation.words_per_chapter
    min_words = int(budget.min)
    max_words = int(budget.max)

    async with session_scope(settings) as session:
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG)
        )
        if project is None:
            raise SystemExit(f"project not found: {PROJECT_SLUG}")

        rows = list(
            await session.execute(
                select(ChapterModel, ChapterDraftVersionModel)
                .join(
                    ChapterDraftVersionModel,
                    ChapterDraftVersionModel.chapter_id == ChapterModel.id,
                )
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number >= args.start,
                    ChapterModel.chapter_number <= args.end,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
                .order_by(ChapterModel.chapter_number.asc())
            )
        )

        blocked: list[dict[str, Any]] = []
        for chapter, draft in rows:
            actual_wc = count_words(draft.content_md or "")
            chars = _effective_chars(draft.content_md or "")
            out_of_range = (
                actual_wc < min_words
                or actual_wc > max_words
                or chars < min_words
                or chars > max_words
            )
            if not out_of_range:
                continue
            payload = {
                "chapter": chapter.chapter_number,
                "word_count": actual_wc,
                "effective_chars": chars,
                "previous_status": chapter.status,
                "previous_production_state": chapter.production_state,
            }
            blocked.append(payload)
            if args.execute:
                chapter.status = ChapterStatus.REVISION.value
                chapter.production_state = "blocked"
                chapter.current_word_count = actual_wc
                chapter.metadata_json = {
                    **(chapter.metadata_json or {}),
                    "blocked_by_repair_audit": "current_chapter_length_out_of_range",
                    "repair_audit_word_count": actual_wc,
                    "repair_audit_effective_chars": chars,
                    "repair_audit_min_words": min_words,
                    "repair_audit_max_words": max_words,
                }

        if args.execute:
            project.metadata_json = {
                **(project.metadata_json or {}),
                "production_paused": True,
                "generation_resume_blocked_until_repair_audit": True,
                "repair_audit_out_of_range_chapters": len(blocked),
                "repair_audit_range": [args.start, args.end],
            }
            await session.flush()

        print(
            {
                "execute": args.execute,
                "range": [args.start, args.end],
                "out_of_range_chapters": len(blocked),
                "sample": blocked[:30],
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
