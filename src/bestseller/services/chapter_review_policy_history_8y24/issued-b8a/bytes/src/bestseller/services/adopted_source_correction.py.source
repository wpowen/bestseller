"""Trusted, bounded correction proposals derived from an adopted current body.

This unregistered operator API neither detects narrative errors nor supplies a
grade, canon fact, adoption, dispatch or spending authority. The caller must
obtain an exact-plan operator approval through the existing trusted API.
Offsets are Unicode code points; hashes bind exact UTF-8 bodies/quotations.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, NoReturn
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
from bestseller.services.operator_action_approval import require_operator_action_approval
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.rewrite_patch import (
    ProtectedSpan,
    RewriteEdit,
    RewritePatch,
    apply_rewrite_patch,
    text_hash,
)

SCHEMA = "adopted-source-correction.v1"
KEY = "source_correction"
REVIEW_KEY = "source_correction_review"
PROJECTION_HISTORY = "source_correction_projection_withdrawals"
# This module validates JSON at runtime; its nested operator payload is heterogeneous.
# ruff: noqa: ANN401


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError("adopted_source_correction_" + code)


def _immutable(*_args: object, **_kwargs: object) -> NoReturn:
    raise TypeError("immutable correction plan")


class _FrozenDict(dict):
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable

    def __deepcopy__(self, memo: dict) -> _FrozenDict:
        return self


class _FrozenList(list):
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = (
        __iadd__
    ) = __imul__ = _immutable

    def __deepcopy__(self, memo: dict) -> _FrozenList:
        return self


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _uuid(value: UUID | str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("adopted_source_correction_invalid_uuid") from None


def _snapshot(row: Any) -> dict[str, Any]:
    return _plain({key: getattr(row, key) for key in row.__mapper__.column_attrs.keys()})


def _source_witness(row: ChapterDraftVersionModel) -> dict[str, Any]:
    snapshot = _snapshot(row)
    fields = (
        "id",
        "project_id",
        "chapter_id",
        "version_no",
        "content_md",
        "is_current",
        "promotion_state",
        "promotion_score",
        "promoted_at",
        "llm_run_id",
    )
    return {**{key: snapshot[key] for key in fields}, "fullrow_sha256": content_hash(snapshot)}


def _chapter_witness(row: ChapterModel) -> dict[str, Any]:
    snapshot = _snapshot(row)
    fields = ("id", "project_id", "chapter_number", "status", "production_state")
    return {
        **{key: snapshot[key] for key in fields},
        "metadata_sha256": content_hash(snapshot["metadata_json"]),
        "fullrow_sha256": content_hash(snapshot),
    }


def _require_flushed_context(
    session: AsyncSession, project_id: UUID | str, chapter_id: UUID | str
) -> None:
    # Refreshes must never silently overwrite a caller's pending context edits.
    project_id, chapter_id = _uuid(project_id), _uuid(chapter_id)
    for row in list(session.dirty) + list(session.new):
        relevant = (
            (isinstance(row, ChapterModel) and row.id == chapter_id)
            or (isinstance(row, ChapterDraftVersionModel) and row.chapter_id == chapter_id)
            or (isinstance(row, WorkflowRunModel) and row.project_id == project_id)
        )
        _need(not relevant, "unflushed_context")


def _list(metadata: dict[str, Any], key: str) -> list:
    value = metadata.get(key, [])
    _need(isinstance(value, list), "malformed_" + key)
    return deepcopy(value)


def _metadata(value: Any) -> dict[str, Any]:
    _need(isinstance(value, dict), "metadata_shape")
    return value


def _build_plan(
    *,
    project_id: UUID,
    chapter_id: UUID,
    source: dict[str, Any],
    chapter_before: dict[str, Any],
    edits: list[dict[str, str]],
    evidence: list[dict[str, str]],
    reason: str,
) -> dict[str, Any]:
    _need(isinstance(source, dict) and isinstance(chapter_before, dict), "snapshot_shape")
    _need(
        source["id"] == str(_uuid(source["id"]))
        and source["project_id"] == str(project_id)
        and source["chapter_id"] == str(chapter_id)
        and chapter_before["id"] == str(chapter_id)
        and chapter_before["project_id"] == str(project_id)
        and source["is_current"] is True
        and source["promotion_state"] == "promoted",
        "plan_scope",
    )
    body = source["content_md"]
    _need(isinstance(body, str) and bool(body.strip()), "empty_source")
    _need(isinstance(reason, str) and bool(reason.strip()), "reason_required")
    _need(isinstance(edits, list) and bool(edits), "edits_required")
    spans, patch_edits = [], []
    for edit in edits:
        _need(isinstance(edit, dict) and set(edit) == {"before", "after"}, "edit_shape")
        before, after = edit["before"], edit["after"]
        _need(
            isinstance(before, str) and bool(before) and isinstance(after, str) and before != after,
            "edit_text",
        )
        start = body.find(before)
        _need(start >= 0 and body.find(before, start + 1) < 0, "edit_not_unique")
        end = start + len(before)
        _need(not any(start < b and a < end for a, b in spans), "overlapping_edits")
        spans.append((start, end))
        patch_edits.append(
            RewriteEdit(
                start,
                end,
                text_hash(before),
                body[max(0, start - 32) : start],
                body[end : end + 32],
                after,
            )
        )
    protected, cursor = [], 0
    for start, end in sorted(spans):
        if cursor < start:
            protected.append(ProtectedSpan(cursor, start, text_hash(body[cursor:start])))
        cursor = end
    if cursor < len(body):
        protected.append(ProtectedSpan(cursor, len(body), text_hash(body[cursor:])))
    patched = apply_rewrite_patch(
        body,
        RewritePatch(text_hash(body), tuple(patch_edits), tuple(protected), max_changed_ratio=0.25),
    )
    _need(patched.accepted, "patch_rejected:" + ",".join(patched.failure_codes))
    _need(bool(patched.candidate_text.strip()) and patched.candidate_text != body, "noop_result")
    _need(isinstance(evidence, list) and len(evidence) >= 2, "two_observations_required")
    quotations, seen = [], set()
    for item in evidence:
        # Only observations are accepted, never caller-supplied grades/canon.
        _need(isinstance(item, dict) and set(item) == {"quote", "observation"}, "evidence_shape")
        quote, observation = item["quote"], item["observation"]
        _need(
            isinstance(quote, str)
            and bool(quote)
            and isinstance(observation, str)
            and bool(observation.strip()),
            "evidence_text",
        )
        start = body.find(quote)
        _need(
            start >= 0 and body.find(quote, start + 1) < 0 and quote not in seen,
            "evidence_not_unique",
        )
        seen.add(quote)
        quotations.append(
            {
                **item,
                "start": start,
                "end": start + len(quote),
                "quote_sha256": text_hash(quote),
                "offset_unit": "unicode_codepoints",
                "parent_draft_id": source["id"],
                "parent_body_sha256": text_hash(body),
                "is_observation_not_canon": True,
            }
        )
    plan = {
        "schema": SCHEMA,
        "project_id": str(project_id),
        "chapter_id": str(chapter_id),
        "source_draft_id": source["id"],
        "source_body_sha256": text_hash(body),
        "source_before": deepcopy(source),
        "chapter_before": deepcopy(chapter_before),
        "edits": deepcopy(edits),
        "evidence": deepcopy(evidence),
        "reason": reason,
        "source_bound_evidence": quotations,
        "edit_spans": [
            {
                "start": a,
                "end": b,
                "before_sha256": text_hash(edit["before"]),
                "after_sha256": text_hash(edit["after"]),
                "offset_unit": "unicode_codepoints",
            }
            for (a, b), edit in zip(spans, edits, strict=True)
        ],
        "protected_spans": [
            {"start": span.start, "end": span.end, "body_sha256": span.content_hash}
            for span in protected
        ],
        "max_changed_ratio": 0.25,
        "changed_ratio": patched.changed_ratio,
        "result_content_md": patched.candidate_text,
        "result_body_sha256": patched.candidate_hash,
        "model_calls": 0,
        "grade_asserted": False,
        "adoption_authorized": False,
        "fresh_review_required": True,
        "is_observation_not_canon": True,
    }
    plan["plan_sha256"] = content_hash(plan)
    plan["target_id"] = str(
        uuid5(NAMESPACE_URL, "bestseller:adopted-source-correction:" + plan["plan_sha256"])
    )
    plan["candidate_id"] = str(uuid5(_uuid(plan["target_id"]), "derived-candidate"))
    return plan


def _checked_plan(plan: dict[str, Any]) -> dict[str, Any]:
    try:
        _need(isinstance(plan, dict), "plan_shape")
        rebuilt = _build_plan(
            project_id=_uuid(plan["project_id"]),
            chapter_id=_uuid(plan["chapter_id"]),
            source=plan["source_before"],
            chapter_before=plan["chapter_before"],
            edits=plan["edits"],
            evidence=plan["evidence"],
            reason=plan["reason"],
        )
        # Python equality treats False/0 and True/1 as equal; exact JSON binding does not.
        _need(content_hash(rebuilt) == content_hash(plan), "plan_tampered")
        return rebuilt
    except (KeyError, TypeError, AttributeError):
        raise ValueError("adopted_source_correction_plan_shape") from None


async def _chapter(
    session: AsyncSession, project_id: UUID | str, chapter_id: UUID | str, *, lock: bool = False
) -> ChapterModel:
    query = (
        select(ChapterModel)
        .where(ChapterModel.id == _uuid(chapter_id), ChapterModel.project_id == _uuid(project_id))
        .execution_options(populate_existing=True)
    )
    chapter = await session.scalar(query.with_for_update() if lock else query)
    _need(chapter is not None, "scope")
    return chapter


async def prepare_adopted_source_correction(
    session: AsyncSession,
    *,
    project_id: UUID | str,
    chapter_id: UUID | str,
    draft_id: UUID | str,
    body_sha256: str,
    edits: list[dict[str, str]],
    evidence: list[dict[str, str]],
    reason: str,
) -> dict[str, Any]:
    """Return deeply immutable JSON-compatible data for exact operator approval.

    ``edits``: [{before, after}]; ``evidence``: [{quote, observation}], at least
    two distinct quotations occurring exactly once in the unchanged parent.
    No caller-provided offsets, grades or canon assertions are authoritative.
    """
    _require_flushed_context(session, project_id, chapter_id)
    with session.no_autoflush:
        chapter = await _chapter(session, project_id, chapter_id)
        source, _ = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
        _need(
            source is not None
            and source.id == _uuid(draft_id)
            and source.is_current
            and source.promotion_state == "promoted",
            "current_promoted_required",
        )
        _need(text_hash(source.content_md) == body_sha256, "body_changed")
        # Validate append-only containers before authorizing any mutation.
        metadata = _metadata(chapter.metadata_json)
        for key in ("continuity_admission_history", "superseded_body_review_history"):
            _list(metadata, key)
        _need(isinstance(metadata.get("continuity_admission_by_source", {}), dict), "ledger_shape")
        _list(_metadata(source.promotion_metadata), "source_correction_links")
        return _freeze(
            _build_plan(
                project_id=chapter.project_id,
                chapter_id=chapter.id,
                source=_source_witness(source),
                chapter_before=_chapter_witness(chapter),
                edits=edits,
                evidence=evidence,
                reason=reason,
            )
        )


def _result(plan: dict[str, Any], approval_id: UUID | str | None, version: int) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "receipt_id": plan["target_id"],
        "plan_sha256": plan["plan_sha256"],
        "approval_id": str(approval_id),
        "candidate_id": plan["candidate_id"],
        "version": version,
        "body_sha256": plan["result_body_sha256"],
        "source_draft_id": plan["source_draft_id"],
        "source_body_sha256": plan["source_body_sha256"],
        "old_source_blocked": True,
        "new_source_unreviewed": True,
        "fresh_review_required": True,
        "is_observation_not_canon": True,
        "new_model_calls": 0,
        "grade_asserted": False,
        "adopted": False,
    }


def _pointer(plan: dict[str, Any], approval_id: UUID | str | None) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "receipt_id": plan["target_id"],
        "plan_sha256": plan["plan_sha256"],
        "approval_id": str(approval_id),
        "candidate_id": plan["candidate_id"],
        "candidate_body_sha256": plan["result_body_sha256"],
        "parent_draft_id": plan["source_draft_id"],
        "parent_body_sha256": plan["source_body_sha256"],
        "fresh_review_required": True,
        "is_observation_not_canon": True,
    }


def _block(plan: dict[str, Any], approval_id: UUID | str | None) -> dict[str, Any]:
    # The existing selector normalizes line endings for its source ledger key.
    digest = text_hash(
        plan["source_before"]["content_md"].replace("\r\n", "\n").replace("\r", "\n")
    )
    return {
        **_pointer(plan, approval_id),
        "draft_id": plan["source_draft_id"],
        "body_sha256": digest,
        "exact_body_sha256": plan["source_body_sha256"],
        "passed": False,
        "reason": "operator_source_correction_requires_fresh_review",
    }


async def _approve(
    session: AsyncSession, plan: dict[str, Any], approval_id: UUID | str | None
) -> dict[str, Any]:
    _need(
        not any(
            isinstance(row, WorkflowRunModel) and str(row.id) == str(approval_id)
            for row in list(session.dirty) + list(session.new)
        ),
        "unflushed_approval",
    )
    try:
        approval = await require_operator_action_approval(
            session,
            approval_id=approval_id,
            purpose="explicit_draft_text_edit",
            binding=plan,
            capability="apply_exact_text_edits",
        )
        _need(content_hash(approval["binding"]) == content_hash(plan), "approval_binding")
        _need(
            approval.get("capabilities") == ["apply_exact_text_edits"]
            and str(approval_id)
            == str(uuid5(NAMESPACE_URL, "bestseller:operator-approval:" + content_hash(approval))),
            "approval_tampered",
        )
        return approval
    except (KeyError, TypeError, AttributeError):
        raise ValueError("adopted_source_correction_approval_shape") from None


async def _validate_receipt(
    session: AsyncSession,
    *,
    chapter: ChapterModel,
    candidate: ChapterDraftVersionModel | None,
    plan: dict[str, Any],
    approval_id: UUID | str | None,
) -> dict[str, Any]:
    plan = _checked_plan(plan)
    _need(
        str(chapter.project_id) == plan["project_id"] and str(chapter.id) == plan["chapter_id"],
        "receipt_scope",
    )
    _need(
        candidate is not None
        and candidate.is_current
        and str(candidate.id) == plan["candidate_id"]
        and candidate.project_id == chapter.project_id
        and candidate.chapter_id == chapter.id
        and candidate.llm_run_id is None
        and candidate.content_md == plan["result_content_md"]
        and text_hash(candidate.content_md) == plan["result_body_sha256"],
        "candidate_changed",
    )
    row = await session.scalar(
        select(WorkflowRunModel)
        .where(WorkflowRunModel.id == _uuid(plan["target_id"]))
        .execution_options(populate_existing=True)
    )
    expected = {
        "plan": plan,
        "approval_id": str(approval_id),
        "result": _result(plan, approval_id, candidate.version_no),
    }
    _need(
        row is not None
        and row.workflow_type == "adopted_source_correction"
        and row.status == "completed"
        and row.project_id == chapter.project_id
        and row.scope_type == "chapter"
        and row.scope_id == chapter.id
        and content_hash(row.metadata_json) == content_hash(expected),
        "receipt_conflict",
    )
    parent = await session.scalar(
        select(ChapterDraftVersionModel)
        .where(ChapterDraftVersionModel.id == _uuid(plan["source_draft_id"]))
        .execution_options(populate_existing=True)
    )
    _need(
        parent is not None
        and parent.project_id == chapter.project_id
        and parent.chapter_id == chapter.id
        and parent.content_md == plan["source_before"]["content_md"]
        and text_hash(parent.content_md) == plan["source_body_sha256"],
        "parent_changed",
    )
    pointer = _pointer(plan, approval_id)
    metadata = _metadata(candidate.promotion_metadata)
    marker = metadata.get(KEY)
    _need(
        isinstance(marker, dict)
        and content_hash(marker) == content_hash({"plan": plan, "receipt": expected["result"]})
        and metadata.get("origin") == "operator_adopted_source_correction_proposal"
        and metadata.get("parent_draft_id") == plan["source_draft_id"]
        and metadata.get("parent_version_no") == plan["source_before"]["version_no"]
        and metadata.get("model_generated_this_version") is False,
        "marker_changed",
    )
    chapter_metadata = _metadata(chapter.metadata_json)
    _need(
        content_hash(chapter_metadata.get(REVIEW_KEY)) == content_hash(pointer), "pointer_changed"
    )
    block = _block(plan, approval_id)
    key = f"{block['draft_id']}:{block['body_sha256']}"
    ledger = chapter_metadata.get("continuity_admission_by_source")
    history = chapter_metadata.get("continuity_admission_history")
    _need(
        isinstance(ledger, dict)
        and content_hash(ledger.get(key)) == content_hash(block)
        and isinstance(history, list)
        and any(content_hash(item) == content_hash(block) for item in history),
        "old_source_block_missing",
    )
    await _approve(session, plan, approval_id)
    return deepcopy(expected["result"])


async def apply_adopted_source_correction(
    session: AsyncSession, *, plan: dict[str, Any], approval_id: UUID | str | None
) -> dict[str, Any]:
    """Save one unreviewed candidate and source block in an owned savepoint.

    Never commits; the caller supplies a transactional connection (SQLite
    legacy autocommit must be disabled). Repeated application requires the same approved receipt and
    exact current candidate; later drafts/adoptions do not authorize a replay.
    """
    plan = _checked_plan(plan)
    approval_id = _uuid(approval_id) if approval_id is not None else None
    # begin_nested flushes caller state: deny absent/foreign approvals first.
    with session.no_autoflush:
        await _approve(session, plan, approval_id)
    async with session.begin_nested():
        with session.no_autoflush:
            # Bible updates lock project before chapter; preserve that order.
            _need(not any(isinstance(row, ProjectModel) and str(row.id) == plan["project_id"]
                          for row in session.dirty), "unflushed_project")
            await session.scalar(select(ProjectModel).where(
                ProjectModel.id == _uuid(plan["project_id"]),
            ).with_for_update().execution_options(populate_existing=True, autoflush=False))
            chapter = await _chapter(session, plan["project_id"], plan["chapter_id"], lock=True)
            prior = await session.scalar(
                select(WorkflowRunModel)
                .where(WorkflowRunModel.id == _uuid(plan["target_id"]))
                .execution_options(populate_existing=True)
            )
            if prior is not None:
                candidate = await session.scalar(
                    select(ChapterDraftVersionModel)
                    .where(ChapterDraftVersionModel.id == _uuid(plan["candidate_id"]))
                    .execution_options(populate_existing=True)
                )
                _need(
                    candidate is not None
                    and candidate.promotion_state == "candidate"
                    and candidate.llm_run_id is None,
                    "replay_candidate_changed",
                )
                return await _validate_receipt(
                    session,
                    chapter=chapter,
                    candidate=candidate,
                    plan=plan,
                    approval_id=approval_id,
                )
            fresh = await prepare_adopted_source_correction(
                session,
                project_id=_uuid(plan["project_id"]),
                chapter_id=chapter.id,
                draft_id=_uuid(plan["source_draft_id"]),
                body_sha256=plan["source_body_sha256"],
                edits=plan["edits"],
                evidence=plan["evidence"],
                reason=plan["reason"],
            )
            _need(content_hash(fresh) == content_hash(plan), "plan_changed")
            await _approve(session, plan, approval_id)
            source = await session.get(ChapterDraftVersionModel, _uuid(plan["source_draft_id"]))
            project = await session.get(ProjectModel, chapter.project_id)
            _need(project is not None, "project_missing")
            version = (
                int(
                    await session.scalar(
                        select(func.max(ChapterDraftVersionModel.version_no)).where(
                            ChapterDraftVersionModel.chapter_id == chapter.id
                        )
                    )
                    or 0
                )
                + 1
            )
        from bestseller.services.drafts import authoritative_word_count_for_language
        from bestseller.services.pipelines import _select_deslop_candidate

        result = _result(plan, approval_id, version)
        candidate = ChapterDraftVersionModel(
            id=_uuid(plan["candidate_id"]),
            project_id=chapter.project_id,
            chapter_id=chapter.id,
            version_no=version,
            content_md=plan["result_content_md"],
            word_count=authoritative_word_count_for_language(
                plan["result_content_md"], language=project.language
            ),
            assembled_from_scene_draft_ids=deepcopy(source.assembled_from_scene_draft_ids),
            is_current=False,
            promotion_state="candidate",
            llm_run_id=None,
            promotion_metadata={
                "origin": "operator_adopted_source_correction_proposal",
                "parent_draft_id": str(source.id),
                "parent_version_no": source.version_no,
                "model_generated_this_version": False,
                KEY: {"plan": plan, "receipt": result},
            },
        )
        session.add(candidate)
        await session.flush()
        old_authority = deepcopy(source.promotion_metadata or {})
        before_history = _list(chapter.metadata_json or {}, "superseded_body_review_history")
        await _select_deslop_candidate(
            session,
            chapter=chapter,
            candidate=candidate,
            language=project.language,
            selected_reason="operator_source_correction_proposal",
            selection_origin="operator_adopted_source_correction",
        )
        selected_metadata = deepcopy(chapter.metadata_json or {})
        history = selected_metadata.get("superseded_body_review_history", [])
        if history and history[-1] != (before_history[-1] if before_history else None):
            entry = deepcopy(history[-1])
            entry["historical_review_snapshot"] = deepcopy(
                (source.promotion_metadata or {}).get("historical_review_snapshot")
            )
            before_history.append(entry)
        if before_history:
            selected_metadata["superseded_body_review_history"] = before_history
        pointer = _pointer(plan, approval_id)
        # Restore the old promotion authority; only the append-only link is new.
        source.promotion_metadata = {
            **old_authority,
            "source_correction_links": [*_list(old_authority, "source_correction_links"), pointer],
        }
        block = _block(plan, approval_id)
        key = f"{block['draft_id']}:{block['body_sha256']}"
        ledger = deepcopy(selected_metadata.get("continuity_admission_by_source", {}))
        admission_history = _list(selected_metadata, "continuity_admission_history")
        # Preserve every complete prior receipt, including legacy scalar ones.
        for field in ("continuity_adoption_block", "continuity_admission"):
            if field in selected_metadata:
                admission_history.append(deepcopy(selected_metadata[field]))
        if key in ledger:
            admission_history.append(deepcopy(ledger[key]))
        ledger[key] = block
        selected_metadata.update(
            {
                "continuity_admission_by_source": ledger,
                "continuity_admission_history": [*admission_history, block],
                REVIEW_KEY: pointer,
            }
        )
        chapter.metadata_json = selected_metadata
        chapter.status, chapter.production_state = "revision", "pending"
        session.add(
            WorkflowRunModel(
                id=_uuid(plan["target_id"]),
                project_id=chapter.project_id,
                workflow_type="adopted_source_correction",
                scope_type="chapter",
                scope_id=chapter.id,
                status="completed",
                current_step="unreviewed_candidate_saved",
                requested_by="authorized-assistant-editor",
                metadata_json={"plan": plan, "approval_id": str(approval_id), "result": result},
            )
        )
        await session.flush()
        await reconcile_adopted_source_correction_projection(
            session, plan=plan, approval_id=approval_id,
        )
        return deepcopy(result)


async def reconcile_adopted_source_correction_projection(
    session: AsyncSession, *, plan: dict[str, Any], approval_id: UUID | str,
) -> dict[str, Any]:
    """Withdraw only known blocked-source effects, retaining every raw receipt.

    Also upgrades an already committed proposal. No extraction, grade, new
    candidate or commit occurs; the caller owns the encompassing transaction.
    """
    from bestseller.services.story_bible_projection import (
        _fields, _plan, reproject_adopted_bible,
    )

    plan = _checked_plan(plan)
    _require_flushed_context(session, plan["project_id"], plan["chapter_id"])
    _need(not any(isinstance(row, ProjectModel) and str(row.id) == plan["project_id"]
                  for row in session.dirty), "unflushed_project")
    with session.no_autoflush:
        await _approve(session, plan, approval_id)
    async with session.begin_nested():
        project = await session.scalar(select(ProjectModel).where(
            ProjectModel.id == _uuid(plan["project_id"]),
        ).with_for_update().execution_options(populate_existing=True, autoflush=False))
        _need(project is not None, "project_missing")
        chapter = await _chapter(session, plan["project_id"], plan["chapter_id"], lock=True)
        candidate = await session.get(ChapterDraftVersionModel, _uuid(plan["candidate_id"]))
        await _validate_receipt(session, chapter=chapter, candidate=candidate,
                                plan=plan, approval_id=approval_id)
        _need(candidate.promotion_state == "candidate", "projection_candidate_already_reviewed")
        retained = (chapter.metadata_json or {}).get("story_bible_update_receipts", [])
        if not any(isinstance(item, dict) and item.get("status") in {"ok", "empty"}
                   and (item.get("source_lineage") or {}).get("source_chapter_draft_version_id")
                   == plan["source_draft_id"] for item in retained):
            return {"status": "no_retained_bible_contribution", "provider_calls": 0}
        changes, legacy, active, _ = await _plan(session, project.id)
        _need(all(value == "legacy_current" for value in legacy), "projection_attribution_unknown")
        _need(plan["chapter_id"] not in active, "blocked_source_still_active")
        before = {str(row.id): content_hash(_fields(row)) for row, _ in changes}
        await reproject_adopted_bible(session, project)
        changes, legacy, active, _ = await _plan(session, project.id)
        _need(all(value == "legacy_current" for value in legacy)
              and all(_fields(row) == desired for row, desired in changes), "projection_not_current")
        after = {str(row.id): content_hash(_fields(row)) for row, _ in changes}
        history = _list(chapter.metadata_json or {}, PROJECTION_HISTORY)
        for receipt in history:
            if (receipt.get("plan_sha256") == plan["plan_sha256"]
                    and receipt.get("after_projection_sha256") == content_hash(after)
                    and before == after):
                return deepcopy(receipt)
        receipt = {
            "schema": "blocked-source-bible-withdrawal.v1", "status": "reprojected",
            "plan_sha256": plan["plan_sha256"], "parent_draft_id": plan["source_draft_id"],
            "parent_body_sha256": plan["source_body_sha256"],
            "candidate_id": plan["candidate_id"], "candidate_body_sha256": plan["result_body_sha256"],
            "before_projection_sha256": content_hash(before), "after_projection_sha256": content_hash(after),
            "changed_entity_ids": sorted(key for key in before if before[key] != after.get(key)),
            "active_sources_sha256": content_hash(active), "old_source_active": False,
            "raw_receipts_retained": True, "provider_calls": 0,
            "grade_asserted": False, "adoption_authorized": False,
        }
        receipt["receipt_sha256"] = content_hash(receipt)
        chapter.metadata_json = {**(chapter.metadata_json or {}), PROJECTION_HISTORY: [*history, receipt]}
        await session.flush()
        return deepcopy(receipt)


async def read_adopted_source_correction_context(
    session: AsyncSession, *, project_id: UUID | str, chapter_id: UUID | str, content_md: str
) -> dict[str, Any] | None:
    """Validate the current body's own proposal marker, even after its adoption.

    Returns None for another transient body or a later ordinary current draft.
    Exact-body missing or malformed correction markers fail closed with ValueError.
    No checker requests are constructed and no historical PASS is returned.
    """
    _require_flushed_context(session, project_id, chapter_id)
    with session.no_autoflush:
        chapter = await _chapter(session, project_id, chapter_id)
        candidate = await session.scalar(
            select(ChapterDraftVersionModel)
            .where(
                ChapterDraftVersionModel.project_id == chapter.project_id,
                ChapterDraftVersionModel.chapter_id == chapter.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
            .execution_options(populate_existing=True)
        )
        if candidate is None or candidate.content_md != content_md:
            return None
        metadata = _metadata(candidate.promotion_metadata)
        if KEY not in metadata:
            pointer = _metadata(chapter.metadata_json).get(REVIEW_KEY)
            _need(
                not (
                    (isinstance(pointer, dict) and pointer.get("candidate_id") == str(candidate.id))
                    or metadata.get("origin") == "operator_adopted_source_correction_proposal"
                ),
                "marker_missing",
            )
            return None
        marker = metadata[KEY]
        _need(
            isinstance(marker, dict)
            and isinstance(marker.get("plan"), dict)
            and isinstance(marker.get("receipt"), dict),
            "marker_shape",
        )
        plan = _checked_plan(marker["plan"])
        approval_id = marker["receipt"].get("approval_id")
        result = await _validate_receipt(
            session, chapter=chapter, candidate=candidate, plan=plan, approval_id=approval_id
        )
        return {
            **_pointer(plan, approval_id),
            "reason": plan["reason"],
            "parent_content_md": plan["source_before"]["content_md"],
            "candidate_content_md": candidate.content_md,
            "source_bound_evidence": deepcopy(plan["source_bound_evidence"]),
            "edits": deepcopy(plan["edits"]),
            "edit_spans": deepcopy(plan["edit_spans"]),
            "protected_spans": deepcopy(plan["protected_spans"]),
            "result": result,
            "authority": "source_bound_observation_and_edit_proposal",
            "grade_asserted": False,
            "adoption_authorized": False,
        }
