"""Pairwise blind arena for STORY/BLURB appeal vs REAL competitors.

The trustworthy story-quality gate. Absolute LLM scores proved unreliable
(calibration showed a same- or cross-family judge swings wildly per prompt), so
— exactly like the prose-layer ``benchmark_arena`` — story quality is judged by
**relative, double-blind, position-swapped pairwise win-rate against real
bestseller blurbs**. Relative judgments are stable where absolute scores are not.

Design (mirrors ``benchmark_arena.py``):
  * candidate blurb vs each same-genre real bestseller blurb (``config/
    appeal_reference_blurbs.yaml``); generic top-tier pool as fallback.
  * each pair judged twice (A/B swapped); only swap-consistent wins count, else tie.
  * de-identified prompt + explicit "don't favor a side you might recognize".
  * win-rate = (win + 0.5·tie) / pairs. ≥ ``story_winrate_min`` ⇒ competitive
    with real bestsellers ⇒ story-quality bar met.
  * the judge is INJECTED (``JudgeFn``) — testable with a fake judge, pluggable
    with a cross-family model (DeepSeek) in production.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import lru_cache

# ruff: noqa: ANN401, RUF001, RUF002, RUF003 — Chinese labels + Any session/settings.
import json
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

JudgeFn = Callable[[str, str], Awaitable[str]]
"""(system_prompt, user_prompt) -> raw model text."""


def _reference_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "appeal_reference_blurbs.yaml"


@lru_cache(maxsize=1)
def load_reference_blurbs() -> dict[str, list[dict[str, str]]]:
    """Load real-bestseller reference blurbs keyed by canonical genre."""

    path = _reference_path()
    if not path.exists():
        logger.warning("appeal reference blurbs not found at %s", path)
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.warning("Failed to load appeal reference blurbs", exc_info=True)
        return {}
    out: dict[str, list[dict[str, str]]] = {}
    for genre, items in raw.items():
        if isinstance(items, list):
            out[str(genre)] = [
                {"title": str(it.get("title", "")), "blurb": str(it.get("blurb", "")).strip()}
                for it in items
                if isinstance(it, dict) and str(it.get("blurb", "")).strip()
            ]
    return out


# Channel-aware top-up pools (审计 P1-7): a 女频 candidate must NEVER be topped
# up with 男频 references (斗破苍穹 vs 言情候选 is not a meaningful click contest),
# and vice versa. Unknown/general candidates may draw from the whole corpus.
_MALE_TOPUP_KEYS = ("xuanhuan", "urban", "xianxia", "history", "suspense", "scifi")
_FEMALE_TOPUP_KEYS = (
    "xian-yan", "gu-yan", "fantasy-romance", "female-growth",
    "female-derivative", "pure-love",
)
_GENERAL_TOPUP_KEYS = ("light-novel", "realistic")


def _genre_channel(canonical: str) -> str:
    """'male' | 'female' | 'general' for a canonical genre key (fail-open general)."""

    try:
        from bestseller.services.genre_taxonomy import get_genre

        g = get_genre(canonical)
        channels = tuple(g.channel) if g is not None else ()
    except Exception:
        channels = ()
    if "female" in channels and "male" not in channels:
        return "female"
    if "male" in channels and "female" not in channels:
        return "male"
    return "general"


def resolve_reference_set(
    genre: str | None, sub_genre: str | None = None, *, min_refs: int = 3
) -> list[dict[str, str]]:
    """Same-genre real bestseller blurbs, topped up channel-aware if scarce.

    Every returned ref carries its TRUE source genre under ``"genre"`` so the
    arena can avoid mislabeling cross-genre pairs in the judge prompt.
    """

    refs = load_reference_blurbs()
    canonical = ""
    try:
        from bestseller.services.genre_taxonomy import canonicalize

        canonical = canonicalize(genre, sub_genre) or ""
    except Exception:
        canonical = ""

    own = [{**r, "genre": canonical} for r in refs.get(canonical, [])]
    if len(own) >= min_refs:
        return own
    # Top up from the SAME channel only (female↛male, male↛female); the general
    # pool is a shared tail. De-identified, so genre leakage stays limited.
    channel = _genre_channel(canonical)
    if channel == "female":
        topup_keys = _FEMALE_TOPUP_KEYS + _GENERAL_TOPUP_KEYS
    elif channel == "male":
        topup_keys = _MALE_TOPUP_KEYS + _GENERAL_TOPUP_KEYS
    else:
        topup_keys = _MALE_TOPUP_KEYS + _FEMALE_TOPUP_KEYS + _GENERAL_TOPUP_KEYS
    seen = {r["blurb"] for r in own}
    pool: list[dict[str, str]] = []
    for g in topup_keys:
        if g == canonical:
            continue
        for r in refs.get(g, []):
            if r["blurb"] not in seen:
                pool.append({**r, "genre": g})
                seen.add(r["blurb"])
    return own + pool[: max(0, min_refs - len(own))]


# ---------------------------------------------------------------------------
# Pair + verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppealArenaPair:
    pair_id: str
    candidate_blurb: str
    reference_blurb: str
    genre: str
    reference_title: str = ""
    # TRUE source genre of the reference (canonical key) + whether this pair is a
    # cross-genre top-up — the judge prompt must not assert the candidate's genre
    # over a reference that isn't of that genre (审计 P1-7 错标题材).
    reference_genre: str = ""
    cross_genre: bool = False


@dataclass(frozen=True)
class AppealMatchResult:
    pair: AppealArenaPair
    outcome: str  # "win" | "loss" | "tie" (candidate's outcome)
    forward: str | None = None
    backward: str | None = None


def build_appeal_system_prompt() -> str:
    return (
        "你是资深网文读者代表。下面给你两个故事的【内容简介】，甲和乙。"
        "只判断一件事：作为读者，你更可能忍不住点开哪一个去读？"
        "综合考虑：卖点是否一眼抓人、冲突与代价是否强、是否有追读欲、是否新鲜不套路。"
        "重要：不要因为你可能认出某个简介的出处而偏向它；只凭简介本身的吸引力判断。"
        '只输出严格 JSON：{"winner": "甲"|"乙"|"持平", "reason": "一句话"}'
    )


def build_appeal_user_prompt(
    blurb_a: str, blurb_b: str, *, genre: str, cross_genre: bool = False
) -> str:
    # 跨题材补池对局：参照并非候选题材，断言「题材：候选题材」= 给判官喂错标签
    # （如把男频玄幻简介当言情评）。此时不标题材，只比点击欲。
    genre_line = (
        "题材：不限（两篇可能属于不同题材，只凭哪个更想点开判断）"
        if cross_genre or not genre
        else f"题材：{genre}"
    )
    return (
        f"{genre_line}\n\n【简介·甲】\n{blurb_a[:900]}\n\n【简介·乙】\n{blurb_b[:900]}\n\n"
        "哪个更让你想点开读？输出严格 JSON。"
    )


def parse_appeal_verdict(raw: str, *, candidate_is_a: bool) -> str | None:
    """Map 甲/乙/持平 → candidate outcome 'win'|'loss'|'tie'. None if unparseable."""

    s = (raw or "").strip()
    payload: dict[str, Any] | None = None
    try:
        payload = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                payload = json.loads(s[start : end + 1])
            except json.JSONDecodeError:
                payload = None
    if not isinstance(payload, dict):
        # last-resort token scan
        token = "甲" if "甲" in s and "乙" not in s else ("乙" if "乙" in s else "")
    else:
        token = str(payload.get("winner", ""))
    if token in ("持平", "tie", "平", ""):
        return "tie" if token else None
    winner_is_a = token in ("甲", "A", "a")
    if winner_is_a:
        return "win" if candidate_is_a else "loss"
    return "loss" if candidate_is_a else "win"


def _swap_consistent(forward: str | None, backward: str | None) -> str:
    """Only a consistent win across both slot orders counts; else tie."""

    if forward == "win" and backward == "win":
        return "win"
    if forward == "loss" and backward == "loss":
        return "loss"
    return "tie"


async def run_appeal_match(pair: AppealArenaPair, judge: JudgeFn) -> AppealMatchResult:
    system = build_appeal_system_prompt()
    forward_raw = await judge(
        system,
        build_appeal_user_prompt(
            pair.candidate_blurb, pair.reference_blurb,
            genre=pair.genre, cross_genre=pair.cross_genre,
        ),
    )
    backward_raw = await judge(
        system,
        build_appeal_user_prompt(
            pair.reference_blurb, pair.candidate_blurb,
            genre=pair.genre, cross_genre=pair.cross_genre,
        ),
    )
    forward = parse_appeal_verdict(forward_raw, candidate_is_a=True)
    backward = parse_appeal_verdict(backward_raw, candidate_is_a=False)
    if forward is None or backward is None:
        return AppealMatchResult(pair=pair, outcome="tie", forward=forward, backward=backward)
    return AppealMatchResult(
        pair=pair, outcome=_swap_consistent(forward, backward), forward=forward, backward=backward
    )


@dataclass(frozen=True)
class AppealArenaSummary:
    genre: str
    pairs: int
    wins: int
    losses: int
    ties: int
    win_rate: float
    details: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "genre": self.genre,
            "pairs": self.pairs,
            "wins": self.wins,
            "losses": self.losses,
            "ties": self.ties,
            "win_rate": self.win_rate,
            "details": list(self.details),
            "schema_version": "appeal-arena.v1",
        }


def summarize_appeal(results: list[AppealMatchResult], *, genre: str) -> AppealArenaSummary:
    pairs = len(results)
    if pairs == 0:
        return AppealArenaSummary(genre=genre, pairs=0, wins=0, losses=0, ties=0, win_rate=0.0)
    wins = sum(1 for r in results if r.outcome == "win")
    losses = sum(1 for r in results if r.outcome == "loss")
    ties = pairs - wins - losses
    details = tuple(
        {"ref": r.pair.reference_title, "ref_genre": r.pair.reference_genre, "outcome": r.outcome}
        for r in results
    )
    return AppealArenaSummary(
        genre=genre, pairs=pairs, wins=wins, losses=losses, ties=ties,
        win_rate=round((wins + 0.5 * ties) / pairs, 3), details=details,
    )


def _fair_length(text: str, max_chars: int) -> str:
    """Truncate an over-long candidate to a fair comparison length.

    Real bestseller reference blurbs are ~80-150 chars. A 400+ char synopsis
    would win on sheer content volume, inflating the win-rate (this is exactly
    how 《废代码库》's 460-char synopsis got 0.83). Cap the candidate at a
    platform-blurb length, cutting at the last sentence boundary so it stays
    coherent.
    """

    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    head = t[:max_chars]
    for sep in ("。", "！", "？", "\n", "；"):
        idx = head.rfind(sep)
        if idx >= max_chars * 0.5:
            return head[: idx + 1]
    return head


async def run_appeal_arena(
    *,
    candidate_blurb: str,
    genre: str | None,
    sub_genre: str | None = None,
    judge: JudgeFn,
    min_refs: int = 3,
    max_refs: int = 6,
    candidate_max_chars: int = 220,
) -> AppealArenaSummary:
    """Score a candidate blurb's click-appeal vs real bestsellers (win-rate).

    The candidate is truncated to ``candidate_max_chars`` so a long synopsis
    cannot win on length alone against the (short) reference blurbs.
    """

    candidate = _fair_length(candidate_blurb, candidate_max_chars)
    refs = resolve_reference_set(genre, sub_genre, min_refs=min_refs)[:max_refs]
    canonical = genre or ""
    try:
        from bestseller.services.genre_taxonomy import canonicalize

        candidate_canonical = canonicalize(genre, sub_genre) or ""
    except Exception:
        candidate_canonical = ""
    pairs = [
        AppealArenaPair(
            pair_id=f"appeal-{i}",
            candidate_blurb=candidate,
            reference_blurb=r["blurb"],
            genre=str(genre or ""),
            reference_title=r.get("title", ""),
            reference_genre=str(r.get("genre", "")),
            # 补池参照非候选题材（或候选题材未知）→ 判官 prompt 不得错标题材
            cross_genre=(
                not candidate_canonical
                or str(r.get("genre", "")) != candidate_canonical
            ),
        )
        for i, r in enumerate(refs)
    ]
    results: list[AppealMatchResult] = []
    for pair in pairs:
        try:
            results.append(await run_appeal_match(pair, judge))
        except Exception:
            logger.warning("appeal arena pair %s failed; scoring tie", pair.pair_id, exc_info=True)
            results.append(AppealMatchResult(pair=pair, outcome="tie"))
    return summarize_appeal(results, genre=str(canonical))


def make_deepseek_judge(
    session: Any, settings: Any, *, model_key: str = "deepseek-v4-flash"
) -> JudgeFn:
    """Cross-family judge fn backed by complete_text (DeepSeek by default)."""

    async def _judge(system_prompt: str, user_prompt: str) -> str:
        from bestseller.services.llm import (
            LLMCompletionRequest,
            complete_text,
        )

        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                model_tier="strong",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response='{"winner": "持平", "reason": "judge unavailable"}',
                prompt_template="appeal_arena_judge",
                prompt_version="v1",
                model_catalog_key=model_key,
                max_tokens_override=400,
            ),
        )
        return completion.content or ""

    return _judge


__all__ = [
    "AppealArenaPair",
    "AppealArenaSummary",
    "AppealMatchResult",
    "JudgeFn",
    "build_appeal_system_prompt",
    "build_appeal_user_prompt",
    "load_reference_blurbs",
    "make_deepseek_judge",
    "parse_appeal_verdict",
    "resolve_reference_set",
    "run_appeal_arena",
    "run_appeal_match",
    "summarize_appeal",
]
