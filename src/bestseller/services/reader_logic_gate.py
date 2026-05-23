"""Reader Logic Gate — adjacent-chapter state continuity checks.

This gate catches failures that are obvious to a reader but easy for token
overlap gates to miss: a chapter says the protagonist did not open a door
after the previous chapter already put them inside, or the next chapter starts
at a different room without a visible movement bridge.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


_ROOM_RE = re.compile(r"(?:30[0-9]|[一二三四五六七八九十两]{1,3}楼|二十三层|二十三楼|[0-9]{1,2}层)")
_NON_PHYSICAL_ROOM_CONTEXT = (
    "镜面",
    "镜子里",
    "映出",
    "监控",
    "照片",
    "地址",
    "A区",
    "收件",
    "窗口",
    "热源",
)
_ROOM_NEGATION_RE = re.compile(r"没有(?:去)?(?:推开|打开|开)([0-9]{3}|那道[^。！？\n]{0,12}门|[^。！？\n]{0,8}门)")
_ENTRY_VERBS = ("开门", "推开门", "走进", "进了", "跨进门槛", "走进走廊", "走进卧室")
_NEGATION_QUALIFIERS = ("再", "重新", "那道", "父亲", "镜门", "声音", "冒充", "凭空", "假")
_TRANSITION_MARKERS = (
    "回到",
    "退回",
    "转回",
    "折回",
    "冲回",
    "走回",
    "赶回",
    "来到",
    "走到",
    "退到",
    "冲到",
    "赶到",
    "没有追进",
    "先前",
    "刚才",
)


@dataclass(frozen=True)
class ReaderLogicFinding:
    code: str
    severity: str
    prev_chapter: int
    current_chapter: int
    message: str
    evidence: dict[str, str]


@dataclass(frozen=True)
class ReaderLogicReport:
    prev_chapter: int
    current_chapter: int
    findings: tuple[ReaderLogicFinding, ...]

    @property
    def passed(self) -> bool:
        return not any(f.severity == "critical" for f in self.findings)


def _strip_heading(text: str) -> str:
    lines = (text or "").splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].lstrip().startswith("#"):
        lines.pop(0)
    return "\n".join(lines).strip()


def _rooms(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _ROOM_RE.finditer(text or ""):
        context = (text or "")[max(0, match.start() - 24) : min(len(text or ""), match.end() + 24)]
        if any(marker in context for marker in _NON_PHYSICAL_ROOM_CONTEXT):
            continue
        token = match.group(0)
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _has_transition(text: str) -> bool:
    return any(marker in text for marker in _TRANSITION_MARKERS) or bool(
        re.search(r"从.{0,20}(到|回|退|赶|冲|走)", text or "")
    )


def evaluate_reader_logic_seam(
    prev_text: str,
    current_text: str,
    *,
    prev_chapter: int,
    current_chapter: int,
    tail_chars: int = 500,
    head_chars: int = 260,
) -> ReaderLogicReport:
    """Check whether adjacent chapters preserve reader-visible state."""

    prev_body = _strip_heading(prev_text)
    current_body = _strip_heading(current_text)
    prev_tail = prev_body[-tail_chars:]
    current_head = current_body[:head_chars]

    findings: list[ReaderLogicFinding] = []

    # A bare "did not open 303" is reader-hostile if the previous chapter
    # already spent prose entering a room. It needs a qualifier such as
    # "that mirror-door" or "again"; otherwise the surface logic contradicts.
    negation = _ROOM_NEGATION_RE.search(current_head)
    if negation is not None and any(verb in prev_body for verb in _ENTRY_VERBS):
        negation_sentence = negation.group(0)
        if not any(q in negation_sentence for q in _NEGATION_QUALIFIERS):
            findings.append(
                ReaderLogicFinding(
                    code="ambiguous_room_entry_negation",
                    severity="critical",
                    prev_chapter=prev_chapter,
                    current_chapter=current_chapter,
                    message=(
                        "本章开头用裸写法否定开门/进门，但上一章已经有进门动作；"
                        "读者会理解成前后矛盾。请改成具体对象，如“没有再开那道镜门”。"
                    ),
                    evidence={
                        "current_opening": current_head[:120],
                        "negation": negation_sentence,
                    },
                )
            )

    prev_rooms = _rooms(prev_tail)
    current_rooms = _rooms(current_head)
    if prev_rooms and current_rooms:
        prev_room = prev_rooms[-1]
        current_room = current_rooms[0]
        if prev_room != current_room and not _has_transition(current_head):
            findings.append(
                ReaderLogicFinding(
                    code="room_jump_without_reader_bridge",
                    severity="critical",
                    prev_chapter=prev_chapter,
                    current_chapter=current_chapter,
                    message=(
                        f"上一章末读者位置在「{prev_room}」附近，本章开头直接到「{current_room}」，"
                        "缺少退回、赶到、被带走或时间跳转等读者可见过桥。"
                    ),
                    evidence={
                        "prev_tail": prev_tail[-160:],
                        "current_opening": current_head[:160],
                    },
                )
            )

    return ReaderLogicReport(
        prev_chapter=prev_chapter,
        current_chapter=current_chapter,
        findings=tuple(findings),
    )


def build_reader_logic_repair_prompt(report: ReaderLogicReport) -> str:
    if report.passed:
        return ""
    lines = ["【读者逻辑断点修复任务】"]
    for finding in report.findings:
        lines.append(f"- {finding.message}")
    lines.append("- 重写要求：先补清楚主角当前所在位置、上一章动作结果、下一步为什么转向，再进入新事件。")
    return "\n".join(lines)


__all__ = [
    "ReaderLogicFinding",
    "ReaderLogicReport",
    "evaluate_reader_logic_seam",
    "build_reader_logic_repair_prompt",
]
