"""Append and read chapter continuity entries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ContinuityEntry:
    chapter_no: int
    new_characters: tuple[str, ...] = ()
    new_objects: tuple[str, ...] = ()
    revealed_ids: tuple[str, ...] = ()
    demonstrated_rules: tuple[str, ...] = ()
    end_state: Mapping[str, Any] = field(default_factory=dict)
    closing_hook: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_no": self.chapter_no,
            "new_characters": list(self.new_characters),
            "new_objects": list(self.new_objects),
            "revealed_ids": list(self.revealed_ids),
            "demonstrated_rules": list(self.demonstrated_rules),
            "end_state": dict(self.end_state),
            "closing_hook": self.closing_hook,
        }


def append_continuity_entry(path: Path, entry: ContinuityEntry) -> dict[str, Any]:
    """Append or replace one chapter entry in ``continuity-ledger.yaml``."""

    payload = _read_ledger(path)
    chapters = payload.setdefault("chapters", [])
    if not isinstance(chapters, list):
        chapters = []
        payload["chapters"] = chapters
    chapters[:] = [
        item
        for item in chapters
        if not (isinstance(item, dict) and item.get("chapter_no") == entry.chapter_no)
    ]
    chapters.append(entry.to_dict())
    chapters.sort(
        key=lambda item: int(item.get("chapter_no") or 0)
        if isinstance(item, dict)
        else 0
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return payload


def load_recent_continuity_entries(
    path: Path,
    *,
    chapter_no: int,
    window: int = 2,
) -> tuple[dict[str, Any], ...]:
    """Load the previous ``window`` continuity entries before ``chapter_no``."""

    payload = _read_ledger(path)
    chapters = payload.get("chapters")
    if not isinstance(chapters, list):
        return ()
    selected = [
        dict(item)
        for item in chapters
        if isinstance(item, dict)
        and chapter_no - window <= int(item.get("chapter_no") or 0) < chapter_no
    ]
    return tuple(sorted(selected, key=lambda item: int(item.get("chapter_no") or 0)))


def render_continuity_prompt_block(entries: Sequence[Mapping[str, Any]]) -> str:
    if not entries:
        return ""
    lines = ["=== Continuity ledger: recent chapter end states ==="]
    for entry in entries:
        lines.append(f"- ch{entry.get('chapter_no')}: {entry.get('closing_hook') or ''}")
        end_state = entry.get("end_state")
        if isinstance(end_state, Mapping) and end_state:
            rendered = yaml.safe_dump(dict(end_state), allow_unicode=True).strip()
            lines.append(f"  end_state: {rendered}")
    return "\n".join(lines)


def _read_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "continuity-ledger.v1", "chapters": []}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {"schema_version": "continuity-ledger.v1", "chapters": []}
    if isinstance(payload, dict):
        return payload
    return {"schema_version": "continuity-ledger.v1", "chapters": []}


__all__ = [
    "ContinuityEntry",
    "append_continuity_entry",
    "load_recent_continuity_entries",
    "render_continuity_prompt_block",
]
