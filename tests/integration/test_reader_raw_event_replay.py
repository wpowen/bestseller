from datetime import UTC, datetime

from bestseller.domain.reader_study import ReaderEvent, ReaderEventType, reduce_reader_events


def test_raw_event_mapping_replays_to_same_result():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        ReaderEvent(
            ReaderEventType.CONSENT_ACCEPTED, "a" * 64, "s", "c", "sess", "a", now, 1, "k1"
        ),
        ReaderEvent(
            ReaderEventType.SESSION_STARTED,
            "a" * 64,
            "s",
            "c",
            "sess",
            "a",
            now,
            2,
            "k2",
            {"assigned_order": "engine_first"},
        ),
        ReaderEvent(
            ReaderEventType.CONTENT_PRESENTED,
            "a" * 64,
            "s",
            "c",
            "sess",
            "a",
            now,
            3,
            "k3",
            {"displayed_characters": 100},
        ),
    ]
    direct = reduce_reader_events(rows, min_n=1)
    replayed = reduce_reader_events(
        [ReaderEvent.from_mapping(row.to_mapping()) for row in rows], min_n=1
    )
    assert direct == replayed
    assert direct.cells[0].itt_n == 1
