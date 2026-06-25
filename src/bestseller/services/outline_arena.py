"""Blind pairwise arena for OUTLINE-gist and CHAPTER-OPENING appeal.

Closes root-cause #1 of the quality blind spot: the trustworthy signal
(``premise_appeal_arena`` blind pairwise vs real bestsellers) only ever judged
the 200-char blurb. A book with a competitive blurb but a generic outline /
hollow opening sailed through. This module extends the *same* double-blind,
position-swapped, cross-family-judge machinery to two deeper artifacts:

  * **outline gist** — the book's premise + first-three-chapter plan, vs real
    bestseller first-act gists (``config/appeal_reference_outlines.yaml``).
  * **chapter opening** — the materialized chapter-1 opening prose, vs real
    bestseller chapter-1 openings (``config/appeal_reference_openings.yaml``).

It reuses the verdict parsing / swap-consistency / summary from
``premise_appeal_arena`` verbatim (single source for the relative-judgement
logic); only the prompt and the reference corpus differ.

The reference corpora must be **hand-curated real bestseller excerpts** — the
arena is only trustworthy because it compares against real top-tier work. The
shipped YAMLs are scaffolds with instructions; until a genre is populated,
``run_*`` returns an empty summary (``pairs == 0``) and the caller treats the
score as "暂无 / 待对标" rather than fabricating a signal.
"""

from __future__ import annotations

# ruff: noqa: RUF001 — Chinese punctuation in judge prompts is intentional.
from collections.abc import Awaitable, Callable
from functools import lru_cache
import logging
from pathlib import Path

import yaml

from bestseller.services.premise_appeal_arena import (
    AppealArenaPair,
    AppealArenaSummary,
    AppealMatchResult,
    _fair_length,
    _swap_consistent,
    parse_appeal_verdict,
    summarize_appeal,
)

logger = logging.getLogger(__name__)

JudgeFn = Callable[[str, str], Awaitable[str]]


def _config_path(name: str) -> Path:
    return Path(__file__).resolve().parents[3] / "config" / name


@lru_cache(maxsize=2)
def _load_reference(file_name: str, text_key: str) -> dict[str, list[dict[str, str]]]:
    path = _config_path(file_name)
    if not path.exists():
        logger.warning("arena reference corpus missing: %s", path)
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.warning("Failed to load arena reference %s", file_name, exc_info=True)
        return {}
    out: dict[str, list[dict[str, str]]] = {}
    for genre, items in raw.items():
        if not isinstance(items, list):
            continue
        rows = [
            {"title": str(it.get("title", "")), "text": str(it.get(text_key, "")).strip()}
            for it in items
            if isinstance(it, dict) and str(it.get(text_key, "")).strip()
        ]
        if rows:
            out[str(genre)] = rows
    return out


def load_reference_outlines() -> dict[str, list[dict[str, str]]]:
    return _load_reference("appeal_reference_outlines.yaml", "gist")


def load_reference_openings() -> dict[str, list[dict[str, str]]]:
    return _load_reference("appeal_reference_openings.yaml", "opening")


def _resolve_refs(
    corpus: dict[str, list[dict[str, str]]],
    genre: str | None,
    sub_genre: str | None,
    *,
    max_refs: int,
) -> list[dict[str, str]]:
    canonical = ""
    try:
        from bestseller.services.genre_taxonomy import canonicalize

        canonical = canonicalize(genre, sub_genre) or ""
    except Exception:
        canonical = ""
    own = list(corpus.get(canonical, [])) or list(corpus.get(str(genre or ""), []))
    return own[:max_refs]


# --- prompts ----------------------------------------------------------------

