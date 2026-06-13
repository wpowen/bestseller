"""Webnovel method cards loader (``config/webnovel_method_cards.yaml``).

Design-layer methodology distilled from external webnovel craft references:

* chapter-end hook taxonomy (13 controlled ``hook_type`` keys)
* chapter-open hook taxonomy (7 keys)
* story-stage -> hook strength routing
* target-emotion controlled vocabulary + emotion -> plot-mode map
* golden-opening (ch1-3) hard rules incl. proper-noun caps

Consumed ONLY by the upstream planner (outline prompts) and the
deterministic outline enrichment — never injected into writer
PROSE_SCENE prompts (architecture decision: bake methodology upstream).

Degrades softly: a missing/corrupt config yields empty structures and
``""`` render fragments, never an exception (fallback cascades must not
hard-block the pipeline).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_int,
    as_str,
    as_str_tuple,
    load_yaml,
)

_CONFIG_FILENAME = "webnovel_method_cards.yaml"

# Hardened fallback so the emotion contract survives a missing config.
_DEFAULT_EMOTION_VOCAB: tuple[str, ...] = (
    "爽", "燃", "暖", "虐", "悬疑", "紧张", "轻松", "甜", "震撼",
)


@dataclass(frozen=True)
class HookCard:
    key: str
    name: str
    formula: str
    emotions: tuple[str, ...] = ()
    strength: str = ""
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageHookProfile:
    stage: str
    label: str
    strength: str
    recommended: tuple[str, ...] = ()


@dataclass(frozen=True)
class GoldenChapterRules:
    must: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    info_release_priority: tuple[str, ...] = ()
    info_release_note: str = ""
    new_proper_noun_caps: dict[str, int] = field(default_factory=dict)
    new_proper_noun_note: str = ""


@dataclass(frozen=True)
class WebnovelMethodCards:
    version: str
    target_emotion_vocabulary: tuple[str, ...]
    emotion_genre_map: dict[str, tuple[str, ...]]
    chapter_end_hooks: dict[str, HookCard]
    chapter_open_hooks: dict[str, HookCard]
    stage_hook_strength: dict[str, StageHookProfile]
    hook_selection_rules: tuple[str, ...]
    golden_chapter_rules: GoldenChapterRules


def _parse_hook(key: str, raw: object) -> HookCard:
    data = as_dict(raw)
    return HookCard(
        key=key,
        name=as_str(data.get("name"), default=key),
        formula=as_str(data.get("formula")),
        emotions=as_str_tuple(data.get("emotions")),
        strength=as_str(data.get("strength")),
        aliases=as_str_tuple(data.get("aliases")),
    )


def _parse_hooks(raw: object) -> dict[str, HookCard]:
    hooks: dict[str, HookCard] = {}
    for key, value in as_dict(raw).items():
        canonical = as_str(key)
        if canonical:
            hooks[canonical] = _parse_hook(canonical, value)
    return hooks


def _parse_stages(raw: object) -> dict[str, StageHookProfile]:
    stages: dict[str, StageHookProfile] = {}
    for key, value in as_dict(raw).items():
        canonical = as_str(key)
        if not canonical:
            continue
        data = as_dict(value)
        stages[canonical] = StageHookProfile(
            stage=canonical,
            label=as_str(data.get("label"), default=canonical),
            strength=as_str(data.get("strength")),
            recommended=as_str_tuple(data.get("recommended")),
        )
    return stages


def _parse_golden_rules(raw: object) -> GoldenChapterRules:
    data = as_dict(raw)
    caps = {
        as_str(key): as_int(value)
        for key, value in as_dict(data.get("new_proper_noun_caps")).items()
        if as_str(key) and as_int(value) > 0
    }
    return GoldenChapterRules(
        must=as_str_tuple(data.get("must")),
        forbidden=as_str_tuple(data.get("forbidden")),
        info_release_priority=as_str_tuple(data.get("info_release_priority")),
        info_release_note=as_str(data.get("info_release_note")),
        new_proper_noun_caps=caps,
        new_proper_noun_note=as_str(data.get("new_proper_noun_note")),
    )


@lru_cache(maxsize=1)
def load_webnovel_method_cards() -> WebnovelMethodCards:
    """Return the typed view; empty structures when the config is absent."""

    try:
        raw = load_yaml(_CONFIG_FILENAME)
    except Exception:  # corrupt YAML must degrade, not block
        raw = {}
    emotion_map: dict[str, tuple[str, ...]] = {}
    for key, value in as_dict(raw.get("emotion_genre_map")).items():
        canonical = as_str(key)
        if canonical:
            emotion_map[canonical] = as_str_tuple(as_dict(value).get("plot_modes"))
    vocab = as_str_tuple(raw.get("target_emotion_vocabulary"))
    if not vocab and raw:
        vocab = _DEFAULT_EMOTION_VOCAB
    return WebnovelMethodCards(
        version=as_str(raw.get("version")),
        target_emotion_vocabulary=vocab,
        emotion_genre_map=emotion_map,
        chapter_end_hooks=_parse_hooks(raw.get("chapter_end_hooks")),
        chapter_open_hooks=_parse_hooks(raw.get("chapter_open_hooks")),
        stage_hook_strength=_parse_stages(raw.get("stage_hook_strength")),
        hook_selection_rules=as_str_tuple(raw.get("hook_selection_rules")),
        golden_chapter_rules=_parse_golden_rules(raw.get("golden_chapter_rules")),
    )


def target_emotion_vocabulary() -> tuple[str, ...]:
    """Controlled ``target_emotion`` vocabulary (hardcoded fallback)."""

    vocab = load_webnovel_method_cards().target_emotion_vocabulary
    return vocab or _DEFAULT_EMOTION_VOCAB


# G7: map free-text tone/mood words (book_spec.tone) onto the controlled
# target_emotion vocabulary so the planner can enforce a per-volume emotion
# mix instead of letting the LLM default every chapter to 紧张.
_TONE_TO_EMOTION: tuple[tuple[tuple[str, ...], str], ...] = (
    (("喜剧", "搞笑", "沙雕", "轻喜", "诙谐", "幽默", "comedy", "comedic"), "轻松"),
    (("轻松", "日常", "悠闲", "治愈日常"), "轻松"),
    (("暖", "治愈", "温情", "温暖", "暖心", "warm", "heartwarming"), "暖"),
    (("甜", "甜宠", "糖", "sweet"), "甜"),
    (("悬念", "悬疑", "推理", "解谜", "suspense", "mystery"), "悬疑"),
    (("紧张", "危机", "压迫", "tension", "thriller"), "紧张"),
    (("爽", "打脸", "逆袭", "装逼", "扮猪吃虎"), "爽"),
    (("燃", "热血", "燃向", "epic"), "燃"),
    (("虐", "虐心", "意难平", "be", "tragic"), "虐"),
    (("震撼", "反转", "shock"), "震撼"),
)
# Emotions that read as "warm / light" vs "serious / heavy" — used to size
# the dominant bucket and cap the serious one.
_WARM_EMOTIONS = frozenset({"轻松", "暖", "甜", "爽", "燃"})


def _map_tone_word(word: str) -> str | None:
    lowered = word.lower()
    for needles, emotion in _TONE_TO_EMOTION:
        if any(n in word or n in lowered for n in needles):
            return emotion
    return None


def render_tone_emotion_contract_block(tone: object, *, language: str = "zh") -> str:
    """Render a per-volume target_emotion mix contract from ``book_spec.tone`` (G7).

    Parses a tone string like ``"喜剧40% + 暖35% + 悬念25%"`` into
    (vocabulary-emotion, weight) pairs, then instructs the planner which
    emotions should dominate the volume and caps the serious bucket — so a
    light-comedy book is not silently inverted into a tension thriller.
    Returns ``""`` when tone is empty/unparseable (soft).
    """

    import re

    text = str(tone or "").strip()
    if not text:
        return ""

    # Extract "word + optional percent" fragments split on +/、/，/,/ / and 空白.
    weighted: list[tuple[str, int]] = []
    seen: set[str] = set()
    for frag in re.split(r"[+、，,／/\s]+", text):
        frag = frag.strip()
        if not frag:
            continue
        m = re.search(r"(\d{1,3})\s*%?", frag)
        weight = int(m.group(1)) if m else 0
        word = re.sub(r"\d+%?", "", frag).strip("：:（）()")
        emotion = _map_tone_word(word) if word else None
        if emotion and emotion not in seen:
            seen.add(emotion)
            weighted.append((emotion, weight))

    if not weighted:
        return ""

    warm = sum(w for e, w in weighted if e in _WARM_EMOTIONS)
    serious = sum(w for e, w in weighted if e not in _WARM_EMOTIONS)
    total = warm + serious
    # When no explicit percentages were given, fall back to ordering only.
    has_weights = any(w > 0 for _, w in weighted)
    serious_cap = round(serious * 100 / total) if total else 35
    warm_share = round(warm * 100 / total) if total else 65

    ordered = sorted(weighted, key=lambda kv: -kv[1])
    primary = [e for e, _ in ordered if e in _WARM_EMOTIONS] or [
        e for e, _ in ordered
    ]
    mix_csv = "、".join(
        f"{e}{'(' + str(w) + '%)' if (has_weights and w) else ''}" for e, w in ordered
    )
    primary_csv = "、".join(primary[:3])

    if language == "en":
        cap_line = (
            f"serious emotions (紧张/悬疑/震撼/虐) together must not exceed ~{serious_cap}% of chapters"
            if has_weights
            else "serious emotions (紧张/悬疑/震撼/虐) must stay a clear minority"
        )
        return (
            "[VOLUME EMOTION MIX — hard tone contract]\n"
            f"BookSpec tone maps to target_emotion mix: {mix_csv}. "
            f"The dominant colours ({primary_csv}) MUST cover the majority of chapters; {cap_line}. "
            "Do NOT let the whole volume default to 紧张/悬疑 — that destroys the book's intended tone."
        )

    cap_line = (
        f"紧张/悬疑/震撼/虐 等严肃情绪合计章节占比不得超过约 {serious_cap}%"
        if has_weights
        else "紧张/悬疑/震撼/虐 等严肃情绪必须是明显少数"
    )
    warm_line = (
        f"暖色调情绪（{primary_csv}）应覆盖约 {warm_share}% 的章节（多数）"
        if has_weights
        else f"暖色调主色调情绪（{primary_csv}）应覆盖多数章节"
    )
    return (
        "【整卷情绪配比 · 基调硬约束】\n"
        f"BookSpec 基调映射到 target_emotion 词表：{mix_csv}。\n"
        f"- {warm_line}；{cap_line}。\n"
        "- 每章选 target_emotion 时先服务此配比再考虑单章戏剧性；"
        "禁止整卷一边倒成「紧张/悬疑」——那会丢失本书应有的基调色彩。"
    )


def chapter_end_hook_keys() -> tuple[str, ...]:
    """Canonical hook_type keys (empty when config is missing)."""

    return tuple(load_webnovel_method_cards().chapter_end_hooks)


def match_hook_type_key(raw_value: str) -> str | None:
    """Map a free-text hook_type onto the nearest canonical key.

    Returns ``None`` when nothing matches — callers keep the original
    value (soft normalization, never blocks).
    """

    text = (raw_value or "").strip()
    if not text:
        return None
    hooks = load_webnovel_method_cards().chapter_end_hooks
    if not hooks:
        return None
    lowered = text.lower().replace("-", "_").replace(" ", "_")
    if lowered in hooks:
        return lowered
    # Exact display-name match first, then alias containment (aliases are
    # ordered specific -> generic inside each card; cards keep YAML order).
    for key, card in hooks.items():
        if text == card.name:
            return key
    for key, card in hooks.items():
        for alias in card.aliases:
            if alias and (alias in text or text in alias):
                return key
    return None


def render_outline_hook_taxonomy_block(stage: str | None = None) -> str:
    """Compact planner-prompt fragment: hook taxonomy + selection rules.

    ``stage`` is one of the ``stage_hook_strength`` keys (``opening`` /
    ``early`` / ``middle`` / ``pre_climax`` / ``finale``); unknown or
    missing stages fall back to showing the whole routing table.
    """

    config = load_webnovel_method_cards()
    if not config.chapter_end_hooks:
        return ""
    lines: list[str] = [
        "【网文方法卡 · 章尾钩子13式 — hook_type 必须从以下 key 中选型】"
    ]
    for card in config.chapter_end_hooks.values():
        emotion_part = "/".join(card.emotions) or "通用"
        lines.append(
            f"- {card.key}（{card.name}）：{card.formula}"
            f"｜适配情绪：{emotion_part}｜强度：{card.strength or '中'}"
        )
    if config.chapter_open_hooks:
        lines.append("【章首钩子7式 — 每章前100字内起钩，禁止风景/天气暖场】")
        for card in config.chapter_open_hooks.values():
            lines.append(f"- {card.key}（{card.name}）：{card.formula}")
    lines.append("【钩子选型规则】")
    profile = config.stage_hook_strength.get(stage or "")
    if profile is not None:
        recommended = "、".join(profile.recommended) or "按情绪选型"
        lines.append(
            f"- 本批次所处阶段：{profile.label}｜推荐钩子强度：{profile.strength}"
            f"｜优先类型：{recommended}"
        )
    elif config.stage_hook_strength:
        for stage_profile in config.stage_hook_strength.values():
            recommended = "、".join(stage_profile.recommended)
            lines.append(
                f"- {stage_profile.label}：强度{stage_profile.strength}"
                f"（{recommended}）"
            )
    for rule in config.hook_selection_rules:
        lines.append(f"- {rule}")
    if config.target_emotion_vocabulary:
        lines.append(
            "- 每章 target_emotion 受控词表：【"
            + "、".join(config.target_emotion_vocabulary)
            + "】；先定情绪再定钩子"
        )
    return "\n".join(lines)


def render_golden_opening_rules_block() -> str:
    """Golden-opening (ch1-3) constraint fragment for planner prompts."""

    rules = load_webnovel_method_cards().golden_chapter_rules
    if not rules.must and not rules.forbidden:
        return ""
    lines: list[str] = ["【黄金三章硬约束 — 仅适用于覆盖第1-3章的批次】"]
    if rules.must:
        lines.append("必达项：")
        lines.extend(f"- {item}" for item in rules.must)
    if rules.forbidden:
        lines.append("绝对禁止：")
        lines.extend(f"- {item}" for item in rules.forbidden)
    if rules.info_release_priority:
        lines.append(
            "信息释放优先级：" + " > ".join(rules.info_release_priority)
        )
    if rules.info_release_note:
        lines.append(f"- {rules.info_release_note}")
    if rules.new_proper_noun_caps:
        caps_text = "，".join(
            f"{key}≤{value}个" for key, value in rules.new_proper_noun_caps.items()
        )
        lines.append(f"新专有名词上限：{caps_text}")
    if rules.new_proper_noun_note:
        lines.append(f"- {rules.new_proper_noun_note}")
    return "\n".join(lines)
