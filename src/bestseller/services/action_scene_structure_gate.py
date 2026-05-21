"""Action-scene methodology gate."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bestseller.services.checker_schema import CheckerIssue, CheckerReport, Severity
from bestseller.services.methodology_overlay import normalize_scene_overlay, text

ACTION_SCENE_GATE_AGENT = "action-scene-structure-gate"

_ACTION_SCENE_TYPES = frozenset(
    {"action", "battle", "chase", "climax", "combat", "confrontation", "fight"}
)
_ACTION_KEYWORDS = (
    "打斗",
    "战斗",
    "交手",
    "厮杀",
    "追击",
    "伏击",
    "出手",
    "格挡",
    "battle",
    "fight",
    "combat",
    "strike",
    "ambush",
    "chase",
)

_REQUIRED_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "fight_objective",
        "ACTION_SCENE_OBJECTIVE_MISSING",
        "动作场景缺少明确目标。",
        "补出角色为什么必须打、要夺回/保护/逃离什么。",
    ),
    (
        "failure_cost",
        "ACTION_SCENE_FAILURE_COST_MISSING",
        "动作场景缺少失败代价。",
        "补出失败后会失去什么、暴露什么、导致谁受伤或让哪条线崩坏。",
    ),
    (
        "opponent_advantage",
        "ACTION_SCENE_OPPONENT_ADVANTAGE_MISSING",
        "动作场景缺少对手优势或环境压制。",
        "补出对手、地形、规则、人数、信息或资源上的优势。",
    ),
    (
        "tactic_shift",
        "ACTION_SCENE_TACTIC_SHIFT_MISSING",
        "动作场景缺少策略变化。",
        "让角色在过程中因受挫、发现或代价改变打法。",
    ),
    (
        "emotion_driver",
        "ACTION_SCENE_EMOTION_DRIVER_MISSING",
        "动作场景缺少情绪驱动。",
        "说明这场战斗背后的恐惧、愤怒、保护欲、羞耻或关系债。",
    ),
    (
        "turning_point",
        "ACTION_SCENE_TURNING_POINT_MISSING",
        "动作场景缺少胜负或局面转折。",
        "补出战局从不利到有利、优势反转或信息揭露的转折点。",
    ),
    (
        "exit_state_delta",
        "ACTION_SCENE_STATE_DELTA_MISSING",
        "动作场景结束后局面没有变化。",
        "明确战斗后资源、关系、伤势、暴露程度或下一步压力如何改变。",
    ),
)


def evaluate_action_scene_structure(
    *,
    scene_text: str = "",
    scene_contract: Mapping[str, Any] | None = None,
    scene_type: str | None = None,
    chapter: int = 0,
    scene_number: int | None = None,
    mode: str = "audit_only",
) -> CheckerReport:
    """Check whether an action scene has a goal/cost/turn/result spine."""

    overlay = normalize_scene_overlay(scene_contract)
    is_action_scene = _is_action_scene(
        scene_text=scene_text,
        scene_type=scene_type,
        overlay=overlay,
    )
    if not is_action_scene:
        return CheckerReport(
            agent=ACTION_SCENE_GATE_AGENT,
            chapter=chapter,
            overall_score=100,
            passed=True,
            issues=(),
            metrics={"is_action_scene": False},
            summary="非动作场景，动作结构 gate 跳过。",
        )

    issues: list[CheckerIssue] = []
    for field, code, description, suggestion in _REQUIRED_FIELDS:
        if text(overlay.get(field)):
            continue
        issues.append(
            _issue(
                code=code,
                description=description,
                suggestion=suggestion,
                chapter=chapter,
                scene_number=scene_number,
                mode=mode,
            )
        )
    if not overlay.get("action_sequence"):
        issues.append(
            _issue(
                code="ACTION_SCENE_TACTIC_SHIFT_MISSING",
                description="动作场景缺少动作序列，无法判断策略是否发生变化。",
                suggestion="至少列出关键动作、受挫点、策略调整和转折动作。",
                chapter=chapter,
                scene_number=scene_number,
                mode=mode,
                severity="medium",
            )
        )

    passed = not issues
    score = max(0, 100 - sum(_severity_penalty(issue.severity) for issue in issues))
    return CheckerReport(
        agent=ACTION_SCENE_GATE_AGENT,
        chapter=chapter,
        overall_score=score,
        passed=passed,
        issues=tuple(issues),
        metrics={
            "is_action_scene": True,
            "scene_number": scene_number,
            "present_fields": [
                field for field, _, _, _ in _REQUIRED_FIELDS if text(overlay.get(field))
            ],
        },
        summary=(
            "动作场景结构通过。"
            if passed
            else f"动作场景结构发现 {len(issues)} 个方法论风险。"
        ),
    )


def _is_action_scene(
    *,
    scene_text: str,
    scene_type: str | None,
    overlay: Mapping[str, Any],
) -> bool:
    scene_type_text = str(scene_type or "").strip().lower()
    if scene_type_text in _ACTION_SCENE_TYPES:
        return True
    if overlay.get("action_sequence") or any(overlay.get(field) for field, *_ in _REQUIRED_FIELDS):
        return True
    lowered = str(scene_text or "").lower()
    return any(token.lower() in lowered for token in _ACTION_KEYWORDS)


def _issue(
    *,
    code: str,
    description: str,
    suggestion: str,
    chapter: int,
    scene_number: int | None,
    mode: str,
    severity: Severity = "high",
) -> CheckerIssue:
    location = f"chapter {chapter}"
    if scene_number is not None:
        location += f" scene {scene_number}"
    return CheckerIssue(
        id=code,
        type="methodology_action_scene",
        severity=severity,
        location=location,
        description=description,
        suggestion=suggestion,
        can_override=str(mode) != "strict",
        allowed_rationales=("EDITORIAL_INTENT", "ARC_TIMING") if str(mode) != "strict" else (),
    )


def _severity_penalty(severity: Severity) -> int:
    return {"critical": 25, "high": 15, "medium": 8, "low": 3}[severity]


__all__ = ["ACTION_SCENE_GATE_AGENT", "evaluate_action_scene_structure"]
