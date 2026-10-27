"""Chapter seam continuity gate.

When a chapter ends on an unresolved hook ("surrounded by guards", "the door
swung open"), the next chapter's opening must explicitly resolve, continue, or
account for that thread. Without this gate, the writer can silently jump to a
new scene and the cliffhanger is wasted -- one of the largest single-chapter
retention drops we observe in production novels.

Public API:

* ``extract_open_threads(prev_chapter_tail)`` -- run heuristics over the last
  ~800 chars of the previous chapter and emit a list of ``OpenThread`` records
  (location / immediate_threat / body_state / unanswered_question).
* ``validate_chapter_seam(prev_tail, current_opening)`` -- check whether each
  open thread is acknowledged inside the first ``opening_window_chars`` chars
  of the current chapter, returning ``SeamReport``.

All checks are local computation -- zero LLM cost. The output feeds critic's
``chapter_seam_continuity`` dimension and, on failure, drives editor's
``chapter_seam_bridge_paragraph`` repair action.

Participant presence is unsupported here: name-like text does not prove
that an actor was present at the chapter boundary. Actor continuity remains
part of the primary cited review and explicit chapter seam contracts.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Heuristic vocabularies (Chinese-first; English fallback)
# ---------------------------------------------------------------------------

_LOCATION_HINTS = (
    "丹房", "藏经阁", "演武场", "宿舍", "木屋", "山门", "宗门", "后山", "禁地",
    "废墟", "残骸", "库房", "山道", "崖", "悬崖", "丹田", "经脉", "城", "街", "府",
    "院", "门口", "门外", "屋内", "殿", "塔", "洞", "穴", "林", "湖", "河", "桥",
    "码头", "客栈", "酒楼", "校场", "刑场",
)

# An isolated character (e.g. a glance, a lock or a grip) cannot establish
# immediate danger. Require an explicit attack or coercive-action predicate.
_THREAT_PREDICATE_RE = re.compile(
    r"(?P<surrounded>围住|围在)|"
    r"围困|追杀|追击|袭击|刺杀|扑杀|砍向|刺向|杀来|"
    r"绑架|劫持|挟持|胁迫|拔刀|拔剑|持枪|封锁退路|堵住去路"
)
_SURROUND_DANGER_RE = re.compile(
    r"持刀|持枪|拔刀|拔剑|刀锋|剑锋|枪口|追兵|卫兵|守卫|歹徒|刺客|敌军|匪徒|执法|"
    r"挟持|退路|逃路|不得离开|不准离开|不许离开|杀气"
)
_NEGATED_THREAT_PREFIX_RE = re.compile(
    r"(?:没有|并未|并不|不再|未曾|无须|停止|放弃)[^，,。！？!?；;\n]{0,5}$"
)
_RESOLVED_THREAT_SUFFIX_RE = re.compile(r"(?:已经|早已)?(?:停止|结束|解除)")


_BODY_STATE_KEYWORDS = (
    "受伤", "重伤", "断", "骨碎", "流血", "昏迷", "晕厥", "失血", "失力", "虚脱",
    "经脉", "走火入魔", "中毒", "封印", "禁制", "封禁", "封死", "动弹不得",
    "无法动弹", "无力", "瘫", "倒下", "跪下",
)

_QUESTION_END_PUNCT = ("？", "?")

_QUESTION_PATTERN = re.compile(r"[^。！!？?…\n]{4,40}[？?]")

# Latin POV-friendly fallbacks (rough heuristics, used when CJK density < 30%)
_LATIN_THREAT_RE = re.compile(
    r"\b(surround|trap|corner|chase|hunt|capture|seize|bind|pin|lock|seal)\w*\b",
    re.IGNORECASE,
)
_LATIN_BODY_RE = re.compile(
    r"\b(wound|bleed|faint|collapse|paralyz|immobil|broken|fractur|poison|seal)\w*\b",
    re.IGNORECASE,
)

# Resolution markers the OPENING of the next chapter may use to validly "release"
# an open thread without on-screen continuation.
_TIME_SKIP_MARKERS = (
    "半个时辰后", "一炷香后", "片刻后", "翌日", "次日", "三日后", "三天后",
    "七日后", "次日清晨", "天亮", "黎明", "黄昏", "傍晚", "入夜", "深夜",
    "醒来", "醒过来", "睁眼", "睁开眼", "再睁眼",
    # English fallbacks
    "later", "the next day", "hours later", "by morning", "by dawn",
    "when he woke", "when she woke", "when they woke",
)

_RELOCATION_MARKERS = (
    "被", "拖", "拽", "扛", "背", "带回", "带到", "押", "押到", "押往", "送至",
    "送回", "运至", "醒来时", "再醒来", "睁眼时", "脱身后", "逃出后",
)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


class ThreadKind(str, Enum):
    LOCATION = "location"
    PARTICIPANT = "participant"
    IMMEDIATE_THREAT = "immediate_threat"
    BODY_STATE = "body_state"
    UNANSWERED_QUESTION = "unanswered_question"


@dataclass(frozen=True)
class OpenThread:
    """A continuity thread extracted from the end of a chapter."""

    kind: ThreadKind
    marker: str          # the exact substring that surfaced this thread
    evidence: str        # surrounding context (<=80 chars) for explainability


@dataclass(frozen=True)
class ThreadResolution:
    """How (if at all) the next chapter's opening handles a thread."""

    thread: OpenThread
    resolution: str      # one of: continuation / skip / relocation / on_screen / silent_drop
    evidence: str        # opening substring that demonstrates resolution; "" if silent_drop


