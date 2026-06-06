"""Corrected real-LLM A/B for the文采 WRITER LEVERS (v2).

Fixes the three confounds the v1 A/B revealed:
  1. **Flat 网文 briefs** (升级打脸/系统流/赘婿装逼) where a plain writer tends to
     produce mechanical prose → real headroom (v1 used literary scenes whose
     baseline already scored 82-85, no room).
  2. **Framing fix applied** — treatment prepends ``render_prose_lever_framing``
     so the budget writer stops reading 留白/克制 as "write less" (v1 root cause:
     treatment cut ~30% length → lost development → lower score).
  3. **More samples + best-of** — SAMPLES=3 per arm; report MEAN *and* MAX, so the
     ceiling (which v1 showed favored treatment: best draft was a treatment one)
     is visible and the single-bad-gen variance can't dominate.

    baseline   = WRITER(brief, SYS_BASE)
    treatment  = WRITER(brief, SYS_BASE + framing + prose_craft + scene_grounding + imagery)
    score both with the LitStyle judge (blind), compare mean + max.

Run:  .venv/bin/python scripts/verify_litstyle_writer_levers_ab2.py
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
from bestseller.services.litstyle_prose import (  # noqa: E402
    detect_ai_tone,
    load_litstyle_config,
    render_prose_lever_framing,
)
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

SAMPLES = 3  # generations per (brief, arm) — variance control + best-of-3 ceiling

SYS_BASE = (
    "你是一名中文网络小说的资深写手。写商业网文正文，遵守：show-don't-tell、"
    "动作代替形容词、对白可区分、句子有长短节奏、禁止 AI 套话（如『空气仿佛凝固』）。"
    "只输出正文，不要任何解释 / 标题 / 标签。"
)

# Flat 网文 briefs: mechanical 爽点 scenes where a plain writer tends to go flat.
GENRES = {
    "升级打脸": {
        "genre": "玄幻",
        "terms": ("玄幻", "仙侠"),
        "brief": (
            "场景：测灵根大典上，主角林轩一直被同门当成废物嘲笑。轮到他上前，把手按在测灵石上，"
            "所有人都等着看他笑话——结果测灵石爆发出前所未有的光柱，长老们脸色大变。请写这个"
            "打脸场景的正文（约 400-480 字），限知第三人称、过去式，只输出正文。"
        ),
        "imagery": {
            "theme_core": "被当成废物的人，用实力把规则砸出裂纹",
            "images": [
                {"name": "测灵石", "carrier": "祭台中央那块温润发暗的测灵石", "emotion_fn": "压抑后的爆发", "theme_fn": "废物的反击"},
                {"name": "光柱", "carrier": "石面炸开冲天的七彩光柱", "emotion_fn": "扬眉吐气", "theme_fn": "实力碾压偏见"},
            ],
        },
    },
    "系统流": {
        "genre": "都市异能",
        "terms": ("都市异能", "都市"),
        "brief": (
            "场景：普通快递员陈凡在送完最后一单瘫坐在出租屋里，眼前突然弹出一块"
            "半透明蓝色光屏：『签到系统已激活』。他将信将疑点了第一次签到，一股信息涌入脑海。"
            "请写这个觉醒场景的正文（约 400-480 字），限知第三人称、过去式，只输出正文。"
        ),
        "imagery": {
            "theme_core": "一无所有的人，第一次握住能改命的东西",
            "images": [
                {"name": "签到光屏", "carrier": "悬在眼前半透明的蓝色光屏", "emotion_fn": "将信将疑的窃喜", "theme_fn": "开挂的底气"},
                {"name": "出租屋", "carrier": "十平米、墙皮起壳的出租屋", "emotion_fn": "落魄", "theme_fn": "起点有多低"},
            ],
        },
    },
    "赘婿装逼": {
        "genre": "都市",
        "terms": ("都市", "现实"),
        "brief": (
            "场景：被岳家看不起的上门女婿苏然，在岳父六十大寿的宴会上被亲戚当众羞辱。就在岳父要"
            "赶他走时，一群西装革履的人匆匆赶到，恭敬地叫他『苏总』。请写这个身份反转场景的正文"
            "（约 400-480 字），限知第三人称、过去式，只输出正文。"
        ),
        "imagery": {
            "theme_core": "被看轻的人，从不需要急着证明自己",
            "images": [
                {"name": "廉价婚戒", "carrier": "苏然手上那枚被岳母嫌弃的廉价婚戒", "emotion_fn": "隐忍", "theme_fn": "被看轻的尊严"},
                {"name": "黑金令牌", "carrier": "下属双手奉上的那块黑金令牌", "emotion_fn": "翻盘", "theme_fn": "深藏不露的实力"},
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
            render_prose_lever_framing("zh"),  # the v2 fix
            render_prose_craft_block(genre_terms=genre_terms, chapter_number=3),
            render_scene_grounding_block(genre_terms=genre_terms, chapter_number=3),
            render_imagery_system_block(artifact=artifact, genre_terms=genre_terms, chapter_number=3),
        ) if b
    )
    return SYS_BASE + "\n\n" + levers


def _cjk(t: str) -> int:
    return sum(1 for c in t if "一" <= c <= "鿿")


async def _generate(name: str, cfg: dict, arm: str, idx: int) -> dict | None:
    system = SYS_BASE if arm == "baseline" else _treatment_system(cfg["terms"], cfg["imagery"])
    text = ""
    for attempt in range(2):
        text = await _complete(WRITER.model, WRITER.api_base, WRITER_KEY, system, cfg["brief"],
                               temperature=0.85, max_tokens=4500 + attempt * 1500)
        if _cjk(text) >= 200:
            break
    if _cjk(text) < 200:
        print(f"[{name}-{arm}-{idx}] unusable ({_cjk(text)} chars) — dropped")
        return None
    return {"id": f"{name}-{arm}-{idx}", "name": name, "arm": arm, "genre": cfg["genre"], "text": text}


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
    print("\n================ 文采写手杠杆 A/B v2（平淡网文brief + 总则 + best-of）================")
    by_arm: dict[str, list[dict]] = {}
    for s in scored:
        by_arm.setdefault(s["arm"], []).append(s)

    def agg(arm: str, key: str, fn: object) -> float:
        vals = [s[key] for s in by_arm.get(arm, []) if key in s]
        return fn(vals) if vals else float("nan")

    print(f"{'维度':14} {'base均':>7} {'treat均':>8} {'Δ均':>6} | {'base最高':>8} {'treat最高':>9} {'Δ最高':>6}")
    for key in ["final", "ai_tone", *dims]:
        bm, tm = agg("baseline", key, statistics.mean), agg("treatment", key, statistics.mean)
        bx, tx = agg("baseline", key, max), agg("treatment", key, max)
        label = {"final": "FinalScore", "ai_tone": "AI腔扣分"}.get(key, key)
        print(f"{label:14} {bm:7.1f} {tm:8.1f} {tm - bm:+6.1f} | {bx:8.0f} {tx:9.0f} {tx - bx:+6.0f}")

    nb, nt = len(by_arm.get("baseline", [])), len(by_arm.get("treatment", []))
    print(f"\nN: baseline={nb} treatment={nt}")
    bm, tm = agg("baseline", "final", statistics.mean), agg("treatment", "final", statistics.mean)
    bx, tx = agg("baseline", "final", max), agg("treatment", "final", max)
    print(f"均值 Δ={tm - bm:+.1f}（best-of 视角 Δ={tx - bx:+.0f}）")
    verdict_mean = "提升" if tm > bm else ("持平" if abs(tm - bm) < 1 else "下降")
    verdict_best = "提升" if tx > bx else ("持平" if abs(tx - bx) < 1 else "下降")
    print(f"结论：均值{verdict_mean}；best-of(取每臂最高，模拟best-of-N生成){verdict_best}。")
    print("（best-of 视角更贴近生产策略：生 N 稿 judge 取最高，keep-better 保证不退步。）")


async def main() -> None:
    drafts: list[dict] = []
    for name, cfg in GENRES.items():
        for arm in ("baseline", "treatment"):
            for i in range(SAMPLES):
                d = await _generate(name, cfg, arm, i)
                if d:
                    drafts.append(d)
                    print(f"  generated {d['id']} ({_cjk(d['text'])} chars)")
    with open("scripts/_litstyle_writer_levers_v2_drafts.json", "w", encoding="utf-8") as f:
        json.dump(drafts, f, ensure_ascii=False, indent=2)

    scored: list[dict] = []
    for d in drafts:
        try:
            s = await _judge(d["text"], d["genre"])
        except Exception as exc:
            print(f"  judge failed {d['id']}: {type(exc).__name__}: {exc}")
            s = None
        if s:
            scored.append({**s, "arm": d["arm"], "name": d["name"], "id": d["id"], "chars": _cjk(d["text"])})
            print(f"  judged {d['id']}: final={s['final']} AI腔={s['ai_tone']} chars={_cjk(d['text'])}")
    with open("scripts/_litstyle_writer_levers_v2_scored.json", "w", encoding="utf-8") as f:
        json.dump(scored, f, ensure_ascii=False, indent=2)
    _report(scored)


if __name__ == "__main__":
    asyncio.run(main())
