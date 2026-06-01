"""Detect stitched-draft artifacts inside an assembled chapter.

This gate targets failure modes that ordinary repetition checks miss: two
draft alternatives merged into one chapter, a character leaving then acting
again without a return beat, or a one-off location/time anchor that appears
without setup.
"""
# ruff: noqa: RUF001

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from difflib import SequenceMatcher
import re
from typing import Any

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.services.checker_schema import CheckerIssue, CheckerReport
from bestseller.services.quality_finding_schema import QualityFinding

_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?]\s*|\n+")
_PUNCT_RE = re.compile(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()\[\]【】《》#*_`-]+")
_LEAVE_RE = re.compile(
    r"(?P<actor>[\u4e00-\u9fff]{2,3})(?:[^。！？\n]{0,18})"
    r"(?:早就走了|已经走了|离开了|走了|转身往[^。！？\n]{0,12}走|往[^。！？\n]{0,12}走去)"
)
_RETURN_RE = re.compile(r"(?:回来|返回|折返|又回到|重新回到|赶回|走回来)")
_ACTION_RE = re.compile(
    r"{actor}(?:[^。！？\n]{{0,18}})(?:说|问|看|盯|抓|递|捏|抬头|皱眉|"
    r"转身|直起身|把|站|走|开口|伸手|低声|枪口|呼吸)"
)
_LOCATION_RE = re.compile(
    r"(?P<name>[\u4e00-\u9fff]{0,4}(?:栋|旧货市场|太平间|医院|证物室|"
    r"楼道|井口|电梯|仓库|地下室|派出所|警局|馄饨摊|摊位|门口))"
)
_TIME_RE = re.compile(
    r"(?:凌晨|清晨|早上|上午|中午|下午|傍晚|黄昏|晚上|深夜|半夜|"
    r"明天|昨天|第二天|第[二三四五六七八九十]+天|"
    r"\d{1,2}[:：]\d{2}|[一二三四五六七八九十两\d]{1,3}点(?:零|半|\d{1,2}分)?)"
)
_BRIDGE_RE = re.compile(
    r"(?:与此同时|另一边|随后|接着|不久|片刻后|十分钟后|半小时后|四十分钟后|"
    r"第二天|转场|回到|赶到|来到|到了|换了地方|车停在|他们进了)"
)


_SPLICE_CRITICAL_CODES: frozenset[str] = frozenset(
    {
        "CHAPTER_SPLICE_REPEATED_SENTENCE",
        "CHAPTER_SPLICE_NEAR_DUPLICATE_BLOCK",
        "CHAPTER_SPLICE_PRESENCE_CONTRADICTION",
    }
)

_SPLICE_HIGH_RATIONALES: dict[str, tuple[str, ...]] = {
    "CHAPTER_SPLICE_LOCATION_DRIFT": ("LOGIC_INTEGRITY", "WORLD_RULE_CONSTRAINT"),
    "CHAPTER_SPLICE_UNSEEDED_LOCATION_REFERENCE": (
        "WORLD_RULE_CONSTRAINT",
        "LOGIC_INTEGRITY",
    ),
    "CHAPTER_SPLICE_TIME_JUMP": ("ARC_TIMING", "EDITORIAL_INTENT"),
}
_SPLICE_PARAGRAPH_SCOPE_CODES: frozenset[str] = frozenset(
    {
        "CHAPTER_SPLICE_REPEATED_SENTENCE",
        "CHAPTER_SPLICE_NEAR_DUPLICATE_BLOCK",
    }
)
_SPLICE_BLOCKING_SEVERITIES: frozenset[str] = frozenset({"critical", "high"})


def _dedupe_findings(findings: Iterable[GateFinding]) -> tuple[GateFinding, ...]:
    deduped: list[GateFinding] = []
    seen: set[tuple[str, str, str]] = set()
    for finding in findings:
        key = (finding.code, finding.path, finding.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return tuple(deduped)


def blocking_splice_findings(verdict: GateVerdict) -> tuple[GateFinding, ...]:
    """Return deduped splice findings that should trigger repair/governance."""
    return tuple(
        finding
        for finding in _dedupe_findings(verdict.findings)
        if finding.severity in _SPLICE_BLOCKING_SEVERITIES
    )


def splice_repair_scope(finding: GateFinding) -> str:
    return "paragraph" if finding.code in _SPLICE_PARAGRAPH_SCOPE_CODES else "chapter"


def _finding_to_issue(finding: GateFinding) -> CheckerIssue:
    if finding.code in _SPLICE_CRITICAL_CODES:
        can_override = False
        rationales: tuple[str, ...] = ()
    else:
        can_override = True
        rationales = _SPLICE_HIGH_RATIONALES.get(
            finding.code, ("EDITORIAL_INTENT", "ARC_TIMING")
        )
    return CheckerIssue(
        id=finding.code,
        type="splice_coherence",
        severity=finding.severity,
        location=finding.path or "chapter",
        description=finding.message,
        suggestion=finding.repair_action or "",
        can_override=can_override,
        allowed_rationales=rationales,
    )


def as_checker_report(
    verdict: GateVerdict,
    *,
    chapter_number: int | None = None,
    issues: "Iterable[CheckerIssue] | None" = None,
) -> CheckerReport:
    materialised_issues = (
        list(issues)
        if issues is not None
        else [_finding_to_issue(f) for f in _dedupe_findings(verdict.findings)]
    )
    passed = verdict.verdict == "pass"
    return CheckerReport(
        agent=verdict.gate_name,
        chapter=chapter_number or 0,
        overall_score=int(round((verdict.coverage or 0.0) * 100)),
        passed=passed,
        issues=tuple(materialised_issues),
        metrics=dict(verdict.metrics),
        summary=verdict.summary,
    )


def as_quality_findings(
    verdict: GateVerdict,
    *,
    chapter_number: int | None = None,
    source: str = "chapter_quality_bundle.splice_coherence",
) -> tuple[QualityFinding, ...]:
    """Adapt splice findings into the chapter quality bundle's normalized shape."""

    findings: list[QualityFinding] = []
    for finding in _dedupe_findings(verdict.findings):
        issue = _finding_to_issue(finding)
        findings.append(
            QualityFinding(
                code=finding.code,
                severity=finding.severity,
                source=source,
                chapter_number=chapter_number,
                evidence={
                    "message": finding.message,
                    "path": finding.path,
                    "gate": verdict.gate_name,
                    "can_override": issue.can_override,
                    "allowed_rationales": list(issue.allowed_rationales),
                    "schema_version": verdict.schema_version,
                },
                repair_hint=finding.repair_action or finding.message,
                repair_scope=splice_repair_scope(finding),
                blocking=finding.severity in _SPLICE_BLOCKING_SEVERITIES,
            )
        )
    return tuple(findings)


def as_repair_patch_points(
    findings: Sequence[GateFinding],
) -> tuple[Mapping[str, object], ...]:
    """Adapt splice findings into autonomous repair patch-point payloads."""

    return tuple(
        {
            "cause_id": finding.code,
            "location": finding.path,
            "issue_summary": finding.message,
            "snippet": "",
            "repair_action_summary": finding.repair_action,
        }
        for finding in _dedupe_findings(findings)
    )


def as_gate_summary(
    verdict: GateVerdict,
    *,
    chapter_number: int | None,
) -> dict[str, Any]:
    """Return the WIP/reporting summary for one splice gate verdict."""

    return {
        "chapter_number": chapter_number,
        "verdict": verdict.verdict,
        "coverage": verdict.coverage,
        "metrics": dict(verdict.metrics),
        "blocking_findings": [
            finding.model_dump(mode="json")
            for finding in blocking_splice_findings(verdict)
        ],
    }


def evaluate_chapter_splice_coherence(
    chapter_text: str,
    *,
    chapter_number: int | None = None,
) -> GateVerdict:
    """Return a blocking verdict for high-confidence splice artifacts."""

    text = chapter_text or ""
    findings: list[GateFinding] = []
    findings.extend(_repeated_sentence_findings(text))
    findings.extend(_near_duplicate_paragraph_findings(text))
    findings.extend(_presence_contradiction_findings(text))
    findings.extend(_location_anchor_findings(text))
    findings.extend(_time_jump_findings(text))

    critical = sum(1 for finding in findings if finding.severity == "critical")
    high = sum(1 for finding in findings if finding.severity == "high")
    verdict = "blocked" if critical or high else "pass"
    return GateVerdict(
        gate_name="chapter_splice_coherence",
        verdict=verdict,
        coverage=1.0 if not findings else 0.0,
        findings=tuple(findings),
        metrics={
            "chapter_number": chapter_number,
            "finding_count": len(findings),
            "critical_count": critical,
            "high_count": high,
        },
        summary=(
            "Chapter has no high-confidence splice artifacts."
            if not findings
            else f"Chapter has {len(findings)} splice coherence finding(s)."
        ),
    )


def _repeated_sentence_findings(text: str) -> list[GateFinding]:
    sentences = [
        sentence.strip()
        for sentence in _SENTENCE_SPLIT_RE.split(text)
        if len(_normalize(sentence)) >= 14
    ]
    by_normalized: dict[str, list[str]] = defaultdict(list)
    for sentence in sentences:
        normalized = _normalize(sentence)
        if normalized:
            by_normalized[normalized].append(sentence)

    findings: list[GateFinding] = []
    for _normalized, variants in by_normalized.items():
        if len(variants) < 2:
            continue
        excerpt = variants[0][:90]
        findings.append(
            GateFinding(
                code="CHAPTER_SPLICE_REPEATED_SENTENCE",
                severity="critical",
                message=f"同一句叙事/对白在章内重复出现 {len(variants)} 次：{excerpt}",
                path=_path_for_excerpt(text, variants[0]),
                repair_action="合并重复草稿段，只保留一次，并把第二次改成递进的新信息或新动作。",
            )
        )
        if len(findings) >= 5:
            break
    return findings


def _near_duplicate_paragraph_findings(text: str) -> list[GateFinding]:
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text)
        if 50 <= len(_normalize(paragraph)) <= 320
    ]
    findings: list[GateFinding] = []
    for left_index, left in enumerate(paragraphs):
        left_norm = _normalize(left)
        for right_index in range(left_index + 2, len(paragraphs)):
            right = paragraphs[right_index]
            ratio = SequenceMatcher(None, left_norm, _normalize(right)).ratio()
            if ratio < 0.82:
                continue
            findings.append(
                GateFinding(
                    code="CHAPTER_SPLICE_NEAR_DUPLICATE_BLOCK",
                    severity="critical",
                    message=(
                        "章内存在高度相似的非相邻段落，疑似两个生成草稿被拼接："
                        f"p{left_index + 1} ↔ p{right_index + 1}"
                    ),
                    path=f"paragraph:{left_index + 1},paragraph:{right_index + 1}",
                    repair_action="删除或合并二选一草稿段，并补一条线性因果过渡。",
                )
            )
            return findings
    return findings


