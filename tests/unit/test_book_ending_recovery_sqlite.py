"""Actual ending/editor-credit/qualification/index/export wiring, inert model/editor boundaries.

Real SQLite state/readbacks. No paid grade, future novel, or production acceptance.
"""

from pathlib import Path
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

import pytest
from sqlalchemy import MetaData, ForeignKeyConstraint, JSON, select
from sqlalchemy.dialects.postgresql import JSONB
from test_book_run_offline import full as full, db as db, offline as offline, permit
from bestseller.infra.db import models as m
from bestseller.services import book_run as goal, book_run_handlers
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def prepare(full, mode, cap, used, monkeypatch):
    from bestseller.services import book_native, reviews
    from bestseller.services.finite_book_budget import install
    from bestseller.services.minimax_book_cost_contract import CONTRACT_SHA256

    async with full.db() as s:
        t = m.RewriteTaskModel.__table__.to_metadata(MetaData())
        for col in t.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()
                col.server_default = None
        for c in list(t.constraints):
            if isinstance(c, ForeignKeyConstraint):
                t.constraints.remove(c)
        async with s.bind.begin() as conn:
            await conn.run_sync(t.create)
        p = await s.get(m.ProjectModel, full.pid)
        p.target_chapters = 4
        p.target_word_count = 8000
        if cap is not None:
            p.metadata_json = {**p.metadata_json, "chapter_first_local_repair_max_attempts": cap}
        chapter = await s.scalar(
            select(m.ChapterModel).where(
                m.ChapterModel.project_id == p.id, m.ChapterModel.chapter_number == 4
            )
        )
        chapter.metadata_json = {**chapter.metadata_json, "chapter_first_local_repair_count": used}
        row = await goal.create(
            s, full.settings, project=p, requested_model="openai/MiniMax-M3.1-Flash"
        )
        budget = await s.get(m.ExecutionBudgetModel, "scope-untouched")
        budget.metadata_json = install(
            budget.metadata_json,
            run_id=row.id,
            project_id=p.id,
            model="openai/MiniMax-M3.1-Flash",
            endpoint="https://api.minimaxi.com/v1",
            caps={
                "calls": 50,
                "primary": 30,
                "hard_repair": 16,
                "transport_retry": 4,
                "money_micro_cny": 50000000,
            },
            counter_sha256=CONTRACT_SHA256,
            authorization_ref="OFFLINE FIXTURE ONLY",
            explicit_approval=True,
        )
        await s.commit()
        rid = row.id
    original = book_native.call
    state = {"ending_calls": 0, "editor_calls": 0, "known_receipt": None}

    async def native(
        session, settings, *, project, template, payload, schema, allow_generate=True, **kw
    ):
        if template == "book_ending" and state["ending_calls"] == 0:
            state["ending_calls"] += 1
            last = payload["full_selected_manuscript"][-1]
            data = {
                "manifest_sha256": payload["manifest_sha256"],
                "ending_complete": False,
                "hard_blockers": [],
                "unknown": ["fixture unknown physical source"] if mode == "unknown" else [],
                "debts": ["keep literary debt"],
                "repair_targets": []
                if mode == "unknown"
                else [
                    {
                        "chapter_number": 4,
                        "body_sha256": last["body_sha256"],
                        "blocker_indexes": [],
                        "ending_incomplete": True,
                    }
                ],
            }
            if mode == "prior":
                earlier = payload["full_selected_manuscript"][-2]
                data["repair_targets"][0].update(
                    chapter_number=earlier["chapter_number"], body_sha256=earlier["body_sha256"]
                )
            elif mode == "wrong_body":
                data["repair_targets"][0]["body_sha256"] = "0" * 64
            elif mode == "missing_scope":
                data["repair_targets"] = []
            elif mode == "coverage":
                data["hard_blockers"] = ["fixture exact known blocker without target coverage"]
            import jsonschema

            jsonschema.validate(data, schema)
            wid = uuid5(NAMESPACE_URL, template + content_hash(payload))
            session.add(
                m.WorkflowRunModel(
                    id=wid,
                    project_id=project.id,
                    workflow_type="book_native_evidence",
                    status="completed",
                    metadata_json={"fixture_only": True, "data": data},
                )
            )
            await session.commit()
            state["known_receipt"] = str(wid)
            return data, {"workflow_id": str(wid), "fixture_only": True}
        if template == "book_ending" and allow_generate:
            state["ending_calls"] += 1
        return await original(
            session,
            settings,
            project=project,
            template=template,
            payload=payload,
            schema=schema,
            allow_generate=allow_generate,
            **kw,
        )

    monkeypatch.setattr(book_native, "call", native)

    async def editor(session, slug, n, *, rewrite_task_id, settings, workflow_run_id):
        state["editor_calls"] += 1
        task = await session.get(m.RewriteTaskModel, rewrite_task_id)
        old = await session.get(
            m.ChapterDraftVersionModel, UUID(task.metadata_json["parent_draft_id"])
        )
        old.is_current = False
        d = m.ChapterDraftVersionModel(
            id=uuid4(),
            project_id=old.project_id,
            chapter_id=old.chapter_id,
            version_no=old.version_no + 1,
            content_md="# 完结\n\n观测记录归档，主要选择已经落实，结局确定。",
            word_count=25,
            is_current=True,
            promotion_state="candidate",
            assembled_from_scene_draft_ids=[],
        )
        session.add(d)
        task.status = "completed"
        task.attempts += 1
        await session.flush()
        return d, task

    monkeypatch.setattr(reviews, "rewrite_chapter_from_task", editor)
    return rid, state


