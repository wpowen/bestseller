"""A/B verification for the 文采 (prose-craft) capability.

Isolates exactly one variable: the writer system prompt is identical EXCEPT the
treatment arm appends ``render_prose_craft_block(genre, chapter)``. Everything
else (model, temperature, max_tokens, scene brief) is held constant.

For each genre × condition we generate N samples, then:
  * deterministic metric: ``count_signatures`` golden_line / total
  * LLM 文采 rating (0-1) by the critic model, blind to the A/B label

Run:  .venv/bin/python scripts/verify_prose_craft_ab.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re

from dotenv import load_dotenv

load_dotenv(".env")

import litellm  # noqa: E402

from bestseller.services.audit_loop import ChapterSignatureAudit  # noqa: E402
from bestseller.services.quality_levers.prose_craft_techniques import (  # noqa: E402
    render_prose_craft_block,
)
from bestseller.settings import get_settings  # noqa: E402

litellm.suppress_debug_info = True

SETTINGS = get_settings()
WRITER = SETTINGS.llm.writer
CRITIC = SETTINGS.llm.critic
WRITER_KEY = os.environ.get(getattr(WRITER, "api_key_env", "") or "")
CRITIC_KEY = os.environ.get(getattr(CRITIC, "api_key_env", "") or "")

SAMPLES = 2

# The CURRENT framework signature instruction (drafts.py:5629 + signature audit
# writer_injection), faithfully reproduced as the BASELINE arm.
BASELINE_SIGNATURE_INSTR = (
    "在场景 60-80% 处，给读者一个「截图段」：金句 / 神描写 / 神细节 任选其一，必须有一个，"
    "让读者愿意停下来截图摘抄。"
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

SYS_BASE = (
    "你是一名中文网络小说的资深写手。写商业网文正文，遵守：show-don't-tell、"
    "动作代替形容词、对白可区分、句子有长短节奏、禁止 AI 套话（如『空气仿佛凝固』）。"
    "只输出正文，不要任何解释 / 标题 / 标签。"
)


async def _complete(
    model: str,
    api_base: str,
    api_key: str,
    system: str,
    user: str,
    *,
    temperature: float,
    max_tokens: int,
) -> str:
    r = await litellm.acompletion(
        model=model,
        api_base=api_base,
        api_key=api_key,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=120,
    )
    return (r.choices[0].message.content or "").strip()


def _sig_counts(text: str) -> tuple[int, int]:
    hist = ChapterSignatureAudit.count_signatures(text)
    return hist.get("golden_line", 0), sum(hist.values())


async def _rate(drafts: list[dict]) -> list[dict]:
    """One batched critic call rating every draft on 文采, blind to arm."""
    payload = [{"id": d["id"], "text": d["text"]} for d in drafts]
    sys = (
        "你是中文文学编辑。只评『文采 / 语言质感』这一个维度，不评剧情。打分 0-1：\n"
        "- 0.9+：至少一处『值得截图摘抄』的金句/神句子，句子有记忆点结构，零辞藻堆砌。\n"
        "- 0.75：语言干净有一两处亮点，但没有真正的金句。\n"
        "- 0.6：平顺但全是大白话，无记忆点。\n"
        "- 扣分项：辞藻堆砌（堆红尘/繁华/形容词叠加/无具体物的抽象感叹）一旦出现，封顶 0.6。\n"
        "对每个 draft 返回 {id, score, best_line, purple_prose:true/false}。"
        '只输出 JSON：{"ratings":[...]}。'
    )
    user = "请评下列 drafts：\n" + json.dumps(payload, ensure_ascii=False)
    raw = await _complete(
        CRITIC.model, CRITIC.api_base, CRITIC_KEY, sys, user, temperature=0.0, max_tokens=4000
    )
    m = re.search(r"\{.*\}", raw, re.S)
    data = json.loads(m.group(0)) if m else {"ratings": []}
    by_id = {str(r.get("id")): r for r in data.get("ratings", [])}
    out = []
    for d in drafts:
        r = by_id.get(d["id"], {})
        raw_score = r.get("score")
        try:
            score = float(raw_score) if raw_score is not None else None
        except (TypeError, ValueError):
            score = None
        out.append(
            {
                **d,
                "score": score,
                "best_line": r.get("best_line", ""),
                "purple": bool(r.get("purple_prose", False)),
            }
        )
    return out


DRAFTS_PATH = "scripts/_prose_craft_ab_drafts.json"


async def _generate_one(
    genre: str, cfg: dict, arm: str, idx: int, *, temperature: float, max_tokens: int
) -> dict | None:
    sys = SYS_BASE + "\n\n# 签名段要求\n" + BASELINE_SIGNATURE_INSTR
    if arm == "treatment":
        sys += "\n\n" + render_prose_craft_block(genre_terms=cfg["terms"], chapter_number=3)
    did = f"{genre}-{arm}-{idx + 1}"
    text = ""
    for attempt in range(2):  # retry once if thinking ate the whole budget
        try:
            text = await _complete(
                WRITER.model,
                WRITER.api_base,
                WRITER_KEY,
                sys,
                cfg["brief"],
                temperature=temperature,
                max_tokens=max_tokens + attempt * 1500,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[{did}] WRITE FAIL (attempt {attempt}): {type(e).__name__}: {e}")
            continue
        if len(text) >= 120:
            break
    gl, tot = _sig_counts(text)
    print(f"[{did}] chars={len(text)} golden_line={gl} sig_total={tot}")
    if len(text) < 120:
        return None  # drop unusable empty/short draft
    return {"id": did, "genre": genre, "arm": arm, "text": text, "golden": gl, "sig_total": tot}


async def _generate_all() -> list[dict]:
    drafts: list[dict] = []
    for genre, cfg in GENRES.items():
        for arm in ("baseline", "treatment"):
            for i in range(SAMPLES):
                d = await _generate_one(genre, cfg, arm, i, temperature=0.85, max_tokens=4200)
                if d is not None:
                    drafts.append(d)
    # Persist raw drafts BEFORE rating so a rating failure never loses generation.
    with open(DRAFTS_PATH, "w", encoding="utf-8") as f:
        json.dump(drafts, f, ensure_ascii=False, indent=2)
    print(f"\nraw drafts saved -> {DRAFTS_PATH}")
    return drafts


def _report(rated: list[dict]) -> None:
    print("\n================ 文采 A/B 结果 ================")
    agg: dict[tuple[str, str], list[dict]] = {}
    for d in rated:
        agg.setdefault((d["genre"], d["arm"]), []).append(d)
    print(f"{'genre':6} {'arm':10} {'文采均分':>8} {'golden均':>8} {'purple率':>8} {'n':>3}")
    summary = {}
    for (genre, arm), items in sorted(agg.items()):
        scored = [x["score"] for x in items if x.get("score") is not None]
        avg = sum(scored) / len(scored) if scored else float("nan")
        gold = sum(x["golden"] for x in items) / len(items)
        purp = sum(1 for x in items if x.get("purple")) / len(items)
        summary[(genre, arm)] = (avg, gold, purp)
        print(f"{genre:6} {arm:10} {avg:8.3f} {gold:8.2f} {purp:8.0%} {len(items):3d}")

    print("\n---------- Δ(treatment - baseline) ----------")
    for genre in GENRES:
        b = summary.get((genre, "baseline"))
        t = summary.get((genre, "treatment"))
        if b and t:
            print(
                f"{genre:6} 文采Δ={t[0] - b[0]:+.3f}  golden_lineΔ={t[1] - b[1]:+.2f}  "
                f"purple: {b[2]:.0%}→{t[2]:.0%}"
            )


async def main() -> None:
    rate_only = os.environ.get("RATE_ONLY") == "1"
    if rate_only and os.path.exists(DRAFTS_PATH):
        with open(DRAFTS_PATH, encoding="utf-8") as f:
            drafts = json.load(f)
        print(f"RATE_ONLY: loaded {len(drafts)} drafts from {DRAFTS_PATH}")
    else:
        drafts = await _generate_all()

    try:
        rated = await _rate(drafts)
    except Exception as e:  # noqa: BLE001
        print(f"RATING FAILED: {type(e).__name__}: {e}; reporting deterministic metrics only")
        rated = [{**d, "score": None, "best_line": "", "purple": False} for d in drafts]

    _report(rated)

    with open("scripts/_prose_craft_ab_rated.json", "w", encoding="utf-8") as f:
        json.dump(rated, f, ensure_ascii=False, indent=2)
    print("\nrated drafts saved -> scripts/_prose_craft_ab_rated.json")
    print("\nfull drafts saved -> scripts/_prose_craft_ab_drafts.json")


if __name__ == "__main__":
    asyncio.run(main())
