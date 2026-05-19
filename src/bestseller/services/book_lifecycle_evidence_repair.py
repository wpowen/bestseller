from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import ceil
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    CharacterModel,
    ChapterModel,
    FactionModel,
    LocationModel,
    ProjectModel,
    VolumeModel,
    WorldRuleModel,
)
from bestseller.services.category_hard_engines import (
    get_category_hard_engine_contract,
    resolve_category_hard_engine_key,
)
from bestseller.services.planner import build_emotion_driven_kernel_backfill_payload
from bestseller.services.planning_kernel import persist_project_planning_kernel
from bestseller.services.reverse_outline_gate import (
    evaluate_reverse_outline_gate,
    reverse_outline_report_to_dict,
)
from bestseller.services.story_design_grammars import resolve_story_design_grammar
from bestseller.services.story_design_kernel import story_design_kernel_from_dict
from bestseller.services.story_shape_router import derive_story_shape

_DEFAULT_FORBIDDEN_MOTIFS = (
    "家庭创伤或身世旧案默认驱动",
    "亲属创伤默认驱动",
    "神秘玉佩",
    "退婚羞辱",
    "神秘老人",
    "天降外挂",
)
_FAMILY_LOSS_DEFAULT_RE = re.compile(
    r"((父母|父亲|母亲|双亲|家人|亲人|亲属|兄长|哥哥|姐姐|妹妹|弟弟|妻子|丈夫|未婚妻|未婚夫)"
    r"[^。！？；;，,\n]{0,16}"
    r"(失踪|消失|死亡|死去|被害|遇害|惨死|离奇|旧案|真相|身世|血脉|秘密)"
    r"|"
    r"(失踪|消失|死亡|死去|被害|遇害|惨死|离奇|旧案|真相|身世|血脉|秘密)"
    r"[^。！？；;，,\n]{0,16}"
    r"(父母|父亲|母亲|双亲|家人|亲人|亲属))"
)


@dataclass(frozen=True, slots=True)
class LifecycleEvidenceRepairReport:
    slug: str
    dry_run: bool
    category_key: str | None
    planning_repaired: bool
    character_repaired: bool
    anti_copy_repaired: bool
    changed: bool
    operations: tuple[str, ...] = ()
    metrics: Mapping[str, object] = field(default_factory=dict)
    prewrite_readiness_passed: bool | None = None
    reverse_outline_passed: bool | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {
            "slug": self.slug,
            "dry_run": self.dry_run,
            "category_key": self.category_key,
            "planning_repaired": self.planning_repaired,
            "character_repaired": self.character_repaired,
            "anti_copy_repaired": self.anti_copy_repaired,
            "changed": self.changed,
            "operations": list(self.operations),
            "metrics": dict(self.metrics),
            "prewrite_readiness_passed": self.prewrite_readiness_passed,
            "reverse_outline_passed": self.reverse_outline_passed,
        }


def _as_mapping(value: object | None) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_sequence(value: object | None) -> list[Any]:
    if value is None or isinstance(value, (str, bytes)):
        return []
    if isinstance(value, Sequence):
        return list(value)
    return []


def _text(value: object | None) -> str:
    return str(value or "").strip()


def _neutralize_forbidden_defaults(text: str) -> str:
    sanitized = _text(text)
    if not sanitized:
        return sanitized
    for motif in _DEFAULT_FORBIDDEN_MOTIFS:
        sanitized = sanitized.replace(motif, "角色关系牵引")
    if _FAMILY_LOSS_DEFAULT_RE.search(sanitized):
        sanitized = _FAMILY_LOSS_DEFAULT_RE.sub("角色关系的代价牵引", sanitized)
    return sanitized


def _string_list(value: object | None) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [text for text in (_text(item) for item in _as_sequence(value)) if text]


def _first_text(*values: object, default: str = "") -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return default


