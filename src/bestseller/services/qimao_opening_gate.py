from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bestseller.services.output_validator import EntityDensityCheck


@dataclass(frozen=True)
class QimaoOpeningFinding:
    code: str
    severity: str
    message: str
    evidence: str
    chapter_number: int | None = None


@dataclass(frozen=True)
class QimaoOpeningGateReport:
    passed: bool
    findings: tuple[QimaoOpeningFinding, ...]


_ORDINARY_ENTRY_TERMS = (
    "清晨",
    "醒来",
    "起床",
    "天气",
    "阳光",
    "风景",
    "街道",
    "背景",
    "世界观",
    "设定",
    "传说",
    "多年以前",
    "在这个世界",
    "普通的一天",
    "平静的一天",
    "normal day",
    "ordinary day",
    "woke up",
    "wakes up",
    "weather",
    "sunlight",
    "scenery",
    "background",
    "worldbuilding",
    "legend",
    "years ago",
    "in this world",
)

_CONFLICT_TERMS = (
    "逼",
    "夺",
    "抢",
    "杀",
    "逃",
    "血",
    "痛",
    "跪",
    "罚",
    "威胁",
    "否则",
    "不能",
    "必须",
    "冲突",
    "打",
    "砸",
    "骂",
    "退婚",
    "证据",
    "毁",
    "死",
    "追",
    "锁",
    "threat",
    "forced",
    "must",
    "cannot",
    "or else",
    "blood",
    "kill",
    "escape",
    "fight",
    "evidence",
    "destroy",
    "danger",
    "pressure",
    "conflict",
    "chased",
    "chase",
    "knife",
    "exposed",
)

_DIRECT_PRESENT_PRESSURE_TERMS = (
    "逼",
    "押",
    "绑",
    "拦",
    "夺",
    "抢",
    "追到",
    "追上",
    "追来",
    "追杀",
    "追捕",
    "被追",
    "逃",
    "杀",
    "砸",
    "烧掉",
    "点火",
    "拖走",
    "威胁",
    "否则",
    "必须",
    "只剩",
    "证据",
    "签字",
    "交出",
    "滚",
    "放人",
    "救下",
    "救人",
    "救命",
    "threat",
    "forced",
    "chased",
    "escape",
    "or else",
    "evidence",
    "must",
)

_ACTION_TERMS = (
    "抓",
    "按",
    "推",
    "摔",
    "砸",
    "冲",
    "躲",
    "拔",
    "抬",
    "扯",
    "扣",
    "夺",
    "撕",
    "盯",
    "跪",
    "拦",
    "挡",
    "退",
    "grab",
    "push",
    "pull",
    "run",
    "strike",
    "dodge",
    "raise",
    "tear",
    "stare",
    "block",
    "move",
    "act",
)

_LORE_DENSITY_TERMS = (
    "器灵",
    "共感",
    "残痕",
    "器魂",
    "器气",
    "寻迹者",
    "铭纹",
    "铭文",
    "地支",
    "方位",
    "镇宅",
    "邪祟",
    "子时",
    "十二枚",
    "三十年前",
    "十二年前",
    "古人",
    "血脉",
    "封印",
    "残相",
    "worldbuilding",
    "system",
    "ancient order",
    "bloodline",
    "seal",
)

_EXPOSITION_TERMS = (
    "背景",
    "世界观",
    "设定",
    "传说",
    "据说",
    "曾经",
    "多年以前",
    "历史",
    "制度",
    "家族由来",
    "势力分布",
    "background",
    "worldbuilding",
    "legend",
    "history",
    "system",
    "institution",
    "years ago",
    "used to",
    "once",
)

_HOOK_TERMS = (
    "？",
    "?",
    "否则",
    "就在这时",
    "突然",
    "门外",
    "真相",
    "谁",
    "不能",
    "必须",
    "只剩",
    "却",
    "响起",
    "抬头",
    "下一刻",
    "?",
    "or else",
    "suddenly",
    "truth",
    "who",
    "must",
    "cannot",
    "only",
    "but",
    "then",
    "next",
)

