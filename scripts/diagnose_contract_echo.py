#!/usr/bin/env python
"""Measure whether writers echo the chapter contract verbatim into prose.

Hypothesis under test
---------------------
The framework feeds each chapter a contract (goal, conflict, hook, information
to reveal) and instructs the writer that those items "must not be omitted,
replaced, or merely hinted at". Downstream gates then check whether the prose
contains them. If the cheapest way to satisfy that loop is to restate the
contract's own wording, the model will do exactly that — and the long-observed
"keyword echo" symptom would be *contract* echo, i.e. self-inflicted.

Method
------
For every finished chapter we take each contract field and measure what share of
its character n-grams also occur in the prose.

The number alone proves nothing: Chinese prose about a fight shares n-grams with
any sentence about a fight. So every chapter is also scored against a **control**
— the contract of a *different, randomly chosen* chapter of the same book. The
signal is the gap:

    echo_lift = own_overlap - control_overlap

``echo_lift`` near zero means the overlap is ordinary shared vocabulary and the
hypothesis is not supported. A large positive lift means the prose really is
tracking the wording it was handed.

Read-only: opens one session, writes nothing, makes no LLM calls.

Usage
-----
    python scripts/diagnose_contract_echo.py                 # whole library
    python scripts/diagnose_contract_echo.py --slug my-book  # one book
    python scripts/diagnose_contract_echo.py --json report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
)
from bestseller.infra.db.session import (  # noqa: E402
    create_engine,
    create_session_factory,
)
from bestseller.settings import load_settings  # noqa: E402

#: Contract fields that are handed to the writer as text it must deliver.
CONTRACT_TEXT_FIELDS: tuple[str, ...] = (
    "chapter_goal",
    "opening_situation",
    "main_conflict",
    "hook_description",
    "chapter_emotion_arc",
)
CONTRACT_LIST_FIELDS: tuple[str, ...] = ("information_revealed",)

#: 4 characters is long enough that a shared n-gram is a real phrase in Chinese
#: rather than a common bigram, and short enough to catch light paraphrase.
NGRAM_SIZE = 4

_PUNCT_CHARS = (
    "　，。！？、；：《》（）【】…—·「」『』“”‘’"
    "()[]{}<>\"'`,.!?;:-_~/\\|@#$%^&*+=\r\n\t "
)
_PUNCT = re.compile("[" + re.escape(_PUNCT_CHARS) + "]+")


def _normalise(text: str | None) -> str:
    if not text:
        return ""
    return _PUNCT.sub("", str(text))


def _ngrams(text: str, n: int = NGRAM_SIZE) -> set[str]:
    cleaned = _normalise(text)
    if len(cleaned) < n:
        return set()
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


def _contract_fragments(chapter: ChapterModel) -> list[str]:
    fragments: list[str] = []
    for field_name in CONTRACT_TEXT_FIELDS:
        value = getattr(chapter, field_name, None)
        if value:
            fragments.append(str(value))
    for field_name in CONTRACT_LIST_FIELDS:
        value = getattr(chapter, field_name, None)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            fragments.extend(str(item) for item in value if item)
    return [f for f in fragments if _normalise(f)]


def _overlap(contract_fragments: Iterable[str], prose: str) -> float | None:
    """Share of contract n-grams that also appear in the prose."""

    contract_grams: set[str] = set()
    for fragment in contract_fragments:
        contract_grams |= _ngrams(fragment)
    if not contract_grams:
        return None
    prose_grams = _ngrams(prose)
    if not prose_grams:
        return None
    return len(contract_grams & prose_grams) / len(contract_grams)


@dataclass
class ChapterEcho:
    chapter_number: int
    own_overlap: float
    control_overlap: float

    @property
    def echo_lift(self) -> float:
        return self.own_overlap - self.control_overlap


@dataclass
class BookEcho:
    slug: str
    title: str
    chapters: list[ChapterEcho] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        if not self.chapters:
            return {"slug": self.slug, "chapters": 0}
        own = [c.own_overlap for c in self.chapters]
        control = [c.control_overlap for c in self.chapters]
        lift = [c.echo_lift for c in self.chapters]
        return {
            "slug": self.slug,
            "title": self.title,
            "chapters": len(self.chapters),
            "own_overlap_mean": round(statistics.mean(own), 4),
            "control_overlap_mean": round(statistics.mean(control), 4),
            "echo_lift_mean": round(statistics.mean(lift), 4),
            "echo_lift_median": round(statistics.median(lift), 4),
            "worst_chapters": [
                {"chapter": c.chapter_number, "echo_lift": round(c.echo_lift, 4)}
                for c in sorted(self.chapters, key=lambda c: -c.echo_lift)[:5]
            ],
        }


async def _load_books(slug: str | None, limit: int) -> list[BookEcho]:
    rng = random.Random(20260804)  # fixed seed: reruns must be comparable
    results: list[BookEcho] = []

    engine = create_engine(load_settings())
    session_factory = create_session_factory(engine=engine)
    async with session_factory() as session:
        project_stmt = select(ProjectModel)
        if slug:
            project_stmt = project_stmt.where(ProjectModel.slug == slug)
        projects = list(await session.scalars(project_stmt))

        for project in projects:
            chapters = list(
                await session.scalars(
                    select(ChapterModel)
                    .where(ChapterModel.project_id == project.id)
                    .order_by(ChapterModel.chapter_number)
                )
            )
            prose_by_chapter = await _load_current_prose(
                session, [c.id for c in chapters]
            )
            usable: list[tuple[ChapterModel, str]] = []
            for chapter in chapters:
                prose = prose_by_chapter.get(chapter.id, "")
                if prose and _contract_fragments(chapter):
                    usable.append((chapter, prose))
            if len(usable) < 2:
                continue

            book = BookEcho(slug=project.slug, title=project.title)
            for index, (chapter, prose) in enumerate(usable):
                own = _overlap(_contract_fragments(chapter), prose)
                # Control: this chapter's prose against a *different* chapter's
                # contract. Same book, same genre, same vocabulary — the only
                # thing that differs is whether the writer was handed this text.
                control_index = rng.randrange(len(usable) - 1)
                if control_index >= index:
                    control_index += 1
                control = _overlap(_contract_fragments(usable[control_index][0]), prose)
                if own is None or control is None:
                    continue
                book.chapters.append(
                    ChapterEcho(
                        chapter_number=chapter.chapter_number,
                        own_overlap=own,
                        control_overlap=control,
                    )
                )
            if book.chapters:
                results.append(book)
            if len(results) >= limit:
                break
    await engine.dispose()
    return results


async def _load_current_prose(session: Any, chapter_ids: Sequence[Any]) -> dict[Any, str]:
    """Current draft text per chapter.

    Falls back to the highest version when no row is flagged current — some
    historical chapters lost the flag, and dropping them would bias the sample
    toward recently-written books.
    """

    if not chapter_ids:
        return {}
    rows = list(
        await session.scalars(
            select(ChapterDraftVersionModel)
            .where(ChapterDraftVersionModel.chapter_id.in_(list(chapter_ids)))
            .order_by(
                ChapterDraftVersionModel.chapter_id,
                ChapterDraftVersionModel.version_no,
            )
        )
    )
    best: dict[Any, ChapterDraftVersionModel] = {}
    for row in rows:
        current = best.get(row.chapter_id)
        if current is None:
            best[row.chapter_id] = row
            continue
        if bool(getattr(row, "is_current", False)) and not bool(
            getattr(current, "is_current", False)
        ):
            best[row.chapter_id] = row
        elif bool(getattr(row, "is_current", False)) == bool(
            getattr(current, "is_current", False)
        ) and int(getattr(row, "version_no", 0)) >= int(
            getattr(current, "version_no", 0)
        ):
            best[row.chapter_id] = row
    return {
        chapter_id: str(getattr(row, "content_md", "") or "")
        for chapter_id, row in best.items()
    }


def _render(books: Sequence[BookEcho]) -> str:
    if not books:
        return "No book had two or more chapters with both a contract and prose."

    lines: list[str] = []
    lines.append(f"{'book':28} {'chs':>4} {'own':>7} {'control':>8} {'lift':>7}")
    lines.append("-" * 60)
    all_lift: list[float] = []
    for book in sorted(books, key=lambda b: -(b.summary().get("echo_lift_mean") or 0)):
        s = book.summary()
        lines.append(
            f"{s['slug'][:28]:28} {s['chapters']:>4} "
            f"{s['own_overlap_mean']:>7.3f} {s['control_overlap_mean']:>8.3f} "
            f"{s['echo_lift_mean']:>7.3f}"
        )
        all_lift.extend(c.echo_lift for c in book.chapters)

    lines.append("")
    lines.append(f"chapters measured: {len(all_lift)}")
    if all_lift:
        mean_lift = statistics.mean(all_lift)
        lines.append(f"mean echo lift:    {mean_lift:+.4f}")
        lines.append(f"median echo lift:  {statistics.median(all_lift):+.4f}")
        lines.append("")
        lines.append("Reading the result:")
        lines.append(
            "  lift <= ~0.02  the overlap is ordinary shared vocabulary; the "
            "contract-echo hypothesis is NOT supported."
        )
        lines.append(
            "  lift >= ~0.10  prose is measurably tracking the contract's own "
            "wording; rewriting the contract as intent is worth an A/B."
        )
        lines.append("  in between      inconclusive; widen the sample before acting.")
    return "\n".join(lines)


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", help="restrict to one book")
    parser.add_argument("--limit", type=int, default=20, help="max books (default 20)")
    parser.add_argument("--json", dest="json_path", help="write the full report here")
    args = parser.parse_args()

    books = await _load_books(args.slug, args.limit)
    print(_render(books))

    if args.json_path:
        payload = {
            "ngram_size": NGRAM_SIZE,
            "books": [b.summary() for b in books],
        }
        Path(args.json_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nwrote {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
