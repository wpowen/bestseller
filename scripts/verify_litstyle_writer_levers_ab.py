"""Real-LLM A/B for the文采 WRITER LEVERS (the *better-first-draft* lever).

Isolates one variable: the writer system prompt is identical EXCEPT the treatment
arm appends the文采 writer levers (prose_craft + scene_grounding[+blank_space] +
imagery_system recall). Everything else (model, temperature, brief) is constant.

This answers the question the closed-loop A/B could not: does writing the FIRST
DRAFT with the文采 levers raise the LitStyle 9 dimensions vs a plain draft? (The
prior lesson — and the closed-loop result — say the first draft is the main lever,
not post-hoc polish.)

    baseline   = WRITER(brief, SYS_BASE)
    treatment  = WRITER(brief, SYS_BASE + prose_craft + scene_grounding + imagery)
    score both with the LitStyle judge (blind to arm), compare 9-dim means.

Run:  .venv/bin/python scripts/verify_litstyle_writer_levers_ab.py
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
from bestseller.services.quality_levers.imagery_system import (  # noqa: E402
    parse_imagery_artifact,
    render_imagery_system_block,
)
from bestseller.services.quality_levers.prose_craft_techniques import (  # noqa: E402
    render_prose_craft_block,
)
from bestseller.services.quality_levers.scene_grounding import (  # noqa: E402
    render_scene_grounding_block,
)
from bestseller.settings import get_settings  # noqa: E402

litellm.suppress_debug_info = True

SETTINGS = get_settings()
WRITER = SETTINGS.llm.writer
CRITIC = SETTINGS.llm.critic
WRITER_KEY = os.environ.get(getattr(WRITER, "api_key_env", "") or "")
CRITIC_KEY = os.environ.get(getattr(CRITIC, "api_key_env", "") or "")
CONFIG = load_litstyle_config()

SAMPLES = 2  # generations per (genre, arm)

SYS_BASE = (
    "你是一名中文网络小说的资深写手。写商业网文正文，遵守：show-don't-tell、"
    "动作代替形容词、对白可区分、句子有长短节奏、禁止 AI 套话（如『空气仿佛凝固』）。"
    "只输出正文，不要任何解释 / 标题 / 标签。"
)

GENRES = {
    "悬疑": {
        "terms": ("悬疑",),
        "brief": (
            "场景：刑警陈默在一桩溺亡案现场，发现死者口袋里有一张自己女儿幼儿园的接送卡——"
            "而他女儿三年前也溺水失踪。他强压情绪继续勘查。请写这个场景的正文"
            "（约 380-460 字），限知第三人称、过去式，只输出正文。"
        ),
        "imagery": {
            "theme_core": "淹没的真相与淹没的人，是同一片水",
            "images": [
                {"name": "接送卡", "carrier": "死者口袋里幼儿园的接送卡", "emotion_fn": "旧伤翻涌", "theme_fn": "女儿失踪的回声"},
                {"name": "水面", "carrier": "溺亡现场那片不起波澜的水", "emotion_fn": "窒息", "theme_fn": "被淹住的真相"},
            ],
        },
    },
    "古风": {
        "terms": ("古风",),
        "brief": (
            "场景：故国已亡，女主沈昭华在废弃的旧宫檐下避雨，怀里抱着先帝留下的半枚玉印。"
            "请写这个场景的正文（约 380-460 字），限知第三人称、过去式，只输出正文。"
        ),
        "imagery": {
            "theme_core": "残破的正统，握在手里也拼不回来",
            "images": [
                {"name": "半枚玉印", "carrier": "先帝留下的、断口仍锋利的半枚玉印", "emotion_fn": "故国之痛", "theme_fn": "残破的正统"},
                {"name": "檐雨", "carrier": "废宫檐角连成线的雨", "emotion_fn": "飘零", "theme_fn": "王朝替人落的泪"},
            ],
        },
    },
    "现实": {
        "terms": ("现实",),
        "brief": (
            "场景：深夜，女主在医院走廊外接到母亲去世的电话，她没有哭，只是走到自动售货机前，"
            "投了币，又忘了自己要买什么。请写这个场景的正文（约 380-460 字），"
            "限知第三人称、过去式，只输出正文。"
        ),
        "imagery": {
            "theme_core": "失去到来时，人先丢的是日常的小动作",
            "images": [
                {"name": "售货机", "carrier": "走廊尽头嗡嗡亮着的自动售货机", "emotion_fn": "麻木", "theme_fn": "日常还在运转，人却空了"},
                {"name": "硬币", "carrier": "投进去却忘了要买什么的硬币", "emotion_fn": "失神", "theme_fn": "失去让人忘记自己要什么"},
            ],
        },
    },
}


async def _complete(model: str, api_base: str | None, api_key: str, system: str, user: str,
                    *, temperature: float, max_tokens: int) -> str:
    r = await litellm.acompletion(
        model=model, api_base=api_base, api_key=api_key,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature, max_tokens=max_tokens, timeout=180,
    )
    return (r.choices[0].message.content or "").strip()


def _treatment_system(genre_terms: tuple[str, ...], imagery: dict) -> str:
    artifact = parse_imagery_artifact(imagery)
    levers = "\n\n".join(
        b for b in (
            render_prose_craft_block(genre_terms=genre_terms, chapter_number=3),
            render_scene_grounding_block(genre_terms=genre_terms, chapter_number=3),
            render_imagery_system_block(artifact=artifact, genre_terms=genre_terms, chapter_number=3),
        ) if b
    )
    return SYS_BASE + "\n\n" + levers


async def _generate(genre: str, cfg: dict, arm: str, idx: int) -> dict | None:
    system = SYS_BASE if arm == "baseline" else _treatment_system(cfg["terms"], cfg["imagery"])
    text = ""
    for attempt in range(2):
        text = await _complete(WRITER.model, WRITER.api_base, WRITER_KEY, system, cfg["brief"],
                               temperature=0.85, max_tokens=4200 + attempt * 1500)
        if len(text) >= 150:
            break
    if len(text) < 150:
        print(f"[{genre}-{arm}-{idx}] unusable ({len(text)} chars) — dropped")
        return None
    return {"id": f"{genre}-{arm}-{idx}", "genre": genre, "arm": arm, "text": text}


async def _judge(text: str, genre: str) -> dict | None:
    gc = resolve_judge_genre_context(genre=genre)
    ai_tone = detect_ai_tone(text, CONFIG)
    system = build_litstyle_system_prompt(config=CONFIG, genre_context=gc)
    user = build_litstyle_user_prompt(chapter_number=3, content_md=text, ai_tone=ai_tone)
    raw = await _complete(CRITIC.model, CRITIC.api_base, CRITIC_KEY, system, user,
                         temperature=0.0, max_tokens=2500)
    res = litstyle_result_from_mapping(_parse_json_object(raw), config=CONFIG,
                                       ai_tone_prior=ai_tone.deterministic_penalty,
                                       ai_tone_flagged=ai_tone.flagged)
    if "LITSTYLE_JUDGE_UNAVAILABLE" in res.top_issues:
        return None
    return {"final": res.final_score, "ai_tone": res.ai_tone_penalty, **dict(res.dimension_scores)}


def _report(scored: list[dict]) -> None:
    dims = list(CONFIG.dimension_keys)
    print("\n================ 文采写手杠杆 A/B（LitStyle 盲评）================")
    by_arm: dict[str, list[dict]] = {}
    for s in scored:
        by_arm.setdefault(s["arm"], []).append(s)

    def _mean(arm: str, key: str) -> float:
        vals = [s[key] for s in by_arm.get(arm, []) if key in s]
        return statistics.mean(vals) if vals else float("nan")

    print(f"{'维度':14} {'baseline':>9} {'treatment':>10} {'Δ':>7}")
    for key in ["final", "ai_tone", *dims]:
        b, t = _mean("baseline", key), _mean("treatment", key)
        label = {"final": "FinalScore", "ai_tone": "AI腔扣分"}.get(key, key)
        print(f"{label:14} {b:9.1f} {t:10.1f} {t - b:+7.1f}")
    nb = len(by_arm.get("baseline", []))
    nt = len(by_arm.get("treatment", []))
    print(f"\nN: baseline={nb} treatment={nt}")
    fb, ft = _mean("baseline", "final"), _mean("treatment", "final")
    print(f"结论：treatment FinalScore {'>' if ft > fb else '≤'} baseline（Δ={ft - fb:+.1f}）"
          f" → 写手杠杆{'确实把初稿文采拉起来' if ft > fb else '未显著拉起，需再调'}。")


async def main() -> None:
    drafts: list[dict] = []
    for genre, cfg in GENRES.items():
        for arm in ("baseline", "treatment"):
            for i in range(SAMPLES):
                d = await _generate(genre, cfg, arm, i)
                if d:
                    drafts.append(d)
                    print(f"  generated {d['id']} ({len(d['text'])} chars)")
    with open("scripts/_litstyle_writer_levers_drafts.json", "w", encoding="utf-8") as f:
        json.dump(drafts, f, ensure_ascii=False, indent=2)

    scored: list[dict] = []
    for d in drafts:
        try:
            s = await _judge(d["text"], d["genre"])
        except Exception as exc:
            print(f"  judge failed {d['id']}: {type(exc).__name__}: {exc}")
            s = None
        if s:
            scored.append({**s, "arm": d["arm"], "genre": d["genre"], "id": d["id"]})
            print(f"  judged {d['id']}: final={s['final']} AI腔={s['ai_tone']}")
    with open("scripts/_litstyle_writer_levers_scored.json", "w", encoding="utf-8") as f:
        json.dump(scored, f, ensure_ascii=False, indent=2)
    _report(scored)


if __name__ == "__main__":
    asyncio.run(main())
