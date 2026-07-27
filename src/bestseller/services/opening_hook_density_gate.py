"""Post-generation opening hook density gate for golden-three chapters."""

# ruff: noqa: RUF001

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")
_SENTENCE_SPLIT_RE = re.compile(r"[。？！!?]")

_ANOMALY_KEYWORDS = (
    "没有",
    "不对",
    "不见",
    "缺",
    "空",
    "冷",
    "突然",
    "反",
    "逆",
    "消失",
    "镜",
    "影",
    "动",
    "响",
    "亮",
    "灭",
    "黑",
    "热",
)

_FLASHBACK_KEYWORDS = (
    "年前",
    "那时候",
    "记得",
    "想起",
    "回忆",
    "当年",
    "小时候",
    "曾经",
    "从前",
    "过去",
    "以前",
)


@dataclass(frozen=True)
class OpeningHookFinding:
    code: str
    severity: str
    detail: str
    evidence: dict[str, Any]


def check_opening_hook_density(
    chapter_text: str,
    chapter_number: int,
    *,
    anomaly_threshold: int = 2,
    flashback_max: int = 2,
    first_sentence_max_cjk: int = 25,
    first_paragraph_max_cjk: int = 50,
) -> list[OpeningHookFinding]:
    """Return golden-three opening findings. Empty list means no finding."""

    if chapter_number > 3 or not chapter_text:
        return []

    cjk_chars = _CJK_RE.findall(chapter_text)
    if len(cjk_chars) < 100:
        return []

    findings: list[OpeningHookFinding] = []
    lines = [
        line.strip()
        for line in chapter_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    if lines:
        first_line = lines[0]
        first_sentence = _SENTENCE_SPLIT_RE.split(first_line, maxsplit=1)[0]
        first_sentence_len = _count_cjk(first_sentence)
        if first_sentence_len > first_sentence_max_cjk:
            findings.append(
                OpeningHookFinding(
                    code="OPENING_FIRST_SENTENCE_TOO_LONG",
                    severity="high",
                    detail=f"第一句 {first_sentence_len} 字，超 {first_sentence_max_cjk} 字硬上限",
                    evidence={"first_sentence": first_sentence[:120]},
                )
            )

        first_para_len = _count_cjk(first_line)
        if first_para_len > first_paragraph_max_cjk:
            findings.append(
                OpeningHookFinding(
                    code="OPENING_FIRST_PARAGRAPH_TOO_LONG",
                    severity="medium",
                    detail=f"第一段 {first_para_len} 字，超 {first_paragraph_max_cjk} 字软上限",
                    evidence={"first_paragraph": first_line[:200]},
                )
            )

    first_200 = "".join(cjk_chars[:200])
    anomaly_hits = sum(1 for keyword in _ANOMALY_KEYWORDS if keyword in first_200)
    if anomaly_hits < anomaly_threshold:
        findings.append(
            OpeningHookFinding(
                # ADVISORY, not blocking. Counting twelve words is a proxy for
                # "does this opening grab", and the proxy fails exactly where
                # the prose is strongest: an anomaly carried by ACTION scores
                # zero. It blocked the first chapter this framework ever
                # produced — a coercion scene with a countdown, a crumpled IOU
                # and a concrete threat in the first 200 characters
                # (2026-07-26, urban-power-reversal-1785026717 ch1).
                # Write-safety promotion keys off {critical, high}, so anything
                # below that bar reports without vetoing. Same lesson as the
                # scene emotion/hook scorer, which punished show-don't-tell for
                # the same reason: a lexical count cannot judge whether a scene
                # is gripping, and must not be the thing that stops a book.
                code="OPENING_NO_ANOMALY",
                severity="medium",
                detail=f"前 200 字仅含 {anomaly_hits} 个异常信号关键词，开篇可能不够抓人",
                evidence={"first_200": first_200, "hits": anomaly_hits},
            )
        )

    first_500 = "".join(cjk_chars[:500])
    flashback_hits = sum(first_500.count(keyword) for keyword in _FLASHBACK_KEYWORDS)
    if flashback_hits > flashback_max:
        findings.append(
            OpeningHookFinding(
                code="OPENING_FLASHBACK_OVERUSE",
                severity="critical",
                detail=f"前 500 字含 {flashback_hits} 个倒叙信号，节奏破坏",
                evidence={"first_500_head": first_500[:300], "hits": flashback_hits},
            )
        )

    return findings


def _count_cjk(text: str) -> int:
    return len(_CJK_RE.findall(text or ""))


__all__ = ["OpeningHookFinding", "check_opening_hook_density"]
