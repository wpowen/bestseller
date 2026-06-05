"""Exposition Density Gate — block chapters that drift into world-building dumps.

The second-largest cause of catastrophic early-chapter dropout (after
hook-echo failure) is **exposition overdose**. The LLM, given a rich
story bible, will reflexively explain things — flashbacks, lineage
recaps, mechanism walk-throughs, "as you know" dialogues.

榜单 authors push exposition into action, dialogue, or terse aside.
Their first 5 chapters average <25% exposition by character count.

This gate measures three signals:
    * **exposition_ratio** — paragraphs dominated by explain / past-tense
      / mechanism words
    * **flashback_ratio** — runs of past-tense narration that aren't
      embedded inside a single scene
    * **info_dump_runs** — N+ consecutive exposition paragraphs
      (a true "wall of text" failure mode)

Severity escalates by chapter position:
    * ch 1-5: > 25% exposition = critical
    * ch 6-10: > 35% = high
    * ch 11+: > 50% = info-only

The gate is non-fatal by default. The rendered prompt block lists the
worst exposition runs so the LLM knows what to cut on rewrite.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# Paragraph splitters — Chinese web fiction uses single \n between paras.
_PARAGRAPH_SPLITTER = re.compile(r"\n+")

# Dialogue markers — paragraphs containing these are NOT exposition.
_DIALOGUE_RE = re.compile(r'[“「『][^”」』\n]{2,}[”」』]')

# Quoted thought / inner monologue — also exempt.
_THOUGHT_RE = re.compile(r"心想|心道|暗想|暗道|心念|脑海中|心里想")

# Action verbs — paragraphs leading with these are usually scene action.
_ACTION_RE = re.compile(
    r"^[\s]*(?:他|她|林渊|林|主角)?[\s]{0,2}"
    r"(?:冲|扑|挥|砸|刺|踢|踹|撞|拔|斩|劈|抓|扯|甩|扔|推|拽|踩|跃|"
    r"扣|扫|落|腾|拔剑|出剑|出拳|握紧|抬手|抬眼|低头|转身|后退)"
)

# Exposition markers — strong signals of explainy / past-summary text.
_EXPOSITION_MARKERS = (
    # Lineage / world-building recap
    "据说", "传说", "传闻", "相传", "原来", "其实", "事实上",
    "讲究", "讲求", "讲究的是", "所谓", "也就是说", "换句话说",
    "古往今来", "自古以来", "千百年来", "几百年前", "几十年前",
    "三十年前", "二十三年前", "三百年前",
    # Mechanism walkthrough
    "工作原理", "原理", "其用法", "其规则", "其本质",
    "分为", "分作", "分类", "可分", "包括",
    # Author intrusion / "as you know"
    "众所周知", "大家都知道", "需要知道的是", "值得一提",
    # Past-tense run starters
    "曾经", "那年", "那时", "彼时", "当年", "那一年",
)

# Pure-info-dump phrases that mean "the next paragraph is going to be
# walking through the rulebook". Heavier weight.
_HEAVY_DUMP_PHRASES = (
    # Genre-neutral structural markers of a "rulebook walkthrough" exposition dump —
    # NOT one detective book's nouns (青囊/罗盘/镜中局/三族/茅山). The anti-pattern is
    # "the next sentence explains a rule/system/lore", whatever the genre's terms are.
    "术法分为", "功法分为", "体系分为", "的规则是", "的用法是",
    "秘卷记载", "典籍记载", "古籍记载", "记载道",
    "解释一下", "解释道", "解释说",
)

# A paragraph longer than this with no dialogue / no action verb is
# treated as a "wall of text" candidate.
_WALL_OF_TEXT_CHARS = 180


@dataclass(frozen=True)
class ExpositionFinding:
    """One audit finding from the gate."""

    code: str  # EXPOSITION_OK | EXPOSITION_HIGH | EXPOSITION_DUMP
    severity: str  # info | high | critical
    chapter_position: int
    exposition_ratio: float
    flashback_ratio: float
    info_dump_runs: int
    longest_dump_chars: int
    worst_excerpts: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class ExpositionReport:
    """Output of the gate — chapter-level summary + finding."""

    chapter_position: int
    finding: ExpositionFinding

    @property
    def passed(self) -> bool:
        return self.finding.severity == "info"

    def to_prompt_block(self, language: str = "zh-CN") -> str:
        """Render as a remediation block for the rewrite prompt."""

        if self.passed:
            return ""
        if language.lower().startswith("zh"):
            lines = ["【铺垫密度门 — 本章铺垫/解释过多】"]
            lines.append(
                f"- 铺垫占比: {self.finding.exposition_ratio:.0%} "
                f"(前 5 章上限 25%，6-10 章上限 35%)"
            )
            if self.finding.info_dump_runs > 0:
                lines.append(
                    f"- 连续解释段: {self.finding.info_dump_runs} 处"
                    f"（最长 {self.finding.longest_dump_chars} 字）"
                )
            if self.finding.worst_excerpts:
                lines.append("- 最严重的解释段（应改为对话/动作/侧面信息）:")
                for excerpt in self.finding.worst_excerpts[:3]:
                    short = excerpt[:60].replace("\n", " ")
                    lines.append(f"  · {short}…")
            lines.append(
                "- 重写要求："
                "（1）把世界观规则塞进对话中，由角色互相质问/反驳；"
                "（2）把历史回忆塞进当下行动的一个细节里；"
                "（3）不要写'据说/传说/原来'开头的整段铺垫。"
            )
            return "\n".join(lines)
        return f"[Exposition density {self.finding.exposition_ratio:.0%}]"


def check_exposition_density(
    text: str,
    *,
    chapter_position: int,
) -> ExpositionReport:
    """Score a chapter's exposition density and return a finding."""

    if not text or chapter_position < 1:
        return _ok_report(chapter_position)

    paragraphs = [p.strip() for p in _PARAGRAPH_SPLITTER.split(text) if p.strip()]
    if not paragraphs:
        return _ok_report(chapter_position)

    exposition_paragraphs: list[tuple[int, str, int]] = []  # (idx, text, weight)
    flashback_paragraphs: list[int] = []
    total_chars = 0
    exposition_chars = 0

    for idx, para in enumerate(paragraphs):
        n = len(para)
        total_chars += n

        is_dialogue = bool(_DIALOGUE_RE.search(para))
        is_thought = bool(_THOUGHT_RE.search(para))
        is_action = bool(_ACTION_RE.search(para))

        if is_dialogue or is_thought or is_action:
            continue

        weight = _exposition_weight(para)
        if weight > 0:
            exposition_paragraphs.append((idx, para, weight))
            exposition_chars += n

        if _is_flashback_paragraph(para):
            flashback_paragraphs.append(idx)

    exposition_ratio = exposition_chars / total_chars if total_chars else 0.0
    flashback_ratio = (
        sum(len(paragraphs[i]) for i in flashback_paragraphs) / total_chars
        if total_chars
        else 0.0
    )

    info_dump_runs, longest_dump = _count_info_dump_runs(
        paragraphs, exposition_paragraphs
    )

    # Sort worst exposition paragraphs by weight*length (the most obnoxious dumps)
    worst = sorted(
        exposition_paragraphs,
        key=lambda x: (-(x[2] * len(x[1])), x[0]),
    )[:4]
    worst_excerpts = tuple(p[1] for p in worst)

    severity, code, detail = _classify(
        chapter_position=chapter_position,
        exposition_ratio=exposition_ratio,
        info_dump_runs=info_dump_runs,
        longest_dump=longest_dump,
    )

    finding = ExpositionFinding(
        code=code,
        severity=severity,
        chapter_position=chapter_position,
        exposition_ratio=exposition_ratio,
        flashback_ratio=flashback_ratio,
        info_dump_runs=info_dump_runs,
        longest_dump_chars=longest_dump,
        worst_excerpts=worst_excerpts,
        detail=detail,
    )
    return ExpositionReport(
        chapter_position=chapter_position,
        finding=finding,
    )


