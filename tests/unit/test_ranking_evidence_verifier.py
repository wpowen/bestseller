from bestseller.domain.ranking_evidence import EvidenceSourceKind
from bestseller.services.ranking_evidence_verifier import (
    event_hash,
    report_hash,
    verify_live_source,
    verify_raw_ledger,
    verify_report_hash,
)


def make_events() -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    previous = ""
    for sequence in range(1, 3):
        row: dict[str, object] = {
            "run_id": "run-1",
            "sequence": sequence,
            "previous_event_hash": previous,
            "event_type": "SAMPLE" if sequence == 1 else "RUN_FINALIZED",
            "occurred_at": "2026-09-02T00:00:00Z",
            "payload": {"value": sequence},
            "ingest_idempotency_key": f"idempotency-{sequence}",
        }
        row["event_hash"] = event_hash(row)
        previous = str(row["event_hash"])
        events.append(row)
    return events


def test_raw_replay_recomputes_chain_and_detects_mutations() -> None:
    events = make_events()
    result = verify_raw_ledger(events, run_id="run-1")
    assert result.valid is True
    assert result.event_count == 2
    assert result.ledger_root == events[-1]["event_hash"]

    mutated = [*events]
    mutated[1] = {**mutated[1], "payload": {"value": 999}}
    assert verify_raw_ledger(mutated, run_id="run-1").code == "EVENT_HASH_MISMATCH"


def test_report_hash_covers_report_body_even_when_manifest_is_correct() -> None:
    ledger = verify_raw_ledger(make_events(), run_id="run-1")
    report = {"ledger_root": ledger.ledger_root, "manifest_hash": "manifest", "sample_count": 2}
    signed = {**report, "report_hash": report_hash(report)}
    assert verify_report_hash(signed, ledger=ledger).valid is True
    assert (
        verify_report_hash({**signed, "sample_count": 1}, ledger=ledger).code
        == "REPORT_CONTENT_HASH_MISMATCH"
    )


def test_live_requires_server_side_chain_evidence() -> None:
    assert verify_live_source(
        source_kind=EvidenceSourceKind.LIVE_VERIFIED,
        auth_receipt="receipt",
        assignment_id="assignment",
        session_id="session",
        event_count=1,
    )
    assert not verify_live_source(
        source_kind=EvidenceSourceKind.FIXTURE,
        auth_receipt="receipt",
        assignment_id="assignment",
        session_id="session",
        event_count=1,
    )
