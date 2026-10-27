"""Real orchestration/preflight/cache, stub storage; stop at editor dispatch.

No PG/provider or new prose is produced. Deterministic literary outcomes and
unrelated planning/storage boundaries are controlled, not production acceptance.
"""

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import inspect
from test_pipeline_services import (
    FakeSession,
    _disable_chapter_length_gate,
    build_chapter,
    build_project,
    build_scene,
    build_settings,
)

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    SceneCardModel,
    WorkflowRunModel,
)
from bestseller.services import (
    ai_flavor_gate,
    chapter_orchestrator,
    deslop_revise,
    drafts,
    llm,
    pipelines,
    reader_judge,
    reviews,
    style_phase_receipts,
    world_law_consistency_gate,
    world_model_injection,
)
from bestseller.services.rewrite_patch import text_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class DispatchBoundary(BaseException):
    """An expected dispatch is observed, never sent to a provider."""


class ForbiddenPaidWork(BaseException):
    """Do not let best-effort catches hide an unexpected paid stage."""


@pytest.fixture
def resume_case(monkeypatch, tmp_path):
    _disable_chapter_length_gate(monkeypatch)
    cfg = pipelines.get_quality_gates_config()
    cfg = replace(
        cfg,
        originality_engine=replace(
            cfg.originality_engine,
            enabled=True,
            persist_persona_feedback=True,
            auto_voice_dna=False,
        ),
        reader_quality=replace(cfg.reader_quality, enable_llm_reader_judge=True),
        ai_flavor=replace(cfg.ai_flavor, enabled=True),
    )
    monkeypatch.setattr(pipelines, "get_quality_gates_config", lambda: cfg)
    monkeypatch.setenv("LITSTYLE_LONGFORM_ADVISORY", "1")
    settings = build_settings()
    settings.output.base_dir = tmp_path
    settings.pipeline.enable_chapter_outline_readiness_gate = False
    settings.pipeline.enable_chapter_predraft_quality_gate = False
    settings.pipeline.enable_chapter_scene_contract_materializer = False
    settings.pipeline.enable_chapter_auto_repair = True
    settings.pipeline.chapter_auto_repair_max_attempts = 2
    settings.pipeline.retention_safety_gate_block_on_failure = True
    settings.pipeline.enable_fanqie_long_ranking_gate = True
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.production_state = "blocked"
    scene = build_scene(project.id, chapter.id)
    draft = ChapterDraftVersionModel(
        id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=20,
        content_md="灯塔管理员把封好的航图交给领航员。领航员当着众人的面收进匣中并锁好匣子。",
        word_count=2400,
        promotion_state="candidate",
        is_current=True,
    )
    fingerprint = pipelines.chapter_review_body_fingerprint(draft.content_md)
    report = ReviewReportModel(
        id=uuid4(),
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        reviewer_type="retained-fixture",
        verdict="rewrite",
        structured_output={
            "draft_id": str(draft.id),
            "reviewed_body_fingerprint": fingerprint,
            "critic_evidence": {"status": "verified"},
        },
    )
    quality = QualityScoreModel(
        id=uuid4(),
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        chapter_draft_version_id=draft.id,
        review_report_id=report.id,
        score_overall=0.86,
    )
    task = RewriteTaskModel(
        id=uuid4(),
        project_id=project.id,
        trigger_type="chapter_review",
        trigger_source_id=chapter.id,
        rewrite_strategy="chapter_coherence_bridge_rewrite",
        instructions="Clarify the consequence of locking the chart.",
        status="pending",
        attempts=0,
        metadata_json={
            "chapter_id": str(chapter.id),
            "review_report_id": str(report.id),
            "draft_id": str(draft.id),
            "parent_draft_id": str(draft.id),
            "parent_draft_hash": text_hash(draft.content_md),
        },
    )
    review_result = ChapterReviewResult(
        verdict="rewrite",
        severity_max="major",
        scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, 0.86)),
        rewrite_instructions=task.instructions,
    )
    cycle_id = pipelines._chapter_critic_cycle_id(project.id, chapter.id, fingerprint["digest"])
    review_owner = WorkflowRunModel(
        id=uuid4(),
        project_id=project.id,
        scope_type="chapter",
        scope_id=chapter.id,
        workflow_type="chapter_pipeline",
        status="completed",
        metadata_json={},
    )
    cycle = {
        "schema_version": "chapter-critic-cycle.v1",
        "cycle_id": cycle_id,
        "state": "completed",
        "source_draft_id": str(draft.id),
        "source_body_hash": fingerprint["digest"],
        "policy_source_hash": "policy-now",
        "semantic_dependencies_sha256": "source-now",
        "review_workflow_run_id": str(review_owner.id),
        "attempts": [
            {
                "attempt": 1,
                "operation_namespace": f"chapter-critic:{cycle_id}:1",
                "draft_id": str(draft.id),
                "body_fingerprint": fingerprint,
                "state": "completed",
                "outcome": "available",
                "review_report_id": str(report.id),
                "quality_score_id": str(quality.id),
                "rewrite_task_id": str(task.id),
                "review_result": review_result.model_dump(mode="json"),
            }
        ],
    }
    active = {
        **deepcopy(cycle),
        "cycle_id": "old16",
        "source_draft_id": str(uuid4()),
        "source_body_hash": "f" * 64,
    }
    chapter.metadata_json = {
        "production_block_code": "OPENING_PRESSURE_THIN",
        "chapter_review_active_cycle": active,
        "chapter_critic_cycles": {cycle_id: cycle, "old16": deepcopy(active)},
    }

    class Session(FakeSession):
        async def execute(self, stmt):
            if not stmt.is_select:
                return await super().execute(stmt)
            # The source reader selects every mapped column with a Core PK
            # predicate, not an ORM entity. Model that read without delegating
            # it to FakeSession's ORM-only predicate evaluator.
            keys = [column.key for column in inspect(ChapterModel).column_attrs]
            if list(stmt.selected_columns.keys()) != keys:
                raise AssertionError(f"Unexpected column read: {stmt}")
            if list(stmt.compile().params.values()) != [chapter.id]:
                raise AssertionError("Unexpected chapter source lookup")
            self.executed.append(stmt)
            row = {key: deepcopy(getattr(chapter, key)) for key in keys}
            return SimpleNamespace(mappings=lambda: [row])

        async def scalar(self, stmt):
            entity = stmt.column_descriptions[0].get("entity")
            if entity is ChapterModel:
                return chapter
            if entity is ChapterDraftVersionModel:
                return draft
            return None

        async def scalars(self, stmt):
            entity = stmt.column_descriptions[0].get("entity")
            if entity is SceneCardModel:
                return [scene]
            if entity is ChapterDraftVersionModel:
                return [draft]
            return []

    session = Session(
        get_map={
            (WorkflowRunModel, review_owner.id): review_owner,
            (ReviewReportModel, report.id): report,
            (QualityScoreModel, quality.id): quality,
            (RewriteTaskModel, task.id): task,
        }
    )
    session.no_autoflush = nullcontext()
    session.dirty = set()
    session.commit = AsyncMock()
    monkeypatch.setattr(pipelines, "get_project_by_slug", AsyncMock(return_value=project))
    monkeypatch.setattr(
        pipelines, "_chapter_critic_semantic_dependencies", AsyncMock(return_value="source-now")
    )
    monkeypatch.setattr(pipelines, "chapter_review_recovery_source_hash", lambda: "policy-now")
    monkeypatch.setattr(pipelines, "_evaluate_retention_safety_after_assembly", AsyncMock())
    monkeypatch.setattr(pipelines, "extract_chapter_state_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(
        chapter_orchestrator, "prepare_chapter_context", Mock(return_value=object())
    )
    monkeypatch.setattr(chapter_orchestrator, "grade_chapter", Mock(return_value=object()))
    monkeypatch.setattr(
        world_model_injection,
        "extract_world_model",
        Mock(return_value={"laws": ["The chart stays locked until arrival."]}),
    )
    forbidden = {}

    def deny(module, name, *, sync=False):
        spy = (Mock if sync else AsyncMock)(side_effect=ForbiddenPaidWork(name))
        monkeypatch.setattr(module, name, spy)
        forbidden[name] = spy

    for name in (
        "_ensure_emotion_kernel_backfill_for_pipeline",
        "_ensure_public_emotion_kernel_backfill_for_pipeline",
        "_ensure_entry_system_backfill_for_pipeline",
        "generate_chapter_draft_once",
        "_run_fanqie_long_gate_for_chapter",
        "_persist_deslop_candidate",
        "_stop_auto_repair_if_latest_quality_clean",
        "review_chapter_draft",
    ):
        deny(pipelines, name)
    deny(drafts, "maybe_prepare_chapter_auto_repair")
    deny(reader_judge, "judge_chapter_readability")
    deny(world_law_consistency_gate, "evaluate_world_law_consistency_llm")
    deny(deslop_revise, "revise_prose_deslop")
    deny(style_phase_receipts, "close_style_phase")
    deny(llm, "complete_text")
    deny(reviews, "complete_text")
    from bestseller.services import litstyle_prose_judge

    deny(litstyle_prose_judge, "judge_chapter_litstyle_stable")
    deny(ai_flavor_gate, "run_ai_flavor_gate", sync=True)
    monkeypatch.setattr(style_phase_receipts, "completed_style_phase", AsyncMock(return_value=None))
    monkeypatch.setattr(
        style_phase_receipts, "style_policy_fingerprint", lambda **kwargs: "style-now"
    )
    monkeypatch.setattr(
        style_phase_receipts, "draft_application_binding", AsyncMock(return_value={})
    )
    observer = Mock(
        return_value=ai_flavor_gate.AiFlavorGateOutcome(
            enabled=True,
            language="zh-CN",
            chapter_number=1,
            before_score=30,
            after_score=30,
            patched_text="This must never replace the current body.",
            decision="pass",
        )
    )
    monkeypatch.setattr(pipelines, "_evaluate_chapter_ai_flavor_without_edits", observer)
    return SimpleNamespace(
        session=session,
        settings=settings,
        project=project,
        chapter=chapter,
        draft=draft,
        report=report,
        quality=quality,
        task=task,
        cycle=deepcopy(cycle),
        active=deepcopy(active),
        review_owner=review_owner,
        forbidden=forbidden,
        observer=observer,
    )


