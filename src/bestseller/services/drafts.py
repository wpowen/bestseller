from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ChapterStatus, SceneStatus
from bestseller.domain.context import SceneWriterContextPacket
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    SceneCardModel,
    SceneDraftVersionModel,
    StyleGuideModel,
)
from bestseller.services.context import build_scene_writer_context_from_models
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.prompt_packs import (
    render_prompt_pack_fragment,
    render_prompt_pack_prompt_block,
    resolve_prompt_pack,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.services.story_bible import load_scene_story_bible_context
from bestseller.services.writing_profile import (
    render_serial_fiction_guardrails,
    render_writing_profile_prompt_block,
    resolve_writing_profile,
)
from bestseller.settings import AppSettings


def count_words(text: str) -> int:
    han_chars = re.findall(r"[\u4e00-\u9fff]", text)
    latin_words = re.findall(r"[A-Za-z0-9_]+", text)
    return len(han_chars) + len(latin_words)


_STRUCTURED_METADATA_KEYS = (
    "scene_summary",
    "chapter_summary",
    "core_conflict",
    "emotional_shift",
    "contract_alignment",
    "story_task",
    "emotion_task",
    "information_release",
    "tail_hook",
    "closing_hook",
    "entry_state",
    "exit_state",
)

_STRUCTURED_METADATA_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*|__)?"
    r"(?P<key>" + "|".join(_STRUCTURED_METADATA_KEYS) + r")"
    r"(?:\*\*|__)?\s*:\s*.+$",
    re.IGNORECASE,
)

# Chinese structural / meta-commentary terms that should NEVER appear in novel prose.
_CN_META_HEADER_RE = re.compile(
    r"^\s*#{1,4}\s*(?:修订说明|上一版草稿|重写策略|写作说明|场景说明|改写说明|润色说明"
    r"|策划说明|提纲|大纲|剧情任务|情绪任务|写法指导)\s*$"
)

_CN_META_LINE_RE = re.compile(
    r"^\s*(?:>+\s*)?[-*]?\s*(?:重写策略|本次任务|修订说明|剧情任务|情绪任务|入场状态|离场状态|收束状态"
    r"|开场状态|场景类型|场景目标|章节目标|本章目标|钩子设计|尾钩|结尾钩子|开场白设计|开场白|设想"
    r"|戏剧反讽意图|过渡方式|主题任务|信息释放|contract|合同式写作约束"
    r"|叙事树上下文|伏笔与兑现约束|关系与情绪推进约束|反派推进约束"
    r"|商业网文硬约束|Prompt Pack)\s*[：:].+$"
)

# Scene/chapter scaffold headings that must never appear in prose:
#   "## 场景 1：xxx"  /  "### 第三场"  /  "第1场" / "第一场"
_CN_SCAFFOLD_HEADING_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:第\s*[一二三四五六七八九十百零\d]+\s*(?:场|章)"
    r"|场景\s*[一二三四五六七八九十百零\d]+|结尾钩子|本章目标)"
    r"(?:\s*[:：].*)?$"
)

_CN_META_PROSE_RE = re.compile(
    r"(?:这一场景要完成的剧情任务是|这一场景的情绪任务是|本场景的写作目标是"
    r"|以下是.*的(?:场景|章节|草稿|初稿|提纲|大纲)"
    r"|以下为.*改写后的版本|以上是.*的(?:重写|修订|润色)版本"
    r"|根据(?:修订|重写|润色)(?:说明|要求|策略))"
)


def sanitize_novel_markdown_content(content_md: str) -> str:
    """Strip non-fiction structural markers and meta-commentary from novel prose."""
    # First pass: remove "### 修订说明" / "### 上一版草稿" blocks entirely.
    # These blocks run from the header to end-of-string or next H2+ header.
    content_md = re.sub(
        r"#{1,4}\s*(?:修订说明|上一版草稿|改写说明|润色说明).*?(?=\n##\s|\Z)",
        "",
        content_md,
        flags=re.DOTALL,
    )

    cleaned_lines: list[str] = []
    for raw_line in content_md.splitlines():
        stripped = raw_line.strip()
        # Drop English metadata lines (original filter)
        if _STRUCTURED_METADATA_LINE_RE.match(stripped):
            continue
        # Drop Chinese meta headers
        if _CN_META_HEADER_RE.match(stripped):
            continue
        # Drop Chinese meta key-value lines
        if _CN_META_LINE_RE.match(stripped):
            continue
        # Drop scaffold headings like "## 场景 1：xxx" / "第一场" / "结尾钩子"
        if _CN_SCAFFOLD_HEADING_RE.match(stripped):
            continue
        # Drop prose-wrapped metadata sentences
        if _CN_META_PROSE_RE.search(stripped):
            continue
        cleaned_lines.append(raw_line.rstrip())

    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


logger = logging.getLogger(__name__)

# Shared prohibition block injected into all writer / editor system prompts.
# Uses triple-quoted string to safely contain Chinese fullwidth quotes.
_NOVEL_OUTPUT_PROHIBITION = """\
【严禁出现以下内容】：
- 不得出现\u201c钩子\u201d\u201c开场白\u201d\u201c设想\u201d\u201c尾钩\u201d\u201c入场状态\u201d\u201c离场状态\u201d\u201c收束状态\u201d\u201c剧情任务\u201d\u201c情绪任务\u201d等策划术语
- 不得出现\u201c修订说明\u201d\u201c重写策略\u201d\u201c上一版草稿\u201d\u201c场景说明\u201d\u201c写法指导\u201d等元评论
- 不得出现\u201c这一场景要完成的剧情任务是\u201d\u201c以下是\u201d\u201c以上是\u201d等解释性前缀
- 不得出现 entry_state / exit_state / contract / scene_type 等英文结构化标签
- 所有策划信息（场景目的、情绪目标、contract 约束）仅供你理解意图，严禁直接输出到正文
- 输出中只允许出现：叙事散文、对话、动作描写、环境描写、内心活动
"""

