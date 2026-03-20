from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ChapterStatus, SceneStatus
from bestseller.domain.review import (
    ChapterReviewFinding,
    ChapterReviewResult,
    ChapterReviewScores,
    SceneReviewFinding,
    SceneReviewResult,
    SceneReviewScores,
)
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    SceneCardModel,
    SceneDraftVersionModel,
    StyleGuideModel,
)
from bestseller.services.context import build_chapter_writer_context, build_scene_writer_context
from bestseller.services.drafts import (
    count_words,
    _normalize_fragment,
    sanitize_novel_markdown_content,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.prompt_packs import (
    render_prompt_pack_fragment,
    render_prompt_pack_prompt_block,
    resolve_prompt_pack,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.services.rewrite_impacts import analyze_rewrite_impacts_for_scene_task
from bestseller.services.writing_profile import (
    render_serial_fiction_guardrails,
    render_writing_profile_prompt_block,
    resolve_writing_profile,
)
from bestseller.settings import AppSettings


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 2)


def _severity_from_score(score: float) -> str:
    if score < 0.45:
        return "high"
    if score < 0.7:
        return "medium"
    return "low"


def _term_candidates(*values: str | None) -> list[str]:
    terms: list[str] = []
    for value in values:
        if not value:
            continue
        normalized = _normalize_fragment(value)
        if normalized and normalized not in terms:
            terms.append(normalized)
        for token in re.findall(r"[0-9A-Za-z\u4e00-\u9fff]{2,}", value):
            if token not in terms:
                terms.append(token)
    return terms


def _contract_field_score(content: str, value: str | None) -> float | None:
    if not value:
        return None
    normalized = _normalize_fragment(value)
    if normalized and normalized in content:
        return 1.0
    terms = _term_candidates(value)[:6]
    if not terms:
        return 0.0
    hit_count = sum(1 for term in terms if term in content)
    return _clamp_score(hit_count / len(terms))


def _evaluate_contract_alignment(
    content: str,
    *,
    expectations: list[tuple[str, str | None]],
) -> tuple[float, dict[str, object]]:
    scored_items: list[tuple[str, float]] = []
    missing_labels: list[str] = []
    for label, value in expectations:
        field_score = _contract_field_score(content, value)
        if field_score is None:
            continue
        scored_items.append((label, field_score))
        if field_score < 0.5:
            missing_labels.append(label)
    if not scored_items:
        return 1.0, {
            "contract_expectation_count": 0,
            "contract_matched_count": 0,
            "contract_missing_labels": [],
            "contract_alignment_breakdown": {},
        }
    breakdown = {label: score for label, score in scored_items}
    matched_count = sum(1 for _, score in scored_items if score >= 0.5)
    return _clamp_score(sum(score for _, score in scored_items) / len(scored_items)), {
        "contract_expectation_count": len(scored_items),
        "contract_matched_count": matched_count,
        "contract_missing_labels": missing_labels,
        "contract_alignment_breakdown": breakdown,
    }


def _scene_contract_expectations(
    *,
    chapter_contract: Any | None = None,
    scene_contract: Any | None = None,
) -> list[tuple[str, str | None]]:
    if scene_contract is not None:
        return [
            ("scene_summary", getattr(scene_contract, "contract_summary", None)),
            ("core_conflict", getattr(scene_contract, "core_conflict", None)),
            ("emotional_shift", getattr(scene_contract, "emotional_shift", None)),
            ("information_release", getattr(scene_contract, "information_release", None)),
            ("tail_hook", getattr(scene_contract, "tail_hook", None)),
        ]
    if chapter_contract is not None:
        return [
            ("chapter_summary", getattr(chapter_contract, "contract_summary", None)),
            ("core_conflict", getattr(chapter_contract, "core_conflict", None)),
            ("emotional_shift", getattr(chapter_contract, "emotional_shift", None)),
            ("information_release", getattr(chapter_contract, "information_release", None)),
            ("closing_hook", getattr(chapter_contract, "closing_hook", None)),
        ]
    return []


def _chapter_contract_expectations(
    *,
    chapter_contract: Any | None = None,
) -> list[tuple[str, str | None]]:
    if chapter_contract is None:
        return []
    return [
        ("chapter_summary", getattr(chapter_contract, "contract_summary", None)),
        ("core_conflict", getattr(chapter_contract, "core_conflict", None)),
        ("emotional_shift", getattr(chapter_contract, "emotional_shift", None)),
        ("information_release", getattr(chapter_contract, "information_release", None)),
        ("closing_hook", getattr(chapter_contract, "closing_hook", None)),
    ]


def _max_severity(findings: list[SceneReviewFinding]) -> str:
    if any(finding.severity == "high" for finding in findings):
        return "high"
    if any(finding.severity == "medium" for finding in findings):
        return "medium"
    return "low"


def render_scene_review_summary(review_result: SceneReviewResult) -> str:
    summary_lines = [
        f"结论：{review_result.verdict}",
        f"总分：{review_result.scores.overall}",
        f"最高严重级别：{review_result.severity_max}",
    ]
    if review_result.findings:
        summary_lines.append("问题列表：")
        summary_lines.extend(
            f"- [{finding.category}/{finding.severity}] {finding.message}"
            for finding in review_result.findings
        )
    if review_result.rewrite_instructions:
        summary_lines.append(f"重写要求：{review_result.rewrite_instructions}")
    return "\n".join(summary_lines)


def _resolve_project_writing_profile(project: Any, style_guide: StyleGuideModel | None = None):
    metadata = getattr(project, "metadata_json", {}) or {}
    raw_profile = metadata.get("writing_profile") if isinstance(metadata, dict) else None
    fallback_style = None
    if style_guide is not None:
        fallback_style = {
            "style": {
                "pov_type": getattr(style_guide, "pov_type", "third-limited"),
                "tense": getattr(style_guide, "tense", "present"),
                "tone_keywords": list(getattr(style_guide, "tone_keywords", []) or []),
                "prose_style": getattr(style_guide, "prose_style", "commercial-web-serial"),
                "sentence_style": getattr(style_guide, "sentence_style", "mixed"),
                "info_density": getattr(style_guide, "info_density", "medium"),
                "dialogue_ratio": float(getattr(style_guide, "dialogue_ratio", 0.4)),
                "reference_works": list(getattr(style_guide, "reference_works", []) or []),
                "custom_rules": list(getattr(style_guide, "custom_rules", []) or []),
            }
        }
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


def build_scene_review_prompts(
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    draft: SceneDraftVersionModel,
    review_result: SceneReviewResult,
) -> tuple[str, str]:
    writing_profile = _resolve_project_writing_profile(project)
    prompt_pack = _resolve_project_prompt_pack(project, writing_profile)
    system_prompt = (
        "你是长篇小说审校系统里的场景评论者。"
        "请输出简洁、专业、可执行的审校意见，不要复述需求。"
    )
    user_prompt = (
        f"项目：《{project.title}》\n"
        f"章节：第{chapter.chapter_number}章\n"
        f"场景：第{scene.scene_number}场 {scene.title or ''}\n"
        f"场景目标：{scene.purpose.get('story', '推进本章主线')}\n"
        f"情绪目标：{scene.purpose.get('emotion', '拉高当前张力')}\n"
        f"写作画像：\n{render_writing_profile_prompt_block(writing_profile)}\n"
        f"{'Prompt Pack：\n' + render_prompt_pack_prompt_block(prompt_pack) + '\n' if prompt_pack else ''}"
        f"{render_prompt_pack_fragment(prompt_pack, 'scene_review') + '\n' if prompt_pack else ''}"
        f"当前评分：{review_result.scores.model_dump(mode='json')}\n"
        f"当前发现：{[finding.model_dump(mode='json') for finding in review_result.findings]}\n"
        f"当前草稿：\n{draft.content_md}\n"
        "请用中文输出一段简洁的审校结论，并给出是否需要重写的理由。"
        "结论要明确指出这段文字是否兑现了平台目标、读者承诺、主角卖点和章节尾钩。"
    )
    return system_prompt, user_prompt


def build_scene_rewrite_prompts(
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    current_draft: SceneDraftVersionModel,
    rewrite_task: RewriteTaskModel,
    style_guide: StyleGuideModel | None,
) -> tuple[str, str]:
    writing_profile = _resolve_project_writing_profile(project, style_guide)
    prompt_pack = _resolve_project_prompt_pack(project, writing_profile)
    system_prompt = (
        "你是长篇中文小说写作系统里的重写编辑。"
        "输出必须是 Markdown 正文，不要解释，不要道歉，不要列修改清单。"
    )
    tone = (
        "、".join(str(keyword) for keyword in style_guide.tone_keywords[:3])
        if style_guide and style_guide.tone_keywords
        else "克制、紧张"
    )
    if not re.search(r"[\u4e00-\u9fff]", tone):
        tone = "克制、紧张"
    user_prompt = (
        f"项目：《{project.title}》\n"
        f"章节：第{chapter.chapter_number}章\n"
        f"场景：第{scene.scene_number}场 {scene.title or ''}\n"
        f"重写任务：{rewrite_task.instructions}\n"
        f"重写策略：{rewrite_task.rewrite_strategy}\n"
        f"章节目标：{chapter.chapter_goal}\n"
        f"剧情目标：{scene.purpose.get('story', '推进本章主线')}\n"
        f"情绪目标：{scene.purpose.get('emotion', '拉高当前张力')}\n"
        f"语气关键词：{tone}\n"
        f"写作画像：\n{render_writing_profile_prompt_block(writing_profile)}\n"
        f"{'Prompt Pack：\n' + render_prompt_pack_prompt_block(prompt_pack) + '\n' if prompt_pack else ''}"
        f"商业网文硬约束：\n{render_serial_fiction_guardrails(writing_profile)}\n"
        f"{render_prompt_pack_fragment(prompt_pack, 'scene_rewrite') + '\n' if prompt_pack else ''}"
        f"当前草稿：\n{current_draft.content_md}\n"
        "请重写当前场景，补强冲突、人物对话、情绪层次和结尾钩子。"
        "要让文本更像平台成品网文，而不是策划草稿或解释说明。"
    )
    return system_prompt, user_prompt


def _render_chapter_context_section(packet) -> str:
    if packet is None:
        return "暂无章节上下文。"
    lines: list[str] = []
    if getattr(packet, "active_plot_arcs", None):
        lines.append("激活叙事线：")
        lines.extend(
            f"- [{item.arc_type}] {item.name}：{item.promise}"
            for item in packet.active_plot_arcs[:4]
        )
    if getattr(packet, "active_arc_beats", None):
        lines.append("本章叙事节拍：")
        lines.extend(
            f"- {item.arc_code} / {item.beat_kind}：{item.summary}"
            for item in packet.active_arc_beats[:6]
        )
    if getattr(packet, "unresolved_clues", None):
        lines.append("未回收伏笔：")
        lines.extend(
            f"- {item.clue_code}：{item.label}"
            for item in packet.unresolved_clues[:6]
        )
    if getattr(packet, "planned_payoffs", None):
        lines.append("近期应兑现节点：")
        lines.extend(
            f"- {item.payoff_code}：{item.label}"
            for item in packet.planned_payoffs[:4]
        )
    if getattr(packet, "active_emotion_tracks", None):
        lines.append("关系与情绪线：")
        lines.extend(
            (
                f"- [{item.track_type}] {item.title}：{item.summary}"
                f" / trust={item.trust_level} / conflict={item.conflict_level}"
            )
            for item in packet.active_emotion_tracks[:4]
        )
    if getattr(packet, "active_antagonist_plans", None):
        lines.append("反派推进：")
        lines.extend(
            (
                f"- [{item.threat_type}] {item.title}：{item.goal}"
                f" / 当前动作:{item.current_move}"
                f" / 下一步:{item.next_countermove}"
            )
            for item in packet.active_antagonist_plans[:4]
        )
    if getattr(packet, "chapter_contract", None):
        lines.append(f"章节 contract：{packet.chapter_contract.contract_summary}")
    if getattr(packet, "tree_context_nodes", None):
        lines.append("叙事树上下文：")
        lines.extend(
            f"- {item.node_path} [{item.node_type}]：{item.summary or item.title}"
            for item in packet.tree_context_nodes[:6]
        )
    if packet.previous_scene_summaries:
        lines.append("近期剧情：")
        lines.extend(
            f"- 第{item.chapter_number}章第{item.scene_number}场 {item.scene_title or ''}：{item.summary}"
            for item in packet.previous_scene_summaries[:4]
        )
    if packet.chapter_scenes:
        lines.append("本章场景计划：")
        lines.extend(
            (
                f"- 第{item.scene_number}场 {item.title or ''} / {item.scene_type} / "
                f"剧情:{item.story_purpose or '未定义'} / 情绪:{item.emotion_purpose or '未定义'}"
            )
            for item in packet.chapter_scenes
        )
    if packet.recent_timeline_events:
        lines.append("时间线节点：")
        lines.extend(
            f"- {item.story_time_label} {item.event_name}：{'；'.join(item.consequences) or item.summary or '推进主线'}"
            for item in packet.recent_timeline_events[:4]
        )
    if packet.retrieval_chunks:
        lines.append("检索上下文：")
        lines.extend(
            f"- [{item.source_type}] {item.chunk_text}"
            for item in packet.retrieval_chunks[:4]
        )
    return "\n".join(lines)


def _count_scene_headings(content: str) -> int:
    return len(re.findall(r"^##\s*场景\s+\d+", content, flags=re.MULTILINE))


def render_chapter_review_summary(review_result: ChapterReviewResult) -> str:
    summary_lines = [
        f"结论：{review_result.verdict}",
        f"总分：{review_result.scores.overall}",
        f"最高严重级别：{review_result.severity_max}",
    ]
    if review_result.findings:
        summary_lines.append("问题列表：")
        summary_lines.extend(
            f"- [{finding.category}/{finding.severity}] {finding.message}"
            for finding in review_result.findings
        )
    if review_result.rewrite_instructions:
        summary_lines.append(f"重写要求：{review_result.rewrite_instructions}")
    return "\n".join(summary_lines)


def build_chapter_review_prompts(
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    chapter_context,
    review_result: ChapterReviewResult,
) -> tuple[str, str]:
    writing_profile = _resolve_project_writing_profile(project)
    prompt_pack = _resolve_project_prompt_pack(project, writing_profile)
    system_prompt = (
        "你是长篇小说审校系统里的章节评论者。"
        "请输出简洁、专业、可执行的章节审校意见，不要复述需求。"
    )
    user_prompt = (
        f"项目：《{project.title}》\n"
        f"章节：第{chapter.chapter_number}章 {chapter.title or ''}\n"
        f"章节目标：{chapter.chapter_goal}\n"
        f"写作画像：\n{render_writing_profile_prompt_block(writing_profile)}\n"
        f"{'Prompt Pack：\n' + render_prompt_pack_prompt_block(prompt_pack) + '\n' if prompt_pack else ''}"
        f"{render_prompt_pack_fragment(prompt_pack, 'chapter_review') + '\n' if prompt_pack else ''}"
        f"上下文：\n{_render_chapter_context_section(chapter_context)}\n"
        f"当前评分：{review_result.scores.model_dump(mode='json')}\n"
        f"当前发现：{[finding.model_dump(mode='json') for finding in review_result.findings]}\n"
        f"当前草稿：\n{draft.content_md}\n"
        "请用中文输出一段简洁的章节审校结论，并给出是否需要重写的理由。"
        "需要判断本章是否真的有追读欲、是否在平台读者预期下足够有吸引力。"
    )
    return system_prompt, user_prompt


def build_chapter_rewrite_prompts(
    project: ProjectModel,
    chapter: ChapterModel,
    current_draft: ChapterDraftVersionModel,
    rewrite_task: RewriteTaskModel,
    chapter_context,
) -> tuple[str, str]:
    writing_profile = _resolve_project_writing_profile(project)
    prompt_pack = _resolve_project_prompt_pack(project, writing_profile)
    system_prompt = (
        "你是长篇中文小说写作系统里的章节重写编辑。"
        "输出必须是 Markdown 正文，不要解释，不要列修改清单。"
    )
    user_prompt = (
        f"项目：《{project.title}》\n"
        f"章节：第{chapter.chapter_number}章 {chapter.title or ''}\n"
        f"章节目标：{chapter.chapter_goal}\n"
        f"重写任务：{rewrite_task.instructions}\n"
        f"重写策略：{rewrite_task.rewrite_strategy}\n"
        f"写作画像：\n{render_writing_profile_prompt_block(writing_profile)}\n"
        f"{'Prompt Pack：\n' + render_prompt_pack_prompt_block(prompt_pack) + '\n' if prompt_pack else ''}"
        f"商业网文硬约束：\n{render_serial_fiction_guardrails(writing_profile)}\n"
        f"{render_prompt_pack_fragment(prompt_pack, 'chapter_rewrite') + '\n' if prompt_pack else ''}"
        f"章节上下文：\n{_render_chapter_context_section(chapter_context)}\n"
        f"当前草稿：\n{current_draft.content_md}\n"
        "请在保留本章核心事件顺序的前提下，重写本章，使场景衔接更顺、章节推进更完整、收尾钩子更明确。"
        "优先强化读者追更欲、爽点兑现、人设辨识和节奏推进。"
    )
    return system_prompt, user_prompt


def evaluate_scene_draft(
    *,
    scene: SceneCardModel,
    chapter: ChapterModel,
    draft: SceneDraftVersionModel,
    settings: AppSettings,
    chapter_contract: Any | None = None,
    scene_contract: Any | None = None,
) -> SceneReviewResult:
    content = draft.content_md
    target_ratio = draft.word_count / max(scene.target_word_count, 1)
    goal = _clamp_score(target_ratio)

    participants_present = sum(
        1 for participant in scene.participants if participant and participant in content
    )
    conflict = _clamp_score(
        0.25
        + (0.25 if scene.scene_type in content else 0.0)
        + min(0.25, participants_present * 0.12)
        + (0.2 if draft.word_count >= 240 else 0.0)
        + (0.05 if "冲突" in content or "碰撞" in content else 0.0)
    )

    emotion_phrase = str(scene.purpose.get("emotion", "")).strip()
    emotion = _clamp_score(
        0.35
        + (0.3 if emotion_phrase and emotion_phrase in content else 0.0)
        + (0.15 if "情绪" in content or "不安" in content or "压迫" in content else 0.0)
        + (0.1 if draft.word_count >= 260 else 0.0)
    )

    dialogue_markers = content.count("“") + content.count("”")
    dialogue = _clamp_score(
        0.15
        + min(0.45, dialogue_markers * 0.08)
        + (0.15 if "说" in content or "问" in content else 0.0)
        + (0.15 if "对话" in content else 0.0)
    )

    style_penalty = 0.15 if "。。" in content or ".." in content else 0.0
    style = _clamp_score(
        0.65
        + (0.1 if "克制、紧张" in content or "整体语气保持" in content else 0.0)
        - style_penalty
    )

    hook = _clamp_score(
        0.35
        + (0.25 if "悬念" in content else 0.0)
        + (0.25 if "不确定性" in content else 0.0)
        + (0.1 if "结尾" in content else 0.0)
    )

    contract_alignment, contract_evidence = _evaluate_contract_alignment(
        content,
        expectations=_scene_contract_expectations(
            chapter_contract=chapter_contract,
            scene_contract=scene_contract,
        ),
    )
    score_parts = [goal, conflict, emotion, dialogue, style, hook]
    if int(contract_evidence["contract_expectation_count"]) > 0:
        score_parts.append(contract_alignment)
    overall = _clamp_score(sum(score_parts) / len(score_parts))
    threshold = settings.quality.thresholds.scene_min_score

    findings: list[SceneReviewFinding] = []
    if goal < threshold:
        findings.append(
            SceneReviewFinding(
                category="goal",
                severity=_severity_from_score(goal),
                message=(
                    f"当前场景字数为 {draft.word_count}，明显低于目标字数 {scene.target_word_count}，"
                    "推进任务展开不够充分。"
                ),
            )
        )
    if conflict < threshold:
        findings.append(
            SceneReviewFinding(
                category="conflict",
                severity=_severity_from_score(conflict),
                message="冲突呈现仍偏概述，缺少更具体的对抗动作和压力升级。",
            )
        )
    if emotion < threshold:
        findings.append(
            SceneReviewFinding(
                category="emotion",
                severity=_severity_from_score(emotion),
                message="情绪变化被直接说明较多，缺少体感、动作和反应层面的表达。",
            )
        )
    if dialogue < threshold:
        findings.append(
            SceneReviewFinding(
                category="dialogue",
                severity=_severity_from_score(dialogue),
                message="缺少有效对话支撑，人物之间的对抗还没有被真正演出来。",
            )
        )
    if int(contract_evidence["contract_expectation_count"]) > 0 and contract_alignment < threshold:
        missing_labels = list(contract_evidence["contract_missing_labels"])
        findings.append(
            SceneReviewFinding(
                category="contract_alignment",
                severity=_severity_from_score(contract_alignment),
                message=(
                    "当前场景没有充分兑现 scene contract。"
                    + (f" 缺失要点：{', '.join(missing_labels)}。" if missing_labels else "")
                ),
            )
        )

    verdict = "pass" if overall >= threshold and not findings else "rewrite"
    rewrite_instructions = None
    if verdict == "rewrite":
        contract_hint = ""
        if int(contract_evidence["contract_expectation_count"]) > 0:
            missing_labels = list(contract_evidence["contract_missing_labels"])
            contract_hint = (
                " 并对齐 scene contract，补齐核心冲突、情绪变化、信息释放和尾钩。"
                if not missing_labels
                else f" 并对齐 scene contract，补齐这些缺口：{', '.join(missing_labels)}。"
            )
        rewrite_instructions = (
            f"请重写第{chapter.chapter_number}章第{scene.scene_number}场，优先补足目标推进、"
            f"冲突升级、人物对话和情绪层次，确保结尾留下明确钩子。{contract_hint}"
        )

    return SceneReviewResult(
        verdict=verdict,
        severity_max=_max_severity(findings),
        scores=SceneReviewScores(
            overall=overall,
            goal=goal,
            conflict=conflict,
            emotion=emotion,
            dialogue=dialogue,
            style=style,
            hook=hook,
            contract_alignment=contract_alignment,
        ),
        findings=findings,
        evidence_summary={
            "word_count": draft.word_count,
            "target_word_count": scene.target_word_count,
            "participants_hit": participants_present,
            "dialogue_markers": dialogue_markers,
            "chapter_goal": chapter.chapter_goal,
            **contract_evidence,
        },
        rewrite_instructions=rewrite_instructions,
    )


def evaluate_chapter_draft(
    *,
    chapter: ChapterModel,
    scenes: list[SceneCardModel],
    draft: ChapterDraftVersionModel,
    settings: AppSettings,
    chapter_contract: Any | None = None,
) -> ChapterReviewResult:
    content = draft.content_md
    target_ratio = draft.word_count / max(chapter.target_word_count, 1)
    goal = _clamp_score(target_ratio)

    scene_heading_count = _count_scene_headings(content)
    expected_scene_count = len(scenes)
    coverage = _clamp_score(scene_heading_count / max(expected_scene_count, 1))

    scene_titles_hit = sum(1 for scene in scenes if scene.title and scene.title in content)
    coherence = _clamp_score(
        0.25
        + min(0.35, scene_titles_hit * 0.15)
        + (0.15 if "## 场景 1" in content else 0.0)
        + (0.15 if content.count("\n\n") >= expected_scene_count * 2 else 0.0)
        + (0.1 if chapter.chapter_goal[:8] in content else 0.0)
    )

    continuity = _clamp_score(
        0.2
        + (0.25 if "上一" in content or "此前" in content else 0.0)
        + (0.2 if "因此" in content or "与此同时" in content or "随后" in content else 0.0)
        + (0.15 if expected_scene_count <= 1 or scene_heading_count == expected_scene_count else 0.0)
        + (0.1 if draft.word_count >= max(900, chapter.target_word_count * 0.5) else 0.0)
    )

    style_penalty = 0.15 if "。。" in content or ".." in content else 0.0
    style = _clamp_score(
        0.65
        + (0.1 if content.startswith("# 第") else 0.0)
        + (0.1 if "> 本章目标：" in content else 0.0)
        - style_penalty
    )

    hook = _clamp_score(
        0.3
        + (0.2 if "悬念" in content else 0.0)
        + (0.25 if "新的不确定性" in content or "真相" in content else 0.0)
        + (0.1 if "下一" in content or "下一个" in content else 0.0)
    )

    contract_alignment, contract_evidence = _evaluate_contract_alignment(
        content,
        expectations=_chapter_contract_expectations(chapter_contract=chapter_contract),
    )
    score_parts = [goal, coverage, coherence, continuity, style, hook]
    if int(contract_evidence["contract_expectation_count"]) > 0:
        score_parts.append(contract_alignment)
    overall = _clamp_score(sum(score_parts) / len(score_parts))
    threshold = settings.quality.thresholds.chapter_coherence_min_score

    findings: list[ChapterReviewFinding] = []
    if goal < threshold:
        findings.append(
            ChapterReviewFinding(
                category="goal",
                severity=_severity_from_score(goal),
                message=(
                    f"当前章节字数为 {draft.word_count}，低于目标字数 {chapter.target_word_count}，"
                    "章节推进还不够完整。"
                ),
            )
        )
    if coverage < threshold:
        findings.append(
            ChapterReviewFinding(
                category="coverage",
                severity=_severity_from_score(coverage),
                message="章节没有充分覆盖当前场景计划，存在场景承接或收束不足的问题。",
            )
        )
    if coherence < threshold:
        findings.append(
            ChapterReviewFinding(
                category="coherence",
                severity=_severity_from_score(coherence),
                message="章节内部场景衔接仍偏松散，缺少更明确的推进逻辑和章节级主线牵引。",
            )
        )
    if continuity < threshold:
        findings.append(
            ChapterReviewFinding(
                category="continuity",
                severity=_severity_from_score(continuity),
                message="章节前后承接不足，缺少对上一阶段局势的衔接和对下一阶段威胁的延展。",
            )
        )
    if int(contract_evidence["contract_expectation_count"]) > 0 and contract_alignment < threshold:
        missing_labels = list(contract_evidence["contract_missing_labels"])
        findings.append(
            ChapterReviewFinding(
                category="contract_alignment",
                severity=_severity_from_score(contract_alignment),
                message=(
                    "当前章节没有充分兑现 chapter contract。"
                    + (f" 缺失要点：{', '.join(missing_labels)}。" if missing_labels else "")
                ),
            )
        )

    blocking_findings = [finding for finding in findings if finding.severity in {"high", "medium"}]
    verdict = "pass" if overall >= threshold and not blocking_findings else "rewrite"
    rewrite_instructions = None
    if verdict == "rewrite":
        contract_hint = ""
        if int(contract_evidence["contract_expectation_count"]) > 0:
            missing_labels = list(contract_evidence["contract_missing_labels"])
            contract_hint = (
                " 并把 chapter contract 的核心冲突、情绪变化、信息释放和尾钩真正落到正文。"
                if not missing_labels
                else f" 并重点修正这些 contract 缺口：{', '.join(missing_labels)}。"
            )
        rewrite_instructions = (
            f"请重写第{chapter.chapter_number}章，保持场景顺序不变，重点补强章节推进、"
            f"场景衔接、连续性和结尾钩子。{contract_hint}"
        )

    return ChapterReviewResult(
        verdict=verdict,
        severity_max=_max_severity(
            [SceneReviewFinding(category=f.category, severity=f.severity, message=f.message) for f in findings]
        ),
        scores=ChapterReviewScores(
            overall=overall,
            goal=goal,
            coverage=coverage,
            coherence=coherence,
            continuity=continuity,
            style=style,
            hook=hook,
            contract_alignment=contract_alignment,
        ),
        findings=findings,
        evidence_summary={
            "word_count": draft.word_count,
            "target_word_count": chapter.target_word_count,
            "scene_heading_count": scene_heading_count,
            "expected_scene_count": expected_scene_count,
            "scene_titles_hit": scene_titles_hit,
            **contract_evidence,
        },
        rewrite_instructions=rewrite_instructions,
    )


async def _load_scene_context(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
) -> tuple[ProjectModel, ChapterModel, SceneCardModel, StyleGuideModel | None, SceneDraftVersionModel]:
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

    draft = await session.scalar(
        select(SceneDraftVersionModel).where(
            SceneDraftVersionModel.scene_card_id == scene.id,
            SceneDraftVersionModel.is_current.is_(True),
        )
    )
    if draft is None:
        raise ValueError(
            f"Scene {scene_number} in chapter {chapter_number} does not have a current draft."
        )

    style_guide = await session.get(StyleGuideModel, project.id)
    return project, chapter, scene, style_guide, draft


async def _load_chapter_context(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
) -> tuple[ProjectModel, ChapterModel, StyleGuideModel | None, list[SceneCardModel], ChapterDraftVersionModel]:
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
        raise ValueError(f"Chapter {chapter_number} does not have any scene cards.")

    draft = await session.scalar(
        select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
    )
    if draft is None:
        raise ValueError(f"Chapter {chapter_number} does not have a current draft.")

    style_guide = await session.get(StyleGuideModel, project.id)
    return project, chapter, style_guide, scenes, draft


async def review_scene_draft(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
    *,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> tuple[SceneReviewResult, ReviewReportModel, QualityScoreModel, RewriteTaskModel | None]:
    project, chapter, scene, _style_guide, draft = await _load_scene_context(
        session,
        project_slug,
        chapter_number,
        scene_number,
    )
    try:
        scene_context = await build_scene_writer_context(
            session,
            settings,
            project_slug,
            chapter_number,
            scene_number,
        )
    except ValueError:
        scene_context = None

    review_result = evaluate_scene_draft(
        scene=scene,
        chapter=chapter,
        draft=draft,
        settings=settings,
        chapter_contract=getattr(scene_context, "chapter_contract", None),
        scene_contract=getattr(scene_context, "scene_contract", None),
    )

    critic_response = render_scene_review_summary(review_result)
    reviewer_type = "mock-critic"
    llm_run_id: UUID | None = None
    system_prompt, user_prompt = build_scene_review_prompts(
        project,
        chapter,
        scene,
        draft,
        review_result,
    )
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=critic_response,
            prompt_template="scene_review",
            prompt_version="1.0",
            project_id=project.id,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
            metadata={
                "project_slug": project.slug,
                "chapter_number": chapter.chapter_number,
                "scene_number": scene.scene_number,
                "verdict": review_result.verdict,
            },
        ),
    )
    critic_response = completion.content.strip() or critic_response
    reviewer_type = completion.model_name
    llm_run_id = completion.llm_run_id

    report = ReviewReportModel(
        project_id=project.id,
        target_type="scene_card",
        target_id=scene.id,
        reviewer_type=reviewer_type,
        verdict=review_result.verdict,
        severity_max=review_result.severity_max,
        llm_run_id=llm_run_id,
        structured_output={
            "draft_id": str(draft.id),
            "findings": [finding.model_dump(mode="json") for finding in review_result.findings],
            "evidence_summary": review_result.evidence_summary,
            "rewrite_instructions": review_result.rewrite_instructions,
            "critic_response": critic_response,
        },
    )
    session.add(report)
    await session.flush()

    await session.execute(
        update(QualityScoreModel)
        .where(
            QualityScoreModel.target_type == "scene_card",
            QualityScoreModel.target_id == scene.id,
            QualityScoreModel.is_current.is_(True),
        )
        .values(is_current=False)
    )

    quality = QualityScoreModel(
        project_id=project.id,
        target_type="scene_card",
        target_id=scene.id,
        review_report_id=report.id,
        is_current=True,
        score_overall=review_result.scores.overall,
        score_goal=review_result.scores.goal,
        score_conflict=review_result.scores.conflict,
        score_emotion=review_result.scores.emotion,
        score_dialogue=review_result.scores.dialogue,
        score_style=review_result.scores.style,
        score_hook=review_result.scores.hook,
        evidence_summary=review_result.evidence_summary,
    )
    session.add(quality)

    rewrite_task: RewriteTaskModel | None = None
    if review_result.verdict == "rewrite":
        rewrite_task = RewriteTaskModel(
            project_id=project.id,
            trigger_type="scene_review",
            trigger_source_id=scene.id,
            rewrite_strategy="scene_dialogue_conflict_expansion",
            priority=3,
            status="pending",
            instructions=review_result.rewrite_instructions or "请补强当前场景。",
            context_required=[
                "scene_card",
                "chapter_context",
                "current_scene_draft",
                "review_findings",
            ],
            metadata_json={
                "scene_id": str(scene.id),
                "chapter_id": str(chapter.id),
                "draft_id": str(draft.id),
                "review_report_id": str(report.id),
            },
        )
        session.add(rewrite_task)
        await session.flush()
        await analyze_rewrite_impacts_for_scene_task(
            session,
            project_id=project.id,
            chapter=chapter,
            scene=scene,
            rewrite_task=rewrite_task,
        )
        scene.status = SceneStatus.NEEDS_REWRITE.value
        chapter.status = ChapterStatus.REVISION.value
    else:
        scene.status = SceneStatus.APPROVED.value
        chapter.status = ChapterStatus.REVIEW.value

    await session.flush()
    return review_result, report, quality, rewrite_task


