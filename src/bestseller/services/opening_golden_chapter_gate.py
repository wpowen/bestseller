"""Opening golden-chapter gate — 网文「黄金一章」工业标准的确定性验收.

Deterministic (no LLM) soft checks that apply ONLY to chapters 1-3:

1. 主角前置 (ch1 only): protagonist name must appear within the first
   300 characters.
2. 期待点信号: the first 1000 characters must contain a conflict /
   anomaly / dialogue signal (dialogue quotes, question or negation /
   turn words, or a 【】 system-panel marker) — deliberately loose.
3. 信息节流 (finding on ch1 only): density of fresh "proper nouns"
   (《》 titles, 【】 system terms, high-frequency 2-4 char name
   candidates). Threshold is loose (12, vs 6 at design layer) because
   prose legitimately carries cast / place names.
4. 章末总结体禁止: the last two paragraphs must not hit
   sublimation / preview patterns (「他终于明白」「注定」「更大的风暴」…).
5. 章末钩子存在: the last paragraph should end on dialogue, an action
   interruption, a suspense question, or new information — flagged via
   the inverse heuristic (pure lyrical/summary close with no dialogue
   and no action verb).
6. 开篇禁忌: the first 200 characters must not be a static weather /
   scenery opening with no character action and no dialogue.

This is an ``advanced`` tier gate (see ``gate_registry``): findings are
warnings only and must NEVER block the chapter or trigger
``machine_repair_required`` (WS-C policy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from bestseller.services.checker_schema import CheckerIssue, CheckerReport
from bestseller.services.progress_context import emit_gate_result

#: Chapters covered by the golden-opening standard.
OPENING_CHAPTER_RANGE = (1, 2, 3)
#: 主角前置 window (ch1).
PROTAGONIST_WINDOW_CHARS = 300
#: 期待点信号 window.
TENSION_WINDOW_CHARS = 1000
#: 开篇禁忌 window.
STATIC_OPENING_WINDOW_CHARS = 200
#: 信息节流 threshold — looser than the design-layer 6 because prose
#: legitimately introduces cast/place names.
NEW_TERM_THRESHOLD_CH1 = 12
#: Minimum occurrences for a 2-4 char CJK gram to count as a proper-noun
#: candidate (heuristic — metrics need not be perfect).
NAME_CANDIDATE_MIN_FREQ = 5

_DIALOGUE_QUOTE_CHARS = ("“", "”", "‘", "’", "「", "」", "『", "』")

#: Question / negation / turn words that signal conflict or anomaly.
_TENSION_WORD_PATTERN = re.compile(
    r"[？?！!]"
    r"|怎么|为什么|什么|难道|竟然|居然|突然|忽然"
    r"|不对|不是|不能|不行|没有|没想到"
    r"|但是?|可是|却|偏偏|然而"
)

#: Sublimation / preview patterns forbidden in the closing paragraphs.
_ENDING_SUMMARY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:他|她)?终于明白"),
    re.compile(r"注定"),
    re.compile(r"更大的(?:风暴|危机|阴谋)"),
    re.compile(r"这一夜[^。！？!?\n]{0,8}无眠"),
    re.compile(r"人生就是"),
    re.compile(r"命运的(?:齿轮|车轮)"),
    re.compile(r"(?:这|那)一切[^。！？!?\n]{0,10}才刚刚开始"),
    re.compile(r"故事[^。！？!?\n]{0,6}才刚刚开始"),
    re.compile(r"等待着(?:他|她|他们)的"),
    re.compile(r"殊不知"),
    re.compile(r"谁也(?:没有|不)(?:想到|知道|料到)"),
)

#: Action verbs — used both for the closing-hook heuristic and for the
#: static-opening check ("人物动作" presence).
_ACTION_VERBS = (
    "走", "跑", "推", "拉", "抓", "打", "摔", "站", "坐", "拿",
    "扔", "跳", "冲", "砸", "踢", "握", "抬", "睁", "喊", "叫",
    "说", "问", "答", "按", "翻", "掏", "摸", "敲", "响", "撞",
    "停", "掉", "滚", "拽", "甩", "签", "写", "盯", "转身", "回头",
    "伸手", "传来", "炸", "劈", "扑", "咬", "退",
)

#: Lyrical / summary cue words — a closing paragraph dominated by these
#: with no dialogue and no action reads as a wrap-up, not a hook.
_LYRICAL_CUES = (
    "平静", "温暖", "幸福", "岁月", "时光", "心里", "心中", "微笑",
    "安然", "睡去", "美好", "希望", "感慨", "回忆", "永远", "宁静",
)

#: Suspense / new-information closers that count as a hook.
_HOOK_SIGNAL_PATTERN = re.compile(r"[？?！!]|……|…|【|》|：|:")

#: Weather / scenery words for the static-opening taboo.
_SCENERY_WORDS = (
    "天空", "阳光", "夕阳", "朝阳", "晨光", "清晨", "黄昏", "夜色",
    "月光", "月亮", "星空", "雨", "雪", "风", "云", "雾",
    "春天", "夏天", "秋天", "冬天", "远山", "群山", "山峦", "树",
    "街道", "城市", "村庄", "原野", "大海", "湖面", "远处",
)

#: Common bigrams that must never be counted as proper-noun candidates.
_NAME_STOPWORDS = frozenset(
    {
        "什么", "没有", "一个", "自己", "这个", "那个", "现在", "知道",
        "时候", "已经", "还是", "怎么", "不是", "就是", "可以", "出来",
        "起来", "看着", "说道", "这么", "那么", "一下", "一声", "不能",
        "这里", "那里", "今天", "明天", "昨天", "一样", "东西", "声音",
    }
)
#: Function characters — a gram containing one is not a name candidate.
_NAME_STOP_CHARS = frozenset("的了是在和与就都也有没不一这那你我他她它们个着把被从向对里上下")


@dataclass(frozen=True)
class OpeningGoldenChapterFinding:
    code: str
    severity: str  # "advice" | "warn"
    category: str
    evidence: str


@dataclass(frozen=True)
class OpeningGoldenChapterReport:
    chapter_position: int
    findings: tuple[OpeningGoldenChapterFinding, ...] = ()
    metrics: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.findings

    def to_checker_report(self) -> CheckerReport:
        issues = [
            CheckerIssue(
                id=f.code,
                type="opening_golden_chapter",
                severity="medium" if f.severity == "warn" else "low",
                location=f"ch{self.chapter_position}",
                description=f"黄金一章验收未达标：{f.category}",
                suggestion=_SUGGESTIONS.get(f.code, "按黄金一章标准修整开篇/章末。"),
                can_override=True,
            )
            for f in self.findings
        ]
        warn_count = sum(1 for f in self.findings if f.severity == "warn")
        advice_count = len(self.findings) - warn_count
        score = max(0, 100 - warn_count * 12 - advice_count * 5)
        return CheckerReport(
            agent="opening-golden-chapter-gate",
            chapter=self.chapter_position,
            overall_score=score,
            passed=self.passed,
            issues=tuple(issues),
            metrics={**self.metrics, "finding_count": len(self.findings)},
            summary=(
                "opening-golden-chapter passed"
                if self.passed
                else f"opening-golden-chapter found {len(issues)} issue(s)"
            ),
        )


_SUGGESTIONS: dict[str, str] = {
    "OPENING_PROTAGONIST_LATE": "把主角名字提到第一段——读者前300字必须知道跟谁走。",
    "OPENING_NO_TENSION_SIGNAL": "前1000字内放一个冲突/异常/对话信号，给读者一个期待点。",
    "OPENING_INFO_OVERLOAD": "砍掉首章一半专有名词：先让一个概念站住，再上下一个。",
    "ENDING_SUMMARY_TONE": "删掉章末升华/预告句，落在一个具体的画面或动作上。",
    "ENDING_HOOK_MISSING": "章末改成对话截断、动作中断、悬念问句或新信息揭示收尾。",
    "OPENING_STATIC_SCENERY": "开篇别写天气和风景，第一句就让人物动起来或开口。",
}


def check_opening_golden_chapter_gate(
    text: str,
    *,
    chapter_position: int,
    protagonist_name: str | None = None,
) -> OpeningGoldenChapterReport:
    """Run the golden-opening acceptance checks on one chapter's prose.

    Chapters outside positions 1-3 (and empty text) pass immediately.
    ``protagonist_name`` is optional — when the caller cannot resolve it,
    the 主角前置 check is skipped instead of failing.
    """

    if chapter_position not in OPENING_CHAPTER_RANGE or not text:
        return OpeningGoldenChapterReport(
            chapter_position=chapter_position,
            metrics={"skipped": 1},
        )

    findings: list[OpeningGoldenChapterFinding] = []
    metrics: dict[str, int] = {"skipped": 0}

    findings.extend(
        _check_protagonist_early(text, chapter_position, protagonist_name)
    )
    findings.extend(_check_tension_signal(text))
    new_term_count, overload_findings = _check_info_throttle(text, chapter_position)
    metrics["new_term_count"] = new_term_count
    findings.extend(overload_findings)
    findings.extend(_check_ending_summary(text))
    findings.extend(_check_ending_hook(text))
    findings.extend(_check_static_opening(text))

    for finding in findings:
        metrics[finding.code] = metrics.get(finding.code, 0) + 1

    report = OpeningGoldenChapterReport(
        chapter_position=chapter_position,
        findings=tuple(findings),
        metrics=metrics,
    )
    warn_count = sum(1 for f in findings if f.severity == "warn")
    emit_gate_result(
        "opening_golden_chapter_gate",
        verdict="pass" if report.passed else "warn_only",
        severity="medium" if warn_count else ("info" if not findings else "low"),
        score=report.to_checker_report().overall_score,
        reasons=[f.code for f in findings],
        chapter=chapter_position,
    )
    return report


# ---------------------------------------------------------------------------
# Individual checks (pure functions).
# ---------------------------------------------------------------------------


def _check_protagonist_early(
    text: str, chapter_position: int, protagonist_name: str | None
) -> list[OpeningGoldenChapterFinding]:
    """主角前置 — ch1 only; skipped when the name is unknown."""

    name = (protagonist_name or "").strip()
    if chapter_position != 1 or not name:
        return []
    window = text[:PROTAGONIST_WINDOW_CHARS]
    if name in window:
        return []
    return [
        OpeningGoldenChapterFinding(
            code="OPENING_PROTAGONIST_LATE",
            severity="warn",
            category="主角前置",
            evidence=window[:80].replace("\n", " "),
        )
    ]


def _check_tension_signal(text: str) -> list[OpeningGoldenChapterFinding]:
    """期待点信号 — loose by design to avoid false kills."""

    window = text[:TENSION_WINDOW_CHARS]
    has_dialogue = any(q in window for q in _DIALOGUE_QUOTE_CHARS)
    has_panel = "【" in window
    has_tension_word = bool(_TENSION_WORD_PATTERN.search(window))
    if has_dialogue or has_panel or has_tension_word:
        return []
    return [
        OpeningGoldenChapterFinding(
            code="OPENING_NO_TENSION_SIGNAL",
            severity="warn",
            category="期待点信号",
            evidence=window[:80].replace("\n", " "),
        )
    ]


def _count_new_terms(text: str) -> int:
    """Heuristic count of distinct fresh proper nouns in the chapter."""

    terms: set[str] = set()
    for match in re.finditer(r"《([^《》\n]{1,20})》", text):
        terms.add(f"《{match.group(1)}》")
    for match in re.finditer(r"【([^【】\n]{1,40})】", text):
        head = re.split(r"[：:·，。、；,]", match.group(1))[0].strip()
        if head:
            terms.add(f"【{head}】")
    # High-frequency 2-4 char name candidates: count CJK bigrams, extend
    # greedily while the longer gram keeps (almost) the same frequency.
    cjk_runs = re.findall(r"[一-鿿]{2,}", text)
    gram_counts: dict[str, int] = {}
    for run in cjk_runs:
        for i in range(len(run) - 1):
            gram = run[i : i + 2]
            gram_counts[gram] = gram_counts.get(gram, 0) + 1
    candidates: set[str] = set()
    for gram, count in gram_counts.items():
        if count < NAME_CANDIDATE_MIN_FREQ:
            continue
        if gram in _NAME_STOPWORDS:
            continue
        if any(ch in _NAME_STOP_CHARS for ch in gram):
            continue
        candidates.add(gram)
    # Merge bigrams that are fragments of a longer recurring name (e.g.
    # 哮天 / 天犬 → 哮天犬) so one name is not double counted.
    merged: set[str] = set()
    consumed: set[str] = set()
    for gram in sorted(candidates):
        if gram in consumed:
            continue
        extended = gram
        for other in candidates:
            if other == extended or other in consumed:
                continue
            if other[0] == extended[-1] and len(extended) < 4:
                joined = extended + other[1]
                if text.count(joined) >= NAME_CANDIDATE_MIN_FREQ:
                    consumed.add(other)
                    extended = joined
        merged.add(extended)
    terms.update(merged)
    return len(terms)


def _check_info_throttle(
    text: str, chapter_position: int
) -> tuple[int, list[OpeningGoldenChapterFinding]]:
    """信息节流 — metric for ch1-3, finding only on ch1."""

    count = _count_new_terms(text)
    if chapter_position != 1 or count <= NEW_TERM_THRESHOLD_CH1:
        return count, []
    return count, [
        OpeningGoldenChapterFinding(
            code="OPENING_INFO_OVERLOAD",
            severity="warn",
            category="信息节流",
            evidence=f"首章新专有名词约{count}个（阈值{NEW_TERM_THRESHOLD_CH1}）",
        )
    ]


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]


def _check_ending_summary(text: str) -> list[OpeningGoldenChapterFinding]:
    """章末总结体禁止 — last two paragraphs."""

    paras = _paragraphs(text)
    if not paras:
        return []
    tail = "\n".join(paras[-2:])
    findings: list[OpeningGoldenChapterFinding] = []
    for pattern in _ENDING_SUMMARY_PATTERNS:
        match = pattern.search(tail)
        if match is None:
            continue
        start = max(0, match.start() - 20)
        findings.append(
            OpeningGoldenChapterFinding(
                code="ENDING_SUMMARY_TONE",
                severity="warn",
                category="章末总结体",
                evidence=tail[start : match.end() + 20].replace("\n", " "),
            )
        )
    return findings


def _check_ending_hook(text: str) -> list[OpeningGoldenChapterFinding]:
    """章末钩子存在 — inverse heuristic on the last paragraph."""

    paras = _paragraphs(text)
    if not paras:
        return []
    last = paras[-1]
    has_dialogue = any(q in last for q in _DIALOGUE_QUOTE_CHARS)
    has_action = any(verb in last for verb in _ACTION_VERBS)
    has_signal = bool(_HOOK_SIGNAL_PATTERN.search(last))
    is_lyrical = any(cue in last for cue in _LYRICAL_CUES)
    if has_dialogue or has_action or has_signal:
        return []
    if not is_lyrical:
        # No hook signal but also not clearly lyrical/summary — too
        # ambiguous to flag (loose by design).
        return []
    return [
        OpeningGoldenChapterFinding(
            code="ENDING_HOOK_MISSING",
            severity="advice",
            category="章末钩子",
            evidence=last[-80:].replace("\n", " "),
        )
    ]


def _check_static_opening(text: str) -> list[OpeningGoldenChapterFinding]:
    """开篇禁忌 — static weather/scenery opening with no人物动作/对话."""

    window = text[:STATIC_OPENING_WINDOW_CHARS]
    head = window[:30]
    starts_with_scenery = any(
        head.startswith(word) or head.find(word) in range(0, 6)
        for word in _SCENERY_WORDS
    )
    if not starts_with_scenery:
        return []
    has_dialogue = any(q in window for q in _DIALOGUE_QUOTE_CHARS)
    has_action = any(verb in window for verb in _ACTION_VERBS)
    if has_dialogue or has_action:
        return []
    return [
        OpeningGoldenChapterFinding(
            code="OPENING_STATIC_SCENERY",
            severity="warn",
            category="开篇禁忌",
            evidence=window[:80].replace("\n", " "),
        )
    ]


__all__ = [
    "OpeningGoldenChapterFinding",
    "OpeningGoldenChapterReport",
    "check_opening_golden_chapter_gate",
]
