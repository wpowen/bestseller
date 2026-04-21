"""Relationship scaling gate — validate that the supporting-cast roster
carries enough distinct roles / spans to populate a book of the planned
length without recycling the same 3-4 faces.

Root cause this module addresses
--------------------------------

Across the 6 production books audited, every long novel (≥ 300 chapters)
shipped with only 3-5 supporting_cast entries. The symptom readers
noticed: scenes across different volumes kept pulling the same small
cluster of side characters, giving the book a "cast of six" feel
regardless of whether the plot was global empire or single monastery.
This is the social-fabric analogue of the world-richness and
foreshadowing-scaling starvation patterns.

The correct scaling rule (derived from both the 6-book audit and the
xianxia/wuxia tradition's social taxonomy):

  * **Total supporting_cast count** scales by volume_count AND chapter
    count. Floor ~1.5 entries per volume (so a 24-volume book needs
    ≥ 36 supporting-cast entries).
  * **Role diversity** — at least three distinct role categories across
    the roster: mentor/teacher, ally/companion, rival/foil,
    confidant/family, romantic_interest, subordinate, neutral_broker.
  * **Per-volume coverage** — every volume must have ≥ 1 active
    non-antagonist supporting-cast member. Without this rule the
    midgame chapters lose warm social contrast.
  * **Role-spread cap** — no single role category may occupy more than
    40% of the roster.

Best-effort gate: critical findings trigger a focused repair pass;
failures fall back to the original cast spec.
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

# Floor multiplier: supporting_cast total >= ceil(volume_count * this).
# 1.5× a 24-volume book → 36 supporting characters.
SUPPORTING_CAST_PER_VOLUME_FLOOR_RATIO: float = 1.5

# Absolute minimum so a 3-volume novella still has 6 supporting entries.
MIN_TOTAL_SUPPORTING_CAST: int = 6

# Soft ceiling: more than this per volume and the roster becomes too
# noisy for any entry to feel real.
SUPPORTING_CAST_PER_VOLUME_CEILING_RATIO: float = 3.0

# Minimum distinct role categories across the supporting_cast.
MIN_DISTINCT_ROLE_CATEGORIES: int = 3

# No single role category may dominate more than this fraction.
MAX_ROLE_SHARE: float = 0.4

# Per-volume non-antagonist coverage floor.
MIN_NON_ANTAGONIST_PER_VOLUME: int = 1

# Canonical role category tokens — kept loose so the LLM can name
# roles with its own vocabulary but we can still recognise them.
# The mapping groups synonyms to a canonical bucket for diversity
# scoring.
ROLE_BUCKETS: dict[str, tuple[str, ...]] = {
    "mentor": ("mentor", "teacher", "master", "师父", "师尊", "老师", "sensei"),
    "ally": ("ally", "companion", "partner", "friend", "同伴", "战友", "挚友"),
    "rival": ("rival", "competitor", "foil", "对手", "劲敌"),
    "family": ("family", "parent", "sibling", "child", "父亲", "母亲", "兄", "姐", "弟", "妹"),
    "romantic": ("romantic", "love_interest", "lover", "beloved", "恋人", "伴侣"),
    "subordinate": ("subordinate", "retainer", "follower", "随从", "部下"),
    "confidant": ("confidant", "advisor", "counselor", "mentor_peer", "智囊", "顾问"),
    "broker": ("broker", "neutral", "intermediary", "merchant", "掮客", "中立"),
    "antagonist": ("antagonist", "enemy", "foe", "villain", "反派", "敌人"),
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RelationshipScalingFinding:
    """One audit finding against the supporting_cast roster."""

    code: str
    severity: str  # "critical" | "warning"
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RelationshipBounds:
    """Per-roster count targets derived from volume_count."""

    floor: int
    ceiling: int


@dataclass(frozen=True)
class RelationshipScalingReport:
    """Aggregate scan of the supporting_cast roster."""

    total_chapters: int
    volume_count: int
    supporting_cast_count: int
    supporting_bounds: RelationshipBounds
    distinct_role_buckets: int
    role_distribution: dict[str, int]
    volumes_without_non_antagonist: tuple[int, ...]
    findings: tuple[RelationshipScalingFinding, ...]

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
            lines.append("[RELATIONSHIP SCALING REPAIR — hard requirements]")
            lines.append(
                f"- supporting_cast floor: {self.supporting_bounds.floor} "
                f"(one per ~{1 / SUPPORTING_CAST_PER_VOLUME_FLOOR_RATIO:.1f} volumes)."
            )
            lines.append(
                "- Every entry must carry: name, role, active_volumes, "
                "relationship_to_protagonist, evolution_arc."
            )
            lines.append(
                f"- Use at least {MIN_DISTINCT_ROLE_CATEGORIES} distinct role "
                "categories (mentor / ally / rival / family / romantic / "
                "subordinate / confidant / broker)."
            )
            lines.append(
                "- Every volume must have at least one active non-antagonist "
                "supporting-cast member."
            )
            lines.append("")
            lines.append("Current findings (fix ALL critical):")
        else:
            lines.append("【关系网规模修复 — 硬性要求】")
            lines.append(
                f"- supporting_cast 人数下限：{self.supporting_bounds.floor}"
                f"（约每 {1 / SUPPORTING_CAST_PER_VOLUME_FLOOR_RATIO:.1f} 卷 1 人）。"
            )
            lines.append(
                "- 每个 supporting_cast 元素必须包含："
                "name、role、active_volumes、relationship_to_protagonist、evolution_arc。"
            )
            lines.append(
                f"- 至少 {MIN_DISTINCT_ROLE_CATEGORIES} 种不同的 role 类别"
                "（mentor/ally/rival/family/romantic/subordinate/confidant/broker）。"
            )
            lines.append(
                "- 每一卷至少要有 1 名活跃的非敌人类 supporting_cast 成员。"
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


def _bucket_for_role(role: str) -> str:
    """Map a free-form role label to its canonical bucket.

    Unknown labels fall into the "other" bucket so the distribution
    math still works — we just won't count them toward role-diversity.
    """

    low = role.lower()
    for bucket, keys in ROLE_BUCKETS.items():
        for key in keys:
            if key in low:
                return bucket
    return "other"


def _parse_active_volumes(value: Any) -> set[int]:
    """Accept flexible active_volumes shapes.

    Supported:
      * [1, 2, 3]
      * [{"start_volume": 1, "end_volume": 3}]
      * [[1, 3], [5, 7]]
      * [{"volumes": [1, 2]}]
    """

    out: set[int] = set()
    if not value:
        return out
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, (int, float)) or (
                isinstance(entry, str) and entry.strip().isdigit()
            ):
                try:
                    out.add(int(entry))
                except (TypeError, ValueError):
                    continue
            elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                try:
                    a, b = int(entry[0]), int(entry[1])
                    for v in range(min(a, b), max(a, b) + 1):
                        out.add(v)
                except (TypeError, ValueError):
                    continue
            elif isinstance(entry, dict):
                start = entry.get("start") or entry.get("start_volume")
                end = entry.get("end") or entry.get("end_volume") or start
                vols_inner = entry.get("volumes")
                if isinstance(vols_inner, list) and vols_inner:
                    for v in vols_inner:
                        if _as_str(v).isdigit():
                            try:
                                out.add(int(v))
                            except (TypeError, ValueError):
                                continue
                elif start is not None:
                    try:
                        a = int(start)
                        b = int(end) if end is not None else a
                        for v in range(min(a, b), max(a, b) + 1):
                            out.add(v)
                    except (TypeError, ValueError):
                        continue
    return out


def compute_supporting_bounds(volume_count: int) -> RelationshipBounds:
    """Derive (floor, ceiling) for supporting_cast count from volume_count."""

    vc = max(int(volume_count or 0), 1)
    floor = max(
        MIN_TOTAL_SUPPORTING_CAST,
        math.ceil(vc * SUPPORTING_CAST_PER_VOLUME_FLOOR_RATIO),
    )
    ceiling = max(
        floor + 1,
        math.ceil(vc * SUPPORTING_CAST_PER_VOLUME_CEILING_RATIO),
    )
    return RelationshipBounds(floor=floor, ceiling=ceiling)


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def scan_relationship_scaling(
    supporting_cast: Any,
    *,
    total_chapters: int,
    volume_count: int,
    language: str = "zh-CN",
) -> RelationshipScalingReport:
    """Audit the supporting_cast roster for scale-appropriate breadth."""

    is_en = _is_english(language)
    volume_count = max(int(volume_count or 0), 1)

    # Normalise input — accept {"supporting_cast": [...]} envelope or the
    # whole cast_spec payload.
    roster: list[dict[str, Any]] = []
    if isinstance(supporting_cast, list):
        roster = _mapping_list(supporting_cast)
    else:
        envelope = _mapping(supporting_cast)
        if isinstance(envelope.get("supporting_cast"), list):
            roster = _mapping_list(envelope["supporting_cast"])

    bounds = compute_supporting_bounds(volume_count)
    findings: list[RelationshipScalingFinding] = []

    bucket_counter: Counter[str] = Counter()
    active_non_antagonist_by_volume: dict[int, int] = {
        v: 0 for v in range(1, volume_count + 1)
    }
    missing_fields_entries: list[str] = []

    for entry in roster:
        name = _as_str(entry.get("name"))
        role_raw = _as_str(entry.get("role"))
        bucket = _bucket_for_role(role_raw)
        bucket_counter[bucket] += 1
        active_vols = _parse_active_volumes(entry.get("active_volumes"))
        rel_to_protag = _as_str(entry.get("relationship_to_protagonist"))
        evolution = _as_str(entry.get("evolution_arc"))
        missing: list[str] = []
        if not name:
            missing.append("name")
        if not role_raw:
            missing.append("role")
        if not active_vols:
            missing.append("active_volumes")
        if not rel_to_protag:
            missing.append("relationship_to_protagonist")
        if not evolution:
            missing.append("evolution_arc")
        if missing:
            missing_fields_entries.append(
                f"{name or '(unnamed)'}:{','.join(missing)}"
            )
        if bucket != "antagonist":
            for v in active_vols:
                if 1 <= v <= volume_count:
                    active_non_antagonist_by_volume[v] = (
                        active_non_antagonist_by_volume.get(v, 0) + 1
                    )

    supporting_cast_count = len(roster)

    # ── Count floor ─────────────────────────────────────────────────
    if supporting_cast_count < bounds.floor:
        findings.append(
            RelationshipScalingFinding(
                code="starved_supporting_cast",
                severity="critical",
                message=(
                    f"Supporting cast has {supporting_cast_count} entries; "
                    f"need ≥ {bounds.floor} for a {volume_count}-volume "
                    f"/ {total_chapters}-chapter book. A thin roster forces "
                    "scenes to recycle the same 3-5 faces across the whole "
                    "novel."
                    if is_en
                    else f"supporting_cast 只有 {supporting_cast_count} 人，"
                    f"{volume_count} 卷 / {total_chapters} 章的书至少需要 "
                    f"{bounds.floor} 人，否则全书只能在 3-5 张脸之间反复切换。"
                ),
                payload={
                    "count": supporting_cast_count,
                    "floor": bounds.floor,
                    "ceiling": bounds.ceiling,
                },
            )
        )

    # ── Ceiling (soft — warning only) ───────────────────────────────
    if supporting_cast_count > bounds.ceiling:
        findings.append(
            RelationshipScalingFinding(
                code="bloated_supporting_cast",
                severity="warning",
                message=(
                    f"Supporting cast has {supporting_cast_count} entries; "
                    f"ceiling for {volume_count} volumes is {bounds.ceiling}. "
                    "Too many named faces dilutes reader attachment."
                    if is_en
                    else f"supporting_cast 有 {supporting_cast_count} 人，"
                    f"{volume_count} 卷规模建议不超过 {bounds.ceiling} 人，"
                    "过多的配角会稀释读者对每个人物的印象。"
                ),
                payload={
                    "count": supporting_cast_count,
                    "ceiling": bounds.ceiling,
                },
            )
        )

    # ── Missing lifecycle fields (critical if any roster entries) ───
    if missing_fields_entries:
        findings.append(
            RelationshipScalingFinding(
                code="supporting_cast_missing_fields",
                severity="critical",
                message=(
                    f"{len(missing_fields_entries)} supporting_cast entries "
                    "are missing required fields (name / role / "
                    "active_volumes / relationship_to_protagonist / "
                    "evolution_arc): "
                    f"{missing_fields_entries[:5]}"
                    f"{'...' if len(missing_fields_entries) > 5 else ''}"
                    if is_en
                    else f"{len(missing_fields_entries)} 个 supporting_cast 条目"
                    "缺少必填字段（name / role / active_volumes / "
                    "relationship_to_protagonist / evolution_arc）："
                    f"{missing_fields_entries[:5]}"
                    f"{'……' if len(missing_fields_entries) > 5 else ''}"
                ),
                payload={"entries": missing_fields_entries},
            )
        )

    # ── Distinct role-bucket diversity ──────────────────────────────
    non_other_buckets = {b for b in bucket_counter if b != "other"}
    distinct_role_buckets = len(non_other_buckets)
    if supporting_cast_count >= MIN_TOTAL_SUPPORTING_CAST and (
        distinct_role_buckets < MIN_DISTINCT_ROLE_CATEGORIES
    ):
        findings.append(
            RelationshipScalingFinding(
                code="monotonous_role_distribution",
                severity="critical",
                message=(
                    f"Only {distinct_role_buckets} distinct role categories "
                    f"across the roster; need ≥ {MIN_DISTINCT_ROLE_CATEGORIES} "
                    "(mentor / ally / rival / family / romantic / "
                    "subordinate / confidant / broker)."
                    if is_en
                    else f"supporting_cast 只覆盖了 {distinct_role_buckets} 种"
                    f"角色类别，需要 ≥ {MIN_DISTINCT_ROLE_CATEGORIES} 种（"
                    "mentor/ally/rival/family/romantic/subordinate/"
                    "confidant/broker 中选择不同组合）。"
                ),
                payload={
                    "distinct": distinct_role_buckets,
                    "required": MIN_DISTINCT_ROLE_CATEGORIES,
                    "present": sorted(non_other_buckets),
                },
            )
        )

    # ── Role-share cap (warning — no role should dominate) ──────────
    if supporting_cast_count >= 4:
        most_common = bucket_counter.most_common(1)
        if most_common:
            dom_bucket, dom_count = most_common[0]
            share = dom_count / supporting_cast_count
            if share > MAX_ROLE_SHARE:
                findings.append(
                    RelationshipScalingFinding(
                        code="dominant_role_share",
                        severity="warning",
                        message=(
                            f"{int(share * 100)}% of supporting_cast share "
                            f"the '{dom_bucket}' role. Spread roles more "
                            "evenly — one role category should not exceed "
                            f"{int(MAX_ROLE_SHARE * 100)}%."
                            if is_en
                            else f"{int(share * 100)}% 的 supporting_cast 都"
                            f"属于『{dom_bucket}』类，应当更均衡——"
                            f"单一 role 类别不得超过 {int(MAX_ROLE_SHARE * 100)}%。"
                        ),
                        payload={
                            "dominant": dom_bucket,
                            "share": round(share, 3),
                        },
                    )
                )

    # ── Per-volume non-antagonist coverage ─────────────────────────
    missing_volumes = tuple(
        v for v in range(1, volume_count + 1)
        if active_non_antagonist_by_volume.get(v, 0) < MIN_NON_ANTAGONIST_PER_VOLUME
    )
    if missing_volumes and supporting_cast_count > 0:
        findings.append(
            RelationshipScalingFinding(
                code="volume_without_non_antagonist",
                severity="critical",
                message=(
                    f"Volumes without any active non-antagonist supporting "
                    f"cast: {list(missing_volumes)}. Every volume needs at "
                    "least one warm relationship to anchor scenes."
                    if is_en
                    else f"没有活跃非敌人类配角的卷：{list(missing_volumes)}。"
                    "每卷至少需要一个友好关系来承载情感戏份。"
                ),
                payload={"volumes": list(missing_volumes)},
            )
        )

    return RelationshipScalingReport(
        total_chapters=max(int(total_chapters or 0), 1),
        volume_count=volume_count,
        supporting_cast_count=supporting_cast_count,
        supporting_bounds=bounds,
        distinct_role_buckets=distinct_role_buckets,
        role_distribution=dict(bucket_counter),
        volumes_without_non_antagonist=missing_volumes,
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# Upstream constraints block — injected before generation
# ---------------------------------------------------------------------------

def render_relationship_constraints_block(
    *,
    total_chapters: int,
    volume_count: int,
    language: str = "zh-CN",
) -> str:
    """Render the up-front relationship-scaling constraints."""

    volume_count = max(int(volume_count or 0), 1)
    bounds = compute_supporting_bounds(volume_count)

    if _is_english(language):
        return (
            "[RELATIONSHIP SCALING HARD CONSTRAINTS]\n"
            f"- Target plan: {total_chapters} chapters / {volume_count} "
            f"volumes; `supporting_cast` MUST contain between "
            f"{bounds.floor} and {bounds.ceiling} entries.\n"
            "- Every entry MUST carry: `name`, `role`, `active_volumes` "
            "(list of volume numbers), `relationship_to_protagonist`, "
            "`evolution_arc` (how the relationship changes across the "
            "book).\n"
            f"- Spread roles across at least {MIN_DISTINCT_ROLE_CATEGORIES} "
            "distinct categories: mentor / ally / rival / family / "
            "romantic / subordinate / confidant / broker.\n"
            f"- No single role category may exceed "
            f"{int(MAX_ROLE_SHARE * 100)}% of the roster.\n"
            "- Every volume MUST have at least one active non-antagonist "
            "supporting-cast member to anchor warm scenes.\n"
        )

    return (
        "【关系网规模硬性要求】\n"
        f"- 全书规划：{total_chapters} 章 / {volume_count} 卷；"
        f"`supporting_cast` 必须在 {bounds.floor} 到 {bounds.ceiling} 人之间。\n"
        "- 每个条目必须包含：`name`、`role`、`active_volumes`（卷号列表）、"
        "`relationship_to_protagonist`、`evolution_arc`（关系随全书演化的方式）。\n"
        f"- 至少覆盖 {MIN_DISTINCT_ROLE_CATEGORIES} 种不同 role 类别："
        "mentor（导师）、ally（挚友）、rival（对手）、family（家人）、"
        "romantic（恋人）、subordinate（下属）、confidant（智囊）、"
        "broker（中立方）。\n"
        f"- 单一 role 类别不得超过全部 supporting_cast 的 "
        f"{int(MAX_ROLE_SHARE * 100)}%。\n"
        "- 每一卷至少要有 1 名活跃的非敌人类配角，承担情感戏份。\n"
    )
