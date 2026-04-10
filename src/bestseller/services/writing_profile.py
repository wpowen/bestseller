from __future__ import annotations

import logging
from typing import Any

from bestseller.domain.project import (
    CharacterEngineConfig,
    MarketPositioningConfig,
    ProjectCreate,
    SerializationStrategyConfig,
    WorldDesignConfig,
    WritingProfile,
)
from bestseller.infra.db.models import ProjectModel, StyleGuideModel
from bestseller.services.prompt_packs import (
    render_prompt_pack_fragment,
    render_prompt_pack_prompt_block,
    resolve_prompt_pack,
)
from bestseller.services.writing_presets import get_platform_preset, infer_genre_preset


def _merge_lists(base: list[Any], override: list[Any]) -> list[Any]:
    merged: list[Any] = []
    for item in [*base, *override]:
        if item not in merged:
            merged.append(item)
    return merged


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        elif isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = _merge_lists(merged[key], value)
        else:
            merged[key] = value
    return merged


_logger = logging.getLogger(__name__)

# Supported language families — used for validation and logging.
_SUPPORTED_LANGUAGE_PREFIXES = ("zh", "en")

_unsupported_warned: set[str] = set()


def is_english_language(language: str | None) -> bool:
    normalized = (language or "").strip().lower()
    return normalized.startswith("en")


def normalize_language(language: str | None) -> str:
    """Return a normalised language tag, defaulting to ``zh-CN``.

    Logs a one-time warning if the language is not in the supported set
    (currently Chinese and English).  Unsupported languages fall back to
    English when the tag looks Latin-script (fr, de, es, pt, it …) and
    to Chinese otherwise, so the prompts at least use a familiar script.
    """
    raw = (language or "").strip()
    if not raw:
        return "zh-CN"
    lower = raw.lower()
    if any(lower.startswith(p) for p in _SUPPORTED_LANGUAGE_PREFIXES):
        return raw
    # Unsupported — warn once per tag and choose the best fallback.
    if lower not in _unsupported_warned:
        _unsupported_warned.add(lower)
        _logger.warning(
            "Language '%s' is not fully supported (supported: zh-*, en-*). "
            "Prompts will use the closest supported language.",
            raw,
        )
    # Latin-script languages → English prompts are closer than Chinese.
    _LATIN_PREFIXES = ("fr", "de", "es", "pt", "it", "nl", "pl", "ro", "sv", "da", "no", "fi", "cs", "hu", "tr", "vi", "id", "ms", "tl")
    if any(lower.startswith(p) for p in _LATIN_PREFIXES):
        return "en-US"
    # CJK or other → Chinese prompts are closer.
    return "zh-CN"


def _default_writing_profile_payload(language: str | None = None) -> dict[str, Any]:
    if is_english_language(language):
        return WritingProfile(
            market=MarketPositioningConfig(
                platform_target="English-language fiction platform",
                content_mode="English-language serial fiction",
                opening_strategy="Open fast with the protagonist edge, an immediate problem, a concrete gain, and visible danger.",
                chapter_hook_strategy="End every chapter with a question, threat, reveal, or costly next move.",
                payoff_rhythm="Frequent short payoffs with longer-arc escalation",
                update_strategy="Serial release",
            ),
            character=CharacterEngineConfig(
                growth_curve="Escalate competence, pressure, and cost in visible steps.",
                relationship_tension="Trust and suspicion should pull against each other inside every alliance.",
                antagonist_mode="Escalating system-level opposition",
                ensemble_mode="Supporting cast should mirror, tempt, or challenge the protagonist's choices.",
            ),
            world=WorldDesignConfig(
                info_reveal_strategy="Reveal background through action, negotiation, conflict, and consequences instead of exposition blocks.",
            ),
            serialization=SerializationStrategyConfig(
                opening_mandate="Reveal the protagonist edge, core disturbance, short-term gain, and immediate danger as early as possible.",
                first_three_chapter_goal="Lock in the protagonist hook, the world disturbance, the first counter-pressure, and a strong read-on hook.",
                scene_drive_rule="Every scene needs a goal, resistance, escalation, information change, and a trailing hook.",
                exposition_rule="Keep exposition light; hide it inside action, trade-offs, conflict, and aftermath.",
                chapter_ending_rule="Every chapter ends with an unresolved question, reversal, or sharper danger.",
                free_chapter_strategy="Sample chapters must prove hook density, pace, and escalation early.",
            ),
        ).model_dump(mode="json")
    return WritingProfile().model_dump(mode="json")


