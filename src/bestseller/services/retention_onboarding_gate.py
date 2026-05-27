"""Retention-focused onboarding gates for commercial serial packages.

The commercial package gate catches canon drift and broad reader-contract
problems. These checks target the opening retention cliff: too many new
concepts, backwards clock anchors, overloaded beats, early reveals, abstract
hooks, and repeated opening forms.
"""

# ruff: noqa: RUF001

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml

from bestseller.domain.gate_verdict import GateFinding, GateVerdict


@dataclass(frozen=True)
class RetentionChapter:
    chapter_no: int
    title: str
    text: str
    path: Path


@dataclass(frozen=True)
class CanonicalTerm:
    term: str
    category: str = "term"
    count_onboarding: bool = True


@dataclass(frozen=True)
class RevealRule:
    rule_id: str
    earliest_chapter: int
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class RetentionGateConfig:
    terms: tuple[CanonicalTerm, ...] = ()
    reveals: tuple[RevealRule, ...] = ()
    onboarding_thresholds: Mapping[int, int] | None = None
    front_beat_threshold: int = 5
    later_beat_threshold: int = 7

    @property
    def active(self) -> bool:
        return bool(self.terms or self.reveals)


DEFAULT_ONBOARDING_THRESHOLDS: dict[int, int] = {1: 5, 2: 8, 5: 14, 10: 20}
CHARACTER_CATEGORIES = {"character", "person", "cast", "人物", "角色"}
OBJECT_CATEGORIES = {"object", "artifact", "rule", "method", "物件", "规则", "术法"}
PLACE_CATEGORIES = {"place", "setting", "location", "space", "地点", "空间"}

_CHAPTER_PATH_RE = re.compile(r"chapter-(\d+)\.md$")
_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)")
_SHICHEN_MINUTES = {
    "子时": 23 * 60,
    "丑时": 1 * 60,
    "寅时": 3 * 60,
    "卯时": 5 * 60,
    "辰时": 7 * 60,
    "巳时": 9 * 60,
    "午时": 11 * 60,
    "未时": 13 * 60,
    "申时": 15 * 60,
    "酉时": 17 * 60,
    "戌时": 19 * 60,
    "亥时": 21 * 60,
}
_SCENE_BREAK_RE = re.compile(r"(?:---|转眼|片刻后|与此同时|另一边)")
_COUNTDOWN_RE = re.compile(
    r"(\d{1,2}[:：]\d{2}|十五分钟|倒计时|子时|三天|七天|第[一二三四五六七八九十\d]+)"
)
_CJK_NAME_RE = re.compile(r"[林张钱王陈周孙赵李沈][\u4e00-\u9fff]{1,2}")


def load_retention_gate_config(root: str | Path) -> RetentionGateConfig:
    package_root = Path(root)
    story_bible = package_root / "story-bible"
    terms = _load_canonical_terms(story_bible / "canonical-terms.yaml")
    reveals = _load_reveal_rules(story_bible / "reveal-schedule.yaml")
    return RetentionGateConfig(terms=terms, reveals=reveals)


def scan_retention_onboarding_package(
    package_dir: str | Path,
    chapters: Sequence[Any] | None = None,
) -> GateVerdict:
    """Run all retention/onboarding checks for one exported book package."""

    root = Path(package_dir)
    config = load_retention_gate_config(root)
    retention_chapters = (
        _coerce_chapters(chapters)
        if chapters is not None
        else _load_retention_chapters(root)
    )
    if not config.active:
        return GateVerdict(
            gate_name="retention_onboarding_gate",
            verdict="not_run",
            coverage=0.0,
            required=False,
            summary=(
                "No story-bible/canonical-terms.yaml or reveal-schedule.yaml found; "
                "retention gates skipped."
            ),
        )

    findings: list[GateFinding] = []
    findings.extend(scan_onboarding_density(retention_chapters, config))
    findings.extend(scan_time_anchor_monotonicity(retention_chapters))
    findings.extend(scan_chapter_beat_count(retention_chapters, config))
    findings.extend(scan_premature_reveals(retention_chapters, config))
    findings.extend(scan_hook_specificity(retention_chapters, config))
    findings.extend(scan_opening_repetition(retention_chapters))

    critical_count = sum(1 for finding in findings if finding.severity == "critical")
    high_count = sum(1 for finding in findings if finding.severity == "high")
    verdict = "blocked" if critical_count else "warn_only" if findings else "pass"
    quality_score = max(0, 100 - critical_count * 18 - high_count * 8 - len(findings) * 2)
    return GateVerdict(
        gate_name="retention_onboarding_gate",
        verdict=verdict,
        coverage=1.0,
        findings=tuple(findings),
        metrics={
            "quality_score": quality_score,
            "issue_counts": dict(Counter(finding.code for finding in findings)),
            "chapters_scanned": len(retention_chapters),
            "canonical_terms": len(config.terms),
            "reveal_rules": len(config.reveals),
        },
        summary="Retention/onboarding gate completed.",
    )


