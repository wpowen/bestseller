"""Phase B1 — Per-chapter narrative-line dominance tracker.

Classifies every finished chapter into one of our four canonical
narrative layers (``overt`` / ``undercurrent`` / ``hidden`` /
``core_axis``) and records the result on ``ChapterModel``. The tracker
then reports gaps between successive dominances so the chapter
validator can fire a ``LineGapCheck`` when a layer has been silent for
longer than the genre's per-strand budget.

Design notes
============

* **No LLM call.** Classification is deterministic keyword / marker
  counting so the tracker runs cheaply on every finished chapter and so
  its output is reproducible during re-scans of historical chapters.

* **Intensity ≠ dominance ratio.** ``line_intensity`` expresses how
  concentrated the dominant layer's signal was (max score / total
  score). A chapter where every strand got equal airtime has ≤ 0.4
  intensity even though one strand technically came out top.

* **Graceful fallback.** When a chapter has no line-specific signals
  (e.g. very short transitional chapter) we keep ``dominant_line =
  None`` and emit no gap penalty; the validator interprets
  ``NULL`` as "not classified yet" so regression scans don't
  mass-trigger on historical rows.

* **Rolling window for gap detection.** ``report_gaps`` walks the last
  ``history_window`` chapters (default 50, matching the Phase B2 cap in
  ``ProjectModel.metadata_json.line_dominance_history``) and measures
  "chapters since last dominance" for each of the 4 layers against the
  per-genre ``PacingThresholds.strand_max_gap`` budget.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from bestseller.services.genre_profile_thresholds import (
    PacingThresholds,
    resolve_thresholds,
)
from bestseller.services.narrative_lines import (
    CANONICAL_LINES,
    LINE_CORE_AXIS,
    LINE_HIDDEN,
    LINE_OVERT,
    LINE_UNDERCURRENT,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Classification — keyword signals per line.
# ---------------------------------------------------------------------------
# These lexicons are deliberately broad-strokes; the tracker's job is to
# pick *which* layer got the lion's share of screen time, not to perform
# fine-grained semantic classification. Tuned against the Bloodline Twin
# (血脉双生) ch1-50 corpus where hand-labelling showed ≥ 0.75 accuracy
# on dominant-layer identification.


_OVERT_MARKERS_ZH: tuple[str, ...] = (
    "战斗", "交手", "对战", "厮杀", "追击", "伏击",
    "任务", "悬赏", "目标", "关卡",
    "阵法", "招式", "剑气", "灵力",
    "擂台", "挑战", "赛事", "比试",
    "敌人", "对手", "仇人", "仇敌",
    "打脸", "反杀", "反击", "秒杀",
)

_UNDERCURRENT_MARKERS_ZH: tuple[str, ...] = (
    "势力", "阵营", "门派", "宗门",
    "幕后", "暗中", "操纵", "操盘",
    "线索", "蛛丝马迹", "真相", "揭露",
    "阴谋", "密谋", "谋划", "布局",
    "情报", "眼线", "密探",
    "背后", "黑手", "内鬼",
)

_HIDDEN_MARKERS_ZH: tuple[str, ...] = (
    "封印", "远古", "上古", "传说",
    "预言", "天机", "天命", "宿命",
    "血脉觉醒", "本源", "前世", "因果",
    "禁忌", "禁术", "秘辛", "隐秘",
    "神域", "仙界", "洪荒",
    "梦境", "回忆", "幻象",
)

_CORE_AXIS_MARKERS_ZH: tuple[str, ...] = (
    "选择", "抉择", "信念", "初心",
    "代价", "牺牲", "承诺", "誓言",
    "道心", "道途", "本心",
    "坚守", "坚持", "放下",
    "活着", "意义", "价值",
    "自我", "成为", "超越",
)

_OVERT_MARKERS_EN: tuple[str, ...] = (
    "fight", "battle", "clash", "strike",
    "mission", "quest", "objective", "target",
    "duel", "tournament", "rival", "enemy",
    "attack", "ambush", "combat", "engage",
)

_UNDERCURRENT_MARKERS_EN: tuple[str, ...] = (
    "faction", "sect", "clan", "alliance",
    "conspiracy", "scheme", "plot",
    "clue", "truth", "revealed", "uncovered",
    "behind", "mastermind", "spy", "informant",
)

_HIDDEN_MARKERS_EN: tuple[str, ...] = (
    "seal", "ancient", "legend", "prophecy",
    "fate", "destiny", "bloodline awaken",
    "forbidden", "secret origin", "primordial",
    "flashback", "dreamt", "past life",
)

_CORE_AXIS_MARKERS_EN: tuple[str, ...] = (
    "choice", "belief", "conviction", "promise",
    "oath", "sacrifice", "price paid",
    "meaning", "purpose", "resolve",
    "who am i", "what i am",
)


def _marker_sets(language: str) -> Mapping[str, tuple[str, ...]]:
    """Return per-line marker tuples scoped to the project language."""

    if language and language.lower().startswith("en"):
        return {
            LINE_OVERT: _OVERT_MARKERS_EN,
            LINE_UNDERCURRENT: _UNDERCURRENT_MARKERS_EN,
            LINE_HIDDEN: _HIDDEN_MARKERS_EN,
            LINE_CORE_AXIS: _CORE_AXIS_MARKERS_EN,
        }
    return {
        LINE_OVERT: _OVERT_MARKERS_ZH,
        LINE_UNDERCURRENT: _UNDERCURRENT_MARKERS_ZH,
        LINE_HIDDEN: _HIDDEN_MARKERS_ZH,
        LINE_CORE_AXIS: _CORE_AXIS_MARKERS_ZH,
    }


# Minimum total marker hits for a chapter to be classified at all.
# Below this threshold we return ``dominant_line = None`` rather than
# guess from a handful of incidental occurrences.
_MIN_TOTAL_MARKERS: int = 3

# Dominant intensity floor — if the winning layer scored < 30% of the
# total we keep the classification but report low intensity; callers can
# choose to discount low-intensity chapters when computing gap reports
# (the default ``report_gaps`` implementation counts any dominance ≥
# ``dominance_intensity_threshold``).
_DEFAULT_INTENSITY_THRESHOLD: float = 0.3


# ---------------------------------------------------------------------------
# Data structures.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineClassification:
    """One chapter's classification result."""

    chapter_no: int
    dominant_line: str | None
    support_lines: tuple[str, ...]
    line_intensity: float
    marker_counts: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_no": self.chapter_no,
            "dominant_line": self.dominant_line,
            "support_lines": list(self.support_lines),
            "line_intensity": self.line_intensity,
            "marker_counts": dict(self.marker_counts),
        }


