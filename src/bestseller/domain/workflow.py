from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

_FUNCTIONAL_TITLE_PREFIXES_ZH = {
    "暗潮",
    "盲区",
    "裂痕",
    "回声",
    "风眼",
    "余烬",
    "伏线",
    "变局",
    "断点",
    "逆流",
    "边界",
    "悬灯",
    "浮标",
    "锈迹",
    "夜隙",
    "残局",
    "沉渊",
    "灰幕",
    "雾锁",
    "棱线",
    "铁壁",
    "荒火",
    "冷锋",
    "碎影",
}
_FUNCTIONAL_TITLE_SUFFIXES_ZH = {
    "初现",
    "入局",
    "投石",
    "试探",
    "铺火",
    "露锋",
    "破冰",
    "起手",
    "掀幕",
    "落子",
    "追索",
    "摸底",
    "拆解",
    "寻隙",
    "探针",
    "回查",
    "溯源",
    "揭层",
    "织网",
    "破壁",
    "加压",
    "围拢",
    "失衡",
    "封锁",
    "死线",
    "逼近",
    "绞杀",
    "窒息",
    "崩弦",
    "缩网",
    "反咬",
    "逆转",
    "偏航",
    "脱钩",
    "换轨",
    "回火",
    "翻盘",
    "倒戈",
    "破局",
    "重铸",
    "爆裂",
    "截断",
    "崩口",
    "闯线",
    "归零",
    "掀牌",
    "决堤",
    "焚天",
    "碎锁",
    "终幕",
}
# Procedural / structural beat labels prepended to a concrete event with a
# middot — "取证·义庄铜镜登记", "反证·林正淳取镜签名". Keep in sync with
# ``services/narrative_contracts.FUNCTIONAL_TITLE_DOT_LABELS_ZH`` (domain may
# not import from services, so the set is intentionally duplicated here).
_FUNCTIONAL_TITLE_DOT_LABELS_ZH = {
    "取证", "举证", "质证", "反证", "验尸", "验骨", "验明", "勘验", "勘查",
    "踏勘", "立案", "结案", "销案", "并案", "破案", "报案", "追凶", "缉凶",
    "缉拿", "缉捕", "查案", "查证", "查访", "排查", "走访", "盘问", "审讯",
    "提审", "对峙", "对质", "布局", "设局", "收网", "定罪", "翻供", "翻案",
    "申冤", "伸冤", "复盘", "推演", "推理", "解谜", "寻凶", "辨伪",
    "开局", "终局", "起手", "落子", "楔子", "引子", "序章", "伏笔", "铺垫",
    "转折", "高潮", "反转", "过渡", "收束", "序幕", "过场", "尾声",
    *_FUNCTIONAL_TITLE_PREFIXES_ZH,
    *_FUNCTIONAL_TITLE_SUFFIXES_ZH,
}
_TITLE_DOT_SEPARATORS = ("·", "・", "•", "‧", "∙", "·")


def _normalize_str_dict_list(value: Any) -> list[dict[str, str]]:
    """Coerce an LLM value into ``list[dict[str, str]]``.

    Tolerates every shape models actually emit for fields like
    ``world_state_deltas``: ``None``, a prose string, a single flat dict, or
    a list whose dict values are ints/bools/None. Returns a clean list so the
    schema never hard-fails in the planner outline repair loop.
    """

    def _coerce_dict(d: dict[Any, Any]) -> dict[str, str]:
        return {str(k): ("" if v is None else str(v)) for k, v in d.items()}

    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [{"change": s}] if s else []
    if isinstance(value, dict):
        coerced = _coerce_dict(value)
        return [coerced] if coerced else []
    if isinstance(value, list):
        out: list[dict[str, str]] = []
        for item in value:
            if isinstance(item, dict):
                ci = _coerce_dict(item)
                if ci:
                    out.append(ci)
            elif isinstance(item, str) and item.strip():
                out.append({"change": item.strip()})
        return out
    return []


