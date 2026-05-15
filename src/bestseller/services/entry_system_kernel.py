from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from bestseller.domain.entry_system import (
    CapabilityAxis,
    EntryAcquisitionModel,
    EntryCostModel,
    EntryGradeLadder,
    EntryGradeLevel,
    EntryLifecycle,
    EntrySystemKernel,
    EntryTypeDefinition,
)
from bestseller.services.entry_blueprint_library import EntryBlueprint


@dataclass(frozen=True, slots=True)
class EntrySystemFinding:
    code: str
    severity: str
    message: str
    path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
        }


_PROGRESSION_MARKERS = (
    "xianxia",
    "cultivation",
    "修仙",
    "仙侠",
    "玄幻",
    "升级",
    "凡人",
    "litrpg",
    "system",
    "系统",
)

_MYSTERY_RULE_MARKERS = (
    "mystery",
    "suspense",
    "horror",
    "rule",
    "悬疑",
    "规则",
    "诡异",
    "灵异",
    "生存",
)

_APOCALYPSE_MARKERS = (
    "apocalypse",
    "末日",
    "末世",
    "灾变",
    "基地",
    "survival",
)

_RELATIONSHIP_MARKERS = (
    "romance",
    "relationship",
    "言情",
    "女频",
    "感情",
    "成长",
)


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _get(obj: object, key: str, default: object = None) -> object:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _contains_any(text: str, markers: Sequence[str]) -> bool:
    normalized = text.lower()
    return any(marker.lower() in normalized for marker in markers)


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return tuple(out)


def _story_design_keywords(story_design_kernel: Mapping[str, object]) -> tuple[str, ...]:
    keywords: list[str] = []
    keywords.extend(_text(item) for item in _as_sequence(story_design_kernel.get("change_vectors")))
    keywords.append(_text(story_design_kernel.get("reader_promise")))
    premise = _as_mapping(story_design_kernel.get("premise_contract"))
    keywords.extend(
        _text(premise.get(key))
        for key in ("unique_hook", "core_question", "commercial_pull")
    )
    return _dedupe(keywords)


def _genre_profile(project_like: object) -> str:
    return " ".join(
        filter(
            None,
            (
                _text(_get(project_like, "genre")),
                _text(_get(project_like, "sub_genre")),
                _text(_get(project_like, "category_key")),
            ),
        )
    )


