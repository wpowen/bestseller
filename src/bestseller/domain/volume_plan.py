from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VolumeMilestone:
    chapter_range: tuple[int, int]
    milestone_label: str
    required_evidence: tuple[str, ...]
    reveals_unlocked: tuple[str, ...] = ()
    character_state_promises: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        start, end = _coerce_range(self.chapter_range)
        object.__setattr__(self, "chapter_range", (start, end))
        object.__setattr__(self, "required_evidence", _clean_tuple(self.required_evidence))
        object.__setattr__(self, "reveals_unlocked", _clean_tuple(self.reveals_unlocked))
        object.__setattr__(
            self,
            "character_state_promises",
            _clean_tuple(self.character_state_promises),
        )
        if len(self.milestone_label.strip()) < 12:
            raise ValueError("milestone_label must be at least 12 characters")
        if not self.required_evidence:
            raise ValueError("required_evidence must contain at least one item")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> VolumeMilestone:
        return cls(
            chapter_range=_parse_range(payload.get("chapter_range")),
            milestone_label=str(payload.get("milestone_label") or payload.get("label") or ""),
            required_evidence=_string_tuple(payload.get("required_evidence")),
            reveals_unlocked=_string_tuple(payload.get("reveals_unlocked")),
            character_state_promises=_string_tuple(
                payload.get("character_state_promises")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_range": [self.chapter_range[0], self.chapter_range[1]],
            "milestone_label": self.milestone_label,
            "required_evidence": list(self.required_evidence),
            "reveals_unlocked": list(self.reveals_unlocked),
            "character_state_promises": list(self.character_state_promises),
        }


@dataclass(frozen=True)
class VolumePlanV2:
    volume_no: int
    chapter_range: tuple[int, int]
    milestones: tuple[VolumeMilestone, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "chapter_range", _coerce_range(self.chapter_range))
        object.__setattr__(self, "milestones", tuple(self.milestones))
        if self.volume_no < 1:
            raise ValueError("volume_no must be positive")
        if len(self.milestones) < 5:
            raise ValueError("each volume must contain at least five milestones")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> VolumePlanV2:
        raw_milestones = payload.get("milestones") or ()
        if not isinstance(raw_milestones, Sequence) or isinstance(raw_milestones, str):
            raw_milestones = ()
        return cls(
            volume_no=int(payload.get("volume_no") or payload.get("volume") or 0),
            chapter_range=_parse_range(payload.get("chapter_range") or payload.get("chapters")),
            milestones=tuple(
                VolumeMilestone.from_mapping(item)
                for item in raw_milestones
                if isinstance(item, Mapping)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "volume_no": self.volume_no,
            "chapter_range": [self.chapter_range[0], self.chapter_range[1]],
            "milestones": [milestone.to_dict() for milestone in self.milestones],
        }


def load_volume_plans_v2(payload: Mapping[str, Any]) -> tuple[VolumePlanV2, ...]:
    raw_volumes = payload.get("volumes") or payload.get("volume_plans") or ()
    if isinstance(raw_volumes, Mapping):
        raw_volumes = raw_volumes.values()
    if not isinstance(raw_volumes, Sequence) or isinstance(raw_volumes, str):
        return ()
    return tuple(
        VolumePlanV2.from_mapping(item) for item in raw_volumes if isinstance(item, Mapping)
    )


def _parse_range(value: object) -> tuple[int, int]:
    if isinstance(value, Sequence) and not isinstance(value, str):
        items = list(value)
        if len(items) >= 2:
            return _coerce_range((int(items[0]), int(items[1])))
    if isinstance(value, str):
        cleaned = value.strip().replace("第", "").replace("章", "")
        for sep in ("-", "\u2013", "\u2014", "~", "至", ".."):
            if sep in cleaned:
                left, _, right = cleaned.partition(sep)
                return _coerce_range((int(left.strip()), int(right.strip())))
    raise ValueError(f"invalid chapter range: {value!r}")


def _coerce_range(value: tuple[int, int]) -> tuple[int, int]:
    start, end = int(value[0]), int(value[1])
    if start < 1 or end < start:
        raise ValueError("chapter_range must be positive and ordered")
    return (start, end)


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Sequence):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _clean_tuple(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(value).strip() for value in values if str(value).strip())