_PAYOFF_TERMS = (
    "拿到",
    "夺回",
    "反制",
    "翻盘",
    "赢",
    "救下",
    "证据",
    "筹码",
    "突破",
    "升级",
    "奖励",
    "兑换",
    "到账",
    "解锁",
    "发现",
    "撬开",
    "wins",
    "take",
    "gain",
    "save",
    "evidence",
    "leverage",
    "breakthrough",
    "reward",
    "unlock",
    "discover",
    "reversal",
)


def _cjk_slice(text: str, limit: int) -> str:
    if not any("\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff" for char in text):
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
    if not any("\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff" for char in text):
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


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _count_terms(text: str, terms: Sequence[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(term.lower()) for term in terms)


def _has_direct_present_pressure(text: str) -> bool:
    """Return whether the opening gives a present-tense scene pressure.

    The gate deliberately separates real scene pressure from atmospheric
    memory fragments. A chapter can contain fire, blood, screams, and death
    yet still fail commercially if all of that conflict is backstory and the
    reader cannot answer "what is happening now?"
    """

    return (
        "“" in text
        or '"' in text
        or _contains_any(text, _DIRECT_PRESENT_PRESSURE_TERMS)
    )


def _normalize_chapter_texts(
    chapter_texts: str | Sequence[str] | Mapping[int, str],
) -> list[tuple[int, str]]:
    if isinstance(chapter_texts, str):
        return [(1, chapter_texts)]
    if isinstance(chapter_texts, Mapping):
        return [(int(number), str(text or "")) for number, text in sorted(chapter_texts.items())]
    return [(idx, str(text or "")) for idx, text in enumerate(chapter_texts, start=1)]


def _named_entity_candidates(text: str) -> set[str]:
    if any("\u3400" <= char <= "\u9fff" for char in text):
        return EntityDensityCheck._extract_zh(text)
    return set()


def _chapter_has_loop_markers(text: str) -> bool:
    has_conflict = _contains_any(text, _CONFLICT_TERMS)
    has_action = _contains_any(text, _ACTION_TERMS)
    has_payoff = _contains_any(text, _PAYOFF_TERMS)
    has_hook = _contains_any(
        _cjk_tail(text, 320),
        _HOOK_TERMS,
    ) or text.rstrip().endswith(("？", "?", "！", "!"))
    return has_conflict and has_action and (has_payoff or has_hook)


def evaluate_qimao_opening_gate(
    chapter_texts: str | Sequence[str] | Mapping[int, str],
    *,
    opening_contract: dict[str, Any] | None = None,
    protagonist_name: str | None = None,
) -> QimaoOpeningGateReport:
    chapters = _normalize_chapter_texts(chapter_texts)
    findings: list[QimaoOpeningFinding] = []
    if not chapters:
        return QimaoOpeningGateReport(
            passed=False,
            findings=(
                QimaoOpeningFinding(
                    code="ordinary_entry",
                    severity="critical",
                    message="没有可检查的开篇文本。",
                    evidence="chapter_texts is empty",
                ),
            ),
        )

    if not protagonist_name and isinstance(opening_contract, dict):
        raw_name = opening_contract.get("protagonist_name")
        protagonist_name = raw_name.strip() if isinstance(raw_name, str) else None

    first_chapter_number, first_text = chapters[0]
    first_150 = _cjk_slice(first_text, 150)
    first_300 = _cjk_slice(first_text, 300)
    first_500 = _cjk_slice(first_text, 500)
    first_800 = _cjk_slice(first_text, 800)
    first_1000 = _cjk_slice(first_text, 1000)

    if protagonist_name and protagonist_name not in first_150:
        findings.append(QimaoOpeningFinding(
            code="weak_immersion",
            severity="critical",
            message="主角没有在前100-150字内成为读者视角焦点。",
            evidence=first_150,
            chapter_number=first_chapter_number,
        ))

    has_present_pressure = _has_direct_present_pressure(first_500)
    has_conflict_or_pressure = _contains_any(first_300, _CONFLICT_TERMS) or has_present_pressure
    if _contains_any(first_300, _ORDINARY_ENTRY_TERMS) and (
        not has_conflict_or_pressure or _count_terms(first_300, _ACTION_TERMS) < 2
    ):
        findings.append(QimaoOpeningFinding(
            code="ordinary_entry",
            severity="critical",
            message="前300字疑似普通日常、醒来、风景、背景或设定切入。",
            evidence=first_300,
            chapter_number=first_chapter_number,
        ))
    elif not has_conflict_or_pressure:
        findings.append(QimaoOpeningFinding(
            code="weak_hook",
            severity="critical",
            message="前300字缺少可感冲突、动作或对话压力。",
            evidence=first_300,
            chapter_number=first_chapter_number,
        ))

    if not has_present_pressure:
        findings.append(QimaoOpeningFinding(
            code="weak_present_conflict",
            severity="critical",
            message="前500字缺少读者能立刻复述的当场压力，容易变成氛围/回忆/设定切入。",
            evidence=first_500,
            chapter_number=first_chapter_number,
        ))

    if (
        not has_present_pressure
        and _contains_any(
            first_500,
            ("不是现在", "很久以前", "记忆", "残相", "backstory", "memory"),
        )
    ):
        findings.append(QimaoOpeningFinding(
            code="retrospective_fake_conflict",
            severity="critical",
            message="开篇冲突主要来自回忆或残影，不是当前场景的明确事件。",
            evidence=first_500,
            chapter_number=first_chapter_number,
        ))

    lore_count = _count_terms(first_800, _LORE_DENSITY_TERMS)
    if lore_count >= 7 and _count_terms(first_800, _DIRECT_PRESENT_PRESSURE_TERMS) < 2:
        findings.append(QimaoOpeningFinding(
            code="opening_lore_overload",
            severity="critical",
            message="前800字专名、规则、历史和术语密度过高，读者难以先抓住故事。",
            evidence=f"lore_term_hits={lore_count}; sample={first_800}",
            chapter_number=first_chapter_number,
        ))

    exposition_count = _count_terms(first_800, _EXPOSITION_TERMS)
    action_count = _count_terms(first_800, _ACTION_TERMS)
    if exposition_count >= 3 and action_count < 2:
        findings.append(QimaoOpeningFinding(
            code="flat_narration",
            severity="critical",
            message="前800字解释/设定占比过高，缺少动作和具体后果承载。",
            evidence=first_800,
            chapter_number=first_chapter_number,
        ))

    names = _named_entity_candidates(first_1000)
    if len(names) > 8:
        findings.append(QimaoOpeningFinding(
            code="weak_immersion",
            severity="warning",
            message="前1000字疑似具名人物过多，容易削弱主角代入。",
            evidence="、".join(sorted(names)[:12]),
            chapter_number=first_chapter_number,
        ))

    ending = _cjk_tail(first_text, 320)
    if not _contains_any(ending, _HOOK_TERMS) and not first_text.rstrip().endswith(
        ("？", "?", "！", "!")
    ):
        findings.append(QimaoOpeningFinding(
            code="weak_hook",
            severity="critical",
            message="第一章结尾缺少问题、威胁、反转、未完成动作或利益诱因。",
            evidence=ending,
            chapter_number=first_chapter_number,
        ))

    golden_three_text = "\n".join(text for _, text in chapters[:3])
    if len(chapters) >= 3 and not _contains_any(golden_three_text, _PAYOFF_TERMS):
        findings.append(QimaoOpeningFinding(
            code="weak_golden_three_payoff",
            severity="critical",
            message="前三章缺少可识别的小爽点、收益或阶段回报。",
            evidence=_cjk_slice(golden_three_text, 500),
        ))

    first_10k_chapters = chapters[:10]
    loopless = [
        number
        for number, text in first_10k_chapters
        if text.strip() and not _chapter_has_loop_markers(text)
    ]
    if loopless:
        findings.append(QimaoOpeningFinding(
            code="first_10k_loop_missing",
            severity="critical",
            message="前一万字样本中存在没有冲突-行动-收益/钩子循环的章节。",
            evidence=", ".join(str(number) for number in loopless),
        ))

    passed = not any(finding.severity == "critical" for finding in findings)
    return QimaoOpeningGateReport(passed=passed, findings=tuple(findings))


def qimao_opening_gate_report_to_dict(report: QimaoOpeningGateReport) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity,
                "message": finding.message,
                "evidence": finding.evidence,
                "chapter_number": finding.chapter_number,
            }
            for finding in report.findings
        ],
    }
