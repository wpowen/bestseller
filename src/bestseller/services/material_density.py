"""Project material density audit and canon-pack hydration.

The global ``material_library`` is useful only after a project has enough
project-scoped ``project_materials`` for prompts to reference. Historical
books often predate Material Forge, so this module provides two pragmatic
operations:

* audit the density of active project materials by dimension;
* hydrate a project-local material pack from an already locked story bible.

The hydration path is intentionally deterministic. It does not ask an LLM to
invent canon; it extracts stable, already-approved anchors into structured
material rows that Planner and Drafter can cite via ``§dimension/project/slug``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.decision_policy import DecisionPolicy
from bestseller.infra.db.models import (
    MaterialLibraryModel,
    ProjectMaterialModel,
    ProjectModel,
)
from bestseller.services.material_forge.base import ProjectMaterial, insert_project_material
from bestseller.services.material_library import genre_aliases
from bestseller.services.material_reference import render_material_reference_block
from bestseller.services.premium_state_ledger import (
    materialize_premium_state_snapshot,
    validate_premium_state_ledger,
)


PROJECT_MATERIAL_TARGETS: dict[str, int] = {
    "world_settings": 4,
    "factions": 4,
    "locale_templates": 5,
    "power_systems": 5,
    "character_archetypes": 4,
    "character_templates": 6,
    "plot_patterns": 4,
    "scene_templates": 6,
    "device_templates": 5,
    "thematic_motifs": 4,
    "emotion_arcs": 3,
    "dialogue_styles": 3,
    "anti_cliche_patterns": 3,
    "real_world_references": 3,
}


MaterialSpec = tuple[str, str, str, dict[str, Any]]


@dataclass(frozen=True)
class MaterialPackSpec:
    pack_id: str
    dimensions: dict[str, tuple[MaterialSpec, ...]]


@dataclass(frozen=True)
class CategoryPackBlueprint:
    category_key: str
    name_zh: str
    name_en: str
    protagonist_zh: str
    protagonist_en: str
    archetype_key: str
    risk_tolerance: str
    system_zh: str
    system_en: str
    world_zh: str
    world_en: str
    tiers_zh: tuple[str, ...]
    tiers_en: tuple[str, ...]
    start_tier_zh: str
    start_tier_en: str
    power_structure_zh: str
    power_structure_en: str
    rule_code: str
    rule_effect_zh: str
    rule_effect_en: str
    rule_cost_zh: str
    rule_cost_en: str
    faction_zh: str
    faction_en: str
    faction_reaction_zh: str
    faction_reaction_en: str
    relationship_target_zh: str
    relationship_target_en: str
    agency_debt_zh: str
    agency_debt_en: str


@dataclass(frozen=True)
class DimensionDensity:
    dimension: str
    active_count: int
    target_count: int
    global_seed_count: int

    @property
    def gap(self) -> int:
        return max(self.target_count - self.active_count, 0)

    @property
    def is_satisfied(self) -> bool:
        return self.gap == 0


@dataclass(frozen=True)
class MaterialDensityReport:
    project_id: str
    genre: str | None
    sub_genre: str | None
    genre_buckets: tuple[str, ...]
    dimensions: tuple[DimensionDensity, ...]
    total_active: int
    total_target: int

    @property
    def passed(self) -> bool:
        return all(item.is_satisfied for item in self.dimensions)


def material_density_report_to_dict(report: MaterialDensityReport) -> dict[str, Any]:
    return {
        "project_id": report.project_id,
        "genre": report.genre,
        "sub_genre": report.sub_genre,
        "genre_buckets": list(report.genre_buckets),
        "total_active": report.total_active,
        "total_target": report.total_target,
        "passed": report.passed,
        "dimensions": [
            {
                "dimension": item.dimension,
                "active_count": item.active_count,
                "target_count": item.target_count,
                "gap": item.gap,
                "global_seed_count": item.global_seed_count,
                "satisfied": item.is_satisfied,
            }
            for item in report.dimensions
        ],
    }


async def audit_project_material_density(
    session: AsyncSession,
    *,
    project_id: str,
    genre: str | None,
    sub_genre: str | None = None,
) -> MaterialDensityReport:
    """Return material density by dimension for one project."""

    genre_buckets = genre_aliases(genre, sub_genre)
    dimensions: list[DimensionDensity] = []
    for dimension, target in PROJECT_MATERIAL_TARGETS.items():
        project_count_stmt = select(func.count(ProjectMaterialModel.id)).where(
            ProjectMaterialModel.project_id == project_id,
            ProjectMaterialModel.material_type == dimension,
            ProjectMaterialModel.status == "active",
        )
        active_count = int((await session.execute(project_count_stmt)).scalar_one() or 0)

        seed_count_stmt = select(func.count(MaterialLibraryModel.id)).where(
            MaterialLibraryModel.dimension == dimension,
            MaterialLibraryModel.status == "active",
        )
        if genre_buckets:
            seed_count_stmt = seed_count_stmt.where(
                MaterialLibraryModel.genre.in_(genre_buckets)
                | MaterialLibraryModel.genre.is_(None)
            )
        global_seed_count = int((await session.execute(seed_count_stmt)).scalar_one() or 0)

        dimensions.append(
            DimensionDensity(
                dimension=dimension,
                active_count=active_count,
                target_count=target,
                global_seed_count=global_seed_count,
            )
        )

    return MaterialDensityReport(
        project_id=project_id,
        genre=genre,
        sub_genre=sub_genre,
        genre_buckets=genre_buckets,
        dimensions=tuple(dimensions),
        total_active=sum(item.active_count for item in dimensions),
        total_target=sum(item.target_count for item in dimensions),
    )


async def hydrate_story_bible_materials(
    session: AsyncSession,
    *,
    project_id: str,
    package_root: Path,
    title: str | None = None,
    genre: str | None = None,
    sub_genre: str | None = None,
    language: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Hydrate project materials from a locked story-bible package.

    Supports deterministic canon/type packs. Unknown packages return a dry
    report with zero candidates instead of inventing material.
    """

    package_text = _read_package_signal(package_root)
    supported_pack, candidates = _select_material_pack(
        project_id,
        package_text,
        title=title,
        genre=genre,
        sub_genre=sub_genre,
        language=language,
    )
    if apply:
        for material in candidates:
            await insert_project_material(session, material)
        await session.flush()

    by_dimension: dict[str, int] = {}
    for material in candidates:
        by_dimension[material.material_type] = by_dimension.get(material.material_type, 0) + 1

    result: dict[str, Any] = {
        "project_id": project_id,
        "package_root": str(package_root),
        "supported_pack": supported_pack,
        "candidate_count": len(candidates),
        "applied": bool(apply),
        "by_dimension": by_dimension,
    }
    if apply:
        result["reference_block"] = await refresh_project_material_reference_block(
            session,
            project_id=project_id,
        )
        result["premium_capability"] = await hydrate_premium_capability_metadata(
            session,
            project_id=project_id,
            pack_id=supported_pack,
        )
    return result


async def hydrate_project_genre_pack(
    session: AsyncSession,
    *,
    project_id: str,
    title: str | None = None,
    genre: str | None = None,
    sub_genre: str | None = None,
    language: str | None = None,
    apply: bool = True,
) -> dict[str, Any]:
    """Hydrate the best genre/category pack for a newly created project."""

    supported_pack, candidates = _select_material_pack(
        project_id,
        "",
        title=title,
        genre=genre,
        sub_genre=sub_genre,
        language=language,
    )
    if apply:
        for material in candidates:
            await insert_project_material(session, material)
        await session.flush()

    by_dimension: dict[str, int] = {}
    for material in candidates:
        by_dimension[material.material_type] = by_dimension.get(material.material_type, 0) + 1

    result: dict[str, Any] = {
        "project_id": project_id,
        "supported_pack": supported_pack,
        "candidate_count": len(candidates),
        "applied": bool(apply),
        "by_dimension": by_dimension,
    }
    if apply and supported_pack:
        result["reference_block"] = await refresh_project_material_reference_block(
            session,
            project_id=project_id,
        )
        result["premium_capability"] = await hydrate_premium_capability_metadata(
            session,
            project_id=project_id,
            pack_id=supported_pack,
        )
    return result


async def refresh_project_material_reference_block(
    session: AsyncSession,
    *,
    project_id: str,
    include_content_preview: bool = False,
) -> dict[str, Any]:
    """Render project materials into project metadata for prompt injection."""

    project = await session.get(ProjectModel, _coerce_uuid(project_id))
    if project is None:
        raise ValueError(f"Project '{project_id}' was not found.")

    block = await render_material_reference_block(
        session,
        project_id,
        include_content_preview=include_content_preview,
    )
    metadata = dict(project.metadata_json or {})
    metadata["material_reference_block"] = block
    metadata["material_reference_block_updated_at"] = datetime.now(
        timezone.utc
    ).isoformat()
    project.metadata_json = metadata
    await session.flush()

    return {
        "project_id": project_id,
        "present": bool(block),
        "line_count": len(block.splitlines()) if block else 0,
        "char_count": len(block),
    }


async def hydrate_premium_capability_metadata(
    session: AsyncSession,
    *,
    project_id: str,
    pack_id: str | None,
) -> dict[str, Any]:
    """Seed project-level premium controls required by long serial output."""

    if not pack_id:
        return {"project_id": project_id, "present": False, "skipped": "unsupported_pack"}
    policy = _decision_policy_for_pack(pack_id)
    ledger = _initial_premium_state_ledger_for_pack(pack_id)
    world_spec = _premium_world_spec_for_pack(pack_id)
    cast_spec = _premium_cast_spec_for_pack(pack_id)
    volume_plan = _premium_volume_plan_for_pack(pack_id)
    if not policy or not ledger:
        return {"project_id": project_id, "present": False, "skipped": "no_capability_pack"}

    project = await session.get(ProjectModel, _coerce_uuid(project_id))
    if project is None:
        return {"project_id": project_id, "present": False, "skipped": "project_missing"}
    if getattr(project, "metadata_json", None) is not None and not isinstance(
        project.metadata_json, dict
    ):
        return {
            "project_id": project_id,
            "present": False,
            "skipped": "invalid_project_model",
        }

    metadata = dict(project.metadata_json or {})
    changed_fields: list[str] = []
    if not metadata.get("decision_policy"):
        metadata["decision_policy"] = policy
        changed_fields.append("decision_policy")
    if not metadata.get("premium_state_ledger"):
        metadata["premium_state_ledger"] = ledger
        changed_fields.append("premium_state_ledger")
    if world_spec:
        metadata["premium_world_spec"] = world_spec
        changed_fields.append("premium_world_spec")
    if cast_spec:
        metadata["premium_cast_spec"] = cast_spec
        changed_fields.append("premium_cast_spec")
    metadata["premium_volume_plan"] = volume_plan
    changed_fields.append("premium_volume_plan")

    report = validate_premium_state_ledger(metadata.get("premium_state_ledger"))
    snapshot = materialize_premium_state_snapshot(metadata.get("premium_state_ledger"))
    metadata["premium_state_ledger_report"] = report.to_dict()
    metadata["premium_state_snapshot"] = snapshot
    metadata["premium_capability_pack"] = pack_id
    metadata["premium_capability_updated_at"] = datetime.now(timezone.utc).isoformat()
    changed_fields.extend(
        [
            "premium_state_ledger_report",
            "premium_state_snapshot",
            "premium_capability_pack",
        ]
    )

    project.metadata_json = metadata
    await session.flush()
    return {
        "project_id": project_id,
        "present": True,
        "pack_id": pack_id,
        "changed_fields": sorted(set(changed_fields)),
        "decision_policy": bool(metadata.get("decision_policy")),
        "premium_state_ledger": bool(metadata.get("premium_state_ledger")),
        "premium_state_snapshot": bool(metadata.get("premium_state_snapshot")),
        "premium_state_passed": report.passed and snapshot.get("passed") is not False,
    }


def _validated_decision_policy(raw: dict[str, Any]) -> dict[str, Any]:
    return DecisionPolicy.model_validate(raw).model_dump(mode="json")


def _decision_policy_for_pack(pack_id: str) -> dict[str, Any] | None:
    raw: dict[str, Any] | None = None
    if pack_id == "qingnang":
        raw = {
            "character_name": "林渊",
            "archetype": "evidence-led-occult-ledger-investigator",
            "risk_tolerance": "medium",
            "pressure_responses": ["investigate", "observe", "prepare", "protect", "bargain", "retreat"],
            "preferred_tactics": [
                {"key": "evidence_chain", "description": "先找物证、方位、账印和现实证据，再判断阴债规则。"},
                {"key": "rule_probe", "description": "用低成本试探确认困魂镜/青囊规则的触发条件。"},
                {"key": "pay_visible_cost", "description": "破局必须让读者看到寿命、身份、关系或证据代价。"},
            ],
            "moral_boundaries": [
                {"key": "do_not_shift_debt_to_innocent", "description": "不把镜债转嫁给无关活人。"},
            ],
            "forbidden_behaviors": [
                {"key": "free_magic_solve", "description": "不得用无代价法术直接解题。"},
                {"key": "ignore_police_evidence", "description": "不得无视苏婉宁的现实证据压力。"},
                {"key": "random_ghost_hunt", "description": "不得偏离十七栋、三族旧契和青囊因果账。"},
            ],
        }
    elif pack_id == "english_romantasy":
        raw = {
            "character_name": "Nora Chen",
            "archetype": "agency-first-shadowbound-romantasy-heroine",
            "risk_tolerance": "medium",
            "pressure_responses": ["observe", "bargain", "conceal", "protect", "prepare", "retreat"],
            "preferred_tactics": [
                {"key": "bargain_with_terms", "description": "Turn fae offers into explicit terms, witnesses, loopholes, and costs."},
                {"key": "protect_agency", "description": "Preserve Nora's choice before accepting romance, court power, or prophecy."},
                {"key": "test_shadow_cost", "description": "Verify shadow sight through a small cost before using it publicly."},
            ],
            "moral_boundaries": [
                {"key": "do_not_trade_friend_consent", "description": "Nora cannot trade another person's freedom or consent for court advantage."},
            ],
            "forbidden_behaviors": [
                {"key": "surrender_agency_for_romance", "description": "Romance cannot solve the central political or magical problem for her."},
                {"key": "trust_fae_oath_for_free", "description": "No fae oath is safe unless price, witness, and loophole are visible."},
                {"key": "costless_shadow_power", "description": "Shadow sight must carry a cost, exposure risk, or court consequence."},
            ],
        }
    elif pack_id in {"english_superhero_breaking_point", "english_superhero_progression"}:
        raw = {
            "character_name": "Cole",
            "archetype": "measured-civilian-protection-superhero-progression-lead",
            "risk_tolerance": "medium",
            "pressure_responses": ["protect", "prepare", "investigate", "conceal", "strike_after_certainty", "retreat"],
            "preferred_tactics": [
                {"key": "measure_power_delta", "description": "Treat each power use as measurable load, cost, and public evidence."},
                {"key": "evacuate_civilians_first", "description": "Civilian safety beats spectacle and public glory."},
                {"key": "train_before_escalation", "description": "Upgrade through training, data, and consequence rather than sudden mastery."},
            ],
            "moral_boundaries": [
                {"key": "do_not_create_collateral_for_status", "description": "Cole cannot endanger civilians to prove he belongs among heroes."},
            ],
            "forbidden_behaviors": [
                {"key": "glory_fight", "description": "Do not accept public fights for vanity or leaderboard status."},
                {"key": "ignore_sophie_deadline", "description": "Family pressure must remain a real constraint."},
                {"key": "costless_power_jump", "description": "New power tiers require load, injury, evidence, or social backlash."},
            ],
        }
    elif pack_id == "english_superhero_witness_protocol":
        raw = {
            "character_name": "Kade",
            "archetype": "evidence-first-mimicry-survivor",
            "risk_tolerance": "low",
            "pressure_responses": ["observe", "investigate", "conceal", "prepare", "protect", "retreat"],
            "preferred_tactics": [
                {"key": "record_before_action", "description": "Secure witness evidence before confronting a stronger faction."},
                {"key": "use_sixty_second_window", "description": "Plan mimicry actions around the sixty-second limit and aftermath."},
                {"key": "protect_maya_boundary", "description": "Keep Maya's safety and consent visible in every escalation."},
            ],
            "moral_boundaries": [
                {"key": "do_not_frame_innocent", "description": "Kade cannot use mimicry to shift blame onto an innocent person."},
            ],
            "forbidden_behaviors": [
                {"key": "identity_theft_without_cost", "description": "Mimicry must create evidence, trust, or legal consequences."},
                {"key": "rush_without_record", "description": "Do not confront factions without recording a useful witness trail."},
                {"key": "ignore_registry_pressure", "description": "Registry and surveillance pressure must constrain choices."},
            ],
        }
    elif pack_id == "female_no_cp_apocalypse":
        raw = {
            "character_name": "林鸢",
            "archetype": "no-cp-cost-conversion-female-lead",
            "risk_tolerance": "medium",
            "pressure_responses": ["observe", "prepare", "protect", "bargain", "strike_after_certainty", "retreat"],
            "preferred_tactics": [
                {"key": "price_every_choice", "description": "每次使用代价转化都要明确代价、收益和后续债务。"},
                {"key": "build_nonromantic_alliance", "description": "通过利益、承诺和能力建立非恋爱同盟。"},
                {"key": "turn_system_pressure", "description": "把方舟城、联盟和清道夫的制度压力转化为反制机会。"},
            ],
            "moral_boundaries": [
                {"key": "do_not_trade_selfhood", "description": "不为了短期胜利放弃自我意志或让源初吞并人格。"},
            ],
            "forbidden_behaviors": [
                {"key": "romance_rescue", "description": "不得用恋爱救援解决核心困境。"},
                {"key": "free_cost_conversion", "description": "代价转化不能无成本、无后账。"},
                {"key": "passive_suffering", "description": "痛感必须转化为主动选择，而不是单纯受虐。"},
            ],
        }
    elif pack_id == "xianxia_upgrade":
        raw = {
            "character_name": "宁尘",
            "archetype": "low-status-cautious-cultivation-upgrader",
            "risk_tolerance": "low",
            "pressure_responses": ["observe", "prepare", "conceal", "bargain", "retreat", "strike_after_certainty"],
            "preferred_tactics": [
                {"key": "resource_accounting", "description": "先算灵米、丹药、名额、时间和反噬，再决定是否突破。"},
                {"key": "hide_true_progress", "description": "用表面功法遮住真实境界和道种痕迹。"},
                {"key": "low_status_leverage", "description": "利用杂役低位身份误判反制高位压力。"},
            ],
            "moral_boundaries": [
                {"key": "do_not_sacrifice_unrelated_weak", "description": "不为突破牺牲无关弱者。"},
            ],
            "forbidden_behaviors": [
                {"key": "public_vanity_duel", "description": "不可为面子接受公开死斗。"},
                {"key": "free_breakthrough", "description": "不可无资源、无风险、无后账突破。"},
                {"key": "boast_dao_seed", "description": "不可主动暴露道种和真实进度。"},
            ],
        }
    elif blueprint := _blueprint_for_pack(pack_id):
        is_en = _pack_is_english(pack_id)
        raw = {
            "character_name": blueprint.protagonist_en if is_en else blueprint.protagonist_zh,
            "archetype": blueprint.archetype_key,
            "risk_tolerance": blueprint.risk_tolerance,
            "pressure_responses": ["observe", "prepare", "bargain", "protect", "retreat"],
            "preferred_tactics": [
                {
                    "key": "track_core_rule",
                    "description": (
                        "Use the category rule engine before escalating the scene."
                        if is_en
                        else "先按品类规则引擎判断，再升级场景冲突。"
                    ),
                },
                {
                    "key": "preserve_state_cost",
                    "description": (
                        "Every win must update power, relationship, faction, or resource state."
                        if is_en
                        else "每次胜利都必须更新实力、关系、阵营或资源状态。"
                    ),
                },
                {
                    "key": "pay_genre_price",
                    "description": blueprint.rule_cost_en if is_en else blueprint.rule_cost_zh,
                },
            ],
            "moral_boundaries": [
                {
                    "key": "do_not_void_reader_promise",
                    "description": (
                        "Do not solve the core conflict by abandoning the genre promise."
                        if is_en
                        else "不得通过放弃品类读者承诺来解决核心冲突。"
                    ),
                }
            ],
            "forbidden_behaviors": [
                {
                    "key": "generic_progress",
                    "description": (
                        "No vague progress; each chapter needs a concrete state delta."
                        if is_en
                        else "禁止虚假推进；每章必须产生可见状态变化。"
                    ),
                },
                {
                    "key": "free_resolution",
                    "description": blueprint.rule_cost_en if is_en else blueprint.rule_cost_zh,
                },
                {
                    "key": "category_drift",
                    "description": (
                        "Do not replace the selected category engine with unrelated tropes."
                        if is_en
                        else "不得用无关套路替代已选品类引擎。"
                    ),
                },
            ],
        }
    if raw is None:
        return None
    return _validated_decision_policy(raw)


