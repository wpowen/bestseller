from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class QimaoPlanningFinding:
    code: str
    severity: str
    message: str
    evidence: str


@dataclass(frozen=True)
class QimaoPlanningGateReport:
    passed: bool
    findings: tuple[QimaoPlanningFinding, ...]


_ORDINARY_OPENING_TERMS = (
    "背景",
    "世界观",
    "设定",
    "风景",
    "天气",
    "清晨醒来",
    "醒来",
    "起床",
    "赶路",
    "路上",
    "旅行",
    "上班",
    "上学",
    "日常",
    "普通一天",
    "平静的一天",
    "normal day",
    "background",
    "worldbuilding",
    "scenery",
    "travel",
    "wakes",
)

_LOOP_MARKERS = ("->", "→", "触发", "行动", "收益", "代价", "钩子", "hook", "reward", "cost")
_CONTRACT_FIELD_NAMES = {
    "opening_incident",
    "first_page_conflict",
    "protagonist_immediate_goal",
    "visible_loss_if_fail",
    "protagonist_edge",
    "edge_limit",
    "chapter_1_small_turn",
    "chapter_2_reveal",
    "chapter_3_payoff",
    "first_10000_loop",
    "forbidden_opening_modes",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_blank(value: Any) -> bool:
    return not _text(value)


def _contract_from_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    data = _mapping(payload)
    if "opening_quality_contract" in data:
        return _mapping(data.get("opening_quality_contract"))
    if "qimao_opening_contract" in data:
        return _mapping(data.get("qimao_opening_contract"))
    if any(field in data for field in _CONTRACT_FIELD_NAMES):
        return data
    return {}


def _looks_like_ordinary_opening(value: str) -> bool:
    lowered = value.lower()
    return any(term in lowered for term in _ORDINARY_OPENING_TERMS)


def _looks_like_loop(value: str) -> bool:
    lowered = value.lower()
    marker_count = sum(1 for marker in _LOOP_MARKERS if marker in lowered)
    has_transition = "->" in value or "→" in value
    has_action = "行动" in value or "action" in lowered
    has_reward_or_cost = any(item in lowered for item in ("收益", "代价", "reward", "cost"))
    has_hook = "钩子" in value or "hook" in lowered
    return has_transition and has_action and has_reward_or_cost and has_hook and marker_count >= 4


def evaluate_qimao_planning_gate(payload: Mapping[str, Any] | None) -> QimaoPlanningGateReport:
    contract = _contract_from_payload(payload)
    findings: list[QimaoPlanningFinding] = []

    if not contract:
        return QimaoPlanningGateReport(
            passed=False,
            findings=(
                QimaoPlanningFinding(
                    code="missing_opening_quality_contract",
                    severity="critical",
                    message="项目缺少 opening_quality_contract，不能进入正文生成。",
                    evidence="opening_quality_contract is absent",
                ),
            ),
        )

    opening_incident = _text(contract.get("opening_incident"))
    if not opening_incident:
        findings.append(QimaoPlanningFinding(
            code="missing_opening_incident",
            severity="critical",
            message="缺少开篇事件，无法证明第一章为什么能点开。",
            evidence="opening_incident is blank",
        ))
    elif _looks_like_ordinary_opening(opening_incident):
        findings.append(QimaoPlanningFinding(
            code="ordinary_entry",
            severity="critical",
            message="开篇事件疑似普通日常、背景、风景、赶路或醒来式切入。",
            evidence=opening_incident,
        ))

    required_fields = (
        ("protagonist_immediate_goal", "missing_protagonist_goal", "缺少主角即时目标，代入感会弱。"),
        ("visible_loss_if_fail", "missing_visible_loss", "缺少失败损失，第一章压力不足。"),
        ("protagonist_edge", "missing_protagonist_edge", "缺少主角差异化优势，第二章前无法建立追读理由。"),
        ("chapter_3_payoff", "missing_chapter_3_payoff", "缺少第三章小爽点或回报，黄金三章闭环不成立。"),
    )
    for field, code, message in required_fields:
        if _is_blank(contract.get(field)):
            findings.append(QimaoPlanningFinding(
                code=code,
                severity="critical",
                message=message,
                evidence=f"{field} is blank",
            ))

    first_10k_loop = _text(contract.get("first_10000_loop"))
    if not first_10k_loop:
        findings.append(QimaoPlanningFinding(
            code="first_10k_loop_missing",
            severity="critical",
            message="缺少前一万字循环，无法证明可持续连载追读。",
            evidence="first_10000_loop is blank",
        ))
    elif not _looks_like_loop(first_10k_loop):
        findings.append(QimaoPlanningFinding(
            code="first_10k_loop_missing",
            severity="critical",
            message="前一万字循环不是清晰的触发-行动-收益/代价-钩子结构。",
            evidence=first_10k_loop,
        ))

    forbidden_modes = contract.get("forbidden_opening_modes")
    if not isinstance(forbidden_modes, list) or not forbidden_modes:
        findings.append(QimaoPlanningFinding(
            code="missing_forbidden_opening_modes",
            severity="warning",
            message="缺少禁用开篇模式列表，后续生成更容易滑回普通开场。",
            evidence="forbidden_opening_modes is empty",
        ))

    passed = not any(item.severity == "critical" for item in findings)
    return QimaoPlanningGateReport(passed=passed, findings=tuple(findings))


def qimao_planning_gate_report_to_dict(report: QimaoPlanningGateReport) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity,
                "message": finding.message,
                "evidence": finding.evidence,
            }
            for finding in report.findings
        ],
    }
