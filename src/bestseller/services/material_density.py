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
    protagonist_name = _project_protagonist_override(project, metadata)
    policy = _decision_policy_for_pack(pack_id, protagonist_name=protagonist_name)
    ledger = _initial_premium_state_ledger_for_pack(
        pack_id,
        protagonist_name=protagonist_name,
    )
    world_spec = _premium_world_spec_for_pack(pack_id, protagonist_name=protagonist_name)
    cast_spec = _premium_cast_spec_for_pack(pack_id, protagonist_name=protagonist_name)
    volume_plan = _premium_volume_plan_for_pack(pack_id, protagonist_name=protagonist_name)
    if not policy or not ledger:
        return {"project_id": project_id, "present": False, "skipped": "no_capability_pack"}

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


def _metadata_text_at(metadata: dict[str, Any], path: tuple[str, ...]) -> str | None:
    value: object = metadata
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _project_protagonist_override(
    project: ProjectModel,
    metadata: dict[str, Any],
) -> str | None:
    for path in (
        ("cast_spec", "protagonist", "name"),
        ("book_spec", "cast_spec", "protagonist", "name"),
        ("premium_cast_spec", "protagonist", "name"),
        ("character", "name"),
        ("protagonist_name",),
    ):
        value = _metadata_text_at(metadata, path)
        if value:
            return value
    return None


