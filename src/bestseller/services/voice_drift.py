from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import CharacterModel, SceneCardModel, SceneDraftVersionModel, ChapterModel
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.prompt_input_formatter import dict_to_markdown
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


_VOICE_DRIFT_SYSTEM_ZH = """# ROLE
你是一位资深小说编辑，专攻"角色声音一致性"维度。
你做过 30+ 本签约长篇的"声音诊断"，能从 5 句对白里嗅出作者的笔锋走样。
你的判断标准来自三种参照：
- 业界共识：角色声音 = 用词层级 + 句式节奏 + 潜台词 三元组
- 编辑直觉：读者在第 10 章质疑"这个人不像他了"时，问题已在第 6-8 章埋下
- 数据感觉：drift_score 0.3 是读者无感的临界，0.5 已影响信任，0.7 是签约编辑会打回

# CONTEXT
这是一本长篇连载小说的中段质量保障流程。
你检测的是单一角色的"近期文本"是否仍然符合"档案中的声音"。
你的判断会决定：是否生成"角色语音修正 prompt"注入后续章节的写手。

# TASK
对照档案，产出 4 项：
1. drift_score: 0.0-1.0 小数（评分纪律见下）
2. drifted_dimensions: 三元组中具体哪几项漂了（sentence_length / word_register / subtext 任选）
3. evidence: 每项至少引用原文 1 句（≤30 字）
4. correction_prompt: 若 drift_score > 0.3，给一段 ≤80 字的"动作级"修正指令

# THINKING（产出 JSON 前在脑内完成）
1. 读档案，提取 3 个最强特征（如"短句"/"理性"/"少感叹词"）
2. 读样本，每段在心里勾选这 3 个特征是否命中
3. 5 段中若 ≥2 段未命中某一维度 → 标该维度 drift
4. 评分严格执行：
   - drift_score ≤ 0.2: 完全一致
   - 0.3-0.5: 局部漂（一个维度偏）
   - 0.6-0.8: 显著漂（≥2 维度偏）
   - ≥ 0.9: 已是另一个角色

# OUTPUT FORMAT（严格 JSON，无 markdown 围栏，无解释文字）
{
  "drift_score": 0.45,
  "drifted_dimensions": ["sentence_length", "subtext"],
  "evidence": [
    {"dim": "sentence_length", "quote": "原文中 ≤30 字的句子"},
    {"dim": "subtext", "quote": "原文中 ≤30 字的句子"}
  ],
  "correction_prompt": "林渊的对白保持短句（≤15 字），不要长解释；潜台词靠动作（如摩挲铜钱）而非直白心理描写。"
}

# NEGATIVE EXAMPLE（绝对不要这样）
{"drift_score": 0.5, "analysis": "感觉有点不一致"}
（没有 evidence、没有具体维度、correction_prompt 缺失 → 下游无法执行）
"""

_VOICE_DRIFT_SYSTEM_EN = """# ROLE
You are a senior fiction editor specializing in "character voice consistency".
You have audited 30+ signed long-form novels and can detect authorial drift from five lines of dialogue.

# CONTEXT
A long-running serial novel quality check.
Your verdict decides whether to inject a "voice correction" prompt into downstream chapter writers.

# TASK
Produce: drift_score, drifted_dimensions, evidence (≤30-char quotes per dim), correction_prompt (if score > 0.3).

# THINKING
1. Extract 3 strongest traits from the profile.
2. Mark each sample against those traits.
3. ≥2 misses on a dim → flag that dim as drifted.
4. Score: ≤0.2 fully consistent / 0.3-0.5 mild / 0.6-0.8 strong / ≥0.9 different character.

# OUTPUT FORMAT (strict JSON, no fences)
{"drift_score": 0.45, "drifted_dimensions": [...], "evidence": [{"dim":..., "quote":...}], "correction_prompt": "..."}
"""


def _build_system_prompt(language: str | None) -> str:
    if str(language or "").lower().startswith("en"):
        return _VOICE_DRIFT_SYSTEM_EN
    return _VOICE_DRIFT_SYSTEM_ZH


class VoiceDriftResult(BaseModel):
    character_name: str
    drift_detected: bool = False
    drift_score: float = Field(default=0.0, ge=0, le=1)
    drifted_dimensions: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    analysis: str = ""
    correction_prompt: str | None = None


