"""A/B Experiment 2 — the decisive test: abstract material vs concrete material.

Experiment 1 (verify_scene_grounding_ab.py) found the scene_grounding *prompt
block* has only a marginal effect on a clean concrete brief — the writer model
already writes cinematically when given concrete material. That points at the
Layer-3 root cause from the diagnosis: the《借运成神》pilot read like an essay
because its bible/material was abstract §default mechanism language
("商业类型状态引擎 / 状态变化规则"), so the writer was reasoning over abstractions.

This script isolates that variable. SAME scene, SAME shipped scene_grounding
block on BOTH arms; the only difference is the SOURCE MATERIAL:

  * ABSTRACT arm — mechanism/meta language, mirroring the pilot's §default bible.
  * CONCRETE arm — specific people, objects, sensory hooks, the actual mechanic.

If CONCRETE strongly beats ABSTRACT in blind pairwise judging, the dominant lever
is upstream material concreteness (Layer 3), not the writer prompt. (It also
validates the harness: a real effect must break the perfect-symmetry 50/50 that
a position-biased judge produces on a null.)

Run:  .venv/bin/python scripts/verify_material_concreteness_ab.py [N_per_arm]

NOTE: the inline pairwise output is POSITION-BIASED (see verify_scene_grounding_ab.py).
The authoritative verdict comes from scripts/_sg_rejudge.py (DeepSeek absolute
scorer on the saved scripts/_mat_ab_drafts.json) + the deterministic A_intrusion
secondary metric, which is model-independent.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys

from dotenv import load_dotenv

load_dotenv(".env")

import os  # noqa: E402

import litellm  # noqa: E402

from bestseller.services.quality_levers.scene_grounding import (  # noqa: E402
    audit_scene_grounding,
    render_scene_grounding_block,
)
from bestseller.settings import get_settings  # noqa: E402

litellm.suppress_debug_info = True

WRITER = get_settings().llm.writer
WRITER_KEY = os.environ.get(getattr(WRITER, "api_key_env", "") or "")
JUDGE_MODEL = "deepseek/deepseek-chat"
JUDGE_KEY = os.environ.get("DEEPSEEK_API_KEY")

N_PER_ARM = int(sys.argv[1]) if len(sys.argv) > 1 else 5
SEED = 20260606

SYS_BASE = (
    "你是一名中文网络小说的资深签约写手。写商业网文正文，遵守："
    "show-don't-tell、用动作/感官/物件外显代替形容词标签、对白可区分人物、"
    "句子有长短节奏、禁止 AI 套话。限知第三人称、过去式。"
    "只输出正文，不要任何解释/标题/标签。"
)

# Each scene has an ABSTRACT material brief (mechanism language, like the pilot's
# §default bible) and a CONCRETE material brief (specific blood-and-bone), aimed
# at the SAME beat. Both arms additionally receive the scene_grounding block.
SCENES = {
    "都市异能": {
        "terms": ("都市异能", "身份反转"),
        "abstract": (
            "题材：都市异能·身份反转。写一整章正文（约 1300-1700 字）。本章要求："
            "主角围绕一个目标、遭遇阻力方、做出一个有代价的选择，并产生一次可记录的状态变化；"
            "用一个『状态账』装置把进展外化；一个阻力方根据主角行为升级反应；"
            "揭示一个实用信息，并制造更尖锐的下一选择；结尾留一个状态钩子。"
        ),
        "concrete": (
            "承上设定：主角陆沉因高压电击成了『气运借贷』的临时节点，掌心一道会蔓延的黑纹，"
            "七日复利，借出去的气运要加倍偿还。写一整章正文（约 1300-1700 字），含三场："
            "（1）陆沉在线人卫东的事务所，卫东把一份二十三人的名单压在一张照片下给他，"
            "并告诉他名单背后还有走暗通道的人、对手沈墨白已盯上他妹妹陆芷晴、只剩三天；"
            "（2）陆沉离开事务所上了出租车，接到陌生号码来电，对方暗示妹妹的安全；"
            "（3）车停在妹妹学校后街，他远远看见妹妹站在校门口低头看手机。"
            "卫东、陆芷晴、沈墨白都要在本章落到。"
        ),
    },
    "悬疑": {
        "terms": ("悬疑",),
        "abstract": (
            "题材：悬疑刑侦。写一整章正文（约 1300-1700 字）。本章要求："
            "主角在一次问询中遭遇信息反转；用一个证据化道具把线索具体化；"
            "揭示一个与主角过去相关的实用信息，并制造更尖锐的下一选择；"
            "一个制度性阻力对主角施压；结尾留一个悬念钩子。"
        ),
        "concrete": (
            "写一整章正文（约 1300-1700 字），含三场：（1）刑警陈默在分局审讯室，"
            "证人周明临时翻供；陈默意识到周明牵连三年前的『城西溺亡案』，而那案牵连他失踪的女儿；"
            "（2）陈默走出审讯室，搭档老郑在走廊提醒他这案上面打过招呼；"
            "（3）陈默回办公室翻出城西旧案卷宗，发现一处被人为抹掉的记录。"
            "陈默、周明、老郑都要在本章落到。"
        ),
    },
    "职场": {
        "terms": ("职场", "都市"),
        "abstract": (
            "题材：都市职场。写一整章正文（约 1300-1700 字）。本章要求："
            "主角发现自己的成果被剥夺、遭遇制度性阻力；用一个能外化进展的装置呈现处境；"
            "识破一个更高层的授意，并做出有代价的选择；结尾留一个状态钩子。"
        ),
        "concrete": (
            "写一整章正文（约 1300-1700 字），含三场：（1）主角林越深夜回公司，"
            "从行政主管口中得知自己主导三年的『启明项目』被砍、功劳划给同期周倩；"
            "（2）林越在茶水间撞见周倩短暂交锋，察觉拍板的是一直器重他的总监郑岩；"
            "（3）林越走到郑岩还亮着灯的办公室门口，停在门外。"
            "林越、周倩、郑岩都要在本章落到。"
        ),
    },
}

_SEM = asyncio.Semaphore(4)


async def _complete(model, api_base, key, system, user, *, max_tokens, temperature):
    async with _SEM:
        r = await litellm.acompletion(
            model=model, api_base=api_base, api_key=key,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature, max_tokens=max_tokens, timeout=180,
        )
    return (r.choices[0].message.content or "").strip()


async def _gen(genre: str, cfg: dict, arm: str, idx: int) -> dict | None:
    block = render_scene_grounding_block(genre_terms=cfg["terms"], chapter_number=5)
    sys_prompt = SYS_BASE + "\n\n" + block  # both arms get the shipped block
    brief = cfg["abstract"] if arm == "abstract" else cfg["concrete"]
    text = ""
    for attempt in range(2):
        try:
            text = await _complete(
                WRITER.model, WRITER.api_base, WRITER_KEY, sys_prompt, brief,
                max_tokens=4200 + attempt * 1500, temperature=0.85,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[{genre}-{arm}-{idx}] FAIL {type(e).__name__}: {e}")
            continue
        if len(text) >= 600:
            break
    if len(text) < 600:
        print(f"[{genre}-{arm}-{idx}] too short, dropped")
        return None
    return {"genre": genre, "arm": arm, "idx": idx, "text": text}


_PAIR_SYS = (
    "你是中文网文资深责编。给你两段正文【甲】和【乙】，判断哪一段更像【好看的网络小说】"
    "而不是【平铺直叙的作文/读后感】。判据：站在主角立场即时落地场景、每处描写服务剧情、"
    "转场有具体锚点、设定与人物关系靠动作和对白演出来而非作者旁白解说。"
    "只输出 JSON：{\"winner\":\"甲\"或\"乙\"或\"平\",\"reason\":\"≤25字\"}。"
)


def _parse_winner(raw: str) -> str | None:
    cleaned = raw.replace("```json", "").replace("```", "")
    for cand in reversed(re.findall(r"\{[^{}]*\}", cleaned, re.DOTALL)):
        try:
            w = str(json.loads(cand).get("winner", "")).strip()
            if w in ("甲", "乙", "平"):
                return w
        except Exception:  # noqa: BLE001
            continue
    m = re.search(r'winner"?\s*[:：]\s*"?(甲|乙|平)', cleaned)
    return m.group(1) if m else None


async def _pair_judge(a_text: str, b_text: str) -> str | None:
    user = f"【甲】\n{a_text}\n\n【乙】\n{b_text}"
    for _ in range(3):
        try:
            raw = await _complete(JUDGE_MODEL, None, JUDGE_KEY, _PAIR_SYS, user,
                                  max_tokens=400, temperature=0.0)
        except Exception as e:  # noqa: BLE001
            print(f"[pair-judge] FAIL {type(e).__name__}: {str(e)[:80]}")
            continue
        w = _parse_winner(raw)
        if w:
            return w
    return None


def _mean(xs: list) -> float:
    vals = [float(x) for x in xs if x is not None]
    return sum(vals) / len(vals) if vals else 0.0


async def main() -> None:
    import random
    rng = random.Random(SEED)
    print(f"writer={WRITER.model}  judge={JUDGE_MODEL} (pairwise)  N/arm/scene={N_PER_ARM}")
    gen = [
        _gen(g, cfg, arm, i)
        for g, cfg in SCENES.items()
        for arm in ("abstract", "concrete")
        for i in range(N_PER_ARM)
    ]
    drafts = [d for d in await asyncio.gather(*gen) if d]
    print(f"generated {len(drafts)} drafts")

    for d in drafts:
        a = audit_scene_grounding(d["text"])
        d["A_intrusion"] = a.intrusion.density_per_kchars
        d["B_coverage"] = a.coverage.coverage
        d["chars"] = len(d["text"])

    json.dump(
        [{k: v for k, v in d.items() if k != "text"} | {"text": d["text"]} for d in drafts],
        open("scripts/_mat_ab_drafts.json", "w", encoding="utf-8"),
        ensure_ascii=False, indent=2,
    )

    # pairwise: concrete[i] vs abstract[i], each judged twice (swapped order)
    plan = []
    for g in SCENES:
        a_list = [d for d in drafts if d["genre"] == g and d["arm"] == "abstract"]
        c_list = [d for d in drafts if d["genre"] == g and d["arm"] == "concrete"]
        for i in range(min(len(a_list), len(c_list))):
            plan.append((g, a_list[i], c_list[i], True))   # abstract as 甲
            plan.append((g, a_list[i], c_list[i], False))  # abstract as 乙

    async def _run(g, abs_d, con_d, abs_is_jia):
        a_text, b_text = (abs_d["text"], con_d["text"]) if abs_is_jia else (con_d["text"], abs_d["text"])
        w = await _pair_judge(a_text, b_text)
        if w is None or w == "平":
            return {"genre": g, "outcome": w or "fail"}
        winner = "abstract" if (w == "甲") == abs_is_jia else "concrete"
        return {"genre": g, "outcome": winner}

    rng.shuffle(plan)
    results = await asyncio.gather(*[_run(*p) for p in plan])
    cw = sum(1 for r in results if r["outcome"] == "concrete")
    aw = sum(1 for r in results if r["outcome"] == "abstract")
    tie = sum(1 for r in results if r["outcome"] == "平")
    fail = sum(1 for r in results if r["outcome"] == "fail")
    decided = cw + aw

    print("\n========  PAIRWISE: concrete vs abstract material (swapped de-biased)  ========")
    print(f"comparisons={len(results)} decided={decided} ties={tie} fail={fail}")
    print(f"  concrete wins : {cw}")
    print(f"  abstract wins : {aw}")
    if decided:
        print(f"  >> CONCRETE win-rate (ties excluded): {cw / decided:.1%}")
    for g in SCENES:
        sub = [r for r in results if r["genre"] == g]
        print(f"    {g:8s}  concrete={sum(1 for r in sub if r['outcome']=='concrete')} "
              f"abstract={sum(1 for r in sub if r['outcome']=='abstract')} "
              f"tie={sum(1 for r in sub if r['outcome']=='平')}")

    print("\n========  deterministic (secondary)  ========")
    for label, key in (("A_intrusion/k (↓good)", "A_intrusion"),
                       ("B_coverage (↑good)", "B_coverage"),
                       ("chars", "chars")):
        a = _mean([d.get(key) for d in drafts if d["arm"] == "abstract"])
        c = _mean([d.get(key) for d in drafts if d["arm"] == "concrete"])
        print(f"{label:22s} abstract={a:8.2f}  concrete={c:8.2f}")


if __name__ == "__main__":
    asyncio.run(main())
