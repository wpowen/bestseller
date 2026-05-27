from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.domain.workflow import ChapterOutlineInput


@dataclass(frozen=True)
class RevealScheduleItem:
    reveal_id: str
    earliest_chapter: int
    tokens: tuple[str, ...] = ()


def load_reveal_schedule(path: str | Path) -> dict[str, RevealScheduleItem]:
    loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        return {}
    raw_reveals = loaded.get("reveals") or ()
    if isinstance(raw_reveals, Mapping):
        raw_reveals = raw_reveals.values()
    if not isinstance(raw_reveals, Sequence) or isinstance(raw_reveals, str):
        return {}
    schedule: dict[str, RevealScheduleItem] = {}
    for item in raw_reveals:
        if not isinstance(item, Mapping):
            continue
        reveal_id = str(item.get("id") or item.get("reveal_id") or "").strip()
        if not reveal_id:
            continue
        schedule[reveal_id] = RevealScheduleItem(
            reveal_id=reveal_id,
            earliest_chapter=int(item.get("earliest_chapter") or 1),
            tokens=tuple(str(token) for token in item.get("tokens") or ()),
        )
    return schedule


def evaluate_outline_reveal_alignment(
    chapter_outline: ChapterOutlineInput | Mapping[str, Any],
    *,
    reveal_schedule: Mapping[str, RevealScheduleItem] | Mapping[str, Mapping[str, Any]],
) -> GateVerdict:
    chapter_no = _chapter_no(chapter_outline)
    key_reveals = _key_reveals(chapter_outline)
    schedule = _coerce_schedule(reveal_schedule)
    findings: list[GateFinding] = []

    for reveal_id in key_reveals:
        if reveal_id == "__no_reveal__":
            continue
        schedule_item = schedule.get(reveal_id)
        if schedule_item is None:
            findings.append(
                GateFinding(
                    code="REVEAL_ID_UNKNOWN",
                    severity="critical",
                    message=f"chapter {chapter_no or '?'} references unknown reveal id {reveal_id}",
                    path=f"chapter:{chapter_no}:key_reveals" if chapter_no else "key_reveals",
                    repair_action=(
                        "use an id from reveal-schedule.yaml or explicitly set "
                        "__no_reveal__"
                    ),
                )
            )
            continue
        if chapter_no and chapter_no < schedule_item.earliest_chapter:
            findings.append(
                GateFinding(
                    code="REVEAL_TOO_EARLY_IN_OUTLINE",
                    severity="critical",
                    message=(
                        f"chapter {chapter_no} unlocks {reveal_id} before "
                        f"earliest chapter {schedule_item.earliest_chapter}"
                    ),
                    path=f"chapter:{chapter_no}:key_reveals:{reveal_id}",
                    repair_action=(
                        "move this reveal to its scheduled chapter or downgrade "
                        "it to a non-reveal clue"
                    ),
                )
            )

    return GateVerdict(
        gate_name="outline_reveal_alignment_gate",
        verdict="blocked" if findings else "pass",
        coverage=1.0 if not findings else max(0.0, 1.0 - len(findings) / max(1, len(key_reveals))),
        findings=tuple(findings),
        metrics={
            "chapter_no": chapter_no,
            "key_reveals": list(key_reveals),
            "known_reveal_count": len(schedule),
        },
    )


def _coerce_schedule(
    reveal_schedule: Mapping[str, RevealScheduleItem] | Mapping[str, Mapping[str, Any]],
) -> dict[str, RevealScheduleItem]:
    schedule: dict[str, RevealScheduleItem] = {}
    for reveal_id, item in reveal_schedule.items():
        if isinstance(item, RevealScheduleItem):
            schedule[reveal_id] = item
        elif isinstance(item, Mapping):
            schedule[reveal_id] = RevealScheduleItem(
                reveal_id=str(item.get("id") or item.get("reveal_id") or reveal_id),
                earliest_chapter=int(item.get("earliest_chapter") or 1),
                tokens=tuple(str(token) for token in item.get("tokens") or ()),
            )
    return schedule


def _chapter_no(chapter_outline: ChapterOutlineInput | Mapping[str, Any]) -> int:
    raw = (
        chapter_outline.get("chapter_number") or chapter_outline.get("chapter_no")
        if isinstance(chapter_outline, Mapping)
        else getattr(chapter_outline, "chapter_number", 0)
    )
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _key_reveals(chapter_outline: ChapterOutlineInput | Mapping[str, Any]) -> tuple[str, ...]:
    raw = (
        chapter_outline.get("key_reveals", ())
        if isinstance(chapter_outline, Mapping)
        else getattr(chapter_outline, "key_reveals", ())
    )
    if isinstance(raw, str):
        return (raw.strip(),) if raw.strip() else ()
    if isinstance(raw, Sequence):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    return ()
