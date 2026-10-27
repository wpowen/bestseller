"""Server-owned blind-reader assignments with signed, one-use receipts."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import secrets
from typing import Any


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True, slots=True)
class AssignmentReceipt:
    assignment_id: str
    study_key: str
    cell_key: str
    participant_hash: str
    assigned_arm: str
    assigned_order: str
    candidate_hash: str
    nonce: str
    expires_at: datetime
    token: str

    @property
    def receipt_hash(self) -> str:
        return hashlib.sha256(self.token.encode("ascii")).hexdigest()


class AssignmentError(ValueError):
    pass


class ReaderAssignmentAuthority:
    """Issues assignments; clients receive no arm/candidate selection API."""

    def __init__(self, secret: bytes | str, *, ttl: timedelta = timedelta(hours=2)) -> None:
        self._secret = secret.encode() if isinstance(secret, str) else bytes(secret)
        if len(self._secret) < 32:
            raise ValueError("assignment signing secret must be at least 32 bytes")
        self._ttl = ttl
        self._used: set[str] = set()

    def create(
        self,
        *,
        study_key: str,
        cell_key: str,
        participant_hash: str,
        assigned_arm: str,
        assigned_order: str,
        candidate_hash: str,
        now: datetime | None = None,
    ) -> AssignmentReceipt:
        if assigned_arm not in {"baseline", "engine"}:
            raise AssignmentError("assigned_arm must be server selected")
        if assigned_order not in {"baseline_first", "engine_first"}:
            raise AssignmentError("assigned_order is invalid")
        if len(participant_hash.strip()) != 64:
            raise AssignmentError("participant_hash must be pseudonymous SHA-256")
        issued = now or datetime.now(UTC)
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=UTC)
        payload: dict[str, Any] = {
            "v": "reader-study-assignment.v1",
            "assignment_id": secrets.token_urlsafe(18),
            "study_key": study_key,
            "cell_key": cell_key,
            "participant_hash": participant_hash.lower(),
            "assigned_arm": assigned_arm,
            "assigned_order": assigned_order,
            "candidate_hash": candidate_hash,
            "nonce": secrets.token_urlsafe(24),
            "expires_at": (issued + self._ttl).isoformat(),
        }
        body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        token = body + "." + _b64(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())
        payload.pop("v", None)
        payload["expires_at"] = datetime.fromisoformat(payload["expires_at"])
        return AssignmentReceipt(
            **payload,
            token=token,
        )

    def verify(
        self, token: str, *, now: datetime | None = None, consume: bool = True
    ) -> AssignmentReceipt:
        try:
            body, signature = token.split(".", 1)
            expected = hmac.new(self._secret, body.encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _unb64(signature)):
                raise AssignmentError("assignment signature invalid")
            payload = json.loads(_unb64(body))
            if payload.get("v") != "reader-study-assignment.v1":
                raise AssignmentError("assignment version invalid")
            assignment_id = str(payload["assignment_id"])
            if assignment_id in self._used:
                raise AssignmentError("assignment token replayed")
            current = now or datetime.now(UTC)
            expires = datetime.fromisoformat(str(payload["expires_at"]))
            if current.tzinfo is None:
                current = current.replace(tzinfo=UTC)
            if current >= expires:
                raise AssignmentError("assignment token expired")
            payload.pop("v", None)
            payload["expires_at"] = expires
            receipt = AssignmentReceipt(**payload, token=token)
            if consume:
                self._used.add(assignment_id)
            return receipt
        except AssignmentError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AssignmentError("assignment token malformed") from exc

    def validate_client_payload(
        self, receipt: AssignmentReceipt, payload: Mapping[str, Any]
    ) -> None:
        """Reject arm/candidate/live values supplied by an untrusted client."""
        for key in ("assigned_arm", "assigned_order", "candidate_hash", "evidence_source"):
            if key in payload:
                expected = getattr(receipt, key, None)
                if key == "evidence_source":
                    raise AssignmentError("client cannot declare evidence_source")
                if payload[key] != expected:
                    raise AssignmentError(f"client attempted to override {key}")
        if payload.get("live") is not None:
            raise AssignmentError("client cannot select live evidence")


__all__ = ["AssignmentError", "AssignmentReceipt", "ReaderAssignmentAuthority"]
