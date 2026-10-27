from bestseller.domain.ranking_evidence import EvidenceLayer, invalidate_evidence


def test_content_and_prompt_changes_invalidate_all_downstream_evidence() -> None:
    assert invalidate_evidence("prose") == frozenset(
        {
            EvidenceLayer.STRUCTURE,
            EvidenceLayer.LIVE_PRODUCTION,
            EvidenceLayer.TEXT_PARITY,
            EvidenceLayer.READER,
            EvidenceLayer.SERIAL_DEPTH,
            EvidenceLayer.PLATFORM_COHORT,
        }
    )


def test_packaging_changes_do_not_invalidate_structure_evidence() -> None:
    affected = invalidate_evidence("title")
    assert EvidenceLayer.READER in affected
    assert EvidenceLayer.PLATFORM_COHORT in affected
    assert EvidenceLayer.STRUCTURE not in affected


def test_unknown_changes_have_no_implicit_authority() -> None:
    assert invalidate_evidence("unregistered_change") == frozenset()
