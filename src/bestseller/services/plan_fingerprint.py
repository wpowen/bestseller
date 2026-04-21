"""Cross-chapter plan fingerprint & near-duplicate detection.

Root cause that this module addresses: even when each individual scene card
passes the richness gate, two DIFFERENT chapters can still end up describing
effectively the same plot beat (same conflict + same hook + overlapping scene
purposes). Post-generation text dedup only catches the symptom; this gate
catches the cause at plan time where the cost of a re-plan is ~0.

The module exposes three layers:

1. ``build_chapter_fingerprint(outline_or_model)`` — normalize a chapter's
   planning text into a single deduplicable payload (main_conflict,
   hook_description, concatenated scene story purposes, hook_type).

2. ``find_near_duplicate_chapters(fingerprints, ...)`` — pairwise Jaccard
   comparison across a set of fingerprints. Returns finding records for
   pairs whose similarity exceeds a configurable threshold.

3. ``scan_batch_for_duplicates(batch_outlines, existing_db_chapters, ...)``
   — convenience entry point for ``materialize_chapter_outline_batch`` that
   checks a new batch both against itself and against previously-persisted
   chapters.

The Jaccard + shingle machinery is reused from ``services.deduplication``
to keep the fingerprint logic consistent with post-text dedup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from bestseller.services.deduplication import compute_jaccard_similarity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Jaccard similarity thresholds. Below _WARNING: no finding. Between
# _WARNING and _CRITICAL: warning finding. At/above _CRITICAL: critical
# finding (blocking the re-plan when settings allow).
DEFAULT_WARNING_THRESHOLD = 0.55
DEFAULT_CRITICAL_THRESHOLD = 0.75

# Minimum character length of the combined fingerprint text before we bother
# running a Jaccard check. Very short strings produce noisy matches.
_MIN_FINGERPRINT_LEN = 20


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChapterFingerprint:
    """Canonical text bundle used for pairwise chapter comparison."""

    chapter_number: int
    hook_type: str                       # exact-match signal (e.g. "shock")
    combined_text: str                   # Jaccard-ready blob
    scene_story_purposes: tuple[str, ...] = ()  # for per-field forensics


@dataclass(frozen=True)
class DuplicationFinding:
    """Single pair of chapters flagged as too similar."""

    chapter_a: int
    chapter_b: int
    similarity: float
    severity: str                        # "warning" | "critical"
    reason: str                          # short human-readable label
    matched_fields: tuple[str, ...] = ()  # e.g. ("hook_type", "conflict")


@dataclass(frozen=True)
class FingerprintScanReport:
    """Outcome of a cross-chapter fingerprint scan."""

    findings: tuple[DuplicationFinding, ...] = field(default_factory=tuple)

    @property
    def has_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)

    @property
    def critical_findings(self) -> tuple[DuplicationFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "critical")

    def to_prompt_block(self, *, language: str = "zh-CN") -> str:
        if not self.findings:
            return ""
        if language.startswith("zh"):
            header = "【章节指纹近似 — 剧情可能重复】"
            lines = [header]
            for f in self.findings:
                tag = "❗关键" if f.severity == "critical" else "⚠️提示"
                lines.append(
                    f"{tag} 第{f.chapter_a}章 ↔ 第{f.chapter_b}章 "
                    f"(相似度 {f.similarity:.2f}): {f.reason}"
                )
            return "\n".join(lines)
        header = "[Chapter fingerprint near-duplicate — potential plot repeat]"
        lines = [header]
        for f in self.findings:
            tag = "CRITICAL" if f.severity == "critical" else "WARN"
            lines.append(
                f"[{tag}] ch{f.chapter_a} ↔ ch{f.chapter_b} "
                f"(sim {f.similarity:.2f}): {f.reason}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fingerprint construction
# ---------------------------------------------------------------------------

def _extract_scene_story_purposes(scenes: Iterable[Any]) -> list[str]:
    """Pull ``purpose.story`` from each scene outline / model."""
    out: list[str] = []
    for scene in scenes:
        purpose = None
        if isinstance(scene, dict):
            purpose = scene.get("purpose")
        else:
            purpose = getattr(scene, "purpose", None)
        if not isinstance(purpose, dict):
            continue
        story = purpose.get("story") if isinstance(purpose, dict) else None
        if isinstance(story, str) and story.strip():
            out.append(story.strip())
    return out


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def build_chapter_fingerprint(outline: Any) -> ChapterFingerprint:
    """Build a ``ChapterFingerprint`` from an outline dict / model / ORM row.

    Accepts anything with ``chapter_number`` plus some subset of
    ``main_conflict`` / ``hook_description`` / ``hook_type`` / ``chapter_goal``
    / ``scenes``. Missing fields default to empty strings.

    Scenes may be a list of dicts (``purpose.story``) or SceneCardModel rows.
    """
    def _get(key: str) -> Any:
        if isinstance(outline, dict):
            return outline.get(key)
        return getattr(outline, key, None)

    chapter_number_raw = _get("chapter_number")
    try:
        chapter_number = int(chapter_number_raw) if chapter_number_raw is not None else 0
    except (TypeError, ValueError):
        chapter_number = 0

    hook_type = _coerce_text(_get("hook_type")).lower()
    hook_description = _coerce_text(_get("hook_description"))
    main_conflict = _coerce_text(_get("main_conflict"))
    chapter_goal = _coerce_text(_get("chapter_goal"))
    scenes = _get("scenes") or []
    if not isinstance(scenes, (list, tuple)):
        scenes = []
    scene_purposes = _extract_scene_story_purposes(scenes)

    # Combined Jaccard payload: weighted by importance.  main_conflict is
    # included twice to give it ~2x the shingle mass vs. scene purposes.
    combined_parts = [
        main_conflict,
        main_conflict,
        hook_description,
        chapter_goal,
    ]
    combined_parts.extend(scene_purposes)
    combined_text = " | ".join(p for p in combined_parts if p)

    return ChapterFingerprint(
        chapter_number=chapter_number,
        hook_type=hook_type,
        combined_text=combined_text,
        scene_story_purposes=tuple(scene_purposes),
    )


# ---------------------------------------------------------------------------
# Pairwise scan
# ---------------------------------------------------------------------------

def _fingerprint_similarity(a: ChapterFingerprint, b: ChapterFingerprint) -> float:
    """Full fingerprint similarity (Jaccard on combined_text)."""
    if len(a.combined_text) < _MIN_FINGERPRINT_LEN:
        return 0.0
    if len(b.combined_text) < _MIN_FINGERPRINT_LEN:
        return 0.0
    return compute_jaccard_similarity(a.combined_text, b.combined_text)


def _matched_field_labels(
    a: ChapterFingerprint,
    b: ChapterFingerprint,
    *,
    field_threshold: float = 0.6,
) -> list[str]:
    """Explain WHY two chapters look alike."""
    labels: list[str] = []
    if a.hook_type and a.hook_type == b.hook_type:
        labels.append(f"hook_type='{a.hook_type}'")
    # Per-scene-purpose overlap — if any pair of scene purposes crosses the
    # field threshold, include that as a concrete reason.
    for pa in a.scene_story_purposes:
        for pb in b.scene_story_purposes:
            sim = compute_jaccard_similarity(pa, pb)
            if sim >= field_threshold:
                labels.append(f"scene_purpose_match(sim={sim:.2f})")
                break
        if labels and labels[-1].startswith("scene_purpose_match"):
            break
    return labels


def find_near_duplicate_chapters(
    fingerprints: list[ChapterFingerprint],
    *,
    warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
    critical_threshold: float = DEFAULT_CRITICAL_THRESHOLD,
    max_chapter_distance: int | None = None,
) -> FingerprintScanReport:
    """Pairwise Jaccard scan across a set of chapter fingerprints.

    Parameters
    ----------
    fingerprints
        Fingerprints to compare, in chapter_number order (the function does
        not sort — caller can sort or not depending on intent).
    warning_threshold
        Similarity ≥ this produces a warning-level finding.
    critical_threshold
        Similarity ≥ this produces a critical-level finding.
    max_chapter_distance
        If set, only pairs whose chapter numbers are within this distance
        are checked. Useful when a long series intentionally revisits motifs
        but you still want to catch *adjacent* repetition.
    """
    findings: list[DuplicationFinding] = []
    n = len(fingerprints)
    for i in range(n):
        for j in range(i + 1, n):
            a = fingerprints[i]
            b = fingerprints[j]
            if (
                max_chapter_distance is not None
                and abs(a.chapter_number - b.chapter_number) > max_chapter_distance
            ):
                continue
            sim = _fingerprint_similarity(a, b)
            if sim < warning_threshold:
                continue
            severity = "critical" if sim >= critical_threshold else "warning"
            matched = _matched_field_labels(a, b)
            reason = (
                ", ".join(matched)
                if matched
                else f"combined Jaccard {sim:.2f}"
            )
            findings.append(DuplicationFinding(
                chapter_a=a.chapter_number,
                chapter_b=b.chapter_number,
                similarity=sim,
                severity=severity,
                reason=reason,
                matched_fields=tuple(matched),
            ))
    return FingerprintScanReport(findings=tuple(findings))


# ---------------------------------------------------------------------------
# Convenience wrapper for planning pipelines
# ---------------------------------------------------------------------------

def scan_batch_for_duplicates(
    batch_outlines: list[Any],
    existing_chapters: list[Any] | None = None,
    *,
    warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
    critical_threshold: float = DEFAULT_CRITICAL_THRESHOLD,
    max_chapter_distance: int | None = None,
) -> FingerprintScanReport:
    """Scan a new batch of chapter outlines against itself AND existing DB rows.

    The batch-internal pairs and cross-batch pairs are merged into a single
    report so callers can apply one severity policy.

    Parameters
    ----------
    batch_outlines
        New ``ChapterOutlineInput`` (or dict) entries being materialized.
    existing_chapters
        Previously-persisted chapters to compare against. Each row must expose
        ``chapter_number``, ``main_conflict``, ``hook_type``,
        ``hook_description``, ``chapter_goal``, and a ``scenes`` relationship
        / list (optional).
    """
    batch_fps = [build_chapter_fingerprint(o) for o in batch_outlines]
    existing_fps = (
        [build_chapter_fingerprint(c) for c in existing_chapters]
        if existing_chapters else []
    )

    # 1. Intra-batch scan
    intra = find_near_duplicate_chapters(
        batch_fps,
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
        max_chapter_distance=max_chapter_distance,
    )

    # 2. Cross-batch scan (every new chapter × every existing chapter)
    cross_findings: list[DuplicationFinding] = []
    for new_fp in batch_fps:
        for exist_fp in existing_fps:
            if (
                max_chapter_distance is not None
                and abs(new_fp.chapter_number - exist_fp.chapter_number) > max_chapter_distance
            ):
                continue
            sim = _fingerprint_similarity(new_fp, exist_fp)
            if sim < warning_threshold:
                continue
            severity = "critical" if sim >= critical_threshold else "warning"
            matched = _matched_field_labels(new_fp, exist_fp)
            reason = ", ".join(matched) if matched else f"combined Jaccard {sim:.2f}"
            cross_findings.append(DuplicationFinding(
                chapter_a=new_fp.chapter_number,
                chapter_b=exist_fp.chapter_number,
                similarity=sim,
                severity=severity,
                reason=reason,
                matched_fields=tuple(matched),
            ))

    merged = tuple(intra.findings) + tuple(cross_findings)
    return FingerprintScanReport(findings=merged)