async def review_chapter_draft(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    *,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> tuple[ChapterReviewResult, ReviewReportModel, QualityScoreModel, RewriteTaskModel | None]:
    project, chapter, _style_guide, scenes, draft = await _load_chapter_context(
        session,
        project_slug,
        chapter_number,
    )
    try:
        chapter_context = await build_chapter_writer_context(
            session,
            settings,
            project_slug,
            chapter_number,
        )
    except ValueError:
        chapter_context = None

    review_result = evaluate_chapter_draft(
        chapter=chapter,
        scenes=scenes,
        draft=draft,
        settings=settings,
        chapter_contract=getattr(chapter_context, "chapter_contract", None),
    )

    critic_response = render_chapter_review_summary(review_result)
    reviewer_type = "mock-critic"
    llm_run_id: UUID | None = None
    system_prompt, user_prompt = build_chapter_review_prompts(
        project,
        chapter,
        draft,
        chapter_context,
        review_result,
    )
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=critic_response,
            prompt_template="chapter_review",
            prompt_version="1.0",
            project_id=project.id,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
            metadata={
                "project_slug": project.slug,
                "chapter_number": chapter.chapter_number,
                "verdict": review_result.verdict,
            },
        ),
    )
    critic_response = completion.content.strip() or critic_response
    reviewer_type = completion.model_name
    llm_run_id = completion.llm_run_id

    report = ReviewReportModel(
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        reviewer_type=reviewer_type,
        verdict=review_result.verdict,
        severity_max=review_result.severity_max,
        llm_run_id=llm_run_id,
        structured_output={
            "draft_id": str(draft.id),
            "findings": [finding.model_dump(mode="json") for finding in review_result.findings],
            "evidence_summary": review_result.evidence_summary,
            "rewrite_instructions": review_result.rewrite_instructions,
            "critic_response": critic_response,
        },
    )
    session.add(report)
    await session.flush()

    await session.execute(
        update(QualityScoreModel)
        .where(
            QualityScoreModel.target_type == "chapter",
            QualityScoreModel.target_id == chapter.id,
            QualityScoreModel.is_current.is_(True),
        )
        .values(is_current=False)
    )

    quality = QualityScoreModel(
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        review_report_id=report.id,
        is_current=True,
        score_overall=review_result.scores.overall,
        score_goal=review_result.scores.goal,
        score_conflict=review_result.scores.coverage,
        score_emotion=review_result.scores.coherence,
        score_dialogue=review_result.scores.continuity,
        score_style=review_result.scores.style,
        score_hook=review_result.scores.hook,
        evidence_summary=review_result.evidence_summary,
    )
    session.add(quality)

    rewrite_task: RewriteTaskModel | None = None
    if review_result.verdict == "rewrite":
        rewrite_task = RewriteTaskModel(
            project_id=project.id,
            trigger_type="chapter_review",
            trigger_source_id=chapter.id,
            rewrite_strategy="chapter_coherence_bridge_rewrite",
            priority=4,
            status="pending",
            instructions=review_result.rewrite_instructions or "请补强当前章节。",
            context_required=[
                "chapter_context",
                "current_chapter_draft",
                "scene_summaries",
                "review_findings",
            ],
            metadata_json={
                "chapter_id": str(chapter.id),
                "draft_id": str(draft.id),
                "review_report_id": str(report.id),
            },
        )
        session.add(rewrite_task)
        chapter.status = ChapterStatus.REVISION.value
    else:
        chapter.status = ChapterStatus.COMPLETE.value

    await session.flush()
    return review_result, report, quality, rewrite_task


