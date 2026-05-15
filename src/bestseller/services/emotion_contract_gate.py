"""CheckerReport adapter for EmotionDrivenKernel contracts.

The core emotion kernel intentionally stays independent from checker and debt
infrastructure.  This adapter is the boundary where deterministic emotion
contract issues become scorecard/debt-friendly ``CheckerReport`` rows.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import re
from typing import Any

from bestseller.services.checker_schema import CheckerIssue, CheckerReport, Severity
from bestseller.services.emotion_driven_kernel import (
    BombContract,
    EmotionContractIssue,
    EmotionDrivenKernel,
    emotion_driven_kernel_from_dict,
    evaluate_emotion_contracts,
)

EMPATHY_CHAIN_MISSING = "EMPATHY_CHAIN_MISSING"
BOMB_TRIGGER_MISSING = "BOMB_TRIGGER_MISSING"
BOMB_PAYOFF_OVERDUE = "BOMB_PAYOFF_OVERDUE"
ANTAGONIST_MASK_FLAT = "ANTAGONIST_MASK_FLAT"
ENDING_COST_ERASED = "ENDING_COST_ERASED"
TRAGEDY_CAUSALITY_WEAK = "TRAGEDY_CAUSALITY_WEAK"

EMOTION_DEBT_CODES: frozenset[str] = frozenset(
    {
        EMPATHY_CHAIN_MISSING,
        BOMB_TRIGGER_MISSING,
        BOMB_PAYOFF_OVERDUE,
        ANTAGONIST_MASK_FLAT,
        ENDING_COST_ERASED,
        TRAGEDY_CAUSALITY_WEAK,
    }
)

_OVERRIDABLE_RATIONALES: tuple[str, ...] = (
    "ARC_TIMING",
    "GENRE_CONVENTION",
    "EDITORIAL_INTENT",
)

_ISSUE_CODE_TO_DEBT_CODE: dict[str, str] = {
    "EMPATHY_CONTRACT_MISSING": EMPATHY_CHAIN_MISSING,
    "EMPATHY_CHAIN_MISSING": EMPATHY_CHAIN_MISSING,
    "BOMB_TRIGGER_MISSING": BOMB_TRIGGER_MISSING,
    "BOMB_CONTRACT_INCOMPLETE": BOMB_TRIGGER_MISSING,
    "ANTAGONIST_MASK_FLAT": ANTAGONIST_MASK_FLAT,
    "ENDING_TEXTURE_MISSING": ENDING_COST_ERASED,
    "ENDING_COST_ERASED": ENDING_COST_ERASED,
    "HE_TEXTURE_INCOMPLETE": ENDING_COST_ERASED,
    "TRAGEDY_CAUSALITY_WEAK": TRAGEDY_CAUSALITY_WEAK,
}

_POLICY_BY_CODE: dict[str, tuple[Severity, bool]] = {
    "EMOTION_KERNEL_MISSING": ("medium", True),
    "EMOTION_PROMISE_MISSING": ("medium", True),
    EMPATHY_CHAIN_MISSING: ("high", True),
    BOMB_TRIGGER_MISSING: ("high", True),
    BOMB_PAYOFF_OVERDUE: ("high", True),
    ANTAGONIST_MASK_FLAT: ("medium", True),
    ENDING_COST_ERASED: ("high", True),
    TRAGEDY_CAUSALITY_WEAK: ("critical", False),
    "TRAGEDY_CHOICE_MISSING": ("critical", False),
    "ENDING_CALLBACK_MISSING": ("high", True),
}

_SUGGESTION_BY_CODE: dict[str, str] = {
    "EMOTION_KERNEL_MISSING": "生成并持久化 EmotionDrivenKernel，再进入卷纲或章纲修复。",
    "EMOTION_PROMISE_MISSING": "补齐读者情绪承诺：读者在等待、担心、期待什么。",
    EMPATHY_CHAIN_MISSING: (
        "补齐代入链：处境、当前欲望、感官入口、判断逻辑、合理行动和行动后果。"
    ),
    BOMB_TRIGGER_MISSING: (
        "补齐桌下炸弹：读者已知、角色盲区、触发条件、倒计时、后果和兑现窗口。"
    ),
    BOMB_PAYOFF_OVERDUE: "兑现或升级已到期的信息差炸弹，不要让读者等待落空。",
    ANTAGONIST_MASK_FLAT: "补齐反派真实善行、隐秘欲望、裂缝、自我辩护和崩塌伤口。",
    ENDING_COST_ERASED: "重写结局纹理：核心圆满可以兑现，但不可逆代价必须被承认。",
    TRAGEDY_CAUSALITY_WEAK: "重建悲剧因果：破灭必须来自人物、责任、身份或世界规则。",
    "TRAGEDY_CHOICE_MISSING": "让人物主动做出价值选择，而不是被作者强行发刀。",
    "ENDING_CALLBACK_MISSING": "设置可回收的意象、台词、场景或信物，承接前文情绪重量。",
}


def emotion_debt_code_for_issue(issue_code: str) -> str:
    """Return the stable debt/scorecard code for a raw emotion issue code."""

    return _ISSUE_CODE_TO_DEBT_CODE.get(issue_code, issue_code)


def build_emotion_contract_checker_report(
    kernel: EmotionDrivenKernel | Mapping[str, Any] | None,
    *,
    chapter: int = 0,
    current_chapter: int | None = None,
    resolved_bomb_ids: Iterable[str] = (),
    agent: str = "emotion-contract-gate",
) -> CheckerReport:
    """Convert emotion contract validation into the unified checker schema.

    ``current_chapter`` and ``resolved_bomb_ids`` let callers surface
    ``BOMB_PAYOFF_OVERDUE`` without requiring a database dependency in this
    module.  Future ChaseDebt integration can pass the persisted resolution
    set at the call site.
    """

    hydrated = _coerce_kernel(kernel)
    gate_report = evaluate_emotion_contracts(hydrated)
    issues = [
        _checker_issue_from_contract_issue(issue)
        for issue in gate_report.issues
    ]
    if hydrated is not None and current_chapter is not None:
        issues.extend(
            _overdue_bomb_issues(
                hydrated.bomb_contracts,
                current_chapter=current_chapter,
                resolved_bomb_ids=frozenset(str(item) for item in resolved_bomb_ids),
            )
        )

    penalty = sum(
        {"critical": 25, "high": 15, "medium": 8, "low": 3}[issue.severity]
        for issue in issues
    )
    blocking_severities = {"critical", "high"}
    passed = not any(issue.severity in blocking_severities for issue in issues)
    return CheckerReport(
        agent=agent,
        chapter=chapter,
        overall_score=max(0, 100 - penalty),
        passed=passed,
        issues=tuple(issues),
        metrics={
            "emotion_kernel_present": hydrated is not None,
            "issue_count": len(issues),
            "debt_codes": [
                issue.id for issue in issues if issue.id in EMOTION_DEBT_CODES
            ],
            "overdue_bomb_ids": [
                issue.location.removeprefix("bomb_contracts.")
                for issue in issues
                if issue.id == BOMB_PAYOFF_OVERDUE
            ],
        },
        summary=(
            "Emotion contracts are ready."
            if not issues
            else f"Emotion contract gate found {len(issues)} issue(s)."
        ),
    )


def emotion_contract_gate_snapshot(report: CheckerReport) -> dict[str, Any]:
    """Return a compact metadata snapshot from an emotion checker report."""

    return {
        "passed": report.passed,
        "score": report.overall_score,
        "issue_codes": [issue.id for issue in report.issues],
        "debt_codes": [
            issue.id for issue in report.issues if issue.id in EMOTION_DEBT_CODES
        ],
        "hard_violation_count": len(report.hard_violations),
        "soft_suggestion_count": len(report.soft_suggestions),
        "report": report.to_dict(),
    }


def _coerce_kernel(
    kernel: EmotionDrivenKernel | Mapping[str, Any] | None,
) -> EmotionDrivenKernel | None:
    if kernel is None:
        return None
    if isinstance(kernel, EmotionDrivenKernel):
        return kernel
    return emotion_driven_kernel_from_dict(dict(kernel))


def _checker_issue_from_contract_issue(issue: EmotionContractIssue) -> CheckerIssue:
    issue_id = emotion_debt_code_for_issue(issue.code)
    severity, can_override = _POLICY_BY_CODE.get(
        issue_id,
        _POLICY_BY_CODE.get(issue.code, ("medium", True)),
    )
    missing = ", ".join(issue.missing_fields)
    description = issue.message + (f" Missing fields: {missing}." if missing else "")
    return CheckerIssue(
        id=issue_id,
        type="emotion_contract",
        severity=severity,
        location=issue.path,
        description=description,
        suggestion=_SUGGESTION_BY_CODE.get(
            issue_id,
            _SUGGESTION_BY_CODE.get(issue.code, "修复情绪合同缺口。"),
        ),
        can_override=can_override,
        allowed_rationales=_OVERRIDABLE_RATIONALES if can_override else (),
    )


def _overdue_bomb_issues(
    bombs: Sequence[BombContract],
    *,
    current_chapter: int,
    resolved_bomb_ids: frozenset[str],
) -> list[CheckerIssue]:
    issues: list[CheckerIssue] = []
    for index, bomb in enumerate(bombs):
        bomb_id = bomb.bomb_id.strip() or f"bomb-{index + 1}"
        if bomb_id in resolved_bomb_ids:
            continue
        due_chapter = _range_end(bomb.payoff_window)
        if due_chapter is None or current_chapter <= due_chapter:
            continue
        issues.append(
            CheckerIssue(
                id=BOMB_PAYOFF_OVERDUE,
                type="emotion_contract",
                severity="high",
                location=f"bomb_contracts.{bomb_id}",
                description=(
                    f"Bomb payoff window is overdue at chapter {current_chapter}: "
                    f"{bomb.payoff_window}"
                ),
                suggestion=_SUGGESTION_BY_CODE[BOMB_PAYOFF_OVERDUE],
                can_override=True,
                allowed_rationales=_OVERRIDABLE_RATIONALES,
            )
        )
    return issues


def _range_end(value: str) -> int | None:
    numbers = [int(match) for match in re.findall(r"\d+", value or "")]
    if not numbers:
        return None
    return max(numbers)


__all__ = [
    "ANTAGONIST_MASK_FLAT",
    "BOMB_PAYOFF_OVERDUE",
    "BOMB_TRIGGER_MISSING",
    "EMOTION_DEBT_CODES",
    "EMPATHY_CHAIN_MISSING",
    "ENDING_COST_ERASED",
    "TRAGEDY_CAUSALITY_WEAK",
    "build_emotion_contract_checker_report",
    "emotion_contract_gate_snapshot",
    "emotion_debt_code_for_issue",
]
