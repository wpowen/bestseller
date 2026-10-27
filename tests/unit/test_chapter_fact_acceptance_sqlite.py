"""Native fact denials with real SQLite rows and real wire action projection.

Controlled wire and completion fixtures are not paid-origin authentication.
No HTTP, provider SDK, model call, or production database is used here.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.schema import CreateTable
from test_chapter_review_commercial_checkpoint import review_db  # noqa: F401
from test_commercial_action_projection import slot, stable  # noqa: F401
from test_primary_review_submission_entrypoint import _tool_completion, primary_db  # noqa: F401

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.services import draft_promotion, reviews
from bestseller.services.commercial_action_projection import project_commercial_actions
from bestseller.services.commercial_source_evidence import assemble_commercial_sources
from bestseller.services.write_gate import GateConfig

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _snapshot(row):
    return {a.key: deepcopy(getattr(row, a.key)) for a in inspect(type(row)).column_attrs}


async def _project(db, aggregate_slots, kind):
    sources = assemble_commercial_sources(
        chapter_number=2,
        content_md=db.draft.content_md,
        previous_chapters=[{"chapter_number": 1, "body": "Yesterday she kept the archive key."}],
        source={
            "approved_chapter_contract": {
                "text": "Keep the archive key until the receipt is signed.",
                "source": "user",
                "hard": True,
            }
        }
        if kind == "constraint_violation"
        else {},
    )
    slots = [slot(sources, i, kind=kind, code="RETAINED_FACT_CONFLICT") for i in range(3)]
    aggregate = await aggregate_slots(slots)
    raw = deepcopy(aggregate.model_dump(mode="json", by_alias=True))
    view, receipt = project_commercial_actions(
        aggregate,
        sources=sources,
        gate_config=GateConfig({}, "block"),
        chapter_number=2,
    )
    assert receipt["status"] == "projected" and view is not None
    assert aggregate.model_dump(mode="json", by_alias=True) == raw
    assert (
        receipt["source_binding"]["body_sha256"]
        == hashlib.sha256(db.draft.content_md.encode()).hexdigest()
    )
    return raw, view, receipt


@pytest.fixture
async def retained_db(review_db, stable):  # noqa: F811
    db = review_db
    db.settings.pipeline.chapter_auto_repair_max_attempts = 3
    db.chapter.metadata_json = {
        "protected": "keep",
        "chapter_review_attempts_active": 5,
        "chapter_review_active_cycle": {"id": "fixture-cycle", "attempts_base": 4},
        "chapter_critic_evidence_cycle": {
            "state": "completed",
            "attempts": [{"state": "completed", "outcome": "verified"}],
        },
    }
    async with db.session.bind.begin() as connection:
        for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    scope = f"fixture-fact-review:{db.project.id}"
    now = datetime.now(UTC)
    budget = ExecutionBudgetModel(
        scope_id=scope,
        max_calls=5,
        max_tokens=5000,
        calls_used=3,
        tokens_charged=321,
        deadline_at=now + timedelta(days=1),
        status="active",
        metadata_json={"fixture_only": True, "no_paid_origin_proof": True},
    )
    operation = ExecutionOperationModel(
        key=uuid4().hex,
        scope_id=scope,
        input_hash="a" * 64,
        status="completed",
        owner="fixture-owner",
        lease_until=now,
        result_json={"retained": "physical-fixture"},
    )
    attempt = ExecutionAttemptModel(
        id=uuid4().hex,
        operation_key=operation.key,
        scope_id=scope,
        model_name="fixture-reviewer",
        input_hash="a" * 64,
        reserved_tokens=500,
        actual_tokens=321,
        status="completed",
        created_at=now,
    )
    db.session.add_all([budget, operation, attempt])
    await db.session.commit()
    db.ledger_rows = [
        (ExecutionBudgetModel, budget.scope_id),
        (ExecutionOperationModel, operation.key),
        (ExecutionAttemptModel, attempt.id),
    ]

    async def seed(kind="current_fact_conflict", mutate=None):
        raw, view, projection = await _project(db, stable, kind)
        evidence = {
            "llm_commercial_judge_original": raw,
            "llm_commercial_judge": view,
            "commercial_action_projection": projection,
            "hard_gates_passed": True,
            "blocking_codes": [],
        }
        if mutate:
            mutate(evidence)
        result = ChapterReviewResult(
            verdict="pass",
            severity_max="major",
            scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, 0.95)),
            evidence_summary=evidence,
            rewrite_instructions="Retained primary literary direction.",
        )
        report = ReviewReportModel(
            project_id=db.project.id,
            target_type="chapter",
            target_id=db.chapter.id,
            reviewer_type="fixture-retained-review",
            verdict="pass",
            severity_max="major",
            structured_output={
                **result.model_dump(mode="json"),
                "draft_id": str(db.draft.id),
                "reviewed_body_fingerprint": reviews.chapter_review_body_fingerprint(
                    db.draft.content_md
                ),
            },
        )
        db.session.add(report)
        await db.session.flush()
        quality = QualityScoreModel(
            project_id=db.project.id,
            target_type="chapter",
            target_id=db.chapter.id,
            chapter_draft_version_id=db.draft.id,
            review_report_id=report.id,
            judge_key="fixture-chapter-core",
            is_current=True,
            score_overall=0.95,
            score_goal=0.95,
            score_conflict=0.95,
            score_emotion=0.95,
            score_style=0.95,
            evidence_summary=deepcopy(evidence),
        )
        db.session.add(quality)
        await db.session.commit()
        db.review_tuple = (result, report, quality, None)
        db.original_result = result.model_dump(mode="json")
        db.rows = [(ReviewReportModel, report.id), (QualityScoreModel, quality.id), *db.ledger_rows]
        async with db.factory() as reader:
            db.before = {
                (model, key): _snapshot(await reader.get(model, key)) for model, key in db.rows
            }
        db.original_chapter = deepcopy(db.chapter.metadata_json)
        return db

    db.seed = seed
    return db


async def _retained(db):
    from bestseller.services.chapter_review_fact_repair import retained_fact_review_result

    return await retained_fact_review_result(
        db.session,
        settings=db.settings,
        draft=db.draft,
        review_tuple=db.review_tuple,
    )


async def _assert_preserved(db):
    assert db.review_tuple[0].model_dump(mode="json") == db.original_result
    async with db.factory() as reader:
        for (model, key), before in db.before.items():
            assert _snapshot(await reader.get(model, key)) == before
        draft = await reader.get(ChapterDraftVersionModel, db.draft.id)
        assert draft.content_md == db.draft.content_md
        assert draft.promotion_state == "candidate" and draft.is_current is True
        chapter = await reader.get(ChapterModel, db.chapter.id)
        assert (
            chapter.metadata_json["chapter_critic_evidence_cycle"]
            == db.original_chapter["chapter_critic_evidence_cycle"]
        )
        assert (
            chapter.metadata_json["chapter_review_active_cycle"]
            == db.original_chapter["chapter_review_active_cycle"]
        )
        assert chapter.metadata_json["protected"] == "keep"


@pytest.mark.parametrize(
    "kind", ["current_fact_conflict", "prior_fact_conflict", "constraint_violation"]
)
async def test_retained_fact_denies_core_eligible_and_creates_exact_bound_task(retained_db, kind):
    db = await retained_db.seed(kind)
    result, report, quality, task = await _retained(db)
    assert result.verdict == "rewrite" and task is not None
    assert (report, quality) == db.review_tuple[1:3]
    assert result.scores == db.review_tuple[0].scores
    assert result.evidence_summary["verified_fact_disposition"]["status"] == "blocked"
    assert draft_promotion._eligible_row(db.draft, quality, min_overall=0.85, min_core=0.8) is None
    assert reviews._promotion_evidence_from_review(result)["hard_gates_passed"] is False
    assert quality.evidence_summary["hard_gates_passed"] is True
    assert float(quality.score_goal) == 0.95
    await db.session.commit()
    async with db.factory() as reader:
        tasks = list(await reader.scalars(select(RewriteTaskModel)))
        assert len(tasks) == 1 and tasks[0].id == task.id
        saved = tasks[0]
        assert saved.trigger_type == "chapter_review" and saved.trigger_source_id == db.chapter.id
        assert saved.status == "pending" and saved.attempts == 0
        assert saved.instructions == result.rewrite_instructions and saved.instructions.strip()
        metadata = saved.metadata_json
        assert all(
            metadata.get(k) == v for k, v in reviews.rewrite_source_binding(db.draft).items()
        )
        assert metadata["review_report_id"] == str(report.id)
        assert (
            metadata["projection_sha256"]
            == result.evidence_summary["commercial_action_projection"]["projection_sha256"]
        )
        assert metadata["attempt_index"] == 2 and metadata["attempt_budget"] == 3
        assert metadata["original_paid_review_preserved"] is True
        chapter = await reader.get(ChapterModel, db.chapter.id)
        assert chapter.metadata_json["chapter_review_attempts_active"] == 6
        assert chapter.status == "revision"
    await _assert_preserved(db)


@pytest.mark.parametrize("terminal", [None, "completed", "cancelled"])
async def test_retained_reentry_is_one_task_one_counter_even_after_terminal(retained_db, terminal):
    db = await retained_db.seed()
    _, _, _, first = await _retained(db)
    await db.session.commit()
    first_id = first.id
    if terminal:
        first.status = terminal
        await db.session.commit()
    _, _, _, second = await _retained(db)
    await db.session.commit()
    assert (second is None) is bool(terminal)
    if second is not None:
        assert second.id == first_id
    async with db.factory() as reader:
        assert [t.id for t in await reader.scalars(select(RewriteTaskModel))] == [first_id]
        assert (await reader.get(ChapterModel, db.chapter.id)).metadata_json[
            "chapter_review_attempts_active"
        ] == 6
    await _assert_preserved(db)


async def test_task_and_counter_rollback_together_and_retry_same_identity(retained_db):
    db = await retained_db.seed()
    ids = db.project.id, db.chapter.id, db.draft.id
    _, _, _, first = await _retained(db)
    first_id = first.id
    await db.session.rollback()
    async with db.factory() as reader:
        assert list(await reader.scalars(select(RewriteTaskModel))) == []
        assert (await reader.get(ChapterModel, ids[1])).metadata_json[
            "chapter_review_attempts_active"
        ] == 5
    db.chapter = await db.session.get(ChapterModel, ids[1])
    db.draft = await db.session.get(ChapterDraftVersionModel, ids[2])
    report_id = next(key for model, key in db.rows if model is ReviewReportModel)
    quality_id = next(key for model, key in db.rows if model is QualityScoreModel)
    db.review_tuple = (
        db.review_tuple[0],
        await db.session.get(ReviewReportModel, report_id),
        await db.session.get(QualityScoreModel, quality_id),
        None,
    )
    _, _, _, retried = await _retained(db)
    assert retried.id == first_id
    await db.session.commit()
    await _assert_preserved(db)


@pytest.mark.parametrize("counter", ["total", "base"])
@pytest.mark.parametrize("invalid", [None, False, -1], ids=["null", "false", "negative"])
async def test_corrupt_counter_shape_rejects_without_task_or_counter_mutation(
    retained_db, counter, invalid
):
    db = await retained_db.seed()
    metadata = deepcopy(db.chapter.metadata_json)
    metadata["chapter_review_attempts_active"] = 0
    metadata["chapter_review_active_cycle"]["attempts_base"] = 0
    if counter == "total":
        metadata["chapter_review_attempts_active"] = invalid
    else:
        metadata["chapter_review_active_cycle"]["attempts_base"] = invalid
    db.chapter.metadata_json = metadata
    await db.session.commit()
    db.original_chapter = deepcopy(metadata)
    chapter_id = db.chapter.id
    async with db.factory() as reader:
        before = _snapshot(await reader.get(ChapterModel, chapter_id))
        assert list(await reader.scalars(select(RewriteTaskModel))) == []

    with pytest.raises(ValueError, match=r"^retained_fact_repair_counter_invalid$"):
        await _retained(db)
    assert not db.session.new and not db.session.dirty and not db.session.deleted
    # A caller commit must not persist a task or a normalized corrupt counter;
    # rollback alone could hide a rejected helper's pending mutations.
    await db.session.commit()
    async with db.factory() as reader:
        chapter = await reader.get(ChapterModel, chapter_id)
        assert _snapshot(chapter) == before
        assert json.dumps(chapter.metadata_json, sort_keys=True) == json.dumps(
            before["metadata_json"], sort_keys=True
        )
        assert list(await reader.scalars(select(RewriteTaskModel))) == []
    await _assert_preserved(db)


async def test_retained_budget_exhaustion_never_grants_new_task_or_counter(retained_db):
    db = await retained_db.seed()
    db.chapter.metadata_json = {**db.chapter.metadata_json, "chapter_review_attempts_active": 7}
    await db.session.commit()
    result, _, _, task = await _retained(db)
    assert result.verdict == "rewrite" and task is None
    await db.session.commit()
    async with db.factory() as reader:
        chapter = await reader.get(ChapterModel, db.chapter.id)
        assert chapter.production_state == "repair_exhausted" and chapter.status == "review"
        assert chapter.metadata_json["chapter_review_attempts_active"] == 7
        assert list(await reader.scalars(select(RewriteTaskModel))) == []
    await _assert_preserved(db)


def _damage(evidence, case):
    projection = evidence["commercial_action_projection"]
    if case == "foreign_body":
        projection["source_binding"]["body_sha256"] = "0" * 64
    elif case == "changed_view":
        evidence["llm_commercial_judge"]["blocking_issues"][0]["required_fix"] += " changed"
    elif case == "missing_view":
        evidence.pop("llm_commercial_judge")
    elif case == "malformed_projection":
        evidence["commercial_action_projection"] = ["invalid"]
    elif case == "unsupported_projection":
        projection["schema"] = "unsupported-fixture-schema"
    elif case == "failed_projection":
        projection["status"] = "blocked"


@pytest.mark.parametrize(
    "case",
    [
        "foreign_body",
        "changed_view",
        "missing_view",
        "malformed_projection",
        "unsupported_projection",
        "failed_projection",
    ],
)
async def test_unavailable_or_foreign_fact_evidence_denies_with_zero_tasks(retained_db, case):
    db = await retained_db.seed(mutate=lambda evidence: _damage(evidence, case))
    result, report, quality, task = await _retained(db)
    assert result.verdict == "unknown" and task is None
    assert result.evidence_summary["verified_fact_disposition"]["status"] == "unknown"
    assert draft_promotion._eligible_row(db.draft, quality, min_overall=0.85, min_core=0.8) is None
    assert (
        draft_promotion.numeric_promotion_debt_receipt(
            db.draft,
            quality,
            report,
            min_overall=0.99,
            min_core=0.99,
        )
        is None
    )
    await db.session.commit()
    async with db.factory() as reader:
        assert list(await reader.scalars(select(RewriteTaskModel))) == []
        assert (await reader.get(ChapterModel, report.target_id)).metadata_json[
            "chapter_review_attempts_active"
        ] == 5
    await _assert_preserved(db)


@pytest.mark.parametrize(
    "kind", ["current_fact_conflict", "prior_fact_conflict", "constraint_violation"]
)
async def test_fact_block_cannot_be_recast_as_numeric_only_debt(retained_db, kind):
    db = await retained_db.seed(kind)
    result, report, quality, _ = db.review_tuple
    quality.score_goal = 0.77
    await db.session.commit()
    async with db.factory() as reader:
        draft = await reader.get(ChapterDraftVersionModel, db.draft.id)
        stored_score = await reader.get(QualityScoreModel, quality.id)
        stored_report = await reader.get(ReviewReportModel, report.id)
        assert stored_score.evidence_summary["hard_gates_passed"] is True
        assert stored_score.evidence_summary["blocking_codes"] == []
        assert (
            draft_promotion.numeric_promotion_debt_receipt(draft, stored_score, stored_report)
            is None
        )
        assert list(await reader.scalars(select(RewriteTaskModel))) == []
    assert result.verdict == "pass"


@pytest.mark.parametrize(
    "mismatch", ["report_draft", "report_body", "quality_report", "quality_evidence"]
)
async def test_retained_repair_refuses_foreign_database_binding(retained_db, mismatch):
    db = await retained_db.seed()
    _, report, quality, _ = db.review_tuple
    if mismatch == "report_draft":
        report.structured_output = {**report.structured_output, "draft_id": str(uuid4())}
    elif mismatch == "report_body":
        report.structured_output = {**report.structured_output, "reviewed_body_fingerprint": {}}
    elif mismatch == "quality_report":
        quality.review_report_id = uuid4()
    elif mismatch == "quality_evidence":
        quality.evidence_summary = {**quality.evidence_summary, "llm_commercial_judge": {}}
    await db.session.commit()
    chapter_id = db.chapter.id
    with pytest.raises(ValueError, match="retained_fact_review_"):
        await _retained(db)
    await db.session.rollback()
    async with db.factory() as reader:
        assert list(await reader.scalars(select(RewriteTaskModel))) == []
        assert (await reader.get(ChapterModel, chapter_id)).metadata_json[
            "chapter_review_attempts_active"
        ] == 5


async def test_craft_audit_keeps_retained_pass_and_numeric_debt_behavior(retained_db):
    db = await retained_db.seed("narrative_quality")
    before = deepcopy(db.review_tuple[0].evidence_summary)
    reused = await _retained(db)
    assert reused == db.review_tuple and reused[0].verdict == "pass" and reused[3] is None
    result, report, quality, _ = reused
    assert result.evidence_summary == before
    assert before["llm_commercial_judge"]["audit_issues"]
    assert before["llm_commercial_judge"]["blocking_issues"] == []
    assert (
        draft_promotion._eligible_row(db.draft, quality, min_overall=0.85, min_core=0.8) is not None
    )
    await db.session.commit()
    await _assert_preserved(db)
    quality.score_goal = 0.77
    await db.session.commit()
    async with db.factory() as reader:
        draft = await reader.get(ChapterDraftVersionModel, db.draft.id)
        score = await reader.get(QualityScoreModel, quality.id)
        receipt = draft_promotion.numeric_promotion_debt_receipt(draft, score, report)
        assert receipt is not None and receipt["below_core_axes"] == ["score_goal"]
        assert list(await reader.scalars(select(RewriteTaskModel))) == []


@pytest.mark.parametrize("status", ["pending", "queued", "completed"])
async def test_original_bound_rewrite_is_reused_without_increment(retained_db, status):
    db = await retained_db.seed()
    task = RewriteTaskModel(
        project_id=db.project.id,
        trigger_type="chapter_review",
        trigger_source_id=db.chapter.id,
        rewrite_strategy="chapter_coherence_bridge_rewrite",
        status=status,
        instructions="Original retained direction.",
        metadata_json={
            **reviews.rewrite_source_binding(db.draft),
            "review_report_id": str(db.review_tuple[1].id),
        },
    )
    db.session.add(task)
    await db.session.commit()
    task_before = _snapshot(task)
    db.review_tuple = (*db.review_tuple[:3], task)
    result, _, _, reused = await _retained(db)
    assert result.verdict == "rewrite"
    assert (reused is None) is (status == "completed")
    if reused is not None:
        assert reused.id == task.id
    await db.session.commit()
    async with db.factory() as reader:
        assert _snapshot(await reader.get(RewriteTaskModel, task.id)) == task_before
        assert len(list(await reader.scalars(select(RewriteTaskModel)))) == 1
        assert (await reader.get(ChapterModel, db.chapter.id)).metadata_json[
            "chapter_review_attempts_active"
        ] == 5
    await _assert_preserved(db)


async def test_retained_current_source_change_does_not_create_stale_repair(retained_db):
    db = await retained_db.seed()
    db.draft.is_current = False
    replacement = ChapterDraftVersionModel(
        project_id=db.project.id,
        chapter_id=db.chapter.id,
        version_no=db.draft.version_no + 1,
        content_md="Replacement current body.",
        word_count=3,
        is_current=True,
        promotion_state="candidate",
    )
    db.session.add(replacement)
    await db.session.commit()
    chapter_id = db.chapter.id
    with pytest.raises(ValueError, match="retained_fact_review_source_changed"):
        await _retained(db)
    await db.session.rollback()
    async with db.factory() as reader:
        assert list(await reader.scalars(select(RewriteTaskModel))) == []
        assert (await reader.get(ChapterModel, chapter_id)).metadata_json[
            "chapter_review_attempts_active"
        ] == 5


async def test_no_projection_preserves_legacy_promotion_behavior(retained_db):
    from bestseller.services.chapter_verified_fact_disposition import (
        apply_verified_fact_disposition,
        decide_verified_fact_disposition,
    )

    def absent_projection(evidence):
        for key in (
            "commercial_action_projection",
            "llm_commercial_judge",
            "llm_commercial_judge_original",
        ):
            evidence.pop(key)

    db = await retained_db.seed(mutate=absent_projection)
    result, _, quality, _ = db.review_tuple
    decision = decide_verified_fact_disposition(
        content_md=db.draft.content_md, evidence=result.evidence_summary
    )
    assert decision["status"] == "no_projection"
    assert apply_verified_fact_disposition(result, content_md=db.draft.content_md) == result
    assert await _retained(db) == db.review_tuple
    assert (
        draft_promotion._eligible_row(db.draft, quality, min_overall=0.85, min_core=0.8) is not None
    )
    await db.session.commit()
    await _assert_preserved(db)


async def test_derived_application_is_idempotent_and_never_authorizes_acceptance(retained_db):
    from bestseller.services.chapter_verified_fact_disposition import (
        apply_verified_fact_disposition,
    )

    db = await retained_db.seed()
    original = db.review_tuple[0]
    derived = apply_verified_fact_disposition(original, content_md=db.draft.content_md)
    assert apply_verified_fact_disposition(derived, content_md=db.draft.content_md) == derived
    receipt = derived.evidence_summary["verified_fact_disposition"]["receipt"]
    for key in (
        "acceptance_authorized",
        "adoption_authorized",
        "dispatch_authorized",
        "grants_credit",
        "authorizes_replay",
        "scores_modified",
        "raw_model_modified",
    ):
        assert receipt[key] is False
    assert (
        derived.evidence_summary["verified_fact_disposition"]["instructions"]
        in derived.rewrite_instructions
    )
    assert derived.scores == original.scores
    await _assert_preserved(db)


async def test_actual_completed_pipeline_branch_reloads_sqlite_cache_once(retained_db, monkeypatch):
    from bestseller.services import pipelines

    db = await retained_db.seed()
    async with db.session.bind.begin() as connection:
        ddl = str(CreateTable(WorkflowRunModel.__table__).compile(dialect=connection.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    owner = WorkflowRunModel(
        project_id=db.project.id,
        workflow_type="chapter_pipeline",
        status="completed",
        scope_type="chapter",
        scope_id=db.chapter.id,
        metadata_json={"retained_owner": True},
    )
    db.session.add(owner)
    await db.session.flush()
    result, report, quality, _ = db.review_tuple
    fingerprint = reviews.chapter_review_body_fingerprint(db.draft.content_md)
    cycle_id = pipelines._chapter_critic_cycle_id(
        db.project.id, db.chapter.id, fingerprint["digest"]
    )
    # Isolate the full approved-plan/history dependency loader. The policy hash,
    # current-body query, cached-result loader and completed branch remain real.
    semantic_hash = "b" * 64

    async def isolated_semantic_dependencies(*args):
        return semantic_hash

    async def forbidden_dispatch(*args, **kwargs):
        pytest.fail("completed cached review must never dispatch a fresh review")

    monkeypatch.setattr(
        pipelines, "_chapter_critic_semantic_dependencies", isolated_semantic_dependencies
    )
    monkeypatch.setattr(pipelines, "review_chapter_draft", forbidden_dispatch)
    cycle = {
        "schema_version": "chapter-critic-cycle.v1",
        "cycle_id": cycle_id,
        "source_draft_id": str(db.draft.id),
        "source_body_hash": fingerprint["digest"],
        "policy_source_hash": pipelines.chapter_review_recovery_source_hash(),
        "semantic_dependencies_sha256": semantic_hash,
        "review_workflow_run_id": str(owner.id),
        "state": "completed",
        "attempts": [
            {
                "attempt": 1,
                "state": "completed",
                "outcome": "available",
                "operation_namespace": f"chapter-critic:{cycle_id}:1",
                "review_report_id": str(report.id),
                "quality_score_id": str(quality.id),
                "draft_id": str(db.draft.id),
                "body_fingerprint": fingerprint,
                "rewrite_task_id": None,
                "review_result": result.model_dump(mode="json"),
            }
        ],
    }
    db.chapter.metadata_json = {
        **db.chapter.metadata_json,
        "chapter_critic_cycles": {cycle_id: cycle},
        "chapter_review_active_cycle": {
            "cycle_id": cycle_id,
            "attempts_base": 4,
            "source_body_hash": fingerprint["digest"],
        },
    }
    await db.session.commit()
    db.original_chapter = deepcopy(db.chapter.metadata_json)
    owner_before = _snapshot(owner)
    ids = db.project.id, db.chapter.id, db.draft.id, owner.id
    task_ids = []
    # Independent sessions prove reuse comes from committed cache rows rather
    # than the fixture's preloaded tuple or ORM identity map.
    for _ in range(2):
        async with db.factory() as session:
            draft = await session.get(ChapterDraftVersionModel, ids[2])
            workflow = await session.get(WorkflowRunModel, ids[3])
            (
                actual,
                actual_report,
                actual_quality,
                task,
            ) = await pipelines._review_chapter_with_evidence_retry(
                session,
                db.settings,
                db.project.slug,
                2,
                workflow=workflow,
                draft=draft,
                source_binding={"draft_id": str(draft.id), "body_hash": fingerprint["digest"]},
                cycle_id=cycle_id,
            )
            assert actual.verdict == "rewrite" and task is not None
            assert actual_report.id == report.id and actual_quality.id == quality.id
            task_ids.append(task.id)
            await session.commit()
    assert task_ids[0] == task_ids[1]
    async with db.factory() as reader:
        chapter = await reader.get(ChapterModel, ids[1])
        assert chapter.metadata_json["chapter_critic_cycles"] == {cycle_id: cycle}
        assert chapter.metadata_json["chapter_review_attempts_active"] == 6
        assert _snapshot(await reader.get(WorkflowRunModel, ids[3])) == owner_before
        assert len(list(await reader.scalars(select(RewriteTaskModel)))) == 1
    await _assert_preserved(db)


async def test_fresh_primary_pass_cannot_erase_fact_when_commercial_block_flag_false(
    primary_db,  # noqa: F811
    stable,  # noqa: F811
    monkeypatch,
):
    from bestseller.services import chapter_llm_quality_judge as commercial

    db = primary_db
    db.settings.pipeline.enable_chapter_llm_commercial_judge = True
    db.settings.pipeline.chapter_llm_commercial_judge_block_on_failure = False
    primary = _tool_completion()
    payload = json.loads(primary.tool_calls[0]["function"]["arguments"])
    payload.update(verdict="pass", rewrite_direction="")
    primary.tool_calls[0]["function"]["arguments"] = json.dumps(payload, ensure_ascii=False)
    primary.raw_message["tool_calls"] = deepcopy(primary.tool_calls)
    physical = deepcopy(vars(primary))
    requests = []
    retained = {}
    original_stable = commercial.judge_chapter_commercial_quality_stable

    async def primary_return(session, settings, request):
        requests.append(request)
        assert (
            request.metadata["primary_review_body_sha256"]
            == hashlib.sha256(db.draft.content_md.encode()).hexdigest()
        )
        return primary

    async def retained_stable(caller, settings, **kwargs):
        selected = commercial._commercial_source_projection(
            kwargs["generation_input"],
            chapter_number=2,
            content_md=kwargs["content_md"],
            previous_chapters=kwargs["previous_chapters"],
        )
        sources = assemble_commercial_sources(
            chapter_number=2,
            content_md=kwargs["content_md"],
            source=selected,
            previous_chapters=kwargs["previous_chapters"],
        )
        samples = [
            slot(sources, i, kind="current_fact_conflict", code="RETAINED_FACT_CONFLICT")
            for i in range(3)
        ]

        async def draw(*args, **draw_kwargs):
            return samples[int(draw_kwargs["sample_slot"].rsplit(":", 1)[1])]

        monkeypatch.setattr(commercial, "judge_chapter_commercial_quality", draw)
        aggregate = await original_stable(
            object(), settings, chapter_number=2, content_md=kwargs["content_md"], samples=3
        )
        retained["raw"] = aggregate.model_dump(mode="json", by_alias=True)
        return aggregate

    # stable's borrowed-session fixture prevents any independent provider pool.
    # The outer stable entry is the explicit retained-provider fixture boundary.
    monkeypatch.setattr(
        commercial, "chapter_commercial_thresholds", lambda *args: (0.82, {"readability": 0.8})
    )
    monkeypatch.setattr(commercial, "judge_chapter_commercial_quality_stable", retained_stable)
    monkeypatch.setattr(reviews, "complete_text", primary_return)
    body = db.draft.content_md
    result, report, quality, task = await reviews.review_chapter_draft(
        db.session,
        db.settings,
        db.project.slug,
        2,
    )
    await db.session.commit()
    assert len(requests) == 1 and vars(primary) == physical
    assert result.verdict == "rewrite" and task is not None
    async with db.factory() as reader:
        stored = await reader.get(ReviewReportModel, report.id)
        score = await reader.get(QualityScoreModel, quality.id)
        draft = await reader.get(ChapterDraftVersionModel, db.draft.id)
        evidence = stored.structured_output["evidence_summary"]
        assert evidence["llm_commercial_judge_original"] == retained["raw"]
        assert evidence["commercial_action_projection"]["status"] == "projected"
        assert evidence["verified_fact_disposition"]["status"] == "blocked"
        assert stored.structured_output["critic_evidence"]["status"] == "verified"
        assert (
            stored.structured_output["primary_submission_receipt"]["parsed_payload"]["verdict"]
            == "pass"
        )
        assert score.evidence_summary["hard_gates_passed"] is False
        assert draft_promotion._eligible_row(draft, score, min_overall=0.85, min_core=0.8) is None
        assert draft.content_md == body and draft.promotion_state == "candidate"
        assert len(list(await reader.scalars(select(RewriteTaskModel)))) == 1
