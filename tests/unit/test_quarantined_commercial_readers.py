"""Real SQLite custody and retained inputs; synthetic returns, no provider or PG."""

from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.schema import CreateTable
import test_commercial_component_cohort as fixture
from test_commercial_component_cohort import _judge
from test_commercial_component_cohort import cohort as cohort
from test_commercial_quarantine_action_proofs import _all_rows
import test_commercial_slot_quarantine as quarantine_fixture
from test_commercial_slot_quarantine import _case

from bestseller.infra.db import models as m
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import llm
from bestseller.services import retained_review_input_proof as retained
from bestseller.services import waiting_chapter_review_resume as waiting
from bestseller.services.budget import bind_execution_scope, current_execution_scope
from bestseller.services.commercial_action_projection import project_commercial_actions
from bestseller.services.commercial_slot_quarantine import load_verified_quarantined_slot_proofs
from bestseller.services.commercial_source_evidence import assemble_commercial_sources
from bestseller.services.creation_contract_authority import select_creation_contracts
from bestseller.services.quality_gates_config import get_quality_gates_config

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
SCOPE = "conception-continuation:sqlite-quarantine-readers"


@pytest.fixture(autouse=True)
def reader_scope(monkeypatch):
    monkeypatch.setattr(fixture, "SCOPE", SCOPE)
    monkeypatch.setattr(quarantine_fixture, "SCOPE", SCOPE)


