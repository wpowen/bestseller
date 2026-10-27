from datetime import UTC, datetime, timedelta

import pytest

from bestseller.domain.platform_observation import FixedFollowupRiskSet


def test_censoring_requires_fixed_192_hour_cutoff() -> None:
    cutoff = datetime.now(UTC)
    risk = FixedFollowupRiskSet(cutoff, cutoff - timedelta(hours=191), frozenset(), frozenset())
    with pytest.raises(ValueError, match="INCONCLUSIVE_PLATFORM_CENSORING"):
        risk.validate()
