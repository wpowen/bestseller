"""Audit gate for writing-principle event-unit contracts.

The source principle is event-oriented, not chapter-oriented: a chapter may
serve one role inside a larger event cycle instead of repeating every step.
This gate therefore checks distribution, handoff, desire, pressure, and payoff
signals across a batch, while keeping findings advisory by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bestseller.domain.workflow import ChapterOutlineBatchInput, ChapterOutlineInput

EVENT_CYCLE_ROLES: tuple[str, ...] = (
    "trigger",
    "desire_lock",
    "obstacle_escalation",
    "method_search",
    "execution_turn",
    "payoff_feedback",
    "reaction_reset",
    "bridge_hook",
)

ROLE_ALIASES: dict[str, str] = {
    "emotion_event": "trigger",
    "emotional_event": "trigger",
    "inciting_event": "trigger",
    "trigger_event": "trigger",
    "desire": "desire_lock",
    "goal_lock": "desire_lock",
    "want_lock": "desire_lock",
    "obstacle": "obstacle_escalation",
    "resistance": "obstacle_escalation",
    "pressure": "obstacle_escalation",
    "solution": "method_search",
    "method": "method_search",
    "search": "method_search",
    "action": "execution_turn",
    "execution": "execution_turn",
    "turning_point": "execution_turn",
    "payoff": "payoff_feedback",
    "feedback": "payoff_feedback",
    "resolution": "payoff_feedback",
    "reaction": "reaction_reset",
    "reset": "reaction_reset",
    "bridge": "bridge_hook",
    "hook": "bridge_hook",
    "transition": "bridge_hook",
    "reveal": "execution_turn",
}

CAUSAL_FUNCTION_ROLE_MAP: dict[str, str] = {
    "payoff": "payoff_feedback",
    "reveal": "execution_turn",
    "action": "execution_turn",
    "transition": "bridge_hook",
}

ROLE_REQUIRED_AXES: dict[str, tuple[str, ...]] = {
    "trigger": ("reader_desire", "pressure_or_obstacle"),
    "desire_lock": ("reader_desire", "pressure_or_obstacle"),
    "obstacle_escalation": ("pressure_or_obstacle",),
    "method_search": ("method_or_action",),
    "execution_turn": ("method_or_action",),
    "payoff_feedback": ("feedback_or_handoff",),
    "reaction_reset": ("feedback_or_handoff",),
    "bridge_hook": ("feedback_or_handoff",),
}

AXIS_LABELS: dict[str, str] = {
    "reader_desire": "reader desire or protagonist goal",
    "pressure_or_obstacle": "pressure, obstacle, resistance, or cost",
    "method_or_action": "solution method, choice, action, or turn",
    "feedback_or_handoff": "payoff, feedback, aftereffect, or next waiting",
}


@dataclass(frozen=True)
class StoryPrincipleFinding:
    code: str
    severity: str
    chapter_number: int | None
    message: str
    path: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "chapter_number": self.chapter_number,
            "message": self.message,
            "path": self.path,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class StoryPrincipleChapterResult:
    chapter_number: int
    role: str | None
    information_gap_mode: str | None
    present_axes: dict[str, bool]
    findings: tuple[StoryPrincipleFinding, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_number": self.chapter_number,
            "role": self.role,
            "information_gap_mode": self.information_gap_mode,
            "present_axes": self.present_axes,
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True)
class StoryPrincipleGateReport:
    passed: bool
    findings: tuple[StoryPrincipleFinding, ...]
    chapter_results: tuple[StoryPrincipleChapterResult, ...]
    present_roles: set[str]

    def to_dict(self) -> dict[str, Any]:
        return story_principle_report_to_dict(self)


def evaluate_story_principle_contract(
    batch: ChapterOutlineBatchInput | dict[str, Any],
    *,
    min_roles_per_batch: int = 3,
    max_same_role_streak: int = 3,
) -> StoryPrincipleGateReport:
    """Evaluate story-principle health across a chapter-outline batch.

    Findings are warnings. This intentionally avoids blocking a chapter because
    it lacks all six event steps; only the event-unit distribution is audited.
    """

    if isinstance(batch, dict):
        batch = ChapterOutlineBatchInput.model_validate(batch)

    findings: list[StoryPrincipleFinding] = []
    chapter_results: list[StoryPrincipleChapterResult] = []
    present_roles: set[str] = set()
    previous_role: str | None = None
    same_role_streak = 0

    for index, chapter in enumerate(batch.chapters):
        chapter_result = _evaluate_chapter(chapter, index=index)
        chapter_results.append(chapter_result)
        findings.extend(chapter_result.findings)

        role = chapter_result.role
        if role:
            present_roles.add(role)
            if role == previous_role:
                same_role_streak += 1
            else:
                previous_role = role
                same_role_streak = 1
            if same_role_streak > max_same_role_streak:
                findings.append(
                    StoryPrincipleFinding(
                        code="EVENT_ROLE_STREAK",
                        severity="warning",
                        chapter_number=chapter.chapter_number,
                        message=(
                            f"Chapter event role `{role}` repeats more than "
                            f"{max_same_role_streak} chapters in a row."
                        ),
                        path=f"chapters[{index}].chapter_event_role",
                        evidence={"role": role, "streak": same_role_streak},
                    )
                )
        else:
            previous_role = None
            same_role_streak = 0

    if len(batch.chapters) >= min_roles_per_batch and len(present_roles) < min_roles_per_batch:
        findings.append(
            StoryPrincipleFinding(
                code="EVENT_ROLE_COVERAGE_LOW",
                severity="warning",
                chapter_number=None,
                message=(
                    "The outline batch uses too few event-cycle roles; this can make "
                    "chapters feel structurally homogeneous."
                ),
                path="chapters",
                evidence={
                    "present_roles": sorted(present_roles),
                    "min_roles_per_batch": min_roles_per_batch,
                },
            )
        )

    _append_batch_axis_warning(
        findings,
        chapter_results,
        axis="reader_desire",
        code="READER_DESIRE_MISSING",
        message="No chapter in the batch exposes a clear reader desire or protagonist goal.",
    )
    _append_batch_axis_warning(
        findings,
        chapter_results,
        axis="pressure_or_obstacle",
        code="PRESSURE_OR_OBSTACLE_MISSING",
        message="No chapter in the batch exposes pressure, obstacle, resistance, or cost.",
    )
    _append_batch_axis_warning(
        findings,
        chapter_results,
        axis="feedback_or_handoff",
        code="FEEDBACK_OR_HANDOFF_MISSING",
        message="No chapter in the batch exposes payoff, feedback, aftereffect, or next waiting.",
    )

    passed = not any(finding.severity in {"block", "critical", "error"} for finding in findings)
    return StoryPrincipleGateReport(
        passed=passed,
        findings=tuple(findings),
        chapter_results=tuple(chapter_results),
        present_roles=present_roles,
    )


def story_principle_report_to_dict(report: StoryPrincipleGateReport) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "findings": [finding.to_dict() for finding in report.findings],
        "chapter_results": [result.to_dict() for result in report.chapter_results],
        "present_roles": sorted(report.present_roles),
    }


def _evaluate_chapter(
    chapter: ChapterOutlineInput,
    *,
    index: int,
) -> StoryPrincipleChapterResult:
    causal_contract = _mapping(chapter.causal_contract)
    event_contract = _event_cycle_contract(chapter, causal_contract)
    role = _normalize_role(
        _first_text(
            getattr(chapter, "chapter_event_role", None),
            event_contract.get("chapter_event_role"),
            event_contract.get("event_cycle_role"),
            event_contract.get("event_role"),
            event_contract.get("step_focus"),
            causal_contract.get("chapter_function"),
        )
    )
    if role is None:
        role = CAUSAL_FUNCTION_ROLE_MAP.get(
            _normalize_token(causal_contract.get("chapter_function"))
        )

    evidence = event_contract if event_contract else causal_contract
    present_axes = {
        "reader_desire": _has_any_text(
            evidence,
            causal_contract,
            "reader_desire",
            "desire_goal",
            "reader_expectation",
            "reader_waiting",
            "next_reader_waiting",
            "next_reader_desire",
            "protagonist_desire",
        ),
        "pressure_or_obstacle": _has_any_text(
            evidence,
            causal_contract,
            "event_pressure",
            "pressure",
            "obstacle",
            "resistance",
            "dilemma",
            "cost",
            "cost_or_tradeoff",
            "main_conflict",
        )
        or _text(chapter.main_conflict) != "",
        "method_or_action": _has_any_text(
            evidence,
            causal_contract,
            "solution_method",
            "method",
            "action_resolution",
            "execution",
            "turn",
            "protagonist_choice",
            "visible_action_or_reaction",
        ),
        "feedback_or_handoff": _has_any_text(
            evidence,
            causal_contract,
            "resolution_feedback",
            "feedback",
            "payoff",
            "handoff_to_next",
            "aftereffect",
            "next_reader_waiting",
            "gain_or_reveal",
            "state_change",
            "next_reader_desire",
            "hook_description",
        )
        or _text(chapter.hook_description) != "",
    }

    findings: list[StoryPrincipleFinding] = []
    if not event_contract and role is None:
        findings.append(
            StoryPrincipleFinding(
                code="EVENT_CYCLE_CONTRACT_MISSING",
                severity="warning",
                chapter_number=chapter.chapter_number,
                message=(
                    "Chapter has no event-cycle role or contract; audit cannot place it "
                    "inside a larger event unit."
                ),
                path=f"chapters[{index}].event_cycle_contract",
            )
        )

    if role:
        for axis in ROLE_REQUIRED_AXES.get(role, ()):
            if not present_axes.get(axis, False):
                findings.append(
                    StoryPrincipleFinding(
                        code="EVENT_ROLE_AXIS_MISSING",
                        severity="warning",
                        chapter_number=chapter.chapter_number,
                        message=f"Event role `{role}` lacks {AXIS_LABELS[axis]}.",
                        path=f"chapters[{index}].event_cycle_contract",
                        evidence={"role": role, "missing_axis": axis},
                    )
                )

    info_gap = _first_text(
        getattr(chapter, "information_gap_mode", None),
        event_contract.get("information_gap_mode"),
        causal_contract.get("information_gap_mode"),
    )
    return StoryPrincipleChapterResult(
        chapter_number=chapter.chapter_number,
        role=role,
        information_gap_mode=info_gap or None,
        present_axes=present_axes,
        findings=tuple(findings),
    )


def _event_cycle_contract(
    chapter: ChapterOutlineInput,
    causal_contract: dict[str, Any],
) -> dict[str, Any]:
    event_contract = _mapping(getattr(chapter, "event_cycle_contract", None))
    if event_contract:
        return event_contract
    for key in ("event_cycle_contract", "event_unit_contract", "chapter_event_contract"):
        nested = _mapping(causal_contract.get(key))
        if nested:
            return nested
    return {}


def _append_batch_axis_warning(
    findings: list[StoryPrincipleFinding],
    chapter_results: list[StoryPrincipleChapterResult],
    *,
    axis: str,
    code: str,
    message: str,
) -> None:
    has_axis = any(result.present_axes.get(axis, False) for result in chapter_results)
    if chapter_results and not has_axis:
        findings.append(
            StoryPrincipleFinding(
                code=code,
                severity="warning",
                chapter_number=None,
                message=message,
                path="chapters",
            )
        )


def _normalize_role(value: Any) -> str | None:
    token = _normalize_token(value)
    if not token:
        return None
    if token in EVENT_CYCLE_ROLES:
        return token
    return ROLE_ALIASES.get(token)


def _normalize_token(value: Any) -> str:
    return _text(value).lower().replace("-", "_").replace(" ", "_")


def _has_any_text(
    primary: dict[str, Any],
    secondary: dict[str, Any],
    *keys: str,
) -> bool:
    for key in keys:
        if _text(primary.get(key)) or _text(secondary.get(key)):
            return True
    return False


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "; ".join(item for item in (_text(item) for item in value) if item)
    if isinstance(value, dict):
        return "; ".join(item for item in (_text(item) for item in value.values()) if item)
    return str(value).strip()