@dataclass(frozen=True)
class SeamReport:
    """Raw observations with supported threads exposed for enforcement.

    Keep legacy participant observations intact for inspection, but never
    give them authority through scores, findings or repair instructions.
    """

    open_threads: tuple[OpenThread, ...]
    resolutions: tuple[ThreadResolution, ...]
    silent_drops: tuple[ThreadResolution, ...] = field(default_factory=tuple)

    @property
    def eligible_open_threads(self) -> tuple[OpenThread, ...]:
        return tuple(t for t in self.open_threads if t.kind != ThreadKind.PARTICIPANT)

    @property
    def eligible_resolutions(self) -> tuple[ThreadResolution, ...]:
        return tuple(r for r in self.resolutions if r.thread.kind != ThreadKind.PARTICIPANT)

    @property
    def eligible_silent_drops(self) -> tuple[ThreadResolution, ...]:
        return tuple(d for d in self.silent_drops if d.thread.kind != ThreadKind.PARTICIPANT)

    @property
    def participant_coverage(self) -> dict[str, str]:
        return {"status": "unsupported", "reason": "no_source_bound_end_presence"}

    @property
    def passed(self) -> bool:
        """No supported drops; does not certify participant continuity."""
        return not self.eligible_silent_drops

    @property
    def score(self) -> float:
        """Supported-thread score; unsupported participants are excluded."""
        threads = self.eligible_open_threads
        if not threads:
            return 1.0
        resolved = len(threads) - len(self.eligible_silent_drops)
        return max(0.0, resolved / len(threads))


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _context_around(text: str, idx: int, span: int = 40) -> str:
    """Return up to ``span`` chars around ``idx`` for evidence display."""
    start = max(0, idx - span)
    end = min(len(text), idx + span)
    return text[start:end].replace("\n", " ").strip()


def _is_cjk_dominant(text: str) -> bool:
    if not text:
        return False
    stripped = text.replace(" ", "")
    if not stripped:
        return False
    cjk = sum(1 for c in stripped if "一" <= c <= "鿿")
    return cjk / len(stripped) > 0.3


