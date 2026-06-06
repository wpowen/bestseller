"""O4: re-judge the saved v2 writer-levers drafts with an INDEPENDENT stronger judge.

The +10.2 LitStyle win was measured with the budget MiniMax judge. To rule out a
"judge-compression / same-model" artifact, this re-judges the *exact same 18
drafts* (no regeneration) with DeepSeek-V4-Pro (NVIDIA NIM) — a different model
family from the MiniMax writer, and the judge the prior scene-grounding A/B
trusted for absolute-blind evaluation. If DeepSeek also scores treatment > baseline,
the uplift is independent of the judge.

Run:  .venv/bin/python scripts/rejudge_v2_with_deepseek.py
"""

# ruff: noqa: RUF001, E501

from __future__ import annotations

import asyncio
import json
import os
import statistics

from dotenv import load_dotenv

load_dotenv(".env")

import litellm  # noqa: E402

from bestseller.domain.litstyle_judge import litstyle_result_from_mapping  # noqa: E402
from bestseller.services.judge_genre_context import resolve_judge_genre_context  # noqa: E402
from bestseller.services.litstyle_prose import detect_ai_tone, load_litstyle_config  # noqa: E402
from bestseller.services.litstyle_prose_judge import (  # noqa: E402
    _parse_json_object,
    build_litstyle_system_prompt,
    build_litstyle_user_prompt,
)

litellm.suppress_debug_info = True

# DeepSeek-V4-Flash direct (config/model_catalog.yaml: deepseek-v4-flash). An
# independent model family from the MiniMax writer, fast direct endpoint. (NVIDIA
# NIM deepseek-v4-pro was tried first but timed out at 180s+.)
JUDGE_MODEL = "deepseek/deepseek-v4-flash"
JUDGE_API_BASE = None
JUDGE_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

CONFIG = load_litstyle_config()
DRAFTS_PATH = "scripts/_litstyle_writer_levers_v2_drafts.json"


async def _judge(text: str, genre: str) -> dict | None:
    gc = resolve_judge_genre_context(genre=genre)
    ai_tone = detect_ai_tone(text, CONFIG)
    system = build_litstyle_system_prompt(config=CONFIG, genre_context=gc)
    user = build_litstyle_user_prompt(chapter_number=3, content_md=text, ai_tone=ai_tone)
    r = await litellm.acompletion(
        model=JUDGE_MODEL, api_base=JUDGE_API_BASE, api_key=JUDGE_KEY,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.0, max_tokens=2500, timeout=180,
    )
    raw = (r.choices[0].message.content or "").strip()
    res = litstyle_result_from_mapping(_parse_json_object(raw), config=CONFIG,
                                       ai_tone_prior=ai_tone.deterministic_penalty,
                                       ai_tone_flagged=ai_tone.flagged)
    if "LITSTYLE_JUDGE_UNAVAILABLE" in res.top_issues:
        return None
    return {"final": res.final_score, "ai_tone": res.ai_tone_penalty, **dict(res.dimension_scores)}


def _report(scored: list[dict]) -> None:
    dims = list(CONFIG.dimension_keys)
    print("\n========= O4: DeepSeek-V4-Pro 独立复评 v2 drafts =========")
    by_arm: dict[str, list[dict]] = {}
    for s in scored:
        by_arm.setdefault(s["arm"], []).append(s)

    def agg(arm: str, key: str, fn: object) -> float:
        vals = [s[key] for s in by_arm.get(arm, []) if key in s]
        return fn(vals) if vals else float("nan")  # type: ignore[operator]

    print(f"{'维度':14} {'base均':>7} {'treat均':>8} {'Δ均':>6} | {'base最高':>8} {'treat最高':>9} {'Δ最高':>6}")
    for key in ["final", "ai_tone", *dims]:
        bm, tm = agg("baseline", key, statistics.mean), agg("treatment", key, statistics.mean)
        bx, tx = agg("baseline", key, max), agg("treatment", key, max)
        label = {"final": "FinalScore", "ai_tone": "AI腔扣分"}.get(key, key)
        print(f"{label:14} {bm:7.1f} {tm:8.1f} {tm - bm:+6.1f} | {bx:8.0f} {tx:9.0f} {tx - bx:+6.0f}")
    nb, nt = len(by_arm.get("baseline", [])), len(by_arm.get("treatment", []))
    bm, tm = agg("baseline", "final", statistics.mean), agg("treatment", "final", statistics.mean)
    print(f"\nN: baseline={nb} treatment={nt}")
    print(f"独立判官(DeepSeek) FinalScore Δ均={tm - bm:+.1f}")
    print("对照 MiniMax 判官曾测 Δ=+10.2 —— 若此处同向且显著为正 → +10.2 非判官伪影。")


async def main() -> None:
    if not JUDGE_KEY:
        print("NVIDIA_API_KEY 未设置，无法用 DeepSeek 判官。退出。")
        return
    drafts = json.load(open(DRAFTS_PATH, encoding="utf-8"))
    print(f"judge={JUDGE_MODEL}  drafts={len(drafts)}")
    scored: list[dict] = []
    for d in drafts:
        try:
            s = await _judge(d["text"], d["genre"])
        except Exception as exc:
            print(f"  judge failed {d['id']}: {type(exc).__name__}: {exc}")
            s = None
        if s:
            scored.append({**s, "arm": d["arm"], "id": d["id"]})
            print(f"  {d['id']}: final={s['final']} AI腔={s['ai_tone']}")
        with open("scripts/_litstyle_v2_deepseek_scored.json", "w", encoding="utf-8") as f:
            json.dump(scored, f, ensure_ascii=False, indent=2)
    _report(scored)


if __name__ == "__main__":
    asyncio.run(main())