def render_rewritten_scene_markdown(
    project: ProjectModel,
    chapter: ChapterModel,
    scene: SceneCardModel,
    current_draft: SceneDraftVersionModel,
    rewrite_task: RewriteTaskModel,
    style_guide: StyleGuideModel | None,
) -> str:
    participants = "、".join(scene.participants) if scene.participants else "相关角色"
    tone = (
        "、".join(str(keyword) for keyword in style_guide.tone_keywords[:3])
        if style_guide and style_guide.tone_keywords
        else "克制、紧张"
    )
    if not re.search(r"[\u4e00-\u9fff]", tone):
        tone = "克制、紧张"
    story_purpose = _normalize_fragment(str(scene.purpose.get("story", "推进本章主线")))
    emotion_purpose = _normalize_fragment(str(scene.purpose.get("emotion", "拉高当前张力")))
    chapter_goal = _normalize_fragment(chapter.chapter_goal)
    title = scene.title or f"场景 {scene.scene_number}"

    revised_sections = [
        f"## 场景 {scene.scene_number}：{title}",
        (
            f"{scene.time_label or '这一刻'}，{participants}重新被推回《{project.title}》第"
            f"{chapter.chapter_number}章的核心冲突。叙事仍采用 "
            f"{style_guide.pov_type if style_guide else 'third-limited'} 视角，但会更强调 {tone} 的压迫感。"
        ),
        (
            f"这一版重写围绕“{story_purpose}”展开，并把“{emotion_purpose}”真正落实到动作、停顿、"
            f"呼吸和目光变化里。本章目标是：{chapter_goal}。"
        ),
        (
            f"{participants}之间的空气一寸寸收紧，没有人愿意先退。"
            f"{scene.participants[0] if scene.participants else '主角'}压低声音说："
            f"“这不是一条能照着旧航图走完的路，我们现在每向前一步，都可能踩进别人故意留下的陷阱。”"
        ),
        (
            f"另一方没有立刻回答，只是盯着光屏上的异常波纹，任由沉默把压力继续抬高。"
            f"他们的分歧不再停留在说明层面，而是直接影响接下来谁来承担风险、谁来做最终决定。"
        ),
        (
            f"随着争执升级，场景里的感官细节被进一步放大：金属舱壁传来的冷意、警报灯反复切换的微红、"
            f"以及每一次视线交锋后更明显的戒备。人物说出口的话和没有说出口的话同时构成冲突。"
        ),
        (
            f"{scene.participants[0] if scene.participants else '主角'}最终意识到，这场对抗真正逼近的不是表面任务，"
            f"而是更深一层的真相。结尾必须留下钩子：有人已经提前一步改写了规则，而他们此刻才刚刚看见痕迹。"
        ),
        "",
        "### 修订说明",
        f"- 重写策略：{rewrite_task.rewrite_strategy}",
        f"- 本次任务：{rewrite_task.instructions}",
        "",
        "### 上一版草稿",
        current_draft.content_md.strip(),
    ]

    content_md = "\n\n".join(section for section in revised_sections if section is not None).strip()
    minimum_words = int(max(scene.target_word_count * 0.72, 720))
    expansion_templates = [
        (
            f"{participants}没有任何一方能够轻易退出这场局面。每一次追问都逼着人物在忠诚、恐惧和判断之间做选择，"
            "让冲突不只是信息交换，而是立场交换。"
        ),
        (
            f"人物之间的对话继续推进：“如果我们现在退回去，后面所有人都会沿着错误答案继续走下去。”"
            f"{scene.participants[-1] if scene.participants else '对方'}抬起下巴，却没有给出真正轻松的回应。"
        ),
        (
            f"情绪层面的张力也在持续升高。{emotion_purpose}不再是抽象标签，而是变成呼吸变短、语速失衡、"
            "以及做决定前那一秒钟过长的停顿。"
        ),
        (
            "场景收尾时，外部局势又向前推了一格。新的异常信号、新的时间压力和新的选择成本同时压下来，"
            "确保下一场戏必须立刻接续，而不是平滑结束。"
        ),
    ]
    expansion_index = 0
    while count_words(content_md) < minimum_words:
        content_md = f"{content_md}\n\n{expansion_templates[expansion_index % len(expansion_templates)]}"
        expansion_index += 1

    return content_md.strip()


