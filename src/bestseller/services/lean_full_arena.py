"""Lean vs full prose-profile pairwise arena helpers (plan B2).

Does not call LLMs. Scripts inject a judge; unit tests use a fake judge.
Reuses ``benchmark_arena`` position-swap semantics with profile labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bestseller.services.benchmark_arena import (
    ArenaMatchResult,
    ArenaPair,
    ArenaVerdict,
    run_arena_match,
)


@dataclass(frozen=True)
class ProfileDraftPair:
    """One same-chapter lean/full draft pair for blind comparison."""

    pair_id: str
    chapter_number: int
    lean_text: str
    full_text: str
    lean_label: str = "lean"
    full_label: str = "full"


def to_arena_pair(item: ProfileDraftPair) -> ArenaPair:
    """Map lean→framework arm and full→benchmark arm for reuse of arena code."""

    return ArenaPair(
        pair_id=item.pair_id,
        framework_text=item.lean_text,
        benchmark_text=item.full_text,
        benchmark_tier="full-profile",
        category="prose-profile",
        chapter_number=item.chapter_number,
        framework_label=item.lean_label,
        benchmark_label=item.full_label,
    )


async def run_profile_pair_match(
    item: ProfileDraftPair,
    judge_fn,
) -> ArenaMatchResult:
    """Position-swap blind match: lean vs full for one chapter."""

    return await run_arena_match(to_arena_pair(item), judge_fn)


#: Minimum judged pairs before a lean-vs-full run may claim a direction.
#: Plan §6 specifies N≈10 per configuration. The number is not arbitrary: the
#: measured round-to-round noise of this blind rank is ±1.5 points, which
#: previously let an N=3 run "confirm" the opening-jargon lever that a later,
#: larger run falsified.
ARENA_MIN_PAIRS = 10

#: Lean must clear this share of decisive pairs to count as better. Matched to
#: the plan's "full 不得稳定赢" bar rather than a bare majority.
ARENA_LEAN_WIN_RATE_BAR = 0.55


def _sign_test_p_value(wins: int, losses: int) -> float:
    """Two-sided exact binomial p-value under H0: lean and full are equal.

    Ties are excluded, per the standard sign test — a tie carries no
    directional evidence. Exact rather than normal-approximated because the
    sample sizes here (10-40) are precisely where the approximation is worst.
    """

    from math import comb

    n = wins + losses
    if n <= 0:
        return 1.0
    observed = abs(wins - n / 2)
    total = 0.0
    for k in range(n + 1):
        if abs(k - n / 2) >= observed:
            total += comb(n, k)
    return min(1.0, total / (2.0**n))


def summarize_lean_wins(
    results: list[ArenaMatchResult],
    *,
    min_pairs: int = ARENA_MIN_PAIRS,
) -> dict[str, Any]:
    """Summarize the lean arm's performance, refusing to over-read small runs.

    ``verdict`` is the field to act on:

    ``inconclusive_underpowered``
        Fewer than ``min_pairs`` judged pairs. NOT a tie and NOT a failure —
        the run simply cannot answer the question. This exists because the
        previous implementation returned ``pass_suggested_threshold`` for any
        sample size, so a single lucky pair produced ``pass: true``.
    ``lean_better`` / ``full_better``
        Powered, separated, and significant at p < 0.05.
    ``no_difference``
        Powered but the split does not separate.

    ``pass_suggested_threshold`` stays for backward compatibility and is now
    true only for a powered ``lean_better``.
    """

    wins = sum(1 for r in results if r.outcome == "win")
    losses = sum(1 for r in results if r.outcome == "loss")
    ties = sum(1 for r in results if r.outcome == "tie")
    n = len(results)
    decisive = wins + losses
    rate = (wins + 0.5 * ties) / n if n else 0.0
    p_value = _sign_test_p_value(wins, losses)
    underpowered = n < max(int(min_pairs), 0)

    if underpowered:
        verdict = "inconclusive_underpowered"
    elif decisive == 0 or p_value >= 0.05:
        verdict = "no_difference"
    elif rate >= ARENA_LEAN_WIN_RATE_BAR and wins > losses:
        verdict = "lean_better"
    elif losses > wins:
        verdict = "full_better"
    else:
        verdict = "no_difference"

    return {
        "pairs": n,
        "decisive_pairs": decisive,
        "lean_win_rate": round(rate, 4),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "min_pairs": int(min_pairs),
        "underpowered": underpowered,
        "p_value": round(p_value, 6),
        "verdict": verdict,
        "pass_suggested_threshold": verdict == "lean_better",
    }


def anonymize_for_judge(text: str, *, max_chars: int = 4500) -> str:
    """Strip obvious profile/meta labels and window the draft for the judge."""

    body = (text or "").strip()
    for token in ("【lean】", "【full】", "prose_prompt_profile", "生效片段"):
        body = body.replace(token, "")
    if max_chars > 0 and len(body) > max_chars:
        head = max_chars // 2
        tail = max_chars - head
        body = body[:head] + "\n…\n" + body[-tail:]
    return body


__all__ = [
    "ARENA_LEAN_WIN_RATE_BAR",
    "ARENA_MIN_PAIRS",
    "ProfileDraftPair",
    "anonymize_for_judge",
    "run_profile_pair_match",
    "summarize_lean_wins",
    "to_arena_pair",
    "ArenaVerdict",
]
