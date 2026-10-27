"""Pure adapter boundary: no scraping and no caller-controlled formal mode."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from bestseller.domain.platform_observation import (
    PlatformObservationEvent,
    PlatformObservationReport,
)
from bestseller.services.platform_adapter_capability import (
    build_fanqie_long_capability_report,
)
from bestseller.services.platform_metric_reducer import (
    all_primary_metrics_pass,
    build_fixed_followup_risk_set,
    qualified_unique_readers,
)


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _string_fields(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(str(value))


def normalize_authorized_events(
    rows: Iterable[Mapping[str, object]], *, source_receipt_id: str | None = None
) -> list[PlatformObservationEvent]:
    """Normalize rows only when each row carries a replayable official receipt."""
    events: list[PlatformObservationEvent] = []
    for row in rows:
        receipt = str(row.get("raw_receipt_id") or source_receipt_id or "")
        if not receipt:
            raise ValueError("INCONCLUSIVE_PLATFORM_DATA_ACCESS")
        events.append(
            PlatformObservationEvent(
                book_id=str(row["book_id"]),
                event=str(row["event"]),
                occurred_at=datetime.fromisoformat(str(row["occurred_at"]).replace("Z", "+00:00")),
                platform_account_key=(
                    str(row["platform_account_key"]) if row.get("platform_account_key") else None
                ),
                identity_source=str(row.get("identity_source", "")),
                exposure_eligible=bool(row.get("exposure_eligible", False)),
                traffic_source=("paid" if row.get("traffic_source") == "paid" else "organic"),
                campaign_id=(str(row["campaign_id"]) if row.get("campaign_id") else None),
                chapter_number=_optional_int(row.get("chapter_number")),
                paid_order_key=(str(row["paid_order_key"]) if row.get("paid_order_key") else None),
                fraud_flag=bool(row.get("fraud_flag", False)),
                refund_flag=bool(row.get("refund_flag", False)),
                raw_receipt_id=receipt,
            )
        )
    return events


def build_fanqie_long_observation_report(
    *,
    events: Iterable[PlatformObservationEvent],
    data_cutoff: datetime,
    capability_metadata: Mapping[str, object],
) -> PlatformObservationReport:
    capability = build_fanqie_long_capability_report(
        authorized=bool(capability_metadata.get("authorized", False)),
        source_receipt_id=(
            str(capability_metadata["source_receipt_id"])
            if capability_metadata.get("source_receipt_id")
            else None
        ),
        fields=_string_fields(capability_metadata.get("fields")),
        reader_identity_granularity=_optional_text(
            capability_metadata.get("reader_identity_granularity")
        ),
        exposure_denominator=_optional_text(capability_metadata.get("exposure_denominator")),
        terms_permit_analysis=bool(capability_metadata.get("terms_permit_analysis")),
    )
    if not capability.formal_eligible:
        reason = capability.reason_code or "INCONCLUSIVE_PLATFORM_DATA_ACCESS"
        return PlatformObservationReport(
            "fanqie-long-v1", capability.mode, reason, reasons=(reason,)
        )
    event_list = list(events)
    risk = build_fixed_followup_risk_set(event_list, data_cutoff=data_cutoff)
    return PlatformObservationReport(
        "fanqie-long-v1",
        "formal",
        "MARKET_PILOT_OBSERVED",
        candidate_unique_readers=qualified_unique_readers(event_list),
        risk_set_size=risk.denominator,
        data_cutoff=data_cutoff,
        acquisition_cutoff=risk.acquisition_cutoff,
    )


def evaluate_formal_platform_gate(
    *,
    capability_metadata: Mapping[str, object],
    candidate_unique_readers: int,
    candidate_paid_conversions: int = 0,
    cohort_book_count: int,
    complete_followup_windows: int,
    observation_days: int,
    primary_results: tuple[tuple[float | None, float | None, float | None, float | None], ...],
    rank_receipt: bool = False,
) -> PlatformObservationReport:
    """Apply the preregistered all-or-nothing formal-v1 gate."""
    capability = build_fanqie_long_capability_report(
        authorized=bool(capability_metadata.get("authorized")),
        source_receipt_id=_optional_text(capability_metadata.get("source_receipt_id")),
        fields=_string_fields(capability_metadata.get("fields")),
        reader_identity_granularity=_optional_text(
            capability_metadata.get("reader_identity_granularity")
        ),
        exposure_denominator=_optional_text(capability_metadata.get("exposure_denominator")),
        terms_permit_analysis=bool(capability_metadata.get("terms_permit_analysis")),
    )
    reasons: list[str] = []
    if not capability.formal_eligible:
        reasons.append(capability.reason_code or "INCONCLUSIVE_PLATFORM_DATA_ACCESS")
    if cohort_book_count < 30:
        reasons.append("INCONCLUSIVE_PLATFORM_COHORT")
    if candidate_unique_readers < 1000:
        reasons.append("INCONCLUSIVE_PLATFORM_POWER")
    if candidate_paid_conversions < 100:
        reasons.append("INCONCLUSIVE_PLATFORM_PAID_POWER")
    if complete_followup_windows < 2 or observation_days < 30:
        reasons.append("INCONCLUSIVE_PLATFORM_FOLLOWUP")
    if not all_primary_metrics_pass(primary_results):
        reasons.append("BLOCKED_PLATFORM_PRIMARY_METRIC")
    status = "RANKING_COMPETITIVE" if not reasons else reasons[0]
    if status == "RANKING_COMPETITIVE" and rank_receipt:
        status = "OBSERVED_RANKED"
    return PlatformObservationReport(
        "fanqie-long-v1",
        capability.mode,
        status,
        candidate_unique_readers=candidate_unique_readers,
        candidate_paid_conversions=candidate_paid_conversions,
        cohort_book_count=cohort_book_count,
        complete_followup_windows=complete_followup_windows,
        reasons=tuple(reasons),
    )


__all__ = [
    "build_fanqie_long_observation_report",
    "evaluate_formal_platform_gate",
    "normalize_authorized_events",
]
