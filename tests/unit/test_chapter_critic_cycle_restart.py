"""Real interpreter exits over file SQLite; synthetic sends use the real ledger.

No PostgreSQL, Redis, model provider, scheduler or production files are used.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest


@pytest.mark.parametrize(
    "cut",
    [
        "prepared",
        "running",
        "paid",
        "round1",
        "exhausted",
        "policy-prepared",
        "policy-running",
        "policy-paid",
        "policy-round1",
    ],
)
def test_real_exit_resumes_same_two_round_cycle_and_paid_operations(tmp_path, cut):
    db = tmp_path / "critic_restart_test.sqlite3"
    env = {
        **os.environ,
        "BESTSELLER__DATABASE__URL": "sqlite+aiosqlite:///:memory:",
        "BESTSELLER_LINEAGE_PG": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    def run(mode):
        return subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(db), mode, cut],
            env=env,
            text=True,
            capture_output=True,
            timeout=45,
        )

    interrupted = run("interrupt")
    assert interrupted.returncode == 86, interrupted.stdout + interrupted.stderr
    with sqlite3.connect(db) as saved:
        meta = json.loads(saved.execute("SELECT metadata FROM chapters").fetchone()[0])
        sent = saved.execute("SELECT count(*) FROM execution_attempts").fetchone()[0]
        assert sent == int(cut in {"paid", "round1", "exhausted", "policy-paid", "policy-round1"})
        if cut in {"policy-prepared", "policy-running"}:
            assert meta["chapter_review_active_cycle"]["state"] == cut.split("-")[1]
            assert not meta.get("chapter_critic_cycles")
        else:
            state = next(iter(meta["chapter_critic_cycles"].values()))
            assert state["state"] == ("prepared" if cut == "prepared" else "running")
            if cut in {"paid", "policy-paid"}:
                assert saved.execute("SELECT count(*) FROM review_reports").fetchone()[0] == 0
    resumed = run("resume")
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    result = json.loads(resumed.stdout.strip().splitlines()[-1])
    assert result["cycle_state"] == "completed"
    assert result["attempts"] == [1, 2]
    assert result["physical_sends"] == 2
    assert result["body_unchanged"]
    if cut not in {"policy-prepared", "policy-running"}:
        assert result["workflow_id"] != result["review_workflow_id"]
    assert result["second_round_prior_report"] == result["first_report"]
    replayed = run("resume")
    assert replayed.returncode == 0, replayed.stdout + replayed.stderr
    final = json.loads(replayed.stdout.strip().splitlines()[-1])
    assert final["physical_sends"] == 2
    if cut.startswith("policy-"):
        assert final["completed_cycle_rejected"]
    else:
        assert final["attempts"] == [1, 2]  # readback, never a third round


async def _child(path: Path, mode: str, cut: str):
    from sqlalchemy import func, select
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.schema import CreateTable

    from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
    from bestseller.infra.db.models import (
        ChapterDraftVersionModel,
        ChapterModel,
        ExecutionAttemptModel,
        ExecutionBudgetModel,
        ExecutionOperationModel,
        LlmRunModel,
        ProjectModel,
        QualityScoreModel,
        ReviewReportModel,
        RewriteTaskModel,
        WorkflowRunModel,
    )
    from bestseller.services import feedback, llm, pipelines
    from bestseller.services.budget import (
        ExecutionLedger,
        bind_execution_scope,
        execution_operation_id,
    )
    from bestseller.settings import BudgetSettings

    @compiles(JSONB, "sqlite")
    def jsonb(_type, _compiler, **_kw):
        return "JSON"

    fresh = not path.exists()
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    pid, cid, did, previous_id = [
        uuid5(NAMESPACE_URL, "critic-exit:" + label)
        for label in ("project", "chapter", "draft", "previous")
    ]
    body = "The courier keeps the sealed ledger."
    policy = "e" * 64
    pipelines.chapter_review_recovery_source_hash = lambda: policy

    async def fixed_fixture_dependencies(*args):
        return "immutable-synthetic-review-inputs"

    pipelines._chapter_critic_semantic_dependencies = fixed_fixture_dependencies
    if fresh:
        async with engine.begin() as conn:
            for model in (
                ProjectModel,
                ChapterModel,
                ChapterDraftVersionModel,
                WorkflowRunModel,
                ReviewReportModel,
                QualityScoreModel,
                RewriteTaskModel,
                LlmRunModel,
                ExecutionBudgetModel,
                ExecutionOperationModel,
                ExecutionAttemptModel,
            ):
                ddl = str(CreateTable(model.__table__).compile(dialect=conn.dialect))
                await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
        async with factory() as s:
            s.add(
                ProjectModel(
                    id=pid,
                    slug="critic-exit-test",
                    title="test",
                    genre="test",
                    target_word_count=10000,
                    target_chapters=4,
                )
            )
            s.add(
                ChapterModel(
                    id=cid, project_id=pid, chapter_number=1, chapter_goal="test", metadata_json={}
                )
            )
            s.add(
                ChapterDraftVersionModel(
                    id=did,
                    project_id=pid,
                    chapter_id=cid,
                    version_no=1,
                    content_md=body,
                    promotion_state="candidate",
                    is_current=True,
                )
            )
            s.add(
                WorkflowRunModel(
                    id=previous_id,
                    project_id=pid,
                    scope_id=cid,
                    scope_type="chapter",
                    workflow_type="chapter_pipeline",
                    status="machine_blocked",
                    metadata_json={},
                )
            )
            await s.commit()

    real_checkpoint = pipelines._checkpoint_commit

    async def checkpoint(s):
        await real_checkpoint(s)
        if mode != "interrupt":
            return
        chapter = await s.get(ChapterModel, cid)
        meta = chapter.metadata_json or {}
        recovery = meta.get("chapter_review_active_cycle") or {}
        if cut == "policy-prepared" and recovery.get("state") == "prepared":
            os._exit(86)
        if cut == "policy-running" and recovery.get("state") == "running":
            os._exit(86)
        for cycle in (meta.get("chapter_critic_cycles") or {}).values():
            attempts = cycle["attempts"]
            if cut == "prepared" and cycle["state"] == "prepared":
                os._exit(86)
            if cut == "running" and attempts and attempts[0]["state"] == "running":
                os._exit(86)
            if (
                cut in {"round1", "policy-round1", "exhausted"}
                and attempts
                and attempts[0]["state"] == "completed"
            ):
                os._exit(86)

    pipelines._checkpoint_commit = checkpoint
    scope = f"project:{pid}"

    async def fake_review(s, settings, slug, number, **kwargs):
        namespace, _slot = llm._semantic_operation_namespace.get()
        # Include the actual recovery prompt inputs: a regenerated prior report
        # or workflow identity would miss the real ledger's cached operation.
        input_hash = hashlib.sha256(
            json.dumps(kwargs, sort_keys=True, default=str).encode()
        ).hexdigest()
        op = execution_operation_id(
            namespace, subject="critic:commercial:test", revision=input_hash
        )
        ledger = ExecutionLedger(engine, scope, BudgetSettings())
        claim = await ledger.claim(op + ":sample:stable-slot", input_hash)
        if claim.cached_result is None:
            attempt_id = await ledger.reserve(claim, "test-model", input_hash, 10)
            paid = {"model_name": "test-model", "content": "synthetic immutable report"}
            await ledger.settle(attempt_id, "completed", actual_tokens=1, claim=claim, result=paid)
            await ledger.finish(claim, paid)
        if mode == "interrupt" and cut in {"paid", "policy-paid"}:
            os._exit(86)
        unknown = cut == "exhausted" or "evidence_retry_context" not in kwargs
        review = ChapterReviewResult(
            verdict="unknown" if unknown else "pass",
            severity_max="major",
            scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, 0.62)),
        )
        report = ReviewReportModel(
            project_id=pid,
            target_type="chapter",
            target_id=cid,
            reviewer_type="synthetic-no-provider",
            verdict=review.verdict,
            severity_max="major",
            structured_output={
                "draft_id": str(did),
                "reviewed_body_fingerprint": pipelines.chapter_review_body_fingerprint(body),
                "critic_evidence": {"status": "unavailable" if unknown else "verified"},
                "test_request": json.loads(json.dumps(kwargs, default=str)),
            },
        )
        s.add(report)
        await s.flush()
        quality = QualityScoreModel(
            project_id=pid,
            target_type="chapter",
            target_id=cid,
            chapter_draft_version_id=did,
            review_report_id=report.id,
            is_current=False,
            **dict.fromkeys(
                (
                    "score_overall",
                    "score_goal",
                    "score_conflict",
                    "score_emotion",
                    "score_dialogue",
                    "score_style",
                    "score_hook",
                ),
                0.62,
            ),
        )
        s.add(quality)
        await s.flush()
        return review, report, quality, None

    pipelines.review_chapter_draft = fake_review

    async def unadopted(*args, **kwargs):
        return {}, "source_not_adopted"

    feedback.validate_adopted_knowledge_source = unadopted
    async with factory() as s:
        draft = await s.get(ChapterDraftVersionModel, did)
        workflow = WorkflowRunModel(
            project_id=pid,
            scope_id=cid,
            scope_type="chapter",
            workflow_type="chapter_pipeline",
            status="running",
            metadata_json={},
        )
        s.add(workflow)
        await s.commit()

        async def run_retained(s, settings, slug, number, **kwargs):
            await pipelines._review_chapter_with_evidence_retry(
                s,
                settings,
                slug,
                number,
                workflow=workflow,
                draft=draft,
                cycle_id=kwargs.get("critic_cycle_id"),
                source_binding=kwargs.get("review_source_binding"),
            )
            return SimpleNamespace(workflow_run_id=workflow.id)

        pipelines.run_chapter_pipeline = run_retained
        rejected = False
        with bind_execution_scope(scope):
            if cut.startswith("policy-"):
                try:
                    await pipelines.resume_chapter_review_after_policy_change(
                        s,
                        object(),
                        "critic-exit-test",
                        1,
                        previous_workflow_run_id=previous_id,
                        expected_draft_id=did,
                        expected_body_hash=pipelines.chapter_review_body_fingerprint(body)[
                            "digest"
                        ],
                        policy_revision="real-exit-fixture",
                        policy_source_hash=policy,
                        reason="test only",
                    )
                except ValueError as exc:
                    if "already_used" not in str(exc):
                        raise
                    rejected = True
            else:
                await run_retained(s, object(), "critic-exit-test", 1)
        await s.refresh(await s.get(ChapterModel, cid))
        chapter = await s.get(ChapterModel, cid)
        cycle = next(iter(chapter.metadata_json["chapter_critic_cycles"].values()))
        first, second = cycle["attempts"]
        second_report = await s.get(ReviewReportModel, UUID(second["review_report_id"]))
        count = await s.scalar(select(func.count()).select_from(ExecutionAttemptModel))
        print(
            json.dumps(
                {
                    "cycle_state": cycle["state"],
                    "attempts": [a["attempt"] for a in cycle["attempts"]],
                    "physical_sends": count,
                    "workflow_id": str(workflow.id),
                    "review_workflow_id": cycle["review_workflow_run_id"],
                    "first_report": first["review_report_id"],
                    "second_round_prior_report": second_report.structured_output["test_request"][
                        "evidence_retry_context"
                    ]["prior_report_id"],
                    "body_unchanged": draft.content_md == body,
                    "completed_cycle_rejected": rejected,
                }
            )
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_child(Path(sys.argv[1]), sys.argv[2], sys.argv[3]))