def _genre_preset(genre: str, sub_genre: str | None = None) -> dict[str, Any]:
    preset = infer_genre_preset(genre, sub_genre)
    if preset is not None:
        return dict(preset.writing_profile_overrides)

    label = f"{genre} {sub_genre or ''}".lower()
    if any(token in label for token in ("末日", "科幻", "星际", "生存")):
        return {
            "market": {
                "platform_target": "番茄小说",
                "reader_promise": "开篇快速亮出异常降临、资源窗口和主角抢占先机的优势，持续用危机升级与资源差制造追读。",
                "selling_points": ["末日降临", "时间差优势", "资源囤积", "秩序崩坏", "真相揭露"],
                "trope_keywords": ["末日", "囤货", "系统", "重生", "规则生存", "打脸反杀"],
                "hook_keywords": ["倒计时", "稀缺资源", "规则异变", "背叛", "升级反杀"],
                "opening_strategy": "第一屏直接抛出末日倒计时、主角知道未来、资源窗口马上关闭。",
                "chapter_hook_strategy": "每章末给新的资源机会、规则异变、敌人反压或更大真相。",
                "pacing_profile": "fast",
                "payoff_rhythm": "短回报强刺激，长期埋更大灾变真相",
            },
            "character": {
                "protagonist_archetype": "先知型求生者",
                "protagonist_core_drive": "抢时间、抢资源、抢先建立安全边界",
                "golden_finger": "重生记忆、外挂商城或信息差优势",
                "growth_curve": "个人生存优势 -> 小团体统治力 -> 末日真相破局",
                "antagonist_mode": "生存竞争者与系统性灾变操盘者双重压迫",
            },
            "world": {
                "worldbuilding_density": "medium",
                "power_system_style": "资源、秩序、能力同步升级",
                "mystery_density": "high",
                "setting_tags": ["生存", "资源争夺", "规则异变", "秩序崩坏"],
            },
            "style": {
                "tone_keywords": ["狠", "快", "压迫感", "危机感"],
                "prose_style": "commercial-web-serial",
                "sentence_style": "short-punchy",
                "info_density": "lean",
                "dialogue_ratio": 0.42,
            },
        }
    if any(token in label for token in ("仙", "玄幻", "奇幻", "升级")):
        return {
            "market": {
                "platform_target": "起点中文网",
                "reader_promise": "快速给出主角天赋/机缘与压迫环境，持续用升级、夺宝、打脸和更大世界打开期待。",
                "selling_points": ["升级成长", "机缘夺取", "势力对抗", "越阶反杀", "世界地图扩张"],
                "trope_keywords": ["废柴逆袭", "秘境", "宗门", "升级", "气运", "打脸"],
                "hook_keywords": ["新境界", "秘宝", "仇敌", "宗门考核", "更高位面"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "逆袭型成长主角",
                "protagonist_core_drive": "向上突破阶层与命运封锁",
                "golden_finger": "特殊血脉、至宝、功法推演或外挂面板",
                "growth_curve": "底层逆袭 -> 越阶争锋 -> 位面扩张",
                "antagonist_mode": "阶层压迫 + 天才对照组 + 上位势力持续镇压",
            },
            "world": {
                "worldbuilding_density": "medium",
                "power_system_style": "境界制与资源制并行",
                "mystery_density": "medium",
                "setting_tags": ["宗门", "境界", "秘境", "传承"],
            },
            "style": {
                "tone_keywords": ["燃", "凌厉", "压迫感"],
                "dialogue_ratio": 0.32,
            },
        }
    if any(token in label for token in ("都市", "异能", "悬疑", "现实")):
        return {
            "market": {
                "platform_target": "番茄小说",
                "reader_promise": "很快让读者看到主角的异常能力、现实压力和可以立刻兑现的利益/反制机会。",
                "selling_points": ["现实代入", "能力外挂", "反差人设", "爽点兑现", "悬念推进"],
                "trope_keywords": ["都市", "系统", "逆袭", "悬疑", "身份反转"],
                "hook_keywords": ["反转", "秘密", "能力亮相", "现实代价"],
                "pacing_profile": "fast",
            },
            "character": {
                "protagonist_archetype": "现实受压型反击主角",
                "protagonist_core_drive": "翻盘现实处境并重塑身份价值",
            },
            "world": {
                "worldbuilding_density": "light",
                "info_reveal_strategy": "背景设定必须紧贴现实场景和事件推进。",
                "mystery_density": "medium",
                "setting_tags": ["现实压迫", "社会关系", "能力介入"],
            },
            "style": {
                "tone_keywords": ["利落", "现实感", "悬念"],
                "dialogue_ratio": 0.45,
            },
        }
    if any(token in label for token in ("女频", "成长", "言情", "宫斗")):
        return {
            "market": {
                "platform_target": "晋江文学城",
                "reader_promise": "快速建立人物关系张力、核心情绪困局与角色成长抓手，持续用关系推进和反转维持阅读欲。",
                "selling_points": ["人物关系拉扯", "情绪兑现", "成长弧光", "秘密揭露"],
                "trope_keywords": ["成长", "情感拉扯", "身份差", "信任博弈"],
                "hook_keywords": ["误解", "靠近", "选择", "秘密"],
                "pacing_profile": "medium-fast",
            },
            "character": {
                "protagonist_archetype": "情绪成长型主角",
                "relationship_tension": "吸引、试探、误解、并肩、再选择",
                "romance_mode": "主线感情线",
            },
            "world": {
                "worldbuilding_density": "light-medium",
                "mystery_density": "medium",
            },
            "style": {
                "tone_keywords": ["细腻", "拉扯感", "高情绪密度"],
                "dialogue_ratio": 0.48,
            },
        }
    return {
        "market": {
            "platform_target": "中文网文平台",
            "reader_promise": "开篇快速亮出主角、冲突、利益与危险，持续维持追读。",
            "selling_points": ["强主线", "持续冲突", "反转", "人物成长"],
            "trope_keywords": ["悬念", "反转", "升级"],
            "hook_keywords": ["危机", "利益", "秘密"],
        },
        "style": {
            "tone_keywords": [genre],
        },
    }


def resolve_writing_profile(
    explicit_profile: WritingProfile | dict[str, Any] | None,
    *,
    genre: str,
    sub_genre: str | None = None,
    audience: str | None = None,
    language: str | None = None,
) -> WritingProfile:
    inferred_genre_preset = infer_genre_preset(genre, sub_genre)
    resolved_language = (
        language
        or (inferred_genre_preset.language if inferred_genre_preset is not None else None)
        or "zh-CN"
    )
    base = _default_writing_profile_payload(resolved_language)
    preset = _genre_preset(genre, sub_genre)
    merged = _deep_merge(base, preset)
    if audience:
        merged["market"]["content_mode"] = audience
    explicit_payload: dict[str, Any] | None = None
    if explicit_profile is not None:
        explicit_payload = (
            explicit_profile.model_dump(mode="json")
            if isinstance(explicit_profile, WritingProfile)
            else dict(explicit_profile)
        )
        pack_key = explicit_payload.get("market", {}).get("prompt_pack_key")
    else:
        pack_key = None
    platform_name = (
        explicit_payload.get("market", {}).get("platform_target")
        if explicit_payload is not None
        else merged.get("market", {}).get("platform_target")
    )
    platform_preset = get_platform_preset(str(platform_name) if platform_name else None)
    if platform_preset is not None:
        merged = _deep_merge(merged, platform_preset.writing_profile_overrides)
    prompt_pack = resolve_prompt_pack(
        pack_key or (inferred_genre_preset.prompt_pack_key if inferred_genre_preset is not None else None),
        genre=genre,
        sub_genre=sub_genre,
    )
    if prompt_pack is not None:
        merged = _deep_merge(merged, prompt_pack.writing_profile_overrides)
    if explicit_payload is not None:
        merged = _deep_merge(merged, explicit_payload)
    profile = WritingProfile.model_validate(merged)
    if prompt_pack is not None:
        profile.market.prompt_pack_key = prompt_pack.key
    if not profile.style.tone_keywords:
        profile.style.tone_keywords = [genre] + ([sub_genre] if sub_genre else [])
    # Interactive fiction mode overrides — applied last so they always win
    if profile.interactive_fiction.enabled:
        profile.market.platform_target = "LifeScript"
        profile.market.content_mode = "交互式小说"
        profile.market.update_strategy = "全本发布"
        profile.style.pov_type = "second"
    return profile


def resolve_project_create_writing_profile(payload: ProjectCreate) -> WritingProfile:
    return resolve_writing_profile(
        payload.writing_profile,
        genre=payload.genre,
        sub_genre=payload.sub_genre,
        audience=payload.audience,
        language=payload.language,
    )


def build_project_metadata(payload: ProjectCreate, writing_profile: WritingProfile) -> dict[str, Any]:
    metadata = dict(payload.metadata)
    metadata["writing_profile"] = writing_profile.model_dump(mode="json")
    metadata.setdefault("platform_target", writing_profile.market.platform_target)
    metadata.setdefault("reader_promise", writing_profile.market.reader_promise)
    metadata.setdefault("selling_points", writing_profile.market.selling_points)
    metadata.setdefault("trope_keywords", writing_profile.market.trope_keywords)
    metadata.setdefault("opening_strategy", writing_profile.market.opening_strategy)
    metadata.setdefault("chapter_hook_strategy", writing_profile.market.chapter_hook_strategy)
    metadata.setdefault("prompt_pack_key", writing_profile.market.prompt_pack_key)
    metadata.setdefault("golden_finger", writing_profile.character.golden_finger)
    metadata.setdefault("protagonist_archetype", writing_profile.character.protagonist_archetype)
    metadata.setdefault("growth_curve", writing_profile.character.growth_curve)
    if payload.publishing is not None:
        metadata["publishing"] = _deep_merge(
            metadata.get("publishing", {}) if isinstance(metadata.get("publishing"), dict) else {},
            payload.publishing.model_dump(mode="json", exclude_none=True),
        )
    return metadata


def get_project_writing_profile(
    project: ProjectModel,
    style_guide: StyleGuideModel | None = None,
) -> WritingProfile:
    raw = project.metadata_json.get("writing_profile")
    if isinstance(raw, dict):
        return resolve_writing_profile(
            raw,
            genre=project.genre,
            sub_genre=project.sub_genre,
            audience=project.audience,
            language=project.language,
        )
    fallback = {}
    if style_guide is not None:
        fallback = {
            "style": {
                "pov_type": style_guide.pov_type,
                "tense": style_guide.tense,
                "tone_keywords": list(style_guide.tone_keywords),
                "prose_style": style_guide.prose_style,
                "sentence_style": style_guide.sentence_style,
                "info_density": style_guide.info_density,
                "dialogue_ratio": float(style_guide.dialogue_ratio),
                "taboo_topics": list(style_guide.taboo_topics),
                "taboo_words": list(style_guide.taboo_words),
                "reference_works": list(style_guide.reference_works),
                "custom_rules": list(style_guide.custom_rules),
            }
        }
    return resolve_writing_profile(
        fallback,
        genre=project.genre,
        sub_genre=project.sub_genre,
        audience=project.audience,
        language=project.language,
    )


def render_writing_profile_prompt_block(profile: WritingProfile, *, language: str | None = None) -> str:
    prompt_pack = resolve_prompt_pack(
        profile.market.prompt_pack_key,
        genre=" ".join(profile.style.tone_keywords) or "通用",
        sub_genre=None,
    )
    if is_english_language(language):
        lines = [
            "Platform and Reader Promise:",
            f"- Platform target: {profile.market.platform_target}",
            f"- Prompt pack: {profile.market.prompt_pack_key or 'auto/unspecified'}",
            f"- Content mode: {profile.market.content_mode}",
            f"- Reader promise: {profile.market.reader_promise or 'Establish a durable read-on desire fast.'}",
            f"- Selling points: {', '.join(profile.market.selling_points) or 'none specified'}",
            f"- Trope tags: {', '.join(profile.market.trope_keywords) or 'none'}",
            f"- Hook tags: {', '.join(profile.market.hook_keywords) or 'none'}",
            f"- Opening strategy: {profile.market.opening_strategy}",
            f"- Chapter hook strategy: {profile.market.chapter_hook_strategy}",
            f"- Pace: {profile.market.pacing_profile} / Payoff rhythm: {profile.market.payoff_rhythm}",
            "Character and Story Engine:",
            f"- Protagonist archetype: {profile.character.protagonist_archetype or 'unspecified'}",
            f"- Protagonist core drive: {profile.character.protagonist_core_drive or 'unspecified'}",
            f"- Unique edge: {profile.character.golden_finger or 'unspecified'}",
            f"- Growth curve: {profile.character.growth_curve}",
            f"- Romance mode: {profile.character.romance_mode}",
            f"- Relationship tension: {profile.character.relationship_tension}",
            f"- Antagonist mode: {profile.character.antagonist_mode}",
            "World and Information Release:",
            f"- Worldbuilding density: {profile.world.worldbuilding_density}",
            f"- Reveal strategy: {profile.world.info_reveal_strategy}",
            f"- Rule hardness: {profile.world.rule_hardness}",
            f"- Power system: {profile.world.power_system_style or 'unspecified'}",
            f"- Mystery density: {profile.world.mystery_density}",
            f"- Setting tags: {', '.join(profile.world.setting_tags) or 'none'}",
            "Style and Serialization Rules:",
            f"- POV: {profile.style.pov_type} / Tense: {profile.style.tense}",
            f"- Tone keywords: {', '.join(profile.style.tone_keywords) or 'unspecified'}",
            f"- Prose style: {profile.style.prose_style}",
            f"- Sentence style: {profile.style.sentence_style} / Info density: {profile.style.info_density} / Dialogue ratio: {profile.style.dialogue_ratio:.2f}",
            f"- Reference works: {', '.join(profile.style.reference_works) or 'none'}",
            f"- Extra rules: {'; '.join(profile.style.custom_rules) or 'none'}",
            "Serialization Guardrails:",
            f"- {profile.serialization.opening_mandate}",
            f"- {profile.serialization.first_three_chapter_goal}",
            f"- {profile.serialization.scene_drive_rule}",
            f"- {profile.serialization.exposition_rule}",
            f"- {profile.serialization.chapter_ending_rule}",
            f"- {profile.serialization.free_chapter_strategy}",
        ]
        pack_block = render_prompt_pack_prompt_block(prompt_pack)
        if pack_block:
            lines.extend(["Prompt Pack Notes:", pack_block])
        return "\n".join(lines)
    lines = [
        "平台与读者承诺：",
        f"- 平台目标：{profile.market.platform_target}",
        f"- Prompt Pack：{profile.market.prompt_pack_key or '自动/未指定'}",
        f"- 内容模式：{profile.market.content_mode}",
        f"- 读者承诺：{profile.market.reader_promise or '必须快速建立持续追读欲。'}",
        f"- 核心卖点：{'、'.join(profile.market.selling_points) or '暂无明确卖点'}",
        f"- 套路标签：{'、'.join(profile.market.trope_keywords) or '暂无'}",
        f"- 钩子标签：{'、'.join(profile.market.hook_keywords) or '暂无'}",
        f"- 开篇策略：{profile.market.opening_strategy}",
        f"- 章节钩子策略：{profile.market.chapter_hook_strategy}",
        f"- 节奏：{profile.market.pacing_profile} / 回报节奏：{profile.market.payoff_rhythm}",
        "人物与故事引擎：",
        f"- 主角原型：{profile.character.protagonist_archetype or '未指定'}",
        f"- 主角核心驱动力：{profile.character.protagonist_core_drive or '未指定'}",
        f"- 外挂/差异化优势：{profile.character.golden_finger or '未指定'}",
        f"- 成长曲线：{profile.character.growth_curve}",
        f"- 感情线模式：{profile.character.romance_mode}",
        f"- 关系张力：{profile.character.relationship_tension}",
        f"- 反派机制：{profile.character.antagonist_mode}",
        "世界与信息释放：",
        f"- 世界观密度：{profile.world.worldbuilding_density}",
        f"- 设定揭示方式：{profile.world.info_reveal_strategy}",
        f"- 规则硬度：{profile.world.rule_hardness}",
        f"- 力量体系：{profile.world.power_system_style or '未指定'}",
        f"- 悬念密度：{profile.world.mystery_density}",
        f"- 设定标签：{'、'.join(profile.world.setting_tags) or '暂无'}",
        "文风与连载规则：",
        f"- 视角：{profile.style.pov_type} / 时态：{profile.style.tense}",
        f"- 语气关键词：{'、'.join(profile.style.tone_keywords) or '未指定'}",
        f"- prose style：{profile.style.prose_style}",
        f"- 句式：{profile.style.sentence_style} / 信息密度：{profile.style.info_density} / 对话占比：{profile.style.dialogue_ratio:.2f}",
        f"- 参考作品：{'、'.join(profile.style.reference_works) or '暂无'}",
        f"- 额外规则：{'；'.join(profile.style.custom_rules) or '暂无'}",
        "连载硬约束：",
        f"- {profile.serialization.opening_mandate}",
        f"- {profile.serialization.first_three_chapter_goal}",
        f"- {profile.serialization.scene_drive_rule}",
        f"- {profile.serialization.exposition_rule}",
        f"- {profile.serialization.chapter_ending_rule}",
        f"- {profile.serialization.free_chapter_strategy}",
    ]
    pack_block = render_prompt_pack_prompt_block(prompt_pack)
    if pack_block:
        lines.extend(["Prompt Pack 设计：", pack_block])
    return "\n".join(lines)


def render_serial_fiction_guardrails(profile: WritingProfile, *, language: str | None = None) -> str:
    if is_english_language(language):
        guardrails = (
            "1. Reveal the protagonist's differentiating edge, the core disturbance, a short-term gain, and immediate danger as early as possible.\n"
            f"2. Deliver a concrete hook within the first {profile.market.hook_deadline_words} words; do not open with encyclopedia-style background.\n"
            "3. Every scene needs a goal, resistance, escalation, an information change, and a trailing hook.\n"
            "4. Release setting information through action, trade-offs, conflict, failure, and consequence instead of long exposition blocks.\n"
            "5. Let the protagonist quickly display an advantage, wound, hunger, blind spot, or sharp contrast readers can remember.\n"
            "6. End every chapter with a question, threat, reveal, or incentive that compels the next click.\n"
            "7. All payoffs, mysteries, and emotional turns must serve the active platform target, selling points, and trope tags."
        )
        prompt_pack = resolve_prompt_pack(
            profile.market.prompt_pack_key,
            genre=" ".join(profile.style.tone_keywords) or "general",
            sub_genre=None,
        )
        pack_rules = render_prompt_pack_fragment(prompt_pack, "global_rules")
        if pack_rules:
            guardrails = f"{guardrails}\n8. Extra Prompt Pack rules: {pack_rules}"
        return guardrails
    guardrails = (
        "1. 开篇要尽快亮出主角差异化优势、核心异变、短期利益与即时危险。\n"
        f"2. 在前 {profile.market.hook_deadline_words} 字内给出明确钩子，不要先铺背景百科。\n"
        "3. 每场必须包含明确目标、阻碍、升级、信息变化和尾钩，不要写成策划说明。\n"
        "4. 设定信息只在角色行动、交易、冲突、失败和代价里释放，禁止长段解释世界观。\n"
        "5. 主角必须尽快展现能让读者记住的优势、判断力、野心、伤口或反差。\n"
        "6. 章节尾部必须留下强迫读者继续阅读的问题、威胁或利益诱因。\n"
        "7. 所有爽点、悬念和情绪推进都要服从当前平台目标、卖点与套路标签。"
    )
    prompt_pack = resolve_prompt_pack(
        profile.market.prompt_pack_key,
        genre=" ".join(profile.style.tone_keywords) or "通用",
        sub_genre=None,
    )
    pack_rules = render_prompt_pack_fragment(prompt_pack, "global_rules")
    if pack_rules:
        guardrails = f"{guardrails}\n8. Prompt Pack 额外硬约束：{pack_rules}"
    return guardrails
