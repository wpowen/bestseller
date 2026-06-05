"""FAITHFUL PILOT A/B — old pipeline vs new full stack, on the real problem book.

The fresh single-chapter A/Bs were noisy because MiniMax already writes decently
on a clean brief. This pilot reproduces the ACTUAL pipeline failure as closely as
possible without running the whole autowrite: it feeds the writer the real
《借运成神》concrete premise PLUS the real ABSTRACT §default material block (the
exact thing the pilot's bible carried — verified 60/60 default-* refs), then
compares:

  OLD  : SYS_BASE only (no scene_grounding, no concretization) — the old pipeline
  NEW  : SYS_BASE + scene_grounding block + concretization directive — full stack

Single book (都市异能) to concentrate samples and cut cross-genre noise. Judge:
DeepSeek absolute (blind, shuffled) + deterministic A_intrusion.

Run:  .venv/bin/python scripts/verify_pilot_fullstack_ab.py [N_per_arm]
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
N_PER_ARM = int(sys.argv[1]) if len(sys.argv) > 1 else 6
SEED = 20260606
TERMS = ("都市异能", "身份反转")

SYS_BASE = (
    "你是一名中文网络小说的资深签约写手。写商业网文正文，遵守："
    "show-don't-tell、用动作/感官/物件外显代替形容词标签、对白可区分人物、"
    "句子有长短节奏、禁止 AI 套话。限知第三人称、过去式。只输出正文，不要解释/标题/标签。"
)

# The real pilot's CONCRETE premise + the real ABSTRACT §default material block
# (the exact abstract scaffolding the pilot bible carried). This is what the
# pipeline actually fed the writer.
BRIEF = (
    "【本书设定】《借运成神》都市异能。主角陆沉，电工，因高压电击成了『气运借贷』的临时节点，"
    "掌心一道会蔓延的黑纹，借出去的气运七日复利、加倍偿还，掌心/手腕会浮现倒计时数字。"
    "线人卫东在事务所给情报；收电费的陈三指缺三根手指、是过来人；幕后是灵溪的沈墨白，"
    "已盯上陆沉的妹妹陆芷晴。\n\n"
    "【可引用物料（Material Forge 已生成）】\n"
    "§power_systems/default-core-system：商业类型状态引擎 — 章节必须围绕目标、阻力、选择、"
    "代价和状态变化推进。\n"
    "§power_systems/default-state-delta-rule：状态变化规则 — 每章产生一个可记录的剧情、人物、"
    "关系或世界状态变化。\n"
    "§power_systems/default-cost-accounting-rule：代价记账规则 — 解决问题不能归零，必须留下"
    "下一章的新压力。\n"
    "§factions/default-core-faction：核心阻力方 — 阻力方会根据主角行为升级、转向或暂时撤退。\n"
    "§scene_templates/default-choice-negotiation：选择谈判场景 — 有人给出帮助或压力，并附带"
    "会改变未来选择的条件。\n"
    "§device_templates/default-state-ledger-device：状态账装置 — 文件、白板、契约、排名、账本，"
    "把进展外化。\n\n"
    "【任务】写一整章正文（约 1300-1700 字），含三场：（1）陆沉在卫东事务所拿到一份二十三人名单，"
    "得知名单背后还有更多人、妹妹被沈墨白盯上、只剩三天；（2）陆沉离开上了出租车，接到陌生号码来电"
    "暗示妹妹安全；（3）车停在妹妹学校后街，他远远看见妹妹站在校门口。"
)

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


async def _gen(arm: str, idx: int) -> dict | None:
    sys_prompt = SYS_BASE
    if arm == "new":
        sys_prompt += "\n\n" + render_scene_grounding_block(genre_terms=TERMS, chapter_number=5)
        sys_prompt += "\n\n" + render_concretization_directive(genre_terms=TERMS)
    text = ""
    for attempt in range(2):
        try:
            text = await _complete(WRITER.model, WRITER.api_base, WRITER_KEY, sys_prompt, BRIEF,
                                   max_tokens=4200 + attempt * 1500, temperature=0.85)
        except Exception as e:  # noqa: BLE001
            print(f"[{arm}-{idx}] FAIL {type(e).__name__}: {e}")
            continue
        if len(text) >= 600:
            break
    if len(text) < 600:
        print(f"[{arm}-{idx}] too short, dropped")
        return None
    return {"arm": arm, "idx": idx, "text": text}


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
    print(f"writer={WRITER.model}  judge={JUDGE_MODEL}  N/arm={N_PER_ARM}  (real premise + real abstract material)")
    gen = [_gen(arm, i) for arm in ("old", "new") for i in range(N_PER_ARM)]
    drafts = [d for d in await asyncio.gather(*gen) if d]
    print(f"generated {len(drafts)} drafts  (old=no levers, new=scene_grounding+concretization)")

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
        open("scripts/_pilot_fullstack_drafts.json", "w", encoding="utf-8"),
        ensure_ascii=False, indent=2,
    )

    def arm(n):
        return [d for d in drafts if d["arm"] == n]

    fails = sum(1 for d in drafts if d.get("camera") is None)
    print("\n========  PILOT: OLD pipeline vs NEW full stack (借运成神, real abstract material)  ========")
    print(f"{'metric':24s} {'OLD':>10s} {'NEW':>10s} {'Δ':>9s}   (judge_fail={fails}/{len(drafts)})")
    for label, key in (("camera 1-10 (↑good)", "camera"),
                       ("essay_feel (↓good)", "essay_feel"),
                       ("A_intrusion/k (↓good)", "A_intrusion"),
                       ("B_coverage (↑good)", "B_coverage")):
        o = _mean([d.get(key) for d in arm("old")])
        n = _mean([d.get(key) for d in arm("new")])
        print(f"{label:24s} {o:10.3f} {n:10.3f} {n - o:+9.3f}")
    # min/max camera to show distribution
    for a_name in ("old", "new"):
        cams = sorted(d.get("camera") for d in arm(a_name) if d.get("camera") is not None)
        intr = sorted(round(d["A_intrusion"], 2) for d in arm(a_name))
        print(f"  {a_name}: camera={cams}  A_intr={intr}")


if __name__ == "__main__":
    asyncio.run(main())
