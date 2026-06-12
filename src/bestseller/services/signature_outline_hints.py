"""Derive per-chapter signature-mandate hints from the DB outline (R25).

The signature-scene bootstrap used to produce archetype/stake skeletons with
``must_include_image`` / ``summary`` / ``title_hint`` all empty — the writer
got no concrete target and the gate had no verifiable standard. When the
project already has a chapter outline (``chapters`` + ``scene_cards``), the
concrete targets can be derived deterministically (no LLM):

* ``signature_images`` — each scene card's ``metadata->signature_image``
  (top-level or nested under ``methodology_contract``), in scene order
* ``goal`` — the chapter's ``chapter_goal``
* ``title`` — the chapter title

``plan_signature_scenes(chapter_outline=...)`` consumes the mapping returned
here. Mandates whose chapter has no outline data stay skeletons and are not
rendered into the writer prompt.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select

logger = logging.getLogger(__name__)


def _scene_signature_image(metadata: Mapping[str, Any] | None) -> str:
    if not isinstance(metadata, Mapping):
        return ""
    direct = str(metadata.get("signature_image") or "").strip()
    if direct:
        return direct
    contract = metadata.get("methodology_contract")
    if isinstance(contract, Mapping):
        return str(contract.get("signature_image") or "").strip()
    return ""


async def load_chapter_outline_hints(
    session: Any,
    project_id: Any,
) -> dict[int, dict[str, Any]]:
    """Build ``{chapter_number: {title, goal, signature_images}}`` from the DB.

    Returns an empty mapping when the project has no chapter rows. Each entry
    only carries non-empty values so downstream derivation can distinguish
    "outline exists but field empty" from "field present".
    """

    from bestseller.infra.db.models import ChapterModel, SceneCardModel

    hints: dict[int, dict[str, Any]] = {}
    chapter_numbers_by_id: dict[Any, int] = {}

    chapters = await session.scalars(
        select(ChapterModel)
        .where(ChapterModel.project_id == project_id)
        .order_by(ChapterModel.chapter_number)
    )
    for chapter in chapters:
        number = int(getattr(chapter, "chapter_number", 0) or 0)
        if number < 1:
            continue
        chapter_numbers_by_id[chapter.id] = number
        entry: dict[str, Any] = {}
        title = str(getattr(chapter, "title", None) or "").strip()
        goal = str(getattr(chapter, "chapter_goal", None) or "").strip()
        if title:
            entry["title"] = title
        if goal:
            entry["goal"] = goal
        hints[number] = entry

    if not hints:
        return {}

    scenes = await session.scalars(
        select(SceneCardModel)
        .where(SceneCardModel.project_id == project_id)
        .order_by(SceneCardModel.scene_number)
    )
    for scene in scenes:
        number = chapter_numbers_by_id.get(getattr(scene, "chapter_id", None))
        if number is None:
            continue
        image = _scene_signature_image(getattr(scene, "metadata_json", None))
        if not image:
            continue
        images = hints[number].setdefault("signature_images", [])
        if image not in images:
            images.append(image)

    # Drop chapters that contributed nothing concrete.
    return {number: entry for number, entry in hints.items() if entry}


__all__ = ["load_chapter_outline_hints"]