def render_exposition_density_block(
    report: ExpositionReport | Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
) -> str:
    """Pre-write prompt nudge to keep exposition down."""

    if report is None:
        return ""
    if isinstance(report, Mapping):
        target = float(report.get("exposition_ratio") or 0)
    elif hasattr(report, "finding"):
        target = report.finding.exposition_ratio
    else:
        return ""

    if language.lower().startswith("zh"):
        lines = ["【铺垫节制 — 本章必须遵守】"]
        lines.append(
            "- 前 5 章铺垫占比上限 25%，6-10 章上限 35%。"
        )
        lines.append(
            "- 世界观规则只用对话/动作展示，不写'据说/传说/原来'整段。"
        )
        lines.append(
            "- 回忆/旧账信息要切碎进当下场景的细节，不连段。"
        )
        if target > 0.25:
            lines.append(
                f"- 上次产出铺垫占比 {target:.0%}，本次必须压到 25% 以下。"
            )
        return "\n".join(lines)
    return "[Exposition density target ≤25% in early chapters]"


# ---------- internals ----------


def _exposition_weight(paragraph: str) -> int:
    """Score how 'expositiony' a paragraph is. 0 = not exposition."""

    weight = 0
    for marker in _EXPOSITION_MARKERS:
        if marker in paragraph:
            weight += 1
    for heavy in _HEAVY_DUMP_PHRASES:
        if heavy in paragraph:
            weight += 3
    # Wall of text with no markers but no dialogue/action either is
    # still suspect.
    if weight == 0 and len(paragraph) >= _WALL_OF_TEXT_CHARS:
        weight = 1
    return weight