def scan_onboarding_density(
    chapters: Sequence[RetentionChapter],
    config: RetentionGateConfig,
) -> tuple[GateFinding, ...]:
    thresholds = dict(config.onboarding_thresholds or DEFAULT_ONBOARDING_THRESHOLDS)
    onboarding_terms = [term for term in config.terms if term.count_onboarding]
    if not onboarding_terms or not chapters:
        return ()

    first_seen: dict[str, int] = {}
    for chapter in chapters:
        if chapter.chapter_no > max(thresholds):
            break
        for term in onboarding_terms:
            if term.term and term.term in chapter.text and term.term not in first_seen:
                first_seen[term.term] = chapter.chapter_no

    findings: list[GateFinding] = []
    for end_chapter, threshold in sorted(thresholds.items()):
        terms = sorted(term for term, chapter_no in first_seen.items() if chapter_no <= end_chapter)
        if len(terms) <= threshold:
            continue
        findings.append(
            GateFinding(
                code="ONBOARDING_OVERLOAD",
                severity="critical" if end_chapter <= 2 else "high",
                message=(
                    f"Chapters 1-{end_chapter} introduce {len(terms)} canonical terms; "
                    f"budget is {threshold}."
                ),
                path=f"chapter-{end_chapter:03d}.md",
                repair_action=(
                    "压缩开篇新名词：只保留本章冲突必需的人物/物件/规则，其余延后到后续章节。"
                ),
            )
        )
        break
    return tuple(findings)


def scan_time_anchor_monotonicity(
    chapters: Sequence[RetentionChapter],
) -> tuple[GateFinding, ...]:
    findings: list[GateFinding] = []
    previous_abs: int | None = None
    previous_label = ""
    previous_chapter: int | None = None
    day_offset = 0
    for chapter in chapters:
        if chapter.chapter_no > 10:
            break
        for minute, label in _iter_time_anchors(chapter.text):
            candidate_abs = day_offset * 24 * 60 + minute
            if previous_abs is not None and candidate_abs < previous_abs:
                previous_mod = previous_abs % (24 * 60)
                if previous_mod >= 21 * 60 and minute <= 3 * 60:
                    day_offset += 1
                    candidate_abs = day_offset * 24 * 60 + minute
                else:
                    findings.append(
                        GateFinding(
                            code="TIME_ANCHOR_BACKWARDS",
                            severity="critical" if chapter.chapter_no <= 10 else "high",
                            message=(
                                f"Time anchor moves backwards from {previous_label} "
                                f"(chapter {previous_chapter}) to {label}."
                            ),
                            path=f"chapter-{chapter.chapter_no:03d}.md",
                            repair_action=(
                                "统一跨章时钟；若是倒叙必须显式标注，否则把本章时间改为上一章之后。"
                            ),
                        )
                    )
                    return tuple(findings)
            previous_abs = candidate_abs
            previous_label = label
            previous_chapter = chapter.chapter_no
    return tuple(findings)


def scan_chapter_beat_count(
    chapters: Sequence[RetentionChapter],
    config: RetentionGateConfig,
) -> tuple[GateFinding, ...]:
    if not config.terms:
        return ()

    findings: list[GateFinding] = []
    seen_terms: set[str] = set()
    for chapter in chapters:
        new_terms = [
            term
            for term in config.terms
            if term.count_onboarding
            and term.term
            and term.term in chapter.text
            and term.term not in seen_terms
        ]
        new_characters = [term.term for term in new_terms if term.category in CHARACTER_CATEGORIES]
        new_settings = [term.term for term in new_terms if term.category in PLACE_CATEGORIES]
        scene_shifts = min(3, max(0, len(_SCENE_BREAK_RE.findall(chapter.text[:2600])) - 1))
        beat_count = 1 + min(3, len(new_characters)) + min(2, len(new_settings)) + scene_shifts
        threshold = (
            config.front_beat_threshold if chapter.chapter_no <= 10 else config.later_beat_threshold
        )
        if beat_count > threshold:
            findings.append(
                GateFinding(
                    code="BEAT_DENSITY_OVERLOAD",
                    severity="high" if chapter.chapter_no <= 10 else "medium",
                    message=(
                        f"Chapter {chapter.chapter_no} carries about {beat_count} major beats; "
                        f"budget is {threshold}."
                    ),
                    path=f"chapter-{chapter.chapter_no:03d}.md",
                    repair_action=(
                        "拆分本章任务：一章只推进一个主冲突、一个短回报和一个尾钩，新增人物/空间延后。"
                    ),
                )
            )
        for term in new_terms:
            seen_terms.add(term.term)
    return tuple(findings)


