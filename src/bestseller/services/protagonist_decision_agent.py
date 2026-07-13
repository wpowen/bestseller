"""Protagonist-centred decision intelligence contracts and audit prompts.

The outline pipeline historically checked that a protagonist *made* a choice,
but not whether a normal, informed person with this character's goals would
make that choice.  This module supplies two layers for the existing outline
judge and repair loop:

1. a deterministic contract audit that makes the decision context inspectable;
2. a semantic first-person counterfactual prompt used by the strong LLM judge.

Keeping the semantic pass inside the existing outline judge avoids a second,
disconnected quality workflow while still giving the decision audit a distinct
role, vocabulary, evidence contract, and repair codes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

DECISION_PROTOCOL_REQUIRED_FIELDS: tuple[str, ...] = (
    "viewpoint_character",
    "decision_point",
    "known_facts",
    "unknowns",
    "immediate_goal",
    "options_considered",
    "obvious_safe_option",
    "chosen_action",
    "why_not_safer_option",
    "personality_basis",
    "risk_control",
    "expected_gain",
    "failure_cost",
    "new_information_or_pressure",
    "first_person_reasoning",
)

_LIST_FIELDS = {"known_facts", "unknowns", "options_considered"}
_HIGH_RISK_MARKERS = (
    "死亡",
    "必死",
    "当场死",
    "丧命",
    "送命",
    "不可逆",
    "无法撤回",
    "终身",
    "毁掉",
    "death",
    "die",
    "fatal",
    "irreversible",
    "no return",
)
_WEAK_RISK_CONTROL = (
    "没有",
    "无",
    "只能赌",
    "随机应变",
    "见机行事",
    "相信自己",
    "剧情需要",
    "none",
    "improvise",
)
_PLACEHOLDER_MARKERS = (
    "推动剧情",
    "剧情需要",
    "情况紧急",
    "没有时间",
    "只能这样",
    "按计划行动",
    "做出选择",
    "采取行动",
    "advance the plot",
    "the story requires",
    "must act",
)


@dataclass(frozen=True, slots=True)
class ProtagonistDecisionFinding:
    code: str
    severity: str
    message: str
    path: str
    repair_hint: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "repair_hint": self.repair_hint,
            "evidence": dict(self.evidence),
            "blocking": self.blocking,
        }


@dataclass(frozen=True, slots=True)
class ProtagonistDecisionReport:
    passed: bool
    score: int
    blocking_findings: tuple[ProtagonistDecisionFinding, ...]
    audit_findings: tuple[ProtagonistDecisionFinding, ...]
    missing_fields: tuple[str, ...]

    @property
    def blocking_codes(self) -> tuple[str, ...]:
        return tuple(finding.code for finding in self.blocking_findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "blocking_findings": [item.to_dict() for item in self.blocking_findings],
            "audit_findings": [item.to_dict() for item in self.audit_findings],
            "missing_fields": list(self.missing_fields),
        }


def evaluate_protagonist_decision_protocol(
    protocol: Mapping[str, Any] | None,
    *,
    chapter_number: int,
    blocking: bool = True,
) -> ProtagonistDecisionReport:
    """Validate that a chapter exposes enough context for a real choice audit.

    This intentionally does not pretend deterministic string rules can decide
    whether a choice is *wise*.  It checks that the semantic judge has the
    evidence needed to do that job, plus one low-false-positive invariant:
    irreversible/high-mortality choices need an explicit exit or mitigation.
    """

    data = dict(protocol or {})
    missing: list[str] = []
    for field_name in DECISION_PROTOCOL_REQUIRED_FIELDS:
        value = data.get(field_name)
        if field_name in _LIST_FIELDS:
            if not _text_list(value):
                missing.append(field_name)
        elif not _text(value):
            missing.append(field_name)

    findings: list[ProtagonistDecisionFinding] = []
    if missing:
        findings.append(
            ProtagonistDecisionFinding(
                code="PROTAGONIST_DECISION_CONTEXT_INCOMPLETE",
                severity="critical" if chapter_number <= 10 else "high",
                message=(f"第{chapter_number}章只有选择结论，没有足够的第一人称决策上下文。"),
                path="methodology_contract.decision_protocol",
                repair_hint=(
                    "补齐角色当时知道/不知道什么、即时目标、至少两个选项、显而易见的"
                    "安全选项、为什么不能选、性格依据、风险控制、收益/损失、新压力，"
                    "并用第一人称写出一句可检验的思考。"
                ),
                evidence={"missing_fields": missing},
                blocking=blocking,
            )
        )

    options = _text_list(data.get("options_considered"))
    if data.get("options_considered") is not None and len(options) < 2:
        findings.append(
            ProtagonistDecisionFinding(
                code="PROTAGONIST_OPTIONS_NOT_COMPARED",
                severity="high",
                message="决策协议没有比较至少两个真实可行的选项。",
                path="methodology_contract.decision_protocol.options_considered",
                repair_hint="至少列出两个主角当时真的能采取的行动，不能用明显送死的假选项衬托作者预设答案。",
                evidence={"options": options},
                blocking=blocking,
            )
        )

    protocol_text = " ".join(_flatten_text(data)).lower()
    risk_control = _text(data.get("risk_control"))
    if any(marker.lower() in protocol_text for marker in _HIGH_RISK_MARKERS) and (
        not risk_control
        or any(marker.lower() == risk_control.lower() for marker in _WEAK_RISK_CONTROL)
    ):
        findings.append(
            ProtagonistDecisionFinding(
                code="PROTAGONIST_HIGH_RISK_WITHOUT_EXIT",
                severity="critical",
                message="主角选择包含死亡或不可逆代价，却没有退出、止损、试探或后手。",
                path="methodology_contract.decision_protocol.risk_control",
                repair_hint="让主角先试探规则、降低暴露、安排同伴/退路/触发条件；若确实无法止损，必须给出逼迫他承担风险的硬约束。",
                evidence={
                    "chosen_action": _text(data.get("chosen_action")),
                    "failure_cost": _text(data.get("failure_cost")),
                },
                blocking=blocking,
            )
        )

    reasoning = _text(data.get("first_person_reasoning"))
    if reasoning and any(marker.lower() in reasoning.lower() for marker in _PLACEHOLDER_MARKERS):
        findings.append(
            ProtagonistDecisionFinding(
                code="PROTAGONIST_REASONING_IS_AUTHOR_PLACEHOLDER",
                severity="high",
                message="第一人称理由仍是作者视角的剧情占位语，不能证明角色会这样选。",
                path="methodology_contract.decision_protocol.first_person_reasoning",
                repair_hint="改写为角色能感知的事实、欲望、恐惧、代价比较和止损方案，禁止使用推动剧情/情况紧急等空话。",
                evidence={"first_person_reasoning": reasoning},
                blocking=blocking,
            )
        )

    blocking_findings = tuple(item for item in findings if item.blocking)
    audit_findings = tuple(item for item in findings if not item.blocking)
    penalty = sum(28 if item.severity == "critical" else 16 for item in findings)
    return ProtagonistDecisionReport(
        passed=not blocking_findings,
        score=max(0, 100 - penalty),
        blocking_findings=blocking_findings,
        audit_findings=audit_findings,
        missing_fields=tuple(missing),
    )


def render_planner_decision_protocol_contract(*, language: str = "zh-CN") -> str:
    """Return the compact schema + reasoning rules required from the planner."""

    if language.lower().startswith("en"):
        return (
            "[PROTAGONIST DECISION PROTOCOL — REQUIRED FOR EVERY CHAPTER] "
            "Inside chapter-level methodology_contract output decision_protocol with: "
            "viewpoint_character, decision_point, known_facts (facts available at that moment), "
            "unknowns, immediate_goal, options_considered (at least 2 real options), "
            "obvious_safe_option, chosen_action, why_not_safer_option, personality_basis, "
            "risk_control (test/exit/backup), expected_gain, failure_cost, "
            "new_information_or_pressure, first_person_reasoning. Ignore the author's desired "
            "plot outcome while choosing: the action must be credible from the character's "
            "limited knowledge, goals, intelligence, fears, resources, and established traits. "
            "Never force risk by hiding an obvious low-cost verification, help-seeking, retreat, "
            "delay, delegation, bargaining, concealment, or backup plan."
        )
    return (
        "【主角决策协议·每章必填】在章节级 methodology_contract 内输出 decision_protocol："
        "viewpoint_character、decision_point、known_facts（当时确实知道的事实）、unknowns、"
        "immediate_goal、options_considered（至少两个真实可行选项）、obvious_safe_option、"
        "chosen_action、why_not_safer_option、personality_basis、risk_control（试探/退路/后手）、"
        "expected_gain、failure_cost、new_information_or_pressure、first_person_reasoning。"
        "做选择时暂时忘掉作者希望剧情去哪里，只允许使用角色当时的有限认知、目标、智力、恐惧、"
        "资源与既定性格。不得故意藏掉显而易见的低成本核验、求助、撤退、拖延、委托、谈判、隐瞒或后手，"
        "再逼主角为剧情冒险。"
    )


def render_outline_decision_agent_prompt(*, language: str = "zh-CN") -> str:
    """Semantic audit instructions injected into the strong outline judge."""

    if language.lower().startswith("en"):
        return (
            "# PROTAGONIST DECISION AGENT\n"
            "Audit each major choice from inside the viewpoint character, not from the author. "
            "Build (1) a normal-person baseline under the same facts and stakes, (2) a character "
            "baseline adjusted for established intelligence, values, fears, resources and flaws, "
            "then compare the planned action. Temporarily forget where the author wants the plot "
            "to go. Search for missing low-cost tests, help, retreat, delay, delegation, "
            "bargaining, "
            "concealment, or backups. Enforce knowledge boundaries. If the planned choice is worse "
            "than both baselines and only plot convenience explains it, emit blocking code "
            "PROTAGONIST_PLOT_SERVING_STUPIDITY. Risky is not automatically stupid when coercion, "
            "values, incomplete information, and mitigation make it the character's best "
            "available move."
        )
    return (
        "# 主角决策代理（逐章第一人称反事实审计）\n"
        "不要问『这样能不能把剧情推到下一幕』，要站进主角身体里判断『我会不会这样做』。\n"
        "1. 正常人基线：在相同已知事实、风险、资源下，一个会趋利避害的正常人先做什么。\n"
        "2. 角色基线：再叠加该人物的智力、人格、欲望、恐惧、职业能力、关系与缺陷，"
        "他最可能做什么。\n"
        "3. 暂时忘掉作者想让剧情去哪里，主动寻找大纲隐去的低成本核验、求助、撤退、拖延、委托、"
        "谈判、隐瞒、试探和后手。\n"
        "4. 严守认知边界：角色不能使用自己尚不知道的信息，也不能因为作者知道真相就精准踩中答案。\n"
        "5. 若实际选择同时劣于正常人基线和角色基线，且唯一解释是方便冲突发生，必须给 blocking code "
        "PROTAGONIST_PLOT_SERVING_STUPIDITY；required_fix 要写成改变信息、压力、"
        "选项成本或人物选择，"
        "不能只补一句心理描写。\n"
        "6. 冒险不等于降智：若硬约束、价值观、有限信息和止损后手共同证明它是当时"
        "最佳可行选项，应通过。"
    )


def render_writer_decision_protocol(
    protocol: Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
) -> str:
    """Render a non-JSON writer instruction so compaction cannot hide the choice."""

    data = dict(protocol or {})
    if not data:
        return ""
    if language.lower().startswith("en"):
        header = "[PROTAGONIST DECISION TO DRAMATIZE — do not print this checklist]"
    else:
        header = "【主角决策落地·不得把本清单写进正文】"
    labels = (
        ("viewpoint_character", "角色"),
        ("known_facts", "他此刻知道"),
        ("unknowns", "他仍不知道"),
        ("immediate_goal", "即时目标"),
        ("options_considered", "可行选项"),
        ("obvious_safe_option", "显然更安全的选项"),
        ("chosen_action", "实际选择"),
        ("why_not_safer_option", "为何不能选更安全方案"),
        ("personality_basis", "人设依据"),
        ("risk_control", "止损/退路/后手"),
        ("first_person_reasoning", "第一人称思考"),
    )
    lines = [header]
    for key, label in labels:
        value = data.get(key)
        values = _text_list(value) if key in _LIST_FIELDS else []
        rendered = "；".join(values) if values else _text(value)
        if rendered:
            lines.append(f"- {label}：{rendered}")
    if len(lines) == 1:
        return ""
    if language.lower().startswith("zh"):
        lines.append(
            "正文用行动、观察、犹豫、试探与后手让选择成立；不要用旁白一句『他别无选择』糊过去。"
        )
    else:
        lines.append(
            "Make the choice credible through action, observation, testing and backup plans; "
            "never hand-wave it as 'no choice'."
        )
    return "\n".join(lines)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [text for item in value if (text := _text(item))]
    return []


def _flatten_text(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        result: list[str] = []
        for item in value.values():
            result.extend(_flatten_text(item))
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result = []
        for item in value:
            result.extend(_flatten_text(item))
        return result
    text = _text(value)
    return [text] if text else []


__all__ = [
    "DECISION_PROTOCOL_REQUIRED_FIELDS",
    "ProtagonistDecisionFinding",
    "ProtagonistDecisionReport",
    "evaluate_protagonist_decision_protocol",
    "render_outline_decision_agent_prompt",
    "render_planner_decision_protocol_contract",
    "render_writer_decision_protocol",
]
