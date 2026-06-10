"""分层对标语料注册表（T1 榜单头部 / T2 题材完本）。

复用 ``.distillation_private/benchmark_sample_set.private.json``（40 本真书：
23 本 ``high_score_seed`` = T1 头部榜单校对版全本，17 本 ``category_fill`` =
T2 题材补充）作为对标语料的唯一注册来源，并通过蒸馏管线的
``distillation_book_parser`` 按需切章取文。

版权红线：真书文本永不入库（git）。本模块只从本地卷读取；当本地卷未挂载
（``/Volumes/书籍`` 不存在）时优雅降级为空语料/None，不抛异常。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_PATH = Path(".distillation_private/benchmark_sample_set.private.json")

TIER_T1 = "t1"
TIER_T2 = "t2"

_T1_SAMPLE_REASON = "high_score_seed"


@dataclass(frozen=True)
class BenchmarkBook:
    """One real published book registered for benchmark comparison."""

    source_id: str
    title_key: str
    category: str
    tier: str
    chapter_count: int
    source_path: str
    file_format: str
    processing_status: str

    @property
    def is_available(self) -> bool:
        """Whether the local source volume currently has this book's file."""
        return Path(self.source_path).is_file()


@dataclass(frozen=True)
class BenchmarkCorpus:
    """Loaded benchmark registry with tier/category accessors."""

    books: tuple[BenchmarkBook, ...]

    def by_tier(self, tier: str) -> tuple[BenchmarkBook, ...]:
        return tuple(book for book in self.books if book.tier == tier)

    def by_category(self, category: str) -> tuple[BenchmarkBook, ...]:
        return tuple(book for book in self.books if book.category == category)

    def available(self) -> tuple[BenchmarkBook, ...]:
        return tuple(book for book in self.books if book.is_available)

    @property
    def categories(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for book in self.books:
            seen.setdefault(book.category, None)
        return tuple(seen)


def _tier_for_sample(sample: dict[str, Any]) -> str:
    reason = str(sample.get("sample_reason") or "")
    return TIER_T1 if reason == _T1_SAMPLE_REASON else TIER_T2


def load_benchmark_corpus(registry_path: Path | None = None) -> BenchmarkCorpus:
    """Load the benchmark registry; empty corpus when the file is absent/bad."""
    path = registry_path or DEFAULT_REGISTRY_PATH
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("benchmark corpus registry missing: %s", path)
        return BenchmarkCorpus(books=())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("benchmark corpus registry unreadable (%s): %s", path, exc)
        return BenchmarkCorpus(books=())

    samples = payload.get("samples")
    if not isinstance(samples, list):
        logger.warning("benchmark corpus registry has no samples list: %s", path)
        return BenchmarkCorpus(books=())

    books: list[BenchmarkBook] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        source_id = str(sample.get("source_id") or "").strip()
        source_path = str(sample.get("source_path") or "").strip()
        if not source_id or not source_path:
            continue
        books.append(
            BenchmarkBook(
                source_id=source_id,
                title_key=str(sample.get("title_key") or source_id),
                category=str(sample.get("canonical_category") or "uncategorized"),
                tier=_tier_for_sample(sample),
                chapter_count=int(sample.get("chapter_count") or 0),
                source_path=source_path,
                file_format=str(sample.get("file_format") or "txt"),
                processing_status=str(sample.get("processing_status") or "unknown"),
            )
        )
    return BenchmarkCorpus(books=tuple(books))


@lru_cache(maxsize=8)
def _parse_book_chapters(source_path: str) -> tuple[Any, ...] | None:
    """Parse a real book into chapter slices, memoized per source path."""
    from bestseller.services.distillation_book_parser import (
        BookParseError,
        parse_source_book,
    )

    path = Path(source_path)
    if not path.is_file():
        logger.warning("benchmark book unavailable (volume unmounted?): %s", source_path)
        return None
    try:
        return parse_source_book(path).chapters
    except (BookParseError, OSError) as exc:
        logger.warning("benchmark book parse failed (%s): %s", source_path, exc)
        return None


def load_benchmark_chapter(book: BenchmarkBook, chapter_number: int) -> str | None:
    """Return the body text of chapter ``chapter_number`` (1-based) or None.

    None means the volume is unmounted, the book failed to parse, or the
    chapter index is out of range — callers must treat it as "skip this book",
    never as an error.
    """
    if chapter_number < 1:
        return None
    chapters = _parse_book_chapters(book.source_path)
    if not chapters:
        return None
    for chapter in chapters:
        if chapter.abs_chapter_no == chapter_number:
            return chapter.body
    if chapter_number <= len(chapters):
        return chapters[chapter_number - 1].body
    return None


def load_benchmark_chapter_window(
    book: BenchmarkBook, start_chapter: int, end_chapter: int
) -> list[tuple[int, str]]:
    """Return [(chapter_no, body), ...] for the inclusive window that exists."""
    window: list[tuple[int, str]] = []
    for chapter_no in range(max(1, start_chapter), end_chapter + 1):
        body = load_benchmark_chapter(book, chapter_no)
        if body:
            window.append((chapter_no, body))
    return window
