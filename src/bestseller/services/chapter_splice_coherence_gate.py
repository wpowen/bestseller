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
from bestseller.services.progress_context import emit_gate_result
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
_COUNTDOWN_CONTEXT_RE = re.compile(
    r"(?:倒计时|接单时限|时限|窗口|归零|剩余|有效期|截止|倒数|还剩|"
    r"剩下|扣减|申诉|余额|归档|清零|配额|体检券|待签|名单|截图|附件|"
    r"回执|回电|审计|十五分钟|八分钟|四小时|五天)"
)
_NON_SCENE_TIME_CONTEXT_RE = re.compile(
    r"(?:想起|记起|回忆|截图|名单|附件|提示|提示栏|待签|归档|清零|"
    r"配额|体检券|审计|之前|以后|过了|剩下|还有|窗口|回电)"
)
_CLOCK_RE = re.compile(r"(?P<hour>\d{1,2})[:：](?P<minute>\d{2})")
_CHINESE_HOUR_RE = re.compile(
    r"(?P<hour>[一二三四五六七八九十两\d]{1,3})点(?P<minute>半|零|[一二三四五六七八九十两\d]{1,2}分)?"
)
_NON_TIME_POINT_SUFFIXES: frozenset[str] = frozenset(
    {
        "头",
        "点",
        "儿",
        "余",
        "钱",
        "灵",
        "学",
        "配",
        "震",
        "光",
        "红",
        "灰",
    }
)
_DAYPART_BUCKETS: dict[str, int] = {
    "凌晨": 3,
    "清晨": 6,
    "早上": 7,
    "上午": 9,
    "中午": 12,
    "下午": 15,
    "傍晚": 18,
    "黄昏": 18,
    "晚上": 20,
    "深夜": 23,
    "半夜": 0,
}
_DAY_OFFSET_ANCHORS: frozenset[str] = frozenset(
    {"明天", "昨天", "第二天", "第三天", "第四天", "第五天", "第六天", "第七天", "第八天", "第九天", "第十天"}
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
    emit_gate_result(
        "splice_coherence_gate",
        verdict=verdict,
        severity="critical" if critical else ("high" if high else "info"),
        score=100 if not findings else 0,
        reasons=[finding.message for finding in findings],
        chapter=chapter_number,
    )
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
        action_match = None
        for candidate in action_re.finditer(search_tail):
            if _is_exit_continuation_action(candidate.group(0)):
                continue
            action_match = candidate
            break
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


def _is_exit_continuation_action(action_text: str) -> bool:
    """Return True for movement that continues the same leaving beat."""

    return bool(
        re.search(
            r"(?:没回头|脚步|电梯|门外|走廊|往[^。！？\n]{0,12}走|向[^。！？\n]{0,12}走)",
            action_text,
        )
    )


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
    anchors = _effective_time_jump_anchors(text)
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


def _effective_time_jump_anchors(text: str) -> list[str]:
    anchors: list[str] = []
    numeric_minutes: list[int] = []
    for match in _TIME_RE.finditer(text):
        raw = match.group(0)
        before = text[max(0, match.start() - 24) : match.start()]
        after = text[match.end() : min(len(text), match.end() + 24)]
        window = f"{before}{raw}{after}"
        if _is_false_time_anchor(raw, after):
            continue
        if _COUNTDOWN_CONTEXT_RE.search(window) or _NON_SCENE_TIME_CONTEXT_RE.search(window):
            minute = _clock_anchor_minutes(raw)
            if minute is not None:
                numeric_minutes.append(minute)
            continue
        minute = _clock_anchor_minutes(raw)
        if minute is not None:
            numeric_minutes.append(minute)
            anchors.append(f"{minute // 60:02d}:xx")
            continue
        bucket = _daypart_bucket(raw)
        if bucket is not None:
            anchors.append(f"{bucket:02d}:daypart")
            continue
        anchors.append(raw)

    anchors = _drop_same_hour_numeric_clusters(anchors, numeric_minutes)
    return _collapse_nearby_daypart_and_clock_anchors(anchors)


def _is_false_time_anchor(raw: str, after: str) -> bool:
    if raw in _DAYPART_BUCKETS or raw in _DAY_OFFSET_ANCHORS:
        return False
    if not raw.endswith("点") and "点" not in raw:
        return False
    suffix = after[:1]
    if suffix in _NON_TIME_POINT_SUFFIXES:
        return True
    return raw.endswith("点零") and suffix and suffix not in {"分", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}


def _clock_anchor_minutes(raw: str) -> int | None:
    match = _CLOCK_RE.fullmatch(raw)
    if match:
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute
        return None
    match = _CHINESE_HOUR_RE.fullmatch(raw)
    if not match:
        return None
    hour = _parse_chinese_hour(match.group("hour"))
    if hour is None or hour > 23:
        return None
    minute_raw = match.group("minute")
    minute = 0
    if minute_raw == "半":
        minute = 30
    elif minute_raw and minute_raw.endswith("分"):
        parsed_minute = _parse_chinese_hour(minute_raw[:-1])
        if parsed_minute is None or parsed_minute > 59:
            return None
        minute = parsed_minute
    return hour * 60 + minute


def _parse_chinese_hour(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if raw == "十":
        return 10
    if raw.startswith("十"):
        tail = raw[1:]
        return 10 + digits.get(tail, 0)
    if "十" in raw:
        head, tail = raw.split("十", 1)
        if head not in digits:
            return None
        return digits[head] * 10 + digits.get(tail, 0)
    if len(raw) == 1 and raw in digits:
        return digits[raw]
    return None


def _daypart_bucket(raw: str) -> int | None:
    if raw in _DAY_OFFSET_ANCHORS:
        return 24
    return _DAYPART_BUCKETS.get(raw)


def _drop_same_hour_numeric_clusters(anchors: list[str], numeric_minutes: list[int]) -> list[str]:
    if len(numeric_minutes) < 3:
        return anchors
    hours = {minute // 60 for minute in numeric_minutes}
    if len(hours) != 1:
        return anchors
    # Dense timestamps such as 03:47, 03:46, 03:45 are usually a countdown or
    # UI progress clock, not a stitched timeline jump.
    return [anchor for anchor in anchors if anchor != f"{next(iter(hours)):02d}:xx"]


def _collapse_nearby_daypart_and_clock_anchors(anchors: list[str]) -> list[str]:
    collapsed: list[str] = []
    for anchor in anchors:
        if collapsed and _time_anchor_hour(collapsed[-1]) == _time_anchor_hour(anchor):
            continue
        collapsed.append(anchor)
    return collapsed


def _time_anchor_hour(anchor: str) -> int | None:
    match = re.match(r"(\d{2}):", anchor)
    if not match:
        return None
    return int(match.group(1))


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
