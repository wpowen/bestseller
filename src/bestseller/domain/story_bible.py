from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_ROLE_MAX_LENGTH = 64
_ROLE_BREAK_SEPARATORS: tuple[str, ...] = (
    "—",
    " - ",
    ". ",
    ": ",
    ", ",
    "; ",
    "。 ",
    "：",
    "，",
    "；",
    "\n",
)
_ROLE_SENTENCE_PREFIXES: tuple[str, ...] = (
    "from ",
    "becomes ",
    "becoming ",
    "remains ",
    "must ",
    "cannot ",
    "needs to ",
)
_ROLE_SENTENCE_PREFIXES_ZH: tuple[str, ...] = ("从", "由", "必须", "需要")
_ROLE_SENTENCE_CONNECTORS: tuple[str, ...] = (
    " when ",
    " because ",
    " while ",
    " through ",
    " specifically ",
)
_AGE_UNKNOWN_MARKERS: tuple[str, ...] = (
    "unknown",
    "indeterminate",
    "ageless",
    "immortal",
    "timeless",
    "不详",
    "未知",
    "不确定",
)
_AGE_APPROX_PREFIX_OFFSETS: dict[str, int] = {
    "early": 2,
    "mid": 5,
    "late": 8,
}


def normalize_character_role_label(value: Any, *, fallback: str | None = None) -> Any:
    """Coerce a role-like value into a short label.

    LLM outputs sometimes stuff a full character-evolution sentence into the
    ``role`` field. This helper keeps the validator permissive enough to
    recover by trimming to the first clause break, while callers that need
    stricter semantics can combine it with ``is_safe_character_role_label``.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return fallback or text
    if len(text) <= _ROLE_MAX_LENGTH:
        return text
    for sep in _ROLE_BREAK_SEPARATORS:
        idx = text.find(sep)
        if 0 < idx <= _ROLE_MAX_LENGTH:
            return text[:idx].strip()
    if fallback:
        return fallback
    return text[:_ROLE_MAX_LENGTH].rstrip()


def is_safe_character_role_label(value: Any) -> bool:
    """Return whether ``value`` looks like a structural role label.

    Accepts compact labels such as ``ally`` / ``antagonist_lieutenant`` /
    ``Theo Blackwood's field operative (lower-tier antagonist)`` and rejects
    sentence-shaped arc descriptions that belong in metadata instead.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > _ROLE_MAX_LENGTH:
        return False
    if any(sep in text for sep in ("\n", "\r", "。", "；")):
        return False
    lower = text.lower()
    if lower.startswith(_ROLE_SENTENCE_PREFIXES) or text.startswith(_ROLE_SENTENCE_PREFIXES_ZH):
        return False
    if len(text) > 32 and any(connector in lower for connector in _ROLE_SENTENCE_CONNECTORS):
        return False
    return True


def normalize_character_age(value: Any) -> int | None:
    """Coerce age-like values into an integer when safe, else ``None``.

    ``cast_spec`` occasionally receives prose like ``late 40s`` or
    ``indeterminate (fae)``.  Approximate decade labels are normalized to a
    representative integer so downstream models stay structured; unbounded
    fantasy labels degrade to ``None`` instead of crashing validation.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    lower = text.lower()
    if any(marker in lower for marker in _AGE_UNKNOWN_MARKERS):
        return None
    if text.isdigit():
        return int(text)

    decade_match = re.search(r"\b(early|mid|late)\s+(\d{2,3})s\b", lower)
    if decade_match:
        prefix = decade_match.group(1)
        decade = int(decade_match.group(2))
        return decade + _AGE_APPROX_PREFIX_OFFSETS[prefix]

    plain_decade_match = re.search(r"\b(\d{2,3})s\b", lower)
    if plain_decade_match:
        return int(plain_decade_match.group(1)) + 5

    precise_match = re.fullmatch(r".*?(\d{1,3})\s*(?:years?\s*old|yo)?", lower)
    if precise_match:
        return int(precise_match.group(1))
    return None


_LLM_STRING_FALLBACK_KEYS: tuple[str, ...] = (
    "description",
    "summary",
    "overview",
    "overall",
    "overall_structure",
    "content",
    "text",
    "narrative",
    "detail",
    "details",
    "story_consequence",
    "notes",
)


def _flatten_to_text(value: Any, _depth: int = 0) -> str:
    """Recursively flatten a nested dict/list into readable Chinese-friendly prose.

    LLMs sometimes emit rich nested objects where the schema expects a single
    narrative string (e.g. ``power_structure`` / ``forbidden_zones``). Instead of
    hard-rejecting them, we rebuild a flat textual representation so downstream
    consumers still see the information.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if _depth > 6:
        return str(value)
    if isinstance(value, dict):
        if not value:
            return ""
        for key in _LLM_STRING_FALLBACK_KEYS:
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
        parts: list[str] = []
        for key, inner in value.items():
            rendered = _flatten_to_text(inner, _depth + 1)
            rendered = rendered.strip()
            if not rendered:
                continue
            parts.append(f"{key}：{rendered}")
        return "\n".join(parts)
    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            rendered = _flatten_to_text(item, _depth + 1).strip()
            if rendered:
                parts.append(f"- {rendered}" if "\n" not in rendered else rendered)
        return "\n".join(parts)
    return str(value)


def coerce_to_narrative_string(value: Any) -> Any:
    """Coerce nested dict/list-shaped LLM output into a single narrative string.

    Falls through unchanged when ``value`` is already a string or ``None`` so
    Pydantic's downstream validators still run.
    """
    if value is None or isinstance(value, str):
        return value
    flattened = _flatten_to_text(value).strip()
    return flattened or None


