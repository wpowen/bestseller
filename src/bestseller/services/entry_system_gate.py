from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bestseller.domain.entry_system import EntryRegistry, EntrySystemKernel
from bestseller.services.entry_system_kernel import validate_entry_system_kernel


@dataclass(frozen=True, slots=True)
class EntrySystemGateFinding:
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


@dataclass(frozen=True, slots=True)
class EntrySystemGateReport:
    passed: bool
    findings: tuple[EntrySystemGateFinding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "findings": [finding.to_dict() for finding in self.findings],
        }


_POWER_CHANGING_EVENTS = {
    "acquired",
    "learned",
    "bonded",
    "used",
    "upgraded",
    "restored",
}

_VAGUE_REWARD_TEXTS = {
    "奖励",
    "变强",
    "获得好处",
    "有所收获",
    "提升实力",
    "reward",
    "upgrade",
    "gets stronger",
    "benefit",
}


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _finding(
    code: str,
    message: str,
    path: str,
    *,
    severity: str = "error",
    entry_id: str | None = None,
) -> EntrySystemGateFinding:
    return EntrySystemGateFinding(
        code=code,
        severity=severity,
        message=message,
        path=path,
        entry_id=entry_id,
    )


def validate_kernel(
    kernel: EntrySystemKernel | Mapping[str, object],
) -> tuple[EntrySystemGateFinding, ...]:
    if isinstance(kernel, Mapping):
        kernel = EntrySystemKernel.model_validate(kernel)
    findings: list[EntrySystemGateFinding] = []
    for item in validate_entry_system_kernel(kernel):
        findings.append(
            _finding(
                item.code,
                item.message,
                item.path,
                severity=item.severity,
            )
        )
    return tuple(findings)


def validate_registry(
    kernel: EntrySystemKernel | Mapping[str, object],
    registry: EntryRegistry | Mapping[str, object],
) -> tuple[EntrySystemGateFinding, ...]:
    if isinstance(kernel, Mapping):
        kernel = EntrySystemKernel.model_validate(kernel)
    if isinstance(registry, Mapping):
        registry = EntryRegistry.model_validate(registry)
    valid_types = kernel.taxonomy_by_type
    findings: list[EntrySystemGateFinding] = []
    major_role_owner: dict[str, str] = {}
    for index, entry in enumerate(registry.entries):
        path = f"entries[{index}]"
        if entry.taxonomy_ref not in valid_types:
            findings.append(
                _finding(
                    "entry_taxonomy_unknown",
                    "Entry references an unknown taxonomy type.",
                    f"{path}.taxonomy_ref",
                    entry_id=entry.entry_id,
                )
            )
        if entry.is_major and not entry.has_limits:
            findings.append(
                _finding(
                    "entry_limit_missing",
                    "Major entry needs visible limits, costs, or forbidden uses.",
                    f"{path}.limits",
                    entry_id=entry.entry_id,
                )
            )
        if entry.is_major:
            for role in entry.narrative_roles:
                existing = major_role_owner.get(role)
                if existing and existing != entry.entry_id:
                    findings.append(
                        _finding(
                            "duplicate_major_entry_role",
                            "Two major entries carry the same narrative role.",
                            f"{path}.narrative_roles",
                            entry_id=entry.entry_id,
                        )
                    )
                major_role_owner.setdefault(role, entry.entry_id)
    return tuple(findings)


def validate_entry_events(
    registry: EntryRegistry | Mapping[str, object],
    events: Sequence[Mapping[str, object] | object],
) -> tuple[EntrySystemGateFinding, ...]:
    if isinstance(registry, Mapping):
        registry = EntryRegistry.model_validate(registry)
    known_ids = set(registry.by_id)
    findings: list[EntrySystemGateFinding] = []
    for index, raw in enumerate(events):
        event = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else _as_mapping(raw)
        path = f"entry_events[{index}]"
        entry_id = _text(event.get("entry_id"))
        event_type = _text(event.get("event_type") or event.get("type") or event.get("kind"))
        trigger = _text(event.get("trigger") or event.get("cause") or event.get("reason"))
        if not entry_id:
            findings.append(_finding("entry_event_entry_id_missing", "Missing entry_id.", path))
        elif entry_id not in known_ids:
            findings.append(
                _finding(
                    "entry_event_unknown_entry",
                    "Entry event references an unknown entry.",
                    f"{path}.entry_id",
                    entry_id=entry_id,
                )
            )
        if not event_type:
            findings.append(
                _finding(
                    "entry_event_type_missing",
                    "Entry event needs event_type.",
                    f"{path}.event_type",
                    entry_id=entry_id or None,
                )
            )
        if event_type in _POWER_CHANGING_EVENTS and not trigger:
            findings.append(
                _finding(
                    "entry_event_trigger_missing",
                    "Power-changing entry event needs a trigger.",
                    f"{path}.trigger",
                    entry_id=entry_id or None,
                )
            )
        if event_type in {"used", "upgraded", "restored"} and not _text(
            event.get("cost_paid") or event.get("cost") or event.get("price")
        ):
            findings.append(
                _finding(
                    "entry_event_cost_missing",
                    "Power-changing entry event should name the visible cost.",
                    f"{path}.cost_paid",
                    severity="warning",
                    entry_id=entry_id or None,
                )
            )
    return tuple(findings)


def validate_reward_specificity(
    text: str,
    registry: EntryRegistry | Mapping[str, object] | None = None,
) -> tuple[EntrySystemGateFinding, ...]:
    stripped = text.strip()
    if not stripped:
        return (
            _finding(
                "entry_reward_too_vague",
                "Reward text is empty.",
                "reward",
                severity="warning",
            ),
        )
    lower = stripped.lower()
    names: list[str] = []
    if registry is not None:
        if isinstance(registry, Mapping):
            registry = EntryRegistry.model_validate(registry)
        names = [entry.name for entry in registry.entries] + [
            entry.entry_id for entry in registry.entries
        ]
    has_entry_reference = any(name and name in stripped for name in names)
    has_state_marker = any(
        marker in lower
        for marker in (
            "获得",
            "升级",
            "消耗",
            "暴露",
            "损坏",
            "失去",
            "兑现",
            "acquire",
            "upgrade",
            "spend",
            "expose",
            "damage",
            "lose",
        )
    )
    if not has_entry_reference and not has_state_marker:
        return (
            _finding(
                "entry_reward_too_vague",
                "Reward must name a concrete entry, resource, state change, or cost.",
                "reward",
                severity="warning",
            ),
        )
    if any(token in lower for token in _VAGUE_REWARD_TEXTS) and not has_entry_reference:
        return (
            _finding(
                "entry_reward_too_vague",
                "Reward is generic and not tied to a concrete entry.",
                "reward",
                severity="warning",
            ),
        )
    return ()


def validate_entry_system_package(
    kernel: EntrySystemKernel | Mapping[str, object],
    registry: EntryRegistry | Mapping[str, object],
    events: Sequence[Mapping[str, object] | object] = (),
) -> EntrySystemGateReport:
    findings = (
        *validate_kernel(kernel),
        *validate_registry(kernel, registry),
        *validate_entry_events(registry, events),
    )
    passed = not any(finding.severity in {"error", "critical"} for finding in findings)
    return EntrySystemGateReport(passed=passed, findings=tuple(findings))


__all__ = [
    "EntrySystemGateFinding",
    "EntrySystemGateReport",
    "validate_entry_events",
    "validate_entry_system_package",
    "validate_kernel",
    "validate_registry",
    "validate_reward_specificity",
]
