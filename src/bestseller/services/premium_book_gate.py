# ruff: noqa: RUF001
"""Project-level premium novel readiness gate.

This gate asks whether a project has the structured genre engine needed to
keep producing ranking-grade serial chapters. It is intentionally metadata-
first: chapter validators can catch local prose defects, but they cannot tell
whether a cultivation, rule-mystery, faction, or relationship engine exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bestseller.services.category_hard_engines import (
    evaluate_category_hard_engine,
    resolve_category_hard_engine_key,
)

_BLOCKING_SEVERITIES = {"critical", "high"}
_MIN_PASS_SCORE = 70

_PROGRESSION_MARKERS = (
    "xianxia",
    "cultivation",
    "progression",
    "wuxia",
    "仙侠",
    "修仙",
    "玄幻",
    "凡人",
    "升级",
)
_RULE_SYSTEM_MARKERS = (
    "rule",
    "mystery",
    "occult",
    "case",
    "detective",
    "horror",
    "规则",
    "诡",
    "悬疑",
    "民俗",
    "怪谈",
    "探案",
    "刑侦",
)
_FACTION_MARKERS = (
    "xianxia",
    "cultivation",
    "faction",
    "sect",
    "clan",
    "court",
    "kingdom",
    "base",
    "military",
    "仙侠",
    "修仙",
    "宗门",
    "家族",
    "朝堂",
    "权谋",
    "领主",
    "经营",
)
_RELATIONSHIP_MARKERS = (
    "romance",
    "romantasy",
    "reverse harem",
    "no-cp",
    "female",
    "言情",
    "女频",
    "女性",
    "无cp",
    "无CP",
    "后宫",
    "情感",
)

_REPAIR_ACTIONS = {
    "premium_state_ledger_blocking": (
        "先修复 premium_state_ledger_report 中的阻断项，再允许下一批章节继续生产。"
    ),
    "premium_state_snapshot_blocking": (
        "重建 premium_state_snapshot；无权威状态快照时不要继续消耗长篇连载状态。"
    ),
    "progression_engine_missing": (
        "补齐 progression/power_system/realm/resource/artifact/technique 等进阶因果数据。"
    ),
    "rule_system_missing": (
        "补齐规则系统：每条规则必须有可见效果、破局路径、代价或反噬。"
    ),
    "faction_ecology_missing": (
        "补齐派系生态：派系目标、资源利益、对主角的差异化反应和下一步压力。"
    ),
    "relationship_agency_missing": (
        "补齐关系代理：关系轴变化、主动选择、承诺/债务、边界和下一次兑现窗口。"
    ),
    "decision_policy_missing": (
        "补齐主角决策策略，明确会做什么、不会做什么、如何权衡风险和收益。"
    ),
    "decision_policy_stale": (
        "处理待决的主角决策策略警告，避免主角选择漂移成随机推进剧情。"
    ),
    "state_loop_missing": (
        "让章节后更新生成 premium_state_ledger 并折叠为 premium_state_snapshot。"
    ),
    "long_arc_payoff_overdue": "处理逾期伏笔/线索，补偿兑现、升级或显式延期。",
    "setup_payoff_debt": "处理 setup/payoff 债务，避免长线承诺只种不收。",
    "repetitive_loop_risk": "调整重复钩子、重复话术或重复章节结构，打散循环感。",
    "scorecard_below_premium_bar": "先修复总分卡低分项，再进入精品书准入。",
    "category_state_ledger_missing": "补齐该类别的权威状态账本，再允许长篇继续扩展。",
    "category_hard_gate_missing": "补齐该类别的写前/章后硬门禁，并用好/坏 fixture 验证。",
    "category_chapter_update_missing": "补齐章节后状态更新通道，让每章变化可折叠进权威快照。",
}


@dataclass(frozen=True, slots=True)
class PremiumBookGateFinding:
    code: str
    severity: str
    message: str
    path: str
    repair_action: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "repair_action": self.repair_action,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class PremiumBookGateReport:
    project_slug: str | None
    genre: str | None
    sub_genre: str | None
    passed: bool
    score: int
    blocking_findings: tuple[PremiumBookGateFinding, ...]
    warnings: tuple[PremiumBookGateFinding, ...]
    recommended_repair_actions: tuple[str, ...]
    capability_snapshot: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return premium_book_gate_report_to_dict(self)


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _non_empty(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping | Sequence) and not isinstance(value, str | bytes):
        return bool(value)
    return True


def _nested_mappings(metadata: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    book_spec = _as_mapping(metadata.get("book_spec"))
    world_spec = _as_mapping(metadata.get("world_spec"))
    cast_spec = _as_mapping(metadata.get("cast_spec"))
    story_bible_context = _as_mapping(metadata.get("story_bible_context"))
    return (metadata, book_spec, world_spec, cast_spec, story_bible_context)


def _has_key_payload(metadata: Mapping[str, object], keys: Sequence[str]) -> bool:
    for container in _nested_mappings(metadata):
        for key in keys:
            if _non_empty(container.get(key)):
                return True
    return False


def _haystack(
    metadata: Mapping[str, object],
    *,
    genre: str | None,
    sub_genre: str | None,
) -> str:
    book_spec = _as_mapping(metadata.get("book_spec"))
    parts = [
        genre,
        sub_genre,
        metadata.get("genre"),
        metadata.get("sub_genre"),
        metadata.get("category"),
        metadata.get("prompt_pack_key"),
        book_spec.get("genre"),
        book_spec.get("sub_genre"),
    ]
    return " ".join(_text(part).lower() for part in parts if _text(part))


def _matches(haystack: str, markers: Sequence[str]) -> bool:
    return any(marker.lower() in haystack for marker in markers)


def _state_snapshot(metadata: Mapping[str, object]) -> dict[str, object]:
    return _as_mapping(metadata.get("premium_state_snapshot"))


def _ledger_report(metadata: Mapping[str, object]) -> dict[str, object]:
    return _as_mapping(metadata.get("premium_state_ledger_report"))


def _snapshot_has(snapshot: Mapping[str, object], key: str) -> bool:
    return _non_empty(snapshot.get(key))


def _has_progression_engine(metadata: Mapping[str, object]) -> bool:
    snapshot = _state_snapshot(metadata)
    return (
        _snapshot_has(snapshot, "resource_balances")
        or _has_key_payload(
            metadata,
            (
                "power_system",
                "progression_system",
                "progression_context",
                "realm_ladder",
                "realms",
                "resources",
                "techniques",
                "artifacts",
            ),
        )
    )


def _has_rule_system(metadata: Mapping[str, object]) -> bool:
    snapshot = _state_snapshot(metadata)
    return (
        _snapshot_has(snapshot, "rule_state")
        or _has_key_payload(
            metadata,
            (
                "rule_system",
                "rule_lattice",
                "rule_ledger",
                "world_rules",
                "rules",
                "occult_rules",
            ),
        )
    )


def _has_faction_ecology(metadata: Mapping[str, object]) -> bool:
    snapshot = _state_snapshot(metadata)
    return (
        _snapshot_has(snapshot, "faction_pressure_queue")
        or _has_key_payload(
            metadata,
            (
                "faction_ecology",
                "faction_pressure",
                "active_factions",
                "factions",
                "sects",
                "clans",
                "organizations",
                "institutions",
                "forces",
            ),
        )
    )


def _cast_has_relationships(metadata: Mapping[str, object]) -> bool:
    cast_spec = _as_mapping(metadata.get("cast_spec"))
    protagonist = _as_mapping(cast_spec.get("protagonist"))
    if _non_empty(protagonist.get("relationships")):
        return True
    for key in ("supporting_cast", "characters", "main_characters"):
        for raw in _as_sequence(cast_spec.get(key)):
            item = _as_mapping(raw)
            if _non_empty(item.get("relationships")) or _non_empty(
                item.get("relationship_to_protagonist")
            ):
                return True
    return False


def _has_relationship_agency(metadata: Mapping[str, object]) -> bool:
    snapshot = _state_snapshot(metadata)
    return (
        _snapshot_has(snapshot, "relationship_state")
        or _snapshot_has(snapshot, "open_agency_debts")
        or _has_key_payload(
            metadata,
            (
                "relationship_contracts",
                "relationship_arcs",
                "relationships",
                "relationship_state",
                "interpersonal_promises",
                "agency_debts",
            ),
        )
        or _cast_has_relationships(metadata)
    )


def _has_decision_policy(metadata: Mapping[str, object]) -> bool:
    if _has_key_payload(metadata, ("decision_policy", "protagonist_decision_policy")):
        return True
    cast_spec = _as_mapping(metadata.get("cast_spec"))
    protagonist = _as_mapping(cast_spec.get("protagonist"))
    return (
        _non_empty(protagonist.get("decision_policy"))
        or _non_empty(protagonist.get("protagonist_decision_policy"))
        or _non_empty(_as_mapping(protagonist.get("metadata")).get("decision_policy"))
    )


def _project_metadata(project_or_metadata: object) -> dict[str, object]:
    if isinstance(project_or_metadata, Mapping):
        direct = _as_mapping(project_or_metadata)
        metadata = _as_mapping(direct.get("metadata_json"))
        return metadata or direct
    return _as_mapping(getattr(project_or_metadata, "metadata_json", None))


def _project_slug(project_or_metadata: object, metadata: Mapping[str, object]) -> str | None:
    slug = getattr(project_or_metadata, "slug", None)
    if slug:
        return str(slug)
    value = metadata.get("slug") or metadata.get("project_slug")
    return _text(value) or None


def _resolve_genre(
    project_or_metadata: object,
    metadata: Mapping[str, object],
    explicit: str | None,
) -> str | None:
    value = explicit or getattr(project_or_metadata, "genre", None) or metadata.get("genre")
    if not value:
        value = _as_mapping(metadata.get("book_spec")).get("genre")
    return _text(value) or None


def _resolve_sub_genre(
    project_or_metadata: object,
    metadata: Mapping[str, object],
    explicit: str | None,
) -> str | None:
    value = (
        explicit
        or getattr(project_or_metadata, "sub_genre", None)
        or metadata.get("sub_genre")
        or _as_mapping(metadata.get("book_spec")).get("sub_genre")
    )
    return _text(value) or None


def _finding(
    code: str,
    message: str,
    path: str,
    *,
    severity: str = "critical",
    evidence: Mapping[str, Any] | None = None,
) -> PremiumBookGateFinding:
    return PremiumBookGateFinding(
        code=code,
        severity=severity,
        message=message,
        path=path,
        repair_action=_REPAIR_ACTIONS.get(code, "补齐对应结构化证据后重新运行精品书门禁。"),
        evidence=dict(evidence or {}),
    )


def _ledger_findings(metadata: Mapping[str, object]) -> list[PremiumBookGateFinding]:
    findings: list[PremiumBookGateFinding] = []
    report = _ledger_report(metadata)
    raw_findings = _as_sequence(report.get("findings"))
    if report and report.get("passed") is False:
        codes = [
            _text(_as_mapping(item).get("code"))
            for item in raw_findings
            if _text(_as_mapping(item).get("code"))
        ]
        findings.append(
            _finding(
                "premium_state_ledger_blocking",
                "premium_state_ledger_report contains blocking state findings: "
                + ", ".join(codes[:8]),
                "premium_state_ledger_report",
                evidence={"finding_codes": codes},
            )
        )

    snapshot = _state_snapshot(metadata)
    if snapshot and snapshot.get("passed") is False:
        codes = [
            _text(_as_mapping(item).get("code"))
            for item in _as_sequence(snapshot.get("blocking_findings"))
            if _text(_as_mapping(item).get("code"))
        ]
        findings.append(
            _finding(
                "premium_state_snapshot_blocking",
                "premium_state_snapshot is marked invalid and must not drive future chapters.",
                "premium_state_snapshot",
                evidence={"finding_codes": codes},
            )
        )

    has_ledger = _non_empty(metadata.get("premium_state_ledger"))
    if has_ledger and not snapshot and report.get("passed") is True:
        findings.append(
            _finding(
                "state_loop_missing",
                "premium_state_ledger exists but no folded premium_state_snapshot is available.",
                "premium_state_snapshot",
                severity="warning",
            )
        )
    return findings


def _project_health_findings(
    project_health: Mapping[str, object] | None,
) -> list[PremiumBookGateFinding]:
    if not project_health:
        return []
    findings: list[PremiumBookGateFinding] = []
    health = _as_mapping(project_health)
    overdue_clues = _as_sequence(health.get("overdue_clues"))
    if overdue_clues:
        findings.append(
            _finding(
                "long_arc_payoff_overdue",
                f"{len(overdue_clues)} overdue clue/payoff item(s) remain unresolved.",
                "project_health.overdue_clues",
                severity="high",
                evidence={"count": len(overdue_clues)},
            )
        )
    payoff_debts = _as_sequence(health.get("setup_payoff_debts"))
    if payoff_debts:
        findings.append(
            _finding(
                "setup_payoff_debt",
                f"{len(payoff_debts)} setup/payoff debt(s) remain open.",
                "project_health.setup_payoff_debts",
                severity="warning",
                evidence={"count": len(payoff_debts)},
            )
        )
    overused_hooks = _as_sequence(health.get("overused_hooks"))
    if overused_hooks:
        findings.append(
            _finding(
                "repetitive_loop_risk",
                f"{len(overused_hooks)} overused hook pattern(s) suggest repetitive loops.",
                "project_health.overused_hooks",
                severity="warning",
                evidence={"count": len(overused_hooks)},
            )
        )
    return findings


def _scorecard_findings(scorecard_quality_score: float | None) -> list[PremiumBookGateFinding]:
    if scorecard_quality_score is None:
        return []
    if scorecard_quality_score < 65:
        return [
            _finding(
                "scorecard_below_premium_bar",
                f"Scorecard quality score {scorecard_quality_score:.1f} is below premium bar.",
                "scorecard.quality_score",
                severity="high",
                evidence={"quality_score": scorecard_quality_score},
            )
        ]
    if scorecard_quality_score < 75:
        return [
            _finding(
                "scorecard_below_premium_bar",
                f"Scorecard quality score {scorecard_quality_score:.1f} is below target range.",
                "scorecard.quality_score",
                severity="warning",
                evidence={"quality_score": scorecard_quality_score},
            )
        ]
    return []


def _metadata_warning_findings(
    metadata: Mapping[str, object],
) -> list[PremiumBookGateFinding]:
    findings: list[PremiumBookGateFinding] = []
    warnings = [
        _text(item)
        for item in _as_sequence(metadata.get("_pending_consistency_warnings"))
        if _text(item)
    ]
    decision_warnings = [
        item
        for item in warnings
        if "decision" in item.lower() or "决策" in item or "policy" in item.lower()
    ]
    if decision_warnings:
        findings.append(
            _finding(
                "decision_policy_stale",
                "Pending consistency warnings mention protagonist decision-policy drift.",
                "_pending_consistency_warnings",
                severity="high",
                evidence={"warnings": decision_warnings[:5]},
            )
        )
    if _non_empty(metadata.get("_overused_phrase_block")):
        findings.append(
            _finding(
                "repetitive_loop_risk",
                "Project has an active overused-phrase avoidance block.",
                "_overused_phrase_block",
                severity="warning",
            )
        )
    return findings


def _capability_snapshot(
    metadata: Mapping[str, object],
    *,
    genre: str | None,
    sub_genre: str | None,
) -> dict[str, Any]:
    haystack = _haystack(metadata, genre=genre, sub_genre=sub_genre)
    progression_required = _matches(haystack, _PROGRESSION_MARKERS)
    rule_required = _matches(haystack, _RULE_SYSTEM_MARKERS)
    faction_required = _matches(haystack, _FACTION_MARKERS)
    relationship_required = _matches(haystack, _RELATIONSHIP_MARKERS)
    snapshot = _state_snapshot(metadata)
    category_key = resolve_category_hard_engine_key(
        metadata,
        genre=genre,
        sub_genre=sub_genre,
    )
    category_engine_report = (
        evaluate_category_hard_engine(metadata, category_key=category_key)
        if category_key
        else None
    )
    return {
        "genre_haystack": haystack,
        "category_hard_engine_key": category_key,
        "category_hard_engine": (
            category_engine_report.to_dict() if category_engine_report else None
        ),
        "required": {
            "progression_engine": progression_required,
            "rule_system": rule_required,
            "faction_ecology": faction_required,
            "relationship_agency": relationship_required,
            "category_hard_engine": category_key is not None,
        },
        "progression_engine": _has_progression_engine(metadata),
        "rule_system": _has_rule_system(metadata),
        "faction_ecology": _has_faction_ecology(metadata),
        "relationship_agency": _has_relationship_agency(metadata),
        "decision_policy": _has_decision_policy(metadata),
        "premium_state_ledger": _non_empty(metadata.get("premium_state_ledger")),
        "premium_state_ledger_report": _non_empty(metadata.get("premium_state_ledger_report")),
        "premium_state_snapshot": bool(snapshot),
        "premium_state_snapshot_passed": snapshot.get("passed") is not False,
    }


def _capability_findings(
    capability: Mapping[str, Any],
) -> list[PremiumBookGateFinding]:
    findings: list[PremiumBookGateFinding] = []
    required = _as_mapping(capability.get("required"))
    if required.get("progression_engine") and not capability.get("progression_engine"):
        findings.append(
            _finding(
                "progression_engine_missing",
                "Progression-heavy genre lacks structured progression state.",
                "metadata.progression",
            )
        )
    if required.get("rule_system") and not capability.get("rule_system"):
        findings.append(
            _finding(
                "rule_system_missing",
                "Rule-heavy genre lacks a structured rule system.",
                "metadata.rules",
            )
        )
    if required.get("faction_ecology") and not capability.get("faction_ecology"):
        findings.append(
            _finding(
                "faction_ecology_missing",
                "Faction-heavy genre lacks active faction ecology.",
                "metadata.factions",
            )
        )
    if required.get("relationship_agency") and not capability.get("relationship_agency"):
        findings.append(
            _finding(
                "relationship_agency_missing",
                "Relationship-heavy genre lacks relationship agency state.",
                "metadata.relationships",
            )
        )
    category_engine = _as_mapping(capability.get("category_hard_engine"))
    if required.get("category_hard_engine") and category_engine.get("passed") is False:
        for raw in _as_sequence(category_engine.get("findings")):
            finding = _as_mapping(raw)
            code = _text(finding.get("code"))
            if code not in {
                "category_state_ledger_missing",
                "category_hard_gate_missing",
                "category_chapter_update_missing",
            }:
                continue
            missing_keys = [
                _text(item)
                for item in _as_sequence(finding.get("missing_keys"))
                if _text(item)
            ]
            findings.append(
                _finding(
                    code,
                    _text(finding.get("message"))
                    or "Category hard-engine contract is incomplete.",
                    _text(finding.get("path")) or "category_hard_engine",
                    severity=_text(finding.get("severity")) or "high",
                    evidence={
                        "category_key": category_engine.get("category_key"),
                        "missing_keys": missing_keys,
                    },
                )
            )
    if not capability.get("decision_policy"):
        findings.append(
            _finding(
                "decision_policy_missing",
                "No explicit protagonist decision policy is available.",
                "metadata.decision_policy",
                severity="warning",
            )
        )
    if (
        not capability.get("premium_state_ledger")
        and not capability.get("premium_state_snapshot")
    ):
        findings.append(
            _finding(
                "state_loop_missing",
                "No premium state loop exists yet; later chapters cannot consume canonical state.",
                "metadata.premium_state_ledger",
                severity="warning",
            )
        )
    return findings


def _score(findings: Sequence[PremiumBookGateFinding]) -> int:
    score = 100
    for finding in findings:
        if finding.severity == "critical":
            score -= 25
        elif finding.severity == "high":
            score -= 18
        elif finding.severity == "warning":
            score -= 8
        else:
            score -= 4
    return max(0, min(100, score))


def _unique_repair_actions(
    findings: Sequence[PremiumBookGateFinding],
) -> tuple[str, ...]:
    actions: list[str] = []
    seen: set[str] = set()
    for finding in findings:
        if finding.repair_action not in seen:
            actions.append(finding.repair_action)
            seen.add(finding.repair_action)
    return tuple(actions)


def evaluate_premium_project_readiness(
    project_or_metadata: object,
    *,
    genre: str | None = None,
    sub_genre: str | None = None,
    project_health: Mapping[str, object] | None = None,
    scorecard_quality_score: float | None = None,
    min_pass_score: int = _MIN_PASS_SCORE,
) -> PremiumBookGateReport:
    """Evaluate whether a project has a complete premium genre engine."""

    metadata = _project_metadata(project_or_metadata)
    resolved_genre = _resolve_genre(project_or_metadata, metadata, genre)
    resolved_sub_genre = _resolve_sub_genre(project_or_metadata, metadata, sub_genre)
    capability = _capability_snapshot(
        metadata,
        genre=resolved_genre,
        sub_genre=resolved_sub_genre,
    )

    findings: list[PremiumBookGateFinding] = []
    findings.extend(_ledger_findings(metadata))
    findings.extend(_capability_findings(capability))
    findings.extend(_metadata_warning_findings(metadata))
    findings.extend(_project_health_findings(project_health))
    findings.extend(_scorecard_findings(scorecard_quality_score))

    score = _score(findings)
    blocking = tuple(
        finding for finding in findings if finding.severity in _BLOCKING_SEVERITIES
    )
    warnings = tuple(
        finding for finding in findings if finding.severity not in _BLOCKING_SEVERITIES
    )
    passed = not blocking and score >= min_pass_score

    return PremiumBookGateReport(
        project_slug=_project_slug(project_or_metadata, metadata),
        genre=resolved_genre,
        sub_genre=resolved_sub_genre,
        passed=passed,
        score=score,
        blocking_findings=blocking,
        warnings=warnings,
        recommended_repair_actions=_unique_repair_actions(findings),
        capability_snapshot=capability,
    )


def premium_book_gate_report_to_dict(report: PremiumBookGateReport) -> dict[str, Any]:
    return {
        "project_slug": report.project_slug,
        "genre": report.genre,
        "sub_genre": report.sub_genre,
        "passed": report.passed,
        "score": report.score,
        "blocking_findings": [
            finding.to_dict() for finding in report.blocking_findings
        ],
        "warnings": [finding.to_dict() for finding in report.warnings],
        "recommended_repair_actions": list(report.recommended_repair_actions),
        "capability_snapshot": dict(report.capability_snapshot),
    }


__all__ = [
    "PremiumBookGateFinding",
    "PremiumBookGateReport",
    "evaluate_premium_project_readiness",
    "premium_book_gate_report_to_dict",
]
