from dataclasses import replace
from datetime import UTC, datetime, timedelta

from bestseller.domain.reader_study import ReaderEvent, ReaderEventType, reduce_reader_events


def _events(
    *, source="live", participant="a" * 64, scope="first_3", continue_delay=5, speed_chars=300
):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    specs = [
        (ReaderEventType.ASSIGNMENT_CREATED, {}),
        (ReaderEventType.CONSENT_ACCEPTED, {}),
        (ReaderEventType.SESSION_STARTED, {"assigned_order": "engine_first"}),
        (ReaderEventType.CONTENT_PRESENTED, {"displayed_characters": speed_chars}),
        (ReaderEventType.VISIBILITY_HEARTBEAT, {}),
        (ReaderEventType.VISIBILITY_HEARTBEAT, {}),
        (ReaderEventType.CONTINUE_CLICKED, {}),
        (ReaderEventType.NEXT_CONTENT_STARTED, {}),
        (ReaderEventType.ATTENTION_ANSWERED, {"correct": True}),
        (ReaderEventType.PREFERENCE_SUBMITTED, {"preferred_variant": "engine"}),
    ]
    events = []
    for index, (kind, payload) in enumerate(specs):
        offset = index * 10
        if kind is ReaderEventType.NEXT_CONTENT_STARTED:
            offset = 60 + continue_delay * 60
        events.append(
            ReaderEvent(
                kind,
                participant,
                "study",
                scope,
                "session",
                "assignment",
                start + timedelta(seconds=offset),
                index + 1,
                f"key-{index}",
                payload,
                source,
            )
        )
    return events


def test_reducer_tracks_itt_early_abandonment_and_heartbeat_cap():
    events = _events(speed_chars=100)
    heartbeat_index = next(
        i
        for i, event in enumerate(events)
        if event.event_type is ReaderEventType.VISIBILITY_HEARTBEAT
    )
    events[heartbeat_index + 1] = replace(
        events[heartbeat_index + 1],
        occurred_at=events[heartbeat_index].occurred_at + timedelta(seconds=20),
    )
    events = [
        event for event in events if event.event_type is not ReaderEventType.NEXT_CONTENT_STARTED
    ]
    result = reduce_reader_events(events, min_n=1)
    cell = result.cells[0]
    assert cell.itt_n == 1 and cell.qualified_n == 1
    assert cell.active_dwell_seconds_mean == 15
    assert "CONTINUE_NOT_OBJECTIVELY_COMPLETED" in cell.blocking_codes
    assert result.status == "INCONCLUSIVE_READER"


def test_continue_requires_next_content_within_30_minutes_and_fixture_cannot_upgrade():
    result = reduce_reader_events(_events(source="fixture", continue_delay=31), min_n=1)
    assert "CONTINUE_NOT_OBJECTIVELY_COMPLETED" in result.cells[0].blocking_codes
    assert "FIXTURE_CANNOT_UPGRADE_LIVE" in result.cells[0].blocking_codes
    assert result.status == "PASS_FIXTURE"


def test_speed_boundary_is_enforced():
    result = reduce_reader_events(_events(speed_chars=2000), min_n=1)
    assert "ABNORMAL_READING_SPEED" in result.cells[0].blocking_codes


def test_first_3_and_first_10_participants_are_not_reusable():
    first = _events(scope="first_3")
    second = [
        event.__class__(
            event.event_type,
            event.participant_hash,
            event.study_key,
            "first_10",
            event.session_id + "2",
            "assignment2",
            event.occurred_at,
            event.client_counter,
            event.idempotency_key + "2",
            event.payload,
            event.evidence_source,
        )
        for event in first
    ]
    result = reduce_reader_events(first + second, min_n=1)
    assert "READER_PARTICIPANT_REUSED" in result.blocking_codes
