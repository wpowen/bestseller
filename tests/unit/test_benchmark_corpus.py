from __future__ import annotations

import json
from pathlib import Path

from bestseller.services.benchmark_corpus import (
    TIER_T1,
    TIER_T2,
    BenchmarkBook,
    _parse_book_chapters,
    load_benchmark_chapter,
    load_benchmark_chapter_window,
    load_benchmark_corpus,
)


def _write_registry(path: Path, samples: list[dict]) -> Path:
    path.write_text(
        json.dumps({"version": 1, "samples": samples}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _write_book(path: Path, chapters: int = 3) -> Path:
    parts = []
    for i in range(1, chapters + 1):
        heading = f"第{'一二三四五六七八九十'[i - 1]}章 试炼{i}"
        body = f"少年握紧了剑。这是第{i}章的正文内容。" * 30
        parts.append(f"{heading}\n{body}")
    path.write_text("\n\n".join(parts), encoding="utf-8")
    return path


def test_load_benchmark_corpus_missing_file_degrades_to_empty(tmp_path: Path) -> None:
    corpus = load_benchmark_corpus(tmp_path / "absent.json")
    assert corpus.books == ()
    assert corpus.by_tier(TIER_T1) == ()
    assert corpus.available() == ()


def test_load_benchmark_corpus_tier_mapping_and_accessors(tmp_path: Path) -> None:
    book_file = _write_book(tmp_path / "real_book.txt")
    registry = _write_registry(
        tmp_path / "registry.json",
        [
            {
                "source_id": "benchmark-source-0001",
                "title_key": "《亵渎》作者烟雨江南",
                "canonical_category": "action-progression",
                "sample_reason": "high_score_seed",
                "chapter_count": 258,
                "source_path": str(book_file),
                "file_format": "txt",
                "processing_status": "parse_ready",
            },
            {
                "source_id": "benchmark-source-0030",
                "title_key": "某题材补充书",
                "canonical_category": "suspense-mystery",
                "sample_reason": "category_fill:suspense-mystery",
                "chapter_count": 120,
                "source_path": str(tmp_path / "not_mounted" / "ghost.txt"),
                "file_format": "txt",
                "processing_status": "parse_ready",
            },
        ],
    )
    corpus = load_benchmark_corpus(registry)
    assert len(corpus.books) == 2
    t1 = corpus.by_tier(TIER_T1)
    t2 = corpus.by_tier(TIER_T2)
    assert [b.source_id for b in t1] == ["benchmark-source-0001"]
    assert [b.source_id for b in t2] == ["benchmark-source-0030"]
    assert corpus.by_category("suspense-mystery") == t2
    assert corpus.categories == ("action-progression", "suspense-mystery")
    # Only the book whose file exists is available.
    assert [b.source_id for b in corpus.available()] == ["benchmark-source-0001"]


def test_load_benchmark_chapter_reads_body_and_handles_missing(tmp_path: Path) -> None:
    _parse_book_chapters.cache_clear()
    book_file = _write_book(tmp_path / "real_book.txt", chapters=3)
    book = BenchmarkBook(
        source_id="benchmark-source-0001",
        title_key="t",
        category="action-progression",
        tier=TIER_T1,
        chapter_count=3,
        source_path=str(book_file),
        file_format="txt",
        processing_status="parse_ready",
    )
    body = load_benchmark_chapter(book, 2)
    assert body is not None
    assert "第2章的正文内容" in body
    assert load_benchmark_chapter(book, 99) is None
    assert load_benchmark_chapter(book, 0) is None

    ghost = BenchmarkBook(
        source_id="ghost",
        title_key="g",
        category="x",
        tier=TIER_T2,
        chapter_count=1,
        source_path=str(tmp_path / "missing.txt"),
        file_format="txt",
        processing_status="parse_ready",
    )
    assert load_benchmark_chapter(ghost, 1) is None


def test_load_benchmark_chapter_window_skips_missing(tmp_path: Path) -> None:
    _parse_book_chapters.cache_clear()
    book_file = _write_book(tmp_path / "real_book.txt", chapters=3)
    book = BenchmarkBook(
        source_id="benchmark-source-0001",
        title_key="t",
        category="action-progression",
        tier=TIER_T1,
        chapter_count=3,
        source_path=str(book_file),
        file_format="txt",
        processing_status="parse_ready",
    )
    window = load_benchmark_chapter_window(book, 2, 5)
    assert [chapter_no for chapter_no, _ in window] == [2, 3]
    assert all("正文内容" in body for _, body in window)


def test_corrupt_registry_degrades_to_empty(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_benchmark_corpus(bad).books == ()
    no_samples = _write_registry(tmp_path / "nos.json", samples=[])
    assert load_benchmark_corpus(no_samples).books == ()
