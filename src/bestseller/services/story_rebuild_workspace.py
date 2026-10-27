"""Isolated shadow-rebuild state machine with atomic cutover semantics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
import json


class RebuildState(StrEnum):
    DRAFT = "DRAFT"
    ARCHITECTURE_VALIDATED = "ARCHITECTURE_VALIDATED"
    FOUNDATION_VALIDATED = "FOUNDATION_VALIDATED"
    OPENING_VALIDATED = "OPENING_VALIDATED"
    PACKAGE_VALIDATED = "PACKAGE_VALIDATED"
    PROMOTABLE = "PROMOTABLE"
    CANONICAL = "CANONICAL"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True, slots=True)
class RebuildReceipt:
    action: str
    workspace_id: str
    source_revision: str
    candidate_revision: str | None
    from_state: RebuildState
    to_state: RebuildState
    success: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ShadowRebuildWorkspace:
    workspace_id: str
    source_revision: str
    source_hash: str
    candidate_revision: str | None = None
    candidate_hash: str | None = None
    state: RebuildState = RebuildState.DRAFT
    current_pointer: str | None = None
    receipts: tuple[RebuildReceipt, ...] = ()


_TRANSITIONS = {
    RebuildState.DRAFT: {RebuildState.ARCHITECTURE_VALIDATED},
    RebuildState.ARCHITECTURE_VALIDATED: {RebuildState.FOUNDATION_VALIDATED},
    RebuildState.FOUNDATION_VALIDATED: {RebuildState.OPENING_VALIDATED},
    RebuildState.OPENING_VALIDATED: {RebuildState.PACKAGE_VALIDATED},
    RebuildState.PACKAGE_VALIDATED: {RebuildState.PROMOTABLE},
    RebuildState.PROMOTABLE: {RebuildState.CANONICAL, RebuildState.ROLLED_BACK},
    RebuildState.CANONICAL: {RebuildState.ROLLED_BACK},
    RebuildState.ROLLED_BACK: set(),
}


def source_content_hash(source: object) -> str:
    payload = json.dumps(
        source, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return sha256(payload.encode()).hexdigest()


def create_shadow_workspace(
    workspace_id: str, source_revision: str, source: object
) -> ShadowRebuildWorkspace:
    return ShadowRebuildWorkspace(
        workspace_id=workspace_id,
        source_revision=source_revision,
        source_hash=source_content_hash(source),
        current_pointer=source_revision,
    )


def advance_workspace(
    workspace: ShadowRebuildWorkspace,
    to_state: RebuildState,
    *,
    candidate_revision: str | None = None,
    candidate_hash: str | None = None,
    reason: str | None = None,
) -> ShadowRebuildWorkspace:
    if to_state not in _TRANSITIONS[workspace.state]:
        raise ValueError(f"invalid rebuild transition {workspace.state} -> {to_state}")
    if to_state is RebuildState.CANONICAL and workspace.state is not RebuildState.PROMOTABLE:
        raise ValueError("CUTOVER_REQUIRES_PROMOTABLE")
    revision = candidate_revision or workspace.candidate_revision
    digest = candidate_hash or workspace.candidate_hash
    if to_state is RebuildState.PROMOTABLE and (not revision or not digest):
        raise ValueError("PROMOTION_REQUIRES_FROZEN_CANDIDATE")
    receipt = RebuildReceipt(
        "advance",
        workspace.workspace_id,
        workspace.source_revision,
        revision,
        workspace.state,
        to_state,
        True,
        reason,
    )
    return replace(
        workspace,
        state=to_state,
        candidate_revision=revision,
        candidate_hash=digest,
        receipts=(*workspace.receipts, receipt),
    )


def cutover(
    workspace: ShadowRebuildWorkspace,
    *,
    candidate_revision: str,
    candidate_hash: str,
    checks_passed: bool,
) -> ShadowRebuildWorkspace:
    if workspace.state is not RebuildState.PROMOTABLE:
        raise ValueError("CUTOVER_REQUIRES_PROMOTABLE")
    if not checks_passed:
        receipt = RebuildReceipt(
            "cutover",
            workspace.workspace_id,
            workspace.source_revision,
            candidate_revision,
            workspace.state,
            workspace.state,
            False,
            "validation_failed",
        )
        return replace(workspace, receipts=(*workspace.receipts, receipt))
    receipt = RebuildReceipt(
        "cutover",
        workspace.workspace_id,
        workspace.source_revision,
        candidate_revision,
        workspace.state,
        RebuildState.CANONICAL,
        True,
    )
    return replace(
        workspace,
        candidate_revision=candidate_revision,
        candidate_hash=candidate_hash,
        state=RebuildState.CANONICAL,
        current_pointer=candidate_revision,
        receipts=(*workspace.receipts, receipt),
    )


def rollback(
    workspace: ShadowRebuildWorkspace, *, reason: str = "rollback"
) -> ShadowRebuildWorkspace:
    if workspace.state not in {RebuildState.PROMOTABLE, RebuildState.CANONICAL}:
        raise ValueError("ROLLBACK_NOT_AVAILABLE")
    receipt = RebuildReceipt(
        "rollback",
        workspace.workspace_id,
        workspace.source_revision,
        workspace.candidate_revision,
        workspace.state,
        RebuildState.ROLLED_BACK,
        True,
        reason,
    )
    return replace(
        workspace,
        state=RebuildState.ROLLED_BACK,
        current_pointer=workspace.source_revision,
        receipts=(*workspace.receipts, receipt),
    )


def assert_source_unchanged(workspace: ShadowRebuildWorkspace, source: object) -> None:
    if source_content_hash(source) != workspace.source_hash:
        raise ValueError("SOURCE_REVISION_MUTATED")


__all__ = [
    "RebuildReceipt",
    "RebuildState",
    "ShadowRebuildWorkspace",
    "advance_workspace",
    "assert_source_unchanged",
    "create_shadow_workspace",
    "cutover",
    "rollback",
    "source_content_hash",
]
