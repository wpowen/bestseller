"""Span-level AI-flavor detector for Chinese and English chapters.

Design contract
---------------
* **Position-faithful**: every finding carries the exact ``(start, end)``
  offsets into the original chapter markdown. The patcher applies fixes
  in reverse order, so offsets remain valid through the whole patch
  pass.
* **Dialogue-protected**: phrase hits located inside quotation marks
  ("..." / "..." / 「...」 / 『...』 / '...') are dropped — characters
  legitimately use clichés in speech, and rewriting dialogue would
  change voice.
* **Bilingual via data**: the only language-specific code is the
  sentence splitter and the dialogue-quote alphabet. All phrase rules
  and cluster thresholds live in ``data/ai_flavor/patterns_{cn,en}.json``
  so curators can edit them without touching Python.
* **Zero LLM cost**: detection is regex/substring based and runs in
  well under 100 ms on a 5k-char chapter, so the gate can sit inline in
  the per-chapter pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any

from bestseller.services.ai_flavor.types import (
    AiFlavorReport,
    AiFlavorSpan,
    Severity,
)

# Default data location resolved relative to the repo root, matching how
# ``DEFAULT_QUALITY_GATES_PATH`` is referenced in
# ``quality_gates_config.py``. Callers can override via ``data_dir``.
DEFAULT_DATA_DIR = Path("data/ai_flavor")


_SENTENCE_BOUNDARY_CN = re.compile(r"[。！？…\n]")
_SENTENCE_BOUNDARY_EN = re.compile(r"(?<=[.!?])\s|\n")

# Quotation pairs by language. Detection-side we treat every quoted span
# as a "do not touch" zone regardless of nesting; this is conservative
# but simple and matches how copyeditors think about dialogue.
_QUOTE_PAIRS_CN: tuple[tuple[str, str], ...] = (
    ("“", "”"),  # " "
    ("‘", "’"),  # ' '
    ("「", "」"),  # 「 」
    ("『", "』"),  # 『 』
    # Many Chinese webnovel CMSes write dialogue with ASCII straight quotes;
    # we protect them too so cluster-rules don't strip phrases that are
    # legitimately voiced by a character.
    ('"', '"'),
)
_QUOTE_PAIRS_EN: tuple[tuple[str, str], ...] = (
    ("“", "”"),
    ("‘", "’"),
    ('"', '"'),
    ("'", "'"),
)


def _normalise_language(language: str | None) -> str:
    raw = (language or "zh").strip().lower()
    if raw.startswith("en"):
        return "en"
    return "zh"


@dataclass(frozen=True)
class _LoadedRules:
    """Parsed pattern file ready for matching."""

    language: str
    case_insensitive: bool
    phrase_rules: tuple[dict[str, Any], ...]
    cluster_rules: tuple[dict[str, Any], ...]
    rhythm_rules: tuple[dict[str, Any], ...]


@lru_cache(maxsize=4)
def _load_rules(language: str, data_dir_str: str) -> _LoadedRules:
    """Load + cache pattern JSON for a language.

    Cached on the (language, data_dir) pair so test fixtures can swap
    data dirs without poisoning the production cache. Cache size 4 is
    plenty: two languages × prod + test.
    """

    data_dir = Path(data_dir_str)
    path = data_dir / f"patterns_{language}.json"
    if not path.exists():
        return _LoadedRules(
            language=language,
            case_insensitive=False,
            phrase_rules=(),
            cluster_rules=(),
            rhythm_rules=(),
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _LoadedRules(
        language=language,
        case_insensitive=bool(raw.get("case_insensitive", language == "en")),
        phrase_rules=tuple(raw.get("phrase_rules") or ()),
        cluster_rules=tuple(raw.get("cluster_rules") or ()),
        rhythm_rules=tuple(raw.get("rhythm_rules") or ()),
    )


def _find_dialogue_ranges(
    text: str, quote_pairs: tuple[tuple[str, str], ...]
) -> list[tuple[int, int]]:
    """Return half-open ranges where text is inside quoted dialogue.

    Quote pairs are scanned greedily left-to-right. Mismatched closers
    (lone open quote) are ignored — better to under-protect than to
    swallow the rest of the chapter.
    """

    ranges: list[tuple[int, int]] = []
    for open_q, close_q in quote_pairs:
        if open_q == close_q:
            # Symmetric quotes (e.g. ASCII '"'): pair them sequentially.
            i = 0
            while True:
                start = text.find(open_q, i)
                if start < 0:
                    break
                end = text.find(close_q, start + 1)
                if end < 0:
                    break
                ranges.append((start, end + 1))
                i = end + 1
        else:
            i = 0
            while True:
                start = text.find(open_q, i)
                if start < 0:
                    break
                end = text.find(close_q, start + 1)
                if end < 0:
                    break
                ranges.append((start, end + 1))
                i = end + 1
    ranges.sort()
    return ranges


def _is_in_ranges(pos: int, ranges: list[tuple[int, int]]) -> bool:
    """Whether ``pos`` falls inside any (start, end) range."""

    for start, end in ranges:
        if start <= pos < end:
            return True
        if start > pos:
            break
    return False


def _sentence_bounds(text: str, pos: int, language: str) -> tuple[int, int]:
    """Return half-open ``(start, end)`` of the sentence containing ``pos``.

    Falls back to paragraph bounds (``\\n`` delimited) when no terminator
    is found — keeps the LLM context window bounded even on prose without
    sentence-end punctuation.
    """

    boundary = _SENTENCE_BOUNDARY_EN if language == "en" else _SENTENCE_BOUNDARY_CN

    # Search backward for the previous boundary.
    start = 0
    for m in boundary.finditer(text, 0, pos):
        start = m.end()

    # Search forward for the next boundary.
    next_match = boundary.search(text, pos)
    end = next_match.end() if next_match else len(text)
    return (start, end)


def _find_all_occurrences(haystack: str, needle: str) -> list[int]:
    if not needle:
        return []
    starts: list[int] = []
    i = 0
    while True:
        idx = haystack.find(needle, i)
        if idx < 0:
            break
        starts.append(idx)
        i = idx + max(1, len(needle))
    return starts


# Sentence terminators used by the rhythm pass. Commas are deliberately
# *not* terminators — a clause chain like "他走着，想着昨晚那事" is one
# breathing sentence, whereas "他走着。想着昨晚那事。" is the staccato tic
# this pass is built to catch.
_RHYTHM_TERMINATORS = "。！？…"


def _split_sentences_with_offsets(text: str, base: int) -> list[tuple[int, int]]:
    """Split ``text`` into sentence ``(start, end)`` ranges (absolute offsets).

    ``end`` is half-open and includes the run of trailing terminators
    ("…。" etc.). Offsets are ``base``-relative so callers can splice back
    into the original chapter markdown.
    """

    spans: list[tuple[int, int]] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i] in _RHYTHM_TERMINATORS:
            j = i + 1
            while j < n and text[j] in _RHYTHM_TERMINATORS:
                j += 1
            spans.append((base + start, base + j))
            start = j
            i = j
        else:
            i += 1
    if start < n:
        spans.append((base + start, base + n))
    return spans


def _rhythm_visible_len(sentence: str) -> int:
    """Content length of a sentence: non-whitespace chars sans terminators.

    Commas/、 count — they are part of the clause and a comma-rich sentence
    is exactly the flowing prose we do *not* want to flag.
    """

    return sum(
        1 for c in sentence if not c.isspace() and c not in _RHYTHM_TERMINATORS
    )


def _detect_rhythm(
    content_md: str,
    *,
    lang: str,
    dialogue_ranges: list[tuple[int, int]],
    rhythm_rules: tuple[dict[str, Any], ...],
) -> list[AiFlavorSpan]:
    """Structural (not lexical) AI-flavor pass — catches 碎句癖.

    The phrase/cluster rules are blind to syntax: a paragraph chopped into
    subjectless equal-length fragments ("风是从楼上下来的。冷得不正常。带着
    铁锈味。") contains no banned *word*, so it scores 0 there. This pass
    measures rhythm instead — per narration paragraph, the mean sentence
    length and the longest run of short sentences — and emits one advisory
    ``warn`` span per choppy paragraph. Warns never auto-patch (no
    suggestion, not block) so the writer-side prompt remains the real fix;
    the span only surfaces the regression in the score + audit trail.
    """

    out: list[AiFlavorSpan] = []
    for rule in rhythm_rules:
        category = str(rule.get("category") or "choppy_rhythm")
        severity = _coerce_severity(rule.get("severity"), default="warn")
        rule_id = str(rule.get("id") or f"{lang}.rhythm.choppy")
        why_base = str(rule.get("why") or "")
        min_sentences = max(int(rule.get("min_sentences", 3)), 1)
        short_chars = int(rule.get("short_sentence_chars", 8))
        choppy_mean = float(rule.get("choppy_mean_chars", 11))
        run_threshold = int(rule.get("short_run_threshold", 4))

        offset = 0
        for para in content_md.split("\n"):
            base = offset
            offset += len(para) + 1  # account for the consumed "\n"
            stripped = para.strip()
            if not stripped or stripped.startswith("#"):
                continue

            narration: list[tuple[int, int, int]] = []
            for sent_start, sent_end in _split_sentences_with_offsets(para, base):
                body = content_md[sent_start:sent_end]
                if not body.strip() or _is_in_ranges(sent_start, dialogue_ranges):
                    continue
                vis = _rhythm_visible_len(body)
                if vis == 0:
                    continue
                narration.append((sent_start, sent_end, vis))

            if len(narration) < min_sentences:
                continue

            lengths = [v for (_, _, v) in narration]
            mean_len = sum(lengths) / len(lengths)
            max_run = run = 0
            for v in lengths:
                if v <= short_chars:
                    run += 1
                    max_run = max(max_run, run)
                else:
                    run = 0

            mean_hit = mean_len <= choppy_mean
            run_hit = max_run >= run_threshold
            if not (mean_hit or run_hit):
                continue

            reasons: list[str] = []
            if mean_hit:
                reasons.append(f"段均句长{mean_len:.1f}字≤{choppy_mean:.0f}")
            if run_hit:
                reasons.append(f"连续{max_run}句≤{short_chars}字")
            why = (
                f"{why_base}（{'，'.join(reasons)}）" if why_base else "，".join(reasons)
            )

            span_start = narration[0][0]
            span_end = narration[-1][1]
            out.append(
                AiFlavorSpan(
                    start=span_start,
                    end=span_end,
                    matched_text=content_md[span_start:span_end],
                    rule_id=rule_id,
                    category=category,
                    severity=severity,
                    suggestions=(),
                    sentence_span=(span_start, span_end),
                    why=why,
                    remove_sentence_on_block=False,
                )
            )
    return out


def _score(spans: tuple[AiFlavorSpan, ...]) -> float:
    """Heuristic 0-100 score. Higher = more AI-flavored.

    Weights chosen to map onto the four-tier semantic from the design
    doc (<25 clean, 25-49 warn, 50-74 dirty, ≥75 block). Calibrate on
    the golden set during Phase 6 — for v1 the weights are intentionally
    coarse and easy to reason about.
    """

    total = 0.0
    for span in spans:
        if span.severity == "block":
            total += 12.0
        elif span.severity == "warn":
            total += 4.0
        else:
            total += 1.0
    return min(total, 100.0)


def detect(
    content_md: str,
    *,
    language: str | None = None,
    chapter_number: int = 0,
    data_dir: Path | None = None,
) -> AiFlavorReport:
    """Detect span-level AI-flavor issues in ``content_md``.

    Returns an empty report (score 0) for empty input or when the pattern
    file is missing — failing open lets pipelines opt in incrementally.
    """

    if not content_md:
        return AiFlavorReport(
            language=_normalise_language(language),
            chapter_number=chapter_number,
            overall_score=0.0,
            spans=(),
        )

    lang = _normalise_language(language)
    effective_dir = data_dir or DEFAULT_DATA_DIR
    rules = _load_rules(lang, str(effective_dir))

    haystack = content_md.lower() if rules.case_insensitive else content_md
    quote_pairs = _QUOTE_PAIRS_EN if lang == "en" else _QUOTE_PAIRS_CN
    dialogue_ranges = _find_dialogue_ranges(content_md, quote_pairs)

    spans: list[AiFlavorSpan] = []

    # ── Phrase / pattern rules ──────────────────────────────────────────
    for rule in rules.phrase_rules:
        phrase = rule.get("phrase") or ""
        pattern = rule.get("pattern") or ""
        if not phrase and not pattern:
            continue
        severity = _coerce_severity(rule.get("severity"), default="block")
        suggestions = tuple(s for s in (rule.get("suggestions") or ()) if isinstance(s, str))
        remove_on_block = bool(rule.get("remove_sentence_on_block", True))
        rule_id = str(rule.get("id") or f"{lang}.phrase.{phrase or pattern}")
        category = str(rule.get("category") or "phrase")
        why = str(rule.get("why") or "")

        matches: list[tuple[int, int]] = []
        if pattern:
            flags = 0
            if rules.case_insensitive or bool(rule.get("case_insensitive")):
                flags |= re.IGNORECASE
            compiled = re.compile(str(pattern), flags)
            matches = [(m.start(), m.end()) for m in compiled.finditer(content_md)]
        else:
            needle = phrase.lower() if rules.case_insensitive else phrase
            matches = [
                (offset, offset + len(needle))
                for offset in _find_all_occurrences(haystack, needle)
            ]

        for offset, end in matches:
            if _is_in_ranges(offset, dialogue_ranges):
                continue
            sent_span = _sentence_bounds(content_md, offset, lang)
            spans.append(
                AiFlavorSpan(
                    start=offset,
                    end=end,
                    matched_text=content_md[offset:end],
                    rule_id=rule_id,
                    category=category,
                    severity=severity,
                    suggestions=suggestions,
                    sentence_span=sent_span,
                    why=why,
                    remove_sentence_on_block=remove_on_block,
                )
            )

    # ── Cluster rules ───────────────────────────────────────────────────
    for cluster in rules.cluster_rules:
        members: dict[str, list[str]] = cluster.get("members") or {}
        if not members:
            continue
        threshold = int(cluster.get("threshold", 3))
        severity = _coerce_severity(cluster.get("severity"), default="warn")
        rule_id = str(cluster.get("id") or f"{lang}.cluster.{cluster.get('category', 'misc')}")
        category = str(cluster.get("category") or "cluster")
        why = str(cluster.get("why") or "")

        # Collect every occurrence of every member, ordered by position.
        occurrences: list[tuple[int, str, tuple[str, ...]]] = []
        for member, member_suggestions in members.items():
            needle = member.lower() if rules.case_insensitive else member
            sugg = tuple(s for s in member_suggestions if isinstance(s, str))
            for offset in _find_all_occurrences(haystack, needle):
                if _is_in_ranges(offset, dialogue_ranges):
                    continue
                occurrences.append((offset, member, sugg))
        if len(occurrences) < threshold:
            continue
        occurrences.sort(key=lambda x: x[0])

        # Keep first hit of each distinct member; flag the rest. This
        # preserves the *first* legitimate use of e.g. "缓缓" so the gate
        # doesn't strip prose down to monotone, but kills the lock-in.
        seen_members: set[str] = set()
        for offset, member, sugg in occurrences:
            if member not in seen_members:
                seen_members.add(member)
                continue
            needle = member.lower() if rules.case_insensitive else member
            end = offset + len(needle)
            sent_span = _sentence_bounds(content_md, offset, lang)
            spans.append(
                AiFlavorSpan(
                    start=offset,
                    end=end,
                    matched_text=content_md[offset:end],
                    rule_id=f"{rule_id}:{member}",
                    category=category,
                    severity=severity,
                    suggestions=sugg,
                    sentence_span=sent_span,
                    why=why,
                    remove_sentence_on_block=False,
                )
            )

    # ── Structural rhythm rules (碎句癖) ────────────────────────────────
    if rules.rhythm_rules:
        spans.extend(
            _detect_rhythm(
                content_md,
                lang=lang,
                dialogue_ranges=dialogue_ranges,
                rhythm_rules=rules.rhythm_rules,
            )
        )

    spans.sort(key=lambda s: (s.start, s.end))
    return AiFlavorReport(
        language=lang,
        chapter_number=chapter_number,
        overall_score=_score(tuple(spans)),
        spans=tuple(spans),
    )


def _coerce_severity(raw: Any, *, default: Severity) -> Severity:
    if raw in ("block", "warn", "info"):
        return raw  # type: ignore[return-value]
    return default