def _initial_premium_state_ledger_for_pack(
    pack_id: str,
) -> dict[str, list[dict[str, Any]]] | None:
    if pack_id == "qingnang":
        return {
            "progression_events": [
                {
                    "event_type": "resource_gained",
                    "subject": "林渊",
                    "resource_key": "青囊线索权",
                    "delta": 1,
                    "cause": "十七栋主镜门和三族旧契已锁定为第一卷主引擎",
                }
            ],
            "rule_events": [
                {
                    "rule_code": "deny-admit-account-rule",
                    "visible_effect": "否认者先入账，认账者暂活",
                    "cost": "真相会暴露亲族债和身份代价",
                }
            ],
            "faction_reactions": [
                {
                    "faction": "林张钱三族旧契",
                    "trigger": "林渊继续追查十七栋",
                    "reaction": "各家只交出对自己有利的一半真相并试图转嫁旧债",
                    "next_pressure": "张家门契与钱家守镜线继续加压",
                }
            ],
            "relationship_events": [
                {
                    "character_a": "林渊",
                    "character_b": "苏婉宁",
                    "axis": "trust",
                    "after": "证据互不完全信任但必须协作",
                    "active_choice": "林渊优先给出可验证物证而非要求她相信灵异",
                }
            ],
            "agency_debts": [
                {"owner": "林渊", "debt": "查清父亲入镜和三族第一账", "due_window": "第一卷后段"}
            ],
        }
    if pack_id == "english_romantasy":
        return {
            "progression_events": [
                {
                    "event_type": "technique_unlock",
                    "subject": "Nora Chen",
                    "technique": "Shadow Sight",
                    "cause": "Court exile record exposes her connection to the Two-Court Shadow Crown.",
                }
            ],
            "rule_events": [
                {
                    "rule_code": "court-bargain-grammar",
                    "visible_effect": "Every fae bargain creates witnessable terms, loopholes, and a price.",
                    "cost": "A vague promise can bind Nora's agency or court standing.",
                }
            ],
            "faction_reactions": [
                {
                    "faction": "Summer Court",
                    "trigger": "Nora becomes legible to the Shadow Crown",
                    "reaction": "They offer protection with ownership terms attached.",
                    "next_pressure": "Force Nora to choose between safety and autonomy.",
                }
            ],
            "relationship_events": [
                {
                    "character_a": "Nora Chen",
                    "character_b": "Victor Hale",
                    "axis": "trust",
                    "after": "attraction exists, but political trust remains conditional",
                    "active_choice": "Nora demands terms before accepting Victor's help.",
                }
            ],
            "agency_debts": [
                {
                    "owner": "Nora Chen",
                    "debt": "Keep her own choice intact while using court power.",
                    "due_window": "next court bargain arc",
                }
            ],
        }
    if pack_id in {"english_superhero_breaking_point", "english_superhero_progression"}:
        return {
            "progression_events": [
                {
                    "event_type": "technique_unlock",
                    "subject": "Cole",
                    "technique": "Reservoir Kinetics",
                    "cause": "Reservoir stress event makes his load-bearing power measurable but unstable.",
                }
            ],
            "rule_events": [
                {
                    "rule_code": "measurable-power-load",
                    "visible_effect": "Each power spike leaves injury, public evidence, or surveillance traces.",
                    "cost": "Overuse risks collapse and institutional attention.",
                }
            ],
            "faction_reactions": [
                {
                    "faction": "Municipal Enforcement",
                    "trigger": "Cole's kinetic signature appears in public incident records",
                    "reaction": "They classify him as an unregistered escalation risk.",
                    "next_pressure": "Registry summons and surveillance pressure tighten.",
                }
            ],
            "relationship_events": [
                {
                    "character_a": "Cole",
                    "character_b": "Sophie",
                    "axis": "promise",
                    "after": "family deadline remains active",
                    "active_choice": "Cole must protect Sophie without turning her into passive leverage.",
                }
            ],
            "agency_debts": [
                {
                    "owner": "Cole",
                    "debt": "Prove his power can protect civilians without becoming public collateral damage.",
                    "due_window": "next escalation sequence",
                }
            ],
        }
    if pack_id == "english_superhero_witness_protocol":
        return {
            "progression_events": [
                {
                    "event_type": "technique_unlock",
                    "subject": "Kade",
                    "technique": "Sixty-Second Mimicry",
                    "cause": "Witness protocol exposure gives him a short identity-copy window.",
                }
            ],
            "rule_events": [
                {
                    "rule_code": "sixty-second-mimicry-limit",
                    "visible_effect": "Kade can copy a visible power/identity trace for sixty seconds.",
                    "cost": "Every use creates surveillance, legal, or trust evidence.",
                }
            ],
            "faction_reactions": [
                {
                    "faction": "Registry",
                    "trigger": "Kade's mimicry generates conflicting witness records",
                    "reaction": "They treat him as evidence contamination, not just a powered suspect.",
                    "next_pressure": "Force Kade to preserve proof before each confrontation.",
                }
            ],
            "relationship_events": [
                {
                    "character_a": "Kade",
                    "character_b": "Maya",
                    "axis": "trust",
                    "after": "Maya helps only while Kade preserves consent and evidence integrity",
                    "active_choice": "Kade records proof before asking Maya to take risk.",
                }
            ],
            "agency_debts": [
                {
                    "owner": "Kade",
                    "debt": "Clear the witness trail without framing an innocent person.",
                    "due_window": "next registry confrontation",
                }
            ],
        }
    if pack_id == "female_no_cp_apocalypse":
        return {
            "progression_events": [
                {
                    "event_type": "technique_unlock",
                    "subject": "林鸢",
                    "technique": "代价转化",
                    "cause": "方舟城压力和源初追猎迫使她把痛感转成可计算资源。",
                }
            ],
            "rule_events": [
                {
                    "rule_code": "cost-conversion-rule",
                    "visible_effect": "林鸢能把伤痛、记忆或关系代价转成异能收益",
                    "cost": "代价会留下身体、人格或源初侵蚀后账",
                }
            ],
            "faction_reactions": [
                {
                    "faction": "清道夫",
                    "trigger": "林鸢的代价转化能力被记录",
                    "reaction": "霍沉把她列为高价值追猎对象，同时评估她的弱点",
                    "next_pressure": "追猎、收容和联盟登记压力升级",
                }
            ],
            "relationship_events": [
                {
                    "character_a": "林鸢",
                    "character_b": "霍沉",
                    "axis": "power",
                    "after": "敌对但互为价值观镜像",
                    "active_choice": "林鸢拒绝被清道夫定义为污染源",
                }
            ],
            "agency_debts": [
                {
                    "owner": "林鸢",
                    "debt": "建立非恋爱同盟并守住自我意志",
                    "due_window": "下一次方舟城/源初双压迫节点",
                }
            ],
        }
    if pack_id == "xianxia_upgrade":
        return {
            "progression_events": [
                {
                    "event_type": "resource_gained",
                    "subject": "宁尘",
                    "resource_key": "道种感应",
                    "delta": 1,
                    "cause": "废灵根旧事和黑铁残片触发有限因果感应。",
                }
            ],
            "rule_events": [
                {
                    "rule_code": "realm-resource-rule",
                    "visible_effect": "炼气、筑基、金丹突破必须有资源、瓶颈和反噬痕迹",
                    "cost": "突破会引来宗门关注、身体反噬或资源亏空",
                }
            ],
            "faction_reactions": [
                {
                    "faction": "杂役峰与丹房",
                    "trigger": "宁尘表现出不符合废灵根身份的进步",
                    "reaction": "执事先克扣资源再试探其后手",
                    "next_pressure": "配给账、考核台和秘境名额继续施压",
                }
            ],
            "relationship_events": [
                {
                    "character_a": "宁尘",
                    "character_b": "苏瑶",
                    "axis": "trust",
                    "after": "互相试探的有限同盟",
                    "active_choice": "宁尘只交换可验证情报，不暴露道种核心",
                }
            ],
            "agency_debts": [
                {"owner": "宁尘", "debt": "在秘境大考前证明低位反制不是侥幸", "due_window": "三个月大考前"}
            ],
        }
    if blueprint := _blueprint_for_pack(pack_id):
        is_en = _pack_is_english(pack_id)
        protagonist = blueprint.protagonist_en if is_en else blueprint.protagonist_zh
        target = blueprint.relationship_target_en if is_en else blueprint.relationship_target_zh
        return {
            "progression_events": [
                {
                    "event_type": "resource_gained",
                    "subject": protagonist,
                    "resource_key": blueprint.system_en if is_en else blueprint.system_zh,
                    "delta": 1,
                    "cause": (
                        "The project category pack initialized the core genre engine."
                        if is_en
                        else "项目品类包已初始化核心类型引擎。"
                    ),
                }
            ],
            "rule_events": [
                {
                    "rule_code": blueprint.rule_code,
                    "visible_effect": blueprint.rule_effect_en if is_en else blueprint.rule_effect_zh,
                    "cost": blueprint.rule_cost_en if is_en else blueprint.rule_cost_zh,
                }
            ],
            "faction_reactions": [
                {
                    "faction": blueprint.faction_en if is_en else blueprint.faction_zh,
                    "trigger": (
                        f"{protagonist} acts on the category promise."
                        if is_en
                        else f"{protagonist}开始兑现品类读者承诺。"
                    ),
                    "reaction": blueprint.faction_reaction_en if is_en else blueprint.faction_reaction_zh,
                    "next_pressure": (
                        "Escalate the next obstacle through this faction's concrete interest."
                        if is_en
                        else "下一轮压力必须来自该阵营的具体利益反应。"
                    ),
                }
            ],
            "relationship_events": [
                {
                    "character_a": protagonist,
                    "character_b": target,
                    "axis": "trust",
                    "after": (
                        "conditional alliance with visible cost"
                        if is_en
                        else "带代价的有限协作"
                    ),
                    "active_choice": (
                        f"{protagonist} chooses a concrete tactic instead of drifting with plot pressure."
                        if is_en
                        else f"{protagonist}主动选择策略，而不是被剧情压力推着走。"
                    ),
                }
            ],
            "agency_debts": [
                {
                    "owner": protagonist,
                    "debt": blueprint.agency_debt_en if is_en else blueprint.agency_debt_zh,
                    "due_window": "next category beat" if is_en else "下一组品类节拍内",
                }
            ],
        }
    return None


def _premium_context_seed(pack_id: str) -> dict[str, Any] | None:
    seeds: dict[str, dict[str, Any]] = {
        "qingnang": {
            "world_name": "十七栋镜债都市",
            "system": "青囊因果账",
            "tiers": ["接案", "试探规则", "认账破局", "旧账追索"],
            "starting_tier": "试探规则",
            "protagonist": "林渊",
            "power_structure": "青囊、困魂镜、三族旧契和现实证据链共同限制破局。",
            "volume_title": "十七栋主镜门",
        },
        "english_romantasy": {
            "world_name": "The Two-Court Shadow Crown",
            "system": "Court Bargain and Shadow Sight",
            "tiers": ["Unbound Exile", "Shadow-Sighted", "Court-Bargained", "Crown-Claimant"],
            "starting_tier": "Shadow-Sighted",
            "protagonist": "Nora Chen",
            "power_structure": "Fae power moves through witnessed bargains, court status, agency, and visible price.",
            "volume_title": "The First Shadow Bargain",
        },
        "english_superhero_breaking_point": {
            "world_name": "Registry Pressure City",
            "system": "Reservoir Kinetics",
            "tiers": ["Unregistered", "Controlled Burst", "Public Incident", "Registry Target"],
            "starting_tier": "Controlled Burst",
            "protagonist": "Cole",
            "power_structure": "Public records, measurable load, injuries, and enforcement response bound every upgrade.",
            "volume_title": "Reservoir Incident",
        },
        "english_superhero_progression": {
            "world_name": "Registry Pressure City",
            "system": "Measured Power Progression",
            "tiers": ["Unregistered", "Controlled Burst", "Public Incident", "Registry Target"],
            "starting_tier": "Controlled Burst",
            "protagonist": "Cole",
            "power_structure": "Power growth must be measurable and must change public, family, and faction pressure.",
            "volume_title": "First Public Incident",
        },
        "english_superhero_witness_protocol": {
            "world_name": "Witness Protocol City",
            "system": "Sixty-Second Mimicry",
            "tiers": ["Witness", "Mimic Window", "Evidence Contaminant", "Protocol Breaker"],
            "starting_tier": "Mimic Window",
            "protagonist": "Kade",
            "power_structure": "Mimicry power is constrained by time windows, evidence integrity, surveillance, and consent.",
            "volume_title": "The First False Record",
        },
        "female_no_cp_apocalypse": {
            "world_name": "方舟城末世秩序",
            "system": "代价转化",
            "tiers": ["觉醒", "代价可计量", "方舟城博弈", "源初对抗"],
            "starting_tier": "代价可计量",
            "protagonist": "林鸢",
            "power_structure": "异能收益必须经过痛感、记忆、关系或源初侵蚀的明确代价。",
            "volume_title": "方舟城代价账",
        },
        "xianxia_upgrade": {
            "world_name": "末法宗门",
            "system": "道种修行",
            "tiers": ["炼气", "筑基", "金丹"],
            "starting_tier": "炼气",
            "protagonist": "宁尘",
            "power_structure": "境界突破受灵米、丹药、名额、道种痕迹和宗门反馈约束。",
            "volume_title": "杂役峰道种初动",
        },
    }
    if pack_id in seeds:
        return seeds[pack_id]
    if blueprint := _blueprint_for_pack(pack_id):
        is_en = _pack_is_english(pack_id)
        return {
            "world_name": blueprint.world_en if is_en else blueprint.world_zh,
            "system": blueprint.system_en if is_en else blueprint.system_zh,
            "tiers": list(blueprint.tiers_en if is_en else blueprint.tiers_zh),
            "starting_tier": blueprint.start_tier_en if is_en else blueprint.start_tier_zh,
            "protagonist": blueprint.protagonist_en if is_en else blueprint.protagonist_zh,
            "power_structure": (
                blueprint.power_structure_en if is_en else blueprint.power_structure_zh
            ),
            "volume_title": (
                f"{blueprint.name_en} Opening Engine"
                if is_en
                else f"{blueprint.name_zh}开篇引擎"
            ),
        }
    return None