# Quick heuristic: if any of these terms appear in the output, it likely
# contains non-fiction meta-commentary that slipped through the regex filter.
_META_LEAK_KEYWORDS = (
    "修订说明", "上一版草稿", "重写策略", "本次任务",
    "剧情任务是", "情绪任务是", "入场状态：", "离场状态：",
    "收束状态：", "开场状态：", "entry_state", "exit_state",
    "scene_summary", "contract_alignment", "tail_hook",
    "closing_hook", "story_task", "emotion_task",
)


def has_meta_leak(content_md: str) -> bool:
    """Return True if *content_md* still contains non-fiction meta-commentary."""
    return any(kw in content_md for kw in _META_LEAK_KEYWORDS)


async def validate_and_clean_novel_content(
    session: AsyncSession,
    settings: AppSettings,
    content_md: str,
    *,
    project_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> str:
    """LLM-based content validation gate.

    Called after ``sanitize_novel_markdown_content`` only when the heuristic
    ``has_meta_leak`` still detects non-fiction markers.  The critic role
    rewrites the offending paragraphs, keeping story content intact.
    """
    # Fast path: no leak detected — skip LLM call entirely.
    if not has_meta_leak(content_md):
        return content_md

    logger.warning(
        "Meta-commentary leak detected in output (len=%d), invoking LLM cleanup",
        len(content_md),
    )

    system_prompt = (
        "你是小说正文校验编辑。你的唯一任务是删除或改写混入正文的非小说内容。\n"
        "非小说内容包括但不限于：\n"
        "1. 策划术语：钩子、开场白、设想、尾钩、剧情任务、情绪任务、入场状态、离场状态、收束状态\n"
        "2. 元评论：修订说明、重写策略、上一版草稿、写法指导、场景说明\n"
        "3. 英文结构标签：entry_state、exit_state、scene_summary、contract 等\n"
        "4. 解释性前缀/后缀：\u201c以下是\u201d\u201c以上是\u201d\u201c这一场景要完成的剧情任务是\u201d\n\n"
        "处理规则：\n"
        "- 如果某个段落完全是元评论/策划说明，直接删除整段\n"
        "- 如果某个段落混合了小说正文和策划术语，只删除策划术语部分，保留小说正文\n"
        "- 不要改变小说正文的情节、对话、描写\n"
        "- 不要添加新内容\n"
        "- 输出清理后的完整正文，直接输出 Markdown，不要解释你做了什么\n"
    )
    user_prompt = f"以下是需要校验的小说正文：\n\n{content_md}"

    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=content_md,
            prompt_template="content_validation",
            prompt_version="1.0",
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
            metadata={"task": "meta_leak_cleanup"},
        ),
    )
    cleaned = sanitize_novel_markdown_content(completion.content)
    if not cleaned:
        logger.warning("LLM cleanup returned empty content, falling back to original")
        return content_md
    return cleaned


def _render_state(state: dict[str, Any]) -> str:
    if not state:
        return "暂无明确状态"
    return "；".join(f"{key}: {value}" for key, value in state.items())


def _render_purpose(purpose: dict[str, Any], key: str, fallback: str) -> str:
    value = purpose.get(key)
    return str(value) if value else fallback


def _normalize_fragment(text: str) -> str:
    return text.strip().rstrip("。！？!?")


def _render_story_bible_section(story_bible_context: dict[str, Any] | None) -> str:
    if not story_bible_context:
        return ""
    lines: list[str] = []
    if story_bible_context.get("logline"):
        lines.append(f"全书主线：{story_bible_context['logline']}")
    backbone = story_bible_context.get("world_backbone") or {}
    if backbone.get("mainline_drive"):
        lines.append(f"全书主旋律：{backbone['mainline_drive']}")
    if backbone.get("thematic_melody"):
        lines.append(f"主题旋律：{backbone['thematic_melody']}")
    if backbone.get("invariant_elements"):
        lines.append(
            f"不可轻改元素：{'、'.join(str(item) for item in backbone['invariant_elements'][:5])}"
        )
    if story_bible_context.get("themes"):
        lines.append(f"主题：{'、'.join(str(item) for item in story_bible_context['themes'])}")
    volume = story_bible_context.get("volume") or {}
    if volume.get("goal"):
        lines.append(f"本卷目标：{volume['goal']}")
    if volume.get("obstacle"):
        lines.append(f"本卷障碍：{volume['obstacle']}")
    frontier = story_bible_context.get("volume_frontier") or {}
    if frontier.get("frontier_summary"):
        lines.append(f"当前世界边界：{frontier['frontier_summary']}")
    if frontier.get("expansion_focus"):
        lines.append(f"当前扩张焦点：{frontier['expansion_focus']}")
    if frontier.get("active_locations"):
        lines.append(
            f"当前主要舞台：{'、'.join(str(item) for item in frontier['active_locations'][:4])}"
        )
    if frontier.get("active_factions"):
        lines.append(
            f"当前活跃势力：{'、'.join(str(item) for item in frontier['active_factions'][:4])}"
        )
    rules = story_bible_context.get("world_rules") or []
    if rules:
        rendered_rules = "；".join(
            f"{item['name']}({item['story_consequence'] or item['description']})"
            for item in rules[:3]
        )
        lines.append(f"关键世界规则：{rendered_rules}")
    reveal_status = story_bible_context.get("deferred_reveal_status") or {}
    hidden_reveal_count = reveal_status.get("hidden_count")
    if isinstance(hidden_reveal_count, int) and hidden_reveal_count > 0:
        lines.append(f"仍有 {hidden_reveal_count} 个延后揭示不得提前说破，只能通过异常与悬念间接保留。")
    next_gate = story_bible_context.get("next_expansion_gate") or {}
    if next_gate.get("condition_summary"):
        lines.append(f"下一层世界解锁条件：{next_gate['condition_summary']}")
    participants = story_bible_context.get("participants") or []
    if participants:
        rendered_participants = "；".join(
            (
                f"{item['name']}[{item.get('role') or 'character'}]"
                f" 目标:{item.get('goal') or '未定义'}"
                f" 弧线状态:{item.get('arc_state') or '未定义'}"
                f" 力量层级:{item.get('power_tier') or '未定义'}"
                f" 情绪:{item.get('emotional_state') or '未定义'}"
            )
            for item in participants[:4]
        )
        lines.append(f"参与角色当前状态：{rendered_participants}")
        voice_lines: list[str] = []
        for item in participants[:4]:
            vp = item.get("voice_profile") or {}
            parts: list[str] = []
            if vp.get("speech_register"):
                parts.append(f"语言层次:{vp['speech_register']}")
            if vp.get("verbal_tics"):
                parts.append(f"口头禅:{'/'.join(vp['verbal_tics'][:3])}")
            if vp.get("sentence_style"):
                parts.append(f"句式:{vp['sentence_style']}")
            if vp.get("emotional_expression"):
                parts.append(f"情绪表达:{vp['emotional_expression']}")
            if vp.get("mannerisms"):
                parts.append(f"习惯动作:{'/'.join(vp['mannerisms'][:2])}")
            if parts:
                voice_lines.append(f"{item['name']}——{'，'.join(parts)}")
        if voice_lines:
            lines.append("角色语言指纹（对话必须体现区分度）：\n" + "\n".join(voice_lines))
    relationships = story_bible_context.get("relationships") or []
    if relationships:
        rendered_relationships = "；".join(
            (
                f"{item.get('relationship_type') or '关系'}:"
                f"{item.get('tension_summary') or item.get('private_reality') or '存在潜在张力'}"
            )
            for item in relationships[:3]
        )
        lines.append(f"当前关系张力：{rendered_relationships}")
    return "\n".join(lines)


