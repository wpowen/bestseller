"""Character Engine loader (``config/character_engine.yaml``).

Provides typed access to per-character profiles (the 8-layer
data contract used by :mod:`writer` and :mod:`critic` to enforce
voice / signature / unique response chains).

Sample profiles for ``shen_qingya`` / ``zhou_shensuan`` /
``the_fourth_man`` are stored under ``sample_profiles`` in the YAML
and serve as canonical fixtures both for tests and as concrete
references the writer can lean on while a specific book's profile
files are being authored.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_str,
    as_str_tuple,
    load_yaml,
)

_CONFIG_FILENAME = "character_engine.yaml"
_CHARACTER_INTELLIGENCE_PROFILE_VERSION = 1


@dataclass(frozen=True)
class WantVsNeed:
    want: str
    need: str
    tension: str


@dataclass(frozen=True)
class ThreeLayerMotivation:
    surface: str
    hidden: str
    suppressed: str


@dataclass(frozen=True)
class ValuesAndRedlines:
    core_value: str
    behavior_preference: tuple[str, ...]
    absolute_no: tuple[str, ...]


@dataclass(frozen=True)
class UniqueResponseSteps:
    """Three steps of the unique response chain for one stimulus type."""

    step_1: str
    step_2: str
    step_3: str


@dataclass(frozen=True)
class SignatureAssets:
    object_marker: str
    action: str
    phrase: str
    tic: str


@dataclass(frozen=True)
class WeaknessAndKillshot:
    weakness_emotional: str
    weakness_physical: str
    killshot: str
    visibility_to_self: bool
    visibility_to_others: tuple[str, ...]


@dataclass(frozen=True)
class VoiceDNA:
    sentence_length_preference: str
    vocabulary_register: str
    forbidden_words: tuple[str, ...]
    signature_words: tuple[str, ...]
    response_pattern_to_question: str
    humor_type: str
    anger_expression: str
    lie_pattern: str


@dataclass(frozen=True)
class RelationshipEntry:
    target_id: str
    good_will: float
    unfinished_business: str
    current_stance: str
    evolution_trajectory: str


@dataclass(frozen=True)
class CharacterProfile:
    """The 8-layer profile for one character."""

    character_id: str
    display_name: str
    role: str
    age: str
    profession: str
    want_vs_need: WantVsNeed
    three_layer_motivation: ThreeLayerMotivation
    values_and_redlines: ValuesAndRedlines
    unique_response_chain: dict[str, UniqueResponseSteps]
    relationship_memory: tuple[RelationshipEntry, ...]
    signature_assets: SignatureAssets
    weakness_and_killshot: WeaknessAndKillshot
    voice_dna: VoiceDNA


@dataclass(frozen=True)
class CharacterEngineConfig:
    version: str
    sample_profiles: dict[str, CharacterProfile]


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------


def _parse_want_vs_need(raw: object) -> WantVsNeed:
    data = as_dict(raw)
    return WantVsNeed(
        want=as_str(data.get("want")),
        need=as_str(data.get("need")),
        tension=as_str(data.get("tension")),
    )


def _parse_three_layer(raw: object) -> ThreeLayerMotivation:
    data = as_dict(raw)
    return ThreeLayerMotivation(
        surface=as_str(data.get("surface")),
        hidden=as_str(data.get("hidden")),
        suppressed=as_str(data.get("suppressed")),
    )


def _parse_values(raw: object) -> ValuesAndRedlines:
    data = as_dict(raw)
    return ValuesAndRedlines(
        core_value=as_str(data.get("core_value")),
        behavior_preference=as_str_tuple(data.get("behavior_preference")),
        absolute_no=as_str_tuple(data.get("absolute_no")),
    )


def _parse_unique_response_chain(
    raw: object,
) -> dict[str, UniqueResponseSteps]:
    data = as_dict(raw)
    chains: dict[str, UniqueResponseSteps] = {}
    for stimulus, payload in data.items():
        body = as_dict(payload)
        chains[as_str(stimulus)] = UniqueResponseSteps(
            step_1=as_str(body.get("step_1")),
            step_2=as_str(body.get("step_2")),
            step_3=as_str(body.get("step_3")),
        )
    return chains


def _parse_signature_assets(raw: object) -> SignatureAssets:
    data = as_dict(raw)
    return SignatureAssets(
        # YAML uses ``object`` which clashes with the builtin in code
        # paths; we keep both readable.
        object_marker=as_str(data.get("object")),
        action=as_str(data.get("action")),
        phrase=as_str(data.get("phrase")),
        tic=as_str(data.get("tic")),
    )


def _parse_weakness(raw: object) -> WeaknessAndKillshot:
    data = as_dict(raw)
    visibility = data.get("visibility_to_self")
    return WeaknessAndKillshot(
        weakness_emotional=as_str(data.get("weakness_emotional")),
        weakness_physical=as_str(data.get("weakness_physical")),
        killshot=as_str(data.get("killshot")),
        visibility_to_self=bool(visibility) if isinstance(visibility, bool) else False,
        visibility_to_others=as_str_tuple(data.get("visibility_to_others")),
    )


def _parse_voice_dna(raw: object) -> VoiceDNA:
    data = as_dict(raw)
    return VoiceDNA(
        sentence_length_preference=as_str(data.get("sentence_length_preference")),
        vocabulary_register=as_str(data.get("vocabulary_register")),
        forbidden_words=as_str_tuple(data.get("forbidden_words")),
        signature_words=as_str_tuple(data.get("signature_words")),
        response_pattern_to_question=as_str(data.get("response_pattern_to_question")),
        humor_type=as_str(data.get("humor_type")),
        anger_expression=as_str(data.get("anger_expression")),
        lie_pattern=as_str(data.get("lie_pattern")),
    )


def _parse_relationship_memory(
    raw: object,
) -> tuple[RelationshipEntry, ...]:
    data = as_dict(raw)
    items: list[RelationshipEntry] = []
    for target, payload in data.items():
        body = as_dict(payload)
        good_will_raw = body.get("good_will")
        try:
            good_will = float(good_will_raw) if good_will_raw is not None else 0.0
        except (TypeError, ValueError):
            good_will = 0.0
        items.append(
            RelationshipEntry(
                target_id=as_str(target),
                good_will=good_will,
                unfinished_business=as_str(body.get("unfinished_business")),
                current_stance=as_str(body.get("current_stance")),
                evolution_trajectory=as_str(body.get("evolution_trajectory")),
            )
        )
    return tuple(items)


def _parse_profile(character_id: str, raw: object) -> CharacterProfile:
    data = as_dict(raw)
    return CharacterProfile(
        character_id=character_id,
        display_name=as_str(data.get("display_name"), default=character_id),
        role=as_str(data.get("role")),
        age=as_str(data.get("age")),
        profession=as_str(data.get("profession")),
        want_vs_need=_parse_want_vs_need(data.get("want_vs_need")),
        three_layer_motivation=_parse_three_layer(data.get("three_layer_motivation")),
        values_and_redlines=_parse_values(data.get("values_and_redlines")),
        unique_response_chain=_parse_unique_response_chain(data.get("unique_response_chain")),
        relationship_memory=_parse_relationship_memory(data.get("relationship_memory")),
        signature_assets=_parse_signature_assets(data.get("signature_assets")),
        weakness_and_killshot=_parse_weakness(data.get("weakness_and_killshot")),
        voice_dna=_parse_voice_dna(data.get("voice_dna")),
    )


@lru_cache(maxsize=1)
def load_character_engine() -> CharacterEngineConfig:
    """Return the typed view over ``character_engine.yaml``."""

    raw = load_yaml(_CONFIG_FILENAME)
    profiles_raw = as_dict(raw.get("sample_profiles"))
    profiles: dict[str, CharacterProfile] = {}
    for char_id, profile_raw in profiles_raw.items():
        canonical = as_str(char_id)
        if not canonical:
            continue
        profiles[canonical] = _parse_profile(canonical, profile_raw)
    return CharacterEngineConfig(
        version=as_str(raw.get("version")),
        sample_profiles=profiles,
    )


def get_character_profile(character_id: str) -> CharacterProfile | None:
    """Look up one profile by id."""

    if not character_id:
        return None
    return load_character_engine().sample_profiles.get(character_id)


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def render_character_profile_block(
    *,
    character_ids: tuple[str, ...] | list[str],
    scene_stimulus: str | None = None,
) -> str:
    """Render a compact per-character packet for the writer prompt.

    Only emits the layers that are most actionable while writing one
    scene: voice_dna, signature_assets, and (when applicable) the
    unique_response_chain entry matching ``scene_stimulus``.
    """

    config = load_character_engine()
    profiles = [
        config.sample_profiles[char_id]
        for char_id in character_ids
        if char_id and char_id in config.sample_profiles
    ]
    if not profiles:
        return ""

    lines: list[str] = ["【character_engine 参与者档案】"]
    for profile in profiles:
        lines.append(f"- {profile.character_id} ({profile.display_name})")
        voice = profile.voice_dna
        lines.append(
            "  voice_dna: 句长 "
            + voice.sentence_length_preference
            + ("; 禁词 " + ", ".join(voice.forbidden_words) if voice.forbidden_words else "")
            + ("; 标志词 " + ", ".join(voice.signature_words[:6]) if voice.signature_words else "")
        )
        sig = profile.signature_assets
        sig_pieces = [
            piece
            for piece in (
                f"物={sig.object_marker}" if sig.object_marker else "",
                f"动作={sig.action}" if sig.action else "",
                f"短句={sig.phrase}" if sig.phrase else "",
                f"tic={sig.tic}" if sig.tic else "",
            )
            if piece
        ]
        if sig_pieces:
            lines.append("  signature: " + "; ".join(sig_pieces))
        if scene_stimulus and scene_stimulus in profile.unique_response_chain:
            chain = profile.unique_response_chain[scene_stimulus]
            lines.append(
                f"  本场 {scene_stimulus} 三步反应链: "
                f"1) {chain.step_1} 2) {chain.step_2} 3) {chain.step_3}"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CastSpec fusion
# ---------------------------------------------------------------------------


def _clean_text(value: object, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _first_text(*values: object, default: str = "") -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return default


def _as_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return [value]


def _string_list(value: object, *, limit: int | None = None) -> list[str]:
    items: list[str] = []
    for raw in _as_list(value):
        if isinstance(raw, Mapping):
            for key in ("phrase", "text", "name", "title", "summary", "bond"):
                text = _clean_text(raw.get(key))
                if text:
                    items.append(text)
                    break
        else:
            text = _clean_text(raw)
            if text:
                items.append(text)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped[:limit] if limit is not None else deduped


def _first_item(value: object, default: str = "") -> str:
    items = _string_list(value, limit=1)
    return items[0] if items else default


def _sentence_length_from_voice(voice_profile: Mapping[str, Any]) -> str:
    style = _first_text(
        voice_profile.get("sentence_style"),
        voice_profile.get("sentence_length_preference"),
        default="",
    ).lower()
    if any(token in style for token in ("短", "short", "碎片", "punchy", "clipped")):
        return "short"
    if any(token in style for token in ("长", "long", "思辨", "analytical")):
        return "long"
    if any(token in style for token in ("混合", "mixed")):
        return "mixed"
    return "medium"


def _good_will_from_relation(relation_type: str) -> float:
    lowered = relation_type.lower()
    if any(token in lowered for token in ("enemy", "敌", "仇", "追捕")):
        return -0.8
    if any(token in lowered for token in ("rival", "对手", "竞争")):
        return -0.3
    if any(token in lowered for token in ("ally", "friend", "mentor", "搭档", "盟友", "友")):
        return 0.6
    return 0.1


def _bool_from_mapping(data: Mapping[str, Any], key: str, *, default: bool = False) -> bool:
    value = data.get(key)
    return value if isinstance(value, bool) else default


def _strategy_number(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _response_steps(
    *,
    name: str,
    stimulus: str,
    signature_action: str,
    pressure_trigger: str,
    choice_axis: str,
) -> dict[str, str]:
    if stimulus == "threat_from_authority":
        return {
            "step_1": f"{name}先压住本能反应, 用{signature_action}稳住场面。",
            "step_2": f"把威胁和「{pressure_trigger}」联系起来, 判断对方真正要夺走什么。",
            "step_3": f"沿「{choice_axis}」做选择, 不用通用硬刚替代角色判断。",
        }
    if stimulus == "betrayal_by_close_one":
        return {
            "step_1": f"{name}不立刻质问, 先出现一个可见的停顿或习惯动作。",
            "step_2": f"回想双方未结的旧账, 确认背叛是否击中「{pressure_trigger}」。",
            "step_3": "保留证据或资源, 先改变关系距离, 再决定是否摊牌。",
        }
    if stimulus == "moral_dilemma":
        return {
            "step_1": f"{name}被迫在目标和底线之间停住。",
            "step_2": f"用「{choice_axis}」重新衡量代价, 而不是只按剧情效率行动。",
            "step_3": "让选择带出明确损失, 并把损失写进后续关系或资源状态。",
        }
    return {
        "step_1": f"{name}先露出标志性动作: {signature_action}。",
        "step_2": f"识别对方正在逼近「{pressure_trigger}」。",
        "step_3": f"用「{choice_axis}」决定反击、让步或隐藏真实意图。",
    }


def _distilled_strategy_layers(
    *,
    character_strategy: Mapping[str, Any],
    name: str,
    role: str,
    goal: str,
    need: str,
    fear: str,
    secret: str,
    core_wound: str,
    choice_axis: str,
    relationships: list[dict[str, Any]],
    voice: Mapping[str, Any],
) -> dict[str, Any]:
    strategy = _as_mapping(character_strategy)
    if not strategy:
        return {}

    agency = _as_mapping(strategy.get("agency_policy"))
    identity = _as_mapping(strategy.get("identity_pressure"))
    relationship = _as_mapping(strategy.get("relationship_policy"))
    antagonist = _as_mapping(strategy.get("antagonist_policy"))
    dialogue = _as_mapping(strategy.get("dialogue_policy"))
    required_axes = _string_list(strategy.get("required_axes"), limit=6)
    state_variables = _string_list(strategy.get("state_variables"), limit=8)
    risk_controls = _string_list(strategy.get("risk_controls"), limit=6)
    rewards = _string_list(strategy.get("reader_reward_contracts"), limit=6)
    role_lower = role.lower()

    agency_modes = _string_list(
        agency.get("default_problem_solving_modes"),
        limit=5,
    ) or ["active_choice_under_pressure"]
    identity_axis = _first_text(identity.get("choice_axis"), default=choice_axis)
    pressure_source = _first_text(secret, core_wound, fear, need)
    relationship_targets = [
        {
            "target_id": item.get("target_id"),
            "unfinished_business": item.get("unfinished_business"),
            "current_stance": item.get("current_stance"),
        }
        for item in relationships[:5]
        if item.get("target_id")
    ]

    return {
        "character_intelligence_version": _CHARACTER_INTELLIGENCE_PROFILE_VERSION,
        "strategy_source": _first_text(
            strategy.get("source"),
            default="distillation_character_intelligence",
        ),
        "distilled_required_axes": required_axes,
        "distilled_state_variables": state_variables,
        "agency_policy": {
            "active_choice_axis": choice_axis,
            "must_act_within_chapters": _strategy_number(
                agency,
                "must_act_within_chapters",
            ),
            "default_problem_solving_modes": agency_modes,
            "choice_with_cost_required": _bool_from_mapping(
                agency,
                "choice_with_cost_required",
                default=True,
            ),
            "forbidden_passive_modes": _string_list(
                agency.get("forbidden_passive_modes"),
                limit=4,
            ),
            "evidence_axes": _string_list(agency.get("evidence_axes"), limit=6),
        },
        "identity_pressure": {
            "choice_axis": identity_axis,
            "pressure_source": pressure_source,
            "required_external_pressure": _bool_from_mapping(
                identity,
                "required_external_pressure",
            ),
            "debt_sources": _string_list(identity.get("debt_sources"), limit=4),
            "forbidden_resolution_modes": _string_list(
                identity.get("forbidden_resolution_modes"),
                limit=4,
            ),
            "track_axes": _string_list(identity.get("track_axes"), limit=5),
        },
        "relationship_debt": {
            "reciprocal_commitment_required": _bool_from_mapping(
                relationship,
                "reciprocal_commitment_required",
            ),
            "cost_or_promise_required": _bool_from_mapping(
                relationship,
                "cost_or_promise_required",
                default=True,
            ),
            "active_relationships": relationship_targets,
            "track_axes": _string_list(relationship.get("track_axes"), limit=6),
            "usable_design_paths": _string_list(
                relationship.get("usable_design_paths"),
                limit=4,
            ),
        },
        "antagonist_misread_hooks": {
            "role_binding": (
                "must visibly recalculate after protagonist action"
                if "antagonist" in role_lower or "反派" in role
                else f"让对手按旧评估体系误读{name}追求「{goal}」的方式"
            ),
            "visible_reaction_required": _bool_from_mapping(
                antagonist,
                "visible_reaction_required",
            ),
            "on_screen_consequence_required": _bool_from_mapping(
                antagonist,
                "on_screen_consequence_required",
            ),
            "misread_payoff_required": _bool_from_mapping(
                antagonist,
                "misread_payoff_required",
            ),
            "resolution_differentiation_required": _bool_from_mapping(
                antagonist,
                "resolution_differentiation_required",
            ),
            "track_axes": _string_list(antagonist.get("track_axes"), limit=6),
            "misread_sources": _string_list(antagonist.get("misread_sources"), limit=4),
        },
        "dialogue_function": {
            "exposition_through_conflict": _bool_from_mapping(
                dialogue,
                "exposition_through_conflict",
            ),
            "reader_surrogate_questions_allowed": _bool_from_mapping(
                dialogue,
                "reader_surrogate_questions_allowed",
            ),
            "antagonist_exposition_has_higher_stakes": _bool_from_mapping(
                dialogue,
                "antagonist_exposition_has_higher_stakes",
            ),
            "max_revelations_before_break": _strategy_number(
                dialogue,
                "max_revelations_before_break",
            ),
            "voice_binding": _first_text(
                voice.get("response_pattern_to_question"),
                default="对话必须推动选择、关系或信息状态变化",
            ),
            "craft_controls": _string_list(dialogue.get("craft_controls"), limit=5),
        },
        "character_reward_contract": {
            "reader_rewards": rewards,
            "payoff_channels": [
                "active choice",
                "visible cost",
                "relationship/faction state change",
            ],
            "risk_controls": risk_controls,
        },
    }


def synthesize_character_engine_profile(
    character: Mapping[str, Any],
    character_strategy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fuse a CastSpec character into the 8-layer character_engine contract.

    The YAML sample profiles remain useful exemplars, but production books need
    the same mechanism generated from their own CastSpec. This function is
    deterministic and intentionally conservative: it only composes fields that
    already exist on the character bible, avoiding new plot facts.  When a
    distilled character strategy is provided, it adds project-level obligations
    as policy layers without changing the character's own facts.
    """

    raw = dict(character)
    name = _first_text(raw.get("name"), default="角色")
    role = _first_text(raw.get("role"), default="supporting")
    voice = _as_mapping(raw.get("voice_profile"))
    moral = _as_mapping(raw.get("moral_framework"))
    ip_anchor = _as_mapping(raw.get("ip_anchor"))
    inner = _as_mapping(raw.get("inner_structure"))
    beliefs = _as_mapping(raw.get("beliefs"))
    family = _as_mapping(raw.get("family_imprint"))
    villain = _as_mapping(raw.get("villain_charisma"))

    goal = _first_text(
        raw.get("goal"),
        raw.get("external_goal"),
        inner.get("want_external"),
        default="推进当前可见目标",
    )
    flaw = _first_text(raw.get("flaw"), inner.get("fatal_flaw"), default="过度依赖旧策略")
    fear = _first_text(raw.get("fear"), inner.get("fear_core"), default="失去最不愿承认的需要")
    secret = _first_text(raw.get("secret"), default="")
    core_wound = _first_text(
        raw.get("core_wound"),
        ip_anchor.get("core_wound"),
        inner.get("ghost"),
        villain.get("pain_origin"),
        default=f"{name}过去某次错误判断留下的未愈伤口",
    )
    arc = _first_text(raw.get("arc_trajectory"), raw.get("arc_state"), inner.get("truth_to_learn"))
    need = _first_text(
        raw.get("need"),
        raw.get("internal_need"),
        inner.get("need_internal"),
        arc,
        default=f"学会不再被「{flaw}」控制",
    )
    hidden_motive = _first_text(secret, fear, villain.get("noble_motivation"), default=need)
    suppressed_motive = _first_text(
        core_wound,
        _first_item(_as_mapping(raw.get("life_history")).get("trauma")),
        default=f"不愿承认自己仍被「{fear}」驱动",
    )

    core_values = _string_list(moral.get("core_values"), limit=3)
    redlines = _string_list(
        moral.get("lines_never_crossed") or moral.get("lines_will_not_cross"),
        limit=3,
    )
    if not core_values:
        core_values = _string_list(family.get("inherited_values"), limit=2)
    ideology = _first_text(beliefs.get("ideology"), beliefs.get("philosophical_stance"))
    if not core_values and ideology:
        core_values = [ideology]

    verbal_tics = _string_list(voice.get("verbal_tics"), limit=4)
    mannerisms = _string_list(voice.get("mannerisms"), limit=4)
    quirks = _string_list(ip_anchor.get("quirks"), limit=4)
    signature_objects = _string_list(ip_anchor.get("signature_objects"), limit=3)
    tag_memory = _first_text(ip_anchor.get("tag_memory"))
    signature_action = _first_text(
        _first_item(mannerisms),
        _first_item(quirks),
        tag_memory,
        default=f"在压力下让「{goal}」变得可见的动作",
    )
    signature_phrase = _first_text(
        _first_item(verbal_tics),
        tag_memory,
        _first_item(core_values),
        default=goal[:30],
    )
    object_marker = _first_text(
        _first_item(signature_objects),
        _first_item(_string_list(ip_anchor.get("sensory_signatures"), limit=1)),
        default="",
    )
    axis_boundary = redlines[0] if redlines else core_values[0] if core_values else fear
    choice_axis = f"{goal} vs {axis_boundary}"
    pressure_trigger = _first_text(secret, fear, core_wound, goal)

    relationships: list[dict[str, Any]] = []
    for relation in _as_list(raw.get("relationships")):
        rel = _as_mapping(relation)
        target = _first_text(rel.get("character"), rel.get("target"), rel.get("name"))
        if not target:
            continue
        rel_type = _first_text(rel.get("type"), rel.get("relationship"), default="关系")
        relationships.append(
            {
                "target_id": target,
                "good_will": _good_will_from_relation(rel_type),
                "unfinished_business": _first_text(rel.get("tension"), default="仍有未结张力"),
                "current_stance": rel_type,
                "evolution_trajectory": _first_text(
                    rel.get("evolution_trajectory"),
                    rel.get("evolution_arc"),
                    default="随主线压力发生阶段性变化",
                ),
            }
        )

    profile = {
        "source": "cast_spec_fusion",
        "character_id": _first_text(raw.get("character_id"), raw.get("id"), default=name),
        "display_name": name,
        "role": role,
        "want_vs_need": {
            "want": goal,
            "need": need,
            "tension": f"越追求「{goal}」, 越会暴露「{need}」尚未完成。",
        },
        "three_layer_motivation": {
            "surface": goal,
            "hidden": hidden_motive,
            "suppressed": suppressed_motive,
        },
        "values_and_redlines": {
            "core_value": core_values[0] if core_values else "在压力下仍保持自我判准",
            "behavior_preference": [item for item in [signature_action, *quirks[:2]] if item],
            "absolute_no": redlines or [f"不能彻底背叛「{need}」"],
        },
        "unique_response_chain": {
            key: _response_steps(
                name=name,
                stimulus=key,
                signature_action=signature_action,
                pressure_trigger=pressure_trigger,
                choice_axis=choice_axis,
            )
            for key in (
                "threat_from_authority",
                "betrayal_by_close_one",
                "moral_dilemma",
                "confrontation_with_villain",
            )
        },
        "relationship_memory": relationships,
        "signature_assets": {
            "object": object_marker,
            "action": signature_action,
            "phrase": signature_phrase,
            "tic": tag_memory or _first_text(_first_item(mannerisms), default=signature_action),
        },
        "weakness_and_killshot": {
            "weakness_emotional": fear,
            "weakness_physical": _first_text(
                raw.get("weakness"),
                default="高压场景下容易回到旧策略",
            ),
            "killshot": _first_text(secret, core_wound, fear),
            "visibility_to_self": "protagonist" in role.lower() or "主角" in role,
            "visibility_to_others": [],
        },
        "voice_dna": {
            "sentence_length_preference": _sentence_length_from_voice(voice),
            "vocabulary_register": _first_text(
                voice.get("speech_register"),
                voice.get("vocabulary_level"),
                default="mixed",
            ),
            "forbidden_words": _string_list(voice.get("forbidden_words"), limit=6),
            "signature_words": [
                item for item in [*verbal_tics, signature_phrase, object_marker] if item
            ][:8],
            "response_pattern_to_question": _first_text(
                voice.get("response_pattern_to_question"),
                default="先出现标志性停顿/动作, 再按价值轴回答",
            ),
            "humor_type": _first_text(voice.get("humor_type"), default="无"),
            "anger_expression": _first_text(
                voice.get("anger_expression"),
                voice.get("emotional_expression"),
                default="通过动作和句式收紧表达",
            ),
            "lie_pattern": _first_text(
                voice.get("lie_pattern"),
                default="不直接撒谎, 优先回避或只说半句" if secret else "少说判断, 多给事实",
            ),
        },
    }
    strategy_layers = _distilled_strategy_layers(
        character_strategy=_as_mapping(character_strategy),
        name=name,
        role=role,
        goal=goal,
        need=need,
        fear=fear,
        secret=secret,
        core_wound=core_wound,
        choice_axis=choice_axis,
        relationships=relationships,
        voice=voice,
    )
    if strategy_layers:
        profile.update(strategy_layers)
    return profile