def _premium_world_spec_for_pack(pack_id: str) -> dict[str, Any] | None:
    seed = _premium_context_seed(pack_id)
    if not seed:
        return None
    return {
        "world_name": seed["world_name"],
        "world_premise": seed["power_structure"],
        "power_system": {
            "name": seed["system"],
            "tiers": seed["tiers"],
            "protagonist_starting_tier": seed["starting_tier"],
            "hard_limits": seed["power_structure"],
        },
        "power_structure": seed["power_structure"],
    }


def _premium_cast_spec_for_pack(pack_id: str) -> dict[str, Any] | None:
    seed = _premium_context_seed(pack_id)
    if not seed:
        return None
    return {
        "protagonist": {
            "name": seed["protagonist"],
            "role": "protagonist",
            "power_tier": seed["starting_tier"],
        }
    }


def _premium_volume_plan_for_pack(pack_id: str) -> list[dict[str, Any]]:
    seed = _premium_context_seed(pack_id)
    if not seed:
        return []
    tiers = list(seed["tiers"])
    target_tier = tiers[1] if len(tiers) > 1 else seed["starting_tier"]
    return [
        {
            "volume_number": 1,
            "title": seed["volume_title"],
            "opening_state": {"protagonist_power_tier": seed["starting_tier"]},
            "volume_resolution": {"protagonist_power_tier": target_tier},
            "volume_goal": seed["power_structure"],
        }
    ]


def _coerce_uuid(value: str) -> UUID | str:
    try:
        return UUID(str(value))
    except ValueError:
        return value


def _read_package_signal(package_root: Path) -> str:
    parts: list[str] = []
    for rel in (
        "README.md",
        "story-bible/series-bible.md",
        "story-bible/rule-ledger.md",
        "story-bible/clue-ledger.md",
        "story-bible/ranking-capability-profile.md",
    ):
        path = package_root / rel
        if path.exists():
            parts.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def _looks_like_qingnang(text: str) -> bool:
    required = ("青囊", "困魂镜", "三族", "林渊")
    return all(token in text for token in required)


def _select_material_pack(
    project_id: str,
    package_text: str,
    *,
    title: str | None = None,
    genre: str | None = None,
    sub_genre: str | None = None,
    language: str | None = None,
) -> tuple[str | None, list[ProjectMaterial]]:
    haystack = " ".join(
        str(part or "")
        for part in (title, genre, sub_genre, language, package_text[:12000])
    )
    lower = haystack.lower()
    if _looks_like_qingnang(haystack):
        return "qingnang", _build_qingnang_pack(project_id)
    if _has_any(lower, ("shadowbound", "fantasy romance", "romantasy", "fae", "chosen one")):
        return "english_romantasy", _build_spec_pack(
            project_id,
            _english_romantasy_pack_spec(),
        )
    if _has_any(lower, ("breaking point", "cole", "reservoir kinetics", "sophie deadline")):
        return "english_superhero_breaking_point", _build_spec_pack(
            project_id,
            _breaking_point_pack_spec(),
        )
    if _has_any(lower, ("witness protocol", "kade", "sixty-second", "maya", "marcus mercer")):
        return "english_superhero_witness_protocol", _build_spec_pack(
            project_id,
            _witness_protocol_pack_spec(),
        )
    if _has_any(lower, ("superhero", "super hero", "urban power")):
        return "english_superhero_progression", _build_spec_pack(
            project_id,
            _generic_superhero_pack_spec(),
        )
    if _has_any(haystack, ("代价之鸢", "末世异能", "无CP", "源初", "方舟城")):
        return "female_no_cp_apocalypse", _build_spec_pack(
            project_id,
            _female_no_cp_pack_spec(),
        )
    if _has_any(haystack, ("道种破虚", "仙侠升级", "宗门逆袭", "道种", "宁尘", "炼气")):
        return "xianxia_upgrade", _build_spec_pack(
            project_id,
            _xianxia_upgrade_pack_spec(),
        )
    category_pack_id = _resolve_category_pack_id(
        title=title,
        genre=genre,
        sub_genre=sub_genre,
        language=language,
    )
    if category_pack_id:
        blueprint = _blueprint_for_pack(category_pack_id)
        if blueprint is not None:
            return category_pack_id, _build_spec_pack(
                project_id,
                _category_pack_spec(blueprint, pack_id=category_pack_id),
            )
    return None, []


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _mat(
    project_id: str,
    material_type: str,
    slug: str,
    name: str,
    summary: str,
    content: dict[str, Any],
    *,
    notes: str = "从本书已锁定 story-bible 提炼，供后续章节引用。",
) -> ProjectMaterial:
    return ProjectMaterial(
        project_id=project_id,
        material_type=material_type,
        slug=slug,
        name=name,
        narrative_summary=summary,
        content_json=content,
        source_library_ids=[],
        variation_notes=notes,
    )


def _build_spec_pack(project_id: str, spec: MaterialPackSpec) -> list[ProjectMaterial]:
    rows: list[ProjectMaterial] = []
    for dimension in PROJECT_MATERIAL_TARGETS:
        for slug, name, summary, content in spec.dimensions.get(dimension, ()):
            rows.append(
                _mat(
                    project_id,
                    dimension,
                    slug,
                    name,
                    summary,
                    {"pack": spec.pack_id, **content},
                )
            )
    return rows


def _s(slug: str, name: str, summary: str, **content: Any) -> MaterialSpec:
    return slug, name, summary, content


def _pack(pack_id: str, dimensions: dict[str, list[MaterialSpec]]) -> MaterialPackSpec:
    return MaterialPackSpec(
        pack_id=pack_id,
        dimensions={key: tuple(value) for key, value in dimensions.items()},
    )


_CATEGORY_BLUEPRINTS: dict[str, CategoryPackBlueprint] = {
    "action-progression": CategoryPackBlueprint(
        category_key="action-progression",
        name_zh="动作升级类",
        name_en="Action Progression",
        protagonist_zh="许燃",
        protagonist_en="Rowan Vale",
        archetype_key="action-progression-state-ledger-lead",
        risk_tolerance="medium",
        system_zh="可计量成长引擎",
        system_en="Measurable Progression Engine",
        world_zh="强压力升级世界",
        world_en="High-Pressure Progression World",
        tiers_zh=("弱势起步", "规则试探", "阶段突破", "高阶反制"),
        tiers_en=("Underdog Start", "Rule Test", "Stage Breakthrough", "Higher-Tier Counterplay"),
        start_tier_zh="规则试探",
        start_tier_en="Rule Test",
        power_structure_zh="成长必须绑定资源、代价、对手反馈和可见战力差。",
        power_structure_en="Growth must be tied to resources, cost, opposition feedback, and visible power delta.",
        rule_code="measurable-progression-delta",
        rule_effect_zh="每章至少改变一项实力、资源、情报或敌方模型。",
        rule_effect_en="Each chapter changes power, resources, intelligence, or the enemy model.",
        rule_cost_zh="升级不能免费，必须留下消耗、暴露、反噬或新敌意。",
        rule_cost_en="Upgrades are not free; they leave depletion, exposure, backlash, or new hostility.",
        faction_zh="资源把门势力",
        faction_en="Resource Gatekeeper Faction",
        faction_reaction_zh="主角每次越级都会触发配给、监视、拉拢或围剿。",
        faction_reaction_en="Each over-tier win triggers rationing, surveillance, recruitment, or containment.",
        relationship_target_zh="条件盟友",
        relationship_target_en="Conditional Ally",
        agency_debt_zh="证明成长不是数值飙升，而是能持续改变局面。",
        agency_debt_en="Prove progression is not number inflation but repeated state control.",
    ),
    "relationship-driven": CategoryPackBlueprint(
        category_key="relationship-driven",
        name_zh="关系情感类",
        name_en="Relationship Driven",
        protagonist_zh="江晚",
        protagonist_en="Mara Vale",
        archetype_key="relationship-boundary-choice-lead",
        risk_tolerance="medium",
        system_zh="关系债务与边界选择",
        system_en="Relationship Debt and Boundary Choice",
        world_zh="关系即权力世界",
        world_en="Relationship-as-Power World",
        tiers_zh=("误判", "试探", "并肩", "再选择"),
        tiers_en=("Misread", "Testing", "Acting Together", "Choosing Again"),
        start_tier_zh="试探",
        start_tier_en="Testing",
        power_structure_zh="吸引、信任、秘密、背叛和边界都必须改变行动权。",
        power_structure_en="Attraction, trust, secrets, betrayal, and boundaries must alter agency.",
        rule_code="relationship-state-delta",
        rule_effect_zh="每个情感节点必须改变信任、边界、债务或共同目标。",
        rule_effect_en="Every emotional beat changes trust, boundary, debt, or shared objective.",
        rule_cost_zh="亲密不能只给安慰，必须带来暴露、承诺、失控或反选择。",
        rule_cost_en="Intimacy cannot be comfort-only; it must create exposure, commitment, loss of control, or counter-choice.",
        faction_zh="关系压力网",
        faction_en="Relationship Pressure Network",
        faction_reaction_zh="亲友、旧账、身份和舆论会根据两人的选择重新施压。",
        faction_reaction_en="Family, old debts, status, and public pressure respond to the pair's choices.",
        relationship_target_zh="强张力对象",
        relationship_target_en="High-Tension Counterpart",
        agency_debt_zh="在情感吸引中保留选择权，并让关系推动外部剧情。",
        agency_debt_en="Keep agency inside attraction and make the relationship move the external plot.",
    ),
    "suspense-mystery": CategoryPackBlueprint(
        category_key="suspense-mystery",
        name_zh="悬疑推理类",
        name_en="Suspense Mystery",
        protagonist_zh="沈砚",
        protagonist_en="Elise Ward",
        archetype_key="evidence-led-misdirection-investigator",
        risk_tolerance="low",
        system_zh="线索链与误导账",
        system_en="Clue Chain and Misdirection Ledger",
        world_zh="证据污染世界",
        world_en="Evidence-Contamination World",
        tiers_zh=("异常入口", "证据试探", "嫌疑翻转", "真相定价"),
        tiers_en=("Anomaly Entry", "Evidence Test", "Suspect Reversal", "Truth Price"),
        start_tier_zh="证据试探",
        start_tier_en="Evidence Test",
        power_structure_zh="线索、证言、物证、误导和现实程序共同限制破案。",
        power_structure_en="Clues, testimony, physical evidence, misdirection, and procedure constrain the case.",
        rule_code="clue-fair-play-chain",
        rule_effect_zh="每个关键反转必须能回溯到已展示线索。",
        rule_effect_en="Every major reversal must trace back to clues already shown.",
        rule_cost_zh="揭开真相必须损失安全、关系、时间窗口或错误假设。",
        rule_cost_en="Truth costs safety, relationships, time windows, or cherished assumptions.",
        faction_zh="隐藏真相的利益方",
        faction_en="Truth-Concealing Interest Group",
        faction_reaction_zh="调查推进后，对方会销毁证据、制造替罪羊或投放新误导。",
        faction_reaction_en="As the case advances, they destroy evidence, create scapegoats, or plant misdirection.",
        relationship_target_zh="证据合作者",
        relationship_target_en="Evidence Partner",
        agency_debt_zh="在公平线索下完成误导与反转，而不是靠作者补丁破案。",
        agency_debt_en="Deliver misdirection and reversal through fair clues, not authorial patches.",
    ),
    "strategy-worldbuilding": CategoryPackBlueprint(
        category_key="strategy-worldbuilding",
        name_zh="策略世界观类",
        name_en="Strategy Worldbuilding",
        protagonist_zh="顾衡",
        protagonist_en="Adrien Vale",
        archetype_key="systems-strategy-worldbuilder",
        risk_tolerance="medium",
        system_zh="势力账与资源博弈",
        system_en="Faction Ledger and Resource Strategy",
        world_zh="多势力战略世界",
        world_en="Multi-Faction Strategic World",
        tiers_zh=("局部生存", "资源置换", "联盟成型", "格局改写"),
        tiers_en=("Local Survival", "Resource Exchange", "Alliance Formation", "Map Rewritten"),
        start_tier_zh="资源置换",
        start_tier_en="Resource Exchange",
        power_structure_zh="战争、制度、贸易、科技或王权都必须有资源约束和势力反馈。",
        power_structure_en="War, institutions, trade, technology, or sovereignty must be bound by resources and faction response.",
        rule_code="faction-resource-feedback",
        rule_effect_zh="任何战略选择都必须改变地图、资源、联盟或敌方判断。",
        rule_effect_en="Every strategic choice changes map position, resources, alliances, or enemy judgment.",
        rule_cost_zh="胜利会制造补给缺口、政治债、暴露风险或更强对手。",
        rule_cost_en="Victory creates supply gaps, political debt, exposure risk, or stronger opposition.",
        faction_zh="主导秩序阵营",
        faction_en="Dominant Order Faction",
        faction_reaction_zh="既得利益者会通过封锁、离间、征召或制度反扑回应。",
        faction_reaction_en="Incumbents respond through blockade, division, conscription, or institutional backlash.",
        relationship_target_zh="利益盟友",
        relationship_target_en="Interest-Bound Ally",
        agency_debt_zh="让世界不是背景板，而是每次选择都能反推剧情的机器。",
        agency_debt_en="Make the world a machine that pushes back on every choice, not scenery.",
    ),
    "esports-competition": CategoryPackBlueprint(
        category_key="esports-competition",
        name_zh="电竞竞技类",
        name_en="Esports Competition",
        protagonist_zh="周野",
        protagonist_en="Kai Mercer",
        archetype_key="competition-meta-adaptation-lead",
        risk_tolerance="medium",
        system_zh="训练-版本-赛点循环",
        system_en="Training-Meta-Matchpoint Loop",
        world_zh="版本更迭竞技世界",
        world_en="Patch-Shift Competition World",
        tiers_zh=("替补边缘", "战术试训", "关键首发", "赛区强敌"),
        tiers_en=("Bench Edge", "Tactical Trial", "Starter Pressure", "League Threat"),
        start_tier_zh="战术试训",
        start_tier_en="Tactical Trial",
        power_structure_zh="实力来自训练负荷、版本理解、团队协同和临场决策。",
        power_structure_en="Strength comes from training load, meta reading, team synergy, and in-match decisions.",
        rule_code="match-state-adaptation",
        rule_effect_zh="每场训练或比赛必须改变版本理解、角色定位或队伍信任。",
        rule_effect_en="Each scrim or match changes meta understanding, role identity, or team trust.",
        rule_cost_zh="高光不能白给，必须消耗体力、暴露套路、制造舆论或队内矛盾。",
        rule_cost_en="Highlights are not free; they cost stamina, reveal tactics, trigger public pressure, or strain teammates.",
        faction_zh="俱乐部与赛区舆论",
        faction_en="Club and League Public Pressure",
        faction_reaction_zh="教练、资本、粉丝和对手会根据战绩快速改变资源分配。",
        faction_reaction_en="Coaches, capital, fans, and rivals rapidly reallocate resources after results.",
        relationship_target_zh="队内核心",
        relationship_target_en="Team Core",
        agency_debt_zh="用可看懂的战术、训练和赛点兑现竞技爽感。",
        agency_debt_en="Deliver competitive payoff through legible tactics, training, and matchpoints.",
    ),
    "female-growth-ncp": CategoryPackBlueprint(
        category_key="female-growth-ncp",
        name_zh="女性成长无CP类",
        name_en="Female Growth No-CP",
        protagonist_zh="林澈",
        protagonist_en="Vera Lin",
        archetype_key="agency-first-nonromantic-growth-lead",
        risk_tolerance="medium",
        system_zh="选择权与非恋爱同盟账",
        system_en="Agency and Nonromantic Alliance Ledger",
        world_zh="选择权重建世界",
        world_en="Agency-Rebuilding World",
        tiers_zh=("被定义", "夺回定价", "同盟重组", "自我立法"),
        tiers_en=("Defined by Others", "Repricing Herself", "Alliance Rebuilt", "Self-Legislated"),
        start_tier_zh="夺回定价",
        start_tier_en="Repricing Herself",
        power_structure_zh="成长来自边界、资源、技能、同盟和旧债清算，不靠恋爱救援。",
        power_structure_en="Growth comes from boundaries, resources, skills, alliances, and old-debt accounting, not romantic rescue.",
        rule_code="agency-without-romance",
        rule_effect_zh="每次成长必须扩大主角选择权或压缩压迫方选择权。",
        rule_effect_en="Every growth beat expands the lead's options or narrows the oppressor's options.",
        rule_cost_zh="独立不是口号，必须付出关系重估、资源代价或身份压力。",
        rule_cost_en="Independence is not a slogan; it costs relationship reassessment, resources, or status pressure.",
        faction_zh="旧秩序压迫网",
        faction_en="Old-Order Pressure Network",
        faction_reaction_zh="旧关系和制度会用恩情、羞辱、利益和规则重新夺权。",
        faction_reaction_en="Old relationships and institutions try to retake control through favors, shame, incentives, and rules.",
        relationship_target_zh="非恋爱同盟者",
        relationship_target_en="Nonromantic Ally",
        agency_debt_zh="让女主的每次选择都可见、可付价、可改变局势。",
        agency_debt_en="Make every choice visible, costly, and capable of changing the situation.",
    ),
    "base-building": CategoryPackBlueprint(
        category_key="base-building",
        name_zh="基建经营类",
        name_en="Base Building",
        protagonist_zh="陆青舟",
        protagonist_en="Mira Chen",
        archetype_key="resource-flywheel-builder-lead",
        risk_tolerance="medium",
        system_zh="资源-人口-设施飞轮",
        system_en="Resource-Population-Infrastructure Flywheel",
        world_zh="稀缺经营世界",
        world_en="Scarcity Management World",
        tiers_zh=("缺口求生", "生产闭环", "制度成型", "外部扩张"),
        tiers_en=("Gap Survival", "Production Loop", "Institution Formed", "External Expansion"),
        start_tier_zh="生产闭环",
        start_tier_en="Production Loop",
        power_structure_zh="基地成长必须受库存、人口、技术、信任和外部威胁限制。",
        power_structure_en="Base growth is constrained by inventory, population, technology, trust, and external threats.",
        rule_code="visible-resource-flywheel",
        rule_effect_zh="每次建设必须改变库存、产能、人口结构或外部议价权。",
        rule_effect_en="Each build changes inventory, capacity, population structure, or external bargaining power.",
        rule_cost_zh="扩张会制造维护成本、治理难题、资源短板或被觊觎风险。",
        rule_cost_en="Expansion creates maintenance costs, governance problems, shortages, or predation risk.",
        faction_zh="外部掠夺与贸易势力",
        faction_en="External Raiders and Trade Faction",
        faction_reaction_zh="基地越有产出，外部越会通过交易、勒索、渗透或战争介入。",
        faction_reaction_en="As output rises, outsiders intervene through trade, extortion, infiltration, or war.",
        relationship_target_zh="技术/后勤搭档",
        relationship_target_en="Technical or Logistics Partner",
        agency_debt_zh="把经营爽点写成资源闭环，而不是凭空变富。",
        agency_debt_en="Turn management payoff into a resource loop, not sudden wealth.",
    ),
    "eastern-aesthetic": CategoryPackBlueprint(
        category_key="eastern-aesthetic",
        name_zh="东方美学类",
        name_en="Eastern Aesthetic",
        protagonist_zh="云岫",
        protagonist_en="Lin Yun",
        archetype_key="image-rule-fantasy-investigator",
        risk_tolerance="low",
        system_zh="意象规则与志怪因果",
        system_en="Image-Rule and Strange-Tale Causality",
        world_zh="东方志怪意象世界",
        world_en="Eastern Strange-Tale Image World",
        tiers_zh=("见异", "识象", "破禁", "承因果"),
        tiers_en=("Seeing the Strange", "Reading the Image", "Breaking the Taboo", "Bearing Causality"),
        start_tier_zh="识象",
        start_tier_en="Reading the Image",
        power_structure_zh="山水、器物、民俗、禁忌和因果必须形成可推理规则。",
        power_structure_en="Landscape, objects, folklore, taboo, and causality must form legible rules.",
        rule_code="image-causality-rule",
        rule_effect_zh="每个东方意象都要承担线索、规则、情绪或代价功能。",
        rule_effect_en="Every Eastern image must carry clue, rule, emotion, or cost function.",
        rule_cost_zh="破禁必须付出名声、记忆、身体、关系或因果后账。",
        rule_cost_en="Breaking taboo costs reputation, memory, body, relationship, or karmic debt.",
        faction_zh="守禁旧族",
        faction_en="Taboo-Keeping Old Clan",
        faction_reaction_zh="守禁者会用家法、传说、山水禁制和半真线索阻拦。",
        faction_reaction_en="Keepers obstruct through clan law, legend, landscape taboo, and half-true clues.",
        relationship_target_zh="知情引路人",
        relationship_target_en="Informed Guide",
        agency_debt_zh="让美学服务规则和剧情，不停留在漂亮描写。",
        agency_debt_en="Make aesthetics serve rules and plot, not decorative prose.",
    ),
    "default": CategoryPackBlueprint(
        category_key="default",
        name_zh="通用品类",
        name_en="Default Commercial Genre",
        protagonist_zh="主角",
        protagonist_en="The Protagonist",
        archetype_key="commercial-state-delta-lead",
        risk_tolerance="medium",
        system_zh="商业类型状态引擎",
        system_en="Commercial Genre State Engine",
        world_zh="可持续连载世界",
        world_en="Sustainable Serial World",
        tiers_zh=("入口压力", "规则确认", "关系/资源变化", "阶段回报"),
        tiers_en=("Entry Pressure", "Rule Confirmed", "Relationship/Resource Shift", "Stage Payoff"),
        start_tier_zh="规则确认",
        start_tier_en="Rule Confirmed",
        power_structure_zh="章节必须围绕目标、阻力、选择、代价和状态变化推进。",
        power_structure_en="Chapters must advance through goal, opposition, choice, cost, and state change.",
        rule_code="commercial-state-delta",
        rule_effect_zh="每章产生一个可记录的剧情、人物、关系或世界状态变化。",
        rule_effect_en="Each chapter creates a recordable plot, character, relationship, or world-state change.",
        rule_cost_zh="解决问题不能归零，必须留下下一章的新压力。",
        rule_cost_en="Resolution cannot reset; it must leave new pressure for the next chapter.",
        faction_zh="核心阻力方",
        faction_en="Core Opposition Force",
        faction_reaction_zh="阻力方会根据主角行为升级、转向或暂时撤退。",
        faction_reaction_en="Opposition escalates, pivots, or retreats based on the lead's action.",
        relationship_target_zh="关键关系对象",
        relationship_target_en="Key Relationship Counterpart",
        agency_debt_zh="保持清晰读者承诺，并持续兑现阶段回报。",
        agency_debt_en="Maintain a clear reader promise and keep delivering staged payoff.",
    ),
}


