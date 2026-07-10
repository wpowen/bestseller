"""Shared structured degradation evidence for generation pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

DegradationSeverity = Literal["info", "warning", "error", "critical"]


@dataclass(frozen=True)
class DegradationEvent:
    stage: str
    component: str
    reason: str
    severity: DegradationSeverity
    fallback: bool
    model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class DegradationTracker:
    """Collect typed degradation events without component-specific strings."""

    def __init__(self) -> None:
        self._events: list[DegradationEvent] = []

    @property
    def events(self) -> tuple[DegradationEvent, ...]:
        return tuple(self._events)

    def record(
        self,
        *,
        stage: str,
        component: str,
        reason: str,
        severity: DegradationSeverity,
        fallback: bool,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DegradationEvent:
        event = DegradationEvent(
            stage=stage,
            component=component,
            reason=reason,
            severity=severity,
            fallback=fallback,
            model=model,
            metadata=dict(metadata or {}),
        )
        if event not in self._events:
            self._events.append(event)
        return event

    def blocking_events(self, required_components: set[str]) -> tuple[DegradationEvent, ...]:
        return tuple(
            event
            for event in self._events
            if event.component in required_components
            and (event.fallback or event.severity in {"error", "critical"})
        )


__all__ = ["DegradationEvent", "DegradationSeverity", "DegradationTracker"]
