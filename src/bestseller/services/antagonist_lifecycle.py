"""Antagonist lifecycle gate — validate that the antagonist roster models
real enemy evolution rather than a rotating-template.

Root cause this module addresses
--------------------------------

The audit of the 6 production novels revealed two stacked failure
modes:

  1. **Antagonist collapse**: 道种破虚 (25 volumes) had every
     volume's antagonist_plan labelled "元婴老者". Even after we
     added per-volume antagonists, the next failure mode surfaced:
  2. **Identical-archetype rotation**: giving each volume a
     different enemy still produced repetition because every enemy
     was a one-volume "stage boss" that gets killed at volume end.
     Readers get no feeling that anything **carries forward** —
     each volume is an isolated combat arena.

The correct pattern (from the classic xianxia / wuxia / web-novel
tradition) is:

  * Antagonists belong to **one of the narrative lines** (overt /
    undercurrent / hidden) defined in ``narrative_lines.py``.
  * Each antagonist has a **lifecycle**: who they are, when they
    matter, and what happens to them. The palette of resolutions
    includes kill, turn-ally, fade-to-irrelevance, vanish-and-return,
    outlive the MC's interest, and ongoing.
  * **Transitions are part of the story**: a V1 antagonist reappearing
    in V7 as a reluctant ally is one of the most repeatable sources
    of reader delight, and it's impossible to produce unless the
    lifecycle schema exists.

This module defines the schema, the scaling / diversity contract, and
the scan. It integrates with ``narrative_lines.py``: every antagonist
references a ``line_id`` so the reader (and downstream generation)
knows whether the antagonist is a surface boss (overt), a shadow hand
(undercurrent), or the final-reveal enemy (hidden).
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Canonical resolution types. The palette intentionally goes beyond
# "defeated" because a book where every antagonist ends in death reads
# as flat as a book where every antagonist survives untouched.
RESOLUTION_DEFEATED_AND_KILLED: str = "defeated_and_killed"
RESOLUTION_TRANSFORMED_TO_ALLY: str = "transformed_to_ally"
RESOLUTION_TRANSFORMED_TO_NEUTRAL: str = "transformed_to_neutral"
RESOLUTION_DEFEATED_AND_REDEEMED: str = "defeated_and_redeemed"
RESOLUTION_DISAPPEARED_UNRESOLVED: str = "disappeared_unresolved"
RESOLUTION_OUTLIVED: str = "outlived"
RESOLUTION_ONGOING: str = "ongoing"

CANONICAL_RESOLUTIONS: tuple[str, ...] = (
    RESOLUTION_DEFEATED_AND_KILLED,
    RESOLUTION_TRANSFORMED_TO_ALLY,
    RESOLUTION_TRANSFORMED_TO_NEUTRAL,
    RESOLUTION_DEFEATED_AND_REDEEMED,
    RESOLUTION_DISAPPEARED_UNRESOLVED,
    RESOLUTION_OUTLIVED,
    RESOLUTION_ONGOING,
)

# Canonical line roles (must match narrative_lines.LINE_*).
LINE_ROLE_OVERT: str = "overt"
LINE_ROLE_UNDERCURRENT: str = "undercurrent"
LINE_ROLE_HIDDEN: str = "hidden"
CANONICAL_LINE_ROLES: tuple[str, ...] = (
    LINE_ROLE_OVERT,
    LINE_ROLE_UNDERCURRENT,
    LINE_ROLE_HIDDEN,
)

# If more than this fraction of antagonists share the same resolution_type,
# the roster is monotonous (the "kill them all" template).
MAX_SAME_RESOLUTION_RATIO: float = 0.7

# At least this many antagonists must have a non-killed resolution so the
# roster has visible transformation.
MIN_NON_KILLED_ANTAGONIST_RATIO: float = 0.2

# Per-volume: each antagonist active in a volume must name at least one
# ``stages_of_relevance`` entry covering that volume.
# Across the book: a volume with NO active overt antagonist is a
# structural hole (except possibly the very last volume if the final
# boss has resolved).
MIN_OVERT_ANTAGONISTS_PER_VOLUME: int = 1

# Minimum antagonist roster count scales by volume_count (otherwise a
# 24-volume book might ship 3 antagonists).
ANTAGONISTS_PER_VOLUMES_FLOOR_DIVISOR: int = 3  # 1 antag per ~3 volumes
MIN_TOTAL_ANTAGONISTS: int = 4


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AntagonistLifecycleFinding:
    """One audit finding against the antagonist roster."""

    code: str
    severity: str  # "critical" | "warning"
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AntagonistSummary:
    """Normalised per-antagonist observability."""

    name: str
    line_role: str           # "overt" | "undercurrent" | "hidden" | ""
    volume_span: tuple[int, int] | None
    resolution_type: str     # canonical constant or "" if missing


@dataclass(frozen=True)
class AntagonistLifecycleReport:
    """Aggregate scan of the antagonist roster."""

    total_chapters: int
    volume_count: int
    antagonist_count: int
    antagonist_summaries: tuple[AntagonistSummary, ...]
    resolution_distribution: dict[str, int]
    volumes_without_overt_antagonist: tuple[int, ...]
    findings: tuple[AntagonistLifecycleFinding, ...]

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
        if not self.findings:
            return ""

        is_en = _is_english(language)
        lines: list[str] = []
        if is_en:
            lines.append("[ANTAGONIST LIFECYCLE REPAIR — hard requirements]")
            lines.append(
                "- The antagonist roster MUST model enemy evolution, not a "
                "rotating template. Every antagonist carries:"
            )
            lines.append(
                "    name, line_role (overt|undercurrent|hidden), "
                "stages_of_relevance [(start_vol, end_vol)], resolution_type "
                f"({'|'.join(CANONICAL_RESOLUTIONS)}), transition_volume, "
                "transition_mechanism."
            )
            lines.append(
                "- The resolution_type distribution MUST NOT collapse to a "
                "single value; at least 20% of antagonists must have a "
                "non-killed resolution (transformed / disappeared / outlived)."
            )
            lines.append(
                "- Every volume MUST have at least one active overt antagonist "
                "(except possibly the very last volume after the final boss)."
            )
            lines.append("")
            lines.append("Current findings (fix ALL critical):")
        else:
            lines.append("【敌人生命周期修复 — 硬性要求】")
            lines.append(
                "- 敌人名单必须刻画出『演化』而不是『轮换模板』。每个敌人都要有："
            )
            lines.append(
                "    name（名字）、line_role（overt｜undercurrent｜hidden，所属线）、"
                "stages_of_relevance（活跃卷区间列表）、resolution_type（"
                f"{'｜'.join(CANONICAL_RESOLUTIONS)}）、"
                "transition_volume（转折卷）、transition_mechanism（转折方式）。"
            )
            lines.append(
                "- resolution_type 分布不能坍缩为单一值——至少 20% 的敌人"
                "要有非被杀结局（转化为盟友/中立/消失/无关）。"
            )
            lines.append(
                "- 每一卷至少要有 1 名活跃的明线敌人"
                "（最后一卷在终局 boss 解决后可例外）。"
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


def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _parse_stages(stages: Any) -> list[tuple[int, int]]:
    """Accept flexible stages_of_relevance shapes.

    Supported:
      * [[1, 3], [5, 7]]
      * [{"start": 1, "end": 3}, ...]
      * [{"start_volume": 1, "end_volume": 3}, ...]
      * [{"volumes": [1, 2, 3]}, ...]
      * [1, 2, 3, 4] (flat list of volumes)
    """

    if not stages:
        return []
    out: list[tuple[int, int]] = []
    if isinstance(stages, list) and all(
        isinstance(s, (int, float, str)) and _as_str(s).isdigit() for s in stages
    ):
        vols = sorted(int(_as_str(s)) for s in stages)
        if vols:
            # Group consecutive volumes into spans.
            start = prev = vols[0]
            for v in vols[1:]:
                if v == prev + 1:
                    prev = v
                else:
                    out.append((start, prev))
                    start = prev = v
            out.append((start, prev))
        return out

    for entry in stages:
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            try:
                a, b = int(entry[0]), int(entry[1])
                out.append((min(a, b), max(a, b)))
            except (TypeError, ValueError):
                continue
        elif isinstance(entry, dict):
            start = entry.get("start") or entry.get("start_volume")
            end = entry.get("end") or entry.get("end_volume") or start
            vols_inner = entry.get("volumes")
            if isinstance(vols_inner, list) and vols_inner:
                try:
                    ints = sorted(int(v) for v in vols_inner if _as_str(v).isdigit())
                    if ints:
                        out.append((min(ints), max(ints)))
                except (TypeError, ValueError):
                    pass
            elif start is not None:
                try:
                    a = int(start)
                    b = int(end) if end is not None else a
                    out.append((min(a, b), max(a, b)))
                except (TypeError, ValueError):
                    continue
    return out


def _volume_span_from_stages(stages: list[tuple[int, int]]) -> tuple[int, int] | None:
    if not stages:
        return None
    return (min(s[0] for s in stages), max(s[1] for s in stages))


def _active_volumes(stages: list[tuple[int, int]]) -> set[int]:
    active: set[int] = set()
    for start, end in stages:
        for v in range(start, end + 1):
            active.add(v)
    return active


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def scan_antagonist_lifecycle(
    antagonists: Any,
    *,
    total_chapters: int,
    volume_count: int,
    language: str = "zh-CN",
) -> AntagonistLifecycleReport:
    """Audit the antagonist roster for lifecycle diversity + coverage.

    Parameters
    ----------
    antagonists
        List of antagonist dicts / pydantic models, each shaped like::

            {
              "name": "...",
              "line_role": "overt|undercurrent|hidden",
              "stages_of_relevance": [[1,3], [7,7]],
              "resolution_type": "transformed_to_ally",
              "transition_volume": 7,
              "transition_mechanism": "revealed identity",
              "archetype": "shadow_hand"
            }

        May also arrive as ``{"antagonists": [...]}`` envelope.
    """

    is_en = _is_english(language)

    # Normalise
    roster: list[dict[str, Any]] = []
    if isinstance(antagonists, list):
        roster = _mapping_list(antagonists)
    else:
        envelope = _mapping(antagonists)
        if isinstance(envelope.get("antagonists"), list):
            roster = _mapping_list(envelope["antagonists"])

    volume_count = max(int(volume_count or 0), 1)
    summaries: list[AntagonistSummary] = []
    resolution_counter: Counter[str] = Counter()
    active_by_volume_overt: dict[int, int] = {v: 0 for v in range(1, volume_count + 1)}
    findings: list[AntagonistLifecycleFinding] = []

    # Per-antagonist normalisation + validation
    for antag in roster:
        name = _as_str(antag.get("name"))
        line_role = _as_str(antag.get("line_role")).lower()
        resolution = _as_str(antag.get("resolution_type")).lower()
        stages = _parse_stages(antag.get("stages_of_relevance"))
        span = _volume_span_from_stages(stages)
        resolution_counter[resolution or "missing"] += 1
        summaries.append(
            AntagonistSummary(
                name=name,
                line_role=line_role,
                volume_span=span,
                resolution_type=resolution,
            )
        )
        if line_role == LINE_ROLE_OVERT:
            for v in _active_volumes(stages):
                if 1 <= v <= volume_count:
                    active_by_volume_overt[v] = active_by_volume_overt.get(v, 0) + 1

    antagonist_count = len(roster)

    # ── Total count floor ────────────────────────────────────────────
    min_floor = max(
        MIN_TOTAL_ANTAGONISTS,
        volume_count // ANTAGONISTS_PER_VOLUMES_FLOOR_DIVISOR,
    )
    if antagonist_count < min_floor:
        findings.append(
            AntagonistLifecycleFinding(
                code="starved_antagonist_roster",
                severity="critical",
                message=(
                    f"Only {antagonist_count} antagonists for a "
                    f"{volume_count}-volume book; need ≥ {min_floor}. "
                    "A sparse roster forces the same enemy to carry too "
                    "much of the story weight."
                    if is_en
                    else f"{volume_count} 卷的书只给出了 {antagonist_count} "
                    f"个敌人，至少需要 {min_floor} 个，"
                    "否则同一敌人承担过多戏份会造成重复。"
                ),
                payload={"count": antagonist_count, "floor": min_floor},
            )
        )

    # ── Missing name / line_role / resolution_type per entry ─────────
    missing_fields: list[dict[str, Any]] = []
    for summary in summaries:
        missing = []
        if not summary.name:
            missing.append("name")
        if summary.line_role not in CANONICAL_LINE_ROLES:
            missing.append("line_role")
        if summary.resolution_type not in CANONICAL_RESOLUTIONS:
            missing.append("resolution_type")
        if missing:
            missing_fields.append({
                "name": summary.name or "(unnamed)",
                "missing": missing,
            })
    if missing_fields:
        findings.append(
            AntagonistLifecycleFinding(
                code="antagonist_missing_lifecycle_fields",
                severity="critical",
                message=(
                    f"{len(missing_fields)} antagonists are missing required "
                    "lifecycle fields "
                    "(name / line_role / resolution_type): "
                    f"{[m['name'] for m in missing_fields][:5]}"
                    f"{'...' if len(missing_fields) > 5 else ''}"
                    if is_en
                    else f"{len(missing_fields)} 个敌人缺少必填生命周期字段"
                    "（name / line_role / resolution_type）："
                    f"{[m['name'] for m in missing_fields][:5]}"
                    f"{'……' if len(missing_fields) > 5 else ''}"
                ),
                payload={"entries": missing_fields},
            )
        )

    # ── Resolution distribution diversity ───────────────────────────
    # Only fire when we have enough antagonists to evaluate a
    # distribution (≥ 3). We exclude the "missing" bucket from ratio
    # math so fixing the missing_fields finding also unlocks this check.
    scored = sum(v for k, v in resolution_counter.items() if k != "missing")
    if scored >= 3:
        # Monotonous: one resolution dominates.
        most_common_type, most_common_count = None, 0
        for k, v in resolution_counter.items():
            if k == "missing":
                continue
            if v > most_common_count:
                most_common_type, most_common_count = k, v
        share = most_common_count / scored if scored else 0.0
        if share > MAX_SAME_RESOLUTION_RATIO:
            findings.append(
                AntagonistLifecycleFinding(
                    code="monotonous_resolution_types",
                    severity="warning",
                    message=(
                        f"{int(share * 100)}% of antagonists resolve as "
                        f"'{most_common_type}'. Vary the palette — "
                        "transformed / disappeared / outlived all carry "
                        "distinct emotional beats."
                        if is_en
                        else f"{int(share * 100)}% 的敌人结局都是"
                        f"『{most_common_type}』，应当多样化"
                        "（转化/消失/脱离主角关心等都各自带来不同情感节奏）。"
                    ),
                    payload={
                        "dominant": most_common_type,
                        "share": round(share, 3),
                    },
                )
            )

        # Minimum non-killed share
        non_killed = scored - resolution_counter.get(RESOLUTION_DEFEATED_AND_KILLED, 0)
        if (non_killed / scored) < MIN_NON_KILLED_ANTAGONIST_RATIO:
            findings.append(
                AntagonistLifecycleFinding(
                    code="all_antagonists_killed_template",
                    severity="warning",
                    message=(
                        "Almost every antagonist ends killed. At least 20% "
                        "should have a non-killed resolution "
                        "(transformed_to_ally / transformed_to_neutral / "
                        "disappeared_unresolved / outlived)."
                        if is_en
                        else "几乎所有敌人都被击杀。至少 20% 应有非被杀结局"
                        "（转化为盟友/中立/消失/脱离主角关心）。"
                    ),
                    payload={
                        "non_killed_ratio": round(non_killed / scored, 3),
                        "floor_ratio": MIN_NON_KILLED_ANTAGONIST_RATIO,
                    },
                )
            )

    # ── Per-volume overt coverage ──────────────────────────────────
    missing_overt_volumes = tuple(
        v for v in range(1, volume_count + 1)
        if active_by_volume_overt.get(v, 0) < MIN_OVERT_ANTAGONISTS_PER_VOLUME
        # Exempt the very final volume — the final boss may have
        # resolved by then (the book itself is ending).
        and v != volume_count
    )
    if missing_overt_volumes:
        findings.append(
            AntagonistLifecycleFinding(
                code="volume_without_overt_antagonist",
                severity="critical",
                message=(
                    f"Volumes without any active overt antagonist: "
                    f"{list(missing_overt_volumes)}. Each volume's "
                    "surface conflict needs a named enemy."
                    if is_en
                    else f"没有活跃明线敌人的卷：{list(missing_overt_volumes)}。"
                    "每卷表层冲突都需要有一个具体敌人。"
                ),
                payload={"volumes": list(missing_overt_volumes)},
            )
        )

    # ── Identical-label rotation (道种破虚 canonical) ──────────────
    # If ≥ 70% of overt antagonists share the same name token, we've
    # regressed into the observed failure mode.
    overt_names = [
        s.name for s in summaries
        if s.line_role == LINE_ROLE_OVERT and s.name
    ]
    if len(overt_names) >= 3:
        name_counts = Counter(overt_names)
        top_name, top_count = name_counts.most_common(1)[0]
        share = top_count / len(overt_names)
        if share > MAX_SAME_RESOLUTION_RATIO:
            findings.append(
                AntagonistLifecycleFinding(
                    code="identical_overt_antagonist_labels",
                    severity="critical",
                    message=(
                        f"{int(share * 100)}% of overt antagonists share "
                        f"the name '{top_name}'. Each volume's surface "
                        "enemy must be a distinct character, not a reused "
                        "label."
                        if is_en
                        else f"{int(share * 100)}% 的明线敌人同名为"
                        f"『{top_name}』。每卷表层敌人必须是不同的具体角色，"
                        "不是重复的标签。"
                    ),
                    payload={"shared_name": top_name, "share": round(share, 3)},
                )
            )

    return AntagonistLifecycleReport(
        total_chapters=max(int(total_chapters or 0), 1),
        volume_count=volume_count,
        antagonist_count=antagonist_count,
        antagonist_summaries=tuple(summaries),
        resolution_distribution=dict(resolution_counter),
        volumes_without_overt_antagonist=missing_overt_volumes,
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# Prompt-block renderer for upstream prompts
# ---------------------------------------------------------------------------

def render_antagonist_lifecycle_constraints_block(
    *,
    total_chapters: int,
    volume_count: int,
    language: str = "zh-CN",
) -> str:
    """Render the up-front antagonist-lifecycle constraints."""

    volume_count = max(int(volume_count or 0), 1)
    min_floor = max(
        MIN_TOTAL_ANTAGONISTS,
        volume_count // ANTAGONISTS_PER_VOLUMES_FLOOR_DIVISOR,
    )

    if _is_english(language):
        return (
            "[ANTAGONIST LIFECYCLE HARD CONSTRAINTS]\n"
            f"- Target plan: {total_chapters} chapters / {volume_count} "
            f"volumes; roster MUST contain ≥ {min_floor} antagonists.\n"
            "- Every antagonist record MUST carry: `name`, `line_role` "
            f"(one of {list(CANONICAL_LINE_ROLES)}), "
            "`stages_of_relevance` (list of (start_volume, end_volume) "
            "pairs), `resolution_type` (one of "
            f"{list(CANONICAL_RESOLUTIONS)}), `transition_volume`, "
            "`transition_mechanism` (how the transition happens).\n"
            "- DO NOT stage every antagonist as a single-volume "
            "kill-and-move-on boss. Stagger lifespans so:\n"
            "    * some overt antagonists from early volumes re-emerge "
            "later as allies or neutral parties (transformed_to_ally / "
            "transformed_to_neutral),\n"
            "    * the undercurrent antagonists are active across "
            "multiple volumes,\n"
            "    * the hidden antagonist is seeded early but its full "
            "role is only revealed in the last quarter.\n"
            "- No more than 70% of antagonists may share the same "
            "`resolution_type`; at least 20% must be non-killed.\n"
            "- Every volume must have ≥ 1 active overt antagonist "
            "(except possibly the very last volume).\n"
            "- Overt antagonist `name` values must be pairwise distinct "
            "within the same book (no 'boss_A / boss_A / boss_A' "
            "rotation).\n"
        )

    return (
        "【敌人生命周期硬性要求】\n"
        f"- 全书规划：{total_chapters} 章 / {volume_count} 卷；"
        f"敌人名单至少包含 {min_floor} 个敌人。\n"
        "- 每个敌人记录必须具备：`name`、`line_role`（"
        f"{list(CANONICAL_LINE_ROLES)} 之一，表明所属叙事线）、"
        "`stages_of_relevance`（活跃卷区间列表）、"
        f"`resolution_type`（{list(CANONICAL_RESOLUTIONS)} 之一）、"
        "`transition_volume`（转折卷）、"
        "`transition_mechanism`（转折方式）。\n"
        "- 禁止每个敌人都是『出场即反派，卷末被杀』的一次性 boss。"
        "要错落生命周期：\n"
        "    * 前期明线敌人中，要有后续回归作为盟友/中立方的"
        "（transformed_to_ally / transformed_to_neutral）；\n"
        "    * 暗线敌人必须跨多卷活跃；\n"
        "    * 隐藏线敌人前期只埋下线索，末 1/4 才完全揭示。\n"
        "- `resolution_type` 分布：单一类型不得超过 70%，"
        "非被杀结局（转化/消失/脱离主角关心）至少占 20%。\n"
        "- 每一卷至少要有 1 名活跃的明线敌人"
        "（最后一卷可例外）。\n"
        "- 同书中明线敌人的 `name` 不得重复"
        "（避免『boss_A / boss_A / boss_A』式的轮换）。\n"
    )
