from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from bestseller.domain.ranking_evidence import AllowedClaim
from bestseller.services.canonical_book_readiness import evaluate_persisted_evidence
from bestseller.services.ranking_evidence_verifier import event_hash


def _run(*, status: str = "finalized") -> SimpleNamespace:
    manifest = {"cells": ["异世界经营喜剧:opening_ch1:T1"], "sealed": True}
    roster = {"member_ids": ["book-1"], "sealed": True}
    preregistration = {"protocol": "v1"}
    analysis = {"estimator": "fixed"}
    from bestseller.domain.ranking_evidence import canonical_json_hash

    return SimpleNamespace(
        id=uuid4(),
        candidate_hash="candidate-1",
        target_platform="fanqie-long-v1",
        target_list="new-book",
        required_cell_manifest_json=manifest,
        required_cell_manifest_hash=canonical_json_hash(manifest),
        roster_json=roster,
        roster_hash=canonical_json_hash(roster),
        preregistration_json=preregistration,
        preregistration_hash=canonical_json_hash(preregistration),
        analysis_plan_json=analysis,
        analysis_plan_hash=canonical_json_hash(analysis),
        frozen_at=datetime(2026, 9, 2, tzinfo=UTC),
        status=status,
    )


def _artifact(layer: str, *, source: str = "live_verified") -> SimpleNamespace:
    return SimpleNamespace(
        artifact_uri=f"memory://{layer}",
        content_hash="a" * 64,
        evidence_layer=layer,
        source_kind=source,
        producer_identity="test",
        producer_version="v1",
        sample_count=100,
        cluster_count=10,
        verdict="PASS",
        blocking_codes=[],
        unknowns=[],
    )


def _event(run_id: object) -> SimpleNamespace:
    row = {
        "run_id": str(run_id),
        "event_uuid": str(uuid4()),
        "sequence": 1,
        "previous_event_hash": "",
        "evidence_layer": "authority",
        "event_type": "RUN_FINALIZED",
        "occurred_at": datetime(2026, 9, 2, tzinfo=UTC),
        "source": "test",
        "session_hash": None,
        "assignment_hash": None,
        "cluster_hash": None,
        "subject_hash": None,
        "candidate_revision": "revision-1",
        "candidate_hash": "candidate-1",
        "payload": {},
        "collector_identity": "test",
        "collector_version": "v1",
        "key_id": None,
        "auth_receipt": "receipt",
        "ingest_idempotency_key": "final",
        "validation_status": "valid",
        "invalid_code": None,
    }
    row["event_hash"] = event_hash(row)
    row["payload_json"] = row.pop("payload")
    return SimpleNamespace(**row)


def test_fixture_high_layer_cannot_gain_commercial_claim() -> None:
    run = _run()
    artifacts = [
        _artifact("structure", source="fixture"),
        _artifact("live_production", source="fixture"),
    ]
    readback = evaluate_persisted_evidence(run, artifacts, [_event(run.id)])

    assert AllowedClaim.COMMERCIAL_RELEASE not in readback.decision.claims_allowed
    assert readback.decision.evaluation_status == "INCONCLUSIVE_LIVE_PRODUCTION_LIVE_SOURCE"


def test_unfinalized_run_never_authorizes_commercial_release() -> None:
    run = _run(status="running")
    readback = evaluate_persisted_evidence(
        run,
        [_artifact("structure")],
        [_event(run.id)],
    )

    assert readback.decision.evaluation_status == "INCONCLUSIVE_RUN_NOT_FINALIZED"
    assert AllowedClaim.COMMERCIAL_RELEASE not in readback.decision.claims_allowed


def test_manifest_tamper_resets_authority_to_unassessed() -> None:
    run = _run()
    run.required_cell_manifest_json = {"cells": ["changed"], "sealed": True}
    readback = evaluate_persisted_evidence(
        run,
        [_artifact("structure")],
        [_event(run.id)],
    )

    assert readback.decision.evaluation_status == "BLOCKED_EVIDENCE_INTEGRITY"
    assert readback.decision.claims_allowed == ()
