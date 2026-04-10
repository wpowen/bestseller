"""Plan validation logic — validates a novel plan against genre-specific rubrics.

Runs universal structural checks on every plan and then applies genre-specific
checks resolved from the genre review profile.  All diagnostic messages are in
Chinese (internal review, not user-facing).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from bestseller.domain.plan_validation import PlanValidationFinding, PlanValidationResult
from bestseller.services.genre_review_profiles import resolve_genre_review_profile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type alias for check functions
# ---------------------------------------------------------------------------
UniversalCheck = Callable[[list[dict[str, Any]]], tuple[bool, PlanValidationFinding | None]]
GenreCheck = Callable[
    [dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]],
    tuple[bool, PlanValidationFinding | None],
]

# ═══════════════════════════════════════════════════════════════════════════
# Universal checks (run for ALL genres)
# ═══════════════════════════════════════════════════════════════════════════


def _check_volume_goals_distinct(
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """No two volumes should share identical volume_goal text."""
    goals: list[str] = []
    for vol in volume_plan:
        goal = str(vol.get("volume_goal", "") or "").strip()
        if goal:
            goals.append(goal)

    if len(goals) != len(set(goals)):
        duplicates = [g for g in set(goals) if goals.count(g) > 1]
        return False, PlanValidationFinding(
            category="volume_goals",
            severity="critical",
            message=f"存在重复的卷目标：{'、'.join(duplicates[:3])}",
            suggestion="每卷应有独立的叙事目标，避免复制粘贴。",
        )
    return True, None


def _check_challenge_evolution(
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """Conflict phases and primary forces must vary across volumes."""
    if len(volume_plan) < 2:
        return True, None

    phases = [str(vol.get("conflict_phase", "") or "").strip() for vol in volume_plan]
    non_empty_phases = [p for p in phases if p]

    forces = [str(vol.get("primary_force_name", "") or "").strip() for vol in volume_plan]
    non_empty_forces = [f for f in forces if f]

    all_phases_same = len(set(non_empty_phases)) <= 1 and len(non_empty_phases) >= 2
    all_forces_same = len(set(non_empty_forces)) <= 1 and len(non_empty_forces) >= 2

    if all_phases_same and all_forces_same:
        return False, PlanValidationFinding(
            category="challenge_evolution",
            severity="critical",
            message="所有卷的冲突阶段和主要对抗力量完全相同，缺乏递进和变化。",
            suggestion="为不同卷设置不同类型的冲突阶段（如 survival、political_intrigue、betrayal 等），并安排不同的对抗势力。",
        )
    if all_phases_same:
        return False, PlanValidationFinding(
            category="challenge_evolution",
            severity="warning",
            message=f"所有卷的冲突阶段均为「{non_empty_phases[0]}」，缺少变化。",
            suggestion="建议在不同卷中采用不同的冲突类型以增强叙事张力。",
        )
    if all_forces_same:
        return False, PlanValidationFinding(
            category="challenge_evolution",
            severity="warning",
            message=f"所有卷的主要对抗力量均为「{non_empty_forces[0]}」，压力源单一。",
            suggestion="引入多元化的冲突力量以避免读者疲劳。",
        )
    return True, None


def _check_foreshadowing_balance(
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """Foreshadowing planted in early volumes should be paid off in later volumes."""
    if len(volume_plan) < 2:
        return True, None

    planted_volumes: list[int] = []
    paid_off_volumes: list[int] = []

    for vol in volume_plan:
        vol_num = vol.get("volume_number", 0)
        planted = vol.get("foreshadowing_planted") or []
        paid_off = vol.get("foreshadowing_paid_off") or []
        if planted:
            planted_volumes.append(vol_num)
        if paid_off:
            paid_off_volumes.append(vol_num)

    if planted_volumes and not paid_off_volumes:
        return False, PlanValidationFinding(
            category="foreshadowing_balance",
            severity="warning",
            message=f"在第{'、'.join(str(v) for v in planted_volumes)}卷埋下了伏笔，但没有任何卷对其进行回收。",
            suggestion="确保伏笔在后续卷中得到兑现，否则会让读者感到被欺骗。",
        )
    return True, None


def _check_hooks(
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """All volumes except the last should have a reader_hook_to_next."""
    if len(volume_plan) < 2:
        return True, None

    sorted_vols = sorted(volume_plan, key=lambda v: v.get("volume_number", 0))
    non_last = sorted_vols[:-1]
    missing: list[int] = []

    for vol in non_last:
        hook = str(vol.get("reader_hook_to_next", "") or "").strip()
        if not hook:
            missing.append(vol.get("volume_number", 0))

    if missing:
        return False, PlanValidationFinding(
            category="hooks",
            severity="warning",
            message=f"第{'、'.join(str(v) for v in missing)}卷缺少读者钩子（reader_hook_to_next），可能导致续读率下降。",
            suggestion="为每个非末卷添加一个引导读者继续阅读的钩子。",
        )
    return True, None


def _check_volume_themes(
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """Each volume should have a non-empty volume_theme."""
    missing: list[int] = []
    for vol in volume_plan:
        theme = str(vol.get("volume_theme", "") or "").strip()
        if not theme:
            missing.append(vol.get("volume_number", 0))

    if missing:
        return False, PlanValidationFinding(
            category="volume_themes",
            severity="warning",
            message=f"第{'、'.join(str(v) for v in missing)}卷缺少卷级主题（volume_theme）。",
            suggestion="每卷应有明确的主题以统领该卷的叙事方向。",
        )
    return True, None


# ---------------------------------------------------------------------------
# Registry of universal checks
# ---------------------------------------------------------------------------
_UNIVERSAL_CHECKS: list[tuple[str, UniversalCheck]] = [
    ("volume_goals_distinct", _check_volume_goals_distinct),
    ("challenge_evolution", _check_challenge_evolution),
    ("foreshadowing_balance", _check_foreshadowing_balance),
    ("hooks", _check_hooks),
    ("volume_themes", _check_volume_themes),
]

# ═══════════════════════════════════════════════════════════════════════════
# Genre-specific checks
# ═══════════════════════════════════════════════════════════════════════════


def _check_power_tier_escalation(
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """For action-progression: protagonist power tier should escalate across volumes.

    Also validates that the world_spec defines a power_system with >= 3 tiers.
    """
    # Check power system depth
    power_system = world_spec.get("power_system") or {}
    tiers = power_system.get("tiers") or power_system.get("power_tiers") or []
    if isinstance(tiers, list) and len(tiers) < 3:
        return False, PlanValidationFinding(
            category="power_tier_escalation",
            severity="warning",
            message=f"力量体系仅定义了{len(tiers)}个层级，建议至少设置3个以支撑长篇递进。",
            suggestion="扩展力量体系层级以提供更多成长空间。",
        )

    # Check tier escalation across volumes
    if len(volume_plan) < 2:
        return True, None

    tier_values: list[str] = []
    for vol in sorted(volume_plan, key=lambda v: v.get("volume_number", 0)):
        opening = vol.get("opening_state") or {}
        resolution = vol.get("volume_resolution") or {}
        tier = (
            str(resolution.get("protagonist_power_tier") or "").strip()
            or str(opening.get("protagonist_power_tier") or "").strip()
        )
        if tier:
            tier_values.append(tier)

    if len(tier_values) >= 2 and len(set(tier_values)) == 1:
        return False, PlanValidationFinding(
            category="power_tier_escalation",
            severity="critical",
            message=f"主角力量层级在所有卷中始终为「{tier_values[0]}」，缺乏成长感。",
            suggestion="规划主角在不同卷中的力量递进，体现角色成长。",
        )
    return True, None


def _check_antagonist_evolution(
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """Antagonist forces should have varied force_types and rotate across volumes."""
    forces = cast_spec.get("antagonist_forces") or []
    if not forces:
        return False, PlanValidationFinding(
            category="antagonist_evolution",
            severity="warning",
            message="角色设定中缺少对抗力量（antagonist_forces），冲突来源不够多元。",
            suggestion="添加2-4个不同类型的冲突力量（character/faction/environment/internal/systemic）。",
        )

    force_types: list[str] = []
    for f in forces:
        ft = str(f.get("force_type", "") if isinstance(f, dict) else getattr(f, "force_type", "")).strip()
        if ft:
            force_types.append(ft)

    if len(force_types) >= 2 and len(set(force_types)) <= 1:
        return False, PlanValidationFinding(
            category="antagonist_evolution",
            severity="warning",
            message=f"所有对抗力量的类型均为「{force_types[0]}」，建议引入多元化的冲突源。",
            suggestion="混合使用不同类型的冲突力量（如角色、势力、环境等）以丰富冲突层次。",
        )

    # Check that different volumes reference different primary forces
    primary_forces_in_volumes: list[str] = []
    for vol in volume_plan:
        pf = str(vol.get("primary_force_name", "") or "").strip()
        if pf:
            primary_forces_in_volumes.append(pf)

    if len(primary_forces_in_volumes) >= 3 and len(set(primary_forces_in_volumes)) <= 1:
        return False, PlanValidationFinding(
            category="antagonist_evolution",
            severity="warning",
            message="所有卷的主要对抗力量引用相同角色/势力，缺乏对抗面的变化。",
            suggestion="安排不同卷面对不同的核心威胁，增强叙事多样性。",
        )
    return True, None


def _check_conflict_phase_variety(
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """At least 3 different conflict_phase values should be used when 4+ volumes exist."""
    if len(volume_plan) < 4:
        return True, None

    phases: set[str] = set()
    for vol in volume_plan:
        phase = str(vol.get("conflict_phase", "") or "").strip()
        if phase:
            phases.add(phase)

    if len(phases) < 3:
        return False, PlanValidationFinding(
            category="conflict_phase_variety",
            severity="warning",
            message=f"共{len(volume_plan)}卷但仅使用了{len(phases)}种冲突阶段类型，变化不足。",
            suggestion="建议使用至少3种不同的冲突阶段（如 survival、political_intrigue、betrayal、faction_war、existential_threat 等）。",
        )
    return True, None


_RELATIONSHIP_KEYWORDS: list[str] = [
    "信任",
    "误解",
    "靠近",
    "选择",
    "并肩",
    "质变",
    "关系",
    "情感",
    "trust",
    "misunderstand",
    "closer",
    "choice",
]


def _check_relationship_milestone_progression(
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """For relationship-driven genres: volume themes/goals should include relationship keywords."""
    if len(volume_plan) < 2:
        return True, None

    volumes_with_keyword: list[int] = []
    for vol in volume_plan:
        text = " ".join([
            str(vol.get("volume_goal", "") or ""),
            str(vol.get("volume_theme", "") or ""),
        ])
        has_keyword = any(kw in text for kw in _RELATIONSHIP_KEYWORDS)
        if has_keyword:
            volumes_with_keyword.append(vol.get("volume_number", 0))

    if not volumes_with_keyword:
        return False, PlanValidationFinding(
            category="relationship_milestones",
            severity="critical",
            message="作为情感/关系驱动的类型，没有任何卷的目标或主题包含关系发展的关键词。",
            suggestion=f"在卷目标或主题中体现关系里程碑（如：{'、'.join(_RELATIONSHIP_KEYWORDS[:6])}）。",
        )
    if len(volumes_with_keyword) < len(volume_plan) // 2:
        return False, PlanValidationFinding(
            category="relationship_milestones",
            severity="warning",
            message=f"仅{len(volumes_with_keyword)}/{len(volume_plan)}卷包含关系发展关键词，比例偏低。",
            suggestion="增加更多卷中关系发展的显性表达，强化情感主线。",
        )
    return True, None


def _check_emotional_arc_explicit(
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """Each volume should have emotional goals visible in volume_goal or volume_theme."""
    missing: list[int] = []
    for vol in volume_plan:
        goal = str(vol.get("volume_goal", "") or "").strip()
        theme = str(vol.get("volume_theme", "") or "").strip()
        if not goal and not theme:
            missing.append(vol.get("volume_number", 0))

    if missing:
        return False, PlanValidationFinding(
            category="emotional_arc",
            severity="warning",
            message=f"第{'、'.join(str(v) for v in missing)}卷同时缺少卷目标和卷主题，情感弧线不明确。",
            suggestion="每卷至少应有一个明确的情感或叙事目标。",
        )
    return True, None


def _check_clue_chain_exists(
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """For suspense-mystery: key_reveals should be distributed across at least 2 volumes."""
    volumes_with_reveals: list[int] = []
    for vol in volume_plan:
        reveals = vol.get("key_reveals") or []
        if reveals:
            volumes_with_reveals.append(vol.get("volume_number", 0))

    if len(volumes_with_reveals) < 2:
        return False, PlanValidationFinding(
            category="clue_chain",
            severity="critical",
            message=f"关键揭示仅分布在{len(volumes_with_reveals)}卷中，悬念线断裂。"
            if volumes_with_reveals
            else "没有任何卷定义了关键揭示（key_reveals），悬疑线缺失。",
            suggestion="将关键线索和揭示分散到至少2个卷中，构建完整的推理/悬疑链条。",
        )
    return True, None


def _check_misdirection_planned(
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """Foreshadowing/misdirection should be planted in at least 2 volumes."""
    planted_count = 0
    for vol in volume_plan:
        planted = vol.get("foreshadowing_planted") or []
        if planted:
            planted_count += 1

    if planted_count < 2:
        return False, PlanValidationFinding(
            category="misdirection",
            severity="warning",
            message=f"仅有{planted_count}卷埋下了伏笔/误导，悬疑效果可能不足。",
            suggestion="在至少2个卷中设置伏笔以构建有效的误导和悬念。",
        )
    return True, None


def _check_information_escalation(
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """Key reveals count should stay significant across volumes (not all crammed in one)."""
    if len(volume_plan) < 2:
        return True, None

    sorted_vols = sorted(volume_plan, key=lambda v: v.get("volume_number", 0))
    reveal_counts = [len(vol.get("key_reveals") or []) for vol in sorted_vols]

    total = sum(reveal_counts)
    if total == 0:
        return False, PlanValidationFinding(
            category="information_escalation",
            severity="warning",
            message="整个计划中没有任何关键揭示（key_reveals），信息层缺失。",
            suggestion="为至少半数的卷分配关键揭示，确保信息逐步释放。",
        )

    max_count = max(reveal_counts)
    if max_count > 0 and max_count >= total * 0.8 and len(volume_plan) >= 3:
        heavy_vol = sorted_vols[reveal_counts.index(max_count)].get("volume_number", 0)
        return False, PlanValidationFinding(
            category="information_escalation",
            severity="warning",
            message=f"超过80%的关键揭示集中在第{heavy_vol}卷，信息释放节奏失衡。",
            suggestion="将关键揭示分散到多个卷中，保持信息递进的节奏感。",
        )
    return True, None


def _check_faction_progression(
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """For strategy-worldbuilding: faction references should change across volumes."""
    if len(volume_plan) < 2:
        return True, None

    force_names: list[str] = []
    for vol in volume_plan:
        name = str(vol.get("primary_force_name", "") or "").strip()
        if name:
            force_names.append(name)

    if len(force_names) >= 2 and len(set(force_names)) <= 1:
        return False, PlanValidationFinding(
            category="faction_progression",
            severity="warning",
            message=f"所有卷的势力引用均为「{force_names[0]}」，缺少势力轮替和博弈变化。",
            suggestion="不同卷中引入不同的势力冲突焦点，体现战略格局的演变。",
        )
    return True, None


def _check_worldbuilding_depth_check(
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> tuple[bool, PlanValidationFinding | None]:
    """World spec should have at least 3 rules and 2 locations for strategy/worldbuilding."""
    rules = world_spec.get("rules") or world_spec.get("world_rules") or []
    locations = world_spec.get("locations") or []

    issues: list[str] = []
    if len(rules) < 3:
        issues.append(f"世界规则仅{len(rules)}条（建议至少3条）")
    if len(locations) < 2:
        issues.append(f"地点设定仅{len(locations)}个（建议至少2个）")

    if issues:
        return False, PlanValidationFinding(
            category="worldbuilding_depth",
            severity="warning",
            message="世界观设定深度不足：" + "；".join(issues) + "。",
            suggestion="丰富世界观设定中的规则体系和地点描述，为长线叙事提供足够的空间。",
        )
    return True, None


# ---------------------------------------------------------------------------
# Genre check registry
# ---------------------------------------------------------------------------
_GENRE_CHECK_REGISTRY: dict[str, GenreCheck] = {
    "power_tier_escalation": _check_power_tier_escalation,
    "antagonist_evolution": _check_antagonist_evolution,
    "conflict_phase_variety": _check_conflict_phase_variety,
    "relationship_milestone_progression": _check_relationship_milestone_progression,
    "emotional_arc_explicit": _check_emotional_arc_explicit,
    "clue_chain_exists": _check_clue_chain_exists,
    "misdirection_planned": _check_misdirection_planned,
    "information_escalation": _check_information_escalation,
    "faction_progression": _check_faction_progression,
    "worldbuilding_depth_check": _check_worldbuilding_depth_check,
}


# ═══════════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════════


def validate_plan(
    *,
    genre: str,
    sub_genre: str | None = None,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> PlanValidationResult:
    """Validate a novel plan against universal and genre-specific rubrics.

    Parameters
    ----------
    genre:
        Primary genre category (e.g. "action-progression", "romance",
        "suspense-mystery", "strategy-worldbuilding").
    sub_genre:
        Optional sub-genre for finer-grained profile selection.
    book_spec:
        High-level book specification dict (title, themes, synopsis, etc.).
    world_spec:
        World specification dict (rules, locations, power_system, etc.).
    cast_spec:
        Cast specification dict (protagonist, antagonist, antagonist_forces, etc.).
    volume_plan:
        List of volume plan dicts, each containing volume_number, volume_goal,
        volume_theme, conflict_phase, key_reveals, foreshadowing_*, etc.

    Returns
    -------
    PlanValidationResult with score, findings, and rubric check outcomes.
    """
    profile = resolve_genre_review_profile(genre=genre, sub_genre=sub_genre)

    findings: list[PlanValidationFinding] = []
    rubric_checks: dict[str, bool] = {}

    # ── 1. Universal checks ──────────────────────────────────────────
    for check_name, check_fn in _UNIVERSAL_CHECKS:
        try:
            passed, finding = check_fn(volume_plan)
        except Exception:
            logger.exception("Universal check '%s' raised an error", check_name)
            passed = False
            finding = PlanValidationFinding(
                category=check_name,
                severity="warning",
                message=f"通用检查「{check_name}」执行异常，请检查计划数据完整性。",
            )
        rubric_checks[check_name] = passed
        if finding is not None:
            findings.append(finding)

    # ── 2. Genre-specific checks ─────────────────────────────────────
    plan_rubric = getattr(profile, "plan_rubric", None)
    required_checks: list[str] = []
    if plan_rubric is not None:
        required_checks = list(getattr(plan_rubric, "required_checks", []) or [])

    for check_name in required_checks:
        check_fn_genre = _GENRE_CHECK_REGISTRY.get(check_name)
        if check_fn_genre is None:
            logger.warning("Genre check '%s' not found in registry — skipping", check_name)
            rubric_checks[check_name] = True
            continue
        try:
            passed, finding = check_fn_genre(book_spec, world_spec, cast_spec, volume_plan)
        except Exception:
            logger.exception("Genre check '%s' raised an error", check_name)
            passed = False
            finding = PlanValidationFinding(
                category=check_name,
                severity="warning",
                message=f"类型检查「{check_name}」执行异常，请检查计划数据完整性。",
            )
        rubric_checks[check_name] = passed
        if finding is not None:
            findings.append(finding)

    # ── 3. Compute score ─────────────────────────────────────────────
    total_checks = len(rubric_checks)
    passing_checks = sum(1 for v in rubric_checks.values() if v)
    score = passing_checks / total_checks if total_checks > 0 else 1.0

    # ── 4. Determine overall pass ────────────────────────────────────
    # overall_pass is False only if a required genre check has a critical failure
    overall_pass = True
    for check_name in required_checks:
        if not rubric_checks.get(check_name, True):
            # Check if this failure is critical
            related_findings = [
                f for f in findings if f.category == check_name and f.severity == "critical"
            ]
            if related_findings:
                overall_pass = False
                break

    genre_category = f"{genre}/{sub_genre}" if sub_genre else genre

    return PlanValidationResult(
        genre_category=genre_category,
        overall_pass=overall_pass,
        score=round(score, 4),
        findings=findings,
        rubric_checks=rubric_checks,
    )
