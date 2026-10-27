"""Pure, fail-closed reader-study contracts and event reduction.

This module deliberately has no database dependency.  A collector can persist the
raw events and later replay the exact same reduction in an independent verifier.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from math import sqrt
from typing import Any


class ReaderEventType(StrEnum):
    ASSIGNMENT_CREATED = "ASSIGNMENT_CREATED"
    CONSENT_ACCEPTED = "CONSENT_ACCEPTED"
    SESSION_STARTED = "SESSION_STARTED"
    CONTENT_PRESENTED = "CONTENT_PRESENTED"
    VISIBILITY_HEARTBEAT = "VISIBILITY_HEARTBEAT"
    PAGE_OR_CHAPTER_REACHED = "PAGE_OR_CHAPTER_REACHED"
    ATTENTION_ANSWERED = "ATTENTION_ANSWERED"
    CONTINUE_CLICKED = "CONTINUE_CLICKED"
    NEXT_CONTENT_STARTED = "NEXT_CONTENT_STARTED"
    PREFERENCE_SUBMITTED = "PREFERENCE_SUBMITTED"
    SESSION_ENDED = "SESSION_ENDED"


class ReaderEvidenceSource(StrEnum):
    FIXTURE = "fixture"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class ReaderEvent:
    event_type: ReaderEventType | str
    participant_hash: str
    study_key: str
    cell_key: str
    session_id: str
    assignment_id: str
    occurred_at: datetime
    client_counter: int
    idempotency_key: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    evidence_source: ReaderEvidenceSource | str = ReaderEvidenceSource.FIXTURE

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", ReaderEventType(self.event_type))
        object.__setattr__(self, "evidence_source", ReaderEvidenceSource(self.evidence_source))
        if not self.participant_hash or len(self.participant_hash.strip()) != 64:
            raise ValueError("participant_hash must be a SHA-256 hex digest")
        try:
            int(self.participant_hash, 16)
        except ValueError as exc:
            raise ValueError("participant_hash must be a SHA-256 hex digest") from exc
        if self.client_counter < 1:
            raise ValueError("client_counter must be positive")
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ReaderEvent:
        raw = dict(value)
        timestamp = raw.get("occurred_at")
        if isinstance(timestamp, str):
            raw["occurred_at"] = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return cls(**raw)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "event_type": ReaderEventType(self.event_type).value,
            "participant_hash": self.participant_hash.lower(),
            "study_key": self.study_key,
            "cell_key": self.cell_key,
            "session_id": self.session_id,
            "assignment_id": self.assignment_id,
            "occurred_at": self.occurred_at.astimezone(UTC).isoformat(),
            "client_counter": self.client_counter,
            "idempotency_key": self.idempotency_key,
            "payload": dict(self.payload),
            "evidence_source": ReaderEvidenceSource(self.evidence_source).value,
        }


@dataclass(frozen=True, slots=True)
class ReaderCellResult:
    cell_key: str
    reading_scope: str
    evidence_source: str
    itt_n: int
    qualified_n: int
    preference_rate: float
    continue_rate: float
    recall_rate: float
    severe_abandonment_rate: float
    active_dwell_seconds_mean: float
    order_imbalance: int
    blocking_codes: tuple[str, ...]
    passed: bool


@dataclass(frozen=True, slots=True)
class ReaderStudyResult:
    study_key: str
    cells: tuple[ReaderCellResult, ...]
    status: str
    blocking_codes: tuple[str, ...]
    evidence_source: str


def wilson_lower(successes: int, total: int, z: float = 1.959963984540054) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * sqrt((p * (1 - p) / total) + z * z / (4 * total * total))
    return (centre - margin) / denominator


def reduce_reader_events(
    events: Iterable[ReaderEvent | Mapping[str, Any]],
    *,
    reading_scope_by_cell: Mapping[str, str] | None = None,
    min_n: int = 60,
    power: float | Mapping[str, float] = 0.8,
    evidence_source: ReaderEvidenceSource | str | None = None,
) -> ReaderStudyResult:
    """Replay raw events into ITT metrics; malformed chains block the cell.

    The reducer never trusts caller supplied ``live``.  A live result requires a
    complete assignment/consent/session/content trajectory and valid counters.
    """
    parsed = tuple(e if isinstance(e, ReaderEvent) else ReaderEvent.from_mapping(e) for e in events)
    if not parsed:
        return ReaderStudyResult("", (), "INCONCLUSIVE_READER_POWER", ("NO_EVENTS",), "unknown")
    study_key = parsed[0].study_key
    by_assignment: dict[str, list[ReaderEvent]] = {}
    global_participants: set[str] = set()
    blocks: list[str] = []
    seen_keys: set[str] = set()
    source_values = {ReaderEvidenceSource(e.evidence_source).value for e in parsed}
    if (
        evidence_source is not None
        and ReaderEvidenceSource(evidence_source).value not in source_values
    ):
        blocks.append("EVIDENCE_SOURCE_MISMATCH")
    for event in parsed:
        if event.study_key != study_key:
            blocks.append("STUDY_KEY_MISMATCH")
        if event.idempotency_key in seen_keys:
            blocks.append("DUPLICATE_IDEMPOTENCY_KEY")
        seen_keys.add(event.idempotency_key)
        by_assignment.setdefault(event.assignment_id, []).append(event)
    cells: list[ReaderCellResult] = []
    for _assignment_id, chain in by_assignment.items():
        chain = sorted(chain, key=lambda e: (e.client_counter, e.occurred_at))
        cell = chain[0].cell_key
        scope = (reading_scope_by_cell or {}).get(
            cell, str(chain[0].payload.get("reading_scope", "first_3"))
        )
        local: list[str] = []
        counters = [e.client_counter for e in chain]
        if counters != list(range(1, len(counters) + 1)):
            local.append("CLIENT_COUNTER_GAP_OR_REPLAY")
        types = {ReaderEventType(e.event_type) for e in chain}
        required = {
            ReaderEventType.CONSENT_ACCEPTED,
            ReaderEventType.SESSION_STARTED,
            ReaderEventType.CONTENT_PRESENTED,
        }
        qualified = required <= types
        if not qualified:
            local.append("MISSING_CONSENT_OR_TRAJECTORY")
        if chain[0].evidence_source is ReaderEvidenceSource.LIVE and not qualified:
            local.append("LIVE_TRAJECTORY_INVALID")
        participant = chain[0].participant_hash.lower()
        if participant in global_participants:
            local.append("READER_PARTICIPANT_REUSED")
        global_participants.add(participant)
        heartbeats = [e for e in chain if e.event_type is ReaderEventType.VISIBILITY_HEARTBEAT]
        dwell = 0.0
        for previous, current in pairwise(heartbeats):
            gap = max(0.0, (current.occurred_at - previous.occurred_at).total_seconds())
            dwell += min(gap, 15.0)
        presented = [e for e in chain if e.event_type is ReaderEventType.CONTENT_PRESENTED]
        chars = sum(int(e.payload.get("displayed_characters", 0) or 0) for e in presented)
        minutes = dwell / 60
        if chars and minutes and not 80 <= chars / minutes <= 1200:
            local.append("ABNORMAL_READING_SPEED")
        continue_clicked = next(
            (e for e in chain if e.event_type is ReaderEventType.CONTINUE_CLICKED), None
        )
        next_started = next(
            (e for e in chain if e.event_type is ReaderEventType.NEXT_CONTENT_STARTED), None
        )
        continue_ok = bool(
            continue_clicked
            and next_started
            and timedelta(0)
            <= next_started.occurred_at - continue_clicked.occurred_at
            <= timedelta(minutes=30)
        )
        if continue_clicked and not continue_ok:
            local.append("CONTINUE_NOT_OBJECTIVELY_COMPLETED")
        preference = next(
            (
                str(e.payload.get("preferred_variant", ""))
                for e in chain
                if e.event_type is ReaderEventType.PREFERENCE_SUBMITTED
            ),
            "",
        )
        recall_answers = [e for e in chain if e.event_type is ReaderEventType.ATTENTION_ANSWERED]
        recall_ok = bool(recall_answers) and all(
            bool(e.payload.get("correct")) for e in recall_answers
        )
        abandoned = any(
            e.event_type is ReaderEventType.SESSION_ENDED
            and str(e.payload.get("reason", "")).lower() in {"abandon", "abandoned"}
            for e in chain
        )
        severe = abandoned and not any(
            e.event_type is ReaderEventType.PAGE_OR_CHAPTER_REACHED
            and int(e.payload.get("progress_percent", 0) or 0) >= 50
            for e in chain
        )
        order = next(
            (
                str(event.payload.get("assigned_order", ""))
                for event in chain
                if event.payload.get("assigned_order")
            ),
        )
        if order not in {"baseline_first", "engine_first"}:
            local.append("INVALID_ASSIGNED_ORDER")
        cells.append(
            ReaderCellResult(
                cell,
                scope,
                ReaderEvidenceSource(chain[0].evidence_source).value,
                1,
                int(qualified),
                float(preference == "engine"),
                float(continue_ok),
                float(recall_ok),
                float(severe),
                dwell,
                0,
                tuple(sorted(set(local))),
                False,
            )
        )
    # ITT is per participant/cell, and duplicate participants are never silently dropped.
    by_cell: dict[str, list[ReaderCellResult]] = {}
    for result in cells:
        by_cell.setdefault(result.cell_key, []).append(result)
    final_cells: list[ReaderCellResult] = []
    for cell, rows in by_cell.items():
        n = len(rows)
        order_counts = {"baseline_first": 0, "engine_first": 0}
        for assignment_id in by_assignment:
            chain = by_assignment[assignment_id]
            if chain[0].cell_key == cell:
                order_counts[str(chain[0].payload.get("assigned_order", ""))] = (
                    order_counts.get(str(chain[0].payload.get("assigned_order", "")), 0) + 1
                )
        imbalance = abs(order_counts.get("baseline_first", 0) - order_counts.get("engine_first", 0))
        code_set = {code for row in rows for code in row.blocking_codes}
        requested_power = power.get(cell, 0.8) if isinstance(power, Mapping) else power
        if n < min_n:
            code_set.add("INCONCLUSIVE_READER_POWER")
        if float(requested_power) < 0.8:
            code_set.add("PREREGISTERED_POWER_BELOW_0_80")
        if imbalance > 1:
            code_set.add("ORDER_IMBALANCE")
        sources = {row.evidence_source for row in rows}
        if len(sources) != 1 or "fixture" in sources:
            code_set.add("FIXTURE_CANNOT_UPGRADE_LIVE")
        final_cells.append(
            ReaderCellResult(
                cell,
                rows[0].reading_scope,
                next(iter(sources), "unknown"),
                n,
                sum(row.qualified_n for row in rows),
                sum(row.preference_rate for row in rows) / n,
                sum(row.continue_rate for row in rows) / n,
                sum(row.recall_rate for row in rows) / n,
                sum(row.severe_abandonment_rate for row in rows) / n,
                sum(row.active_dwell_seconds_mean for row in rows) / n,
                imbalance,
                tuple(sorted(code_set)),
                not code_set,
            )
        )
    all_blocks = set(blocks)
    all_blocks.update(code for cell in final_cells for code in cell.blocking_codes)
    source = "mixed" if len(source_values) > 1 else next(iter(source_values), "unknown")
    status = (
        "READER_VALIDATED"
        if final_cells and all(cell.passed for cell in final_cells) and source == "live"
        else ("PASS_FIXTURE" if source == "fixture" else "INCONCLUSIVE_READER")
    )
    return ReaderStudyResult(
        study_key, tuple(final_cells), status, tuple(sorted(all_blocks)), source
    )


__all__ = [
    "ReaderCellResult",
    "ReaderEvent",
    "ReaderEventType",
    "ReaderEvidenceSource",
    "ReaderStudyResult",
    "reduce_reader_events",
    "wilson_lower",
]
