"""Chapter-level causality and reader-desire gate for outline planning.

This is a planning gate, not a prose-style review.  Its job is to make sure a
chapter outline contains enough reader-visible cause/effect material before we
ask the writer model to produce正文.  The contract is deliberately flexible:
action, reaction, reveal, transition, and payoff chapters can satisfy the same
reader-desire axes in different ways.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import re

from bestseller.domain.workflow import ChapterOutlineBatchInput, ChapterOutlineInput

BLOCKING_SEVERITIES = {"critical", "major"}

ESSENTIAL_AXES = (
    "pressure",
    "choice_or_action",
    "next_reader_desire",
)

MIN_PRESENT_AXES = 5

GENERIC_PATTERNS: tuple[str, ...] = (
    "推动剧情",
    "推动本章",
    "推进剧情",
    "推进主线",
    "承接上文",
    "引出下文",
    "继续处理",
    "继续面对",
    "继续推进",
    "局势继续",
    "压力继续",
    "新的情况",
    "新的压力",
    "新的证据、时限或代价",
    "新的证据、时限、代价",
    "情绪复杂",
    "思考局势",
    "处理局势",
    "发展情节",
    "build tension",
    "advance the plot",
    "move the story forward",
    "new evidence, deadline, or cost",
)

PRESSURE_TOKENS: tuple[str, ...] = (
    "必须",
    "需要",
    "不得不",
    "不能",
    "无法",
    "被迫",
    "逼",
    "逼迫",
    "威胁",
    "封",
    "追杀",
    "倒计时",
    "时限",
    "阻止",
    "夺",
    "搜",
    "暴露",
    "风险",
    "禁令",
    "生效前",
    "危机",
    "陷入",
    "处死",
    "格杀",
    "囚禁",
    "警告",
    "怀疑",
    "颠覆",
    "两难",
    "deadline",
    "must",
    "threat",
    "risk",
    "before",
)

ACTION_TOKENS: tuple[str, ...] = (
    "选择",
    "决定",
    "试图",
    "前往",
    "接触",
    "调查",
    "追查",
    "获取",
    "进入",
    "读取",
    "确认",
    "接下",
    "击败",
    "潜入",
    "夺回",
    "救",
    "阻止",
    "交出",
    "撬开",
    "引出",
    "比对",
    "假意",
    "违令",
    "出手",
    "行动",
    "亮出",
    "相信",
    "触碰",
    "打开",
    "寻找",
    "完成",
    "choose",
    "decide",
    "enter",
    "find",
    "confirm",
    "confront",
    "infiltrate",
)

RESISTANCE_TOKENS: tuple[str, ...] = (
    "阻",
    "拒绝",
    "搜身",
    "追",
    "拦",
    "岔开",
    "警告",
    "怀疑",
    "不得",
    "不许",
    "逼",
    "封",
    "禁",
    "敌",
    "对手",
    "反派",
    "失败",
    "崩盘",
    "trap",
    "block",
    "refuse",
    "enemy",
    "antagonist",
)

COST_TOKENS: tuple[str, ...] = (
    "代价",
    "损失",
    "失去",
    "暴露",
    "受伤",
    "牺牲",
    "亏空",
    "记名",
    "风险",
    "两难",
    "欠下",
    "失明",
    "消耗",
    "寿元",
    "减半",
    "动摇",
    "关闭",
    "cost",
    "loss",
    "risk",
    "expose",
    "sacrifice",
)

GAIN_TOKENS: tuple[str, ...] = (
    "获得",
    "拿到",
    "确认",
    "证明",
    "发现",
    "得知",
    "看到",
    "捕捉",
    "知道",
    "明白",
    "揭",
    "线索",
    "实证",
    "资源",
    "突破",
    "真相",
    "入口",
    "侧门",
    "gain",
    "reveal",
    "discover",
    "prove",
    "clue",
)

NEXT_DESIRE_TOKENS: tuple[str, ...] = (
    "指向",
    "倒计时",
    "只剩",
    "开启",
    "棺材",
    "侧门",
    "祖坟",
    "第二",
    "是谁",
    "谁",
    "什么",
    "为何",
    "为什么",
    "如何",
    "哪",
    "是否",
    "吗",
    "？",
    "?",
    "真相",
    "目的",
    "意味着",
    "藏着",
    "敌是友",
    "读者想",
    "下一章",
    "next",
    "reader wants",
    "opens",
    "points to",
)

WEAK_ACTION_ONLY_TOKENS = {"思考", "考虑", "整理", "回忆", "观察", "等待", "处理"}


@dataclass(frozen=True)
class ChapterCausalityFinding:
    code: str
    chapter_number: int
    message: str
    severity: str = "major"
    evidence: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "chapter_number": self.chapter_number,
            "message": self.message,
            "severity": self.severity,
            "evidence": self.evidence,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ChapterCausalityResult:
    chapter_number: int
    chapter_function: str
    present_axes: dict[str, bool]
    missing_axes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "chapter_number": self.chapter_number,
            "chapter_function": self.chapter_function,
            "present_axes": dict(self.present_axes),
            "missing_axes": list(self.missing_axes),
        }


@dataclass(frozen=True)
class ChapterCausalityReport:
    gate_name: str = "chapter_causality_contract"
    findings: tuple[ChapterCausalityFinding, ...] = ()
    chapter_results: tuple[ChapterCausalityResult, ...] = ()

    @property
    def blocking_findings(self) -> tuple[ChapterCausalityFinding, ...]:
        return tuple(f for f in self.findings if f.severity in BLOCKING_SEVERITIES)

    @property
    def passed(self) -> bool:
        return not self.blocking_findings

    def to_prompt_block(self, *, language: str = "zh-CN", limit: int = 8) -> str:
        if not self.findings:
            return ""
        is_zh = language.startswith("zh")
        header = "【章节因果合同未满足】" if is_zh else "[Chapter causality contract gaps]"
        lines = [header]
        for finding in self.findings[:limit]:
            if is_zh:
                lines.append(
                    f"- 第{finding.chapter_number}章 {finding.code}: {finding.message}"
                )
            else:
                lines.append(
                    f"- Chapter {finding.chapter_number} {finding.code}: {finding.message}"
                )
        return "\n".join(lines)


def chapter_causality_report_to_dict(report: ChapterCausalityReport) -> dict[str, object]:
    return {
        "gate_name": report.gate_name,
        "passed": report.passed,
        "findings": [finding.to_dict() for finding in report.findings],
        "chapter_results": [result.to_dict() for result in report.chapter_results],
    }


def evaluate_chapter_causality_contract(
    batch: ChapterOutlineBatchInput,
) -> ChapterCausalityReport:
    findings: list[ChapterCausalityFinding] = []
    chapter_results: list[ChapterCausalityResult] = []

    previous_next_desire = ""
    for chapter in batch.chapters:
        result = _evaluate_chapter(chapter, previous_next_desire=previous_next_desire)
        chapter_results.append(result)
        findings.extend(_findings_for_result(chapter, result))
        previous_next_desire = _best_text(
            _contract_text(chapter, "next_reader_desire", "next_chapter_desire"),
            chapter.hook_description,
        )

    return ChapterCausalityReport(
        findings=tuple(findings),
        chapter_results=tuple(chapter_results),
    )


def _evaluate_chapter(
    chapter: ChapterOutlineInput,
    *,
    previous_next_desire: str = "",
) -> ChapterCausalityResult:
    contract = _contract(chapter)
    chapter_function = _clean(
        contract.get("chapter_function")
        or contract.get("function")
        or contract.get("chapter_type")
        or "unspecified"
    )

    scene_story = "; ".join(_scene_story_texts(chapter))
    state_text = _state_text(chapter)

    pressure_candidates = (
        _contract_text(chapter, "pressure", "chapter_pressure"),
        chapter.main_conflict,
        chapter.opening_situation,
        previous_next_desire,
    )
    choice_action_candidates = (
        _contract_text(
            chapter,
            "protagonist_choice",
            "choice",
            "visible_action_or_reaction",
            "visible_action",
            "reaction",
        ),
        chapter.chapter_goal,
        scene_story,
    )
    resistance_candidates = (
        _contract_text(chapter, "resistance", "obstacle", "阻力"),
        chapter.main_conflict,
        scene_story,
    )
    cost_candidates = (
        _contract_text(chapter, "cost_or_tradeoff", "cost", "tradeoff", "代价"),
        chapter.chapter_goal,
        chapter.main_conflict,
        scene_story,
    )
    gain_candidates = (
        _contract_text(chapter, "gain_or_reveal", "gain", "reveal", "收益", "揭露"),
        chapter.chapter_goal,
        chapter.hook_description,
        scene_story,
    )
    state_change_candidates = (
        _contract_text(chapter, "state_change", "state_delta", "状态变化"),
        state_text,
        chapter.chapter_goal,
    )
    next_desire_candidates = (
        _contract_text(
            chapter,
            "next_reader_desire",
            "next_desire",
            "next_chapter_desire",
            "reader_question_after",
        ),
        chapter.hook_description,
    )

    present_axes = {
        "pressure": _has_contract_axis(chapter, "pressure", "chapter_pressure")
        or any(_has_pressure(text) for text in pressure_candidates),
        "choice_or_action": _has_contract_axis(
            chapter,
            "protagonist_choice",
            "choice",
            "visible_action_or_reaction",
            "visible_action",
            "reaction",
        )
        or any(_has_choice_or_action(text) for text in choice_action_candidates),
        "resistance": _has_contract_axis(chapter, "resistance", "obstacle", "阻力")
        or any(
            _has_axis_signal(text, RESISTANCE_TOKENS)
            for text in resistance_candidates
        ),
        "cost_or_tradeoff": _has_contract_axis(
            chapter, "cost_or_tradeoff", "cost", "tradeoff", "代价"
        )
        or any(
            _has_axis_signal(text, COST_TOKENS)
            for text in cost_candidates
        ),
        "gain_or_reveal": _has_contract_axis(
            chapter, "gain_or_reveal", "gain", "reveal", "收益", "揭露"
        )
        or any(
            _has_axis_signal(text, GAIN_TOKENS)
            for text in gain_candidates
        ),
        "state_change": _has_contract_axis(
            chapter, "state_change", "state_delta", "状态变化"
        )
        or any(
            _has_state_change(chapter, text)
            for text in state_change_candidates
        ),
        "next_reader_desire": _has_contract_axis(
            chapter,
            "next_reader_desire",
            "next_desire",
            "next_chapter_desire",
            "reader_question_after",
        )
        or any(
            _has_next_reader_desire(text)
            for text in next_desire_candidates
        ),
    }
    missing_axes = tuple(axis for axis, present in present_axes.items() if not present)
    return ChapterCausalityResult(
        chapter_number=chapter.chapter_number,
        chapter_function=chapter_function,
        present_axes=present_axes,
        missing_axes=missing_axes,
    )


def _findings_for_result(
    chapter: ChapterOutlineInput,
    result: ChapterCausalityResult,
) -> list[ChapterCausalityFinding]:
    findings: list[ChapterCausalityFinding] = []
    present_count = sum(1 for present in result.present_axes.values() if present)
    if not result.present_axes["pressure"]:
        findings.append(
            ChapterCausalityFinding(
                code="CHAPTER_CAUSAL_PRESSURE_WEAK",
                chapter_number=chapter.chapter_number,
                message="本章没有足够具体的读者可见压力; 需要明确谁/什么在逼主角立刻应对。",
                evidence=_clean(chapter.main_conflict or chapter.opening_situation),
            )
        )
    if not result.present_axes["choice_or_action"]:
        findings.append(
            ChapterCausalityFinding(
                code="CHAPTER_CAUSAL_CHOICE_OR_ACTION_WEAK",
                chapter_number=chapter.chapter_number,
                message="本章缺少主角选择、行动或反应; 读者看不到主角如何改变局面。",
                evidence=_clean(chapter.chapter_goal),
            )
        )
    if not result.present_axes["next_reader_desire"]:
        findings.append(
            ChapterCausalityFinding(
                code="CHAPTER_CAUSAL_NEXT_DESIRE_WEAK",
                chapter_number=chapter.chapter_number,
                message="本章尾部没有形成具体的下一章阅读欲望。",
                evidence=_clean(chapter.hook_description),
            )
        )
    if present_count < MIN_PRESENT_AXES:
        findings.append(
            ChapterCausalityFinding(
                code="CHAPTER_CAUSAL_MINIMUM_AXES_MISSING",
                chapter_number=chapter.chapter_number,
                message=(
                    "章节因果轴不足; 至少需要压力、选择/行动、阻力、代价、收益/揭露、"
                    "状态变化、下一章欲望中的五项成立。"
                ),
                severity="critical",
                metadata={
                    "present_axes": dict(result.present_axes),
                    "present_count": present_count,
                    "required": MIN_PRESENT_AXES,
                },
            )
        )
    return findings


def _contract(chapter: ChapterOutlineInput) -> Mapping[str, object]:
    value = getattr(chapter, "causal_contract", None)
    return value if isinstance(value, Mapping) else {}


def _contract_text(chapter: ChapterOutlineInput, *keys: str) -> str:
    contract = _contract(chapter)
    parts: list[str] = []
    for key in keys:
        value = contract.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "; ".join(parts)


def _has_contract_axis(chapter: ChapterOutlineInput, *keys: str) -> bool:
    contract = _contract(chapter)
    for key in keys:
        value = contract.get(key)
        if isinstance(value, str) and _is_specific_contract_value(value):
            return True
    return False


def _scene_story_texts(chapter: ChapterOutlineInput) -> list[str]:
    texts: list[str] = []
    for scene in chapter.scenes:
        purpose = scene.purpose if isinstance(scene.purpose, dict) else {}
        story = purpose.get("story")
        if isinstance(story, str) and story.strip():
            texts.append(story.strip())
    return texts


def _state_text(chapter: ChapterOutlineInput) -> str:
    parts: list[str] = []
    for scene in chapter.scenes:
        for value in (scene.entry_state, scene.exit_state):
            if isinstance(value, dict) and value:
                parts.append(str(value))
    return "; ".join(parts)


def _has_pressure(text: str) -> bool:
    return _is_specific(text) and _has_any(text, PRESSURE_TOKENS)


def _has_choice_or_action(text: str) -> bool:
    if not _is_specific(text):
        return False
    if not _has_any(text, ACTION_TOKENS):
        return False
    stripped = _clean(text)
    if any(token in stripped for token in WEAK_ACTION_ONLY_TOKENS) and not (
        "选择" in stripped or "决定" in stripped or "必须" in stripped
    ):
        return False
    return True


def _has_next_reader_desire(text: str) -> bool:
    if not _is_specific(text):
        return False
    if _has_any(text, NEXT_DESIRE_TOKENS):
        return True
    return _has_any(text, PRESSURE_TOKENS) and _has_any(text, COST_TOKENS + GAIN_TOKENS)


def _has_state_change(chapter: ChapterOutlineInput, text: str) -> bool:
    if _is_specific(text) and _has_any(
        text,
        ("变成", "从", "获得", "失去", "掌握", "暴露", "更", "state", "change"),
    ):
        return True
    for scene in chapter.scenes:
        if scene.entry_state and scene.exit_state and scene.entry_state != scene.exit_state:
            return True
    return False


def _has_axis_signal(text: str, tokens: Iterable[str]) -> bool:
    return _is_specific(text) and _has_any(text, tokens)


def _is_specific(text: str) -> bool:
    value = _clean(text)
    if len(value) < 8:
        return False
    if _looks_generic(value):
        return False
    return True


def _is_specific_contract_value(text: str) -> bool:
    value = _clean(text)
    if len(value) < 4:
        return False
    if _looks_generic(value):
        return False
    return True


def _looks_generic(text: str) -> bool:
    lowered = text.lower()
    if any(pattern.lower() in lowered for pattern in GENERIC_PATTERNS):
        return True
    if re.fullmatch(r"[\w\s]*pressure[\w\s]*", lowered):
        return True
    return False


def _has_any(text: str, tokens: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)


def _best_text(*values: object) -> str:
    parts: list[str] = []
    for value in values:
        text = _clean(value)
        if text:
            parts.append(text)
    return "; ".join(parts)


def _clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
