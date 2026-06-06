"""Real-LLM A/B for the LitStyle 文采 CLOSED LOOP.

Proves two things the advisory judge alone cannot:
  1. **Improvement** — does polishing actually raise the LitStyle FinalScore /
     lower AI腔 on real chapters?
  2. **Complete + safe loop** — generate → judge → polish → re-judge → keep the
     higher-scoring of {original, polished}. The keep-better step guarantees the
     loop can never ship a worse chapter; this harness reports how often it had
     to (i.e. how often polish regressed and was rejected).

For each real chapter:
    base   = judge(original)                      [stable, N samples, median]
    if base.final < target and judge available:
        polished_text = WRITER(polish_prompt(original, base))
        cand   = judge(polished_text)             [stable, N samples, median]
        kept   = polished if cand.final > base.final else original   (keep-better)

Run:
    .venv/bin/python scripts/verify_litstyle_closed_loop_ab.py \
        --genre 都市异能 --samples 2 --target 85 \
        output/oracle-pilot-dianshen/chapter-001.md output/oracle-pilot-dianshen/chapter-005.md
Defaults to the oracle-pilot-dianshen ch1-ch4 if no paths are given.
"""

# ruff: noqa: RUF001, E501

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import statistics

from dotenv import load_dotenv

load_dotenv(".env")

import litellm  # noqa: E402

from bestseller.domain.litstyle_judge import (  # noqa: E402
    LitStyleJudgeResult,
    litstyle_result_from_mapping,
)
from bestseller.services.judge_genre_context import resolve_judge_genre_context  # noqa: E402
from bestseller.services.litstyle_polish import build_litstyle_polish_prompt  # noqa: E402
from bestseller.services.litstyle_prose import (  # noqa: E402
    detect_ai_tone,
    load_litstyle_config,
)
from bestseller.services.litstyle_prose_judge import (  # noqa: E402
    _parse_json_object,
    build_litstyle_system_prompt,
    build_litstyle_user_prompt,
)
from bestseller.settings import get_settings  # noqa: E402

litellm.suppress_debug_info = True

SETTINGS = get_settings()
WRITER = SETTINGS.llm.writer
CRITIC = SETTINGS.llm.critic
WRITER_KEY = os.environ.get(getattr(WRITER, "api_key_env", "") or "")
CRITIC_KEY = os.environ.get(getattr(CRITIC, "api_key_env", "") or "")
CONFIG = load_litstyle_config()

DEFAULT_CHAPTERS = [f"output/oracle-pilot-dianshen/chapter-00{i}.md" for i in range(1, 5)]
OUT_PATH = "scripts/_litstyle_closed_loop_ab.json"


