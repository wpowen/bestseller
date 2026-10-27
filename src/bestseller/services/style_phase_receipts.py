"""Exact draft/application receipts for the bounded chapter style phase.

Uses existing draft metadata and workflow steps; no new table or book exceptions.
A resurrected draft is deliberately ineligible: old selection is not a new application.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
import hashlib
import inspect
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    LlmRunModel,
    ProjectModel,
    WorkflowStepRunModel,
)
from bestseller.services.ai_flavor_gate import AiFlavorGateConfig, AiFlavorGateOutcome
from bestseller.services.reviews import chapter_review_body_fingerprint
from bestseller.settings import AppSettings

SCHEMA = "chapter-style-phase.v1"
RECEIPT_KEY = "style_phase_receipt"


class StylePhaseReceiptError(RuntimeError):
    """Receipt persistence must not be swallowed by the advisory gate handler."""


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False).encode()
    ).hexdigest()


def _public_config(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: _public_config(v)
            for k, v in value.items()
            if not any(
                word in k.lower()
                for word in (
                    "secret",
                    "password",
                    "api_key",
                    "access_token",
                    "refresh_token",
                    "auth_token",
                )
            )
        }
    if isinstance(value, list):
        return [_public_config(v) for v in value]
    return value


def style_policy_fingerprint(
    *,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    config: AiFlavorGateConfig,
) -> str:
    """Hash effective settings and actual implementation/assets, never credentials."""
    from bestseller.services.drafts import render_hype_preservation_block
    from bestseller.services.quality_levers.cinematic_pov import render_cinematic_pov_block
    from bestseller.settings import apply_runtime_llm_profile

    effective = apply_runtime_llm_profile(settings)
    root = Path(__file__).resolve().parents[3]
    services = Path(__file__).resolve().parent
    files = [
        services / "deslop_revise.py",
        services / "deslop_information_preservation.py",
        services / "ai_flavor_gate.py",
        services / "hype_engine.py",
        root / "config/model_catalog.yaml",
        root / "config/cinematic_pov.yaml",
    ]
    files += sorted((services / "ai_flavor").glob("*.py"))
    # Deployment roots are locators, not style policy. Keep stable logical keys
    # while reading/hash-checking the actual configured files on this host.
    assets = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        if p.is_file()
        else None
        for p in files
    }
    configured_data = Path(config.data_dir)
    assets.update(
        {
            f"configured_ai_flavor/{p.relative_to(configured_data).as_posix()}": hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
            for p in sorted(configured_data.glob("*.json"))
        }
    )
    # deslop's own detector uses the default assets, independently of gate data_dir.
    assets.update(
        {
            p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((root / "data/ai_flavor").glob("*.json"))
        }
    )
    gate_policy = {**asdict(config), "data_dir": "configured_ai_flavor"}
    writer_policy = {
        "writer": effective.llm.writer.model_dump(mode="json"),
        "mock": effective.llm.mock,
        "writer_total_input_budget_tokens": effective.llm.writer_total_input_budget_tokens,
        "writer_prompt_safety_margin": effective.llm.writer_prompt_safety_margin,
        "retry": effective.llm.retry.model_dump(mode="json"),
    }
    language = getattr(project, "language", None) or "zh-CN"
    return _hash(
        {
            "schema": SCHEMA,
            "config": gate_policy,
            "assets": assets,
            "llm": _public_config(writer_policy),
            "model_selection": (project.metadata_json or {}).get("llm_model_id"),
            "language": language,
            "chapter_number": getattr(chapter, "chapter_number", None),
            "target_word_count": getattr(chapter, "target_word_count", None),
            "rounds": 2,
            "role": "writer",
            "tier": "strong",
            "cinematic": render_cinematic_pov_block(language=language),
            "hype": render_hype_preservation_block(chapter, language=language),
            "hype_renderer": inspect.getsource(render_hype_preservation_block),
        }
    )


async def draft_application_binding(
    session: AsyncSession, draft: ChapterDraftVersionModel
) -> dict | None:
    metadata = draft.promotion_metadata or {}
    # Every production selection preserves supersession markers. Do not reuse
    # A's old completion after A -> B -> A, even if the row/text is identical.
    if not draft.is_current or metadata.get("superseded_by_version_id"):
        return None
    parent_id = metadata.get("parent_draft_id")
    parent = None
    if parent_id:
        try:
            parent = await session.get(ChapterDraftVersionModel, UUID(str(parent_id)))
        except (ValueError, TypeError):
            return None
        if (
            parent is None
            or parent.chapter_id != draft.chapter_id
            or parent.project_id != draft.project_id
            or metadata.get("parent_version_no") != parent.version_no
            or metadata.get("source_body_fingerprint")
            != chapter_review_body_fingerprint(parent.content_md)
        ):
            return None
    return {
        "draft_id": str(draft.id),
        "project_id": str(draft.project_id),
        "chapter_id": str(draft.chapter_id),
        "version_no": draft.version_no,
        "body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest(),
        "llm_run_id": str(draft.llm_run_id) if draft.llm_run_id else None,
        "scene_draft_ids": list(draft.assembled_from_scene_draft_ids or []),
        "parent_id": str(parent.id) if parent else None,
        "parent_body_sha256": hashlib.sha256(parent.content_md.encode()).hexdigest()
        if parent
        else None,
        "source": {
            key: metadata.get(key)
            for key in (
                "parent_version_no",
                "source_body_fingerprint",
                "transformation",
                "deslop_round",
                "selected_reason",
            )
        },
    }


async def completed_style_phase(
    session: AsyncSession, *, draft: ChapterDraftVersionModel, policy: str
) -> dict | None:
    receipt = (draft.promotion_metadata or {}).get(RECEIPT_KEY)
    if not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA:
        return None
    binding = await draft_application_binding(session, draft)
    if (
        binding is None
        or receipt.get("state") != "completed"
        or receipt.get("policy") != policy
        or receipt.get("output") != binding
    ):
        return None
    try:
        step = await session.get(WorkflowStepRunModel, UUID(receipt["step_id"]))
    except (ValueError, TypeError, KeyError):
        return None
    if (
        step is None
        or step.step_name != "ai_flavor_gate"
        or step.status != "completed"
        or str(step.workflow_run_id) != receipt.get("workflow_run_id")
        or (step.output_ref or {}).get(RECEIPT_KEY) != receipt
    ):
        return None
    return receipt


async def successful_style_rounds(
    session: AsyncSession,
    *,
    candidates: Sequence[ChapterDraftVersionModel],
    workflow_run_id: UUID,
    phase_result: str | None = None,
    input_content: str | None = None,
) -> bool:
    """Require explicit completion and validate every actual model round, including early stops."""
    from bestseller.services.deslop_revise import DeslopPhaseResult, DeslopPhaseStatus

    if not isinstance(phase_result, DeslopPhaseResult):
        return False
    phase = phase_result.phase_status
    if not isinstance(phase, DeslopPhaseStatus) or type(phase.attempted_rounds) is not int:
        return False
    count = phase.attempted_rounds
    if (
        phase.state != "completed"
        or phase.max_rounds != 2
        or not 0 <= count <= 2
        or phase.workflow_run_id != str(workflow_run_id)
        or phase.output_sha256 != hashlib.sha256(phase_result.encode()).hexdigest()
        or phase.stop_reason not in {"clean", "round_limit_reached", "not_applicable"}
    ):
        return False
    if phase.stop_reason == "round_limit_reached" and count != phase.max_rounds:
        return False
    if (
        input_content is not None
        and phase.input_sha256 != hashlib.sha256(input_content.encode()).hexdigest()
    ):
        return False
    if [int((c.promotion_metadata or {}).get("deslop_round") or 0) for c in candidates] != list(
        range(1, count + 1)
    ):
        return False
    if len({c.llm_run_id for c in candidates}) != count:
        return False
    for candidate in candidates:
        if not candidate.llm_run_id or phase.project_id != str(candidate.project_id):
            return False
        run = await session.get(LlmRunModel, candidate.llm_run_id)
        if (
            run is None
            or run.workflow_run_id != workflow_run_id
            or run.project_id != candidate.project_id
            or run.prompt_template != "scene_writer"
            or run.prompt_version != "deslop_revise"
            or run.finish_reason not in {"stop", "end_turn"}
            or run.provider in {"fallback", "mock"}
            or (run.metadata_json or {}).get("fallback_used")
        ):
            return False
    return True


async def close_style_phase(
    session: AsyncSession,
    *,
    draft: ChapterDraftVersionModel,
    policy: str,
    step: WorkflowStepRunModel,
    input_binding: dict | None,
    outcome: AiFlavorGateOutcome,
    complete: bool,
    phase_evidence: dict | None = None,
) -> dict:
    binding = await draft_application_binding(session, draft)
    state = (
        "completed"
        if complete and binding is not None and outcome.decision != "block"
        else "incomplete"
    )
    receipt = {
        "schema": SCHEMA,
        "state": state,
        "policy": policy,
        "workflow_run_id": str(step.workflow_run_id),
        "step_id": str(step.id),
        "input": input_binding,
        "output": binding,
        "decision": outcome.decision,
        "residual_score": outcome.after_score,
        "phase": phase_evidence,
    }
    step.output_ref = {**(step.output_ref or {}), RECEIPT_KEY: receipt}
    # Preserve every old receipt on its owning step; draft metadata is the live pointer.
    draft.promotion_metadata = {**(draft.promotion_metadata or {}), RECEIPT_KEY: receipt}
    await session.flush()
    return receipt


async def reconcile_legacy_style_phase(
    session: AsyncSession,
    *,
    draft: ChapterDraftVersionModel,
    policy: str,
    step_id: UUID,
    apply: bool = False,
) -> dict:
    """Operator helper, dry-run by default. Never infer completion from an origin label.

    Legacy steps without an exact output AND contemporaneous policy evidence are
    refused. A successful import references the historical step; it is not a new run.
    Caller owns transaction/authorization; this helper performs no model calls/commit.
    """
    step = await session.get(WorkflowStepRunModel, step_id)
    binding = await draft_application_binding(session, draft)
    reasons = []
    output = (step.output_ref or {}) if step is not None else {}
    if step is None or step.step_name != "ai_flavor_gate" or step.status != "completed":
        reasons.append("missing_completed_style_step")
    if binding is None or output.get("style_output_binding") != binding:
        reasons.append("missing_exact_output_application_evidence")
    if output.get("style_policy_fingerprint") != policy:
        reasons.append("missing_contemporaneous_policy_evidence")
    if output.get("decision") not in {"pass", "patched"}:
        reasons.append("blocked_or_unknown_outcome")
    candidate_ids = output.get("style_candidate_ids") or []
    candidates = []
    try:
        for candidate_id in candidate_ids:
            candidate = await session.get(ChapterDraftVersionModel, UUID(str(candidate_id)))
            if candidate is not None and candidate.chapter_id == draft.chapter_id:
                candidates.append(candidate)
    except (ValueError, TypeError):
        candidates = []
    # Import only an actual historical termination observation, never infer
    # phase completion from its candidate count or origin label.
    from bestseller.services.deslop_revise import DeslopPhaseResult, DeslopPhaseStatus

    historical_phase = None
    try:
        historical_phase = DeslopPhaseResult(
            draft.content_md,
            DeslopPhaseStatus(**output["style_phase_status"]),
        )
    except (KeyError, TypeError):
        pass
    if (
        step is None
        or historical_phase is None
        or (output.get("style_input_binding") or {}).get("body_sha256")
        != historical_phase.phase_status.input_sha256
        or not await successful_style_rounds(
            session,
            candidates=candidates,
            workflow_run_id=step.workflow_run_id,
            phase_result=historical_phase,
        )
    ):
        reasons.append("missing_phase_completion_evidence")
    result = {
        "eligible": not reasons,
        "reasons": reasons,
        "applied": False,
        "historical_step_id": str(step_id),
    }
    if apply and not reasons:
        receipt = {
            "schema": SCHEMA,
            "state": "completed",
            "policy": policy,
            "workflow_run_id": str(step.workflow_run_id),
            "step_id": str(step.id),
            "input": output.get("style_input_binding"),
            "output": binding,
            "decision": output["decision"],
            "residual_score": output.get("after_score"),
            "reconciled_from_step_id": str(step.id),
            "phase": historical_phase.phase_status.to_dict(),
        }
        step.output_ref = {**output, RECEIPT_KEY: receipt}
        draft.promotion_metadata = {**(draft.promotion_metadata or {}), RECEIPT_KEY: receipt}
        await session.flush()
        result["applied"] = True
    return result
