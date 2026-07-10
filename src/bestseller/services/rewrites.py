"""Compatibility surface for rewrite prompt builders and executors.

The long-term target is to move rewrite implementation out of ``reviews.py``.
For this migration step, this module gives new callers a rewrite-only import
surface without forcing a risky same-PR relocation of several thousand lines.
"""

from __future__ import annotations

from bestseller.services.reviews import (
    build_chapter_rewrite_prompts,
    build_scene_rewrite_prompts,
    rewrite_chapter_from_task,
    rewrite_scene_from_task,
)
from bestseller.services.rewrite_patch import (
    RewritePatch,
    RewritePatchResult,
    apply_rewrite_patch,
)


def apply_rewrite_patch_candidate(
    parent_text: str,
    patch: RewritePatch,
) -> RewritePatchResult:
    """Apply a validated local patch without mutating or promoting its parent.

    The pipeline must still create a new draft version, rerun hard gates, and
    obtain promotion evidence.  Keeping this adapter pure prevents a failed
    patch attempt from silently replacing the last known-good draft.
    """

    return apply_rewrite_patch(parent_text, patch)

__all__ = [
    "build_chapter_rewrite_prompts",
    "build_scene_rewrite_prompts",
    "apply_rewrite_patch_candidate",
    "rewrite_chapter_from_task",
    "rewrite_scene_from_task",
]
