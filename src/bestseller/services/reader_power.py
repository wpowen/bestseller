from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from bestseller.services.hype_engine import HypeType, classify_hype, extract_ending_sentence

DEFAULT_GOLDEN_THREE_REQUIRED_CHAPTERS = 3
DEFAULT_GOLDEN_THREE_MIN_HYPE_CHAPTERS = 2
DEFAULT_GOLDEN_THREE_MIN_ENDING_HOOK_CHAPTERS = 2
DEFAULT_MIN_HYPE_CONFIDENCE = 2.0

_HOOK_KEYWORDS_ZH = (
    "？",
    "!",
    "！",
    "却",
    "但",
    "然而",
    "忽然",
    "突然",
    "下一刻",
    "门外",
    "身后",
    "电话",
    "倒计时",
    "真相",
    "秘密",
    "名单",
    "令牌",
    "血",
    "印记",
    "系统",
    "觉醒",
)
_HOOK_KEYWORDS_EN = (
    "?",
    "!",
    "but",
    "however",
    "suddenly",
    "then",
    "outside",
    "behind",
    "call",
    "countdown",
    "truth",
    "secret",
    "blood",
    "mark",
    "system",
    "awakens",
)
_CONFLICT_KEYWORDS_ZH = (
    "威胁",
    "追杀",
    "围住",
    "逼",
    "怒",
    "冷笑",
    "羞辱",
    "背叛",
    "禁令",
    "危险",
    "杀",
    "抢",
    "夺",
)
_CONFLICT_KEYWORDS_EN = (
    "threat",
    "hunt",
    "cornered",
    "forced",
    "rage",
    "mocked",
    "betray",
    "ban",
    "danger",
    "kill",
)
_STATUS_CHANGE_KEYWORDS_ZH = (
    "突破",
    "觉醒",
    "升级",
    "晋升",
    "反击",
    "翻盘",
    "打脸",
    "身份",
    "底牌",
    "碾压",
)
_STATUS_CHANGE_KEYWORDS_EN = (
    "breakthrough",
    "awaken",
    "level up",
    "promoted",
    "counterattack",
    "reversal",
    "identity",
    "trump card",
    "dominate",
)


@dataclass(frozen=True)
class GoldenThreeChapterSignal:
    chapter_number: int
    assigned_hype_type: HypeType | None
    classified_hype_type: HypeType | None
    classified_hype_confidence: float
    tail_hype_type: HypeType | None
    tail_hype_confidence: float
    ending_sentence: str
    has_ending_hook: bool
    signal_codes: tuple[str, ...]
    issue_codes: tuple[str, ...]

    @property
    def has_strong_hype(self) -> bool:
        return "HYPE_PRESENT" in self.signal_codes


@dataclass(frozen=True)
class GoldenThreeReport:
    enabled: bool
    chapters_checked: int
    strong_hype_chapters: int
    ending_hook_chapters: int
    chapter_signals: tuple[GoldenThreeChapterSignal, ...]
    issue_codes: tuple[str, ...]


def _as_hype_type(value: HypeType | str | None) -> HypeType | None:
    if value is None:
        return None
    if isinstance(value, HypeType):
        return value
    try:
        return HypeType(str(value))
    except ValueError:
        return None