def render_character_engine_profile_block(
    profiles: Iterable[Mapping[str, Any]],
    *,
    scene_stimulus: str | None = None,
    max_profiles: int = 4,
) -> str:
    """Render project-specific character_engine profiles for scene prompts."""

    normalized = [dict(profile) for profile in profiles if isinstance(profile, Mapping)]
    if not normalized:
        return ""

    lines = ["【character_engine 融合档案】"]
    for profile in normalized[:max_profiles]:
        name = _first_text(profile.get("display_name"), profile.get("name"), default="角色")
        role = _first_text(profile.get("role"), default="supporting")
        want_need = _as_mapping(profile.get("want_vs_need"))
        motivation = _as_mapping(profile.get("three_layer_motivation"))
        values = _as_mapping(profile.get("values_and_redlines"))
        signature = _as_mapping(profile.get("signature_assets"))
        voice = _as_mapping(profile.get("voice_dna"))
        agency = _as_mapping(profile.get("agency_policy"))
        identity = _as_mapping(profile.get("identity_pressure"))
        reward_contract = _as_mapping(profile.get("character_reward_contract"))
        dialogue_function = _as_mapping(profile.get("dialogue_function"))

        lines.append(f"- {name} ({role})")
        if want_need:
            lines.append(
                "  want/need: "
                f"{_first_text(want_need.get('want'))} / {_first_text(want_need.get('need'))}; "
                f"张力={_first_text(want_need.get('tension'))}"
            )
        if motivation:
            lines.append(
                "  motivation: "
                f"surface={_first_text(motivation.get('surface'))}; "
                f"hidden={_first_text(motivation.get('hidden'))}; "
                f"suppressed={_first_text(motivation.get('suppressed'))}"
            )
        if values:
            redlines = "、".join(_string_list(values.get("absolute_no"), limit=2))
            lines.append(
                "  values/redline: "
                f"{_first_text(values.get('core_value'))}"
                + (f"; 不可越线={redlines}" if redlines else "")
            )
        if agency:
            modes = ",".join(_string_list(agency.get("default_problem_solving_modes"), limit=3))
            forbidden_modes = "、".join(
                _string_list(agency.get("forbidden_passive_modes"), limit=2)
            )
            lines.append(
                "  agency: "
                f"choice={_first_text(agency.get('active_choice_axis'))}"
                + (f"; modes={modes}" if modes else "")
                + (f"; 禁止={forbidden_modes}" if forbidden_modes else "")
            )
        if identity:
            debt_sources = "、".join(_string_list(identity.get("debt_sources"), limit=2))
            lines.append(
                "  identity_pressure: "
                f"axis={_first_text(identity.get('choice_axis'))}; "
                f"pressure={_first_text(identity.get('pressure_source'))}"
                + (f"; debt={debt_sources}" if debt_sources else "")
            )
        sig_pieces = [
            piece
            for piece in (
                f"物={_first_text(signature.get('object'))}" if signature.get("object") else "",
                f"动作={_first_text(signature.get('action'))}" if signature.get("action") else "",
                f"短句={_first_text(signature.get('phrase'))}" if signature.get("phrase") else "",
                f"tic={_first_text(signature.get('tic'))}" if signature.get("tic") else "",
            )
            if piece
        ]
        if sig_pieces:
            lines.append("  signature: " + "; ".join(sig_pieces))
        voice_pieces = [
            _first_text(voice.get("sentence_length_preference")),
            _first_text(voice.get("vocabulary_register")),
        ]
        signature_words = _string_list(voice.get("signature_words"), limit=5)
        forbidden = _string_list(voice.get("forbidden_words"), limit=4)
        lines.append(
            "  voice_dna: "
            + " / ".join(piece for piece in voice_pieces if piece)
            + (f"; 标志词={','.join(signature_words)}" if signature_words else "")
            + (f"; 禁词={','.join(forbidden)}" if forbidden else "")
        )
        if dialogue_function:
            controls = "、".join(_string_list(dialogue_function.get("craft_controls"), limit=2))
            max_reveal = _first_text(dialogue_function.get("max_revelations_before_break"))
            lines.append(
                "  dialogue_function: "
                f"conflict_exposition={bool(dialogue_function.get('exposition_through_conflict'))}"
                + (f"; break_after={max_reveal}" if max_reveal else "")
                + (f"; controls={controls}" if controls else "")
            )
        if reward_contract:
            rewards = "、".join(_string_list(reward_contract.get("reader_rewards"), limit=2))
            risk = "、".join(_string_list(reward_contract.get("risk_controls"), limit=2))
            if rewards or risk:
                lines.append(
                    "  reward_contract: "
                    + (f"rewards={rewards}" if rewards else "")
                    + (f"; risk={risk}" if risk else "")
                )

        chains = _as_mapping(profile.get("unique_response_chain"))
        chain_key = scene_stimulus if scene_stimulus and scene_stimulus in chains else ""
        if not chain_key and chains:
            chain_key = next(iter(chains))
        chain = _as_mapping(chains.get(chain_key)) if chain_key else {}
        if chain:
            lines.append(
                f"  反应链[{chain_key}]: "
                f"1) {_first_text(chain.get('step_1'))} "
                f"2) {_first_text(chain.get('step_2'))} "
                f"3) {_first_text(chain.get('step_3'))}"
            )
    return "\n".join(lines)