def _render_retrieval_section(chunks: list[dict[str, Any]] | None) -> str:
    if not chunks:
        return ""
    return "\n".join(
        f"- [{chunk.get('source_type')}] {chunk.get('chunk_text')}"
        for chunk in chunks[:4]
    )


def _render_recent_scene_section(recent_scene_summaries: list[dict[str, Any]] | None) -> str:
    if not recent_scene_summaries:
        return ""
    return "\n".join(
        (
            f"- 第{item.get('chapter_number')}章第{item.get('scene_number')}场"
            f" {item.get('scene_title') or ''}：{item.get('summary')}"
        )
        for item in recent_scene_summaries[:4]
        if item.get("summary")
    )


def _render_timeline_section(timeline_events: list[dict[str, Any]] | None) -> str:
    if not timeline_events:
        return ""
    return "\n".join(
        (
            f"- {item.get('story_time_label') or '未指定时间'} / {item.get('event_name')}："
            f"{'；'.join(item.get('consequences') or []) or item.get('summary') or '推进主线'}"
        )
        for item in timeline_events[:4]
    )


def _render_participant_fact_section(participant_facts: list[dict[str, Any]] | None) -> str:
    if not participant_facts:
        return ""
    return "\n".join(
        (
            f"- {item.get('subject_label')} / {item.get('predicate')}："
            f"{item.get('value')}"
        )
        for item in participant_facts[:6]
    )


def _render_arc_section(
    plot_arcs: list[dict[str, Any]] | None,
    arc_beats: list[dict[str, Any]] | None,
) -> str:
    sections: list[str] = []
    if plot_arcs:
        sections.append("激活叙事线：")
        sections.extend(
            f"- [{item.get('arc_type')}] {item.get('name')}：{item.get('promise')}"
            for item in plot_arcs[:4]
        )
    if arc_beats:
        sections.append("当前承担的叙事节拍：")
        sections.extend(
            (
                f"- {item.get('arc_code')} / {item.get('beat_kind')}：{item.get('summary')}"
                + (f" / 情绪:{item.get('emotional_shift')}" if item.get("emotional_shift") else "")
            )
            for item in arc_beats[:6]
        )
    return "\n".join(sections)


def _render_clue_section(
    unresolved_clues: list[dict[str, Any]] | None,
    planned_payoffs: list[dict[str, Any]] | None,
) -> str:
    sections: list[str] = []
    if unresolved_clues:
        sections.append("未回收伏笔：")
        sections.extend(
            f"- {item.get('clue_code')} / {item.get('label')}：{item.get('description')}"
            for item in unresolved_clues[:6]
        )
    if planned_payoffs:
        sections.append("近期应兑现节点：")
        sections.extend(
            f"- {item.get('payoff_code')} / {item.get('label')}：{item.get('description')}"
            for item in planned_payoffs[:4]
        )
    return "\n".join(sections)


def _render_emotion_track_section(emotion_tracks: list[dict[str, Any]] | None) -> str:
    if not emotion_tracks:
        return ""
    lines = ["当前关系/情绪线："]
    lines.extend(
        (
            f"- [{item.get('track_type')}] {item.get('title')}：{item.get('summary')}"
            f" / trust={item.get('trust_level')}"
            f" / attraction={item.get('attraction_level')}"
            f" / conflict={item.get('conflict_level')}"
            f" / stage={item.get('intimacy_stage')}"
        )
        for item in emotion_tracks[:4]
    )
    return "\n".join(lines)


def _render_antagonist_plan_section(antagonist_plans: list[dict[str, Any]] | None) -> str:
    if not antagonist_plans:
        return ""
    lines = ["当前反派推进："]
    lines.extend(
        (
            f"- [{item.get('threat_type')}] {item.get('title')}：{item.get('goal')}"
            f" / 当前动作:{item.get('current_move')}"
            f" / 下一步:{item.get('next_countermove')}"
        )
        for item in antagonist_plans[:4]
    )
    return "\n".join(lines)


