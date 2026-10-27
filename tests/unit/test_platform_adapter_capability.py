from bestseller.services.platform_adapter_capability import (
    FANQIE_LONG_PRIMARY_FIELDS,
    build_fanqie_long_capability_report,
)


def test_without_authorized_receipt_is_shadow() -> None:
    report = build_fanqie_long_capability_report()
    assert report.mode == "shadow"
    assert not report.formal_eligible
    assert report.reason_code == "INCONCLUSIVE_PLATFORM_DATA_ACCESS"


def test_complete_authorized_field_contract_is_formal() -> None:
    report = build_fanqie_long_capability_report(
        authorized=True,
        source_receipt_id="official-export-1",
        fields=FANQIE_LONG_PRIMARY_FIELDS,
        reader_identity_granularity="platform_account",
        exposure_denominator="eligible_impression",
        terms_permit_analysis=True,
    )
    assert report.formal_eligible
