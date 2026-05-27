"""Scene Coherence Gate — detect location jumps without transitions.

Detects the failure mode where a chapter's narrative jumps between
locations (e.g. 十七栋 → 城南旧事馆 → 十七栋) without explicit transition
markers (movement verbs + time markers). The ch1 historical version of
青囊不语问阴阳 had two such jumps that current gates didn't catch.

Logic:
    1. Split text into paragraphs.
    2. Tag each paragraph with the location it occupies (or None).
    3. Detect location transitions (paragraph_i in location A,
       paragraph_i+1 in different location B).
    4. For each transition, look within a 3-paragraph window (the
       previous + current + 1 ahead) for transition markers
       (走/赶/到/抵达/分钟后 etc.).
    5. If no marker found within window → critical SceneJump.
    6. If only one weak marker found → high SceneJump.

Block code: ``SCENE_JUMP_UNRESOLVED`` — eligible for auto-repair.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


SCENE_JUMP_BLOCK_CODE = "SCENE_JUMP_UNRESOLVED"


# Location groups: tokens within the same group are considered nested
# or same-scene (e.g. 十七栋 + 二十三层 + 303 = one scene cluster).
# Transitions WITHIN a group don't count as jumps.
_DEFAULT_LOCATION_GROUPS: tuple[tuple[str, ...], ...] = (
    # Group A: 十七栋 + 内部
    (
        "十七栋", "二十三层", "二十三楼", "23 层", "二十三层走廊",
        "303", "305", "306",
    ),
    # Group B: 旧货市场区域
    ("城南旧事馆", "城北旧货市场", "旧事馆", "旧货市场"),
    # Group C: 医院 / 太平间
    ("太平间", "ICU", "病房", "医院", "停尸柜"),
    # Group D: 警局
    ("派出所", "刑警支队", "警局"),
    # Group E: 清水桥义庄（外部历史地，本书第 1 章背景之一）
    # 注意：故意不收录 "茅山"，因为它在本书里多作流派/技法名词使用
    # （茅山术/茅山请神术），不是物理地点。
    ("清水桥", "义庄"),
)


def _build_default_location_lookup() -> tuple[
    tuple[str, ...], dict[str, str]
]:
    """Return (all_tokens, token→group_label)."""

    all_tokens: list[str] = []
    lookup: dict[str, str] = {}
    for group in _DEFAULT_LOCATION_GROUPS:
        if not group:
            continue
        label = group[0]
        for token in group:
            if token and token not in lookup:
                lookup[token] = label
                all_tokens.append(token)
    return tuple(all_tokens), lookup


_DEFAULT_LOCATION_TOKENS, _DEFAULT_LOCATION_LOOKUP = _build_default_location_lookup()

# Transition markers — movement verbs + time markers.
_STRONG_TRANSITION_MARKERS: tuple[str, ...] = (
    "半小时后", "二十分钟后", "十分钟后", "一炷香后", "片刻后",
    "几分钟后", "数分钟后", "数分钟过去",
    "零点已过", "零点过后", "次日", "三日后", "几日后", "翌日",
    "天亮前", "天亮后",
    "赶到", "抵达", "走到", "回到", "返回", "返", "驶向", "走向",
    "冲下楼梯", "冲下", "冲出", "退出", "出门",
    "电动车", "出租车", "推开门", "拉开门",
)

# Weak markers must appear standalone (with explicit movement context),
# not embedded in compound words. We model this by requiring them to
# follow common sentence-start patterns.
_WEAK_TRANSITION_PATTERNS: tuple[str, ...] = (
    "他走出", "他走向", "他出门", "他出去", "他过去", "他进入",
    "便走出", "便出门", "便起身",
    "起身离开", "起身出门",
)

_REFERENTIAL_LOCATION_MARKERS: tuple[str, ...] = (
    "记得",
    "想起",
    "想到了",
    "回忆",
    "回想",
    "当年",
    "当时",
    "那年",
    "那时",
    "年前",
    "多年前",
    "小时候",
    "梦里",
    "脑海里",
    "照片里",
    "档案里",
    "账页上",
    "信里写着",
    "爷爷说",
    "父亲说",
    "传闻里",
    "故事里",
    # 2026-05-23: extend with the missing case from 青囊不语问阴阳 ch1:
    # 「这张脸他在城南旧货市场见过」— "见过" must be treated as
    # backstory reference, not a current location switch.
    "见过",
    "听说过",
    "听人说",
    "听过",
    "认得她",
    "认得他",
    "认出",
    "曾经",
    "以前",
    "过去",
    "上次",
    "上回",
    "上一次",
    "上个月",
    "去年",
    "昨天",
    "昨日",
)

_NON_SCENE_LOCATION_MARKERS: tuple[str, ...] = (
    "电话里",
    "打电话",
    "来电",
    "从哪买",
    "哪买的",
    "买的",
    "买来",
    "铺子",
    "问林渊接不接",
    "老板想找人看看",
    "离他住的地方",
    "在十七栋楼下等我",
    "等我",
    "摆了十几年摊子",
    "做旧货生意",
    "方向",
    "指向",
    "正对着",
)

_PARAGRAPH_SPLITTER = re.compile(r"\n+")

@dataclass(frozen=True)
class SceneJump:
    """One detected location transition."""

    from_location: str
    to_location: str
    paragraph_idx: int
    transition_marker_found: bool
    weak_marker_only: bool
    severity: str  # "critical" | "high" | "info"
    detail: str


@dataclass(frozen=True)
class SceneCoherenceReport:
    chapter_position: int
    jumps: tuple[SceneJump, ...]

    @property
    def passed(self) -> bool:
        return not any(j.severity == "critical" for j in self.jumps)

    @property
    def has_critical(self) -> bool:
        return any(j.severity == "critical" for j in self.jumps)


def check_scene_coherence(
    chapter_text: str,
    *,
    chapter_position: int,
    location_tokens: Sequence[str] | None = None,
    location_groups: Sequence[Sequence[str]] | None = None,
    strong_markers: Sequence[str] | None = None,
    weak_patterns: Sequence[str] | None = None,
    transition_window: int = 1,
) -> SceneCoherenceReport:
    """Scan chapter for unresolved location jumps."""

    if not chapter_text.strip():
        return SceneCoherenceReport(
            chapter_position=chapter_position, jumps=()
        )

    if location_groups is not None:
        tokens_list: list[str] = []
        lookup: dict[str, str] = {}
        for group in location_groups:
            if not group:
                continue
            label = group[0]
            for token in group:
                if token and token not in lookup:
                    lookup[token] = label
                    tokens_list.append(token)
        all_tokens = tuple(tokens_list)
    elif location_tokens is not None:
        all_tokens = tuple(location_tokens)
        # Each token is its own group label (no nesting)
        lookup = {t: t for t in all_tokens}
    else:
        all_tokens = _DEFAULT_LOCATION_TOKENS
        lookup = _DEFAULT_LOCATION_LOOKUP

    strong_set = tuple(strong_markers or _STRONG_TRANSITION_MARKERS)
    weak_set = tuple(weak_patterns or _WEAK_TRANSITION_PATTERNS)

    paragraphs = [
        p.strip()
        for p in _PARAGRAPH_SPLITTER.split(chapter_text)
        if p.strip()
    ]
    if len(paragraphs) < 2:
        return SceneCoherenceReport(
            chapter_position=chapter_position, jumps=()
        )

    # Tag each paragraph with the set of group labels it touches.
    para_groups: list[set[str]] = [
        (
            set()
            if _is_referential_location_paragraph(
                p,
                strong_markers=strong_set,
                weak_patterns=weak_set,
            )
            else _paragraph_groups(p, all_tokens, lookup)
        )
        for p in paragraphs
    ]

    jumps: list[SceneJump] = []
    last_meaningful_groups: set[str] | None = None
    last_meaningful_idx = -1

    for idx, groups in enumerate(para_groups):
        if not groups:
            continue
        if last_meaningful_groups is None:
            last_meaningful_groups = groups
            last_meaningful_idx = idx
            continue
        # Same scene if any group overlaps
        if groups & last_meaningful_groups:
            last_meaningful_groups = last_meaningful_groups | groups
            last_meaningful_idx = idx
            continue
        # Location changed — check for transition markers in window
        window_start = max(0, last_meaningful_idx)
        window_end = min(len(paragraphs), idx + transition_window + 1)
        window_text = "\n".join(paragraphs[window_start:window_end])

        strong_hits = sum(1 for m in strong_set if m in window_text)
        weak_hits = sum(1 for m in weak_set if m in window_text)

        from_label = next(iter(last_meaningful_groups))
        to_label = next(iter(groups))

        if strong_hits >= 1:
            severity = "info"
            transition_found = True
            weak_only = False
            detail = (
                f"transition {from_label} → {to_label} has strong markers "
                f"({strong_hits} hits)"
            )
        elif weak_hits >= 1:
            severity = "high"
            transition_found = True
            weak_only = True
            detail = (
                f"transition {from_label} → {to_label} only has weak markers "
                f"({weak_hits} hits); needs explicit time + strong movement"
            )
        else:
            severity = "critical"
            transition_found = False
            weak_only = False
            detail = (
                f"abrupt jump {from_label} → {to_label} at paragraph "
                f"{idx} with no transition markers in window"
            )

        jumps.append(
            SceneJump(
                from_location=from_label,
                to_location=to_label,
                paragraph_idx=idx,
                transition_marker_found=transition_found,
                weak_marker_only=weak_only,
                severity=severity,
                detail=detail,
            )
        )
        last_meaningful_groups = groups
        last_meaningful_idx = idx

    return SceneCoherenceReport(
        chapter_position=chapter_position,
        jumps=tuple(jumps),
    )


def render_scene_coherence_block(
    *,
    language: str = "zh-CN",
) -> str:
    """Render a generic scene-coherence advisory for the writing prompt."""

    if language.lower().startswith("zh"):
        return "\n".join(
            [
                "【场景连贯门 — 必须遵守】",
                "- 任意两段之间如果场景位置改变（如 17号楼 → 旧事馆 → 17号楼），"
                "必须在过渡处明确写出时间标记 + 移动动作。",
                "- 强过渡词举例：半小时后 / 二十分钟后 / 零点已过 / 抵达 / 冲下楼梯 / 推开门。",
                "- 禁止：前一段还在 A 地，下一段突然在 B 地，没有任何时间/移动说明。",
            ]
        )
    return "[Scene coherence — explicit transitions required]"


def render_scene_jump_violations_block(
    report: SceneCoherenceReport,
    *,
    language: str = "zh-CN",
) -> str:
    """Render detected jumps for the rewrite prompt."""

    if report.passed and not report.jumps:
        return ""
    if language.lower().startswith("zh"):
        lines = ["【场景跳跃门禁 — 本章必须修复】"]
        for jump in report.jumps[:5]:
            sev = "✗" if jump.severity == "critical" else "⚠"
            lines.append(
                f"  · {sev} 段落 {jump.paragraph_idx}: "
                f"{jump.from_location} → {jump.to_location} | {jump.detail}"
            )
        lines.append(
            "- 重写时必须在每处场景跳转前后加 1-2 句过渡："
            "用时间词（半小时后 / 零点已过）+ 移动动作（赶到 / 推门 / 抵达）。"
        )
        return "\n".join(lines)
    return f"[Scene jumps: {len(report.jumps)}]"


# ---------- helpers ----------


def _paragraph_groups(
    paragraph: str,
    tokens: Sequence[str],
    token_to_group: dict[str, str],
) -> set[str]:
    """Return the set of group labels mentioned in this paragraph."""

    groups: set[str] = set()
    for token in tokens:
        if token and token in paragraph:
            group = token_to_group.get(token, token)
            groups.add(group)
    return groups


def _is_referential_location_paragraph(
    paragraph: str,
    *,
    strong_markers: Sequence[str],
    weak_patterns: Sequence[str],
) -> bool:
    """Return True when a location mention belongs to memory/documentation.

    Scene jumps are about the character's current physical location. A
    paragraph such as "他想起三十年前爷爷在清水桥义庄..." names another place,
    but the narration has not moved there. Strong movement/time markers keep
    real flashback scene transitions detectable.
    """

    if any(marker in paragraph for marker in _NON_SCENE_LOCATION_MARKERS):
        return True
    if not any(marker in paragraph for marker in _REFERENTIAL_LOCATION_MARKERS):
        return False
    if any(marker in paragraph for marker in strong_markers):
        return False
    return not any(pattern in paragraph for pattern in weak_patterns)


__all__ = [
    "SCENE_JUMP_BLOCK_CODE",
    "SceneCoherenceReport",
    "SceneJump",
    "check_scene_coherence",
    "render_scene_coherence_block",
    "render_scene_jump_violations_block",
]
