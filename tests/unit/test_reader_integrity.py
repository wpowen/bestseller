from datetime import UTC, datetime

from bestseller.domain.reader_study import ReaderEvent, ReaderEventType
from bestseller.services.reader_integrity import validate_reader_integrity


def test_integrity_requires_consent_and_trajectory():
    event = ReaderEvent(
        ReaderEventType.SESSION_STARTED, "a" * 64, "s", "c", "sess", "a", datetime.now(UTC), 1, "k"
    )
    report = validate_reader_integrity([event])
    assert not report.valid
    assert "MISSING_CONSENT" in report.codes
    assert "MISSING_SESSION_TRAJECTORY" in report.codes


def test_integrity_detects_attention_failure_and_counter_replay():
    base = datetime.now(UTC)
    events = [
        ReaderEvent(
            ReaderEventType.CONSENT_ACCEPTED, "a" * 64, "s", "c", "sess", "a", base, 1, "k1"
        ),
        ReaderEvent(
            ReaderEventType.SESSION_STARTED, "a" * 64, "s", "c", "sess", "a", base, 1, "k2"
        ),
        ReaderEvent(
            ReaderEventType.CONTENT_PRESENTED, "a" * 64, "s", "c", "sess", "a", base, 3, "k3"
        ),
        ReaderEvent(
            ReaderEventType.ATTENTION_ANSWERED,
            "a" * 64,
            "s",
            "c",
            "sess",
            "a",
            base,
            4,
            "k4",
            {"correct": False},
        ),
    ]
    report = validate_reader_integrity(events)
    assert "CLIENT_COUNTER_GAP_OR_REPLAY" in report.codes
    assert "ATTENTION_CHECK_FAILED" in report.codes