def _keyword_table(language: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if (language or "").lower().startswith("zh"):
        return _HOOK_KEYWORDS_ZH, _CONFLICT_KEYWORDS_ZH, _STATUS_CHANGE_KEYWORDS_ZH
    return _HOOK_KEYWORDS_EN, _CONFLICT_KEYWORDS_EN, _STATUS_CHANGE_KEYWORDS_EN


def _contains_any(text: str, keywords: Sequence[str], *, case_sensitive: bool) -> bool:
    if not text:
        return False
    haystack = text if case_sensitive else text.lower()
    for keyword in keywords:
        needle = keyword if case_sensitive else keyword.lower()
        if needle and needle in haystack:
            return True
    return False


def _classify(text: str, language: str, *, segment: str) -> tuple[HypeType | None, float]:
    result = classify_hype(text, language=language, segment=segment)  # type: ignore[arg-type]
    if result is None:
        return None, 0.0
    return result[0], float(result[1])


def analyze_golden_three(
    *,
    chapter_texts: Sequence[tuple[int, str]],
    chapter_hype: Sequence[tuple[int, HypeType | str | None]] = (),
    language: str = "zh-CN",
    required_chapters: int = DEFAULT_GOLDEN_THREE_REQUIRED_CHAPTERS,
    min_hype_chapters: int = DEFAULT_GOLDEN_THREE_MIN_HYPE_CHAPTERS,
    min_ending_hook_chapters: int = DEFAULT_GOLDEN_THREE_MIN_ENDING_HOOK_CHAPTERS,
    min_hype_confidence: float = DEFAULT_MIN_HYPE_CONFIDENCE,
) -> GoldenThreeReport:
    required = max(1, int(required_chapters or DEFAULT_GOLDEN_THREE_REQUIRED_CHAPTERS))
    min_hype = max(0, int(min_hype_chapters or 0))
    min_hooks = max(0, int(min_ending_hook_chapters or 0))

    texts_by_chapter = {
        int(chapter_number): str(text or "")
        for chapter_number, text in chapter_texts
        if 1 <= int(chapter_number) <= required and str(text or "").strip()
    }
    hype_by_chapter = {
        int(chapter_number): _as_hype_type(hype_type)
        for chapter_number, hype_type in chapter_hype
        if 1 <= int(chapter_number) <= required
    }

    hook_keywords, conflict_keywords, status_keywords = _keyword_table(language)
    is_zh = (language or "").lower().startswith("zh")
    signals: list[GoldenThreeChapterSignal] = []
    missing_chapters: list[int] = []

    for chapter_number in range(1, required + 1):
        text = texts_by_chapter.get(chapter_number, "")
        if not text:
            missing_chapters.append(chapter_number)
            continue

        assigned_hype = hype_by_chapter.get(chapter_number)
        classified_hype, classified_confidence = _classify(text, language, segment="full")
        tail_hype, tail_confidence = _classify(text, language, segment="tail")
        ending_sentence = extract_ending_sentence(text, language=language)
        has_ending_hook = (
            tail_hype is not None
            or _contains_any(ending_sentence, hook_keywords, case_sensitive=is_zh)
        )

        signal_codes: list[str] = []
        if assigned_hype is not None or classified_confidence >= min_hype_confidence:
            signal_codes.append("HYPE_PRESENT")
        if has_ending_hook:
            signal_codes.append("ENDING_HOOK")
        if _contains_any(text, conflict_keywords, case_sensitive=is_zh):
            signal_codes.append("OPEN_CONFLICT")
        if _contains_any(text, status_keywords, case_sensitive=is_zh):
            signal_codes.append("STATUS_CHANGE")

        issue_codes: list[str] = []
        if "HYPE_PRESENT" not in signal_codes:
            issue_codes.append("CHAPTER_LACKS_HYPE_SIGNAL")
        if "ENDING_HOOK" not in signal_codes:
            issue_codes.append("CHAPTER_LACKS_ENDING_HOOK")
        if "OPEN_CONFLICT" not in signal_codes:
            issue_codes.append("CHAPTER_LACKS_OPEN_CONFLICT")

        signals.append(
            GoldenThreeChapterSignal(
                chapter_number=chapter_number,
                assigned_hype_type=assigned_hype,
                classified_hype_type=classified_hype,
                classified_hype_confidence=classified_confidence,
                tail_hype_type=tail_hype,
                tail_hype_confidence=tail_confidence,
                ending_sentence=ending_sentence,
                has_ending_hook=has_ending_hook,
                signal_codes=tuple(signal_codes),
                issue_codes=tuple(issue_codes),
            )
        )

    strong_hype_count = sum(1 for signal in signals if signal.has_strong_hype)
    ending_hook_count = sum(1 for signal in signals if signal.has_ending_hook)
    report_issues: list[str] = []
    if missing_chapters:
        report_issues.append("GOLDEN_THREE_INCOMPLETE")
    if strong_hype_count < min_hype:
        report_issues.append("GOLDEN_THREE_LOW_HYPE")
    if ending_hook_count < min_hooks:
        report_issues.append("GOLDEN_THREE_WEAK_ENDING_HOOKS")
    if any("CHAPTER_LACKS_OPEN_CONFLICT" in signal.issue_codes for signal in signals):
        report_issues.append("GOLDEN_THREE_WEAK_OPEN_CONFLICT")

    return GoldenThreeReport(
        enabled=True,
        chapters_checked=len(signals),
        strong_hype_chapters=strong_hype_count,
        ending_hook_chapters=ending_hook_count,
        chapter_signals=tuple(signals),
        issue_codes=tuple(report_issues),
    )


def serialize_golden_three_report(report: GoldenThreeReport) -> dict[str, Any]:
    return {
        "enabled": report.enabled,
        "chapters_checked": report.chapters_checked,
        "strong_hype_chapters": report.strong_hype_chapters,
        "ending_hook_chapters": report.ending_hook_chapters,
        "issue_codes": list(report.issue_codes),
        "chapters": [
            {
                "chapter_number": signal.chapter_number,
                "assigned_hype_type": signal.assigned_hype_type.value
                if signal.assigned_hype_type
                else None,
                "classified_hype_type": signal.classified_hype_type.value
                if signal.classified_hype_type
                else None,
                "classified_hype_confidence": signal.classified_hype_confidence,
                "tail_hype_type": signal.tail_hype_type.value if signal.tail_hype_type else None,
                "tail_hype_confidence": signal.tail_hype_confidence,
                "ending_sentence": signal.ending_sentence,
                "has_ending_hook": signal.has_ending_hook,
                "signal_codes": list(signal.signal_codes),
                "issue_codes": list(signal.issue_codes),
            }
            for signal in report.chapter_signals
        ],
    }


__all__ = [
    "GoldenThreeChapterSignal",
    "GoldenThreeReport",
    "analyze_golden_three",
    "serialize_golden_three_report",
]
