"""按用户方法论验证钩子判官本身（2026-08-11）。

    「我们需要构建一套完整的评判标准，用这个标准去给榜单级简介打分。
      如果榜单级简介的得分都很低，说明裁判模型有问题。」

验收线（写死在 config/hook_pull_eval.yaml 的 note 里）：
  - 榜单正样本中位 ≥ 7（榜单钩子在自己的尺子上必须是高分）
  - 用户定罪负样本中位 ≤ 4
  - 用户认可的对照条 ≥ 6
  - 负样本最高分不得超过正样本中位（分离度）
任何一条不满足 → 判官不合格，改判官，不改数据。

跑法（容器内，真 LLM）：python scripts/validate_hook_pull_judge.py [samples_per_item]
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys

sys.path.insert(0, "/app/src")

import yaml  # noqa: E402

from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.hook_pull_judge import (  # noqa: E402
    anchor_texts,
    detect_condemned_hook_structures,
    evaluate_hook_pull,
)
from bestseller.settings import load_settings  # noqa: E402


async def main() -> None:
    samples = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    spec = yaml.safe_load(open("/app/config/hook_pull_eval.yaml", encoding="utf-8"))

    # 防泄漏：验证集与锚例不得重叠
    anchors = anchor_texts()
    for group in ("positives", "negatives", "controls"):
        for item in spec[group]:
            for a in anchors:
                assert item["hook"][:20] not in a and a[:20] not in item["hook"], (
                    f"eval item overlaps judge anchor: {item['hook'][:30]}"
                )

    settings = load_settings()
    results: dict[str, list[tuple[str, float, str, tuple[str, ...]]]] = {
        "positives": [], "negatives": [], "controls": [],
    }
    async with session_scope(settings) as session:
        for group in ("positives", "negatives", "controls"):
            for item in spec[group]:
                channel = "女频" if item.get("channel") == "女频" else "男频"
                verdict = await evaluate_hook_pull(
                    session, settings,
                    title=item["title"], hook=item["hook"],
                    genre=item["genre"], channel=channel, samples=samples,
                )
                if verdict is None:
                    print(f"  !! judge unavailable for {item['title'][:16]}", flush=True)
                    continue
                # 完整标准=欲望判官+确定性定罪句式检测：句法归句法（LLM flag
                # 真机 3 采样只抓到 1 次），命中即封顶 3 分。
                structure_hits = detect_condemned_hook_structures(item["hook"])
                score = min(verdict.score, 3.0) if structure_hits else verdict.score
                flags = tuple(dict.fromkeys((*verdict.flags, *structure_hits)))
                results[group].append((item["title"], score, verdict.craving, flags))
                mark = {"positives": "P", "negatives": "N", "controls": "C"}[group]
                det = f" DET={structure_hits}" if structure_hits else ""
                print(f"  [{mark}] {score:4.1f} {item['title'][:16]:　<16} "
                      f"flags={list(flags)}{det} craving={verdict.craving[:24]}",
                      flush=True)

    pos = [s for _, s, _, _ in results["positives"]]
    neg = [s for _, s, _, _ in results["negatives"]]
    ctrl = [s for _, s, _, _ in results["controls"]]
    print("\n" + "=" * 60)
    checks = {
        "榜单正样本中位≥7": statistics.median(pos) >= 7 if pos else False,
        "定罪负样本中位≤4": statistics.median(neg) <= 4 if neg else False,
        "用户认可对照条≥6": min(ctrl) >= 6 if ctrl else False,
        "负样本最高<正样本中位": (max(neg) < statistics.median(pos)) if pos and neg else False,
    }
    print(f"pos n={len(pos)} 中位={statistics.median(pos) if pos else '-'} "
          f"p10={sorted(pos)[max(0,len(pos)//10)] if pos else '-'} min={min(pos) if pos else '-'}")
    print(f"neg n={len(neg)} 中位={statistics.median(neg) if neg else '-'} max={max(neg) if neg else '-'}")
    print(f"ctrl={ctrl}")
    ok = all(checks.values())
    for name, passed in checks.items():
        print(("  ✓ " if passed else "  ✗ ") + name)
    print("JUDGE " + ("VALIDATED" if ok else "REJECTED — 改判官，不改数据"))
    json.dump({"results": {k: [(t, s, c, list(f)) for t, s, c, f in v]
                           for k, v in results.items()},
               "checks": checks},
              open("/app/output/_hook_judge_validation.json", "w"),
              ensure_ascii=False, indent=1)
    print("WROTE /app/output/_hook_judge_validation.json")


asyncio.run(main())
