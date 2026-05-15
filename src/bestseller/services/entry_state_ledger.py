from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bestseller.domain.entry_system import (
    EntryDefinition,
    EntryEvent,
    EntryMigrationChange,
    EntryMigrationReport,
    EntryRegistry,
    EntryStateSnapshot,
)


@dataclass(frozen=True, slots=True)
class EntryStateFinding:
    code: str
    severity: str
    message: str
    path: str
    entry_id: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "entry_id": self.entry_id,
        }


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _event(raw: EntryEvent | Mapping[str, object]) -> EntryEvent:
    if isinstance(raw, EntryEvent):
        return raw
    return EntryEvent.model_validate(raw)


def _initial_state(entry: EntryDefinition) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "name": entry.name,
        "type": entry.type,
        "tier": str(entry.tier),
        "state": entry.visibility or "planned",
        "owner": entry.owner,
        "current_grade": entry.current_grade,
        "last_event_type": None,
        "last_event_chapter": None,
        "last_trigger": None,
        "last_cost_paid": None,
        "continuity_notes": [],
    }


def apply_entry_events(
    registry: EntryRegistry | Mapping[str, object],
    events: Sequence[EntryEvent | Mapping[str, object]],
) -> EntryStateSnapshot:
    """Fold append-only entry events into a current state snapshot."""

    if isinstance(registry, Mapping):
        registry = EntryRegistry.model_validate(registry)
    states = {entry.entry_id: _initial_state(entry) for entry in registry.entries}
    current_chapter = 0
    for raw in sorted((_event(item) for item in events), key=lambda item: item.chapter_number):
        current_chapter = max(current_chapter, raw.chapter_number)
        state = states.setdefault(
            raw.entry_id,
            {
                "entry_id": raw.entry_id,
                "name": raw.entry_id,
                "type": "unknown",
                "tier": "supporting",
                "state": "unknown",
                "owner": None,
                "current_grade": None,
                "last_event_type": None,
                "last_event_chapter": None,
                "last_trigger": None,
                "last_cost_paid": None,
                "continuity_notes": [],
            },
        )
        if raw.to_state:
            state["state"] = raw.to_state
        elif raw.event_type in {"acquired", "learned", "bonded"}:
            state["state"] = "owned" if raw.event_type == "acquired" else str(raw.event_type)
        elif raw.event_type in {"lost", "spent", "sealed", "paid_off"}:
            state["state"] = str(raw.event_type)
        if raw.owner_after:
            state["owner"] = raw.owner_after
        if raw.visibility_after:
            state["visibility"] = raw.visibility_after
        if raw.to_grade:
            state["current_grade"] = raw.to_grade
        if raw.cost_paid:
            state["last_cost_paid"] = raw.cost_paid
        if raw.continuity_note:
            notes = list(state.get("continuity_notes") or [])
            notes.append(raw.continuity_note)
            state["continuity_notes"] = notes[-8:]
        state["last_event_type"] = str(raw.event_type)
        state["last_event_chapter"] = raw.chapter_number
        state["last_trigger"] = raw.trigger
    stale_ids = tuple(f.entry_id for f in detect_stale_entries(registry, events, current_chapter))
    return EntryStateSnapshot(
        current_chapter=current_chapter,
        entry_states=states,
        stale_entry_ids=tuple(item for item in stale_ids if item),
    )


def current_entry_state(
    snapshot: EntryStateSnapshot | Mapping[str, object],
    entry_id: str,
) -> dict[str, object]:
    if isinstance(snapshot, EntryStateSnapshot):
        return dict(snapshot.entry_states.get(entry_id) or {})
    return _as_mapping(_as_mapping(snapshot).get("entry_states")).get(entry_id, {})