def render_rewritten_chapter_markdown(
    project: ProjectModel,
    chapter: ChapterModel,
    current_draft: ChapterDraftVersionModel,
    rewrite_task: RewriteTaskModel,
    chapter_context,
) -> str:
    title = chapter.title or f"第{chapter.chapter_number}章"
    original_content = current_draft.content_md.strip()
    if original_content.startswith("# 第"):
        parts = original_content.split("\n\n", 2)
        original_body = parts[2] if len(parts) >= 3 else parts[-1]
    else:
        original_body = original_content

    previous_summary = "上一阶段的冲突余波仍未散去。"
    if chapter_context is not None and chapter_context.previous_scene_summaries:
        previous_summary = chapter_context.previous_scene_summaries[0].summary
    final_hook_source = chapter.chapter_goal
    if (
        chapter_context is not None
        and chapter_context.recent_timeline_events
        and chapter_context.recent_timeline_events[0].summary
    ):
        final_hook_source = chapter_context.recent_timeline_events[0].summary
    transition_lines = [
        f"# 第{chapter.chapter_number}章 {title}",
        f"> 本章目标：{chapter.chapter_goal}",
        (
            f"上一阶段留下的局势仍压在众人心头：{previous_summary}"
            " 这一章不再只是承接，而是要把冲突继续推向更高层级。"
        ),
        original_body,
        (
            f"章节收束时，{final_hook_source}不再只是背景，而变成下一章必须立刻面对的现实。"
            " 真正的危险已经被看见，但还没有被解决。"
        ),
    ]
    content_md = "\n\n".join(section.strip() for section in transition_lines if section and section.strip())
    minimum_words = int(max(chapter.target_word_count * 0.75, 1200))
    padding_templates = [
        "人物在章节内部的每一次选择，都在把局势从局部问题推成更难回头的整体问题。",
        "场景与场景之间的推进不该像并列事件，而要像一根逐步收紧的绳索，把人物逼向同一个结果。",
        "章节末尾留下的不是单纯的信息点，而是更高一级的压力、代价和无法回避的新决定。",
    ]
    index = 0
    while count_words(content_md) < minimum_words:
        content_md = f"{content_md}\n\n{padding_templates[index % len(padding_templates)]}"
        index += 1
    return content_md.strip()