def coerce_to_string_list(value: Any) -> Any:
    """Coerce scalar/string/dict into list[str] for list-of-strings fields.

    - ``list`` is passed through (each item stringified if needed).
    - ``None`` / empty → ``[]``.
    - ``str`` is wrapped into a single-element list after stripping; callers
      keep multi-clause phrases intact to avoid false splits on punctuation.
    - ``dict`` is flattened so each ``key: value`` pair becomes one entry.
    """
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, str):
                text = item.strip()
                if text:
                    out.append(text)
            elif isinstance(item, (int, float, bool)):
                out.append(str(item))
            else:
                flattened = _flatten_to_text(item).strip()
                if flattened:
                    out.append(flattened)
        return out
    if isinstance(value, tuple):
        return coerce_to_string_list(list(value))
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, dict):
        out = []
        for key, inner in value.items():
            inner_text = _flatten_to_text(inner).strip()
            if inner_text:
                out.append(f"{key}：{inner_text}" if not isinstance(inner, str) else inner_text)
            else:
                out.append(str(key))
        return out
    return value


_VOLUME_RANGE_PATTERN = re.compile(r"(\d+)\s*[-–—~到至]\s*(\d+)")
_VOLUME_INT_PATTERN = re.compile(r"\d+")


def coerce_to_int_list(value: Any) -> Any:
    """Coerce human-shaped descriptors like ``'1-10章'`` / ``'1,3,5'`` into ``list[int]``.

    Ranges are expanded inclusively, capped at 200 entries to avoid runaway
    memory when the LLM says something like ``'1-999'``. Unparseable strings
    (e.g. ``'贯穿全书'``) return ``[]`` so downstream Pydantic validation sees a
    valid empty list rather than the raw string.
    """
    if value is None:
        return []
    if isinstance(value, list):
        out: list[int] = []
        for item in value:
            if isinstance(item, bool):
                continue
            if isinstance(item, int):
                out.append(item)
            elif isinstance(item, float) and item.is_integer():
                out.append(int(item))
            elif isinstance(item, str):
                m = _VOLUME_INT_PATTERN.search(item)
                if m:
                    out.append(int(m.group(0)))
        return out
    if isinstance(value, bool):
        return []
    if isinstance(value, (int,)):
        return [value]
    if isinstance(value, float):
        return [int(value)] if value.is_integer() else []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        numbers: list[int] = []
        for start_str, end_str in _VOLUME_RANGE_PATTERN.findall(text):
            start, end = int(start_str), int(end_str)
            if start <= end and end - start <= 200:
                numbers.extend(range(start, end + 1))
        if numbers:
            return sorted(set(numbers))
        fallback = [int(m) for m in _VOLUME_INT_PATTERN.findall(text)]
        return sorted(set(fallback))[:50]
    return value


class WorldRuleInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rule_id: str | None = Field(default=None, max_length=32)
    name: str = Field(min_length=1, max_length=4000)
    description: str = Field(min_length=1)
    story_consequence: str | None = None
    exploitation_potential: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_rule_name_alias(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "name" not in data and "rule_name" in data:
            data = {**data, "name": data.get("rule_name")}
        if "description" in data and not isinstance(data["description"], str):
            data = {**data, "description": coerce_to_narrative_string(data["description"])}
        return data


class PowerSystemInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    tiers: list[str] = Field(default_factory=list)
    acquisition_method: str | None = None
    hard_limits: str | None = None
    protagonist_starting_tier: str | None = None

    @field_validator("tiers", mode="before")
    @classmethod
    def _coerce_tiers(cls, v: Any) -> Any:
        return coerce_to_string_list(v)

    @field_validator("acquisition_method", "hard_limits", "protagonist_starting_tier", mode="before")
    @classmethod
    def _coerce_text_field(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)


class LocationInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=4000)
    location_type: str = Field(default="location", alias="type", min_length=1, max_length=4000)
    atmosphere: str | None = None
    key_rules: list[str] = Field(default_factory=list)
    story_role: str | None = None


class FactionInput(BaseModel):
    name: str = Field(min_length=1, max_length=4000)
    goal: str | None = None
    method: str | None = None
    relationship_to_protagonist: str | None = None
    internal_conflict: str | None = None


class HistoryEventInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    event: str = Field(min_length=1)
    relevance: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_event_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "event" in data and isinstance(data["event"], str) and data["event"].strip():
            return data
        for alias in ("name", "title", "event_name", "label"):
            alt = data.get(alias)
            if isinstance(alt, str) and alt.strip():
                merged = {**data, "event": alt}
                if "relevance" not in merged or not merged.get("relevance"):
                    description = data.get("description") or data.get("summary")
                    if isinstance(description, str) and description.strip():
                        merged["relevance"] = description
                return merged
        return data

    @field_validator("event", "relevance", mode="before")
    @classmethod
    def _coerce_text(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)


class WorldSpecInput(BaseModel):
    world_name: str | None = None
    world_premise: str | None = None
    rules: list[WorldRuleInput] = Field(default_factory=list)
    power_system: PowerSystemInput = Field(default_factory=PowerSystemInput)
    locations: list[LocationInput] = Field(default_factory=list)
    factions: list[FactionInput] = Field(default_factory=list)
    power_structure: str | None = None
    history_key_events: list[HistoryEventInput] = Field(default_factory=list)
    forbidden_zones: str | None = None

    @field_validator("power_structure", "forbidden_zones", "world_premise", mode="before")
    @classmethod
    def _coerce_narrative_fields(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)

    @field_validator("power_system", mode="before")
    @classmethod
    def _coerce_power_system(cls, v: Any) -> Any:
        if isinstance(v, str):
            text = v.strip()
            if not text:
                return {}
            return {"name": text[:4000]}
        return v


class CharacterRelationshipInput(BaseModel):
    character: str = Field(min_length=1, max_length=4000)
    type: str = Field(min_length=1, max_length=4000)
    tension: str | None = None