async def test_normal_entry_consumes_current_cached_review_then_dispatches_same_pending_rewrite(
    resume_case,
    monkeypatch,
):
    rig = resume_case
    events = []
    actual_consume = pipelines._review_chapter_with_evidence_retry

    async def consume(*args, **kwargs):
        result = await actual_consume(*args, **kwargs)
        events.append(("cached_review", result[1].id, result[3].id))
        return result

    async def rewrite(session, slug, chapter_number, **kwargs):
        events.append(("rewrite", kwargs["rewrite_task_id"]))
        assert kwargs["rewrite_task_id"] == rig.task.id
        raise DispatchBoundary("normal editor dispatch reached without executing a provider")

    monkeypatch.setattr(pipelines, "_review_chapter_with_evidence_retry", consume)
    monkeypatch.setattr(pipelines, "rewrite_chapter_from_task", rewrite)
    original = rig.draft.content_md
    with pytest.raises(DispatchBoundary):
        await pipelines.run_chapter_pipeline.__wrapped__(
            rig.session,
            rig.settings,
            rig.project.slug,
            1,
            chapter_first=True,
            supersede_pending_rewrites=False,
            resume_existing_draft_only=False,
        )
    assert events == [("cached_review", rig.report.id, rig.task.id), ("rewrite", rig.task.id)]
    assert rig.observer.call_count == 1
    assert chapter_orchestrator.grade_chapter.call_count == 1
    assert all(spy.call_count == 0 for spy in rig.forbidden.values())
    assert rig.draft.content_md == original and rig.task.status == "pending"
    assert rig.chapter.metadata_json["chapter_review_active_cycle"] == rig.active
    assert rig.chapter.metadata_json["chapter_critic_cycles"][rig.cycle["cycle_id"]] == rig.cycle
    workflow = next(row for row in rig.session.added if isinstance(row, WorkflowRunModel))
    receipt = workflow.metadata_json["retained_review_first_reuse"]
    assert receipt["draft_id"] == str(rig.draft.id) and receipt["task_id"] == str(rig.task.id)


