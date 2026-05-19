"""Creative direction packs for quickstart genre selection.

The quickstart UI should not turn a genre preset into a fixed story template.
This module builds small, deterministic creative directions from the genre
catalog, story-design grammars, and available distillation aggregates.  The
directions are prompt hints, not plot locks.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bestseller.services.distilled_strategy_compiler import (
    DistilledStrategyCard,
    compile_distilled_strategy_card,
)
from bestseller.services.story_design_grammars import resolve_story_design_grammar
from bestseller.services.writing_presets import GenrePreset, get_genre_preset, list_genre_presets


class GenreCreativeDirection(BaseModel, frozen=True):
    """One selectable creative bias for a genre preset."""

    model_config = ConfigDict(extra="ignore")

    key: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    stance: str = Field(min_length=1, max_length=160)
    logline: str = Field(min_length=1, max_length=360)
    source_mix: list[str] = Field(default_factory=list)
    genre_lenses: list[str] = Field(default_factory=list)
    reader_rewards: list[str] = Field(default_factory=list)
    narrative_drives: list[str] = Field(default_factory=list)
    conflict_engine: str = Field(default="", max_length=360)
    opening_hook: str = Field(default="", max_length=360)
    novelty_pressure: str = Field(default="", max_length=360)
    distilled_mechanisms: list[str] = Field(default_factory=list)
    anti_cliche_guardrails: list[str] = Field(default_factory=list)
    prompt_hints: dict[str, Any] = Field(default_factory=dict)


class GenreCreativityPack(BaseModel, frozen=True):
    """Creative directions generated for one genre preset."""

    model_config = ConfigDict(extra="ignore")

    genre_key: str = Field(min_length=1)
    default_key: str = Field(min_length=1)
    evidence_summary: list[str] = Field(default_factory=list)
    directions: list[GenreCreativeDirection] = Field(default_factory=list)


def _dedupe(values: list[object], *, limit: int = 12) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


_FAMILY_TRAUMA_DISPLAY_RE = re.compile(
    r"(父母|父亲|母亲|双亲|家人|亲人|亲属|失踪父母)"
    r"[^。！？；;，,\n]{0,12}"
    r"(失踪|消失|死亡|死去|被害|遇害|旧案|身世|真相|羁绊|动机)"
    r"|"
    r"(失踪|消失|死亡|死去|被害|遇害|旧案|身世|真相)"
    r"[^。！？；;，,\n]{0,12}"
    r"(父母|父亲|母亲|双亲|家人|亲人|亲属)",
)

_FAMILY_TRAUMA_DISPLAY_GUARDRAIL = "家庭创伤或身世旧案不得作为默认驱动"


def _normalize_guardrail_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _FAMILY_TRAUMA_DISPLAY_RE.search(text):
        return _FAMILY_TRAUMA_DISPLAY_GUARDRAIL
    return text


def _first(values: list[str], fallback: str) -> str:
    return values[0] if values else fallback


def _second(values: list[str], fallback: str) -> str:
    return values[1] if len(values) > 1 else fallback


def _platform_label(preset: GenrePreset) -> str:
    return _first(preset.recommended_platforms, "目标平台")


def _fallback_cross_heat(preset: GenrePreset) -> str:
    rewards = set(preset.reader_rewards)
    drives = set(preset.narrative_drives)
    if "认知回报" in rewards or "谜题真相" in drives:
        return "悬疑/惊悚/犯罪"
    if "关系回报" in rewards or "情绪关系" in drives:
        return "言情/女性向"
    if "能力回报" in rewards or "升级成长" in drives:
        return "奇幻/玄幻/异世界"
    if "身份回报" in rewards:
        return "都市/现实"
    return "悬疑/惊悚/犯罪"


def _preferred_value(values: list[str], preferred: list[str], fallback: str) -> str:
    for item in preferred:
        if item in values:
            return item
    return _first(values, fallback)


def _reward_priority(grammar_key: str, preset: GenrePreset) -> list[str]:
    haystack = f"{preset.genre} {preset.sub_genre} {' '.join(preset.aliases)}".lower()
    if grammar_key == "relationship-driven" or any(
        token in haystack for token in ("言情", "恋爱", "双男主", "bl", "耽美", "青春")
    ):
        return ["关系回报", "身份回报", "认知回报", "能力回报"]
    if grammar_key == "suspense-mystery" or any(token in haystack for token in ("悬疑", "推理", "谜")):
        return ["认知回报", "关系回报", "身份回报", "能力回报"]
    if grammar_key in {"action-progression", "otherworld-cross-system", "base-building"}:
        return ["能力回报", "身份回报", "认知回报", "关系回报"]
    if grammar_key == "strategy-worldbuilding":
        return ["身份回报", "认知回报", "能力回报", "关系回报"]
    return ["能力回报", "关系回报", "认知回报", "身份回报"]


def _drive_priority(grammar_key: str, preset: GenrePreset) -> list[str]:
    haystack = f"{preset.genre} {preset.sub_genre} {' '.join(preset.aliases)}".lower()
    if grammar_key == "relationship-driven" or any(
        token in haystack for token in ("言情", "恋爱", "双男主", "bl", "耽美", "青春")
    ):
        return ["情绪关系", "相互拯救", "成长选择", "权力博弈", "谜题真相"]
    if grammar_key == "suspense-mystery" or any(token in haystack for token in ("悬疑", "推理", "谜")):
        return ["谜题真相", "情绪关系", "世界规则揭示", "生存压力"]
    if grammar_key in {"action-progression", "otherworld-cross-system"}:
        return ["升级成长", "世界规则揭示", "势力经营", "权力博弈"]
    if grammar_key == "strategy-worldbuilding":
        return ["权力博弈", "势力经营", "世界规则揭示", "谜题真相"]
    return ["升级成长", "情绪关系", "谜题真相", "权力博弈"]


def _distilled_card_for_preset(preset: GenrePreset) -> DistilledStrategyCard | None:
    context = {
        "reader_promise": preset.trend_summary or preset.description,
        "unique_hook": " / ".join(preset.trend_keywords[:3]),
    }
    card = compile_distilled_strategy_card(
        genre=preset.genre,
        sub_genre=preset.sub_genre,
        project_context=context,
        max_mechanisms=5,
    )
    if card and card.selected_mechanisms and card.aggregate_key != "distillation-generic":
        return card

    # The current distillation library has a mature cross-system otherworld
    # aggregate.  Use it as an abstract mechanism source for fantasy/world-rule
    # genres, but keep the generated hint explicit that mechanisms must be
    # transformed into the selected genre's own rules.
    if (
        "奇幻/玄幻/异世界" in preset.heat_domains
        or "世界规则揭示" in preset.narrative_drives
        or "魔法" in preset.description
    ):
        fantasy_card = compile_distilled_strategy_card(
            category_key="otherworld-cross-system",
            genre=preset.genre,
            sub_genre=preset.sub_genre,
            project_context=context,
            max_mechanisms=5,
        )
        if fantasy_card:
            return fantasy_card
    if card and (card.selected_mechanisms or card.reader_reward_mix or card.craft_controls):
        return card
    return card


def _source_mix(
    preset: GenrePreset,
    *,
    card: DistilledStrategyCard | None,
    grammar_key: str,
) -> list[str]:
    values: list[object] = ["题材库"]
    if preset.trend_keywords or preset.trend_summary:
        values.append("调研热度库")
    if grammar_key and grammar_key != "default":
        values.append(f"剧情语法库:{grammar_key}")
    if card and (card.selected_mechanisms or card.reader_reward_mix or card.craft_controls):
        values.append(f"蒸馏库:{card.aggregate_key}")
    return _dedupe(values, limit=5)


def _evidence_summary(
    preset: GenrePreset,
    *,
    card: DistilledStrategyCard | None,
    grammar_key: str,
) -> list[str]:
    values: list[object] = []
    values.extend(preset.heat_domains[:2])
    values.extend(preset.reader_rewards[:2])
    values.extend(preset.narrative_drives[:2])
    if preset.trend_score:
        values.append(f"趋势分 {preset.trend_score}")
    if grammar_key and grammar_key != "default":
        values.append(f"语法 {grammar_key}")
    if card and card.aggregate_key:
        values.append(f"蒸馏 {card.aggregate_key}")
    return _dedupe(values, limit=10)


def _mechanism_names(card: DistilledStrategyCard | None, *, limit: int = 4) -> list[str]:
    if not card:
        return []
    return [item.mechanism_id for item in card.selected_mechanisms[:limit]]


def _anti_cliche_guardrails(
    card: DistilledStrategyCard | None,
    grammar_forbidden: list[str],
) -> list[str]:
    values: list[object] = []
    if card:
        values.extend(card.anti_copy_boundaries[:5])
    values.extend(grammar_forbidden[:5])
    return _dedupe([_normalize_guardrail_text(item) for item in values], limit=6)


def _direction(
    *,
    key: str,
    title: str,
    stance: str,
    logline: str,
    source_mix: list[str],
    genre_lenses: list[str],
    reader_rewards: list[str],
    narrative_drives: list[str],
    conflict_engine: str,
    opening_hook: str,
    novelty_pressure: str,
    distilled_mechanisms: list[str],
    anti_cliche_guardrails: list[str],
) -> GenreCreativeDirection:
    prompt_hints = {
        "creative_direction": title,
        "creative_stance": stance,
        "premise_seed": logline,
        "genre_lenses": genre_lenses,
        "reader_rewards": reader_rewards,
        "narrative_drives": narrative_drives,
        "conflict_engine": conflict_engine,
        "opening_hook": opening_hook,
        "novelty_pressure": novelty_pressure,
        "distilled_mechanisms": distilled_mechanisms,
        "anti_cliche_guardrails": anti_cliche_guardrails,
        "usage_rule": (
            "作为创意偏置使用，不得把题材标签写成固定套路；每个机制都要转化为本书专属规则、"
            "人物选择或代价。不得套用家庭创伤、身世旧案、神秘信物等默认动机；"
            "必须根据所选类型动态创造主角目标。"
        ),
    }
    return GenreCreativeDirection(
        key=key,
        title=title,
        stance=stance,
        logline=logline,
        source_mix=source_mix,
        genre_lenses=genre_lenses,
        reader_rewards=reader_rewards,
        narrative_drives=narrative_drives,
        conflict_engine=conflict_engine,
        opening_hook=opening_hook,
        novelty_pressure=novelty_pressure,
        distilled_mechanisms=distilled_mechanisms,
        anti_cliche_guardrails=anti_cliche_guardrails,
        prompt_hints=prompt_hints,
    )


def _build_pack(preset: GenrePreset) -> GenreCreativityPack:
    grammar = resolve_story_design_grammar(genre=preset.genre, sub_genre=preset.sub_genre)
    card = _distilled_card_for_preset(preset)
    source_mix = _source_mix(preset, card=card, grammar_key=grammar.key)
    mechanisms = _mechanism_names(card)
    guardrails = _anti_cliche_guardrails(card, grammar.forbidden_defaults)
    primary_heat = _first(preset.heat_domains, preset.genre)
    secondary_heat = _second(preset.heat_domains, _fallback_cross_heat(preset))
    primary_reward = _preferred_value(
        preset.reader_rewards,
        _reward_priority(grammar.key, preset),
        "短兑现",
    )
    secondary_reward = _preferred_value(
        [item for item in preset.reader_rewards if item != primary_reward],
        [item for item in _reward_priority(grammar.key, preset) if item != primary_reward],
        "新悬念",
    )
    primary_drive = _preferred_value(
        preset.narrative_drives,
        _drive_priority(grammar.key, preset),
        "目标推进",
    )
    secondary_drive = _preferred_value(
        [item for item in preset.narrative_drives if item != primary_drive],
        [item for item in _drive_priority(grammar.key, preset) if item != primary_drive],
        "压力升级",
    )
    platform = _platform_label(preset)
    lenses = _dedupe([primary_heat, secondary_heat, preset.genre, preset.sub_genre], limit=5)
    rewards = _dedupe([primary_reward, secondary_reward, *grammar.reader_rewards[:3]], limit=6)
    drives = _dedupe([primary_drive, secondary_drive, *grammar.chapter_change_vectors[:3]], limit=6)
    forbidden = "、".join(guardrails[:2]) if guardrails else "标签化开局和无代价外挂"
    mechanism_text = "、".join(mechanisms[:2]) if mechanisms else "章节变化向量和安全写法控制"

    directions = [
        _direction(
            key="genre-synthesis",
            title=f"{primary_reward}主轴",
            stance="保留题材承诺，但把回报绑定到更具体的选择代价。",
            logline=(
                f"在{preset.sub_genre}框架中，主角每次获得{primary_reward}，"
                f"都必须引发{primary_drive}层面的新压力，而不是只重复题材惯例。"
            ),
            source_mix=source_mix,
            genre_lenses=lenses,
            reader_rewards=rewards,
            narrative_drives=drives,
            conflict_engine=(
                f"{platform}读者能看到{primary_reward}，但每次兑现后都会暴露新的身份、关系或规则成本。"
            ),
            opening_hook=f"第一章从一次不可回避的{primary_drive}事件切入，结尾留下更高一级的{secondary_drive}压力。",
            novelty_pressure=f"同类题材常见的{forbidden}降权，优先写人物选择造成的后果。",
            distilled_mechanisms=mechanisms,
            anti_cliche_guardrails=guardrails,
        ),
        _direction(
            key="cross-genre-friction",
            title=f"{primary_heat} × {secondary_heat}",
            stance="用第二母题制造摩擦，让题材不是单一路线。",
            logline=(
                f"把{primary_heat}的核心奖赏放进{secondary_heat}的压力场："
                f"主角想拿到{primary_reward}，却必须先处理{secondary_drive}带来的误判。"
            ),
            source_mix=source_mix,
            genre_lenses=_dedupe([primary_heat, secondary_heat, *preset.heat_domains], limit=5),
            reader_rewards=rewards,
            narrative_drives=drives,
            conflict_engine=f"外层是{primary_drive}，内层用{secondary_drive}不断改变读者对人物关系和世界规则的判断。",
            opening_hook=f"第一章给一个看似属于{primary_heat}的开局，但在章末翻成{secondary_heat}问题。",
            novelty_pressure=f"避免只按{preset.genre}标签铺设定；每三章至少让另一个母题改变一次局势。",
            distilled_mechanisms=mechanisms,
            anti_cliche_guardrails=guardrails,
        ),
        _direction(
            key="distilled-mechanism-remix",
            title="蒸馏机制重组",
            stance="只借用成熟作品的抽象结构功能，全部改写为本题材专属规则。",
            logline=(
                f"以{mechanism_text}为结构灵感，把它改造成{preset.genre}中的原创矛盾："
                f"收益越明显，后续牵连越重。"
            ),
            source_mix=source_mix,
            genre_lenses=lenses,
            reader_rewards=rewards,
            narrative_drives=drives,
            conflict_engine=(
                f"每个蒸馏机制都必须落到本书自己的世界规则、人物弱点或资源代价，不能直接复述机制名。"
            ),
            opening_hook="第一章不解释大设定，先让一个机制在小事件里发生作用，并让代价立刻可见。",
            novelty_pressure="把蒸馏库当作反套路压力源，而不是桥段库；相似桥段、专名和组合全部禁止复用。",
            distilled_mechanisms=mechanisms,
            anti_cliche_guardrails=guardrails,
        ),
        _direction(
            key="anti-cliche-opening",
            title="反套路开局",
            stance="先排除最容易模板化的入口，再生成新的第一章问题。",
            logline=(
                f"开局避开{forbidden}，用一个会改变主角身份、关系或规则认知的事件，"
                f"把{primary_reward}和{secondary_reward}同时抛给读者。"
            ),
            source_mix=source_mix,
            genre_lenses=lenses,
            reader_rewards=rewards,
            narrative_drives=drives,
            conflict_engine=f"冲突不靠标签本身成立，而靠主角在{primary_drive}和{secondary_drive}之间做无法回退的选择。",
            opening_hook=f"开场 800 字内出现具体损失、异常或关系越界；章末必须让{secondary_reward}升级。",
            novelty_pressure=f"禁止把{preset.genre}写成预设套路清单；必须让设定服务一次新选择。",
            distilled_mechanisms=mechanisms,
            anti_cliche_guardrails=guardrails,
        ),
    ]
    return GenreCreativityPack(
        genre_key=preset.key,
        default_key=directions[0].key,
        evidence_summary=_evidence_summary(preset, card=card, grammar_key=grammar.key),
        directions=directions,
    )


@lru_cache(maxsize=1)
def get_genre_creativity_catalog_payload() -> dict[str, dict[str, Any]]:
    """Return web-safe creative packs keyed by genre preset key."""

    return {
        preset.key: _build_pack(preset).model_dump(mode="json")
        for preset in list_genre_presets()
    }


def get_genre_creativity_pack(genre_key: str | None) -> GenreCreativityPack | None:
    preset = get_genre_preset(genre_key)
    if preset is None:
        return None
    return _build_pack(preset)


def get_genre_creative_direction(
    genre_key: str | None,
    direction_key: str | None,
) -> GenreCreativeDirection | None:
    pack = get_genre_creativity_pack(genre_key)
    if pack is None or not pack.directions:
        return None
    wanted = str(direction_key or "").strip()
    for direction in pack.directions:
        if direction.key == wanted:
            return direction
    for direction in pack.directions:
        if direction.key == pack.default_key:
            return direction
    return pack.directions[0]


def creative_direction_to_user_hints(
    direction: GenreCreativeDirection | None,
) -> dict[str, Any]:
    if direction is None:
        return {}
    return dict(direction.prompt_hints)


__all__ = [
    "GenreCreativeDirection",
    "GenreCreativityPack",
    "creative_direction_to_user_hints",
    "get_genre_creative_direction",
    "get_genre_creativity_catalog_payload",
    "get_genre_creativity_pack",
]
