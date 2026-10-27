"""Pending lane adapters; all creative work uses existing normal services."""

from copy import deepcopy
from uuid import UUID
from sqlalchemy import select
from bestseller.infra.db import models as m
from bestseller.services.prompt_source_policy import content_hash


def handlers(settings):
    from bestseller.services.autonomous_window_handlers import handlers as normal_handlers

    base = normal_handlers(settings)

    async def plan(session, project, row, outputs):
        from bestseller.services import chapter_outline_readiness as ready
        from bestseller.services.planning_continuity_context import (
            build_authorized_planning_context,
        )

        b = row.metadata_json["binding"]
        start = b["after_chapter"] + 1
        context = await build_authorized_planning_context(
            session, project_id=project.id, before_chapter=start, project=project
        )
        candidates = await session.scalars(
            select(m.WorkflowRunModel)
            .where(
                m.WorkflowRunModel.project_id == project.id,
                m.WorkflowRunModel.workflow_type == ready.WORKFLOW_TYPE,
                m.WorkflowRunModel.status == "completed",
            )
            .order_by(m.WorkflowRunModel.created_at.desc())
        )
        for candidate in candidates:
            state = candidate.metadata_json or {}
            source = state.get("source_payload")
            if not isinstance(source, dict) or state.get("phase") != "completed":
                continue
            numbers = {c.get("chapter_number") for c in source.get("chapters", [])}
            if not set(range(start, b["through_chapter"] + 1)) <= numbers:
                continue
            await ready.verify_outline_prefix_readiness(
                session, project=project, artifact_id=UUID(state["artifact_id"]), settings=settings
            )
            return {
                "source_payload": deepcopy(source),
                "from_chapter": start,
                "through_chapter": b["through_chapter"],
                "parent_workflow_id": str(candidate.id),
                "qualified_context_sha256": content_hash(context),
            }
        # Use the existing macro/rolling loader and normal planner for a real
        # scheduled successor window; never manufacture missing chapter slots.
        row.metadata_json = {**row.metadata_json, "binding": {**row.metadata_json["binding"], "source_lane": "pending_reference"}}
        return await base["plan"](session, project, row, outputs)

    async def write(session, project, row, outputs):
        from bestseller.services.prefix_window_activation import verify_activated_prefix_window
        from bestseller.services.chapter_outline_readiness import verify_outline_prefix_readiness
        from bestseller.services.workflows import create_workflow_run
        from bestseller.services.drafts import generate_chapter_draft_once
        from bestseller.domain.enums import WorkflowStatus

        # One next working chapter per durable checkpoint; later safety qualification
        # creates its own successor. No skipped unqualified intra-window chapter.
        number = row.metadata_json["binding"]["after_chapter"] + 1
        await verify_activated_prefix_window(session, settings, project)
        chapter = await session.scalar(
            select(m.ChapterModel).where(
                m.ChapterModel.project_id == project.id, m.ChapterModel.chapter_number == number
            )
        )
        if chapter is None:
            raise ValueError("pending_next_chapter_not_materialized")
        marker = (chapter.metadata_json or {}).get("outline_prefix_readiness") or {}
        await verify_outline_prefix_readiness(
            session,
            project=project,
            artifact_id=UUID(marker["artifact_id"]),
            chapter_number=number,
            settings=settings,
        )
        identity = content_hash(
            {
                "checkpoint": str(row.id),
                "number": number,
                "admission": outputs["admit"],
                "references": row.metadata_json["qualified_references"],
            }
        )
        workflow = await create_workflow_run(
            session,
            project_id=project.id,
            workflow_type="pending_manuscript_draft",
            status=WorkflowStatus.RUNNING,
            scope_type="chapter",
            scope_id=chapter.id,
            requested_by="normal-pending-continuation",
            current_step="normal_writer_once",
            metadata={"pending_checkpoint_id": str(row.id)},
            idempotency_key=identity,
        )
        await session.commit()
        draft = await generate_chapter_draft_once(
            session, project.slug, number, settings=settings, workflow_run_id=workflow.id
        )
        workflow.status = "completed"
        workflow.current_step = "draft_created_pending_safety"
        result = {
            "workflow_id": str(workflow.id),
            "draft_id": str(draft.id),
            "body_sha256": __import__("hashlib").sha256(draft.content_md.encode()).hexdigest(),
            "chapter_number": number,
            "formal_adoption": False,
            "canon_authority": False,
            "safety_qualified": False,
        }
        workflow.metadata_json = {**workflow.metadata_json, **result}
        await session.commit()
        return result

    return {
        **{k: base[k] for k in ("admit", "materialize", "activate")},
        "plan": plan,
        "write": write,
    }
