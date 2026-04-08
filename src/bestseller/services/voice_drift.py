from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import CharacterModel, SceneCardModel, SceneDraftVersionModel, ChapterModel
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


class VoiceDriftResult(BaseModel):
    character_name: str
    drift_detected: bool = False
    drift_score: float = Field(default=0.0, ge=0, le=1)
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

    user_prompt = (
        f"Compare the following recent dialogue/narration excerpts for the character "
        f"'{character_name}' against their established voice profile.\n\n"
        f"## Voice Profile\n{voice_profile}\n\n"
        f"## Recent Excerpts (chapters {recent_chapter_start}-{recent_chapter_end})\n"
        + "\n---\n".join(text_snippets[:5])
        + "\n\n"
        f"Respond in JSON:\n"
        f'{{"drift_score": <0.0-1.0 where 0=consistent, 1=completely drifted>, '
        f'"analysis": "<brief analysis>", '
        f'"correction_prompt": "<if drift_score > 0.3, a short prompt to inject into future scene context to correct the drift, else null>"}}'
    )

    response = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            system_prompt="You are a literary voice consistency analyst. Detect character voice drift by comparing recent text against established voice profiles.",
            user_prompt=user_prompt,
            fallback_response=f'{{"drift_score": 0.0, "analysis": "Voice drift analysis unavailable (fallback).", "correction_prompt": null}}',
            prompt_template="voice_drift_check",
            project_id=project_id,
            workflow_run_id=workflow_run_id,
        ),
    )

    import json
    try:
        parsed = json.loads(response.content.strip())
        drift_score = float(parsed.get("drift_score", 0.0))
        analysis = parsed.get("analysis", "")
        correction = parsed.get("correction_prompt")
    except (json.JSONDecodeError, ValueError, TypeError):
        drift_score = 0.0
        analysis = response.content.strip()
        correction = None

    drift_detected = drift_score > 0.3

    if drift_detected:
        logger.warning(
            "Voice drift detected for '%s' (score=%.2f): %s",
            character_name,
            drift_score,
            analysis[:200],
        )

    return VoiceDriftResult(
        character_name=character_name,
        drift_detected=drift_detected,
        drift_score=drift_score,
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
