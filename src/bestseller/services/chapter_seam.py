"""Chapter seam continuity gate.

When a chapter ends on an unresolved hook ("surrounded by guards", "the door
swung open"), the next chapter's opening must explicitly resolve, continue, or
account for that thread. Without this gate, the writer can silently jump to a
new scene and the cliffhanger is wasted -- one of the largest single-chapter
retention drops we observe in production novels.

Public API:

* ``extract_open_threads(prev_chapter_tail)`` -- run heuristics over the last
  ~800 chars of the previous chapter and emit a list of ``OpenThread`` records
  (location / participants / immediate_threat / body_state / unanswered_question).
* ``validate_chapter_seam(prev_tail, current_opening)`` -- check whether each
  open thread is acknowledged inside the first ``opening_window_chars`` chars
  of the current chapter, returning ``SeamReport``.

All checks are local computation -- zero LLM cost. The output feeds critic's
``chapter_seam_continuity`` dimension and, on failure, drives editor's
``chapter_seam_bridge_paragraph`` repair action.
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

_THREAT_VERBS = (
    "围", "围住", "围困", "封", "封住", "拦", "截", "堵", "追", "扑", "袭", "杀",
    "斩", "刺", "捉", "擒", "锁", "缚", "抓", "扣", "审", "逼", "迫", "胁", "胁迫",
    "挡", "挟", "盯", "监视", "盯上", "盯梢",
)

_BODY_STATE_KEYWORDS = (
    "受伤", "重伤", "断", "骨碎", "流血", "昏迷", "晕厥", "失血", "失力", "虚脱",
    "经脉", "走火入魔", "中毒", "封印", "禁制", "封禁", "封死", "动弹不得",
    "无法动弹", "无力", "瘫", "倒下", "跪下",
)

_QUESTION_END_PUNCT = ("？", "?")

_QUESTION_PATTERN = re.compile(r"[^。！!？?…\n]{4,40}[？?]")

# Common 2-3 char prose tokens that LOOK like names but aren't (must be kept
# in sync with the same list in :mod:`deduplication`).
_NAME_STOPWORDS = frozenset({
    "他们", "她们", "你们", "我们", "自己", "别人", "众人", "众弟", "那人", "这人",
    "门口", "门外", "屋内", "屋外", "丹房", "藏经", "演武", "宿舍", "院子", "山门",
    "庄严", "突然", "忽然", "刚才", "片刻", "片晌", "瞬间", "果然", "终于", "原来",
    "其实", "其中", "另一", "其他", "其余", "那个", "这个", "什么", "怎么", "怎样",
    "为什么", "怎么办", "没料", "没想", "想到", "似乎", "仿佛", "宛如", "好像",
    "月光", "晨光", "夜色", "黄昏", "傍晚", "深夜",
    # Body / location prepositional words (frequent false positives in CJK prose)
    "掌中", "袖中", "胸中", "心中", "手中", "怀中", "眼中", "嘴中", "口中",
    "头顶", "身前", "身后", "身上", "身侧", "脚下", "面前", "眼前", "面上",
    "腰间", "肩头", "肩上", "膝上",
    # Common dialogue / mood tokens that surface inside quoted speech
    "跑不", "跑得", "跑了", "走了", "走开", "走吧", "知道", "不知", "不是",
    "不能", "不行", "不要", "还是", "已经", "现在",
})

_NAME_BOUNDARY_CHARS = set("\n\t 　，。：；！？、「」『』\"\"''（）()【】…·—-")


def _scan_names(text: str) -> dict[str, list[int]]:
    """Return ``{name: [positions]}`` for 2-3 char Han words at word boundaries.

    The position is the *start* offset of each occurrence. False positives
    are filtered downstream by frequency (most plot-relevant names appear
    multiple times in any non-trivial chapter tail).
    """
    out: dict[str, list[int]] = {}
    if not text:
        return out
    n = len(text)
    for i in range(n):
        if i > 0 and text[i - 1] not in _NAME_BOUNDARY_CHARS:
            continue
        for length in (2, 3):
            end = i + length
            if end > n:
                continue
            cand = text[i:end]
            if not all("一" <= c <= "鿿" for c in cand):
                continue
            if cand in _NAME_STOPWORDS:
                continue
            out.setdefault(cand, []).append(i)

    # Drop 3-char tokens whose 2-char prefix is either (a) in the pool with
    # equal-or-higher frequency, or (b) already a known stopword (because
    # then the 3-char form is just stopword+1-char like "掌中令"/"袖中物").
    dropped: list[str] = []
    for token in out:
        if len(token) != 3:
            continue
        prefix = token[:2]
        if prefix in _NAME_STOPWORDS:
            dropped.append(token)
        elif prefix in out and len(out[prefix]) >= len(out[token]):
            dropped.append(token)
    for t in dropped:
        del out[t]
    return out

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
    """Result of validating a chapter seam."""

    open_threads: tuple[OpenThread, ...]
    resolutions: tuple[ThreadResolution, ...]
    silent_drops: tuple[ThreadResolution, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not self.silent_drops

    @property
    def score(self) -> float:
        """Continuity score in [0, 1]. 1.0 if all threads resolved; linear penalty per silent drop."""
        if not self.open_threads:
            return 1.0
        resolved = len(self.open_threads) - len(self.silent_drops)
        return max(0.0, resolved / len(self.open_threads))


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

    # 2. Participants: rank by frequency, keep top 3.
    name_positions = _scan_names(text)
    name_counts = {n: len(positions) for n, positions in name_positions.items()}
    # In a short tail a name only needs to appear once to count -- we are
    # cataloguing actors present at the cliffhanger, not finding nicknames.
    for name in sorted(name_counts, key=lambda n: (-name_counts[n], name_positions[n][0]))[:3]:
        add(ThreadKind.PARTICIPANT, name, name_positions[name][0])

    # 3. Immediate threat: scan whole tail for threat verbs in the last 200 chars
    threat_window = text[-200:]
    threat_offset = len(text) - len(threat_window)
    cjk = _is_cjk_dominant(text)
    if cjk:
        for verb in _THREAT_VERBS:
            idx = threat_window.rfind(verb)
            if idx >= 0:
                add(ThreadKind.IMMEDIATE_THREAT, verb, threat_offset + idx)
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
    if thread.kind == ThreadKind.PARTICIPANT:
        # The other named character doesn't appear, but the protagonist might be
        # alone now -- we don't penalize this if some explanation marker appears
        # later. Treat it as on_screen if any first/third-person body-state cue
        # is in the opening.
        if any(cue in opening for cue in ("醒", "脱身", "逃", "甩开", "摆脱", "独自", "孤身")):
            return "on_screen", "状态过渡词"
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
        ``SeamReport`` -- ``passed`` is ``True`` iff zero silent drops.
    """
    threads = extract_open_threads(prev_chapter_tail)
    if not threads:
        return SeamReport(open_threads=(), resolutions=())

    opening_window = (current_opening or "")[:opening_window_chars]
    resolutions: list[ThreadResolution] = []
    for thread in threads:
        kind, evidence = _classify_resolution(thread, opening_window)
        resolutions.append(ThreadResolution(thread=thread, resolution=kind, evidence=evidence))

    # Two-pass refinement: if both LOCATION and at least one PARTICIPANT
    # continued directly into the opening, an outstanding IMMEDIATE_THREAT
    # marked ``silent_drop`` is almost certainly being continued in place
    # (the antagonist is still pressing the cliffhanger). Upgrade it to
    # ``continuation``.
    has_loc_continuation = any(
        r.thread.kind == ThreadKind.LOCATION and r.resolution == "continuation"
        for r in resolutions
    )
    has_participant_continuation = any(
        r.thread.kind == ThreadKind.PARTICIPANT and r.resolution == "continuation"
        for r in resolutions
    )
    if has_loc_continuation and has_participant_continuation:
        for idx, r in enumerate(resolutions):
            if r.thread.kind == ThreadKind.IMMEDIATE_THREAT and r.resolution == "silent_drop":
                resolutions[idx] = ThreadResolution(
                    thread=r.thread,
                    resolution="continuation",
                    evidence="威胁场景延续（场景 + 参与者均承接）",
                )

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
    for drop in report.silent_drops:
        thread = drop.thread
        if thread.kind == ThreadKind.IMMEDIATE_THREAT:
            bullets.append(f"- 前章末尾尚有威胁未处理（标志：「{thread.marker}」）；请在本章开篇 200-300 字内交代主角如何脱身 / 被压制 / 暂时摆脱该威胁。")
        elif thread.kind == ThreadKind.LOCATION:
            bullets.append(f"- 前章末尾主角位于「{thread.marker}」；本章开篇若已换场景，请明示空间转场（被拖走 / 主动脱身 / 时间跳跃后醒来）。")
        elif thread.kind == ThreadKind.PARTICIPANT:
            bullets.append(f"- 前章末尾「{thread.marker}」在场；本章开篇请说明该角色去向（追丢 / 退去 / 同行 / 仍在监视）。")
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
