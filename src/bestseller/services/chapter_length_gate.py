"""Chapter Length Gate — enforce minimum chapter body size.

The framework previously had no gate on chapter body length, allowing
1300-character chapters to ship even though commercial bestseller
chapters average 2500-4000 Chinese characters. Short chapters are the
single biggest "省事感" tell — the model takes the cheapest path and
declares completion.

This gate runs after assembly. It counts Chinese characters (CJK
unified ideographs) only — markdown headings, latin punctuation, and
whitespace do not contribute to the word count.

Severity ladder
---------------
- ``critical`` ``CHAPTER_TOO_SHORT`` — below the hard floor; auto-repair.
- ``high`` ``CHAPTER_BELOW_TARGET`` — within "soft warning" band; advisory.
- pass — at or above target.

Default thresholds (overridable per project via series-bible):
- hard floor: 2000 zh chars
- soft warning: 2500 zh chars

Block code: ``CHAPTER_TOO_SHORT`` — eligible for auto-repair.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)


CHAPTER_TOO_SHORT_BLOCK_CODE: Final[str] = "CHAPTER_TOO_SHORT"
CHAPTER_BELOW_TARGET_BLOCK_CODE: Final[str] = "CHAPTER_BELOW_TARGET"

# Default thresholds. Chinese commercial serial fiction lands 2500-4000
# zh-chars per chapter; below 2000 reads as a sketch.
DEFAULT_HARD_FLOOR_ZH_CHARS: Final[int] = 2000
DEFAULT_SOFT_WARNING_ZH_CHARS: Final[int] = 2500

# Match any CJK unified ideograph plus the common extension. We
# deliberately exclude latin chars, digits, and punctuation: a chapter
# with 5000 markdown rules but only 800 zh chars is still too short.
_CJK_CHAR_RE = re.compile(r"[㐀-䶿一-鿿]")


@dataclass(frozen=True)
class ChapterLengthFinding:
    severity: str  # "critical" | "high" | "info"
    code: str
    detail: str
    zh_char_count: int
    hard_floor: int
    soft_warning: int


@dataclass(frozen=True)
class ChapterLengthReport:
    chapter_position: int
    finding: ChapterLengthFinding

    @property
    def passed(self) -> bool:
        return self.finding.severity == "info"

    @property
    def has_critical(self) -> bool:
        return self.finding.severity == "critical"


def count_zh_chars(text: str) -> int:
    """Count CJK characters in *text* — the only "real word count" metric.

    Latin characters, digits, punctuation, markdown formatting and
    whitespace are excluded. This matches how Chinese commercial
    readers count words.
    """

    if not text:
        return 0
    return sum(1 for _ in _CJK_CHAR_RE.finditer(text))


def check_chapter_length(
    chapter_text: str,
    *,
    chapter_position: int,
    hard_floor: int = DEFAULT_HARD_FLOOR_ZH_CHARS,
    soft_warning: int = DEFAULT_SOFT_WARNING_ZH_CHARS,
) -> ChapterLengthReport:
    """Score chapter body length against the configured thresholds."""

    zh_count = count_zh_chars(chapter_text)

    if zh_count < hard_floor:
        severity = "critical"
        code = CHAPTER_TOO_SHORT_BLOCK_CODE
        detail = (
            f"chapter has {zh_count} CJK chars, "
            f"below hard floor {hard_floor} — fails commercial minimum; "
            f"target ≥ {soft_warning} zh chars"
        )
    elif zh_count < soft_warning:
        severity = "high"
        code = CHAPTER_BELOW_TARGET_BLOCK_CODE
        detail = (
            f"chapter has {zh_count} CJK chars, "
            f"between floor {hard_floor} and target {soft_warning} — "
            f"advisory only, prefer reaching {soft_warning}"
        )
    else:
        severity = "info"
        code = "CHAPTER_LENGTH_OK"
        detail = f"chapter has {zh_count} CJK chars ≥ target {soft_warning}"

    return ChapterLengthReport(
        chapter_position=chapter_position,
        finding=ChapterLengthFinding(
            severity=severity,
            code=code,
            detail=detail,
            zh_char_count=zh_count,
            hard_floor=hard_floor,
            soft_warning=soft_warning,
        ),
    )


def render_chapter_length_block(
    *,
    hard_floor: int = DEFAULT_HARD_FLOOR_ZH_CHARS,
    soft_warning: int = DEFAULT_SOFT_WARNING_ZH_CHARS,
    language: str = "zh-CN",
) -> str:
    """Render a writing-prompt block telling the LLM the target band."""

    if language.lower().startswith("zh"):
        return (
            "【章节体量门 — 必须满足商业连载标准】\n"
            f"- 本章正文 CJK 字数下限：{hard_floor} 字（低于此数视为残章，触发重写）。\n"
            f"- 本章正文 CJK 字数目标：{soft_warning} 字以上（榜单连载标准）。\n"
            "- CJK 字数 = 中文汉字（不含标点/空白/英文字母/数字）。\n"
            "- 不要靠拼接重复段、堆描述性形容词凑字数；通过加签名场景、"
            "深化情绪、补足因果链来扩展。"
        )

    return (
        f"[Chapter length gate — minimum {hard_floor} CJK chars, "
        f"target {soft_warning}]"
    )


def render_chapter_length_violation_block(
    report: ChapterLengthReport,
    *,
    language: str = "zh-CN",
) -> str:
    """Render a rewrite-prompt block when the chapter is too short."""

    if report.passed:
        return ""
    if language.lower().startswith("zh"):
        lines = [
            "【章节体量门 — 本章必须扩写】",
            f"- 当前 CJK 字数：{report.finding.zh_char_count}。",
            f"- 目标 CJK 字数：≥ {report.finding.soft_warning}。",
            f"- 距离目标差距：{report.finding.soft_warning - report.finding.zh_char_count} 字。",
            "- 扩写方法（按优先级）：",
            "  ① 加 1 个签名画面（视觉爆点 + 不可替代物件 + 反常动作）。",
            "  ② 把现有强场景的情绪 / 感官 / 心理深化（嗅觉 / 触觉 / 内心独白）。",
            "  ③ 补足事件之间的因果链：A 发生 → 主角的反应 → B 才发生。",
            "  ④ 拒绝灌水：禁止重复同义句、禁止形容词堆叠。",
        ]
        return "\n".join(lines)
    return f"[Chapter too short: {report.finding.zh_char_count} chars]"


__all__ = [
    "CHAPTER_BELOW_TARGET_BLOCK_CODE",
    "CHAPTER_TOO_SHORT_BLOCK_CODE",
    "DEFAULT_HARD_FLOOR_ZH_CHARS",
    "DEFAULT_SOFT_WARNING_ZH_CHARS",
    "ChapterLengthFinding",
    "ChapterLengthReport",
    "check_chapter_length",
    "count_zh_chars",
    "render_chapter_length_block",
    "render_chapter_length_violation_block",
]
