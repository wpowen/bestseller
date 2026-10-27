from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bestseller.domain.ranking_evidence import (
    AllowedClaim,
    EvidenceArtifactRef,
    EvidenceLayer,
    EvidenceOutcome,
    EvidenceSourceKind,
    Maturity,
    RankingEvidenceBundle,
    RankingExperimentPreregistration,
    RequiredCellManifest,
    SealedExperimentRoster,
    canonical_json,
    canonical_json_hash,
    canonical_timestamp,
    reduce_maturity,
)


def artifact(
    layer: EvidenceLayer, verdict: EvidenceOutcome = EvidenceOutcome.PASS
) -> EvidenceArtifactRef:
    return EvidenceArtifactRef(
        artifact_uri=f"memory://{layer.value}",
        content_hash="a" * 64,
        layer=layer,
        source_kind=EvidenceSourceKind.LIVE_VERIFIED,
        verdict=verdict,
        candidate_revision_hash="revision-1",
    )


def test_canonical_json_is_order_and_unicode_stable() -> None:
    assert canonical_json({"b": "e\u0301", "a": Decimal("1.20")}) == '{"a":"1.20","b":"é"}'
    assert canonical_json_hash({"a": 1, "b": 2}) == canonical_json_hash({"b": 2, "a": 1})


def test_canonical_timestamp_requires_timezone_and_normalises_utc() -> None:
    assert canonical_timestamp("2026-09-02T12:00:00+08:00") == "2026-09-02T04:00:00Z"
    assert canonical_timestamp(datetime(2026, 9, 2, tzinfo=UTC)) == "2026-09-02T00:00:00Z"
    with pytest.raises(ValueError):
        canonical_timestamp("2026-09-02T12:00:00")


def test_manifest_is_sealed_by_content_not_insertion_order() -> None:
    left = RequiredCellManifest(cells=("b", "a"), sealed=True)
    right = RequiredCellManifest(cells=("a", "b"), sealed=True)
    assert left.cells == ("a", "b")
    assert left.stable_hash() == right.stable_hash()


def test_reducer_is_contiguous_and_does_not_skip_missing_layer() -> None:
    bundle = RankingEvidenceBundle(
        run_id="run-1",
        candidate_revision_hash="revision-1",
        artifacts=(artifact(EvidenceLayer.STRUCTURE),),
    )
    decision = reduce_maturity(bundle)
    assert decision.highest_contiguous_maturity == Maturity.STRUCTURE_VALIDATED
    assert decision.evaluation_status == "INCONCLUSIVE_LIVE_PRODUCTION"
    assert decision.claims_allowed == (AllowedClaim.STRUCTURE_PRODUCTION_READY,)


def test_reducer_hard_failure_preserves_previous_maturity() -> None:
    bundle = _sealed_bundle_artifacts(
        artifact(EvidenceLayer.STRUCTURE),
        artifact(EvidenceLayer.LIVE_PRODUCTION, EvidenceOutcome.FAIL).model_copy(
            update={"blocking_codes": ("PROVIDER_RECEIPT_MISSING",)}
        ),
    )
    decision = reduce_maturity(bundle)
    assert decision.highest_contiguous_maturity == Maturity.STRUCTURE_VALIDATED
    assert decision.evaluation_status == "BLOCKED_PROVIDER_RECEIPT_MISSING"


def _sealed_bundle_artifacts(*artifacts: EvidenceArtifactRef) -> RankingEvidenceBundle:
    manifest = RequiredCellManifest(cells=("required",), sealed=True)
    roster = SealedExperimentRoster(member_ids=("member",), sealed=True)
    return RankingEvidenceBundle(
        run_id="run-1",
        candidate_revision_hash="revision-1",
        preregistration=RankingExperimentPreregistration(
            run_id="run-1",
            candidate_revision_hash="revision-1",
            analysis_plan_hash="analysis",
            required_cell_manifest_hash=manifest.stable_hash(),
            sealed_roster_hash=roster.stable_hash(),
            frozen_at="2026-09-02T00:00:00Z",
        ),
        required_cell_manifest=manifest,
        sealed_roster=roster,
        artifacts=artifacts,
    )


def test_fixture_cannot_upgrade_a_live_evidence_layer() -> None:
    live_production = artifact(EvidenceLayer.LIVE_PRODUCTION).model_copy(
        update={"source_kind": EvidenceSourceKind.FIXTURE}
    )
    decision = reduce_maturity(
        _sealed_bundle_artifacts(artifact(EvidenceLayer.STRUCTURE), live_production)
    )

    assert decision.highest_contiguous_maturity == Maturity.STRUCTURE_VALIDATED
    assert decision.evaluation_status == "INCONCLUSIVE_LIVE_PRODUCTION_LIVE_SOURCE"
    assert AllowedClaim.PRODUCTION_VALIDATED not in decision.claims_allowed


def test_unsealed_formal_run_stops_before_live_production() -> None:
    decision = reduce_maturity(
        RankingEvidenceBundle(
            run_id="run-1",
            candidate_revision_hash="revision-1",
            artifacts=(
                artifact(EvidenceLayer.STRUCTURE),
                artifact(EvidenceLayer.LIVE_PRODUCTION),
            ),
        )
    )

    assert decision.highest_contiguous_maturity == Maturity.STRUCTURE_VALIDATED
    assert decision.evaluation_status == "INCONCLUSIVE_PREREGISTRATION_NOT_SEALED"
