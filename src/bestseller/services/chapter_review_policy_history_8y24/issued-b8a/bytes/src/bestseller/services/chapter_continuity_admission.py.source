"""Candidate continuity evidence is checked before it becomes adopted knowledge."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ChapterStateSnapshotModel
from bestseller.services.feedback import KNOWLEDGE_LINEAGE_SCHEMA, KNOWLEDGE_SOURCE_KEYS, _knowledge_llm_receipt, _knowledge_operation_id
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings

PREVIEW_KEY = "continuity_admission_preview"
PREVIEW_SCHEMA = "chapter-continuity-preview.v1"


def reusable_preview(
    draft: ChapterDraftVersionModel, source: Mapping[str, Any],
    dependencies: Mapping[str, Any], *, prompt_sha256: str,
) -> dict[str, Any] | None:
    """Only reuse an exact successful extraction, never an adoption claim."""
    preview = (draft.promotion_metadata or {}).get(PREVIEW_KEY)
    if not isinstance(preview, dict) or preview.get("schema") != PREVIEW_SCHEMA:
        return None
    if (preview.get("status") != "extracted" or preview.get("fallback_used") is not False
            or preview.get("prompt_sha256") != prompt_sha256
            or preview.get("snapshot_dependencies") != dict(dependencies)):
        return None
    prior = preview.get("source")
    if not isinstance(prior, Mapping) or any(
        source.get(key) is None or prior.get(key) != source[key] for key in KNOWLEDGE_SOURCE_KEYS
    ):
        return None
    raw = preview.get("raw")
    if not isinstance(raw, str) or hashlib.sha256(raw.encode()).hexdigest() != preview.get("raw_sha256"):
        return None
    return deepcopy(preview)


def preview_snapshot(
    preview: Mapping[str, Any], *, project_id: UUID, chapter_id: UUID, chapter_number: int,
) -> ChapterStateSnapshotModel:
    from bestseller.services import continuity

    facts, anchor, span, error = continuity._parse_extraction_payload(str(preview.get("raw") or ""))
    if error:
        raise ValueError("chapter_continuity_preview_invalid")
    return ChapterStateSnapshotModel(
        project_id=project_id, chapter_id=chapter_id, chapter_number=chapter_number,
        facts={**continuity._facts_to_storage(facts), "snapshot_contract": {
            **dict(preview["source"]), "is_usable": False, "purpose": "pre_adoption_validation",
            **{key: deepcopy(preview[key]) for key in (
                "llm_run_id", "operation_id", "workflow_run_id", "step_run_id", "provider",
                "model", "prompt_template", "prompt_version", "prompt_sha256", "fallback_used",
            ) if key in preview},
            "llm_operations": deepcopy(preview.get("llm_operations", [])),
            "snapshot_dependencies": preview["snapshot_dependencies"],
        }},
        extraction_status="candidate_preview", raw_extraction=preview["raw"],
        extraction_model=preview.get("model"), time_anchor=anchor, chapter_time_span=span,
    )


async def read_phase_d_snapshot_context(
    session: AsyncSession, *, project_id: UUID, chapter_number: int,
    snapshot: ChapterStateSnapshotModel, candidate_draft_id: UUID | None = None,
):
    """Validate current or provisional evidence before any hard time checker.

    Candidate evidence stays on its working draft. This reader publishes no
    adopted knowledge, grade, or source receipt and never calls a model.
    """
    from bestseller.services import continuity

    if snapshot.project_id != project_id or snapshot.chapter_number != chapter_number:
        raise ValueError("chapter_continuity_snapshot_scope_changed")
    if candidate_draft_id is None:
        if snapshot in session.dirty or snapshot in session.new:
            raise ValueError("chapter_continuity_snapshot_uncommitted")
        selected = await continuity._current_snapshot_input(
            session, project_id=project_id, chapter_number=chapter_number, include_source=True,
        )
        if selected.context is None or selected.chapter_id != snapshot.chapter_id:
            raise RuntimeError("source_context_unavailable: current_chapter_snapshot")
        expected = await session.scalar(select(ChapterStateSnapshotModel.id).where(
            ChapterStateSnapshotModel.project_id == project_id,
            ChapterStateSnapshotModel.chapter_id == selected.chapter_id,
        ))
        if expected != snapshot.id:
            raise ValueError("chapter_continuity_snapshot_source_changed")
        return selected.context
    if any(isinstance(value, ChapterDraftVersionModel) and value.id == candidate_draft_id
           for value in session.dirty):
        raise ValueError("chapter_continuity_preview_uncommitted")
    with session.no_autoflush:
        draft = await session.scalar(select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.id == candidate_draft_id,
            ChapterDraftVersionModel.project_id == project_id,
            ChapterDraftVersionModel.chapter_id == snapshot.chapter_id,
        ).execution_options(populate_existing=True, autoflush=False))
        from bestseller.services.retained_planning_generation import _Reader

        chapter = await _Reader(session).get(ChapterModel, snapshot.chapter_id)
        if (draft is None or chapter is None or chapter.project_id != project_id
                or not draft.is_current or chapter.chapter_number != chapter_number):
            raise ValueError("chapter_continuity_preview_source_changed")
        body = draft.content_md or ""
        if not body.strip():
            raise ValueError("chapter_continuity_preview_source_changed")
        source = {
            "schema": KNOWLEDGE_LINEAGE_SCHEMA, "source_version_no": draft.version_no,
            "project_id": str(project_id), "chapter_id": str(chapter.id),
            "source_chapter_draft_version_id": str(draft.id),
            "source_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        }
        previous = await continuity._current_snapshot_input(
            session, project_id=project_id, chapter_number=chapter_number - 1, include_source=True,
        )
        if chapter_number > 1 and (previous.source is None or previous.context is None):
            raise RuntimeError("source_context_unavailable: previous_chapter_snapshot")
        dependencies = continuity._snapshot_dependencies(chapter_number, previous)
        prompt = continuity._build_extraction_user_prompt(
            chapter=chapter, chapter_md=body, previous_snapshot=previous.context,
        )
        prompt_sha = hashlib.sha256((continuity._EXTRACTION_SYSTEM_PROMPT + "\n" + prompt).encode()).hexdigest()
        preview = reusable_preview(draft, source, dependencies, prompt_sha256=prompt_sha)
        if preview is None or snapshot.raw_extraction != preview["raw"]:
            raise ValueError("chapter_continuity_preview_evidence_changed")
        expected = preview_snapshot(preview, project_id=project_id,
            chapter_id=chapter.id, chapter_number=chapter_number)
        facts = continuity._normalize_inferred_countdown_jumps(
            continuity._facts_from_storage(expected.facts), previous.context, chapter_md=body,
        )
        expected.facts = {**expected.facts, **continuity._facts_to_storage(facts)}
        if (snapshot.facts != expected.facts or snapshot.time_anchor != expected.time_anchor
                or snapshot.chapter_time_span != expected.chapter_time_span
                or snapshot.extraction_status != expected.extraction_status):
            raise ValueError("chapter_continuity_preview_projection_changed")
        view = continuity.build_snapshot_epistemic_view(
            expected, source_body=body, previous=previous,
        )
        context = continuity._snapshot_context(expected, view=view)
        # This view validates a candidate, but cannot authorize the strict
        # next-chapter writer channel before formal adoption and publication.
        return context.model_copy(update={
            "epistemic_policy": "candidate-preview:" + str(view["policy"]),
            "epistemic_application_sha256": None,
        })


async def extract_continuity_admission_preview(
    session: AsyncSession, settings: AppSettings, *, project_id: UUID,
    chapter: ChapterModel, draft: ChapterDraftVersionModel, workflow_run_id: UUID | None,
) -> ChapterStateSnapshotModel:
    """Retain the paid extraction on the candidate without publishing facts.

    The same input and operation identity are used by the adopted extractor;
    successful preview bytes can be adopted after all hard checks pass. A
    preview is never itself a chapter snapshot or a Canon/Timeline source.
    """
    from bestseller.services import continuity

    chapter_id, draft_id, number = chapter.id, draft.id, chapter.chapter_number
    body = draft.content_md or ""
    if (chapter.project_id != project_id or draft.project_id != project_id
            or draft.chapter_id != chapter_id or not body.strip() or not draft.is_current):
        raise ValueError("chapter_continuity_preview_source_invalid")
    if session.in_nested_transaction():
        raise ValueError("chapter_continuity_preview_requires_outer_transaction")
    source = {
        "schema": KNOWLEDGE_LINEAGE_SCHEMA, "source_version_no": draft.version_no,
        "project_id": str(project_id), "chapter_id": str(chapter_id),
        "source_chapter_draft_version_id": str(draft_id),
        "source_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }
    previous = await continuity._current_snapshot_input(
        session, project_id=project_id, chapter_number=number - 1, include_source=True,
    )
    dependencies = continuity._snapshot_dependencies(number, previous)
    user_prompt = continuity._build_extraction_user_prompt(
        chapter=chapter, chapter_md=body, previous_snapshot=previous.context,
    )
    prompt_sha = hashlib.sha256((continuity._EXTRACTION_SYSTEM_PROMPT + "\n" + user_prompt).encode()).hexdigest()
    preview = reusable_preview(draft, source, dependencies, prompt_sha256=prompt_sha)
    def candidate_snapshot(value: Mapping[str, Any]) -> ChapterStateSnapshotModel:
        snapshot = preview_snapshot(value, project_id=project_id, chapter_id=chapter_id, chapter_number=number)
        facts = continuity._normalize_inferred_countdown_jumps(
            continuity._facts_from_storage(snapshot.facts), previous.context, chapter_md=body,
        )
        snapshot.facts = {**snapshot.facts, **continuity._facts_to_storage(facts)}
        return snapshot

    if preview is not None:
        return candidate_snapshot(preview)
    operation = _knowledge_operation_id("snapshot", source) + ":deps:" + continuity._snapshot_digest(dependencies)
    request = LLMCompletionRequest(
        logical_role="editor", system_prompt=continuity._EXTRACTION_SYSTEM_PROMPT,
        user_prompt=user_prompt, fallback_response='{"facts": []}',
        prompt_template="chapter_state_snapshot", prompt_version="2.0",
        project_id=project_id, workflow_run_id=workflow_run_id, operation_id=operation,
        metadata={"source_lineage": {**source, "snapshot_dependencies": dependencies},
                  "chapter_number": number, "task": "continuity_hard_fact_extraction"},
    )
    final_request = request
    completion = await complete_text(session, settings, request)
    raw = completion.content or ""
    _, _, _, error = continuity._parse_extraction_payload(raw)
    attempts = [{**_knowledge_llm_receipt(completion, request), "raw": raw, "parse_error": error}]
    if error is not None and not completion.fallback_used:
        from bestseller.services.llm_closed_loop import LLMGateFinding, build_repair_user_prompt

        # Match the extractor's existing one-repair contract and keep the
        # first paid bytes. Never retry after a source/dependency change.
        await session.refresh(draft)
        current_previous = await continuity._current_snapshot_input(
            session, project_id=project_id, chapter_number=number - 1, include_source=True,
        )
        if (not draft.is_current or hashlib.sha256((draft.content_md or "").encode()).hexdigest()
                != source["source_body_sha256"]
                or continuity._snapshot_dependencies(number, current_previous) != dependencies):
            raise ValueError("chapter_continuity_preview_source_changed")
        repair_request = LLMCompletionRequest(
            logical_role="editor", system_prompt=request.system_prompt,
            user_prompt=build_repair_user_prompt(
                original_user_prompt=user_prompt, language=getattr(chapter, "language", None),
                findings=[LLMGateFinding(
                    code="CONTINUITY_EXTRACTION_PARSE_ERROR", severity="major", path="$",
                    message=error, expected="A JSON object with a facts array and optional time fields.",
                    actual=raw[:240], repair_action=(
                        "Return only valid JSON. Keep extracted facts concrete, "
                        "chapter-local, and canon-safe."
                    ),
                )],
            ), fallback_response=request.fallback_response,
            prompt_template="chapter_state_snapshot_repair", prompt_version="2.0",
            project_id=project_id, workflow_run_id=workflow_run_id, operation_id=operation + ":repair",
            metadata={**request.metadata, "task": "continuity_hard_fact_extraction_repair",
                      "semantic_repair_of": str(completion.llm_run_id) if completion.llm_run_id else None,
                      "previous_error": error},
        )
        final_request = repair_request
        completion = await complete_text(session, settings, repair_request)
        raw = completion.content or ""
        _, _, _, error = continuity._parse_extraction_payload(raw)
        attempts.append({**_knowledge_llm_receipt(completion, repair_request), "raw": raw, "parse_error": error})
    preview = {
        "schema": PREVIEW_SCHEMA, "source": source, "snapshot_dependencies": dependencies,
        **_knowledge_llm_receipt(completion, final_request), "prompt_sha256": prompt_sha,
        "raw": raw, "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "status": "extracted" if error is None and not completion.fallback_used else "unavailable",
        "error": error,
        "llm_operations": attempts,
    }
    # Publish only evidence on this candidate, under short locks after payment.
    current = await session.scalar(select(ChapterDraftVersionModel).where(
        ChapterDraftVersionModel.id == draft_id,
        ChapterDraftVersionModel.project_id == project_id,
        ChapterDraftVersionModel.chapter_id == chapter_id,
    ).with_for_update().execution_options(populate_existing=True))
    if current is None:
        raise ValueError("chapter_continuity_preview_source_changed")
    metadata = dict(current.promotion_metadata or {})
    history = list(metadata.get(PREVIEW_KEY + "_history", []))
    if isinstance(metadata.get(PREVIEW_KEY), Mapping):
        history.append(metadata[PREVIEW_KEY])
    metadata[PREVIEW_KEY + "_history"] = history
    if not current.is_current or hashlib.sha256((current.content_md or "").encode()).hexdigest() != source["source_body_sha256"]:
        preview["status"] = "rejected_source"
    metadata[PREVIEW_KEY] = preview
    current.promotion_metadata = metadata
    await session.flush()
    # Retain evidence even if a later hard gate rejects this candidate.
    await session.commit()
    if preview["status"] != "extracted":
        raise ValueError("chapter_continuity_preview_" + preview["status"])
    current_previous = await continuity._current_snapshot_input(
        session, project_id=project_id, chapter_number=number - 1, include_source=True,
    )
    if continuity._snapshot_dependencies(number, current_previous) != dependencies:
        raise ValueError("chapter_continuity_preview_dependencies_changed")
    return candidate_snapshot(preview)
