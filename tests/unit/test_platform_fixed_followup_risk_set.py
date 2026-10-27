from datetime import UTC, datetime, timedelta

from bestseller.domain.platform_observation import PlatformObservationEvent
from bestseller.services.platform_metric_reducer import build_fixed_followup_risk_set


def test_late_entrant_is_outside_common_risk_set() -> None:
    cutoff = datetime.now(UTC)
    events = [
        PlatformObservationEvent(
            "b",
            "chapter_start",
            cutoff - timedelta(hours=200),
            "u1",
            "platform_account",
            True,
            chapter_number=1,
        ),
        PlatformObservationEvent(
            "b",
            "chapter_start",
            cutoff - timedelta(hours=20),
            "u2",
            "platform_account",
            True,
            chapter_number=1,
        ),
        PlatformObservationEvent(
            "b",
            "chapter_start",
            cutoff - timedelta(hours=20),
            "u1",
            "platform_account",
            True,
            chapter_number=2,
        ),
    ]
    risk = build_fixed_followup_risk_set(events, data_cutoff=cutoff)
    assert risk.eligible_reader_keys == frozenset({"u1"})
    assert risk.late_reader_keys == frozenset({"u2"})
    assert risk.denominator == 1
    assert risk.retention == 1.0
