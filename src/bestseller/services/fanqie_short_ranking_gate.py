"""番茄短故事榜单级门禁。

The checks here are deterministic guardrails used by planning, export, and
pipeline review. They intentionally catch framework-level failures early:
weak opening focus, missing first payoff before the unlock line, ability without
cost, serial-style endings, and protagonist-name drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from bestseller.domain.fanqie_short import DEFAULT_UNLOCK_LINE_RATIO
from bestseller.services.fanqie_short_opening_gate import evaluate_fanqie_short_opening_gate


@dataclass(frozen=True)
class FanqieRankingFinding:
    code: str
    severity: str
    message: str
    evidence: str
    phase: str
    target: str = "whole_story"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
            "phase": self.phase,
            "target": self.target,
        }


@dataclass(frozen=True)
class FanqieRankingGateReport:
    passed: bool
    phase: str
    findings: tuple[FanqieRankingFinding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "phase": self.phase,
            "findings": [finding.to_dict() for finding in self.findings],
        }


_PRESSURE_TERMS = (
    "逼",
    "押",
    "拦",
    "夺",
    "抢",
    "追",
    "逃",
    "杀",
    "痛",
    "威胁",
    "否则",
    "必须",
    "签字",
    "证据",
    "开除",
    "诬陷",
    "挪用",
    "挪",
    "贪污",
    "背锅",
    "公告",
    "冻结",
    "封号",
    "锁屏",
    "警察",
    "封杀",
    "报警",
    "跪",
)

_ACTION_TERMS = (
    "抓",
    "按",
    "推",
    "摔",
    "砸",
    "冲",
    "躲",
    "扯",
    "扣",
    "夺",
    "撕",
    "盯",
    "拦",
    "挡",
    "反击",
    "反手",
)

_PAYOFF_TERMS = (
    "反击",
    "反制",
    "翻盘",
    "打脸",
    "赢",
    "救下",
    "证据",
    "筹码",
    "突破",
    "到账",
    "解锁",
    "发现",
    "真相",
    "曝光",
    "公开",
    "撤回",
    "自爆",
    "露馅",
    "认账",
    "道歉",
    "保住",
)

_ABILITY_TERMS = (
    "能力",
    "异能",
    "系统",
    "面板",
    "数值",
    "能量",
    "解锁",
    "天赋",
    "契约",
    "技能",
    "金手指",
    "黑屏",
    "点选",
)

_COST_TERMS = (
    "代价",
    "反噬",
    "疼",
    "痛",
    "失控",
    "昏",
    "流血",
    "暴露",
    "损失",
    "扣除",
    "透支",
    "限制",
    "冷却",
    "惩罚",
)

_CLOSURE_TERMS = (
    "结束",
    "收场",
    "落定",
    "真相大白",
    "付出代价",
    "判决",
    "认罪",
    "离开",
    "放下",
    "重新开始",
)

_CLIFFHANGER_TERMS = (
    "未完待续",
    "下章",
    "下一章",
    "且听",
    "五个小时后",
    "三天后",
    "地下三层",
    "还有一半",
    "真正的真相",
    "欠我一个真相",
    "你到底是谁",
)


def _contains_cjk(text: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff" for char in text)


def _cjk_slice(text: str, limit: int) -> str:
    if not _contains_cjk(text):
        return text[: limit * 6]
    chars: list[str] = []
    count = 0
    for char in text:
        chars.append(char)
        if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff":
            count += 1
        if count >= limit:
            break
    return "".join(chars)


def _cjk_tail(text: str, limit: int) -> str:
    if not _contains_cjk(text):
        return text[-limit * 6 :]
    chars: list[str] = []
    count = 0
    for char in reversed(text):
        chars.append(char)
        if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff":
            count += 1
        if count >= limit:
            break
    return "".join(reversed(chars))


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _merge_reports(phase: str, reports: Iterable[FanqieRankingGateReport]) -> FanqieRankingGateReport:
    findings = tuple(finding for report in reports for finding in report.findings)
    return FanqieRankingGateReport(
        passed=not any(finding.severity == "critical" for finding in findings),
        phase=phase,
        findings=findings,
    )


def evaluate_fanqie_opening_ranking_gate(
    full_text: str,
    *,
    unlock_line_ratio: float = DEFAULT_UNLOCK_LINE_RATIO,
    protagonist_name: str | None = None,
) -> FanqieRankingGateReport:
    text = (full_text or "").strip()
    findings: list[FanqieRankingFinding] = []
    opening = evaluate_fanqie_short_opening_gate(
        text,
        unlock_line_ratio=unlock_line_ratio,
        protagonist_name=protagonist_name,
    )
    for item in opening.findings:
        findings.append(
            FanqieRankingFinding(
                code=item.code,
                severity=item.severity,
                message=item.message,
                evidence=item.evidence,
                phase="opening",
                target="opening_30_percent",
            )
        )

    first_300 = _cjk_slice(text, 300)
    first_220 = _cjk_slice(text, 220)
    first_800 = _cjk_slice(text, 800)
    if not _contains_any(first_300, _PRESSURE_TERMS):
        findings.append(
            FanqieRankingFinding(
                code="opening_pressure_missing",
                severity="critical",
                message="前300字缺少明确压迫、威胁、诬陷或当前冲突。",
                evidence=first_300,
                phase="opening",
                target="opening_300",
            )
        )
    if not _contains_any(first_800, _ACTION_TERMS):
        findings.append(
            FanqieRankingFinding(
                code="opening_action_missing",
                severity="critical",
                message="前800字缺少主角可见动作或反击动作。",
                evidence=first_800,
                phase="opening",
                target="opening_800",
            )
        )
    if not _contains_any(first_300, _PAYOFF_TERMS):
        findings.append(
            FanqieRankingFinding(
                code="opening_fast_payoff_missing",
                severity="critical",
                message="前300字缺少第一次可见小爽点或反击结果，短故事开篇反馈不够快。",
                evidence=first_300,
                phase="opening",
                target="opening_300",
            )
        )
    if _contains_any(text, _ABILITY_TERMS) and not _contains_any(first_220, _ABILITY_TERMS):
        findings.append(
            FanqieRankingFinding(
                code="opening_ability_late",
                severity="critical",
                message="全文存在金手指/能力设定，但前约200字没有让能力可见并参与当前冲突。",
                evidence=first_220,
                phase="opening",
                target="opening_200",
            )
        )
    return FanqieRankingGateReport(
        passed=not any(finding.severity == "critical" for finding in findings),
        phase="opening",
        findings=tuple(findings),
    )


def evaluate_fanqie_unlock_ranking_gate(
    full_text: str,
    *,
    unlock_line_ratio: float = DEFAULT_UNLOCK_LINE_RATIO,
) -> FanqieRankingGateReport:
    text = (full_text or "").strip()
    unlock_chars = max(300, int(len(text) * unlock_line_ratio))
    unlock_slice = text[:unlock_chars]
    findings: list[FanqieRankingFinding] = []
    if not _contains_any(unlock_slice, _PAYOFF_TERMS):
        findings.append(
            FanqieRankingFinding(
                code="unlock_payoff_missing",
                severity="critical",
                message="前30%缺少可识别的小爽点、收益、证据、反制或爆点。",
                evidence=_cjk_slice(unlock_slice, 500),
                phase="unlock",
                target="unlock_30_percent",
            )
        )
    if not (_contains_any(unlock_slice, _PRESSURE_TERMS) and _contains_any(unlock_slice, _ACTION_TERMS)):
        findings.append(
            FanqieRankingFinding(
                code="unlock_conflict_loop_missing",
                severity="critical",
                message="前30%没有形成压迫-行动-回报的基础循环。",
                evidence=_cjk_slice(unlock_slice, 500),
                phase="unlock",
                target="unlock_30_percent",
            )
        )
    return FanqieRankingGateReport(
        passed=not any(finding.severity == "critical" for finding in findings),
        phase="unlock",
        findings=tuple(findings),
    )


def evaluate_fanqie_ability_cost_gate(full_text: str) -> FanqieRankingGateReport:
    text = (full_text or "").strip()
    findings: list[FanqieRankingFinding] = []
    if _contains_any(text, _ABILITY_TERMS) and not _contains_any(text, _COST_TERMS):
        findings.append(
            FanqieRankingFinding(
                code="ability_cost_missing",
                severity="critical",
                message="能力/系统出现后缺少可见代价、限制、反噬或暴露风险。",
                evidence=_cjk_slice(text, 700),
                phase="ability_cost",
                target="whole_story",
            )
        )
    return FanqieRankingGateReport(
        passed=not any(finding.severity == "critical" for finding in findings),
        phase="ability_cost",
        findings=tuple(findings),
    )


def evaluate_fanqie_closure_gate(full_text: str) -> FanqieRankingGateReport:
    text = (full_text or "").strip()
    tail = _cjk_tail(text, 650)
    findings: list[FanqieRankingFinding] = []
    if _contains_any(tail, _CLIFFHANGER_TERMS):
        findings.append(
            FanqieRankingFinding(
                code="serial_cliffhanger_ending",
                severity="critical",
                message="结尾出现连载式悬念或下一段入口，不符合单篇完结。",
                evidence=tail,
                phase="closure",
                target="ending",
            )
        )
    if not _contains_any(tail, _CLOSURE_TERMS):
        findings.append(
            FanqieRankingFinding(
                code="closure_signal_missing",
                severity="warning",
                message="结尾缺少明确收束信号，可能不像单篇完结。",
                evidence=tail,
                phase="closure",
                target="ending",
            )
        )
    return FanqieRankingGateReport(
        passed=not any(finding.severity == "critical" for finding in findings),
        phase="closure",
        findings=tuple(findings),
    )


def evaluate_fanqie_name_continuity_gate(
    full_text: str,
    *,
    protagonist_name: str | None = None,
) -> FanqieRankingGateReport:
    text = (full_text or "").strip()
    findings: list[FanqieRankingFinding] = []
    expected = (protagonist_name or "").strip()
    if expected and expected != "我" and expected not in _cjk_slice(text, 1000):
        findings.append(
            FanqieRankingFinding(
                code="protagonist_name_missing",
                severity="critical",
                message=f"正文前1000字没有出现规划主角名：{expected}。",
                evidence=_cjk_slice(text, 500),
                phase="continuity",
                target="opening_1000",
            )
        )
    return FanqieRankingGateReport(
        passed=not any(finding.severity == "critical" for finding in findings),
        phase="continuity",
        findings=tuple(findings),
    )


def evaluate_fanqie_ranking_readiness(
    full_text: str,
    *,
    unlock_line_ratio: float = DEFAULT_UNLOCK_LINE_RATIO,
    protagonist_name: str | None = None,
) -> FanqieRankingGateReport:
    return _merge_reports(
        "ranking_readiness",
        (
            evaluate_fanqie_opening_ranking_gate(
                full_text,
                unlock_line_ratio=unlock_line_ratio,
                protagonist_name=protagonist_name,
            ),
            evaluate_fanqie_unlock_ranking_gate(
                full_text,
                unlock_line_ratio=unlock_line_ratio,
            ),
            evaluate_fanqie_ability_cost_gate(full_text),
            evaluate_fanqie_closure_gate(full_text),
            evaluate_fanqie_name_continuity_gate(
                full_text,
                protagonist_name=protagonist_name,
            ),
        ),
    )


def build_fanqie_ranking_rewrite_instructions(report: FanqieRankingGateReport) -> str:
    criticals = [finding for finding in report.findings if finding.severity == "critical"]
    findings = criticals or list(report.findings)
    lines = ["番茄短故事榜单级门禁未过，请按以下清单局部重写，不要改动无关设定："]
    for index, finding in enumerate(findings, start=1):
        lines.append(
            f"{index}. [{finding.code}] {finding.message} "
            f"目标位置：{finding.target}。整改时保留已成立的主线信息，但必须补足该项。"
        )
    return "\n".join(lines)