def _cjk_len(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


async def _complete(
    model: str,
    api_base: str | None,
    api_key: str,
    system: str,
    user: str,
    *,
    temperature: float,
    max_tokens: int,
) -> str:
    r = await litellm.acompletion(
        model=model, api_base=api_base, api_key=api_key,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature, max_tokens=max_tokens, timeout=180,
    )
    return (r.choices[0].message.content or "").strip()


async def _judge_once(text: str, genre_context: object) -> LitStyleJudgeResult:
    ai_tone = detect_ai_tone(text, CONFIG)
    system = build_litstyle_system_prompt(config=CONFIG, genre_context=genre_context)
    user = build_litstyle_user_prompt(chapter_number=1, content_md=text, ai_tone=ai_tone)
    raw = await _complete(CRITIC.model, CRITIC.api_base, CRITIC_KEY, system, user,
                          temperature=0.0, max_tokens=2500)
    return litstyle_result_from_mapping(
        _parse_json_object(raw), config=CONFIG,
        ai_tone_prior=ai_tone.deterministic_penalty, ai_tone_flagged=ai_tone.flagged,
    )


async def _judge_stable(text: str, genre_context: object, samples: int) -> LitStyleJudgeResult:
    """Median-aggregate ``samples`` judge calls (skips unavailable responses)."""

    results: list[LitStyleJudgeResult] = []
    for _ in range(max(1, samples)):
        try:
            r = await _judge_once(text, genre_context)
        except Exception as exc:
            print(f"    judge sample failed: {type(exc).__name__}: {exc}")
            continue
        if "LITSTYLE_JUDGE_UNAVAILABLE" not in r.top_issues:
            results.append(r)
    if not results:
        # All samples failed/empty — return an explicit unavailable marker.
        return litstyle_result_from_mapping({}, config=CONFIG)
    if len(results) == 1:
        return results[0]
    med = {dim.key: round(statistics.median([int(r.dimension_scores.get(dim.key, 0)) for r in results]))
           for dim in CONFIG.dimensions}
    med["ai_tone_penalty"] = round(statistics.median([r.ai_tone_penalty for r in results]))
    rep = min(results, key=lambda r: abs(r.final_score - statistics.median([x.final_score for x in results])))
    med["evidence"] = list(rep.evidence)
    med["revision_priority"] = list(rep.revision_priority)
    return litstyle_result_from_mapping(med, config=CONFIG)


async def _polish(text: str, base: LitStyleJudgeResult, *, variant: int = 0) -> str:
    system, user = build_litstyle_polish_prompt(draft=text, result=base, config=CONFIG)
    # Vary temperature per candidate so best-of-N gets diverse polish attempts.
    temp = min(0.95, 0.6 + 0.12 * variant)
    for attempt in range(2):
        out = await _complete(WRITER.model, WRITER.api_base, WRITER_KEY, system, user,
                              temperature=temp, max_tokens=6000 + attempt * 2000)
        if _cjk_len(out) >= 0.5 * _cjk_len(text):  # reject truncated polish
            return out
    return out


async def _run_chapter(
    path: str, genre: str | None, samples: int, target: int, best_of: int = 1
) -> dict | None:
    p = Path(path)
    if not p.exists():
        print(f"[{path}] missing — skipped")
        return None
    text = p.read_text(encoding="utf-8").strip()
    if _cjk_len(text) < 80:
        print(f"[{path}] too short — skipped")
        return None
    genre_context = resolve_judge_genre_context(genre=genre) if genre else None

    print(f"\n--- {p.name} ({_cjk_len(text)} CJK chars) ---")
    base = await _judge_stable(text, genre_context, samples)
    if "LITSTYLE_JUDGE_UNAVAILABLE" in base.top_issues:
        print("  base judge unavailable — skipped")
        return None
    print(f"  base   FinalScore={base.final_score}  AI腔={base.ai_tone_penalty}  level={base.level}")

    row: dict = {
        "chapter": p.name, "base_final": base.final_score, "base_ai_tone": base.ai_tone_penalty,
        "base_dims": dict(base.dimension_scores), "base_chars": _cjk_len(text),
        "polished": False,
    }
    if base.final_score >= target:
        print(f"  already ≥ target {target} — no polish needed")
        return row

    # best-of-N: generate N polish candidates, judge each, keep the best — then
    # keep-better against the original. More attempts ⇒ higher hit-rate, while
    # keep-better still guarantees the shipped chapter is never worse than base.
    candidates: list[tuple[LitStyleJudgeResult, str]] = []
    for k in range(max(1, best_of)):
        ptext = await _polish(text, base, variant=k)
        pcand = await _judge_stable(ptext, genre_context, samples)
        if "LITSTYLE_JUDGE_UNAVAILABLE" in pcand.top_issues:
            print(f"  polish#{k + 1} judge unavailable — skipped")
            continue
        candidates.append((pcand, ptext))
        print(f"  polish#{k + 1} FinalScore={pcand.final_score}  AI腔={pcand.ai_tone_penalty}  "
              f"Δ={pcand.final_score - base.final_score:+d}")
    if not candidates:
        print("  all polish candidates unavailable — keeping original")
        return row
    cand, polished_text = max(candidates, key=lambda c: c[0].final_score)
    kept_polished = cand.final_score > base.final_score
    print(f"  → best of {len(candidates)}: FinalScore={cand.final_score}  "
          f"Δ={cand.final_score - base.final_score:+d}  "
          f"→ {'KEEP polished' if kept_polished else 'KEEP original (keep-better caught regression)'}")
    row.update({
        "polished": True,
        "best_of": len(candidates),
        "polished_final": cand.final_score, "polished_ai_tone": cand.ai_tone_penalty,
        "polished_dims": dict(cand.dimension_scores), "polished_chars": _cjk_len(polished_text),
        "delta_final": cand.final_score - base.final_score,
        "kept_polished": kept_polished,
        "length_ratio": round(_cjk_len(polished_text) / max(1, _cjk_len(text)), 3),
    })
    return row


def _report(rows: list[dict], target: int) -> None:
    print("\n================ 文采闭环 A/B 结果 ================")
    print(f"{'chapter':22} {'base':>5} {'polish':>7} {'Δ':>5} {'kept':>8} {'len比':>6} {'AI腔':>8}")
    polished_rows = [r for r in rows if r.get("polished")]
    for r in rows:
        if r.get("polished"):
            print(f"{r['chapter']:22} {r['base_final']:5d} {r['polished_final']:7d} "
                  f"{r['delta_final']:+5d} {'polish' if r['kept_polished'] else 'orig':>8} "
                  f"{r.get('length_ratio', 0):6.2f} {r['base_ai_tone']}→{r['polished_ai_tone']:>3}")
        else:
            print(f"{r['chapter']:22} {r['base_final']:5d} {'—':>7} {'—':>5} {'(≥target)':>8}")

    if polished_rows:
        deltas = [r["delta_final"] for r in polished_rows]
        improved = sum(1 for d in deltas if d > 0)
        regressed = sum(1 for d in deltas if d < 0)
        avg_base = statistics.mean(r["base_final"] for r in polished_rows)
        avg_kept = statistics.mean(
            r["polished_final"] if r["kept_polished"] else r["base_final"] for r in polished_rows
        )
        print("\n---------- 汇总（仅低于 target 触发 polish 的章）----------")
        print(f"触发 polish 章数: {len(polished_rows)}")
        print(f"polish 后 final 提升: {improved} 章；持平/下降: {len(polished_rows) - improved} 章（其中下降 {regressed} 章被 keep-better 拦下）")
        print(f"平均 final: 基线 {avg_base:.1f} → 闭环保留稿 {avg_kept:.1f}（Δ={avg_kept - avg_base:+.1f}）")
        print(f"平均 polish 候选 Δ: {statistics.mean(deltas):+.1f}（min={min(deltas):+d}, max={max(deltas):+d}）")
        avg_len = statistics.mean(r.get("length_ratio", 1.0) for r in polished_rows)
        print(f"平均字数比(润色/原文): {avg_len:.2f}（越接近 1.0 越守约）")
        print("\n结论判据：①平均闭环保留稿 final > 基线 → 体系提升质量；"
              "②keep-better 保证保留稿 final ≥ 基线（绝不改差）→ 闭环安全完整。")


async def main() -> None:
    parser = argparse.ArgumentParser(description="LitStyle 文采 closed-loop A/B (real LLM).")
    parser.add_argument("paths", nargs="*", default=DEFAULT_CHAPTERS, help="chapter .md files")
    parser.add_argument("--genre", default="都市异能")
    parser.add_argument("--samples", type=int, default=2, help="judge samples per evaluation (median)")
    parser.add_argument("--target", type=int, default=85, help="polish triggers below this FinalScore")
    parser.add_argument("--best-of", type=int, default=1, dest="best_of",
                        help="generate N polish candidates, keep the best (raises hit-rate)")
    args = parser.parse_args()
    paths = args.paths or DEFAULT_CHAPTERS

    print(f"writer={WRITER.model}  critic={CRITIC.model}  samples={args.samples}  "
          f"target={args.target}  best_of={args.best_of}")
    rows: list[dict] = []
    for path in paths:
        try:
            row = await _run_chapter(path, args.genre, args.samples, args.target, args.best_of)
        except Exception as exc:
            print(f"[{path}] FAILED: {type(exc).__name__}: {exc}")
            row = None
        if row:
            rows.append(row)
            Path(OUT_PATH).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    _report(rows, args.target)
    print(f"\nraw results -> {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