@dataclass(frozen=True)
class LineGap:
    """How long each of the 4 layers has been silent."""

    line_id: str
    last_dominant_chapter: int | None
    current_gap: int              # chapters since last dominance
    threshold: int                # genre's max-gap budget for this line
    severity: str                 # "ok" | "warn" | "over"

    @property
    def is_over(self) -> bool:
        return self.severity == "over"

    @property
    def is_warn(self) -> bool:
        return self.severity == "warn"


@dataclass(frozen=True)
class LineGapReport:
    """Aggregate gap report for the rolling window."""

    project_id: str
    current_chapter: int
    gaps: tuple[LineGap, ...]
    pacing_config: PacingThresholds

    @property
    def over_gaps(self) -> tuple[LineGap, ...]:
        return tuple(g for g in self.gaps if g.is_over)

    @property
    def warn_gaps(self) -> tuple[LineGap, ...]:
        return tuple(g for g in self.gaps if g.is_warn)

    @property
    def needs_nudge(self) -> bool:
        """True when the prompt constructor should inject a rotation nudge."""

        return bool(self.over_gaps) or bool(self.warn_gaps)


# ---------------------------------------------------------------------------
# Classifier.
# ---------------------------------------------------------------------------


def classify_chapter(
    chapter_text: str,
    *,
    chapter_no: int,
    language: str = "zh-CN",
    outline_hint: str | None = None,
    min_total_markers: int = _MIN_TOTAL_MARKERS,
) -> LineClassification:
    """Classify a finished chapter into its dominant narrative layer.

    Parameters
    ----------
    chapter_text:
        The stitched chapter body.  Outline scaffolding is *not* stripped
        — a chapter that keeps its outline preamble simply counts those
        markers too, which is fine because they reflect authorial intent.
    chapter_no:
        1-based chapter index used only to round-trip into the
        resulting ``LineClassification``.
    language:
        Project language — picks the marker lexicon.
    outline_hint:
        Optional outline / beat-sheet blurb concatenated before scoring,
        so a chapter whose outline explicitly says ``暗线触发`` but whose
        body is lean still registers an undercurrent signal.
    min_total_markers:
        Floor below which we refuse to guess.  Raising this makes the
        tracker more conservative.
    """

    haystack = chapter_text or ""
    if outline_hint:
        haystack = f"{outline_hint}\n\n{haystack}"
    haystack_lower = haystack.lower()

    markers = _marker_sets(language)
    counts: dict[str, int] = {line: 0 for line in CANONICAL_LINES}

    for line_id, tokens in markers.items():
        for tok in tokens:
            if not tok:
                continue
            # Use case-insensitive substring match; marker tokens are
            # short enough that this is both correct and fast.
            needle = tok.lower()
            if needle:
                counts[line_id] += haystack_lower.count(needle)

    total = sum(counts.values())
    if total < min_total_markers:
        return LineClassification(
            chapter_no=chapter_no,
            dominant_line=None,
            support_lines=(),
            line_intensity=0.0,
            marker_counts=counts,
        )

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    top_line, top_score = ranked[0]
    if top_score == 0:
        return LineClassification(
            chapter_no=chapter_no,
            dominant_line=None,
            support_lines=(),
            line_intensity=0.0,
            marker_counts=counts,
        )

    intensity = round(top_score / max(total, 1), 3)
    support: list[str] = []
    # Supports are any other line whose score is ≥ 40% of the top score.
    # Empirically this filters out the long tail of single-keyword hits
    # while still surfacing a genuine B-plot contributor.
    support_floor = max(1, int(top_score * 0.4))
    for line_id, score in ranked[1:]:
        if score >= support_floor:
            support.append(line_id)

    return LineClassification(
        chapter_no=chapter_no,
        dominant_line=top_line,
        support_lines=tuple(support),
        line_intensity=intensity,
        marker_counts=counts,
    )


