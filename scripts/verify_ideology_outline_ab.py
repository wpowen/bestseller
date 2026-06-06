"""A/B pilot: does the core-ideology (母题) layer raise OUTLINE quality?

Two arms, same premise + genre:

  * BASELINE  — genre-only outline (what the framework plans today).
  * TREATMENT — derive an IdeologyKernel (thesis + primary/secondary/hidden
    motifs + belief-arc + cost system), run the coherence gate, then generate
    the SAME outline with the ideology prompt block injected so the worldview /
    volume arc / golden-three beats grow from the thesis.

Both outlines are then:
  1. blind-scored by the advisory ideology judge (kernel=None, symmetric) on the
     9 "思想/深度" dimensions → final_score 0-100;
  2. pairwise blind-judged ("哪个更有灵魂而非题材套路堆砌"), positions swapped to
     cancel the judge's position bias.

This is the closed loop in miniature: derive → gate → generate → judge → compare.

Run:  .venv/bin/python scripts/verify_ideology_outline_ab.py [N_briefs]
Needs .env with the planner/critic api keys (MiniMax) + DEEPSEEK_API_KEY for the
independent judge. With no keys it prints a deterministic structural demo instead.
"""

# ruff: noqa: E402, RUF001, E501, S112, ANN001, ANN202

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

from dotenv import load_dotenv

load_dotenv(".env")

import litellm

from bestseller.domain.ideology import render_ideology_kernel_prompt_block
from bestseller.services.ideology_coherence_gate import evaluate_ideology_kernel_coherence
from bestseller.services.ideology_judge import (
    build_ideology_judge_system_prompt,
    build_ideology_judge_user_prompt,
    score_ideology_from_judge_json,
)
from bestseller.services.ideology_kernel import (
    build_ideology_system_prompt,
    build_ideology_user_prompt,
    fallback_ideology_kernel,
    ideology_kernel_health_summary,
    parse_ideology_kernel,
)
from bestseller.settings import get_settings

litellm.suppress_debug_info = True

_S = get_settings()
PLANNER = _S.llm.planner
PLANNER_KEY = os.environ.get(getattr(PLANNER, "api_key_env", "") or "")
JUDGE_MODEL = "deepseek/deepseek-chat"
JUDGE_KEY = os.environ.get("DEEPSEEK_API_KEY") or PLANNER_KEY
JUDGE_BASE = None if os.environ.get("DEEPSEEK_API_KEY") else PLANNER.api_base

VOLUMES = 8
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3