_KIND_PROMPTS: dict[str, tuple[str, str]] = {
    "outline": (
        "你是资深网文读者代表。下面是两本书的【开篇梗概】（前三章会发生什么），甲和乙。"
        "只判断一件事：作为读者，看完梗概你更想追读哪一本？"
        "综合：开局是否抓人、冲突与代价是否强、是否有持续追读的钩子、是否新鲜不套路。"
        "重要：不要因为可能认出出处而偏向；只凭梗概本身的追读欲判断。"
        '只输出严格 JSON：{"winner": "甲"|"乙"|"持平", "reason": "一句话"}',
        "开篇梗概",
    ),
    "opening": (
        "你是资深网文读者代表。下面是两本书的【第一章开头】，甲和乙。"
        "只判断一件事：作为读者，读这段开头你更想继续读哪一本？"
        "综合：是否一句话入戏、有无画面感与悬念、文字是否克制不AI腔、结尾有没有让你想翻页。"
        "重要：不要因为可能认出出处而偏向；只凭文字本身的吸引力判断。"
        '只输出严格 JSON：{"winner": "甲"|"乙"|"持平", "reason": "一句话"}',
        "第一章开头",
    ),
}


def _user_prompt(kind: str, text_a: str, text_b: str, *, genre: str) -> str:
    _, label = _KIND_PROMPTS[kind]
    return (
        f"题材：{genre}\n\n【{label}·甲】\n{text_a[:1100]}\n\n【{label}·乙】\n{text_b[:1100]}\n\n"
        "哪个更让你想继续读？输出严格 JSON。"
    )


async def _run_match(kind: str, pair: AppealArenaPair, judge: JudgeFn) -> AppealMatchResult:
    system = _KIND_PROMPTS[kind][0]
    forward_raw = await judge(
        system, _user_prompt(kind, pair.candidate_blurb, pair.reference_blurb, genre=pair.genre)
    )
    backward_raw = await judge(
        system, _user_prompt(kind, pair.reference_blurb, pair.candidate_blurb, genre=pair.genre)
    )
    forward = parse_appeal_verdict(forward_raw, candidate_is_a=True)
    backward = parse_appeal_verdict(backward_raw, candidate_is_a=False)
    if forward is None or backward is None:
        return AppealMatchResult(pair=pair, outcome="tie", forward=forward, backward=backward)
    return AppealMatchResult(
        pair=pair, outcome=_swap_consistent(forward, backward), forward=forward, backward=backward
    )


async def run_outline_arena(
    *,
    kind: str,
    candidate_text: str,
    genre: str | None,
    sub_genre: str | None = None,
    judge: JudgeFn,
    max_refs: int = 6,
    candidate_max_chars: int = 900,
) -> AppealArenaSummary:
    """Score a candidate outline-gist or opening vs real bestsellers (win-rate).

    ``kind`` is "outline" or "opening". Returns an empty summary (pairs == 0)
    when the genre has no curated reference corpus — the caller must treat that
    as "暂无对标", never as a pass/fail.
    """

    if kind not in _KIND_PROMPTS:
        raise ValueError(f"Unknown arena kind: {kind!r}")
    corpus = load_reference_outlines() if kind == "outline" else load_reference_openings()
    refs = _resolve_refs(corpus, genre, sub_genre, max_refs=max_refs)
    candidate = _fair_length(candidate_text, candidate_max_chars)
    pairs = [
        AppealArenaPair(
            pair_id=f"{kind}-{i}",
            candidate_blurb=candidate,
            reference_blurb=r["text"],
            genre=str(genre or ""),
            reference_title=r.get("title", ""),
        )
        for i, r in enumerate(refs)
    ]
    results: list[AppealMatchResult] = []
    for pair in pairs:
        try:
            results.append(await _run_match(kind, pair, judge))
        except Exception:
            logger.warning("outline arena pair %s failed; scoring tie", pair.pair_id, exc_info=True)
            results.append(AppealMatchResult(pair=pair, outcome="tie"))
    return summarize_appeal(results, genre=str(genre or ""))


__all__ = [
    "JudgeFn",
    "load_reference_openings",
    "load_reference_outlines",
    "run_outline_arena",
]