def _render_contract_section(
    chapter_contract: dict[str, Any] | None,
    scene_contract: dict[str, Any] | None,
) -> str:
    sections: list[str] = []
    if chapter_contract:
        sections.append(
            f"章节 contract：{chapter_contract.get('contract_summary') or '本章需要承担明确叙事任务'}"
        )
        if chapter_contract.get("core_conflict"):
            sections.append(f"- 章节核心冲突：{chapter_contract['core_conflict']}")
        if chapter_contract.get("closing_hook"):
            sections.append(f"- 章节尾钩：{chapter_contract['closing_hook']}")
    if scene_contract:
        sections.append(
            f"场景 contract：{scene_contract.get('contract_summary') or '本场必须完成清晰推进'}"
        )
        if scene_contract.get("core_conflict"):
            sections.append(f"- 场景核心冲突：{scene_contract['core_conflict']}")
        if scene_contract.get("tail_hook"):
            sections.append(f"- 场景尾钩：{scene_contract['tail_hook']}")
        if scene_contract.get("thematic_task"):
            sections.append(f"- 主题任务：{scene_contract['thematic_task']}（通过行动和意象表达，不要直白说教）")
        if scene_contract.get("dramatic_irony_intent"):
            sections.append(f"- 戏剧反讽：{scene_contract['dramatic_irony_intent']}（读者知道但角色不知道）")
        if scene_contract.get("transition_type"):
            sections.append(f"- 过渡方式：{scene_contract['transition_type']}")
        if scene_contract.get("subplot_codes"):
            sections.append(f"- 推进副线：{'、'.join(scene_contract['subplot_codes'])}")
    return "\n".join(sections)


def _render_tree_section(tree_context_nodes: list[dict[str, Any]] | None) -> str:
    if not tree_context_nodes:
        return ""
    return "\n".join(
        (
            f"- {item.get('node_path')} [{item.get('node_type')}]："
            f"{item.get('summary') or item.get('title') or '无摘要'}"
        )
        for item in tree_context_nodes[:8]
    )


def _resolve_project_writing_profile(project: Any, style_guide: StyleGuideModel | None) -> Any:
    metadata = getattr(project, "metadata_json", {}) or {}
    raw_profile = metadata.get("writing_profile") if isinstance(metadata, dict) else None
    fallback_style = (
        {
            "style": {
                "pov_type": getattr(style_guide, "pov_type", "third-limited"),
                "tense": getattr(style_guide, "tense", "present"),
                "tone_keywords": list(getattr(style_guide, "tone_keywords", []) or []),
                "prose_style": getattr(style_guide, "prose_style", "commercial-web-serial"),
                "sentence_style": getattr(style_guide, "sentence_style", "mixed"),
                "info_density": getattr(style_guide, "info_density", "medium"),
                "dialogue_ratio": float(getattr(style_guide, "dialogue_ratio", 0.4)),
                "taboo_topics": list(getattr(style_guide, "taboo_topics", []) or []),
                "taboo_words": list(getattr(style_guide, "taboo_words", []) or []),
                "reference_works": list(getattr(style_guide, "reference_works", []) or []),
                "custom_rules": list(getattr(style_guide, "custom_rules", []) or []),
            }
        }
        if style_guide is not None
        else None
    )
    return resolve_writing_profile(
        raw_profile or fallback_style,
        genre=str(getattr(project, "genre", "general-fiction") or "general-fiction"),
        sub_genre=getattr(project, "sub_genre", None),
        audience=getattr(project, "audience", None),
    )


def _resolve_project_prompt_pack(project: Any, writing_profile: Any):
    return resolve_prompt_pack(
        getattr(writing_profile.market, "prompt_pack_key", None),
        genre=str(getattr(project, "genre", "general-fiction") or "general-fiction"),
        sub_genre=getattr(project, "sub_genre", None),
    )