def _taxonomy_for_profile(profile: str) -> tuple[EntryTypeDefinition, ...]:
    if _contains_any(profile, _PROGRESSION_MARKERS):
        specs = (
            (
                "artifact",
                "法宝",
                ("weapon", "proof", "key", "bond"),
                ("origin", "capabilities", "limits"),
            ),
            (
                "technique",
                "功法/术法",
                ("unlock", "combat", "breakthrough"),
                ("required_realm", "limits", "costs"),
            ),
            (
                "cultivation_method",
                "修行路径",
                ("growth_engine", "bottleneck"),
                ("core_principle", "grade_ladder", "cost_model"),
            ),
            (
                "resource",
                "资源",
                ("spend", "gate", "trade"),
                ("source", "amount_or_quality", "spend_rule"),
            ),
            (
                "identity",
                "身份/权限",
                ("access", "status", "pressure"),
                ("authority_scope", "visibility", "costs"),
            ),
            (
                "formation",
                "阵法/禁制",
                ("obstacle", "arena", "rule"),
                ("activation_rule", "break_rule", "costs"),
            ),
            (
                "companion_asset",
                "器灵/契约资产",
                ("ally", "debt", "constraint"),
                ("agency", "bonding_condition", "limits"),
            ),
            (
                "motif",
                "母题/意象",
                ("theme", "memory", "echo"),
                ("symbol", "meanings", "recurrence_rule"),
            ),
        )
    elif _contains_any(profile, _MYSTERY_RULE_MARKERS):
        specs = (
            (
                "evidence",
                "证据/线索",
                ("proof", "misdirection", "unlock"),
                ("source", "verification", "limits"),
            ),
            (
                "rule",
                "规则/禁忌",
                ("constraint", "threat", "solution"),
                ("trigger", "effect", "exploit", "cost"),
            ),
            (
                "identity",
                "身份/伪装",
                ("access", "suspicion", "reversal"),
                ("cover", "exposure_risk", "authority_scope"),
            ),
            (
                "location_access",
                "地点权限",
                ("gate", "danger", "clue"),
                ("entry_condition", "risk", "reward"),
            ),
            (
                "resource",
                "生存资源",
                ("survival", "trade", "timer"),
                ("quantity", "expiry", "cost"),
            ),
            (
                "threat",
                "威胁/污染",
                ("timer", "pressure", "backlash"),
                ("symptom", "escalation", "countermeasure"),
            ),
            (
                "motif",
                "母题/意象",
                ("theme", "foreshadowing"),
                ("symbol", "meaning", "payoff_rule"),
            ),
        )
    elif _contains_any(profile, _APOCALYPSE_MARKERS):
        specs = (
            (
                "resource",
                "资源",
                ("survival", "trade", "authority"),
                ("source", "quantity", "decay_or_cost"),
            ),
            (
                "tool",
                "工具/装备",
                ("solve_problem", "combat", "logistics"),
                ("condition", "capabilities", "limits"),
            ),
            (
                "zone",
                "区域/据点",
                ("safety", "threat", "resource"),
                ("access_rule", "risk", "resource_output"),
            ),
            (
                "identity",
                "权限/职位",
                ("command", "access", "faction_pressure"),
                ("scope", "witness", "cost"),
            ),
            (
                "faction_asset",
                "阵营资产",
                ("leverage", "defense", "production"),
                ("maintenance", "owner", "risk"),
            ),
            (
                "threat",
                "感染/灾害",
                ("timer", "pressure", "mutation"),
                ("stage", "symptom", "countermeasure"),
            ),
            (
                "motif",
                "母题/意象",
                ("theme", "memory"),
                ("symbol", "meaning", "recurrence_rule"),
            ),
        )
    elif _contains_any(profile, _RELATIONSHIP_MARKERS):
        specs = (
            (
                "promise_token",
                "承诺物",
                ("bond", "debt", "memory"),
                ("promise", "holder", "break_cost"),
            ),
            (
                "secret",
                "秘密",
                ("tension", "reveal", "choice"),
                ("owner", "risk", "reveal_condition"),
            ),
            (
                "identity",
                "身份/社会位置",
                ("status", "constraint", "access"),
                ("public_face", "private_reality", "cost"),
            ),
            (
                "obligation",
                "义务/债",
                ("pressure", "choice", "payoff"),
                ("owed_to", "deadline", "cost"),
            ),
            (
                "motif",
                "母题/意象",
                ("emotion", "memory", "theme"),
                ("symbol", "meaning", "recurrence_rule"),
            ),
        )
    else:
        specs = (
            (
                "artifact",
                "关键物",
                ("key", "proof", "cost"),
                ("origin", "capabilities", "limits"),
            ),
            (
                "resource",
                "资源",
                ("spend", "gate", "trade"),
                ("source", "quantity_or_quality", "cost"),
            ),
            (
                "evidence",
                "证据/信息",
                ("proof", "unlock", "misdirection"),
                ("source", "verification", "limits"),
            ),
            (
                "identity",
                "身份/权限",
                ("access", "status", "pressure"),
                ("scope", "visibility", "cost"),
            ),
            (
                "motif",
                "母题/意象",
                ("theme", "echo"),
                ("symbol", "meaning", "recurrence_rule"),
            ),
        )

    return tuple(
        EntryTypeDefinition(
            type=key,
            label=label,
            allowed_roles=roles,
            required_fields=fields,
            forbidden_patterns=("无代价万能", "同功能重复", "只改名不改机制"),
        )
        for key, label, roles, fields in specs
    )