async def rewrite_chapter_from_task(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    *,
    rewrite_task_id: UUID | None = None,
    settings: AppSettings | None = None,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> tuple[ChapterDraftVersionModel, RewriteTaskModel]:
    project, chapter, _style_guide, _scenes, current_draft = await _load_chapter_context(
        session,
        project_slug,
        chapter_number,
    )

    rewrite_query = select(RewriteTaskModel).where(
        RewriteTaskModel.project_id == project.id,
        RewriteTaskModel.trigger_source_id == chapter.id,
    )
    if rewrite_task_id is not None:
        rewrite_query = rewrite_query.where(RewriteTaskModel.id == rewrite_task_id)
    else:
        rewrite_query = rewrite_query.where(RewriteTaskModel.status.in_(["pending", "queued"]))
    rewrite_query = rewrite_query.order_by(RewriteTaskModel.created_at.desc())

    rewrite_task = await session.scalar(rewrite_query.limit(1))
    if rewrite_task is None:
        raise ValueError(f"Chapter {chapter_number} does not have a pending rewrite task.")

    chapter_context = None
    if settings is not None:
        chapter_context = await build_chapter_writer_context(
            session,
            settings,
            project_slug,
            chapter_number,
        )
    fallback_content = render_rewritten_chapter_markdown(
        project,
        chapter,
        current_draft,
        rewrite_task,
        chapter_context,
    )

    model_name = "mock-editor"
    llm_run_id: UUID | None = None
    generation_mode = "chapter-rewrite-fallback"
    content_md = fallback_content
    if settings is not None and chapter_context is not None:
        system_prompt, user_prompt = build_chapter_rewrite_prompts(
            project,
            chapter,
            current_draft,
            rewrite_task,
            chapter_context,
        )
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="editor",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=fallback_content,
                prompt_template="chapter_rewrite",
                prompt_version="1.0",
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                metadata={
                    "project_slug": project.slug,
                    "chapter_number": chapter.chapter_number,
                    "rewrite_task_id": str(rewrite_task.id),
                },
            ),
        )
        content_md = sanitize_novel_markdown_content(completion.content) or fallback_content
        model_name = completion.model_name
        llm_run_id = completion.llm_run_id
        generation_mode = completion.provider
    else:
        content_md = sanitize_novel_markdown_content(content_md)

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

    new_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=next_version,
        content_md=content_md,
        word_count=word_count,
        assembled_from_scene_draft_ids=list(current_draft.assembled_from_scene_draft_ids),
        is_current=True,
        llm_run_id=llm_run_id,
    )
    session.add(new_draft)
    await session.flush()
    rewrite_task.status = "completed"
    rewrite_task.attempts = int(rewrite_task.attempts or 0) + 1
    rewrite_task.metadata_json = {
        **(rewrite_task.metadata_json or {}),
        "rewritten_chapter_draft_id": str(new_draft.id),
        "generation_mode": generation_mode,
    }
    chapter.status = ChapterStatus.REVIEW.value
    return new_draft, rewrite_task


