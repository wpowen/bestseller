"""Timeline Consistency Gate — detect time-anchor contradictions in a chapter.

Catches three classes of failure that the auto-repair loop previously
missed:

1. **Forbidden anchors** — "X 年前" values not in canonical timeline
   (e.g. "十七年前" when the canon only allows 23 / 3 / 30 / 300).
2. **Subject-anchor mismatch** — within ±N characters of a canonical
   subject (e.g. 林正淳, 林家辉), the time anchor must match canon.
3. **Internal contradictions** — same subject mentioned with two
   different "X 年前" values inside the same chapter.

Canonical data source: ``output/<slug>/story-bible/timeline-canon.md``
(YAML front-matter format described in
``STORY_INTEGRITY_GATES_DEVELOPMENT_PLAN.md``).

Block code: ``TIMELINE_INCONSISTENT`` — eligible for auto-repair.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


TIMELINE_INCONSISTENT_BLOCK_CODE = "TIMELINE_INCONSISTENT"


# Chinese number tokens used in "X 年前 / X 岁那年" expressions.
_CHINESE_NUMS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "百": 100, "千": 1000, "万": 10000,
}

_YEARS_AGO_RE = re.compile(
    r"([零〇一二三四五六七八九十百千万两\d]+)\s*年\s*(前|之前|以前)"
)
_AGE_THAT_YEAR_RE = re.compile(
    r"([零〇一二三四五六七八九十百千万两\d]+)\s*岁"
)
# 干支纪年 (e.g. 戊子年, 庚午年) — 60 distinct values.
_GANZHI_RE = re.compile(
    r"([甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥])\s*年"
)


@dataclass(frozen=True)
class TimelineFact:
    """One canonical fact loaded from timeline-canon.md."""

    event_id: str
    label: str
    years_ago: int | None
    year_name: str | None
    protagonist_age_at_event: int | None
    subjects: tuple[str, ...]
    aliases: tuple[str, ...] = ()  # kinship terms like 爷爷, 父亲

    @property
    def all_referents(self) -> tuple[str, ...]:
        """All ways to refer to this event's subject (names + aliases)."""
        return tuple(dict.fromkeys((*self.subjects, *self.aliases)))


@dataclass(frozen=True)
class TimelineCanon:
    """Loaded canonical timeline."""

    present_year: int | None
    protagonist_name: str | None
    protagonist_current_age: int | None
    events: tuple[TimelineFact, ...]
    forbidden_anchors: tuple[int, ...]
    locked_names: dict[str, str] = field(default_factory=dict)

    @property
    def allowed_years_ago(self) -> tuple[int, ...]:
        return tuple(
            sorted({e.years_ago for e in self.events if e.years_ago is not None})
        )

    def event_for_subject(self, subject: str) -> TimelineFact | None:
        """Return the canonical event most strongly associated with the subject."""

        for event in self.events:
            if subject in event.subjects:
                return event
        return None


@dataclass(frozen=True)
class TimelineViolation:
    severity: str  # "critical" | "high"
    code: str
    detail: str
    found_anchor: str
    paragraph_idx: int
    canonical_anchor: str | None = None


@dataclass(frozen=True)
class TimelineReport:
    chapter_position: int
    violations: tuple[TimelineViolation, ...]

    @property
    def passed(self) -> bool:
        return not any(v.severity == "critical" for v in self.violations)

    @property
    def has_critical(self) -> bool:
        return any(v.severity == "critical" for v in self.violations)


# ---------- canon loader ----------