def scan_premature_reveals(
    chapters: Sequence[RetentionChapter],
    config: RetentionGateConfig,
) -> tuple[GateFinding, ...]:
    if not config.reveals:
        return ()

    findings: list[GateFinding] = []
    for chapter in chapters:
        for rule in config.reveals:
            if chapter.chapter_no >= rule.earliest_chapter:
                continue
            hits = [token for token in rule.tokens if token and token in chapter.text]
            if not hits:
                continue
            findings.append(
                GateFinding(
                    code="PREMATURE_REVEAL",
                    severity="critical",
                    message=(
                        f"Reveal '{rule.rule_id}' appears in chapter {chapter.chapter_no}; "
                        f"earliest allowed chapter is {rule.earliest_chapter}."
                    ),
                    path=f"chapter-{chapter.chapter_no:03d}.md",
                    repair_action=(
                        "撤回终局词/真相词，改成局部证据、误导线索或不可命名的异象。"
                    ),
                )
            )
    return tuple(findings)


def scan_hook_specificity(
    chapters: Sequence[RetentionChapter],
    config: RetentionGateConfig,
) -> tuple[GateFinding, ...]:
    character_terms = {term.term for term in config.terms if term.category in CHARACTER_CATEGORIES}
    object_terms = {term.term for term in config.terms if term.category in OBJECT_CATEGORIES}
    place_terms = {term.term for term in config.terms if term.category in PLACE_CATEGORIES}
    findings: list[GateFinding] = []
    for chapter in chapters:
        tail = _chapter_body(chapter.text)[-220:]
        has_named_person = bool(
            character_terms.intersection(_terms_in_text(character_terms, tail))
        ) or bool(_CJK_NAME_RE.search(tail))
        specificity = {
            "named_person": has_named_person,
            "concrete_object": bool(object_terms.intersection(_terms_in_text(object_terms, tail))),
            "place": bool(place_terms.intersection(_terms_in_text(place_terms, tail)))
            or bool(re.search(r"\d{2,3}室|十七栋|楼道|镜门", tail)),
            "countdown": bool(_COUNTDOWN_RE.search(tail)),
        }
        if sum(1 for value in specificity.values() if value) >= 2:
            continue
        findings.append(
            GateFinding(
                code="HOOK_TOO_ABSTRACT",
                severity="high" if chapter.chapter_no <= 10 else "medium",
                message=(
                    f"Chapter {chapter.chapter_no} ending hook lacks concrete pursuit "
                    f"handles: {specificity}."
                ),
                path=f"chapter-{chapter.chapter_no:03d}.md",
                repair_action=(
                    "章尾必须落到可追读的具体人、物、地点或倒计时，避免只用抽象情绪收尾。"
                ),
            )
        )
    return tuple(findings)


def scan_opening_repetition(
    chapters: Sequence[RetentionChapter],
    *,
    window_size: int = 6,
) -> tuple[GateFinding, ...]:
    patterns = [(chapter, _opening_pattern(chapter.text)) for chapter in chapters]
    findings: list[GateFinding] = []
    reported: set[tuple[str, tuple[int, ...]]] = set()
    for index in range(0, max(0, len(patterns) - window_size + 1)):
        window = patterns[index : index + window_size]
        counts = Counter(pattern for _, pattern in window)
        for pattern, count in counts.items():
            chapters_hit = [chapter.chapter_no for chapter, item in window if item == pattern]
            report_key = (pattern, tuple(chapters_hit))
            if count < 3 or report_key in reported:
                continue
            findings.append(
                GateFinding(
                    code="OPENING_PATTERN_OVERUSED",
                    severity="medium",
                    message=(
                        f"Opening pattern '{pattern}' appears {count} times in a "
                        f"{window_size}-chapter window: {chapters_hit}."
                    ),
                    path=f"chapter-{chapters_hit[0]:03d}.md",
                    repair_action=(
                        "调整窗口内开篇方式：行动承接、对话压迫、物证异常、场景反转交替使用。"
                    ),
                )
            )
            reported.add(report_key)
    return tuple(findings)


