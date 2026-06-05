"""A/B — does the scene_grounding block reduce essay-like prose?

Isolates exactly the shipped change: treatment = baseline writer prompt +
render_scene_grounding_block(); baseline = the same prompt without it.

Briefs deliberately reproduce the《借运成神》ch5 trap: a mid-book scene that
*demands* plot exposition, a scene transition, and several named parties — the
exact setup that tempts the model into authorial summary / jump-cuts / name
floods. A good lever makes the model *dramatise* that load instead of narrating it.

Scoring is dual-signal:
  * deterministic  — audit_scene_grounding (model-independent): authorial-intrusion
    density (A, the validated discriminator) + grounding coverage (B) + flood (C).
  * blind LLM judge — anonymised, shuffled; a strict behavioural rubric scores
    镜头感 (1-10) and flags 作文感 (essay feel).

Run:  .venv/bin/python scripts/verify_scene_grounding_ab.py [N_per_arm]
Writes scripts/_sg_ab_drafts.json + prints the Δ table.

NOTE: the inline pairwise output is POSITION-BIASED — swapped-order de-biasing
forces an exact 50/50 when the judge picks by position, so it cannot reveal a
content difference. For the authoritative verdict, run scripts/_sg_rejudge.py,
which re-scores the saved drafts with a reliable DeepSeek ABSOLUTE rubric.
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

_S = get_settings()
WRITER = _S.llm.writer
WRITER_KEY = os.environ.get(getattr(WRITER, "api_key_env", "") or "")
CRITIC = _S.llm.critic
CRITIC_KEY = os.environ.get(getattr(CRITIC, "api_key_env", "") or "")

N_PER_ARM = int(sys.argv[1]) if len(sys.argv) > 1 else 4
SEED = 20260606

# Baseline = the framework's realistic generic writer charter, WITHOUT the new
# scene_grounding block. Treatment appends only that block.
SYS_BASE = (
    "你是一名中文网络小说的资深签约写手。写商业网文正文，遵守："
    "show-don't-tell、用动作/感官/物件外显代替形容词标签、对白可区分人物、"
    "句子有长短节奏、禁止 AI 套话（如『空气仿佛凝固』『不易察觉』）。"
    "本场需要植入一个签名段（金句/神描写/神细节 任选其一）。"
    "限知第三人称、过去式。只输出正文，不要任何解释/标题/标签。"
)

# Harder briefs: each is a *full mini-chapter* (~1300-1700 字) with 2-3 scene
# shifts, several accumulated named parties, and multiple plot facts to convey —
# faithfully reproducing the《借运成神》ch5 trap that tempts the model into
# authorial summary / jump-cuts / name floods. This gives the baseline room to
# fail and the deterministic A signal room to fire.
BRIEFS = {
    "都市异能": {
        "terms": ("都市异能", "身份反转"),
        "brief": (
            "承上设定：主角陆沉因高压电击成了「气运借贷」的临时节点，掌心一道会蔓延的黑纹，"
            "七日复利，借出去的气运要加倍偿还。\n"
            "写一整章正文（约 1300-1700 字），含三场：\n"
            "（1）陆沉在线人卫东的事务所，卫东给他一份二十三人的名单，"
            "并告诉他：名单背后还有一百个走暗通道的人、对手沈墨白已经在盯他妹妹陆芷晴、只剩三天；\n"
            "（2）陆沉离开事务所，在出租车上接到陌生号码来电，对方暗示妹妹的安全；\n"
            "（3）车停在妹妹学校后街，陆沉远远看见妹妹站在校门口。\n"
            "必须交代清楚：『名单背后还有更多人』『妹妹被盯上』『幕后是沈墨白』三条信息；"
            "卫东、陆芷晴、沈墨白、出租车司机都要在本章出现。"
        ),
    },
    "悬疑": {
        "terms": ("悬疑",),
        "brief": (
            "写一整章正文（约 1300-1700 字），含三场：\n"
            "（1）刑警陈默在分局审讯室，关键证人周明临时翻供；陈默意识到周明与三年前那桩"
            "悬而未决的『城西溺亡案』有牵连，而那桩旧案牵连他自己失踪的女儿；\n"
            "（2）陈默走出审讯室，搭档老郑在走廊提醒他这案子上面有人打过招呼；\n"
            "（3）陈默回到办公室，翻出城西旧案的卷宗，发现一处被人为抹掉的记录。\n"
            "必须交代清楚：『周明翻供』『周明牵连城西旧案』『旧案与女儿有关』三条信息；"
            "陈默、周明、老郑、女儿都要在本章落到。"
        ),
    },
    "职场": {
        "terms": ("职场", "都市"),
        "brief": (
            "写一整章正文（约 1300-1700 字），含三场：\n"
            "（1）主角林越深夜回到公司，从行政主管口中得知自己主导三年的『启明项目』被砍、"
            "功劳划给了同期周倩；\n"
            "（2）林越在茶水间撞见周倩，两人短暂交锋，林越察觉拍板的是一直器重他的总监郑岩；\n"
            "（3）林越走到总监郑岩还亮着灯的办公室门口，停在门外。\n"
            "必须交代清楚：『启明项目被砍』『功劳被周倩拿走』『是郑岩授意』三条信息；"
            "林越、周倩、郑岩、行政主管都要在本章落到。"
        ),
    },
}

_SEM = asyncio.Semaphore(4)


async def _complete(model, api_base, key, system, user, *, max_tokens, temperature):
    async with _SEM:
        r = await litellm.acompletion(
            model=model,
            api_base=api_base,
            api_key=key,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=180,
        )
    return (r.choices[0].message.content or "").strip()


async def _gen(genre: str, cfg: dict, arm: str, idx: int) -> dict | None:
    sys_prompt = SYS_BASE
    if arm == "treatment":
        block = render_scene_grounding_block(genre_terms=cfg["terms"], chapter_number=5)
        sys_prompt = SYS_BASE + "\n\n" + block
    text = ""
    for attempt in range(2):
        try:
            text = await _complete(
                WRITER.model, WRITER.api_base, WRITER_KEY,
                sys_prompt, cfg["brief"],
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


# Independent judge (DeepSeek): removes same-model self-enhancement bias and is
# far more reliable at returning clean JSON than the MiniMax critic was.
JUDGE_MODEL = "deepseek/deepseek-chat"
JUDGE_KEY = os.environ.get("DEEPSEEK_API_KEY")

# PAIRWISE: comparing two drafts of the same scene is much more sensitive to a
# marginal effect than absolute 1-10 scoring, and controls for brief difficulty.
_PAIR_SYS = (
    "你是中文网文资深责编。给你同一场景设定下的两段正文【甲】和【乙】，"
    "判断哪一段更像【好看的网络小说】而不是【平铺直叙的作文/读后感】。\n"
    "判据：是否站在主角立场即时落地场景、每处描写服务剧情、转场有具体锚点、"
    "世界设定与人物关系靠动作和对白演出来而非作者旁白解说、不在一段里砸出多个人名或数字。\n"
    "谁更靠近这些就选谁；只有几乎无差别才判平。只输出 JSON："
    "{\"winner\":\"甲\"或\"乙\"或\"平\",\"reason\":\"≤25字\"}。"
)


def _parse_winner(raw: str) -> str | None:
    cleaned = raw.replace("```json", "").replace("```", "")
    for cand in reversed(re.findall(r"\{[^{}]*\}", cleaned, re.DOTALL)):
        try:
            data = json.loads(cand)
            w = str(data.get("winner", "")).strip()
            if w in ("甲", "乙", "平"):
                return w
        except Exception:  # noqa: BLE001
            continue
    m = re.search(r'winner"?\s*[:：]\s*"?(甲|乙|平)', cleaned)
    return m.group(1) if m else None


async def _pair_judge(a_text: str, b_text: str) -> str | None:
    """Return '甲' / '乙' / '平' / None (unparseable). Retries empties."""

    user = f"【甲】\n{a_text}\n\n【乙】\n{b_text}"
    for _ in range(3):
        try:
            raw = await _complete(
                JUDGE_MODEL, None, JUDGE_KEY, _PAIR_SYS, user,
                max_tokens=400, temperature=0.0,
            )
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
    print(f"writer={WRITER.model}  judge={JUDGE_MODEL} (pairwise)  N/arm/brief={N_PER_ARM}")
    gen_tasks = [
        _gen(g, cfg, arm, i)
        for g, cfg in BRIEFS.items()
        for arm in ("baseline", "treatment")
        for i in range(N_PER_ARM)
    ]
    drafts = [d for d in await asyncio.gather(*gen_tasks) if d]
    print(f"generated {len(drafts)} drafts")

    # deterministic audit (secondary, model-independent signal)
    for d in drafts:
        a = audit_scene_grounding(d["text"])
        d["A_intrusion"] = a.intrusion.density_per_kchars
        d["B_coverage"] = a.coverage.coverage
        d["det_pass"] = a.passed

    json.dump(
        [{k: v for k, v in d.items() if k != "text"} | {"text": d["text"]} for d in drafts],
        open("scripts/_sg_ab_drafts.json", "w", encoding="utf-8"),
        ensure_ascii=False, indent=2,
    )

    # ---- PAIRWISE blind judging (primary signal) ----
    # For each brief, pair baseline[i] vs treatment[i]; judge each pair TWICE
    # with swapped positions to cancel the judge's position bias.
    pair_plan: list[tuple[str, dict, dict, bool]] = []  # (genre, base, treat, base_is_甲)
    for g in BRIEFS:
        b_list = [d for d in drafts if d["genre"] == g and d["arm"] == "baseline"]
        t_list = [d for d in drafts if d["genre"] == g and d["arm"] == "treatment"]
        for i in range(min(len(b_list), len(t_list))):
            pair_plan.append((g, b_list[i], t_list[i], True))   # baseline as 甲
            pair_plan.append((g, b_list[i], t_list[i], False))  # baseline as 乙 (swapped)

    async def _run_pair(g, base, treat, base_is_jia):
        a_text, b_text = (base["text"], treat["text"]) if base_is_jia else (treat["text"], base["text"])
        w = await _pair_judge(a_text, b_text)
        if w is None or w == "平":
            outcome = w or "fail"
        else:
            winner_arm = (
                "baseline" if (w == "甲") == base_is_jia else "treatment"
            )
            outcome = winner_arm
        return {"genre": g, "outcome": outcome}

    rng.shuffle(pair_plan)
    results = await asyncio.gather(*[_run_pair(*p) for p in pair_plan])

    treat_w = sum(1 for r in results if r["outcome"] == "treatment")
    base_w = sum(1 for r in results if r["outcome"] == "baseline")
    ties = sum(1 for r in results if r["outcome"] == "平")
    fails = sum(1 for r in results if r["outcome"] == "fail")
    decided = treat_w + base_w

    print("\n================  PAIRWISE (DeepSeek, swapped-order de-biased)  ================")
    print(f"comparisons: {len(results)}  decided: {decided}  ties: {ties}  judge_fail: {fails}")
    print(f"  treatment wins : {treat_w}")
    print(f"  baseline  wins : {base_w}")
    if decided:
        print(f"  >> treatment win-rate (ties excluded): {treat_w / decided:.1%}")
    for g in BRIEFS:
        sub = [r for r in results if r["genre"] == g]
        tw = sum(1 for r in sub if r["outcome"] == "treatment")
        bw = sum(1 for r in sub if r["outcome"] == "baseline")
        tie = sum(1 for r in sub if r["outcome"] == "平")
        print(f"    {g:8s}  T={tw}  B={bw}  tie={tie}")

    # ---- deterministic secondary ----
    def arm(name):
        return [d for d in drafts if d["arm"] == name]

    print("\n================  deterministic (secondary, model-independent)  ================")
    print(f"{'metric':24s} {'baseline':>10s} {'treatment':>10s} {'Δ':>8s}")
    for label, key in (
        ("A_intrusion/k (↓good)", "A_intrusion"),
        ("B_coverage (↑good)", "B_coverage"),
        ("det_pass rate (↑good)", "det_pass"),
    ):
        b = _mean([d.get(key) for d in arm("baseline")])
        t = _mean([d.get(key) for d in arm("treatment")])
        print(f"{label:24s} {b:10.3f} {t:10.3f} {t - b:+8.3f}")


if __name__ == "__main__":
    asyncio.run(main())