def _normalize_str_list(value: Any) -> list[str]:
    """Coerce common LLM shapes into ``list[str]``.

    Reference fields are sometimes emitted as structured objects such as
    ``{"asset_key": "...", "description": "..."}``. Preserve the stable key
    when present, and fall back to a compact key-value string so schema
    validation does not reject otherwise usable outline intent.
    """

    def _coerce_item(item: Any) -> str | None:
        if item is None:
            return None
        if isinstance(item, str):
            stripped = item.strip()
            return stripped or None
        if isinstance(item, dict):
            for key in (
                "asset_key",
                "claim_key",
                "key",
                "id",
                "ref",
                "reference",
                "name",
                "title",
            ):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            parts = [
                f"{str(k).strip()}={str(v).strip()}"
                for k, v in item.items()
                if v is not None and str(k).strip() and str(v).strip()
            ]
            return "; ".join(parts) or None
        return str(item).strip() or None

    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            coerced = _coerce_item(item)
            if coerced:
                out.append(coerced)
        return out
    coerced = _coerce_item(value)
    return [coerced] if coerced else []


def _normalize_information_gap_mode(value: Any) -> str | None:
    """Coerce common LLM shapes for the chapter information-gap mode."""

    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, dict):
        for key in (
            "mode",
            "information_gap_mode",
            "gap_mode",
            "gap_type",
            "type",
            "value",
            "label",
        ):
            item = value.get(key)
            if item is not None:
                coerced = str(item).strip()
                if coerced:
                    return coerced
        parts = [
            f"{str(k).strip()}={str(v).strip()}"
            for k, v in value.items()
            if k is not None and v is not None and str(k).strip() and str(v).strip()
        ]
        return "; ".join(parts) or None
    return str(value).strip() or None