def render_scene_draft_markdown(
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    style_guide: StyleGuideModel | None,
    story_bible_context: dict[str, Any] | None = None,
    retrieval_context: list[dict[str, Any]] | None = None,
    recent_scene_summaries: list[dict[str, Any]] | None = None,
    recent_timeline_events: list[dict[str, Any]] | None = None,
    participant_canon_facts: list[dict[str, Any]] | None = None,
    active_plot_arcs: list[dict[str, Any]] | None = None,
    active_arc_beats: list[dict[str, Any]] | None = None,
    unresolved_clues: list[dict[str, Any]] | None = None,
    planned_payoffs: list[dict[str, Any]] | None = None,
    chapter_contract: dict[str, Any] | None = None,
    scene_contract: dict[str, Any] | None = None,
    tree_context_nodes: list[dict[str, Any]] | None = None,
    active_emotion_tracks: list[dict[str, Any]] | None = None,
    active_antagonist_plans: list[dict[str, Any]] | None = None,
) -> str:
    title = scene.title or f"场景 {scene.scene_number}"
    participants = "、".join(scene.participants) if scene.participants else "相关角色"
    story_purpose = _render_purpose(scene.purpose, "story", "推进本章主线")
    emotion_purpose = _render_purpose(scene.purpose, "emotion", "拉高当前张力")
    raw_tone_keywords = (
        [str(keyword) for keyword in style_guide.tone_keywords[:3]]
        if style_guide and style_guide.tone_keywords
        else []
    )
    has_han_tone_keywords = any(re.search(r"[\u4e00-\u9fff]", keyword) for keyword in raw_tone_keywords)
    tone = "、".join(raw_tone_keywords) if has_han_tone_keywords else "克制、紧张"
    pov = style_guide.pov_type if style_guide is not None else "third-limited"
    chapter_goal = _normalize_fragment(chapter.chapter_goal)
    story_purpose = _normalize_fragment(story_purpose)
    emotion_purpose = _normalize_fragment(emotion_purpose)
    story_bible_section = _render_story_bible_section(story_bible_context)
    retrieval_section = _render_retrieval_section(retrieval_context)
    recent_scene_section = _render_recent_scene_section(recent_scene_summaries)
    recent_timeline_section = _render_timeline_section(recent_timeline_events)
    participant_fact_section = _render_participant_fact_section(participant_canon_facts)
    arc_section = _render_arc_section(active_plot_arcs, active_arc_beats)
    clue_section = _render_clue_section(unresolved_clues, planned_payoffs)
    emotion_track_section = _render_emotion_track_section(active_emotion_tracks)
    antagonist_plan_section = _render_antagonist_plan_section(active_antagonist_plans)
    contract_section = _render_contract_section(chapter_contract, scene_contract)
    tree_section = _render_tree_section(tree_context_nodes)
    writing_profile = _resolve_project_writing_profile(project, style_guide)
    writing_profile_section = render_writing_profile_prompt_block(writing_profile)

    paragraphs = [
        f"## 场景 {scene.scene_number}：{title}",
        (
            f"{scene.time_label or '这一刻'}，{participants}被推入《{project.title}》第"
            f"{chapter.chapter_number}章的核心冲突。叙事采用 {pov} 视角，整体语气保持 {tone}。"
        ),
        (
            f"这一场景要完成的剧情任务是“{story_purpose}”，情绪任务是“{emotion_purpose}”。"
            f"本章的总目标仍然是：{chapter_goal}。"
        ),
        (
            f"开场状态：{_render_state(scene.entry_state)}。角色在互动中不断试探、施压、暴露信息，"
            f"让冲突不只是说明，而是推动局势继续向前。"
        ),
        (
            f"场景推进过程中，{participants}围绕“{story_purpose}”发生正面碰撞。"
            f"对话、动作和观察需要服务于场景类型“{scene.scene_type}”，并把悬念留到结尾。"
        ),
        (
            f"收束状态：{_render_state(scene.exit_state)}。场景结束时要留下新的不确定性，"
            f"让下一场戏可以自然承接，同时保持章节钩子不断线。"
        ),
    ]
    if writing_profile_section:
        paragraphs.insert(2, f"商业写作画像：\n{writing_profile_section}")
    if story_bible_section:
        paragraphs.insert(
            3,
            "这一场景必须服从以下长篇约束："
            f"{story_bible_section if story_bible_section.startswith('全书') else chr(10) + story_bible_section}",
        )
    if recent_scene_section:
        paragraphs.insert(4 if story_bible_section else 3, f"近期剧情回顾：\n{recent_scene_section}")
    if recent_timeline_section:
        insert_at = 5 if story_bible_section and recent_scene_section else 4 if (story_bible_section or recent_scene_section) else 3
        paragraphs.insert(insert_at, f"已知时间线节点：\n{recent_timeline_section}")
    if arc_section:
        paragraphs.insert(len(paragraphs) - 2, f"当前叙事线与节拍：\n{arc_section}")
    if clue_section:
        paragraphs.insert(len(paragraphs) - 2, f"伏笔与兑现约束：\n{clue_section}")
    if emotion_track_section:
        paragraphs.insert(len(paragraphs) - 2, f"关系与情绪推进约束：\n{emotion_track_section}")
    if antagonist_plan_section:
        paragraphs.insert(len(paragraphs) - 2, f"反派推进约束：\n{antagonist_plan_section}")
    if contract_section:
        paragraphs.insert(len(paragraphs) - 2, f"合同式写作约束：\n{contract_section}")
    if tree_section:
        paragraphs.insert(len(paragraphs) - 2, f"叙事树上下文：\n{tree_section}")
    if participant_fact_section:
        insert_at = 6 if story_bible_section and recent_scene_section and recent_timeline_section else len(paragraphs) - 2
        paragraphs.insert(insert_at, f"参与角色可见事实：\n{participant_fact_section}")
    if retrieval_section:
        paragraphs.insert(len(paragraphs) - 2, f"相关检索上下文：\n{retrieval_section}")
    return "\n\n".join(paragraphs).strip()


_SCENE_TYPE_GUIDANCE: dict[str, str] = {
    "hook": "请输出完整场景，至少包含冲突推进、人物动作、有效对话、信息变化和结尾钩子。",
    "setup": "请输出完整场景，至少包含冲突推进、人物动作、有效对话、信息变化和结尾钩子。",
    "transition": "请输出完整场景，至少包含冲突推进、人物动作、有效对话、信息变化和结尾钩子。",
    "conflict": "请输出完整场景，至少包含冲突推进、人物动作、有效对话、信息变化和结尾钩子。",
    "reveal": "请输出完整场景，至少包含冲突推进、人物动作、有效对话、信息变化和结尾钩子。",
    "introspection": (
        "这是一个沉思/内省场景。不需要强制外部冲突，重点放在角色内心世界："
        "让角色回顾过去、质疑自我、整理情绪。用内心独白、环境映射和感官细节构建氛围。"
        "结尾留下角色心态转变或新决定的暗示。"
    ),
    "relationship_building": (
        "这是一个关系深化场景。重点放在两个或多个角色之间的互动质量："
        "通过共同经历、坦诚对话或无声默契加深关系。展示角色间的化学反应和信任变化。"
        "不需要高强度冲突，但需要情感层次推进。"
    ),
    "worldbuilding_discovery": (
        "这是一个世界观发现场景。通过角色的亲身体验让读者感受世界："
        "用五感细节、角色反应和具体互动展示世界规则。严禁长段解释，一切设定信息必须藏在行动里。"
    ),
    "aftermath": (
        "这是一个余波/善后场景。上一个高潮刚刚结束，角色需要消化后果："
        "处理伤亡、评估损失、重新规划。情绪从高强度向内收，展示事件对角色的真实影响。"
        "节奏放慢，但要留下下一步行动的种子。"
    ),
    "preparation": (
        "这是一个蓄势场景。角色在为接下来的大事件做准备："
        "收集资源、制定计划、联络盟友。通过准备过程侧面展示挑战的严峻。"
        "营造紧迫感和期待感，但不要提前揭示结果。"
    ),
    "comic_relief": (
        "这是一个调剂场景。在持续紧张的剧情后给读者喘息空间："
        "用轻松幽默的日常互动展示角色的另一面。可以有轻微的搞笑冲突或温馨时刻。"
        "但调剂中也要自然植入一两个对后续情节有用的信息或线索。"
    ),
    "montage": (
        "这是一个时间流逝/蒙太奇场景。通过场景片段展示一段时间内的变化："
        "用精炼的场景碎片串联成长、训练、旅途或时间推进。每个碎片要有鲜明的感官标记。"
    ),
}


