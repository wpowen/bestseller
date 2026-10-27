"""Whole-book adapters on normal planning/writing/source/closure services."""

from types import SimpleNamespace
from uuid import UUID, uuid5
from bestseller.infra.db import models as m
from bestseller.services import draft_continuity as dc
from bestseller.services.prompt_source_policy import content_hash


def handlers(settings):
    from bestseller.services.pending_manuscript_handlers import handlers as pending_handlers

    normal = pending_handlers(settings)

    def view(book, number):
        selected = book.metadata_json["selected"]
        b = book.metadata_json["binding"]
        return SimpleNamespace(
            id=uuid5(book.id, "chapter:" + str(number)),
            metadata_json={
                "binding": {
                    "after_chapter": number - 1,
                    "through_chapter": number,
                    "target_chapters": b["target_chapters"],
                    "source_lane": "pending_reference",
                    "next_window_proposal": {"start": number, "end": number},
                },
                "qualified_references": [
                    {"chapter_number": int(n), **s} for n, s in selected.items() if int(n) < number
                ],
            },
        )

    def outputs(ctx):
        number = ctx["chapter_number"]
        return {
            key.split(":")[1]: value
            for key, value in ctx["stage_outputs"].items()
            if key.split(":")[0] == str(number)
        }

    adapters = {}
    for stage in ("plan", "admit", "materialize", "activate", "write"):

        async def adapt(session, project, book, ctx, stage=stage):
            result = await normal[stage](
                session, project, view(book, ctx["chapter_number"]), outputs(ctx)
            )
            if stage == "plan" and result.get("planning_workflow_id"):
                workflow = await session.get(
                    m.WorkflowRunModel, UUID(result["planning_workflow_id"])
                )
                if workflow is not None:
                    result = {
                        **result,
                        "debts": (workflow.metadata_json or {}).get("editorial_quality_debt", []),
                    }
            return result

        adapters[stage] = adapt

    async def draft_for(session, project, book, number):
        source = (
            book.metadata_json["existing"].get(str(number))
            or book.metadata_json["outputs"].get(f"{number}:write")
            or book.metadata_json["selected"].get(str(number))
        )
        if source is None:
            raise ValueError("book_draft_source_missing")
        draft = await session.get(
            m.ChapterDraftVersionModel, UUID(source["draft_id"]), populate_existing=True
        )
        chapter = (
            await session.get(m.ChapterModel, draft.chapter_id, populate_existing=True)
            if draft
            else None
        )
        if (
            draft is None
            or chapter is None
            or chapter.project_id != project.id
            or chapter.chapter_number != number
            or dc.body_hash(draft.content_md) != source["body_sha256"]
        ):
            raise ValueError("book_working_body_changed")
        return chapter, draft

    async def review(session, project, book, ctx):
        chapter, draft = await draft_for(session, project, book, ctx["chapter_number"])
        pair = await dc.select_reference(session, settings, project=project, chapter=chapter)
        if pair and pair[0].id == draft.id:
            return {
                "qualified_source": {
                    "chapter_id": str(chapter.id),
                    "draft_id": str(draft.id),
                    "body_sha256": dc.body_hash(draft.content_md),
                    "reference": pair[1],
                },
                "reused": True,
            }
        from bestseller.services.book_source_review import review as review_source

        scope = (book.metadata_json.get("prepared_scopes") or {}).get(str(ctx["chapter_number"]))
        from bestseller.services.book_hard_repair import KnownHardBlock, repair

        while True:
            try:
                source = await review_source(
                    session,
                    settings,
                    project=project,
                    chapter=chapter,
                    draft=draft,
                    book=book,
                    prepared_scope=scope,
                )
                break
            except KnownHardBlock as block:
                draft = await repair(
                    session,
                    settings,
                    project=project,
                    chapter=chapter,
                    draft=draft,
                    book=book,
                    evidence=block.evidence,
                )
                # A repaired body is a new request, never the old exact sample.
                scope = None
        return {
            "qualified_source": source,
            "debts": [
                {
                    "chapter_number": ctx["chapter_number"],
                    "code": "literary_review_deferred",
                    "status": "NotRun",
                }
            ],
        }

    async def knowledge(session, project, book, ctx):
        from bestseller.services.book_continuity_index import build

        _, draft = await draft_for(session, project, book, ctx["chapter_number"])
        return {
            "index": await build(session, settings, project=project, draft=draft),
            "formal_canon": False,
        }

    async def ending(session, project, book, ctx):
        from bestseller.services.book_ending_recovery import close

        return await close(
            session,
            settings,
            project=project,
            book=book,
            draft_for=draft_for,
            review=review,
            knowledge=knowledge,
        )

    async def export(session, project, book, ctx):
        total = book.metadata_json["binding"]["target_chapters"]
        end = book.metadata_json["outputs"].get(f"{total}:ending")
        if (
            not end
            or end.get("ending_verified") is not True
            or end["manifest_sha256"] != content_hash(book.metadata_json["selected"])
        ):
            raise ValueError("book_export_ending_not_verified")
        from bestseller.services.book_ending_recovery import verify

        await verify(session, settings, project=project, book=book, draft_for=draft_for)
        from bestseller.services.exports import export_project_closure_draft_markdown

        artifact, path = await export_project_closure_draft_markdown(
            session,
            settings,
            project.slug,
            created_by_run_id=book.id,
            source_manifest=book.metadata_json["selected"],
        )
        await session.commit()
        return {
            "artifact_id": str(artifact.id),
            "path": str(path),
            "manifest_sha256": content_hash(book.metadata_json["selected"]),
            "publication_ready": False,
        }

    async def reopen(session, project, book, ctx):
        from bestseller.services.book_run_export import verify_export

        total = book.metadata_json["binding"]["target_chapters"]
        # New database session, no identity-map proof or current-pointer fallback.
        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(bind=session.bind, expire_on_commit=False) as fresh:
            actual = await fresh.get(m.ProjectModel, project.id)
            from bestseller.services.book_ending_recovery import verify

            await verify(fresh, settings, project=actual, book=book, draft_for=draft_for)
            return await verify_export(
                fresh,
                settings,
                project=actual,
                manifest=book.metadata_json["selected"],
                artifact_id=book.metadata_json["outputs"][f"{total}:export"]["artifact_id"],
                book_run_id=book.id,
            )

    return {
        **adapters,
        "review": review,
        "knowledge": knowledge,
        "ending": ending,
        "export": export,
        "reopen": reopen,
    }