def _presence_contradiction_findings(text: str) -> list[GateFinding]:
    findings: list[GateFinding] = []
    matches = list(_LEAVE_RE.finditer(text))
    for match in matches:
        actor = match.group("actor")
        if not _looks_like_actor(actor):
            continue
        tail = text[match.end() : match.end() + 900]
        return_match = _RETURN_RE.search(tail)
        search_tail = tail if return_match is None else tail[: return_match.start()]
        action_re = re.compile(_ACTION_RE.pattern.format(actor=re.escape(actor)))
        action_match = action_re.search(search_tail)
        if action_match is None:
            continue
        findings.append(
            GateFinding(
                code="CHAPTER_SPLICE_PRESENCE_CONTRADICTION",
                severity="critical",
                message=(
                    f"{actor} 已离场后又在无回场标记的后文继续行动，疑似时序拼接。"
                ),
                path=_path_for_excerpt(text, actor),
                repair_action="明确角色全程在场、明确回场动作，或删除后续误拼的角色行动。",
            )
        )
        if len(findings) >= 3:
            break
    return findings


def _location_anchor_findings(text: str) -> list[GateFinding]:
    names = [match.group("name").strip() for match in _LOCATION_RE.finditer(text)]
    counts = Counter(name for name in names if name and len(name) >= 2)
    findings: list[GateFinding] = []

    market_directions = {
        name[:2]
        for name in counts
        if name.endswith("旧货市场") and name[:2] in {"城东", "城北", "城南", "城西"}
    }
    if len(market_directions) >= 2:
        findings.append(
            GateFinding(
                code="CHAPTER_SPLICE_LOCATION_DRIFT",
                severity="high",
                message="同章旧货市场方向锚点不一致，读者会误判为两条空间线。",
                path="location:旧货市场",
                repair_action="统一地点命名，或补出两个地点之间的明确转场和差异功能。",
            )
        )

    building_numbers = {
        match.group(1)
        for match in re.finditer(r"([一二三四五六七八九十\d]{1,3})栋", text)
    }
    if len(building_numbers) >= 2:
        findings.append(
            GateFinding(
                code="CHAPTER_SPLICE_LOCATION_DRIFT",
                severity="high",
                message=(
                    f"同章出现多个楼栋锚点 {sorted(building_numbers)}，"
                    "但未证明它们是同一条行动线。"
                ),
                path="location:building",
                repair_action="保留主楼栋锚点；若必须换楼栋，补清楚时间、交通和目的。",
            )
        )

    for abrupt in ("医院", "太平间", "证物室", "井口"):
        if counts.get(abrupt, 0) == 1 and _abrupt_location_reference(text, abrupt):
            findings.append(
                GateFinding(
                    code="CHAPTER_SPLICE_UNSEEDED_LOCATION_REFERENCE",
                    severity="high",
                    message=f"{abrupt} 作为状态/行踪锚点突然出现，前后缺少进出场桥接。",
                    path=_path_for_excerpt(text, abrupt),
                    repair_action="删除突兀地点，或补前置抵达、离开、目的与时间桥。",
                )
            )
            break
    return findings


