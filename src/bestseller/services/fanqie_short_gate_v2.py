# ruff: noqa: RUF001
"""Fanqie short-story v2 ranking-readiness gates."""

from __future__ import annotations

from collections.abc import Iterable

from bestseller.domain.fanqie_short import DEFAULT_UNLOCK_LINE_RATIO
from bestseller.domain.fanqie_short_v2 import (
    FanqieShortEmotionStack,
    FanqieShortRewriteRoute,
)
from bestseller.services.fanqie_short_ranking_gate import (
    FanqieRankingFinding,
    FanqieRankingGateReport,
    evaluate_fanqie_core_ranking_readiness,
)

_TITLE_PRESSURE_TERMS = (
    "被",
    "逼",
    "背锅",
    "栽赃",
    "羞辱",
    "挂",
    "开除",
    "背叛",
    "悔婚",
    "网暴",
    "抢走",
)

_TITLE_PAYOFF_TERMS = (
    "我让",
    "反击",
    "翻车",
    "自爆",
    "认罪",
    "打脸",
    "公开",
    "赢",
    "反杀",
    "曝光",
)

_PRESSURE_TERMS = (
    "逼",
    "威胁",
    "羞辱",
    "背锅",
    "栽赃",
    "开除",
    "悔婚",
    "网暴",
    "报警",
    "倒计时",
    "赔偿",
    "抢走",
    "封杀",
)

_PAYOFF_TERMS = (
    "反击",
    "反制",
    "翻盘",
    "打脸",
    "证据",
    "曝光",
    "公开",
    "自爆",
    "露馅",
    "认罪",
    "道歉",
    "保住",
    "救下",
    "赢",
)

_SOCIAL_RESONANCE_TERMS = (
    "公司",
    "主管",
    "领导",
    "同事",
    "绩效",
    "项目",
    "父亲",
    "母亲",
    "家人",
    "亲戚",
    "婚礼",
    "前任",
    "闺蜜",
    "同学",
    "宿舍",
    "物业",
    "房东",
    "医院",
    "群",
    "热搜",
    "直播",
    "评论区",
    "师门",
    "宗门",
    "族人",
)

_EXTREME_EMOTION_TERMS = (
    "哭",
    "泪",
    "哽咽",
    "病房",
    "手术费",
    "最后一面",
    "旧物",
    "笑",
    "社死",
    "滑稽",
    "当场愣住",
    "全场安静",
)

_LONGFORM_CONTAMINATION_TERMS = (
    "第一章",
    "第1章",
    "卷一",
    "卷二",
    "全书",
    "本书主线",
    "主线谜团贯穿",
    "下章",
    "下一章",
    "未完待续",
    "且听下回",
    "多年以前",
    "世界观由此展开",
    "设定铺陈",
)


def evaluate_fanqie_short_v2_title_gate(title: str | None) -> FanqieRankingGateReport:
    text = (title or "").strip()
    findings: list[FanqieRankingFinding] = []
    if not text:
        return FanqieRankingGateReport(passed=True, phase="title_v2", findings=())

    cjk_count = _count_cjk(text)
    has_pressure = _contains_any(text, _TITLE_PRESSURE_TERMS)
    has_payoff = _contains_any(text, _TITLE_PAYOFF_TERMS)
    if cjk_count <= 6 and not (has_pressure or has_payoff):
        findings.append(
            _finding(
                "title_abstract_setting_only",
                "critical",
                "标题像抽象设定名，缺少开局冲突、身份压力或爽点结果。",
                text,
                "title",
                "title",
            )
        )
    elif not (has_pressure and has_payoff):
        findings.append(
            _finding(
                "title_click_contract_missing",
                "critical",
                "标题没有同时交代压迫入口和反击/翻车结果，信息流点击感不足。",
                text,
                "title",
                "title",
            )
        )
    return _report("title_v2", findings)


def evaluate_reader_retention_gate(
    full_text: str,
    *,
    protagonist_name: str | None = None,
) -> FanqieRankingGateReport:
    text = (full_text or "").strip()
    first_80 = _cjk_slice(text, 80)
    first_150 = _cjk_slice(text, 150)
    first_300 = _cjk_slice(text, 300)
    findings: list[FanqieRankingFinding] = []
    protagonist = (protagonist_name or "我").strip()

    if protagonist and protagonist not in first_150:
        findings.append(
            _finding(
                "first_screen_protagonist_missing",
                "critical",
                "前150字没有让主角成为读者视角焦点。",
                first_150,
                "opening",
                "opening_150",
            )
        )
    if not _contains_any(first_80, _PRESSURE_TERMS):
        findings.append(
            _finding(
                "first_screen_pressure_missing",
                "critical",
                "前80字缺少可感的羞辱、危机、威胁或损失。",
                first_80,
                "opening",
                "opening_80",
            )
        )
    if not _contains_any(first_300, _PAYOFF_TERMS):
        findings.append(
            _finding(
                "first_screen_feedback_missing",
                "critical",
                "前300字缺少第一次反击信号、证据、小打脸或可见反馈。",
                first_300,
                "opening",
                "opening_300",
            )
        )
    return _report("reader_retention_v2", findings)


