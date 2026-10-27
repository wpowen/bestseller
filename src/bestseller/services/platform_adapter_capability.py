"""Capability checks for authorised platform adapters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

from bestseller.domain.platform_observation import PlatformAdapterCapabilityReport

FANQIE_LONG_PRIMARY_FIELDS = frozenset(
    {
        "platform_account_key",
        "exposure_denominator",
        "eligible_ch1_start",
        "first_10_arrival",
        "day7_retention",
        "traffic_source",
        "refund_flag",
        "fraud_flag",
        "rank_receipt",
    }
)


def build_fanqie_long_capability_report(
    *,
    authorized: bool = False,
    source_receipt_id: str | None = None,
    fields: Iterable[str] = (),
    reader_identity_granularity: str | None = None,
    exposure_denominator: str | None = None,
    terms_permit_analysis: bool = False,
) -> PlatformAdapterCapabilityReport:
    provided = frozenset(fields)
    missing = tuple(sorted(FANQIE_LONG_PRIMARY_FIELDS - provided))
    identity_ok = reader_identity_granularity == "platform_account"
    exposure_ok = bool(exposure_denominator)
    if not authorized or not source_receipt_id or not terms_permit_analysis:
        mode: Literal["shadow", "formal", "inconclusive"] = "shadow"
        reason = "INCONCLUSIVE_PLATFORM_DATA_ACCESS"
    elif not identity_ok:
        mode = "inconclusive"
        reason = "INCONCLUSIVE_PLATFORM_IDENTITY"
    elif not exposure_ok or missing:
        mode = "inconclusive"
        reason = "INCONCLUSIVE_PLATFORM_DATA"
    else:
        mode = "formal"
        reason = ""
    return PlatformAdapterCapabilityReport(
        adapter_id="fanqie-long-v1",
        platform="fanqie-long",
        mode=mode,
        authorized=authorized,
        source_receipt_id=source_receipt_id,
        fields=provided,
        reader_identity_granularity=reader_identity_granularity,
        exposure_denominator=exposure_denominator,
        missing_primary_fields=missing,
        reason_code=reason,
        terms_permit_analysis=terms_permit_analysis,
    )


def capability_report_from_metadata(
    metadata: Mapping[str, object],
) -> PlatformAdapterCapabilityReport:
    raw_fields = metadata.get("fields")
    fields = (
        tuple(str(item) for item in raw_fields)
        if isinstance(raw_fields, (list, tuple, set, frozenset))
        else ()
    )
    raw_identity = metadata.get("reader_identity_granularity")
    identity = raw_identity if isinstance(raw_identity, str) else None
    raw_exposure = metadata.get("exposure_denominator")
    exposure = raw_exposure if isinstance(raw_exposure, str) else None
    return build_fanqie_long_capability_report(
        authorized=bool(metadata.get("authorized")),
        source_receipt_id=(
            str(metadata["source_receipt_id"]) if metadata.get("source_receipt_id") else None
        ),
        fields=fields,
        reader_identity_granularity=identity,
        exposure_denominator=exposure,
        terms_permit_analysis=bool(metadata.get("terms_permit_analysis")),
    )


def check_fanqie_long_capability(
    *,
    authorized: bool = False,
    source_receipt_id: str | None = None,
    fields: Iterable[str] = (),
    reader_identity_granularity: str | None = None,
    exposure_denominator: str | None = None,
    terms_permit_analysis: bool = False,
) -> PlatformAdapterCapabilityReport:
    """Compatibility entry point used by adapters and verification jobs."""
    return build_fanqie_long_capability_report(
        authorized=authorized,
        source_receipt_id=source_receipt_id,
        fields=fields,
        reader_identity_granularity=reader_identity_granularity,
        exposure_denominator=exposure_denominator,
        terms_permit_analysis=terms_permit_analysis,
    )


__all__ = [
    "FANQIE_LONG_PRIMARY_FIELDS",
    "build_fanqie_long_capability_report",
    "capability_report_from_metadata",
    "check_fanqie_long_capability",
]