@pytest.fixture
async def custody(cohort, monkeypatch, tmp_path):
    r = cohort
    physical = fixture._review_completion

    def valid_return(**kwargs):
        wire = json.loads(kwargs["content"])
        wire["overall_score"] = 0.94
        wire["dimension_scores"] = dict.fromkeys(judge.chapter_commercial_thresholds(2)[1], 0.94)
        for issue in wire["issues"]:
            issue["severity"] = "low"
        kwargs["content"] = json.dumps(wire, ensure_ascii=False)
        return physical(**kwargs)

    monkeypatch.setattr(fixture, "_review_completion", valid_return)
    r.generation_input = {
        "approved_creation_contracts": select_creation_contracts({}, source_bound=False),
        "adopted_continuity": {},
    }
    case = await _case(r, monkeypatch)
    result = await _judge(r, bound=True)
    assert result.passed and not result.infrastructure_failure
    sources = assemble_commercial_sources(
        chapter_number=2,
        content_md=r.draft.content_md,
        source=judge._commercial_source_projection(
            r.generation_input,
            chapter_number=2,
            content_md=r.draft.content_md,
            previous_chapters=[],
        ),
        previous_chapters=[],
    )
    r.project.metadata_json = {**r.project.metadata_json, "execution_scope_id": SCOPE}
    r.owner.metadata_json = {**r.owner.metadata_json, "execution_scope_id": SCOPE}
    from sqlalchemy import select

    for run in await r.session.scalars(select(m.LlmRunModel)):
        run.metadata_json = {
            **run.metadata_json,
            "effective_input_receipt": {"hash": run.prompt_hash},
        }
    await r.session.commit()
    async with r.engine.begin() as connection:
        ddl = str(CreateTable(m.SceneCardModel.__table__).compile(dialect=connection.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    # Only upstream context storage is injected. Renderers, wire/ledger/source
    # verifiers and projector remain production code.
    from bestseller.services import (
        chapter_generation_input_builder,
        commercial_review_context,
        context,
    )

    async def packet(*args, **kwargs):
        return SimpleNamespace(story_bible={})

    async def history(*args, **kwargs):
        return {"recent_adopted_prose": []}

    monkeypatch.setattr(context, "build_chapter_writer_context", packet)
    monkeypatch.setattr(retained, "build_chapter_writer_context", packet)
    monkeypatch.setattr(
        chapter_generation_input_builder,
        "build_chapter_generation_input_bundle",
        lambda **kwargs: SimpleNamespace(model_dump=lambda **kw: {}),
    )
    monkeypatch.setattr(commercial_review_context, "load_authorized_commercial_history", history)
    monkeypatch.setenv("JUDGE_SAMPLES", "3")
    catalog = tmp_path / "reader_catalog.yaml"
    catalog.write_text('{"models": []}')
    monkeypatch.setattr(retained.model_catalog, "_CATALOG_PATH", catalog)
    with bind_execution_scope(SCOPE):
        proofs = await load_verified_quarantined_slot_proofs(
            r.session,
            project_id=r.project.id,
            chapter_id=r.chapter.id,
            content_md=r.draft.content_md,
            result=result,
            sources=sources,
        )
    view, projection = project_commercial_actions(
        result,
        sources=sources,
        chapter_number=2,
        gate_config=get_quality_gates_config().l6_gate,
        verified_quarantine_slots=proofs,
    )
    assert view is not None and projection["status"] == "projected", projection
    case.result, case.sources, case.view, case.projection = result, sources, view, projection
    return case


async def commercial(case, *, ambient=SCOPE):
    r = case.rig
    report = SimpleNamespace(
        structured_output={
            "evidence_summary": {
                "llm_commercial_judge_original": case.result.model_dump(mode="json"),
                "llm_commercial_judge": case.view,
                "commercial_action_projection": case.projection,
            }
        }
    )
    with bind_execution_scope(ambient) if ambient is not None else nullcontext():
        return await retained._commercial_proof(
            retained._ReadOnlySession(r.session),
            r.settings,
            project=r.project,
            chapter=r.chapter,
            draft=r.draft,
            report=report,
            catalog={},
            scope=SCOPE,
            commercial_sources=case.sources,
        )


async def prerequisite(case, *, ambient=SCOPE):
    r = case.rig
    with bind_execution_scope(ambient) if ambient is not None else nullcontext():
        return await waiting._normal_commercial_prerequisite(
            r.session,
            r.settings,
            project=r.project,
            chapter=r.chapter,
            draft=r.draft,
            owner=r.owner,
            scope=SCOPE,
        )


@pytest.mark.parametrize("reader", [commercial, prerequisite])
@pytest.mark.parametrize("ambient", [SCOPE, None])
async def test_two_paid_one_quarantine_reuse_is_readonly_compact_and_idempotent(
    custody, reader, ambient
):
    c = custody
    before = await _all_rows(c.rig)
    original = c.result.model_dump(mode="json")
    first = await reader(c, ambient=ambient)
    assert await reader(c, ambient=ambient) == first
    assert await _all_rows(c.rig) == before and c.result.model_dump(mode="json") == original
    assert current_execution_scope() is None and len(c.rig.calls) == 2 and c.forbidden == []
    encoded = json.dumps(first)
    assert "reconciliations" not in encoded and "error_chain" not in encoded
    assert c.rig.draft.content_md not in encoded
    if reader is commercial:
        assert len(first["llm_run_ids"]) == 2
        assert first["quarantined_slots"][c.slot]["abstains"] is True
    else:
        assert first["attempts_granted"] == first["slots_prepared"] == first["provider_calls"] == 0
        assert first["history_replays"] == 2


@pytest.mark.parametrize("reader", [commercial, prerequisite])
@pytest.mark.parametrize(
    "fault", ["missing", "receipt", "source", "contract", "scope", "unbound", "duplicate_paid"]
)
async def test_missing_tampered_source_policy_and_identity_fail_closed(custody, reader, fault):
    c, r = custody, custody.rig
    ambient = SCOPE
    if fault == "missing":
        row = await r.session.get(m.ExecutionOperationModel, c.reconciliation_key)
        await r.session.delete(row)
    elif fault == "receipt":
        row = await r.session.get(m.ExecutionOperationModel, c.reconciliation_key)
        data = deepcopy(row.result_json)
        data["receipt"]["credit_released"] = True
        row.result_json = data
    elif fault == "source":
        r.draft.content_md += "\nA different causal event."
    elif fault == "scope":
        ambient = "test:another-book"
    elif fault == "unbound":
        ambient = None
        r.project.metadata_json = {}
    else:
        owner = await r.session.get(m.WorkflowRunModel, c.owner_id, populate_existing=True)
        data = deepcopy(owner.metadata_json)
        journal = data["commercial_component_cohorts"][c.key]
        if fault == "contract":
            journal["binding"]["evaluation_contract"]["min_overall"] = 0.01
        else:
            journal["slots"]["chapter-quality.independent-v2:1"]["report"] = deepcopy(
                journal["slots"]["chapter-quality.independent-v2:0"]["report"]
            )
        owner.metadata_json = data
    await r.session.commit()
    before = await _all_rows(r)
    with pytest.raises((ValueError, TypeError, KeyError)):
        await reader(c, ambient=ambient)
    assert await _all_rows(r) == before and len(r.calls) == 2 and not c.forbidden
    assert current_execution_scope() is None


async def test_missing_pointer_historical_quarantine_is_not_new_cohort_credit(custody):
    c, r = custody, custody.rig
    r.session.add(
        m.ReviewReportModel(
            project_id=r.project.id,
            target_type="chapter",
            target_id=r.chapter.id,
            reviewer_type="critic",
            verdict="unknown",
            severity_max="critical",
            structured_output={
                "draft_id": str(r.draft.id),
                "reviewed_body_fingerprint": {
                    "digest": hashlib.sha256(r.draft.content_md.encode()).hexdigest()
                },
                "evidence_summary": {"llm_commercial_judge": c.result.model_dump(mode="json")},
            },
        )
    )
    r.chapter.metadata_json = {}
    await r.session.commit()
    before = await _all_rows(r)
    with pytest.raises(ValueError, match="complete_receipt_required"):
        await prerequisite(c)
    assert await _all_rows(r) == before and len(r.calls) == 2


async def _primary(case, monkeypatch, tmp_path):
    r = case.rig
    source = {
        "schema": retained.SOURCE_POLICY,
        "adopted_continuity": {"recent_adopted_prose": []},
        "current_source_channels": {},
        "authority": retained._SOURCE_AUTHORITY,
    }

    async def load(*args, **kwargs):
        return deepcopy(source)

    monkeypatch.setattr(retained, "load_review_source_context", load)
    request = llm.LLMCompletionRequest(
        logical_role="critic",
        system_prompt="Full retained primary system.",
        user_prompt=json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        fallback_response="unused",
        project_id=r.project.id,
        workflow_run_id=r.owner.id,
        prompt_template="chapter_review",
        prompt_version="fixture-v1",
    )
    h = llm._effective_input_hash(request, r.settings.llm.critic)
    run_id, root, aid = uuid4(), uuid4().hex, uuid4().hex
    execution = {
        "scope_id": SCOPE,
        "operation_key": root,
        "attempts": [{"attempt_id": aid, "status": "completed"}],
    }
    paid = llm.LLMCompletionResult(
        content="VERDICT: pass",
        provider="minimax",
        model_name=r.settings.llm.critic.model,
        finish_reason="stop",
        input_tokens=3,
        output_tokens=4,
        metadata={
            "paid_call_origin": llm._paid_call_origin(request, h),
            "effective_input_receipt": {"hash": h},
        },
    ).model_dump(mode="json")
    payload = {
        "schema_version": "llm-payload.v1",
        "kind": "request",
        "replayable": True,
        "llm_run_id": str(run_id),
        "project_id": str(r.project.id),
        "workflow_run_id": str(r.owner.id),
        "logical_role": "critic",
        "prompt_template": request.prompt_template,
        "prompt_version": request.prompt_version,
        "requested": {
            k: getattr(request, k)
            for k in (
                "model_tier",
                "model_catalog_key",
                "temperature_override",
                "max_tokens_override",
                "cache_system",
            )
        },
        "messages": {
            "source": "system_user",
            "replayable": True,
            "system": llm._bounded_text(request.system_prompt),
            "user": llm._bounded_text(request.user_prompt),
        },
    }
    path = tmp_path / "primary.request.json"
    path.write_text(json.dumps(payload))
    r.settings.artifact_store.local_dir = str(tmp_path)
    until = datetime.now(UTC) + timedelta(hours=1)
    r.session.add_all(
        [
            m.LlmRunModel(
                id=run_id,
                project_id=r.project.id,
                workflow_run_id=r.owner.id,
                logical_role="critic",
                provider="minimax",
                model_name=r.settings.llm.critic.model,
                prompt_template=request.prompt_template,
                prompt_version=request.prompt_version,
                prompt_hash=h,
                finish_reason="stop",
                request_payload_ref=path.name,
                metadata_json={
                    "execution_receipt": execution,
                    "effective_input_receipt": {"hash": h},
                    "review_source_receipt": {
                        "source_complete": True,
                        "source_sha256": retained._hash(source),
                    },
                },
            ),
            m.ExecutionOperationModel(
                key=root,
                scope_id=SCOPE,
                input_hash=h,
                status="completed",
                owner="primary",
                lease_until=until,
                result_json=paid,
            ),
            m.ExecutionAttemptModel(
                id=aid,
                operation_key=root,
                scope_id=SCOPE,
                input_hash=h,
                model_name=r.settings.llm.critic.model,
                status="completed",
                reserved_tokens=7,
                actual_tokens=7,
                created_at=datetime.now(UTC),
            ),
            m.ExecutionOperationModel(
                key=hashlib.sha256(f"transport-result.v1\n{SCOPE}\n{aid}".encode()).hexdigest(),
                scope_id=SCOPE,
                input_hash=h,
                status="transport_result",
                owner="primary",
                lease_until=until,
                result_json={
                    "operation_key": root,
                    "attempt_id": aid,
                    "input_hash": h,
                    "result": paid,
                },
            ),
        ]
    )
    report = m.ReviewReportModel(
        project_id=r.project.id,
        target_type="chapter",
        target_id=r.chapter.id,
        reviewer_type="critic",
        verdict="pass",
        severity_max="info",
        llm_run_id=run_id,
        structured_output={
            "draft_id": str(r.draft.id),
            "evidence_summary": {
                "llm_commercial_judge_original": case.result.model_dump(mode="json"),
                "llm_commercial_judge": case.view,
                "commercial_action_projection": case.projection,
            },
        },
    )
    r.session.add(report)
    await r.session.commit()
    cycle = {
        "state": "completed",
        "cycle_id": "retained-reader-test",
        "source_draft_id": str(r.draft.id),
        "source_body_hash": hashlib.sha256(r.draft.content_md.encode()).hexdigest(),
        "review_workflow_run_id": str(r.owner.id),
    }
    return report, cycle


@pytest.mark.parametrize("fault", [None, "old_unknown", "cycle", "primary_source"])
async def test_actual_retained_entry_preserves_primary_grade_and_cycle_fences(
    custody, monkeypatch, tmp_path, fault
):
    c, r = custody, custody.rig
    report, cycle = await _primary(c, monkeypatch, tmp_path)
    if fault == "old_unknown":
        report.verdict, report.llm_run_id = "unknown", None
    elif fault == "cycle":
        cycle["state"] = "waiting_infrastructure"
    elif fault == "primary_source":
        path = tmp_path / "primary.request.json"
        data = json.loads(path.read_text())
        data["messages"]["user"] = llm._bounded_text("Changed source")
        path.write_text(json.dumps(data))
    await r.session.commit()
    before = await _all_rows(r)

    async def verify():
        return await retained.verify_retained_review_inputs(
            r.session,
            r.settings,
            project=r.project,
            chapter=r.chapter,
            draft=r.draft,
            report=report,
            cycle=cycle,
        )

    if fault:
        with pytest.raises(ValueError):
            await verify()
    else:
        result = await verify()
        assert len(result["commercial"]["llm_run_ids"]) == 2
        assert result["commercial"]["quarantined_slots"][c.slot]["abstains"] is True
        assert result["writes"] == result["provider_calls"] == 0
    assert await _all_rows(r) == before and len(r.calls) == 2
    assert current_execution_scope() is None


@pytest.mark.parametrize("fault", [None, "policy", "semantic"])
async def test_actual_waiting_cursor_rearms_same_slot_without_new_grade_or_allowance(
    custody, monkeypatch, fault
):
    from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
    from bestseller.services import pipelines
    from bestseller.services.chapter_review_prerequisite import (
        WAITING,
        prerequisite_wait_receipt,
        wait_binding,
    )

    c, r = custody, custody.rig
    async with r.engine.begin() as connection:
        ddl = str(CreateTable(m.QualityScoreModel.__table__).compile(dialect=connection.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))

    async def semantic(*args):
        return "a" * 64

    monkeypatch.setattr(pipelines, "_chapter_critic_semantic_dependencies", semantic)
    monkeypatch.setattr(pipelines, "chapter_review_recovery_source_hash", lambda: "current-policy")
    fingerprint = pipelines.chapter_review_body_fingerprint(r.draft.content_md)
    cid = pipelines._chapter_critic_cycle_id(r.project.id, r.chapter.id, fingerprint["digest"])
    entry = {"attempt": 1, "state": WAITING, "operation_namespace": f"chapter-critic:{cid}:1"}
    cycle = {
        "schema_version": "chapter-critic-cycle.v1",
        "state": WAITING,
        "cycle_id": cid,
        "source_draft_id": str(r.draft.id),
        "source_body_hash": fingerprint["digest"],
        "policy_source_hash": "current-policy",
        "semantic_dependencies_sha256": "a" * 64,
        "review_workflow_run_id": str(r.owner.id),
        "attempts": [entry],
    }
    result = ChapterReviewResult(
        verdict="unknown",
        severity_max="high",
        scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, 0.9)),
        evidence_summary={
            "primary_review_not_dispatched": {
                "reason": "required_judge_evidence_unavailable",
                "provider_calls": 0,
                "body_fingerprint": fingerprint,
            }
        },
    )
    report = m.ReviewReportModel(
        project_id=r.project.id,
        target_type="chapter",
        target_id=r.chapter.id,
        reviewer_type="critic",
        verdict="unknown",
        severity_max="high",
        llm_run_id=None,
        structured_output={
            **result.model_dump(mode="json"),
            "draft_id": str(r.draft.id),
            "reviewed_body_fingerprint": fingerprint,
            "primary_submission_original": None,
            "primary_submission_receipt": None,
            "untrusted_critic_response": "",
            "critic_evidence": {"passed": False, "status": "unavailable"},
        },
    )
    r.session.add(report)
    await r.session.flush()
    quality = m.QualityScoreModel(
        project_id=r.project.id,
        target_type="chapter",
        target_id=r.chapter.id,
        chapter_draft_version_id=r.draft.id,
        review_report_id=report.id,
        evaluation_round=1,
        judge_key="chapter_quality_v1",
        is_current=True,
        score_overall=0.9,
        evidence_summary={},
    )
    r.session.add(quality)
    await r.session.flush()
    receipt = prerequisite_wait_receipt(
        report,
        binding=wait_binding(
            cycle,
            entry,
            project_id=r.project.id,
            chapter_id=r.chapter.id,
            body_fingerprint=fingerprint,
        ),
    )
    assert receipt is not None
    entry["infrastructure_history"] = [
        {
            "attempt": 1,
            "outcome": "unknown",
            "draft_id": str(r.draft.id),
            "body_fingerprint": fingerprint,
            "review_result": result.model_dump(mode="json"),
            "review_report_id": str(report.id),
            "quality_score_id": str(quality.id),
            "rewrite_task_id": None,
            "waiting_receipt": receipt,
        }
    ]
    r.owner.status, r.owner.current_step = "machine_blocked", WAITING
    if fault == "policy":
        cycle["policy_source_hash"] = "old-policy"
    if fault == "semantic":
        cycle["semantic_dependencies_sha256"] = "b" * 64
    pipelines._store_chapter_critic_cycle(r.chapter, cycle)
    await r.session.commit()
    actual = await prerequisite(c)
    probe = {
        **actual,
        "status": "completed",
        "blocked_gateway_attempts": 0,
        "data_updates": 0,
        "protected_rows_and_ledger_unchanged": True,
    }
    kw = {
        "project_id": r.project.id,
        "chapter_id": r.chapter.id,
        "requested_by": "offline-test",
        "prerequisite_proof": probe,
    }
    before = await _all_rows(r)
    await r.session.refresh(quality)
    old_quality = waiting._row(quality)
    if fault:
        with pytest.raises(ValueError, match=r"policy_changed|semantic_dependencies_changed"):
            await waiting.resume_waiting_chapter_review(r.session, r.settings, **kw)
        assert await _all_rows(r) == before
        return
    plan = await waiting.resume_waiting_chapter_review(r.session, r.settings, **kw)
    assert await _all_rows(r) == before
    rearmed = await waiting.resume_waiting_chapter_review(
        r.session, r.settings, **kw, execute=True, expected_plan_sha256=plan["plan_sha256"]
    )
    await r.session.commit()
    current = r.chapter.metadata_json["chapter_critic_cycles"][cid]
    assert current["state"] == current["attempts"][0]["state"] == "prepared"
    assert len(current["attempts"]) == 1
    assert current["attempts"][0]["operation_namespace"] == entry["operation_namespace"]
    assert current["attempts"][0]["infrastructure_history"] == entry["infrastructure_history"]
    assert rearmed["new_attempts"] == 0 and rearmed["credit_released"] is False
    assert rearmed["dispatch_authorized"] is rearmed["adoption_authorized"] is False
    after = await _all_rows(r)
    assert all(after[k] == v for k, v in before.items() if k != "chapters")
    await r.session.refresh(report)
    await r.session.refresh(quality)
    assert waiting._row(quality) == old_quality
    assert report.verdict == "unknown" and report.llm_run_id is None
    assert len(r.calls) == 2 and not c.forbidden