BRIEFS: dict[str, dict] = {
    "仙侠": {
        "premise": (
            "边城百年一次被「天罚」清洗，幸存少年陆沉拜入仙门后逐渐发现，"
            "所谓天罚只是上宗收割灵脉的周期工程。为救城，他要不断交易寿命换取真相，"
            "最终在毁掉仙门与保全城民之间作选择。"
        ),
    },
    "悬疑": {
        "premise": (
            "刑警陈默接手一桩看似自杀的坠楼案，越查越发现死者牵连三年前一桩被人为抹掉的旧案，"
            "而那桩旧案与他失踪的女儿有关。每接近一层真相，他就失去一个还信任他的人。"
        ),
    },
    "都市异能": {
        "premise": (
            "外卖员林越因一次高压电击成为「气运借贷」的临时节点，借出去的气运要七日复利偿还。"
            "他想用这股力量翻身，却发现每一次借贷都在悄悄抽走他生命里最重要的东西。"
        ),
    },
    "历史": {
        "premise": (
            "寒门书生赵元在连年大灾的边郡做小吏，发现救灾的真正障碍不是天灾而是水利失修与官制腐坏。"
            "他一边建制度一边对抗既得利益，逐渐明白打赢容易、重建难。"
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
            timeout=240,
        )
    return (r.choices[0].message.content or "").strip()


# --- outline generation -----------------------------------------------------

_OUTLINE_SYS = (
    "你是商业网文长篇规划师。基于给定前提产出一份『基础大纲』JSON，字段：\n"
    "{thesis(一句话主题), worldview_invariants:[≥3条世界硬规则], "
    "volumes:[{title, goal, obstacle, climax, key_reveal, cost_paid}](共"
    f"{VOLUMES}卷), golden_three_chapters:[{{goal, conflict, hook}}](3章)}}。\n"
    "只输出 JSON，不要解释。中文。每个字符串 ≤ 60 字。"
)


async def _gen_outline(premise: str, ideology_block: str | None) -> str:
    sys_prompt = _OUTLINE_SYS
    user = f"前提：\n{premise}\n\n规划卷数：{VOLUMES}。"
    if ideology_block:
        user = (
            f"{user}\n\n以下是本书必须贯彻的核心理念内核，世界观/各卷/黄金三章都要服务它：\n"
            f"{ideology_block}\n\n"
            "请让 thesis 与 core_question 落到 volumes 的 cost_paid / key_reveal 与 "
            "golden_three_chapters 的 conflict 上；不要把主题只写进 thesis 字段。"
        )
    for attempt in range(2):
        try:
            text = await _complete(
                PLANNER.model, PLANNER.api_base, PLANNER_KEY,
                sys_prompt, user, max_tokens=2600 + attempt * 800, temperature=0.8,
            )
            if len(text) >= 200:
                return text
        except Exception as e:
            print(f"  [outline] FAIL {type(e).__name__}: {str(e)[:90]}")
    return ""


async def _derive_kernel_live(premise: str, genre: str):
    """Derive the ideology kernel via the planner LLM (fallback-safe)."""

    fb = fallback_ideology_kernel(genre=genre, premise=premise, volumes=VOLUMES)
    try:
        raw = await _complete(
            PLANNER.model, PLANNER.api_base, PLANNER_KEY,
            build_ideology_system_prompt(language="zh"),
            build_ideology_user_prompt(
                premise=premise, genre=genre, volumes=VOLUMES, fallback_payload=fb
            ),
            max_tokens=3200, temperature=0.7,
        )
    except Exception as e:
        print(f"  [kernel] FAIL {type(e).__name__}: {str(e)[:90]} — using deterministic fallback")
        raw = json.dumps(fb, ensure_ascii=False)
    return parse_ideology_kernel(raw, genre=genre, premise=premise, volumes=VOLUMES)


# --- judging ----------------------------------------------------------------


async def _score_ideology(outline_text: str) -> int:
    """Blind ideology depth score (kernel=None → symmetric across arms)."""

    sys_p = build_ideology_judge_system_prompt()
    usr_p = build_ideology_judge_user_prompt(kernel=None, outline_text=outline_text)
    raw = await _complete(JUDGE_MODEL, JUDGE_BASE, JUDGE_KEY, sys_p, usr_p,
                          max_tokens=1600, temperature=0.0)
    result = score_ideology_from_judge_json(raw, kernel=None, outline_text=outline_text)
    return result.final_score


_PAIR_SYS = (
    "你是中文网文资深责编。给你同一前提下的两份大纲【甲】【乙】，判断哪一份更有"
    "『核心理念/灵魂』——即有一句贯穿全书、能生长出世界观与走向的思想（如《诛仙》的"
    "天地不仁），且主题被情节、代价与反转真正承载，而不是题材套路的堆砌或只写在主题句里。\n"
    "判据：世界是否有统一的宇宙前提、主角信念是否有『信→碎→立』的弧、力量/真相是否绑定代价、"
    "结局是否有价值反转。谁更靠近就选谁；几乎无差别才判平。\n"
    "只输出 JSON：{\"winner\":\"甲\"或\"乙\"或\"平\",\"reason\":\"≤25字\"}。"
)


def _parse_winner(raw: str) -> str | None:
    cleaned = raw.replace("```json", "").replace("```", "")
    for cand in reversed(re.findall(r"\{[^{}]*\}", cleaned, re.DOTALL)):
        try:
            w = str(json.loads(cand).get("winner", "")).strip()
            if w in ("甲", "乙", "平"):
                return w
        except Exception:
            continue
    m = re.search(r'winner"?\s*[:：]\s*"?(甲|乙|平)', cleaned)
    return m.group(1) if m else None


async def _pair_judge(a_text: str, b_text: str) -> str | None:
    user = f"【甲】\n{a_text}\n\n【乙】\n{b_text}"
    for _ in range(3):
        try:
            raw = await _complete(JUDGE_MODEL, JUDGE_BASE, JUDGE_KEY, _PAIR_SYS, user,
                                  max_tokens=300, temperature=0.0)
            w = _parse_winner(raw)
            if w:
                return w
        except Exception as e:
            print(f"  [pair] FAIL {type(e).__name__}: {str(e)[:80]}")
    return None


# --- dry (no-key) structural demo -------------------------------------------


def _dry_demo() -> None:
    print("\n=== NO API KEY — deterministic structural demo (no live A/B) ===\n")
    for genre, cfg in list(BRIEFS.items())[:N]:
        kernel = parse_ideology_kernel(
            json.dumps(fallback_ideology_kernel(genre=genre, premise=cfg["premise"], volumes=VOLUMES)),
            genre=genre, premise=cfg["premise"], volumes=VOLUMES,
        )
        gate = evaluate_ideology_kernel_coherence(kernel, volumes=VOLUMES)
        summary = ideology_kernel_health_summary(kernel)
        print(f"### {genre}")
        print(f"  thesis     : {kernel.thesis_statement}")
        print(f"  primary    : {summary['primary_motif']}  | secondary: {summary['secondary_motifs']} | hidden: {summary['hidden_motif']}")
        print(f"  layers     : {summary['covered_layers']}  ({summary['layer_count']}/4)")
        print(f"  coherence  : verdict={gate.verdict} coverage={gate.coverage:.2f} findings={len(gate.findings)}")
        print()
    print("Set MINIMAX_API_KEY / DEEPSEEK_API_KEY (in .env) to run the live A/B.\n")


# --- main -------------------------------------------------------------------


async def main() -> None:
    if not PLANNER_KEY:
        _dry_demo()
        return

    print(f"\n=== Ideology Outline A/B === planner={PLANNER.model}  judge={JUDGE_MODEL}  briefs={N}\n")
    rows = []
    pair_results = []
    for genre, cfg in list(BRIEFS.items())[:N]:
        premise = cfg["premise"]
        print(f"--- {genre} ---")
        kernel = await _derive_kernel_live(premise, genre)
        gate = evaluate_ideology_kernel_coherence(kernel, volumes=VOLUMES)
        block = render_ideology_kernel_prompt_block(kernel)
        summary = ideology_kernel_health_summary(kernel)
        print(f"  kernel: {summary['primary_motif']} + {summary['secondary_motifs']} + hidden={summary['hidden_motif']}"
              f" | layers {summary['layer_count']}/4 | gate={gate.verdict}({gate.coverage:.2f})")

        base_outline, treat_outline = await asyncio.gather(
            _gen_outline(premise, None),
            _gen_outline(premise, block),
        )
        if not base_outline or not treat_outline:
            print("  outline generation failed — skipping brief\n")
            continue

        base_score, treat_score = await asyncio.gather(
            _score_ideology(base_outline),
            _score_ideology(treat_outline),
        )
        # Pairwise, both orders (cancel position bias). 甲=baseline,乙=treatment in r1.
        r1, r2 = await asyncio.gather(
            _pair_judge(base_outline, treat_outline),
            _pair_judge(treat_outline, base_outline),
        )
        # Normalise to "treatment win?" votes.
        votes = []
        if r1:
            votes.append("treatment" if r1 == "乙" else ("baseline" if r1 == "甲" else "tie"))
        if r2:
            votes.append("treatment" if r2 == "甲" else ("baseline" if r2 == "乙" else "tie"))
        pair_results.extend(votes)
        rows.append((genre, base_score, treat_score, treat_score - base_score, votes))
        print(f"  ideology score: baseline={base_score}  treatment={treat_score}  Δ={treat_score - base_score:+d}  pair={votes}\n")

    if not rows:
        print("No briefs completed.")
        return

    print("=== RESULTS ===")
    print(f"{'genre':<10}{'base':>6}{'treat':>7}{'Δ':>6}  pairwise")
    for genre, b, t, d, votes in rows:
        print(f"{genre:<10}{b:>6}{t:>7}{d:>+6}  {votes}")
    avg_b = sum(r[1] for r in rows) / len(rows)
    avg_t = sum(r[2] for r in rows) / len(rows)
    tw = pair_results.count("treatment")
    bw = pair_results.count("baseline")
    tie = pair_results.count("tie")
    print(f"\nmean ideology score: baseline={avg_b:.1f}  treatment={avg_t:.1f}  Δ={avg_t - avg_b:+.1f}")
    print(f"pairwise: treatment={tw}  baseline={bw}  tie={tie}  (n={len(pair_results)})")
    print("(treatment = ideology-kernel-driven outline; baseline = genre-only outline)\n")


if __name__ == "__main__":
    asyncio.run(main())