def detect_stale_entries(
    registry: EntryRegistry | Mapping[str, object],
    events: Sequence[EntryEvent | Mapping[str, object]],
    current_chapter: int,
    max_gap: int = 12,
) -> tuple[EntryStateFinding, ...]:
    """Find major entries that have not changed state for too many chapters."""

    if isinstance(registry, Mapping):
        registry = EntryRegistry.model_validate(registry)
    last_seen: dict[str, int] = {}
    for raw in events:
        event = _event(raw)
        last_seen[event.entry_id] = max(last_seen.get(event.entry_id, 0), event.chapter_number)
    findings: list[EntryStateFinding] = []
    for entry in registry.entries:
        if not entry.is_major:
            continue
        last = last_seen.get(entry.entry_id, 0)
        if current_chapter - last > max_gap:
            findings.append(
                EntryStateFinding(
                    code="stale_major_entry",
                    severity="warning",
                    message="Major entry has no recent state change.",
                    path=f"entries.{entry.entry_id}",
                    entry_id=entry.entry_id,
                )
            )
    return tuple(findings)


def _changed_tuple(old: EntryDefinition, new: EntryDefinition, field: str) -> bool:
    return tuple(getattr(old, field) or ()) != tuple(getattr(new, field) or ())


def build_entry_migration_report(
    old_registry: EntryRegistry | Mapping[str, object],
    new_registry: EntryRegistry | Mapping[str, object],
    reason: str,
) -> EntryMigrationReport:
    """Compare two registries and describe changes that may require prose repair."""

    if isinstance(old_registry, Mapping):
        old_registry = EntryRegistry.model_validate(old_registry)
    if isinstance(new_registry, Mapping):
        new_registry = EntryRegistry.model_validate(new_registry)
    old_by_id = old_registry.by_id
    changes: list[EntryMigrationChange] = []
    repairs: list[str] = []
    for entry in new_registry.entries:
        old = old_by_id.get(entry.entry_id)
        if old is None:
            changes.append(
                EntryMigrationChange(
                    entry_id=entry.entry_id,
                    change_type="entry_added",
                    old=None,
                    new=entry.model_dump(mode="json"),
                    requires_story_patch=False,
                )
            )
            continue
        for field in ("limits", "costs", "capabilities", "forbidden_uses"):
            if not _changed_tuple(old, entry, field):
                continue
            change_type = f"{field}_changed"
            changes.append(
                EntryMigrationChange(
                    entry_id=entry.entry_id,
                    change_type=change_type,
                    old=list(getattr(old, field) or ()),
                    new=list(getattr(entry, field) or ()),
                    requires_story_patch=field in {"limits", "costs", "capabilities"},
                )
            )
            if field in {"limits", "costs", "capabilities"}:
                repairs.append(f"{entry.entry_id}:{change_type}")
    return EntryMigrationReport(
        migration_id="entry-registry-migration",
        reason=reason,
        changes=tuple(changes),
        required_repairs=tuple(repairs),
    )


def render_entry_state_ledger_block(
    snapshot: EntryStateSnapshot | Mapping[str, object] | None,
    stale_findings: Sequence[EntryStateFinding | Mapping[str, object]] = (),
    *,
    max_entries: int = 12,
) -> str:
    if snapshot is None:
        return ""
    if isinstance(snapshot, Mapping):
        snapshot = EntryStateSnapshot.model_validate(snapshot)
    if not snapshot.entry_states and not stale_findings:
        return ""
    lines = ["【词条状态账本】"]
    for entry_id, state in list(snapshot.entry_states.items())[:max_entries]:
        parts = [
            entry_id,
            f"state={_text(state.get('state')) or 'unknown'}",
        ]
        grade = _text(state.get("current_grade"))
        if grade:
            parts.append(f"grade={grade}")
        owner = _text(state.get("owner"))
        if owner:
            parts.append(f"owner={owner}")
        trigger = _text(state.get("last_trigger"))
        if trigger:
            parts.append(f"trigger={trigger}")
        cost = _text(state.get("last_cost_paid"))
        if cost:
            parts.append(f"cost={cost}")
        notes = state.get("continuity_notes")
        if isinstance(notes, list) and notes:
            parts.append(f"note={_text(notes[-1])}")
        lines.append("  - " + " | ".join(parts))
    for raw in stale_findings:
        finding = raw if isinstance(raw, EntryStateFinding) else EntryStateFinding(**raw)
        lines.append(f"  - stale:{finding.entry_id or finding.path} | {finding.message}")
    return "\n".join(lines)


__all__ = [
    "EntryStateFinding",
    "apply_entry_events",
    "build_entry_migration_report",
    "current_entry_state",
    "detect_stale_entries",
    "render_entry_state_ledger_block",
]
