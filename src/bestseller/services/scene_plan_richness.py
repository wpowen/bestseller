"""Scene-plan richness gate — validates scene card specificity at runtime.

Root cause of looped / duplicated chapters (ch181 "浮标封锁" case study): the
LLM was being asked to write 1000+ words from a scene card whose
``purpose.story`` was a generic template like ``"advance the chapter spine"``
or ``"推进真相揭示"``. With no concrete action anchor, the model falls back to
safe short-dialogue repetition to fill the word count.

This module adds a plan-time gate *before* ``generate_scene_draft`` runs:

* If the scene card's purpose/entry/exit state are too thin or match generic
  templates, the LLM is skipped and the card is flagged for planner rewrite.
* Severity is classified so non-critical thinness only emits a warning log
  (the draft proceeds) while critical thinness blocks generation.

The validator is DB-free and deterministic — every check runs on the
scene card + chapter model fields already loaded by the pipeline.

Why a runtime gate, not only ``plan_judge``:
  ``plan_judge`` handles project-wide structural validation at the end of
  planning. Richness validation has to happen *per-scene* right before LLM
  generation because individual cards can be thin even when the overall
  plan passes structural checks, and because partial re-plans can revive
  old thin cards without re-running the project-wide judge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Generic-template blacklists
# ---------------------------------------------------------------------------

# Phrases that suggest the planner fell back to a template rather than
# describing this specific scene. Matched case-insensitively against the
# purpose.story / purpose.emotion fields.
GENERIC_STORY_PATTERNS: tuple[str, ...] = (
    "advance the chapter spine",
    "advance the plot",
    "push the story forward",
    "move the story forward",
    "推进剧情",
    "推进主线",
    "推进真相揭示",
    "推进情节",
    "推进故事",
    "推进章节",
    "展开剧情",
    "展开冲突",
    "承接上文",
    "引出下文",
    "过渡场景",
    "铺垫后续",
    "为后续做铺垫",
    "描写场景",
    "讲述过程",
    "展现冲突",
    "展现角色",
    "塑造角色",
    "继续发展",
    "继续推进",
    "完成本章目标",
    "按章节大纲",
)

GENERIC_EMOTION_PATTERNS: tuple[str, ...] = (
    "raise tension",
    "build tension",
    "increase tension",
    "create conflict",
    "raise stakes",
    "提升紧张感",
    "提升张力",
    "营造紧张",
    "制造冲突",
    "升级冲突",
    "情感起伏",
    "情绪推进",
    "情绪张力",
    "强烈情感",
    "复杂情感",
)

GENERIC_STATE_PATTERNS: tuple[str, ...] = (
    "待定",
    "未定",
    "tbd",
    "tbc",
    "to be determined",
    "to be continued",
    "unknown",
    "n/a",
    "null",
    "none",
    "默认",
    "unchanged",
    "状态不变",
    "维持现状",
    "无变化",
)

# Minimum meaningful length for purpose/state fields (in CJK or Latin chars).
# These are tuned for zh-CN / en which are the two supported languages —
# CJK text packs ~2x info per char vs Latin, so we differentiate.
_MIN_STORY_LEN_CJK = 8          # e.g. "林风偷越禁地取灵石"
_MIN_STORY_LEN_LATIN = 18       # e.g. "Lin sneaks into the forbidden zone"
_MIN_EMOTION_LEN_CJK = 6        # e.g. "羞愧与不甘交织"
_MIN_EMOTION_LEN_LATIN = 14     # e.g. "shame tangled with defiance"
_MIN_STATE_STR_LEN_CJK = 4      # e.g. "受伤藏匿"
_MIN_STATE_STR_LEN_LATIN = 10   # e.g. "wounded, hiding"


# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RichnessIssue:
    """Single issue found in a scene card."""

    field_path: str          # e.g. "purpose.story" / "entry_state" / "scene_type"
    severity: str            # "critical" | "warning"
    code: str                # short machine-readable code
    message: str             # human-readable explanation


@dataclass(frozen=True)
class RichnessReport:
    """Outcome of validating one scene card."""

    is_rich_enough: bool                         # False if any critical issue present
    severity: str                                # "pass" | "warning" | "critical"
    issues: tuple[RichnessIssue, ...] = field(default_factory=tuple)

    @property
    def critical_issues(self) -> tuple[RichnessIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "critical")

    @property
    def warning_issues(self) -> tuple[RichnessIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")

    def to_prompt_block(self, *, language: str = "zh-CN") -> str:
        """Render the issues as a planner-facing prompt block."""
        if not self.issues:
            return ""
        if language.startswith("zh"):
            header = "【场景卡片稠密度不足 — 需要重新设计此场景】"
            lines = [header]
            for i in self.issues:
                tag = "❗关键" if i.severity == "critical" else "⚠️提示"
                lines.append(f"{tag} {i.field_path}: {i.message}")
            return "\n".join(lines)
        header = "[Scene card richness insufficient — re-plan this scene]"
        lines = [header]
        for i in self.issues:
            tag = "CRITICAL" if i.severity == "critical" else "WARN"
            lines.append(f"[{tag}] {i.field_path}: {i.message}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_STRIP_PUNCT_RE = re.compile(r"[\s\u3000，。！？、：；（）《》\"'.,!?;:()\"<>\-—]+")


def _normalize_for_match(text: str) -> str:
    """Lowercase + strip punctuation/whitespace for blacklist matching."""
    if not text:
        return ""
    return _STRIP_PUNCT_RE.sub("", text.lower())


def _count_meaningful_chars(text: str) -> int:
    """Count non-punct, non-whitespace chars. CJK char = 1, Latin char = 1."""
    if not text:
        return 0
    return len(_STRIP_PUNCT_RE.sub("", text))


def _is_primarily_cjk(text: str) -> bool:
    if not text:
        return False
    cjk_hits = len(_CJK_CHAR_RE.findall(text))
    total = _count_meaningful_chars(text)
    return total > 0 and cjk_hits / total >= 0.3


def _matches_generic_template(text: str, patterns: tuple[str, ...]) -> str | None:
    """Return the matched generic phrase (original form) if found, else None."""
    if not text:
        return None
    normalized = _normalize_for_match(text)
    for pattern in patterns:
        if _normalize_for_match(pattern) in normalized:
            return pattern
    return None


def _coerce_to_string(value: Any) -> str:
    """Flatten dict/list/str into a single searchable string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        return " ".join(_coerce_to_string(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_coerce_to_string(v) for v in value)
    return str(value)


# ---------------------------------------------------------------------------
# Field-level checks
# ---------------------------------------------------------------------------

def _check_purpose_story(
    purpose: dict[str, Any],
    *,
    language: str,
) -> list[RichnessIssue]:
    issues: list[RichnessIssue] = []
    story_raw = purpose.get("story") if isinstance(purpose, dict) else None
    story = _coerce_to_string(story_raw).strip()

    if not story:
        issues.append(RichnessIssue(
            field_path="purpose.story",
            severity="critical",
            code="missing_story_purpose",
            message="缺少 story purpose —— 无法生成有锚点的场景",
        ))
        return issues

    length = _count_meaningful_chars(story)
    min_len = _MIN_STORY_LEN_CJK if _is_primarily_cjk(story) else _MIN_STORY_LEN_LATIN
    if length < min_len:
        issues.append(RichnessIssue(
            field_path="purpose.story",
            severity="critical",
            code="story_purpose_too_short",
            message=(
                f"story purpose 过短 ({length} 字) —— "
                f"至少需要 {min_len} 字的具体动作/事件描述"
            ),
        ))

    matched = _matches_generic_template(story, GENERIC_STORY_PATTERNS)
    if matched:
        issues.append(RichnessIssue(
            field_path="purpose.story",
            severity="critical",
            code="story_purpose_generic_template",
            message=(
                f"story purpose 是泛化模板 ('{matched}') —— "
                f"必须改为本场景的具体动作/事件"
            ),
        ))
    return issues


def _check_purpose_emotion(
    purpose: dict[str, Any],
    *,
    language: str,
) -> list[RichnessIssue]:
    issues: list[RichnessIssue] = []
    if not isinstance(purpose, dict):
        return issues
    emotion_raw = purpose.get("emotion")
    emotion = _coerce_to_string(emotion_raw).strip()

    if not emotion:
        issues.append(RichnessIssue(
            field_path="purpose.emotion",
            severity="warning",
            code="missing_emotion_purpose",
            message="缺少 emotion purpose —— 场景可能缺乏情感驱动",
        ))
        return issues

    length = _count_meaningful_chars(emotion)
    min_len = _MIN_EMOTION_LEN_CJK if _is_primarily_cjk(emotion) else _MIN_EMOTION_LEN_LATIN
    if length < min_len:
        issues.append(RichnessIssue(
            field_path="purpose.emotion",
            severity="warning",
            code="emotion_purpose_too_short",
            message=(
                f"emotion purpose 过短 ({length} 字) —— "
                f"建议至少 {min_len} 字描述情绪变化"
            ),
        ))

    matched = _matches_generic_template(emotion, GENERIC_EMOTION_PATTERNS)
    if matched:
        issues.append(RichnessIssue(
            field_path="purpose.emotion",
            severity="warning",
            code="emotion_purpose_generic_template",
            message=(
                f"emotion purpose 是泛化模板 ('{matched}') —— "
                f"建议改为本场景的具体情绪变化"
            ),
        ))
    return issues


def _state_is_empty_or_generic(state: Any) -> tuple[bool, str]:
    """Return (is_empty_or_generic, reason)."""
    if state is None:
        return True, "未提供"
    if isinstance(state, dict) and not state:
        return True, "空对象"
    if isinstance(state, (list, tuple)) and not state:
        return True, "空列表"
    flat = _coerce_to_string(state).strip()
    if not flat:
        return True, "空字符串"
    length = _count_meaningful_chars(flat)
    min_len = _MIN_STATE_STR_LEN_CJK if _is_primarily_cjk(flat) else _MIN_STATE_STR_LEN_LATIN
    if length < min_len:
        return True, f"过短 ({length} 字 < {min_len})"
    matched = _matches_generic_template(flat, GENERIC_STATE_PATTERNS)
    if matched:
        return True, f"泛化模板 '{matched}'"
    return False, ""


def _check_entry_exit_states(
    entry_state: dict[str, Any],
    exit_state: dict[str, Any],
    *,
    language: str,
) -> list[RichnessIssue]:
    issues: list[RichnessIssue] = []

    entry_empty, entry_reason = _state_is_empty_or_generic(entry_state)
    exit_empty, exit_reason = _state_is_empty_or_generic(exit_state)

    if entry_empty:
        issues.append(RichnessIssue(
            field_path="entry_state",
            severity="warning",
            code="entry_state_empty_or_generic",
            message=f"entry_state {entry_reason} —— 场景起始缺乏锚点",
        ))
    if exit_empty:
        issues.append(RichnessIssue(
            field_path="exit_state",
            severity="critical",
            code="exit_state_empty_or_generic",
            message=f"exit_state {exit_reason} —— 场景无法推进状态",
        ))

    # State delta check: if both present, they must differ — a scene that
    # doesn't change anything is dead weight and breeds repetition.
    if not entry_empty and not exit_empty:
        entry_flat = _normalize_for_match(_coerce_to_string(entry_state))
        exit_flat = _normalize_for_match(_coerce_to_string(exit_state))
        if entry_flat == exit_flat:
            issues.append(RichnessIssue(
                field_path="exit_state",
                severity="critical",
                code="no_state_delta",
                message="entry_state 与 exit_state 完全相同 —— 场景未推进任何状态",
            ))
    return issues


def _check_participants(
    participants: list[str] | tuple[str, ...],
    scene_type: str,
) -> list[RichnessIssue]:
    issues: list[RichnessIssue] = []
    stype = (scene_type or "").lower().strip()
    # Solo scenes (inner monologue, travel) can legitimately have 0–1
    # participants; interactive scene types require at least 2.
    interactive_types = {"dialogue", "confrontation", "conflict", "对话", "冲突", "对峙"}
    if not participants:
        severity = "warning" if stype not in interactive_types else "critical"
        issues.append(RichnessIssue(
            field_path="participants",
            severity=severity,
            code="no_participants",
            message="participants 为空 —— 场景缺乏角色锚点",
        ))
    elif len(participants) == 1 and stype in interactive_types:
        issues.append(RichnessIssue(
            field_path="participants",
            severity="critical",
            code="interactive_needs_two",
            message=f"scene_type='{scene_type}' 需要≥2 名参与者，当前仅 1 名",
        ))
    return issues


def _check_scene_type(scene_type: str | None) -> list[RichnessIssue]:
    issues: list[RichnessIssue] = []
    if not scene_type or not scene_type.strip():
        issues.append(RichnessIssue(
            field_path="scene_type",
            severity="critical",
            code="missing_scene_type",
            message="scene_type 缺失 —— 无法确定场景功能",
        ))
    return issues


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_scene_card_richness(
    *,
    scene_type: str | None,
    purpose: dict[str, Any] | None,
    entry_state: dict[str, Any] | None,
    exit_state: dict[str, Any] | None,
    participants: list[str] | tuple[str, ...] | None,
    language: str = "zh-CN",
) -> RichnessReport:
    """Validate a scene card's richness and return a structured report.

    All inputs map directly to ``SceneCardModel`` fields so the caller can
    pass them in without adapting. None/missing fields are treated as empty
    and produce issues where appropriate.

    Parameters
    ----------
    scene_type
        The card's ``scene_type`` (required).
    purpose
        The ``purpose`` JSON dict; may contain ``story`` / ``emotion`` keys.
    entry_state / exit_state
        The ``entry_state`` / ``exit_state`` JSON dicts.
    participants
        List of participant character names.
    language
        Primary language of the project ("zh-CN" or "en") — affects how
        prompt blocks are rendered and which length thresholds apply.

    Returns
    -------
    RichnessReport
        ``is_rich_enough`` is True iff there are no critical issues.
        ``severity`` is "pass" / "warning" / "critical".
    """
    purpose = purpose or {}
    entry_state = entry_state or {}
    exit_state = exit_state or {}
    participants = list(participants or [])

    issues: list[RichnessIssue] = []
    issues.extend(_check_scene_type(scene_type))
    issues.extend(_check_purpose_story(purpose, language=language))
    issues.extend(_check_purpose_emotion(purpose, language=language))
    issues.extend(_check_entry_exit_states(entry_state, exit_state, language=language))
    issues.extend(_check_participants(participants, scene_type or ""))

    critical_count = sum(1 for i in issues if i.severity == "critical")
    warning_count = sum(1 for i in issues if i.severity == "warning")
    if critical_count:
        severity = "critical"
    elif warning_count:
        severity = "warning"
    else:
        severity = "pass"

    return RichnessReport(
        is_rich_enough=(critical_count == 0),
        severity=severity,
        issues=tuple(issues),
    )


def validate_scene_model(scene: Any, *, language: str = "zh-CN") -> RichnessReport:
    """Convenience wrapper — accept a ``SceneCardModel`` instance directly."""
    return validate_scene_card_richness(
        scene_type=getattr(scene, "scene_type", None),
        purpose=getattr(scene, "purpose", None),
        entry_state=getattr(scene, "entry_state", None),
        exit_state=getattr(scene, "exit_state", None),
        participants=getattr(scene, "participants", None),
        language=language,
    )
