from datetime import UTC, datetime

from bestseller.domain.platform_observation import PlatformObservationEvent
from bestseller.services.platform_metric_reducer import (
    exposure_denominator,
    qualified_unique_readers,
)


def _event(
    key: str, identity: str = "platform_account", eligible: bool = True
) -> PlatformObservationEvent:
    return PlatformObservationEvent("book", "exposure", datetime.now(UTC), key, identity, eligible)


def test_device_cookie_aggregation_cannot_claim_formal_identity() -> None:
    assert qualified_unique_readers([_event("device-a", "device")]) == 0
    assert exposure_denominator([_event("device-a", "device")]) == 0


def test_platform_account_dedup_and_exposure_denominator() -> None:
    events = [_event("u1"), _event("u1"), _event("u2", eligible=False)]
    assert qualified_unique_readers(events) == 2
    assert exposure_denominator(events) == 1
