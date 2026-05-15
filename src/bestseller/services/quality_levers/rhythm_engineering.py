"""Rhythm Engineering loader + detector (``config/rhythm_engineering.yaml``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_int,
    as_str,
    as_str_tuple,
    load_yaml,
)
from bestseller.services.quality_levers.detectors import count_cjk_chars


_CONFIG_FILENAME = "rhythm_engineering.yaml"


@dataclass(frozen=True)
class RhythmAnchorType:
    """One of the 4 anchor types (hard_stop / acceleration / delay / external_interrupt)."""

    anchor_id: str
    display: str
    description: str
    rule: str = ""
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class RhythmEngineeringConfig:
    version: str
    rhythm_anchors: dict[str, RhythmAnchorType]
    per_1500_min_count: int
    per_1500_min_types: int


def _parse_anchor(anchor_id: str, raw: object) -> RhythmAnchorType:
    data = as_dict(raw)
    examples_raw = data.get("examples")
    if isinstance(examples_raw, str):
        examples = (examples_raw.strip(),) if examples_raw.strip() else ()
    else:
        examples = as_str_tuple(examples_raw)
    return RhythmAnchorType(
        anchor_id=anchor_id,
        display=as_str(data.get("display"), default=anchor_id),
        description=as_str(data.get("description")),
        rule=as_str(data.get("rule")),
        examples=examples,
    )


@lru_cache(maxsize=1)
def load_rhythm_engineering() -> RhythmEngineeringConfig:
    """Return the typed view."""

    raw = load_yaml(_CONFIG_FILENAME)
    anchors_raw = as_dict(raw.get("rhythm_anchors"))
    anchors: dict[str, RhythmAnchorType] = {}
    for anchor_id, anchor_raw in anchors_raw.items():
        canonical = as_str(anchor_id)
        if not canonical:
            continue
        anchors[canonical] = _parse_anchor(canonical, anchor_raw)

    minimum = as_dict(raw.get("per_chapter_minimum"))
    # Extract the integer thresholds embedded in the rule string.
    rule_text = as_str(minimum.get("rule"))
    nums = [int(n) for n in re.findall(r"\d+", rule_text)]
    # rule reads: "每 1500 字至少包含 4 种锚点中的 3 种"
    per_count = nums[1] if len(nums) > 1 else 4
    per_types = nums[2] if len(nums) > 2 else 3

    return RhythmEngineeringConfig(
        version=as_str(raw.get("version")),
        rhythm_anchors=anchors,
        per_1500_min_count=per_count,
        per_1500_min_types=per_types,
    )


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_EXTERNAL_INTERRUPT_KEYWORDS = (
    "门外", "院里", "正厅", "远处", "忽然", "猛地", "门缝", "井底",
    "屋外", "传来", "推开", "突然",
)


@dataclass(frozen=True)
class RhythmAuditResult:
    hard_stop_count: int
    acceleration_count: int
    delay_count: int
    external_interrupt_count: int
    total_anchors: int
    types_covered: int
    expected_min_count: int
    expected_min_types: int
    passed: bool


def _count_hard_stops(paragraphs: list[str]) -> int:
    """A hard_stop = a paragraph whose CJK length is ≤ 12 chars and lacks dialogue."""

    return sum(
        1
        for paragraph in paragraphs
        if count_cjk_chars(paragraph) <= 12
        and not paragraph.startswith("“")
    )


def _count_acceleration(paragraphs: list[str]) -> int:
    """Count clusters of ≥ 3 consecutive short paragraphs (CJK ≤ 8)."""

    count = 0
    run = 0
    for paragraph in paragraphs:
        if 1 <= count_cjk_chars(paragraph) <= 8:
            run += 1
            if run == 3:
                count += 1
        else:
            run = 0
    return count


def _count_delay(text: str) -> int:
    """A ``delay`` reads as repeated waiting beats: ``停。`` / ``停了一拍。``."""

    return len(
        re.findall(r"停[一二三四五]?[拍息]?[。\n]|又敲了一下|再敲一下", text)
    )


def _count_external_interrupts(text: str) -> int:
    return sum(text.count(keyword) for keyword in _EXTERNAL_INTERRUPT_KEYWORDS)


def audit_rhythm(text: str) -> RhythmAuditResult:
    """Run the four anchor detectors on ``text``."""

    config = load_rhythm_engineering()
    if not text:
        return RhythmAuditResult(
            0, 0, 0, 0, 0, 0,
            expected_min_count=config.per_1500_min_count,
            expected_min_types=config.per_1500_min_types,
            passed=False,
        )

    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text) if p.strip()]
    chars = count_cjk_chars(text)
    multiplier = max(1, chars / 1500)
    expected_count = int(round(config.per_1500_min_count * multiplier))

    hard_stops = _count_hard_stops(paragraphs)
    acceleration = _count_acceleration(paragraphs)
    delay = _count_delay(text)
    interrupts = _count_external_interrupts(text)

    total = hard_stops + acceleration + delay + interrupts
    types_covered = sum(
        1
        for value in (hard_stops, acceleration, delay, interrupts)
        if value > 0
    )

    passed = (
        total >= expected_count and types_covered >= config.per_1500_min_types
    )

    return RhythmAuditResult(
        hard_stop_count=hard_stops,
        acceleration_count=acceleration,
        delay_count=delay,
        external_interrupt_count=interrupts,
        total_anchors=total,
        types_covered=types_covered,
        expected_min_count=expected_count,
        expected_min_types=config.per_1500_min_types,
        passed=passed,
    )


def render_rhythm_block() -> str:
    """Render the writer-facing fragment for the rhythm contract."""

    config = load_rhythm_engineering()
    if not config.rhythm_anchors:
        return ""
    lines: list[str] = ["【rhythm_engineering · 节奏锚点契约】"]
    lines.append(
        f"- 每 1500 字 ≥ {config.per_1500_min_count} 锚点 + "
        f"覆盖 ≥ {config.per_1500_min_types} 种类型"
    )
    for anchor in config.rhythm_anchors.values():
        lines.append(
            f"  - {anchor.anchor_id} ({anchor.display}): {anchor.description}"
        )
    return "\n".join(lines)