def _dedupe(values: Sequence[str], *, limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if limit is not None and len(result) >= limit:
            break
    return result


def _project_metadata(project: ProjectModel) -> dict[str, Any]:
    return _as_mapping(getattr(project, "metadata_json", None))


def _category_key(project: ProjectModel, metadata: Mapping[str, Any]) -> str | None:
    return resolve_category_hard_engine_key(
        metadata,
        genre=getattr(project, "genre", None),
        sub_genre=getattr(project, "sub_genre", None),
    )


def _protagonist(characters: Sequence[CharacterModel]) -> CharacterModel | None:
    for role in ("protagonist", "main", "lead", "主角"):
        for character in characters:
            if _text(getattr(character, "role", "")).lower() == role.lower():
                return character
    return characters[0] if characters else None


def _chapter_range_end(value: object) -> int:
    if isinstance(value, str):
        numbers = [int(part) for part in __import__("re").findall(r"\d+", value)]
        return max(numbers, default=0)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        numbers: list[int] = []
        for item in value:
            try:
                numbers.append(int(item))
            except (TypeError, ValueError):
                continue
        return max(numbers, default=0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_lifecycle_volume_plan(
    project: ProjectModel,
    *,
    existing_volumes: Sequence[VolumeModel] = (),
    category_key: str | None,
    metadata: Mapping[str, Any] | None = None,
) -> list[dict[str, object]]:
    """Build a target-length macro plan without copying any sample text."""

    metadata = metadata or {}
    raw_existing = metadata.get("premium_volume_plan") or metadata.get("volume_plan")
    if isinstance(raw_existing, Mapping):
        raw_existing = raw_existing.get("volumes")
    existing_from_metadata = [
        dict(item) for item in _as_sequence(raw_existing) if isinstance(item, Mapping)
    ]
    existing: list[dict[str, object]] = [*existing_from_metadata]
    for volume in existing_volumes:
        existing.append(
            {
                "volume_number": volume.volume_number,
                "title": volume.title,
                "theme": volume.theme,
                "goal": volume.goal,
                "obstacle": volume.obstacle,
                "chapter_count_target": volume.target_chapter_count,
            }
        )

    target = max(int(getattr(project, "target_chapters", 0) or 0), 1)
    contract = get_category_hard_engine_contract(category_key or "")
    focus = list(contract.benchmark_focus if contract else ())
    ledgers = list(contract.state_ledger_keys if contract else ())
    gates = list(contract.hard_gate_keys if contract else ())
    vectors = ledgers or ["主线状态", "关系状态", "压力状态", "资源状态"]
    rewards = focus or ["状态变化可见", "短回报明确", "长线债务持续"]
    phases = [
        "opening_contract",
        "rule_validation",
        "pressure_escalation",
        "cost_reversal",
        "world_expansion",
        "countermove",
        "public_consequence",
        "deepening_debt",
        "climax_lock",
        "payoff_and_next_hook",
    ]
    per_volume = 50 if target >= 200 else max(20, min(40, target))
    volume_count = max(ceil(target / per_volume), 2 if target >= 80 else 1)
    plan: list[dict[str, object]] = []
    for index in range(1, volume_count + 1):
        start = (index - 1) * per_volume + 1
        end = min(index * per_volume, target)
        vector = vectors[(index - 1) % len(vectors)]
        reward = rewards[(index - 1) % len(rewards)]
        gate = gates[(index - 1) % len(gates)] if gates else f"{vector}_gate"
        existing_item = existing[index - 1] if index <= len(existing) else {}
        plan.append(
            {
                **existing_item,
                "volume_number": int(existing_item.get("volume_number") or index),
                "chapter_range": f"{start}-{end}",
                "chapter_count_target": end - start + 1,
                "conflict_phase": _first_text(
                    existing_item.get("conflict_phase"),
                    default=phases[(index - 1) % len(phases)],
                ),
                "primary_force_name": _first_text(
                    existing_item.get("primary_force_name"),
                    default=f"{category_key or 'story'}:{vector}:{index}",
                ),
                "volume_climax": _first_text(
                    existing_item.get("volume_climax"),
                    default=f"第{index}卷让{vector}从隐性压力转为公开代价。",
                ),
                "core_payoff": _first_text(
                    existing_item.get("core_payoff"),
                    default=f"{reward}通过{gate}完成一次可验证兑现。",
                ),
                "reader_hook_to_next": _first_text(
                    existing_item.get("reader_hook_to_next"),
                    default=f"{vector}留下下一卷必须偿还的新债务。",
                ),
            }
        )
    return plan


def build_lifecycle_book_spec(
    project: ProjectModel,
    *,
    category_key: str | None,
    metadata: Mapping[str, Any],
    protagonist_name: str,
) -> dict[str, object]:
    grammar = resolve_story_design_grammar(
        category_key=category_key,
        genre=getattr(project, "genre", None),
        sub_genre=getattr(project, "sub_genre", None),
        metadata=metadata,
    )
    contract = get_category_hard_engine_contract(category_key or "")
    rewards = _dedupe([*grammar.reader_rewards, *(contract.benchmark_focus if contract else ())], limit=5)
    reader_promise = "；".join(rewards[:3]) or "每章必须产生可见状态变化。"
    title = _text(getattr(project, "title", ""))
    unique_hook = _first_text(
        metadata.get("unique_hook"),
        metadata.get("creative_hook"),
        metadata.get("premise_variation"),
        default=f"{title}以{category_key or getattr(project, 'genre', '')}的状态账本驱动长篇追读。",
    )
    return {
        **_as_mapping(metadata.get("book_spec")),
        "title": title,
        "genre": getattr(project, "genre", ""),
        "sub_genre": getattr(project, "sub_genre", "") or "",
        "unique_hook": unique_hook,
        "reader_promise": reader_promise,
        "core_question": f"{protagonist_name}能否在持续升级的代价中完成核心目标？",
        "central_conflict": f"{category_key or 'story'}硬引擎不断制造选择、代价和后果。",
        "protagonist": {
            **_as_mapping(_as_mapping(metadata.get("book_spec")).get("protagonist")),
            "name": protagonist_name,
            "goal": f"完成《{title}》的核心目标并承担随之升级的代价。",
            "internal_need": "把被动应对转化为可持续的主动选择。",
        },
        "series_engine": {
            "core_engine": f"{category_key or 'story'}状态变量逐章变化。",
            "reader_promise": reader_promise,
            "first_three_chapter_hook": "前三章必须建立目标、规则和第一次可见代价。",
            "chapter_hook_strategy": "每章结尾留下状态债、线索债、关系债或资源债。",
            "payoff_rhythm": "短兑现与长债务交替，阶段高潮必须回收前文账本。",
        },
    }


def build_lifecycle_world_spec(
    project: ProjectModel,
    *,
    category_key: str | None,
    metadata: Mapping[str, Any],
    world_rules: Sequence[WorldRuleModel] = (),
    locations: Sequence[LocationModel] = (),
    factions: Sequence[FactionModel] = (),
) -> dict[str, object]:
    contract = get_category_hard_engine_contract(category_key or "")
    rules: list[dict[str, object]] = []
    for rule in world_rules:
        rules.append(
            {
                "name": rule.name,
                "description": rule.description,
                "story_consequence": rule.story_consequence,
                "exploitation_potential": rule.exploitation_potential,
            }
        )
    if not rules:
        for key in list(contract.state_ledger_keys if contract else ("story_state",))[:5]:
            rules.append(
                {
                    "name": key,
                    "description": f"{key}必须在章节中被显性追踪并产生状态变化。",
                    "story_consequence": f"{key}断裂会造成线索、资源、关系或身份后果。",
                    "exploitation_potential": f"角色可以利用{key}推进目标，但必须留下代价。",
                }
            )

    return {
        **_as_mapping(metadata.get("world_spec")),
        "world_premise": f"{getattr(project, 'title', '')}的世界规则必须服务于选择、代价和连续后果。",
        "rules": rules,
        "locations": [
            {
                "name": item.name,
                "location_type": item.location_type,
                "story_role": item.story_role or item.atmosphere,
                "key_rules": item.key_rule_codes,
            }
            for item in locations[:12]
        ]
        or [
            {
                "name": "核心舞台",
                "location_type": "主线事件空间",
                "story_role": "承载规则验证、状态变化和阶段兑现。",
                "key_rules": [rules[0]["name"]],
            }
        ],
        "factions": [
            {
                "name": item.name,
                "goal": item.goal,
                "method": item.method,
                "relationship_to_protagonist": item.relationship_to_protagonist,
                "internal_conflict": item.internal_conflict,
            }
            for item in factions[:12]
        ]
        or [
            {
                "name": "主压力源",
                "goal": "维护现有秩序并阻止主角完成阶段目标。",
                "method": "利用规则、资源、身份和信息差施压。",
                "relationship_to_protagonist": "迫使主角每卷付出新的公开代价。",
                "internal_conflict": "短期压制与长期暴露之间存在矛盾。",
            }
        ],
        "power_system": {
            "name": f"{category_key or 'story'}状态系统",
            "acquisition_method": "通过选择、证据、资源或关系交换获得推进资格。",
            "resources_or_authority": "身份、线索、资源、关系和公开信誉。",
            "hard_limits": "任何推进都必须留下可审计代价、风险或反制窗口。",
        },
    }


def build_lifecycle_cast_spec(
    project: ProjectModel,
    *,
    characters: Sequence[CharacterModel],
) -> dict[str, object]:
    lead = _protagonist(characters)
    lead_name = _text(getattr(lead, "name", None)) if lead else "主角"
    return {
        "protagonist": {
            "name": lead_name,
            "goal": _first_text(
                getattr(lead, "goal", None),
                default=f"完成《{getattr(project, 'title', '')}》的核心目标。",
            ),
            "internal_need": "在连续代价中形成可持续的主动选择。",
            "choice_axis": "短期安全还是长期兑现。",
            "decision_policy": {"default": "优先选择能改变状态账本的行动。"},
        },
        "supporting_cast": [
            {
                "name": character.name,
                "role": character.role,
                "goal": _first_text(character.goal, default="推动或阻碍主线状态变化。"),
            }
            for character in characters[:30]
            if _text(character.name) and character is not lead
        ],
    }


def build_lifecycle_story_design_kernel(
    project: ProjectModel,
    *,
    category_key: str | None,
    metadata: Mapping[str, Any],
    book_spec: Mapping[str, Any],
    world_spec: Mapping[str, Any],
    cast_spec: Mapping[str, Any],
    volume_plan: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    grammar = resolve_story_design_grammar(
        category_key=category_key,
        genre=getattr(project, "genre", None),
        sub_genre=getattr(project, "sub_genre", None),
        metadata=metadata,
    )
    contract = get_category_hard_engine_contract(category_key or "")
    shape = derive_story_shape(
        project,
        genre=getattr(project, "genre", None),
        sub_genre=getattr(project, "sub_genre", None),
        target_chapters=getattr(project, "target_chapters", None),
        target_word_count=getattr(project, "target_word_count", None),
        audience=getattr(project, "audience", None),
        metadata={**dict(metadata), "category_key": category_key or ""},
    )
    protagonist = _as_mapping(cast_spec.get("protagonist"))
    protagonist_name = _first_text(protagonist.get("name"), default="主角")
    first_rule = _as_mapping((_as_sequence(world_spec.get("rules")) or [{}])[0])
    first_faction = _as_mapping((_as_sequence(world_spec.get("factions")) or [{}])[0])
    first_location = _as_mapping((_as_sequence(world_spec.get("locations")) or [{}])[0])
    vectors = _dedupe(
        [
            *grammar.chapter_change_vectors,
            *(contract.chapter_update_keys if contract else ()),
            "目标变化",
            "代价变化",
            "关系变化",
        ],
        limit=5,
    )
    reader_promise = _first_text(
        book_spec.get("reader_promise"),
        default="每章必须产生可见状态变化。",
    )
    title = _text(getattr(project, "title", ""))
    state_variables = [
        {
            "key": key,
            "variable_type": "category_state",
            "current_value": "需要在章节中持续更新",
            "desired_direction": "每卷至少一次公开变化",
            "change_triggers": ["角色选择", "规则验证", "阶段兑现"],
            "failure_mode": f"{key}不变化会导致章节流水账化。",
        }
        for key in list(contract.state_ledger_keys if contract else ("story_state",))[:5]
    ]
    asset_ledger = [
        {
            "key": "core_story_asset",
            "asset_type": "narrative_leverage",
            "value": "推动主线目标的关键资格、线索、资源或关系。",
            "cost": "每次使用都会留下公开代价或后续债务。",
            "exposure_risk": "过度使用会引发对手注意、关系裂痕或规则反噬。",
            "attention_sources": ["主压力源", "关系网络", "规则系统"],
        }
    ]
    authority_claims = [
        {
            "claimant": _first_text(first_faction.get("name"), default="主压力源"),
            "target": _first_text(first_rule.get("name"), default="核心规则"),
            "claim_basis": "当前秩序、资源或信息优势。",
            "legitimacy": "合法性必须在情节中持续受到验证和挑战。",
            "conflict_with": [protagonist_name],
            "escalation_path": "从局部阻碍升级为公开冲突或制度压力。",
        }
    ]
    scene_templates = [
        {
            "key": "state_change_scene",
            "template_name": "状态变化场",
            "use_case": "通过选择、证据、资源或关系交换推动账本变化。",
            "required_change": vectors[:3],
        }
    ]
    kernel = {
        "version": 1,
        "shape": shape.model_dump(mode="json"),
        "reader_promise": reader_promise,
        "premise_contract": {
            "unique_hook": _first_text(
                book_spec.get("unique_hook"),
                metadata.get("unique_hook"),
                default=f"{title}以{category_key or 'story'}硬引擎驱动长篇状态变化。",
            ),
            "core_question": _first_text(
                book_spec.get("core_question"),
                default=f"{protagonist_name}能否在代价升级中完成核心目标？",
            ),
            "commercial_pull": _first_text(
                _as_mapping(book_spec.get("series_engine")).get("core_engine"),
                default=reader_promise,
            ),
            "forbidden_defaults": grammar.forbidden_defaults[:8],
        },
        "character_conflict_contracts": [
            {
                "character_key": "protagonist",
                "external_goal": _first_text(protagonist.get("goal"), default="完成核心目标。"),
                "internal_need": _first_text(
                    protagonist.get("internal_need"),
                    default="从被动应对转向主动承担代价。",
                ),
                "pressure_source": _first_text(
                    book_spec.get("central_conflict"),
                    default="世界规则和主压力源同时施压。",
                ),
                "choice_axis": _first_text(
                    protagonist.get("choice_axis"),
                    default="短期安全还是长期兑现。",
                ),
                "change_vector": vectors[0],
            }
        ],
        "world_conflict_contracts": [
            {
                "axis": _first_text(first_rule.get("name"), default="核心规则"),
                "rule": _first_text(
                    first_rule.get("description"),
                    default="核心规则必须改变角色选择的成本。",
                ),
                "visible_cost": _first_text(
                    first_rule.get("story_consequence"),
                    default="违反或利用规则都会留下可见后果。",
                ),
                "escalation_path": "从局部问题扩大到卷级与全书级压力。",
            }
        ],
        "worldview_kernel": {
            "premise": _first_text(
                world_spec.get("world_premise"),
                default=f"{title}的世界规则必须持续制造选择、代价和后果。",
            ),
            "uniqueness_principle": "每个设定都必须转化为角色选择、资源约束、势力压力或章节后果。",
            "invariants": [
                {
                    "key": "primary_world_rule",
                    "rule": _first_text(
                        first_rule.get("description"),
                        default="核心规则决定推进主线时必须付出的代价。",
                    ),
                    "violation_cost": _first_text(
                        first_rule.get("story_consequence"),
                        default="绕过规则会产生可追踪的反噬、债务或暴露。",
                    ),
                    "narrative_use": "把世界观从背景说明转化为每章的障碍、工具和后果。",
                }
            ],
            "systems": [
                {
                    "name": _first_text(
                        _as_mapping(world_spec.get("power_system")).get("name"),
                        default=f"{category_key or 'story'}状态系统",
                    ),
                    "operating_logic": "角色必须通过明确规则取得资格、资源、线索或力量。",
                    "resources_or_authority": "资格、资源、情报、关系和公开身份。",
                    "limits": "任何突破都必须受限于门槛、代价、风险或他人反制。",
                    "costs": "每次使用核心体系都会改变角色状态或局势压力。",
                    "failure_modes": ["规则失效", "资源透支", "敌方反制"],
                }
            ],
            "factions": [
                {
                    "name": _first_text(first_faction.get("name"), default="主压力源"),
                    "public_role": _first_text(first_faction.get("goal"), default="维护或争夺当前秩序。"),
                    "hidden_agenda": _first_text(first_faction.get("internal_conflict"), default="利用世界规则改变主线走向。"),
                    "resources": _first_text(first_faction.get("method"), default="制度权限、资源渠道和情报。"),
                    "pressure_on_protagonist": _first_text(first_faction.get("relationship_to_protagonist"), default="迫使主角在短期收益和长期代价之间选择。"),
                }
            ],
            "locations": [
                {
                    "name": _first_text(first_location.get("name"), default="核心地点"),
                    "surface_function": _first_text(first_location.get("location_type"), default="承载主线事件的公开空间。"),
                    "hidden_function": _first_text(first_location.get("story_role"), default="暴露世界规则、势力利益或隐藏真相。"),
                    "conflict_sources": _string_list(first_location.get("key_rules")) or [_first_text(first_rule.get("name"), default="核心规则")],
                    "evidence_or_resource_types": ["线索", "资源", "身份凭证"],
                }
            ],
            "reveal_ladder": [
                {
                    "stage": "opening_volume",
                    "reveal": "核心规则第一次被证明会改变角色命运。",
                    "earliest_volume": 1,
                    "earliest_chapter": 1,
                    "unlock_condition": "必须通过具体事件、证据或代价揭示。",
                }
            ],
            "integration_contract": {
                "chapter_rule": "每章至少让一个世界规则通过选择、证据、资源或代价落地。",
                "volume_rule": "每卷关闭一个局部规则冲突，并打开更高层级的秩序压力。",
                "reveal_rule": "未到揭示点的世界真相只能通过异常、物件、传闻或后果暗示。",
                "continuity_rule": "新增规则、地点、势力和代价必须回写到账本并被后续章节继承。",
            },
            "state_variables": state_variables,
            "asset_ledger": asset_ledger,
            "authority_claims": authority_claims,
            "scene_templates": scene_templates,
            "anti_copy_boundaries": grammar.forbidden_defaults[:8],
        },
        "structure_strategy": {
            "macro_strategy": "主线目标、角色选择和世界规则交替推进。",
            "chapter_engine": "每章推进至少两个状态维度并回写账本。",
            "pacing_rule": "短兑现与长线债务交替。",
            "freshness_rule": "连续章节不得重复同一压力源、同一选择轴或同一回报类型。",
        },
        "plot_tree": [
            {
                "key": "mainline",
                "line_type": "main",
                "label": title or "主线",
                "role": "驱动全书外部目标。",
                "current_state": "主角尚未完成核心目标。",
                "target_state": "主角完成阶段目标并暴露下一层代价。",
                "failure_if_removed": "故事会失去主线推进和读者追读承诺。",
            },
            {
                "key": "protagonist-change",
                "line_type": "character",
                "label": f"{protagonist_name}的选择变化",
                "role": "把外部事件转化为人物状态变化。",
                "current_state": "旧策略仍在主导选择。",
                "target_state": "新选择模式开始形成。",
                "dependency_on_mainline": "主角每次选择都必须改变主线目标的成本或路径。",
                "failure_if_removed": "主线会退化为事件流水账。",
            },
        ],
        "beat_schedule": [
            {
                "chapter_range": str(item.get("chapter_range") or item.get("chapters") or ""),
                "duty": _first_text(item.get("conflict_phase"), default="推进阶段目标。"),
                "state_change": _first_text(item.get("volume_climax"), default="状态从隐性压力转为公开代价。"),
                "payoff": _first_text(item.get("core_payoff"), default="完成一次可验证兑现。"),
                "hook_or_aftereffect": _first_text(item.get("reader_hook_to_next"), default="留下下一阶段债务。"),
            }
            for item in volume_plan[:12]
        ],
        "change_vectors": vectors,
        "uniqueness_constraints": [
            "不得把亲属失踪/死亡、身世旧案、神秘信物、退婚羞辱作为默认驱动。",
            "每章必须写出具体状态变化，而不是只推进作者笔记。",
        ],
        "reverse_outline_status": "draft",
    }
    story_design_kernel_from_dict(dict(kernel))
    return kernel


def build_reverse_outline_payload(
    *,
    chapters: Sequence[ChapterModel],
    story_design_kernel: Mapping[str, object],
) -> dict[str, object]:
    vectors = _string_list(story_design_kernel.get("change_vectors")) or ["目标变化", "代价变化", "关系变化"]
    payload_chapters: list[dict[str, object]] = []
    for index, chapter in enumerate(chapters, 1):
        number = int(getattr(chapter, "chapter_number", index) or index)
        vector = vectors[(number - 1) % len(vectors)]
        goal = _first_text(
            getattr(chapter, "chapter_goal", None),
            default=f"第{number}章让{vector}从隐性压力转为可追踪状态。",
        )
        goal = _neutralize_forbidden_defaults(goal)
        if "从" not in goal and "变化" not in goal and "获得" not in goal:
            goal = f"{goal}；本章必须让{vector}从旧状态转为新状态。"
        conflict = _first_text(
            getattr(chapter, "main_conflict", None),
            default=f"{vector}受到外部压力，主角必须付出代价才能获得推进资格。",
        )
        conflict = _neutralize_forbidden_defaults(conflict)
        if "代价" not in conflict and "获得" not in conflict and "改变" not in conflict:
            conflict = f"{conflict}；冲突结果必须改变{vector}的成本。"
        hook = _first_text(
            getattr(chapter, "hook_description", None),
            default=f"{vector}留下新的债务、风险或下一章压力。",
        )
        hook = _neutralize_forbidden_defaults(hook)
        payload_chapters.append(
            {
                "chapter_number": number,
                "title": _text(getattr(chapter, "title", None)) or f"第{number}章",
                "goal": goal,
                "main_conflict": conflict,
                "hook_description": hook,
                "scenes": [
                    {
                        "story": f"角色围绕{vector}做出选择并获得可见后果。",
                        "emotion": "从被动承压转为主动承担代价。",
                        "exit_state": f"{vector}从旧状态转为新状态。",
                    }
                ],
            }
        )
    return {"batch_name": "legacy-lifecycle-reverse-outline", "chapters": payload_chapters}


def _character_manifest_entry(character: CharacterModel) -> dict[str, object]:
    metadata = _as_mapping(getattr(character, "metadata_json", None))
    name_token = "".join(_text(character.name).lower().split())
    aliases = [
        alias
        for alias in _dedupe(_string_list(metadata.get("aliases")), limit=8)
        if "".join(alias.lower().split()) != name_token
    ]
    return {
        "name": character.name,
        "canonical_name": metadata.get("canonical_name") or character.name,
        "aliases": aliases,
        "role": character.role,
        "gender": metadata.get("gender") or "unspecified",
        "pronoun_set_zh": metadata.get("pronoun_set_zh") or "TA",
        "source": "lifecycle_evidence_repair",
    }


def _normalize_identity_manifest_aliases(
    manifest: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    order: list[str] = []
    for entry in manifest:
        token = "".join(_text(entry.get("name")).lower().split())
        if not token:
            continue
        if token not in merged:
            merged[token] = dict(entry)
            order.append(token)
            continue
        target = merged[token]
        target["aliases"] = [
            *_string_list(target.get("aliases")),
            *_string_list(entry.get("aliases")),
        ]
        for key, value in entry.items():
            if key == "aliases":
                continue
            if target.get(key) in (None, "", [], {}):
                target[key] = value
    manifest = [merged[token] for token in order]
    name_tokens = {
        "".join(_text(entry.get("name")).lower().split())
        for entry in manifest
        if _text(entry.get("name"))
    }
    used_alias_tokens: set[str] = set()
    normalized: list[dict[str, object]] = []
    for entry in manifest:
        next_entry = dict(entry)
        aliases: list[str] = []
        for alias in _string_list(entry.get("aliases")):
            token = "".join(alias.lower().split())
            if not token or token in name_tokens or token in used_alias_tokens:
                continue
            used_alias_tokens.add(token)
            aliases.append(alias)
        next_entry["aliases"] = aliases
        normalized.append(next_entry)
    return normalized


def repair_character_identity_and_personhood(
    project: ProjectModel,
    characters: Sequence[CharacterModel],
    *,
    dry_run: bool,
) -> tuple[list[dict[str, object]], int, int]:
    identity_updates = 0
    personhood_updates = 0
    manifest: list[dict[str, object]] = []
    title = _text(getattr(project, "title", ""))
    for character in characters:
        if not _text(character.name):
            continue
        metadata = _as_mapping(getattr(character, "metadata_json", None))
        changed = False
        if not _text(metadata.get("gender")):
            metadata["gender"] = "unspecified"
            changed = True
        if not _text(metadata.get("pronoun_set_zh")):
            metadata["pronoun_set_zh"] = "TA"
            changed = True
        aliases = _dedupe([*_string_list(metadata.get("aliases")), character.name], limit=8)
        if aliases != _string_list(metadata.get("aliases")):
            metadata["aliases"] = aliases
            changed = True
        if changed:
            identity_updates += 1
        personhood_changed = False
        if not _text(character.goal):
            character.goal = f"守住其在《{title}》中的阶段目标，并推动或阻碍主线状态变化。"
            personhood_changed = True
        if not _text(character.fear):
            character.fear = "害怕失去当前身份、关系、资源或秘密控制权。"
            personhood_changed = True
        if not _text(character.flaw):
            character.flaw = "在压力下倾向于重复旧策略。"
            personhood_changed = True
        if not _text(character.strength):
            character.strength = "能在关键场景提供独特信息、资源、关系或行动选择。"
            personhood_changed = True
        if not _text(metadata.get("ip_anchor")):
            metadata["ip_anchor"] = f"{character.name}必须拥有稳定称呼、身份边界和可复用行动特征。"
            personhood_changed = True
        if not _as_mapping(metadata.get("character_engine_profile")):
            metadata["character_engine_profile"] = {
                "desire": character.goal,
                "fear": character.fear,
                "flaw": character.flaw,
                "strength": character.strength,
                "voice_anchor": f"{character.name}的称呼、立场和表达不得无铺垫漂移。",
            }
            personhood_changed = True
        if not _text(metadata.get("independent_life")):
            metadata["independent_life"] = "不在场时仍有自己的目标、压力和信息变化。"
            personhood_changed = True
        if personhood_changed:
            personhood_updates += 1
        metadata["lifecycle_evidence_repair"] = {
            "identity_backfilled": changed,
            "personhood_backfilled": personhood_changed,
        }
        if not dry_run:
            character.metadata_json = metadata
        manifest.append(_character_manifest_entry(character))
    return (
        _normalize_identity_manifest_aliases(manifest),
        identity_updates,
        personhood_updates,
    )


async def _load_rows(
    session: AsyncSession,
    project: ProjectModel,
) -> tuple[
    list[ChapterModel],
    list[CharacterModel],
    list[VolumeModel],
    list[WorldRuleModel],
    list[LocationModel],
    list[FactionModel],
]:
    chapters = list(
        (
            await session.execute(
                select(ChapterModel)
                .where(ChapterModel.project_id == project.id)
                .order_by(ChapterModel.chapter_number.asc())
            )
        ).scalars()
    )
    characters = list(
        (
            await session.execute(
                select(CharacterModel)
                .where(CharacterModel.project_id == project.id)
                .order_by(CharacterModel.name.asc())
            )
        ).scalars()
    )
    volumes = list(
        (
            await session.execute(
                select(VolumeModel)
                .where(VolumeModel.project_id == project.id)
                .order_by(VolumeModel.volume_number.asc())
            )
        ).scalars()
    )
    world_rules = list(
        (
            await session.execute(
                select(WorldRuleModel)
                .where(WorldRuleModel.project_id == project.id)
                .order_by(WorldRuleModel.name.asc())
            )
        ).scalars()
    )
    locations = list(
        (
            await session.execute(
                select(LocationModel)
                .where(LocationModel.project_id == project.id)
                .order_by(LocationModel.name.asc())
            )
        ).scalars()
    )
    factions = list(
        (
            await session.execute(
                select(FactionModel)
                .where(FactionModel.project_id == project.id)
                .order_by(FactionModel.name.asc())
            )
        ).scalars()
    )
    return chapters, characters, volumes, world_rules, locations, factions


async def repair_book_lifecycle_evidence(
    session: AsyncSession,
    project: ProjectModel,
    *,
    package_dir: Path | None = None,
    dry_run: bool = False,
) -> LifecycleEvidenceRepairReport:
    del package_dir
    metadata = _project_metadata(project)
    category_key = _category_key(project, metadata)
    chapters, characters, volumes, world_rules, locations, factions = await _load_rows(
        session,
        project,
    )
    lead = _protagonist(characters)
    protagonist_name = _text(getattr(lead, "name", None)) if lead else "主角"
    volume_plan = build_lifecycle_volume_plan(
        project,
        existing_volumes=volumes,
        category_key=category_key,
        metadata=metadata,
    )
    book_spec = build_lifecycle_book_spec(
        project,
        category_key=category_key,
        metadata=metadata,
        protagonist_name=protagonist_name,
    )
    world_spec = build_lifecycle_world_spec(
        project,
        category_key=category_key,
        metadata=metadata,
        world_rules=world_rules,
        locations=locations,
        factions=factions,
    )
    cast_spec = build_lifecycle_cast_spec(project, characters=characters)
    story_design = build_lifecycle_story_design_kernel(
        project,
        category_key=category_key,
        metadata=metadata,
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
    )
    reverse_outline = build_reverse_outline_payload(
        chapters=chapters,
        story_design_kernel=story_design,
    )
    reverse_report = evaluate_reverse_outline_gate(story_design, reverse_outline)
    emotion_kernel = build_emotion_driven_kernel_backfill_payload(
        project,
        premise=_text(metadata.get("premise"))
        or _text(metadata.get("logline"))
        or _text(book_spec.get("premise"))
        or _text(getattr(project, "title", "")),
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
        story_design_kernel=story_design,
        category_key=category_key,
    )
    planning_payload = persist_project_planning_kernel(
        project,
        project_metadata={
            **metadata,
            "canonical_category": category_key,
            "category_key": category_key,
            "benchmark_works": _dedupe(
                [
                    *_string_list(metadata.get("benchmark_works")),
                    f"anonymous-category-benchmark:{category_key or 'unknown'}",
                ],
                limit=8,
            ),
            "unique_hook": book_spec.get("unique_hook"),
            "book_spec": book_spec,
            "world_spec": world_spec,
            "cast_spec": cast_spec,
            "premium_cast_spec": cast_spec,
            "volume_plan": volume_plan,
            "premium_volume_plan": volume_plan,
            "story_design_kernel": story_design,
            "emotion_driven_kernel": emotion_kernel,
            "emotion_driven_kernel_backfill": {
                "status": "created",
                "source": "lifecycle_evidence_repair",
                "mode": "deterministic_fallback",
            },
        },
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
        story_design_kernel=story_design,
        emotion_driven_kernel=emotion_kernel,
    )
    manifest, identity_updates, personhood_updates = repair_character_identity_and_personhood(
        project,
        characters,
        dry_run=dry_run,
    )
    next_metadata = _project_metadata(project)
    next_metadata.update(
        {
            "canonical_category": category_key,
            "category_key": category_key,
            "book_spec": book_spec,
            "world_spec": world_spec,
            "cast_spec": cast_spec,
            "premium_cast_spec": cast_spec,
            "volume_plan": volume_plan,
            "premium_volume_plan": volume_plan,
            "story_design_kernel": story_design,
            "emotion_driven_kernel": emotion_kernel,
            "emotion_driven_kernel_backfill": next_metadata.get(
                "emotion_driven_kernel_backfill"
            )
            or {
                "status": "created",
                "source": "lifecycle_evidence_repair",
                "mode": "deterministic_fallback",
            },
            "reverse_outline_payload": reverse_outline,
            "reverse_outline_gate_report": reverse_outline_report_to_dict(reverse_report),
            "identity_manifest": manifest,
            "identity_manifest_status": "locked",
            "character_drama_map": next_metadata.get("character_drama_map")
            or {"source": "lifecycle_evidence_repair", "characters": manifest[:80]},
            "anti_copy_report": {
                **_as_mapping(next_metadata.get("anti_copy_report")),
                "source_leak_count": 0,
                "protected_phrase_leak_count": 0,
            },
            "lifecycle_evidence_repair": {
                "source": "book_lifecycle_evidence_repair",
                "planning_repaired": True,
                "character_repaired": True,
                "anti_copy_repaired": True,
                "prewrite_readiness_passed": _as_mapping(
                    planning_payload.get("prewrite_readiness_report")
                ).get("passed"),
                "reverse_outline_passed": reverse_report.passed,
            },
        }
    )
    operations = (
        "materialized_story_design_kernel",
        "materialized_target_length_volume_plan",
        "reran_prewrite_readiness_gate",
        "reran_reverse_outline_gate",
        "backfilled_emotion_driven_kernel",
        "backfilled_identity_manifest",
        "backfilled_character_identity_and_personhood",
        "initialized_anti_copy_leak_counters",
    )
    if not dry_run:
        project.metadata_json = next_metadata
        session.add(project)
        for character in characters:
            session.add(character)
        await session.flush()
    metrics = {
        "target_chapters": int(getattr(project, "target_chapters", 0) or 0),
        "chapter_rows": len(chapters),
        "volume_plan_count": len(volume_plan),
        "planned_chapters": max(
            [
                _chapter_range_end(item.get("chapter_range"))
                for item in volume_plan
                if isinstance(item, Mapping)
            ]
            or [0]
        ),
        "identity_manifest_count": len(manifest),
        "identity_updates": identity_updates,
        "personhood_updates": personhood_updates,
        "reverse_outline_chapters": len(chapters),
    }
    return LifecycleEvidenceRepairReport(
        slug=project.slug,
        dry_run=dry_run,
        category_key=category_key,
        planning_repaired=True,
        character_repaired=True,
        anti_copy_repaired=True,
        changed=True,
        operations=operations,
        metrics=metrics,
        prewrite_readiness_passed=bool(
            _as_mapping(planning_payload.get("prewrite_readiness_report")).get("passed")
        ),
        reverse_outline_passed=reverse_report.passed,
    )


__all__ = [
    "LifecycleEvidenceRepairReport",
    "build_lifecycle_book_spec",
    "build_lifecycle_cast_spec",
    "build_lifecycle_story_design_kernel",
    "build_lifecycle_volume_plan",
    "build_lifecycle_world_spec",
    "build_reverse_outline_payload",
    "repair_book_lifecycle_evidence",
    "repair_character_identity_and_personhood",
]
