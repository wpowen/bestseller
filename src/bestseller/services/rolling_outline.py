"""Deterministic primitives for rolling, just-in-time chapter planning.

The full-book macro plan is an immutable map of chapter slots.  A rolling
outline is a bounded view over that map plus the state needed to ask a planner
for the next detail batch.  This module deliberately contains no LLM calls or
persistence; callers may store the returned metadata as an artifact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
import hashlib
import json
from types import MappingProxyType
from typing import Any

WINDOW_MIN = 6
WINDOW_MAX = 10
BATCH_MIN = 3
BATCH_MAX = 5
PROMOTION_STATUSES = frozenset({"planned", "needs_replan", "approved"})


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rolling_window_schedule_hash(schedule: Sequence[Mapping[str, Any]]) -> str:
    """Hash the execution-window schedule that advances a rolling plan."""

    return _canonical_hash([dict(item) for item in schedule])


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_deep_freeze(item) for item in value)
    return value


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_deep_thaw(item) for item in value]
    return value


def _mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return _deep_freeze(value)


@dataclass(frozen=True, slots=True)
class MacroChapterSlot:
    """One immutable full-book chapter anchor."""

    chapter_number: int
    anchor: str
    metadata: Mapping[str, Any] = dataclass_field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_number": self.chapter_number,
            "anchor": self.anchor,
            "metadata": _deep_thaw(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class MacroPlan:
    """Immutable full-book macro chapter slots and their content hash."""

    slots: tuple[MacroChapterSlot, ...]
    macro_plan_hash: str

    @property
    def total_chapters(self) -> int:
        return len(self.slots)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slots": [slot.to_dict() for slot in self.slots],
            "macro_plan_hash": self.macro_plan_hash,
        }


@dataclass(frozen=True, slots=True)
class RollingOutlinePlan:
    """A deterministic rolling window request/plan artifact."""

    window_start: int
    window_end: int
    batch_size: int
    detail_slots: tuple[MacroChapterSlot, ...]
    current_state_snapshot: Mapping[str, Any]
    next_macro_anchor: Mapping[str, Any] | str
    source_snapshot_hash: str
    current_state_hash: str
    macro_plan_hash: str
    previous_state_hash: str
    status: str = "planned"

    def __post_init__(self) -> None:
        object.__setattr__(self, "current_state_snapshot", _mapping(self.current_state_snapshot))
        if isinstance(self.next_macro_anchor, Mapping):
            object.__setattr__(self, "next_macro_anchor", _mapping(self.next_macro_anchor))

    @property
    def window_size(self) -> int:
        return self.window_end - self.window_start + 1

    @property
    def plan_hash(self) -> str:
        return _canonical_hash(self.to_dict(include_plan_hash=False))

    def to_dict(self, *, include_plan_hash: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {
            "window_start": self.window_start,
            "window_end": self.window_end,
            "batch_size": self.batch_size,
            "detail_slots": [slot.to_dict() for slot in self.detail_slots],
            "current_state_snapshot": _deep_thaw(self.current_state_snapshot),
            "next_macro_anchor": _deep_thaw(self.next_macro_anchor),
            "source_snapshot_hash": self.source_snapshot_hash,
            "current_state_hash": self.current_state_hash,
            "macro_plan_hash": self.macro_plan_hash,
            "previous_state_hash": self.previous_state_hash,
            "status": self.status,
        }
        if include_plan_hash:
            value["plan_hash"] = self.plan_hash
        return value


def build_macro_plan(slots: Sequence[MacroChapterSlot | Mapping[str, Any]]) -> MacroPlan:
    """Normalize and freeze contiguous full-book macro slots."""
    normalized: list[MacroChapterSlot] = []
    for index, item in enumerate(slots, start=1):
        if isinstance(item, MacroChapterSlot):
            slot = item
        else:
            if not isinstance(item, Mapping):
                raise TypeError("macro slot must be a mapping or MacroChapterSlot")
            slot = MacroChapterSlot(
                chapter_number=int(item.get("chapter_number", index)),
                anchor=str(item.get("anchor", "")).strip(),
                metadata=_mapping(item.get("metadata", {})),
            )
        if slot.chapter_number != index:
            raise ValueError("macro chapter slots must be contiguous from chapter 1")
        if not slot.anchor:
            raise ValueError(f"macro chapter {index} requires a non-empty anchor")
        normalized.append(slot)
    if not normalized:
        raise ValueError("macro plan requires at least one chapter slot")
    frozen = tuple(normalized)
    return MacroPlan(
        slots=frozen,
        macro_plan_hash=_canonical_hash([slot.to_dict() for slot in frozen]),
    )


def build_rolling_outline_plan(
    macro_plan: MacroPlan,
    *,
    current_state_snapshot: Mapping[str, Any],
    next_macro_anchor: Mapping[str, Any] | str,
    source_snapshot_hash: str,
    window_start: int | None = None,
    window_end: int | None = None,
    window_size: int = WINDOW_MIN,
    batch_size: int = 4,
    confirmed_chapters: Sequence[int] = (),
    previous_state_snapshot: Mapping[str, Any] | None = None,
) -> RollingOutlinePlan:
    """Build a bounded detail window without rewriting confirmed/past chapters."""
    if not isinstance(current_state_snapshot, Mapping):
        raise TypeError("current_state_snapshot must be a mapping")
    if not str(source_snapshot_hash or "").strip():
        raise ValueError("source_snapshot_hash is required")
    if not BATCH_MIN <= batch_size <= BATCH_MAX:
        raise ValueError(f"batch_size must be between {BATCH_MIN} and {BATCH_MAX}")
    confirmed = {int(chapter) for chapter in confirmed_chapters}
    state_chapter = current_state_snapshot.get("current_chapter", 0)
    try:
        past_chapter = int(state_chapter or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("current_state_snapshot.current_chapter must be an integer") from exc
    if past_chapter < 0:
        raise ValueError("current_state_snapshot.current_chapter cannot be negative")
    baseline_chapter = max((*confirmed, past_chapter), default=0)
    if window_start is None:
        window_start = baseline_chapter + 1
    if window_end is None:
        window_end = window_start + window_size - 1
    minimum_window = min(WINDOW_MIN, macro_plan.total_chapters)
    final_partial_window = (
        1 <= window_size < minimum_window
        and window_end == macro_plan.total_chapters
        and window_start > 1
    )
    if not minimum_window <= window_size <= WINDOW_MAX and not final_partial_window:
        raise ValueError(
            f"window_size must be between {minimum_window} and {WINDOW_MAX} "
            "unless this is the final partial window"
        )
    if window_end - window_start + 1 != window_size:
        raise ValueError("window_start/window_end do not match window_size")
    if window_start < 1 or window_end > macro_plan.total_chapters:
        raise ValueError("rolling detail window is outside the macro plan bounds")
    if any(chapter < 1 or chapter > macro_plan.total_chapters for chapter in confirmed):
        raise ValueError("confirmed chapter is outside the macro plan bounds")
    if any(chapter in confirmed for chapter in range(window_start, window_end + 1)):
        raise ValueError("rolling window cannot rewrite confirmed chapters")
    if window_start <= baseline_chapter:
        raise ValueError("rolling window cannot include past/confirmed chapters")
    previous = previous_state_snapshot
    if previous is None:
        previous = current_state_snapshot.get("previous_state", {})
    previous_hash = current_state_snapshot.get("previous_state_hash")
    if not isinstance(previous_hash, str) or not previous_hash:
        previous_hash = _canonical_hash(previous)
    detail = macro_plan.slots[window_start - 1 : window_end]
    return RollingOutlinePlan(
        window_start=window_start,
        window_end=window_end,
        batch_size=batch_size,
        detail_slots=detail,
        current_state_snapshot=_mapping(current_state_snapshot),
        next_macro_anchor=next_macro_anchor,
        source_snapshot_hash=str(source_snapshot_hash),
        current_state_hash=_canonical_hash(current_state_snapshot),
        macro_plan_hash=macro_plan.macro_plan_hash,
        previous_state_hash=previous_hash,
    )


def promote_rolling_outline(plan: RollingOutlinePlan, status: str) -> RollingOutlinePlan:
    """Return a new plan with explicit promotion status; the source remains frozen."""
    if status not in PROMOTION_STATUSES:
        raise ValueError(f"invalid rolling outline status: {status!r}")
    return replace(plan, status=status)


def load_rolling_outline_plan(
    raw_macro: Mapping[str, Any],
    raw_plan: Mapping[str, Any],
    *,
    source_snapshot_hash: str | None = None,
    require_approved: bool = True,
) -> tuple[MacroPlan, RollingOutlinePlan]:
    """Load and integrity-check persisted macro/rolling artifacts.

    Persisted plans are execution authority.  A missing field, silently dropped
    slot, stale hash, or mismatched detail slice therefore fails closed instead
    of becoming a shorter or wider writing plan.
    """

    raw_slots = raw_macro.get("slots")
    if not isinstance(raw_slots, Sequence) or isinstance(raw_slots, (str, bytes)):
        raise ValueError("rolling macro plan slots must be an array")
    if not raw_slots or any(not isinstance(slot, Mapping) for slot in raw_slots):
        raise ValueError("every rolling macro slot must be an object")
    macro_plan = build_macro_plan(raw_slots)  # type: ignore[arg-type]
    stored_macro_hash = str(raw_macro.get("macro_plan_hash") or "")
    if stored_macro_hash != macro_plan.macro_plan_hash:
        raise ValueError("rolling macro plan hash mismatch")

    required_fields = {
        "window_start",
        "window_end",
        "batch_size",
        "detail_slots",
        "current_state_snapshot",
        "next_macro_anchor",
        "source_snapshot_hash",
        "current_state_hash",
        "macro_plan_hash",
        "previous_state_hash",
        "status",
        "plan_hash",
    }
    missing = sorted(required_fields - set(raw_plan))
    if missing:
        raise ValueError(f"rolling outline plan is missing fields: {', '.join(missing)}")
    if require_approved and raw_plan.get("status") != "approved":
        raise ValueError("rolling outline plan is not approved")
    if str(raw_plan.get("macro_plan_hash") or "") != macro_plan.macro_plan_hash:
        raise ValueError("rolling outline macro hash mismatch")
    if source_snapshot_hash is not None and (
        str(raw_plan.get("source_snapshot_hash") or "") != source_snapshot_hash
    ):
        raise ValueError("rolling outline source snapshot hash mismatch")

    state = raw_plan.get("current_state_snapshot")
    if not isinstance(state, Mapping):
        raise ValueError("rolling outline current state must be an object")
    if str(raw_plan.get("current_state_hash") or "") != _canonical_hash(state):
        raise ValueError("rolling outline current state hash mismatch")
    if "previous_state" in state and str(raw_plan.get("previous_state_hash") or "") != (
        _canonical_hash(state.get("previous_state"))
    ):
        raise ValueError("rolling outline previous state hash mismatch")
    details = raw_plan.get("detail_slots")
    if not isinstance(details, Sequence) or isinstance(details, (str, bytes)):
        raise ValueError("rolling outline detail slots must be an array")
    if any(not isinstance(slot, Mapping) for slot in details):
        raise ValueError("every rolling outline detail slot must be an object")

    try:
        window_start = int(raw_plan["window_start"])
        window_end = int(raw_plan["window_end"])
        batch_size = int(raw_plan["batch_size"])
    except (TypeError, ValueError) as exc:
        raise ValueError("rolling outline bounds and batch size must be integers") from exc
    if window_start < 1 or window_end > macro_plan.total_chapters or window_end < window_start:
        raise ValueError("rolling outline window is outside macro plan bounds")
    window_size = window_end - window_start + 1
    minimum_window = min(WINDOW_MIN, macro_plan.total_chapters)
    final_partial = (
        1 <= window_size < minimum_window
        and window_end == macro_plan.total_chapters
        and window_start > 1
    )
    if not minimum_window <= window_size <= WINDOW_MAX and not final_partial:
        raise ValueError("rolling outline window size is invalid")
    if not BATCH_MIN <= batch_size <= BATCH_MAX:
        raise ValueError("rolling outline batch size is invalid")

    expected_details = macro_plan.slots[window_start - 1 : window_end]
    normalized_details = tuple(
        MacroChapterSlot(
            chapter_number=int(slot.get("chapter_number", 0)),
            anchor=str(slot.get("anchor") or "").strip(),
            metadata=slot.get("metadata") if isinstance(slot.get("metadata"), Mapping) else {},
        )
        for slot in details  # type: ignore[union-attr]
    )
    if [slot.to_dict() for slot in normalized_details] != [
        slot.to_dict() for slot in expected_details
    ]:
        raise ValueError("rolling outline detail slots do not match the macro window")
    expected_next_anchor: Mapping[str, Any] | str = (
        macro_plan.slots[window_end].to_dict()
        if window_end < macro_plan.total_chapters
        else "book_complete"
    )
    if _deep_thaw(raw_plan.get("next_macro_anchor")) != _deep_thaw(expected_next_anchor):
        raise ValueError("rolling outline next macro anchor mismatch")

    plan = RollingOutlinePlan(
        window_start=window_start,
        window_end=window_end,
        batch_size=batch_size,
        detail_slots=normalized_details,
        current_state_snapshot=state,
        next_macro_anchor=raw_plan.get("next_macro_anchor"),
        source_snapshot_hash=str(raw_plan.get("source_snapshot_hash") or ""),
        current_state_hash=str(raw_plan.get("current_state_hash") or ""),
        macro_plan_hash=str(raw_plan.get("macro_plan_hash") or ""),
        previous_state_hash=str(raw_plan.get("previous_state_hash") or ""),
        status=str(raw_plan.get("status") or ""),
    )
    if str(raw_plan.get("plan_hash") or "") != plan.plan_hash:
        raise ValueError("rolling outline plan hash mismatch")
    return macro_plan, plan


# Descriptive alias for callers that model this artifact as a request.
build_rolling_outline_request = build_rolling_outline_plan