def _grade_ladders_for_taxonomy(
    taxonomy: Sequence[EntryTypeDefinition],
) -> tuple[EntryGradeLadder, ...]:
    types = {item.type for item in taxonomy}
    ladders: list[EntryGradeLadder] = []
    if {"artifact", "tool", "faction_asset", "companion_asset"} & types:
        ladders.append(
            EntryGradeLadder(
                ladder_key="asset_grade",
                label="资产成熟度",
                applies_to=tuple(
                    sorted({"artifact", "tool", "faction_asset", "companion_asset"} & types)
                ),
                levels=(
                    EntryGradeLevel(
                        key="seeded",
                        name="伏笔",
                        capability_ceiling="只能被暗示或被动影响局势",
                    ),
                    EntryGradeLevel(
                        key="usable",
                        name="可用",
                        capability_ceiling="可解决局部问题但必须付出代价",
                    ),
                    EntryGradeLevel(
                        key="recognized",
                        name="被承认",
                        capability_ceiling="可改变他人判断或阵营态度",
                    ),
                    EntryGradeLevel(
                        key="contested",
                        name="被争夺",
                        capability_ceiling="可推动卷级冲突并引来反制",
                    ),
                ),
                promotion_rule=(
                    "升级必须由公开使用、资源投入、见证承认、危机触发或关系绑定之一造成,"
                    "并支付可见代价。"
                ),
            )
        )
    if {"technique", "cultivation_method"} & types:
        ladders.append(
            EntryGradeLadder(
                ladder_key="mastery_grade",
                label="掌握层级",
                applies_to=tuple(sorted({"technique", "cultivation_method"} & types)),
                levels=(
                    EntryGradeLevel(
                        key="known",
                        name="知其名",
                        capability_ceiling="只能理解方向,不能稳定使用",
                    ),
                    EntryGradeLevel(
                        key="practiced",
                        name="可施展",
                        capability_ceiling="可解决同级问题",
                    ),
                    EntryGradeLevel(
                        key="bottleneck",
                        name="临瓶颈",
                        capability_ceiling="可触及越级效果但代价上升",
                    ),
                    EntryGradeLevel(
                        key="integrated",
                        name="入体系",
                        capability_ceiling="可改变长期成长路径",
                    ),
                ),
                promotion_rule="掌握升级必须有练习、资源、试炼、顿悟、导师或失败复盘支撑。",
            )
        )
    if {"evidence", "rule", "identity", "location_access"} & types:
        ladders.append(
            EntryGradeLadder(
                ladder_key="legitimacy_grade",
                label="可信/权限强度",
                applies_to=tuple(
                    sorted({"evidence", "rule", "identity", "location_access"} & types)
                ),
                levels=(
                    EntryGradeLevel(key="rumor", name="传闻", capability_ceiling="只能制造怀疑"),
                    EntryGradeLevel(
                        key="private",
                        name="私证",
                        capability_ceiling="可影响少数知情者",
                    ),
                    EntryGradeLevel(key="public", name="公证", capability_ceiling="可改变群体态度"),
                    EntryGradeLevel(
                        key="institutional",
                        name="制度承认",
                        capability_ceiling="可改变规则或权限",
                    ),
                ),
                promotion_rule="可信度或权限提升必须通过证人、文书、公开事件、规则验证或权力承认完成。",
            )
        )
    if {"resource"} & types:
        ladders.append(
            EntryGradeLadder(
                ladder_key="resource_quality",
                label="资源品质",
                applies_to=("resource",),
                levels=(
                    EntryGradeLevel(
                        key="scarce",
                        name="稀缺",
                        capability_ceiling="只能支撑一次局部行动",
                    ),
                    EntryGradeLevel(
                        key="stable",
                        name="稳定",
                        capability_ceiling="可支撑一段行动计划",
                    ),
                    EntryGradeLevel(
                        key="strategic",
                        name="战略",
                        capability_ceiling="可改变阵营筹码",
                    ),
                ),
                promotion_rule="资源品质提升必须解释来源、保管成本和争夺压力。",
            )
        )
    return tuple(ladders)


def _default_cost_types(profile: str, blueprints: Sequence[EntryBlueprint]) -> tuple[str, ...]:
    values: list[str] = []
    if _contains_any(profile, _PROGRESSION_MARKERS):
        values.extend(["injury", "resource_spend", "exposure", "faction_attention"])
    elif _contains_any(profile, _MYSTERY_RULE_MARKERS):
        values.extend(["danger", "suspicion", "irreversible_knowledge", "time_pressure"])
    elif _contains_any(profile, _APOCALYPSE_MARKERS):
        values.extend(["scarcity", "decay", "maintenance", "faction_attention"])
    elif _contains_any(profile, _RELATIONSHIP_MARKERS):
        values.extend(["relationship_debt", "trust_loss", "public_exposure", "promise_cost"])
    else:
        values.extend(["cost", "exposure", "debt", "resource_spend"])
    for blueprint in blueprints:
        values.extend(blueprint.required_cost_patterns)
    return _dedupe(values)