async def test_known_ending_repair_requalifies_reindexes_and_exports_exact_new_body(
    full, monkeypatch
):
    rid, state = await prepare(full, "known", None, 0, monkeypatch)
    async with full.db() as s:
        result = await goal.execute(
            s,
            rid,
            settings=full.settings,
            handlers=book_run_handlers.handlers(full.settings),
            permit=permit,
            owner_id="ending-fixture",
        )
        assert result.get("whole_book_complete") and not result["publication_ready"], result
    async with full.db() as fresh:
        book = await fresh.get(m.WorkflowRunModel, rid)
        meta = book.metadata_json
        history = meta["ending_recovery_history"]
        assert len(history) == 1
        old = history[0]["old_manifest"]["4"]
        new = meta["selected"]["4"]
        assert old["draft_id"] != new["draft_id"]
        assert meta["outputs"]["4:review"]["qualified_source"] == new
        assert meta["outputs"]["4:knowledge"]["index"]["body_sha256"] == new["body_sha256"]
        assert meta["outputs"]["4:knowledge"]["index"]["draft_id"] == new["draft_id"]
        old_ref = await fresh.get(m.WorkflowRunModel, UUID(old["reference"]["receipt_id"]))
        assert old_ref.metadata_json["proof"]["body_sha256"] == old["body_sha256"]
        audit = await fresh.scalar(
            select(m.WorkflowRunModel).where(
                m.WorkflowRunModel.workflow_type == "book_source_selection"
            )
        )
        assert audit.metadata_json["old_reference_id"] == str(old_ref.id)
        assert audit.metadata_json["known_hard_repair"]["old"]["draft_id"] == old["draft_id"]
        ending = await fresh.get(m.WorkflowRunModel, UUID(state["known_receipt"]))
        assert not ending.metadata_json["data"]["ending_complete"]
        artifact = await fresh.get(
            m.ExportArtifactModel, UUID(meta["outputs"]["4:export"]["artifact_id"])
        )
        assert artifact.metadata_json["source_drafts"][-1]["draft_id"] == new["draft_id"]
        draft = await fresh.get(m.ChapterDraftVersionModel, UUID(new["draft_id"]))
        assert draft.content_md in Path(artifact.storage_uri).read_text()
        chapter = await fresh.get(m.ChapterModel, draft.chapter_id)
        assert chapter.metadata_json["chapter_first_local_repair_count"] == 1
        assert state["editor_calls"] == 1 and state["ending_calls"] == 2


@pytest.mark.parametrize(
    "mode,cap,used,reason",
    [
        ("unknown", 2, 0, "book_ending_unknown"),
        ("known", 0, 0, "book_existing_hard_repair_credit_exhausted"),
        ("known", 2, 2, "book_existing_hard_repair_credit_exhausted"),
        ("prior", 2, 0, "book_ending_hard_block_requires_prior_or_unscoped_repair"),
        ("wrong_body", 2, 0, "book_ending_hard_block_requires_prior_or_unscoped_repair"),
        ("missing_scope", 2, 0, "book_ending_hard_block_requires_prior_or_unscoped_repair"),
        ("coverage", 2, 0, "book_ending_hard_evidence_coverage_missing"),
    ],
)
async def test_unknown_or_exhausted_local_credits_never_regenerate_ending(
    full, monkeypatch, mode, cap, used, reason
):
    rid, state = await prepare(full, mode, cap, used, monkeypatch)
    async with full.db() as s:
        result = await goal.execute(
            s,
            rid,
            settings=full.settings,
            handlers=book_run_handlers.handlers(full.settings),
            permit=permit,
            owner_id="blocked-fixture",
        )
        assert result["state"] == "machine_blocked" and result["reason"] == reason
    async with full.db() as fresh:
        row = await fresh.get(m.WorkflowRunModel, rid)
        assert (
            "4:ending" not in row.metadata_json["outputs"]
            and "4:export" not in row.metadata_json["outputs"]
        )
        c = await fresh.scalar(
            select(m.ChapterModel).where(
                m.ChapterModel.project_id == full.pid, m.ChapterModel.chapter_number == 4
            )
        )
        assert c.metadata_json["chapter_first_local_repair_count"] == used
        assert state["editor_calls"] == 0 and state["ending_calls"] == 1
        assert list(await fresh.scalars(select(m.RewriteTaskModel))) == []
