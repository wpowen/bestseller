"""从真书语料构建结构对标基线（榜单对标闭环 P3.3）。

按 T1/T2 层对每本书前 ``--max-chapter`` 章计算确定性结构指标，聚合分布写入
``data/benchmark_capability/structure_baseline.json``（只含聚合统计，repo 安全）。

用法：
  python scripts/build_benchmark_structure_baseline.py --max-chapter 60
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestseller.services.benchmark_corpus import (  # noqa: E402
    load_benchmark_chapter,
    load_benchmark_corpus,
)
from bestseller.services.benchmark_structure import (  # noqa: E402
    DEFAULT_BASELINE_PATH,
    aggregate_profiles,
    profile_chapter,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-chapter", type=int, default=60)
    parser.add_argument("--out", default=str(DEFAULT_BASELINE_PATH))
    args = parser.parse_args()

    corpus = load_benchmark_corpus()
    if not corpus.available():
        print("FATAL: corpus unavailable — mount /Volumes/书籍")
        sys.exit(1)

    baseline: dict[str, object] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "max_chapter": args.max_chapter,
        "privacy": "aggregate statistics only — no source text",
    }
    for tier in ("t1", "t2"):
        profiles = []
        books_used = 0
        for book in corpus.by_tier(tier):
            book_profiles = []
            for chapter_no in range(1, args.max_chapter + 1):
                body = load_benchmark_chapter(book, chapter_no)
                if body:
                    profile = profile_chapter(body)
                    if profile:
                        book_profiles.append(profile)
            if book_profiles:
                books_used += 1
                profiles.extend(book_profiles)
            print(f"  [{tier}] {book.title_key[:24]}: {len(book_profiles)} chapters")
        aggregated = aggregate_profiles(profiles)
        aggregated["n_books"] = books_used
        baseline[tier] = aggregated
        print(f"== {tier}: {books_used} books, {len(profiles)} chapters")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"baseline written: {out_path}")


if __name__ == "__main__":
    main()
