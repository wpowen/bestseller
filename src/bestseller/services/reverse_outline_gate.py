# ruff: noqa: RUF001
"""Reverse-outline gate for story-design verification.

The forward planner can produce plausible-looking outlines that do not actually
change story state.  This gate reads the generated outline backward and checks
whether chapters contain concrete state movement instead of generic progression
or forbidden default motivations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bestseller.services.story_design_kernel import story_design_kernel_from_dict

_BLOCKING_SEVERITIES = {"critical", "high"}
_DEFAULT_FORBIDDEN_MOTIFS = (
    "父母失踪",
    "亲人失踪",
    "神秘玉佩",
    "退婚羞辱",
    "神秘老人",
    "天降外挂",
)
_GENERIC_PROGRESS_PHRASES = (
    "推进主线",
    "继续承压推进",
    "出现新的问题",
    "建立世界观",
    "引入势力",
    "深化主题",
    "完善体系",
    "推动剧情",
    "制造冲突",
    "advance the plot",
    "new problem appears",
)
_STATE_CHANGE_MARKERS = (
    "从",
    "到",
    "转为",
    "改变",
    "获得",
    "拿到",
    "失去",
    "暴露",
    "避开",
    "封锁",
    "升级",
    "下降",
    "上升",
    "资格",
    "债",
    "信任",
    "资源",
    "身份",
    "风险",
    "成本",
    "代价",
    "gain",
    "lose",
    "shift",
    "change",
    "cost",
    "risk",
)


@dataclass(frozen=True, slots=True)
class ReverseOutlineFinding:
    code: str
    severity: str
    message: str
    path: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class ReverseOutlineReport:
    passed: bool
    score: int
    blocking_findings: tuple[ReverseOutlineFinding, ...]
    warnings: tuple[ReverseOutlineFinding, ...]
    state_snapshot: Mapping[str, Any]


def reverse_outline_report_to_dict(report: ReverseOutlineReport) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "score": report.score,
        "blocking_findings": [finding.to_dict() for finding in report.blocking_findings],
        "warnings": [finding.to_dict() for finding in report.warnings],
        "state_snapshot": dict(report.state_snapshot),
    }


def build_story_state_snapshot(
    story_design_kernel: Mapping[str, Any] | None,
    outline_payload: Mapping[str, Any] | Sequence[Any] | None,
) -> dict[str, Any]:
    """Extract a compact state-change snapshot from kernel + outline."""

    kernel_change_vectors: list[str] = []
    reader_promise = ""
    if story_design_kernel:
        try:
            kernel = story_design_kernel_from_dict(dict(story_design_kernel))
            kernel_change_vectors = list(kernel.change_vectors)
            reader_promise = kernel.reader_promise
        except Exception:
            kernel_change_vectors = _string_list(
                _as_mapping(story_design_kernel).get("change_vectors")
            )
            reader_promise = _text(_as_mapping(story_design_kernel).get("reader_promise"))

    chapters = _outline_chapters(outline_payload)
    chapter_state_changes: list[dict[str, Any]] = []
    for index, chapter in enumerate(chapters, 1):
        text = _chapter_text(chapter)
        chapter_state_changes.append(
            {
                "chapter_number": int(chapter.get("chapter_number") or index),
                "title": _text(chapter.get("title")),
                "goal": _text(chapter.get("goal")),
                "main_conflict": _text(chapter.get("main_conflict")),
                "hook_description": _text(chapter.get("hook_description")),
                "has_state_change": _chapter_has_state_change(chapter),
                "state_markers": [
                    marker for marker in _STATE_CHANGE_MARKERS if marker.lower() in text.lower()
                ][:8],
            }
        )

    return {
        "reader_promise": reader_promise,
        "kernel_change_vectors": kernel_change_vectors,
        "chapter_count": len(chapters),
        "chapter_state_changes": chapter_state_changes,
    }


def evaluate_reverse_outline_gate(
    story_design_kernel: Mapping[str, Any] | None,
    outline_payload: Mapping[str, Any] | Sequence[Any] | None,
) -> ReverseOutlineReport:
    findings: list[ReverseOutlineFinding] = []
    chapters = _outline_chapters(outline_payload)
    snapshot = build_story_state_snapshot(story_design_kernel, outline_payload)
    forbidden = _forbidden_motifs(story_design_kernel)

    if not story_design_kernel:
        findings.append(
            ReverseOutlineFinding(
                code="story_design_kernel_missing",
                severity="warning",
                message="Reverse outline cannot verify against a missing StoryDesignKernel.",
                path="story_design_kernel",
            )
        )

    if not chapters:
        findings.append(
            ReverseOutlineFinding(
                code="outline_missing_chapters",
                severity="high",
                message="Outline has no chapters to verify.",
                path="chapters",
            )
        )

    previous_conflict = ""
    for index, chapter in enumerate(chapters, 1):
        number = int(chapter.get("chapter_number") or index)
        text = _chapter_text(chapter)
        matched_forbidden = [motif for motif in forbidden if motif and motif in text]
        if matched_forbidden:
            findings.append(
                ReverseOutlineFinding(
                    code="forbidden_default_motivation",
                    severity="high",
                    message="Chapter outline uses a forbidden default motivation.",
                    path=f"chapters[{index - 1}]",
                    evidence={"chapter_number": number, "motifs": matched_forbidden},
                )
            )
        if not _chapter_has_state_change(chapter):
            findings.append(
                ReverseOutlineFinding(
                    code="chapter_missing_state_change",
                    severity="high",
                    message="Chapter lacks a concrete state change in goal/conflict/scenes.",
                    path=f"chapters[{index - 1}]",
                    evidence={"chapter_number": number, "title": _text(chapter.get("title"))},
                )
            )
        conflict = _normalize(_text(chapter.get("main_conflict")))
        if conflict and previous_conflict and conflict == previous_conflict:
            findings.append(
                ReverseOutlineFinding(
                    code="duplicate_adjacent_conflict",
                    severity="warning",
                    message="Adjacent chapters repeat the same normalized main conflict.",
                    path=f"chapters[{index - 1}].main_conflict",
                    evidence={"chapter_number": number},
                )
            )
        previous_conflict = conflict

    blocking = tuple(
        finding for finding in findings if finding.severity in _BLOCKING_SEVERITIES
    )
    warnings = tuple(
        finding for finding in findings if finding.severity not in _BLOCKING_SEVERITIES
    )
    penalty = sum(
        20 if finding.severity == "critical" else 12 if finding.severity == "high" else 5
        for finding in findings
    )
    score = max(0, 100 - penalty)
    return ReverseOutlineReport(
        passed=not blocking,
        score=score,
        blocking_findings=blocking,
        warnings=warnings,
        state_snapshot=snapshot,
    )


def _outline_chapters(
    outline_payload: Mapping[str, Any] | Sequence[Any] | None,
) -> list[dict[str, Any]]:
    if isinstance(outline_payload, Mapping):
        raw = outline_payload.get("chapters")
        return _mapping_list(raw)
    return _mapping_list(outline_payload)


def _chapter_has_state_change(chapter: Mapping[str, Any]) -> bool:
    fields = [
        _text(chapter.get("goal")),
        _text(chapter.get("main_conflict")),
        _text(chapter.get("hook_description")),
    ]
    scene_texts = []
    for scene in _mapping_list(chapter.get("scenes")):
        scene_texts.append(_text(scene.get("story")))
        scene_texts.append(_text(scene.get("emotion")))
        scene_texts.append(_text(scene.get("exit_state")))
    concrete_fields = [
        field
        for field in [*fields, *scene_texts]
        if len(field) >= 8 and not _is_generic_progress(field)
    ]
    joined = " ".join(concrete_fields).lower()
    marker_count = len(
        {marker for marker in _STATE_CHANGE_MARKERS if marker.lower() in joined}
    )
    return len(concrete_fields) >= 1 and marker_count >= 1


def _chapter_text(chapter: Mapping[str, Any]) -> str:
    parts = [
        _text(chapter.get("title")),
        _text(chapter.get("goal")),
        _text(chapter.get("main_conflict")),
        _text(chapter.get("hook_description")),
    ]
    for scene in _mapping_list(chapter.get("scenes")):
        parts.extend(_text(scene.get(key)) for key in ("story", "emotion", "exit_state"))
    return " ".join(part for part in parts if part)


def _forbidden_motifs(story_design_kernel: Mapping[str, Any] | None) -> list[str]:
    motifs = list(_DEFAULT_FORBIDDEN_MOTIFS)
    kernel = _as_mapping(story_design_kernel)
    premise = _as_mapping(kernel.get("premise_contract"))
    motifs.extend(_string_list(premise.get("forbidden_defaults")))
    return _dedupe([motif for motif in motifs if motif])


def _is_generic_progress(text: str) -> bool:
    normalized = _normalize(text)
    return any(_normalize(phrase) in normalized for phrase in _GENERIC_PROGRESS_PHRASES)


def _normalize(text: str) -> str:
    return "".join(text.lower().split()).strip("。,.，；;：:")


def _as_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_list(value: object) -> list[dict[str, Any]]:
    if value is None or isinstance(value, str):
        return []
    if isinstance(value, Sequence):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