async def rewrite_scene_from_task(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    scene_number: int,
    *,
    rewrite_task_id: UUID | None = None,
    settings: AppSettings | None = None,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> tuple[SceneDraftVersionModel, RewriteTaskModel]:
    project, chapter, scene, style_guide, current_draft = await _load_scene_context(
        session,
        project_slug,
        chapter_number,
        scene_number,
    )

    rewrite_query = select(RewriteTaskModel).where(
        RewriteTaskModel.project_id == project.id,
        RewriteTaskModel.trigger_source_id == scene.id,
    )
    if rewrite_task_id is not None:
        rewrite_query = rewrite_query.where(RewriteTaskModel.id == rewrite_task_id)
    else:
        rewrite_query = rewrite_query.where(RewriteTaskModel.status.in_(["pending", "queued"]))
    rewrite_query = rewrite_query.order_by(RewriteTaskModel.created_at.desc())

    rewrite_task = await session.scalar(rewrite_query.limit(1))
    if rewrite_task is None:
        raise ValueError(
            f"Scene {scene_number} in chapter {chapter_number} does not have a pending rewrite task."
        )

    fallback_content = render_rewritten_scene_markdown(
        project,
        chapter,
        scene,
        current_draft,
        rewrite_task,
        style_guide,
    )
    model_name = "mock-editor"
    llm_run_id: UUID | None = None
    generation_mode = "rewrite-fallback"
    content_md = fallback_content
    if settings is not None:
        system_prompt, user_prompt = build_scene_rewrite_prompts(
            project,
            chapter,
            scene,
            current_draft,
            rewrite_task,
            style_guide,
        )
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="editor",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=fallback_content,
                prompt_template="scene_rewrite",
                prompt_version="1.0",
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                metadata={
                    "project_slug": project.slug,
                    "chapter_number": chapter.chapter_number,
                    "scene_number": scene.scene_number,
                    "rewrite_task_id": str(rewrite_task.id),
                },
            ),
        )
        content_md = sanitize_novel_markdown_content(completion.content) or fallback_content
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

    new_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=next_version,
        content_md=content_md,
        word_count=word_count,
        is_current=True,
        model_name=model_name,
        prompt_template="scene_rewrite",
        prompt_version="1.0",
        llm_run_id=llm_run_id,
        generation_params={
            "mode": generation_mode,
            "rewrite_task_id": str(rewrite_task.id),
            "target_word_count": scene.target_word_count,
        },
    )
    session.add(new_draft)
    await session.flush()

    rewrite_task.status = "completed"
    rewrite_task.attempts = int(rewrite_task.attempts) + 1
    rewrite_task.metadata_json = {
        **rewrite_task.metadata_json,
        "completed_draft_id": str(new_draft.id),
        "previous_draft_id": str(current_draft.id),
    }
    scene.status = SceneStatus.DRAFTED.value
    chapter.status = ChapterStatus.DRAFTING.value
    await session.flush()
    return new_draft, rewrite_task