def _load_canonical_terms(path: Path) -> tuple[CanonicalTerm, ...]:
    payload = _load_yaml_mapping(path)
    raw_terms = payload.get("terms", ())
    terms: list[CanonicalTerm] = []
    if not isinstance(raw_terms, Sequence) or isinstance(raw_terms, (str, bytes)):
        return ()
    for item in raw_terms:
        if isinstance(item, str):
            term = item.strip()
            if term:
                terms.append(CanonicalTerm(term=term))
            continue
        if not isinstance(item, Mapping):
            continue
        term = str(item.get("term") or item.get("name") or "").strip()
        if not term:
            continue
        terms.append(
            CanonicalTerm(
                term=term,
                category=str(item.get("category") or "term").strip() or "term",
                count_onboarding=bool(item.get("count_onboarding", True)),
            )
        )
    return tuple(terms)


def _load_reveal_rules(path: Path) -> tuple[RevealRule, ...]:
    payload = _load_yaml_mapping(path)
    raw_reveals = payload.get("reveals", ())
    rules: list[RevealRule] = []
    if not isinstance(raw_reveals, Sequence) or isinstance(raw_reveals, (str, bytes)):
        return ()
    for index, item in enumerate(raw_reveals, start=1):
        if not isinstance(item, Mapping):
            continue
        raw_tokens = item.get("tokens") or item.get("terms") or ()
        tokens = tuple(str(token).strip() for token in raw_tokens if str(token).strip())
        try:
            earliest = int(item.get("earliest_chapter") or item.get("floor_chapter") or 1)
        except (TypeError, ValueError):
            earliest = 1
        if tokens:
            rules.append(
                RevealRule(
                    rule_id=str(item.get("id") or item.get("name") or f"reveal_{index}"),
                    earliest_chapter=earliest,
                    tokens=tokens,
                )
            )
    return tuple(rules)


def _load_yaml_mapping(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _load_retention_chapters(root: Path) -> tuple[RetentionChapter, ...]:
    chapters: list[RetentionChapter] = []
    for path in sorted(root.glob("chapter-*.md")):
        match = _CHAPTER_PATH_RE.search(path.name)
        if match is None:
            continue
        text = path.read_text(encoding="utf-8")
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        chapters.append(
            RetentionChapter(
                chapter_no=int(match.group(1)),
                title=first_line.lstrip("#").strip(),
                text=text,
                path=path,
            )
        )
    return tuple(chapters)


def _coerce_chapters(chapters: Sequence[Any]) -> tuple[RetentionChapter, ...]:
    coerced: list[RetentionChapter] = []
    for chapter in chapters:
        chapter_no = int(chapter.chapter_no)
        coerced.append(
            RetentionChapter(
                chapter_no=chapter_no,
                title=str(getattr(chapter, "title", "")),
                text=str(chapter.text),
                path=Path(getattr(chapter, "path", f"chapter-{chapter_no:03d}.md")),
            )
        )
    return tuple(coerced)


def _iter_time_anchors(text: str) -> tuple[tuple[int, str], ...]:
    anchors: list[tuple[int, str, int]] = []
    for match in _TIME_RE.finditer(text):
        minute = int(match.group(1)) * 60 + int(match.group(2))
        anchors.append((minute, match.group(0), match.start()))
    for label, minute in _SHICHEN_MINUTES.items():
        start = 0
        while True:
            index = text.find(label, start)
            if index < 0:
                break
            anchors.append((minute, label, index))
            start = index + len(label)
    anchors.sort(key=lambda item: item[2])
    return tuple((minute, label) for minute, label, _ in anchors)


def _chapter_body(text: str) -> str:
    lines = text.splitlines()
    body_lines = [line for line in lines if not line.strip().startswith("#")]
    return "\n".join(body_lines).strip()


def _terms_in_text(terms: set[str], text: str) -> set[str]:
    return {term for term in terms if term and term in text}


def _opening_pattern(text: str) -> str:
    for paragraph in re.split(r"\n\s*\n", _chapter_body(text)):
        opening = paragraph.strip()
        if not opening:
            continue
        has_time_label = any(label in opening[:24] for label in _SHICHEN_MINUTES)
        if _TIME_RE.search(opening[:40]) or has_time_label:
            return "time_anchor"
        if opening[0] in {"“", "\"", "'"}:
            return "dialogue"
        if any(term in opening[:80] for term in ("镜", "罗盘", "铜钱", "青囊", "手机", "回执")):
            return "object_signal"
        if _CJK_NAME_RE.search(opening[:16]):
            return "character_action"
        if any(term in opening[:50] for term in ("楼道", "十七栋", "门口", "房间", "旧事馆")):
            return "place_anchor"
        return "exposition"
    return "empty"
