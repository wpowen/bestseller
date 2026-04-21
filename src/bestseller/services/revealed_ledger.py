"""Cross-chapter revealed facts & used-beats ledger for the planner.

Root cause this module addresses: even with plan-richness validation and
fingerprint dedup, the planner can still re-reveal the same fact or spin
a hook type into a rut because it has no aggregated view of what has
already been established across the book. The ledger solves that by
summarizing — at plan time — what has already been revealed, which hook
types are over-represented, and which beat motifs recur.

This is the third leg of the plan-time dedup stack:

  1. scene_plan_richness — per-scene card richness validation
  2. plan_fingerprint — pairwise chapter fingerprint Jaccard scan
  3. revealed_ledger (this module) — aggregate state for the next re-plan

The ledger is computed on-demand from existing DB rows; no new persistence
is introduced. Sources:

  * ``ChapterModel.hook_type`` / ``hook_description`` / ``main_conflict`` /
    ``chapter_goal`` / ``information_revealed``
  * ``SceneCardModel.purpose.story`` / ``purpose.emotion`` / ``scene_type``
  * ``ChapterStateSnapshotModel.facts`` (already-established fact values)

Two entry points:

  * :func:`build_revealed_ledger` — async helper that queries DB rows for a
    given project and returns a :class:`RevealedLedger` snapshot.
  * :meth:`RevealedLedger.to_prompt_block` — renders a compact prompt block
    (Chinese or English) suitable for injection into planner prompts.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults / tunables
# ---------------------------------------------------------------------------

# How many recent chapters influence hook-overuse detection.
_RECENT_WINDOW = 20
# Minimum times a hook_type must appear in recent window to trigger a warning.
_HOOK_OVERUSE_THRESHOLD = 4
# Minimum times a motif phrase must appear to be listed.
_BEAT_MOTIF_MIN_COUNT = 3
# Truncate very long conflict snippets before showing.
_CONFLICT_SNIPPET_MAX = 60
# Cap list sizes so the prompt block stays small.
_FACTS_CAP = 40
_HOOK_CAP = 8
_MOTIF_CAP = 10
_RECENT_CONFLICTS_CAP = 12


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _is_cjk(text: str) -> bool:
    if not text:
        return False
    return bool(_CJK_RE.search(text))


def _normalize_motif(phrase: str) -> str:
    """Normalize a beat/motif phrase to a canonical shorter form.

    Collapses whitespace, strips punctuation edge-cases, and lowercases
    Latin text. CJK text is left case-agnostic (no lowercasing).
    """
    if not phrase:
        return ""
    trimmed = re.sub(r"\s+", " ", phrase.strip())
    trimmed = trimmed.strip("，。！？,.!?;:；：\"'“”‘’`()[]{}")
    if _is_cjk(trimmed):
        return trimmed
    return trimmed.lower()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RevealedFactEntry:
    """Fact already established in the narrative (from state snapshots or
    ``ChapterModel.information_revealed``)."""

    name: str
    value: str
    kind: str            # "countdown" | "level" | "resource" | "information" | ...
    first_chapter: int
    subject: str | None = None


@dataclass(frozen=True)
class HookUsage:
    """How often a given hook_type appears — global + recent-window count."""

    hook_type: str
    total_count: int
    recent_count: int
    recent_chapters: tuple[int, ...] = ()


@dataclass(frozen=True)
class BeatMotif:
    """Recurring phrase in scene.purpose.story / chapter.main_conflict that
    signals the planner is looping the same beat."""

    phrase: str
    count: int
    example_chapters: tuple[int, ...] = ()


@dataclass(frozen=True)
class RevealedLedger:
    """Aggregate cross-chapter state for plan-time consumption."""

    project_id: UUID
    chapters_covered: tuple[int, ...] = ()
    facts: tuple[RevealedFactEntry, ...] = ()
    hook_usage: tuple[HookUsage, ...] = ()
    beat_motifs: tuple[BeatMotif, ...] = ()
    recent_conflicts: tuple[tuple[int, str], ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return (
            not self.facts
            and not self.hook_usage
            and not self.beat_motifs
            and not self.recent_conflicts
        )

    def overused_hooks(
        self,
        *,
        recent_threshold: int = _HOOK_OVERUSE_THRESHOLD,
    ) -> tuple[HookUsage, ...]:
        return tuple(
            h for h in self.hook_usage if h.recent_count >= recent_threshold
        )

    def to_prompt_block(self, *, language: str = "zh-CN") -> str:
        if self.is_empty:
            return ""
        zh = not (language or "").lower().startswith("en")
        lines: list[str] = []
        if zh:
            lines.append("【已揭示与已用节拍 — 规划时请避免重复】")
        else:
            lines.append("[Already-revealed facts & used beats — avoid replaying in the new plan]")

        # Facts
        if self.facts:
            if zh:
                lines.append("已确立的事实（不要再作为新揭示）:")
            else:
                lines.append("Established facts (do NOT re-reveal as if new):")
            for fact in self.facts[:_FACTS_CAP]:
                subj = f"{fact.subject}·" if fact.subject else ""
                tag = f"[{fact.kind}]" if fact.kind else ""
                lines.append(
                    f"  - ch{fact.first_chapter} {tag} {subj}{fact.name} = {fact.value}"
                )

        # Hook usage
        overused = self.overused_hooks()
        if overused:
            if zh:
                lines.append("近期钩子类型使用频率过高（请本批次改用其他 hook_type）:")
            else:
                lines.append("Recently overused hook_types (pick a different hook_type for the new batch):")
            for h in overused[:_HOOK_CAP]:
                sample = ", ".join(f"ch{c}" for c in h.recent_chapters[:6])
                lines.append(
                    f"  - {h.hook_type}: 最近{h.recent_count}次" + (f" ({sample})" if sample else "")
                    if zh else
                    f"  - {h.hook_type}: {h.recent_count} recent uses"
                    + (f" ({sample})" if sample else "")
                )

        # Beat motifs
        if self.beat_motifs:
            if zh:
                lines.append("已反复出现的节拍/短语（请换新表达）:")
            else:
                lines.append("Recurring beat phrases (rephrase or skip in the new batch):")
            for m in self.beat_motifs[:_MOTIF_CAP]:
                sample = ", ".join(f"ch{c}" for c in m.example_chapters[:4])
                lines.append(
                    f"  - \"{m.phrase}\" ×{m.count}" + (f" ({sample})" if sample else "")
                )

        # Recent conflicts
        if self.recent_conflicts:
            if zh:
                lines.append("近期主要冲突（不要重复相同的事件类型）:")
            else:
                lines.append("Recent main conflicts (avoid repeating the same event type):")
            for ch_num, snippet in self.recent_conflicts[:_RECENT_CONFLICTS_CAP]:
                lines.append(f"  - ch{ch_num}: {snippet}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers — DB → ledger entries
# ---------------------------------------------------------------------------

def _extract_information_revealed(chapter: Any) -> list[tuple[str, str]]:
    """Return a list of ``(name, value)`` pairs from ``chapter.information_revealed``.

    The column historically holds a list of either strings (short bullets)
    or dicts ``{"name": ..., "value": ...}`` — we support both.
    """
    items = getattr(chapter, "information_revealed", None)
    if not items or not isinstance(items, list):
        return []
    out: list[tuple[str, str]] = []
    for entry in items:
        if isinstance(entry, str) and entry.strip():
            out.append((entry.strip()[:40], entry.strip()))
        elif isinstance(entry, dict):
            name = entry.get("name") or entry.get("fact") or entry.get("key")
            value = entry.get("value") or entry.get("description") or entry.get("detail")
            if isinstance(name, str) and name.strip():
                val_s = str(value).strip() if value is not None else ""
                out.append((name.strip(), val_s))
    return out


def _extract_snapshot_facts(snapshot_row: Any) -> list[dict[str, Any]]:
    facts = getattr(snapshot_row, "facts", None)
    if not isinstance(facts, dict):
        return []
    entries = facts.get("facts")
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def _collect_scene_purpose_stories(scenes: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for scene in scenes:
        purpose = getattr(scene, "purpose", None)
        if not isinstance(purpose, dict):
            continue
        story = purpose.get("story")
        if isinstance(story, str) and story.strip():
            out.append(story.strip())
    return out


def _build_beat_motifs(
    chapter_conflicts: list[tuple[int, str]],
    scene_story_map: dict[int, list[str]],
) -> list[BeatMotif]:
    """Find recurring short phrases that dominate the plan.

    We use a coarse heuristic: group by a *normalized* key, and flag any
    phrase that appears ≥ _BEAT_MOTIF_MIN_COUNT times. This catches both
    verbatim repeats ("继续推进真相揭示") and exact main_conflict replays.
    """
    counter: Counter[str] = Counter()
    examples: dict[str, list[int]] = {}

    def _record(phrase: str, ch_num: int) -> None:
        norm = _normalize_motif(phrase)
        if not norm or len(norm) < 6:
            return
        counter[norm] += 1
        examples.setdefault(norm, []).append(ch_num)

    for ch_num, conflict in chapter_conflicts:
        if conflict:
            _record(conflict, ch_num)
    for ch_num, stories in scene_story_map.items():
        for story in stories:
            _record(story, ch_num)

    motifs: list[BeatMotif] = []
    for phrase, count in counter.most_common():
        if count < _BEAT_MOTIF_MIN_COUNT:
            break
        example_chapters = tuple(sorted(set(examples.get(phrase, [])))[:8])
        motifs.append(BeatMotif(
            phrase=phrase,
            count=count,
            example_chapters=example_chapters,
        ))
    return motifs


def _build_hook_usage(
    chapter_hook_pairs: list[tuple[int, str]],
    *,
    recent_window: int = _RECENT_WINDOW,
) -> list[HookUsage]:
    if not chapter_hook_pairs:
        return []
    ordered = sorted(chapter_hook_pairs, key=lambda p: p[0])
    all_counts: Counter[str] = Counter()
    for _, hook in ordered:
        if hook:
            all_counts[hook] += 1
    latest_ch = ordered[-1][0]
    recent_cutoff = latest_ch - recent_window + 1
    recent_counts: Counter[str] = Counter()
    recent_examples: dict[str, list[int]] = {}
    for ch_num, hook in ordered:
        if ch_num < recent_cutoff or not hook:
            continue
        recent_counts[hook] += 1
        recent_examples.setdefault(hook, []).append(ch_num)

    usage: list[HookUsage] = []
    for hook, total in all_counts.most_common():
        recent = recent_counts.get(hook, 0)
        if recent == 0 and total < _HOOK_OVERUSE_THRESHOLD:
            continue
        usage.append(HookUsage(
            hook_type=hook,
            total_count=total,
            recent_count=recent,
            recent_chapters=tuple(sorted(recent_examples.get(hook, []))),
        ))
    return usage


def _snippet(text: str, limit: int = _CONFLICT_SNIPPET_MAX) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip())
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def build_revealed_ledger(
    session: AsyncSession,
    project_id: UUID,
    *,
    up_to_chapter: int | None = None,
    recent_window: int = _RECENT_WINDOW,
) -> RevealedLedger:
    """Build an aggregate ledger for ``project_id``.

    Parameters
    ----------
    session
        An open async SQLAlchemy session.
    project_id
        The project whose chapters / scenes / state snapshots we scan.
    up_to_chapter
        If set, only chapters with ``chapter_number <= up_to_chapter`` are
        counted. This lets the planner call ``build_revealed_ledger(..., up_to_chapter=N-1)``
        right before planning chapter N.
    recent_window
        Window size (in chapters) for hook-overuse detection.
    """
    # Imports are kept local to avoid a circular import at module load when
    # callers live inside ``bestseller.services.planner``.
    from bestseller.infra.db.models import (
        ChapterModel,
        ChapterStateSnapshotModel,
        SceneCardModel,
    )

    # 1. Chapters
    chapter_q = (
        select(ChapterModel)
        .where(ChapterModel.project_id == project_id)
        .order_by(ChapterModel.chapter_number)
    )
    if up_to_chapter is not None:
        chapter_q = chapter_q.where(ChapterModel.chapter_number <= up_to_chapter)
    chapters = list(await session.scalars(chapter_q))

    if not chapters:
        return RevealedLedger(project_id=project_id)

    chapter_ids = [c.id for c in chapters]
    chapters_covered = tuple(c.chapter_number for c in chapters)
    latest_chapter = chapters[-1].chapter_number
    recent_cutoff = latest_chapter - recent_window + 1

    # 2. Scene cards (batched per-chapter list)
    scene_q = (
        select(SceneCardModel)
        .where(SceneCardModel.chapter_id.in_(chapter_ids))
        .order_by(SceneCardModel.chapter_id, SceneCardModel.scene_number)
    )
    scenes = list(await session.scalars(scene_q))
    scenes_by_chapter: dict[UUID, list[SceneCardModel]] = {}
    for s in scenes:
        scenes_by_chapter.setdefault(s.chapter_id, []).append(s)

    # 3. Chapter state snapshots (facts)
    snap_q = (
        select(ChapterStateSnapshotModel)
        .where(ChapterStateSnapshotModel.project_id == project_id)
        .order_by(ChapterStateSnapshotModel.chapter_number)
    )
    if up_to_chapter is not None:
        snap_q = snap_q.where(ChapterStateSnapshotModel.chapter_number <= up_to_chapter)
    snapshots = list(await session.scalars(snap_q))

    # -- Build facts --
    facts: list[RevealedFactEntry] = []
    seen_fact_keys: set[tuple[str, str]] = set()  # (kind/subject, name) dedup
    for snap in snapshots:
        for entry in _extract_snapshot_facts(snap):
            name = str(entry.get("name") or "").strip()
            value = entry.get("value")
            if not name or value is None:
                continue
            kind = str(entry.get("kind") or "other").strip()
            subject = entry.get("subject")
            subject_s = str(subject).strip() if subject else None
            key = (kind + "|" + (subject_s or ""), name)
            if key in seen_fact_keys:
                continue
            seen_fact_keys.add(key)
            unit = entry.get("unit")
            value_repr = f"{value}{unit}" if unit else str(value)
            facts.append(RevealedFactEntry(
                name=name,
                value=value_repr,
                kind=kind,
                first_chapter=int(snap.chapter_number),
                subject=subject_s,
            ))

    # Pull richer info from ``information_revealed`` too.
    for ch in chapters:
        for name, value in _extract_information_revealed(ch):
            key = ("information|", name)
            if key in seen_fact_keys:
                continue
            seen_fact_keys.add(key)
            facts.append(RevealedFactEntry(
                name=name,
                value=_snippet(value, 80) or name,
                kind="information",
                first_chapter=int(ch.chapter_number),
            ))

    # -- Build hook usage --
    hook_usage = _build_hook_usage(
        [(int(c.chapter_number), (c.hook_type or "").strip()) for c in chapters if c.hook_type],
        recent_window=recent_window,
    )

    # -- Build motifs --
    chapter_conflicts = [
        (int(c.chapter_number), (c.main_conflict or "").strip())
        for c in chapters
        if c.main_conflict
    ]
    scene_story_map: dict[int, list[str]] = {}
    for ch in chapters:
        scene_story_map[int(ch.chapter_number)] = _collect_scene_purpose_stories(
            scenes_by_chapter.get(ch.id, [])
        )
    beat_motifs = _build_beat_motifs(chapter_conflicts, scene_story_map)

    # -- Recent conflicts (just the tail window) --
    recent_conflicts = tuple(
        (ch_num, _snippet(conflict))
        for ch_num, conflict in chapter_conflicts
        if ch_num >= recent_cutoff
    )

    return RevealedLedger(
        project_id=project_id,
        chapters_covered=chapters_covered,
        facts=tuple(facts[:_FACTS_CAP]),
        hook_usage=tuple(hook_usage),
        beat_motifs=tuple(beat_motifs),
        recent_conflicts=recent_conflicts,
    )


def build_revealed_ledger_from_rows(
    project_id: UUID,
    chapters: list[Any],
    scenes_by_chapter: dict[Any, list[Any]] | None = None,
    snapshots: list[Any] | None = None,
    *,
    recent_window: int = _RECENT_WINDOW,
) -> RevealedLedger:
    """Build a ledger from already-loaded rows (sync path, useful in tests
    or callers that already have the data in memory).

    ``chapters`` must be sorted by ``chapter_number`` ascending.
    ``scenes_by_chapter`` keys are chapter primary keys.
    """
    if not chapters:
        return RevealedLedger(project_id=project_id)

    chapters_covered = tuple(int(c.chapter_number) for c in chapters)
    latest = chapters_covered[-1]
    recent_cutoff = latest - recent_window + 1

    scenes_by_chapter = scenes_by_chapter or {}
    snapshots = snapshots or []

    facts: list[RevealedFactEntry] = []
    seen_keys: set[tuple[str, str]] = set()
    for snap in snapshots:
        for entry in _extract_snapshot_facts(snap):
            name = str(entry.get("name") or "").strip()
            value = entry.get("value")
            if not name or value is None:
                continue
            kind = str(entry.get("kind") or "other").strip()
            subject = entry.get("subject")
            subject_s = str(subject).strip() if subject else None
            key = (kind + "|" + (subject_s or ""), name)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unit = entry.get("unit")
            value_repr = f"{value}{unit}" if unit else str(value)
            facts.append(RevealedFactEntry(
                name=name, value=value_repr, kind=kind,
                first_chapter=int(snap.chapter_number), subject=subject_s,
            ))
    for ch in chapters:
        for name, value in _extract_information_revealed(ch):
            key = ("information|", name)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            facts.append(RevealedFactEntry(
                name=name, value=_snippet(value, 80) or name,
                kind="information", first_chapter=int(ch.chapter_number),
            ))

    hook_usage = _build_hook_usage(
        [(int(c.chapter_number), (c.hook_type or "").strip()) for c in chapters if c.hook_type],
        recent_window=recent_window,
    )

    chapter_conflicts = [
        (int(c.chapter_number), (c.main_conflict or "").strip())
        for c in chapters
        if c.main_conflict
    ]
    scene_story_map: dict[int, list[str]] = {}
    for ch in chapters:
        scene_story_map[int(ch.chapter_number)] = _collect_scene_purpose_stories(
            scenes_by_chapter.get(ch.id, [])
        )
    beat_motifs = _build_beat_motifs(chapter_conflicts, scene_story_map)

    recent_conflicts = tuple(
        (ch_num, _snippet(conflict))
        for ch_num, conflict in chapter_conflicts
        if ch_num >= recent_cutoff
    )

    return RevealedLedger(
        project_id=project_id,
        chapters_covered=chapters_covered,
        facts=tuple(facts[:_FACTS_CAP]),
        hook_usage=tuple(hook_usage),
        beat_motifs=tuple(beat_motifs),
        recent_conflicts=recent_conflicts,
    )
