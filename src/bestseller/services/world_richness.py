"""World-spec richness gate — validate world_rules / locations / factions
breadth against the planned novel length *before* the cast spec and volume
plan are generated.

Root cause this module addresses
--------------------------------

Across the 6-book audit we found two extreme failure modes in the
world_spec generation:

  * **Starved world (道种破虚)**: 18 world_rules + 5 locations across 316
    chapters / 24 volumes. Every chapter drew from a tiny exploitation
    pool, so the same rules were re-used chapter after chapter and the
    story collapsed into repeating pressure motifs.
  * **Bloated world (EN projects)**: 574-716 rules with no downstream
    grounding, producing shallow worldbuilding where every rule is
    referenced at most once. The LLM had too much surface area to
    maintain continuity.

Both failure modes share a root cause: the world_spec generator does not
scale its output to the novel's **chapter count** — it just generates
whatever the LLM produces with no floor/ceiling, no distinctiveness
check, and no observability on the trade-off.

This module is the **world-level peer** of ``foundation_richness`` (cast
spec) and runs at the same point in the planner flow: right after
world_spec generation, right before the cast-spec prompt.

Scaling formulas (linear with chapter count; see :func:`compute_world_bounds`):

    world_rules : floor = max(8, ceil(chapters/25)),
                  ceiling = max(60, chapters/2)
    locations   : floor = max(5, ceil(chapters/40)),
                  ceiling = max(25, chapters/4)
    factions    : floor = max(3, ceil(chapters/60)),
                  ceiling = max(15, chapters/8)

Below floor → "starved_world" critical finding (volume plans have too
little to draw from). Above ceiling → "bloated_world" warning (rules
exist but no chapter will ground them).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Chapter divisors. Tuned against the 6-book audit:
#   * 道种破虚 (316 ch, 24 vol): observed 18 rules → floor should be ~12
#     to flag the starvation; observed 5 locations → floor should be ~8
#     to flag the starvation.
#   * The Witness Protocol (544 ch, 16 vol): observed 574 rules → ceiling
#     should be ~272 to flag the bloat.
RULES_CHAPTER_FLOOR_DIVISOR: int = 25
RULES_CHAPTER_CEILING_DIVISOR: int = 2

LOCATIONS_CHAPTER_FLOOR_DIVISOR: int = 40
LOCATIONS_CHAPTER_CEILING_DIVISOR: int = 4

FACTIONS_CHAPTER_FLOOR_DIVISOR: int = 60
FACTIONS_CHAPTER_CEILING_DIVISOR: int = 8

# Absolute minimums — even a 30-chapter novella needs this much foundation.
MIN_WORLD_RULES: int = 8
MIN_LOCATIONS: int = 5
MIN_FACTIONS: int = 3

# Absolute ceilings — even a mega-series doesn't need more than this in
# the spec artifact (further rules can be added dynamically per-volume).
MAX_REASONABLE_WORLD_RULES: int = 60
MAX_REASONABLE_LOCATIONS: int = 25
MAX_REASONABLE_FACTIONS: int = 15


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WorldRichnessFinding:
    """One audit finding against the world spec."""

    code: str              # short identifier, stable across runs
    severity: str          # "critical" | "warning"
    message: str           # human-readable message (zh or en)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorldRichnessBounds:
    """Floor/ceiling bounds for one element, derived from chapter count."""

    floor: int
    ceiling: int


@dataclass(frozen=True)
class WorldRichnessReport:
    """Collected findings from scanning a world spec for scale-appropriate breadth."""

    total_chapters: int
    rule_count: int
    rule_bounds: WorldRichnessBounds
    location_count: int
    location_bounds: WorldRichnessBounds
    faction_count: int
    faction_bounds: WorldRichnessBounds
    duplicate_rule_names: tuple[str, ...]
    findings: tuple[WorldRichnessFinding, ...]

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
        """Render the report into a repair prompt block appended to the
        world-spec repair user prompt.

        We tell the LLM the target counts explicitly so it regenerates the
        right amount of content on the first repair attempt, rather than
        making a vague "make it richer" request.
        """

        is_en = _is_english(language)
        if not self.findings:
            return ""

        lines: list[str] = []
        if is_en:
            lines.append("[WORLD RICHNESS REPAIR — hard requirements]")
            lines.append(
                f"- `rules` MUST contain between {self.rule_bounds.floor} and "
                f"{self.rule_bounds.ceiling} distinct world_rules for this "
                f"{self.total_chapters}-chapter plan (you have {self.rule_count})."
            )
            lines.append(
                f"- `locations` MUST contain between {self.location_bounds.floor} "
                f"and {self.location_bounds.ceiling} distinct locations "
                f"(you have {self.location_count})."
            )
            lines.append(
                f"- `factions` MUST contain between {self.faction_bounds.floor} "
                f"and {self.faction_bounds.ceiling} distinct factions "
                f"(you have {self.faction_count})."
            )
            lines.append(
                "- Every rule must carry a non-empty `description` AND "
                "`story_consequence`; do not emit stubs."
            )
            lines.append(
                "- Rule names must be distinct; do not create two rules with "
                "identical names (later ones silently overwrite earlier ones)."
            )
            lines.append("")
            lines.append("Current findings (fix ALL critical):")
        else:
            lines.append("【世界观丰富度修复 — 硬性要求】")
            lines.append(
                f"- 当前规划共 {self.total_chapters} 章，`rules` 必须在 "
                f"{self.rule_bounds.floor} 到 {self.rule_bounds.ceiling} 条之间"
                f"（当前 {self.rule_count} 条）。"
            )
            lines.append(
                f"- `locations` 必须在 {self.location_bounds.floor} 到 "
                f"{self.location_bounds.ceiling} 个之间（当前 "
                f"{self.location_count} 个）。"
            )
            lines.append(
                f"- `factions` 必须在 {self.faction_bounds.floor} 到 "
                f"{self.faction_bounds.ceiling} 个之间（当前 "
                f"{self.faction_count} 个）。"
            )
            lines.append(
                "- 每条 rule 必须同时提供非空的 `description` 与 "
                "`story_consequence`；不得出现空占位。"
            )
            lines.append(
                "- rule 名称必须两两不同；禁止产生重名规则"
                "（重名条目会被静默覆盖，导致素材实际变少）。"
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


def _bounded(
    chapters: int,
    *,
    floor_divisor: int,
    ceiling_divisor: int,
    min_floor: int,
    min_ceiling: int,
) -> WorldRichnessBounds:
    """Compute a (floor, ceiling) pair for one element from chapter count."""

    chapters = max(int(chapters or 0), 1)
    floor = max(min_floor, math.ceil(chapters / max(floor_divisor, 1)))
    ceiling = max(min_ceiling, chapters // max(ceiling_divisor, 1))
    # Floor must not exceed ceiling (guard against weird divisor combos).
    if ceiling < floor:
        ceiling = floor * 2
    return WorldRichnessBounds(floor=floor, ceiling=ceiling)


def compute_world_bounds(total_chapters: int) -> dict[str, WorldRichnessBounds]:
    """Derive (floor, ceiling) for rules, locations, and factions."""

    return {
        "rules": _bounded(
            total_chapters,
            floor_divisor=RULES_CHAPTER_FLOOR_DIVISOR,
            ceiling_divisor=RULES_CHAPTER_CEILING_DIVISOR,
            min_floor=MIN_WORLD_RULES,
            min_ceiling=MAX_REASONABLE_WORLD_RULES,
        ),
        "locations": _bounded(
            total_chapters,
            floor_divisor=LOCATIONS_CHAPTER_FLOOR_DIVISOR,
            ceiling_divisor=LOCATIONS_CHAPTER_CEILING_DIVISOR,
            min_floor=MIN_LOCATIONS,
            min_ceiling=MAX_REASONABLE_LOCATIONS,
        ),
        "factions": _bounded(
            total_chapters,
            floor_divisor=FACTIONS_CHAPTER_FLOOR_DIVISOR,
            ceiling_divisor=FACTIONS_CHAPTER_CEILING_DIVISOR,
            min_floor=MIN_FACTIONS,
            min_ceiling=MAX_REASONABLE_FACTIONS,
        ),
    }


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def scan_world_spec_richness(
    world_payload: dict[str, Any] | Any,
    *,
    total_chapters: int,
    language: str = "zh-CN",
) -> WorldRichnessReport:
    """Audit a world_spec artifact for scale-appropriate breadth.

    Parameters
    ----------
    world_payload
        The world spec artifact content (dict or pydantic model).
    total_chapters
        Target chapter count for the novel. Scales the bounds.
    language
        Locale for generated messages.

    Returns
    -------
    WorldRichnessReport
        Aggregate of issues. Empty findings means the world material is
        healthy enough to pass through to cast spec + volume plan.
    """

    is_en = _is_english(language)
    world = _mapping(world_payload)
    bounds = compute_world_bounds(total_chapters)
    findings: list[WorldRichnessFinding] = []

    rules = _mapping_list(world.get("rules"))
    locations = _mapping_list(world.get("locations"))
    factions = _mapping_list(world.get("factions"))

    rule_count = len(rules)
    location_count = len(locations)
    faction_count = len(factions)

    # ── Rule count floor/ceiling ─────────────────────────────────────
    if rule_count < bounds["rules"].floor:
        findings.append(
            WorldRichnessFinding(
                code="starved_world_rules",
                severity="critical",
                message=(
                    f"world_spec has only {rule_count} rules for a "
                    f"{total_chapters}-chapter plan; need ≥ "
                    f"{bounds['rules'].floor} to avoid chapter-level material "
                    "starvation."
                    if is_en
                    else f"{total_chapters} 章规划只给出了 {rule_count} 条 world_rules，"
                    f"至少需要 {bounds['rules'].floor} 条，否则章节层会素材不足。"
                ),
                payload={"count": rule_count, "floor": bounds["rules"].floor},
            )
        )
    elif rule_count > bounds["rules"].ceiling:
        findings.append(
            WorldRichnessFinding(
                code="bloated_world_rules",
                severity="warning",
                message=(
                    f"world_spec has {rule_count} rules for a {total_chapters}-"
                    f"chapter plan; > ceiling {bounds['rules'].ceiling}. "
                    "Most rules will never ground; trim to essentials."
                    if is_en
                    else f"{total_chapters} 章规划却给出了 {rule_count} 条 world_rules，"
                    f"超过上限 {bounds['rules'].ceiling}，多数规则将无从落地。"
                ),
                payload={"count": rule_count, "ceiling": bounds["rules"].ceiling},
            )
        )

    # ── Location floor/ceiling ──────────────────────────────────────
    if location_count < bounds["locations"].floor:
        findings.append(
            WorldRichnessFinding(
                code="starved_locations",
                severity="critical",
                message=(
                    f"world_spec has only {location_count} locations for a "
                    f"{total_chapters}-chapter plan; need ≥ "
                    f"{bounds['locations'].floor}. Scenes will repeat the "
                    "same settings."
                    if is_en
                    else f"{total_chapters} 章规划只给出了 {location_count} 个 locations，"
                    f"至少需要 {bounds['locations'].floor} 个，否则场景会反复重复。"
                ),
                payload={"count": location_count, "floor": bounds["locations"].floor},
            )
        )
    elif location_count > bounds["locations"].ceiling:
        findings.append(
            WorldRichnessFinding(
                code="bloated_locations",
                severity="warning",
                message=(
                    f"world_spec has {location_count} locations for a "
                    f"{total_chapters}-chapter plan; > ceiling "
                    f"{bounds['locations'].ceiling}."
                    if is_en
                    else f"{total_chapters} 章规划给出了 {location_count} 个 locations，"
                    f"超过上限 {bounds['locations'].ceiling}。"
                ),
                payload={"count": location_count, "ceiling": bounds["locations"].ceiling},
            )
        )

    # ── Faction floor/ceiling ───────────────────────────────────────
    if faction_count < bounds["factions"].floor:
        findings.append(
            WorldRichnessFinding(
                code="starved_factions",
                severity="critical",
                message=(
                    f"world_spec has only {faction_count} factions for a "
                    f"{total_chapters}-chapter plan; need ≥ "
                    f"{bounds['factions'].floor}. Political/factional conflict "
                    "will collapse to two-sided."
                    if is_en
                    else f"{total_chapters} 章规划只给出了 {faction_count} 个 factions，"
                    f"至少需要 {bounds['factions'].floor} 个，否则势力冲突将退化为两方对峙。"
                ),
                payload={"count": faction_count, "floor": bounds["factions"].floor},
            )
        )
    elif faction_count > bounds["factions"].ceiling:
        findings.append(
            WorldRichnessFinding(
                code="bloated_factions",
                severity="warning",
                message=(
                    f"world_spec has {faction_count} factions for a "
                    f"{total_chapters}-chapter plan; > ceiling "
                    f"{bounds['factions'].ceiling}."
                    if is_en
                    else f"{total_chapters} 章规划给出了 {faction_count} 个 factions，"
                    f"超过上限 {bounds['factions'].ceiling}。"
                ),
                payload={"count": faction_count, "ceiling": bounds["factions"].ceiling},
            )
        )

    # ── Duplicate rule names ─────────────────────────────────────────
    # Duplicate names silently overwrite each other in upsert_world_spec
    # (stable_world_rule_id keys on rule_code, and name collisions collapse
    # to one DB row). Flag them explicitly so the repair prompt can force
    # distinct names.
    from collections import Counter as _NameCounter

    name_counter = _NameCounter(
        str(r.get("name") or "").strip() for r in rules
    )
    duplicate_names = sorted(
        name for name, count in name_counter.items() if name and count > 1
    )
    if duplicate_names:
        findings.append(
            WorldRichnessFinding(
                code="duplicate_rule_names",
                severity="critical",
                message=(
                    f"world_rules reuse the same name: {duplicate_names[:5]}"
                    f"{'...' if len(duplicate_names) > 5 else ''}; duplicate "
                    "names silently overwrite each other in storage."
                    if is_en
                    else f"world_rules 存在重名规则：{duplicate_names[:5]}"
                    f"{'……' if len(duplicate_names) > 5 else ''}，"
                    "重名条目会被静默覆盖，实际素材会比外观更少。"
                ),
                payload={"names": duplicate_names},
            )
        )

    # ── Empty descriptions ───────────────────────────────────────────
    blank_rule_names: list[str] = []
    for r in rules:
        desc = str(r.get("description") or "").strip()
        name = str(r.get("name") or "").strip()
        if name and not desc:
            blank_rule_names.append(name)
    if blank_rule_names:
        findings.append(
            WorldRichnessFinding(
                code="blank_rule_descriptions",
                severity="warning",
                message=(
                    f"{len(blank_rule_names)} world_rules have empty "
                    f"descriptions: {blank_rule_names[:5]}"
                    f"{'...' if len(blank_rule_names) > 5 else ''}"
                    if is_en
                    else f"{len(blank_rule_names)} 条 world_rules 缺少 description："
                    f"{blank_rule_names[:5]}"
                    f"{'……' if len(blank_rule_names) > 5 else ''}"
                ),
                payload={"names": blank_rule_names},
            )
        )

    return WorldRichnessReport(
        total_chapters=max(int(total_chapters or 0), 1),
        rule_count=rule_count,
        rule_bounds=bounds["rules"],
        location_count=location_count,
        location_bounds=bounds["locations"],
        faction_count=faction_count,
        faction_bounds=bounds["factions"],
        duplicate_rule_names=tuple(duplicate_names),
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# Prompt-block renderer for the *upstream* world-spec prompt
# ---------------------------------------------------------------------------

def render_world_constraints_block(
    *,
    total_chapters: int,
    language: str = "zh-CN",
) -> str:
    """Render the up-front constraints injected into the world-spec prompt.

    This is the inverse of :meth:`WorldRichnessReport.to_prompt_block` — instead
    of telling the LLM what it got wrong after the fact, it tells the LLM
    exactly how many rules / locations / factions to produce on the first
    pass, preventing both the starved-world and bloated-world failures.
    """

    bounds = compute_world_bounds(total_chapters)

    if _is_english(language):
        return (
            "[WORLD RICHNESS HARD CONSTRAINTS]\n"
            f"- This novel has {total_chapters} chapters. Scale the world "
            "accordingly.\n"
            f"- `rules` MUST contain between {bounds['rules'].floor} and "
            f"{bounds['rules'].ceiling} distinct world_rules. Each must have "
            "a non-empty `description` and `story_consequence`.\n"
            f"- `locations` MUST contain between {bounds['locations'].floor} "
            f"and {bounds['locations'].ceiling} distinct locations.\n"
            f"- `factions` MUST contain between {bounds['factions'].floor} "
            f"and {bounds['factions'].ceiling} distinct factions.\n"
            "- Rule names MUST be pairwise distinct — duplicate names "
            "silently overwrite in storage.\n"
            "- Do NOT pad with filler rules. Every rule should be exploitable "
            "in the story (inform climaxes, reveals, power progression, or "
            "faction conflict).\n"
        )

    return (
        "【世界观丰富度硬性要求】\n"
        f"- 本书共 {total_chapters} 章，世界观设定要与之匹配。\n"
        f"- `rules` 必须在 {bounds['rules'].floor} 到 "
        f"{bounds['rules'].ceiling} 条之间，每条都要有非空的 "
        "`description` 和 `story_consequence`。\n"
        f"- `locations` 必须在 {bounds['locations'].floor} 到 "
        f"{bounds['locations'].ceiling} 个之间。\n"
        f"- `factions` 必须在 {bounds['factions'].floor} 到 "
        f"{bounds['factions'].ceiling} 个之间。\n"
        "- rule 名称必须两两不同；重名条目会被静默覆盖。\n"
        "- 不要用填充性规则凑数。每条 rule 都应能在故事中被利用"
        "（推动高潮、反转、力量进阶、或势力冲突）。\n"
    )
