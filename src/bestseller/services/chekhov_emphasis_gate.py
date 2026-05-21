"""Chekhov-emphasis methodology gate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bestseller.services.checker_schema import CheckerIssue, CheckerReport, Severity
from bestseller.services.methodology_overlay import as_mapping, text

CHEKHOV_GATE_AGENT = "chekhov-emphasis-gate"

_PAID_STATUSES = {"paid", "paid_off", "resolved", "used", "completed", "done"}
_HIGH_PROMINENCE = {"high", "heavy", "major", "重点", "高", "强"}
_MINOR_TYPES = {"minor", "small", "background", "atmosphere", "小伏笔", "氛围"}


def evaluate_chekhov_emphasis(
    *,
    emphasized_items: Sequence[Mapping[str, Any]] = (),
    chapter_contract: Mapping[str, Any] | None = None,
    current_chapter: int = 0,
    mode: str = "audit_only",
) -> CheckerReport:
    """Check structured emphasized items for functional commitment and payoff debt."""

    items = list(emphasized_items) or _extract_emphasized_items(chapter_contract)
    issues: list[CheckerIssue] = []

    for index, raw in enumerate(items, start=1):
        item = as_mapping(raw)
        label = _item_label(item, index)
        prominence = text(item.get("prominence") or item.get("weight")).lower()
        clue_type = text(item.get("clue_type") or item.get("type")).lower()
        expected_function = text(
            item.get("expected_function")
            or item.get("function")
            or item.get("expected_use")
        )
        expected_window = _safe_int(
            item.get("expected_use_by_chapter")
            or item.get("expected_payoff_by_chapter")
            or item.get("payoff_by_chapter")
        )
        status = text(item.get("status")).lower()

        if _is_high_prominence(prominence) and not expected_function:
            issues.append(
                _issue(
                    code="CHEKHOV_EXPECTED_FUNCTION_MISSING",
                    description=f"被强调元素“{label}”缺少后续功能承诺。",
                    suggestion="补出它后文要承担的行动用途、信息用途或反转用途。",
                    chapter=current_chapter,
                    label=label,
                    mode=mode,
                )
            )
        if _is_high_prominence(prominence) and expected_window is None:
            issues.append(
                _issue(
                    code="CHEKHOV_USE_WINDOW_MISSING",
                    description=f"被强调元素“{label}”缺少预计使用章节。",
                    suggestion="给出 expected_use_by_chapter，避免强调物无限期悬挂。",
                    chapter=current_chapter,
                    label=label,
                    mode=mode,
                    severity="medium",
                )
            )
        if (
            expected_window is not None
            and current_chapter
            and expected_window <= current_chapter
            and status not in _PAID_STATUSES
        ):
            issues.append(
                _issue(
                    code="CHEKHOV_USE_OVERDUE",
                    description=f"被强调元素“{label}”已到第 {expected_window} 章使用窗口但仍未完成。",
                    suggestion="本章回收该元素，或显式改签新的兑现窗口和原因。",
                    chapter=current_chapter,
                    label=label,
                    mode=mode,
                )
            )
        if _is_high_prominence(prominence) and clue_type in _MINOR_TYPES:
            issues.append(
                _issue(
                    code="CHEKHOV_MINOR_CLUE_OVEREMPHASIZED",
                    description=f"小伏笔“{label}”被写成高显著强调物。",
                    suggestion="降低描写权重，或把它升级为真正承担后续功能的 Chekhov 元素。",
                    chapter=current_chapter,
                    label=label,
                    mode=mode,
                    severity="medium",
                )
            )
        if bool(item.get("dual_type")) and not (
            text(item.get("clue_code")) or text(item.get("foreshadowing_id"))
        ):
            issues.append(
                _issue(
                    code="CHEKHOV_DUAL_TYPE_UNLINKED",
                    description=f"元素“{label}”标记为 Chekhov/伏笔双类型，但未关联伏笔 ledger。",
                    suggestion="补充 clue_code 或 foreshadowing_id，避免检查器无法追踪回收。",
                    chapter=current_chapter,
                    label=label,
                    mode=mode,
                    severity="medium",
                )
            )

    passed = not issues
    score = max(0, 100 - sum(_severity_penalty(issue.severity) for issue in issues))
    return CheckerReport(
        agent=CHEKHOV_GATE_AGENT,
        chapter=current_chapter,
        overall_score=score,
        passed=passed,
        issues=tuple(issues),
        metrics={
            "emphasized_item_count": len(items),
            "current_chapter": current_chapter,
        },
        summary=(
            "契诃夫强调物检查通过。"
            if passed
            else f"契诃夫强调物检查发现 {len(issues)} 个方法论风险。"
        ),
    )


def _extract_emphasized_items(chapter_contract: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    data = as_mapping(chapter_contract)
    metadata = as_mapping(data.get("metadata"))
    candidates = (
        data.get("emphasized_items")
        or metadata.get("emphasized_items")
        or data.get("chekhov_items")
        or metadata.get("chekhov_items")
    )
    if isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes)):
        return [item for item in candidates if isinstance(item, Mapping)]
    return []


def _item_label(item: Mapping[str, Any], index: int) -> str:
    return text(item.get("item") or item.get("name") or item.get("label")) or f"item-{index}"


def _is_high_prominence(value: str) -> bool:
    return value in _HIGH_PROMINENCE or value.startswith("high")


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _issue(
    *,
    code: str,
    description: str,
    suggestion: str,
    chapter: int,
    label: str,
    mode: str,
    severity: Severity = "high",
) -> CheckerIssue:
    return CheckerIssue(
        id=code,
        type="methodology_chekhov",
        severity=severity,
        location=f"chapter {chapter}: {label}",
        description=description,
        suggestion=suggestion,
        can_override=str(mode) != "strict",
        allowed_rationales=("EDITORIAL_INTENT", "ARC_TIMING") if str(mode) != "strict" else (),
    )


def _severity_penalty(severity: Severity) -> int:
    return {"critical": 25, "high": 15, "medium": 8, "low": 3}[severity]


__all__ = ["CHEKHOV_GATE_AGENT", "evaluate_chekhov_emphasis"]