def _time_jump_findings(text: str) -> list[GateFinding]:
    anchors = [match.group(0) for match in _TIME_RE.finditer(text)]
    distinct = tuple(dict.fromkeys(anchors))
    if len(distinct) < 4 or _BRIDGE_RE.search(text):
        return []
    return [
        GateFinding(
            code="CHAPTER_SPLICE_TIME_JUMP",
            severity="high",
            message=f"章内时间锚点过多且缺少桥接：{', '.join(distinct[:6])}",
            path="time:anchors",
            repair_action="统一到一条小时表，或补出每次时间跳转的因果桥。",
        )
    ]


def _abrupt_location_reference(text: str, location: str) -> bool:
    index = text.find(location)
    if index < 0:
        return False
    window = text[max(0, index - 80) : min(len(text), index + 120)]
    if _BRIDGE_RE.search(window):
        return False
    return bool(re.search(r"(十分钟前|刚才|早就|已经|突然|却|又|重新)", window))


def _path_for_excerpt(text: str, excerpt: str) -> str:
    index = text.find(excerpt)
    if index < 0:
        return ""
    line = text.count("\n", 0, index) + 1
    return f"line:{line}"


def _normalize(value: str) -> str:
    return _PUNCT_RE.sub("", value or "")


def _looks_like_actor(value: str) -> bool:
    if not value or len(value) < 2:
        return False
    if value.endswith(("把", "将", "被", "向", "往", "和", "与", "跟")):
        return False
    return value not in {"他们", "我们", "有人", "那人", "众人", "所有人", "电梯", "镜子"}


__all__ = [
    "as_checker_report",
    "as_gate_summary",
    "as_quality_findings",
    "as_repair_patch_points",
    "blocking_splice_findings",
    "evaluate_chapter_splice_coherence",
    "splice_repair_scope",
]