async def test_body_change_before_style_dispatch_invalidates_entry_proof(resume_case, monkeypatch):
    rig = resume_case
    original = rig.draft.content_md

    async def changed_binding(session, draft):
        draft.content_md += "领航员又取出另一张航图。"
        return {}

    monkeypatch.setattr(style_phase_receipts, "draft_application_binding", changed_binding)
    gate = Mock(side_effect=DispatchBoundary("changed body requires ordinary style admission"))
    monkeypatch.setattr(ai_flavor_gate, "run_ai_flavor_gate", gate)
    critic = AsyncMock(side_effect=ForbiddenPaidWork("stale proof reached cached review"))
    monkeypatch.setattr(pipelines, "_review_chapter_with_evidence_retry", critic)
    with pytest.raises(DispatchBoundary):
        await pipelines.run_chapter_pipeline.__wrapped__(
            rig.session,
            rig.settings,
            rig.project.slug,
            1,
            chapter_first=True,
            supersede_pending_rewrites=False,
            resume_existing_draft_only=False,
        )
    gate.assert_called_once()
    rig.observer.assert_not_called()
    critic.assert_not_awaited()
    assert rig.draft.content_md != original
    assert all(spy.call_count == 0 for spy in rig.forbidden.values())


@pytest.mark.parametrize("state", ["prepared", "running"])
async def test_partial_review_main_entry_skips_craft_and_resumes_original_review_dispatch(
    resume_case,
    monkeypatch,
    state,
):
    from bestseller.services import llm

    rig = resume_case
    cycle = deepcopy(rig.cycle)
    cycle.update(
        state=state,
        attempts=[
            {
                "attempt": 1,
                "state": state,
                "operation_namespace": f"chapter-critic:{cycle['cycle_id']}:1",
            }
        ],
    )
    rig.chapter.metadata_json = {
        **rig.chapter.metadata_json,
        "chapter_critic_cycles": {
            **rig.chapter.metadata_json["chapter_critic_cycles"],
            cycle["cycle_id"]: cycle,
        },
    }
    calls = []

    async def review_boundary(*args, **kwargs):
        calls.append((kwargs["workflow_run_id"], llm._semantic_operation_namespace.get()[0]))
        raise DispatchBoundary("resume existing review operation, never dispatch provider")

    monkeypatch.setattr(pipelines, "review_chapter_draft", review_boundary)
    editor = AsyncMock(side_effect=ForbiddenPaidWork("pending review cannot authorize editing"))
    monkeypatch.setattr(pipelines, "rewrite_chapter_from_task", editor)
    with pytest.raises(DispatchBoundary):
        await pipelines.run_chapter_pipeline.__wrapped__(
            rig.session,
            rig.settings,
            rig.project.slug,
            1,
            chapter_first=True,
            supersede_pending_rewrites=False,
            resume_existing_draft_only=False,
        )
    assert calls == [(rig.review_owner.id, f"chapter-critic:{cycle['cycle_id']}:1")]
    editor.assert_not_awaited()
    assert rig.observer.call_count == 1
    assert all(spy.call_count == 0 for spy in rig.forbidden.values())
    workflow = next(row for row in rig.session.added if isinstance(row, WorkflowRunModel))
    proof = workflow.metadata_json["retained_review_first_reuse"]
    assert proof["reuse_kind"] == "resume_pending_review"
    assert all(
        proof[key] is None for key in ("verdict", "report_id", "quality_score_id", "task_id")
    )
    assert rig.task.status == "pending" and rig.task.attempts == 0