def _decision_policy_for_pack(
    pack_id: str,
    *,
    protagonist_name: str | None = None,
) -> dict[str, Any] | None:
    # Neutral placeholder when no upstream name is chosen yet — the conception/
    # planner LLM names the protagonist, instead of every pack-hit book reusing
    # the same baked protagonist name. See conception._naming_constraint_block.
    proto_override = (
        protagonist_name.strip()
        if isinstance(protagonist_name, str) and protagonist_name.strip()
        else "主角"
    )
    raw: dict[str, Any] | None = None
    if pack_id == "qingnang":
        raw = {
            "character_name": "主角",
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
                {"key": "ignore_police_evidence", "description": "不得无视警方负责人的现实证据压力。"},
                {"key": "random_ghost_hunt", "description": "不得偏离十七栋、三族旧契和青囊因果账。"},
            ],
        }
    elif pack_id == "english_romantasy":
        raw = {
            "character_name": "the heroine",
            "archetype": "agency-first-shadowbound-romantasy-heroine",
            "risk_tolerance": "medium",
            "pressure_responses": ["observe", "bargain", "conceal", "protect", "prepare", "retreat"],
            "preferred_tactics": [
                {"key": "bargain_with_terms", "description": "Turn fae offers into explicit terms, witnesses, loopholes, and costs."},
                {"key": "protect_agency", "description": "Preserve the heroine's choice before accepting romance, court power, or prophecy."},
                {"key": "test_shadow_cost", "description": "Verify shadow sight through a small cost before using it publicly."},
            ],
            "moral_boundaries": [
                {"key": "do_not_trade_friend_consent", "description": "the heroine cannot trade another person's freedom or consent for court advantage."},
            ],
            "forbidden_behaviors": [
                {"key": "surrender_agency_for_romance", "description": "Romance cannot solve the central political or magical problem for her."},
                {"key": "trust_fae_oath_for_free", "description": "No fae oath is safe unless price, witness, and loophole are visible."},
                {"key": "costless_shadow_power", "description": "Shadow sight must carry a cost, exposure risk, or court consequence."},
            ],
        }
    elif pack_id in {"english_superhero_breaking_point", "english_superhero_progression"}:
        raw = {
            "character_name": "the protagonist",
            "archetype": "measured-civilian-protection-superhero-progression-lead",
            "risk_tolerance": "medium",
            "pressure_responses": ["protect", "prepare", "investigate", "conceal", "strike_after_certainty", "retreat"],
            "preferred_tactics": [
                {"key": "measure_power_delta", "description": "Treat each power use as measurable load, cost, and public evidence."},
                {"key": "evacuate_civilians_first", "description": "Civilian safety beats spectacle and public glory."},
                {"key": "train_before_escalation", "description": "Upgrade through training, data, and consequence rather than sudden mastery."},
            ],
            "moral_boundaries": [
                {"key": "do_not_create_collateral_for_status", "description": "the protagonist cannot endanger civilians to prove he belongs among heroes."},
            ],
            "forbidden_behaviors": [
                {"key": "glory_fight", "description": "Do not accept public fights for vanity or leaderboard status."},
                {"key": "ignore_sophie_deadline", "description": "Family pressure must remain a real constraint."},
                {"key": "costless_power_jump", "description": "New power tiers require load, injury, evidence, or social backlash."},
            ],
        }
    elif pack_id == "english_superhero_witness_protocol":
        raw = {
            "character_name": "the protagonist",
            "archetype": "evidence-first-mimicry-survivor",
            "risk_tolerance": "low",
            "pressure_responses": ["observe", "investigate", "conceal", "prepare", "protect", "retreat"],
            "preferred_tactics": [
                {"key": "record_before_action", "description": "Secure witness evidence before confronting a stronger faction."},
                {"key": "use_sixty_second_window", "description": "Plan mimicry actions around the sixty-second limit and aftermath."},
                {"key": "protect_maya_boundary", "description": "Keep the family anchor's safety and consent visible in every escalation."},
            ],
            "moral_boundaries": [
                {"key": "do_not_frame_innocent", "description": "the protagonist cannot use mimicry to shift blame onto an innocent person."},
            ],
            "forbidden_behaviors": [
                {"key": "identity_theft_without_cost", "description": "Mimicry must create evidence, trust, or legal consequences."},
                {"key": "rush_without_record", "description": "Do not confront factions without recording a useful witness trail."},
                {"key": "ignore_registry_pressure", "description": "Registry and surveillance pressure must constrain choices."},
            ],
        }
    elif pack_id == "female_no_cp_apocalypse":
        raw = {
            "character_name": proto_override,
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
            "character_name": proto_override,
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
        protagonist = (
            protagonist_name.strip()
            if isinstance(protagonist_name, str) and protagonist_name.strip()
            else blueprint.protagonist_en
            if is_en
            else blueprint.protagonist_zh
        )
        raw = {
            "character_name": protagonist,
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
    *,
    protagonist_name: str | None = None,
) -> dict[str, list[dict[str, Any]]] | None:
    # Neutral placeholder unless an upstream name is chosen — keeps the de-named
    # zh packs consistent with premium_cast_spec / decision_policy.
    proto = (
        protagonist_name.strip()
        if isinstance(protagonist_name, str) and protagonist_name.strip()
        else "主角"
    )
    if pack_id == "qingnang":
        return {
            "progression_events": [
                {
                    "event_type": "resource_gained",
                    "subject": "主角",
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
                    "trigger": "主角继续追查十七栋",
                    "reaction": "各家只交出对自己有利的一半真相并试图转嫁旧债",
                    "next_pressure": "开门家族门契与守镜家族守镜线继续加压",
                }
            ],
            "relationship_events": [
                {
                    "character_a": "主角",
                    "character_b": "警方负责人",
                    "axis": "trust",
                    "after": "证据互不完全信任但必须协作",
                    "active_choice": "主角优先给出可验证物证而非要求她相信灵异",
                }
            ],
            "agency_debts": [
                {"owner": "主角", "debt": "查清父亲入镜和三族第一账", "due_window": "第一卷后段"}
            ],
        }
    if pack_id == "english_romantasy":
        return {
            "progression_events": [
                {
                    "event_type": "technique_unlock",
                    "subject": "the heroine",
                    "technique": "Shadow Sight",
                    "cause": "Court exile record exposes her connection to the Two-Court Shadow Crown.",
                }
            ],
            "rule_events": [
                {
                    "rule_code": "court-bargain-grammar",
                    "visible_effect": "Every fae bargain creates witnessable terms, loopholes, and a price.",
                    "cost": "A vague promise can bind the heroine's agency or court standing.",
                }
            ],
            "faction_reactions": [
                {
                    "faction": "Summer Court",
                    "trigger": "the heroine becomes legible to the Shadow Crown",
                    "reaction": "They offer protection with ownership terms attached.",
                    "next_pressure": "Force the heroine to choose between safety and autonomy.",
                }
            ],
            "relationship_events": [
                {
                    "character_a": "the heroine",
                    "character_b": "the records antagonist",
                    "axis": "trust",
                    "after": "attraction exists, but political trust remains conditional",
                    "active_choice": "the heroine demands terms before accepting the records antagonist's help.",
                }
            ],
            "agency_debts": [
                {
                    "owner": "the heroine",
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
                    "subject": "the protagonist",
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
                    "trigger": "the protagonist's kinetic signature appears in public incident records",
                    "reaction": "They classify him as an unregistered escalation risk.",
                    "next_pressure": "Registry summons and surveillance pressure tighten.",
                }
            ],
            "relationship_events": [
                {
                    "character_a": "the protagonist",
                    "character_b": "the family anchor",
                    "axis": "promise",
                    "after": "family deadline remains active",
                    "active_choice": "the protagonist must protect the family anchor without turning her into passive leverage.",
                }
            ],
            "agency_debts": [
                {
                    "owner": "the protagonist",
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
                    "subject": "the protagonist",
                    "technique": "Sixty-Second Mimicry",
                    "cause": "Witness protocol exposure gives him a short identity-copy window.",
                }
            ],
            "rule_events": [
                {
                    "rule_code": "sixty-second-mimicry-limit",
                    "visible_effect": "the protagonist can copy a visible power/identity trace for sixty seconds.",
                    "cost": "Every use creates surveillance, legal, or trust evidence.",
                }
            ],
            "faction_reactions": [
                {
                    "faction": "Registry",
                    "trigger": "the protagonist's mimicry generates conflicting witness records",
                    "reaction": "They treat him as evidence contamination, not just a powered suspect.",
                    "next_pressure": "Force the protagonist to preserve proof before each confrontation.",
                }
            ],
            "relationship_events": [
                {
                    "character_a": "the protagonist",
                    "character_b": "the family anchor",
                    "axis": "trust",
                    "after": "the family anchor helps only while the protagonist preserves consent and evidence integrity",
                    "active_choice": "the protagonist records proof before asking the family anchor to take risk.",
                }
            ],
            "agency_debts": [
                {
                    "owner": "the protagonist",
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
                    "subject": proto,
                    "technique": "代价转化",
                    "cause": "方舟城压力和源初追猎迫使她把痛感转成可计算资源。",
                }
            ],
            "rule_events": [
                {
                    "rule_code": "cost-conversion-rule",
                    "visible_effect": "主角能把伤痛、记忆或关系代价转成异能收益",
                    "cost": "代价会留下身体、人格或源初侵蚀后账",
                }
            ],
            "faction_reactions": [
                {
                    "faction": "清道夫",
                    "trigger": "主角的代价转化能力被记录",
                    "reaction": "追猎者把她列为高价值追猎对象，同时评估她的弱点",
                    "next_pressure": "追猎、收容和联盟登记压力升级",
                }
            ],
            "relationship_events": [
                {
                    "character_a": proto,
                    "character_b": "追猎者",
                    "axis": "power",
                    "after": "敌对但互为价值观镜像",
                    "active_choice": "主角拒绝被清道夫定义为污染源",
                }
            ],
            "agency_debts": [
                {
                    "owner": proto,
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
                    "subject": proto,
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
                    "trigger": "主角表现出不符合废灵根身份的进步",
                    "reaction": "执事先克扣资源再试探其后手",
                    "next_pressure": "配给账、考核台和秘境名额继续施压",
                }
            ],
            "relationship_events": [
                {
                    "character_a": proto,
                    "character_b": "内门盟友",
                    "axis": "trust",
                    "after": "互相试探的有限同盟",
                    "active_choice": "主角只交换可验证情报，不暴露道种核心",
                }
            ],
            "agency_debts": [
                {"owner": proto, "debt": "在秘境大考前证明低位反制不是侥幸", "due_window": "三个月大考前"}
            ],
        }
    if blueprint := _blueprint_for_pack(pack_id):
        is_en = _pack_is_english(pack_id)
        protagonist = (
            protagonist_name.strip()
            if isinstance(protagonist_name, str) and protagonist_name.strip()
            else blueprint.protagonist_en
            if is_en
            else blueprint.protagonist_zh
        )
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


def _premium_context_seed(
    pack_id: str,
    *,
    protagonist_name: str | None = None,
) -> dict[str, Any] | None:
    seeds: dict[str, dict[str, Any]] = {
        "qingnang": {
            "world_name": "十七栋镜债都市",
            "system": "青囊因果账",
            "tiers": ["接案", "试探规则", "认账破局", "旧账追索"],
            "starting_tier": "试探规则",
            "protagonist": "主角",
            "power_structure": "青囊、困魂镜、三族旧契和现实证据链共同限制破局。",
            "volume_title": "十七栋主镜门",
        },
        "english_romantasy": {
            "world_name": "The Two-Court Shadow Crown",
            "system": "Court Bargain and Shadow Sight",
            "tiers": ["Unbound Exile", "Shadow-Sighted", "Court-Bargained", "Crown-Claimant"],
            "starting_tier": "Shadow-Sighted",
            "protagonist": "the heroine",
            "power_structure": "Fae power moves through witnessed bargains, court status, agency, and visible price.",
            "volume_title": "The First Shadow Bargain",
        },
        "english_superhero_breaking_point": {
            "world_name": "Registry Pressure City",
            "system": "Reservoir Kinetics",
            "tiers": ["Unregistered", "Controlled Burst", "Public Incident", "Registry Target"],
            "starting_tier": "Controlled Burst",
            "protagonist": "the protagonist",
            "power_structure": "Public records, measurable load, injuries, and enforcement response bound every upgrade.",
            "volume_title": "Reservoir Incident",
        },
        "english_superhero_progression": {
            "world_name": "Registry Pressure City",
            "system": "Measured Power Progression",
            "tiers": ["Unregistered", "Controlled Burst", "Public Incident", "Registry Target"],
            "starting_tier": "Controlled Burst",
            "protagonist": "the protagonist",
            "power_structure": "Power growth must be measurable and must change public, family, and faction pressure.",
            "volume_title": "First Public Incident",
        },
        "english_superhero_witness_protocol": {
            "world_name": "Witness Protocol City",
            "system": "Sixty-Second Mimicry",
            "tiers": ["Witness", "Mimic Window", "Evidence Contaminant", "Protocol Breaker"],
            "starting_tier": "Mimic Window",
            "protagonist": "the protagonist",
            "power_structure": "Mimicry power is constrained by time windows, evidence integrity, surveillance, and consent.",
            "volume_title": "The First False Record",
        },
        "female_no_cp_apocalypse": {
            "world_name": "方舟城末世秩序",
            "system": "代价转化",
            "tiers": ["觉醒", "代价可计量", "方舟城博弈", "源初对抗"],
            "starting_tier": "代价可计量",
            "protagonist": "主角",
            "power_structure": "异能收益必须经过痛感、记忆、关系或源初侵蚀的明确代价。",
            "volume_title": "方舟城代价账",
        },
        "xianxia_upgrade": {
            "world_name": "末法宗门",
            "system": "道种修行",
            "tiers": ["炼气", "筑基", "金丹"],
            "starting_tier": "炼气",
            "protagonist": "主角",
            "power_structure": "境界突破受灵米、丹药、名额、道种痕迹和宗门反馈约束。",
            "volume_title": "杂役峰道种初动",
        },
    }
    if pack_id in seeds:
        seed = dict(seeds[pack_id])
        if isinstance(protagonist_name, str) and protagonist_name.strip():
            seed["protagonist"] = protagonist_name.strip()
        return seed
    if blueprint := _blueprint_for_pack(pack_id):
        is_en = _pack_is_english(pack_id)
        protagonist = (
            protagonist_name.strip()
            if isinstance(protagonist_name, str) and protagonist_name.strip()
            else blueprint.protagonist_en
            if is_en
            else blueprint.protagonist_zh
        )
        return {
            "world_name": blueprint.world_en if is_en else blueprint.world_zh,
            "system": blueprint.system_en if is_en else blueprint.system_zh,
            "tiers": list(blueprint.tiers_en if is_en else blueprint.tiers_zh),
            "starting_tier": blueprint.start_tier_en if is_en else blueprint.start_tier_zh,
            "protagonist": protagonist,
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


def _premium_world_spec_for_pack(
    pack_id: str,
    *,
    protagonist_name: str | None = None,
) -> dict[str, Any] | None:
    seed = _premium_context_seed(pack_id, protagonist_name=protagonist_name)
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


def _premium_cast_spec_for_pack(
    pack_id: str,
    *,
    protagonist_name: str | None = None,
) -> dict[str, Any] | None:
    seed = _premium_context_seed(pack_id, protagonist_name=protagonist_name)
    if not seed:
        return None
    return {
        "protagonist": {
            "name": seed["protagonist"],
            "role": "protagonist",
            "power_tier": seed["starting_tier"],
        }
    }


def _premium_volume_plan_for_pack(
    pack_id: str,
    *,
    protagonist_name: str | None = None,
) -> list[dict[str, Any]]:
    seed = _premium_context_seed(pack_id, protagonist_name=protagonist_name)
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


def _select_material_pack(
    project_id: str,
    package_text: str,
    *,
    title: str | None = None,
    genre: str | None = None,
    sub_genre: str | None = None,
    language: str | None = None,
) -> tuple[str | None, list[ProjectMaterial]]:
    """Route a project to a CATEGORY-level material blueprint only.

    (2026-07-31 product ruling) Every single-book reference pack —
    qingnang / 道种破虚 / 代价之鸢 / shadowbound / breaking point / witness
    protocol — was DELETED: a new book must never inherit a historical book's
    private world, cast, or mechanisms, no matter how exact the routing token.
    Only genre-level category blueprints (the world type the user selected by
    picking the genre) remain.
    """
    lower = " ".join(
        str(part or "")
        for part in (title, genre, sub_genre, language, package_text[:12000])
    ).lower()
    if _has_any(lower, ("superhero", "super hero", "urban power")):
        return "english_superhero_progression", _build_spec_pack(
            project_id,
            _generic_superhero_pack_spec(),
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
        protagonist_zh="主角",
        protagonist_en="The Protagonist",
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
        protagonist_zh="主角",
        protagonist_en="The Protagonist",
        archetype_key="relationship-boundary-choice-lead",
        risk_tolerance="medium",
        system_zh="关系亏欠与边界选择",
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
        rule_effect_zh="每个情感节点必须改变信任、边界、亏欠或共同目标。",
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
        protagonist_zh="主角",
        protagonist_en="The Protagonist",
        archetype_key="evidence-led-misdirection-investigator",
        risk_tolerance="low",
        system_zh="线索链与误导迷局",
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
        protagonist_zh="主角",
        protagonist_en="The Protagonist",
        archetype_key="systems-strategy-worldbuilder",
        risk_tolerance="medium",
        system_zh="势力格局与资源博弈",
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
        rule_cost_zh="胜利会制造补给缺口、政治人情、暴露风险或更强对手。",
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
        protagonist_zh="主角",
        protagonist_en="The Protagonist",
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
        protagonist_zh="主角",
        protagonist_en="The Protagonist",
        archetype_key="agency-first-nonromantic-growth-lead",
        risk_tolerance="medium",
        system_zh="选择权与非恋爱同盟网",
        system_en="Agency and Nonromantic Alliance Ledger",
        world_zh="选择权重建世界",
        world_en="Agency-Rebuilding World",
        tiers_zh=("被定义", "夺回定价", "同盟重组", "自我立法"),
        tiers_en=("Defined by Others", "Repricing Herself", "Alliance Rebuilt", "Self-Legislated"),
        start_tier_zh="夺回定价",
        start_tier_en="Repricing Herself",
        power_structure_zh="成长来自边界、资源、技能、同盟和旧怨了结，不靠恋爱救援。",
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
        protagonist_zh="主角",
        protagonist_en="The Protagonist",
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
        protagonist_zh="主角",
        protagonist_en="The Protagonist",
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
                    _s(f"{base}-persistent-state-world", "Persistent State World", "Plot, power, relationships, factions, and resources persist after each chapter instead of resetting.", category=base, rule="persistent state"),
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
                    _s(f"{base}-progress-track-device", "Progress-Tracking Device", "A file, board, roster, ranking, or map that externalizes progress.", function="progress tracking"),
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
                _s(f"{base}-persistent-state-world", "持续状态世界", "剧情、能力、关系、阵营和资源在每章后持续存在，不允许重置。", category=base, rule="状态持续"),
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
                _s(f"{base}-cost-accounting-rule", "代价追踪规则", rule_cost, fields=["代价", "承担者", "可见性", "回调"]),
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
                _s(f"{base}-cost-aftermath", "代价后果场景", "胜利之后记录身体、资源、关系或阵营代价。", beats=["疼痛", "代价落地", "新限制"]),
                _s(f"{base}-state-hook-ending", "状态钩子结尾", "以明确状态更新和新暴露问题收束。", beats=["变化", "揭示", "新压力"]),
            ],
            "device_templates": [
                _s(f"{base}-progress-track-device", "进度外化装置", "文件、白板、名册、排名、日志或地图，把进展外化。", function="进度追踪"),
                _s(f"{base}-threshold-token", "门槛信物", "给出进入资格，同时改变义务。", function="带代价的入口"),
                _s(f"{base}-evidence-prop", "证据化道具", "线索、影像、收据、伤痕或记录，让品类规则具体化。", function="证明"),
                _s(f"{base}-resource-key", "稀缺资源钥匙", "强迫主角做交换和取舍的稀缺物或能力。", function="稀缺"),
                _s(f"{base}-faction-signal", "阵营信号物", "徽记、消息、传闻、印章或公开标记，显示阵营反应。", function="压力标记"),
            ],
            "thematic_motifs": [
                _s(f"{base}-choice-and-price", "选择与代价", "只有代价可见，主动性才有意义。", symbols=["选择", "价格"]),
                _s(f"{base}-state-and-memory", "状态与记忆", "世界会记住胜利、谎言、失败和承诺。", symbols=["印记", "疤"]),
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
                _s(f"{base}-subtext-dialogue", "潜台词台词", "表层话里藏亏欠、吸引、怀疑或算计。", style="多层潜台词"),
                _s(f"{base}-institutional-dialogue", "制度型台词", "权力方使用流程、数字、契约、等级或公开评价施压。", style="正式压迫"),
            ],
            # 2026-08-02 用 no-unearned-win 替换 no-free-win。原条目按题材自动生成
            # 进每一本书，内容是 rule_cost（"升级不能免费，必须留下消耗、暴露、反噬
            # 或新敌意"）——正是纯爽门用来处决书的那些词：框架一边命令写反噬，一边
            # 因反噬杀书。胜利该怎么挣来由这本书决定，但"白给"依然是坏写法。
            "anti_cliche_patterns": [
                _s(f"{base}-no-vague-progress", "禁止虚假推进", "没有状态变量改变，就不能声称剧情推进。", avoid="空转"),
                _s(f"{base}-no-unearned-win", "禁止白给胜利", "胜利要由主角的选择和行动挣来，不能靠巧合或对手降智直接送。", avoid="天上掉结果"),
                _s(f"{base}-no-category-drift", "禁止品类漂移", f"不得用无关套路替代{name}核心机制。", avoid="读者承诺破裂"),
            ],
            "real_world_references": [
                _s(f"{base}-beat-sheet-reference", "商业节拍表", "跟踪场景目标、阻力、策略、转折、代价和钩子。", methods=["目标", "转折", "钩子"]),
                _s(f"{base}-institutional-logic", "制度逻辑参考", "用规则、激励、文书、排名、物流或公开记录落地压力。", methods=["激励", "记录"]),
                _s(f"{base}-continuity-ledger", "连续性记录", "维护能力、关系、阵营、资源和线索的前后状态。", methods=["之前", "之后"]),
            ],
        },
    )


# (2026-07-31 product ruling) The single-book reference packs
# (_english_romantasy_pack_spec / _breaking_point_pack_spec /
# _witness_protocol_pack_spec) were deleted: historical demo books must not
# live in framework source. Only the genre-generic superhero blueprint stays.
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


# (2026-07-31 product ruling) _build_qingnang_pack — the full private world of
# 《青囊不语问阴阳》 (three-clan mirror-debt contracts, named cast, ledger
# rules) — was deleted together with the other single-book reference packs.
# Historical books' data must never seed a new book.