def _is_flashback_paragraph(paragraph: str) -> bool:
    flashback_starters = (
        "曾经", "那年", "那时", "彼时", "当年", "那一年",
        "三十年前", "二十三年前", "三年前", "前些年",
    )
    return any(starter in paragraph[:30] for starter in flashback_starters)


def _count_info_dump_runs(
    paragraphs: Sequence[str],
    exposition_paragraphs: Sequence[tuple[int, str, int]],
    *,
    min_run: int = 3,
) -> tuple[int, int]:
    """Count contiguous exposition runs of length ≥ min_run."""

    if not exposition_paragraphs:
        return 0, 0

    exposition_indices = sorted(p[0] for p in exposition_paragraphs)
    runs = 0
    longest_run_chars = 0
    current: list[int] = []
    for idx in exposition_indices:
        if not current or idx == current[-1] + 1:
            current.append(idx)
        else:
            if len(current) >= min_run:
                runs += 1
                run_chars = sum(len(paragraphs[i]) for i in current)
                longest_run_chars = max(longest_run_chars, run_chars)
            current = [idx]
    if len(current) >= min_run:
        runs += 1
        run_chars = sum(len(paragraphs[i]) for i in current)
        longest_run_chars = max(longest_run_chars, run_chars)
    return runs, longest_run_chars


def _classify(
    *,
    chapter_position: int,
    exposition_ratio: float,
    info_dump_runs: int,
    longest_dump: int,
) -> tuple[str, str, str]:
    """Return (severity, code, detail) based on chapter position thresholds."""

    if chapter_position <= 5:
        critical_ratio, high_ratio = 0.25, 0.18
    elif chapter_position <= 10:
        critical_ratio, high_ratio = 0.35, 0.25
    else:
        critical_ratio, high_ratio = 0.55, 0.40

    if info_dump_runs >= 2 or longest_dump >= 600:
        return (
            "critical",
            "EXPOSITION_DUMP",
            f"{info_dump_runs} info-dump run(s) detected; "
            f"longest contiguous exposition = {longest_dump} chars",
        )

    if exposition_ratio >= critical_ratio:
        return (
            "critical",
            "EXPOSITION_DUMP",
            f"exposition ratio {exposition_ratio:.0%} "
            f"exceeds chapter-{chapter_position} ceiling {critical_ratio:.0%}",
        )
    if exposition_ratio >= high_ratio:
        return (
            "high",
            "EXPOSITION_HIGH",
            f"exposition ratio {exposition_ratio:.0%} above target "
            f"{high_ratio:.0%} for chapter {chapter_position}",
        )
    return (
        "info",
        "EXPOSITION_OK",
        f"exposition ratio {exposition_ratio:.0%} within target",
    )


def _ok_report(chapter_position: int) -> ExpositionReport:
    return ExpositionReport(
        chapter_position=max(1, chapter_position),
        finding=ExpositionFinding(
            code="EXPOSITION_OK",
            severity="info",
            chapter_position=max(1, chapter_position),
            exposition_ratio=0.0,
            flashback_ratio=0.0,
            info_dump_runs=0,
            longest_dump_chars=0,
            worst_excerpts=(),
            detail="empty or invalid input — skipped",
        ),
    )


__all__ = [
    "ExpositionFinding",
    "ExpositionReport",
    "check_exposition_density",
    "render_exposition_density_block",
]