def collect_signature_words(character_ids: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Return the union of ``voice_dna.signature_words`` for the given characters.

    Useful for the detector module which needs a flat word list for
    counting density inside a chapter.
    """

    config = load_character_engine()
    seen: list[str] = []
    seen_set: set[str] = set()
    for char_id in character_ids:
        profile = config.sample_profiles.get(char_id) if char_id else None
        if profile is None:
            continue
        for word in profile.voice_dna.signature_words:
            if word and word not in seen_set:
                seen_set.add(word)
                seen.append(word)
        sig = profile.signature_assets
        for value in (sig.object_marker, sig.action, sig.phrase, sig.tic):
            if value and value not in seen_set:
                seen_set.add(value)
                seen.append(value)
    return tuple(seen)


def collect_signature_words_from_profiles(
    profiles: Iterable[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Return signature words/assets from project-local fused profiles."""

    seen: list[str] = []
    seen_set: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, Mapping):
            continue
        voice = _as_mapping(profile.get("voice_dna"))
        signature = _as_mapping(profile.get("signature_assets"))
        candidates = [
            *_string_list(voice.get("signature_words")),
            _first_text(signature.get("object")),
            _first_text(signature.get("action")),
            _first_text(signature.get("phrase")),
            _first_text(signature.get("tic")),
        ]
        for word in candidates:
            if word and word not in seen_set:
                seen_set.add(word)
                seen.append(word)
    return tuple(seen)


def collect_forbidden_words(character_ids: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Return the union of ``voice_dna.forbidden_words`` across the given characters."""

    config = load_character_engine()
    seen: list[str] = []
    seen_set: set[str] = set()
    for char_id in character_ids:
        profile = config.sample_profiles.get(char_id) if char_id else None
        if profile is None:
            continue
        for word in profile.voice_dna.forbidden_words:
            if word and word not in seen_set:
                seen_set.add(word)
                seen.append(word)
    return tuple(seen)


def collect_forbidden_words_from_profiles(
    profiles: Iterable[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Return forbidden voice words from project-local fused profiles."""

    seen: list[str] = []
    seen_set: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, Mapping):
            continue
        voice = _as_mapping(profile.get("voice_dna"))
        for word in _string_list(voice.get("forbidden_words")):
            if word and word not in seen_set:
                seen_set.add(word)
                seen.append(word)
    return tuple(seen)
