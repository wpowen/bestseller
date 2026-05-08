from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from bestseller.domain.decision_policy import (
    DecisionAudit,
    DecisionEvent,
    DecisionFinding,
    DecisionPolicy,
    ForbiddenBehavior,
    MoralBoundary,
    PreferredTactic,
    PressureResponse,
    RiskTolerance,
)


def _normalize(value: str) -> str:
    return value.strip().lower()


def _finding(
    code: str,
    message: str,
    *,
    severity: Literal["info", "warning", "error"] = "error",
    blocking: bool = True,
) -> DecisionFinding:
    return DecisionFinding(
        code=code,
        severity=severity,
        message=message,
        blocking=blocking,
    )


def _audit(findings: Sequence[DecisionFinding]) -> DecisionAudit:
    return DecisionAudit(
        passed=not any(finding.blocking for finding in findings),
        findings=tuple(findings),
    )


def cautious_survival_policy(character_name: str) -> DecisionPolicy:
    """Return a default `凡人流` cautious-survival protagonist policy."""
    return DecisionPolicy(
        character_name=character_name,
        archetype="cautious_survivalist",
        risk_tolerance=RiskTolerance.LOW,
        pressure_responses=(
            PressureResponse.OBSERVE,
            PressureResponse.PREPARE,
            PressureResponse.CONCEAL,
            PressureResponse.BARGAIN,
            PressureResponse.RETREAT,
            PressureResponse.STRIKE_AFTER_CERTAINTY,
        ),
        preferred_tactics=(
            PreferredTactic(key="observe", description="先观察局势和对手底牌。"),
            PreferredTactic(key="prepare", description="先准备资源、后手和退路。"),
            PreferredTactic(key="conceal", description="隐藏真实实力和核心秘密。"),
            PreferredTactic(key="retreat", description="收益不足或风险失控时主动撤退。"),
            PreferredTactic(key="strike_after_certainty", description="确认胜算后再出手。"),
        ),
        moral_boundaries=(
            MoralBoundary(key="do_not_harm_unrelated_weak", description="不为便利伤害无关弱者。"),
        ),
        forbidden_behaviors=(
            ForbiddenBehavior(key="public_vanity_duel", description="不可为虚荣接受公开决斗。"),
            ForbiddenBehavior(key="boast_secret_power", description="不可主动炫耀秘密底牌。"),
            ForbiddenBehavior(key="trust_stranger_freely", description="不可无成本信任陌生人。"),
        ),
    )


def _allowed_high_risk_causes(policy: DecisionPolicy, event: DecisionEvent) -> set[str]:
    causes = {_normalize(tag) for tag in event.motive_tags}
    if event.is_life_threat:
        causes.add("life_threat")
    if event.has_credible_escape_route:
        causes.add("credible_escape_route")
    if event.protects_innocent:
        causes.add("protect_innocent")
    return causes & {_normalize(tag) for tag in policy.high_risk_allowances}


def _forbidden_behavior_findings(
    policy: DecisionPolicy,
    event: DecisionEvent,
) -> list[DecisionFinding]:
    event_behaviors = {_normalize(tag) for tag in event.behavior_tags}
    findings: list[DecisionFinding] = []
    for behavior in policy.forbidden_behaviors:
        if _normalize(behavior.key) in event_behaviors:
            findings.append(
                _finding(
                    "FORBIDDEN_BEHAVIOR",
                    f"{event.character_name} action violates forbidden behavior "
                    f"{behavior.key}: {behavior.description}",
                ),
            )
    return findings


def _boundary_findings(
    policy: DecisionPolicy,
    event: DecisionEvent,
) -> list[DecisionFinding]:
    violated = {_normalize(key) for key in event.violated_boundary_keys}
    findings: list[DecisionFinding] = []
    for boundary in policy.moral_boundaries:
        if _normalize(boundary.key) in violated:
            findings.append(
                _finding(
                    "MORAL_BOUNDARY_VIOLATED",
                    f"{event.character_name} crosses moral boundary {boundary.key}: "
                    f"{boundary.description}",
                    blocking=boundary.absolute,
                ),
            )
    return findings


