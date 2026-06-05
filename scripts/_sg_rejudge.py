"""Re-judge the already-generated A/B drafts with a RELIABLE absolute scorer.

The pairwise judge in the verify_* scripts had a fatal position bias: swapped-
order de-biasing forces an exact 50/50 whenever the judge picks by position,
so it can never reveal a content difference. This script instead uses DeepSeek
(reliable JSON, no empty responses) for ANONYMISED, SHUFFLED absolute scoring
on a strict rubric, reusing the cached drafts (no regeneration cost):

  scripts/_sg_ab_drafts.json   — Experiment 1: baseline vs treatment (prompt block)
  scripts/_mat_ab_drafts.json  — Experiment 2: abstract vs concrete material
"""

from __future__ import annotations

import asyncio
import json
import re

from dotenv import load_dotenv

load_dotenv(".env")

import os  # noqa: E402

import litellm  # noqa: E402

litellm.suppress_debug_info = True
JUDGE_MODEL = "deepseek/deepseek-chat"
JUDGE_KEY = os.environ.get("DEEPSEEK_API_KEY")
SEED = 20260606
_SEM = asyncio.Semaphore(6)

_SYS = (
    "你是中文网文资深责编，只评叙事手法。给你一段正文，严格打分，只输出 JSON："
    "{\"camera\":1-10,\"essay_feel\":0或1,\"reason\":\"≤25字\"}。\n"
    "camera（越高越好）：站在主角立场即时落地场景、每处描写服务剧情、转场有锚点、"
    "设定与人物关系靠动作/对白演出来。\n"
    "essay_feel（1=有，越低越好）：像作文/读后感——作者旁白解说剧情因果、平铺关系背景、"
    "一段砸多个人名或数字、场景悬空。\n"
    "标准要狠：信息靠旁白交代而非演出来→essay_feel=1 且 camera≤5。"
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
    async with _SEM:
        for _ in range(3):
            try:
                r = await litellm.acompletion(
                    model=JUDGE_MODEL, api_key=JUDGE_KEY,
                    messages=[{"role": "system", "content": _SYS},
                              {"role": "user", "content": text}],
                    temperature=0.0, max_tokens=400, timeout=120,
                )
                d = _parse(r.choices[0].message.content or "")
                if d:
                    return d
            except Exception as e:  # noqa: BLE001
                print(f"  judge retry: {type(e).__name__}")
    return None


def _mean(xs: list) -> float:
    v = [float(x) for x in xs if x is not None]
    return sum(v) / len(v) if v else 0.0


async def judge_file(path: str, arms: tuple[str, str], title: str) -> None:
    import random
    try:
        drafts = json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        print(f"\n[{title}] missing {path} — skip")
        return
    rng = random.Random(SEED)
    order = list(range(len(drafts)))
    rng.shuffle(order)  # anonymise / shuffle so the judge can't infer arm
    scores = await asyncio.gather(*[_score(drafts[i]["text"]) for i in order])
    for pos, i in enumerate(order):
        s = scores[pos] or {}
        drafts[i]["camera"] = s.get("camera")
        drafts[i]["essay_feel"] = int(bool(s.get("essay_feel", 0))) if s else None

    print(f"\n================  {title}  (judge=DeepSeek absolute, blind)  ================")
    fails = sum(1 for d in drafts if d.get("camera") is None)
    print(f"{'metric':24s} {arms[0]:>10s} {arms[1]:>10s} {'Δ(2-1)':>9s}   (judge_fail={fails}/{len(drafts)})")
    for label, key in (("camera 1-10 (↑good)", "camera"),
                       ("essay_feel (↓good)", "essay_feel"),
                       ("A_intrusion/k (↓good)", "A_intrusion"),
                       ("B_coverage (↑good)", "B_coverage")):
        a0 = _mean([d.get(key) for d in drafts if d["arm"] == arms[0]])
        a1 = _mean([d.get(key) for d in drafts if d["arm"] == arms[1]])
        print(f"{label:24s} {a0:10.3f} {a1:10.3f} {a1 - a0:+9.3f}")
    print("per-genre camera:")
    genres = sorted({d["genre"] for d in drafts})
    for g in genres:
        cells = []
        for arm in arms:
            sub = [d for d in drafts if d["genre"] == g and d["arm"] == arm]
            cells.append(f"{arm}={_mean([d.get('camera') for d in sub]):.1f}")
        print(f"  {g:8s} " + "  ".join(cells))


async def main() -> None:
    await judge_file("scripts/_sg_ab_drafts.json", ("baseline", "treatment"),
                     "EXP1 · scene_grounding prompt block")
    await judge_file("scripts/_mat_ab_drafts.json", ("abstract", "concrete"),
                     "EXP2 · material concreteness")


if __name__ == "__main__":
    asyncio.run(main())