def load_timeline_canon(path: str | Path) -> TimelineCanon | None:
    """Load timeline-canon.md (YAML front-matter)."""

    effective = Path(path)
    if not effective.exists():
        return None
    try:
        raw = effective.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("timeline canon read failed: %s", exc)
        return None

    yaml_block = _extract_yaml_front_matter(raw)
    if not yaml_block:
        return None
    try:
        import yaml

        parsed = yaml.safe_load(yaml_block) or {}
    except Exception as exc:
        logger.warning("timeline canon yaml parse failed: %s", exc)
        return None
    if not isinstance(parsed, dict):
        return None

    protagonist_block = parsed.get("protagonist") or {}
    protagonist_name = str(protagonist_block.get("name") or "").strip() or None
    protagonist_age = _safe_int(protagonist_block.get("current_age"))

    events_raw = parsed.get("events") or []
    events: list[TimelineFact] = []
    for entry in events_raw:
        if not isinstance(entry, dict):
            continue
        events.append(
            TimelineFact(
                event_id=str(entry.get("id") or "").strip(),
                label=str(entry.get("label") or "").strip(),
                years_ago=_safe_int(entry.get("anchor_years_ago")),
                year_name=(str(entry.get("anchor_year_name") or "").strip() or None),
                protagonist_age_at_event=_safe_int(
                    entry.get("protagonist_age_at_event")
                ),
                subjects=tuple(
                    str(s).strip()
                    for s in (entry.get("related_subjects") or [])
                    if str(s).strip()
                ),
                aliases=tuple(
                    str(s).strip()
                    for s in (entry.get("aliases") or [])
                    if str(s).strip()
                ),
            )
        )

    forbidden_raw = parsed.get("forbidden_anchors") or []
    forbidden: list[int] = []
    for entry in forbidden_raw:
        if isinstance(entry, dict):
            value = _safe_int(entry.get("years_ago"))
            if value is not None:
                forbidden.append(value)
        elif isinstance(entry, (int, float)):
            forbidden.append(int(entry))

    locked_names_raw = parsed.get("locked_names") or {}
    locked_names = {
        str(k): str(v) for k, v in locked_names_raw.items() if v
    } if isinstance(locked_names_raw, dict) else {}

    return TimelineCanon(
        present_year=_safe_int(parsed.get("present_year")),
        protagonist_name=protagonist_name,
        protagonist_current_age=protagonist_age,
        events=tuple(events),
        forbidden_anchors=tuple(forbidden),
        locked_names=locked_names,
    )


# ---------- chapter analysis ----------