class CharacterKnowledgeStateInput(BaseModel):
    knows: list[str] = Field(default_factory=list)
    falsely_believes: list[str] = Field(default_factory=list)
    unaware_of: list[str] = Field(default_factory=list)


class CharacterVoiceProfileInput(BaseModel):
    """Per-character speech and behavioural fingerprint."""

    speech_register: str | None = None  # 文雅/口语/粗犷/书卷气/军事化/…
    verbal_tics: list[str] = Field(default_factory=list)  # 口头禅/标志性用语
    sentence_style: str | None = None  # 长句思辨型/短句利落型/碎片独白型/…
    emotional_expression: str | None = None  # 内敛/外放/反讽/冷幽默/沉默型/…
    mannerisms: list[str] = Field(default_factory=list)  # 标志性肢体语言/习惯动作
    internal_monologue_style: str | None = None  # 内心独白语气特征
    vocabulary_level: str | None = None  # 高/中/低/混合

    @field_validator("verbal_tics", "mannerisms", mode="before")
    @classmethod
    def _coerce_tic_lists(cls, v: Any) -> Any:
        return coerce_to_string_list(v)

    @field_validator(
        "speech_register",
        "sentence_style",
        "emotional_expression",
        "internal_monologue_style",
        "vocabulary_level",
        mode="before",
    )
    @classmethod
    def _coerce_voice_text(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)


class CharacterMoralFramework(BaseModel):
    """Per-character moral compass — what lines they will/won't cross."""

    core_values: list[str] = Field(default_factory=list)  # 核心信条
    lines_never_crossed: list[str] = Field(default_factory=list)  # 不可逾越的底线
    willing_to_sacrifice: str | None = None  # 愿意为目标牺牲什么

    @field_validator("core_values", "lines_never_crossed", mode="before")
    @classmethod
    def _coerce_moral_lists(cls, v: Any) -> Any:
        return coerce_to_string_list(v)

    @field_validator("willing_to_sacrifice", mode="before")
    @classmethod
    def _coerce_moral_text(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)


class CharacterIPAnchorInput(BaseModel):
    """Commercial-novel IP anchors — the 3-quirks-and-a-wound checklist.

    Commercial bestsellers make readers remember characters by giving each one
    concrete, sensory hooks: unusual quirks (at least three per protagonist),
    signature objects they carry, a distinctive sensory signature (smell,
    sound, touch), and a single core psychological wound that explains every
    irrational decision. Without these, even well-plotted protagonists blur
    into interchangeable archetypes — the root cause of historical bug #14
    ("protagonist has no memorable features").

    All lists are plain strings so Pydantic can validate loose LLM output; L2
    Bible Gate layers stricter checks on top (protagonist needs >=3 quirks,
    core_wound must be non-empty, etc.).
    """

    quirks: list[str] = Field(default_factory=list)
    sensory_signatures: list[str] = Field(default_factory=list)
    signature_objects: list[str] = Field(default_factory=list)
    core_wound: str | None = None
    tag_memory: str | None = Field(
        default=None,
        max_length=4000,
        description="每角色1个跨章节重复的标签动作/口头禅，如'拇指摩挲铜钱边缘''推眼镜'",
    )
    independent_life: str | None = Field(
        default=None,
        max_length=4000,
        description="配角独立于主线之外的生活细节，如'今天上午还在工地刮大白，一天260块'",
    )

    @field_validator("quirks", "sensory_signatures", "signature_objects", mode="before")
    @classmethod
    def _coerce_ip_lists(cls, v: Any) -> Any:
        return coerce_to_string_list(v)

    @field_validator("core_wound", "tag_memory", "independent_life", mode="before")
    @classmethod
    def _coerce_ip_text(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)


# ---------------------------------------------------------------------------
# Personhood layer — psych profile, life history, social network, beliefs,
# family imprint, villain charisma. All optional, all nested on CharacterInput.
# Together they answer: "what makes this character feel like a real person, not
# a plot function?" — bug #14's deeper sibling.
# ---------------------------------------------------------------------------


_BIG_FIVE_KEYS: tuple[str, ...] = (
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
)


