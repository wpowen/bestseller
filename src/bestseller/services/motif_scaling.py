"""Motif-placement scaling gate — validate that the narrative-layer motif
schedule scales with the novel's volume count.

Root cause this module addresses
--------------------------------

Inspection of ``narrative._build_motif_placement_specs`` (pre-fix) showed
that the motif layer was **globally hard-coded to 4 placements** for any
novel length:

    placement_points = [
        (0,            "plant"),
        (total // 4,   "echo"),
        (total // 2,   "transform"),
        (total - 1,    "resolve"),
    ]

The consequences across the 6-book audit:

  * 316-chapter novel (道种破虚, 24 volumes): 4 motif placements over
    316 chapters → one placement per ~79 chapters, and *20 of 24 volumes
    had zero motif placements*. The "core theme imagery" never surfaced
    inside 80 %+ of the chapters, so every volume collapsed to the same
    surface-level pressure template.
  * 800/1200-chapter novels: even worse — one placement per 200-300
    chapters means the motif layer is effectively invisible.

Both failure modes share the same root cause: the motif layer did not
scale to **volume count**. This module provides the scaling bounds and
findings; the narrative builder applies the bounds when it constructs
the motif placement specs.

Scaling contract (calibrated against the 6-book data; see
:func:`compute_motif_bounds`):

    total placements : floor   = max(2 × volumes, 8)
                       ceiling = max(4 × volumes, 16)
    per-volume       : each volume MUST receive ≥ 1 placement (V1
                       frequently gets plant+echo, the final volume
                       typically gets transform+resolve)

Below floor → "starved_motif_placements" critical finding (the theme
never grounds in most volumes). Above ceiling → "bloated_motif_placements"
warning (motifs dilute into noise).
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Per-volume multipliers. The floor target is 2 placements per volume
# (enough for at least one echo per volume on top of the volume's anchor)
# and the ceiling is 4 (beyond which motifs start cannibalising each
# other's attention budget).
MOTIFS_PER_VOLUME_FLOOR: int = 2
MOTIFS_PER_VOLUME_CEILING: int = 4

# Absolute minimums — even tiny novellas need this many motif placements
# to give the theme a plant→echo→transform→resolve arc.
MIN_TOTAL_MOTIFS: int = 8
MIN_TOTAL_MOTIFS_CEILING: int = 16

# Each volume must receive ≥ this many placements — violating this rule
# is the canonical dead-zone failure mode (20/24 volumes empty in
# 道种破虚).
MIN_PLACEMENTS_PER_VOLUME: int = 1

# A streak of consecutive volumes missing any motif. One orphan volume
# is tolerated (the rhythm sometimes skips a beat); two in a row is a
# structural dead zone.
MAX_CONSECUTIVE_EMPTY_MOTIF_VOLUMES: int = 1

# Canonical placement types expected across the plant→resolve rhythm.
EXPECTED_PLACEMENT_TYPES: tuple[str, ...] = (
    "plant",
    "echo",
    "transform",
    "resolve",
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MotifScalingFinding:
    """One audit finding against the motif placement schedule."""

    code: str              # short identifier, stable across runs
    severity: str          # "critical" | "warning"
    message: str           # human-readable message (zh or en)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MotifScalingBounds:
    """Floor/ceiling bounds for total motif placements."""

    floor: int
    ceiling: int


@dataclass(frozen=True)
class MotifScalingReport:
    """Aggregate scan of a set of motif placement specs."""

    total_chapters: int
    volume_count: int
    placement_count: int
    placement_bounds: MotifScalingBounds
    volumes_without_motifs: tuple[int, ...]
    placement_types_missing: tuple[str, ...]
    findings: tuple[MotifScalingFinding, ...]

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    @property
    def is_critical(self) -> bool:
        return self.critical_count > 0

    def to_prompt_block(self, *, language: str = "zh-CN") -> str:
        """Render the report into a repair prompt block."""

        if not self.findings:
            return ""

        is_en = _is_english(language)
        lines: list[str] = []
        if is_en:
            lines.append("[MOTIF SCALING REPAIR — hard requirements]")
            lines.append(
                f"- The motif schedule MUST contain between "
                f"{self.placement_bounds.floor} and "
                f"{self.placement_bounds.ceiling} placements across "
                f"{self.volume_count} volumes "
                f"(currently {self.placement_count})."
            )
            lines.append(
                "- Every volume MUST receive ≥ 1 placement; volumes "
                "without a motif lose their share of the theme arc."
            )
            lines.append(
                "- The placement_type sequence should cover "
                f"{list(EXPECTED_PLACEMENT_TYPES)} at least once each "
                "across the full novel (plant early, resolve late)."
            )
            lines.append("")
            lines.append("Current findings (fix ALL critical):")
        else:
            lines.append("【主题意象密度修复 — 硬性要求】")
            lines.append(
                f"- motif 排布总数必须在 {self.placement_bounds.floor} 到 "
                f"{self.placement_bounds.ceiling} 之间（当前 "
                f"{self.placement_count}，共 {self.volume_count} 卷）。"
            )
            lines.append(
                "- 每卷至少要有 1 条 motif 排布；缺失的卷会失去主题落地点。"
            )
            lines.append(
                "- 全书 placement_type 序列至少要覆盖 "
                f"{list(EXPECTED_PLACEMENT_TYPES)} 各一次（plant 早布、resolve 收束）。"
            )
            lines.append("")
            lines.append("当前审查结果（所有 critical 项必须修复）：")

        for finding in self.findings:
            bullet = "×" if finding.severity == "critical" else "!"
            lines.append(f"  {bullet} [{finding.severity}] {finding.code}: {finding.message}")

        return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_english(language: str | None) -> bool:
    if not language:
        return False
    return language.lower().startswith("en")


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
        except Exception:
            return {}
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "__dict__"):
        return {k: v for k, v in value.__dict__.items() if not k.startswith("_")}
    return {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return []
    return [_mapping(item) for item in value if item is not None]


def compute_motif_bounds(volume_count: int) -> MotifScalingBounds:
    """Derive (floor, ceiling) for total motif placements from volume count.

    Guarantees:
      * floor ≥ ``MIN_TOTAL_MOTIFS``
      * ceiling ≥ floor (and never below ``MIN_TOTAL_MOTIFS_CEILING``)
      * ceiling ≤ ``MAX_REASONABLE_TOTAL_MOTIFS``
    """

    volumes = max(int(volume_count or 0), 1)
    floor = max(MIN_TOTAL_MOTIFS, volumes * MOTIFS_PER_VOLUME_FLOOR)
    ceiling = max(
        MIN_TOTAL_MOTIFS_CEILING,
        volumes * MOTIFS_PER_VOLUME_CEILING,
    )
    if ceiling < floor:
        ceiling = floor * 2
    return MotifScalingBounds(floor=floor, ceiling=ceiling)


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def scan_motif_placements(
    placements: Any,
    *,
    total_chapters: int,
    volume_count: int,
    language: str = "zh-CN",
) -> MotifScalingReport:
    """Audit a motif-placement schedule for scale-appropriate density.

    Parameters
    ----------
    placements
        A list of motif-placement spec dicts (or pydantic models).
        Each entry is expected to carry ``volume_number`` (int, optional
        — some specs carry only ``chapter_number``) and
        ``placement_type``.
    total_chapters
        Target chapter count for the novel (used for observability only;
        volume_count drives the scaling math).
    volume_count
        Planned volume count for the novel. Drives the per-volume floor.
    language
        Locale for generated messages.

    Returns
    -------
    MotifScalingReport
        Empty findings means the motif schedule is healthy enough to
        carry the theme layer through the novel.
    """

    is_en = _is_english(language)

    specs = _mapping_list(placements)
    volume_count = max(int(volume_count or 0), 1)
    bounds = compute_motif_bounds(volume_count)
    findings: list[MotifScalingFinding] = []

    placement_count = len(specs)
    present_types = tuple(sorted({
        str(s.get("placement_type") or "").strip().lower()
        for s in specs
        if str(s.get("placement_type") or "").strip()
    }))
    missing_types = tuple(
        t for t in EXPECTED_PLACEMENT_TYPES if t not in present_types
    )

    # Per-volume counts
    per_volume_counts: Counter[int] = Counter()
    for s in specs:
        vol = s.get("volume_number")
        if isinstance(vol, int) and vol > 0:
            per_volume_counts[vol] += 1

    volumes_without_motifs = tuple(
        v for v in range(1, volume_count + 1)
        if per_volume_counts.get(v, 0) < MIN_PLACEMENTS_PER_VOLUME
    )

    # ── Total placement floor/ceiling ────────────────────────────────
    if placement_count < bounds.floor:
        findings.append(
            MotifScalingFinding(
                code="starved_motif_placements",
                severity="critical",
                message=(
                    f"motif schedule has only {placement_count} placements "
                    f"across {volume_count} volumes; need ≥ {bounds.floor}. "
                    "Most volumes will have no theme anchor."
                    if is_en
                    else f"motif 排布仅 {placement_count} 条，"
                    f"覆盖 {volume_count} 卷，至少需要 {bounds.floor} 条，"
                    "否则大多数卷没有主题落地点。"
                ),
                payload={
                    "count": placement_count,
                    "floor": bounds.floor,
                },
            )
        )
    elif placement_count > bounds.ceiling:
        findings.append(
            MotifScalingFinding(
                code="bloated_motif_placements",
                severity="warning",
                message=(
                    f"motif schedule has {placement_count} placements across "
                    f"{volume_count} volumes; > ceiling {bounds.ceiling}. "
                    "Signal will dilute into noise."
                    if is_en
                    else f"motif 排布共 {placement_count} 条，覆盖 "
                    f"{volume_count} 卷，超过上限 {bounds.ceiling}，"
                    "主题信号会被稀释。"
                ),
                payload={
                    "count": placement_count,
                    "ceiling": bounds.ceiling,
                },
            )
        )

    # ── Per-volume dead zones ────────────────────────────────────────
    if volumes_without_motifs:
        findings.append(
            MotifScalingFinding(
                code="volume_motif_dead_zone",
                severity="critical",
                message=(
                    f"Volumes with zero motif placements (every volume must "
                    f"carry ≥ 1): {list(volumes_without_motifs)}"
                    if is_en
                    else f"未排布任何 motif 的卷（每卷至少 1 条）："
                    f"{list(volumes_without_motifs)}"
                ),
                payload={"volumes": list(volumes_without_motifs)},
            )
        )

    # ── Consecutive dead-streak check ───────────────────────────────
    streak_start: int | None = None
    worst_streak_run: tuple[int, int] | None = None
    # Iterate V1..Vn to detect runs of consecutive empty volumes. Unlike
    # foreshadowing, V1 is NOT exempt — it should carry at least the
    # "plant" placement.
    for v in range(1, volume_count + 1):
        if per_volume_counts.get(v, 0) < MIN_PLACEMENTS_PER_VOLUME:
            if streak_start is None:
                streak_start = v
        else:
            if streak_start is not None:
                run_len = v - streak_start
                if run_len > MAX_CONSECUTIVE_EMPTY_MOTIF_VOLUMES:
                    if worst_streak_run is None or run_len > (
                        worst_streak_run[1] - worst_streak_run[0]
                    ):
                        worst_streak_run = (streak_start, v)
                streak_start = None
    if streak_start is not None:
        run_len = (volume_count + 1) - streak_start
        if run_len > MAX_CONSECUTIVE_EMPTY_MOTIF_VOLUMES:
            if worst_streak_run is None or run_len > (
                worst_streak_run[1] - worst_streak_run[0]
            ):
                worst_streak_run = (streak_start, volume_count + 1)

    if worst_streak_run is not None:
        start_v, end_v_exclusive = worst_streak_run
        last_v = end_v_exclusive - 1
        findings.append(
            MotifScalingFinding(
                code="consecutive_motif_dead_streak",
                severity="critical",
                message=(
                    f"Volumes {start_v}-{last_v} are a consecutive motif dead "
                    "zone (no placements for 2+ volumes in a row); the theme "
                    "arc loses continuity here."
                    if is_en
                    else f"第 {start_v}-{last_v} 卷连续 2 卷以上没有任何 motif，"
                    "主题线在此断裂。"
                ),
                payload={
                    "volumes": list(range(start_v, last_v + 1)),
                },
            )
        )

    # ── Missing canonical placement types ───────────────────────────
    # Only fire the warning when we do have placements but they cluster
    # in a subset of types (e.g. all "plant" and no "resolve").
    if placement_count >= bounds.floor and missing_types:
        findings.append(
            MotifScalingFinding(
                code="missing_motif_placement_types",
                severity="warning",
                message=(
                    f"motif schedule is missing placement_type(s): "
                    f"{list(missing_types)}. Expected to cover "
                    f"{list(EXPECTED_PLACEMENT_TYPES)}."
                    if is_en
                    else f"motif 排布缺少 placement_type：{list(missing_types)}，"
                    f"应覆盖 {list(EXPECTED_PLACEMENT_TYPES)} 各至少一次。"
                ),
                payload={"missing": list(missing_types)},
            )
        )

    return MotifScalingReport(
        total_chapters=max(int(total_chapters or 0), 1),
        volume_count=volume_count,
        placement_count=placement_count,
        placement_bounds=bounds,
        volumes_without_motifs=volumes_without_motifs,
        placement_types_missing=missing_types,
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# Prompt-block renderer for upstream (volume-plan / narrative seed) prompts
# ---------------------------------------------------------------------------

def render_motif_constraints_block(
    *,
    total_chapters: int,
    volume_count: int,
    language: str = "zh-CN",
) -> str:
    """Render the up-front motif scaling constraints.

    This is consumed by prompts that derive motif placements (volume
    plan, narrative seed) so the LLM knows the target per-volume motif
    budget up front, preventing the starvation failure mode observed
    across the 6-book audit.
    """

    volume_count = max(int(volume_count or 0), 1)
    bounds = compute_motif_bounds(volume_count)
    per_volume_floor = max(MIN_PLACEMENTS_PER_VOLUME, bounds.floor // volume_count)

    if _is_english(language):
        return (
            "[MOTIF SCALING HARD CONSTRAINTS]\n"
            f"- Target plan: {total_chapters} chapters across "
            f"{volume_count} volumes.\n"
            f"- Total motif placements MUST be between {bounds.floor} and "
            f"{bounds.ceiling} (~{per_volume_floor}+ per volume).\n"
            "- Every volume MUST carry ≥ 1 motif placement. Volumes with "
            "zero motif anchors lose their theme grounding.\n"
            "- The placement_type sequence must cover "
            f"{list(EXPECTED_PLACEMENT_TYPES)} at least once across the "
            "full novel (plant early, resolve late).\n"
            "- Each placement should name a CONCRETE symbol/object/scene "
            "that the chapter writer can stage — not a vague theme label.\n"
        )

    return (
        "【主题意象密度硬性要求】\n"
        f"- 全书规划：{total_chapters} 章，共 {volume_count} 卷。\n"
        f"- motif 排布总数必须在 {bounds.floor} 到 {bounds.ceiling} 之间"
        f"（每卷 ≥ {per_volume_floor} 条）。\n"
        "- 每卷必须至少安排 1 条 motif；缺失的卷会失去主题落地点。\n"
        f"- 全书 placement_type 序列应覆盖 {list(EXPECTED_PLACEMENT_TYPES)} "
        "各至少一次（plant 早布、resolve 收束）。\n"
        "- 每条 motif 应当指向具体可视化符号 / 物件 / 场景，不要用空泛的主题标签。\n"
    )