def extract_open_threads(prev_chapter_tail: str) -> list[OpenThread]:
    """Surface open continuity threads from the end of the prior chapter.

    The input should already be the last ~600-1000 chars of the previous
    chapter (use ``build_prior_chapter_tail`` upstream if you have the full
    chapter). The function works on the tail because cliffhangers live there.
    """
    text = (prev_chapter_tail or "").strip()
    if not text:
        return []

    threads: list[OpenThread] = []
    seen_markers: set[tuple[ThreadKind, str]] = set()

    def add(kind: ThreadKind, marker: str, idx: int) -> None:
        key = (kind, marker)
        if key in seen_markers:
            return
        seen_markers.add(key)
        threads.append(OpenThread(kind=kind, marker=marker, evidence=_context_around(text, idx)))

    # 1. Location: take the right-most location hint in the entire tail.
    # The "last third" filter from earlier prototypes was too aggressive for
    # short tails and routinely missed the active scene's location.
    best_loc: tuple[str, int] | None = None
    for word in _LOCATION_HINTS:
        idx = text.rfind(word)
        if idx >= 0 and (best_loc is None or idx > best_loc[1]):
            best_loc = (word, idx)
    if best_loc is not None:
        add(ThreadKind.LOCATION, best_loc[0], best_loc[1])

    # 2. Participant presence is unsupported. A spelling or name catalogue
    # cannot establish who was actually present at this chapter boundary.

    # 3. Immediate threat: scan whole tail for threat verbs in the last 200 chars
    threat_window = text[-200:]
    threat_offset = len(text) - len(threat_window)
    cjk = _is_cjk_dominant(text)
    if cjk:
        for match in reversed(list(_THREAT_PREDICATE_RE.finditer(threat_window))):
            prefix = threat_window[max(0, match.start() - 12):match.start()]
            suffix = threat_window[match.end():]
            if match.group("surrounded"):
                clause = (
                    re.split(r"[，,。！？!?；;\n]", threat_window[:match.start()])[-1]
                    + match.group(0)
                    + re.split(r"[，,。！？!?；;\n]", suffix, maxsplit=1)[0]
                )
                if not _SURROUND_DANGER_RE.search(clause):
                    continue
            if _NEGATED_THREAT_PREFIX_RE.search(prefix) or _RESOLVED_THREAT_SUFFIX_RE.match(suffix):
                continue
            marker = match.group("surrounded") or match.group(0)
            add(ThreadKind.IMMEDIATE_THREAT, marker, threat_offset + match.start())
            break
    else:
        m = _LATIN_THREAT_RE.search(threat_window)
        if m:
            add(ThreadKind.IMMEDIATE_THREAT, m.group(0).lower(), threat_offset + m.start())

    # 4. Body state: scan whole tail
    if cjk:
        for marker in _BODY_STATE_KEYWORDS:
            idx = text.rfind(marker)
            if idx >= 0 and idx >= len(text) - 400:  # must be near the end
                add(ThreadKind.BODY_STATE, marker, idx)
                break
    else:
        m = _LATIN_BODY_RE.search(text[-400:])
        if m:
            add(ThreadKind.BODY_STATE, m.group(0).lower(), len(text) - 400 + m.start())

    # 5. Unanswered question: explicit question-ending punctuation in last 300 chars
    q_window = text[-300:]
    q_offset = len(text) - len(q_window)
    for m in _QUESTION_PATTERN.finditer(q_window):
        question = m.group(0).strip()
        # Skip rhetorical inner-thought tags
        if any(tag in question for tag in ("他想", "她想", "心想", "暗想")):
            continue
        add(ThreadKind.UNANSWERED_QUESTION, question[:30], q_offset + m.start())
        break

    return threads


# ---------------------------------------------------------------------------
# Resolution check
# ---------------------------------------------------------------------------


def _classify_resolution(
    thread: OpenThread,
    opening: str,
) -> tuple[str, str]:
    """Return (resolution_kind, evidence_snippet). ``silent_drop`` if none."""
    opening_lower = opening.lower()

    # 1. Direct continuation: marker appears verbatim in the opening
    if thread.marker and thread.marker in opening:
        idx = opening.find(thread.marker)
        return "continuation", _context_around(opening, idx)

    # 2. Time skip
    for marker in _TIME_SKIP_MARKERS:
        if marker in opening or marker in opening_lower:
            return "skip", marker

    # 3. Relocation explanation
    for marker in _RELOCATION_MARKERS:
        if marker in opening:
            return "relocation", marker

    # 4. Kind-specific fallbacks
    if thread.kind == ThreadKind.IMMEDIATE_THREAT:
        if any(cue in opening for cue in ("脱险", "逃出", "甩开", "甩脱", "摆脱", "突围", "撤离", "退去")):
            return "on_screen", "脱险词"
    if thread.kind == ThreadKind.BODY_STATE:
        if any(cue in opening for cue in ("伤口", "疼", "痛", "包扎", "止血", "倒下", "醒来", "缓过", "气息", "胸口")):
            return "on_screen", "身体感知延续"
    if thread.kind == ThreadKind.UNANSWERED_QUESTION:
        # If the question is acknowledged conceptually -- harder to detect; we
        # require a noun overlap of >=2 chars from the question
        for token_len in (4, 3, 2):
            for i in range(len(thread.marker) - token_len + 1):
                token = thread.marker[i : i + token_len]
                if re.match(r"^[一-鿿]+$", token) and token in opening:
                    return "on_screen", token
    if thread.kind == ThreadKind.LOCATION:
        # A new location word at the start often means an explicit relocation
        for word in _LOCATION_HINTS:
            if word == thread.marker:
                continue
            if word in opening[:120]:
                # Reader can see we've moved; treat as relocation-by-display
                return "relocation", word

    return "silent_drop", ""


