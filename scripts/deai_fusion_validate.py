#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""去AI味融合批的端到端验收（L3 离线）：真 detect() 跑三组语料。

与 deai_fusion_calibrate.py 的分工：那边测「候选正则的裸密度分布」（准入），
这边测「接线后的生产检测器」（门控行为验收）——阈值、min_chars、末段窗口、
对白豁免全部走真代码。三问：
  1. 人类出版章上新轴几乎不触发（advisory 误报率 ≤~1%/轴）；
  2. AI 稿上新轴按预期点名（饱和章被抓，干净章放过）；
  3. 既有轴分数分布无回归（新轴进的是 advisory 封顶，总分不应漂移）。

用法：
    python scripts/deai_fusion_validate.py --human-n 400 --ai-dir <corpus>
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import argparse
import collections
import glob
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestseller.services.ai_flavor.detector import detect  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
HUMAN_GLOB = str(REPO / ".distillation_private" / "source-*" / "chunks" / "chapter-*.txt")

NEW_CATEGORIES = (
    "stock_reaction",
    "micro_action_tic",
    "reverse_contrast",
    "voice_contrast",
    "trailer_ending",
    "trailer_summary",
    "sentence_signature_run",
)


def collect_human(n: int, seed: int) -> list[Path]:
    files = sorted(glob.glob(HUMAN_GLOB))
    by_source: dict[str, list[str]] = collections.defaultdict(list)
    for f in files:
        by_source[Path(f).parts[-3]].append(f)
    rng = random.Random(seed)
    picked: list[str] = []
    for chapter_files in by_source.values():
        rng.shuffle(chapter_files)
        picked.extend(chapter_files[:1])
    rng.shuffle(picked)
    return [Path(f) for f in picked[:n]]


def run(label: str, docs: list[Path]) -> dict:
    cat_chapters: collections.Counter = collections.Counter()
    scores: list[float] = []
    examples: dict[str, list[str]] = collections.defaultdict(list)
    n = 0
    for path in docs:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if len(text) < 600:
            continue
        n += 1
        report = detect(text, language="zh-CN")
        scores.append(report.overall_score)
        cats = {s.category for s in report.spans}
        for c in NEW_CATEGORIES:
            if c in cats:
                cat_chapters[c] += 1
                if len(examples[c]) < 3:
                    span = next(s for s in report.spans if s.category == c)
                    examples[c].append(
                        f"{path.parent.parent.name if 'source' in str(path) else ''}"
                        f"{path.name}: {span.matched_text[:26]}"
                    )
    print(f"\n── {label}（{n} 章）score mean={statistics.fmean(scores):.1f} "
          f"p90={sorted(scores)[int(0.9 * len(scores))]:.1f}")
    for c in NEW_CATEGORIES:
        rate = cat_chapters[c] / n * 100 if n else 0.0
        print(f"  {c:26s} {cat_chapters[c]:4d} 章 ({rate:5.2f}%)")
        for e in examples[c]:
            print(f"      · {e}")
    return {"n": n, "cats": dict(cat_chapters), "scores": scores}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--human-n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20260830)
    ap.add_argument("--ai-dir", type=str, required=True)
    args = ap.parse_args()

    human = collect_human(args.human_n, args.seed)
    ai_base = Path(args.ai_dir)
    res_h = run("human 出版章", human)
    res_c = run("ai-current 在架稿", sorted((ai_base / "ai-current").glob("*")))
    res_r = run("ai-raw 被淘汰稿", sorted((ai_base / "ai-raw").glob("*")))

    print("\n══ 验收判定 ══")
    ok = True
    for c in NEW_CATEGORIES:
        h_rate = res_h["cats"].get(c, 0) / max(1, res_h["n"])
        if h_rate > 0.015:
            ok = False
            print(f"  ✗ {c} 人类误报率 {h_rate*100:.2f}% > 1.5%")
    any_ai = sum(res_c["cats"].values()) + sum(res_r["cats"].values())
    print(f"  人类侧全轴误报率 ≤1.5%：{'✓' if ok else '✗'}")
    print(f"  AI 侧新轴合计点名 {any_ai} 章次（应 >0，证明轴活着）")
    sys.exit(0 if ok and any_ai > 0 else 1)


if __name__ == "__main__":
    main()
