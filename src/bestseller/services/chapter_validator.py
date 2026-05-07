"""L5 Chapter-Assembly Validator.

Runs on a *finished* chapter draft (post-scene-assembly) and catches defects
that only manifest at chapter scope — structure that's invisible inside a
single scene but breaks on stitching:

* ``DialogIntegrityCheck`` — paired-quote state machine catches the
  chapter-050 class of bug where quotes open but never close (bug #2).
* ``POVLockCheck`` — samples narrative prose (excluding quoted dialogue)
  and rejects drafts that drift across POV persons (bug #12).
* ``RepeatedEventBeatCheck`` — catches chapter assemblies that replay the
  same high-impact event beat instead of escalating it.
* ``CliffhangerRotationCheck`` — classifies the chapter ending's
  cliffhanger type and rejects it when the same type appears in the
  most-recent-N window tracked by ``DiversityBudget`` (bug #10).

We deliberately **reuse** the L4 ``Violation`` / ``ValidationContext`` /
``QualityReport`` dataclasses so the same L6 write-gate aggregates both
levels into a single decision. Each check is a ``Check`` under the L4
``Protocol``; callers can compose an ``OutputValidator`` with L4 + L5
checks mixed together if they want one pass.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
import re

from bestseller.services.hype_engine import (
    HypeRecipe,
    HypeType,
    classify_hype,
    extract_ending_sentence,
)
from bestseller.services.invariants import CliffhangerType
from bestseller.services.output_validator import (
    Check,
    QualityReport,
    ValidationContext,
    Violation,
)

# ---------------------------------------------------------------------------
# Quote pairs — per-language.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuotePair:
    """One opening char → matching closing char.

    Straight ASCII quotes (``"`` / ``'``) are their own partner — they are
    ambiguous open/close, so we handle them by parity rather than
    state-machine matching.
    """

    open: str
    close: str
    name: str
    is_ambiguous: bool = False  # True for ASCII quotes where open == close


_ZH_PAIRS: tuple[QuotePair, ...] = (
    QuotePair("\u201c", "\u201d", "curly_double"),
    QuotePair("\u2018", "\u2019", "curly_single"),
    QuotePair("\u300c", "\u300d", "corner"),  # 「」
    QuotePair("\u300e", "\u300f", "white_corner"),  # 『』
    QuotePair('"', '"', "straight_double", is_ambiguous=True),
)

_EN_PAIRS: tuple[QuotePair, ...] = (
    QuotePair("\u201c", "\u201d", "curly_double"),
    QuotePair("\u2018", "\u2019", "curly_single"),
    QuotePair('"', '"', "straight_double", is_ambiguous=True),
)


def _pairs_for_language(language: str) -> tuple[QuotePair, ...]:
    return _ZH_PAIRS if language.lower().startswith("zh") else _EN_PAIRS


# ---------------------------------------------------------------------------
# DialogIntegrityCheck — bug #2.
# ---------------------------------------------------------------------------


_PARAGRAPH_BREAK_RE = re.compile(r"\n\s*\n")


class DialogIntegrityCheck:
    """Detects unclosed quotes across the chapter.

    We check *globally*, not per-paragraph, because legitimate multi-
    paragraph quoted content (long speeches, letters, handwritten notes
    reproduced verbatim) opens a quote in one paragraph and closes it
    several paragraphs later. A per-paragraph check would flag those as
    "unclosed" even though they're correctly balanced across the chapter.

    The canonical ch-050 defect — opens a quote, runs through the rest of
    the chapter, and never closes — is caught by the global scan. Ambiguous
    ASCII quotes use parity counting since they can't be distinguished
    open-vs-close without context.
    """

    code = "DIALOG_UNPAIRED"

    def __init__(self, *, max_samples: int = 3) -> None:
        self.max_samples = max_samples

    def run(self, text: str, ctx: ValidationContext) -> Iterable[Violation]:
        if not text:
            return []
        pairs = _pairs_for_language(ctx.invariants.language)
        violations: list[Violation] = []

        # Pass 1 — state-machine check on matched (non-ambiguous) pairs
        # across the WHOLE chapter. A quote is "unclosed" only when no
        # matching close ever appears.
        stack: list[tuple[QuotePair, int]] = []
        for i, ch in enumerate(text):
            for pair in pairs:
                if pair.is_ambiguous:
                    continue
                if ch == pair.open:
                    stack.append((pair, i))
                    break
                if ch == pair.close and stack and stack[-1][0] is pair:
                    stack.pop()
                    break
        if stack:
            pair, offset = stack[0]
            # Report which paragraph the unclosed quote lives in so the
            # prompt_feedback can point the regen at a concrete location.
            paragraphs = _PARAGRAPH_BREAK_RE.split(text)
            para_idx, local_offset = _offset_to_paragraph(text, offset, paragraphs)
            snippet = _context_window(text, offset, radius=25)
            violations.append(
                Violation(
                    code=self.code,
                    severity="block",
                    location=f"paragraph:{para_idx}:char:{offset}",
                    detail=(
                        f"Paragraph {para_idx}: unclosed {pair.name} "
                        f"quote opened at offset {local_offset}; no matching "
                        f"{pair.close} found in the rest of the chapter"
                    ),
                    prompt_feedback=(
                        f"段落 {para_idx} 在位置 {local_offset} 打开了 "
                        f"{pair.open}（{pair.name}），但直到章末都未闭合。"
                        f"上下文：『{snippet}』。"
                        f"请检查并补齐对应的 {pair.close}，"
                        f"确保所有对话引号成对出现。"
                    ),
                )
            )

        # Pass 2 — parity check on ambiguous ASCII quotes over the whole text.
        for pair in pairs:
            if not pair.is_ambiguous:
                continue
            count = text.count(pair.open)
            if count % 2 == 1:
                violations.append(
                    Violation(
                        code=self.code,
                        severity="block",
                        location=f"ambiguous_quote:{pair.name}",
                        detail=(
                            f"Odd count ({count}) of {pair.name} quotes across "
                            f"the chapter — one dialogue block is unterminated"
                        ),
                        prompt_feedback=(
                            f"本章包含 {count} 个直引号 ({pair.open})，总数为奇数——"
                            f"意味着至少一段对话未闭合。"
                            f"请检查全文并补齐未闭合的对话引号。"
                        ),
                    )
                )
                break  # One ambiguous-quote violation per chapter is enough.

        return violations


def _offset_to_paragraph(
    text: str, absolute_offset: int, paragraphs: list[str]
) -> tuple[int, int]:
    """Translate an absolute char offset into ``(paragraph_idx, local_offset)``.

    Paragraphs are separated by ``_PARAGRAPH_BREAK_RE``; we walk them in
    order accumulating consumed text until the absolute offset falls inside
    one. Returns ``(0, absolute_offset)`` as a safe default if we overshoot.
    """

    consumed = 0
    for idx, para in enumerate(paragraphs):
        # Locate this paragraph within ``text`` starting from ``consumed``
        # so repeated identical paragraphs don't collide.
        start = text.find(para, consumed)
        if start == -1:
            # Should not happen since paragraphs came from splitting text,
            # but guard anyway.
            start = consumed
        end = start + len(para)
        if absolute_offset < end:
            return idx, max(absolute_offset - start, 0)
        consumed = end
    return len(paragraphs) - 1, absolute_offset


def _context_window(text: str, center: int, *, radius: int = 20) -> str:
    lo = max(0, center - radius)
    hi = min(len(text), center + radius + 1)
    snippet = text[lo:hi].replace("\n", " ").strip()
    return snippet


# ---------------------------------------------------------------------------
# POVLockCheck — bug #12.
# ---------------------------------------------------------------------------


# Pronoun sets for out-of-dialogue narrative prose.
_FIRST_PERSON_ZH = {"我", "我们", "吾", "朕", "余"}
_FIRST_PERSON_EN = {
    "i", "i'm", "i've", "i'd", "i'll",
    "me", "my", "myself", "mine",
    "we", "we're", "we've", "us", "our", "ours", "ourselves",
}
_THIRD_PERSON_ZH = {"他", "她", "他们", "她们", "它", "它们"}
_THIRD_PERSON_EN = {
    "he", "she", "him", "her", "his", "hers", "himself", "herself",
    "they", "them", "their", "theirs", "themselves",
}


# Sentence splitters per language.
_ZH_SENTENCE_RE = re.compile(r"[^。！？\n]+[。！？]")
_EN_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]")


class POVLockCheck:
    """Reject drafts whose narrative prose drifts across POV persons.

    Only *narrative* prose is checked — dialogue inside quotes is stripped
    first, because characters legitimately say "I" in their own dialogue
    regardless of the narrator POV.

    The check is deliberately conservative (requires multiple mismatches
    before firing) — POV drift is notoriously easy to false-flag on a
    single sentence that slips into free-indirect discourse. We only fire
    when at least ``min_drift_sentences`` sentences clearly break the
    declared POV.
    """

    code = "POV_DRIFT"

    def __init__(
        self,
        *,
        sample_size: int = 40,
        min_drift_sentences_close_third: int = 3,
        min_drift_ratio_first: float = 0.5,
    ) -> None:
        """Configure drift thresholds per POV type.

        The two POV types need very different thresholds:

        * ``close_third``: narrator never says "I/我" outside dialogue, so
          even 3 such sentences is a concrete bug. Dialogue is stripped first.
        * ``first``: narrator describes other characters constantly; a
          sentence with pure third-person pronouns is *normal* (e.g., "She
          reached for the door"). We only fire when the majority of sampled
          narrative sentences have zero first-person hits — i.e., the
          narrator has actually slipped into close-third reportage.
        """

        self.sample_size = sample_size
        self.min_drift_sentences_close_third = min_drift_sentences_close_third
        self.min_drift_ratio_first = min_drift_ratio_first

    def run(self, text: str, ctx: ValidationContext) -> Iterable[Violation]:
        if not text:
            return []
        pov = ctx.invariants.pov
        # Omniscient narrators legitimately switch pronouns — skip.
        if pov == "omniscient":
            return []

        narrative = _strip_quoted_dialogue(text, ctx.invariants.language)
        sentences = _split_sentences(narrative, ctx.invariants.language)
        if not sentences:
            return []
        # Take only the first N + a tail slice so we sample across the chapter.
        head = sentences[: self.sample_size // 2]
        tail = sentences[-(self.sample_size // 2) :] if len(sentences) > self.sample_size else []
        sample = head + tail

        mismatches: list[tuple[int, str]] = []
        for idx, sent in enumerate(sample):
            tokens = _tokens_for_pov(sent, ctx.invariants.language)
            if pov == "first":
                # First-person narrative must use "I/我" — if a sentence uses
                # third-person referring to the protagonist, that's drift.
                # We can't disambiguate "他 = protagonist" vs "他 = other
                # character" without NLP, so we only warn when the sentence
                # has *zero* first-person pronouns and *at least one* third-
                # person pronoun, AND the ratio across the sample is strong.
                if self._is_third(tokens, ctx.invariants.language) and not self._is_first(
                    tokens, ctx.invariants.language
                ):
                    mismatches.append((idx, sent))
            elif pov == "close_third":
                # Close-third narrative prose should not say "I/我" outside
                # dialogue. Dialogue has already been stripped.
                if self._is_first(tokens, ctx.invariants.language):
                    mismatches.append((idx, sent))

        # POV-specific thresholds: close-third uses an absolute count
        # (narrator saying "I" is always a bug), while first-person uses a
        # ratio (narrator describing others is normal, so we need a majority
        # before we can claim real drift into close-third reportage).
        if pov == "first":
            ratio = len(mismatches) / max(len(sample), 1)
            if ratio < self.min_drift_ratio_first:
                return []
        else:  # close_third
            if len(mismatches) < self.min_drift_sentences_close_third:
                return []

        # Report the top 3 mismatches.
        example_lines = [
            f"  - {_truncate(s, 60)}" for _, s in mismatches[:3]
        ]
        return [
            Violation(
                code=self.code,
                severity="block",
                location=f"sample:{len(sample)}:mismatches:{len(mismatches)}",
                detail=(
                    f"POV declared as '{pov}' but {len(mismatches)}/"
                    f"{len(sample)} sampled narrative sentences use the wrong person"
                ),
                prompt_feedback=(
                    f"本章 POV 应为 {pov}，但在 {len(sample)} 条抽样叙述句中有"
                    f" {len(mismatches)} 条违反该 POV 的代词规则。"
                    f"示例：\n" + "\n".join(example_lines) + "\n"
                    f"请全文检查叙述部分（不包括引号内的对话），"
                    f"将人称统一到 {pov}。"
                ),
            )
        ]

    @staticmethod
    def _is_first(tokens: set[str], language: str) -> bool:
        if language.lower().startswith("zh"):
            return bool(tokens & _FIRST_PERSON_ZH)
        return bool(tokens & _FIRST_PERSON_EN)

    @staticmethod
    def _is_third(tokens: set[str], language: str) -> bool:
        if language.lower().startswith("zh"):
            return bool(tokens & _THIRD_PERSON_ZH)
        return bool(tokens & _THIRD_PERSON_EN)


# ---------------------------------------------------------------------------
# Dialogue stripping + sentence splitting helpers.
# ---------------------------------------------------------------------------


def _strip_quoted_dialogue(text: str, language: str) -> str:
    """Return ``text`` with everything inside paired quotes replaced by a
    single space — preserving sentence boundaries and paragraph structure."""

    pairs = _pairs_for_language(language)
    out: list[str] = []
    depth = 0
    active: QuotePair | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        handled = False
        for pair in pairs:
            if pair.is_ambiguous:
                if ch == pair.open:
                    if depth == 0:
                        depth = 1
                        active = pair
                        out.append(" ")
                    elif depth == 1 and active is pair:
                        depth = 0
                        active = None
                        out.append(" ")
                    else:
                        out.append(" ")
                    handled = True
                    break
                continue
            if depth == 0 and ch == pair.open:
                depth = 1
                active = pair
                out.append(" ")
                handled = True
                break
            if depth == 1 and active is pair and ch == pair.close:
                depth = 0
                active = None
                out.append(" ")
                handled = True
                break
        if not handled:
            if depth > 0:
                if ch in "\n":  # preserve structure
                    out.append("\n")
                else:
                    out.append(" ")
            else:
                out.append(ch)
        i += 1
    return "".join(out)


def _split_sentences(text: str, language: str) -> list[str]:
    if language.lower().startswith("zh"):
        sents = _ZH_SENTENCE_RE.findall(text)
    else:
        sents = _EN_SENTENCE_RE.findall(text)
    out: list[str] = []
    for s in sents:
        cleaned = s.strip()
        if cleaned:
            out.append(cleaned)
    return out


_WORD_BOUNDARY_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z']+")


def _tokens_for_pov(sentence: str, language: str) -> set[str]:
    if language.lower().startswith("zh"):
        # CJK: character-by-character membership is fine because pronouns are
        # single- or two-char sequences and we're doing set membership.
        chars: set[str] = set()
        for ch in sentence:
            if "\u4e00" <= ch <= "\u9fff":
                chars.add(ch)
        # Add two-char compounds when adjacent matches form them.
        # (Cheap: just substring contains for each target.)
        extra = {tok for tok in _FIRST_PERSON_ZH | _THIRD_PERSON_ZH if tok in sentence}
        return chars | extra
    # Latin: tokenize on word boundaries, lowercase.
    raw = _WORD_BOUNDARY_RE.split(sentence.lower())
    return {w for w in raw if w}


def _truncate(text: str, limit: int) -> str:
    clean = text.replace("\n", " ").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# CliffhangerRotationCheck — bug #10.
# ---------------------------------------------------------------------------


# Heuristic keyword map used by ``classify_cliffhanger``. Languages share the
# same ``CliffhangerType`` taxonomy; keywords are per-language to keep the
# matcher cheap. These are *signals*, not proof — when nothing scores, we
# return ``None`` so the rotation check never blocks on ambiguity.
_CLIFFHANGER_KEYWORDS_ZH: dict[CliffhangerType, tuple[str, ...]] = {
    CliffhangerType.REVELATION: (
        "真相", "原来", "竟然", "其实", "秘密", "身份", "揭开", "揭露",
    ),
    CliffhangerType.INTRUSION: (
        "入侵", "闯入", "敌袭", "破门", "杀来", "杀到", "攻来", "冲入",
    ),
    CliffhangerType.DECISION: (
        "决定", "抉择", "选择", "必须做出", "不得不", "无论如何",
    ),
    CliffhangerType.BODY_REACTION: (
        "心跳", "心脏", "颤抖", "发抖", "冷汗", "呼吸", "胸口", "脉搏",
    ),
    CliffhangerType.NEW_CHARACTER: (
        "陌生", "陌生人", "不知何时", "一道身影", "一个身影", "一道人影",
    ),
    CliffhangerType.POWER_SHIFT: (
        "突破", "暴涨", "灵力", "力量", "修为", "境界", "觉醒",
    ),
    CliffhangerType.ENVIRONMENTAL: (
        "天地", "震动", "地动", "风暴", "天色", "天空", "巨响", "雷霆",
    ),
    CliffhangerType.INTERNAL_CRISIS: (
        "崩溃", "绝望", "心中", "心头", "心如", "无力", "彷徨", "自责",
    ),
}

_CLIFFHANGER_KEYWORDS_EN: dict[CliffhangerType, tuple[str, ...]] = {
    CliffhangerType.REVELATION: (
        "the truth", "it was", "she realized", "he realized",
        "revealed", "secret", "identity",
    ),
    CliffhangerType.INTRUSION: (
        "burst in", "broke down", "stormed in", "intruder",
        "attackers", "breached",
    ),
    CliffhangerType.DECISION: (
        "she had to", "he had to", "must choose", "decided", "no choice",
    ),
    CliffhangerType.BODY_REACTION: (
        "her heart", "his heart", "pulse", "trembled", "breath caught",
        "cold sweat",
    ),
    CliffhangerType.NEW_CHARACTER: (
        "a stranger", "a figure", "unknown voice", "a voice she",
        "a voice he",
    ),
    CliffhangerType.POWER_SHIFT: (
        "power surged", "new strength", "ability awakened",
        "broke through", "surge of",
    ),
    CliffhangerType.ENVIRONMENTAL: (
        "the sky", "earthquake", "thunder", "ground shook", "storm",
    ),
    CliffhangerType.INTERNAL_CRISIS: (
        "despair", "doubt", "collapsed inside", "her mind", "his mind",
    ),
}


def classify_cliffhanger(
    text: str, language: str, *, line_tail: int = 30
) -> CliffhangerType | None:
    """Classify the cliffhanger type of a chapter ending.

    Looks at the last ``line_tail`` non-empty lines of ``text`` and scores
    each ``CliffhangerType`` by keyword frequency. The winner must beat a
    minimum score (``>=2``) to reduce false positives; otherwise returns
    ``None`` (meaning "unclassified — don't rotate-block").
    """

    if not text or not text.strip():
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    tail = "\n".join(lines[-line_tail:]) if lines else ""
    if not tail:
        return None
    table = (
        _CLIFFHANGER_KEYWORDS_ZH
        if language.lower().startswith("zh")
        else _CLIFFHANGER_KEYWORDS_EN
    )
    lower_tail = tail.lower() if not language.lower().startswith("zh") else tail
    scores: dict[CliffhangerType, int] = {}
    for kind, keywords in table.items():
        hits = 0
        for kw in keywords:
            needle = kw if not language.lower().startswith("en") else kw.lower()
            hits += lower_tail.count(needle)
        if hits:
            scores[kind] = hits
    if not scores:
        return None
    best_kind, best_score = max(scores.items(), key=lambda kv: kv[1])
    if best_score < 2:
        return None
    return best_kind


@dataclass(frozen=True)
class CliffhangerRotationCheck:
    """Reject endings whose type was used in the last N chapters.

    Relies on ``ctx.recent_cliffhangers`` populated by the caller from
    ``DiversityBudget.recent_cliffhangers(policy.no_repeat_within)``.
    Severity is ``block`` so the L6 gate can downgrade to ``audit_only``
    via per-violation config (plan §9 decision 2 defaults
    ``CLIFFHANGER_REPEAT`` to ``audit_only``).
    """

    code: str = "CLIFFHANGER_REPEAT"
    line_tail: int = 30

    def run(
        self, text: str, ctx: ValidationContext
    ) -> Iterable[Violation]:
        if ctx.scope != "chapter":
            return []
        if not ctx.recent_cliffhangers:
            return []
        language = ctx.invariants.language
        detected = classify_cliffhanger(
            text, language, line_tail=self.line_tail
        )
        if detected is None:
            return []
        if detected not in ctx.recent_cliffhangers:
            return []
        window_n = len(ctx.recent_cliffhangers)
        recent_codes = [k.value for k in ctx.recent_cliffhangers]
        available = [
            k.value
            for k in ctx.invariants.cliffhanger_policy.allowed_types
            if k not in ctx.recent_cliffhangers
        ]
        return [
            Violation(
                code=self.code,
                severity="block",
                location="chapter:ending",
                detail=(
                    f"Detected cliffhanger type '{detected.value}' was used "
                    f"in the last {window_n} chapters ({recent_codes})."
                ),
                prompt_feedback=(
                    f"本章结尾悬念类型 {detected.value} 在最近 "
                    f"{window_n} 章已使用（{recent_codes}）。"
                    f"请改写章尾，采用以下尚未使用的悬念类型之一："
                    f"{available or [k.value for k in CliffhangerType]}。"
                    f"保持主线情节和人物行为不变。"
                ),
            )
        ]


# ---------------------------------------------------------------------------
# HypeOccurrenceCheck — the assigned hype recipe must actually land.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HypeOccurrenceCheck:
    """Fail when the chapter doesn't deliver the hype peak it was assigned.

    Two independent signals either of which counts as "delivered":
      1. The recipe's ``trigger_keywords`` appear at least
         ``min_keyword_hits`` times in the draft.
      2. The language-specific ``classify_hype`` returns the same
         ``HypeType`` as the assignment (classifier fallback for recipes
         whose trigger set is too narrow).

    Severity is ``block`` so the gate can downgrade via config; plan §2
    table pins ``HYPE_MISSING`` to ``audit_only`` by default.
    """

    code: str = "HYPE_MISSING"
    min_keyword_hits: int = 2

    def run(
        self, text: str, ctx: ValidationContext
    ) -> Iterable[Violation]:
        if ctx.scope != "chapter" or not text:
            return []

        recipe = ctx.assigned_hype_recipe
        if recipe is None or not isinstance(recipe, HypeRecipe):
            return []
        # Allow presets to opt out by setting min_hype_per_chapter = 0
        # (carried through ``assigned_hype_type``/``assigned_hype_recipe``
        # being populated only when the scheme demands a hype).
        if not recipe.trigger_keywords:
            return []

        language = ctx.invariants.language
        is_en = (language or "").lower().startswith("en")
        haystack = text.lower() if is_en else text
        hits = sum(
            haystack.count(kw.lower() if is_en else kw)
            for kw in recipe.trigger_keywords
        )
        if hits >= self.min_keyword_hits:
            return []

        # Classifier fallback — if the chapter landed the assigned type
        # via a different keyword set, we still pass.
        classified = classify_hype(text, language)
        if classified is not None and classified[0] == recipe.hype_type:
            return []

        keywords_preview = "、".join(recipe.trigger_keywords[:5]) or "（无）"
        return [
            Violation(
                code=self.code,
                severity="block",
                location=f"chapter:hype:{recipe.hype_type.value}",
                detail=(
                    f"Assigned hype type '{recipe.hype_type.value}' (recipe "
                    f"'{recipe.key}') did not land: only {hits} trigger-keyword "
                    f"hits (<{self.min_keyword_hits}), classifier didn't "
                    f"confirm either."
                ),
                prompt_feedback=(
                    f"本章指定爽点类型为 {recipe.hype_type.value}，配方《{recipe.key}》，"
                    f"但关键词命中仅 {hits} 次（要求 ≥ {self.min_keyword_hits}）。"
                    f"请保持剧情不变，在中段或末段写一个明显的"
                    f"{recipe.hype_type.value} 峰值，触发关键词示例：{keywords_preview}。"
                    f"不要把爽点写进章末最后一句（那里留给悬念）。"
                ),
            )
        ]


# ---------------------------------------------------------------------------
# HypeDiversityCheck — forbid identical HypeType in consecutive chapters.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HypeDiversityCheck:
    """Reject assignments that repeat the hype type the reader just saw.

    Mirrors ``CliffhangerRotationCheck`` but on a tighter window: plan
    guidance is "no 2 consecutive same-type" because hype payoffs fatigue
    readers faster than cliffhangers. We don't classify the current draft
    — the check fires off the *assignment* ``ctx.assigned_hype_type`` vs
    ``ctx.recent_hype_types``. If the LLM happened to write a different
    type anyway (unusual), that's surfaced by ``HypeOccurrenceCheck``.
    """

    code: str = "HYPE_REPEAT"
    forbid_run_length: int = 2

    def run(
        self, text: str, ctx: ValidationContext
    ) -> Iterable[Violation]:
        if ctx.scope != "chapter":
            return []
        assigned = ctx.assigned_hype_type
        if assigned is None or not isinstance(assigned, HypeType):
            return []
        if not ctx.recent_hype_types:
            return []
        recent = [t for t in ctx.recent_hype_types if t is not None]
        if not recent:
            return []
        last = recent[: self.forbid_run_length]
        # Fire only when every slot in the window matches the assignment.
        if (
            len(last) >= self.forbid_run_length
            and all(_hype_type_value(t) == assigned.value for t in last)
        ):
            return [
                Violation(
                    code=self.code,
                    severity="block",
                    location=f"chapter:hype:{assigned.value}",
                    detail=(
                        f"HypeType '{assigned.value}' has been used in the "
                        f"last {self.forbid_run_length} chapters; repeating "
                        f"it again will burn reader patience."
                    ),
                    prompt_feedback=(
                        f"最近 {self.forbid_run_length} 章的爽点类型均为 "
                        f"{assigned.value}，连续第三次会让读者疲劳。"
                        "请改写本章的情绪峰值，采用其他爽点类型之一："
                        "face_slap / power_reveal / level_up / reversal / "
                        "counterattack / underdog_win / golden_finger_reveal / "
                        "comedic_beat / revenge_closure / caress_by_fate / "
                        "status_jump / domination。保持主线情节不变。"
                    ),
                )
            ]
        return []


def _hype_type_value(value: object) -> str | None:
    if isinstance(value, HypeType):
        return value.value
    if value is None:
        return None
    return str(value)


# ---------------------------------------------------------------------------
# EndingSentenceImpactCheck — last sentence must hook the next chapter.
# ---------------------------------------------------------------------------


# Cliffhanger / suspense words whose presence in the final sentence signals a
# real hook. Kept conservative — hits contribute to score, absence costs a
# point but isn't a killer on its own.
_ENDING_HOOK_WORDS_ZH: tuple[str, ...] = (
    "忽然", "突然", "竟", "竟然", "居然", "却", "而", "只见", "却见",
    "就在此时", "就在这时", "就在", "然而", "不料", "没想到", "谁也没想到",
    "谁知", "谁想", "骤然", "霎时", "刹那", "转眼",
)

_ENDING_HOOK_WORDS_EN: tuple[str, ...] = (
    "suddenly", "then", "but", "yet", "however", "just as",
    "at that moment", "before", "no one saw", "what no one",
)

# Words / phrases that indicate resolution, not suspense — their presence at
# the ending pushes the score down.
_ENDING_RESOLUTION_WORDS_ZH: tuple[str, ...] = (
    "终于平静", "终于放下", "安心", "安然", "就此结束",
    "一切都好", "圆满", "皆大欢喜", "放下心来",
)

_ENDING_RESOLUTION_WORDS_EN: tuple[str, ...] = (
    "finally at peace", "safely", "all was well", "everything settled",
    "peace at last",
)

_ENDING_STRONG_PUNCT_ZH = "！？…"
_ENDING_STRONG_PUNCT_EN = "!?"


@dataclass(frozen=True)
class EndingSentenceImpactCheck:
    """Grade the last sentence on a 4-point scale; fail if < ``pass_score``.

    Dimensions (each contributes 1 point when satisfied):
      1. Length in the "tight" range — not too long (chapter-summarizing
         rambles), not empty. ZH target 4-40 chars; EN target 3-22 words.
      2. Contains at least one cliffhanger / suspense word.
      3. Does NOT contain resolution vocabulary.
      4. Ends with a strong punctuation mark (！？… / !?).

    Plan decision: first 3 chapters emit ``severity="block"`` (informational
    — the gate's per-chapter override is the authoritative signal); chapter
    4+ emit ``severity="warn"``. The gate consults ``chapter_no`` directly
    via ``write_gate.resolve_mode(code, chapter_no=…)``, which promotes
    ``ENDING_SENTENCE_WEAK`` to ``block`` for chapters 1-3 regardless of the
    ``audit_only`` base config. See plan §2 "EndingSentenceImpactCheck 通过
    ctx.chapter_no <= 3 判断".
    """

    code: str = "ENDING_SENTENCE_WEAK"
    pass_score: int = 2
    first_n_chapters_blocking: int = 3

    def run(
        self, text: str, ctx: ValidationContext
    ) -> Iterable[Violation]:
        if ctx.scope != "chapter" or not text:
            return []

        language = ctx.invariants.language
        sentence = extract_ending_sentence(text, language)
        if not sentence:
            return []

        score, notes = _score_ending_sentence(sentence, language)
        if score >= self.pass_score:
            return []

        chapter_no = ctx.chapter_no or 0
        is_golden = 0 < chapter_no <= self.first_n_chapters_blocking
        severity = "block" if is_golden else "warn"
        golden_note = (
            f"（黄金前 {self.first_n_chapters_blocking} 章章末弱钩直接阻塞）"
            if is_golden
            else "（前三章外，章末弱钩记入审计）"
        )
        preview = sentence if len(sentence) <= 80 else sentence[:77] + "…"
        return [
            Violation(
                code=self.code,
                severity=severity,
                location=f"chapter:{chapter_no or 'n/a'}:ending",
                detail=(
                    f"Ending-sentence impact score {score}/4 < "
                    f"{self.pass_score}. Sentence: '{preview}'. "
                    f"Notes: {'; '.join(notes) or 'n/a'}."
                ),
                prompt_feedback=(
                    f"本章最后一句执行力不足（得分 {score}/4）{golden_note}。"
                    f"原句：『{preview}』。"
                    "请重写章末最后一句：保持短句、埋一个悬念词（忽然/竟然/就在此时/却见 等）、"
                    "避免解决本章悬念、以 ！/？/… 等强标点收尾；"
                    "让读者自然想追下一章。不要改动前文情节。"
                ),
            )
        ]


def _score_ending_sentence(
    sentence: str, language: str
) -> tuple[int, list[str]]:
    """Return ``(score, notes)`` where score ∈ [0, 4]."""

    notes: list[str] = []
    score = 0

    is_zh = (language or "").lower().startswith("zh")
    is_en = (language or "").lower().startswith("en")
    stripped = sentence.strip()

    # Dim 1: length in tight range.
    if is_zh:
        if 4 <= len(stripped) <= 40:
            score += 1
        else:
            notes.append(f"长度 {len(stripped)} 字超出 4-40 区间")
    else:
        words = [w for w in stripped.split() if w]
        if 3 <= len(words) <= 22:
            score += 1
        else:
            notes.append(f"长度 {len(words)} 词超出 3-22 区间")

    # Dim 2: contains a cliffhanger / suspense word.
    hook_words = _ENDING_HOOK_WORDS_ZH if is_zh else _ENDING_HOOK_WORDS_EN
    haystack = stripped if is_zh else stripped.lower()
    if any(
        (kw if is_zh else kw.lower()) in haystack for kw in hook_words
    ):
        score += 1
    else:
        notes.append("未出现悬念词 / cliffhanger cue")

    # Dim 3: does NOT contain resolution vocabulary.
    resolution_words = (
        _ENDING_RESOLUTION_WORDS_ZH if is_zh else _ENDING_RESOLUTION_WORDS_EN
    )
    if any(
        (kw if is_zh else kw.lower()) in haystack for kw in resolution_words
    ):
        notes.append("含有'一切平息'式解决句")
    else:
        score += 1

    # Dim 4: ends with strong punctuation.
    punct_set = _ENDING_STRONG_PUNCT_ZH if is_zh else _ENDING_STRONG_PUNCT_EN
    last_char = stripped[-1] if stripped else ""
    if last_char in punct_set:
        score += 1
    else:
        notes.append(f"末字符 '{last_char}' 不是强标点")

    # Unused language-branch guard — keeps mypy quiet about `is_en`.
    _ = is_en

    return score, notes


# ---------------------------------------------------------------------------
# GoldenThreeChapterCheck — chapters 1-3 carry selling-point + hype weight.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenThreeChapterCheck:
    """The first 3 chapters must do the heaviest lifting.

    Two requirements for each of chapters 1-3:
      A. At least one ``selling_points`` / ``hook_keywords`` keyword must
         appear in the first ``head_chars`` characters of the draft. The
         "promise" from the preset must show up at the top of the book.
      B. At least ``min_trigger_hits`` hype ``trigger_keywords`` (from the
         assigned recipe) must appear across the chapter, giving the reader
         a real payoff during the golden window.

    No-op for chapters 4+ and for projects whose ``HypeScheme.is_empty`` is
    True. Default severity is ``block`` so the gate can downgrade via
    config; plan §2 maps ``GOLDEN_THREE_WEAK`` to ``audit_only``.
    """

    code: str = "GOLDEN_THREE_WEAK"
    head_chars: int = 1000
    min_trigger_hits: int = 2

    def run(
        self, text: str, ctx: ValidationContext
    ) -> Iterable[Violation]:
        if ctx.scope != "chapter" or not text:
            return []
        chapter_no = ctx.chapter_no or 0
        if chapter_no <= 0 or chapter_no > 3:
            return []

        scheme = ctx.invariants.hype_scheme
        if scheme is None or scheme.is_empty:
            return []

        language = ctx.invariants.language
        is_en = (language or "").lower().startswith("en")
        head = text[: self.head_chars]
        head_haystack = head.lower() if is_en else head
        full_haystack = text.lower() if is_en else text

        problems: list[str] = []
        feedback_parts: list[str] = []

        # Rule A — selling-point / hook keyword in the opener.
        promise_keywords = tuple(scheme.selling_points) + tuple(scheme.hook_keywords)
        promise_hit = False
        for kw in promise_keywords:
            if not kw:
                continue
            needle = kw.lower() if is_en else kw
            if needle in head_haystack:
                promise_hit = True
                break
        if promise_keywords and not promise_hit:
            problems.append(
                f"no selling_point/hook_keyword in first {self.head_chars} chars"
            )
            preview = "、".join(k for k in promise_keywords[:5] if k) or "（无）"
            feedback_parts.append(
                f"前 {self.head_chars} 字内必须出现以下卖点关键词之一："
                f"{preview}（从卖点清单中挑一个融入开场）。"
            )

        # Rule B — assigned hype's triggers appear ≥ min_trigger_hits times.
        recipe = ctx.assigned_hype_recipe
        if isinstance(recipe, HypeRecipe) and recipe.trigger_keywords:
            hits = sum(
                full_haystack.count(kw.lower() if is_en else kw)
                for kw in recipe.trigger_keywords
            )
            if hits < self.min_trigger_hits:
                problems.append(
                    f"only {hits} hype trigger hits (need ≥ {self.min_trigger_hits})"
                )
                preview = "、".join(recipe.trigger_keywords[:5]) or "（无）"
                feedback_parts.append(
                    f"本章爽点配方《{recipe.key}》关键词命中仅 {hits} 次"
                    f"（前三章要求 ≥ {self.min_trigger_hits} 次，"
                    f"且至少一次发生在前 {self.head_chars} 字内）。"
                    f"关键词示例：{preview}。"
                )

        if not problems:
            return []

        return [
            Violation(
                code=self.code,
                severity="block",
                location=f"chapter:{chapter_no}:golden_window",
                detail=(
                    f"Golden-3 requirements unmet in chapter {chapter_no}: "
                    + "; ".join(problems)
                ),
                prompt_feedback=(
                    f"黄金前三章（本章第 {chapter_no} 章）承担开篇卖点 + 首个爽点峰值的双重任务。"
                    + " ".join(feedback_parts)
                    + " 请重写保持情节不变，将上述内容融入开场与中段，"
                    "不要影响章末悬念。"
                ),
            )
        ]


# ---------------------------------------------------------------------------
# Phase B1 — LineGapCheck (narrative-line dominance rotation).
# ---------------------------------------------------------------------------


_LINE_LABELS_ZH_LOCAL: dict[str, str] = {
    "overt": "明线",
    "undercurrent": "暗线",
    "hidden": "隐藏线",
    "core_axis": "核心轴",
}


class LineGapCheck:
    """Fire when a narrative layer has been dormant past its budget.

    This check reads ``ctx.line_gap_report`` (``LineGapReport`` produced
    by ``narrative_line_tracker.report_gaps``) rather than scanning the
    draft text; dominance is classified on the *previous* chapters, and
    this validator's job is to keep the author from writing yet another
    chapter in a single layer.

    Severity tiers:

    * ``"over"`` gap → ``block`` (critical). Routed through the Phase C
      Override Contract when the call site registers
      ``LINE_GAP_OVER`` in ``soft_constraint_codes``; otherwise a hard
      regen trigger.
    * ``"warn"`` gap → ``warn``. Never blocks; appears as a soft
      suggestion the author may act on.

    All outputs pass through ``Violation`` → ``CheckerReport`` via
    ``QualityReport.as_checker_report`` at the call site so downstream
    aggregation (scorecard, debt ledger) sees a consistent shape.
    """

    # Check Protocol requires a ``code`` attribute; emitted violations
    # still use the tier-specific codes below so write_gate can resolve
    # modes per-severity.
    code = "LINE_GAP"
    code_over = "LINE_GAP_OVER"
    code_warn = "LINE_GAP_WARN"

    def run(self, text: str, ctx: ValidationContext) -> Iterable[Violation]:
        report = ctx.line_gap_report
        if report is None:
            return []
        # Duck-type: we only need ``over_gaps`` / ``warn_gaps`` attrs and
        # iterable ``LineGap``-shaped objects (line_id / current_gap /
        # threshold / last_dominant_chapter).
        over = getattr(report, "over_gaps", ()) or ()
        warn = getattr(report, "warn_gaps", ()) or ()
        violations: list[Violation] = []
        chapter_no = ctx.chapter_no or getattr(report, "current_chapter", 0)

        for gap in over:
            label = _LINE_LABELS_ZH_LOCAL.get(gap.line_id, gap.line_id)
            last = gap.last_dominant_chapter or 0
            violations.append(
                Violation(
                    code=self.code_over,
                    severity="block",
                    location=f"chapter:{chapter_no}:line:{gap.line_id}",
                    detail=(
                        f"narrative layer '{gap.line_id}' dormant for "
                        f"{gap.current_gap} chapters (budget {gap.threshold}); "
                        f"last dominated at chapter {last}"
                    ),
                    prompt_feedback=(
                        f"{label}已连续 {gap.current_gap} 章未作为主导线"
                        f"（预算 {gap.threshold} 章，上次主导于第 {last} 章）。"
                        f"请在本章以 {label} 为主导或底色，让该线重新浮出水面——"
                        "例如回到该线的关键人物、推进该线的目标或揭示该线相关的线索。"
                    ),
                )
            )

        for gap in warn:
            label = _LINE_LABELS_ZH_LOCAL.get(gap.line_id, gap.line_id)
            last = gap.last_dominant_chapter or 0
            violations.append(
                Violation(
                    code=self.code_warn,
                    severity="warn",
                    location=f"chapter:{chapter_no}:line:{gap.line_id}",
                    detail=(
                        f"narrative layer '{gap.line_id}' near budget "
                        f"({gap.current_gap}/{gap.threshold})"
                    ),
                    prompt_feedback=(
                        f"{label}距上次主导已 {gap.current_gap} 章"
                        f"（预算 {gap.threshold} 章）；"
                        "建议在本章安排一次明显的回归，避免读者感知这条线已被遗忘。"
                    ),
                )
            )

        return violations


# ---------------------------------------------------------------------------
# RepeatedEventBeatCheck — prevents pasted/replayed chapter beats.
# ---------------------------------------------------------------------------


_ZH_EVENT_OBJECT_GROUPS: dict[str, tuple[str, ...]] = {
    "mirror": ("困魂镜", "穿衣镜", "镜面", "镜子", "镜框", "铜镜"),
    "screen": ("手机屏幕", "屏幕", "手机", "直播间", "短视频"),
}

_ZH_EVENT_ACTION_GROUPS: dict[str, tuple[str, ...]] = {
    "trap_or_entry": (
        "困在",
        "拖进",
        "拖入",
        "拖走",
        "往里拖",
        "拽进",
        "拽住",
        "吞",
        "吞咽",
        "没入",
        "穿过",
        "伸进",
        "滑进",
        "走进",
        "消失在",
        "被拖",
        "被吞",
        "被困",
    ),
}

_ZH_EVENT_OBJECT_LABELS: dict[str, str] = {
    "mirror": "镜面/镜子",
    "screen": "手机/屏幕",
}

_ZH_EVENT_ACTION_LABELS: dict[str, str] = {
    "trap_or_entry": "被困、被拖入或没入",
}

_ZH_FALLBACK_NAME_RE = re.compile(
    r"(?:[老小][\u4e00-\u9fff]{1,2}|[\u4e00-\u9fff]{2,3})"
)

_ZH_NAME_STOPWORDS = frozenset(
    {
        "下一秒",
        "镜面",
        "镜子",
        "手机",
        "屏幕",
        "穿衣镜",
        "困魂镜",
        "走廊",
        "房间",
        "声音",
        "身体",
        "眼睛",
        "黑影",
        "那团",
        "所有人",
        "没有人",
        "为什么",
        "怎么办",
    }
)


@dataclass(frozen=True)
class _BeatOccurrence:
    paragraph_idx: int
    snippet: str


class RepeatedEventBeatCheck:
    """Detect repeated high-impact event beats within one assembled chapter.

    The failure this catches is not generic word repetition, nor a legitimate
    chapter-break continuation. Long serials often let one rescue, trial,
    pursuit, or reveal run across multiple chapters. That is fine when the
    later passage starts from a changed state: new clue, new cost, new tactic,
    new location, new participant, or an irreversible result.

    This check only inspects one assembled chapter and only fires when that
    chapter appears to replay the same event beat after a long gap: the same
    named character is again trapped, swallowed, dragged into, or lost through
    the same mirror/screen mechanism. That pattern makes the reader feel the
    chapter is stitched from alternate drafts instead of moving forward.

    The check is intentionally conservative:
    * Chinese keyword heuristic only; English projects no-op.
    * Requires named actors plus an object group and action group.
    * Adjacent paragraphs are clustered as one event, so a multi-paragraph
      rescue/death scene is allowed. Two separated clusters for the same
      signature are blocked.
    """

    code = "REPEATED_EVENT_BEAT"

    def __init__(
        self,
        *,
        min_paragraph_chars: int = 18,
        max_cluster_gap: int = 3,
        min_cluster_separation: int = 16,
        min_total_occurrences: int = 3,
    ) -> None:
        self.min_paragraph_chars = min_paragraph_chars
        self.max_cluster_gap = max_cluster_gap
        self.min_cluster_separation = min_cluster_separation
        self.min_total_occurrences = min_total_occurrences

    def run(self, text: str, ctx: ValidationContext) -> Iterable[Violation]:
        if ctx.scope != "chapter" or not text:
            return []
        if not (ctx.invariants.language or "").lower().startswith("zh"):
            return []

        names = _character_names_for_repeated_beats(text, ctx)
        if not names:
            return []

        paragraphs = [
            para.strip()
            for para in _PARAGRAPH_BREAK_RE.split(text)
            if para and para.strip()
        ]
        occurrences: dict[tuple[str, str, str], list[_BeatOccurrence]] = defaultdict(list)

        for idx, para in enumerate(paragraphs, 1):
            normalized = _normalize_repeated_beat_paragraph(para)
            if len(normalized) < self.min_paragraph_chars:
                continue
            object_group = _first_group_hit(normalized, _ZH_EVENT_OBJECT_GROUPS)
            action_group = _first_group_hit(normalized, _ZH_EVENT_ACTION_GROUPS)
            if object_group is None or action_group is None:
                continue
            for name in names:
                if name in normalized:
                    occurrences[(name, object_group, action_group)].append(
                        _BeatOccurrence(
                            paragraph_idx=idx,
                            snippet=_context_window(para, 0, radius=36),
                        )
                    )

        for (name, object_group, action_group), hits in occurrences.items():
            if len(hits) < self.min_total_occurrences:
                continue
            clusters = _cluster_beat_occurrences(
                hits,
                max_gap=self.max_cluster_gap,
            )
            if len(clusters) < 2:
                continue
            separation = clusters[1][0].paragraph_idx - clusters[0][-1].paragraph_idx
            if separation < self.min_cluster_separation:
                continue

            first_para = clusters[0][0].paragraph_idx
            second_para = clusters[1][0].paragraph_idx
            object_label = _ZH_EVENT_OBJECT_LABELS.get(object_group, object_group)
            action_label = _ZH_EVENT_ACTION_LABELS.get(action_group, action_group)
            sample_a = clusters[0][0].snippet
            sample_b = clusters[1][0].snippet
            return [
                Violation(
                    code=self.code,
                    severity="block",
                    location=f"paragraph:{first_para},paragraph:{second_para}",
                    detail=(
                        "Repeated event beat in one chapter: "
                        f"{name} + {object_group} + {action_group} appears in "
                        f"separated clusters starting at paragraphs "
                        f"{first_para} and {second_para}"
                    ),
                    prompt_feedback=(
                        f"本章在第 {first_para} 段和第 {second_para} 段附近重复上演"
                        f"“{name} 与{object_label}{action_label}”这一事件。"
                        f"第一次样例：『{sample_a}』；第二次样例：『{sample_b}』。"
                        "请只保留一次完整事件，把另一处改写为新的状态推进："
                        "例如线索揭露、代价升级、人物选择、规则变化或章末反转。"
                        "如果这是上一章未完成事件的续写，必须从新状态继续，"
                        "不要回放已发生的拖入/吞入/困住过程；"
                        "如果仍在同一章内推进，则需要让第二处承担新的不可逆结果。"
                    ),
                )
            ]

        return []


def _character_names_for_repeated_beats(
    text: str,
    ctx: ValidationContext,
) -> tuple[str, ...]:
    names: set[str] = set()
    names.update(n.strip() for n in ctx.allowed_names if n and n.strip())
    naming_scheme = ctx.invariants.naming_scheme
    if naming_scheme is not None:
        names.update(n.strip() for n in naming_scheme.seed_pool if n and n.strip())

    if not names:
        # Fallback for ad-hoc/manual validation without a populated roster.
        counts: dict[str, int] = {}
        for match in _ZH_FALLBACK_NAME_RE.finditer(text):
            candidate = match.group(0)
            if candidate in _ZH_NAME_STOPWORDS:
                continue
            if len(candidate) < 2 or len(candidate) > 4:
                continue
            counts[candidate] = counts.get(candidate, 0) + 1
        names.update(name for name, count in counts.items() if count >= 2)

    filtered = {
        name
        for name in names
        if 2 <= len(name) <= 4 and name not in _ZH_NAME_STOPWORDS
    }
    return tuple(sorted(filtered, key=lambda item: (-len(item), item)))


def _normalize_repeated_beat_paragraph(paragraph: str) -> str:
    return re.sub(r"\s+", "", paragraph)


def _first_group_hit(
    text: str,
    groups: dict[str, tuple[str, ...]],
) -> str | None:
    for group, needles in groups.items():
        if any(needle in text for needle in needles):
            return group
    return None


def _cluster_beat_occurrences(
    occurrences: list[_BeatOccurrence],
    *,
    max_gap: int,
) -> list[list[_BeatOccurrence]]:
    ordered = sorted(occurrences, key=lambda item: item.paragraph_idx)
    clusters: list[list[_BeatOccurrence]] = []
    for occurrence in ordered:
        if not clusters:
            clusters.append([occurrence])
            continue
        if occurrence.paragraph_idx - clusters[-1][-1].paragraph_idx <= max_gap:
            clusters[-1].append(occurrence)
        else:
            clusters.append([occurrence])
    return clusters


# ---------------------------------------------------------------------------
# Canon guardrails — per-book forbidden terms + state-regression checks.
# ---------------------------------------------------------------------------


class CanonForbiddenTermCheck:
    """Block deprecated/foreign canon terms from leaking into new chapters."""

    code = "CANON_FORBIDDEN_TERM"

    def run(self, text: str, ctx: ValidationContext) -> Iterable[Violation]:
        if ctx.scope != "chapter" or not text:
            return []
        guardrails = ctx.canon_guardrails
        terms = tuple(getattr(guardrails, "forbidden_terms", ()) or ())
        if not terms:
            return []

        violations: list[Violation] = []
        for item in terms:
            term = str(getattr(item, "term", "") or "").strip()
            if not term or term not in text:
                continue
            offset = text.find(term)
            reason = str(getattr(item, "reason", "") or "").strip()
            suggestion = str(getattr(item, "suggestion", "") or "").strip()
            detail = f"Forbidden canon term appears in chapter: {term}"
            if reason:
                detail += f" ({reason})"
            feedback = (
                f"本章出现了已禁止的旧设定/非正典词：『{term}』。"
                f"{'原因：' + reason + '。' if reason else ''}"
                f"{'请改用：' + suggestion + '。' if suggestion else ''}"
                "请删除该词及其关联设定，不要把废弃体系重新带回当前正典。"
            )
            violations.append(
                Violation(
                    code=self.code,
                    severity="block",
                    location=f"char:{offset}",
                    detail=detail,
                    prompt_feedback=feedback,
                )
            )
            break

        return violations


class CanonStateRegressionCheck:
    """Block project-declared character/event state rollback patterns."""

    code = "CANON_STATE_REGRESSION"

    def run(self, text: str, ctx: ValidationContext) -> Iterable[Violation]:
        if ctx.scope != "chapter" or not text:
            return []
        guardrails = ctx.canon_guardrails
        rules = tuple(getattr(guardrails, "state_rules", ()) or ())
        if not rules:
            return []

        for rule in rules:
            subject = str(getattr(rule, "subject", "") or "").strip()
            status = str(getattr(rule, "status", "") or "").strip()
            applies_after = getattr(rule, "applies_after_chapter", None)
            if (
                isinstance(applies_after, int)
                and ctx.chapter_no is not None
                and ctx.chapter_no <= applies_after
            ):
                continue
            reason = str(getattr(rule, "reason", "") or "").strip()
            allowed_next = str(getattr(rule, "allowed_next", "") or "").strip()
            patterns = tuple(getattr(rule, "forbidden_patterns", ()) or ())
            for raw_pattern in patterns:
                pattern = str(raw_pattern or "").strip()
                if not pattern:
                    continue
                match = _search_canon_state_pattern(pattern, text)
                if match is None:
                    continue
                snippet = _context_window(text, match.start(), radius=42)
                return [
                    Violation(
                        code=self.code,
                        severity="block",
                        location=f"char:{match.start()}",
                        detail=(
                            f"Canon state regression for {subject or 'unknown'}"
                            f"{' (' + status + ')' if status else ''}: "
                            f"matched pattern {pattern!r}"
                        ),
                        prompt_feedback=(
                            f"本章触发了正典状态回滚规则："
                            f"{subject or '（未命名对象）'}"
                            f"{' 当前状态为“' + status + '”' if status else ''}。"
                            f"{'原因：' + reason + '。' if reason else ''}"
                            f"命中片段：『{snippet}』。"
                            f"{'后续允许方向：' + allowed_next + '。' if allowed_next else ''}"
                            "请从上一章末已经改变的状态继续推进，不要把已死亡、"
                            "已获救、已离局、已入镜或已揭露的事件重置回旧过程。"
                        ),
                    )
                ]

        return []


def _search_canon_state_pattern(pattern: str, text: str) -> re.Match[str] | None:
    try:
        return re.search(pattern, text, flags=re.DOTALL)
    except re.error:
        escaped = re.escape(pattern)
        return re.search(escaped, text, flags=re.DOTALL)


# ---------------------------------------------------------------------------
# Factory.
# ---------------------------------------------------------------------------


def build_chapter_validator_checks() -> list[Check]:
    """L5 default check list — dialog + POV + cliffhanger + 4 hype checks.

    Returned as a list so callers can splice into an existing
    ``OutputValidator`` (L4 + L5 combined pass). All checks gracefully
    no-op when their required context is absent: ``CliffhangerRotationCheck``
    skips without ``recent_cliffhangers``; the four hype checks skip when
    no hype is assigned or the project's ``HypeScheme`` is empty (legacy
    projects predating migration 0019); ``LineGapCheck`` skips when
    ``line_gap_report`` is ``None`` (project hasn't opted into the
    narrative-line tracker).
    """

    return [
        DialogIntegrityCheck(),
        POVLockCheck(),
        RepeatedEventBeatCheck(),
        CanonForbiddenTermCheck(),
        CanonStateRegressionCheck(),
        CliffhangerRotationCheck(),
        HypeOccurrenceCheck(),
        HypeDiversityCheck(),
        EndingSentenceImpactCheck(),
        GoldenThreeChapterCheck(),
        LineGapCheck(),
    ]


def validate_chapter(text: str, ctx: ValidationContext) -> QualityReport:
    """Convenience wrapper: run L5 checks and return a ``QualityReport``."""

    violations: list[Violation] = []
    for check in build_chapter_validator_checks():
        violations.extend(check.run(text, ctx) or [])
    return QualityReport(tuple(violations))
