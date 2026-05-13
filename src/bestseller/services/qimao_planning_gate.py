from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


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

_URGENT_OPENING_TERMS = (
    "异常",
    "异象",
    "异动",
    "危机",
    "冲突",
    "即时",
    "损失",
    "危险",
    "逼迫",
    "被迫",
    "压力",
    "反制",
    "真相揭露",
    "anomaly",
    "crisis",
    "conflict",
    "forced",
    "loss",
    "threat",
    "pressure",
)

_CONCRETE_PRESSURE_TERMS = (
    "被逼",
    "逼迫",
    "逼着",
    "逼问",
    "逼交",
    "否则",
    "抢",
    "夺",
    "烧",
    "烧掉",
    "点火",
    "杀",
    "追到",
    "追上",
    "逃",
    "绑",
    "押",
    "拦",
    "交出",
    "签字",
    "灭口",
    "救下",
    "救人",
    "救命",
    "反制",
    "揭穿",
    "逼出",
    "保住",
    "拿到",
    "证据",
    "账本",
    "火把",
    "柴堆",
    "人质",
    "当场",
    "门口",
    "谁",
    "不说",
    "threatens",
    "or else",
    "forced",
    "evidence",
    "hostage",
    "burn",
    "kill",
    "chased",
)

_ABSTRACT_OPENING_TERMS = (
    "视觉锚点",
    "世界观入口",
    "异象",
    "异动",
    "差异化身份",
    "能力展示",
    "首次展示",
    "暗示",
    "前置",
    "自然带出",
    "隐秘过往",
    "设定感",
    "小型真相揭露",
    "危险逼近",
    "维持",
    "气质",
    "基调",
    "氛围",
    "残相",
    "记忆",
    "建立",
    "呈现",
    "同步",
    "anomaly",
    "visual anchor",
    "worldbuilding",
    "showcase",
    "foreshadow",
)

_META_GOLDEN_THREE_TERMS = (
    "第一章",
    "第二章",
    "第三章",
    "前三章",
    "三章内",
    "世界观",
    "身份辨识度",
    "基本规则",
    "节奏口径",
    "读者获得",
    "心理反馈",
    "chapter 1",
    "chapter 2",
    "chapter 3",
    "first three",
)
_LOOP_MARKERS = ("->", "→", "触发", "行动", "收益", "代价", "钩子", "hook", "reward", "cost")
_LOOP_ACTION_TERMS = (
    "行动",
    "抢先",
    "主动",
    "反制",
    "追查",
    "选择",
    "升级手段",
    "act",
    "action",
)
_LOOP_REWARD_TERMS = (
    "收益",
    "回报",
    "得到",
    "拿到",
    "获得",
    "线索",
    "筹码",
    "reward",
    "payoff",
    "gain",
)
_LOOP_COST_TERMS = (
    "代价",
    "反压",
    "威胁",
    "损失",
    "成本",
    "危险",
    "pressure",
    "threat",
    "cost",
    "loss",
)
_LOOP_HOOK_TERMS = (
    "钩子",
    "更深真相",
    "更大谜题",
    "谜题",
    "悬念",
    "下一轮",
    "引出",
    "揭开",
    "hook",
    "question",
    "reveal",
)
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
    if not any(term in lowered for term in _ORDINARY_OPENING_TERMS):
        return False
    return not any(term in lowered for term in _URGENT_OPENING_TERMS)


def _looks_like_loop(value: str) -> bool:
    lowered = value.lower()
    explicit_marker_count = sum(1 for marker in _LOOP_MARKERS if marker in lowered)
    has_transition = "->" in value or "→" in value
    has_action = any(item in lowered for item in _LOOP_ACTION_TERMS)
    has_reward = any(item in lowered for item in _LOOP_REWARD_TERMS)
    has_cost = any(item in lowered for item in _LOOP_COST_TERMS)
    has_hook = any(item in lowered for item in _LOOP_HOOK_TERMS)
    semantic_marker_count = sum((has_action, has_reward, has_cost, has_hook))
    return has_transition and semantic_marker_count >= 4 and (
        explicit_marker_count >= 4 or semantic_marker_count >= 4
    )