def _capability_axes(
    taxonomy: Sequence[EntryTypeDefinition],
    blueprints: Sequence[EntryBlueprint],
) -> tuple[CapabilityAxis, ...]:
    axes: list[CapabilityAxis] = []
    type_keys = tuple(item.type for item in taxonomy)
    for blueprint in blueprints:
        for variable in blueprint.state_variables:
            axes.append(
                CapabilityAxis(
                    axis=variable,
                    label=variable.replace("_", " "),
                    meaning=f"由蒸馏蓝图 {blueprint.blueprint_id} 引入的词条状态变量。",
                    valid_for=blueprint.entry_types or type_keys,
                )
            )
    if not axes:
        axes = [
            CapabilityAxis(
                axis="story_leverage",
                label="剧情杠杆",
                meaning="词条改变角色选择、局势筹码或未来代价的能力。",
                valid_for=type_keys,
            ),
            CapabilityAxis(
                axis="visibility_pressure",
                label="可见性压力",
                meaning="词条从私密到公开后引发态度变化、争夺或反制的能力。",
                valid_for=type_keys,
            ),
        ]
    seen: set[str] = set()
    unique: list[CapabilityAxis] = []
    for axis in axes:
        if axis.axis not in seen:
            seen.add(axis.axis)
            unique.append(axis)
    return tuple(unique)


def _coverage_targets(
    taxonomy: Sequence[EntryTypeDefinition],
    *,
    target_chapters: int,
) -> dict[str, object]:
    if target_chapters <= 10:
        pillar, supporting = 3, 12
    elif target_chapters <= 80:
        pillar, supporting = 8, 50
    else:
        pillar, supporting = 15, 120
    active_axes = [item.type for item in taxonomy]
    return {
        "target_chapters": target_chapters,
        "active_axes": active_axes,
        "pillar_entries": min(max(3, len(active_axes)), pillar),
        "supporting_entries": supporting,
        "per_volume_minimums": {
            "new_entries": 4 if target_chapters > 20 else 2,
            "entry_state_changes": 8 if target_chapters > 20 else 3,
            "major_entry_payoffs": 1,
        },
    }


def build_fallback_entry_system_kernel(
    project_like: object,
    *,
    story_design_kernel: Mapping[str, object] | None = None,
    blueprints: Sequence[EntryBlueprint] = (),
) -> EntrySystemKernel:
    """Build a deterministic, book-specific entry kernel without LLM calls."""

    story_design = _as_mapping(story_design_kernel)
    profile = _genre_profile(project_like)
    taxonomy = _taxonomy_for_profile(profile)
    target_chapters_raw = _get(project_like, "target_chapters", 60)
    try:
        target_chapters = int(target_chapters_raw or 60)
    except (TypeError, ValueError):
        target_chapters = 60
    reader_promise = _text(story_design.get("reader_promise"))
    change_vectors = _story_design_keywords(story_design)
    blueprint_summaries = [bp.mechanism_summary for bp in blueprints[:3]]
    system_promise_parts = [
        reader_promise or "本书词条必须服务剧情推进、状态变化和可见代价。",
        *change_vectors[:2],
        *blueprint_summaries,
    ]
    anti_copy_rules = [
        "只借鉴抽象机制,不复用来源书人名、地名、法宝名、功法名或获取桥段。",
        "新词条必须改变机制、代价或叙事功能,不能只改名。",
    ]
    for blueprint in blueprints:
        anti_copy_rules.extend(blueprint.anti_copy_boundaries)

    return EntrySystemKernel(
        version=1,
        kernel_key="entry_system_kernel",
        genre_profile=profile or None,
        system_promise=";".join(part for part in system_promise_parts if part)[:4000],
        taxonomy=taxonomy,
        grade_ladders=_grade_ladders_for_taxonomy(taxonomy),
        capability_axes=_capability_axes(taxonomy, blueprints),
        cost_model=EntryCostModel(
            default_cost_types=_default_cost_types(profile, blueprints),
            hard_rule=(
                "任何改变战力、权限、真相、资源或局势的词条效果,"
                "都必须支付读者可见的代价或制造后续压力。"
            ),
        ),
        acquisition_model=EntryAcquisitionModel(
            valid_sources=(
                "trial",
                "trade",
                "inheritance",
                "witness",
                "choice",
                "relationship_trust",
                "enemy_loot",
                "rule_discovery",
            ),
            reader_visible_required=True,
        ),
        lifecycle=EntryLifecycle(),
        uniqueness_rules=(
            "同一卷内不得出现两个承担相同 narrative_role 的支柱词条。",
            "支柱词条必须有未来兑现、反噬或升级路径。",
            "章节奖励必须指向具体词条、具体状态变化或具体资源账。",
        ),
        anti_copy_rules=_dedupe(anti_copy_rules),
        coverage_targets=_coverage_targets(taxonomy, target_chapters=target_chapters),
    )


