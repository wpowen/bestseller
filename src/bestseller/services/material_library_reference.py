"""Soft reference renderer for the global material library.

This module is the **non-invasive** companion to
:mod:`bestseller.services.material_reference`.  The original module
renders *project-local* forged materials as hard ``§dim/proj/slug``
references that the LLM **must** cite.  This module instead renders
*library-wide* entries as read-only **inspiration blocks** so that
projects which never ran a Forge — i.e. every historical novel — can
still benefit from the library on their next chapter.

Why a soft block and not the hard reference block?
--------------------------------------------------

* **Historical projects have no ``project_materials`` rows.**  The
  existing hard reference block would come back empty for them.
* **Replaying them through Forge would rewrite earlier content.**
  Users explicitly do not want that — the constraint for this feature
  is "subsequent chapters only, no data migration".
* **Soft references are suggestions, not constraints.**  The Drafter
  prompt wording must make clear the model *may* borrow phrasing,
  atmosphere, or structural ideas but **must not** invent new proper
  nouns conflicting with the running novel's own bible.

Activation
----------

Gated by :attr:`PipelineSettings.enable_library_soft_reference`
(default False).  When off, :func:`render_library_soft_reference_block`
short-circuits to the empty string; nothing in the prompt changes.

Contract
--------

* **Read-only.** Never inserts into ``material_library`` and never
  touches ``project_materials``.
* **No usage-count bumps here.**  ``mark_used`` is reserved for Forge
  consumption because soft-reference retrieval is speculative and we
  don't want to skew cross-project-novelty scoring for tentative use.
* **Fail-soft.**  Any error during retrieval logs and returns an empty
  string — the Drafter keeps running on the old path.
"""

from __future__ import annotations

import logging
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.material_library import (
    MaterialEntry,
    NoveltyFilter,
    query_library,
)

logger = logging.getLogger(__name__)


# Dimensions that most help scene-level drafting; kept short so the
# prompt budget stays predictable on long chapters.
_DRAFTER_PRIORITY_DIMS: tuple[str, ...] = (
    "scene_templates",
    "emotion_arcs",
    "dialogue_styles",
    "thematic_motifs",
    "anti_cliche_patterns",
)


def _truncate(text: str, limit: int = 90) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


async def render_library_soft_reference_block(
    session: AsyncSession,
    *,
    query: str,
    genre: str | None,
    sub_genre: str | None = None,
    dimensions: Sequence[str] | None = None,
    top_k: int = 4,
    max_usage_count: int | None = 8,
) -> str:
    """Return a soft "inspiration" block drawn from the global library.

    Parameters
    ----------
    session:
        Active async session. Caller owns the transaction.
    query:
        Free-text retrieval query. Typically "chapter N beat description"
        or "scene brief + POV + stakes". Used for pgvector ranking.
    genre, sub_genre:
        Filters. ``None`` falls back to cross-genre commons.
    dimensions:
        Which dimensions to sample. Defaults to
        :data:`_DRAFTER_PRIORITY_DIMS` which emphasises scene / emotion
        / dialogue / motif / anti-cliche — the things a live Drafter
        call can actually use without re-architecting the book.
    top_k:
        Entries per dimension. Keep small (≤ 6) to bound prompt size.
    max_usage_count:
        Drop entries that have already been referenced too often across
        other projects (cross-project novelty guard, cheap version).

    Returns
    -------
    str
        A Markdown block ready to concatenate into
        ``build_scene_draft_prompts``'s ``user_prompt``, or ``""`` when
        no meaningful material is available.
    """

    dims = tuple(dimensions) if dimensions else _DRAFTER_PRIORITY_DIMS
    novelty_filter = (
        NoveltyFilter(max_usage_count=max_usage_count)
        if max_usage_count is not None
        else None
    )

    picked: dict[str, list[MaterialEntry]] = {}
    for dim in dims:
        try:
            entries = await query_library(
                session,
                dimension=dim,
                query=query,
                genre=genre,
                sub_genre=sub_genre,
                top_k=top_k,
                novelty_filter=novelty_filter,
                include_generic=True,
            )
        except Exception as exc:  # noqa: BLE001 — soft-fail by design
            logger.warning(
                "library soft-reference query failed for dim=%s genre=%s: %s",
                dim,
                genre,
                exc,
            )
            continue
        if entries:
            picked[dim] = entries

    if not picked:
        return ""

    lines = [
        "## 资源库灵感（仅供参考，不强制引用）",
        "下列条目来自共享物料库，可借鉴气氛、结构、意象，",
        "**但不得直接套用其中的专有名词；本书人物/宗门/地名必须以本书大纲为准。**",
        "",
    ]
    for dim in dims:
        items = picked.get(dim)
        if not items:
            continue
        lines.append(f"### {dim}")
        for entry in items:
            summary = _truncate(entry.narrative_summary)
            lines.append(f"  • {entry.name} — {summary}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "render_library_soft_reference_block",
]