def _pack_is_english(pack_id: str) -> bool:
    return pack_id.startswith("english_") or pack_id.endswith("_en")


def _base_category_key(pack_id: str) -> str | None:
    if not pack_id.startswith("category_"):
        return None
    raw = pack_id.removeprefix("category_")
    if raw.endswith("_zh") or raw.endswith("_en"):
        raw = raw[:-3]
    return raw.replace("_", "-")


def _blueprint_for_pack(pack_id: str) -> CategoryPackBlueprint | None:
    category_key = _base_category_key(pack_id)
    if not category_key:
        return None
    return _CATEGORY_BLUEPRINTS.get(category_key)


def _localized_category_pack_id(category_key: str, language: str | None) -> str:
    lang = "en" if str(language or "").lower().startswith("en") else "zh"
    safe_key = category_key if category_key in _CATEGORY_BLUEPRINTS else "default"
    return f"category_{safe_key.replace('-', '_')}_{lang}"


def _resolve_category_pack_id(
    *,
    title: str | None,
    genre: str | None,
    sub_genre: str | None,
    language: str | None,
) -> str | None:
    signal = " ".join(str(part or "").strip() for part in (title, genre, sub_genre)).strip()
    if not signal:
        return None
    try:
        from bestseller.services.novel_categories import resolve_novel_category

        category = resolve_novel_category(genre or signal, sub_genre)
        category_key = category.key
        if category_key == "default":
            category_key = _fallback_category_key(signal)
    except Exception:
        category_key = _fallback_category_key(signal)
    if category_key not in _CATEGORY_BLUEPRINTS:
        category_key = "default"
    return _localized_category_pack_id(category_key, language)


def _fallback_category_key(signal: str) -> str:
    lower = signal.lower()
    keyword_map: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("action-progression", ("动作", "仙", "修仙", "玄幻", "末日", "异能", "升级", "litrpg", "progression", "cultivation", "superhero")),
        ("relationship-driven", ("关系情感", "情感", "言情", "浪漫", "宫斗", "romance", "romantasy", "harem")),
        ("suspense-mystery", ("悬疑", "推理", "怪谈", "惊悚", "灵异", "驱魔", "horror", "mystery", "thriller", "detective")),
        ("strategy-worldbuilding", ("策略", "世界观", "权谋", "历史", "争霸", "科幻", "机甲", "战争", "黑科技", "strategy", "epic", "space", "military", "scifi", "sci-fi")),
        ("esports-competition", ("竞技", "玩家", "电竞", "游戏", "esport", "competition")),
        ("female-growth-ncp", ("女性成长无cp", "无cp", "大女主", "女帝", "female growth")),
        ("base-building", ("基建", "经营", "种田", "base", "tycoon")),
        ("eastern-aesthetic", ("东方美学", "国风", "志怪", "水墨", "eastern")),
    )
    for category_key, needles in keyword_map:
        if any(needle in lower for needle in needles):
            return category_key
    return "default"