def validate_chapter_seam(
    prev_chapter_tail: str,
    current_opening: str,
    *,
    opening_window_chars: int = 300,
) -> SeamReport:
    """Validate that ``current_opening`` resolves the open threads from ``prev_chapter_tail``.

    Args:
        prev_chapter_tail: last 600-1000 chars of the previous chapter
            (typically what ``build_prior_chapter_tail`` produces minus its header).
        current_opening: full text of the current chapter; only the first
            ``opening_window_chars`` are inspected.
        opening_window_chars: how many chars of the current chapter to scan.

    Returns:
        ``SeamReport`` -- ``passed`` means zero supported silent drops;
        participant continuity remains unsupported.
    """
    threads = extract_open_threads(prev_chapter_tail)
    if not threads:
        return SeamReport(open_threads=(), resolutions=())

    opening_window = (current_opening or "")[:opening_window_chars]
    resolutions: list[ThreadResolution] = []
    for thread in threads:
        kind, evidence = _classify_resolution(thread, opening_window)
        resolutions.append(ThreadResolution(thread=thread, resolution=kind, evidence=evidence))

    drops = [r for r in resolutions if r.resolution == "silent_drop"]
    return SeamReport(
        open_threads=tuple(threads),
        resolutions=tuple(resolutions),
        silent_drops=tuple(drops),
    )


# ---------------------------------------------------------------------------
# Repair prompt helper
# ---------------------------------------------------------------------------


def build_seam_bridge_repair_prompt(report: SeamReport) -> str:
    """Render an editor-facing instruction describing what bridge paragraph to insert.

    The output is wrapped by callers in the standard ``=== reference only ===``
    fence per [invariants.md] before being injected into editor prompts.
    """
    if report.passed:
        return ""

    bullets: list[str] = []
    for drop in report.eligible_silent_drops:
        thread = drop.thread
        if thread.kind == ThreadKind.IMMEDIATE_THREAT:
            bullets.append(f"- 前章末尾尚有威胁未处理（标志：「{thread.marker}」）；请在本章开篇 200-300 字内交代主角如何脱身 / 被压制 / 暂时摆脱该威胁。")
        elif thread.kind == ThreadKind.LOCATION:
            bullets.append(f"- 前章末尾主角位于「{thread.marker}」；本章开篇若已换场景，请明示空间转场（被拖走 / 主动脱身 / 时间跳跃后醒来）。")
        elif thread.kind == ThreadKind.BODY_STATE:
            bullets.append(f"- 前章末尾主角身体处于「{thread.marker}」状态；本章开篇请承接该身体感知或交代缓和过程。")
        elif thread.kind == ThreadKind.UNANSWERED_QUESTION:
            bullets.append(f"- 前章抛出疑问「{thread.marker}…」；本章开篇前 300 字内至少回应该疑问的存在（不必揭谜）。")

    header = "【章节断点修复任务】\n以下连续性线索在本章开篇被遗漏，请插入 100-300 字的 bridge 段落收尾后再进入正文：\n"
    return header + "\n".join(bullets)


__all__ = [
    "ThreadKind",
    "OpenThread",
    "ThreadResolution",
    "SeamReport",
    "extract_open_threads",
    "validate_chapter_seam",
    "build_seam_bridge_repair_prompt",
]
