"""Explicit restoration of source-owned plan fields polluted by body observations.

The original admission marker and its projection hash remain authoritative.
This migration cannot bless arbitrary edits: changing only the two hashed hype
fields must reproduce that exact original projection and pass the normal full
source-admission reader. The caller commits, or rolls back for a dry run.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import inspect, select

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    SceneCardModel,
    WorkflowRunModel,
)
from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
from bestseller.services.outline_source_entry import require_current_outline_source_admission
from bestseller.services.rewrite_outline_binding import validate_source_plan_binding

FIELDS = ("hype_type", "hype_intensity")
AUDIT_KEY = "deterministic_audit_source_plan_field_recovery"


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False, default=str).encode()).hexdigest()


def _require(ok: object, reason: str) -> None:
    if not ok:
        raise ValueError("source_plan_field_recovery:" + reason)


async def restore_source_owned_hype_fields(
    session,
    settings,
    *,
    chapter_id: UUID,
    expected_draft_id: UUID,
    expected_body_sha256: str,
    expected_current_values: Mapping,
    original_values: Mapping,
    actor: str,
    reason: str,
) -> dict:
    """Atomically stage one restoration; caller commits or explicitly rolls back."""
    _require(not session.new and not session.dirty and not session.deleted,
        "clean_session_required")
    async with session.begin_nested():
        return await _restore_source_owned_hype_fields(session, settings,
            chapter_id=chapter_id, expected_draft_id=expected_draft_id,
            expected_body_sha256=expected_body_sha256,
            expected_current_values=expected_current_values, original_values=original_values,
            actor=actor, reason=reason)


async def _restore_source_owned_hype_fields(
    session,
    settings,
    *,
    chapter_id: UUID,
    expected_draft_id: UUID,
    expected_body_sha256: str,
    expected_current_values: Mapping,
    original_values: Mapping,
    actor: str,
    reason: str,
) -> dict:
    """Stage one auditable restoration; normal admission must pass before return.

    This intentionally performs writes even for a caller's rollback-only probe.
    Never put a model call inside this transaction. No grade, body, source marker,
    knowledge, historical report, counter or paid operation is changed.
    """
    _require(bool(actor.strip()) and bool(reason.strip()), "actor_and_reason_required")
    _require(not session.new and not session.dirty and not session.deleted,
        "clean_session_required")
    _require(set(expected_current_values) == set(original_values) == set(FIELDS),
        "exact_field_set_required")
    chapter = await session.scalar(select(ChapterModel).where(ChapterModel.id == chapter_id)
        .execution_options(populate_existing=True).with_for_update())
    _require(chapter is not None, "chapter_missing")
    project = await session.get(ProjectModel, chapter.project_id)
    _require(project is not None, "project_missing")
    _require(await session.scalar(select(WorkflowRunModel.id).where(
        WorkflowRunModel.project_id == project.id,
        WorkflowRunModel.status.in_(("pending", "queued", "running"))).limit(1)) is None,
        "active_workflow")
    currents = list(await session.scalars(select(ChapterDraftVersionModel).where(
        ChapterDraftVersionModel.project_id == project.id,
        ChapterDraftVersionModel.chapter_id == chapter.id,
        ChapterDraftVersionModel.is_current.is_(True)).execution_options(populate_existing=True)))
    _require(len(currents) == 1 and currents[0].id == expected_draft_id,
        "current_draft_changed")
    draft = currents[0]
    _require(draft.promoted_at is None and draft.promotion_state != "promoted",
        "unadopted_source_required")
    adopted, _numeric_receipt = await select_adopted_chapter_source(
        session, chapter=chapter, lock=False)
    _require(adopted is None or adopted.id != draft.id, "unadopted_source_required")
    _require(isinstance(draft.content_md, str) and draft.content_md.strip()
        and hashlib.sha256(draft.content_md.encode()).hexdigest() == expected_body_sha256,
        "body_changed")
    metadata = deepcopy(chapter.metadata_json or {})
    marker = validate_source_plan_binding(metadata.get("outline_source_admission"))
    scenes = list(await session.scalars(select(SceneCardModel).where(
        SceneCardModel.project_id == project.id, SceneCardModel.chapter_id == chapter.id)
        .order_by(SceneCardModel.scene_number).execution_options(populate_existing=True)))
    before = {key: deepcopy(getattr(chapter, key)) for key in FIELDS}
    original = deepcopy(dict(original_values))
    history = deepcopy(metadata.get(AUDIT_KEY, []))
    _require(isinstance(history, list) and all(isinstance(x, dict) for x in history),
        "audit_history_malformed")
    if before == original:
        matches = [item for item in history if item.get("original_values") == original
            and item.get("draft_id") == str(draft.id)
            and item.get("body_sha256") == expected_body_sha256
            and item.get("original_marker") == marker]
        _require(len(matches) == 1, "already_original_without_migration_receipt")
        await require_current_outline_source_admission(session, settings,
            project=project, chapter=chapter, scenes=scenes)
        return matches[0]
    _require(before == dict(expected_current_values), "current_values_changed")
    prospective = SimpleNamespace(**{field.key: deepcopy(getattr(chapter, field.key))
        for field in inspect(ChapterModel).column_attrs})
    for key in FIELDS:
        setattr(prospective, key, original[key])
    before_hash = outline_prefix_projection_hash(chapter, scenes)
    _require(before_hash != marker["projection_hash"], "original_projection_not_changed")
    restored_hash = outline_prefix_projection_hash(prospective, scenes)
    _require(restored_hash == marker["projection_hash"], "original_projection_not_reproduced")
    for key in FIELDS:
        setattr(chapter, key, original[key])
    await session.flush()
    # This is the ordinary source reader, including immutable canonical,
    # foundation, creator, materializer, saved projection and actual marker.
    admission = await require_current_outline_source_admission(session, settings,
        project=project, chapter=chapter, scenes=scenes)
    _require(admission is not None, "full_admission_required")
    _require(chapter.metadata_json == metadata, "metadata_changed")
    receipt = {
        "schema": "source-plan-observation-restoration.v1",
        "project_id": str(project.id), "chapter_id": str(chapter.id),
        "draft_id": str(draft.id), "body_sha256": expected_body_sha256,
        "previous_values": before, "original_values": original,
        "previous_projection_sha256": before_hash,
        "restored_projection_sha256": restored_hash,
        "original_marker": deepcopy(marker), "admission_sha256": _hash(admission),
        "actor": actor, "reason": reason, "adoption_authorized": False,
        "paid_result_authority": False,
    }
    receipt["receipt_sha256"] = _hash(receipt)
    history.append(deepcopy(receipt))
    chapter.metadata_json = {**metadata, AUDIT_KEY: history}
    await session.flush()
    return receipt