def _category_pack_spec(blueprint: CategoryPackBlueprint, *, pack_id: str) -> MaterialPackSpec:
    is_en = _pack_is_english(pack_id)
    base = blueprint.category_key
    name = blueprint.name_en if is_en else blueprint.name_zh
    protagonist = blueprint.protagonist_en if is_en else blueprint.protagonist_zh
    target = blueprint.relationship_target_en if is_en else blueprint.relationship_target_zh
    system = blueprint.system_en if is_en else blueprint.system_zh
    world = blueprint.world_en if is_en else blueprint.world_zh
    faction = blueprint.faction_en if is_en else blueprint.faction_zh
    tiers = list(blueprint.tiers_en if is_en else blueprint.tiers_zh)
    rule_effect = blueprint.rule_effect_en if is_en else blueprint.rule_effect_zh
    rule_cost = blueprint.rule_cost_en if is_en else blueprint.rule_cost_zh
    power_structure = blueprint.power_structure_en if is_en else blueprint.power_structure_zh

    if is_en:
        return _pack(
            pack_id,
            {
                "world_settings": [
                    _s(f"{base}-promise-world", f"{name} Promise World", f"{world} locks the reader promise into repeatable pressure, payoff, and consequence.", category=base, promise=name),
                    _s(f"{base}-state-ledger-world", "State Ledger World", "Plot, power, relationships, factions, and resources persist after each chapter instead of resetting.", category=base, rule="persistent state"),
                    _s(f"{base}-escalation-clock-world", "Escalation Clock World", "The first volume needs a visible clock that turns local wins into larger category pressure.", category=base, clock="volume-level escalation"),
                    _s(f"{base}-reader-contract-boundary", "Reader Contract Boundary", f"The story must not abandon {name} mechanics when pressure rises.", category=base, boundary="genre promise"),
                ],
                "factions": [
                    _s(f"{base}-core-faction", faction, blueprint.faction_reaction_en, goal="protect interest and force adaptation"),
                    _s(f"{base}-rival-counterforce", "Adaptive Rival Counterforce", f"A rival reads {protagonist}'s method and changes tactics after every visible win.", role="adaptive opposition"),
                    _s(f"{base}-resource-gatekeeper", "Resource Gatekeeper", "Controls access to information, money, territory, status, time, or tools required by the genre engine.", role="scarcity pressure"),
                    _s(f"{base}-public-pressure-network", "Public Pressure Network", "Audience, rumor, procedure, family, market, or institution converts private choices into public stakes.", role="social consequence"),
                ],
                "locale_templates": [
                    _s(f"{base}-opening-pressure-site", "Opening Pressure Site", f"Launch scene that makes {protagonist}'s problem and the category promise visible fast.", use="opening hook"),
                    _s(f"{base}-rule-test-arena", "Rule Test Arena", f"A bounded place to test {system} with one visible result and one cost.", use="rule proof"),
                    _s(f"{base}-negotiation-threshold", "Negotiation Threshold", "A threshold space where offers, refusals, witnesses, and hidden prices collide.", use="choice pressure"),
                    _s(f"{base}-public-scoreboard", "Public Scoreboard", "A public setting that turns progress into reputation, danger, or measurable ranking.", use="external feedback"),
                    _s(f"{base}-hidden-archive", "Hidden Archive", "A repository of old records, secrets, data, or memory that can answer one question and open another.", use="long mystery"),
                    _s(f"{base}-aftermath-site", "Aftermath Site", "A quiet location where body cost, relationship cost, or faction reaction becomes the next constraint.", use="consequence"),
                ],
                "power_systems": [
                    _s(f"{base}-core-system", system, power_structure, tiers=tiers, starting_tier=blueprint.start_tier_en),
                    _s(f"{base}-state-delta-rule", "State Delta Rule", rule_effect, fields=["before", "action", "after", "new pressure"]),
                    _s(f"{base}-cost-accounting-rule", "Cost Accounting Rule", rule_cost, fields=["price", "carrier", "visibility", "callback"]),
                    _s(f"{base}-faction-feedback-rule", "Faction Feedback Rule", "A faction must learn, react, misread, or counter after a meaningful protagonist move.", use="smart opposition"),
                    _s(f"{base}-bounded-reveal-rule", "Bounded Reveal Rule", "Reveals answer one practical question and create a sharper next decision.", use="serial hook discipline"),
                ],
                "character_archetypes": [
                    _s(f"{base}-lead-archetype", f"{name} Lead", f"{protagonist} advances through concrete choices under the {name} promise.", function="agency carrier"),
                    _s(f"{base}-pressure-opponent", "Pressure Opponent", "The opponent expresses the category's central pressure instead of generic hostility.", function="genre-specific conflict"),
                    _s(f"{base}-conditional-ally", "Conditional Ally", "Help comes with terms, timing, risk, or incomplete information.", function="non-static support"),
                    _s(f"{base}-cost-witness", "Cost Witness", "A witness makes the consequence of each win visible to readers and other characters.", function="cost externalization"),
                ],
                "character_templates": [
                    _s(f"{base}-protagonist", protagonist, f"Lead whose method must update {system} and the story state every chapter.", role="protagonist"),
                    _s(f"{base}-relationship-target", target, "Key counterpart who changes trust, leverage, and tactical options.", role="relationship pressure"),
                    _s(f"{base}-faction-face", faction, f"The face of organized pressure against {protagonist}.", role="faction pressure"),
                    _s(f"{base}-rival", "Adaptive Rival", "A rival who loses information before losing status, forcing smarter next pressure.", role="rival"),
                    _s(f"{base}-gatekeeper", "Gatekeeper Mentor", "Gives partial access, never complete solution, and has a separate agenda.", role="limited guide"),
                    _s(f"{base}-witness", "Consequence Witness", "Tracks the cost of victory and carries public or emotional fallout.", role="consequence carrier"),
                ],
                "plot_patterns": [
                    _s(f"{base}-promise-state-loop", "Promise-State Loop", "Open with a genre promise, force a choice, update state, and leave a sharper problem.", rule="serial engine"),
                    _s(f"{base}-pressure-reversal-payoff", "Pressure-Reversal-Payoff", "Pressure escalates, the lead exploits a known rule, payoff lands, and cost follows.", rule="commercial beat"),
                    _s(f"{base}-cost-callback", "Cost Callback", "A cost paid in one chapter returns as leverage, obstacle, or relationship shift later.", rule="continuity"),
                    _s(f"{base}-faction-response-escalation", "Faction Response Escalation", "Local success triggers smarter faction response rather than repeating the same obstacle.", rule="adaptive antagonist"),
                ],
                "scene_templates": [
                    _s(f"{base}-opening-contract-scene", "Opening Contract Scene", f"Show {protagonist}, immediate pressure, the category promise, and a concrete next want.", beats=["pressure", "want", "promise", "hook"]),
                    _s(f"{base}-first-rule-test", "First Rule Test", f"Test {system} with a limited action, visible result, and cost.", beats=["hypothesis", "test", "cost"]),
                    _s(f"{base}-choice-negotiation", "Choice Negotiation", "Someone offers help or pressure with terms that change future options.", beats=["offer", "terms", "countermove"]),
                    _s(f"{base}-public-reversal", "Public Reversal", "A public setback becomes a tactical reversal with witnesses and fallout.", beats=["humiliation", "exploit", "fallout"]),
                    _s(f"{base}-cost-aftermath", "Cost Aftermath", "The scene after a win records body, resource, relationship, or faction cost.", beats=["pain", "ledger", "new constraint"]),
                    _s(f"{base}-state-hook-ending", "State Hook Ending", "End after a concrete state update and a newly exposed problem.", beats=["delta", "reveal", "next pressure"]),
                ],
                "device_templates": [
                    _s(f"{base}-state-ledger-device", "State Ledger Device", "A file, board, contract, ranking, ledger, or map that externalizes progress.", function="progress tracking"),
                    _s(f"{base}-threshold-token", "Threshold Token", "A key object that grants access while changing obligation.", function="access with price"),
                    _s(f"{base}-evidence-prop", "Evidence Prop", "A visible clue, clip, receipt, scar, or record that makes the genre rule concrete.", function="proof"),
                    _s(f"{base}-resource-key", "Resource Key", "A scarce item or capability that forces tradeoffs.", function="scarcity"),
                    _s(f"{base}-faction-signal", "Faction Signal", "A badge, message, rumor, seal, or public mark that shows faction reaction.", function="pressure marker"),
                ],
                "thematic_motifs": [
                    _s(f"{base}-choice-and-price", "Choice and Price", "Agency becomes meaningful only when the price is visible.", symbols=["choice", "price"]),
                    _s(f"{base}-state-and-memory", "State and Memory", "The world remembers wins, lies, losses, and promises.", symbols=["ledger", "scar"]),
                    _s(f"{base}-threshold-and-return", "Threshold and Return", "Crossing a door, rank, pact, or map line changes what can be undone.", symbols=["door", "line"]),
                    _s(f"{base}-mask-and-proof", "Mask and Proof", "Identity claims must be tested through action and evidence.", symbols=["mask", "proof"]),
                ],
                "emotion_arcs": [
                    _s(f"{base}-pressure-to-choice", "Pressure to Choice", "Emotion moves from being pressured to making a costly active choice.", beats=["pressure", "calculation", "choice"]),
                    _s(f"{base}-win-to-cost", "Win to Cost", "Payoff is followed by the emotional weight of what changed.", beats=["payoff", "realization", "burden"]),
                    _s(f"{base}-mistrust-to-working-trust", "Mistrust to Working Trust", "Trust grows only after risk-bearing action, not reassurance.", beats=["doubt", "risk", "limited trust"]),
                ],
                "dialogue_styles": [
                    _s(f"{base}-tactical-dialogue", "Tactical Dialogue", "Characters speak in wants, constraints, terms, and consequences.", style="specific pressure"),
                    _s(f"{base}-subtext-dialogue", "Subtext Dialogue", "Surface lines carry hidden debt, attraction, suspicion, or calculation.", style="layered subtext"),
                    _s(f"{base}-institutional-dialogue", "Institutional Dialogue", "Authority figures use procedure, numbers, contracts, ranks, or public judgment.", style="formal pressure"),
                ],
                "anti_cliche_patterns": [
                    _s(f"{base}-no-vague-progress", "No Vague Progress", "Do not claim the story advanced unless a state variable changed.", avoid="empty motion"),
                    _s(f"{base}-no-free-win", "No Free Win", rule_cost, avoid="costless solution"),
                    _s(f"{base}-no-category-drift", "No Category Drift", f"Do not replace {name} mechanics with unrelated tropes.", avoid="reader-promise break"),
                ],
                "real_world_references": [
                    _s(f"{base}-beat-sheet-reference", "Beat Sheet Reference", "Track scene goal, opposition, tactic, turn, cost, and hook.", methods=["goal", "turn", "hook"]),
                    _s(f"{base}-institutional-logic", "Institutional Logic", "Use rules, incentives, paperwork, rankings, logistics, or public records to ground pressure.", methods=["incentives", "records"]),
                    _s(f"{base}-continuity-ledger", "Continuity Ledger", "Maintain before/after state for power, relationship, faction, resource, and clue.", methods=["before", "after"]),
                ],
            },
        )

    return _pack(
        pack_id,
        {
            "world_settings": [
                _s(f"{base}-promise-world", f"{name}读者承诺世界", f"{world}把品类承诺锁成可重复的压力、回报和后果。", category=base, promise=name),
                _s(f"{base}-state-ledger-world", "状态账持续世界", "剧情、能力、关系、阵营和资源在每章后持续存在，不允许重置。", category=base, rule="状态持续"),
                _s(f"{base}-escalation-clock-world", "卷级升级时钟世界", "第一卷必须有可见时钟，把局部胜利推向更大品类压力。", category=base, clock="卷级升级"),
                _s(f"{base}-reader-contract-boundary", "读者契约边界", f"压力上升时不得抛弃{name}的核心机制。", category=base, boundary="品类承诺"),
            ],
            "factions": [
                _s(f"{base}-core-faction", faction, blueprint.faction_reaction_zh, goal="守住利益并迫使主角适应"),
                _s(f"{base}-rival-counterforce", "适应型竞争对手", f"对手会读取{protagonist}的方法，并在每次可见胜利后调整战术。", role="适应型反派"),
                _s(f"{base}-resource-gatekeeper", "资源把门人", "控制该类型所需的信息、金钱、地盘、身份、时间或工具。", role="稀缺压力"),
                _s(f"{base}-public-pressure-network", "公开压力网络", "舆论、流程、亲友、市场或制度把私人选择转成公开代价。", role="社会后果"),
            ],
            "locale_templates": [
                _s(f"{base}-opening-pressure-site", "开篇压力场", f"快速呈现{protagonist}的困境和品类承诺。", use="开篇钩子"),
                _s(f"{base}-rule-test-arena", "规则试验场", f"用一个可见结果和一个代价测试{system}。", use="规则证明"),
                _s(f"{base}-negotiation-threshold", "谈判门槛场", "让请求、拒绝、见证和隐藏价格集中碰撞。", use="选择压力"),
                _s(f"{base}-public-scoreboard", "公开计分场", "把进展转成名声、危险或可测排名。", use="外部反馈"),
                _s(f"{base}-hidden-archive", "隐藏档案场", "旧记录、秘密、数据或记忆回答一个问题，同时打开新问题。", use="长线谜题"),
                _s(f"{base}-aftermath-site", "后果结算场", "身体代价、关系代价或阵营反应在这里变成下一步限制。", use="后果"),
            ],
            "power_systems": [
                _s(f"{base}-core-system", system, power_structure, tiers=tiers, starting_tier=blueprint.start_tier_zh),
                _s(f"{base}-state-delta-rule", "状态变化规则", rule_effect, fields=["之前", "行动", "之后", "新压力"]),
                _s(f"{base}-cost-accounting-rule", "代价记账规则", rule_cost, fields=["代价", "承担者", "可见性", "回调"]),
                _s(f"{base}-faction-feedback-rule", "阵营反馈规则", "主角完成有效行动后，至少一个阵营必须学习、反应、误判或反制。", use="聪明阻力"),
                _s(f"{base}-bounded-reveal-rule", "有限揭示规则", "每次揭示只回答一个实用问题，并制造更尖锐的下一选择。", use="连载钩子纪律"),
            ],
            "character_archetypes": [
                _s(f"{base}-lead-archetype", f"{name}主角", f"{protagonist}通过{name}承诺下的具体选择推进。", function="主动性载体"),
                _s(f"{base}-pressure-opponent", "品类压力对手", "对手表达该品类的核心压力，而不是泛泛敌意。", function="类型冲突"),
                _s(f"{base}-conditional-ally", "条件盟友", "帮助总带条件、时限、风险或不完整信息。", function="非静态支持"),
                _s(f"{base}-cost-witness", "代价见证者", "让每次胜利的后果被读者和角色同时看见。", function="代价外化"),
            ],
            "character_templates": [
                _s(f"{base}-protagonist", protagonist, f"主角的方法必须持续更新{system}和故事状态。", role="protagonist"),
                _s(f"{base}-relationship-target", target, "关键关系对象，负责改变信任、筹码和行动选择。", role="关系压力"),
                _s(f"{base}-faction-face", faction, f"组织化压力在{protagonist}面前的具体面孔。", role="阵营压力"),
                _s(f"{base}-rival", "适应型对手", "先输信息再输地位，逼出更聪明的下一轮压力。", role="竞争者"),
                _s(f"{base}-gatekeeper", "有限导师/把门人", "只给局部入口，不给完整答案，并拥有独立利益。", role="有限引导"),
                _s(f"{base}-witness", "后果见证者", "记录胜利代价并承担公开或情绪余波。", role="后果承载"),
            ],
            "plot_patterns": [
                _s(f"{base}-promise-state-loop", "承诺-选择-状态循环", "以品类承诺开场，逼出选择，更新状态，留下更尖锐问题。", rule="连载引擎"),
                _s(f"{base}-pressure-reversal-payoff", "压力-反转-回报", "压力升级，主角利用已知规则反制，回报落地，代价跟进。", rule="商业节拍"),
                _s(f"{base}-cost-callback", "代价回调", "前文付出的代价必须在后文变成筹码、阻碍或关系变化。", rule="连续性"),
                _s(f"{base}-faction-response-escalation", "阵营反馈升级", "局部成功触发更聪明的阵营反应，而不是重复同一障碍。", rule="适应型反派"),
            ],
            "scene_templates": [
                _s(f"{base}-opening-contract-scene", "开篇契约场景", f"呈现{protagonist}、即时压力、品类承诺和具体下一目标。", beats=["压力", "欲望", "承诺", "钩子"]),
                _s(f"{base}-first-rule-test", "第一次规则试探", f"用有限行动测试{system}，给出可见结果和代价。", beats=["假设", "试探", "代价"]),
                _s(f"{base}-choice-negotiation", "选择谈判场景", "有人给出帮助或压力，并附带会改变未来选择的条件。", beats=["提议", "条件", "反制"]),
                _s(f"{base}-public-reversal", "公开反转场景", "公开挫败变成战术反转，并留下见证和余波。", beats=["受压", "利用", "余波"]),
                _s(f"{base}-cost-aftermath", "代价后果场景", "胜利之后记录身体、资源、关系或阵营代价。", beats=["疼痛", "记账", "新限制"]),
                _s(f"{base}-state-hook-ending", "状态钩子结尾", "以明确状态更新和新暴露问题收束。", beats=["变化", "揭示", "新压力"]),
            ],
            "device_templates": [
                _s(f"{base}-state-ledger-device", "状态账装置", "文件、白板、契约、排名、账本或地图，把进展外化。", function="进度追踪"),
                _s(f"{base}-threshold-token", "门槛信物", "给出进入资格，同时改变义务。", function="带代价的入口"),
                _s(f"{base}-evidence-prop", "证据化道具", "线索、影像、收据、伤痕或记录，让品类规则具体化。", function="证明"),
                _s(f"{base}-resource-key", "稀缺资源钥匙", "强迫主角做交换和取舍的稀缺物或能力。", function="稀缺"),
                _s(f"{base}-faction-signal", "阵营信号物", "徽记、消息、传闻、印章或公开标记，显示阵营反应。", function="压力标记"),
            ],
            "thematic_motifs": [
                _s(f"{base}-choice-and-price", "选择与代价", "只有代价可见，主动性才有意义。", symbols=["选择", "价格"]),
                _s(f"{base}-state-and-memory", "状态与记忆", "世界会记住胜利、谎言、失败和承诺。", symbols=["账", "疤"]),
                _s(f"{base}-threshold-and-return", "门槛与不可回头", "跨过门、等级、契约或地图线后，有些事不能归零。", symbols=["门", "线"]),
                _s(f"{base}-mask-and-proof", "面具与证明", "身份宣称必须通过行动和证据验证。", symbols=["面具", "证据"]),
            ],
            "emotion_arcs": [
                _s(f"{base}-pressure-to-choice", "压力到选择", "情绪从被压迫移动到主动做出有代价的选择。", beats=["压力", "计算", "选择"]),
                _s(f"{base}-win-to-cost", "胜利到代价", "回报之后必须承受状态变化带来的情绪重量。", beats=["回报", "意识到", "负担"]),
                _s(f"{base}-mistrust-to-working-trust", "不信任到有限协作", "信任只通过承担风险的行动增长，而不是口头保证。", beats=["怀疑", "风险", "有限信任"]),
            ],
            "dialogue_styles": [
                _s(f"{base}-tactical-dialogue", "战术型台词", "人物围绕欲望、限制、条件和后果说话。", style="具体压力"),
                _s(f"{base}-subtext-dialogue", "潜台词台词", "表层话里藏债务、吸引、怀疑或算计。", style="多层潜台词"),
                _s(f"{base}-institutional-dialogue", "制度型台词", "权力方使用流程、数字、契约、等级或公开评价施压。", style="正式压迫"),
            ],
            "anti_cliche_patterns": [
                _s(f"{base}-no-vague-progress", "禁止虚假推进", "没有状态变量改变，就不能声称剧情推进。", avoid="空转"),
                _s(f"{base}-no-free-win", "禁止免费胜利", rule_cost, avoid="无代价解决"),
                _s(f"{base}-no-category-drift", "禁止品类漂移", f"不得用无关套路替代{name}核心机制。", avoid="读者承诺破裂"),
            ],
            "real_world_references": [
                _s(f"{base}-beat-sheet-reference", "商业节拍表", "跟踪场景目标、阻力、策略、转折、代价和钩子。", methods=["目标", "转折", "钩子"]),
                _s(f"{base}-institutional-logic", "制度逻辑参考", "用规则、激励、文书、排名、物流或公开记录落地压力。", methods=["激励", "记录"]),
                _s(f"{base}-continuity-ledger", "连续性账本", "维护能力、关系、阵营、资源和线索的前后状态。", methods=["之前", "之后"]),
            ],
        },
    )


