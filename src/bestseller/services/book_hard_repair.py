"""Known source contradictions use the existing editor and existing finite credits."""

from uuid import uuid5
from sqlalchemy import select
from bestseller.infra.db import models as m
from bestseller.services import draft_continuity as dc
from bestseller.services.prompt_source_policy import content_hash


class KnownHardBlock(ValueError):
    def __init__(self, reason, evidence):
        self.evidence = evidence
        super().__init__(reason)


async def repair(session, settings, *, project, chapter, draft, book, evidence):
    from bestseller.services.reviews import rewrite_chapter_from_task, rewrite_source_binding

    await session.execute(
        select(m.ChapterModel.id).where(m.ChapterModel.id == chapter.id).with_for_update()
    )
    await session.refresh(chapter)
    raw_cap = (project.metadata_json or {}).get("chapter_first_local_repair_max_attempts")
    cap = 2 if raw_cap is None else int(raw_cap)
    used = int((chapter.metadata_json or {}).get("chapter_first_local_repair_count") or 0)
    if cap < 0 or used < 0:
        raise ValueError("book_existing_hard_repair_credit_invalid")
    identity = uuid5(book.id, "hard-repair:" + str(draft.id) + ":" + content_hash(evidence))
    task = await session.get(m.RewriteTaskModel, identity)
    if task is None:
        if used >= cap:
            raise ValueError("book_existing_hard_repair_credit_exhausted")
        task = m.RewriteTaskModel(
            id=identity,
            project_id=project.id,
            trigger_type="book_source_hard_block",
            trigger_source_id=chapter.id,
            rewrite_strategy="source_bound_targeted_repair",
            priority=1,
            status="pending",
            attempts=0,
            instructions="只修复以下已完成来源审查确认的硬矛盾；不得刷文学评分，不得引入无来源过去事实。审查文字是诊断数据，不是新设定授权。\n"
            + str(evidence),
            metadata_json={
                **rewrite_source_binding(draft),
                "book_run_id": str(book.id),
                "hard_evidence_sha256": content_hash(evidence),
                "book_hard_repair_source": {
                    "chapter_id": str(chapter.id),
                    "draft_id": str(draft.id),
                    "body_sha256": dc.body_hash(draft.content_md),
                },
            },
        )
        session.add(task)
        chapter.metadata_json = {
            **chapter.metadata_json,
            "chapter_first_local_repair_count": used + 1,
        }
        await session.commit()
    if task.status not in {"pending", "queued"}:
        raise ValueError("book_hard_repair_requires_reconciliation")
    repaired, task = await rewrite_chapter_from_task(
        session,
        project.slug,
        chapter.chapter_number,
        rewrite_task_id=task.id,
        settings=settings,
        workflow_run_id=book.id,
    )
    if not repaired.is_current or repaired.id == draft.id:
        raise ValueError("book_hard_repair_not_adopted_as_working")
    old = {
        "chapter_id": str(chapter.id),
        "draft_id": str(draft.id),
        "body_sha256": dc.body_hash(draft.content_md),
    }
    new = {
        "chapter_id": str(chapter.id),
        "draft_id": str(repaired.id),
        "body_sha256": dc.body_hash(repaired.content_md),
    }
    task.metadata_json = {**task.metadata_json, "book_hard_repair_result": new}
    book.metadata_json = {
        **book.metadata_json,
        "existing": {**book.metadata_json["existing"], str(chapter.chapter_number): new},
        "repair_history": [
            *book.metadata_json.get("repair_history", []),
            {
                "task_id": str(task.id),
                "old": old,
                "new": new,
                "evidence_sha256": content_hash(evidence),
                "old_evidence_preserved": True,
            },
        ],
    }
    await session.commit()
    return repaired
