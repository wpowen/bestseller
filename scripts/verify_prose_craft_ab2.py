"""A/B v2 — fair, blind test of whether the 文采 craft block HARMS prose quality.

Differences from v1 (which was inconclusive):
  * FAIR baseline: the terse signature instruction the framework actually uses
    today (no craft techniques), so treatment = baseline + craft block isolates
    exactly the change being shipped.
  * Larger N, concurrent generation.
  * NO same-model self-rating. Instead the drafts are written out ANONYMISED
    (opaque shuffled ids), so a stronger external rater (the operating agent)
    can score them blind; a second pass maps ids back to arms.

Phase 1 (this script):  generate + anonymise.
Phase 2 (agent rates):  fill scripts/_ab2_ratings.json  {id: {score, purple}}.
Phase 3 (score.py):     join ratings with the hidden map, print Δ.

Run:  .venv/bin/python scripts/verify_prose_craft_ab2.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random

from dotenv import load_dotenv

load_dotenv(".env")

import litellm  # noqa: E402

from bestseller.services.quality_levers.prose_craft_techniques import (  # noqa: E402
    render_prose_craft_block,
)
from bestseller.settings import get_settings  # noqa: E402

litellm.suppress_debug_info = True

WRITER = get_settings().llm.writer
WRITER_KEY = os.environ.get(getattr(WRITER, "api_key_env", "") or "")

SAMPLES = 4
SEED = 20260605

# FAIR baseline: the framework's real terse signature ask (drafts.py:5629),
# WITHOUT any craft-technique guidance.
BASELINE_SIGNATURE_INSTR = (
    "本场需要植入一个「签名段/截图段」：金句 / 神描写 / 神细节 / 反应放大 任选其一，必须有一个。"
)

SYS_BASE = (
    "你是一名中文网络小说的资深写手。写商业网文正文，遵守：show-don't-tell、"
    "动作代替形容词、对白可区分、句子有长短节奏、禁止 AI 套话（如『空气仿佛凝固』）。"
    "只输出正文，不要任何解释 / 标题 / 标签。"
)

GENRES = {
    "都市": {
        "terms": ("都市", "职场"),
        "brief": (
            "场景：深夜加班的写字楼，主角林越刚被告知自己主导三年的项目被高层砍掉、"
            "功劳划给了同期。他独自留在工位收拾东西。请写这个场景的正文（约 350-450 字），"
            "限知第三人称、过去式，只输出正文。"
        ),
    },
    "古风": {
        "terms": ("古风", "仙侠"),
        "brief": (
            "场景：故国已亡，女主沈昭华在废弃的旧宫檐下避雨，怀里抱着先帝留下的半枚玉印。"
            "请写这个场景的正文（约 350-450 字），限知第三人称、过去式，只输出正文。"
        ),
    },
    "悬疑": {
        "terms": ("悬疑",),
        "brief": (
            "场景：刑警陈默在一桩溺亡案现场，发现死者口袋里有一张自己女儿幼儿园的接送卡——"
            "而他女儿三年前也溺水失踪。他强压情绪继续勘查。请写这个场景的正文"
            "（约 350-450 字），限知第三人称、过去式，只输出正文。"
        ),
    },
}

_SEM = asyncio.Semaphore(3)


async def _complete(system: str, user: str, *, max_tokens: int) -> str:
    async with _SEM:
        r = await litellm.acompletion(
            model=WRITER.model,
            api_base=WRITER.api_base,
            api_key=WRITER_KEY,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.85,
            max_tokens=max_tokens,
            timeout=150,
        )
    return (r.choices[0].message.content or "").strip()


async def _gen(genre: str, cfg: dict, arm: str, idx: int) -> dict | None:
    sys = SYS_BASE + "\n\n# 签名段要求\n" + BASELINE_SIGNATURE_INSTR
    if arm == "treatment":
        sys += "\n\n" + render_prose_craft_block(genre_terms=cfg["terms"], chapter_number=3)
    text = ""
    for attempt in range(2):
        try:
            text = await _complete(sys, cfg["brief"], max_tokens=4200 + attempt * 1500)
        except Exception as e:  # noqa: BLE001
            print(f"[{genre}-{arm}-{idx}] FAIL {type(e).__name__}: {e}")
            continue
        if len(text) >= 120:
            break
    if len(text) < 120:
        print(f"[{genre}-{arm}-{idx}] empty, dropped")
        return None
    return {"genre": genre, "arm": arm, "text": text}


async def main() -> None:
    tasks = [
        _gen(genre, cfg, arm, i)
        for genre, cfg in GENRES.items()
        for arm in ("baseline", "treatment")
        for i in range(SAMPLES)
    ]
    results = [r for r in await asyncio.gather(*tasks) if r is not None]
    print(f"generated {len(results)} drafts")

    rng = random.Random(SEED)
    rng.shuffle(results)
    anon = {}
    mapping = {}
    for n, r in enumerate(results, 1):
        oid = f"d{n:02d}"
        anon[oid] = r["text"]
        mapping[oid] = {"genre": r["genre"], "arm": r["arm"]}

    with open("scripts/_ab2_anon.json", "w", encoding="utf-8") as f:
        json.dump(anon, f, ensure_ascii=False, indent=2)
    with open("scripts/_ab2_map.json", "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    print("anonymised drafts -> scripts/_ab2_anon.json")
    print("hidden map        -> scripts/_ab2_map.json")
    print("next: agent rates each id into scripts/_ab2_ratings.json {id:{score,purple}}")


if __name__ == "__main__":
    asyncio.run(main())
