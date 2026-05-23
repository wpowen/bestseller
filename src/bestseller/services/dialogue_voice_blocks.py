"""Prompt rendering for dialogue voice contracts."""

from __future__ import annotations

from collections.abc import Sequence
import re

from bestseller.domain.dialogue_voice import DialogueVoiceDNA, DialogueVoiceReport
from bestseller.services.dialogue_archetypes import common_dialogue_forbidden_phrases


def render_dialogue_voice_block(
    profiles: Sequence[DialogueVoiceDNA],
    *,
    language: str = "zh-CN",
    max_profiles: int = 8,
) -> str:
    if not profiles:
        return ""
    if language.lower().startswith("en"):
        return _render_en(profiles, max_profiles=max_profiles)
    return _render_zh(profiles, max_profiles=max_profiles)


def render_dialogue_voice_violations_block(
    report: DialogueVoiceReport,
    *,
    language: str = "zh-CN",
) -> str:
    if not report.findings:
        return ""
    if language.lower().startswith("en"):
        lines = ["[Dialogue Voice Gate - repair required]"]
        for finding in report.findings[:8]:
            who = f"{finding.character}: " if finding.character else ""
            lines.append(f"- {finding.severity} {finding.code}: {who}{finding.detail}")
        lines.append(
            "Rewrite dialogue so each character follows its voice DNA, uses subtext, "
            "and includes non-answer beats instead of generic AI phrases."
        )
        return "\n".join(lines)

    lines = ["【对白声纹门禁 — 本章必须修复】"]
    for finding in report.findings[:8]:
        who = f"{finding.character}: " if finding.character else ""
        marker = "✗" if finding.severity == "critical" else "⚠"
        lines.append(f"  · {marker} [{finding.code}] {who}{finding.detail}")
    lines.append(
        "- 重写对白：每个角色必须回到自己的声纹、地域/口音规则和留白方式；"
        "禁止用「有意思/看来/果然/淡淡地说」等通用 AI 腔。"
    )
    return "\n".join(lines)


def _render_zh(profiles: Sequence[DialogueVoiceDNA], *, max_profiles: int) -> str:
    lines = ["【对白声纹合同 — 框架级硬约束】"]
    lines.append(
        "- 这是角色说话方式合同，不是本书临时补丁。"
        "每句对白必须同时体现：声纹、语境调度、潜台词、留白。"
    )
    lines.append("- 至少 30% 对话回合不能正面回答：用动作、沉默、反问、偏题或半句中断。")
    lines.append(
        "- 地域特色/口音只点关键字和语气，不整段音译；"
        "需要口译时用上下文解释，不写括号翻译。"
    )
    lines.append(
        "- 角色声纹靠语域、句法、节奏、关系策略和留白区分；"
        "不要为了达标机械复用固定口头禅。"
    )
    lines.append(
        "- 全角色硬禁词: " + "、".join(common_dialogue_forbidden_phrases("zh-CN")[:18])
    )
    for profile in profiles[:max_profiles]:
        lines.append(f"  · {profile.character_name} [{profile.archetype or 'custom'}]")
        if profile.register:
            lines.append(f"      语域: {profile.register}")
        if profile.voice_traits:
            lines.append(f"      声纹特征: {'；'.join(profile.voice_traits[:4])}")
        if profile.lexical_strategy:
            lines.append(f"      选词策略: {profile.lexical_strategy}")
        min_len, max_len = profile.sentence_length_zh
        lines.append(
            f"      句长/语速: {min_len}-{max_len}字 / {profile.speech_speed}"
        )
        if profile.syntax_quirks:
            lines.append(f"      句式: {'、'.join(profile.syntax_quirks[:4])}")
        if profile.rhythm_rules:
            lines.append(f"      节奏: {'；'.join(profile.rhythm_rules[:3])}")
        if profile.relationship_rules:
            lines.append(f"      关系调度: {'；'.join(profile.relationship_rules[:3])}")
        if profile.genre_adaptations:
            lines.append(f"      类型适配: {'；'.join(profile.genre_adaptations[:3])}")
        if profile.pet_phrases:
            lines.append(
                f"      词汇方向示例（可替换，不强制照抄）: "
                f"{'、'.join(profile.pet_phrases[:6])}"
            )
        if profile.body_tells:
            lines.append(f"      动作方向示例（可同功能改写）: {'、'.join(profile.body_tells[:4])}")
        if profile.regional_markers or profile.accent_profile:
            regional = "、".join(profile.regional_markers[:4])
            accent = f"；口音: {profile.accent_profile}" if profile.accent_profile else ""
            lines.append(f"      地域/口音: {regional}{accent}")
        if profile.interpretation_rules:
            lines.append(f"      口译规则: {'；'.join(profile.interpretation_rules[:3])}")
        if profile.context_modulation:
            samples = [
                f"{item.context}→{item.sample or item.pace}"
                for item in profile.context_modulation[:4]
                if item.context
            ]
            if samples:
                lines.append(f"      语境调度: {'；'.join(samples)}")
        if profile.negative_space:
            samples = [
                f"{item.condition or '不答'}→{item.response}"
                for item in profile.negative_space[:3]
            ]
            lines.append(f"      留白方式: {'；'.join(samples)}")
        if profile.forbidden_phrases:
            lines.append(f"      个人禁词: {'、'.join(profile.forbidden_phrases[:6])}")
    lines.append(
        "- 禁止所有角色共用同一套短语；同场对话里，"
        "每个角色的词、句长、动作标签必须能互相区分。"
    )
    return "\n".join(lines)


