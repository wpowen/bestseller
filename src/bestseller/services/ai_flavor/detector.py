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
        if narration_total == 0 or solo_count == 0:
            continue
        ratio = solo_count / narration_total

        ratio_hit = solo_count >= min_solo and ratio >= ratio_threshold
        run_hit = max_run >= run_threshold
        subject_hit = max_subj_run >= subject_threshold
        if not (ratio_hit or run_hit or subject_hit):
            continue

        reasons: list[str] = []
        if ratio_hit:
            reasons.append(f"单句独段{solo_count}/{narration_total}段({ratio*100:.0f}%)")
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

    Each rule has a regex ``pattern`` and a ``threshold``: count narration-only
    matches and, if they cross the threshold, emit ONE advisory ``warn`` span
    for the chapter (anchored at the first hit). Used for constructs that are
    fine in moderation but read as a tic when repeated — anonymous crowd-
    reaction beats, 「他没X」negative-action filler. Advisory only (no
    suggestion → patcher skips); capped at the score layer.
    """

    out: list[AiFlavorSpan] = []
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
        threshold = int(rule.get("threshold", 3))

        hits: list[tuple[int, int]] = []
        for m in pattern.finditer(content_md):
            if _is_in_ranges(m.start(), dialogue_ranges):
                continue
            hits.append((m.start(), m.end()))
        if len(hits) < threshold:
            continue
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
                why=f"{why}（共{len(hits)}处）" if why else f"{len(hits)} hits",
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

    # ── Chapter-level repetition (车轱辘内心戏 / 感觉词堆叠) ─────────────
    spans.extend(_detect_repetition(content_md, lang=lang))

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
        out.append(
            AiFlavorSpan(
                start=pos,
                end=pos + len(top_gram),
                matched_text=top_gram,
                rule_id=f"{lang}.repetition.ngram",
                category="narrative_repetition",
                severity="warn",
                suggestions=(),
                sentence_span=(pos, pos + len(top_gram)),
                why=(
                    f"篇章级车轱辘：{len(repeated)} 个实义短语各重复≥5次"
                    f"（如「{top_gram}」{top_count}次）——同一意思反复换皮写，"
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
    return out


def _coerce_severity(raw: Any, *, default: Severity) -> Severity:
    if raw in ("block", "warn", "info"):
        return raw  # type: ignore[return-value]
    return default