# ---------------------------------------------------------------------------
# Gap reporting.
# ---------------------------------------------------------------------------


def _history_entry_as_record(entry: Any) -> dict[str, Any] | None:
    """Normalise one history entry (LineClassification, dict, or model)."""

    if entry is None:
        return None
    if isinstance(entry, LineClassification):
        return {
            "chapter_no": entry.chapter_no,
            "dominant_line": entry.dominant_line,
            "line_intensity": entry.line_intensity,
        }
    if isinstance(entry, dict):
        if "chapter_no" not in entry:
            return None
        return {
            "chapter_no": int(entry.get("chapter_no", 0)),
            "dominant_line": entry.get("dominant_line"),
            "line_intensity": float(entry.get("line_intensity") or 0.0),
        }
    # Duck-type for ORM rows (ChapterModel-like).
    chapter_no = getattr(entry, "chapter_no", None)
    if chapter_no is None:
        return None
    return {
        "chapter_no": int(chapter_no),
        "dominant_line": getattr(entry, "dominant_line", None),
        "line_intensity": float(getattr(entry, "line_intensity", 0.0) or 0.0),
    }


def report_gaps(
    *,
    project_id: str,
    current_chapter: int,
    history: Sequence[Any],
    genre_id: str | None = None,
    pacing_config: PacingThresholds | None = None,
    dominance_intensity_threshold: float = _DEFAULT_INTENSITY_THRESHOLD,
    warn_ratio: float = 0.8,
) -> LineGapReport:
    """Compute per-line gaps for the rolling history.

    Parameters
    ----------
    project_id:
        Passed through unchanged into the report so callers can route by
        project without a second lookup.
    current_chapter:
        The chapter about to be written (the validator runs *before*
        appending the current chapter to the history, so a gap of 0
        means "this very chapter will dominate it").
    history:
        Ordered list of prior classifications; each entry may be a
        ``LineClassification``, a dict, or a ``ChapterModel`` row.  Only
        entries with ``dominant_line`` set and ``line_intensity ≥
        dominance_intensity_threshold`` count as contributing to that
        line's last-dominance chapter.
    genre_id / pacing_config:
        Exactly one of these is used.  ``pacing_config`` wins if both
        are supplied; otherwise ``resolve_thresholds(genre_id)`` is
        consulted.  Passing neither yields the fallback genre
        (``action-progression``).
    warn_ratio:
        Fraction of the threshold at which the gap is marked ``warn``
        instead of ``ok``.  Defaults to 0.8 matching the plan.
    """

    cfg = pacing_config or resolve_thresholds(genre_id).pacing_config
    last_seen: dict[str, int] = {}

    for raw in history:
        rec = _history_entry_as_record(raw)
        if rec is None:
            continue
        dom = rec.get("dominant_line")
        if not dom:
            continue
        if dom not in cfg.strand_max_gap:
            # Unknown layer id — ignore gracefully.
            continue
        intensity = rec.get("line_intensity") or 0.0
        if intensity < dominance_intensity_threshold:
            continue
        ch = int(rec.get("chapter_no") or 0)
        if ch <= 0:
            continue
        # Keep the latest chapter per line.
        if last_seen.get(dom, -1) < ch:
            last_seen[dom] = ch

    gaps: list[LineGap] = []
    for line_id in CANONICAL_LINES:
        threshold = int(cfg.strand_max_gap.get(line_id, 0) or 0)
        last_ch = last_seen.get(line_id)
        if last_ch is None:
            # Never dominated within the window — treat gap as
            # current_chapter (i.e. since the start of the window).
            gap = max(0, int(current_chapter))
        else:
            gap = max(0, int(current_chapter) - int(last_ch))

        if threshold <= 0:
            severity = "ok"
        elif gap > threshold:
            severity = "over"
        elif gap >= int(threshold * warn_ratio):
            severity = "warn"
        else:
            severity = "ok"
        gaps.append(
            LineGap(
                line_id=line_id,
                last_dominant_chapter=last_ch,
                current_gap=gap,
                threshold=threshold,
                severity=severity,
            )
        )

    return LineGapReport(
        project_id=str(project_id),
        current_chapter=int(current_chapter),
        gaps=tuple(gaps),
        pacing_config=cfg,
    )