def _coerce_big_five(value: Any) -> Any:
    """Normalise OCEAN scores into ``{trait: 0..100}``.

    LLMs return Big Five scores in many shapes: 0-1 floats, 1-5 Likert,
    Chinese trait names, or full sentences. We clamp to 0-100 ints and only
    keep the canonical English keys so downstream lookup is stable.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        return value  # let pydantic reject non-dicts loudly
    aliases = {
        "o": "openness", "开放性": "openness", "open": "openness",
        "c": "conscientiousness", "尽责性": "conscientiousness", "responsibility": "conscientiousness",
        "e": "extraversion", "外向性": "extraversion", "extroversion": "extraversion",
        "a": "agreeableness", "宜人性": "agreeableness", "friendliness": "agreeableness",
        "n": "neuroticism", "神经质": "neuroticism", "emotional_stability": "neuroticism",
    }
    out: dict[str, int] = {}
    for raw_key, raw_score in value.items():
        if not isinstance(raw_key, str):
            continue
        key = aliases.get(raw_key.strip().lower(), raw_key.strip().lower())
        if key not in _BIG_FIVE_KEYS:
            continue
        score = raw_score
        if isinstance(score, bool):
            continue
        if isinstance(score, (int, float)):
            score_f = float(score)
            if 0 <= score_f <= 1:
                score_f *= 100
            elif 1 <= score_f <= 5:
                score_f = (score_f - 1) * 25
            out[key] = max(0, min(100, int(round(score_f))))
        elif isinstance(score, str):
            m = re.search(r"-?\d+(?:\.\d+)?", score)
            if m:
                num = float(m.group(0))
                if 0 <= num <= 1:
                    num *= 100
                elif 1 <= num <= 5:
                    num = (num - 1) * 25
                out[key] = max(0, min(100, int(round(num))))
    return out


class CharacterPsychProfile(BaseModel):
    """Personality fingerprint anchored in real psychometric frameworks.

    The point isn't clinical accuracy — it's giving the LLM a stable,
    well-known reference so two prompts that mention the same character
    converge on the same decision-making style. MBTI alone is famously
    crude, so we layer four complementary frames: type (MBTI), trait
    intensity (OCEAN), motivational core (Enneagram), and intimacy
    response (attachment style).
    """

    mbti: str | None = None  # 如 "INTJ"，决策与认知风格
    big_five: dict[str, int] = Field(default_factory=dict)  # OCEAN 五维 0-100
    enneagram: str | None = None  # 如 "5w4"，动机内核 + 翼型
    attachment_style: str | None = None  # 安全/焦虑/回避/混乱
    cognitive_biases: list[str] = Field(default_factory=list)  # 解释非理性决定
    temperament: str | None = None  # 胆汁/多血/黏液/抑郁

    @field_validator("big_five", mode="before")
    @classmethod
    def _coerce_big_five_field(cls, v: Any) -> Any:
        return _coerce_big_five(v)

    @field_validator("cognitive_biases", mode="before")
    @classmethod
    def _coerce_psych_lists(cls, v: Any) -> Any:
        return coerce_to_string_list(v)

    @field_validator(
        "mbti",
        "enneagram",
        "attachment_style",
        "temperament",
        mode="before",
    )
    @classmethod
    def _coerce_psych_text(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)


class LifeEventInput(BaseModel):
    """A single dated/aged formative event in the character's life."""

    age: int | None = Field(default=None, ge=0)
    title: str = Field(min_length=1, max_length=4000)
    summary: str | None = None
    impact: str | None = None  # 该事件对人格/选择的塑造方式

    @field_validator("age", mode="before")
    @classmethod
    def _coerce_event_age(cls, v: Any) -> int | None:
        return normalize_character_age(v)

    @field_validator("title", "summary", "impact", mode="before")
    @classmethod
    def _coerce_event_text(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)