def check_timeline_consistency(
    chapter_text: str,
    *,
    chapter_position: int,
    canon: TimelineCanon | None,
) -> TimelineReport:
    """Scan chapter for anchors, cross-ref canon, return violations."""

    if not chapter_text.strip() or canon is None:
        return TimelineReport(
            chapter_position=chapter_position, violations=()
        )

    violations: list[TimelineViolation] = []
    paragraphs = [p for p in chapter_text.split("\n") if p.strip()]
    forbidden_set = set(canon.forbidden_anchors)

    # 1. Forbidden anchor scan + collect all anchors per paragraph
    per_para_anchors: list[list[tuple[int, str]]] = []
    for idx, para in enumerate(paragraphs):
        anchors = _extract_year_ago_anchors(para)
        per_para_anchors.append(anchors)
        for years, raw in anchors:
            if years in forbidden_set:
                violations.append(
                    TimelineViolation(
                        severity="critical",
                        code="FORBIDDEN_ANCHOR",
                        detail=(
                            f"'{raw}' ({years} 年前) is in the canon's "
                            f"forbidden_anchors list (allowed: "
                            f"{list(canon.allowed_years_ago)})"
                        ),
                        found_anchor=raw,
                        paragraph_idx=idx,
                    )
                )

    # 2. Subject-anchor proximity check (with kinship aliases)
    # For each anchor, find the CLOSEST canonical referent (subject name OR
    # alias like 爷爷/父亲) within ±60 chars. If that closest referent's
    # event has matching years → resolved. Else → violation.
    #
    # This handles "爷爷三十年前补镜。林渊往前迈" correctly:
    # closest referent to 三十年前 is 爷爷 (distance 0) which is alias of
    # 林家辉 (event 30 年前) → resolved.
    PROXIMITY_WINDOW = 60

    for idx, para in enumerate(paragraphs):
        if not per_para_anchors[idx]:
            continue
        for years, raw in per_para_anchors[idx]:
            anchor_pos = para.find(raw)
            if anchor_pos < 0:
                continue
            window_lo = max(0, anchor_pos - PROXIMITY_WINDOW)
            window_hi = min(len(para), anchor_pos + len(raw) + PROXIMITY_WINDOW)

            # Collect all (referent, event, distance) tuples in window
            referent_hits: list[tuple[int, str, TimelineFact]] = []
            for event in canon.events:
                if event.years_ago is None:
                    continue
                for referent in event.all_referents:
                    if not referent:
                        continue
                    # Find closest occurrence of referent to anchor
                    closest_dist = _closest_distance(
                        para, referent, anchor_pos, window_lo, window_hi
                    )
                    if closest_dist is not None:
                        referent_hits.append((closest_dist, referent, event))

            if not referent_hits:
                continue  # no canonical referent near anchor → not flagged

            # Sort by distance; closest wins
            referent_hits.sort(key=lambda h: h[0])
            closest_dist, closest_referent, closest_event = referent_hits[0]

            if closest_event.years_ago == years:
                continue  # closest referent's event matches → resolved

            # Closest doesn't match — but check if ANY referent in window matches
            any_match = any(
                e.years_ago == years for _, _, e in referent_hits
            )
            if any_match:
                continue

            violations.append(
                TimelineViolation(
                    severity="critical",
                    code="SUBJECT_ANCHOR_MISMATCH",
                    detail=(
                        f"near '{raw}' ({years} 年前), closest referent "
                        f"'{closest_referent}' (dist {closest_dist}) belongs "
                        f"to {closest_event.label} which happened "
                        f"{closest_event.years_ago} 年前, not {years}"
                    ),
                    found_anchor=raw,
                    paragraph_idx=idx,
                    canonical_anchor=f"{closest_event.years_ago} 年前",
                )
            )

    # 3. Age × years-ago consistency
    # If a paragraph says "X 岁那年" and "Y 年前", we expect:
    #   protagonist_current_age - Y == X
    if canon.protagonist_current_age is not None:
        for idx, para in enumerate(paragraphs):
            anchors = per_para_anchors[idx]
            ages = _extract_age_anchors(para)
            if not anchors or not ages:
                continue
            for age, age_raw in ages:
                for years, year_raw in anchors:
                    if canon.protagonist_current_age - years != age:
                        violations.append(
                            TimelineViolation(
                                severity="critical",
                                code="AGE_YEAR_MISMATCH",
                                detail=(
                                    f"'{age_raw}' + '{year_raw}' inconsistent: "
                                    f"protagonist age now is "
                                    f"{canon.protagonist_current_age}, so "
                                    f"{years} 年前 = "
                                    f"{canon.protagonist_current_age - years} 岁, "
                                    f"not {age} 岁"
                                ),
                                found_anchor=f"{age_raw} + {year_raw}",
                                paragraph_idx=idx,
                            )
                        )

    # 4. Internal contradictions — same subject mentioned with multiple
    # conflicting "X 年前" values, where the anchor was nearest to that
    # subject. Use the same proximity rule as section 2 to avoid false
    # positives like 林渊 being attributed to 30 年前 when 爷爷 is closer.
    PROXIMITY_FOR_CONTRA = 60
    subject_anchors: dict[str, set[int]] = {}
    for idx, para in enumerate(paragraphs):
        for years, raw in per_para_anchors[idx]:
            anchor_pos = para.find(raw)
            if anchor_pos < 0:
                continue
            window_lo = max(0, anchor_pos - PROXIMITY_FOR_CONTRA)
            window_hi = min(len(para), anchor_pos + len(raw) + PROXIMITY_FOR_CONTRA)

            # For each canonical subject, find its closest referent (subject
            # OR alias) within window. If closest is this subject's referent,
            # this anchor is attributed to this subject.
            closest_referent_per_event: dict[str, tuple[int, str]] = {}
            for event in canon.events:
                if event.years_ago is None:
                    continue
                for referent in event.all_referents:
                    dist = _closest_distance(
                        para, referent, anchor_pos, window_lo, window_hi
                    )
                    if dist is not None:
                        key = "|".join(event.subjects) or referent
                        if key not in closest_referent_per_event or dist < closest_referent_per_event[key][0]:
                            closest_referent_per_event[key] = (dist, referent)
            # Pick the GLOBALLY closest referent — only attribute anchor to that
            # subject (not all subjects in window).
            if not closest_referent_per_event:
                continue
            best_key = min(
                closest_referent_per_event.keys(),
                key=lambda k: closest_referent_per_event[k][0],
            )
            # best_key represents the subject group; record anchor against
            # the first canonical name in that group.
            primary_subject = best_key.split("|")[0]
            subject_anchors.setdefault(primary_subject, set()).add(years)

    for subject, year_values in subject_anchors.items():
        if len(year_values) <= 1:
            continue
        canonical_values = {
            e.years_ago
            for e in canon.events
            if subject in e.subjects and e.years_ago is not None
        }
        non_canonical = year_values - canonical_values
        if non_canonical and len(year_values) >= 2:
            violations.append(
                TimelineViolation(
                    severity="high",
                    code="INTERNAL_CONTRADICTION",
                    detail=(
                        f"subject '{subject}' has conflicting anchors in "
                        f"chapter: {sorted(year_values)} 年前 "
                        f"(canon allows {sorted(canonical_values) or 'none'})"
                    ),
                    found_anchor=f"{subject} → {sorted(year_values)}",
                    paragraph_idx=-1,
                )
            )

    return TimelineReport(
        chapter_position=chapter_position,
        violations=tuple(violations),
    )