def _english_romantasy_pack_spec() -> MaterialPackSpec:
    return _pack(
        "english_romantasy",
        {
            "world_settings": [
                _s("two-court-shadow-crown", "Two-Court Shadow Crown", "Summer and Winter courts treat Rowan as leverage in a crown-binding system where romance, inheritance, and magic are inseparable.", rules=["court leverage", "crown-binding", "public bargains"]),
                _s("exile-record-world", "Exile Record World", "The mother's exile is not backstory; every archive, witness, and glamour stain must expose who benefited from the banishment.", function="long mystery ladder"),
                _s("shadow-sight-politics", "Shadow-Sight Politics", "Shadow sight reveals debt, desire, and betrayal, but every vision creates a political footprint someone can notice.", cost="visibility and misread risk"),
                _s("fae-bargain-economy", "Fae Bargain Economy", "Gifts, dances, protection, and intimacy all carry hidden prices that constrain later choices.", rule="no free favor"),
            ],
            "factions": [
                _s("summer-court-pressure", "Summer Court Pressure", "Summer Court weaponizes warmth, spectacle, and public favor to make Rowan's refusal look like treason.", tools=["revels", "public gifts", "shame"]),
                _s("winter-court-pressure", "Winter Court Pressure", "Winter Court uses silence, protection, contracts, and old debts to pull Rowan into colder bargains.", tools=["oaths", "archives", "protective threats"]),
                _s("nora-chen-network", "Nora Chen Network", "Nora Chen's informants trade secrets for survival; help from her network always shifts future leverage.", function="information broker"),
                _s("victor-hale-bureaucracy", "Victor Hale Bureaucracy", "Victor Hale's records, offices, and procedural delays turn romance and magic into evidence.", function="paperwork antagonist"),
            ],
            "locale_templates": [
                _s("crown-ballroom", "Crown Ballroom", "Public intimacy, faction watching, and magical glamour collide during dances, introductions, and forced appearances.", use="romance plus exposure"),
                _s("shadow-archive", "Shadow Archive", "Records shift under shadow sight; a page can answer one exile question while exposing Rowan's intrusion.", use="mystery reveal"),
                _s("winter-glasshouse", "Winter Glasshouse", "A private negotiation space where protection sounds like tenderness but functions as leverage.", use="slow-burn pressure"),
                _s("summer-revel", "Summer Revel", "A bright, crowded setting where refusal is visible and every smile becomes a political trap.", use="public bargain"),
                _s("oath-chamber", "Oath Chamber", "Contracts, blood marks, and witnesses make choices irreversible.", use="cost lock-in"),
                _s("exile-road", "Exile Road", "A liminal route tied to Rowan's mother, useful for chase, memory residue, and forbidden testimony.", use="exile callback"),
            ],
            "power_systems": [
                _s("shadow-sight-cost", "Shadow Sight Cost", "Every use must specify what Rowan sees, what she misreads, what it costs, and who might notice.", fields=["vision", "misread", "cost", "watcher"]),
                _s("court-bargain-grammar", "Court Bargain Grammar", "A proper offer contains gift, hook, threat, and hidden price; missing one element means a trap is concealed.", fields=["gift", "hook", "threat", "price"]),
                _s("glamour-residue-rule", "Glamour Residue Rule", "Strong emotions and broken oaths leave residue that shadow sight can read imperfectly.", limit="emotion distorts evidence"),
                _s("crown-binding-mechanism", "Crown-Binding Mechanism", "The crown binds power through public recognition, private vows, and court witnesses.", cost="agency traded for legitimacy"),
                _s("name-and-oath-limit", "Name and Oath Limit", "Names, nicknames, and titles change what bargains can touch; using the wrong name creates loopholes.", use="dialogue and contract tension"),
            ],
            "character_archetypes": [
                _s("agency-first-heroine", "Agency-First Heroine", "The heroine may desire love and safety, but each chapter must show the choice she makes and the cost she accepts.", function="anti-passive-romance"),
                _s("morally-gray-protector", "Morally Gray Protector", "Protection is useful but suspect; the protector's agenda must sometimes oppose Rowan's truth.", function="romance conflict"),
                _s("court-broker-ally", "Court Broker Ally", "An ally sells access, not loyalty; every favor creates a later invoice.", function="information pressure"),
                _s("beautiful-traitor", "Beautiful Traitor", "A charming court figure gives a real gift while steering Rowan into exposure.", function="double movement"),
            ],
            "character_templates": [
                _s("rowan", "Rowan", "Shadow-sighted chosen heroine whose power forces decisions instead of solving scenes.", role="protagonist"),
                _s("sam", "Sam", "Infiltration-linked ally whose help may protect Rowan or expose her mother's trail.", role="ambiguous ally"),
                _s("nora-chen", "Nora Chen", "Networked information broker who trades truth in fragments.", role="broker"),
                _s("victor-hale", "Victor Hale", "Bureaucratic antagonist whose records can hurt more than blades.", role="records pressure"),
                _s("winter-heir", "Winter Heir", "Potential romantic/political lead whose restraint hides a hard agenda.", role="slow-burn leverage"),
                _s("summer-envoy", "Summer Envoy", "Public-facing court pressure who turns charm into obligation.", role="court threat"),
            ],
            "plot_patterns": [
                _s("intimacy-changes-leverage", "Intimacy Changes Leverage", "A touch, confession, rescue, or kiss must alter trust, exposure, faction position, or debt.", rule="no static romantic beat"),
                _s("bargain-cost-callback", "Bargain Cost Callback", "A bargain accepted now must constrain a later scene in a concrete way.", rule="deferred price"),
                _s("exile-clue-ladder", "Exile Clue Ladder", "Each clue answers one layer of the mother's exile and opens a sharper political question.", rule="fragmented reveal"),
                _s("faction-reaction-loop", "Faction Reaction Loop", "After Rowan acts, at least one court or broker changes tactics.", rule="no reset"),
            ],
            "scene_templates": [
                _s("public-dance-bargain", "Public Dance Bargain", "A dance lets attraction, threat, witness pressure, and hidden contract operate in one scene.", beats=["invitation", "watchers", "offer", "cost"]),
                _s("shadow-sight-misread", "Shadow-Sight Misread", "Rowan reads a shadow clue but later discovers emotion or glamour distorted it.", beats=["vision", "decision", "wrong inference"]),
                _s("archive-theft", "Archive Theft", "A record retrieval becomes a political crime with a named witness or altered page.", beats=["entry", "page", "alarm"]),
                _s("near-intimacy-under-threat", "Near Intimacy Under Threat", "A romantic beat happens because danger forces a choice, not because the plot pauses.", beats=["danger", "choice", "boundary"]),
                _s("court-interrogation", "Court Interrogation", "Questions are weapons; answers must reveal motive while concealing vulnerability.", beats=["charge", "half-truth", "counterprice"]),
                _s("protection-betrayal", "Protection Betrayal", "A protective act saves Rowan but proves someone withheld a crucial truth.", beats=["save", "debt", "breach"]),
            ],
            "device_templates": [
                _s("shadowbound-mark", "Shadowbound Mark", "A visible or hidden mark that reacts to bargains, desire, or crown pressure.", function="cost tracker"),
                _s("exile-page", "Exile Page", "A record of the mother's exile that changes meaning under shadow sight.", function="mystery object"),
                _s("court-token", "Court Token", "A gift that grants access while signaling allegiance to watchers.", function="political prop"),
                _s("oath-wine", "Oath Wine", "A ritual drink that makes casual words contractually dangerous.", function="bargain prop"),
                _s("crown-ledger", "Crown Ledger", "A bureaucratic book tying romance choices to succession and magical legitimacy.", function="institutional threat"),
            ],
            "thematic_motifs": [
                _s("shadow-and-crown", "Shadow and Crown", "Private truth and public power keep contradicting each other.", symbols=["shadow", "crown"]),
                _s("touch-and-cost", "Touch and Cost", "Physical closeness must carry magical or political consequence.", symbols=["hands", "marks"]),
                _s("thresholds-and-doors", "Thresholds and Doors", "Entering a room, court, archive, or bargain should mark a loss of innocence or option.", symbols=["doors", "keys"]),
                _s("names-and-oaths", "Names and Oaths", "What someone is called changes what they can promise or betray.", symbols=["true name", "title"]),
            ],
            "emotion_arcs": [
                _s("mistrust-to-action-trust", "Mistrust to Action Trust", "Trust changes only when someone risks leverage, not through reassurance.", beats=["suspicion", "risk", "limited trust"]),
                _s("desire-under-consequence", "Desire Under Consequence", "Attraction intensifies because refusing or accepting it has a price.", beats=["pull", "cost", "choice"]),
                _s("betrayal-to-boundary", "Betrayal to Boundary", "A betrayal should make Rowan define new terms instead of merely forgive or flee.", beats=["hurt", "terms", "countermove"]),
            ],
            "dialogue_styles": [
                _s("court-double-speech", "Court Double Speech", "Courtiers say courtesy while negotiating threat, debt, and rumor.", style="polite menace"),
                _s("intimate-restraint", "Intimate Restraint", "Romantic dialogue should be specific, withheld, and action-backed.", style="subtext and restraint"),
                _s("bureaucratic-threat", "Bureaucratic Threat", "Victor-style pressure uses dates, records, signatures, and procedural consequence.", style="documentary precision"),
            ],
            "anti_cliche_patterns": [
                _s("no-static-love-interest", "No Static Love Interest", "No love interest may exist only to comfort, rescue, or tempt Rowan.", avoid="decorative romance"),
                _s("no-costless-shadow-sight", "No Costless Shadow Sight", "Shadow sight cannot solve a scene without risk, misread, or observer consequence.", avoid="free magic answer"),
                _s("no-vague-court-intrigue", "No Vague Court Intrigue", "Every court scene needs a named gain, loss, witness, or debt.", avoid="empty scheming"),
            ],
            "real_world_references": [
                _s("romance-beat-craft", "Romance Beat Craft", "Use proximity, forced choice, boundary, consequence, and delayed payoff rather than generic yearning.", methods=["proximity", "boundary", "payoff"]),
                _s("court-protocol-reference", "Court Protocol Reference", "Public order, seating, titles, gifts, and witnesses can become plot mechanics.", methods=["titles", "protocol", "gifts"]),
                _s("records-and-surveillance", "Records and Surveillance", "Letters, archives, signatures, and testimony turn magical romance into evidence.", methods=["archives", "signatures", "witnesses"]),
            ],
        },
    )


def _breaking_point_pack_spec() -> MaterialPackSpec:
    return _superhero_pack_spec(
        pack_id="english_superhero_breaking_point",
        protagonist="Cole",
        family_anchor="Sophie",
        power_name="Reservoir Kinetics",
        power_slug="reservoir-kinetics",
        faction_names=("Municipal Enforcement", "Victor Kane Network", "Elena Vasquez Leak Line", "Sports Sponsorship System"),
        character_names=("Cole", "Sophie", "Elena Vasquez", "Victor Kane", "Municipal handler", "former coach"),
        signature_clock="Sophie's treatment deadline",
        public_frame="track meets, body cameras, sponsorship paperwork, and viral footage",
        method_frame="sports biomechanics, reservoir capacity, leakage, overload, and precision release",
    )


def _witness_protocol_pack_spec() -> MaterialPackSpec:
    return _superhero_pack_spec(
        pack_id="english_superhero_witness_protocol",
        protagonist="Kade",
        family_anchor="Maya",
        power_name="Sixty-Second Mimicry",
        power_slug="sixty-second-mimicry",
        faction_names=("Aegis Coalition", "The Collective", "Registry Office", "Silas Crane Cell"),
        character_names=("Kade", "Maya", "Marcus Mercer", "Silas Crane", "Aegis analyst", "Registry witness"),
        signature_clock="Maya's school-and-guardianship exposure clock",
        public_frame="witness reports, surveillance clips, registry flags, and contradictory testimony",
        method_frame="trigger, borrowed ability, countdown, degradation, aftereffect, and trace",
    )


def _generic_superhero_pack_spec() -> MaterialPackSpec:
    return _superhero_pack_spec(
        pack_id="english_superhero_progression",
        protagonist="the protagonist",
        family_anchor="the protected family anchor",
        power_name="Measured Emergence Power",
        power_slug="measured-emergence-power",
        faction_names=("Public Registry", "Containment Authority", "Corporate Sponsor", "Underground Cape Network"),
        character_names=("the protagonist", "family anchor", "registry analyst", "corporate fixer", "rival cape", "field mentor"),
        signature_clock="public exposure and family safety deadline",
        public_frame="footage, witnesses, registry data, and media pressure",
        method_frame="source, duration, capacity, cost, trace, and tactical exploit",
    )


def _superhero_pack_spec(
    *,
    pack_id: str,
    protagonist: str,
    family_anchor: str,
    power_name: str,
    power_slug: str,
    faction_names: tuple[str, str, str, str],
    character_names: tuple[str, str, str, str, str, str],
    signature_clock: str,
    public_frame: str,
    method_frame: str,
) -> MaterialPackSpec:
    return _pack(
        pack_id,
        {
            "world_settings": [
                _s("public-record-superhero-city", "Public-Record Superhero City", f"Every powered incident leaves {public_frame}; action cannot reset after a fight.", evidence=public_frame),
                _s("progression-under-surveillance", "Progression Under Surveillance", f"{protagonist}'s growth must be measurable and watched by institutions.", rule="power growth creates data"),
                _s("civilian-stakes-system", "Civilian Stakes System", f"{family_anchor} creates operational stakes through medical, school, guardianship, money, or reputation pressure.", clock=signature_clock),
                _s("smart-faction-city", "Smart Faction City", "Authorities, enemies, and sponsors update tactics after every visible win.", rule="no repeated failed tactic"),
            ],
            "factions": [
                _s("faction-one", faction_names[0], f"{faction_names[0]} interprets power incidents through control, liability, and capture doctrine.", role="institutional pressure"),
                _s("faction-two", faction_names[1], f"{faction_names[1]} adapts through fixers, leaks, and targeted tests.", role="conspiracy pressure"),
                _s("faction-three", faction_names[2], f"{faction_names[2]} turns partial evidence into actionable risk.", role="information pressure"),
                _s("faction-four", faction_names[3], f"{faction_names[3]} constrains money, public legitimacy, and routes.", role="resource pressure"),
            ],
            "locale_templates": [
                _s("camera-heavy-street", "Camera-Heavy Street", "Public action space where bystanders, phones, body cameras, and damage reports become later plot evidence.", use="exposure"),
                _s("training-threshold-zone", "Training Threshold Zone", f"A controlled place to test {power_name} parameters and injuries.", use="progression measurement"),
                _s("family-pressure-location", "Family Pressure Location", f"A school, clinic, home, or transit point tied to {family_anchor}.", use="civilian stakes"),
                _s("registry-office", "Registry Office", "Paperwork, interviews, and flags transform action into institutional consequence.", use="bureaucratic threat"),
                _s("ambush-under-infrastructure", "Infrastructure Ambush", "Bridge, station, tunnel, or stadium space that forces power use under witnesses.", use="tactical action"),
                _s("leak-contact-site", "Leak Contact Site", "A public-private meeting spot where a warning can be trap, gift, or surveillance bait.", use="conspiracy exchange"),
            ],
            "power_systems": [
                _s(power_slug, power_name, f"Every activation must track {method_frame}.", fields=method_frame),
                _s("holding-cost-rule", "Holding Cost Rule", "Not using stored power can be as dangerous as releasing it.", use="delayed cost"),
                _s("trace-and-residue-rule", "Trace and Residue Rule", "Power leaves forensic, sensory, bodily, or digital traces.", use="evidence continuity"),
                _s("precision-over-output-rule", "Precision Over Output Rule", "Progression includes control, timing, restraint, and deception, not only stronger blasts.", use="skill ladder"),
                _s("overload-injury-rule", "Overload Injury Rule", "A big win must create a new bodily, exposure, or tactical constraint.", use="costful payoff"),
            ],
            "character_archetypes": [
                _s("measured-progression-hero", "Measured Progression Hero", f"{protagonist} grows through testing parameters, paying costs, and exploiting constraints.", function="power ladder"),
                _s("civilian-stakes-anchor", "Civilian Stakes Anchor", f"{family_anchor} is not a prop; their logistics change the plan.", function="operational stakes"),
                _s("adaptive-fixer-antagonist", "Adaptive Fixer Antagonist", "A fixer changes methods after every failure.", function="smart opposition"),
                _s("evidence-bearing-ally", "Evidence-Bearing Ally", "An ally brings actionable risk: file, clip, message, or witness name.", function="plot-moving ally"),
            ],
            "character_templates": [
                _s("hero", character_names[0], f"{character_names[0]} must choose between winning, hiding, saving someone, and avoiding power cost.", role="protagonist"),
                _s("family-anchor", character_names[1], f"{character_names[1]} creates concrete logistical stakes.", role="civilian anchor"),
                _s("evidence-ally", character_names[2], f"{character_names[2]} moves the plot through risky information.", role="information ally"),
                _s("network-antagonist", character_names[3], f"{character_names[3]} tests the power system through proxies and leaks.", role="conspiracy antagonist"),
                _s("institutional-handler", character_names[4], f"{character_names[4]} represents procedure, liability, and capture logic.", role="institutional pressure"),
                _s("training-or-old-life-anchor", character_names[5], f"{character_names[5]} ties present power choices to old identity and discipline.", role="identity pressure"),
            ],
            "plot_patterns": [
                _s("incident-to-record-loop", "Incident to Record Loop", "A fight or rescue becomes footage, report, flag, or rumor that returns later.", rule="evidence continuity"),
                _s("power-test-cost-payoff", "Power Test Cost Payoff", "A new parameter is tested, exploited, then paid for.", rule="measurable progression"),
                _s("family-clock-tradeoff", "Family Clock Tradeoff", f"A tactical win may damage {signature_clock}.", rule="unclean win"),
                _s("adaptive-antagonist-loop", "Adaptive Antagonist Loop", "Local victory forces smarter enemy model updates.", rule="faction adaptation"),
            ],
            "scene_templates": [
                _s("measured-training-scene", "Measured Training Scene", "Test one parameter, produce one visible result, and define one new risk.", beats=["test", "measurement", "cost"]),
                _s("public-rescue-exposure", "Public Rescue Exposure", "Saving someone creates witnesses and registry consequences.", beats=["choice", "save", "record"]),
                _s("family-route-compromise", "Family Route Compromise", f"Protecting {family_anchor} forces a worse tactical path.", beats=["family logistics", "route", "loss"]),
                _s("fixer-trap-scene", "Fixer Trap Scene", "An offer or leak is designed to reveal how the power works.", beats=["bait", "activation", "data taken"]),
                _s("institutional-interview", "Institutional Interview", "A handler's questions narrow identity, timeline, and exposure.", beats=["question", "contradiction", "flag"]),
                _s("overload-aftermath", "Overload Aftermath", "After action, body cost and evidence state drive the next scene.", beats=["pain", "trace", "next constraint"]),
            ],
            "device_templates": [
                _s("incident-footage", "Incident Footage", "A clip that can be misread, leaked, or used to triangulate power limits.", function="evidence"),
                _s("registry-flag", "Registry Flag", "A formal marker that changes what institutions can do next.", function="institutional pressure"),
                _s("training-log", "Training Log", "A record of capacity, duration, injury, and exploit.", function="progression tracker"),
                _s("medical-or-school-file", "Medical or School File", f"A document tied to {family_anchor} that gives enemies leverage.", function="civilian stakes"),
                _s("anonymous-message", "Anonymous Message", "A warning that can be help, bait, or controlled leak.", function="conspiracy hook"),
            ],
            "thematic_motifs": [
                _s("power-as-data", "Power as Data", "Every heroic act teaches enemies something.", symbols=["camera", "file", "trace"]),
                _s("body-as-ledger", "Body as Ledger", "Injuries, fatigue, and overload make progression physically legible.", symbols=["breath", "pulse", "scar"]),
                _s("ordinary-life-pressure", "Ordinary Life Pressure", "Bills, schools, races, jobs, and clinics keep the story grounded.", symbols=["forms", "schedules"]),
                _s("mask-and-witness", "Mask and Witness", "Identity is shaped by what others think they saw.", symbols=["mask", "testimony"]),
            ],
            "emotion_arcs": [
                _s("fear-to-controlled-risk", "Fear to Controlled Risk", "The scene moves from panic to a chosen, measured risk.", beats=["fear", "calculation", "commitment"]),
                _s("protective-guilt-to-agency", "Protective Guilt to Agency", f"{protagonist}'s guilt over {family_anchor} must become a tactical decision.", beats=["guilt", "choice", "cost"]),
                _s("humiliation-to-precision", "Humiliation to Precision", "Being underestimated becomes a precision advantage, not empty revenge.", beats=["dismissed", "observes", "exploits"]),
            ],
            "dialogue_styles": [
                _s("tactical-short-speech", "Tactical Short Speech", "Action dialogue is clipped, specific, and parameter-aware.", style="short tactical lines"),
                _s("institutional-cold-speech", "Institutional Cold Speech", "Authorities use procedure, timestamps, liability, and records.", style="bureaucratic pressure"),
                _s("family-grounded-speech", "Family Grounded Speech", "Family dialogue uses practical logistics rather than abstract motivation.", style="concrete care"),
            ],
            "anti_cliche_patterns": [
                _s("no-unlimited-spectacle", "No Unlimited Spectacle", "Powers must stay bounded by duration, cost, trace, or injury.", avoid="free spectacle"),
                _s("no-hostage-prop-family", "No Hostage-Prop Family", f"{family_anchor} must have choices, logistics, and consequences.", avoid="flat motivation"),
                _s("no-resetting-exposure", "No Resetting Exposure", "Public incidents must persist through records, witnesses, and tactics.", avoid="episodic reset"),
            ],
            "real_world_references": [
                _s("surveillance-chain", "Surveillance Chain", "Use phone clips, timestamps, body cameras, and chain-of-custody logic.", methods=["footage", "timestamps"]),
                _s("sports-or-body-mechanics", "Sports or Body Mechanics", "Translate power use through breath, leverage, timing, joints, and fatigue.", methods=["timing", "leverage"]),
                _s("institutional-risk-process", "Institutional Risk Process", "Registry, insurance, school, medical, or legal forms can drive plot pressure.", methods=["forms", "procedures"]),
            ],
        },
    )