class CharacterLifeHistory(BaseModel):
    """A character's biography — not the plot they're in, but who they were
    before page one.

    A protagonist who shows up at chapter 1 with no past is a plot function,
    not a person. This schema captures the off-page life: education,
    careers tried and abandoned, defining moments, lingering regrets.
    Trauma is split out from generic ``formative_events`` because it has
    different narrative behaviour (it gets *triggered*, not just remembered).
    """

    formative_events: list[LifeEventInput] = Field(default_factory=list)
    education: str | None = None  # 学历/师承/自学路径
    career_history: list[str] = Field(default_factory=list)  # 职业履历，含失败
    defining_moments: list[str] = Field(default_factory=list)  # 3-5 个人格定型瞬间
    trauma: list[str] = Field(default_factory=list)  # 可触发的 PTSD/回避场景
    achievements: list[str] = Field(default_factory=list)  # 引以为傲的成就
    regrets: list[str] = Field(default_factory=list)  # 终生遗憾

    @field_validator(
        "career_history",
        "defining_moments",
        "trauma",
        "achievements",
        "regrets",
        mode="before",
    )
    @classmethod
    def _coerce_history_lists(cls, v: Any) -> Any:
        return coerce_to_string_list(v)

    @field_validator("education", mode="before")
    @classmethod
    def _coerce_history_text(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)

    @field_validator("formative_events", mode="before")
    @classmethod
    def _coerce_event_list(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            return [v]
        if isinstance(v, str):
            text = v.strip()
            return [{"title": text}] if text else []
        return v


class SocialTieInput(BaseModel):
    """One named tie in the social network — name + relationship + emotional
    weight. Distinct from ``CharacterRelationshipInput`` (which is plot-scoped)
    because social ties include people who never appear on-page but shape the
    character (a dead grandmother, a missing father, a hometown rival)."""

    name: str = Field(min_length=1, max_length=4000)
    bond: str | None = None  # 关系性质：母亲/恩师/初恋/宿敌/…
    emotional_weight: str | None = None  # 情感强度与色彩：依赖/愧疚/憎恨/敬畏/…
    influence: str | None = None  # 此人如何塑造该角色的价值观或行为

    @model_validator(mode="before")
    @classmethod
    def _coerce_keyed_tie(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "name" in data:
            return data
        if len(data) != 1:
            return data
        ((bond, inner),) = data.items()
        if isinstance(inner, dict):
            merged = {**inner}
            merged.setdefault("bond", str(bond))
            if not str(merged.get("name") or "").strip():
                merged["name"] = str(bond)
            return merged
        text = _flatten_to_text(inner).strip()
        return {
            "name": text or str(bond),
            "bond": str(bond),
        }

    @field_validator("name", "bond", "emotional_weight", "influence", mode="before")
    @classmethod
    def _coerce_tie_text(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)


class CharacterSocialNetwork(BaseModel):
    """Layered social graph — family, mentors, peers, hierarchy, community.

    A character without ties is a free-floating plot device. This schema
    forces the planner to anchor every major character to at least a
    handful of named others, so chapter generation can reach for "would
    her brother approve?" or "the mentor's voice in her head" without
    having to invent connections mid-scene.
    """

    family: list[SocialTieInput] = Field(default_factory=list)
    mentors: list[SocialTieInput] = Field(default_factory=list)  # 恩师/影响 ta 价值观的人
    peers: list[SocialTieInput] = Field(default_factory=list)  # 同辈/朋友/友谊
    superiors: list[SocialTieInput] = Field(default_factory=list)  # 上级/组织内尊者
    subordinates: list[SocialTieInput] = Field(default_factory=list)  # 下属/被庇护者
    community: list[str] = Field(default_factory=list)  # 所属社群：家乡/校友/教派/行会
    enemies: list[SocialTieInput] = Field(default_factory=list)  # 仇人，含成因
    dependencies: list[str] = Field(default_factory=list)  # 谁需要 ta、ta 需要谁

    @field_validator("community", "dependencies", mode="before")
    @classmethod
    def _coerce_social_lists(cls, v: Any) -> Any:
        return coerce_to_string_list(v)

    @field_validator(
        "family",
        "mentors",
        "peers",
        "superiors",
        "subordinates",
        "enemies",
        mode="before",
    )
    @classmethod
    def _coerce_tie_list(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            return [v]
        if isinstance(v, str):
            text = v.strip()
            return [{"name": text}] if text else []
        return v


class CharacterBelief(BaseModel):
    """What the character believes about the universe and themselves.

    Belief drives behaviour at the moments where personality alone is
    ambiguous — does she pray before the duel? Does he sacrifice the one
    for the many? Without a declared belief system, the LLM defaults
    every character to a vaguely modern-secular stance, which flattens
    historical, fantasy, and devout-character voices alike.
    """

    religion: str | None = None  # 宗教身份
    devotion_level: str | None = None  # 虔诚程度：狂热/虔诚/挂名/怀疑/无信仰
    philosophical_stance: str | None = None  # 儒/道/佛/法/虚无/实用/存在主义/…
    political_view: str | None = None  # 政治立场（如剧情相关）
    superstitions: list[str] = Field(default_factory=list)  # 个人迷信
    ideology: str | None = None  # 终极信念：自由/秩序/革命/守旧/进化/混沌
    crisis_of_faith: str | None = None  # 信仰可能动摇的触发点

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_belief_shape(cls, data: Any) -> Any:
        if data is None:
            return {}
        if isinstance(data, str):
            text = data.strip()
            return {"ideology": text} if text else {}
        if isinstance(data, (list, tuple, set)):
            texts = [_flatten_to_text(item).strip() for item in data]
            texts = [text for text in texts if text]
            return {"ideology": "；".join(texts)} if texts else {}
        return data

    @field_validator("superstitions", mode="before")
    @classmethod
    def _coerce_belief_lists(cls, v: Any) -> Any:
        return coerce_to_string_list(v)

    @field_validator(
        "religion",
        "devotion_level",
        "philosophical_stance",
        "political_view",
        "ideology",
        "crisis_of_faith",
        mode="before",
    )
    @classmethod
    def _coerce_belief_text(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)


class CharacterFamilyImprint(BaseModel):
    """The original-family blueprint — what the character was shaped by
    before they could choose.

    Distinct from ``CharacterSocialNetwork.family`` (which lists the *who*)
    and ``CharacterLifeHistory.formative_events`` (which lists the *what*).
    This schema is the *imprint*: the unspoken rules, role assignments,
    and inherited assumptions a character carries into every adult choice,
    often without noticing. Common source of "why does she always defer
    to authority figures" / "why does he sabotage every promotion".
    """

    parenting_style: str | None = None  # 权威/放任/虎妈/缺席/溺爱/暴力
    family_socioeconomic: str | None = None  # 出身阶层
    sibling_dynamics: str | None = None  # 被偏爱/被忽视/替罪羊/长姐如母
    inherited_values: list[str] = Field(default_factory=list)  # 从家庭继承的价值观
    family_secrets: list[str] = Field(default_factory=list)  # 家族秘密
    breaking_points: list[str] = Field(default_factory=list)  # 与原生家庭的决裂点

    @field_validator(
        "inherited_values",
        "family_secrets",
        "breaking_points",
        mode="before",
    )
    @classmethod
    def _coerce_imprint_lists(cls, v: Any) -> Any:
        return coerce_to_string_list(v)

    @field_validator(
        "parenting_style",
        "family_socioeconomic",
        "sibling_dynamics",
        mode="before",
    )
    @classmethod
    def _coerce_imprint_text(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)


class VillainCharismaProfile(BaseModel):
    """Antagonist humanity layer — what makes the villain magnetic, not just
    obstructive.

    A pure-evil antagonist is a difficulty slider, not a character. Readers
    don't remember villains who lose; they remember villains who *almost
    convinced them*. This schema demands the planner declare:
    1. A noble-origin motivation (so cruelty has a heart).
    2. A pain origin (so the cruelty is earned, not innate).
    3. Redeeming qualities (so the reader feels the loss when the villain falls).
    4. Philosophical appeal (so the reader briefly considers the villain right).
    5. A personal code (so the villain isn't a chaos engine).
    6. Tragic irony (so the arc has weight).
    7. Mirror to protagonist (so the conflict means something).

    Only required for ``role`` = ``antagonist`` (or roles containing it);
    supporting heavies can leave it empty.
    """

    noble_motivation: str | None = None  # 高尚出发点：救妹妹/变革腐朽/守护理想
    pain_origin: str | None = None  # 黑化起因：被背叛/被剥夺/亲历不公
    redeeming_qualities: list[str] = Field(default_factory=list)  # 让读者心软的瞬间
    philosophical_appeal: str | None = None  # 反派理念中合理的部分
    personal_code: list[str] = Field(default_factory=list)  # 反派绝不做的事，让 ta 不疯
    tragic_irony: str | None = None  # 为达目的反而毁掉初心所爱
    protagonist_mirror: str | None = None  # 与主角的相似处：同根而异路

    @field_validator(
        "redeeming_qualities",
        "personal_code",
        mode="before",
    )
    @classmethod
    def _coerce_villain_lists(cls, v: Any) -> Any:
        return coerce_to_string_list(v)

    @field_validator(
        "noble_motivation",
        "pain_origin",
        "philosophical_appeal",
        "tragic_irony",
        "protagonist_mirror",
        mode="before",
    )
    @classmethod
    def _coerce_villain_text(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)


class CharacterInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=4000)
    role: str = Field(default="supporting", min_length=1, max_length=_ROLE_MAX_LENGTH)

    @field_validator("role", mode="before")
    @classmethod
    def _coerce_role_to_short_label(cls, v: Any) -> Any:
        return normalize_character_role_label(v)

    age: int | None = Field(default=None, ge=0)

    @field_validator("age", mode="before")
    @classmethod
    def _coerce_age_to_int(cls, v: Any) -> int | None:
        return normalize_character_age(v)

    background: str | None = None
    goal: str | None = None
    fear: str | None = None
    flaw: str | None = None
    strength: str | None = None
    secret: str | None = None
    arc_trajectory: str | None = None
    arc_state: str | None = None
    knowledge_state: CharacterKnowledgeStateInput = Field(default_factory=CharacterKnowledgeStateInput)
    power_tier: str | None = None
    relationships: list[CharacterRelationshipInput] = Field(default_factory=list)
    voice_profile: CharacterVoiceProfileInput = Field(default_factory=CharacterVoiceProfileInput)
    moral_framework: CharacterMoralFramework = Field(default_factory=CharacterMoralFramework)
    ip_anchor: CharacterIPAnchorInput = Field(default_factory=CharacterIPAnchorInput)
    psych_profile: CharacterPsychProfile = Field(default_factory=CharacterPsychProfile)
    life_history: CharacterLifeHistory = Field(default_factory=CharacterLifeHistory)
    social_network: CharacterSocialNetwork = Field(default_factory=CharacterSocialNetwork)
    beliefs: CharacterBelief = Field(default_factory=CharacterBelief)
    family_imprint: CharacterFamilyImprint = Field(default_factory=CharacterFamilyImprint)
    villain_charisma: VillainCharismaProfile = Field(default_factory=VillainCharismaProfile)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConflictMapInput(BaseModel):
    character_a: str = Field(min_length=1, max_length=4000)
    character_b: str = Field(min_length=1, max_length=4000)
    conflict_type: str = Field(min_length=1, max_length=4000)
    trigger_condition: str | None = None


class ConflictForceInput(BaseModel):
    """A named conflict force active during specific volumes of the story.

    Unlike a single antagonist, conflict forces represent the diverse
    challenges the protagonist faces at different stages of their journey:
    local bullies, political intrigue, betrayals, faction wars, etc.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=4000)
    force_type: Literal["character", "faction", "environment", "internal", "systemic"] = Field(
        description="character / faction / environment / internal / systemic",
    )
    active_volumes: list[int] = Field(
        default_factory=list,
        description="Volume numbers where this force is the primary threat. Empty = all volumes.",
    )
    threat_description: str | None = None
    relationship_to_protagonist: str | None = None
    escalation_path: str | None = None
    character_ref: str | None = Field(
        default=None,
        max_length=4000,
        description="Name of a character in supporting_cast when force_type is 'character'.",
    )

    @field_validator("active_volumes", mode="before")
    @classmethod
    def _coerce_active_volumes(cls, v: Any) -> Any:
        return coerce_to_int_list(v)

    @field_validator(
        "threat_description",
        "relationship_to_protagonist",
        "escalation_path",
        mode="before",
    )
    @classmethod
    def _coerce_conflict_text(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)

    @field_validator("force_type", mode="before")
    @classmethod
    def _coerce_force_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            normalized = v.strip().lower()
            aliases = {
                "person": "character",
                "individual": "character",
                "group": "faction",
                "organization": "faction",
                "org": "faction",
                "place": "environment",
                "location": "environment",
                "setting": "environment",
                "mental": "internal",
                "psychological": "internal",
                "system": "systemic",
                "structural": "systemic",
            }
            return aliases.get(normalized, normalized)
        return v


_CHARACTER_DICT_INNER_KEYS: tuple[str, ...] = (
    "role",
    "age",
    "background",
    "goal",
    "fear",
    "flaw",
    "strength",
    "secret",
    "arc_trajectory",
    "arc_state",
    "knowledge_state",
    "power_tier",
    "relationships",
    "voice_profile",
    "moral_framework",
    "ip_anchor",
    "psych_profile",
    "life_history",
    "social_network",
    "beliefs",
    "family_imprint",
    "villain_charisma",
    "metadata",
)


def _looks_like_name_keyed_character(payload: Any) -> bool:
    """Return True when ``payload`` is ``{"角色名": {...character fields...}}``.

    LLMs occasionally wrap a single character in an outer name → dict dict.
    We detect this by checking that every value is itself a dict carrying at
    least one recognizable character field; the outer keys are the names.
    """
    if not isinstance(payload, dict) or not payload:
        return False
    for value in payload.values():
        if not isinstance(value, dict):
            return False
        if not any(inner_key in value for inner_key in _CHARACTER_DICT_INNER_KEYS):
            return False
    return True


def _unwrap_name_keyed_character(payload: Any) -> Any:
    """Convert ``{"名字": {...}}`` → ``{"name": "名字", ...}`` for a single character."""
    if not isinstance(payload, dict) or len(payload) != 1:
        return payload
    ((outer_key, inner),) = payload.items()
    if not isinstance(inner, dict):
        return payload
    merged: dict[str, Any] = {**inner}
    if "name" not in merged or not str(merged.get("name") or "").strip():
        merged["name"] = outer_key
    return merged


def _coerce_character_list(value: Any) -> Any:
    """Normalize character collections, unwrapping name-keyed dicts when needed."""
    if value is None:
        return []
    if isinstance(value, list):
        return [_unwrap_name_keyed_character(item) if _looks_like_name_keyed_character(item) else item for item in value]
    if isinstance(value, dict):
        if _looks_like_name_keyed_character(value):
            return [
                _unwrap_name_keyed_character({outer_key: inner})
                for outer_key, inner in value.items()
            ]
        return [value]
    return value


def _coerce_conflict_map(value: Any) -> Any:
    """Flatten a dict-shaped ``conflict_map`` into a list of conflict records.

    LLMs sometimes emit ``{"王青峰 vs 李墨白": {...}}`` instead of a list.
    We rebuild each entry as a dict, recovering ``character_a`` / ``character_b``
    from the outer key when the inner payload omits them.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        out: list[dict[str, Any]] = []
        for key, inner in value.items():
            if isinstance(inner, dict):
                merged: dict[str, Any] = {**inner}
                if not merged.get("character_a") or not merged.get("character_b"):
                    for separator in (" vs ", "vs.", " v. ", " 对 ", "↔", "→"):
                        if separator in str(key):
                            parts = [part.strip() for part in str(key).split(separator) if part.strip()]
                            if len(parts) == 2:
                                merged.setdefault("character_a", parts[0])
                                merged.setdefault("character_b", parts[1])
                                break
                if "conflict_type" not in merged:
                    merged["conflict_type"] = str(key)
                out.append(merged)
            elif isinstance(inner, str):
                out.append({"character_a": "", "character_b": "", "conflict_type": str(key), "trigger_condition": inner})
        return out
    return value


def _pop_primary_character(
    data: dict[str, Any],
    key: str,
    supporting_cast: list[Any],
) -> Any:
    value = data.get(key)
    if isinstance(value, list):
        characters = [
            _unwrap_name_keyed_character(item) if _looks_like_name_keyed_character(item) else item
            for item in value
            if isinstance(item, dict)
        ]
        if not characters:
            return value
        for extra in characters[1:]:
            if isinstance(extra, dict):
                extra = {**extra}
                extra.setdefault("role", key)
            supporting_cast.append(extra)
        first = characters[0]
        if isinstance(first, dict):
            first = {**first}
            first.setdefault("role", key)
        return first
    if _looks_like_name_keyed_character(value):
        return _unwrap_name_keyed_character(value)
    return value


class CastSpecInput(BaseModel):
    protagonist: CharacterInput | None = None
    antagonist: CharacterInput | None = None
    antagonist_forces: list[ConflictForceInput] = Field(default_factory=list)
    supporting_cast: list[CharacterInput] = Field(default_factory=list)
    conflict_map: list[ConflictMapInput] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_cast_shapes(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        patched = {**data}
        supporting_cast = _coerce_character_list(patched.get("supporting_cast"))
        if not isinstance(supporting_cast, list):
            supporting_cast = []
        if "protagonist" in patched:
            patched["protagonist"] = _pop_primary_character(
                patched,
                "protagonist",
                supporting_cast,
            )
        if "antagonist" in patched:
            patched["antagonist"] = _pop_primary_character(
                patched,
                "antagonist",
                supporting_cast,
            )
        if "supporting_cast" in patched or supporting_cast:
            patched["supporting_cast"] = supporting_cast
        if "conflict_map" in patched:
            patched["conflict_map"] = _coerce_conflict_map(patched["conflict_map"])
        return patched

    @model_validator(mode="after")
    def normalize_roles(self) -> "CastSpecInput":
        if self.protagonist is not None:
            self.protagonist.role = "protagonist"
        if self.antagonist is not None:
            self.antagonist.role = "antagonist"
        return self

    def all_characters(self) -> list[CharacterInput]:
        items: list[CharacterInput] = []
        if self.protagonist is not None:
            items.append(self.protagonist)
        if self.antagonist is not None:
            items.append(self.antagonist)
        items.extend(self.supporting_cast)
        return items


class VolumePlanOpeningStateInput(BaseModel):
    protagonist_status: str | None = None
    protagonist_power_tier: str | None = None
    world_situation: str | None = None


class VolumePlanResolutionInput(BaseModel):
    protagonist_power_tier: str | None = None
    goal_achieved: bool | None = None
    cost_paid: str | None = None
    new_threat_introduced: str | None = None


_WORD_COUNT_INT_PATTERN = re.compile(r"(\d[\d,]*)")


def _coerce_word_count_target(value: Any) -> Any:
    """Coerce ``"约 12000 字"`` / ``"12,000 words"`` into a numeric value.

    Keeps numeric input unchanged so downstream Pydantic validation still runs;
    unparseable strings degrade to ``None`` to avoid crashing the planner.
    """
    if value is None or isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    match = _WORD_COUNT_INT_PATTERN.search(text)
    if not match:
        return None
    digits = match.group(1).replace(",", "")
    try:
        return int(digits)
    except ValueError:
        return None


class VolumePlanEntryInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    volume_number: int = Field(gt=0)
    volume_title: str = Field(alias="title", min_length=1, max_length=4000)
    volume_theme: str | None = None
    word_count_target: float | int | str | None = None
    chapter_count_target: int | None = Field(default=None, ge=1)
    opening_state: VolumePlanOpeningStateInput = Field(default_factory=VolumePlanOpeningStateInput)
    volume_goal: str | None = None
    volume_obstacle: str | None = None
    volume_climax: str | None = None
    volume_resolution: VolumePlanResolutionInput = Field(default_factory=VolumePlanResolutionInput)
    key_reveals: list[str] = Field(default_factory=list)
    foreshadowing_planted: list[str] = Field(default_factory=list)
    foreshadowing_paid_off: list[str] = Field(default_factory=list)
    reader_hook_to_next: str | None = None

    @field_validator("word_count_target", mode="before")
    @classmethod
    def _coerce_word_count(cls, v: Any) -> Any:
        return _coerce_word_count_target(v)

    @field_validator(
        "key_reveals",
        "foreshadowing_planted",
        "foreshadowing_paid_off",
        mode="before",
    )
    @classmethod
    def _coerce_list_fields(cls, v: Any) -> Any:
        return coerce_to_string_list(v)

    @field_validator(
        "volume_theme",
        "volume_goal",
        "volume_obstacle",
        "volume_climax",
        "reader_hook_to_next",
        mode="before",
    )
    @classmethod
    def _coerce_text_fields(cls, v: Any) -> Any:
        return coerce_to_narrative_string(v)


class StoryBibleMaterializationResult(BaseModel):
    workflow_run_id: UUID
    project_id: UUID
    applied_artifacts: list[str] = Field(default_factory=list)
    world_rules_upserted: int = 0
    locations_upserted: int = 0
    factions_upserted: int = 0
    characters_upserted: int = 0
    relationships_upserted: int = 0
    state_snapshots_created: int = 0
    volumes_upserted: int = 0
    world_backbones_upserted: int = 0
    volume_frontiers_upserted: int = 0
    deferred_reveals_upserted: int = 0
    expansion_gates_upserted: int = 0
    voice_profiles_populated: int = 0
    moral_frameworks_populated: int = 0
    novelty_fingerprints_registered: int = 0
    source_artifact_ids: dict[str, UUID] = Field(default_factory=dict)


class CharacterStateSnapshotRead(BaseModel):
    chapter_number: int = Field(ge=0)
    scene_number: int | None = Field(default=None, ge=0)
    arc_state: str | None = None
    emotional_state: str | None = None
    physical_state: str | None = None
    power_tier: str | None = None
    trust_map: dict[str, Any] = Field(default_factory=dict)
    beliefs: list[Any] = Field(default_factory=list)
    notes: str | None = None


class StoryBibleWorldRuleRead(BaseModel):
    rule_code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    story_consequence: str | None = None
    exploitation_potential: str | None = None


class StoryBibleLocationRead(BaseModel):
    name: str = Field(min_length=1)
    location_type: str = Field(min_length=1)
    atmosphere: str | None = None
    key_rule_codes: list[str] = Field(default_factory=list)
    story_role: str | None = None


class StoryBibleFactionRead(BaseModel):
    name: str = Field(min_length=1)
    goal: str | None = None
    method: str | None = None
    relationship_to_protagonist: str | None = None
    internal_conflict: str | None = None


class StoryBibleRelationshipRead(BaseModel):
    character_a: str = Field(min_length=1)
    character_b: str = Field(min_length=1)
    relationship_type: str = Field(min_length=1)
    strength: float
    public_face: str | None = None
    private_reality: str | None = None
    tension_summary: str | None = None
    established_chapter_no: int | None = None
    last_changed_chapter_no: int | None = None


class StoryBibleCharacterRead(BaseModel):
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    goal: str | None = None
    fear: str | None = None
    flaw: str | None = None
    secret: str | None = None
    arc_trajectory: str | None = None
    arc_state: str | None = None
    power_tier: str | None = None
    is_pov_character: bool = False
    knowledge_state: dict[str, Any] = Field(default_factory=dict)
    voice_profile: dict[str, Any] = Field(default_factory=dict)
    moral_framework: dict[str, Any] = Field(default_factory=dict)
    latest_state: CharacterStateSnapshotRead | None = None


class WorldBackboneRead(BaseModel):
    title: str = Field(min_length=1)
    core_promise: str = Field(min_length=1)
    mainline_drive: str = Field(min_length=1)
    protagonist_destiny: str | None = None
    antagonist_axis: str | None = None
    thematic_melody: str | None = None
    world_frame: str | None = None
    invariant_elements: list[str] = Field(default_factory=list)
    stable_unknowns: list[str] = Field(default_factory=list)


class VolumeFrontierRead(BaseModel):
    volume_number: int = Field(ge=1)
    title: str = Field(min_length=1)
    frontier_summary: str = Field(min_length=1)
    expansion_focus: str | None = None
    start_chapter_number: int = Field(ge=1)
    end_chapter_number: int | None = Field(default=None, ge=1)
    visible_rule_codes: list[str] = Field(default_factory=list)
    active_locations: list[str] = Field(default_factory=list)
    active_factions: list[str] = Field(default_factory=list)
    active_arc_codes: list[str] = Field(default_factory=list)
    future_reveal_codes: list[str] = Field(default_factory=list)


class DeferredRevealRead(BaseModel):
    reveal_code: str = Field(min_length=1)
    label: str = Field(min_length=1)
    category: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    source_volume_number: int | None = Field(default=None, ge=1)
    reveal_volume_number: int = Field(ge=1)
    reveal_chapter_number: int = Field(ge=1)
    guard_condition: str | None = None
    status: str = Field(min_length=1)


class ExpansionGateRead(BaseModel):
    gate_code: str = Field(min_length=1)
    label: str = Field(min_length=1)
    gate_type: str = Field(min_length=1)
    condition_summary: str = Field(min_length=1)
    unlocks_summary: str = Field(min_length=1)
    source_volume_number: int | None = Field(default=None, ge=1)
    unlock_volume_number: int = Field(ge=1)
    unlock_chapter_number: int = Field(ge=1)
    status: str = Field(min_length=1)


class StoryBibleOverview(BaseModel):
    project_id: UUID
    project_slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    world_backbone: WorldBackboneRead | None = None
    world_rules: list[StoryBibleWorldRuleRead] = Field(default_factory=list)
    locations: list[StoryBibleLocationRead] = Field(default_factory=list)
    factions: list[StoryBibleFactionRead] = Field(default_factory=list)
    characters: list[StoryBibleCharacterRead] = Field(default_factory=list)
    relationships: list[StoryBibleRelationshipRead] = Field(default_factory=list)
    volume_frontiers: list[VolumeFrontierRead] = Field(default_factory=list)
    deferred_reveals: list[DeferredRevealRead] = Field(default_factory=list)
    expansion_gates: list[ExpansionGateRead] = Field(default_factory=list)
