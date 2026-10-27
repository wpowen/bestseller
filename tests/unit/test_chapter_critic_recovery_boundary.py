"""Invalid critic evidence cannot rewrite a body or counterfeit its adoption."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_adopted_knowledge_lineage import db as db
from test_adopted_knowledge_lineage import make_accepted_debt

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.services import pipelines, reviews
from bestseller.services.budget import ExecutionBudgetError, bind_execution_scope
from bestseller.services.critic_evidence_gate import validate_critic_commentary

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolated_semantic_dependencies(monkeypatch):
    """These boundary fixtures stub the reviewer; real dependency reads have separate tests."""
    monkeypatch.setattr(
        pipelines, "_chapter_critic_semantic_dependencies", AsyncMock(return_value="fixture-sources"),
    )
    # These isolated sessions deliberately do not implement ORM unit-of-work
    # state. Invariant initialization has its own real-SQLite boundary tests.
    monkeypatch.setattr(pipelines, "_ensure_project_invariants", AsyncMock())
    # Pipeline wiring fakes use the existing injected planning selector. They
    # have no ORM reader; source admission itself has real database regressions.
    # Preserve the actual selector for real SQLite sessions in this module.
    from sqlalchemy.ext.asyncio import AsyncSession
    from bestseller.services import outline_source_entry
    original = outline_source_entry.current_outline_artifact

    async def outline(session, *, project, chapter):
        if isinstance(session, AsyncSession):
            return await original(session, project=project, chapter=chapter)
        return await pipelines.get_latest_approved_planning_artifact(
            session, project_id=project.id,
            artifact_type=pipelines.ArtifactType.CHAPTER_OUTLINE_BATCH,
        )

    monkeypatch.setattr(outline_source_entry, "current_outline_artifact", outline)


@pytest.mark.parametrize("adopted", [False, True])
async def test_pipeline_passes_formal_adoption_selector_to_both_materializers(monkeypatch, adopted):
    from test_pipeline_services import (
        FakeSession,
        build_chapter,
        build_project,
        build_scene,
        build_settings,
    )
    from bestseller.infra.db.models import ChapterModel

    project = build_project()
    project.target_chapters = 500
    chapter = build_chapter(project.id)
    chapter.production_state = "ok"  # Must not grant adoption when the selector says False.
    scene = build_scene(project.id, chapter.id)

    class Session(FakeSession):
        async def scalar(self, stmt):
            return chapter if stmt.column_descriptions[0].get("entity") is ChapterModel else None

    class WiringObserved(RuntimeError):
        pass

    selector = AsyncMock(return_value=adopted)
    seen = []

    def chapter_materializer(**kwargs):
        seen.append(("chapter", kwargs["verified_adoption"]))
        return SimpleNamespace(to_dict=lambda: {})

    def scene_materializer(**kwargs):
        seen.append(("scenes", kwargs["verified_adoption"]))
        raise WiringObserved("Both actual pipeline callsites reached before writing")

    monkeypatch.setattr(pipelines, "get_project_by_slug", AsyncMock(return_value=project))
    monkeypatch.setattr(pipelines, "chapter_contract_has_adopted_source", selector)
    monkeypatch.setattr(
        pipelines, "materialize_chapter_contract_from_chapter", chapter_materializer
    )
    monkeypatch.setattr(pipelines, "materialize_chapter_scene_contracts", scene_materializer)
    settings = build_settings()
    settings.pipeline.enable_chapter_scene_contract_materializer = True
    with pytest.raises(WiringObserved):
        await pipelines.run_chapter_pipeline(
            Session(scalars_results=[[scene]]),
            settings,
            project.slug,
            1,
            chapter_first=True,
        )
    selector.assert_awaited_once()
    assert selector.call_args.kwargs["chapter"] is chapter
    assert seen == [("chapter", adopted), ("scenes", adopted)]


def _review():
    return ChapterReviewResult(
        verdict="rewrite",
        severity_max="critical",
        scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, 0.62)),
        rewrite_instructions="UNSUPPORTED_EDIT: erase the existing scene",
        evidence_summary={
            "llm_commercial_judge": {
                "pass": False,
                "rewrite_plan": {"instructions": "UNSUPPORTED_EDIT"},
            }
        },
    )


@pytest.mark.parametrize("status", ["evidence_invalid", "fallback", "unavailable"])
def test_bad_critic_becomes_unknown_and_preserves_original_as_diagnostics(status):
    original = _review()
    before = original.model_dump(mode="json")
    bad = validate_critic_commentary(
        "EVIDENCE: “This quoted scene never occurred.”\n"
        "COMMENTARY: Delete the scene and create a different ending.",
        chapter_text="The courier holds the sealed ledger.",
    )
    assert not bad.passed
    result = reviews._quarantine_unverified_chapter_review(
        original, status=status, findings=bad.findings
    )
    assert result.verdict == "unknown" and result.rewrite_instructions is None
    assert result.evidence_summary["review_evidence_failure"]["rewrite_authorized"] is False
    assert result.evidence_summary["unverified_review_snapshot"] == before
    assert original.model_dump(mode="json") == before


@pytest.mark.parametrize("second_valid", [False, True])
async def test_critic_retry_is_bounded_on_same_body_and_has_distinct_diagnostic_prompt(
    monkeypatch, second_valid
):
    draft = SimpleNamespace(
        id=uuid4(), project_id=uuid4(), chapter_id=uuid4(), content_md="A courier holds the ledger."
    )
    chapter = SimpleNamespace(metadata_json={})
    workflow = SimpleNamespace(id=uuid4(), metadata_json={})
    session = SimpleNamespace(
        flush=AsyncMock(),
        commit=AsyncMock(),
        scalar=AsyncMock(return_value=chapter),
        scalars=AsyncMock(return_value=[]),
    )
    calls = []

    async def review(*args, **kwargs):
        calls.append(kwargs)
        unknown = len(calls) == 1 or not second_valid
        report = SimpleNamespace(
            id=uuid4(),
            structured_output={
                "critic_evidence": {
                    "passed": not unknown,
                    "status": "evidence_invalid" if unknown else "verified",
                }
            },
        )
        return (_review(), report, SimpleNamespace(id=uuid4()), None)

    monkeypatch.setattr(pipelines, "review_chapter_draft", review)
    before = deepcopy(draft.__dict__)
    result = await pipelines._review_chapter_with_evidence_retry(
        session, object(), "generic-book", 1, workflow=workflow, draft=draft
    )
    assert len(calls) == 2 and session.commit.await_count >= 2
    assert "evidence_retry_context" not in calls[0]
    assert (
        calls[1]["evidence_retry_context"]["prior_report_id"]
        == workflow.metadata_json["critic_evidence_attempts"][0]["review_report_id"]
    )
    assert draft.__dict__ == before
    assert pipelines._chapter_critic_evidence_unknown(result[1]) is (not second_valid)
    assert len(workflow.metadata_json["critic_evidence_attempts"]) == 2


async def test_execution_budget_stop_never_gets_a_second_critic(monkeypatch):
    call = AsyncMock(side_effect=ExecutionBudgetError("execution_paused"))
    monkeypatch.setattr(pipelines, "review_chapter_draft", call)
    with pytest.raises(ExecutionBudgetError):
        await pipelines._review_chapter_with_evidence_retry(
            SimpleNamespace(
                scalar=AsyncMock(return_value=SimpleNamespace(metadata_json={})),
                scalars=AsyncMock(return_value=[]),
                commit=AsyncMock(),
            ),
            object(),
            "book",
            1,
            workflow=SimpleNamespace(id=uuid4(), metadata_json={}),
            draft=SimpleNamespace(
                id=uuid4(), project_id=uuid4(), chapter_id=uuid4(), content_md="Retained body"
            ),
        )
    assert call.await_count == 1


@pytest.mark.parametrize("change", ["completed", "draft_uuid", "policy", "missing_report", "semantic"])
async def test_durable_rounds_never_reopen_on_new_workflow_or_stale_receipt(
    db, monkeypatch, change
):
    from sqlalchemy.schema import CreateTable
    from bestseller.infra.db.models import (
        ChapterDraftVersionModel,
        QualityScoreModel,
        ReviewReportModel,
        WorkflowRunModel,
    )

    session, project, chapter, draft = db
    draft.promotion_state = "candidate"
    conn = await session.connection()
    ddl = str(CreateTable(WorkflowRunModel.__table__).compile(dialect=conn.dialect))
    await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    monkeypatch.setattr(pipelines, "chapter_review_recovery_source_hash", lambda: "f" * 64)
    calls = []

    async def review(*args, **kwargs):
        calls.append(kwargs)
        unknown = len(calls) == 1
        result = _review().model_copy(update={"verdict": "unknown" if unknown else "pass"})
        report = ReviewReportModel(
            project_id=project.id,
            target_type="chapter",
            target_id=chapter.id,
            reviewer_type="test",
            verdict=result.verdict,
            severity_max="critical",
            structured_output={
                "draft_id": str(draft.id),
                "reviewed_body_fingerprint": reviews.chapter_review_body_fingerprint(
                    draft.content_md
                ),
                "critic_evidence": {"status": "unavailable" if unknown else "verified"},
            },
        )
        session.add(report)
        await session.flush()
        quality = QualityScoreModel(
            project_id=project.id,
            target_type="chapter",
            target_id=chapter.id,
            chapter_draft_version_id=draft.id,
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
        session.add(quality)
        await session.flush()
        return result, report, quality, None

    monkeypatch.setattr(pipelines, "review_chapter_draft", review)

    async def run(current_draft):
        workflow = WorkflowRunModel(
            project_id=project.id,
            scope_type="chapter",
            scope_id=chapter.id,
            workflow_type="chapter_pipeline",
            status="running",
            metadata_json={},
        )
        session.add(workflow)
        await session.commit()
        return await pipelines._review_chapter_with_evidence_retry(
            session,
            object(),
            project.slug,
            chapter.chapter_number,
            workflow=workflow,
            draft=current_draft,
        )

    result = await run(draft)
    original_ids = (result[1].id, result[2].id)
    if change == "completed":
        replay = await run(draft)
        assert (replay[1].id, replay[2].id) == original_ids
    else:
        if change == "draft_uuid":
            draft = ChapterDraftVersionModel(
                id=uuid4(),
                project_id=project.id,
                chapter_id=chapter.id,
                version_no=2,
                content_md=draft.content_md,
                promotion_state="candidate",
                is_current=True,
            )
            session.add(draft)
            expected = "cycle_source_mismatch"
        elif change == "policy":
            monkeypatch.setattr(pipelines, "chapter_review_recovery_source_hash", lambda: "a" * 64)
            expected = "policy_changed_requires_explicit_recovery"
        elif change == "semantic":
            monkeypatch.setattr(pipelines, "_chapter_critic_semantic_dependencies",
                                AsyncMock(return_value="changed-real-dependencies"))
            expected = "semantic_dependencies_changed"
        else:
            await session.delete(result[1])
            expected = "result_binding_mismatch"
        with pytest.raises(ValueError, match=expected):
            await run(draft)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "promotion,numeric,adopted",
    [
        ("candidate", None, False),
        ("promoted", None, True),
        ("candidate", {"exact": "verified"}, True),
    ],
)
def test_terminal_status_requires_actual_adoption(promotion, numeric, adopted):
    workflow = SimpleNamespace(
        status="running", current_step="review", metadata_json={"old_history": [1, 2]}
    )
    chapter = SimpleNamespace(production_state="quality_debt", status="revision")
    draft = SimpleNamespace(id=uuid4(), content_md="Retained body", promotion_state=promotion)
    assert (
        pipelines._record_chapter_adoption_outcome(
            workflow=workflow, chapter=chapter, draft=draft, numeric_receipt=numeric
        )
        is adopted
    )
    assert workflow.metadata_json["actual_adoption_receipt"]["adopted"] is adopted
    assert workflow.metadata_json["old_history"] == [1, 2]
    if not adopted:
        assert (
            workflow.status == "machine_blocked"
            and workflow.current_step == "chapter_adoption_required"
        )
        assert chapter.production_state == "blocked"


async def test_explicit_policy_recovery_preserves_body_counters_and_invalid_task_history(
    monkeypatch,
):
    project = SimpleNamespace(id=uuid4(), metadata_json={})
    chapter = SimpleNamespace(
        id=uuid4(),
        metadata_json={"chapter_review_attempts_active": 9},
        production_state="quality_debt",
        status="revision",
    )
    draft = SimpleNamespace(
        id=uuid4(),
        chapter_id=chapter.id,
        project_id=project.id,
        is_current=True,
        promotion_state="candidate",
        content_md="Current retained body.",
    )
    previous = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        scope_id=chapter.id,
        workflow_type=pipelines.WORKFLOW_TYPE_CHAPTER_PIPELINE,
        status="completed",
        current_step="completed_with_quality_debt",
        metadata_json={"paid_history": ["paid"]},
    )
    report = SimpleNamespace(
        id=uuid4(), structured_output={"critic_evidence": {"status": "evidence_invalid"}}
    )
    task = SimpleNamespace(
        id=uuid4(),
        status="pending",
        metadata_json={"review_report_id": str(report.id), "original": "kept"},
    )
    mapping = {previous.id: previous, draft.id: draft, report.id: report}
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[0, chapter, chapter, 0, chapter]),
        scalars=AsyncMock(return_value=[task]),
        get=AsyncMock(side_effect=lambda model, key: mapping.get(key)),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(pipelines, "get_project_by_slug", AsyncMock(return_value=project))
    from bestseller.services import feedback

    lineage_guard = AsyncMock(return_value=({}, "source_not_adopted"))
    monkeypatch.setattr(feedback, "validate_adopted_knowledge_source", lineage_guard)
    run = AsyncMock(return_value="review-only-result")
    monkeypatch.setattr(pipelines, "run_chapter_pipeline", run)
    source_hash = pipelines.chapter_review_recovery_source_hash()
    kwargs = {
        "previous_workflow_run_id": previous.id,
        "expected_draft_id": draft.id,
        "expected_body_hash": reviews.chapter_review_body_fingerprint(draft.content_md)["digest"],
        "policy_revision": "critic-evidence-v2",
        "policy_source_hash": source_hash,
        "reason": "Evidence consumer fixed",
    }
    with bind_execution_scope("test-review-recovery"):
        result = await pipelines.resume_chapter_review_after_policy_change(
            session, object(), "book", 1, **kwargs
        )
        assert result == "review-only-result"
        with pytest.raises(ValueError, match="already_used"):
            await pipelines.resume_chapter_review_after_policy_change(
                session, object(), "book", 1, **kwargs
            )
    assert run.await_count == 1 and run.call_args.kwargs["chapter_first"] is True
    assert run.call_args.kwargs["supersede_pending_rewrites"] is False
    assert run.call_args.kwargs["resume_existing_draft_only"] is True
    assert run.call_args.kwargs["review_source_binding"] == {
        "draft_id": str(draft.id),
        "body_hash": kwargs["expected_body_hash"],
    }
    assert lineage_guard.call_args.kwargs["lock"] is True
    assert draft.content_md == "Current retained body." and draft.is_current
    assert chapter.metadata_json["chapter_review_attempts_active"] == 9
    assert chapter.metadata_json["chapter_review_active_cycle"]["attempts_base"] == 9
    assert task.status == "quarantined" and task.metadata_json["original"] == "kept"
    assert task.metadata_json["prior_status"] == "pending"
    assert previous.metadata_json["paid_history"] == ["paid"]
    assert previous.status == "machine_blocked"


@pytest.mark.parametrize("adoption", ["promoted", "candidate_debt", "unadopted"])
async def test_recovery_reads_real_adopted_lineage_including_candidate_debt(db, adoption):
    session, project, chapter, draft = db
    if adoption == "candidate_debt":
        await make_accepted_debt(db)
        draft.promotion_state = "candidate"
    elif adoption == "unadopted":
        draft.promotion_state = "candidate"
    await session.flush()
    body = draft.content_md
    metadata = deepcopy(chapter.metadata_json)
    if adoption == "unadopted":
        await pipelines._assert_chapter_review_source_unadopted(
            session, project=project, chapter=chapter, draft=draft
        )
    else:
        with pytest.raises(ValueError, match="source_already_adopted"):
            await pipelines._assert_chapter_review_source_unadopted(
                session, project=project, chapter=chapter, draft=draft
            )
    assert draft.content_md == body and chapter.metadata_json == metadata


@pytest.mark.parametrize("changed_body", [False, True])
async def test_recovery_budget_is_keyed_by_actual_policy_and_body_not_version_or_nonce(
    monkeypatch, changed_body
):
    from bestseller.services import feedback

    policy = pipelines.chapter_review_recovery_source_hash()
    old_body = "The courier set the ledger on the table."
    old_hash = reviews.chapter_review_body_fingerprint(old_body)["digest"]
    prior_cycle = {
        "policy_source_hash": policy,
        "source_body_hash": old_hash,
        "source_draft_id": str(uuid4()),
        "paid_receipts": ["preserved"],
    }
    project = SimpleNamespace(id=uuid4())
    chapter = SimpleNamespace(
        id=uuid4(), metadata_json={"chapter_review_recovery_cycles": [prior_cycle]}
    )
    # A new draft ID/version with identical text must not purchase another cycle.
    draft = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        is_current=True,
        promotion_state="candidate",
        content_md=old_body.replace("table", "locked drawer") if changed_body else old_body,
    )
    previous = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        scope_id=chapter.id,
        workflow_type=pipelines.WORKFLOW_TYPE_CHAPTER_PIPELINE,
        status="machine_blocked",
        current_step="critic_evidence_unknown",
        metadata_json={"old_paid_history": [1, 2]},
    )
    rows = {previous.id: previous, draft.id: draft}
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[0, chapter, chapter]),
        scalars=AsyncMock(return_value=[]),
        get=AsyncMock(side_effect=lambda model, key: rows.get(key)),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(pipelines, "get_project_by_slug", AsyncMock(return_value=project))
    monkeypatch.setattr(
        feedback,
        "validate_adopted_knowledge_source",
        AsyncMock(return_value=({}, "source_not_adopted")),
    )
    run = AsyncMock(return_value="review-result")
    monkeypatch.setattr(pipelines, "run_chapter_pipeline", run)
    kwargs = {
        "previous_workflow_run_id": previous.id,
        "expected_draft_id": draft.id,
        "expected_body_hash": reviews.chapter_review_body_fingerprint(draft.content_md)["digest"],
        "policy_source_hash": policy,
        "policy_revision": "changed-label-is-not-an-authorization",
        "reason": "Retained source received a targeted correction",
    }
    history = deepcopy(chapter.metadata_json["chapter_review_recovery_cycles"])
    with bind_execution_scope("test-policy-body-pair"):
        if changed_body:
            assert (
                await pipelines.resume_chapter_review_after_policy_change(
                    session, object(), "book", 1, **kwargs
                )
                == "review-result"
            )
        else:
            with pytest.raises(ValueError, match="already_used"):
                await pipelines.resume_chapter_review_after_policy_change(
                    session, object(), "book", 1, **kwargs
                )
    assert chapter.metadata_json["chapter_review_recovery_cycles"][:1] == history
    assert run.await_count == int(changed_body)
    if changed_body:
        assert len(chapter.metadata_json["chapter_review_recovery_cycles"]) == 2
        assert run.call_args.kwargs["review_source_binding"]["draft_id"] == str(draft.id)
        assert previous.metadata_json["old_paid_history"] == [1, 2]


@pytest.mark.parametrize(
    "dependency",
    [
        "payoff_ledger.py",
        "chekhov_emphasis_gate.py",
        "chapter_splice_coherence_gate.py",
        "chapter_first_sentence_diversity_gate.py",
        "judge_rubrics.py",
        "quality_gates.yaml",
        "commercial_review_context.py",
        "commercial_source_evidence.py",
        "commercial_judge_recovery.py",
        "commercial_review_wire.py",
        "commercial_review_tool.py",
        "review_source_context.py",
        "methodology_overlay.py",
        "chapter_draft_candidate_eligibility.py",
        "context.py",
        "model_catalog.yaml",
        "ideology_methodology.py",
        "judge_genre_context.py",
        "adopted_chapter_source.py",
        "story_effect_realization.py",
        "ideology_coherence_gate.py",
        "entry_system_kernel.py",
    ],
)
def test_recovery_policy_hash_tracks_actual_review_dependencies(monkeypatch, dependency):
    from pathlib import Path

    baseline = pipelines.chapter_review_recovery_source_hash()
    original = Path.read_bytes
    reads = []

    def changed(path):
        reads.append(path.name)
        content = original(path)
        return content + b"\n# changed real policy fixture" if path.name == dependency else content

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert pipelines.chapter_review_recovery_source_hash() != baseline
    assert dependency in reads


@pytest.mark.parametrize("score,blocked", [(12, False), (90, True)])
def test_retained_flavor_evaluation_never_patches_and_keeps_original_hard_findings(
    monkeypatch, score, blocked
):
    from bestseller.services import ai_flavor_gate
    from bestseller.services.ai_flavor.types import AiFlavorReport

    monkeypatch.setattr(
        ai_flavor_gate,
        "detect",
        lambda *a, **k: AiFlavorReport("zh-CN", 1, score, ()),
    )
    monkeypatch.setattr(
        ai_flavor_gate,
        "apply_patches",
        lambda *a, **k: pytest.fail("retained body must never enter patcher"),
    )
    outcome = pipelines._evaluate_chapter_ai_flavor_without_edits(
        chapter_number=1,
        content_md="Original retained bytes.",
        language="zh-CN",
        config=ai_flavor_gate.AiFlavorGateConfig(),
    )
    assert outcome.patched_text is None and outcome.edits == ()
    assert outcome.before_score == outcome.after_score == score
    assert (outcome.decision == "block") is blocked
    assert outcome.report.passed is (not blocked)


@pytest.mark.parametrize(
    "failure",
    [
        "unknown",
        "stale_report",
        "current_changed",
        "in_place_changed",
        "valid_unpromoted",
        "valid_rewrite",
    ],
)
async def test_real_pipeline_recovery_never_deslops_or_passes_unknown_or_stale_source(
    monkeypatch, failure
):
    from test_pipeline_services import (
        FakeSession,
        _disable_chapter_length_gate,
        build_chapter,
        build_project,
        build_scene,
        build_settings,
    )

    from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, WorkflowRunModel
    from bestseller.services import ai_flavor_gate, deslop_revise

    _disable_chapter_length_gate(monkeypatch)
    monkeypatch.setenv("LITSTYLE_LONGFORM_ADVISORY", "0")
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    draft = ChapterDraftVersionModel(
        id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=7,
        content_md="The courier set the sealed ledger beside the door.",
        word_count=1985,
        promotion_state="candidate",
        is_current=True,
    )
    newer = ChapterDraftVersionModel(
        id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=8,
        content_md="A different current source.",
        word_count=2000,
        promotion_state="candidate",
        is_current=True,
    )
    current = draft

    class CurrentSession(FakeSession):
        async def scalar(self, stmt):
            entity = stmt.column_descriptions[0].get("entity")
            if entity is ChapterModel:
                return chapter
            if entity is ChapterDraftVersionModel:
                return current
            return None

    session = CurrentSession(scalars_results=[[scene]])
    session.commit = AsyncMock()
    monkeypatch.setattr(pipelines, "get_project_by_slug", AsyncMock(return_value=project))
    monkeypatch.setattr(pipelines, "_evaluate_retention_safety_after_assembly", AsyncMock())
    for name in (
        "generate_chapter_draft_once",
        "_persist_deslop_candidate",
        "_select_deslop_candidate",
        "rewrite_chapter_from_task",
        "_promote_reviewed_chapter_draft",
    ):
        monkeypatch.setattr(pipelines, name, AsyncMock(side_effect=AssertionError(name)))
    best = AsyncMock(side_effect=AssertionError("implicit best selection forbidden"))
    monkeypatch.setattr(pipelines, "_promote_best_scoring_chapter_draft_on_stall", best)
    if failure == "valid_unpromoted":
        monkeypatch.setattr(
            pipelines,
            "_promote_reviewed_chapter_draft",
            AsyncMock(return_value=(False, None)),
        )
    deslop = AsyncMock(side_effect=AssertionError("no precritic rewrite"))
    monkeypatch.setattr(deslop_revise, "revise_prose_deslop", deslop)
    monkeypatch.setattr(
        ai_flavor_gate,
        "run_ai_flavor_gate",
        lambda **k: pytest.fail("recovery must use read-only original-body evaluation"),
    )
    monkeypatch.setattr(
        pipelines,
        "_evaluate_chapter_ai_flavor_without_edits",
        lambda **k: ai_flavor_gate.AiFlavorGateOutcome(
            enabled=True,
            language="zh-CN",
            chapter_number=1,
            before_score=30,
            after_score=30,
            patched_text="MUST NOT PERSIST",
            decision="pass",
        ),
    )
    reviews_seen = []

    async def review(*a, **k):
        nonlocal current
        reviews_seen.append(k)
        if failure == "current_changed":
            current = newer
            draft.is_current = False
        if failure == "in_place_changed":
            draft.content_md = "Externally changed bytes on the same ORM object."
        report = SimpleNamespace(
            id=uuid4(),
            verdict="unknown" if failure == "unknown" else "pass",
            structured_output={
                "draft_id": str(uuid4()) if failure == "stale_report" else str(draft.id),
                "reviewed_body_fingerprint": reviews.chapter_review_body_fingerprint(
                    draft.content_md
                ),
                "critic_evidence": {
                    "status": "evidence_invalid" if failure == "unknown" else "verified",
                },
            },
        )
        # Deliberately inconsistent consumer tuple: durable invalid evidence must win.
        task = SimpleNamespace(id=uuid4(), status="pending") if failure == "valid_rewrite" else None
        return (
            _review().model_copy(update={"verdict": "rewrite" if failure == "valid_rewrite" else "pass"}),
            report,
            SimpleNamespace(id=uuid4()),
            task,
        )

    monkeypatch.setattr(pipelines, "review_chapter_draft", review)
    settings = build_settings()
    settings.pipeline.enable_chapter_outline_readiness_gate = False
    settings.pipeline.enable_chapter_predraft_quality_gate = False
    settings.pipeline.enable_chapter_scene_contract_materializer = False
    original = draft.content_md
    result = await pipelines.run_chapter_pipeline(
        session,
        settings,
        project.slug,
        1,
        chapter_first=True,
        supersede_pending_rewrites=False,
        resume_existing_draft_only=True,
    )
    workflow = next(row for row in session.added if isinstance(row, WorkflowRunModel))
    verdict = {"valid_unpromoted": "pass", "valid_rewrite": "rewrite"}.get(failure, "unknown")
    assert result.final_verdict == verdict and result.requires_human_review
    assert result.chapter_draft_id == current.id
    assert (result.rewrite_task_id is not None) is (failure == "valid_rewrite")
    assert workflow.status == "machine_blocked"
    assert workflow.metadata_json["final_verdict"] == verdict
    assert workflow.metadata_json["actual_adoption_receipt"]["adopted"] is False
    assert workflow.metadata_json["review_recovery_source"] == {
        "draft_id": str(draft.id),
        "body_hash": reviews.chapter_review_body_fingerprint(original)["digest"],
    }
    assert session.commit.await_count >= 2
    assert len(reviews_seen) == (2 if failure == "unknown" else 1)
    if failure != "in_place_changed":
        assert draft.content_md == original
    assert deslop.await_count == best.await_count == 0
    assert not any(isinstance(row, ChapterDraftVersionModel) for row in session.added)


async def test_unknown_terminal_is_committed_even_for_detached_workflow(db):
    from sqlalchemy import select
    from sqlalchemy.schema import CreateTable

    from bestseller.infra.db.models import ChapterModel, WorkflowRunModel

    session, project, chapter, draft = db
    conn = await session.connection()
    ddl = str(CreateTable(WorkflowRunModel.__table__).compile(dialect=conn.dialect))
    await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    workflow = WorkflowRunModel(
        project_id=project.id,
        workflow_type="chapter_pipeline",
        status="running",
        scope_type="chapter",
        scope_id=chapter.id,
        current_step="review_chapter_v1",
        metadata_json={"paid_history": ["kept"]},
    )
    draft.promotion_state = "candidate"
    session.add(workflow)
    await session.commit()
    workflow_id = workflow.id
    session.expunge(workflow)
    result = await pipelines._finish_unknown_chapter_review(
        session,
        workflow=workflow,
        chapter=chapter,
        draft=draft,
        review_iterations=1,
    )
    saved = await session.scalar(
        select(WorkflowRunModel)
        .where(WorkflowRunModel.id == workflow_id)
        .execution_options(populate_existing=True)
    )
    saved_chapter = await session.scalar(
        select(ChapterModel)
        .where(ChapterModel.id == chapter.id)
        .execution_options(populate_existing=True)
    )
    assert saved.status == "machine_blocked" and saved.current_step == "critic_evidence_unknown"
    assert saved.metadata_json["paid_history"] == ["kept"]
    assert saved.metadata_json["final_verdict"] == result.final_verdict == "unknown"
    assert saved_chapter.production_state == "blocked"
    assert draft.is_current and draft.promotion_state == "candidate"