def _preferred_tactic_finding(
    policy: DecisionPolicy,
    event: DecisionEvent,
) -> DecisionFinding | None:
    if event.risk_level != "high" or not policy.preferred_tactics:
        return None
    event_tactics = {_normalize(tag) for tag in event.tactic_tags}
    preferred = {_normalize(tactic.key) for tactic in policy.preferred_tactics}
    if event_tactics & preferred:
        return None
    return _finding(
        "PREFERRED_TACTIC_MISSING",
        f"{event.character_name} takes a high-risk action without using a preferred tactic.",
        severity="warning",
        blocking=False,
    )


def validate_decision(policy: DecisionPolicy, event: DecisionEvent) -> DecisionAudit:
    """Validate a major protagonist decision against the character contract."""
    findings: list[DecisionFinding] = []
    if _normalize(policy.character_name) != _normalize(event.character_name):
        findings.append(
            _finding(
                "CHARACTER_MISMATCH",
                f"Decision event belongs to {event.character_name}, not {policy.character_name}.",
            ),
        )
        return _audit(findings)

    findings.extend(_forbidden_behavior_findings(policy, event))
    findings.extend(_boundary_findings(policy, event))

    allowed_causes = _allowed_high_risk_causes(policy, event)
    if (
        policy.risk_tolerance is RiskTolerance.LOW
        and event.risk_level == "high"
        and not allowed_causes
    ):
        findings.append(
            _finding(
                "HIGH_RISK_WITHOUT_CAUSE",
                f"{event.character_name} has low risk tolerance but takes high risk without "
                "life threat, rare upside, escape route, protection motive, or strategic need.",
            ),
        )

    if event.public_vanity and not allowed_causes:
        findings.append(
            _finding(
                "VANITY_RISK",
                f"{event.character_name} accepts public vanity pressure without sufficient cause.",
            ),
        )

    tactic_finding = _preferred_tactic_finding(policy, event)
    if tactic_finding is not None:
        findings.append(tactic_finding)

    return _audit(findings)


def build_decision_policy_block(
    policy: DecisionPolicy,
    *,
    language: str = "zh-CN",
) -> str:
    """Render the protagonist decision contract for writer prompts."""
    is_zh = language.lower().startswith("zh")
    if is_zh:
        lines = [
            "【主角决策策略】",
            f"角色: {policy.character_name}",
            f"原型: {policy.archetype}",
            f"风险承受: {policy.risk_tolerance.value}",
        ]
        if policy.pressure_responses:
            lines.append(
                "压力反应: "
                + ", ".join(response.value for response in policy.pressure_responses),
            )
        if policy.preferred_tactics:
            lines.append(
                "偏好策略: " + ", ".join(tactic.key for tactic in policy.preferred_tactics),
            )
        if policy.forbidden_behaviors:
            lines.append(
                "禁止行为: "
                + ", ".join(behavior.key for behavior in policy.forbidden_behaviors),
            )
        lines.append("硬规则: 重大冒险必须有生死威胁、稀缺收益、退路或战略必要性。")
        return "\n".join(lines)

    lines = [
        "[PROTAGONIST DECISION POLICY]",
        f"Character: {policy.character_name}",
        f"Archetype: {policy.archetype}",
        f"Risk tolerance: {policy.risk_tolerance.value}",
    ]
    if policy.preferred_tactics:
        lines.append("Preferred tactics: " + ", ".join(t.key for t in policy.preferred_tactics))
    if policy.forbidden_behaviors:
        lines.append("Forbidden behaviors: " + ", ".join(b.key for b in policy.forbidden_behaviors))
    lines.append("Hard rule: major risks require threat, rare upside, escape route, or strategy.")
    return "\n".join(lines)
