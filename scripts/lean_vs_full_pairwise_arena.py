"""Lean vs full prose-profile pairwise blind arena (quality plan B2).

Usage (after you have paired chapter drafts on disk):

  python scripts/lean_vs_full_pairwise_arena.py \\
      --lean-dir output/my-book-lean/volumes \\
      --full-dir output/my-book-full/volumes \\
      --chapters 1,2,3,5,8,10,15,20,30,40 \\
      --out output/_arena/lean-vs-full.json

Each chapter pair is position-swapped (lean as arm A then arm B) to cancel
slot bias. Does not modify production books or pipeline defaults.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
load_dotenv(".env")

import litellm  # noqa: E402

from bestseller.services.benchmark_arena import ArenaMatchResult  # noqa: E402
from bestseller.services.lean_full_arena import (  # noqa: E402
    ARENA_MIN_PAIRS,
    ProfileDraftPair,
    anonymize_for_judge,
    run_profile_pair_match,
    summarize_lean_wins,
    to_arena_pair,
)
from bestseller.settings import get_settings  # noqa: E402

litellm.suppress_debug_info = True

_CH_RE = re.compile(r"ch-(\d+)", re.IGNORECASE)


def _read_chapter_md(root: Path, chapter_number: int) -> str | None:
    patterns = (
        f"**/ch-{chapter_number:03d}*.md",
        f"**/ch-{chapter_number:02d}*.md",
        f"**/ch-{chapter_number}*.md",
    )
    for pattern in patterns:
        hits = sorted(root.glob(pattern))
        if hits:
            return hits[0].read_text(encoding="utf-8")
    for path in sorted(root.rglob("*.md")):
        match = _CH_RE.search(path.name)
        if match and int(match.group(1)) == chapter_number:
            return path.read_text(encoding="utf-8")
    return None


def _strip_frontmatter(text: str) -> str:
    body = text or ""
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4 :]
    return body.strip()


async def _llm_judge(system_prompt: str, user_prompt: str) -> str:
    settings = get_settings()
    critic = settings.llm.critic
    api_key = os.environ.get(getattr(critic, "api_key_env", "") or "") or None
    response = await litellm.acompletion(
        model=critic.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=1200,
        api_base=getattr(critic, "api_base", None) or None,
        api_key=api_key,
    )
    return str(response.choices[0].message.content or "")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    lean_root = Path(args.lean_dir)
    full_root = Path(args.full_dir)
    chapters = [int(x) for x in str(args.chapters).split(",") if str(x).strip()]
    pairs: list[ProfileDraftPair] = []
    missing: list[int] = []
    for ch in chapters:
        lean_raw = _read_chapter_md(lean_root, ch)
        full_raw = _read_chapter_md(full_root, ch)
        if not lean_raw or not full_raw:
            missing.append(ch)
            continue
        pairs.append(
            ProfileDraftPair(
                pair_id=f"ch-{ch:03d}",
                chapter_number=ch,
                lean_text=anonymize_for_judge(_strip_frontmatter(lean_raw)),
                full_text=anonymize_for_judge(_strip_frontmatter(full_raw)),
            )
        )

    arena_results: list[ArenaMatchResult] = []
    rows: list[dict[str, Any]] = []
    for item in pairs:
        match = await run_profile_pair_match(item, _llm_judge)
        arena_results.append(match)
        rows.append(
            {
                "pair_id": item.pair_id,
                "chapter_number": item.chapter_number,
                "outcome": match.outcome,
                "dimension_outcomes": match.dimension_outcomes,
                "lean_label": to_arena_pair(item).framework_label,
                "full_label": to_arena_pair(item).benchmark_label,
            }
        )

    summary = summarize_lean_wins(arena_results, min_pairs=int(args.min_pairs))
    verdict = str(summary.get("verdict") or "")
    payload = {
        "lean_dir": str(lean_root),
        "full_dir": str(full_root),
        "missing_chapters": missing,
        "results": rows,
        "summary": summary,
        # ``verdict`` is the field to read. ``pass`` is kept for older consumers
        # but is now true only for a POWERED lean win — an underpowered run
        # reports ``inconclusive_underpowered`` rather than borrowing a verdict
        # from a handful of pairs.
        "verdict": verdict,
        "underpowered": bool(summary.get("underpowered")),
        "pass": bool(summary.get("pass_suggested_threshold")),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if summary.get("underpowered"):
        print(
            f"[arena] 样本量不足：{summary['pairs']} 对 < {summary['min_pairs']} 对，"
            "本次结果不能作为结论（历史教训：N=3 噪声 ±1.5 分足以淹没信号）。",
            file=sys.stderr,
        )
    else:
        print(
            f"[arena] verdict={verdict} pairs={summary['pairs']} "
            f"win_rate={summary['lean_win_rate']} p={summary['p_value']}",
            file=sys.stderr,
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lean-dir", required=True)
    parser.add_argument("--full-dir", required=True)
    parser.add_argument(
        "--chapters",
        default="1,2,3,5,8,10,15,20,30,40",
        help="Comma-separated chapter numbers (target N≈10).",
    )
    parser.add_argument("--out", default="output/_arena/lean-vs-full.json")
    parser.add_argument(
        "--min-pairs",
        type=int,
        default=ARENA_MIN_PAIRS,
        help=(
            "Judged pairs required before a direction may be claimed "
            f"(default {ARENA_MIN_PAIRS}, per plan §6). Lower it only for a "
            "deliberate pilot — the report then says so."
        ),
    )
    args = parser.parse_args()
    payload = asyncio.run(_run(args))
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"wrote {args.out}")
    if payload["summary"].get("pairs", 0) == 0:
        return 0
    return 0 if payload.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