def _female_no_cp_pack_spec() -> MaterialPackSpec:
    return _pack(
        "female_no_cp_apocalypse",
        {
            "world_settings": [
                _s("ark-city-debt-system", "方舟城债务系统", "方舟城不是安全区背景，而是资源配给、异能登记、旧案遮掩和人情债组成的压迫机器。", rule="资源和旧债绑定"),
                _s("cost-transfer-apocalypse", "代价转化末世", "救人必须决定谁受益、谁承担、承担什么，以及后遗症如何进入关系账本。", rule="无免费救援"),
                _s("source-origin-hunt-world", "源初追猎世界", "源初通过观测、标记、诱饵、舆论和样本回收学习林鸢能力。", rule="敌人学习"),
                _s("no-cp-growth-world", "无CP成长世界", "关系推进来自信任、债务、利用、清算和共同风险，不能靠恋爱线驱动。", rule="非恋爱关系网"),
            ],
            "factions": [
                _s("ark-city-management", "方舟城管理部", "管理部以登记、配给、封锁、旧案档案控制异能者。", tools=["登记", "配给", "封锁"]),
                _s("source-origin", "源初", "源初把林鸢的救人记录当作能力样本持续更新捕捉策略。", tools=["标记", "样本", "舆论"]),
                _s("scavenger-network", "清道夫网络", "清道夫在兽潮边缘交易药品、情报、身份和背叛。", tools=["黑市", "路线", "药剂"]),
                _s("old-exile-signers", "三年前签字者", "三年前放逐事件的签字者、受益者、沉默者构成旧债网络。", tools=["签名", "沉默", "旧证词"]),
            ],
            "locale_templates": [
                _s("ark-distribution-hall", "方舟城配给大厅", "资源、公平幻觉和权力羞辱集中爆发的公共空间。", use="资源冲突"),
                _s("ability-registration-room", "异能登记室", "能力信息被记录、低估或伪造，直接影响行动自由。", use="能力曝光"),
                _s("beast-tide-perimeter", "兽潮外围线", "怪物压力、撤离路线和救援定价同时发生。", use="末世行动"),
                _s("abandoned-shelter", "废弃收容所", "适合发现旧案遗留物、幸存者证词和源初标记。", use="旧案线索"),
                _s("black-market-clinic", "黑市诊所", "治疗、药剂和代价交易的灰区。", use="身体代价"),
                _s("source-sample-site", "源初样本点", "源初留下诱饵或回收样本的地点。", use="追猎升级"),
            ],
            "power_systems": [
                _s("cost-transfer-ledger", "代价转化账本", "每次使用前必须写清请求方、受益方、承担方、代价类型和比例。", fields=["请求", "受益", "承担", "比例"]),
                _s("aftereffect-rule", "后遗症规则", "代价必须留下身体、精神、记忆、稳定性或社会身份后果。", fields=["身体", "精神", "身份"]),
                _s("nontransferable-boundary", "不可转移边界", "某些代价不能转移，错误估价会反噬林鸢。", use="边界反噬"),
                _s("source-data-learning", "源初数据学习", "林鸢每救一次人，源初获得或误判一条能力数据。", use="敌人升级"),
                _s("active-pricing-rule", "主动定价规则", "林鸢可以开价、拒绝、拆穿动机，把代价变成筹码。", use="大女主爽点"),
            ],
            "character_archetypes": [
                _s("pricing-heroine", "主动定价女主", "女主的成长落在选择权扩大，不是被认可、拯救或原谅。", function="主角方法论"),
                _s("nonromantic-debt-ally", "非恋爱债务盟友", "盟友关系靠风险共担和债务重估推进。", function="无CP关系"),
                _s("resource-bureaucrat", "资源官僚", "对手用配给和登记压迫，而不是单纯战斗。", function="现实压力"),
                _s("sample-hunter", "样本猎手", "敌人通过样本和诱饵学习主角能力。", function="源初压力"),
            ],
            "character_templates": [
                _s("lin-yuan-female", "林鸢", "代价转化者，核心爽点是主动定价、拒绝无偿牺牲和清算旧债。", role="protagonist"),
                _s("jiang-cheng", "姜澄", "旧背叛线人物，必须通过行动补偿和风险共担推进，不能滑向CP。", role="old debt ally"),
                _s("zhou-yanning", "周砚宁", "方舟城信息/权力接口，帮助总带操控风险。", role="institutional ally"),
                _s("ark-officer", "管理部登记官", "以程序和配给制造压力。", role="bureaucratic pressure"),
                _s("source-observer", "源初观测者", "追踪能力样本并更新捕捉策略。", role="hunter"),
                _s("saved-debtor", "被救欠债者", "被救后必须改变立场、投靠、怀疑或背刺。", role="relationship ledger"),
            ],
            "plot_patterns": [
                _s("rescue-to-debt-loop", "救援转债务循环", "救人后必须改变关系账本和资源局面。", rule="救援不归零"),
                _s("price-refusal-payoff", "开价/拒绝爽点", "林鸢通过拒绝或抬价拿回选择权。", rule="主动权"),
                _s("source-hunt-feedback", "源初追猎反馈", "每次能力使用都让源初调整策略。", rule="敌人适应"),
                _s("old-exile-fragment", "三年前旧债碎片", "旧案每次只揭一层签字者、受益者或沉默者。", rule="长线揭示"),
            ],
            "scene_templates": [
                _s("pricing-negotiation", "代价定价场景", "请求方求救，林鸢问清受益和承担，再开出条件。", beats=["求救", "拆动机", "定价"]),
                _s("beast-tide-rescue-cost", "兽潮救援代价场景", "救援和撤离路线必须带身体或资源成本。", beats=["兽潮", "取舍", "反噬"]),
                _s("registration-pressure", "登记压迫场景", "管理部用流程逼林鸢暴露能力参数。", beats=["登记", "诱导", "反问"]),
                _s("source-bait", "源初诱饵场景", "看似求救的信息其实在测能力边界。", beats=["诱饵", "使用", "数据泄露"]),
                _s("old-debt-testimony", "旧债证词场景", "证人只说对自己有利的一半，推动旧案一层。", beats=["证词", "隐瞒", "新债"]),
                _s("noncp-trust-action", "无CP信任行动场景", "信任通过行动、风险共担或利益让渡建立。", beats=["怀疑", "行动", "重估"]),
            ],
            "device_templates": [
                _s("cost-ledger-card", "代价账卡", "记录谁欠谁、欠什么、何时清算。", function="债务追踪"),
                _s("ability-registration-file", "异能登记档案", "能力参数和身份风险的正式记录。", function="制度压力"),
                _s("source-marker", "源初标记", "源初观测或定位林鸢的痕迹。", function="追猎线索"),
                _s("exile-signature-page", "放逐签字页", "三年前旧案签字者与沉默者的证物。", function="旧案证据"),
                _s("ration-token", "配给令牌", "资源争夺和人情债的实体化道具。", function="资源账"),
            ],
            "thematic_motifs": [
                _s("price-and-choice", "代价与选择", "成长不是被救，而是能决定代价如何分配。", symbols=["账", "价"]),
                _s("debt-without-romance", "非恋爱债务关系", "亲密关系之外的信任、利用和清算同样强烈。", symbols=["欠条", "并肩"]),
                _s("body-as-cost", "身体作为代价", "伤、失忆、异能不稳让能力成本可见。", symbols=["伤口", "颤抖"]),
                _s("shelter-as-cage", "安全区即牢笼", "方舟城保护与控制是一体两面。", symbols=["城门", "登记"]),
            ],
            "emotion_arcs": [
                _s("pity-to-price", "怜悯到定价", "林鸢可以同情，但最终必须转化为有条件选择。", beats=["触动", "问价", "定价"]),
                _s("betrayal-to-accounting", "背叛到记账", "旧背叛不靠原谅解决，靠补偿和新风险重估。", beats=["刺痛", "证据", "清算"]),
                _s("exhaustion-to-agency", "疲惫到主动权", "代价反噬后仍由林鸢重新设定规则。", beats=["疲惫", "拒绝", "重订规则"]),
            ],
            "dialogue_styles": [
                _s("price-cross-exam", "定价式逼问", "林鸢用短问句逼出谁受益谁承担。", style="冷静、具体、问责"),
                _s("bureaucratic-ration-speech", "配给官话术", "管理部台词以流程、配额、风险等级施压。", style="制度化压迫"),
                _s("noncp-trust-speech", "无CP信任话术", "盟友少说保护，多说交换、路线、证据和风险。", style="行动导向"),
            ],
            "anti_cliche_patterns": [
                _s("no-hidden-romance-drive", "禁止隐性恋爱主驱动", "姜澄线必须围绕补偿、风险和旧债，不能写成男主保护。", avoid="CP滑坡"),
                _s("no-free-healing", "禁止无损治疗", "代价转化不能变成万能治疗或复活。", avoid="无成本能力"),
                _s("no-monster-only-chapters", "禁止纯打怪章节", "兽潮必须推进代价规则、源初数据或方舟城旧债。", avoid="副本化"),
            ],
            "real_world_references": [
                _s("disaster-logistics", "灾难物流参考", "配给、撤离、收容、医疗排队可制造现实压力。", methods=["配给", "撤离"]),
                _s("bureaucratic-registration", "登记制度参考", "表格、等级、审批、档案能压缩行动空间。", methods=["登记", "审批"]),
                _s("trauma-boundary-growth", "创伤边界成长", "成长线应表现边界重建、选择权恢复和关系重估。", methods=["边界", "选择"]),
            ],
        },
    )


def _xianxia_upgrade_pack_spec() -> MaterialPackSpec:
    return _pack(
        "xianxia_upgrade",
        {
            "world_settings": [
                _s("late-dharma-sect-world", "末法宗门世界", "灵气稀薄、资源稀缺让每次突破和机缘都有高烈度争夺。", rule="稀缺驱动"),
                _s("dao-seed-causality-world", "道种因果世界", "道种提供感知、推演和局部转化，不能替主角直接解决所有问题。", rule="金手指有限"),
                _s("sect-resource-ledger-world", "宗门资源账世界", "灵米、丹药、名额、残页、配给和身份都必须入账。", rule="资源可追踪"),
                _s("exam-secret-realm-clock", "大考秘境时钟", "三个月大考和秘境试炼驱动准备、资源争夺和旧事揭示。", rule="阶段主时钟"),
            ],
            "factions": [
                _s("outer-servant-peak", "杂役峰", "底层资源、羞辱和低位反制的主场。", role="低位压力"),
                _s("alchemy-hall", "丹房", "丹药、灵草和执事利益构成资源争夺。", role="资源入口"),
                _s("inner-sect-line", "内门线", "内门资格、长老眼线和苏瑶/陆沉关系推动身份升级。", role="上升通道"),
                _s("old-root-conspiracy", "废灵根旧事势力", "二十年前旧事和道种来历背后的高层遮掩。", role="长线阴谋"),
            ],
            "locale_templates": [
                _s("servant-peak-yard", "杂役峰院落", "羞辱、资源克扣和第一次反制发生地。", use="低位爽点"),
                _s("abandoned-scripture-corner", "废弃藏经角", "残页、旧功法和道种异动的发现点。", use="机缘"),
                _s("alchemy-storehouse", "丹房库房", "丹药账目和灵草失踪牵出执事利益。", use="资源账"),
                _s("sect-exam-platform", "宗门考核台", "公开低位反制和身份误判利用。", use="打脸但带后账"),
                _s("secret-realm-gate", "秘境入口", "倒计时、名额争夺和试炼需求汇聚。", use="阶段门槛"),
                _s("old-spirit-root-cave", "废灵根旧洞", "二十年前旧事和道种来历的一层真相。", use="长线揭示"),
            ],
            "power_systems": [
                _s("dao-seed-trigger-rule", "道种触发规则", "每次异动必须写清触发条件、可见效果、限制和代价。", fields=["触发", "效果", "限制", "代价"]),
                _s("realm-resource-rule", "炼气-筑基-金丹境界资源规则", "炼气、筑基、金丹等境界台阶必须绑定资源、时间、瓶颈、风险或势力关注，突破不是免费数值上涨。", fields=["境界", "资源", "时间", "瓶颈", "风险"]),
                _s("surface-hidden-cultivation", "明暗双修规则", "表面功法掩护真实进度，禁忌功法提供越阶基础但会留下痕迹。", use="身份伪装"),
                _s("causality-sense-limit", "因果感应限制", "感应只能给路径、风险或局部推演，不能给完整答案。", use="有限提示"),
                _s("misjudged-breakthrough-cost", "误判突破代价", "突破误判会带来身体反噬、痕迹暴露或资源亏空。", use="反噬"),
            ],
            "character_archetypes": [
                _s("calculating-low-status-hero", "低位计算型主角", "主角以隐忍、试探、借势和低风险破局推进。", function="方法论"),
                _s("resource-gatekeeper", "资源把门人", "执事/丹房人物通过配给和账目施压。", function="资源压力"),
                _s("misjudging-genius-rival", "误判型天才对手", "对手轻视主角，但失败后必须改变策略或引来后台。", function="后账"),
                _s("ambiguous-inner-ally", "内门暧昧盟友", "盟友有利益位置，不能只送资源。", function="势力博弈"),
            ],
            "character_templates": [
                _s("ning-chen", "宁尘", "废灵根低位主角，以道种、资源账和计算反制高位。", role="protagonist"),
                _s("su-yao", "苏瑶", "内门线人物，帮忙和试探并存。", role="ambiguous ally"),
                _s("fang-yu", "方域", "竞争者或派系压力，失败后推动更高层试探。", role="rival pressure"),
                _s("lu-chen", "陆沉", "不能只做送资源工具人，必须有自身利益和选择。", role="ally with agenda"),
                _s("sect-steward", "执事", "通过配给、考核、账目压制主角。", role="bureaucratic antagonist"),
                _s("old-servant", "老杂役", "提供底层秘辛和代价感。", role="early guide"),
            ],
            "plot_patterns": [
                _s("small-resource-big-leverage", "小资源撬大机会", "低级资源通过道种或情报变成高阶机会。", rule="低位反制"),
                _s("breakthrough-with-back-debt", "突破带后账", "每次突破必须引来关注、亏空或新敌意。", rule="无免费升级"),
                _s("sect-feedback-loop", "宗门反馈循环", "宁尘行动改变配给、监视、拉拢或打压。", rule="势力适应"),
                _s("secret-realm-prep-payoff", "秘境准备兑现", "准备阶段每个技能/资源都要在秘境中兑现需求。", rule="准备-兑现"),
            ],
            "scene_templates": [
                _s("resource-accounting-scene", "资源记账场景", "获得、消耗、交换或争夺一项资源，并改变下一步选择。", beats=["资源", "账目", "选择"]),
                _s("dao-seed-test-scene", "道种试探场景", "主角用低风险方式验证一条道种规则。", beats=["试探", "异动", "代价"]),
                _s("public-low-status-reversal", "公开低位反制场景", "反制高位但留下后台压力。", beats=["轻视", "反制", "后账"]),
                _s("sect-ledger-interrogation", "宗门账目逼问场景", "用账目、配给、证据逼出执事漏洞。", beats=["账目", "漏洞", "反将"]),
                _s("secret-realm-need-setup", "秘境需求铺垫场景", "当前准备对应秘境中的战力、识药、阵法、隐匿或保命需求。", beats=["需求", "准备", "倒计时"]),
                _s("old-root-reveal", "废灵根旧事揭示场景", "旧事只揭一层，连到道种来历或宗门遮掩。", beats=["遗痕", "误读", "新疑问"]),
            ],
            "device_templates": [
                _s("black-iron-fragment", "黑铁残片", "禁忌功法和道种异动的载体。", function="金手指入口"),
                _s("resource-ledger-slip", "资源账条", "记录灵米、丹药、名额和克扣。", function="资源证据"),
                _s("secret-realm-token", "秘境名额令", "争夺、交换、伪装和身份升级的关键物。", function="阶段目标"),
                _s("dao-seed-mark", "道种痕迹", "每次使用可能留下被高阶修士察觉的痕迹。", function="暴露风险"),
                _s("old-root-record", "废灵根旧档", "二十年前旧事的证据碎片。", function="长线谜题"),
            ],
            "thematic_motifs": [
                _s("resource-as-fate", "资源即命运", "低位者的命运首先体现为资源账。", symbols=["灵米", "丹药"]),
                _s("hidden-seed-visible-trace", "暗种与明痕", "真正力量藏在暗处，但每次使用都会留下痕迹。", symbols=["种", "痕"]),
                _s("low-position-high-leverage", "低位高杠杆", "弱势身份既是压迫也是掩护。", symbols=["杂役牌", "旧衣"]),
                _s("cause-and-payback", "因果与后账", "每次胜利都引出更高层因果。", symbols=["因果线", "账"]),
            ],
            "emotion_arcs": [
                _s("humiliation-to-calculation", "羞辱到计算", "被辱后不是莽撞，而是转成风险计算。", beats=["受辱", "观察", "布局"]),
                _s("scarcity-to-breakthrough", "匮乏到突破", "资源缺口逼出非常规路径。", beats=["缺口", "试探", "突破"]),
                _s("hidden-power-to-risk", "暗力到风险", "爽点后立刻意识到暴露或反噬。", beats=["成功", "快感", "后账"]),
            ],
            "dialogue_styles": [
                _s("sect-rank-pressure", "宗门等级话术", "高位者用身份、配给、规矩压人。", style="等级压迫"),
                _s("calculating-hero-speech", "计算型主角台词", "宁尘少放狠话，多问账、问规矩、问后果。", style="克制试探"),
                _s("elder-half-truth", "长老半真话术", "高层只给局部真相，借机观察主角反应。", style="留白压迫"),
            ],
            "anti_cliche_patterns": [
                _s("no-free-breakthrough", "禁止无成本突破", "升级必须有资源、风险、师承、仇恨或痕迹。", avoid="空升级"),
                _s("no-almighty-dao-seed", "禁止万能道种", "道种只能提供路径/感知/推演/局部转化。", avoid="万能系统"),
                _s("no-face-slap-only", "禁止纯打脸", "打脸必须推进资源账、境界账或宗门反馈。", avoid="爽点空转"),
            ],
            "real_world_references": [
                _s("resource-economy-design", "资源经济设计", "把货币、配给、名额、库存、账目当作剧情压力。", methods=["库存", "配给"]),
                _s("training-load-design", "训练负荷设计", "修炼写时间、恢复、风险、瓶颈和边际收益。", methods=["负荷", "恢复"]),
                _s("institutional-hierarchy", "宗门层级制度", "外门、内门、执事、长老、宗主候选构成行动边界。", methods=["层级", "考核"]),
            ],
        },
    )


