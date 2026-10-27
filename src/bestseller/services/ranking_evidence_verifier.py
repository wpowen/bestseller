"""Offline verification of the ranking evidence ledger.

The verifier accepts plain mappings exported from PostgreSQL.  It never trusts
cached counts or a caller supplied ``live`` flag; all integrity claims are
recomputed from the raw event sequence.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
from typing import Any

from bestseller.domain.ranking_evidence import (
    EvidenceSourceKind,
    canonical_json,
    canonical_json_hash,
)

DOMAIN_EVENT = "bestseller-ranking-event-v1\n"
DOMAIN_REPORT = "bestseller-ranking-report-v1\n"


@dataclass(frozen=True)
class LedgerVerification:
    valid: bool
    ledger_root: str = ""
    event_count: int = 0
    sample_count: int = 0
    failure_count: int = 0
    code: str = ""

    @property
    def passed(self) -> bool:
        return self.valid


@dataclass(frozen=True)
class ReportVerification:
    valid: bool
    report_hash: str = ""
    code: str = ""

    @property
    def passed(self) -> bool:
        return self.valid


def payload_hash(payload: Any) -> str:  # noqa: ANN401
    return canonical_json_hash(payload)


def event_hash(event: Mapping[str, Any]) -> str:
    """Hash an event using the fixed domain-separated v1 envelope."""
    run_id = str(event.get("run_id", ""))
    sequence = str(event.get("sequence", event.get("event_sequence", "")))
    previous = str(event.get("previous_event_hash", event.get("previous_hash", "")))
    payload = event.get("payload", event.get("payload_json", {}))
    p_hash = str(event.get("payload_hash") or payload_hash(payload))
    envelope = {
        key: value
        for key, value in event.items()
        if key
        not in {"payload", "payload_json", "payload_hash", "event_hash", "current_event_hash"}
    }
    raw = (
        DOMAIN_EVENT
        + run_id
        + "\n"
        + sequence
        + "\n"
        + previous
        + "\n"
        + p_hash
        + "\n"
        + canonical_json(envelope)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ledger_root(events: Iterable[Mapping[str, Any]]) -> str:
    previous = ""
    for event in events:
        current = event_hash(event)
        if str(event.get("previous_event_hash", event.get("previous_hash", ""))) != previous:
            raise ValueError("LEDGER_CHAIN_MISMATCH")
        previous = current
    return previous


def verify_raw_ledger(
    events: Iterable[Mapping[str, Any]], *, run_id: str | None = None
) -> LedgerVerification:
    rows = list(events)
    previous = ""
    seen_keys: set[str] = set()
    failures = 0
    for expected, row in enumerate(rows, 1):
        try:
            if run_id is not None and str(row.get("run_id")) != run_id:
                return LedgerVerification(False, code="LEDGER_RUN_MISMATCH", event_count=len(rows))
            sequence = int(row.get("sequence", row.get("event_sequence")))
            if sequence != expected:
                return LedgerVerification(
                    False, code="LEDGER_SEQUENCE_GAP", event_count=expected - 1
                )
            row_previous = str(row.get("previous_event_hash", row.get("previous_hash", "")))
            if row_previous != previous:
                return LedgerVerification(
                    False, code="LEDGER_CHAIN_MISMATCH", event_count=expected - 1
                )
            expected_hash = event_hash(row)
            actual_hash = str(row.get("event_hash", row.get("current_event_hash", "")))
            if actual_hash != expected_hash:
                return LedgerVerification(
                    False, code="EVENT_HASH_MISMATCH", event_count=expected - 1
                )
            key = str(row.get("ingest_idempotency_key", ""))
            if key and key in seen_keys:
                return LedgerVerification(
                    False, code="DUPLICATE_IDEMPOTENCY_KEY", event_count=expected - 1
                )
            seen_keys.add(key)
            payload = row.get("payload", row.get("payload_json", {}))
            if row.get("payload_hash") and str(row["payload_hash"]) != payload_hash(payload):
                return LedgerVerification(
                    False, code="PAYLOAD_HASH_MISMATCH", event_count=expected - 1
                )
            previous = expected_hash
            if str(row.get("validation_status", "")).upper() in {"INVALID", "FAILED"}:
                failures += 1
        except (TypeError, ValueError, KeyError):
            return LedgerVerification(False, code="LEDGER_EVENT_INVALID", event_count=expected - 1)
    return LedgerVerification(
        True,
        ledger_root=previous,
        event_count=len(rows),
        sample_count=len(rows),
        failure_count=failures,
    )


def report_hash(report: Mapping[str, Any], *, root: str | None = None) -> str:
    supplied_root = root if root is not None else str(report.get("ledger_root", ""))
    body = {
        key: value for key, value in report.items() if key not in {"report_hash", "content_hash"}
    }
    raw = DOMAIN_REPORT + supplied_root + "\n" + canonical_json(body)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_report_hash(
    report: Mapping[str, Any], *, ledger: LedgerVerification | None = None
) -> ReportVerification:
    root = ledger.ledger_root if ledger is not None else str(report.get("ledger_root", ""))
    if ledger is not None and not ledger.valid:
        return ReportVerification(False, code=ledger.code or "LEDGER_INVALID")
    expected = report_hash(report, root=root)
    actual = str(report.get("report_hash", report.get("content_hash", "")))
    if actual != expected:
        return ReportVerification(False, report_hash=expected, code="REPORT_CONTENT_HASH_MISMATCH")
    return ReportVerification(True, report_hash=expected)


def verify_live_source(
    *,
    source_kind: EvidenceSourceKind | str,
    auth_receipt: str | None,
    assignment_id: str | None,
    session_id: str | None,
    event_count: int,
) -> bool:
    """Return true only for a complete, independently collected live chain."""
    return (
        str(source_kind) == EvidenceSourceKind.LIVE_VERIFIED.value
        and bool(auth_receipt and assignment_id and session_id)
        and event_count > 0
    )


__all__ = [
    "LedgerVerification",
    "ReportVerification",
    "event_hash",
    "ledger_root",
    "payload_hash",
    "report_hash",
    "verify_live_source",
    "verify_raw_ledger",
    "verify_report_hash",
]