def evaluate_social_resonance_gate(full_text: str) -> FanqieRankingGateReport:
    opening = _cjk_slice((full_text or "").strip(), 1200)
    if _contains_any(opening, _SOCIAL_RESONANCE_TERMS):
        return FanqieRankingGateReport(passed=True, phase="social_resonance_v2", findings=())
    return _report(
        "social_resonance_v2",
        [
            _finding(
                "social_resonance_missing",
                "critical",
                "前1200字缺少可识别的社会化情绪锚点，读者难以快速代入。",
                opening,
                "emotion",
                "opening_1200",
            )
        ],
    )


def evaluate_payoff_density_gate(full_text: str) -> FanqieRankingGateReport:
    text = (full_text or "").strip()
    cjk_count = max(_count_cjk(text), len(text) // 3)
    expected = 2 if cjk_count < 2000 else max(3, cjk_count // 2500)
    hits = _count_term_hits(text, _PAYOFF_TERMS)
    if hits >= expected:
        return FanqieRankingGateReport(passed=True, phase="payoff_density_v2", findings=())
    return _report(
        "payoff_density_v2",
        [
            _finding(
                "payoff_density_too_low",
                "critical",
                f"全文可识别回报点不足，当前 {hits} 个，期望至少 {expected} 个。",
                _cjk_slice(text, 900),
                "payoff",
                "whole_story",
            )
        ],
    )


def evaluate_comedy_or_tear_point_gate(full_text: str) -> FanqieRankingGateReport:
    text = (full_text or "").strip()
    if _contains_any(text, _EXTREME_EMOTION_TERMS):
        return FanqieRankingGateReport(passed=True, phase="emotion_peak_v2", findings=())
    return _report(
        "emotion_peak_v2",
        [
            _finding(
                "comedy_or_tear_point_light",
                "warning",
                "全文缺少明确喜剧点、催泪点或强共鸣峰值，可能只有平直爽点。",
                _cjk_slice(text, 900),
                "emotion",
                "whole_story",
            )
        ],
    )


def evaluate_anti_longform_contamination_gate(full_text: str) -> FanqieRankingGateReport:
    text = (full_text or "").strip()
    findings = [
        _finding(
            "longform_contamination",
            "critical",
            f"短篇正文出现长篇连载/设定铺陈信号：{term}。",
            _window_around(text, term),
            "anti_longform",
            "whole_story",
        )
        for term in _LONGFORM_CONTAMINATION_TERMS
        if term in text
    ]
    return _report("anti_longform_v2", findings)


def evaluate_emotion_stack_gate(
    full_text: str,
    *,
    emotion_stack: FanqieShortEmotionStack | None = None,
) -> FanqieRankingGateReport:
    if emotion_stack is None:
        return FanqieRankingGateReport(passed=True, phase="emotion_stack_v2", findings=())
    text = (full_text or "").strip()
    cards = emotion_stack.cards
    if any(_contains_any(text, [card.emotion, *card.tags]) for card in cards):
        return FanqieRankingGateReport(passed=True, phase="emotion_stack_v2", findings=())
    return _report(
        "emotion_stack_v2",
        [
            _finding(
                "emotion_stack_not_reflected",
                "warning",
                "规划情绪栈没有在正文形成清晰可识别的情绪锚点。",
                emotion_stack.to_prompt_block(),
                "emotion",
                "whole_story",
            )
        ],
    )


def evaluate_fanqie_short_v2_readiness(
    full_text: str,
    *,
    title: str | None = None,
    unlock_line_ratio: float = DEFAULT_UNLOCK_LINE_RATIO,
    protagonist_name: str | None = None,
    emotion_stack: FanqieShortEmotionStack | None = None,
) -> FanqieRankingGateReport:
    core = evaluate_fanqie_core_ranking_readiness(
        full_text,
        unlock_line_ratio=unlock_line_ratio,
        protagonist_name=protagonist_name,
    )
    supplemental = evaluate_fanqie_short_v2_supplemental_readiness(
        full_text,
        title=title,
        protagonist_name=protagonist_name,
        emotion_stack=emotion_stack,
    )
    findings = (*core.findings, *supplemental.findings)
    return FanqieRankingGateReport(
        passed=not any(finding.severity == "critical" for finding in findings),
        phase="short_v2_readiness",
        findings=findings,
    )


def evaluate_fanqie_short_v2_supplemental_readiness(
    full_text: str,
    *,
    title: str | None = None,
    protagonist_name: str | None = None,
    emotion_stack: FanqieShortEmotionStack | None = None,
) -> FanqieRankingGateReport:
    """Evaluate v2-only checks that sit on top of the legacy ranking gate."""
    reports = (
        evaluate_fanqie_short_v2_title_gate(title),
        evaluate_reader_retention_gate(full_text, protagonist_name=protagonist_name),
        evaluate_social_resonance_gate(full_text),
        evaluate_payoff_density_gate(full_text),
        evaluate_comedy_or_tear_point_gate(full_text),
        evaluate_anti_longform_contamination_gate(full_text),
        evaluate_emotion_stack_gate(full_text, emotion_stack=emotion_stack),
    )
    findings = tuple(finding for report in reports for finding in report.findings)
    return FanqieRankingGateReport(
        passed=not any(finding.severity == "critical" for finding in findings),
        phase="short_v2_supplemental_readiness",
        findings=findings,
    )


def build_fanqie_short_v2_rewrite_routes(
    report: FanqieRankingGateReport,
) -> list[FanqieShortRewriteRoute]:
    routes: list[FanqieShortRewriteRoute] = []
    for finding in report.findings:
        if finding.severity != "critical":
            continue
        worker, action = _route_for_finding(finding.code)
        routes.append(
            FanqieShortRewriteRoute(
                finding_code=finding.code,
                worker=worker,
                action=action,
                target=finding.target,
                priority=1,
            )
        )
    return routes


def build_fanqie_short_v2_rewrite_instructions(report: FanqieRankingGateReport) -> str:
    routes = build_fanqie_short_v2_rewrite_routes(report)
    if not routes:
        return ""
    lines = ["番茄短故事 v2 门禁未过，请按 Worker 路由闭环修复："]
    for route in routes:
        lines.append(
            f"- {route.worker} 修复 {route.finding_code} "
            f"({route.target})：{route.action}"
        )
    return "\n".join(lines)


def _route_for_finding(code: str) -> tuple[str, str]:
    if code.startswith("title_"):
        return "TitleWorker", "重写标题为“开局压迫 + 我让反派翻车/自爆/认罪”的点击合同。"
    if code.startswith("first_screen") or code.startswith("opening_"):
        return "OpeningContractWorker", "重写前300字，让主角承压、反击信号和小反馈同时出现。"
    if code.startswith("social_") or code.startswith("emotion_"):
        return "EmotionSelectionWorker", "重新选择社会情绪栈，并把痛点落到开局可见场景。"
    if code.startswith("payoff_") or code.startswith("unlock_"):
        return "ShortStructureWorker", "压缩铺垫，提高前30%压迫-行动-回报循环密度。"
    if code.startswith("longform_") or "cliffhanger" in code:
        return "ResourceAdapterWorker", "删除长篇卷/章/下章信号，改成本篇闭合结构。"
    if code.startswith("closure_"):
        return "EditorWorker", "补足末段胜负、代价和情绪落点。"
    return "RankingGateWorker", "按门禁证据定位重写，不改无关设定。"


def _report(
    phase: str,
    findings: Iterable[FanqieRankingFinding],
) -> FanqieRankingGateReport:
    items = tuple(findings)
    return FanqieRankingGateReport(
        passed=not any(finding.severity == "critical" for finding in items),
        phase=phase,
        findings=items,
    )


def _finding(
    code: str,
    severity: str,
    message: str,
    evidence: str,
    phase: str,
    target: str,
) -> FanqieRankingFinding:
    return FanqieRankingFinding(
        code=code,
        severity=severity,
        message=message,
        evidence=evidence,
        phase=phase,
        target=target,
    )


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(term and term.lower() in lowered for term in terms)


def _count_term_hits(text: str, terms: Iterable[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(term.lower()) for term in terms if term)


def _count_cjk(text: str) -> int:
    return sum(1 for char in text if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff")


def _cjk_slice(text: str, limit: int) -> str:
    if _count_cjk(text) == 0:
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


def _window_around(text: str, term: str, *, radius: int = 80) -> str:
    index = text.find(term)
    if index < 0:
        return ""
    return text[max(0, index - radius) : index + len(term) + radius]
