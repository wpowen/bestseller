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

from collections import Counter
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
    staccato_rules: tuple[dict[str, Any], ...]
    terse_tag_rules: tuple[dict[str, Any], ...]
    discourse_rules: tuple[dict[str, Any], ...]


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
            staccato_rules=(),
            terse_tag_rules=(),
            discourse_rules=(),
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _LoadedRules(
        language=language,
        case_insensitive=bool(raw.get("case_insensitive", language == "en")),
        phrase_rules=tuple(raw.get("phrase_rules") or ()),
        cluster_rules=tuple(raw.get("cluster_rules") or ()),
        rhythm_rules=tuple(raw.get("rhythm_rules") or ()),
        staccato_rules=tuple(raw.get("staccato_rules") or ()),
        terse_tag_rules=tuple(raw.get("terse_tag_rules") or ()),
        discourse_rules=tuple(raw.get("discourse_rules") or ()),
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
        # Calibration (2026-06-13, validated on real MiniMax-M3 stress output):
        # the bare ``mean_hit`` branch false-positives on skilled emotional
        # restraint — short sentences that lead with *varied pronoun subjects*
        # ("他…。她…。他…。") and carry distinct concrete actions read well.
        # We therefore EXEMPT only that one shape (pronoun-led AND varied); every
        # other low-mean paragraph still fires — subjectless fragments
        # ("冷得不正常。带着铁锈味。"), same-pronoun droning ("他没…。他只…。"),
        # and noun-staccato. ``run_hit`` (a long ultra-short run) always fires.
        pronoun_lead_min = float(rule.get("pronoun_lead_min", 0.6))
        pronoun_concentration_max = float(rule.get("pronoun_concentration_max", 0.75))

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

            run_hit = max_run >= run_threshold
            # Exempt the one good-craft shape: pronoun-led AND varied.
            pronoun_leads = [
                content_md[s:e].strip()[:1] for (s, e, _) in narration
            ]
            pronoun_leads = [c for c in pronoun_leads if c in "他她它我你咱俺"]
            pronoun_lead_ratio = len(pronoun_leads) / len(narration)
            if pronoun_leads:
                top_pronoun = max(
                    pronoun_leads.count(c) for c in set(pronoun_leads)
                )
                concentration = top_pronoun / len(narration)
            else:
                concentration = 0.0
            pronoun_led_varied = (
                pronoun_lead_ratio >= pronoun_lead_min
                and concentration < pronoun_concentration_max
            )
            mean_hit = mean_len <= choppy_mean and not pronoun_led_varied
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


def _leading_subject(line: str) -> str:
    """Heuristic sentence-initial subject token for repetition detection.

    Returns a short opener key — a leading pronoun (他/她/它), a 2-3 char
    proper-name-like run, or the first character otherwise. Used only to
    spot the mechanical "他…。他…。他…。" hammering, so precision over
    recall is fine.
    """

    s = line.strip().lstrip("　 ")
    if not s:
        return ""
    if s[0] in "他她它":
        return s[0]
    # Leading run of CJK name-ish chars (stop at a verb-ish/particle char).
    run = []
    for ch in s[:3]:
        if "一" <= ch <= "鿿":
            run.append(ch)
        else:
            break
    return "".join(run) if run else s[0]


def _detect_staccato(
    content_md: str,
    *,
    lang: str,
    dialogue_ranges: list[tuple[int, int]],
    staccato_rules: tuple[dict[str, Any], ...],
) -> list[AiFlavorSpan]:
    """Chapter-level staccato-saturation pass — the cross-paragraph 装腔病.

    ``_detect_rhythm`` only sees choppiness *inside* one paragraph (needs
    ≥3 sentences there). The dominant real-output tic is the inverse: every
    short sentence gets its own paragraph, so each paragraph holds a single
    sentence and the choppy rule scores 0. This pass walks paragraph units
    (non-empty, non-heading lines), counts the ones that are a single short
    narration sentence, and flags the chapter when those solo short lines
    *saturate* — by ratio, by consecutive run, or by mechanically repeated
    sentence-initial subjects. One advisory ``warn`` span per chapter; like
    the rhythm pass it never auto-patches, so the writer-side ceiling is the
    real fix and the span only surfaces the regression in score + audit.
    """

    out: list[AiFlavorSpan] = []
    for rule in staccato_rules:
        category = str(rule.get("category") or "staccato_saturation")
        severity = _coerce_severity(rule.get("severity"), default="warn")
        rule_id = str(rule.get("id") or f"{lang}.staccato.saturation")
        why_base = str(rule.get("why") or "")
        max_chars = int(rule.get("solo_line_max_chars", 12))
        min_solo = int(rule.get("min_solo_lines", 8))
        ratio_threshold = float(rule.get("solo_ratio_threshold", 0.33))
        run_threshold = int(rule.get("run_threshold", 4))
        subject_threshold = int(rule.get("subject_repeat_threshold", 3))

        # Walk paragraph units (split on newlines; blank lines are skipped
        # but do NOT break a run — in webnovel markdown every paragraph is
        # blank-line separated, so "consecutive" means consecutive units).
        narration_total = 0
        prose_total = 0  # non-dialogue narration paragraphs (ratio denominator)
        solo_lines: list[tuple[int, int]] = []  # (offset, end) of each solo line
        run = 0
        max_run = 0
        first_solo_offset: int | None = None
        last_solo_end: int | None = None
        subj_run = 0
        max_subj_run = 0
        prev_subject = ""

        offset = 0
        for raw_line in content_md.split("\n"):
            base = offset
            offset += len(raw_line) + 1
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _is_in_ranges(base, dialogue_ranges) or stripped[0] in "“\"「『‘『":
                # Dialogue paragraph — legit, and it breaks the staccato run.
                narration_total += 1
                run = 0
                subj_run = 0
                prev_subject = ""
                continue
            narration_total += 1
            prose_total += 1

            # Single-sentence? (no internal terminator before a trailing one)
            core = stripped.rstrip("。！？…")
            visible = sum(1 for c in core if not c.isspace())
            is_single = not re.search(r"[。！？…].", core)
            if is_single and 0 < visible <= max_chars:
                line_start = base + (len(raw_line) - len(raw_line.lstrip()))
                solo_lines.append((line_start, line_start + len(stripped)))
                if first_solo_offset is None:
                    first_solo_offset = line_start
                last_solo_end = line_start + len(stripped)
                run += 1
                max_run = max(max_run, run)
                subject = _leading_subject(stripped)
                if subject and subject == prev_subject:
                    subj_run += 1
                else:
                    subj_run = 1
                prev_subject = subject
                max_subj_run = max(max_subj_run, subj_run)
            else:
                run = 0
                subj_run = 0
                prev_subject = ""

        solo_count = len(solo_lines)
        if prose_total == 0 or solo_count == 0:
            continue
        # Ratio over NON-dialogue narration only — heavy dialogue must not mask
        # staccato narration (a chapter can be 40% single-sentence narration yet
        # read <30% when dialogue paragraphs dilute the denominator).
        ratio = solo_count / prose_total

        ratio_hit = solo_count >= min_solo and ratio >= ratio_threshold
        run_hit = max_run >= run_threshold
        subject_hit = max_subj_run >= subject_threshold
        if not (ratio_hit or run_hit or subject_hit):
            continue

        reasons: list[str] = []
        if ratio_hit:
            reasons.append(f"单句独段{solo_count}/{prose_total}叙述段({ratio*100:.0f}%)")
        if run_hit:
            reasons.append(f"连续{max_run}段单句独行")
        if subject_hit:
            reasons.append(f"连续{max_subj_run}句同主语开头")
        why = f"{why_base}（{'，'.join(reasons)}）" if why_base else "，".join(reasons)

        span_start = first_solo_offset if first_solo_offset is not None else 0
        span_end = last_solo_end if last_solo_end is not None else span_start
        out.append(
            AiFlavorSpan(
                start=span_start,
                end=span_end,
                matched_text=content_md[span_start:span_end][:60],
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


# Bare-attribution verbs: the tag IS just "who said it", no action beat.
_TERSE_TAG_VERBS = "说道问应答喝骂叫嚷"
_OPEN_QUOTES = "“\"「『‘"
_CLOSE_QUOTES = "”\"」』’"
# A short emotional one-word line glued to a bare tag, e.g. 「冷。」他说。
# Matched starting AT the closing quote.
_TERSE_TAG_RE = re.compile(
    r"[" + _CLOSE_QUOTES + r"]\s*[，,。]?\s*"
    r"(?:他|她|它|[一-鿿]{2,3})[" + _TERSE_TAG_VERBS + r"]"
    r"(?P<after>.?)"
)


def _detect_terse_tag(
    content_md: str,
    *,
    lang: str,
    terse_tag_rules: tuple[dict[str, Any], ...],
) -> list[AiFlavorSpan]:
    """Terse bare-dialogue-tag saturation — the 「冷。」他说 model-ism.

    A one-word emotional line glued to a bare attribution with no action
    beat is fine once; a chapter strung together from them reads like a
    model. We count quotes whose *visible* content is ≤ ``max_quote_chars``
    that are immediately followed by a bare ``X说/道`` tag (terminator or
    newline right after — i.e. no trailing action beat), and flag the
    chapter once the count crosses ``threshold``. One advisory ``warn``
    span; never auto-patches (writer-side beat is the real fix).
    """

    out: list[AiFlavorSpan] = []
    for rule in terse_tag_rules:
        category = str(rule.get("category") or "terse_dialogue_tag")
        severity = _coerce_severity(rule.get("severity"), default="warn")
        rule_id = str(rule.get("id") or f"{lang}.terse_tag.bare")
        why = str(rule.get("why") or "")
        max_quote_chars = int(rule.get("max_quote_chars", 3))
        threshold = int(rule.get("threshold", 3))

        hits: list[tuple[int, int]] = []
        # Walk quotes; for each short quote, inspect the immediately
        # following bare tag.
        i = 0
        n = len(content_md)
        while i < n:
            ch = content_md[i]
            if ch not in _OPEN_QUOTES:
                i += 1
                continue
            # Find the matching close quote.
            close_idx = -1
            for j in range(i + 1, n):
                if content_md[j] in _CLOSE_QUOTES:
                    close_idx = j
                    break
            if close_idx < 0:
                break
            inner = content_md[i + 1 : close_idx]
            visible = sum(1 for c in inner.rstrip("。！？，,") if not c.isspace())
            if 0 < visible <= max_quote_chars:
                m = _TERSE_TAG_RE.match(content_md, close_idx)
                if m is not None:
                    after = m.group("after")
                    # Bare = the tag is end-of-clause: terminator/newline/EOF.
                    if after == "" or after in "。！？，,\n":
                        hits.append((i, m.end()))
            i = close_idx + 1

        if len(hits) < threshold:
            continue
        span_start = hits[0][0]
        span_end = hits[-1][1]
        out.append(
            AiFlavorSpan(
                start=span_start,
                end=span_end,
                matched_text=content_md[span_start : span_start + 30],
                rule_id=rule_id,
                category=category,
                severity=severity,
                suggestions=(),
                sentence_span=(span_start, span_end),
                why=f"{why}（共{len(hits)}处）" if why else f"{len(hits)} terse tags",
                remove_sentence_on_block=False,
            )
        )
    return out


def _detect_discourse(
    content_md: str,
    *,
    lang: str,
    dialogue_ranges: list[tuple[int, int]],
    discourse_rules: tuple[dict[str, Any], ...],
) -> list[AiFlavorSpan]:
    """Density-gated discourse-level tells (the sticky ones prompt can't kill).

    Each rule has a regex ``pattern`` and a gate: count narration-only matches
    and, if they cross it, emit ONE advisory ``warn`` span for the chapter
    (anchored at the first hit). Used for constructs that are fine in
    moderation but read as a tic when repeated — anonymous crowd-reaction
    beats, 「他没X」negative-action filler. Advisory only (no suggestion →
    patcher skips); capped at the score layer.

    Two gate flavours:

    * ``threshold`` (default) — absolute match count. Right for constructs
      that are rare-by-nature, where one extra occurrence is one too many.
    * ``per_1k_threshold`` — matches per 1000 characters. Right for
      punctuation and other constructs whose acceptable count scales with
      chapter length, so a 6000-char chapter is not flagged for doing twice
      what a 3000-char chapter does. A rate is only meaningful at chapter
      scale, so these rules also require ``min_chars`` of text (default 1200)
      and ``threshold`` raw hits (default 3) before the rate is consulted —
      otherwise one dash in a 20-char scene fragment reads as 50 per 1000.

    ``escalate_per_1k`` adds a second, higher band: above it the span is
    re-tagged with ``escalate_category``. That is how a rule separates
    "denser than a human writer, worth noting" from "pathological, must be
    rewritten" — the two bands can then be wired to different consequences
    (see ``DESLOP_DISCOURSE_CATEGORIES``). Without this, folding N matches
    into one flat span made 2 hits and 170 hits score identically.
    """

    out: list[AiFlavorSpan] = []
    total_chars = max(1, len(content_md))
    for rule in discourse_rules:
        raw_pat = rule.get("pattern")
        if not raw_pat:
            continue
        try:
            pattern = re.compile(str(raw_pat))
        except re.error:
            continue
        category = str(rule.get("category") or "discourse")
        severity = _coerce_severity(rule.get("severity"), default="warn")
        rule_id = str(rule.get("id") or f"{lang}.discourse.{category}")
        why = str(rule.get("why") or "")

        hits: list[tuple[int, int]] = []
        for m in pattern.finditer(content_md):
            if _is_in_ranges(m.start(), dialogue_ranges):
                continue
            hits.append((m.start(), m.end()))
        if not hits:
            continue

        raw_per_1k = rule.get("per_1k_threshold")
        rate = len(hits) / total_chars * 1000.0
        if raw_per_1k is not None:
            if total_chars < int(rule.get("min_chars", 1200)):
                continue
            if len(hits) < int(rule.get("threshold", 3)):
                continue
            if rate < float(raw_per_1k):
                continue
            magnitude = f"{len(hits)}处/{rate:.1f}每千字"
        else:
            if len(hits) < int(rule.get("threshold", 3)):
                continue
            magnitude = f"共{len(hits)}处"

        escalate_at = rule.get("escalate_per_1k")
        if escalate_at is not None and rate >= float(escalate_at):
            category = str(rule.get("escalate_category") or category)
            severity = _coerce_severity(
                rule.get("escalate_severity"), default=severity
            )

        span_start, _ = hits[0]
        _, span_end = hits[-1]
        out.append(
            AiFlavorSpan(
                start=span_start,
                end=span_end,
                matched_text=content_md[span_start : span_start + 30],
                rule_id=rule_id,
                category=category,
                severity=severity,
                suggestions=(),
                sentence_span=(span_start, span_end),
                why=f"{why}（{magnitude}）" if why else magnitude,
                remove_sentence_on_block=False,
                hit_count=len(hits),
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

    # Structural-rhythm signals (choppy_rhythm / staccato_saturation) are
    # content-blind: they cannot reliably tell skilled emotional fragments
    # ("用袖口。" / "牙长齐了。") from 伪文学装腔 ("冷得不正常。带着铁锈味。").
    # They stay advisory — visible in the report — but their *combined* score
    # contribution is capped so a richly-fragmented but good chapter can never
    # be pushed over the block threshold (→ quality-degrading repair) on rhythm
    # alone. Reflective/lexical signals (epiphany / not-X-but-Y / tier-1
    # clichés) are not capped and remain blockable.
    # Content-blind signals that count constructs/words which also appear
    # abundantly in *normal* good prose (rhythm fragments; emotion words like
    # 震惊/恐惧; filter/cognition words like 知道/明白/一种/似乎; abstract
    # adjectives like 强大/神秘). Their score is real-but-noisy, so the family's
    # *combined* contribution is capped — they stay advisory (visible) but can
    # never alone push a normal chapter over the block threshold into
    # quality-degrading repair. Distinctive high-confidence signals (tier-1
    # clichés, epiphany/not-X-but-Y over-reliance, terse tags) are NOT capped.
    _ADVISORY_STRUCTURAL = {
        "choppy_rhythm",
        "staccato_saturation",
        "emotion_label_density",
        "filter_word_density",
        "abstract_evaluation_density",
        "crowd_reaction_beat",
        "negative_action_filler",
        "face_emotion_label",
        "empty_reaction_shot",
        # Density-gated translationese-adjacent tells (anti-vibe-writing fusion,
        # 2026-07-03): legitimate in moderation, so they stay advisory-capped.
        # Distinctive shapes (category "translationese") are NOT capped.
        "lifted_copula",
        # Only the mild dash band is capped. Its escalated twin "dash_train"
        # (≥10 dashes/1k chars — above the max of 1187 real published
        # chapters) is deliberately absent so it scores uncapped: at that
        # density the punctuation is not a stylistic preference, it is the
        # chapter's dominant sentence-joining device.
        "dash_density",
        "then_now_contrast",
        "adjective_colon_verdict",
        # Simile DENSITY is noisy (a genuinely image-rich chapter can be high)
        # so it stays advisory-capped: visible + drives deslop, never blocks
        # alone. The cross-modal 通感病句 (synaesthesia_mismatch) is a real
        # error and is deliberately NOT listed here so it can raise the score.
        "simile_overrun",
        # Inner-voice ABSENCE is a chapter-level readability nudge, not a
        # per-sentence defect — advisory forever, never a block driver.
        "inner_voice_absence",
        # 母题族饱和：判据（密度越 p99 + ≥2 子族）虽是强信号，但它测的是
        # *题材内容*，不是句法 tell。给它独立推高分数=把一本真写债务/丧礼的
        # 书推进 block→重写，正是 debt_metaphor_leak 退役的死因。封顶留在
        # advisory，靠 deslop 触发集拿定向重写，不靠分数。
        "motif_saturation",
        # 对话占比是**章级可读性提示**，与 inner_voice_absence 同族：
        # 它测的是这一章有没有人开口，不是句法 tell。给它独立推高分数
        # = 把一章合法的独处/赶路章推进 block→重写。advisory forever。
        "dialogue_starvation",
    }
    _STRUCTURAL_CAP = 24.0

    total = 0.0
    structural = 0.0
    for span in spans:
        if span.severity == "block":
            weight = 12.0
        elif span.severity == "warn":
            weight = 4.0
        else:
            weight = 1.0
        if span.category in _ADVISORY_STRUCTURAL:
            structural += weight
        else:
            total += weight
    total += min(structural, _STRUCTURAL_CAP)
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
        # ``family_flag``: when true, the whole member set is one template
        # *family* — over-reliance counts even when each hit is a different
        # member (e.g. 瞳孔一缩 / 心一沉 / 眉心一皱 are all the same body-tic
        # template). Default false keeps the per-member-first semantics used
        # by weak_adverb etc. (preserve the first 缓缓, flag only repeats).
        family_flag = bool(cluster.get("family_flag", False))
        # ``advisory_only``: emit detection spans for scoring/audit but attach
        # NO replacement suggestion, so the patcher leaves the prose untouched.
        # Required for clusters whose members are *predicate phrases*
        # (瞳孔一缩 / 心头一紧 / 忽然明白): a static empty-string "delete" would
        # strip a load-bearing verb and break the sentence
        # ("他忽然意识到一件事。" → "他一件事。"). Only safe-to-delete *modifier*
        # clusters (缓缓 / 其实) should carry an empty-string suggestion.
        advisory_only = bool(cluster.get("advisory_only", False))

        # Collect every occurrence of every member, ordered by position.
        occurrences: list[tuple[int, str, tuple[str, ...]]] = []
        for member, member_suggestions in members.items():
            needle = member.lower() if rules.case_insensitive else member
            sugg = (
                ()
                if advisory_only
                else tuple(s for s in member_suggestions if isinstance(s, str))
            )
            for offset in _find_all_occurrences(haystack, needle):
                if _is_in_ranges(offset, dialogue_ranges):
                    continue
                occurrences.append((offset, member, sugg))
        if len(occurrences) < threshold:
            continue
        occurrences.sort(key=lambda x: x[0])

        # Keep first hit of each distinct member (or, in family mode, the
        # first hit overall); flag the rest. This preserves the *first*
        # legitimate use so the gate doesn't strip prose to monotone, but
        # kills the lock-in.
        seen_members: set[str] = set()
        family_seen = False
        for offset, member, sugg in occurrences:
            if family_flag:
                if not family_seen:
                    family_seen = True
                    continue
            elif member not in seen_members:
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

    # ── Cross-paragraph staccato saturation (独行短句装腔) ───────────────
    if rules.staccato_rules:
        spans.extend(
            _detect_staccato(
                content_md,
                lang=lang,
                dialogue_ranges=dialogue_ranges,
                staccato_rules=rules.staccato_rules,
            )
        )

    # ── Terse bare dialogue-tag saturation (「冷。」他说) ─────────────────
    if rules.terse_tag_rules:
        spans.extend(
            _detect_terse_tag(
                content_md,
                lang=lang,
                terse_tag_rules=rules.terse_tag_rules,
            )
        )

    # ── Discourse-level density tells (他没X 填充 / 群体反应beat重复) ─────
    if rules.discourse_rules:
        spans.extend(
            _detect_discourse(
                content_md,
                lang=lang,
                dialogue_ranges=dialogue_ranges,
                discourse_rules=rules.discourse_rules,
            )
        )

    # ── Embodied-verb tic saturation (撞/烫/爬 复读) ─────────────────────
    spans.extend(_detect_verb_tic_spam(content_md, lang=lang))

    # ── 无生命主语拟人动词过密 (凿子吃进/石头拱 万物皆动腔) ─────────────
    spans.extend(_detect_inanimate_agency(content_md, lang=lang))
    spans.extend(_detect_dialogue_starvation(content_md, lang=lang))

    # ── Chapter-level repetition (车轱辘内心戏 / 感觉词堆叠) ─────────────
    spans.extend(_detect_repetition(content_md, lang=lang))

    # ── 明喻过密 / 跨模态通感病句 (什么都像什么 / 响湿得像) ──────────────
    spans.extend(_detect_simile_overrun(content_md, lang=lang))

    # ── 正文债务化比喻回流 (概念层干净但写手自己长出"欠条/认账") ─────────
    spans.extend(_detect_debt_metaphor_leak(content_md, lang=lang))

    # ── 第一人称内心声音缺失 (全章零盘算/自问 → 冷读者跟不上动机) ────────
    spans.extend(_detect_inner_voice_absence(content_md, lang=lang))

    # ── 默认母题族饱和 (一族意象占满全章，不是"出现"是"支配") ───────────
    spans.extend(_detect_motif_saturation(content_md, lang=lang))

    # ── 对话饥饿 (整章几乎没有人说话 → 评委口中的断代级差距) ─────────────
    spans.extend(_detect_dialogue_famine(content_md, lang=lang))

    spans.sort(key=lambda s: (s.start, s.end))
    return AiFlavorReport(
        language=lang,
        chapter_number=chapter_number,
        overall_score=_score(tuple(spans)),
        spans=tuple(spans),
    )


# Body-sensation chars whose stacking/over-repetition signals 车轱辘 inner
# monologue (the same feeling written over and over with new skin).
_SENSATION_CHARS = "麻凉酸痒胀痛钻爬渗顶涩"
_SENSATION_STACK_RE = re.compile(
    r"(?:[" + _SENSATION_CHARS + r"][^一-鿿]{0,2}){2,}[" + _SENSATION_CHARS + r"]"
)
# Function chars that recur naturally; a 4-gram made almost entirely of these
# is structural, not a repeated *meaningful* phrase.
_GRAM_FUNCTION_CHARS = frozenset("的了在是这那他她你我们个一不没有就都也很还又把被让将对向于之其着过再")
# Near-copy line detection: an ≥8-char substring repeated within this char
# window is the same line written twice (not far-apart 首尾呼应).
_NEARCOPY_N = 8
_NEARCOPY_WINDOW = 600
_NEARCOPY_PUNCT = frozenset("，。、！？；：")


_VERB_TIC_LEXICON_ZH: tuple[str, ...] = (
    # High-impact embodied verbs the writer models over-reuse chapter-wide.
    # Any one of them is fine; the tic is FREQUENCY (真机: 爬×124 / 烫×101 /
    # 钻×83 across 10 chapters — an order of magnitude above human prose).
    "撞", "烫", "钻", "咬", "爬", "砸", "碾", "蹿", "拧", "洇", "攥", "掐",
    # Added 2026-07-21 from a live chapter-first run: 压 and 抽 were the two
    # most-abused verbs in shipped prose (压×50 / 抽×39 over 8 chapters, one
    # chapter carrying 压×11) yet both were absent here, so the gate scored
    # those chapters 4-16 and passed them. A lexicon that misses the top
    # offenders makes the whole tic detector look clean while the prose reads
    # mechanical.
    "压", "抽",
)

_MEASURE_TIC_RE = re.compile(r"半寸|一寸|三寸|半尺|三分|半息|半分")


def _detect_verb_tic_spam(content_md: str, *, lang: str) -> list[AiFlavorSpan]:
    """Chapter-level embodied-verb tic saturation — the "撞/烫/爬" AI accent.

    Flags verbs whose per-10k-char frequency AND absolute count both exceed
    human-prose ceilings, plus measurement-phrase (半寸/三分) saturation. One
    advisory span per chapter; the writer-prompt lexical discipline block is
    the real ceiling, this pass surfaces regressions in score + audit.
    """

    if lang != "zh" or not content_md:
        return []
    total = len(content_md)
    if total < 800:
        return []
    offenders: list[tuple[str, int]] = []
    for verb in _VERB_TIC_LEXICON_ZH:
        count = content_md.count(verb)
        if count >= 6 and count * 10000 // total >= 6:
            offenders.append((verb, count))
    measure_hits = len(_MEASURE_TIC_RE.findall(content_md))
    if measure_hits >= 5 and measure_hits * 10000 // total >= 5:
        offenders.append(("半寸/一寸/三分类度量腔", measure_hits))
    # 词族聚合口径(2026-07-08): 单词各只出现3-4次躲过上面的单词阈值,但
    # 全章"撞烫钻攥爬"家族合计6-9次、跨章复读——读者连读时词族感极强
    # (真机用户终审:"老是出现撞烫,百分百是人不会写的")。家族合计≥6且
    # ≥15/万字即计一名 offender,经 deslop 触发集清理。
    if not offenders:
        family_total = sum(content_md.count(v) for v in _VERB_TIC_LEXICON_ZH)
        if family_total >= 6 and family_total * 10000 // total >= 15:
            offenders.append(("撞/烫/钻/攥/爬类具身动词词族(合计)", family_total))
    if not offenders:
        return []
    offenders.sort(key=lambda kv: -kv[1])
    worst = offenders[0][0].split("/")[0]
    pos = content_md.find(worst)
    if pos < 0:
        pos = 0
    detail = "、".join(f"{v}×{c}" for v, c in offenders[:6])
    return [
        AiFlavorSpan(
            start=pos,
            end=min(pos + len(worst), total),
            matched_text=worst,
            rule_id="zh.tic.embodied_verb_spam",
            category="verb_tic_spam",
            severity="warn",
            suggestions=(),
            sentence_span=_sentence_bounds(content_md, pos, lang),
            why=(
                f"高冲击具身动词复读（{detail}）——同一动词全章应≤4次；"
                "改用平实动词（闻到/听见/看见/摸到）或换具体动作，"
                "这是读者最容易识别的AI腔之一。"
            ),
            remove_sentence_on_block=False,
        )
    ]


# 无生命主语 + 强施动动词 = 万物拟人腔（2026-08-18《矿脉认主》用户终审
# 「凿子吃进/石头拱了一下——动词总是用错，一个字都不想读」）。
# 病根是「生动压力」：纪律禁了动词复读后，模型改为轮换生僻强动词，
# 给声音/影子/寒意逐句安排吃/咬/爬/蹿这类肢体动作。人类写手 90% 用平实
# 动词，把强动词留给高潮。词表只在检测器层（种词铁律）。
_INANIMATE_SUBJECTS_ZH: tuple[str, ...] = (
    # 跨题材普适的无生命名词（避免题材专名，防题材不公平）
    "影子", "声音", "风", "光", "月光", "灯火", "火把", "血", "字",
    "名字", "空气", "地面", "墙", "雾", "夜色", "寒意", "凉意", "疼痛",
    "痛感", "汗", "石头", "规矩", "热气", "哨声", "靴声", "脚步声",
)
_AGENCY_VERBS_ZH: tuple[str, ...] = (
    # 强施动动词：安在无生命主语上时构成拟人/错搭配。
    # 只收肢体/口部类施动词；状态词（烫/沉/凉）不算拟人——「石头烫」是
    # 合法搭配，收进来会让 deslop 去改合法句（真机 ch2「滚烫石头」教训）。
    "吃进", "拱", "钻", "爬", "舔", "啃", "咬", "挤", "撞", "劈",
    "淌", "蹿", "弹", "压过", "压得", "扑", "抓", "攥", "嚼",
    "吞", "漫过", "犁", "挠", "撕", "掐", "勒", "扎进",
)
_INANIMATE_AGENCY_RE = re.compile(
    r"(" + "|".join(_INANIMATE_SUBJECTS_ZH) + r")"
    r"[^。！？\n]{0,8}"
    r"(" + "|".join(_AGENCY_VERBS_ZH) + r")"
)


def _detect_inanimate_agency(content_md: str, *, lang: str) -> list[AiFlavorSpan]:
    """无生命主语拟人动词过密 —— chapter-level advisory, CJK only.

    量的是「东西在做人的动作」的密度，不是单处修辞：单处拟人是合法重锤，
    逐句拟人是 AI 腔（每个名词都被安排一个"生动"动词，读者读三句就累）。

    校准（2026-08-18，.distillation_private 400 章抽样，同一正则）：
    人类 中位 0.00/千字、p90 0.31、p99 0.95；《矿脉认主》中位 1.25、
    ch1=5.94（人类 p99 的 6 倍）。阈值取人类 p99：密度 ≥1.0/千字 且
    绝对数 ≥4（防短章折叠计数的量级失明）。误报率按校准 ≈1%。
    """

    if lang != "zh" or not content_md:
        return []
    total = len(content_md)
    if total < 800:
        return []
    matches = list(_INANIMATE_AGENCY_RE.finditer(content_md))
    count = len(matches)
    if count < 4 or count * 1000 / total < 1.0:
        return []
    pair_counts: Counter[str] = Counter(
        f"{m.group(1)}…{m.group(2)}" for m in matches
    )
    detail = "、".join(f"{p}×{c}" for p, c in pair_counts.most_common(5))
    first = matches[0]
    return [
        AiFlavorSpan(
            start=first.start(),
            end=first.end(),
            matched_text=content_md[first.start() : first.end()],
            rule_id="zh.tic.inanimate_agency",
            category="inanimate_agency",
            severity="warn",
            suggestions=(),
            sentence_span=_sentence_bounds(content_md, first.start(), lang),
            why=(
                f"万物拟人过密（全章 {count} 处无生命主语接强施动动词，"
                f"{count * 1000 / total:.1f}/千字，人类出版章 p99=0.95；如 {detail}）"
                "——东西只做它物理上真会做的事，用平实动词写"
                "（响、落、晃、停、亮、渗）；拟人化动词是重锤，"
                "全章至多保留 1-2 处最关键的，其余全部换平实说法。"
            ),
            remove_sentence_on_block=False,
        )
    ]


# 对话引号：中文出版章的三种成对写法。只用于**测量占比**，不做任何改写。
_DIALOGUE_QUOTE_RE = re.compile(r"[“\"「][^”\"」]{1,400}[”\"」]")
_ZH_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")

# 人类出版章校准（2026-08-20，.distillation_private 1526 章 / 400 本，同一正则）：
# p05=1.4% p10=3.1% p25=9.3% 中位=20.7% p75=34.0% p90=46.5%。
# 阈值不取分位数而取**实测误报率**：另一份 400 章独立抽样上
# 1.4%→7.5% 误报、1.0%→4.2%、0.8%→2.8%、0.6%→2.0%。取 0.8%
# （实测误报 2.8%），只报「整章确实没有人开口」。
_DIALOGUE_STARVATION_FLOOR = 0.008


def _detect_dialogue_starvation(content_md: str, *, lang: str) -> list[AiFlavorSpan]:
    """整章几乎无人开口 —— chapter-level advisory, CJK only.

    `style_guide.dialogue_ratio` 此前只被**声明进 prompt**（本书声明 0.35），
    全库没有任何一处测量它。真机《罚我守坟》21 章实测中位 6.1%，
    ch08/ch20/ch21 分别 0.8%/0.7%/1.0%——整章基本没有人说话，
    读起来是一条不断的旁白。

    warn-only，且**故意不进 deslop 触发集**：去水器换的是措辞，
    凭空造对话是内容捏造。本观测器只挣留痕。
    """

    if lang != "zh" or not content_md:
        return []
    zh_total = len(_ZH_CHAR_RE.findall(content_md))
    if zh_total < 800:
        return []
    quoted = sum(
        len(_ZH_CHAR_RE.findall(m.group(0)))
        for m in _DIALOGUE_QUOTE_RE.finditer(content_md)
    )
    ratio = quoted / zh_total
    if ratio >= _DIALOGUE_STARVATION_FLOOR:
        return []
    return [
        AiFlavorSpan(
            start=0,
            end=min(40, len(content_md)),
            matched_text=content_md[:40],
            rule_id="zh.structure.dialogue_starvation",
            category="dialogue_starvation",
            severity="warn",
            suggestions=(),
            sentence_span=(0, min(40, len(content_md))),
            why=(
                f"整章几乎没有人开口（引号内文字占 {ratio:.1%}，"
                f"人类出版章 p05=1.4%、中位 20.7%）——"
                "冲突、身份、情绪在网文里主要靠人物当场说出来推进；"
                "整章旁白会让读者失去可代入的落点。"
            ),
            remove_sentence_on_block=False,
        )
    ]


def _detect_repetition(content_md: str, *, lang: str) -> list[AiFlavorSpan]:
    """Chapter-level narrative repetition (车轱辘内心戏) — CJK only.

    Two篇章-level tells the sentence/span rules cannot see:
    1. the same meaningful 4-gram repeated many times (one beat written over
       and over with new skin), and
    2. body-sensation words stacked into 排比 (酸,凉,痒,麻…).
    Only a whole-passage deslop can collapse these, so each emits an advisory
    ``narrative_repetition`` span that routes the chapter to deslop.
    Calibrated on real samples: bad ch004 = 16 repeated grams, good drafts = 0.
    """

    if lang != "zh":
        return []
    out: list[AiFlavorSpan] = []

    grams: Counter[str] = Counter()
    for run in re.findall(r"[一-鿿]+", content_md):
        for i in range(len(run) - 3):
            grams[run[i : i + 4]] += 1
    repeated = [
        (g, c)
        for g, c in grams.items()
        if c >= 5 and sum(1 for ch in g if ch in _GRAM_FUNCTION_CHARS) <= 2
    ]
    if len(repeated) >= 4:
        top_gram, top_count = max(repeated, key=lambda kv: kv[1])
        pos = max(0, content_md.find(top_gram))
        # Severity has to track magnitude. Emitting one fixed ``warn`` meant a
        # chapter with 4 repeated phrases and a chapter with 198 scored exactly
        # the same 4.0 points, so the worst prose in the book cleared the gate:
        # live ch16 had 198 repeated phrases, the top one 75 times, and 47.6% of
        # its 4-grams belonged to a heavy repeat — it passed.
        #
        # ``load`` is the share of the chapter's 4-grams sitting inside a heavy
        # repeat, so it is length-normalised: on the same 50-chapter book the
        # clean chapters measured 0.0–1.2% and the unreadable ones 19.8–47.6%,
        # a clean 15x separation with nothing in between.
        total_grams = sum(grams.values()) or 1
        load = sum(count for _, count in repeated) / total_grams
        severe = load >= 0.15 or top_count >= 20
        out.append(
            AiFlavorSpan(
                start=pos,
                end=pos + len(top_gram),
                matched_text=top_gram,
                rule_id=f"{lang}.repetition.ngram",
                category="narrative_repetition",
                severity="block" if severe else "warn",
                suggestions=(),
                sentence_span=(pos, pos + len(top_gram)),
                why=(
                    f"篇章级车轱辘：{len(repeated)} 个实义短语各重复≥5次"
                    f"（如「{top_gram}」{top_count}次），占全章四字串 {load:.0%}"
                    "——同一意思反复换皮写，"
                    "删冗余、把重复的身体感觉/心理解读合并成一次"
                ),
                remove_sentence_on_block=False,
            )
        )

    for m in _SENSATION_STACK_RE.finditer(content_md):
        out.append(
            AiFlavorSpan(
                start=m.start(),
                end=m.end(),
                matched_text=content_md[m.start() : m.start() + 20],
                rule_id=f"{lang}.repetition.sensation_stack",
                category="narrative_repetition",
                severity="warn",
                suggestions=(),
                sentence_span=(m.start(), m.end()),
                why="身体感觉词排比堆叠（酸凉痒麻胀痛…）——装腔，改成一个具体的身体反应",
                remove_sentence_on_block=False,
            )
        )

    # 3. Near-copy lines: an ≥8-char meaningful substring repeated within a short
    #    window (the same action/description written twice with minor reskinning,
    #    e.g. "他低头看那道旧疤——疤的边缘沾着粉" / "他盯着那道旧疤——疤的边缘沾着粉").
    #    The window excludes far-apart 首尾呼应; good drafts score 0, ch004 = 10.
    #    This catches the residual that n-gram frequency can't (frequency误伤
    #    legitimate prop names) and that逐句 deslop/LLM self-review can't see.
    seen_pos: dict[str, int] = {}
    near_copies: dict[str, int] = {}
    for m in re.finditer(r"[一-鿿，。、]+", content_md):
        run, base = m.group(), m.start()
        for i in range(len(run) - _NEARCOPY_N + 1):
            gram = run[i : i + _NEARCOPY_N]
            if sum(1 for ch in gram if ch not in _NEARCOPY_PUNCT) < 6:
                continue  # mostly punctuation — not a real repeated phrase
            pos = base + i
            prev = seen_pos.get(gram)
            if prev is not None and pos - prev < _NEARCOPY_WINDOW:
                near_copies.setdefault(gram, prev)
            seen_pos[gram] = pos
    if len(near_copies) >= 2:
        first_gram = min(near_copies, key=lambda g: near_copies[g])
        anchor = near_copies[first_gram]
        out.append(
            AiFlavorSpan(
                start=anchor,
                end=anchor + _NEARCOPY_N,
                matched_text=first_gram,
                rule_id=f"{lang}.repetition.near_copy",
                category="narrative_repetition",
                severity="warn",
                suggestions=(),
                sentence_span=(anchor, anchor + _NEARCOPY_N),
                why=(
                    f"近乎复制句：{len(near_copies)} 处≥8字描述在相邻段落里重复"
                    f"（如「{first_gram}」）——同一个动作/描写写了两遍，删一处或并成一句"
                ),
                remove_sentence_on_block=False,
            )
        )
    return out


# Explicit simile connectors (longest-first so the alternation prefers the
# multi-char forms; bare 像 is last). 好像/就像 already contain 像 but the
# regex is non-overlapping so each simile counts once.
_SIMILE_MARKER_RE = re.compile(
    r"仿佛|仿若|宛如|宛若|犹如|恰似|好似|如同|好像|就像|像"
)
# Bare 像 inside noun compounds (画像/头像/偶像/像样/想像…) is not a simile.
# Only the single-char match needs the check — the multi-char connectors are
# unambiguous, and 好像 is consumed as a whole before bare 像 can match.
_SIMILE_FALSE_PRE = "画图影偶头雕塑录人遗佛镜成造想"
_SIMILE_FALSE_POST = "样话素"


def _is_noun_compound_simile(content_md: str, m: re.Match[str]) -> bool:
    if m.group() != "像":
        return False
    pre = content_md[m.start() - 1] if m.start() > 0 else ""
    post = content_md[m.end()] if m.end() < len(content_md) else ""
    return pre in _SIMILE_FALSE_PRE or post in _SIMILE_FALSE_POST
# Moisture/texture attributes that a *sound* physically cannot have. Temperature
# (冷/热) and taste (甜/苦) onto voice are conventional Chinese synaesthesia and
# are deliberately excluded to avoid false positives ("声音冷得像冰").
_SOUND_NOUN_CHARS = "响声音"
_WET_TEXTURE_CHARS = "湿潮黏腻滑"
# sound-noun, then within a few chars a moisture adjective + a simile connector:
# 「闷响，湿得像…」「一声，潮得如同…」. The 得+simile tail is what makes this a
# 病句 rather than a bare (still-odd but rarer) collocation.
_SYNAESTHESIA_WET_SOUND_RE = re.compile(
    r"[" + _SOUND_NOUN_CHARS + r"][，,、。\s]{0,3}"
    r"[" + _WET_TEXTURE_CHARS + r"]得(?:像|如同?|似|跟)"
)


def _detect_simile_overrun(content_md: str, *, lang: str) -> list[AiFlavorSpan]:
    """明喻过密 + 跨模态通感病句 —— CJK only.

    Two tells the phrase/rhythm rules can't see:

    1. **simile_overrun** (advisory, capped): narration-wide simile density.
       cinematic_pov 第 9 条限额靠 prompt，M3 服从性差；真机 ch1 达 81/万字
       （正常散文 ~0-10/万字）。计数超阈值时发一个 advisory span，经
       deslop_revise 闭环喂回写手把"什么都像什么"压回去。

    2. **synaesthesia_mismatch** (warn, NOT capped): 把听觉（响/声/音）说成有
       水分/黏腻（湿/潮/黏/腻/滑）再接明喻 —— 物理不通的通感病句
       （真机首句「门板上三下闷响，湿得像有人拿额头在撞」）。这是真实语病，
       每处单独标记，不进结构性 advisory 上限。
    """

    if lang != "zh" or not content_md:
        return []
    out: list[AiFlavorSpan] = []
    total = len(content_md)

    # 1. Simile density (chapter-level, one advisory span).
    if total >= 800:
        matches = [
            m
            for m in _SIMILE_MARKER_RE.finditer(content_md)
            if not _is_noun_compound_simile(content_md, m)
        ]
        count = len(matches)
        if count >= 8 and count * 10000 // total >= 32:
            m = matches[0]
            pos = m.start()
            out.append(
                AiFlavorSpan(
                    start=pos,
                    end=pos + (len(m.group()) if m else 1),
                    matched_text=content_md[pos : pos + 12],
                    rule_id="zh.simile.overrun",
                    category="simile_overrun",
                    severity="warn",
                    suggestions=(),
                    sentence_span=_sentence_bounds(content_md, pos, lang),
                    why=(
                        f"明喻过密（全章 {count} 处「像/仿佛/如同…」，"
                        f"{count * 10000 // total}/万字，正常散文约≤30/万字）"
                        "——什么都'像什么'会稀释画面，只留最有力的 2-3 个明喻，"
                        "其余改成直接的动作/细节。"
                    ),
                    remove_sentence_on_block=False,
                )
            )

    # 2. Cross-modal synaesthesia病句 (per-occurrence).
    for m in _SYNAESTHESIA_WET_SOUND_RE.finditer(content_md):
        start = m.start()
        out.append(
            AiFlavorSpan(
                start=start,
                end=m.end(),
                matched_text=content_md[start : m.end()],
                rule_id="zh.synaesthesia.wet_sound",
                category="synaesthesia_mismatch",
                severity="warn",
                suggestions=(),
                sentence_span=_sentence_bounds(content_md, start, lang),
                why=(
                    "跨模态病句：声音（响/声/音）没有'湿/潮/黏'这类水分属性，"
                    "却直接接明喻——冷读者会卡住。先落地一个真实的听觉细节"
                    "（闷、钝、发颤、隔着门板），再决定要不要比喻。"
                ),
                remove_sentence_on_block=False,
            )
        )

    return out


# ── 默认母题族饱和 (2026-08-16) ─────────────────────────────────────────────
# 与上面那个**已退役**的 debt_metaphor_leak 是两回事，区别就是它退役的理由：
# 那个把正文里每一个 债/账/欠 都当 AI 味标记，于是在一本本来就写债务的书里
# 「把故事本身删掉」。出现 ≠ 病。
#
# 这里测的是**支配**：整章密度越过人类天花板，且多个子族同时在场。
# 语料标定（.distillation_private 969 章，与 anti_default_motif 同一套正则）：
#   每千字 中位 0.00 / p90 0.38 / p95 0.56 / p99 1.67 / max 14.88
#   子族数 ≥2 的章 占 2.8%
# 真机《破澡堂真话局》（爽文喜剧）：中位 3.88（人类 p99 的 2.3 倍），
#   34/50 章越过 p99，24/50 章 ≥2 子族 —— 相对人类 2.8% 是 17 倍富集。
#   一本澡堂喜剧的正文被丧葬账簿和命债填满，而它的种子里只提过一次「追债的」。
#
# 判据要求**两个条件同时成立**（任一单独都会误伤真写债务的书）：
#   ① 每千字密度 ≥ 人类 p99；② 同时命中 ≥2 个子族。
# 处置遵守铁律：advisory + 计分封顶（永不单独把一章打成 block），只进 deslop
# 触发集拿重写；改写指令只给类别与改法，不给 token（种词铁律）。
_MOTIF_SATURATION_MIN_CHARS = 1200
_MOTIF_SATURATION_PER_1K = 1.67  # 人类 p99
_MOTIF_SATURATION_MIN_DISTINCT = 2  # 人类仅 2.8% 的章达到


def _motif_family_patterns() -> tuple[tuple[str, tuple[re.Pattern[str], ...]], ...]:
    """按**语义**分族（不是按正则条数分）。

    anti_default_motif 的正则表把「账」「债/欠」「讨债」拆成三条，但语义上
    它们是同一件事：钱。按条数数「子族」会让一本真写债务的书天然凑够 2 条
    —— 正是它保护不了的那种书。按语义合并后，「≥2 族」才真正意味着
    *不同题材的意象同时占满一章*（钱 + 丧葬），也就是真机那本澡堂喜剧
    被丧葬账簿填满的形状。
    """

    from bestseller.services.anti_default_motif import _DEFAULT_DEBT_FAMILY_RES

    money = tuple(_DEFAULT_DEBT_FAMILY_RES[0:3])
    funeral = (_DEFAULT_DEBT_FAMILY_RES[3],)
    lifespan = (_DEFAULT_DEBT_FAMILY_RES[4],)
    return (("金钱账目", money), ("丧葬", funeral), ("寿元", lifespan))


def _detect_motif_saturation(content_md: str, *, lang: str) -> list[AiFlavorSpan]:
    """默认母题族占满全章 —— 全章级 advisory，CJK only。"""

    if lang.startswith("en") or not content_md:
        return []
    body = _DIALOGUE_QUOTE_RE.sub("", content_md)
    chars = len(re.findall(r"[一-鿿]", body))
    if chars < _MOTIF_SATURATION_MIN_CHARS:
        return []

    total = 0
    distinct = 0
    first: int | None = None
    for _label, patterns in _motif_family_patterns():
        found = [m for pattern in patterns for m in pattern.finditer(body)]
        if not found:
            continue
        distinct += 1
        total += len(found)
        start = min(m.start() for m in found)
        if first is None or start < first:
            first = start
    if distinct < _MOTIF_SATURATION_MIN_DISTINCT:
        return []
    rate = total / chars * 1000.0
    if rate < _MOTIF_SATURATION_PER_1K:
        return []

    anchor = first or 0
    return [
        AiFlavorSpan(
            start=anchor,
            end=min(anchor + 30, len(content_md)),
            matched_text=content_md[anchor : anchor + 30],
            rule_id=f"{lang}.motif.saturation",
            category="motif_saturation",
            severity="warn",
            suggestions=(),
            sentence_span=(anchor, min(anchor + 30, len(content_md))),
            why=(
                "改法：金钱债务与丧葬两类意象已经占满全章，把它们压回背景——"
                "只在真正推进剧情的那一两处保留，其余换成本章场景里已有的"
                "具体事物、动作或人物关系。这类意象在真实出版章节里的中位数是"
                "零，本章的密度和同时出场的子族数都远超人类写作区间，"
                f"读起来会像整本书只有这一个话题（{total}处/{rate:.1f}每千字/"
                f"{distinct}个子族）"
            ),
            remove_sentence_on_block=False,
            hit_count=total,
        )
    ]


# ── 对话饥饿 (2026-08-16) ───────────────────────────────────────────────────
# 评委盲评把它判成断代级差距（对话维 AI 2 分 vs 人类 7 分），两本真机书都复发：
#   《端盘画神》全书对话占比中位 0.0%（44/50 章 ≤5%）——主角设定成哑女；
#   《破澡堂真话局》中位 1.3%、24/50 章一句对白都没有——而它的核心机制
#   就是「人必须当众说真心话」。机制写在设定里，正文里没人开口。
# 语料标定（.distillation_private 1160 章）：
#   对话占比 中位 26.5% / p10 7.2% / p5 3.6% / p1 0.3%
#   完全没有对话的章只占 1.7%
# 阈值取 p5=3.6%：人类里 5% 的章会命中（写景/赶路/单人潜行确实存在），
# 换来对我们这种「整本书没人说话」的高召回。
# 处置：advisory + 计分封顶（缺对话不是句法 tell，不该独立毙章）+ 进 deslop
# 触发集；改法只说「把已有的信息交换改成人物开口」，绝不要求硬塞对白。
_DIALOGUE_FAMINE_MIN_CHARS = 1200
_DIALOGUE_FAMINE_RATIO = 3.6  # 人类 p5


def _detect_dialogue_famine(content_md: str, *, lang: str) -> list[AiFlavorSpan]:
    """整章几乎无人说话 —— 全章级 advisory，CJK only。"""

    if lang.startswith("en") or not content_md:
        return []
    chars = len(re.findall(r"[一-鿿]", content_md))
    if chars < _DIALOGUE_FAMINE_MIN_CHARS:
        return []
    spoken = sum(
        len(next(g for g in groups if g))
        for groups in _DIALOGUE_SPOKEN_RE.findall(content_md)
        if any(groups)
    )
    ratio = spoken / chars * 100.0
    if ratio >= _DIALOGUE_FAMINE_RATIO:
        return []
    return [
        AiFlavorSpan(
            start=0,
            end=min(30, len(content_md)),
            matched_text=content_md[:30],
            rule_id=f"{lang}.dialogue.famine",
            category="dialogue_famine",
            severity="warn",
            suggestions=(),
            sentence_span=(0, min(30, len(content_md))),
            why=(
                "改法：本章几乎没有人开口。不要硬塞寒暄——把章里已经发生的"
                "信息交换、试探、讨价还价、下判断，改成人物当场说出来，"
                "让对方接话；叙述者替人物转述的那些内容，正是该由人物自己讲的。"
                "真实出版章节的对话占比中位数是四分之一强，完全没有对话的章"
                f"只占百分之二（本章 {ratio:.1f}%）"
            ),
            remove_sentence_on_block=False,
            hit_count=1,
        )
    ]


def _detect_debt_metaphor_leak(content_md: str, *, lang: str) -> list[AiFlavorSpan]:
    """正文债务化比喻回流(warn,不设密度上限,逐处标记)。

    真机终审(2026-07-08):构思层反债务化闸门只治金手指/前提文本——某书
    金手指干干净净写"污染值/协议区共生绑定"，一个"账"字没有，写手描写
    "签字接受代价"这个动作时却自己长出"但写了他就是认下这笔账……白板上
    的字就是给协议区的欠条，第一条欠条"。概念层干净不等于正文干净，写手
    自己的语言习惯一遇到"接受代价/后果"的场景就会本能地套财务记账比喻。
    复用 conception._DEBT_LEDGER_TOKENS 同一份判定词表，每处单独标记
    （不是"什么都像什么"的密度问题，一处也不该有）。
    """

    # RETIRED 2026-08-02 together with the rest of the motif police. Flagging
    # every 债/账/欠 in finished prose treated a whole vocabulary as an AI tell,
    # and the deslop pass then rewrote those sentences out of the chapter. In a
    # book about a debt, a sect's resource accounts, or a favour owed, that is
    # the story being deleted. AI-flavour detection stays on the things that are
    # actually AI tells: staccato paragraphs, sensation stacking, near-copy lines.
    del content_md, lang
    return []


# ── First-person inner-voice absence (advisory) ─────────────────────────────
# 口径 = E3 真机盲评量尺 (scratchpad metrics.py, 记忆 pov-inner-voice-…):
# 第一人称叙述层没有「心道」标记，内心声音只能从盘算/自问句式和叙述层问号
# 里读出来。E1 病灶三章 命中全 0（对标《诡秘》6 处 + 15 个问号）——冷读者
# 跟不上主角动机，可读性盲评全败。cinematic_pov 第 8 条靠 prompt 授权，
# M3 服从性差，这里给确定性兜底：advisory only，deslop 跑其他问题时把
# 「补 2-3 处内心声音」的指令捎给写手。
_FP_INNER_VOICE_RE = re.compile(
    r"我得|我不能|我怕|我赌|我猜|我算了算|我告诉自己|怎么办|难道|万一|"
    r"不会是|要不要|还是说|凭什么|说不定"
)
_INNER_MARKER_RE = re.compile(r"心里默念|心道|心想|心说|暗道|暗想|自问|腹诽")
# 对白引号：模型在不同轮次会切换引号风格——真机《健身房》ch1 v1/v3 用弯引号
# “”，v2 整章改用直引号 "。只认弯引号的正则会把 v2 读成「零对话」，于是
# dialogue_famine 误报、moment_slice 的对白屏蔽也失效。三处正则必须同源。
_DIALOGUE_QUOTE_RE = re.compile(
    r"“[^”\n]*”|「[^」\n]*」|『[^』\n]*』|\"[^\"\n]*\""
)
_DIALOGUE_SPOKEN_RE = re.compile(
    r"“([^”\n]{1,400})”|「([^」\n]{1,400})」|『([^』\n]{1,400})』|\"([^\"\n]{1,400})\""
)
_INNER_VOICE_MIN_CHARS = 1500  # 全章口径，短卡/片段不评
_FIRST_PERSON_MIN_WO = 8  # 叙述层「我」达此数才认定第一人称叙述


def _detect_inner_voice_absence(content_md: str, *, lang: str) -> list[AiFlavorSpan]:
    """第一人称章内心声音缺失 —— 全章级 advisory，CJK only。

    对白内文字用等长占位符掩掉（位置不漂移），只在叙述层上：
    1. 「我」≥ 阈值 → 第一人称叙述（对白里的"我"不算）；
    2. 盘算/自问句式 + 心理标记 + 叙述层问号 合计 <2 → 一个 info span。
    第三人称章不适用（有「心道」标记体系，另行评判），直接跳过。
    """

    if lang != "zh" or len(content_md) < _INNER_VOICE_MIN_CHARS:
        return []
    masked = _DIALOGUE_QUOTE_RE.sub(lambda m: "�" * len(m.group()), content_md)
    if masked.count("我") < _FIRST_PERSON_MIN_WO:
        return []
    hits = (
        len(_FP_INNER_VOICE_RE.findall(masked))
        + len(_INNER_MARKER_RE.findall(masked))
        + masked.count("？")
        + masked.count("?")
    )
    if hits >= 2:
        return []
    pos = masked.find("我")
    return [
        AiFlavorSpan(
            start=pos,
            end=pos + 1,
            matched_text=content_md[pos : pos + 12],
            rule_id="zh.inner_voice.absence",
            category="inner_voice_absence",
            severity="info",
            suggestions=(),
            sentence_span=_sentence_bounds(content_md, pos, lang),
            why=(
                f"第一人称全章内心声音缺失（盘算/自问句式仅 {hits} 处，达标≥2）"
                "——读者听不见主角的念头就跟不上动机。在做决定/遇险/起疑的节点"
                "补 2-3 句第一人称盘算或自问（'我得…'/'万一…'/'难道…？'），"
                "直接写念头本身，不要加'我心想'标记，也不要转成生理症状。"
            ),
            remove_sentence_on_block=False,
        )
    ]


def _coerce_severity(raw: Any, *, default: Severity) -> Severity:
    if raw in ("block", "warn", "info"):
        return raw  # type: ignore[return-value]
    return default