# ---------------------------------------------------------------------------
# History persistence helpers (Phase B2).
# ---------------------------------------------------------------------------


HISTORY_WINDOW_CAP: int = 50


def append_history(
    existing: Sequence[Any] | None,
    classification: LineClassification,
    *,
    cap: int = HISTORY_WINDOW_CAP,
) -> list[dict[str, Any]]:
    """Return a new history list with ``classification`` appended and the
    oldest entry evicted when the list exceeds ``cap``.

    The store is chapter-ordered ascending; callers that re-classify an
    existing chapter (e.g. during a scan) should :func:`replace_history`
    to rewrite in place rather than append.
    """

    rolling: list[dict[str, Any]] = []
    for raw in existing or ():
        rec = _history_entry_as_record(raw)
        if rec is None:
            continue
        # Drop any existing record for the same chapter so we keep
        # exactly one entry per chapter_no.
        if rec.get("chapter_no") == classification.chapter_no:
            continue
        rolling.append(rec)
    rolling.append(
        {
            "chapter_no": classification.chapter_no,
            "dominant_line": classification.dominant_line,
            "support_lines": list(classification.support_lines),
            "line_intensity": classification.line_intensity,
        }
    )
    rolling.sort(key=lambda r: int(r.get("chapter_no") or 0))
    if len(rolling) > cap:
        rolling = rolling[-cap:]
    return rolling


