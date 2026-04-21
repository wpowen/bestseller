"""Per-book forward-plan adjustment — canon-respecting recommendations.

Root cause this module addresses
--------------------------------

After the 6-book audit flagged 278 critical chapter-level antagonist
findings across 4 projects, the user directed us to **leave the already
-written canon untouched and make targeted adjustments only to the
*forward* (unwritten) portion of each book**.

The naive "re-run the scaling gates on the whole plan and regenerate"
approach is wrong for two reasons:

  1. It would change antagonist rosters, narrative-line definitions
     and volume themes that already appear in thousands of written
     chapters — breaking continuity.
  2. Scaling-gate findings that fire on *early-book* data (e.g.
     "overt_line has too few arcs") cannot be fixed without
     rewriting canon; listing them as actionable for the forward
     portion is misleading.

This module computes the **frontier volume** (the earliest volume that
still has unwritten chapters) and then produces a report that is
scoped strictly to volume ≥ frontier:

  * ``forward_antagonist_scope`` — which already-defined antagonists
    are still live going forward? Which are retired (span ended
    before the frontier)? Which new antagonists need to be
    introduced in the forward portion to keep each volume covered?
  * ``forward_lifecycle_diversity`` — run the lifecycle diversity
    checks using only antagonists that are active ≥ frontier. Flag
    when the forward-only roster collapses to a single resolution
    type (e.g. "kill them all" in the remaining 12 volumes).
  * ``forward_narrative_coverage`` — for each forward volume, does
    an overt antagonist exist? Does the undercurrent/hidden line
    still have runway?
  * ``recommendations`` — concrete per-volume actions the planner
    can apply without re-running the writer on finished chapters.

The module is pure: inputs are plain dicts / pydantic models, outputs
are frozen dataclasses. It does not hit the DB; callers load data
(see ``scripts/adjust_forward_plan.py``) and feed it in.

Contract notes
--------------

A volume counts as ``written`` if **every** chapter in the volume has
status ``complete``. A volume is ``in_progress`` if it has ≥ 1 written
chapter AND ≥ 1 unwritten chapter. ``unwritten`` volumes have no
complete chapters.

The frontier volume is the first volume that is NOT fully written —
i.e. the earliest volume with work still ahead. For a 20-volume book
where volumes 1-7 are complete and volume 8 has 4/20 chapters done,
the frontier is 8. For a 20-volume book where every volume is fully
written, the frontier is ``volume_count + 1`` (nothing to do).

``forward_volumes`` is the inclusive range ``[frontier, volume_count]``.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from bestseller.services.antagonist_lifecycle import (
    CANONICAL_RESOLUTIONS,
    LINE_ROLE_OVERT,
    MAX_SAME_RESOLUTION_RATIO,
    MIN_NON_KILLED_ANTAGONIST_RATIO,
    RESOLUTION_DEFEATED_AND_KILLED,
)
from bestseller.services.chapter_antagonist_audit import (
    _as_str,
    _mapping,
    _mapping_list,
    _name_is_auditable,
    _parse_stages_to_volumes,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status constants — a chapter/volume must match one of these to count as
# canon-locked. Matches the ChapterStatus enum defined in the domain layer.
# ---------------------------------------------------------------------------

CHAPTER_STATUS_COMPLETE: str = "complete"
CHAPTER_STATUS_REVISION: str = "revision"

# A chapter in either of these statuses is treated as written canon —
# everything upstream (outline, antagonist assignments, theme) should
# not change, because doing so would invalidate the drafted text.
CANON_CHAPTER_STATUSES: frozenset[str] = frozenset(
    {CHAPTER_STATUS_COMPLETE, CHAPTER_STATUS_REVISION}
)


# ---------------------------------------------------------------------------
# Frozen report dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AntagonistForwardSummary:
    """Per-antagonist view of where each entry stands vs. the frontier."""

    name: str
    line_role: str
    volume_span: tuple[int, int] | None
    resolution_type: str
    status_vs_frontier: str  # "retired" | "carries_forward" | "fully_forward"


@dataclass(frozen=True)
class VolumeForwardCoverage:
    """One forward volume and its antagonist coverage."""

    volume_number: int
    overt_antagonists: tuple[str, ...]
    undercurrent_antagonists: tuple[str, ...]
    hidden_antagonists: tuple[str, ...]
    has_overt: bool

    @property
    def is_covered(self) -> bool:
        return self.has_overt


@dataclass(frozen=True)
class ForwardRecommendation:
    """Concrete recommended action (human-readable)."""

    code: str
    severity: str  # "critical" | "warning" | "info"
    message: str
    volume_number: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ForwardPlanReport:
    """Aggregate forward-only audit for one project."""

    project_slug: str
    volume_count: int
    frontier_volume: int
    forward_volumes: tuple[int, ...]
    fully_written_volumes: tuple[int, ...]
    in_progress_volumes: tuple[int, ...]
    unwritten_volumes: tuple[int, ...]
    antagonist_summaries: tuple[AntagonistForwardSummary, ...]
    coverage_by_volume: tuple[VolumeForwardCoverage, ...]
    resolution_distribution_forward: dict[str, int]
    uncovered_forward_volumes: tuple[int, ...]
    recommendations: tuple[ForwardRecommendation, ...]

    @property
    def critical_count(self) -> int:
        return sum(1 for r in self.recommendations if r.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.recommendations if r.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for r in self.recommendations if r.severity == "info")

    @property
    def has_forward_work(self) -> bool:
        return bool(self.forward_volumes)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chapter_is_written(chapter: Any) -> bool:
    status = _as_str(_mapping(chapter).get("status")).lower()
    return status in CANON_CHAPTER_STATUSES


def _volume_span_from_plan(plan: dict[str, Any]) -> tuple[int, int] | None:
    scope = plan.get("scope_volume_number")
    stages = _parse_stages_to_volumes(plan.get("stages_of_relevance"))
    volumes: set[int] = set()
    if scope is not None:
        try:
            v = int(scope)
            if v > 0:
                volumes.add(v)
        except (TypeError, ValueError):
            pass
    volumes.update(stages)
    if not volumes:
        return None
    return (min(volumes), max(volumes))


def _status_vs_frontier(
    span: tuple[int, int] | None, frontier: int
) -> str:
    """Classify an antagonist relative to the forward frontier.

    * ``retired`` — span ends before the frontier; only appears in canon.
    * ``carries_forward`` — span straddles the frontier (entered
      before, still active at / past it).
    * ``fully_forward`` — span starts at or after the frontier; the
      antagonist is entirely in the unwritten portion.
    * ``book_wide`` — no span information, treat as still active.
    """
    if span is None:
        return "book_wide"
    start, end = span
    if end < frontier:
        return "retired"
    if start >= frontier:
        return "fully_forward"
    return "carries_forward"


# ---------------------------------------------------------------------------
# Frontier computation
# ---------------------------------------------------------------------------


def compute_frontier_volume(
    chapters: Iterable[Any],
    *,
    volume_count: int,
) -> tuple[int, set[int], set[int], set[int]]:
    """Return ``(frontier, fully_written, in_progress, unwritten)``.

    ``chapters`` is a list of dicts/pydantic with ``volume_number``
    and ``status``. A volume is fully_written iff it has ≥ 1 chapter
    AND every chapter has a canon status. A volume with zero chapters
    is treated as ``unwritten`` (planning hasn't materialised yet).

    The frontier is the smallest volume number that is NOT in
    ``fully_written``. If every volume is fully written, the frontier
    is ``volume_count + 1`` (meaning "no forward work").
    """
    volume_count = max(int(volume_count or 0), 1)
    written: dict[int, bool] = {v: True for v in range(1, volume_count + 1)}
    any_chapter: dict[int, bool] = {v: False for v in range(1, volume_count + 1)}
    has_unwritten: dict[int, bool] = {v: False for v in range(1, volume_count + 1)}

    for ch in _mapping_list(chapters):
        try:
            vn = int(ch.get("volume_number") or 0)
        except (TypeError, ValueError):
            continue
        if vn < 1 or vn > volume_count:
            continue
        any_chapter[vn] = True
        if _chapter_is_written(ch):
            # leave written[vn] = True unless a later unwritten chapter flips it
            pass
        else:
            written[vn] = False
            has_unwritten[vn] = True

    # A volume with NO chapters was initialised as written=True — fix that.
    for v in range(1, volume_count + 1):
        if not any_chapter[v]:
            written[v] = False
            has_unwritten[v] = True

    fully_written = {v for v in range(1, volume_count + 1) if written[v]}
    unwritten: set[int] = set()
    in_progress: set[int] = set()
    for v in range(1, volume_count + 1):
        if v in fully_written:
            continue
        if any_chapter[v] and any(
            _chapter_is_written(ch)
            for ch in _mapping_list(chapters)
            if _as_str(_mapping(ch).get("volume_number")) == str(v)
            or _mapping(ch).get("volume_number") == v
        ):
            in_progress.add(v)
        else:
            unwritten.add(v)

    # Frontier: first volume ≥ 1 that is not fully_written
    frontier = volume_count + 1
    for v in range(1, volume_count + 1):
        if v not in fully_written:
            frontier = v
            break

    return frontier, fully_written, in_progress, unwritten


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_forward_plan_report(
    *,
    project_slug: str,
    volume_count: int,
    chapters: Iterable[Any],
    antagonist_plans: Iterable[Any],
    language: str = "zh-CN",
) -> ForwardPlanReport:
    """Produce a canon-respecting forward-plan audit for one project.

    Parameters
    ----------
    project_slug
        Human-readable identifier, only used in output — no DB access.
    volume_count
        Total number of volumes declared in the book outline.
    chapters
        Iterable of ``{"chapter_number", "volume_number", "status"}``
        dict-likes. Status classification follows
        :data:`CANON_CHAPTER_STATUSES`.
    antagonist_plans
        Iterable of ``{"name", "line_role", "scope_volume_number",
        "stages_of_relevance", "resolution_type"}`` dict-likes.
    language
        ``zh-CN`` (default) or ``en-US`` — controls recommendation text.

    Returns
    -------
    :class:`ForwardPlanReport`
        A frozen dataclass describing the forward-only state.
    """
    volume_count = max(int(volume_count or 0), 1)
    frontier, fully_written, in_progress, unwritten = compute_frontier_volume(
        chapters, volume_count=volume_count
    )
    forward_volumes = tuple(range(frontier, volume_count + 1)) if frontier <= volume_count else ()

    # Per-antagonist classification --------------------------------------
    plan_rows = _mapping_list(antagonist_plans)
    antag_summaries: list[AntagonistForwardSummary] = []
    forward_active_by_volume: dict[int, dict[str, list[str]]] = {
        v: {"overt": [], "undercurrent": [], "hidden": []} for v in forward_volumes
    }
    resolution_counter_forward: Counter[str] = Counter()

    for plan in plan_rows:
        name = _as_str(plan.get("name") or plan.get("antagonist_label"))
        if not _name_is_auditable(name):
            continue
        line_role = _as_str(plan.get("line_role")).lower()
        resolution = _as_str(plan.get("resolution_type")).lower()
        span = _volume_span_from_plan(plan)
        status = _status_vs_frontier(span, frontier)
        antag_summaries.append(
            AntagonistForwardSummary(
                name=name,
                line_role=line_role,
                volume_span=span,
                resolution_type=resolution,
                status_vs_frontier=status,
            )
        )

        if status == "retired":
            # not in the forward distribution
            continue

        # Count towards forward resolution distribution (only antagonists
        # whose arcs have any run-time in the forward portion).
        resolution_counter_forward[resolution or "missing"] += 1

        # Populate per-volume coverage
        scoped_volumes = set()
        if plan.get("scope_volume_number") is not None:
            try:
                sv = int(plan["scope_volume_number"])
                if sv > 0:
                    scoped_volumes.add(sv)
            except (TypeError, ValueError):
                pass
        scoped_volumes.update(_parse_stages_to_volumes(plan.get("stages_of_relevance")))
        if not scoped_volumes:
            # Book-wide: treat as present in every forward volume
            scoped_volumes = set(forward_volumes)

        for v in scoped_volumes:
            if v not in forward_active_by_volume:
                continue
            bucket = "overt" if line_role == LINE_ROLE_OVERT else (
                "undercurrent" if line_role == "undercurrent" else (
                    "hidden" if line_role == "hidden" else "overt"
                )
            )
            forward_active_by_volume[v][bucket].append(name)

    # Per-forward-volume coverage records --------------------------------
    coverage: list[VolumeForwardCoverage] = []
    uncovered_forward: list[int] = []
    for v in forward_volumes:
        buckets = forward_active_by_volume[v]
        overt = tuple(sorted(set(buckets["overt"])))
        undercurrent = tuple(sorted(set(buckets["undercurrent"])))
        hidden = tuple(sorted(set(buckets["hidden"])))
        has_overt = bool(overt)
        coverage.append(
            VolumeForwardCoverage(
                volume_number=v,
                overt_antagonists=overt,
                undercurrent_antagonists=undercurrent,
                hidden_antagonists=hidden,
                has_overt=has_overt,
            )
        )
        if not has_overt:
            uncovered_forward.append(v)

    # Recommendations ----------------------------------------------------
    recommendations = _build_recommendations(
        frontier=frontier,
        volume_count=volume_count,
        forward_volumes=forward_volumes,
        coverage=coverage,
        resolution_counter_forward=resolution_counter_forward,
        antag_summaries=antag_summaries,
        language=language,
    )

    return ForwardPlanReport(
        project_slug=project_slug,
        volume_count=volume_count,
        frontier_volume=frontier,
        forward_volumes=forward_volumes,
        fully_written_volumes=tuple(sorted(fully_written)),
        in_progress_volumes=tuple(sorted(in_progress)),
        unwritten_volumes=tuple(sorted(unwritten)),
        antagonist_summaries=tuple(antag_summaries),
        coverage_by_volume=tuple(coverage),
        resolution_distribution_forward=dict(resolution_counter_forward),
        uncovered_forward_volumes=tuple(uncovered_forward),
        recommendations=tuple(recommendations),
    )


def _is_english(language: str | None) -> bool:
    if not language:
        return False
    return language.lower().startswith("en")


def _build_recommendations(
    *,
    frontier: int,
    volume_count: int,
    forward_volumes: tuple[int, ...],
    coverage: list[VolumeForwardCoverage],
    resolution_counter_forward: Counter[str],
    antag_summaries: list[AntagonistForwardSummary],
    language: str,
) -> list[ForwardRecommendation]:
    """Deterministic rule-set over the forward-only view.

    Each rule must be explainable in a single sentence — we are asking
    the planner (human or LLM) to make a concrete change, not to
    re-interpret a whole book.
    """
    is_en = _is_english(language)
    recs: list[ForwardRecommendation] = []

    # Short-circuit: no forward work at all.
    if not forward_volumes:
        recs.append(
            ForwardRecommendation(
                code="no_forward_work",
                severity="info",
                message=(
                    "Every volume is fully written — no forward adjustment needed."
                    if is_en
                    else "全书已全部完成，无需再做前瞻性调整。"
                ),
            )
        )
        return recs

    # Rule 1: forward volumes with no overt antagonist.
    for cov in coverage:
        if not cov.has_overt:
            recs.append(
                ForwardRecommendation(
                    code="forward_volume_missing_overt_antagonist",
                    severity="critical",
                    volume_number=cov.volume_number,
                    message=(
                        f"Volume {cov.volume_number} (forward) has no active "
                        "overt antagonist. Introduce a stage-boss whose "
                        "stages_of_relevance covers this volume, OR extend "
                        "an existing overt antagonist's span."
                        if is_en
                        else f"第 {cov.volume_number} 卷（前瞻卷）没有活跃的明线敌人。"
                        "需要新立一位本卷的明线敌人（stages_of_relevance 覆盖本卷），"
                        "或把已有明线敌人的活跃区间延伸到本卷。"
                    ),
                    payload={"volume": cov.volume_number},
                )
            )

    # Rule 2: forward-portion resolution distribution collapses to a
    # single value ("kill them all in the remaining volumes").
    scored = sum(v for k, v in resolution_counter_forward.items() if k != "missing")
    if scored >= 3:
        most_type, most_count = None, 0
        for k, v in resolution_counter_forward.items():
            if k == "missing":
                continue
            if v > most_count:
                most_type, most_count = k, v
        share = most_count / scored if scored else 0.0
        if share > MAX_SAME_RESOLUTION_RATIO:
            recs.append(
                ForwardRecommendation(
                    code="forward_monotonous_resolution_types",
                    severity="warning",
                    message=(
                        f"{int(share * 100)}% of forward-portion antagonists "
                        f"resolve as '{most_type}'. Diversify the palette "
                        f"(one of: {'|'.join(CANONICAL_RESOLUTIONS)})."
                        if is_en
                        else f"前瞻卷的敌人结局有 {int(share * 100)}% 都是"
                        f"『{most_type}』，建议在剩余卷里多样化结局"
                        f"（可选：{'｜'.join(CANONICAL_RESOLUTIONS)}）。"
                    ),
                    payload={
                        "dominant_resolution": most_type,
                        "share": share,
                    },
                )
            )

        # Check non-killed ratio in forward portion
        non_killed = sum(
            v for k, v in resolution_counter_forward.items()
            if k not in (RESOLUTION_DEFEATED_AND_KILLED, "missing")
        )
        non_killed_ratio = non_killed / scored if scored else 0.0
        if non_killed_ratio < MIN_NON_KILLED_ANTAGONIST_RATIO:
            recs.append(
                ForwardRecommendation(
                    code="forward_lacks_non_killed_outcomes",
                    severity="warning",
                    message=(
                        f"Only {int(non_killed_ratio * 100)}% of forward-portion "
                        "antagonists have a non-killed resolution. Flip at "
                        "least one forward antagonist to transformed / "
                        "disappeared / outlived."
                        if is_en
                        else f"前瞻卷里只有 {int(non_killed_ratio * 100)}% 的敌人"
                        "走的是『被杀』以外的结局，建议把至少一个前瞻敌人"
                        "改为『转化为盟友/中立』『消失未解』『被主角超越而无关』。"
                    ),
                    payload={"non_killed_ratio": non_killed_ratio},
                )
            )

    # Rule 3: if there are ≥ 2 forward volumes but they share a single
    # overt antagonist (same name across all forward volumes), flag
    # it as a "rotation collapse in the remaining book".
    overt_counter: Counter[str] = Counter()
    for cov in coverage:
        for name in cov.overt_antagonists:
            overt_counter[name] += 1
    if len(forward_volumes) >= 2 and overt_counter:
        dominant_overt, dom_count = overt_counter.most_common(1)[0]
        if dom_count == len(forward_volumes) and len(overt_counter) == 1:
            recs.append(
                ForwardRecommendation(
                    code="forward_single_overt_across_all_remaining",
                    severity="warning",
                    message=(
                        f"Every forward volume is covered by the same overt "
                        f"antagonist '{dominant_overt}'. Consider rotating "
                        "in at least one additional stage-boss so the "
                        "remaining volumes don't read as one long setpiece."
                        if is_en
                        else f"所有前瞻卷都只由同一明线敌人『{dominant_overt}』"
                        "承担，建议至少再引入一位阶段性敌人，避免剩余卷"
                        "读起来像一场绵延不绝的同一场战斗。"
                    ),
                    payload={"antagonist": dominant_overt},
                )
            )

    # Rule 3b: "ghost antagonist" — a single overt antagonist appears in
    # ALL forward volumes while other antagonists rotate. This is the
    # signature of a mis-scoped carryover (the V2 boss still listed as
    # active in V7-V22, as we observed in 道种破虚). Multiple "ghosts"
    # is still suspect: every one of them needs the planner to confirm
    # whether it's the hidden/core-axis antagonist (book-spanning is
    # legitimate there) or a scoping bug to fix.
    if len(forward_volumes) >= 3 and len(overt_counter) >= 2:
        ghost_names = [
            name for name, count in overt_counter.items()
            if count == len(forward_volumes)
        ]
        if ghost_names:
            recs.append(
                ForwardRecommendation(
                    code="forward_book_wide_overt_antagonist",
                    severity="warning",
                    message=(
                        f"Antagonist(s) {ghost_names} appear as overt in "
                        f"every one of the {len(forward_volumes)} forward "
                        "volumes. If this is a hidden/core-axis enemy, "
                        "re-classify its line_role to 'hidden' or "
                        "'undercurrent'. Otherwise tighten its "
                        "stages_of_relevance to the volumes where it is "
                        "actually the present-tense stage boss — leaving "
                        "it as book-wide overt reproduces the 道种破虚 "
                        "leak where a retired boss kept showing up."
                        if is_en
                        else f"敌人 {ghost_names} 在所有 {len(forward_volumes)} 个前瞻卷"
                        "都作为『明线』出现。如果它其实是隐藏线/核心轴上的终极敌人，"
                        "请把 line_role 改为 'hidden' 或 'undercurrent'；"
                        "否则请把 stages_of_relevance 收窄到它真正作为当下舞台敌人的卷——"
                        "如果让它保持『全书明线』，会复现道种破虚里退役敌人不断乱入的那种漏。"
                    ),
                    payload={"antagonists": list(ghost_names)},
                )
            )

    # Rule 4: no forward-active antagonists at all — the remaining book
    # has been deprived of enemy pressure.
    forward_active = [
        s for s in antag_summaries
        if s.status_vs_frontier in ("carries_forward", "fully_forward", "book_wide")
    ]
    if not forward_active and forward_volumes:
        recs.append(
            ForwardRecommendation(
                code="no_forward_active_antagonists",
                severity="critical",
                message=(
                    f"{len(forward_volumes)} remaining volume(s) have no "
                    "antagonist whose span reaches the frontier. The forward "
                    "portion has been left without pressure — urgently add "
                    "at least one overt + one undercurrent antagonist."
                    if is_en
                    else f"剩余的 {len(forward_volumes)} 卷已经没有任何敌人覆盖到当前推进点。"
                    "前瞻部分失去了故事压力，需紧急补上至少一位明线敌人和一位暗线敌人。"
                ),
                payload={"remaining_volumes": len(forward_volumes)},
            )
        )

    # Rule 5: informational — summarise the scope.
    recs.append(
        ForwardRecommendation(
            code="forward_scope_summary",
            severity="info",
            message=(
                f"Forward scope: volumes {frontier}..{volume_count} "
                f"({len(forward_volumes)} volumes, "
                f"{len(forward_active)} antagonists still active)."
                if is_en
                else f"前瞻范围：第 {frontier} 卷至第 {volume_count} 卷"
                f"（共 {len(forward_volumes)} 卷，仍有 {len(forward_active)} 位敌人活跃）。"
            ),
        )
    )

    return recs


__all__ = [
    "CANON_CHAPTER_STATUSES",
    "AntagonistForwardSummary",
    "ForwardPlanReport",
    "ForwardRecommendation",
    "VolumeForwardCoverage",
    "build_forward_plan_report",
    "compute_frontier_volume",
]