def entry_system_kernel_from_dict(data: dict[str, object]) -> EntrySystemKernel:
    return EntrySystemKernel.model_validate(data)


def entry_system_kernel_to_dict(kernel: EntrySystemKernel) -> dict[str, object]:
    return kernel.model_dump(mode="json")


def render_entry_system_kernel_prompt_block(
    kernel: EntrySystemKernel | dict[str, object] | None,
    *,
    max_types: int = 8,
    max_ladders: int = 5,
    max_axes: int = 6,
) -> str:
    """Render a compact Chinese prompt block for planners and drafters."""

    if kernel is None:
        return ""
    if isinstance(kernel, dict):
        kernel = entry_system_kernel_from_dict(kernel)
    lines = [
        "【词条体系约束】",
        f"- 体系承诺: {kernel.system_promise}",
        f"- 硬规则: {kernel.cost_model.hard_rule}",
    ]
    if kernel.cost_model.default_cost_types:
        lines.append("- 默认代价: " + "、".join(kernel.cost_model.default_cost_types[:8]))
    lines.append("- 允许词条类型:")
    for item in kernel.taxonomy[:max_types]:
        required = "、".join(item.required_fields[:5]) if item.required_fields else "按本章功能补足"
        roles = "、".join(item.allowed_roles[:5]) if item.allowed_roles else "剧情资产"
        lines.append(f"  - {item.type}/{item.label}: roles={roles}; required={required}")
    if kernel.grade_ladders:
        lines.append("- 等级线:")
        for ladder in kernel.grade_ladders[:max_ladders]:
            levels = " -> ".join(level.name for level in ladder.levels)
            lines.append(f"  - {ladder.label}: {levels}; 升级规则: {ladder.promotion_rule}")
    if kernel.capability_axes:
        axis_labels = (
            f"{axis.axis}/{axis.label}" for axis in kernel.capability_axes[:max_axes]
        )
        lines.append("- 能力轴: " + "、".join(axis_labels))
    if kernel.uniqueness_rules:
        lines.append("- 唯一性规则: " + ";".join(kernel.uniqueness_rules[:3]))
    if kernel.anti_copy_rules:
        lines.append("- 反抄袭边界: " + ";".join(kernel.anti_copy_rules[:3]))
    return "\n".join(lines)


def _finding(code: str, message: str, path: str, severity: str = "error") -> EntrySystemFinding:
    return EntrySystemFinding(code=code, severity=severity, message=message, path=path)


def validate_entry_system_kernel(kernel: EntrySystemKernel) -> tuple[EntrySystemFinding, ...]:
    """Structural validation beyond Pydantic shape checks."""

    findings: list[EntrySystemFinding] = []
    if not kernel.taxonomy:
        findings.append(_finding("taxonomy_missing", "Entry system taxonomy is empty.", "taxonomy"))
    if not kernel.grade_ladders:
        findings.append(
            _finding(
                "grade_ladders_missing",
                "Entry system has no grade ladders.",
                "grade_ladders",
                "warning",
            )
        )
    for index, ladder in enumerate(kernel.grade_ladders):
        path = f"grade_ladders[{index}]"
        if not ladder.levels:
            findings.append(
                _finding(
                    "grade_ladder_levels_missing",
                    "Grade ladder needs levels.",
                    f"{path}.levels",
                )
            )
        if not ladder.promotion_rule.strip():
            findings.append(
                _finding(
                    "grade_ladder_promotion_rule_missing",
                    "Grade ladder needs a promotion rule.",
                    f"{path}.promotion_rule",
                )
            )
    for index, item in enumerate(kernel.taxonomy):
        path = f"taxonomy[{index}]"
        if not item.required_fields:
            findings.append(
                _finding(
                    "taxonomy_required_fields_missing",
                    f"Entry type {item.type} should name required fields.",
                    f"{path}.required_fields",
                    "warning",
                )
            )
    if not kernel.cost_model.hard_rule.strip():
        findings.append(
            _finding(
                "cost_model_hard_rule_missing",
                "Cost model needs a hard rule.",
                "cost_model.hard_rule",
            )
        )
    return tuple(findings)