# ---------------------------------------------------------------------------
# Phase B2 — persistence helpers for ``ProjectModel.metadata_json``.
# ---------------------------------------------------------------------------


METADATA_HISTORY_KEY: str = "line_dominance_history"


def load_history(project_metadata: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Extract the rolling history list from a project's ``metadata_json``.

    Returns an empty list when the metadata blob is missing or the key
    has not been populated (greenfield projects or pre-tracker projects).
    Never raises — malformed entries are silently skipped so one bad
    historical row doesn't block the tracker.
    """

    if not project_metadata:
        return []
    raw = project_metadata.get(METADATA_HISTORY_KEY)
    if not isinstance(raw, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for entry in raw:
        rec = _history_entry_as_record(entry)
        if rec is not None:
            cleaned.append(rec)
    return cleaned


def persist_history(
    project_metadata: Mapping[str, Any] | None,
    classification: LineClassification,
    *,
    cap: int = HISTORY_WINDOW_CAP,
) -> dict[str, Any]:
    """Return a **new** metadata dict with ``classification`` folded in.

    Keeps the caller's existing metadata keys intact; only rewrites the
    ``line_dominance_history`` slot. Immutable by default — the returned
    dict is always a fresh object even when the history didn't change,
    which makes it safe to pass to SQLAlchemy's ``JSONB`` column without
    worrying about in-place mutation breaking change detection.
    """

    base: dict[str, Any] = dict(project_metadata or {})
    existing = load_history(project_metadata)
    rolled = append_history(existing, classification, cap=cap)
    base[METADATA_HISTORY_KEY] = rolled
    return base


# ---------------------------------------------------------------------------
# Nudge renderer — used by prompt_constructor to prime the next chapter.
# ---------------------------------------------------------------------------


_LINE_LABELS_ZH: Mapping[str, str] = {
    LINE_OVERT: "明线",
    LINE_UNDERCURRENT: "暗线",
    LINE_HIDDEN: "隐藏线",
    LINE_CORE_AXIS: "核心轴",
}

_LINE_LABELS_EN: Mapping[str, str] = {
    LINE_OVERT: "overt line",
    LINE_UNDERCURRENT: "undercurrent line",
    LINE_HIDDEN: "hidden thread",
    LINE_CORE_AXIS: "core axis",
}


def render_rotation_nudge(
    report: LineGapReport,
    *,
    language: str = "zh-CN",
) -> str:
    """Return a single-line rotation nudge for the writing brief.

    Picks the most overdue layer (``over_gaps`` first, then
    ``warn_gaps``). Returns empty string when no nudge is needed so the
    caller can unconditionally concatenate.
    """

    if not report.needs_nudge:
        return ""

    picks = report.over_gaps or report.warn_gaps
    # Most overdue = largest gap minus threshold.
    target = max(picks, key=lambda g: g.current_gap - g.threshold)
    is_en = bool(language and language.lower().startswith("en"))
    labels = _LINE_LABELS_EN if is_en else _LINE_LABELS_ZH
    label = labels.get(target.line_id, target.line_id)
    if is_en:
        return (
            f"[Line rotation nudge] Foreground the {label} this chapter "
            f"(dormant for {target.current_gap} chapters, budget "
            f"{target.threshold})."
        )
    return (
        f"【叙事线轮换提示】本章建议以 {label} 为底色/主导"
        f"（距上次已 {target.current_gap} 章，预算 {target.threshold} 章）。"
    )