def _build_qingnang_pack(project_id: str) -> list[ProjectMaterial]:
    """Project-scoped material pack for 《青囊不语问阴阳》."""

    rows: list[ProjectMaterial] = []

    rows += [
        _mat(project_id, "world_settings", "mirror-debt-city", "镜债外溢都市", "十七栋困魂镜局从凶宅向医院、旧货市场、警方证据和现实监控外溢，城市表层秩序与阴债规则互相污染。", {"rules": ["反光物成镜眼", "回执外开门", "现实证据可被污染"], "uses": ["现实压力", "案件升级"]}),
        _mat(project_id, "world_settings", "three-clan-contract-world", "三族契约世界", "林家记账、张家开门、钱家守镜构成长期债务结构，所有单元案都必须回流到三族旧契。", {"clans": ["林家", "张家", "钱家"], "tension": "合作与互相甩债并存"}),
        _mat(project_id, "world_settings", "qingnang-causality-ledger-world", "青囊因果账世界", "青囊不是法术大全，而是只记录因果、账印、方位与代价的阴阳账本。", {"artifact": "青囊", "limit": "不给最终答案"}),
        _mat(project_id, "world_settings", "seventeen-building-main-gate", "十七栋主镜门", "第一卷固定入口，所有外部异闻必须证明是十七栋主镜门的回执、分账或反噬。", {"entry": "十七栋", "forbidden": "无关城市怪谈"}),
    ]

    rows += [
        _mat(project_id, "factions", "lin-bookkeepers", "林家记账人", "林家承担记录和压账功能，但并非天然正义，林渊越用青囊越接近本金身份。", {"goal": "压住新名并追前账", "internal_conflict": "救人与认账冲突"}),
        _mat(project_id, "factions", "zhang-door-openers", "张家开门人", "张家掌握旧门、门契和开门痕迹，王建业遗物与旧货市场都指向张家内鬼。", {"goal": "控制镜门开启时机", "tools": ["门契", "旧门", "三短一长"]}),
        _mat(project_id, "factions", "qian-mirror-keepers", "钱家守镜人", "钱家知道守镜代价和过户规则，钱婆婆既怕镜子失控，也试图让林家付债。", {"goal": "守镜与甩债", "cost": "寿命和身份"}),
        _mat(project_id, "factions", "police-evidence-pressure", "苏婉宁现实证据线", "警方不替灵异背书，只用监控、尸检、封楼和物证压缩林渊行动空间。", {"role": "现实压力", "lead": "苏婉宁"}),
    ]

    rows += [
        _mat(project_id, "locale_templates", "seventeen-building", "十七栋", "困魂镜主场，走廊、303、四楼窗口、灵位室和旧门共同组成镜债迷宫。", {"atmosphere": "潮湿、反光、封闭", "dangers": ["镜眼", "借脸", "照名"]}),
        _mat(project_id, "locale_templates", "room-303-mirror-eye", "303 镜眼", "陈默入镜的核心人质点，后续救援必须先揭开替认对象。", {"function": "人质线", "callbacks": ["陈默", "小雨"]}),
        _mat(project_id, "locale_templates", "mortuary-receipt-zone", "太平间回执区", "王建业尸体和回执镜片连接现实尸检与镜门外开，适合制造警方证据污染。", {"function": "现实外溢", "props": ["尸体", "镜片", "冷光"]}),
        _mat(project_id, "locale_templates", "old-market-door-contract", "城北旧货市场", "王建业死前去过的旧货市场，张家门契和铜镜流转线索集中在这里。", {"function": "张家内鬼线", "props": ["铜镜", "门契"]}),
        _mat(project_id, "locale_templates", "old-door-thirty-years", "三十年前旧门", "三短一长敲门节奏连接林正淳、旧门与真假父亲回应。", {"sound": "三短一长", "risk": "真假难辨"}),
        _mat(project_id, "locale_templates", "ancestral-house-well", "老宅井口", "第32-37章目标地点，井口铜钱应揭开林家辉三十年前补镜代价。", {"future_use": "三百年第一账", "prop": "井口铜钱"}),
    ]

    rows += [
        _mat(project_id, "power_systems", "deny-admit-account-rule", "否认/认账/入账规则", "否认者先入账，认账者可活；承认不是洗罪，而是暂免被镜吞。", {"visible_effect": "灰线/死亡顺序", "counter": "逼出真事", "cost": "真相伤人"}),
        _mat(project_id, "power_systems", "receipt-door-system", "回执外开门规则", "死者回执可让困魂镜绕过十七栋从现实外开门，回执被夺会制造镜影行动。", {"visible_effect": "尸体遗物/镜片", "counter": "取回或暂封回执"}),
        _mat(project_id, "power_systems", "debt-transfer-system", "镜债过户规则", "债可过户，替认必须付寿命、名字或现实身份，裴镜渊只作为旧账名方法线索存在。", {"visible_effect": "账页旧名", "cost": "寿命/身份"}),
        _mat(project_id, "power_systems", "midnight-name-illumination", "午夜照名规则", "真话可活，假话入账；真话也必须交出亲族债或身份代价。", {"visible_effect": "名单点名", "counter": "说真话并付代价"}),
        _mat(project_id, "power_systems", "old-account-priority", "前账优先规则", "前账未清，不书新名可暂压第七名真名，但会反向催林渊追三百年第一账。", {"visible_effect": "账本催促", "cost": "旧账反噬"}),
    ]

    rows += [
        _mat(project_id, "character_archetypes", "debt-ledger-protagonist", "执卷记账主角", "主角不是普通驱鬼人，而是用青囊、罗盘、铜钱和物证判断规则的债务拆解者。", {"motivation": "救人并查父亲", "wound": "不愿承认自己是本金"}),
        _mat(project_id, "character_archetypes", "evidence-pressure-detective", "现实证据压迫者", "不信灵异但追求证据的人物，用法律和物证持续逼迫主角解释异常。", {"function": "现实压力", "risk": "误抓主角"}),
        _mat(project_id, "character_archetypes", "ambivalent-keeper", "隐瞒真相的守镜人", "知道大量规则但每次只说对自己有利的一半，既是信息源也是阻力。", {"function": "半导师半反派"}),
        _mat(project_id, "character_archetypes", "borrowed-face-threat", "借脸身份威胁", "镜影借熟人脸行动，不靠战斗压迫，而靠关系误判和身份栽赃压迫主角。", {"function": "身份危机"}),
    ]

    rows += [
        _mat(project_id, "character_templates", "lin-yuan", "林渊", "落魄风水师与青囊执卷人，核心爽点是用民俗方法和证据链拆解镜债规则。", {"role": "protagonist", "methods": ["青囊", "罗盘", "铜钱", "阴阳眼"]}),
        _mat(project_id, "character_templates", "su-wanning", "苏婉宁", "现实案件线负责人，以监控、尸检和封楼持续压缩林渊空间，不应工具人化。", {"role": "police_pressure", "boundary": "不无条件相信灵异"}),
        _mat(project_id, "character_templates", "sun-jiujin", "孙九斤", "懂旧城民俗和桃木铜钱的怕死盟友，前期提供资源，中期必须用命还债。", {"role": "folk_resource_ally", "wound": "欠林正淳一命"}),
        _mat(project_id, "character_templates", "qian-poppo", "钱婆婆", "钱家守镜人，既恨林家又怕困魂镜失控，每次揭示真相都保留钱家利益。", {"role": "ambivalent_keeper"}),
        _mat(project_id, "character_templates", "chen-mo", "陈默", "主动入镜的人质线，证明镜局会利用善意和替认；救他必须先揭示他替谁认账。", {"role": "hostage", "rule": "替认"}),
        _mat(project_id, "character_templates", "lin-jiahui", "林家辉", "林渊祖父，三十年前补镜，眉心疤与康熙铜钱绑定。", {"role": "grandfather_debt", "must_not_confuse_with": "林远山"}),
        _mat(project_id, "character_templates", "lin-zhengchun", "林正淳", "林渊父亲，二十三年前第一次入局，三年前再次入镜并替林渊挡债。", {"role": "father_mystery"}),
        _mat(project_id, "character_templates", "lin-yuanshan", "林远山", "三百年前封镜先祖，只能作为契约源头和旧账源头出现。", {"role": "ancestor_contract", "forbidden": "父亲/爷爷"}),
    ]

    rows += [
        _mat(project_id, "plot_patterns", "case-to-contract-loop", "单元案回流三族契约", "每个短案先交付局部解谜爽点，再把线索回收到林家、张家、钱家的旧账结构。", {"steps": ["入口异常", "规则试探", "局部破局", "长线钩子"]}),
        _mat(project_id, "plot_patterns", "rule-reveal-cost-escalation", "规则揭示与代价升级", "规则第一次出现必须可见，第二次必须被利用，第三次必须反噬。", {"rhythm": "显规则-用规则-付代价"}),
        _mat(project_id, "plot_patterns", "reality-and-occult-cross-pressure", "现实证据与灵异规则夹击", "苏婉宁推进现实物证，困魂镜污染证据，林渊必须同时解决法律和阴债压力。", {"pressure": ["警方", "镜影", "尸检"]}),
        _mat(project_id, "plot_patterns", "old-account-reversal", "旧账反转结构", "新危机看似来自当前受害者，结尾反转为旧账催收，迫使主角回十七栋。", {"payoff": "前账未清"}),
    ]

    rows += [
        _mat(project_id, "scene_templates", "folk-method-deduction", "民俗方法推理场景", "林渊用方位、铜钱、罗盘或门槛痕迹推理，而不是直接宣布答案。", {"beats": ["观察物证", "民俗解释", "现实验证", "代价显露"]}),
        _mat(project_id, "scene_templates", "receipt-object-horror", "回执物证恐怖场景", "尸体、镜片、手机或旧物成为回执载体，恐怖和证据同时推进。", {"beats": ["发现物", "反光异常", "身份风险"]}),
        _mat(project_id, "scene_templates", "mirror-face-misidentification", "借脸误认场景", "镜影借林渊或林家辉的脸出场，重点制造关系误判而非普通打斗。", {"beats": ["熟脸出现", "细节不对", "关系崩裂"]}),
        _mat(project_id, "scene_templates", "account-page-reveal", "账页揭示场景", "账页出现新名字或旧印，只给账法和代价，不给完整答案。", {"beats": ["显字", "误读", "代价", "新钩子"]}),
        _mat(project_id, "scene_templates", "police-evidence-interrupt", "警方物证打断场景", "苏婉宁带来监控、尸检或封楼命令，迫使林渊改变灵异处理节奏。", {"beats": ["物证压迫", "无法解释", "抢时间"]}),
        _mat(project_id, "scene_templates", "old-door-knock", "旧门敲门场景", "三短一长的敲门声召回父亲线，必须真假难辨并带出新账。", {"beats": ["敲门", "熟悉节奏", "验证失败", "账本催促"]}),
    ]

    rows += [
        _mat(project_id, "device_templates", "qingnang-ledger", "青囊秘卷", "只记因果、账印、方位和代价，不替任何人赎罪。", {"power": "显字记账", "limit": "不给答案", "cost": "林家账压"}),
        _mat(project_id, "device_templates", "kangxi-coin", "康熙铜钱", "林家辉补镜相关物，能暂封、验阴阳或缝镜，但会把债转成烙印。", {"power": "暂封/验痕", "cost": "债印扩散"}),
        _mat(project_id, "device_templates", "receipt-mirror-shard", "回执镜片", "死者离局后的镜门凭证，能让镜债从现实外开门。", {"power": "外开门", "risk": "镜影行动"}),
        _mat(project_id, "device_templates", "zhang-door-contract", "张家门契", "开门资格和张家内鬼交易的物证，连接旧货市场和十七栋旧门。", {"power": "标记门权", "risk": "被夺开门"}),
        _mat(project_id, "device_templates", "well-mouth-coin", "井口铜钱", "第32-37章应出现的关键物，指向林家辉三十年前补镜代价。", {"future_use": "第一账入口"}),
    ]

    rows += [
        _mat(project_id, "thematic_motifs", "debt-and-name", "债与名字", "名字不是身份标签，而是账本收人的入口；真名越清晰，代价越近。", {"symbols": ["名单", "账页", "照名"]}),
        _mat(project_id, "thematic_motifs", "mirror-and-face", "镜与脸", "镜子不只是恐怖物，而是身份、替罪和被冒名的象征。", {"symbols": ["无影", "借脸", "回执"]}),
        _mat(project_id, "thematic_motifs", "old-door-knocking", "旧门敲声", "三短一长连接亲情、骗局和旧账催收，任何回应都要先验真假。", {"symbols": ["旧门", "敲声", "父亲"]}),
        _mat(project_id, "thematic_motifs", "coin-scar-ledger", "铜钱与疤", "康熙铜钱和眉心疤把林家辉旧债视觉化，是祖父线的重复意象。", {"symbols": ["铜钱", "眉心疤"]}),
    ]

    rows += [
        _mat(project_id, "emotion_arcs", "fear-to-rule-control", "恐惧到掌控规则", "场景情绪从被怪异压迫，转为林渊找到规则并反压对方，但结尾必须付出新代价。", {"beats": ["恐惧", "观察", "推断", "反压", "反噬"]}),
        _mat(project_id, "emotion_arcs", "trust-to-evidence-conflict", "信任到证据冲突", "苏婉宁线的情绪不是暧昧，而是证据与经验互相不信任后的有限协作。", {"beats": ["怀疑", "物证", "无法解释", "临时合作"]}),
        _mat(project_id, "emotion_arcs", "family-truth-wound", "亲情真相创伤", "林渊每接近父亲或祖父真相，都必须面对亲人可能也欠债的刺痛。", {"beats": ["想救", "发现隐瞒", "愤怒", "继续追"]}),
    ]

    rows += [
        _mat(project_id, "dialogue_styles", "ledger-cross-examination", "账本式逼问", "林渊逼问时不靠大段解释，而是用一个物证、一条规则、一个无法否认的问题推进。", {"style": "短句、反问、证据落点"}),
        _mat(project_id, "dialogue_styles", "qian-poppo-half-truth", "钱婆婆半真半假", "钱婆婆说话要像砂纸，给真相也留缺口，每一句都带钱家自保。", {"style": "干涩、讽刺、避重就轻"}),
        _mat(project_id, "dialogue_styles", "police-evidence-speech", "警方证据话术", "苏婉宁台词以时间、物证、程序和嫌疑链为核心，压迫感来自现实逻辑。", {"style": "冷静、具体、证据导向"}),
    ]

    rows += [
        _mat(project_id, "anti_cliche_patterns", "no-random-ghost-case", "禁止无关怪谈", "第一卷所有异闻必须证明来自十七栋、三族契约、回执外溢或青囊反噬。", {"avoid": "每几章换鬼"}),
        _mat(project_id, "anti_cliche_patterns", "no-all-knowing-elder", "禁止全知老人讲完真相", "钱婆婆、旧账页、父亲回声都只能给局部信息，真相必须通过物证和规则拼出。", {"avoid": "口述大揭秘"}),
        _mat(project_id, "anti_cliche_patterns", "no-free-magic-solve", "禁止无代价法术解题", "铜钱、青囊、罗盘每次有效都必须带身体、身份或关系代价。", {"avoid": "主角突然开挂"}),
    ]

    rows += [
        _mat(project_id, "real_world_references", "folk-fengshui-methods", "风水方位方法库", "门槛、方位、时辰、罗盘、铜钱这些民俗方法要作为推理证据使用。", {"methods": ["方位", "时辰", "罗盘", "门槛灰"]}),
        _mat(project_id, "real_world_references", "police-evidence-chain", "现实证据链方法", "监控、尸检、封楼、笔录、物证保管能给灵异事件制造现实限制。", {"methods": ["监控", "尸检", "封楼", "笔录"]}),
        _mat(project_id, "real_world_references", "chinese-mirror-taboo", "中式镜忌素材", "镜子、反光面、魂魄、照名和替身禁忌可作为困魂镜规则的民俗底层。", {"motifs": ["镜", "照", "替身", "魂"]}),
    ]

    return rows
