from bestseller.services.platform_adapter_capability import FANQIE_LONG_PRIMARY_FIELDS
from bestseller.services.platform_evidence_adapter import evaluate_formal_platform_gate


def _metadata() -> dict[str, object]:
    return {
        "authorized": True,
        "source_receipt_id": "official-export-1",
        "fields": FANQIE_LONG_PRIMARY_FIELDS,
        "reader_identity_granularity": "platform_account",
        "exposure_denominator": "eligible_impression",
        "terms_permit_analysis": True,
    }


def _primary() -> tuple[tuple[float, float, float, float], ...]:
    return ((0.70, 0.65, 0.50, 0.60), (0.60, 0.55, 0.40, 0.50))


def test_all_hard_prerequisites_and_primary_metrics_pass() -> None:
    report = evaluate_formal_platform_gate(
        capability_metadata=_metadata(),
        candidate_unique_readers=1000,
        candidate_paid_conversions=100,
        cohort_book_count=30,
        complete_followup_windows=2,
        observation_days=30,
        primary_results=_primary(),
    )
    assert report.status == "RANKING_COMPETITIVE"


def test_any_primary_failure_blocks_overall_gate() -> None:
    report = evaluate_formal_platform_gate(
        capability_metadata=_metadata(),
        candidate_unique_readers=1000,
        candidate_paid_conversions=100,
        cohort_book_count=30,
        complete_followup_windows=2,
        observation_days=30,
        primary_results=((0.70, 0.65, 0.50, 0.60), (0.60, 0.30, 0.40, 0.50)),
    )
    assert report.status == "BLOCKED_PLATFORM_PRIMARY_METRIC"


def test_missing_rank_receipt_cannot_be_observed_ranked() -> None:
    report = evaluate_formal_platform_gate(
        capability_metadata=_metadata(),
        candidate_unique_readers=1000,
        candidate_paid_conversions=100,
        cohort_book_count=30,
        complete_followup_windows=2,
        observation_days=30,
        primary_results=_primary(),
        rank_receipt=False,
    )
    assert report.status != "OBSERVED_RANKED"