async def check_voice_drift(
    session: AsyncSession,
    settings: AppSettings,
    project_id: UUID,
    character_name: str,
    recent_chapter_start: int,
    recent_chapter_end: int,
    *,
    workflow_run_id: UUID | None = None,
    language: str = "zh-CN",
) -> VoiceDriftResult:
    """Compare recent dialogue against the character's voice profile to detect drift.

    Samples dialogue from recent chapters and compares against the voice_profile_json
    stored on the character model. Returns a drift score and optional correction prompt.
    """
    # Load character
    character = await session.scalar(
        select(CharacterModel).where(
            CharacterModel.project_id == project_id,
            CharacterModel.name == character_name,
        )
    )
    if character is None:
        return VoiceDriftResult(
            character_name=character_name,
            analysis=f"Character '{character_name}' not found.",
        )

    voice_profile = character.voice_profile_json or {}
    if not voice_profile:
        return VoiceDriftResult(
            character_name=character_name,
            analysis="No voice profile defined; drift check skipped.",
        )

    # Gather recent scene drafts for dialogue sampling
    chapter_ids = list(
        await session.scalars(
            select(ChapterModel.id).where(
                ChapterModel.project_id == project_id,
                ChapterModel.chapter_number >= recent_chapter_start,
                ChapterModel.chapter_number <= recent_chapter_end,
            )
        )
    )
    if not chapter_ids:
        return VoiceDriftResult(
            character_name=character_name,
            analysis="No chapters found in the specified range.",
        )

    scene_ids = list(
        await session.scalars(
            select(SceneCardModel.id).where(
                SceneCardModel.chapter_id.in_(chapter_ids)
            )
        )
    )
    if not scene_ids:
        return VoiceDriftResult(
            character_name=character_name,
            analysis="No scenes found in the specified chapter range.",
        )

    drafts = list(
        await session.scalars(
            select(SceneDraftVersionModel).where(
                SceneDraftVersionModel.scene_card_id.in_(scene_ids),
                SceneDraftVersionModel.is_current.is_(True),
            ).limit(10)  # Sample up to 10 recent scenes
        )
    )

    if not drafts:
        return VoiceDriftResult(
            character_name=character_name,
            analysis="No current drafts found for sampling.",
        )

    # Extract text snippets (limit to avoid prompt overflow)
    text_snippets = []
    for draft in drafts:
        text = draft.content_md or ""
        if character_name in text and len(text) > 100:
            # Extract a window around the character's name
            idx = text.find(character_name)
            start = max(0, idx - 200)
            end = min(len(text), idx + 500)
            text_snippets.append(text[start:end])
    if not text_snippets:
        return VoiceDriftResult(
            character_name=character_name,
            analysis=f"Character '{character_name}' not found in recent scene text.",
        )

    profile_md = dict_to_markdown(voice_profile)
    snippets_md = "\n\n".join(
        f"### 样本 {i + 1}\n```\n{s.strip()}\n```"
        for i, s in enumerate(text_snippets[:5])
    )
    user_prompt = (
        "## 任务参数\n"
        f"- 角色：{character_name}\n"
        f"- 检测窗口：第 {recent_chapter_start}-{recent_chapter_end} 章\n"
        f"- 样本数：{len(text_snippets[:5])}\n\n"
        "## 角色 Voice 档案\n"
        f"{profile_md}\n\n"
        "## 待检样本（原文片段）\n"
        f"{snippets_md}\n\n"
        "## 立即开始\n"
        "按 system 中的 THINKING 步骤思考后，输出严格 JSON。"
    )

    response = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            system_prompt=_build_system_prompt(language),
            user_prompt=user_prompt,
            fallback_response=f'{{"drift_score": 0.0, "analysis": "Voice drift analysis unavailable (fallback).", "correction_prompt": null}}',
            prompt_template="voice_drift_check",
            project_id=project_id,
            workflow_run_id=workflow_run_id,
        ),
    )

    import json

    def _extract_fields(payload: dict) -> tuple[float, list[str], list[dict], str | None, str]:
        score = float(payload.get("drift_score", 0.0))
        dims = list(payload.get("drifted_dimensions") or [])
        ev = list(payload.get("evidence") or [])
        corr = payload.get("correction_prompt")
        # 兼容旧版返回 analysis；新版没有时拼一份给日志用
        legacy_analysis = str(payload.get("analysis") or "")
        if not legacy_analysis and dims:
            legacy_analysis = f"drift on: {', '.join(dims)}"
        return score, dims, ev, corr, legacy_analysis

    try:
        parsed = json.loads(response.content.strip())
        drift_score, drifted_dims, evidence_list, correction, analysis = _extract_fields(parsed)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        try:
            from bestseller.services.llm_closed_loop import (
                build_repair_user_prompt,
                findings_from_exception,
            )

            repair = await complete_text(
                session,
                settings,
                LLMCompletionRequest(
                    logical_role="critic",
                    system_prompt=_build_system_prompt(language),
                    user_prompt=build_repair_user_prompt(
                        original_user_prompt=user_prompt,
                        findings=findings_from_exception(exc),
                        language=language,
                    ),
                    fallback_response='{"drift_score": 0.0, "drifted_dimensions": [], "evidence": [], "correction_prompt": null}',
                    prompt_template="voice_drift_check_repair",
                    project_id=project_id,
                    workflow_run_id=workflow_run_id,
                    metadata={"semantic_repair": True},
                ),
            )
            parsed = json.loads(repair.content.strip())
            drift_score, drifted_dims, evidence_list, correction, analysis = _extract_fields(parsed)
        except (json.JSONDecodeError, ValueError, TypeError):
            drift_score = 0.0
            drifted_dims = []
            evidence_list = []
            analysis = "Voice drift analysis unavailable (parse_error_degraded)."
            correction = None

    drift_detected = drift_score > 0.3

    if drift_detected:
        logger.warning(
            "Voice drift detected for '%s' (score=%.2f, dims=%s): %s",
            character_name,
            drift_score,
            drifted_dims,
            analysis[:200],
        )

    return VoiceDriftResult(
        character_name=character_name,
        drift_detected=drift_detected,
        drift_score=drift_score,
        drifted_dimensions=drifted_dims,
        evidence=evidence_list,
        analysis=analysis,
        correction_prompt=correction if drift_detected else None,
    )


async def check_all_pov_voice_drift(
    session: AsyncSession,
    settings: AppSettings,
    project_id: UUID,
    recent_chapter_start: int,
    recent_chapter_end: int,
    *,
    workflow_run_id: UUID | None = None,
) -> list[VoiceDriftResult]:
    """Check voice drift for all POV characters in the project."""
    characters = list(
        await session.scalars(
            select(CharacterModel).where(
                CharacterModel.project_id == project_id,
                CharacterModel.is_pov_character.is_(True),
            )
        )
    )
    results = []
    for char in characters:
        result = await check_voice_drift(
            session,
            settings,
            project_id,
            char.name,
            recent_chapter_start,
            recent_chapter_end,
            workflow_run_id=workflow_run_id,
        )
        results.append(result)
    return results