async def test_terminal_unknown_main_entry_skips_craft_and_never_executes_rewrite(
    resume_case, monkeypatch
):
    rig = resume_case
    cycle = deepcopy(rig.cycle)
    first = deepcopy(cycle["attempts"][0])
    first.update(outcome="unknown", rewrite_task_id=None)
    first["review_result"]["verdict"] = "unknown"
    second = deepcopy(first)
    second.update(attempt=2, operation_namespace=f"chapter-critic:{cycle['cycle_id']}:2")
    cycle["attempts"] = [first, second]
    rig.chapter.metadata_json = {
        **rig.chapter.metadata_json,
        "chapter_critic_cycles": {
            **rig.chapter.metadata_json["chapter_critic_cycles"],
            cycle["cycle_id"]: cycle,
        },
    }
    editor = AsyncMock(side_effect=ForbiddenPaidWork("unknown cannot authorize editing"))
    monkeypatch.setattr(pipelines, "rewrite_chapter_from_task", editor)
    result = await pipelines.run_chapter_pipeline.__wrapped__(
        rig.session,
        rig.settings,
        rig.project.slug,
        1,
        chapter_first=True,
        supersede_pending_rewrites=False,
        resume_existing_draft_only=False,
    )
    assert result.final_verdict == "unknown" and result.requires_human_review
    assert result.chapter_draft_id == rig.draft.id
    editor.assert_not_awaited()
    assert rig.observer.call_count == 1
    assert all(spy.call_count == 0 for spy in rig.forbidden.values())
    workflow = next(row for row in rig.session.added if isinstance(row, WorkflowRunModel))
    assert workflow.status == "machine_blocked"
    proof = workflow.metadata_json["retained_review_first_reuse"]
    assert proof["reuse_kind"] == "consume_retained_unknown"
    assert proof["verdict"] == "unknown" and proof["task_id"] is None
    assert proof["report_id"] == str(rig.report.id)
    assert workflow.metadata_json["actual_adoption_receipt"]["adopted"] is False
    assert rig.chapter.metadata_json["chapter_critic_cycles"][cycle["cycle_id"]] == cycle