def render_timeline_canon_block(
    canon: TimelineCanon | None,
    *,
    language: str = "zh-CN",
) -> str:
    """Render canon facts as a prompt block."""

    if canon is None or not canon.events:
        return ""

    if language.lower().startswith("zh"):
        lines = ["【时间线锁定 — 必须遵守】"]
        if canon.protagonist_current_age is not None:
            lines.append(
                f"- 主角{canon.protagonist_name or ''}当下 "
                f"{canon.protagonist_current_age} 岁"
            )
        lines.append("- 全书唯一允许的时间锚:")
        for event in canon.events:
            if event.years_ago is None:
                continue
            label = event.label
            age_part = (
                f"（主角当时 {event.protagonist_age_at_event} 岁）"
                if event.protagonist_age_at_event is not None
                else ""
            )
            year_name = f"（{event.year_name}）" if event.year_name else ""
            lines.append(
                f"  · {event.years_ago} 年前{year_name}: {label}{age_part}"
            )
        if canon.forbidden_anchors:
            forbidden_str = "、".join(
                f"{a} 年前" for a in sorted(canon.forbidden_anchors)
            )
            lines.append(f"- 严禁出现的时间锚: {forbidden_str}")
        if canon.protagonist_current_age is not None:
            lines.append(
                "- 年龄校验规则: 主角当下年龄 - N 年前 = X 岁那年。"
                "凡同段出现 'X 岁那年' 与 'Y 年前'，必须满足这个等式。"
            )
        return "\n".join(lines)

    return "[Timeline Canon — locked anchors only]"


def render_timeline_violations_block(
    report: TimelineReport,
    *,
    language: str = "zh-CN",
) -> str:
    """Render violations for rewrite prompts."""

    if report.passed:
        return ""
    if language.lower().startswith("zh"):
        lines = ["【时间线门禁 — 本章必须修复】"]
        for v in report.violations[:6]:
            lines.append(f"  · [{v.code}] {v.detail}")
        lines.append("- 重写时必须使用 timeline-canon 中允许的时间锚，不得增删时间点。")
        return "\n".join(lines)
    return f"[Timeline violations: {len(report.violations)}]"


# ---------- helpers ----------


def _parse_chinese_number(text: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            return None
    # Handle simple "X" / "X十" / "X十Y" / "X百Y十Z" patterns
    result = 0
    current = 0
    last_unit = 1
    for ch in text:
        if ch in ("零", "〇"):
            continue
        if ch in _CHINESE_NUMS:
            value = _CHINESE_NUMS[ch]
            if value >= 10:
                if current == 0:
                    current = 1
                result += current * value
                current = 0
                last_unit = value
            else:
                current = current * 10 + value if last_unit < 10 else value
        else:
            return None
    return result + current


def _extract_year_ago_anchors(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for match in _YEARS_AGO_RE.finditer(text):
        raw = match.group(0)
        num_str = match.group(1)
        n = _parse_chinese_number(num_str)
        if n is not None and n > 0:
            out.append((n, raw))
    return out


def _extract_age_anchors(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for match in _AGE_THAT_YEAR_RE.finditer(text):
        raw = match.group(0)
        num_str = match.group(1)
        n = _parse_chinese_number(num_str)
        if n is not None and 0 <= n < 150:
            out.append((n, raw))
    return out


def _closest_distance(
    text: str,
    needle: str,
    anchor_pos: int,
    window_lo: int,
    window_hi: int,
) -> int | None:
    """Find the closest character-distance from ``needle`` to ``anchor_pos``
    within ``[window_lo, window_hi]``. Returns None if needle not in window.
    """

    best: int | None = None
    search_lo = window_lo
    while True:
        found = text.find(needle, search_lo, window_hi)
        if found < 0:
            break
        # Compute distance from anchor_pos to nearest edge of needle
        needle_end = found + len(needle)
        if needle_end <= anchor_pos:
            dist = anchor_pos - needle_end
        elif found >= anchor_pos:
            dist = found - anchor_pos
        else:
            dist = 0  # overlapping
        if best is None or dist < best:
            best = dist
        search_lo = found + 1
    return best


def _extract_yaml_front_matter(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    closing = text.find("\n---", 3)
    if closing < 0:
        return None
    return text[3:closing].strip()


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "TIMELINE_INCONSISTENT_BLOCK_CODE",
    "TimelineCanon",
    "TimelineFact",
    "TimelineReport",
    "TimelineViolation",
    "check_timeline_consistency",
    "load_timeline_canon",
    "render_timeline_canon_block",
    "render_timeline_violations_block",
]
