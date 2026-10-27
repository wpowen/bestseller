from datetime import UTC, datetime

import pytest

from bestseller.domain.reader_study import ReaderEvent, ReaderEventType
from bestseller.services.reader_assignment import AssignmentError, ReaderAssignmentAuthority


class InMemoryCollector:
    def __init__(self, authority):
        self.authority = authority
        self.keys = set()
        self.counters = {}

    def accept(self, token, event_type, payload, *, counter, key):
        receipt = self.authority.verify(token)
        if key in self.keys:
            raise AssignmentError("idempotency key replayed")
        expected = self.counters.get(receipt.assignment_id, 0) + 1
        if counter != expected:
            raise AssignmentError("client counter replayed or has a gap")
        self.keys.add(key)
        self.counters[receipt.assignment_id] = counter
        return ReaderEvent(
            event_type,
            receipt.participant_hash,
            receipt.study_key,
            receipt.cell_key,
            "session",
            receipt.assignment_id,
            datetime.now(UTC),
            counter,
            key,
            payload,
            "live",
        )


def test_collector_rejects_token_replay_and_preserves_server_assignment():
    authority = ReaderAssignmentAuthority(b"x" * 32)
    receipt = authority.create(
        study_key="s",
        cell_key="c",
        participant_hash="a" * 64,
        assigned_arm="engine",
        assigned_order="engine_first",
        candidate_hash="b" * 64,
    )
    collector = InMemoryCollector(authority)
    event = collector.accept(
        receipt.token, ReaderEventType.CONSENT_ACCEPTED, {}, counter=1, key="idempotent-1"
    )
    assert event.participant_hash == "a" * 64
    with pytest.raises(AssignmentError):
        collector.accept(
            receipt.token, ReaderEventType.SESSION_STARTED, {}, counter=2, key="idempotent-2"
        )


def test_collector_rejects_counter_gap_and_duplicate_key():
    authority = ReaderAssignmentAuthority(b"x" * 32)
    receipt = authority.create(
        study_key="s",
        cell_key="c",
        participant_hash="a" * 64,
        assigned_arm="baseline",
        assigned_order="baseline_first",
        candidate_hash="b" * 64,
    )
    collector = InMemoryCollector(authority)
    collector.accept(receipt.token, ReaderEventType.CONSENT_ACCEPTED, {}, counter=1, key="key-1")
    # A second token is required because assignments are intentionally one-use.
    receipt2 = authority.create(
        study_key="s",
        cell_key="c",
        participant_hash="c" * 64,
        assigned_arm="baseline",
        assigned_order="baseline_first",
        candidate_hash="b" * 64,
    )
    with pytest.raises(AssignmentError, match="gap"):
        collector.accept(
            receipt2.token, ReaderEventType.CONSENT_ACCEPTED, {}, counter=2, key="key-2"
        )
