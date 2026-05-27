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

__all__ = [
    "build_chapter_rewrite_prompts",
    "build_scene_rewrite_prompts",
    "rewrite_chapter_from_task",
    "rewrite_scene_from_task",
]
