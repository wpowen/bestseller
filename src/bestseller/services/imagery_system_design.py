"""Design-time generation of a book's imagery system (makes the lever non-no-op).

The ``imagery_system`` writer lever (``quality_levers/imagery_system.py``) only
renders when a book's ``story_bible`` actually contains a designed imagery system.
Nothing populated it, so in production it was a no-op. This module closes that gap
with a single idempotent, soft, book-level step that:

* designs 2-3 core images once per book via an LLM (the LitStyle 意象系统设计器),
* stores the artifact in ``project.metadata_json['imagery_system']`` (so
  ``load_scene_story_bible_context`` can expose it),
* is **idempotent** (returns the stored artifact without an LLM call once present),
  **soft** (any failure is a no-op — imagery simply won't render) and **zh-only**.

It is deliberately wired at the scene-generation entry (``generate_scene_draft``),
NOT in the conception/planner pipeline, to stay isolated from that (concurrently
edited) code. Generating lazily on first draft is fine: the artifact persists to
the DB, so every subsequent chapter picks it up.
"""

# ruff: noqa: ANN401, E501

from __future__ import annotations

from dataclasses import asdict
import json
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.quality_levers.imagery_system import (
    build_imagery_designer_prompt,
    parse_imagery_artifact,
)
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

_META_KEY = "imagery_system"


def imagery_system_design_enabled(settings: AppSettings) -> bool:
    """Whether design-time imagery generation is on (flag, default True)."""

    return bool(getattr(settings.pipeline, "enable_imagery_system_design", True))


def _resolve_premise(project: Any) -> str:
    """Best-effort premise/logline for the designer, from the project's metadata."""

    meta = project.metadata_json if isinstance(getattr(project, "metadata_json", None), dict) else {}
    logline = meta.get("logline")
    if isinstance(logline, str) and logline.strip():
        return logline.strip()
    book_spec = meta.get("book_spec")
    if isinstance(book_spec, dict):
        for key in ("premise", "logline", "synopsis", "one_liner"):
            value = book_spec.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for attr in ("premise", "description", "logline"):
        value = getattr(project, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _existing_artifact(project: Any) -> dict[str, Any] | None:
    meta = project.metadata_json if isinstance(getattr(project, "metadata_json", None), dict) else {}
    existing = meta.get(_META_KEY)
    if isinstance(existing, dict) and existing.get("images"):
        return existing
    return None


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    unfenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I | re.S).strip()
    for candidate in (stripped, unfenced):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", unfenced, flags=re.S)
    if match:
        try:
            value = json.loads(match.group(0))
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    return {}


async def ensure_book_imagery_system(
    session: AsyncSession,
    settings: AppSettings,
    project: Any,
) -> dict[str, Any] | None:
    """Idempotently design + persist this book's imagery system. Soft, zh-only.

    Returns the stored artifact dict, or ``None`` when skipped/failed (no-op).
    """

    if not imagery_system_design_enabled(settings):
        return None
    language = str(getattr(project, "language", "") or "")
    if language.lower().startswith("en"):
        return None

    existing = _existing_artifact(project)
    if existing is not None:
        return existing  # idempotent — no LLM call

    premise = _resolve_premise(project)
    if not premise:
        return None
    genre = str(getattr(project, "genre", "") or "")

    system_prompt, user_prompt = build_imagery_designer_prompt(premise=premise, genre=genre)
    try:
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="planner",
                model_tier="strong",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response='{"images": []}',
                prompt_template="imagery_system_designer",
                prompt_version="v1",
                max_tokens_override=1500,
            ),
        )
    except Exception:
        logger.exception(
            "imagery system design LLM call failed for project %s (non-fatal)",
            getattr(project, "id", "?"),
        )
        return None

    artifact = parse_imagery_artifact(_parse_json_object(completion.content))
    if not artifact.images:
        return None

    stored: dict[str, Any] = {
        "theme_core": artifact.theme_core,
        "images": [asdict(image) for image in artifact.images],
    }
    meta = project.metadata_json if isinstance(getattr(project, "metadata_json", None), dict) else {}
    project.metadata_json = {**meta, _META_KEY: stored}
    try:
        flag_modified(project, "metadata_json")
        await session.flush()
    except Exception:
        logger.exception(
            "persisting imagery system failed for project %s (non-fatal)",
            getattr(project, "id", "?"),
        )
        return stored
    logger.info(
        "designed imagery system for project %s: %d images",
        getattr(project, "id", "?"),
        len(artifact.images),
    )
    return stored


__all__ = ["ensure_book_imagery_system", "imagery_system_design_enabled"]
