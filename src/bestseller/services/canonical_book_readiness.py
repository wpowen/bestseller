"""Canonical, fail-closed book maturity decision.

The legacy ``ranking_readiness`` service is useful editorial telemetry.  This
module is the only authority allowed to turn persisted evidence into release
claims.  It verifies the exact candidate, sealed manifests, the append-only
ledger, and then applies the contiguous maturity reducer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.ranking_evidence import (
    AllowedClaim,
    CanonicalBookReadinessDecision,
    EvidenceArtifactRef,
    EvidenceLayer,
    EvidenceOutcome,
    EvidenceSourceKind,
    Maturity,
    RankingEvidenceBundle,
    RankingExperimentPreregistration,
    RequiredCellManifest,
    SealedExperimentRoster,
    canonical_json_hash,
    reduce_maturity,
)
from bestseller.infra.db.models import (
    ProjectModel,
    RankingEvaluationRunModel,
    RankingEvidenceArtifactModel,
    RankingRawEvidenceEventModel,
)
from bestseller.services.ranking_evidence_verifier import (
    report_hash,
    verify_raw_ledger,
)


@dataclass(frozen=True, slots=True)
class CanonicalReadinessReadback:
    decision: CanonicalBookReadinessDecision
    run_id: str | None = None
    run_status: str | None = None
    diagnostic_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_status": self.run_status,
            "diagnostic_only": self.diagnostic_only,
            **self.decision.model_dump(mode="json"),
        }


class CommercialReleaseNotAuthorizedError(RuntimeError):
    """Raised before any external publication side effect is attempted."""

    def __init__(self, decision: CanonicalBookReadinessDecision) -> None:
        self.decision = decision
        super().__init__(f"commercial release is not authorized: {decision.evaluation_status}")


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _cell_key(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    item = _mapping(value)
    explicit = str(item.get("key") or item.get("cell_key") or "").strip()
    if explicit:
        return explicit
    parts = [
        str(item.get(key) or "").strip()
        for key in ("genre", "narrative_stage", "tier", "scope", "reader_segment")
    ]
    return ":".join(part for part in parts if part)


def _manifest_from_run(run: object) -> RequiredCellManifest | None:
    payload = _mapping(getattr(run, "required_cell_manifest_json", None))
    raw_cells = payload.get("cells") or payload.get("required_cells") or ()
    cells = tuple(filter(None, (_cell_key(item) for item in raw_cells)))
    if not cells:
        return None
    return RequiredCellManifest(
        cells=cells,
        sealed=bool(payload.get("sealed", getattr(run, "frozen_at", None) is not None)),
        content_hash=str(getattr(run, "required_cell_manifest_hash", "") or ""),
    )


def _roster_member_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    explicit = payload.get("member_ids")
    if isinstance(explicit, Sequence) and not isinstance(explicit, str | bytes):
        return tuple(str(item) for item in explicit if str(item).strip())
    members: list[str] = []
    for key, values in payload.items():
        if key in {"sealed", "version", "roster_version"}:
            continue
        if not isinstance(values, Sequence) or isinstance(values, str | bytes):
            continue
        for value in values:
            if isinstance(value, Mapping):
                identity = next(
                    (
                        str(value.get(name) or "").strip()
                        for name in (
                            "id",
                            "member_id",
                            "book_id",
                            "author_id",
                            "participant_id",
                            "slot_id",
                        )
                        if str(value.get(name) or "").strip()
                    ),
                    "",
                )
                if identity:
                    members.append(f"{key}:{identity}")
            elif str(value).strip():
                members.append(f"{key}:{value}")
    return tuple(members)


def _roster_from_run(run: object) -> SealedExperimentRoster | None:
    payload = _mapping(getattr(run, "roster_json", None))
    members = _roster_member_ids(payload)
    if not members:
        return None
    return SealedExperimentRoster(
        member_ids=members,
        sealed=bool(payload.get("sealed", getattr(run, "frozen_at", None) is not None)),
        content_hash=str(getattr(run, "roster_hash", "") or ""),
    )


def _preregistration_from_run(
    run: object,
    manifest: RequiredCellManifest | None,
    roster: SealedExperimentRoster | None,
) -> RankingExperimentPreregistration | None:
    frozen_at = getattr(run, "frozen_at", None)
    if frozen_at is None or manifest is None or roster is None:
        return None
    return RankingExperimentPreregistration(
        run_id=str(getattr(run, "id", "")),
        candidate_revision_hash=str(getattr(run, "candidate_hash", "") or ""),
        analysis_plan_hash=str(getattr(run, "analysis_plan_hash", "") or ""),
        required_cell_manifest_hash=str(getattr(run, "required_cell_manifest_hash", "") or ""),
        sealed_roster_hash=str(getattr(run, "roster_hash", "") or ""),
        target_platform=str(getattr(run, "target_platform", "") or ""),
        target_list=str(getattr(run, "target_list", "") or ""),
        frozen_at=frozen_at,
    )


def _artifact_ref(row: Any, candidate_hash: str) -> EvidenceArtifactRef:  # noqa: ANN401
    return EvidenceArtifactRef(
        artifact_uri=str(getattr(row, "artifact_uri", "") or ""),
        content_hash=str(getattr(row, "content_hash", "") or ""),
        layer=EvidenceLayer(str(row.evidence_layer)),
        source_kind=EvidenceSourceKind(str(row.source_kind)),
        producer=(
            f"{getattr(row, 'producer_identity', '')}@{getattr(row, 'producer_version', '')}"
        ),
        sample_count=int(getattr(row, "sample_count", 0) or 0),
        cluster_count=int(getattr(row, "cluster_count", 0) or 0),
        verdict=EvidenceOutcome(str(getattr(row, "verdict", "MISSING") or "MISSING")),
        blocking_codes=tuple(getattr(row, "blocking_codes", None) or ()),
        unknowns=tuple(getattr(row, "unknowns", None) or ()),
        candidate_revision_hash=candidate_hash,
    )


def raw_event_mapping(row: Any) -> dict[str, Any]:  # noqa: ANN401
    """Export only the fixed semantic envelope used by canonical hash v1."""

    return {
        "run_id": str(row.run_id),
        "event_uuid": str(getattr(row, "event_uuid", getattr(row, "id", ""))),
        "sequence": int(row.sequence),
        "previous_event_hash": str(getattr(row, "previous_event_hash", "") or ""),
        "event_hash": str(getattr(row, "event_hash", "") or ""),
        "evidence_layer": str(getattr(row, "evidence_layer", "") or ""),
        "event_type": str(getattr(row, "event_type", "") or ""),
        "occurred_at": row.occurred_at,
        "source": str(getattr(row, "source", "") or ""),
        "session_hash": getattr(row, "session_hash", None),
        "assignment_hash": getattr(row, "assignment_hash", None),
        "cluster_hash": getattr(row, "cluster_hash", None),
        "subject_hash": getattr(row, "subject_hash", None),
        "candidate_revision": str(getattr(row, "candidate_revision", "") or ""),
        "candidate_hash": str(getattr(row, "candidate_hash", "") or ""),
        "payload": _mapping(getattr(row, "payload_json", None)),
        "payload_hash": str(getattr(row, "payload_hash", "") or ""),
        "collector_identity": str(getattr(row, "collector_identity", "") or ""),
        "collector_version": str(getattr(row, "collector_version", "") or ""),
        "key_id": getattr(row, "key_id", None),
        "auth_receipt": getattr(row, "auth_receipt", None),
        "ingest_idempotency_key": str(getattr(row, "ingest_idempotency_key", "") or ""),
        "validation_status": str(getattr(row, "validation_status", "") or ""),
        "invalid_code": getattr(row, "invalid_code", None),
    }


def evaluate_persisted_evidence(
    run: object,
    artifacts: Iterable[object],
    raw_events: Iterable[object],
) -> CanonicalReadinessReadback:
    manifest_payload = _mapping(getattr(run, "required_cell_manifest_json", None))
    roster_payload = _mapping(getattr(run, "roster_json", None))
    prereg_payload = _mapping(getattr(run, "preregistration_json", None))
    analysis_payload = _mapping(getattr(run, "analysis_plan_json", None))
    integrity_codes: list[str] = []
    for payload, expected, code in (
        (
            manifest_payload,
            str(getattr(run, "required_cell_manifest_hash", "") or ""),
            "REQUIRED_CELL_MANIFEST_HASH_MISMATCH",
        ),
        (roster_payload, str(getattr(run, "roster_hash", "") or ""), "ROSTER_HASH_MISMATCH"),
        (
            prereg_payload,
            str(getattr(run, "preregistration_hash", "") or ""),
            "PREREGISTRATION_HASH_MISMATCH",
        ),
        (
            analysis_payload,
            str(getattr(run, "analysis_plan_hash", "") or ""),
            "ANALYSIS_PLAN_HASH_MISMATCH",
        ),
    ):
        if not payload or not expected or canonical_json_hash(payload) != expected:
            integrity_codes.append(code)

    manifest = _manifest_from_run(run)
    roster = _roster_from_run(run)
    preregistration = _preregistration_from_run(run, manifest, roster)
    candidate_hash = str(getattr(run, "candidate_hash", "") or "")
    artifact_refs: list[EvidenceArtifactRef] = []
    for artifact in artifacts:
        try:
            artifact_refs.append(_artifact_ref(artifact, candidate_hash))
        except ValueError:
            integrity_codes.append("EVIDENCE_ARTIFACT_ENUM_INVALID")

    event_rows = [raw_event_mapping(row) for row in raw_events]
    ledger = verify_raw_ledger(event_rows, run_id=str(getattr(run, "id", "")))
    if event_rows and not ledger.valid:
        integrity_codes.append(ledger.code or "LEDGER_INVALID")
    bundle = RankingEvidenceBundle(
        run_id=str(getattr(run, "id", "")),
        candidate_revision_hash=candidate_hash,
        preregistration=preregistration,
        required_cell_manifest=manifest,
        sealed_roster=roster,
        artifacts=tuple(artifact_refs),
        integrity_violation=bool(integrity_codes),
        integrity_codes=tuple(dict.fromkeys(integrity_codes)),
    )
    decision = reduce_maturity(bundle)
    if not event_rows and any(item.layer != EvidenceLayer.STRUCTURE for item in artifact_refs):
        claims = tuple(
            claim
            for claim in decision.claims_allowed
            if claim == AllowedClaim.STRUCTURE_PRODUCTION_READY
        )
        decision = decision.model_copy(
            update={
                "highest_contiguous_maturity": (
                    decision.highest_contiguous_maturity
                    if AllowedClaim.STRUCTURE_PRODUCTION_READY in claims
                    else Maturity.UNASSESSED
                ),
                "evaluation_status": "INCONCLUSIVE_RAW_EVENT_LEDGER_MISSING",
                "claims_allowed": claims,
                "unknowns": (*decision.unknowns, "raw_event_ledger_missing"),
            }
        )
    if str(getattr(run, "status", "")) != "finalized":
        decision = decision.model_copy(
            update={
                "evaluation_status": "INCONCLUSIVE_RUN_NOT_FINALIZED",
                "claims_allowed": tuple(
                    claim
                    for claim in decision.claims_allowed
                    if claim == AllowedClaim.STRUCTURE_PRODUCTION_READY
                ),
            }
        )
    root = ledger.ledger_root if ledger.valid else ""
    body = {
        "run_id": str(getattr(run, "id", "")),
        "candidate_revision_hash": candidate_hash,
        "ledger_root": root,
        "decision": decision.model_dump(mode="json", exclude={"report_hash", "ledger_root"}),
    }
    decision = decision.model_copy(
        update={"ledger_root": root, "report_hash": report_hash(body, root=root)}
    )
    return CanonicalReadinessReadback(
        decision=decision,
        run_id=str(getattr(run, "id", "")),
        run_status=str(getattr(run, "status", "")),
    )


async def load_canonical_book_readiness(
    session: AsyncSession,
    project: ProjectModel,
) -> CanonicalReadinessReadback:
    metadata = _mapping(getattr(project, "metadata_json", None))
    active_id = metadata.get("active_ranking_run_id")
    statement = select(RankingEvaluationRunModel).where(
        RankingEvaluationRunModel.project_id == project.id
    )
    if active_id:
        try:
            statement = statement.where(RankingEvaluationRunModel.id == UUID(str(active_id)))
        except ValueError:
            active_id = None
    if not active_id:
        statement = statement.order_by(RankingEvaluationRunModel.created_at.desc()).limit(1)
    run = await session.scalar(statement)
    if run is None:
        return CanonicalReadinessReadback(
            decision=CanonicalBookReadinessDecision(
                candidate_revision_hash="",
                evaluation_status="UNASSESSED_NO_EVIDENCE_RUN",
            )
        )
    artifact_rows = (
        await session.scalars(
            select(RankingEvidenceArtifactModel).where(
                RankingEvidenceArtifactModel.run_id == run.id
            )
        )
    ).all()
    event_rows = (
        await session.scalars(
            select(RankingRawEvidenceEventModel)
            .where(RankingRawEvidenceEventModel.run_id == run.id)
            .order_by(RankingRawEvidenceEventModel.sequence)
        )
    ).all()
    readback = evaluate_persisted_evidence(run, artifact_rows, event_rows)
    active_packaging_hash = str(metadata.get("active_packaging_contract_hash") or "")
    run_packaging_hash = str(getattr(run, "packaging_contract_hash", "") or "")
    if active_packaging_hash and active_packaging_hash != run_packaging_hash:
        retained_claims = tuple(
            claim
            for claim in readback.decision.claims_allowed
            if claim
            in {
                AllowedClaim.STRUCTURE_PRODUCTION_READY,
                AllowedClaim.PRODUCTION_VALIDATED,
                AllowedClaim.TEXT_PARITY_VALIDATED,
            }
        )
        if AllowedClaim.TEXT_PARITY_VALIDATED in retained_claims:
            maturity = Maturity.TEXT_PARITY_VALIDATED
        elif AllowedClaim.PRODUCTION_VALIDATED in retained_claims:
            maturity = Maturity.PRODUCTION_VALIDATED
        elif AllowedClaim.STRUCTURE_PRODUCTION_READY in retained_claims:
            maturity = Maturity.STRUCTURE_VALIDATED
        else:
            maturity = Maturity.UNASSESSED
        readback = CanonicalReadinessReadback(
            decision=readback.decision.model_copy(
                update={
                    "highest_contiguous_maturity": maturity,
                    "evaluation_status": "STALE_PACKAGING_EVIDENCE",
                    "claims_allowed": retained_claims,
                    "blocking_codes": tuple(
                        dict.fromkeys(
                            (*readback.decision.blocking_codes, "STALE_PACKAGING_EVIDENCE")
                        )
                    ),
                }
            ),
            run_id=readback.run_id,
            run_status=readback.run_status,
            diagnostic_only=readback.diagnostic_only,
        )
    return readback


async def require_commercial_release_authority(
    session: AsyncSession,
    project: ProjectModel,
) -> CanonicalReadinessReadback:
    readback = await load_canonical_book_readiness(session, project)
    if AllowedClaim.COMMERCIAL_RELEASE not in readback.decision.claims_allowed:
        raise CommercialReleaseNotAuthorizedError(readback.decision)
    return readback


__all__ = [
    "CanonicalReadinessReadback",
    "CommercialReleaseNotAuthorizedError",
    "evaluate_persisted_evidence",
    "load_canonical_book_readiness",
    "raw_event_mapping",
    "require_commercial_release_authority",
]
