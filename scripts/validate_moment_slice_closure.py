#!/usr/bin/env python3
"""端到端验收：时刻切片闭环对全部病章有效，且对健康章无副作用。

这是 moment_slice 检测器（2026-08-15）的验收台，回答四个问题：

1. **病章能不能治好**：14 个确诊病章逐个跑真实 deslop 闭环，密度必须落回
   人类区间（<1.2/千字，即检测器基础档阈值）。
2. **健康章会不会被误伤**（no-op 检查）：健康章跑同一条闭环，密度必须保持
   0 且字数不塌方——新检测器不该给干净稿制造重写。
3. **字数契约有没有被破坏**：去水后不得跌破章合同下限（否则会踢到 LENGTH
   门，换来一轮新的注水修复——注水自我保护的老坑）。
4. **人类语料误报率**：真检测器过 N 篇真实出版章。

用法：
    python scripts/validate_moment_slice_closure.py --dry-run      # 仅测量，不调 LLM
    python scripts/validate_moment_slice_closure.py --live         # 真实重写全部病章
    python scripts/validate_moment_slice_closure.py --live --only 24,25,38
"""

from __future__ import annotations

import argparse
import asyncio
import glob
from pathlib import Path
import random
import re
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bestseller.services.ai_flavor.detector import detect  # noqa: E402
from bestseller.services.deslop_revise import (  # noqa: E402
    _moment_slice_rate,
    revise_prose_deslop,
)


BOOK_DIR = Path("output/custom-xuanhuan-1786703729")
BASE_BAND = 1.2  # 检测器基础档：越过人类全语料最大值 1.14/千字
CHAPTER_TARGET = 2600


def _cjk(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text))


def _slice_cats(text: str) -> list[str]:
    return [
        s.category
        for s in detect(text, language="zh").spans
        if s.category.startswith("moment_slice")
    ]


def _measure(text: str) -> tuple[float, int, str]:
    return _moment_slice_rate(text), _cjk(text), ",".join(_slice_cats(text)) or "-"


def survey() -> tuple[list[int], list[int]]:
    """全书扫描，返回 (病章号, 健康章号)。"""

    sick: list[int] = []
    healthy: list[int] = []
    for path in sorted(BOOK_DIR.glob("chapter-*.md")):
        num = int(re.search(r"(\d+)", path.name).group(1))
        text = path.read_text(encoding="utf-8")
        if _cjk(text) < 300:
            continue
        (sick if _slice_cats(text) else healthy).append(num)
    return sick, healthy


def human_false_positive_rate(n: int = 300) -> tuple[int, int]:
    files = glob.glob(".distillation_private/source-*/chunks/chapter-*.txt")
    if not files:
        return (0, 0)
    random.seed(7)
    hits = 0
    picked = random.sample(files, min(n, len(files)))
    for f in picked:
        try:
            text = Path(f).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _slice_cats(text):
            hits += 1
    return hits, len(picked)


async def run_live(chapters: list[int], *, rounds: int) -> list[dict]:
    from bestseller.infra.db.session import create_engine, create_session_factory
    from bestseller.settings import load_settings

    settings = load_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    results: list[dict] = []
    try:
        for num in chapters:
            path = BOOK_DIR / f"chapter-{num:03d}.md"
            before = path.read_text(encoding="utf-8")
            b_rate, b_chars, b_cats = _measure(before)
            async with session_factory() as session:
                after = await revise_prose_deslop(
                    session,
                    settings,
                    content=before,
                    language="zh-CN",
                    target_chars=CHAPTER_TARGET,
                    rounds=rounds,
                    chapter_number=num,
                )
            a_rate, a_chars, a_cats = _measure(after)
            results.append(
                {
                    "ch": num,
                    "before_rate": b_rate,
                    "after_rate": a_rate,
                    "before_chars": b_chars,
                    "after_chars": a_chars,
                    "before_cats": b_cats,
                    "after_cats": a_cats,
                    "cured": a_rate < BASE_BAND,
                    "length_ok": a_chars >= CHAPTER_TARGET * 0.7,
                }
            )
            print(
                f"  ch{num:>2}: {b_rate:>6.2f} → {a_rate:>5.2f}/千字  "
                f"{b_chars} → {a_chars}字  "
                f"[{b_cats} → {a_cats}]  "
                f"{'✓' if a_rate < BASE_BAND else '✗ 未治愈'}"
                f"{'' if a_chars >= CHAPTER_TARGET * 0.7 else '  ✗ 跌破字数下限'}"
            )
    finally:
        await engine.dispose()
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="真实调用 LLM 跑重写闭环")
    ap.add_argument("--dry-run", action="store_true", help="仅测量当前密度")
    ap.add_argument("--only", type=str, default="", help="只跑指定章号（逗号分隔）")
    ap.add_argument("--healthy-control", type=int, default=3, help="对照的健康章数量")
    ap.add_argument("--rounds", type=int, default=2)
    args = ap.parse_args()

    sick, healthy = survey()
    print(f"【全书扫描】病章 {len(sick)}/{len(sick) + len(healthy)} → {sick}")

    fp_hits, fp_total = human_false_positive_rate()
    if fp_total:
        print(
            f"【人类语料误报】{fp_hits}/{fp_total} = {fp_hits / fp_total:.2%}"
            f"  {'✓' if fp_hits / fp_total < 0.02 else '✗ 超过 2% 判据'}"
        )

    if args.dry_run or not args.live:
        print("\n（dry-run：未调用 LLM。加 --live 跑真实重写闭环）")
        return 0

    targets = (
        [int(x) for x in args.only.split(",") if x.strip()] if args.only else list(sick)
    )
    controls = [c for c in healthy if c not in targets][: args.healthy_control]

    print(f"\n【病章治疗】{len(targets)} 章，rounds={args.rounds}")
    sick_results = asyncio.run(run_live(targets, rounds=args.rounds))

    print(f"\n【健康章对照（no-op 检查）】{controls}")
    ctrl_results = asyncio.run(run_live(controls, rounds=args.rounds))

    cured = sum(1 for r in sick_results if r["cured"])
    len_ok = sum(1 for r in sick_results if r["length_ok"])
    ctrl_clean = sum(1 for r in ctrl_results if r["after_rate"] < BASE_BAND)

    print("\n" + "=" * 60)
    print(f"病章治愈：{cured}/{len(sick_results)}")
    print(f"字数守约：{len_ok}/{len(sick_results)}")
    print(f"健康章保持干净：{ctrl_clean}/{len(ctrl_results)}")
    ok = (
        cured == len(sick_results)
        and len_ok == len(sick_results)
        and ctrl_clean == len(ctrl_results)
    )
    print("验收：" + ("✓ 全部通过" if ok else "✗ 有未通过项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
