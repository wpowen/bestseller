#!/usr/bin/env python3
"""用人类出版章标定**语义判官**的 payoff_density。

为什么必须做：词表分类器已被证明两个方向都会错（真机实例：ch1 判官 0.88 而
分类器判 None；ch15 分类器判 face_slap 而判官仅 0.30），所以爽点覆盖率的
正确尺子是语义判官。但判官此前**没有人类基线**——读到 0.35 时，不知道这是
高于人类还是低于人类，于是任何书间比较都没有参照系。

方法与覆盖率标定同源：按题材分层（分层判据也和 audit_loop 一致），
每章重复评分取均值（单次读数方差实测 ~0.17，单次评分测不出 0.1 量级差异）。

⚠️ 判官读的是「这一章是否兑现」，人类章同样会有大量低分章——低分不等于坏书。
输出的是**分布**，用 p10/中位定告警带，不是及格线。

用法：
    python scripts/calibrate_judge_against_human_corpus.py [-n 20] [-k 2]
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import glob
from pathlib import Path
import random
import re
import statistics
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.reader_judge import judge_chapter_readability  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402


# 分层判据与 audit_loop.PleasureDistributionAudit 的题材层保持同一套思路：
# 玄幻类词 vs 市井/现实类词，谁多算谁。
XUANHUAN = re.compile(r"修炼|灵力|宗门|境界|真元|丹田|法宝|元婴|筑基|剑气|仙人|妖兽")
MARKET = re.compile(r"食堂|菜市|派出所|办公室|医院|车间|工资|房租|地铁|超市|同事|科长")


def _stratum(text: str) -> str:
    x, m = len(XUANHUAN.findall(text)), len(MARKET.findall(text))
    if x >= 3 and x > m * 2:
        return "玄幻向"
    if m >= 3 and m > x * 2:
        return "市井/现实向"
    return "混合/其它"


async def run(per_stratum: int, repeats: int, seed: int) -> int:
    settings = load_settings()
    files = glob.glob(".distillation_private/source-*/chunks/chapter-*.txt")
    if not files:
        print("没有找到人类语料（.distillation_private）")
        return 1
    rng = random.Random(seed)
    rng.shuffle(files)

    buckets: dict[str, list[float]] = collections.defaultdict(list)
    scanned = 0

    async with session_scope(settings) as session:
        for path in files:
            if all(len(v) >= per_stratum for v in buckets.values()) and len(buckets) >= 3:
                break
            try:
                text = Path(path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if len(text) < 1500:
                continue
            stratum = _stratum(text)
            if len(buckets[stratum]) >= per_stratum:
                continue
            scanned += 1

            scores: list[float] = []
            for _ in range(repeats):
                result = await judge_chapter_readability(
                    session, settings, text, chapter_number=0
                )
                value = result.dimensions.get("payoff_density")
                if value is not None and result.used_llm:
                    scores.append(float(value))
            if not scores:
                continue
            mean = statistics.fmean(scores)
            buckets[stratum].append(mean)
            spread = max(scores) - min(scores) if len(scores) > 1 else 0.0
            print(
                f"  [{stratum:8s}] {len(buckets[stratum]):>2d}/{per_stratum}  "
                f"payoff={mean:.2f} (±{spread:.2f})  {Path(path).parent.parent.name}"
            )

    print("\n" + "=" * 62)
    print(f"{'题材层':14s} {'n':>3s} {'p10':>6s} {'中位':>6s} {'p90':>6s} {'均值':>6s}")
    for stratum, values in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        if not values:
            continue
        ordered = sorted(values)

        def pct(q: float) -> float:
            return ordered[min(len(ordered) - 1, int(len(ordered) * q))]

        print(
            f"{stratum:14s} {len(ordered):>3d} {pct(0.10):>6.2f} "
            f"{statistics.median(ordered):>6.2f} {pct(0.90):>6.2f} "
            f"{statistics.fmean(ordered):>6.2f}"
        )
    print(
        "\n这是**人类出版章**在同一把语义尺子下的分布。"
        "\n低分章在人类里同样常见——用 p10 定告警带，不要当及格线。"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--per-stratum", type=int, default=12)
    parser.add_argument("-k", "--repeats", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260817)
    args = parser.parse_args()
    return asyncio.run(run(args.per_stratum, args.repeats, args.seed))


if __name__ == "__main__":
    raise SystemExit(main())
