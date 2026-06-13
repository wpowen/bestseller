"""G11 (zhaoshen-hr-v5 regeneration): ReverseOutlineStatus schema tolerance.

The StoryDesignKernel.reverse_outline_status field is a controlled Literal
(not_started/draft/verified/needs_repair), but the planner LLM emitted
'completed_v1' — a literal_error that, on a fail-closed kernel, retries 4x
and then aborts the whole planning run before it ever reaches the volume
plan. Status is a progress marker, not content, so it must coerce, not abort.
"""

from __future__ import annotations

import pytest

from bestseller.services.story_design_kernel import coerce_reverse_outline_status


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("completed_v1", "verified"),  # the exact production failure
        ("completed", "verified"),
        ("done", "verified"),
        ("finished", "verified"),
        ("ready", "verified"),
        ("needs_repair", "needs_repair"),
        ("repair_required", "needs_repair"),
        ("failed", "needs_repair"),
        ("not_started", "not_started"),
        ("pending", "not_started"),
        ("draft", "draft"),
        ("VERIFIED", "verified"),  # case-insensitive
        ("  draft  ", "draft"),  # trimmed
        ("something_weird", "draft"),  # unknown → safe default
        ("", "draft"),
        (None, "draft"),
    ],
)
def test_coerce_reverse_outline_status(raw: object, expected: str) -> None:
    assert coerce_reverse_outline_status(raw) == expected
