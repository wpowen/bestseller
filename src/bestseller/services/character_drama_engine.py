# ruff: noqa: RUF001
"""Compile character bibles into usable drama constraints.

The planner already stores rich character facts: voice, psychology, moral lines,
family imprint, social ties, and IP anchors. This module turns those static facts
into the units that actually move plot: choices, pressure triggers, temptations,
relationship tensions, and scene tests.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CharacterDramaContract(BaseModel, frozen=True):
    """A role-level contract that can drive scenes instead of just describing a person."""

    model_config = ConfigDict(extra="ignore")

    character_key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    dramatic_function: str = Field(min_length=1)
    surface_persona: str = Field(min_length=1)
    hidden_need: str = Field(min_length=1)
    false_belief: str = Field(min_length=1)
    pressure_trigger: str = Field(min_length=1)
    choice_axis: str = Field(min_length=1)
    boundary_line: str = Field(min_length=1)
    temptation: str = Field(min_length=1)
    signature_action: str = Field(min_length=1)
    scene_test: str = Field(min_length=1)
    payoff_mode: str = Field(min_length=1)


class RelationshipTensionContract(BaseModel, frozen=True):
    """A pair-level source of conflict or alliance movement."""

    model_config = ConfigDict(extra="ignore")

    pair: list[str] = Field(min_length=2)
    surface_relation: str = Field(min_length=1)
    hidden_dependency: str = Field(min_length=1)
    trust_axis: str = Field(min_length=1)
    conflict_trigger: str = Field(min_length=1)
    escalation_path: str = Field(min_length=1)
    payoff_or_break: str = Field(min_length=1)


class CharacterDramaMap(BaseModel, frozen=True):
    """The character layer exported to story design and downstream outline planning."""

    model_config = ConfigDict(extra="ignore")

    version: int = 1
    protagonist: CharacterDramaContract
    antagonists: list[CharacterDramaContract] = Field(default_factory=list)
    supporting: list[CharacterDramaContract] = Field(default_factory=list)
    relationship_tensions: list[RelationshipTensionContract] = Field(default_factory=list)
    top_choice_axes: list[str] = Field(default_factory=list)
    reader_empathy_handles: list[str] = Field(default_factory=list)
    memorable_scene_seeds: list[str] = Field(default_factory=list)


def build_character_drama_map(
    cast_spec: dict[str, Any] | None,
    *,
    language: str = "zh-CN",
) -> CharacterDramaMap:
    """Build a deterministic drama map from an existing CastSpec payload."""

    cast = _mapping(cast_spec)
    is_en = language.lower().startswith("en")
    protagonist_raw = _mapping(cast.get("protagonist"))
    protagonist = _build_character_contract(
        "protagonist",
        protagonist_raw,
        default_name="Protagonist" if is_en else "主角",
        default_role="protagonist",
        is_antagonist=False,
        is_en=is_en,
    )

    antagonists = _antagonist_contracts(cast, is_en=is_en)
    supporting = [
        _build_character_contract(
            f"supporting_{index}",
            item,
            default_name=f"Supporting {index}" if is_en else f"配角{index}",
            default_role=str(item.get("role") or "supporting"),
            is_antagonist=False,
            is_en=is_en,
        )
        for index, item in enumerate(_mapping_list(cast.get("supporting_cast")), start=1)
    ]

    relationship_tensions = _relationship_tensions(
        cast,
        protagonist=protagonist,
        antagonists=antagonists,
        supporting=supporting,
        is_en=is_en,
    )
    contracts = [protagonist, *antagonists, *supporting]
    return CharacterDramaMap(
        protagonist=protagonist,
        antagonists=antagonists,
        supporting=supporting,
        relationship_tensions=relationship_tensions,
        top_choice_axes=_dedupe([item.choice_axis for item in contracts])[:6],
        reader_empathy_handles=_reader_empathy_handles(protagonist_raw, protagonist, is_en=is_en),
        memorable_scene_seeds=_memorable_scene_seeds(contracts, relationship_tensions, is_en=is_en),
    )


def render_character_drama_prompt_block(
    drama_map: CharacterDramaMap | dict[str, Any] | None,
    *,
    max_characters: int = 8,
    max_relationships: int = 6,
) -> str:
    """Render character drama contracts as compact planner instructions."""

    if drama_map is None:
        return ""
    if isinstance(drama_map, dict):
        drama_map = CharacterDramaMap.model_validate(drama_map)

    contracts = [drama_map.protagonist, *drama_map.antagonists, *drama_map.supporting]
    lines = [
        "## Character Drama Engine",
        (
            "- Personality facts are not decoration: convert each character fact into "
            "a visible choice, cost, relationship shift, or scene test."
        ),
        "- Do not repeat static type labels as plot logic; use the dynamic contracts below.",
    ]
    if drama_map.top_choice_axes:
        lines.append(f"- Top choice axes: {' | '.join(drama_map.top_choice_axes[:6])}")
    if drama_map.reader_empathy_handles:
        handles = " | ".join(drama_map.reader_empathy_handles[:5])
        lines.append(f"- Reader empathy handles: {handles}")

    lines.append("### Character contracts")
    for contract in contracts[:max_characters]:
        lines.extend(
            [
                f"- {contract.name} ({contract.role})",
                f"  - Dramatic function: {contract.dramatic_function}",
                f"  - Hidden need: {contract.hidden_need}",
                f"  - False belief: {contract.false_belief}",
                f"  - Pressure trigger: {contract.pressure_trigger}",
                f"  - Choice axis: {contract.choice_axis}",
                f"  - Boundary line: {contract.boundary_line}",
                f"  - Temptation: {contract.temptation}",
                f"  - Signature action: {contract.signature_action}",
                f"  - Scene test: {contract.scene_test}",
                f"  - Payoff mode: {contract.payoff_mode}",
            ]
        )

    if drama_map.relationship_tensions:
        lines.append("### Relationship tensions")
        for tension in drama_map.relationship_tensions[:max_relationships]:
            lines.append(
                f"- {' / '.join(tension.pair)}: relation={tension.surface_relation}; "
                f"dependency={tension.hidden_dependency}; trust={tension.trust_axis}; "
                f"trigger={tension.conflict_trigger}; escalation={tension.escalation_path}; "
                f"payoff/break={tension.payoff_or_break}"
            )

    if drama_map.memorable_scene_seeds:
        lines.append("### Memorable scene seeds")
        for seed in drama_map.memorable_scene_seeds[:6]:
            lines.append(f"- {seed}")
    return "\n".join(lines)


def character_drama_map_to_dict(drama_map: CharacterDramaMap) -> dict[str, Any]:
    """Serialize the drama map for project metadata."""

    return drama_map.model_dump(mode="json")


def _antagonist_contracts(cast: dict[str, Any], *, is_en: bool) -> list[CharacterDramaContract]:
    candidates: list[dict[str, Any]] = []
    antagonist = _mapping(cast.get("antagonist"))
    if antagonist:
        candidates.append(antagonist)
    for item in _mapping_list(cast.get("antagonists")):
        candidates.append(item)
    for item in _mapping_list(cast.get("antagonist_forces")):
        candidates.append(item)

    contracts: list[CharacterDramaContract] = []
    for index, item in enumerate(candidates, start=1):
        contracts.append(
            _build_character_contract(
                f"antagonist_{index}",
                item,
                default_name=f"Antagonist {index}" if is_en else f"反派{index}",
                default_role=str(item.get("role") or "antagonist"),
                is_antagonist=True,
                is_en=is_en,
            )
        )
    return contracts


def _build_character_contract(
    character_key: str,
    raw: dict[str, Any],
    *,
    default_name: str,
    default_role: str,
    is_antagonist: bool,
    is_en: bool,
) -> CharacterDramaContract:
    name = _first_text(raw.get("name"), raw.get("force_name"), default=default_name)
    role = _first_text(raw.get("role"), default=default_role)
    goal = _first_text(
        raw.get("goal"),
        raw.get("external_goal"),
        raw.get("agenda"),
        default="pursue the visible goal" if is_en else "推进可见目标",
    )
    fear = _first_text(
        raw.get("fear"),
        default="losing the thing they cannot admit they need" if is_en else "失去不愿承认的需要",
    )
    flaw = _first_text(
        raw.get("flaw"),
        default="protecting the old strategy too tightly" if is_en else "过度依赖旧策略",
    )
    secret = _first_text(raw.get("secret"), default="")
    arc = _first_text(raw.get("arc_trajectory"), raw.get("arc_state"), default="")
    moral = _mapping(raw.get("moral_framework"))
    values = _string_list(moral.get("core_values"))
    lines = _string_list(moral.get("lines_never_crossed"))
    sacrifice = _string_list(moral.get("willing_to_sacrifice"))
    ip_anchor = _mapping(raw.get("ip_anchor"))
    quirks = _string_list(ip_anchor.get("quirks"))
    tag_memory = _first_text(ip_anchor.get("tag_memory"), default="")
    core_wound = _first_text(ip_anchor.get("core_wound"), default="")
    independent_life = _first_text(ip_anchor.get("independent_life"), default="")
    beliefs = _mapping(raw.get("beliefs"))
    ideology = _first_text(
        beliefs.get("ideology"),
        beliefs.get("philosophical_stance"),
        default="",
    )
    crisis = _first_text(beliefs.get("crisis_of_faith"), default="")
    family = _mapping(raw.get("family_imprint"))
    breaking_points = _string_list(family.get("breaking_points"))
    inherited_values = _string_list(family.get("inherited_values"))
    villain = _mapping(raw.get("villain_charisma"))

    boundary_line = _first_text(
        _first_item(lines),
        _first_item(values),
        _first_item(sacrifice),
        default="do not betray the deepest value" if is_en else "不能背叛最深层价值",
    )
    surface_persona = _join_or_default(
        [*_first_n(quirks, 2), tag_memory, independent_life],
        default=f"{name} carries a repeatable external behavior under pressure."
        if is_en
        else f"{name}在压力下有可复现的外在行为。",
    )
    hidden_need = _first_text(
        raw.get("internal_need"),
        raw.get("need"),
        arc,
        _need_from_flaw(flaw, is_en=is_en),
    )
    false_belief = _first_text(
        raw.get("false_belief"),
        _belief_from_parts(flaw, fear, core_wound, is_en=is_en),
    )
    pressure_trigger = _first_text(
        _first_item(breaking_points),
        secret,
        crisis,
        fear,
        goal,
    )
    choice_axis = _choice_axis(
        goal=goal,
        boundary_line=boundary_line,
        value=_first_text(_first_item(values), _first_item(inherited_values), ideology),
        is_en=is_en,
    )
    temptation = _temptation(
        raw=raw,
        goal=goal,
        flaw=flaw,
        villain=villain,
        is_antagonist=is_antagonist,
        is_en=is_en,
    )
    dramatic_function = _dramatic_function(
        role=role,
        goal=goal,
        villain=villain,
        is_antagonist=is_antagonist,
        is_en=is_en,
    )
    signature_action = _first_text(
        _first_item(quirks),
        tag_memory,
        independent_life,
        default=f"make {goal} visible through a concrete gesture"
        if is_en
        else f"用具体动作让“{goal}”可视化",
    )
    scene_test = _scene_test(
        name=name,
        pressure_trigger=pressure_trigger,
        choice_axis=choice_axis,
        is_en=is_en,
    )
    payoff_mode = _payoff_mode(
        name=name,
        goal=goal,
        boundary_line=boundary_line,
        is_antagonist=is_antagonist,
        is_en=is_en,
    )
    return CharacterDramaContract(
        character_key=character_key,
        name=name,
        role=role,
        dramatic_function=dramatic_function,
        surface_persona=surface_persona,
        hidden_need=hidden_need,
        false_belief=false_belief,
        pressure_trigger=pressure_trigger,
        choice_axis=choice_axis,
        boundary_line=boundary_line,
        temptation=temptation,
        signature_action=signature_action,
        scene_test=scene_test,
        payoff_mode=payoff_mode,
    )


def _relationship_tensions(
    cast: dict[str, Any],
    *,
    protagonist: CharacterDramaContract,
    antagonists: list[CharacterDramaContract],
    supporting: list[CharacterDramaContract],
    is_en: bool,
) -> list[RelationshipTensionContract]:
    tensions: list[RelationshipTensionContract] = []
    for item in _mapping_list(cast.get("conflict_map")):
        first = _first_text(
            item.get("character_a"),
            item.get("source"),
            item.get("from"),
            default=protagonist.name,
        )
        second = _first_text(
            item.get("character_b"),
            item.get("target"),
            item.get("to"),
            default=_default_counterpart(antagonists, supporting, is_en=is_en),
        )
        conflict = _first_text(
            item.get("conflict_type"),
            item.get("type"),
            default="value collision" if is_en else "价值观冲突",
        )
        trigger = _first_text(
            item.get("trigger_condition"),
            item.get("trigger"),
            item.get("flashpoint"),
            default=conflict,
        )
        tensions.append(
            RelationshipTensionContract(
                pair=[first, second],
                surface_relation=conflict,
                hidden_dependency=_first_text(
                    item.get("hidden_dependency"),
                    default="they need each other's pressure to expose the true choice"
                    if is_en
                    else "双方需要彼此施压才能暴露真正选择",
                ),
                trust_axis=_first_text(
                    item.get("trust_axis"),
                    default="control versus trust" if is_en else "控制与信任",
                ),
                conflict_trigger=trigger,
                escalation_path=_first_text(
                    item.get("escalation_path"),
                    default="private disagreement becomes public cost"
                    if is_en
                    else "私下分歧升级成公开代价",
                ),
                payoff_or_break=_first_text(
                    item.get("payoff_or_break"),
                    default="one side must change terms or the alliance breaks"
                    if is_en
                    else "一方必须改变条件，否则关系破裂",
                ),
            )
        )

    protagonist_raw = _mapping(cast.get("protagonist"))
    for item in _mapping_list(protagonist_raw.get("relationships")):
        target = _first_text(
            item.get("character"),
            item.get("target"),
            item.get("name"),
            default=_default_counterpart(antagonists, supporting, is_en=is_en),
        )
        tension = _first_text(
            item.get("tension"),
            item.get("conflict"),
            item.get("dynamic"),
            default="a concrete trust test" if is_en else "一次具体信任测试",
        )
        tensions.append(
            RelationshipTensionContract(
                pair=[protagonist.name, target],
                surface_relation=_first_text(
                    item.get("type"),
                    item.get("relationship"),
                    default="relationship pressure" if is_en else "关系压力",
                ),
                hidden_dependency=tension,
                trust_axis="authorization versus fear" if is_en else "授权与恐惧",
                conflict_trigger=tension,
                escalation_path="small refusal becomes plot-level cost"
                if is_en
                else "小拒绝升级为主线代价",
                payoff_or_break=(
                    "earned trust or visible rupture" if is_en else "信任兑现或可见破裂"
                ),
            )
        )

    if not tensions and antagonists:
        antagonist = antagonists[0]
        tensions.append(
            RelationshipTensionContract(
                pair=[protagonist.name, antagonist.name],
                surface_relation="mainline opposition" if is_en else "主线对抗",
                hidden_dependency="each side proves the other's worldview incomplete"
                if is_en
                else "双方互相证明对方世界观不完整",
                trust_axis="value collision" if is_en else "价值观冲突",
                conflict_trigger=antagonist.pressure_trigger,
                escalation_path="tactical clash becomes ideological cost"
                if is_en
                else "战术冲突升级成理念代价",
                payoff_or_break=protagonist.payoff_mode,
            )
        )
    return _dedupe_tensions(tensions)[:8]


def _reader_empathy_handles(
    protagonist_raw: dict[str, Any],
    protagonist: CharacterDramaContract,
    *,
    is_en: bool,
) -> list[str]:
    moral = _mapping(protagonist_raw.get("moral_framework"))
    ip_anchor = _mapping(protagonist_raw.get("ip_anchor"))
    handles = [
        *_string_list(moral.get("core_values")),
        _first_text(ip_anchor.get("independent_life"), default=""),
        protagonist.boundary_line,
        protagonist.pressure_trigger,
    ]
    fallback = "show the cost before the victory" if is_en else "先让读者看见代价，再兑现胜利"
    return _dedupe([item for item in handles if item] or [fallback])[:5]


def _memorable_scene_seeds(
    contracts: list[CharacterDramaContract],
    relationships: list[RelationshipTensionContract],
    *,
    is_en: bool,
) -> list[str]:
    seeds: list[str] = []
    for contract in contracts[:4]:
        if is_en:
            seeds.append(
                f"{contract.name}: use '{contract.signature_action}' while choosing "
                f"between {contract.choice_axis}."
            )
        else:
            seeds.append(
                f"{contract.name}在“{contract.choice_axis}”之间做选择时，使用“{contract.signature_action}”。"
            )
    for relation in relationships[:2]:
        if is_en:
            seeds.append(
                f"{' / '.join(relation.pair)} relationship scene: {relation.conflict_trigger}."
            )
        else:
            seeds.append(f"{' / '.join(relation.pair)}关系场：{relation.conflict_trigger}。")
    return _dedupe(seeds)[:6]


def _choice_axis(*, goal: str, boundary_line: str, value: str, is_en: bool) -> str:
    if is_en:
        if value:
            return f"{goal} versus {boundary_line}, with {value} tested under pressure"
        return f"{goal} versus {boundary_line}"
    if value:
        return f"为了{goal}，是否越过“{boundary_line}”；压力下检验“{value}”"
    return f"为了{goal}，是否越过“{boundary_line}”"


def _temptation(
    *,
    raw: dict[str, Any],
    goal: str,
    flaw: str,
    villain: dict[str, Any],
    is_antagonist: bool,
    is_en: bool,
) -> str:
    appeal = _first_text(
        villain.get("philosophical_appeal"),
        villain.get("noble_motivation"),
        raw.get("temptation"),
        default="",
    )
    if appeal:
        return appeal
    if is_en:
        return f"use {flaw} as a shortcut to get {goal}"
    return f"用“{flaw}”当捷径去换取“{goal}”"


def _dramatic_function(
    *,
    role: str,
    goal: str,
    villain: dict[str, Any],
    is_antagonist: bool,
    is_en: bool,
) -> str:
    mirror = _first_text(villain.get("protagonist_mirror"), default="")
    if is_antagonist and mirror:
        return mirror
    if is_antagonist:
        return f"force the protagonist to pay for {goal}" if is_en else f"迫使主角为“{goal}”付代价"
    if "protagonist" in role.lower():
        return (
            "carry the external goal through an internal choice"
            if is_en
            else "用内部选择承载外部目标"
        )
    return f"change the mainline by pursuing {goal}" if is_en else f"通过追求“{goal}”改变主线"


def _scene_test(*, name: str, pressure_trigger: str, choice_axis: str, is_en: bool) -> str:
    if is_en:
        return f"Put {name} under '{pressure_trigger}' and force a visible choice: {choice_axis}."
    return f"让{name}遭遇“{pressure_trigger}”，必须做出可见选择：{choice_axis}。"


def _payoff_mode(
    *,
    name: str,
    goal: str,
    boundary_line: str,
    is_antagonist: bool,
    is_en: bool,
) -> str:
    if is_antagonist:
        return (
            f"{name}'s appeal must win short-term proof before its cost is exposed"
            if is_en
            else f"{name}的逻辑先获得短期证明，再暴露代价"
        )
    if is_en:
        return f"{goal} pays off only when {name} protects {boundary_line} at a real cost"
    return f"只有{name}付出真实代价仍守住“{boundary_line}”，{goal}才算兑现"


def _belief_from_parts(flaw: str, fear: str, core_wound: str, *, is_en: bool) -> str:
    if is_en:
        if core_wound:
            return f"Because of '{core_wound}', {flaw} feels safer than admitting {fear}."
        return f"{flaw} feels safer than admitting {fear}."
    if core_wound:
        return f"因“{core_wound}”，{flaw}比承认“{fear}”更安全。"
    return f"{flaw}比承认“{fear}”更安全。"


def _need_from_flaw(flaw: str, *, is_en: bool) -> str:
    if is_en:
        return f"learn when to release the old strategy: {flaw}"
    return f"学会放下旧策略：“{flaw}”"


def _default_counterpart(
    antagonists: list[CharacterDramaContract],
    supporting: list[CharacterDramaContract],
    *,
    is_en: bool,
) -> str:
    if antagonists:
        return antagonists[0].name
    if supporting:
        return supporting[0].name
    return "Counterpart" if is_en else "对手"


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mapping_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _first_item(items: list[str]) -> str:
    return items[0] if items else ""


def _first_n(items: list[str], count: int) -> list[str]:
    return [item for item in items[:count] if item]


def _first_text(*values: object, default: str = "") -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _join_or_default(items: list[str], *, default: str) -> str:
    joined = "；".join(_dedupe([item for item in items if item]))
    return joined or default


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _dedupe_tensions(
    items: list[RelationshipTensionContract],
) -> list[RelationshipTensionContract]:
    seen: set[tuple[str, str, str]] = set()
    result: list[RelationshipTensionContract] = []
    for item in items:
        key = (item.pair[0], item.pair[1], item.conflict_trigger)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
