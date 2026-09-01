"""Evidence-backed StoryEngine transition review and atomic promotion.

The writer receives an intended current-chapter transition.  This module checks
the prose result instead of trusting that intent: evidence quotes must exist in
the exact draft, observed state changes must replay from the canonical pre-state,
and receipt persistence, snapshot folding, and draft promotion share one
SAVEPOINT.  A failed gate therefore cannot advance canonical story state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
import json
import re
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.context import StoryEngineCreativeCore
from bestseller.domain.enums import ArtifactType, DraftPromotionState
from bestseller.domain.story_engine import (
    ChapterTransitionReceipt,
    StoryEngineDefinition,
    canonical_json_hash,
    replay_receipts,
)
from bestseller.domain.story_state import (
    MonotonicPolicy,
    StateCategory,
    StoryState,
    StoryStateTransition,
)
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterStateSnapshotModel,
    PlanningArtifactVersionModel,
)
from bestseller.services.draft_promotion import (
    PromotionOutcome,
    mark_candidate_under_review,
    mark_draft_eligible,
    promote_chapter_draft,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings

STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE = (
    ArtifactType.STORY_TRANSITION_RECEIPT_V1.value
)
STORY_TRANSITION_RECEIPT_SCHEMA_VERSION = "1.0"


class StoryEngineReceiptVerdict(StrEnum):
    MATCHED = "matched"
    DIVERGED = "diverged"
    INVALID = "invalid"


@dataclass(frozen=True)
class StoryEngineReceiptReview:
    verdict: StoryEngineReceiptVerdict
    blocking_codes: tuple[str, ...]
    content: dict[str, Any]
    replay_passed: bool


@dataclass(frozen=True)
class StoryEnginePromotionResult:
    receipt_artifact: PlanningArtifactVersionModel
    snapshot: ChapterStateSnapshotModel
    promotion_outcome: PromotionOutcome


class StoryEngineReceiptRejected(ValueError):  # noqa: N818
    """Raised when an Engine-controlled chapter cannot advance canonical state."""

    def __init__(
        self,
        message: str,
        *,
        review: StoryEngineReceiptReview | None = None,
        blocking_codes: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.review = review
        self.blocking_codes = tuple(
            dict.fromkeys(
                (
                    *(review.blocking_codes if review is not None else ()),
                    *(str(code).strip() for code in blocking_codes if str(code).strip()),
                )
            )
        )


def _story_state_mapping(state: StoryState) -> dict[str, dict[str, Any]]:
    return {
        key: {
            "category": value.category.value if value.category is not None else None,
            "value": deepcopy(value.value),
        }
        for key, value in state.values.items()
    }


def _string_sequence(value: Any) -> tuple[str, ...]:  # noqa: ANN401
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _transition_mapping(transition: StoryStateTransition) -> dict[str, Any]:
    evidence: str | list[str]
    if isinstance(transition.evidence, tuple):
        evidence = [str(item) for item in transition.evidence]
    else:
        evidence = str(transition.evidence)
    return {
        "key": transition.key,
        "category": StateCategory(transition.category).value,
        "before": deepcopy(transition.before),
        "operator": transition.operator,
        "after": deepcopy(transition.after),
        "evidence": evidence,
        "monotonic": MonotonicPolicy(transition.monotonic).value,
    }


def _transition_signature(transition: Mapping[str, Any]) -> dict[str, Any]:
    operator_aliases = {
        "increase": "add",
        "decrease": "subtract",
        "replace": "set",
    }
    operator = str(transition.get("operator") or "").strip()
    monotonic = str(transition.get("monotonic") or "any").replace("-", "_")
    return {
        "key": str(transition.get("key") or "").strip(),
        "category": str(transition.get("category") or "").strip(),
        "before": deepcopy(transition.get("before")),
        "operator": operator_aliases.get(operator, operator),
        "after": deepcopy(transition.get("after")),
        "monotonic": monotonic,
    }


_ABSTRACT_ACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:局势|情况|关系|命运|一切).{0,4}(?:变化|改变|不同|升级)了?。?$"),
    re.compile(r"^(?:完成|解决|推进|应对)(?:了)?(?:任务|问题|危机|冲突)?。?$"),
    re.compile(r"^(?:the )?(?:situation|relationship|conflict) changed[.!]?$", re.I),
)


def _action_is_abstract(action: str) -> bool:
    normalized = re.sub(r"\s+", "", action)
    if len(normalized) < 8:
        return True
    return any(pattern.fullmatch(normalized) for pattern in _ABSTRACT_ACTION_PATTERNS)


def _parse_observed_transitions(
    raw_transitions: Any,  # noqa: ANN401
) -> tuple[tuple[StoryStateTransition, ...], str | None]:
    if not isinstance(raw_transitions, Sequence) or isinstance(
        raw_transitions, (str, bytes)
    ):
        return (), "STORY_ENGINE_RECEIPT_TRANSITION_MISSING"
    parsed: list[StoryStateTransition] = []
    try:
        for raw_transition in raw_transitions:
            if not isinstance(raw_transition, Mapping):
                return (), "STORY_ENGINE_RECEIPT_TRANSITION_INVALID"
            evidence = raw_transition.get("evidence")
            if isinstance(evidence, list):
                evidence = tuple(str(item) for item in evidence)
            parsed.append(
                StoryStateTransition(
                    key=str(raw_transition.get("key") or ""),
                    category=str(raw_transition.get("category") or ""),
                    before=deepcopy(raw_transition.get("before")),
                    operator=str(raw_transition.get("operator") or ""),
                    after=deepcopy(raw_transition.get("after")),
                    evidence=cast(str | tuple[str, ...], evidence or ""),
                    monotonic=str(raw_transition.get("monotonic") or "any"),
                )
            )
    except (TypeError, ValueError):
        return (), "STORY_ENGINE_RECEIPT_TRANSITION_INVALID"
    if not parsed:
        return (), "STORY_ENGINE_RECEIPT_TRANSITION_MISSING"
    return tuple(parsed), None


def _evidence_strings(
    transitions: Sequence[StoryStateTransition],
) -> tuple[str, ...]:
    out: list[str] = []
    for transition in transitions:
        if isinstance(transition.evidence, tuple):
            out.extend(str(item).strip() for item in transition.evidence)
        else:
            out.append(str(transition.evidence).strip())
    return tuple(item for item in out if item)


def review_story_engine_transition(
    *,
    creative_core: StoryEngineCreativeCore | Mapping[str, Any],
    observation: Mapping[str, Any],
    draft_content_md: str,
    project_id: UUID,
    chapter_id: UUID,
    draft_version_id: UUID,
    workflow_run_id: UUID,
    allow_non_authoritative: bool = False,
) -> StoryEngineReceiptReview:
    """Compare intended and observed chapter consequences, then replay them."""

    invalid_codes: list[str] = []
    divergence_codes: list[str] = []
    try:
        if isinstance(creative_core, StoryEngineCreativeCore):
            core = creative_core
        else:
            core_input = dict(creative_core)
            if allow_non_authoritative and (
                core_input.get("can_drive_generation") is False
            ):
                core_input["can_drive_generation"] = True
            core = StoryEngineCreativeCore.model_validate(core_input)
    except ValidationError as exc:
        invalid_codes.append("STORY_ENGINE_RECEIPT_CREATIVE_CORE_INVALID")
        core_payload = (
            dict(creative_core) if isinstance(creative_core, Mapping) else {}
        )
        content = {
            "artifact_type": STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE,
            "schema_version": STORY_TRANSITION_RECEIPT_SCHEMA_VERSION,
            "verdict": StoryEngineReceiptVerdict.INVALID.value,
            "blocking_codes": invalid_codes,
            "project_id": str(project_id),
            "chapter_id": str(chapter_id),
            "draft_version_id": str(draft_version_id),
            "chapter_number": core_payload.get("chapter_number"),
            "receipt": {},
            "pre_state_hash": str(core_payload.get("pre_state_hash") or ""),
            "post_state": {},
            "post_state_hash": "",
            "replay_passed": False,
            "_meta": {
                "workflow_run_id": str(workflow_run_id),
                "validation_error": str(exc),
                "input_hash": canonical_json_hash(
                    {
                        "creative_core": core_payload,
                        "observation": dict(observation),
                        "draft_hash": canonical_json_hash(draft_content_md),
                    }
                ),
                "receipt_hash": "",
            },
        }
        return StoryEngineReceiptReview(
            verdict=StoryEngineReceiptVerdict.INVALID,
            blocking_codes=tuple(invalid_codes),
            content=content,
            replay_passed=False,
        )

    observed_action = str(observation.get("observed_action") or "").strip()
    observed_choice_id = str(observation.get("choice_id") or "").strip()
    observed_pre_state_hash = str(observation.get("pre_state_hash") or "").strip()
    opponent_counteraction = str(
        observation.get("opponent_counteraction") or ""
    ).strip()
    new_obligations = _string_sequence(observation.get("new_obligations"))
    evidence_quotes = _string_sequence(observation.get("evidence_quotes"))
    transitions, transition_error = _parse_observed_transitions(
        observation.get("observed_transitions")
    )

    if not observed_action or _action_is_abstract(observed_action):
        invalid_codes.append("STORY_ENGINE_RECEIPT_ACTION_ABSTRACT")
    if not evidence_quotes:
        invalid_codes.append("STORY_ENGINE_RECEIPT_EVIDENCE_MISSING")
    for quote in (*evidence_quotes, *_evidence_strings(transitions)):
        if len(quote) < 4 or quote not in draft_content_md:
            invalid_codes.append("STORY_ENGINE_RECEIPT_QUOTE_NOT_FOUND")
            break
    if not opponent_counteraction:
        invalid_codes.append("STORY_ENGINE_RECEIPT_OPPONENT_RESPONSE_MISSING")
    if transition_error:
        invalid_codes.append(transition_error)

    if observed_choice_id != core.choice_id:
        divergence_codes.append("STORY_ENGINE_RECEIPT_CHOICE_MISMATCH")
    if observed_pre_state_hash != core.pre_state_hash:
        divergence_codes.append("STORY_ENGINE_RECEIPT_PRE_STATE_MISMATCH")

    expected_signatures = [
        _transition_signature(item) for item in core.required_state_changes
    ]
    observed_mappings = [_transition_mapping(item) for item in transitions]
    observed_signatures = [
        _transition_signature(item) for item in observed_mappings
    ]
    if expected_signatures != observed_signatures:
        divergence_codes.append("STORY_ENGINE_RECEIPT_TRANSITION_DIVERGED")

    replay_passed = False
    post_state: dict[str, Any] = {}
    post_state_hash = ""
    receipt = ChapterTransitionReceipt(
        choice_id=observed_choice_id,
        transitions=transitions,
        opponent_counteraction=opponent_counteraction or None,
        future_obligations=new_obligations,
        fingerprint=core.projection_hash,
        chapter=core.chapter_number,
        receipt_id=(
            f"chapter:{core.chapter_number}:draft:{draft_version_id}:"
            f"projection:{core.projection_hash}"
        ),
        verification_status="verified",
    )
    if transitions and observed_choice_id:
        try:
            replay = replay_receipts(
                StoryEngineDefinition(
                    engine_id=f"receipt-review:{core.engine_artifact_id}",
                    version=core.engine_version,
                    initial_state=core.pre_state,
                ),
                [receipt],
            )
            post_state = _story_state_mapping(replay.state)
            post_state_hash = canonical_json_hash(post_state)
            replay_passed = replay.applied_count == 1
        except (TypeError, ValueError):
            invalid_codes.append("STORY_ENGINE_RECEIPT_REPLAY_INVALID")
    if post_state_hash and post_state_hash != core.expected_post_state_hash:
        divergence_codes.append("STORY_ENGINE_RECEIPT_POST_STATE_MISMATCH")

    invalid_codes = list(dict.fromkeys(invalid_codes))
    divergence_codes = list(dict.fromkeys(divergence_codes))
    if invalid_codes:
        verdict = StoryEngineReceiptVerdict.INVALID
        blocking_codes = (*invalid_codes, *divergence_codes)
    elif divergence_codes:
        verdict = StoryEngineReceiptVerdict.DIVERGED
        blocking_codes = tuple(divergence_codes)
    else:
        verdict = StoryEngineReceiptVerdict.MATCHED
        blocking_codes = ()

    receipt_payload = {
        "choice_id": observed_choice_id,
        "pre_state_hash": observed_pre_state_hash,
        "observed_action": observed_action,
        "observed_transitions": observed_mappings,
        "opponent_counteraction": opponent_counteraction,
        "new_obligations": list(new_obligations),
        "evidence_quotes": list(evidence_quotes),
        "fingerprint": core.projection_hash,
        "receipt_id": receipt.receipt_id,
    }
    receipt_hash = canonical_json_hash(receipt_payload)
    content = {
        "artifact_type": STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE,
        "schema_version": STORY_TRANSITION_RECEIPT_SCHEMA_VERSION,
        "verdict": verdict.value,
        "blocking_codes": list(dict.fromkeys(blocking_codes)),
        "project_id": str(project_id),
        "chapter_id": str(chapter_id),
        "draft_version_id": str(draft_version_id),
        "chapter_number": core.chapter_number,
        "receipt": receipt_payload,
        "pre_state_hash": core.pre_state_hash,
        "post_state": post_state,
        "post_state_hash": post_state_hash,
        "replay_passed": replay_passed,
        "_meta": {
            "engine_artifact_id": str(core.engine_artifact_id),
            "engine_version": core.engine_version,
            "window_artifact_id": str(core.window_artifact_id),
            "projection_hash": core.projection_hash,
            "workflow_run_id": str(workflow_run_id),
            "draft_content_hash": canonical_json_hash(draft_content_md),
            "input_hash": canonical_json_hash(
                {
                    "creative_core": core.model_dump(mode="json"),
                    "observation": dict(observation),
                    "draft_content_hash": canonical_json_hash(draft_content_md),
                }
            ),
            "receipt_hash": receipt_hash,
        },
    }
    return StoryEngineReceiptReview(
        verdict=verdict,
        blocking_codes=tuple(cast(list[str], content["blocking_codes"])),
        content=content,
        replay_passed=replay_passed,
    )


def _extract_json_object(raw: str) -> Mapping[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, Mapping) else {}


async def extract_story_engine_receipt_observation(
    session: AsyncSession,
    settings: AppSettings,
    *,
    creative_core: StoryEngineCreativeCore | Mapping[str, Any],
    draft_content_md: str,
    project_id: UUID,
    chapter_id: UUID,
    draft_version_id: UUID,
    workflow_run_id: UUID,
    step_run_id: UUID | None = None,
) -> Mapping[str, Any]:
    """Extract observed consequences; deterministic review remains authoritative."""

    core = (
        creative_core
        if isinstance(creative_core, StoryEngineCreativeCore)
        else StoryEngineCreativeCore.model_validate(creative_core)
    )
    fallback = {
        "choice_id": "",
        "pre_state_hash": "",
        "observed_action": "",
        "observed_transitions": [],
        "opponent_counteraction": "",
        "new_obligations": [],
        "evidence_quotes": [],
    }
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            system_prompt=(
                "You audit a completed novel chapter against a StoryEngine projection. "
                "Report only consequences explicitly realized in the prose. Return one "
                "JSON object and no commentary. Every evidence quote must be copied "
                "verbatim from the chapter. Never copy intended transitions unless the "
                "chapter proves them."
            ),
            user_prompt=(
                "Return keys: choice_id, pre_state_hash, observed_action, "
                "observed_transitions, opponent_counteraction, new_obligations, "
                "evidence_quotes. Each observed transition needs key, category, before, "
                "operator, after, evidence, monotonic.\n\n"
                "STORY_ENGINE_PROJECTION:\n"
                f"{json.dumps(core.model_dump(mode='json'), ensure_ascii=False)}"
                f"\n\nCHAPTER_DRAFT:\n{draft_content_md}"
            ),
            fallback_response=json.dumps(fallback, ensure_ascii=False),
            prompt_template="story_engine_transition_receipt",
            prompt_version=STORY_TRANSITION_RECEIPT_SCHEMA_VERSION,
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
            metadata={
                "chapter_id": str(chapter_id),
                "chapter_number": core.chapter_number,
                "draft_version_id": str(draft_version_id),
                "projection_hash": core.projection_hash,
            },
        ),
    )
    return _extract_json_object(completion.content)


def _validate_receipt_content(content: Mapping[str, Any]) -> None:
    if content.get("artifact_type") != STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE:
        raise ValueError("story transition receipt artifact type mismatch")
    if content.get("schema_version") != STORY_TRANSITION_RECEIPT_SCHEMA_VERSION:
        raise ValueError("story transition receipt schema version mismatch")
    if content.get("verdict") != StoryEngineReceiptVerdict.MATCHED.value:
        raise ValueError("only matched story transition receipts may be persisted")
    if content.get("blocking_codes"):
        raise ValueError("matched story transition receipt contains blocking codes")
    if content.get("replay_passed") is not True:
        raise ValueError("story transition receipt did not replay")
    meta = content.get("_meta")
    receipt = content.get("receipt")
    if not isinstance(meta, Mapping) or not isinstance(receipt, Mapping):
        raise ValueError("story transition receipt lineage is incomplete")
    if canonical_json_hash(receipt) != str(meta.get("receipt_hash") or ""):
        raise ValueError("story transition receipt hash mismatch")
    if str(content.get("post_state_hash") or "") != canonical_json_hash(
        content.get("post_state")
    ):
        raise ValueError("story transition receipt post-state hash mismatch")
    required_meta = (
        "engine_artifact_id",
        "window_artifact_id",
        "projection_hash",
        "workflow_run_id",
    )
    if any(not str(meta.get(key) or "").strip() for key in required_meta):
        raise ValueError("story transition receipt lineage is incomplete")


async def create_story_transition_receipt_artifact(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    content: Mapping[str, Any],
    idempotency_key: str,
    source_run_id: UUID,
    created_by: str = "story_engine_review",
) -> PlanningArtifactVersionModel:
    """Persist a matched receipt with strict retry and conflict semantics."""

    _validate_receipt_content(content)
    normalized_key = str(idempotency_key).strip()
    if not normalized_key:
        raise ValueError("story transition receipt idempotency key is required")
    expected_content = deepcopy(dict(content))
    existing = await session.scalar(
        select(PlanningArtifactVersionModel).where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type
            == STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE,
            PlanningArtifactVersionModel.idempotency_key == normalized_key,
        )
    )
    if existing is not None:
        if canonical_json_hash(existing.content) != canonical_json_hash(expected_content):
            raise ValueError(
                "same idempotency key was used with different story transition receipt content"
            )
        if existing.scope_ref_id != chapter_id or existing.source_run_id != source_run_id:
            raise ValueError("story transition receipt retry lineage mismatch")
        return existing

    latest_version = await session.scalar(
        select(func.max(PlanningArtifactVersionModel.version_no)).where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type
            == STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE,
            PlanningArtifactVersionModel.scope_ref_id == chapter_id,
        )
    )
    artifact = PlanningArtifactVersionModel(
        project_id=project_id,
        artifact_type=STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE,
        scope_ref_id=chapter_id,
        version_no=int(latest_version or 0) + 1,
        status=StoryEngineReceiptVerdict.MATCHED.value,
        schema_version=STORY_TRANSITION_RECEIPT_SCHEMA_VERSION,
        content=expected_content,
        source_run_id=source_run_id,
        idempotency_key=normalized_key,
        created_by=created_by,
    )
    try:
        async with session.begin_nested():
            session.add(artifact)
            await session.flush()
    except IntegrityError:
        winner = await session.scalar(
            select(PlanningArtifactVersionModel).where(
                PlanningArtifactVersionModel.project_id == project_id,
                PlanningArtifactVersionModel.artifact_type
                == STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE,
                PlanningArtifactVersionModel.idempotency_key == normalized_key,
            )
        )
        if winner is None:
            raise
        if canonical_json_hash(winner.content) != canonical_json_hash(expected_content):
            raise ValueError(
                "concurrent receipt insert used different content"
            ) from None
        if winner.scope_ref_id != chapter_id or winner.source_run_id != source_run_id:
            raise ValueError("concurrent receipt insert lineage mismatch") from None
        return winner
    return artifact


async def fold_story_engine_receipt_into_snapshot(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    chapter_number: int,
    draft_version_id: UUID,
    receipt_artifact: PlanningArtifactVersionModel,
    workflow_run_id: UUID,
) -> ChapterStateSnapshotModel:
    """Fold matched replay state into the chapter snapshot without losing facts."""

    content = receipt_artifact.content
    _validate_receipt_content(content)
    if receipt_artifact.project_id != project_id or receipt_artifact.scope_ref_id != chapter_id:
        raise ValueError("receipt artifact scope does not match snapshot scope")
    if receipt_artifact.source_run_id != workflow_run_id:
        raise ValueError("receipt artifact workflow does not match snapshot workflow")
    if str(content.get("draft_version_id")) != str(draft_version_id):
        raise ValueError("receipt artifact draft does not match snapshot draft")

    existing = await session.scalar(
        select(ChapterStateSnapshotModel).where(
            ChapterStateSnapshotModel.project_id == project_id,
            ChapterStateSnapshotModel.chapter_id == chapter_id,
        )
    )
    existing_facts = (
        dict(existing.facts)
        if existing is not None and isinstance(existing.facts, Mapping)
        else {"facts": []}
    )
    existing_contract = existing_facts.get("snapshot_contract")
    snapshot_contract = (
        dict(existing_contract) if isinstance(existing_contract, Mapping) else {}
    )
    story_engine_state = {
        "workflow_run_id": str(workflow_run_id),
        "receipt_artifact_id": str(receipt_artifact.id),
        "draft_version_id": str(draft_version_id),
        "pre_state_hash": str(content.get("pre_state_hash") or ""),
        "post_state_hash": str(content.get("post_state_hash") or ""),
        "state": deepcopy(content.get("post_state") or {}),
        "projection_hash": str(content.get("_meta", {}).get("projection_hash") or ""),
        "replay_passed": True,
    }
    stored_facts = {
        **existing_facts,
        "story_engine": story_engine_state,
        "snapshot_contract": {
            **snapshot_contract,
            "source_chapter_draft_version_id": str(draft_version_id),
            "source_promotion_state": "promoted",
            "is_usable": True,
        },
    }
    if existing is None:
        snapshot = ChapterStateSnapshotModel(
            project_id=project_id,
            chapter_id=chapter_id,
            chapter_number=chapter_number,
            facts=stored_facts,
            raw_extraction=None,
            extraction_model="story_engine_v2",
            extraction_status="ok_promoted",
        )
        session.add(snapshot)
    else:
        existing.chapter_number = chapter_number
        existing.facts = stored_facts
        existing.extraction_status = "ok_promoted"
        existing.extraction_model = existing.extraction_model or "story_engine_v2"
        snapshot = existing
    await session.flush()
    return snapshot


def story_transition_receipt_idempotency_key(
    *,
    project_id: UUID,
    chapter_id: UUID,
    draft_version_id: UUID,
    workflow_run_id: UUID,
    projection_hash: str,
) -> str:
    return "story-transition-receipt:" + canonical_json_hash(
        {
            "project_id": str(project_id),
            "chapter_id": str(chapter_id),
            "draft_version_id": str(draft_version_id),
            "workflow_run_id": str(workflow_run_id),
            "projection_hash": str(projection_hash),
        }
    )


async def promote_chapter_draft_with_story_engine_receipt(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    chapter_number: int,
    draft: ChapterDraftVersionModel,
    quality_score_id: UUID,
    judge_key: str,
    workflow_run_id: UUID,
    review: StoryEngineReceiptReview,
) -> StoryEnginePromotionResult:
    """Atomically persist receipt, fold state, and promote the exact draft."""

    if review.verdict is not StoryEngineReceiptVerdict.MATCHED:
        raise StoryEngineReceiptRejected(
            "story engine receipt did not match the chapter projection",
            review=review,
        )
    content = review.content
    _validate_receipt_content(content)
    meta = cast(Mapping[str, Any], content["_meta"])
    identity = {
        "project_id": (str(project_id), str(content.get("project_id") or "")),
        "chapter_id": (str(chapter_id), str(content.get("chapter_id") or "")),
        "draft_version_id": (
            str(draft.id),
            str(content.get("draft_version_id") or ""),
        ),
        "workflow_run_id": (
            str(workflow_run_id),
            str(meta.get("workflow_run_id") or ""),
        ),
    }
    mismatches = [key for key, pair in identity.items() if pair[0] != pair[1]]
    if mismatches:
        raise StoryEngineReceiptRejected(
            "story engine receipt promotion lineage mismatch: "
            + ", ".join(mismatches),
            review=review,
            blocking_codes=("STORY_ENGINE_RECEIPT_LINEAGE_MISMATCH",),
        )
    idempotency_key = story_transition_receipt_idempotency_key(
        project_id=project_id,
        chapter_id=chapter_id,
        draft_version_id=draft.id,
        workflow_run_id=workflow_run_id,
        projection_hash=str(meta["projection_hash"]),
    )

    async with session.begin_nested():
        receipt_artifact = await create_story_transition_receipt_artifact(
            session,
            project_id=project_id,
            chapter_id=chapter_id,
            content=content,
            idempotency_key=idempotency_key,
            source_run_id=workflow_run_id,
        )
        snapshot = await fold_story_engine_receipt_into_snapshot(
            session,
            project_id=project_id,
            chapter_id=chapter_id,
            chapter_number=chapter_number,
            draft_version_id=draft.id,
            receipt_artifact=receipt_artifact,
            workflow_run_id=workflow_run_id,
        )
        if draft.promotion_state != DraftPromotionState.PROMOTED.value:
            await mark_candidate_under_review(
                session,
                project_id=project_id,
                draft_kind="chapter",
                draft_id=draft.id,
                workflow_run_id=workflow_run_id,
            )
            await mark_draft_eligible(
                session,
                project_id=project_id,
                draft_kind="chapter",
                draft_id=draft.id,
                quality_score_id=quality_score_id,
                workflow_run_id=workflow_run_id,
            )
            outcome = await promote_chapter_draft(
                session,
                project_id=project_id,
                chapter_id=chapter_id,
                judge_key=judge_key,
                workflow_run_id=workflow_run_id,
            )
        else:
            outcome = PromotionOutcome(
                changed=False,
                reason="already_promoted",
                promoted_draft_id=draft.id,
                incumbent_draft_id=draft.id,
            )
        if outcome.promoted_draft_id != draft.id:
            raise StoryEngineReceiptRejected(
                "story engine receipt cannot promote a draft other than the exact reviewed draft",
                review=review,
                blocking_codes=("STORY_ENGINE_RECEIPT_PROMOTION_MISMATCH",),
            )
        draft.promotion_metadata = {
            **(draft.promotion_metadata or {}),
            "story_engine_receipt_id": str(receipt_artifact.id),
            "story_engine_workflow_run_id": str(workflow_run_id),
            "story_engine_projection_hash": str(meta["projection_hash"]),
            "story_engine_post_state_hash": str(content["post_state_hash"]),
        }
        await session.flush()

    return StoryEnginePromotionResult(
        receipt_artifact=receipt_artifact,
        snapshot=snapshot,
        promotion_outcome=outcome,
    )


__all__ = [
    "STORY_TRANSITION_RECEIPT_ARTIFACT_TYPE",
    "STORY_TRANSITION_RECEIPT_SCHEMA_VERSION",
    "StoryEnginePromotionResult",
    "StoryEngineReceiptRejected",
    "StoryEngineReceiptReview",
    "StoryEngineReceiptVerdict",
    "create_story_transition_receipt_artifact",
    "extract_story_engine_receipt_observation",
    "fold_story_engine_receipt_into_snapshot",
    "promote_chapter_draft_with_story_engine_receipt",
    "review_story_engine_transition",
    "story_transition_receipt_idempotency_key",
]
