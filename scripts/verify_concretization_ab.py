"""PILOT A/B — does the concretization directive RESCUE abstract-material prose?

Layer-3 validation. EXP2 (verify_material_concreteness_ab.py) proved abstract
§default material produces ~14× the authorial intrusion of concrete material.
This script tests the FIX: holding the (abstract) material constant and the
scene_grounding block ON for both arms, does adding the concretization directive
move the output toward concrete-material quality?

  BASELINE  : abstract material brief + scene_grounding block
  TREATMENT : abstract material brief + scene_grounding block + concretization directive

Judge: DeepSeek absolute, anonymised + shuffled (the reliable scorer; the
pairwise judge has a fatal position bias — see verify_scene_grounding_ab.py).
Secondary: deterministic A_intrusion (model-independent, validated discriminator).

Reference values from EXP2 (same briefs, material swapped instead of directive):
  abstract: A_intrusion=0.66  camera=7.80   |   concrete: A_intrusion=0.05  camera=8.33

Run:  .venv/bin/python scripts/verify_concretization_ab.py [N_per_arm]
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

from bestseller.services.quality_levers.material_concreteness import (  # noqa: E402
    render_concretization_directive,
)
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
    "句子有长短节奏、禁止 AI 套话。限知第三人称、过去式。只输出正文，不要解释/标题/标签。"
)

# ABSTRACT material briefs (mechanism language — mirrors the pilot's §default bible).
ABSTRACT_BRIEFS = {
    "都市异能": {
        "terms": ("都市异能", "身份反转"),
        "brief": (
            "题材：都市异能·身份反转。写一整章正文（约 1300-1700 字）。本章要求："
            "主角围绕一个目标、遭遇阻力方、做出一个有代价的选择，并产生一次可记录的状态变化；"
            "用一个『状态账』装置把进展外化；一个阻力方根据主角行为升级反应；"
            "揭示一个实用信息，并制造更尖锐的下一选择；结尾留一个状态钩子。"
        ),
    },
    "悬疑": {
        "terms": ("悬疑",),
        "brief": (
            "题材：悬疑刑侦。写一整章正文（约 1300-1700 字）。本章要求："
            "主角在一次问询中遭遇信息反转；用一个证据化道具把线索具体化；"
            "揭示一个与主角过去相关的实用信息，并制造更尖锐的下一选择；"
            "一个制度性阻力对主角施压；结尾留一个悬念钩子。"
        ),
    },
    "职场": {
        "terms": ("职场", "都市"),
        "brief": (
            "题材：都市职场。写一整章正文（约 1300-1700 字）。本章要求："
            "主角发现自己的成果被剥夺、遭遇制度性阻力；用一个能外化进展的装置呈现处境；"
            "识破一个更高层的授意，并做出有代价的选择；结尾留一个状态钩子。"
        ),
    },
}

_SEM = asyncio.Semaphore(4)


async def _complete(model, api_base, key, system, user, *, max_tokens, temperature):
    async with _SEM:
        r = await litellm.acompletion(
            model=model, api_base=api_base, api_key=key,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=temperature, max_tokens=max_tokens, timeout=180,
        )
    return (r.choices[0].message.content or "").strip()


async def _gen(genre: str, cfg: dict, arm: str, idx: int) -> dict | None:
    sys_prompt = SYS_BASE + "\n\n" + render_scene_grounding_block(
        genre_terms=cfg["terms"], chapter_number=5
    )
    if arm == "treatment":
        sys_prompt += "\n\n" + render_concretization_directive(genre_terms=cfg["terms"])
    text = ""
    for attempt in range(2):
        try:
            text = await _complete(
                WRITER.model, WRITER.api_base, WRITER_KEY, sys_prompt, cfg["brief"],
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


_JUDGE_SYS = (
    "你是中文网文资深责编，只评叙事手法。给你一段正文，严格打分，只输出 JSON："
    "{\"camera\":1-10,\"essay_feel\":0或1,\"reason\":\"≤25字\"}。\n"
    "camera（越高越好）：站在主角立场即时落地场景、每处描写服务剧情、转场有锚点、"
    "设定与人物关系靠动作/对白演出来。\n"
    "essay_feel（1=有，越低越好）：像作文/读后感——作者旁白解说剧情、平铺关系背景、"
    "一段砸多个人名或数字。\n标准要狠：信息靠旁白交代而非演出来→essay_feel=1 且 camera≤5。"
)


def _parse(raw: str) -> dict | None:
    cleaned = raw.replace("```json", "").replace("```", "")
    for cand in reversed(re.findall(r"\{[^{}]*\}", cleaned, re.DOTALL)):
        try:
            d = json.loads(cand)
            if "camera" in d:
                return d
        except Exception:  # noqa: BLE001
            continue
    cam = re.search(r'camera"?\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)', cleaned)
    if cam:
        ess = re.search(r'essay_feel"?\s*[:：]\s*([01])', cleaned)
        return {"camera": float(cam.group(1)), "essay_feel": int(ess.group(1)) if ess else 0}
    return None


async def _score(text: str) -> dict | None:
    for _ in range(3):
        try:
            r = await _complete(JUDGE_MODEL, None, JUDGE_KEY, _JUDGE_SYS, text,
                                max_tokens=400, temperature=0.0)
        except Exception:  # noqa: BLE001
            continue
        d = _parse(r)
        if d:
            return d
    return None


def _mean(xs: list) -> float:
    v = [float(x) for x in xs if x is not None]
    return sum(v) / len(v) if v else 0.0


async def main() -> None:
    import random
    rng = random.Random(SEED)
    print(f"writer={WRITER.model}  judge={JUDGE_MODEL} (absolute, blind)  N/arm/brief={N_PER_ARM}")
    gen = [
        _gen(g, cfg, arm, i)
        for g, cfg in ABSTRACT_BRIEFS.items()
        for arm in ("baseline", "treatment")
        for i in range(N_PER_ARM)
    ]
    drafts = [d for d in await asyncio.gather(*gen) if d]
    print(f"generated {len(drafts)} drafts (abstract material; baseline=block only, treatment=+concretization)")

    for d in drafts:
        a = audit_scene_grounding(d["text"])
        d["A_intrusion"] = a.intrusion.density_per_kchars
        d["B_coverage"] = a.coverage.coverage

    order = list(range(len(drafts)))
    rng.shuffle(order)
    scores = await asyncio.gather(*[_score(drafts[i]["text"]) for i in order])
    for pos, i in enumerate(order):
        s = scores[pos] or {}
        drafts[i]["camera"] = s.get("camera")
        drafts[i]["essay_feel"] = int(bool(s.get("essay_feel", 0))) if s else None

    json.dump(
        [{k: v for k, v in d.items() if k != "text"} | {"text": d["text"]} for d in drafts],
        open("scripts/_concretization_ab_drafts.json", "w", encoding="utf-8"),
        ensure_ascii=False, indent=2,
    )

    def arm(n):
        return [d for d in drafts if d["arm"] == n]

    fails = sum(1 for d in drafts if d.get("camera") is None)
    print("\n========  PILOT: concretization directive OFF vs ON (abstract material held constant)  ========")
    print(f"{'metric':24s} {'baseline':>10s} {'treatment':>10s} {'Δ':>9s}   (judge_fail={fails}/{len(drafts)})")
    for label, key in (("camera 1-10 (↑good)", "camera"),
                       ("essay_feel (↓good)", "essay_feel"),
                       ("A_intrusion/k (↓good)", "A_intrusion"),
                       ("B_coverage (↑good)", "B_coverage")):
        b = _mean([d.get(key) for d in arm("baseline")])
        t = _mean([d.get(key) for d in arm("treatment")])
        print(f"{label:24s} {b:10.3f} {t:10.3f} {t - b:+9.3f}")
    print("\nref EXP2 (material swapped): abstract A=0.66 cam=7.80  →  concrete A=0.05 cam=8.33")
    print("per-genre camera / A_intrusion:")
    for g in ABSTRACT_BRIEFS:
        for a_name in ("baseline", "treatment"):
            sub = [d for d in drafts if d["genre"] == g and d["arm"] == a_name]
            print(f"  {g:8s} {a_name:9s} camera={_mean([d.get('camera') for d in sub]):4.1f} "
                  f"A_intr={_mean([d.get('A_intrusion') for d in sub]):5.2f} (n={len(sub)})")


if __name__ == "__main__":
    asyncio.run(main())