def _scene_type_writing_guidance(scene_type: str) -> str:
    return _SCENE_TYPE_GUIDANCE.get(
        scene_type,
        "请输出完整场景，至少包含冲突推进、人物动作、有效对话、信息变化和结尾钩子。",
    )


def build_scene_draft_prompts(
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    style_guide: StyleGuideModel | None,
    story_bible_context: dict[str, Any] | None = None,
    retrieval_context: list[dict[str, Any]] | None = None,
    recent_scene_summaries: list[dict[str, Any]] | None = None,
    recent_timeline_events: list[dict[str, Any]] | None = None,
    participant_canon_facts: list[dict[str, Any]] | None = None,
    active_plot_arcs: list[dict[str, Any]] | None = None,
    active_arc_beats: list[dict[str, Any]] | None = None,
    unresolved_clues: list[dict[str, Any]] | None = None,
    planned_payoffs: list[dict[str, Any]] | None = None,
    chapter_contract: dict[str, Any] | None = None,
    scene_contract: dict[str, Any] | None = None,
    tree_context_nodes: list[dict[str, Any]] | None = None,
    active_emotion_tracks: list[dict[str, Any]] | None = None,
    active_antagonist_plans: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    writing_profile = _resolve_project_writing_profile(project, style_guide)
    prompt_pack = _resolve_project_prompt_pack(project, writing_profile)
    system_prompt = (
        "你是长篇中文小说写作系统里的场景写手。"
        "输出必须直接是 Markdown 正文，不要解释，不要列清单。"
        "必须写成可接续的小说场景，而不是策划说明。"
        "文本要像可以直接投到中文网文平台的成品章节，不要像策划案、提纲或润色说明。\n"
        + _NOVEL_OUTPUT_PROHIBITION
    )
    tone = (
        "、".join(str(keyword) for keyword in style_guide.tone_keywords[:3])
        if style_guide and style_guide.tone_keywords
        else "克制、紧张"
    )
    if not re.search(r"[\u4e00-\u9fff]", tone):
        tone = "克制、紧张"
    participants = "、".join(scene.participants) if scene.participants else "相关角色"
    story_bible_section = _render_story_bible_section(story_bible_context)
    retrieval_section = _render_retrieval_section(retrieval_context)
    recent_scene_section = _render_recent_scene_section(recent_scene_summaries)
    recent_timeline_section = _render_timeline_section(recent_timeline_events)
    participant_fact_section = _render_participant_fact_section(participant_canon_facts)
    arc_section = _render_arc_section(active_plot_arcs, active_arc_beats)
    clue_section = _render_clue_section(unresolved_clues, planned_payoffs)
    emotion_track_section = _render_emotion_track_section(active_emotion_tracks)
    antagonist_plan_section = _render_antagonist_plan_section(active_antagonist_plans)
    contract_section = _render_contract_section(chapter_contract, scene_contract)
    tree_section = _render_tree_section(tree_context_nodes)
    writing_profile_section = render_writing_profile_prompt_block(writing_profile)
    prompt_pack_section = render_prompt_pack_prompt_block(prompt_pack)
    serial_guardrails = render_serial_fiction_guardrails(writing_profile)
    prompt_pack_scene_writer = render_prompt_pack_fragment(prompt_pack, "scene_writer")
    _pp_line = f"Prompt Pack：\n{prompt_pack_section}\n" if prompt_pack_section else ""
    _pp_writer_line = f"Prompt Pack 额外写法：\n{prompt_pack_scene_writer}\n" if prompt_pack_scene_writer else ""
    user_prompt = (
        f"项目：《{project.title}》\n"
        f"章节：第{chapter.chapter_number}章 {chapter.title or ''}\n"
        f"章节目标：{chapter.chapter_goal}\n"
        f"场景：第{scene.scene_number}场 {scene.title or ''}\n"
        f"场景类型：{scene.scene_type}\n"
        f"时间标签：{scene.time_label or '未指定'}\n"
        f"参与者：{participants}\n"
        f"剧情目的：{scene.purpose.get('story', '推进本章主线')}\n"
        f"情绪目的：{scene.purpose.get('emotion', '拉高当前张力')}\n"
        f"入场状态：{scene.entry_state}\n"
        f"离场状态：{scene.exit_state}\n"
        f"目标字数：{scene.target_word_count}\n"
        f"视角：{style_guide.pov_type if style_guide else 'third-limited'}\n"
        f"语气关键词：{tone}\n"
        f"写作画像：\n{writing_profile_section}\n"
        f"{_pp_line}"
        f"故事圣经约束：\n{story_bible_section or '暂无额外故事圣经约束'}\n"
        f"近期剧情回顾：\n{recent_scene_section or '暂无近期剧情回顾'}\n"
        f"已知时间线节点：\n{recent_timeline_section or '暂无已知时间线节点'}\n"
        f"当前叙事线与节拍：\n{arc_section or '暂无显式叙事线约束'}\n"
        f"伏笔与兑现约束：\n{clue_section or '暂无显式伏笔/兑现约束'}\n"
        f"关系与情绪推进约束：\n{emotion_track_section or '暂无显式关系/情绪线约束'}\n"
        f"反派推进约束：\n{antagonist_plan_section or '暂无显式反派推进约束'}\n"
        f"chapter/scene contract：\n{contract_section or '暂无显式 contract 约束'}\n"
        f"叙事树上下文：\n{tree_section or '暂无叙事树上下文'}\n"
        f"参与角色当前可见事实：\n{participant_fact_section or '暂无额外角色事实'}\n"
        f"检索到的相关上下文：\n{retrieval_section or '暂无额外检索上下文'}\n"
        f"商业网文硬约束：\n{serial_guardrails}\n"
        f"{_pp_writer_line}"
        f"{_scene_type_writing_guidance(scene.scene_type)}"
        "不得泄露未来章节才会揭示的信息，不得与当前已知事实和时间线冲突。"
        "优先服从 deterministic path retrieval 与 narrative tree 提供的结构化约束。"
        "必须覆盖 scene contract 的核心冲突、情绪变化、信息释放和尾钩。"
        "背景说明必须压缩到最少，优先把设定藏进人物行动、交易、冲突后果和细节里。"
        "不要用空泛抒情、不要先解释世界观、不要写成提纲口吻。"
    )
    return system_prompt, user_prompt


def _packet_story_bible_context(packet: SceneWriterContextPacket | None) -> dict[str, Any] | None:
    return packet.story_bible if packet is not None else None


def _packet_recent_scene_summaries(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.recent_scene_summaries]


def _packet_recent_timeline_events(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.recent_timeline_events]


def _packet_participant_canon_facts(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.participant_canon_facts]


def _packet_active_plot_arcs(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.active_plot_arcs]


def _packet_active_arc_beats(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.active_arc_beats]


def _packet_unresolved_clues(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.unresolved_clues]


def _packet_planned_payoffs(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.planned_payoffs]


def _packet_chapter_contract(packet: SceneWriterContextPacket | None) -> dict[str, Any] | None:
    if packet is None or packet.chapter_contract is None:
        return None
    return packet.chapter_contract.model_dump(mode="json")


def _packet_scene_contract(packet: SceneWriterContextPacket | None) -> dict[str, Any] | None:
    if packet is None or packet.scene_contract is None:
        return None
    return packet.scene_contract.model_dump(mode="json")


def _packet_tree_context(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.tree_context_nodes]


def _packet_retrieval_context(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.retrieval_chunks]


def _packet_emotion_tracks(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.active_emotion_tracks]


def _packet_antagonist_plans(packet: SceneWriterContextPacket | None) -> list[dict[str, Any]]:
    if packet is None:
        return []
    return [item.model_dump(mode="json") for item in packet.active_antagonist_plans]


def render_chapter_draft_markdown(
    chapter: ChapterModel,
    scene_drafts: list[SceneDraftVersionModel],
) -> str:
    title = chapter.title or f"第{chapter.chapter_number}章"
    header = [f"# 第{chapter.chapter_number}章 {title}"]
    scene_sections = [
        sanitize_novel_markdown_content(scene_draft.content_md)
        for scene_draft in scene_drafts
    ]
    return "\n\n".join(header + scene_sections).strip()


async def generate_scene_draft(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
    *,
    settings: AppSettings | None = None,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> SceneDraftVersionModel:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")

    scene = await session.scalar(
        select(SceneCardModel).where(
            SceneCardModel.chapter_id == chapter.id,
            SceneCardModel.scene_number == scene_number,
        )
    )
    if scene is None:
        raise ValueError(
            f"Scene {scene_number} was not found in chapter {chapter_number} for '{project_slug}'."
        )

    style_guide = await session.get(StyleGuideModel, project.id)
    context_packet = None
    if settings is not None:
        context_packet = await build_scene_writer_context_from_models(
            session,
            settings,
            project,
            chapter,
            scene,
        )
    else:
        story_bible_context = await load_scene_story_bible_context(
            session,
            project=project,
            chapter=chapter,
            scene=scene,
        )
        context_packet = SceneWriterContextPacket(
            project_id=project.id,
            project_slug=project.slug,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            query_text=(
                f"{chapter.chapter_goal} "
                f"{scene.title or ''} "
                f"{scene.purpose.get('story', '')} "
                f"{' '.join(scene.participants)}"
            ).strip(),
            story_bible=story_bible_context,
            recent_scene_summaries=[],
            recent_timeline_events=[],
            participant_canon_facts=[],
            active_plot_arcs=[],
            active_arc_beats=[],
            unresolved_clues=[],
            planned_payoffs=[],
            active_emotion_tracks=[],
            active_antagonist_plans=[],
            chapter_contract=None,
            scene_contract=None,
            tree_context_nodes=[],
            retrieval_chunks=[],
        )
    fallback_content = render_scene_draft_markdown(
        project,
        chapter,
        scene,
        style_guide,
        _packet_story_bible_context(context_packet),
        _packet_retrieval_context(context_packet),
        _packet_recent_scene_summaries(context_packet),
        _packet_recent_timeline_events(context_packet),
        _packet_participant_canon_facts(context_packet),
        _packet_active_plot_arcs(context_packet),
        _packet_active_arc_beats(context_packet),
        _packet_unresolved_clues(context_packet),
        _packet_planned_payoffs(context_packet),
        _packet_chapter_contract(context_packet),
        _packet_scene_contract(context_packet),
        _packet_tree_context(context_packet),
        _packet_emotion_tracks(context_packet),
        _packet_antagonist_plans(context_packet),
    )
    model_name = "mock-writer"
    llm_run_id: UUID | None = None
    generation_mode = "template-fallback"
    content_md = fallback_content
    if settings is not None:
        system_prompt, user_prompt = build_scene_draft_prompts(
            project,
            chapter,
            scene,
            style_guide,
            _packet_story_bible_context(context_packet),
            _packet_retrieval_context(context_packet),
            _packet_recent_scene_summaries(context_packet),
            _packet_recent_timeline_events(context_packet),
            _packet_participant_canon_facts(context_packet),
            _packet_active_plot_arcs(context_packet),
            _packet_active_arc_beats(context_packet),
            _packet_unresolved_clues(context_packet),
            _packet_planned_payoffs(context_packet),
            _packet_chapter_contract(context_packet),
            _packet_scene_contract(context_packet),
            _packet_tree_context(context_packet),
            _packet_emotion_tracks(context_packet),
            _packet_antagonist_plans(context_packet),
        )
        # Inject voice drift correction prompts for scene participants
        proj_metadata = getattr(project, "metadata_json", None) or {}
        voice_corrections = proj_metadata.get("voice_corrections", {}) if isinstance(proj_metadata, dict) else {}
        if voice_corrections and scene.participants:
            correction_lines: list[str] = []
            for participant in scene.participants:
                correction = voice_corrections.get(participant)
                if correction:
                    correction_lines.append(f"【{participant}语音修正】{correction}")
            if correction_lines:
                system_prompt += "\n\n" + "\n".join(correction_lines)
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="writer",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=fallback_content,
                prompt_template="scene_writer",
                prompt_version="1.0",
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                metadata={
                    "project_slug": project.slug,
                    "chapter_number": chapter.chapter_number,
                    "scene_number": scene.scene_number,
                    "context_query": context_packet.query_text,
                },
            ),
        )
        content_md = sanitize_novel_markdown_content(completion.content) or fallback_content
        # LLM-based cleanup if regex sanitizer missed meta-commentary
        if has_meta_leak(content_md):
            content_md = await validate_and_clean_novel_content(
                session,
                settings,
                content_md,
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
            )
        model_name = completion.model_name
        llm_run_id = completion.llm_run_id
        generation_mode = completion.provider
    else:
        content_md = sanitize_novel_markdown_content(content_md)
    word_count = count_words(content_md)
    next_version = int(
        (
            await session.scalar(
                select(func.coalesce(func.max(SceneDraftVersionModel.version_no), 0)).where(
                    SceneDraftVersionModel.scene_card_id == scene.id
                )
            )
        )
        or 0
    ) + 1

    await session.execute(
        update(SceneDraftVersionModel)
        .where(
            SceneDraftVersionModel.scene_card_id == scene.id,
            SceneDraftVersionModel.is_current.is_(True),
        )
        .values(is_current=False)
    )

    draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=next_version,
        content_md=content_md,
        word_count=word_count,
        is_current=True,
        model_name=model_name,
        prompt_template="scene_writer",
        prompt_version="1.0",
        llm_run_id=llm_run_id,
        generation_params={
            "mode": generation_mode,
            "scene_type": scene.scene_type,
            "target_word_count": scene.target_word_count,
            "story_bible_context_used": bool(_packet_story_bible_context(context_packet)),
            "recent_scene_count": len(_packet_recent_scene_summaries(context_packet)),
            "recent_timeline_count": len(_packet_recent_timeline_events(context_packet)),
            "participant_fact_count": len(_packet_participant_canon_facts(context_packet)),
            "active_arc_count": len(_packet_active_plot_arcs(context_packet)),
            "active_beat_count": len(_packet_active_arc_beats(context_packet)),
            "unresolved_clue_count": len(_packet_unresolved_clues(context_packet)),
            "emotion_track_count": len(_packet_emotion_tracks(context_packet)),
            "antagonist_plan_count": len(_packet_antagonist_plans(context_packet)),
            "tree_context_count": len(_packet_tree_context(context_packet)),
            "retrieval_chunk_count": len(_packet_retrieval_context(context_packet)),
        },
    )
    session.add(draft)
    scene.status = SceneStatus.DRAFTED.value
    chapter.status = ChapterStatus.DRAFTING.value
    await session.flush()
    return draft


