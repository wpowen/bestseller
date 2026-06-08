"""Scene-time generation of the protagonist's first-person interiority (入戏).

Wires the ``character_embodiment`` prose lever into production: just before the
writer drafts a scene, the model inhabits the protagonist and emits raw
first-person interiority for *this* scene, which is injected into the writer
prompt (see ``methodology_compiler`` PROSE_SCENE + ``quality_levers/character_embodiment``).

Unlike the book-level imagery system (designed once, persisted), embodiment is
**per-scene** — the interiority is specific to this scene's decision, so it is
generated fresh each draft and passed through the in-memory story-bible context
(never persisted, never summarized).

Soft + zh-only + gated (``enable_character_embodiment``, default True). Any failure
— disabled, English book, no usable situation, LLM error — is a clean no-op that
returns ``""`` so the writer proceeds exactly as before.
"""

# ruff: noqa: ANN401, E501, RUF001

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.quality_levers.character_embodiment import build_embodiment_prompt
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

_MAX_SITUATION_CHARS = 1400
_MIN_SITUATION_CHARS = 24


def character_embodiment_enabled(settings: AppSettings) -> bool:
    """Whether scene-time embodiment is on (flag, default True)."""

    return bool(getattr(settings.pipeline, "enable_character_embodiment", True))


def _coerce_text(value: Any, *, limit: int = 320) -> str:
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, Mapping):
        text = "；".join(
            f"{k}:{v}" for k, v in value.items() if isinstance(v, str | int | float) and str(v).strip()
        )
    elif isinstance(value, (list, tuple)):
        text = "；".join(str(item).strip() for item in value if str(item).strip())
    else:
        text = str(value).strip() if value not in (None, "") else ""
    return text[:limit].strip()


def resolve_protagonist(story_bible: Mapping[str, Any] | None, scene: Any) -> tuple[str, str]:
    """Best-effort (name, persona) for the spotlight character of this scene.

    Prefers the scene's spotlight/first participant, then matches against the
    bible cast for a persona blurb. Returns ("", "") when nothing usable.
    """

    name = ""
    participants = list(getattr(scene, "participants", None) or [])
    meta = getattr(scene, "metadata_json", None)
    if isinstance(meta, Mapping):
        spotlight = meta.get("spotlight_character") or meta.get("pov_character")
        if isinstance(spotlight, str) and spotlight.strip():
            name = spotlight.strip()
    if not name and participants:
        first = participants[0]
        name = first.strip() if isinstance(first, str) else ""

    persona = ""
    if isinstance(story_bible, Mapping):
        cast_spec = story_bible.get("cast_spec") or {}
        characters: list[Any] = []
        if isinstance(cast_spec, Mapping):
            characters = list(cast_spec.get("characters") or [])
            if not characters:
                for key in ("protagonist", "allies", "antagonists"):
                    val = cast_spec.get(key)
                    if isinstance(val, Mapping):
                        characters.append(val)
                    elif isinstance(val, list):
                        characters.extend(c for c in val if isinstance(c, Mapping))
        chosen: Mapping[str, Any] | None = None
        for char in characters:
            if isinstance(char, Mapping) and name and str(char.get("name", "")).strip() == name:
                chosen = char
                break
        if chosen is None and characters and isinstance(characters[0], Mapping):
            chosen = characters[0]
            if not name:
                name = str(chosen.get("name", "")).strip()
        if isinstance(chosen, Mapping):
            bits = []
            for key in ("role", "background", "personality", "want", "wound", "voice"):
                v = chosen.get(key)
                if isinstance(v, str) and v.strip():
                    bits.append(f"{v.strip()[:120]}")
            persona = "；".join(bits)
    return name, persona


def resolve_situation(chapter: Any, scene: Any, *, protagonist: str = "") -> str:
    """Assemble a compact 'what is happening to the protagonist now' brief.

    Pulls from chapter goal + scene purpose/title/entry_state/dialogue beats +
    the scene methodology_contract (conflict/cut_point). Returns "" when too thin.
    """

    parts: list[str] = []
    goal = _coerce_text(getattr(chapter, "chapter_goal", ""), limit=200)
    if goal:
        parts.append(f"本章目标：{goal}")

    title = _coerce_text(getattr(scene, "title", ""), limit=80)
    if title:
        parts.append(f"本场：{title}")

    purpose = getattr(scene, "purpose", None)
    if isinstance(purpose, Mapping):
        story = _coerce_text(purpose.get("story"), limit=200)
        if story:
            parts.append(f"本场要发生：{story}")

    entry = _coerce_text(getattr(scene, "entry_state", None), limit=240)
    if entry:
        parts.append(f"起点状态：{entry}")

    meta = getattr(scene, "metadata_json", None)
    contract = meta.get("methodology_contract") if isinstance(meta, Mapping) else None
    if isinstance(contract, Mapping):
        for label, key in (("冲突/利害", "conflict_stakes"), ("断点", "cut_point"),
                           ("标志画面", "signature_image")):
            val = _coerce_text(contract.get(key), limit=160)
            if val:
                parts.append(f"{label}：{val}")

    beats = _coerce_text(getattr(scene, "key_dialogue_beats", None), limit=200)
    if beats:
        parts.append(f"对白拍子：{beats}")

    situation = "\n".join(parts)
    return situation[:_MAX_SITUATION_CHARS].strip()


async def generate_scene_embodiment(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: Any,
    chapter: Any,
    scene: Any,
    story_bible: Mapping[str, Any] | None,
) -> str:
    """Generate raw first-person interiority for this scene. Soft + zh-only.

    Returns the interiority string, or ``""`` when skipped/failed (no-op).
    """

    if not character_embodiment_enabled(settings):
        return ""
    language = str(getattr(project, "language", "") or "")
    if language.lower().startswith("en"):
        return ""

    situation = resolve_situation(chapter, scene)
    if len(situation) < _MIN_SITUATION_CHARS:
        return ""
    name, persona = resolve_protagonist(story_bible, scene)
    if persona:
        situation = f"我是谁：{persona}\n{situation}"

    genre = str(getattr(project, "genre", "") or "")
    system_prompt, user_prompt = build_embodiment_prompt(
        protagonist=name,
        situation=situation,
        genre=genre,
    )
    try:
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="writer",
                model_tier="standard",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=" ",  # min_length=1; .strip() → "" → clean no-op
                prompt_template="character_embodiment",
                prompt_version="v1",
                max_tokens_override=900,
            ),
        )
    except Exception:
        logger.debug(
            "character embodiment LLM call failed for %s ch%s (non-fatal)",
            getattr(project, "slug", "?"),
            getattr(chapter, "chapter_number", "?"),
            exc_info=True,
        )
        return ""

    return (completion.content or "").strip()


__all__ = [
    "character_embodiment_enabled",
    "generate_scene_embodiment",
    "resolve_protagonist",
    "resolve_situation",
]