def _has_concrete_pressure(value: str) -> bool:
    lowered = value.lower()
    return any(term.lower() in lowered for term in _CONCRETE_PRESSURE_TERMS)


def _looks_like_abstract_opening(value: str) -> bool:
    lowered = value.lower()
    abstract_hits = sum(1 for term in _ABSTRACT_OPENING_TERMS if term.lower() in lowered)
    return abstract_hits >= 2 and not _has_concrete_pressure(value)


def _looks_like_non_immediate_goal(value: str) -> bool:
    lowered = value.lower()
    if len(value) > 180 and not _has_concrete_pressure(value):
        return True
    return any(
        term in lowered
        for term in (
            "逐渐触及",
            "逐渐揭开",
            "追查",
            "终极秘密",
            "死亡真相",
            "母亲死亡",
            "下落",
            "流向",
            "天道",
            "国运",
            "过程中",
            "这片土地",
            "三百年前",
            "世界观",
            "long-term",
            "ultimate",
        )
    ) and not _has_concrete_pressure(value)


def _looks_like_meta_payoff(value: str) -> bool:
    lowered = value.lower()
    meta_hits = sum(1 for term in _META_GOLDEN_THREE_TERMS if term.lower() in lowered)
    if _has_concrete_pressure(value):
        return False
    if meta_hits >= 2:
        return True
    return meta_hits >= 1 and any(
        term in lowered
        for term in (
            "让主角",
            "完成",
            "打开",
            "引出",
            "小回报",
            "筹码",
            "更大威胁",
            "更大谜题",
            "下一轮",
        )
    )


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
    elif _looks_like_abstract_opening(opening_incident):
        findings.append(QimaoPlanningFinding(
            code="abstract_opening_incident",
            severity="critical",
            message="开篇事件是抽象执行口号，不是可拍成一场戏的当场事件。",
            evidence=opening_incident,
        ))

    first_page_conflict = _text(contract.get("first_page_conflict"))
    if not first_page_conflict:
        findings.append(QimaoPlanningFinding(
            code="missing_first_page_conflict",
            severity="critical",
            message="缺少前600字冲突，第一屏没有明确压力。",
            evidence="first_page_conflict is blank",
        ))
    elif _looks_like_abstract_opening(first_page_conflict) or not _has_concrete_pressure(first_page_conflict):
        findings.append(QimaoPlanningFinding(
            code="abstract_first_page_conflict",
            severity="critical",
            message="前600字冲突没有落到谁逼谁、主角要保住什么、失败会损失什么。",
            evidence=first_page_conflict,
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

    protagonist_goal = _text(contract.get("protagonist_immediate_goal"))
    if protagonist_goal and _looks_like_non_immediate_goal(protagonist_goal):
        findings.append(QimaoPlanningFinding(
            code="non_immediate_protagonist_goal",
            severity="critical",
            message="主角即时目标是长期主线或世界观目标，不是第一章可执行动作。",
            evidence=protagonist_goal,
        ))

    chapter_1_turn = _text(contract.get("chapter_1_small_turn"))
    if not chapter_1_turn:
        findings.append(QimaoPlanningFinding(
            code="missing_chapter_1_small_turn",
            severity="critical",
            message="缺少第一章小转折，黄金三章没有第一口回报。",
            evidence="chapter_1_small_turn is blank",
        ))
    elif _looks_like_abstract_opening(chapter_1_turn) or not _has_concrete_pressure(chapter_1_turn):
        findings.append(QimaoPlanningFinding(
            code="abstract_chapter_1_turn",
            severity="critical",
            message="第一章小转折不是具体动作或具体信息差，无法指导场景生成。",
            evidence=chapter_1_turn,
        ))

    chapter_3_payoff = _text(contract.get("chapter_3_payoff"))
    if chapter_3_payoff and _looks_like_meta_payoff(chapter_3_payoff):
        findings.append(QimaoPlanningFinding(
            code="meta_chapter_3_payoff",
            severity="critical",
            message="第三章回报是规划口号，不是具体可兑现的爽点或线索回报。",
            evidence=chapter_3_payoff,
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
