"""Auditable contracts for formal platform observations.

This module deliberately models *evidence* and not predictions.  In particular,
an adapter without an authorised, replayable source can never produce a formal
ranking claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

PlatformMode = Literal["shadow", "formal", "inconclusive"]


@dataclass(frozen=True, slots=True)
class PlatformAdapterCapabilityReport:
    adapter_id: str
    platform: str
    mode: PlatformMode
    authorized: bool
    source_receipt_id: str | None
    fields: frozenset[str] = frozenset()
    reader_identity_granularity: str | None = None
    exposure_denominator: str | None = None
    missing_primary_fields: tuple[str, ...] = ()
    reason_code: str = ""
    terms_permit_analysis: bool = False

    @property
    def formal_eligible(self) -> bool:
        return (
            self.mode == "formal"
            and self.authorized
            and not self.missing_primary_fields
            and bool(self.source_receipt_id)
            and self.terms_permit_analysis
        )

    def require_formal(self) -> None:
        if not self.formal_eligible:
            raise ValueError(self.reason_code or "INCONCLUSIVE_PLATFORM_DATA_ACCESS")


@dataclass(frozen=True, slots=True)
class PlatformObservationEvent:
    book_id: str
    event: str
    occurred_at: datetime
    platform_account_key: str | None = None
    identity_source: str = ""
    exposure_eligible: bool = False
    traffic_source: Literal["organic", "paid"] = "organic"
    campaign_id: str | None = None
    chapter_number: int | None = None
    paid_order_key: str | None = None
    fraud_flag: bool = False
    refund_flag: bool = False
    raw_receipt_id: str | None = None


@dataclass(frozen=True, slots=True)
class PlatformBookMetrics:
    book_id: str
    qualified_unique_reader: int
    eligible_ch1_start: int
    first_10_arrival: float | None
    day7_retention: float | None
    paid_conversion: float | None = None
    paid_retention: float | None = None
    risk_set_size: int = 0
    followup_complete: bool = False
    source: str = ""


@dataclass(frozen=True, slots=True)
class PlatformPrimaryMetricResult:
    name: str
    estimate: float | None
    lower_ci: float | None
    cohort_p25: float | None
    cohort_p50: float | None
    passed: bool
    reason_code: str = ""


@dataclass(frozen=True, slots=True)
class PlatformObservationReport:
    adapter_id: str
    mode: PlatformMode
    status: str
    primary_metrics: tuple[PlatformPrimaryMetricResult, ...] = ()
    candidate_unique_readers: int = 0
    candidate_paid_conversions: int = 0
    cohort_book_count: int = 0
    risk_set_size: int = 0
    complete_followup_windows: int = 0
    data_cutoff: datetime | None = None
    acquisition_cutoff: datetime | None = None
    reasons: tuple[str, ...] = ()

    @property
    def all_primary_metrics_pass(self) -> bool:
        return bool(self.primary_metrics) and all(item.passed for item in self.primary_metrics)


@dataclass(frozen=True, slots=True)
class FixedFollowupRiskSet:
    data_cutoff: datetime
    acquisition_cutoff: datetime
    eligible_reader_keys: frozenset[str]
    observed_reader_keys: frozenset[str]
    late_reader_keys: frozenset[str] = frozenset()

    @property
    def denominator(self) -> int:
        return len(self.eligible_reader_keys)

    @property
    def retained(self) -> int:
        return len(self.eligible_reader_keys & self.observed_reader_keys)

    @property
    def retention(self) -> float | None:
        return self.retained / self.denominator if self.denominator else None

    def validate(self) -> None:
        if self.acquisition_cutoff != self.data_cutoff - timedelta(hours=192):
            raise ValueError("INCONCLUSIVE_PLATFORM_CENSORING")
        if self.eligible_reader_keys & self.late_reader_keys:
            raise ValueError("RISK_SET_LATE_ENTRY_OVERLAP")


__all__ = [
    "FixedFollowupRiskSet",
    "PlatformAdapterCapabilityReport",
    "PlatformBookMetrics",
    "PlatformObservationEvent",
    "PlatformObservationReport",
    "PlatformPrimaryMetricResult",
]

# Explicit adapter-facing alias keeps the contract discoverable without
# creating a second, divergent schema.
FanqieLongCapabilityReport = PlatformAdapterCapabilityReport
__all__.append("FanqieLongCapabilityReport")
