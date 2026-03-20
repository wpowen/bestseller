from __future__ import annotations

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
from bestseller.services.projects import get_project_by_slug
from bestseller.services.story_bible import load_scene_story_bible_context
from bestseller.settings import AppSettings


def count_words(text: str) -> int:
    han_chars = re.findall(r"[\u4e00-\u9fff]", text)
    latin_words = re.findall(r"[A-Za-z0-9_]+", text)
    return len(han_chars) + len(latin_words)


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
    if story_bible_context.get("themes"):
        lines.append(f"主题：{'、'.join(str(item) for item in story_bible_context['themes'])}")
    volume = story_bible_context.get("volume") or {}
    if volume.get("goal"):
        lines.append(f"本卷目标：{volume['goal']}")
    if volume.get("obstacle"):
        lines.append(f"本卷障碍：{volume['obstacle']}")
    rules = story_bible_context.get("world_rules") or []
    if rules:
        rendered_rules = "；".join(
            f"{item['name']}({item['story_consequence'] or item['description']})"
            for item in rules[:3]
        )
        lines.append(f"关键世界规则：{rendered_rules}")
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
    contract_section = _render_contract_section(chapter_contract, scene_contract)
    tree_section = _render_tree_section(tree_context_nodes)

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
) -> tuple[str, str]:
    system_prompt = (
        "你是长篇中文小说写作系统里的场景写手。"
        "输出必须直接是 Markdown 正文，不要解释，不要列清单。"
        "必须写成可接续的小说场景，而不是策划说明。"
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
    contract_section = _render_contract_section(chapter_contract, scene_contract)
    tree_section = _render_tree_section(tree_context_nodes)
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
        f"故事圣经约束：\n{story_bible_section or '暂无额外故事圣经约束'}\n"
        f"近期剧情回顾：\n{recent_scene_section or '暂无近期剧情回顾'}\n"
        f"已知时间线节点：\n{recent_timeline_section or '暂无已知时间线节点'}\n"
        f"当前叙事线与节拍：\n{arc_section or '暂无显式叙事线约束'}\n"
        f"伏笔与兑现约束：\n{clue_section or '暂无显式伏笔/兑现约束'}\n"
        f"chapter/scene contract：\n{contract_section or '暂无显式 contract 约束'}\n"
        f"叙事树上下文：\n{tree_section or '暂无叙事树上下文'}\n"
        f"参与角色当前可见事实：\n{participant_fact_section or '暂无额外角色事实'}\n"
        f"检索到的相关上下文：\n{retrieval_section or '暂无额外检索上下文'}\n"
        "请输出完整场景，至少包含冲突推进、人物动作、有效对话和结尾钩子。"
        "不得泄露未来章节才会揭示的信息，不得与当前已知事实和时间线冲突。"
        "优先服从 deterministic path retrieval 与 narrative tree 提供的结构化约束。"
        "必须覆盖 scene contract 的核心冲突、情绪变化、信息释放和尾钩。"
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


def render_chapter_draft_markdown(
    chapter: ChapterModel,
    scene_drafts: list[SceneDraftVersionModel],
) -> str:
    title = chapter.title or f"第{chapter.chapter_number}章"
    header = [
        f"# 第{chapter.chapter_number}章 {title}",
        f"> 本章目标：{chapter.chapter_goal}",
    ]
    scene_sections = [scene_draft.content_md.strip() for scene_draft in scene_drafts]
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
        )
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
        content_md = completion.content.strip() or fallback_content
        model_name = completion.model_name
        llm_run_id = completion.llm_run_id
        generation_mode = completion.provider
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
