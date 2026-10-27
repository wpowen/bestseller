from datetime import UTC, datetime, timedelta

import pytest

from bestseller.services.reader_assignment import AssignmentError, ReaderAssignmentAuthority


def _authority():
    return ReaderAssignmentAuthority(b"x" * 32)


def _receipt(authority):
    return authority.create(
        study_key="study",
        cell_key="first-3",
        participant_hash="a" * 64,
        assigned_arm="engine",
        assigned_order="engine_first",
        candidate_hash="b" * 64,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_assignment_is_server_owned_and_one_use():
    authority = _authority()
    receipt = _receipt(authority)
    assert (
        authority.verify(receipt.token, now=datetime(2026, 1, 1, 1, tzinfo=UTC)).assigned_arm
        == "engine"
    )
    with pytest.raises(AssignmentError, match="replayed"):
        authority.verify(receipt.token, now=datetime(2026, 1, 1, 1, tzinfo=UTC))


def test_assignment_rejects_expired_and_client_arm_override():
    authority = _authority()
    receipt = _receipt(authority)
    with pytest.raises(AssignmentError, match="expired"):
        authority.verify(
            receipt.token, now=receipt.expires_at + timedelta(seconds=1), consume=False
        )
    with pytest.raises(AssignmentError, match="assigned_arm"):
        authority.validate_client_payload(receipt, {"assigned_arm": "baseline"})
    with pytest.raises(AssignmentError, match="evidence_source"):
        authority.validate_client_payload(receipt, {"evidence_source": "live"})
