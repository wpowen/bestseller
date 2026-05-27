"""Stable artifact topology for reverse quality attribution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict


class ArtifactNode(TypedDict):
    upstream: list[str]


ARTIFACT_TOPOLOGY: dict[str, ArtifactNode] = {
    "chapter_text": {"upstream": ["scene_plan", "chapter_outline"]},
    "scene_plan": {
        "upstream": [
            "chapter_outline",
            "world_bible",
            "character_card",
            "material_entry",
        ]
    },
    "chapter_outline": {"upstream": ["volume_plan", "reveal_schedule", "rule_ledger"]},
    "volume_plan": {"upstream": ["series_bible"]},
    "rule_ledger": {"upstream": ["world_bible"]},
    "reveal_schedule": {"upstream": ["series_bible"]},
    "character_card": {"upstream": ["series_bible"]},
    "world_bible": {"upstream": ["series_bible", "distilled_mechanism"]},
    "series_bible": {"upstream": ["methodology_card"]},
    "material_entry": {"upstream": ["distilled_mechanism"]},
    "distilled_mechanism": {"upstream": ["methodology_card"]},
    "methodology_card": {"upstream": []},
}

REPAIR_PRIORITY: tuple[str, ...] = (
    "methodology_card",
    "distilled_mechanism",
    "series_bible",
    "world_bible",
    "volume_plan",
    "rule_ledger",
    "reveal_schedule",
    "character_card",
    "material_entry",
    "chapter_outline",
    "scene_plan",
    "chapter_text",
)

REPAIR_PRIORITY_RULE = """
artifact 修复必须自上而下:
  series_bible > world_bible > volume_plan > rule_ledger
  > chapter_outline > scene_plan > chapter_text

下游 artifact 健康度未达标时, 禁止重写上游产物以外的内容.
""".strip()


def artifact_priority(layer: str, topology: Mapping[str, object] | None = None) -> int:
    """Return a stable top-down sort key for an artifact layer."""

    if layer in REPAIR_PRIORITY:
        return REPAIR_PRIORITY.index(layer)
    if topology and layer in topology:
        return len(REPAIR_PRIORITY) - 1
    return len(REPAIR_PRIORITY)


__all__ = [
    "ARTIFACT_TOPOLOGY",
    "REPAIR_PRIORITY",
    "REPAIR_PRIORITY_RULE",
    "ArtifactNode",
    "artifact_priority",
]