def _render_en(profiles: Sequence[DialogueVoiceDNA], *, max_profiles: int) -> str:
    lines = ["[Dialogue voice contract - framework-level hard constraints]"]
    lines.append(
        "- This is a character speech contract. Dialogue must show voice DNA, "
        "context modulation, subtext, and negative space."
    )
    lines.append(
        "- At least 30% of dialogue turns should be non-answers: action, silence, "
        "counter-question, deflection, or interruption."
    )
    lines.append(
        "- Regional flavor and accents should remain readable; interpret through "
        "context, not parenthetical translation."
    )
    lines.append(
        "- Voice distinction comes from register, syntax, rhythm, relationship "
        "strategy, and subtext. Do not mechanically reuse fixed catchphrases."
    )
    lines.append(
        "- Global hard bans: "
        + ", ".join(common_dialogue_forbidden_phrases("en")[:10])
    )
    for profile in profiles[:max_profiles]:
        lines.append(f"- {profile.character_name} [{profile.archetype or 'custom'}]")
        min_len, max_len = profile.sentence_length_zh
        lines.append(
            f"  register={profile.register}; approximate length band={min_len}-{max_len}; "
            f"pace={profile.speech_speed}"
        )
        if profile.voice_traits:
            lines.append(f"  voice traits: {'; '.join(profile.voice_traits[:4])}")
        if profile.lexical_strategy:
            lines.append(f"  diction strategy: {profile.lexical_strategy}")
        if profile.rhythm_rules:
            lines.append(f"  rhythm: {'; '.join(profile.rhythm_rules[:3])}")
        if profile.relationship_rules:
            lines.append(f"  relationship modulation: {'; '.join(profile.relationship_rules[:3])}")
        if profile.genre_adaptations:
            lines.append(f"  genre adaptation: {'; '.join(profile.genre_adaptations[:3])}")
        lexical_examples = _language_examples(profile.pet_phrases, language="en")
        if lexical_examples:
            lines.append(
                "  lexical examples, replace freely: "
                + ", ".join(lexical_examples[:6])
            )
        body_examples = _language_examples(profile.body_tells, language="en")
        if body_examples:
            lines.append(
                "  physical-beat examples, paraphrase freely: "
                + ", ".join(body_examples[:4])
            )
        if profile.negative_space:
            lines.append(
                "  negative space: "
                + "; ".join(
                    f"{item.condition or 'non-answer'} -> {item.response}"
                    for item in profile.negative_space[:3]
                )
            )
    return "\n".join(lines)


__all__ = [
    "render_dialogue_voice_block",
    "render_dialogue_voice_violations_block",
]


def _language_examples(items: Sequence[str], *, language: str) -> list[str]:
    if not language.lower().startswith("en"):
        return [item for item in items if item]
    return [item for item in items if item and not re.search(r"[\u4e00-\u9fff]", item)]
