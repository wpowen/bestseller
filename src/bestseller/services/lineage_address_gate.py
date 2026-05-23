from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from bestseller.domain.honorific_system import HonorificSystem
from bestseller.domain.lineage_system import LineageKernel


@dataclass(frozen=True)
class LineageAddressFinding:
    code: str
    severity: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


def expected_lineage_address(
    kernel: LineageKernel,
    honorifics: HonorificSystem,
    *,
    speaker_id: UUID,
    listener_id: UUID,
) -> str | None:
    nodes = {
        node.person_id: node
        for school_nodes in kernel.schools.values()
        for node in school_nodes
    }
    speaker = nodes.get(speaker_id)
    listener = nodes.get(listener_id)
    if speaker is None or listener is None or speaker.school != listener.school:
        return None
    if speaker.generation > listener.generation:
        return honorifics.lookup("disciple", "elder") or "师叔"
    if speaker.generation < listener.generation:
        return honorifics.lookup("elder", "disciple") or "小辈"
    return honorifics.lookup("disciple", "disciple") or "同门"