def _looks_like_functional_chapter_title(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    for separator in _TITLE_DOT_SEPARATORS:
        if separator in text:
            head = text.split(separator, 1)[0].strip()
            if head and head in _FUNCTIONAL_TITLE_DOT_LABELS_ZH:
                return True
    if len(text) > 8:
        return False
    return any(text.startswith(prefix) for prefix in _FUNCTIONAL_TITLE_PREFIXES_ZH) and any(
        text.endswith(suffix) for suffix in _FUNCTIONAL_TITLE_SUFFIXES_ZH
    )


class SceneOutlineInput(BaseModel):
    """Scene outline input with resilient parsing for LLM output variations.

    MiniMax M2.7 (and other LLMs) sometimes return non-standard field names:
      - story_task → purpose.story
      - emotion_task → purpose.emotion
      - scene_location → time_label
    The model_validator normalizes these before Pydantic field validation.
    """

    model_config = ConfigDict(populate_by_name=True)

    scene_number: int = Field(gt=0)
    # Default to "development" — LLMs sometimes omit this field entirely.
    scene_type: str = Field(
        default="development",
        max_length=4000,
        validation_alias=AliasChoices("scene_type", "type"),
    )
    title: str | None = Field(default=None, max_length=4000)
    time_label: str | None = None
    participants: list[str] = Field(default_factory=list)
    purpose: dict[str, Any] = Field(default_factory=dict)
    methodology_contract: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "methodology_contract",
            "methodology_overlay",
            "execution_overlay",
            "scene_methodology_contract",
        ),
    )
    entry_state: dict[str, Any] = Field(default_factory=dict)
    exit_state: dict[str, Any] = Field(default_factory=dict)
    key_dialogue_beats: list[str] = Field(default_factory=list)
    sensory_anchors: dict[str, Any] = Field(default_factory=dict)
    forbidden_actions: list[str] = Field(default_factory=list)
    hook_requirement: str | None = None
    signature_image: str | None = None
    cut_point: str | None = Field(
        default=None,
        validation_alias=AliasChoices("cut_point", "breakpoint", "scene_cut_point"),
    )
    action_sequence: list[str] = Field(default_factory=list)
    relationship_debts: list[str] = Field(default_factory=list)
    information_control_mode: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "information_control_mode",
            "information_control",
            "reader_information_mode",
        ),
    )
    target_word_count: int = Field(default=700, gt=0)

    # ── Outline-v2 executable script fields (all optional for backward compat) ──
    # These fields shift the outline from abstract goals to a concrete execution
    # plan that the scene drafter can follow directly.
    concrete_goal: str | None = Field(
        default=None,
        description=(
            "Executable scene goal — a specific physical action or event "
            "that happens in this scene (not an abstract thematic intention). "
            "E.g. '林渊用铜钱压住镜脚，阻止无脸人的手臂伸出' rather than '建立恐惧感'."
        ),
        validation_alias=AliasChoices("concrete_goal", "scene_concrete_goal", "concrete_action"),
    )
    protagonist_state: str | None = Field(
        default=None,
        description=(
            "What the protagonist is specifically feeling or wanting at the START "
            "of this scene, tied to a concrete object or event — not a generic emotion. "
            "E.g. '摸到铜钱时认出了和父亲记录本同一支笔的字，心里有一层什么东西开始松动'."
        ),
    )
    information_introduced: list[str] = Field(
        default_factory=list,
        description=(
            "Concrete facts/clues the reader learns in this scene. "
            "Each item should be specific enough that a reader could write it "
            "down as a clue. E.g. ['303是父亲戊子年未结账的地址', '王建业裤脚黑水来自303门缝']."
        ),
        validation_alias=AliasChoices("information_introduced", "reader_learns", "clues_revealed"),
    )
    information_held_back: list[str] = Field(
        default_factory=list,
        description=(
            "Facts the author knows but deliberately withholds from the reader "
            "in this scene — the deliberate tension gap. "
            "E.g. ['镜子里的无脸人是谁', '父亲为什么没有回来']."
        ),
        validation_alias=AliasChoices(
            "information_held_back", "reader_does_not_learn", "withheld_info"
        ),
    )
    object_signal: str | None = Field(
        default=None,
        description=(
            "How supernatural objects behave in this scene and what that signals. "
            "Must be specific: which object, what sensation, what it means. "
            "E.g. '铜钱边缘发凉（不是发烫）——冷是警示，代表镜局有主动意识在看林渊'."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_fields(cls, data: Any) -> Any:
        """Map non-standard LLM field names to expected schema fields."""
        if not isinstance(data, dict):
            return data

        # ── Coerce list-typed fields that LLMs frequently emit as a single
        # string (an arrow/comma-separated sequence, or a placeholder like '无').
        # Without this, deepseek/MiniMax outputs such as
        # action_sequence='扫视→锁定→确认' or relationship_debts='无' hard-fail
        # Pydantic list validation and force the ENTIRE outline batch to retry,
        # wasting planning time (observed on deepseek-v4-flash, 2026-06).
        for _list_field in (
            "participants",
            "key_dialogue_beats",
            "forbidden_actions",
            "action_sequence",
            "relationship_debts",
        ):
            if _list_field not in data:
                continue
            _val = data[_list_field]
            if _val is None:
                data[_list_field] = []
            elif isinstance(_val, str):
                _s = _val.strip()
                if not _s or _s in {"无", "暂无", "没有", "None", "null", "N/A", "n/a", "-", "—", "/"}:
                    data[_list_field] = []
                else:
                    _parts = re.split(r"[→、,，;；/\n|]+", _s)
                    data[_list_field] = [p.strip() for p in _parts if p.strip()]

        # ── scene_number: MiniMax uses float like 1.1, 1.2, 2.1 (chapter.scene)
        # Extract the fractional part as the scene-within-chapter ordinal.
        sn = data.get("scene_number")
        if isinstance(sn, float):
            # e.g. 5.2 → scene 2 within chapter 5
            frac = round((sn - int(sn)) * 10)
            data["scene_number"] = max(frac, 1)
        elif isinstance(sn, str):
            # "1.2" string → parse same logic
            try:
                fval = float(sn)
                frac = round((fval - int(fval)) * 10)
                data["scene_number"] = max(frac, 1)
            except ValueError:
                pass

        # story_task / emotion_task and newer planner aliases -> purpose dict
        purpose = data.get("purpose")
        if isinstance(purpose, str):
            purpose = {"story": purpose}
        elif not isinstance(purpose, dict):
            purpose = {}
        story_parts: list[str] = []
        for key in (
            "story_task",
            "story_emotion_task",
            "scene_purpose",
            "scene_goal",
            "plot_task",
        ):
            value = data.pop(key, None)
            if isinstance(value, str) and value.strip():
                story_parts.append(value.strip())
        if story_parts and "story" not in purpose:
            purpose["story"] = "；".join(story_parts)
        if "emotion_task" in data and "emotion" not in purpose:
            purpose["emotion"] = data.pop("emotion_task")
        if "aesthetic_goal" in data:
            aesthetic_goal = data.pop("aesthetic_goal")
            if isinstance(aesthetic_goal, str) and aesthetic_goal.strip():
                if "emotion" not in purpose:
                    purpose["emotion"] = aesthetic_goal.strip()
                elif "story" in purpose and aesthetic_goal not in str(purpose["story"]):
                    purpose["story"] = f"{purpose['story']}；{aesthetic_goal.strip()}"
        if "philosophical_anchor" in data:
            philosophical_anchor = data.pop("philosophical_anchor")
            if isinstance(philosophical_anchor, str) and philosophical_anchor.strip():
                if "story" not in purpose:
                    purpose["story"] = philosophical_anchor.strip()
                else:
                    purpose["story"] = f"{purpose['story']}；{philosophical_anchor.strip()}"
        if purpose:
            data["purpose"] = purpose
        # scene_location / scene_setting -> time_label
        for key in ("scene_location", "scene_setting", "setting", "location", "place"):
            if key in data and not data.get("time_label"):
                data["time_label"] = data.pop(key)
                break
        # participant aliases
        for key in ("active_characters", "characters", "cast", "participant_names"):
            if key not in data or data.get("participants"):
                continue
            raw_participants = data.pop(key)
            if isinstance(raw_participants, str):
                data["participants"] = [
                    item.strip()
                    for item in raw_participants.replace("，", ",").replace("、", ",").split(",")
                    if item.strip()
                ]
            elif isinstance(raw_participants, list):
                participants: list[str] = []
                for item in raw_participants:
                    if isinstance(item, str) and item.strip():
                        participants.append(item.strip())
                    elif isinstance(item, dict):
                        name = item.get("name") or item.get("character")
                        if isinstance(name, str) and name.strip():
                            participants.append(name.strip())
                data["participants"] = participants
        # scene_title → title
        if "scene_title" in data and not data.get("title"):
            data["title"] = data.pop("scene_title")
        # Rich scene-control aliases used by methodology packs and planner
        # repair prompts. Keep them structured so chapter-first drafting can
        # consume them directly instead of losing them in prose summaries.
        for src, dst in (
            ("dialogue_beats", "key_dialogue_beats"),
            ("key_dialogue", "key_dialogue_beats"),
            ("sensory_details", "sensory_anchors"),
            ("sensory_plan", "sensory_anchors"),
            ("must_not_do", "forbidden_actions"),
            ("forbidden_moves", "forbidden_actions"),
            ("signature_visual", "signature_image"),
            ("signature_scene_image", "signature_image"),
            ("action_beats", "action_sequence"),
            ("relationship_debt", "relationship_debts"),
        ):
            if src in data and not data.get(dst):
                data[dst] = data.pop(src)
        return data


class ChapterOutlineInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    chapter_number: int = Field(gt=0)
    title: str | None = Field(
        default=None,
        max_length=4000,
        validation_alias=AliasChoices("title", "chapter_title"),
    )
    chapter_goal: str = Field(
        default="推动本章剧情发展",
        validation_alias=AliasChoices("chapter_goal", "goal"),
        serialization_alias="goal",
    )
    opening_pressure: str | None = None
    protagonist_flaw: str | None = None
    required_payoff: str | None = None
    tail_hook: str | None = None
    opening_situation: str | None = None
    main_conflict: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "main_conflict",
            "chapter_main_conflict",
            "conflict",
            "core_conflict",
        ),
    )
    hook_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices("hook_type", "chapter_hook_type"),
    )
    hook_description: str | None = None
    # Webnovel method cards: the single emotion this chapter's conflict must
    # deliver (controlled vocabulary, e.g. 爽/燃/暖/虐/悬疑/紧张/轻松/甜/震撼).
    # Optional for backward compatibility with pre-existing outline payloads.
    target_emotion: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "target_emotion",
            "chapter_target_emotion",
            "core_emotion",
            "emotion_goal",
        ),
    )
    causal_contract: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "causal_contract",
            "causality_contract",
            "chapter_causal_skeleton",
            "causal_skeleton",
            "reader_desire_chain",
        ),
    )
    event_cycle_contract: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "event_cycle_contract",
            "event_unit_contract",
            "chapter_event_contract",
            "event_six_step",
        ),
    )
    chapter_event_role: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "chapter_event_role",
            "event_cycle_role",
            "event_unit_role",
            "event_role",
        ),
    )
    information_gap_mode: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "information_gap_mode",
            "info_gap_mode",
            "reader_information_mode",
        ),
    )
    methodology_contract: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "methodology_contract",
            "methodology_overlay",
            "execution_overlay",
            "chapter_methodology_contract",
        ),
    )
    world_rule_refs: list[str] = Field(default_factory=list)
    world_rule_landing: str | None = None
    world_state_deltas: list[dict[str, str]] = Field(default_factory=list)
    world_asset_refs: list[str] = Field(default_factory=list)
    authority_claim_refs: list[str] = Field(default_factory=list)
    world_scene_template_ref: str | None = None
    reveal_weight: int = Field(default=0, ge=0, le=5)
    anti_copy_boundary_notes: list[str] = Field(default_factory=list)
    location_refs: list[str] = Field(default_factory=list)
    faction_refs: list[str] = Field(default_factory=list)
    key_reveals: list[str] = Field(default_factory=list)
    volume_number: int = Field(default=1, gt=0)
    target_word_count: int = Field(default=2200, gt=0)
    scenes: list[SceneOutlineInput] = Field(default_factory=list)

    # ── Outline-v2 chapter-level executable fields (all optional) ──
    protagonist_inner_state: str | None = Field(
        default=None,
        description=(
            "The protagonist's specific inner goal or emotional state at the START "
            "of this chapter — must be tied to a concrete event or object. "
            "E.g. '看到名片背面字迹认出和父亲记录本的同一支笔，林渊的平静里有一层没有压下去的东西'. "
            "NOT: '林渊很镇定'. The inner state should imply forward momentum."
        ),
        validation_alias=AliasChoices(
            "protagonist_inner_state",
            "protagonist_inner_goal",
            "protagonist_motivation",
        ),
    )
    chapter_concrete_actions: list[str] = Field(
        default_factory=list,
        description=(
            "The 3-5 specific physical actions the protagonist takes in this chapter. "
            "Each should be observable: something a camera could record. "
            "E.g. ['用伞柄卡住电梯门防止门关上', '把铜钱甩向穿衣镜镜脚压住人影', "
            "'抓住王建业被拽入镜的瞬间——只拿到一只鞋']."
        ),
        validation_alias=AliasChoices(
            "chapter_concrete_actions", "concrete_actions", "protagonist_actions"
        ),
    )
    chapter_object_uses: list[str] = Field(
        default_factory=list,
        description=(
            "How each supernatural/professional object is used in this chapter. "
            "Format: '<object>: <action> → <result or signal>'. "
            "E.g. ['铜钱: 甩向镜脚→压住人影轮廓; 边缘崩裂→说明镜局力量超出铜钱承受极限', "
            "'罗盘: 揣入内袋未使用→暗示本章没有勘测需要，情境已无需定位']."
        ),
        validation_alias=AliasChoices(
            "chapter_object_uses", "object_uses", "tool_uses"
        ),
    )
    chapter_information_introduced: list[str] = Field(
        default_factory=list,
        description=(
            "Specific facts/clues the reader learns by the end of this chapter. "
            "Each should be concrete enough to write on a detective's whiteboard. "
            "E.g. ['303是父亲戊子年未结账的地址', '铜钱崩裂意味着镜局中存在比铜钱能压制的更强存在', "
            "'张建军手里有一枚铁片，形状和穿衣镜钥匙一模一样']."
        ),
        validation_alias=AliasChoices(
            "chapter_information_introduced", "information_introduced", "chapter_reveals"
        ),
    )
    chapter_information_held_back: list[str] = Field(
        default_factory=list,
        description=(
            "Facts the author knows but deliberately does NOT reveal in this chapter. "
            "These are the tension gaps that pull readers into the next chapter. "
            "E.g. ['镜子里的无脸人的真实身份', '父亲是否还活着（在镜中）', "
            "'张建军手里的铁片是怎么来的']."
        ),
        validation_alias=AliasChoices(
            "chapter_information_held_back", "information_held_back", "chapter_withheld"
        ),
    )
    selected_effect_skills: dict[str, Any] = Field(default_factory=dict)
    brainhole_contract: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_chapter_fields(cls, data: Any) -> Any:
        """Normalize common LLM aliases before schema validation."""
        if not isinstance(data, dict):
            return data

        # Robustness: every list[str] field below may be emitted by the LLM as
        # a single prose string or as structured reference objects. Normalize
        # these shapes so valid intent is never rejected on a type technicality.
        _STR_LIST_FIELDS = (
            "world_rule_refs",
            "world_asset_refs",
            "authority_claim_refs",
            "anti_copy_boundary_notes",
            "location_refs",
            "faction_refs",
            "key_reveals",
            "chapter_concrete_actions",
            "chapter_object_uses",
            "chapter_information_introduced",
            "chapter_information_held_back",
        )
        for _field in _STR_LIST_FIELDS:
            data[_field] = _normalize_str_list(data.get(_field))
        for _field in (
            "information_gap_mode",
            "info_gap_mode",
            "reader_information_mode",
        ):
            if _field in data:
                data[_field] = _normalize_information_gap_mode(data.get(_field))

        story_title = data.get("chapter_title") or data.get("subtitle")
        if story_title and (
            not data.get("title") or _looks_like_functional_chapter_title(data.get("title"))
        ):
            data["title"] = story_title

        scenes = data.get("scenes")
        if isinstance(scenes, list):
            for idx, scene in enumerate(scenes):
                if isinstance(scene, dict):
                    sn = scene.get("scene_number")
                    if sn is None:
                        # Missing entirely — assign 1-based index
                        scene["scene_number"] = idx + 1
                    elif isinstance(sn, float) and sn != int(sn):
                        # Float like 5.2 — will be handled by SceneOutlineInput
                        # but if extraction gives 0, override with index
                        frac = round((sn - int(sn)) * 10)
                        if frac < 1:
                            scene["scene_number"] = idx + 1
        reveal_weight = data.get("reveal_weight")
        if isinstance(reveal_weight, (int, float)):
            data["reveal_weight"] = max(0, min(5, int(reveal_weight)))
        elif isinstance(reveal_weight, str):
            stripped = reveal_weight.strip()
            if stripped.isdigit():
                data["reveal_weight"] = max(0, min(5, int(stripped)))
            else:
                # Robustness: a model may emit a prose description here
                # instead of an int (observed with MiniMax). Try to extract a
                # leading 0-5 digit, else default to 0 rather than hard-fail.
                m = re.search(r"[0-5]", stripped)
                data["reveal_weight"] = int(m.group()) if m else 0
        # str → list coercion for fields the schema requires as lists but a
        # prose model may emit as a single string.
        _notes = data.get("anti_copy_boundary_notes")
        if isinstance(_notes, str):  # list[str]
            _ns = _notes.strip()
            data["anti_copy_boundary_notes"] = [_ns] if _ns else []
        # world_state_deltas schema is list[dict[str, str]] but the LLM emits
        # it in every possible shape: a prose string, a single flat dict, or a
        # list whose dict values are ints/bools/None. Normalize ALL shapes so
        # valid intent is never hard-failed in the outline repair loop.
        data["world_state_deltas"] = _normalize_str_dict_list(
            data.get("world_state_deltas")
        )
        return data


class ChapterOutlineBatchInput(BaseModel):
    batch_name: str = Field(default="default-batch", min_length=1, max_length=4000)
    chapters: list[ChapterOutlineInput] = Field(default_factory=list)


class WorkflowMaterializationResult(BaseModel):
    workflow_run_id: UUID
    project_id: UUID
    batch_name: str
    chapters_created: int
    scenes_created: int
    source_artifact_id: UUID | None = None