async def assemble_chapter_draft(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
) -> ChapterDraftVersionModel:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")

    scenes = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.chapter_id == chapter.id)
            .order_by(SceneCardModel.scene_number.asc())
        )
    )
    if not scenes:
        raise ValueError(f"Chapter {chapter_number} does not have any scene cards to assemble.")

    scene_drafts: list[SceneDraftVersionModel] = []
    missing_scenes: list[int] = []
    for scene in scenes:
        draft = await session.scalar(
            select(SceneDraftVersionModel).where(
                SceneDraftVersionModel.scene_card_id == scene.id,
                SceneDraftVersionModel.is_current.is_(True),
            )
        )
        if draft is None:
            missing_scenes.append(scene.scene_number)
            continue
        scene_drafts.append(draft)

    if missing_scenes:
        missing = ", ".join(str(scene_number) for scene_number in missing_scenes)
        raise ValueError(
            f"Chapter {chapter_number} cannot be assembled because current drafts are missing for scenes: {missing}."
        )

    content_md = render_chapter_draft_markdown(chapter, scene_drafts)
    word_count = count_words(content_md)
    next_version = int(
        (
            await session.scalar(
                select(func.coalesce(func.max(ChapterDraftVersionModel.version_no), 0)).where(
                    ChapterDraftVersionModel.chapter_id == chapter.id
                )
            )
        )
        or 0
    ) + 1

    await session.execute(
        update(ChapterDraftVersionModel)
        .where(
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .values(is_current=False)
    )

    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=next_version,
        content_md=content_md,
        word_count=word_count,
        assembled_from_scene_draft_ids=[str(scene_draft.id) for scene_draft in scene_drafts],
        is_current=True,
    )
    session.add(chapter_draft)
    chapter.current_word_count = word_count
    chapter.status = ChapterStatus.DRAFTING.value
    await session.flush()
    return chapter_draft
