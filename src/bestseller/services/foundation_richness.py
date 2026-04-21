"""Foundation richness gate — validates the cast spec's antagonist-force pool
and supporting-cast breadth *before* the volume plan is generated.

Root cause this module addresses: even with per-scene, cross-chapter, and
cross-volume fingerprinting in place, the planner can still converge when
the upstream foundational material is too thin. Concretely: if the cast
spec ships only one antagonist (or one antagonist with blank
``active_volumes``), every volume's ``primary_force_name`` falls back to
that single antagonist's name — turning the 24-volume plan into 24 slight
variations on the same pressure. This was the exact failure mode observed
on the xianxia project (道种破虚), where all 25 antagonist_plans resolved
to a single label "元婴老者" and every volume goal reduced to one of three
"survival pressure" templates.

The richness gate runs once, right after the cast spec is produced and
right before ``_volume_plan_prompts`` is invoked. It is:

  * **DB-free and deterministic** — every check operates on the in-memory
    cast payload dict.
  * **Additive, not destructive** — it returns a report and a prompt block;
    the planner decides whether to auto-repair.
  * **Severity-tiered** — critical findings gate the volume plan and
    trigger a single focused LLM repair on the ``antagonist_forces`` /
    ``supporting_cast`` fields; warnings are logged and carried forward.

This module is the foundational-material peer of:

  * ``scene_plan_richness`` — per-scene card richness validation
  * ``plan_fingerprint``   — pairwise chapter fingerprint Jaccard scan
  * ``revealed_ledger``    — aggregate cross-chapter revealed-facts summary
  * ``volume_fingerprint`` — pairwise volume similarity scan

Together they form a top-down gating stack: *foundation* → *volume* →
*chapter* → *scene*.
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

# A force is "generic" if its name reduces to one of these canonical labels.
# When every volume's primary_force falls back to the main antagonist name,
# that antagonist typically carries a generic descriptor, not a specific
# role. We don't hard-block these — we just flag them so the LLM is told
# to differentiate.
GENERIC_FORCE_NAME_PATTERNS: tuple[str, ...] = (
    "反派",
    "敌对势力",
    "未知",
    "主要反派",
    "the antagonist",
    "unknown enemy",
    "main antagonist",
    "primary force",
)

# Volumes-per-force lower bound: at least one distinct force for every
# ``FORCES_PER_VOLUME_RATIO`` volumes. A 24-volume plan therefore needs
# at least ceil(24/4) = 6 distinct antagonist forces.
FORCES_PER_VOLUME_RATIO: int = 4

# Any single force may not dominate more than this fraction of volumes.
# Above this ratio the plan will collapse into a one-antagonist arc.
MAX_FORCE_SHARE: float = 0.40

# The union of all active_volumes across forces should cover at least this
# fraction of the volume plan. Below this, downstream volumes fall through
# to the single-antagonist fallback in ``_build_volume_plan_fallback``.
MIN_VOLUME_COVERAGE: float = 0.80

# Minimum distinct force_type values across the roster.
MIN_FORCE_TYPE_DIVERSITY: int = 2

# Minimum antagonist-role supporting-cast entries so per-volume plans have
# human faces to attach to each force, rather than a faceless "the enemy".
SUPPORTING_ANTAGONIST_PER_VOLUME_RATIO: int = 3


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FoundationRichnessFinding:
    """One audit finding against the cast spec."""

    code: str              # short identifier, stable across runs
    severity: str          # "critical" | "warning"
    message: str           # human-readable message (zh or en)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FoundationRichnessReport:
    """Collected findings from scanning a cast spec for foundational richness."""

    volume_count: int
    force_count: int
    forces_required: int
    distinct_volume_coverage: int
    coverage_ratio: float
    max_single_force_share: float
    distinct_force_types: int
    supporting_antagonist_count: int
    generic_force_names: tuple[str, ...]
    findings: tuple[FoundationRichnessFinding, ...]

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
        """Render the report into a repair prompt block.

        The block is designed to be appended to the cast-spec repair user
        prompt when the planner decides to auto-repair critical findings.
        It tells the LLM *exactly* which constraints to satisfy rather
        than asking for a vague "make it richer" rewrite.
        """

        is_en = _is_english(language)
        if not self.findings:
            return ""

        lines: list[str] = []
        if is_en:
            lines.append("【FOUNDATION RICHNESS REPAIR — hard requirements】")
            lines.append(
                f"- `antagonist_forces` MUST contain at least {self.forces_required} "
                f"distinct forces (you have {self.force_count})."
            )
            lines.append(
                "- Each force must declare `active_volumes` covering specific "
                "volume numbers; the union MUST cover at least "
                f"{int(MIN_VOLUME_COVERAGE * 100)}% of volumes 1..{self.volume_count}."
            )
            lines.append(
                f"- No single force may span more than "
                f"{int(MAX_FORCE_SHARE * 100)}% of the volume count (≤ "
                f"{max(1, int(self.volume_count * MAX_FORCE_SHARE))} volumes)."
            )
            lines.append(
                "- Use at least 2 distinct `force_type` values across the "
                "roster (mix of character / faction / systemic / environmental / "
                "internal); do not make every force a 'character'."
            )
            lines.append(
                "- Replace any generic force name (e.g. 'the antagonist', "
                "'main enemy', 'primary force') with a specific faction or "
                "character descriptor rooted in this world."
            )
            lines.append(
                f"- `supporting_cast` must include at least "
                f"{self._required_supporting_antagonists()} distinct "
                "antagonist-role entries, one per force where feasible."
            )
            lines.append("")
            lines.append("Current findings (fix ALL critical):")
        else:
            lines.append("【基础素材丰富度修复 — 硬性要求】")
            lines.append(
                f"- `antagonist_forces` 必须包含至少 {self.forces_required} "
                f"个不同的冲突力量（当前只有 {self.force_count} 个）。"
            )
            lines.append(
                f"- 每个 force 必须声明 `active_volumes` 覆盖具体卷号；"
                f"所有 force 的 active_volumes 并集必须覆盖 1..{self.volume_count} "
                f"中至少 {int(MIN_VOLUME_COVERAGE * 100)}% 的卷。"
            )
            lines.append(
                f"- 任何单一 force 的 active_volumes 不得超过 "
                f"{int(MAX_FORCE_SHARE * 100)}%（≤ "
                f"{max(1, int(self.volume_count * MAX_FORCE_SHARE))} 卷）。"
            )
            lines.append(
                "- 整个 roster 至少要有 2 种不同的 `force_type`"
                "（character / faction / systemic / environmental / internal "
                "混合），不能让每一个 force 都是 character。"
            )
            lines.append(
                "- 任何通用名（如「反派」「敌对势力」「主要反派」）都必须"
                "替换为扎根于本世界观的具体势力或角色名。"
            )
            lines.append(
                f"- `supporting_cast` 必须至少包含 "
                f"{self._required_supporting_antagonists()} 个 antagonist 类角色，"
                "力求每个 force 都能对应到一个有血有肉的人物。"
            )
            lines.append("")
            lines.append("当前审查结果（所有 critical 项必须修复）：")

        for finding in self.findings:
            bullet = "×" if finding.severity == "critical" else "!"
            lines.append(f"  {bullet} [{finding.severity}] {finding.code}: {finding.message}")

        return "\n".join(lines).strip()

    def _required_supporting_antagonists(self) -> int:
        if self.volume_count <= 0:
            return 1
        return max(1, math.ceil(self.volume_count / SUPPORTING_ANTAGONIST_PER_VOLUME_RATIO))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_english(language: str | None) -> bool:
    if not language:
        return False
    return language.lower().startswith("en")


def _mapping(value: Any) -> dict[str, Any]:
    """Normalize a dict / pydantic model / arbitrary mapping into a plain dict."""

    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
        except Exception:  # pragma: no cover — defensive
            return {}
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "__dict__"):
        return {k: v for k, v in value.__dict__.items() if not k.startswith("_")}
    return {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    """Normalize an iterable of mappings into a list of plain dicts."""

    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return []
    return [_mapping(item) for item in value if item is not None]


def _is_generic_force_name(name: str) -> bool:
    if not name:
        return True
    lowered = name.strip().lower()
    for pattern in GENERIC_FORCE_NAME_PATTERNS:
        if pattern.lower() in lowered:
            return True
    return False


def _normalize_active_volumes(raw: Any, *, volume_count: int) -> tuple[int, ...]:
    """Parse an active_volumes field, returning sorted unique volume ints
    within [1, volume_count]."""

    if not raw:
        return ()
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return ()
    result: set[int] = set()
    for item in raw:
        try:
            vol = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= vol <= volume_count:
            result.add(vol)
    return tuple(sorted(result))


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def scan_cast_foundation_richness(
    cast_payload: dict[str, Any] | Any,
    *,
    volume_count: int,
    language: str = "zh-CN",
) -> FoundationRichnessReport:
    """Audit the cast spec for foundational richness.

    Parameters
    ----------
    cast_payload
        The cast spec artifact content (dict or pydantic model).
    volume_count
        Total volumes in the planned novel.
    language
        Used when we need to generate locale-aware messages.

    Returns
    -------
    FoundationRichnessReport
        Aggregate of issues; empty findings tuple means the material is
        healthy enough to pass through to volume-plan generation.
    """

    is_en = _is_english(language)
    cast = _mapping(cast_payload)
    volume_count = max(int(volume_count), 1)
    forces_required = max(1, math.ceil(volume_count / FORCES_PER_VOLUME_RATIO))
    findings: list[FoundationRichnessFinding] = []

    forces = _mapping_list(cast.get("antagonist_forces"))
    supporting_cast = _mapping_list(cast.get("supporting_cast"))

    # ── Force count ────────────────────────────────────────────────────
    if len(forces) < forces_required:
        findings.append(
            FoundationRichnessFinding(
                code="insufficient_force_count",
                severity="critical",
                message=(
                    f"Need ≥ {forces_required} distinct antagonist_forces "
                    f"for a {volume_count}-volume plan; found {len(forces)}."
                    if is_en
                    else f"{volume_count} 卷的规划至少需要 {forces_required} 个不同的 "
                    f"antagonist_forces，当前只有 {len(forces)} 个。"
                ),
                payload={"required": forces_required, "actual": len(forces)},
            )
        )

    # ── active_volumes coverage + per-force share ─────────────────────
    coverage: set[int] = set()
    max_share = 0.0
    per_force_volumes: list[tuple[str, tuple[int, ...]]] = []
    generic_names: list[str] = []
    for force in forces:
        name = str(force.get("name") or "").strip()
        if _is_generic_force_name(name):
            generic_names.append(name or "<blank>")
        active = _normalize_active_volumes(
            force.get("active_volumes"),
            volume_count=volume_count,
        )
        per_force_volumes.append((name or "<unnamed>", active))
        coverage.update(active)
        share = len(active) / volume_count if volume_count else 0.0
        if share > max_share:
            max_share = share

    coverage_count = len(coverage)
    coverage_ratio = coverage_count / volume_count if volume_count else 0.0

    if forces and coverage_ratio < MIN_VOLUME_COVERAGE:
        findings.append(
            FoundationRichnessFinding(
                code="insufficient_volume_coverage",
                severity="critical",
                message=(
                    f"antagonist_forces active_volumes cover only "
                    f"{coverage_count}/{volume_count} volumes "
                    f"({int(coverage_ratio * 100)}%); need ≥ "
                    f"{int(MIN_VOLUME_COVERAGE * 100)}%."
                    if is_en
                    else f"antagonist_forces 的 active_volumes 并集只覆盖了 "
                    f"{coverage_count}/{volume_count} 卷"
                    f"（{int(coverage_ratio * 100)}%），需要 ≥ "
                    f"{int(MIN_VOLUME_COVERAGE * 100)}%。"
                ),
                payload={
                    "covered": sorted(coverage),
                    "uncovered": sorted(set(range(1, volume_count + 1)) - coverage),
                },
            )
        )

    if forces and max_share > MAX_FORCE_SHARE:
        dominant = max(per_force_volumes, key=lambda t: len(t[1]))
        findings.append(
            FoundationRichnessFinding(
                code="single_force_dominance",
                severity="critical",
                message=(
                    f"Force '{dominant[0]}' spans "
                    f"{len(dominant[1])}/{volume_count} volumes "
                    f"({int(max_share * 100)}%); max allowed is "
                    f"{int(MAX_FORCE_SHARE * 100)}%."
                    if is_en
                    else f"单一 force「{dominant[0]}」覆盖 "
                    f"{len(dominant[1])}/{volume_count} 卷"
                    f"（{int(max_share * 100)}%），超过上限 "
                    f"{int(MAX_FORCE_SHARE * 100)}%。"
                ),
                payload={"force": dominant[0], "active_volumes": list(dominant[1])},
            )
        )

    # ── force_type diversity ──────────────────────────────────────────
    force_types = {
        str(f.get("force_type") or "").strip().lower()
        for f in forces
        if str(f.get("force_type") or "").strip()
    }
    if forces and len(force_types) < MIN_FORCE_TYPE_DIVERSITY:
        findings.append(
            FoundationRichnessFinding(
                code="insufficient_force_type_diversity",
                severity="warning",
                message=(
                    f"Only {len(force_types)} distinct force_type values "
                    f"({sorted(force_types) or ['<none>']}); need ≥ "
                    f"{MIN_FORCE_TYPE_DIVERSITY}."
                    if is_en
                    else f"force_type 只有 {len(force_types)} 种"
                    f"（{sorted(force_types) or ['<无>']}），需要 ≥ "
                    f"{MIN_FORCE_TYPE_DIVERSITY}。"
                ),
                payload={"types": sorted(force_types)},
            )
        )

    # ── Duplicate force names ─────────────────────────────────────────
    # A force roster of six entries all named "生存压力" trivially fails the
    # downstream per-volume narrative: the UI, antagonist_plans, and LLM
    # context all show the same label regardless of which volume is active.
    # Flag any name that repeats and require distinct labels.
    from collections import Counter as _NameCounter

    name_counter = _NameCounter(
        (str(f.get("name") or "").strip() for f in forces)
    )
    duplicate_names = sorted(
        name for name, count in name_counter.items() if name and count > 1
    )
    if duplicate_names:
        findings.append(
            FoundationRichnessFinding(
                code="duplicate_force_names",
                severity="critical",
                message=(
                    f"antagonist_forces reuse the same name: {duplicate_names}; "
                    "every force must be named distinctly so per-volume antagonist "
                    "labels do not collapse to a single string."
                    if is_en
                    else f"antagonist_forces 存在同名 force：{duplicate_names}，"
                    "每个 force 必须使用不同的名字，避免每卷标签塌缩为同一个字符串。"
                ),
                payload={"names": duplicate_names},
            )
        )

    # ── Generic force names ───────────────────────────────────────────
    if generic_names:
        findings.append(
            FoundationRichnessFinding(
                code="generic_force_names",
                severity="warning",
                message=(
                    f"Force names look generic: {generic_names}; "
                    "replace with world-specific labels."
                    if is_en
                    else f"以下 force 名称过于通用：{generic_names}，"
                    "需要替换为扎根本世界观的具体名称。"
                ),
                payload={"names": generic_names},
            )
        )

    # ── Supporting-cast antagonist breadth ────────────────────────────
    supporting_antagonists = [
        sc for sc in supporting_cast
        if "antag" in str(sc.get("role") or "").lower()
    ]
    required_supporting = max(
        1, math.ceil(volume_count / SUPPORTING_ANTAGONIST_PER_VOLUME_RATIO)
    )
    if len(supporting_antagonists) < required_supporting:
        findings.append(
            FoundationRichnessFinding(
                code="insufficient_supporting_antagonists",
                severity="warning",
                message=(
                    f"Only {len(supporting_antagonists)} antagonist-role "
                    f"supporting_cast entries; need ≥ {required_supporting} "
                    f"for a {volume_count}-volume plan."
                    if is_en
                    else f"supporting_cast 中 antagonist 类角色只有 "
                    f"{len(supporting_antagonists)} 个，{volume_count} 卷规划"
                    f"建议 ≥ {required_supporting} 个。"
                ),
                payload={
                    "required": required_supporting,
                    "actual": len(supporting_antagonists),
                },
            )
        )

    return FoundationRichnessReport(
        volume_count=volume_count,
        force_count=len(forces),
        forces_required=forces_required,
        distinct_volume_coverage=coverage_count,
        coverage_ratio=coverage_ratio,
        max_single_force_share=max_share,
        distinct_force_types=len(force_types),
        supporting_antagonist_count=len(supporting_antagonists),
        generic_force_names=tuple(generic_names),
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# Prompt-block renderer for the *upstream* cast-spec prompt
# ---------------------------------------------------------------------------

def render_foundation_constraints_block(
    *,
    volume_count: int,
    language: str = "zh-CN",
) -> str:
    """Render the up-front constraints injected into the cast-spec prompt.

    This is the inverse of ``to_prompt_block`` on the report: instead of
    telling the LLM what it got wrong, it tells the LLM what it MUST
    produce on the first pass — preventing the thin-roster problem
    before it happens.
    """

    forces_required = max(1, math.ceil(volume_count / FORCES_PER_VOLUME_RATIO))
    required_supporting = max(
        1, math.ceil(volume_count / SUPPORTING_ANTAGONIST_PER_VOLUME_RATIO)
    )
    max_single = max(1, int(volume_count * MAX_FORCE_SHARE))

    if _is_english(language):
        return (
            "【FOUNDATION RICHNESS HARD CONSTRAINTS】\n"
            f"- `antagonist_forces` MUST contain at least {forces_required} "
            f"distinct forces for this {volume_count}-volume plan.\n"
            "- Every force MUST declare `active_volumes` (a list of volume "
            "numbers). The union MUST cover at least "
            f"{int(MIN_VOLUME_COVERAGE * 100)}% of volumes 1..{volume_count}.\n"
            f"- No single force may span more than {max_single} volumes "
            f"(≤ {int(MAX_FORCE_SHARE * 100)}%).\n"
            "- Use at least 2 distinct `force_type` values across the roster.\n"
            f"- `supporting_cast` should include at least {required_supporting} "
            "antagonist-role entries, one per force when feasible.\n"
            "- Do NOT use generic labels like 'the antagonist' / 'main enemy' "
            "/ 'primary force'. Every force name must be rooted in this world.\n"
        )

    return (
        "【基础素材硬性要求】\n"
        f"- `antagonist_forces` 必须包含至少 {forces_required} 个不同的冲突力量"
        f"（本书 {volume_count} 卷）。\n"
        "- 每个 force 必须声明 `active_volumes`（卷号列表），所有 force 的 "
        f"active_volumes 并集必须覆盖 1..{volume_count} 中至少 "
        f"{int(MIN_VOLUME_COVERAGE * 100)}% 的卷。\n"
        f"- 任何单一 force 的 active_volumes 不得超过 {max_single} 卷"
        f"（≤ {int(MAX_FORCE_SHARE * 100)}%）。\n"
        "- 整个 roster 至少要有 2 种不同的 `force_type`。\n"
        f"- `supporting_cast` 至少包含 {required_supporting} 个 antagonist 类角色，"
        "力求每个 force 都有对应人物。\n"
        "- 禁止使用「反派」「敌对势力」「主要反派」等通用标签；"
        "每个 force 名称必须扎根本世界观。\n"
    )
