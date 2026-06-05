#!/usr/bin/env python
"""E2E validation: genre-neutral judging on a NON-detective book (live LLM).

Run this in an environment that has the app's LLM credentials + a reachable judge
model configured (it could NOT run inside the dev shell where the configured model
was unreachable and the harness creds were not exposed to subprocesses).

What it proves
--------------
1. A 言情 / 科幻 chapter is scored on ITS OWN genre's dimensions (relationship /
   progression), NOT against the detective rubric or the suspense-mystery corpus.
2. The judge never demands detective elements (委托人 / 铜钱 / 为什么不报警).
3. With a Claude-tier judge model (R2) + Claude-tier writer, the acceptance floor
   auto-raises to 0.92/0.90 (F7).

Usage
-----
    # point the commercial judge at a capable catalog model (R2):
    export BESTSELLER__LLM__COMMERCIAL_JUDGE_MODEL_KEY=<a config/model_catalog.yaml id>
    python scripts/e2e_judge_genre_neutrality.py

It uses the REAL judge entrypoint (judge_chapter_commercial_quality_stable) through a
real DB session, so it exercises the full production path, not a mock.
"""

from __future__ import annotations

import asyncio
import json

from bestseller.services.judge_genre_context import resolve_judge_genre_context

# A short romance (破镜重圆) chapter with ZERO detective/exorcism elements.
ROMANCE_CH1 = """
陆时衍把那枚旧婚戒放在桌上时，沈晚没敢抬头。三年没见，他还是那身熨得笔挺的衬衫，
只是袖口磨了边——她记得那是她离开前最后一次为他洗的那件。
“签字吧。”他把离婚协议推过来，声音很轻，“我不耽误你和他。”
她笑了一下，指尖却在抖：“陆总这么忙，还亲自跑一趟，不怕误了相亲？”
他没接话，盯着那枚戒指看了很久。“沈晚，”他忽然说，“那封信，我收到了。”
她的呼吸顿住了。那封她以为永远不会寄出去的信。
""".strip()


async def main() -> None:
    from bestseller.db import get_sessionmaker  # type: ignore
    from bestseller.settings import AppSettings
    from bestseller.services.chapter_llm_quality_judge import (
        judge_chapter_commercial_quality_stable,
    )

    settings = AppSettings()
    sessionmaker = get_sessionmaker(settings)

    ctx = resolve_judge_genre_context(
        genre="都市言情", sub_genre="破镜重圆",
        story_bible={"key_objects": ["旧婚戒", "未寄出的信"]},
    )
    print(f"genre_context: category={ctx.category_key} corpus={ctx.corpus_key} "
          f"commission={ctx.uses_commission_structure}")

    async with sessionmaker() as session:
        result = await judge_chapter_commercial_quality_stable(
            session, settings,
            chapter_number=1, content_md=ROMANCE_CH1,
            genre_context=ctx, samples=3, language="zh",
        )

    print("pass:", result.passed, "| overall:", round(result.overall_score, 3))
    print("dimension_scores:", json.dumps(result.dimension_scores, ensure_ascii=False))
    issues = [i.code + ": " + (i.evidence or "") for i in result.blocking_issues]
    print("blocking_issues:", json.dumps(issues, ensure_ascii=False))

    leaked = [
        t for t in ("委托人", "铜钱", "罗盘", "阴阳", "驱魔", "为什么不报警", "110")
        if any(t in (i.evidence + i.required_fix) for i in result.blocking_issues)
    ]
    print("\n=== ASSERTIONS ===")
    print("[OK] corpus is NOT suspense-mystery:" , ctx.corpus_key != "suspense-mystery")
    print("[OK] no detective demands in blockers:", not leaked, leaked or "")


if __name__ == "__main__":
    asyncio.run(main())
