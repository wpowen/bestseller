"""Deterministic integrity checks for reader-study event chains."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from itertools import pairwise

from bestseller.domain.reader_study import ReaderEvent, ReaderEventType


@dataclass(frozen=True, slots=True)
class ReaderIntegrityReport:
    valid: bool
    codes: tuple[str, ...]
    participant_count: int
    assignment_count: int


def validate_reader_integrity(events: Iterable[ReaderEvent]) -> ReaderIntegrityReport:
    rows = tuple(events)
    codes: set[str] = set()
    participants: dict[str, str] = {}
    assignments: set[str] = set()
    counters: dict[str, list[int]] = {}
    for event in rows:
        assignments.add(event.assignment_id)
        counters.setdefault(event.assignment_id, []).append(event.client_counter)
        previous = participants.get(event.participant_hash)
        if previous is not None and previous != event.assignment_id:
            codes.add("PARTICIPANT_REUSED_ACROSS_ASSIGNMENTS")
        participants[event.participant_hash] = event.assignment_id
    for assignment_id, values in counters.items():
        if len(values) != len(set(values)) or sorted(values) != list(range(1, len(values) + 1)):
            codes.add("CLIENT_COUNTER_GAP_OR_REPLAY")
        chain = sorted(
            (event for event in rows if event.assignment_id == assignment_id),
            key=lambda event: event.client_counter,
        )
        types = {event.event_type for event in chain}
        if ReaderEventType.CONSENT_ACCEPTED not in types:
            codes.add("MISSING_CONSENT")
        if (
            ReaderEventType.SESSION_STARTED not in types
            or ReaderEventType.CONTENT_PRESENTED not in types
        ):
            codes.add("MISSING_SESSION_TRAJECTORY")
        for first, second in pairwise(chain):
            if second.occurred_at < first.occurred_at:
                codes.add("EVENT_TIME_REVERSED")
        for event in chain:
            if event.event_type is ReaderEventType.CONTINUE_CLICKED:
                follow = next(
                    (
                        candidate
                        for candidate in chain
                        if candidate.event_type is ReaderEventType.NEXT_CONTENT_STARTED
                        and candidate.occurred_at >= event.occurred_at
                    ),
                    None,
                )
                if follow is None or follow.occurred_at - event.occurred_at > timedelta(minutes=30):
                    codes.add("CONTINUE_WITHOUT_NEXT_CONTENT")
            if event.event_type is ReaderEventType.ATTENTION_ANSWERED and not event.payload.get(
                "correct", False
            ):
                codes.add("ATTENTION_CHECK_FAILED")
    return ReaderIntegrityReport(
        not codes and bool(rows), tuple(sorted(codes)), len(participants), len(assignments)
    )


__all__ = ["ReaderIntegrityReport", "validate_reader_integrity"]
