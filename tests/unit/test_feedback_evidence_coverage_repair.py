"""Native strict evidence coverage plus one bounded child; no real provider.

Only the model wire is controlled in gateway cases. SQLite claim/reservation,
input hashing, caching and source/decision validators remain production code.
"""

from copy import deepcopy
import hashlib
import json
import socket
from types import SimpleNamespace
from uuid import uuid4

from gateway_ledger_support import (
    gateway_ledger,  # noqa: F401
    replay_reserved_http_send,
)
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_adopted_knowledge_lineage import db  # noqa: F401

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
)
from bestseller.services import feedback, llm
from bestseller.services import knowledge_evidence_reconciliation as service
from bestseller.services.budget import ExecutionBudgetError, current_execution_scope
from bestseller.services.critic_evidence_gate import build_critic_body_anchors
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
BODY = "甲把信放在桌上。\n\n乙拿走信封。\n\n甲关上门。\n\n乙离开院子。"
IDS = ("canon_facts:1", "canon_facts:8", "timeline_events:5", "timeline_events:6")


def raw_decisions(body=BODY, ids=IDS):
    anchors = build_critic_body_anchors(body)
    return (
        json.dumps(
            {
                "decisions": [
                    {
                        "item_id": key,
                        "supported": True,
                        "anchor_ids": [anchors[min(index, len(anchors) - 1)]["id"]],
                        "reason": "The selected literal paragraph establishes this action.",
                    }
                    for index, key in enumerate(ids)
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def context(body=BODY):
    pid, wid, cid, did, sid = (uuid4() for _ in range(5))
    pending = {
        key: {"kind": key.split(":")[0], "claim": {"evidence": "retained paraphrase"}}
        for key in IDS
    }
    payload_sha = service.payload_digest({"candidate_payload": pending})
    source = {
        "schema": "adopted-knowledge.v1",
        "project_id": str(pid),
        "chapter_id": str(cid),
        "source_chapter_draft_version_id": str(did),
        "source_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }
    operation = (
        f"knowledge:feedback-evidence:v2:{cid}:{did}:{source['source_body_sha256']}:{payload_sha}"
    )
    request = llm.LLMCompletionRequest(
        logical_role="editor",
        system_prompt="Audit retained claims against source anchors.",
        user_prompt=json.dumps(
            {
                "untrusted_candidate_claims": pending,
                "CURRENT_BODY_ANCHORS": build_critic_body_anchors(body),
            }
        ),
        fallback_response="{}",
        prompt_template="feedback_evidence_reconciliation",
        prompt_version="1.0",
        project_id=pid,
        workflow_run_id=wid,
        step_run_id=sid,
        operation_id=operation,
        metadata={
            "source_lineage": source,
            "source_payload_sha256": payload_sha,
            "fixture_history": ["preserve", {"raw": True}],
        },
    )
    completion = llm.LLMCompletionResult(
        content=raw_decisions(body, IDS[:3]),
        provider="fixture",
        model_name="fixture-editor",
        finish_reason="stop",
        llm_run_id=uuid4(),
        input_tokens=7,
        output_tokens=9,
    )
    return SimpleNamespace(request=request, parent=completion, pending=pending, body=body)


async def invoke(case, session=None, settings=None):
    return await service._resolve_with_coverage_repair(
        session,
        settings or load_settings(env={}),
        request=case.request,
        completion=case.parent,
        pending=case.pending,
        body=case.body,
    )


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("coverage repair tests cannot access network/provider SDK")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "_get_litellm", forbidden)


def controlled_child(monkeypatch, case, *, content=None, **updates):
    calls = []
    child = llm.LLMCompletionResult(
        content=raw_decisions(case.body) if content is None else content,
        provider="fixture",
        model_name="fixture-editor",
        finish_reason="stop",
        llm_run_id=uuid4(),
    ).model_copy(update=updates)

    async def wire(session, settings, request):
        calls.append(request)
        return child

    monkeypatch.setattr(service, "complete_text", wire)
    return calls, child


async def test_valid_parent_takes_original_path_without_call_or_audit(monkeypatch):
    case = context()
    case.parent.content = raw_decisions()
    calls, _ = controlled_child(monkeypatch, case)
    before = deepcopy((case.request.model_dump(), case.parent.model_dump(), case.pending))
    decisions, completion, request, audit = await invoke(case)
    assert set(decisions) == set(IDS)
    assert completion is case.parent and request is case.request and audit is None
    assert calls == [] and before == (
        case.request.model_dump(),
        case.parent.model_dump(),
        case.pending,
    )


async def test_actual_four_vs_three_shape_gets_one_complete_source_bound_child(monkeypatch):
    case = context()
    calls, child = controlled_child(monkeypatch, case)
    before = deepcopy((case.request.model_dump(), case.parent.model_dump(), case.pending))
    decisions, chosen, request, audit = await invoke(case)
    assert set(decisions) == set(IDS) and len(calls) == 1
    assert chosen is child and request is calls[0]
    assert (request.project_id, request.workflow_run_id, request.step_run_id) == (
        case.request.project_id,
        case.request.workflow_run_id,
        case.request.step_run_id,
    )
    expected = (
        "knowledge:feedback-evidence-coverage:v1:"
        + hashlib.sha256(case.request.operation_id.encode()).hexdigest()
    )
    assert request.operation_id == expected
    packet = json.loads(request.user_prompt)
    assert packet["untrusted_previous_reply"] == case.parent.content
    assert packet["omitted_item_ids"] == ["timeline_events:6"]
    assert packet["untrusted_candidate_claims"] == case.pending
    assert packet["CURRENT_BODY_ANCHORS"] == list(build_critic_body_anchors(case.body))
    assert request.metadata["source_lineage"] == case.request.metadata["source_lineage"]
    assert (
        request.metadata["source_payload_sha256"] == case.request.metadata["source_payload_sha256"]
    )
    assert request.metadata["fixture_history"] == case.request.metadata["fixture_history"]
    assert audit["original_raw_response"] == case.parent.content
    assert audit["original_llm_run_id"] == str(case.parent.llm_run_id)
    assert (
        audit["original_response_sha256"]
        == hashlib.sha256(case.parent.content.encode()).hexdigest()
    )
    assert audit["repair_operation_id"] == expected and audit["repair_llm_run_id"] == str(
        child.llm_run_id
    )
    assert audit["repair_response_sha256"] == hashlib.sha256(child.content.encode()).hexdigest()
    assert audit["maximum_supplemental_operations"] == 1
    assert all(
        audit[key] is False
        for key in ("new_review_credit", "grade_asserted", "adoption_authorized")
    )
    assert before == (case.request.model_dump(), case.parent.model_dump(), case.pending)


@pytest.mark.parametrize(
    "fault",
    [
        "blank",
        "empty_subset",
        "duplicate_id",
        "extra_id",
        "bad_anchor",
        "blank_reason",
        "false_authority",
        "unsupported_with_anchors",
        "duplicate_anchors",
        "fallback",
        "unknown_finish",
        "no_run",
        "no_project",
        "no_workflow",
        "wrong_operation",
        "wrong_body",
        "wrong_project",
        "wrong_chapter",
        "wrong_draft",
        "missing_payload",
        "bad_payload",
        "wrong_role",
        "wrong_version",
        "wrong_template",
        "wrong_schema",
        "tools",
        "messages_override",
    ],
)
async def test_bad_parent_never_dispatches_a_child(monkeypatch, fault):
    case = context()
    payload = json.loads(case.parent.content)
    first = payload["decisions"][0]
    if fault == "blank":
        case.parent.content = ""
    elif fault == "empty_subset":
        payload["decisions"] = []
    elif fault == "duplicate_id":
        payload["decisions"].append(deepcopy(first))
    elif fault == "extra_id":
        first["item_id"] = "timeline_events:unknown"
    elif fault == "bad_anchor":
        first["anchor_ids"] = [build_critic_body_anchors("Foreign body.")[0]["id"]]
    elif fault == "blank_reason":
        first["reason"] = " "
    elif fault == "false_authority":
        payload["adoption_authorized"] = True
    elif fault == "unsupported_with_anchors":
        first["supported"] = False
    elif fault == "duplicate_anchors":
        first["anchor_ids"] *= 2
    elif fault == "fallback":
        case.parent.fallback_used = True
    elif fault == "unknown_finish":
        case.parent.finish_reason = "length"
    elif fault == "no_run":
        case.parent.llm_run_id = None
    elif fault == "no_project":
        case.request.project_id = None
    elif fault == "no_workflow":
        case.request.workflow_run_id = None
    elif fault == "wrong_operation":
        case.request.operation_id += ":foreign"
    elif fault in {"wrong_body", "wrong_project", "wrong_chapter", "wrong_draft", "wrong_schema"}:
        key = {
            "wrong_body": "source_body_sha256",
            "wrong_project": "project_id",
            "wrong_chapter": "chapter_id",
            "wrong_draft": "source_chapter_draft_version_id",
            "wrong_schema": "schema",
        }[fault]
        case.request.metadata["source_lineage"][key] = "foreign"
    elif fault == "missing_payload":
        case.request.metadata.pop("source_payload_sha256")
    elif fault == "bad_payload":
        case.request.metadata["source_payload_sha256"] = "bad"
    elif fault == "wrong_role":
        case.request.logical_role = "critic"
    elif fault == "wrong_version":
        case.request.prompt_version = "9.9"
    elif fault == "wrong_template":
        case.request.prompt_template = "chapter_review"
    elif fault == "tools":
        case.request.tools = [{"type": "function", "function": {"name": "foreign"}}]
    elif fault == "messages_override":
        case.request.messages_override = [{"role": "user", "content": "foreign"}]
    if fault != "blank":
        case.parent.content = json.dumps(payload)
    calls, _ = controlled_child(monkeypatch, case)
    before = deepcopy((case.request.model_dump(), case.parent.model_dump(), case.pending))
    with pytest.raises(ValueError):
        await invoke(case)
    assert calls == [] and before == (
        case.request.model_dump(),
        case.parent.model_dump(),
        case.pending,
    )


@pytest.mark.parametrize(
    "fault",
    [
        "subset",
        "empty",
        "extra",
        "duplicate",
        "anchor",
        "authority",
        "fallback",
        "unknown_finish",
        "missing_run",
    ],
)
async def test_bad_child_fails_once_without_recursive_repair(monkeypatch, fault):
    case = context()
    payload = json.loads(raw_decisions())
    updates = {}
    if fault == "subset":
        payload["decisions"].pop()
    elif fault == "empty":
        payload["decisions"] = []
    elif fault == "extra":
        payload["decisions"][0]["item_id"] = "foreign"
    elif fault == "duplicate":
        payload["decisions"].append(deepcopy(payload["decisions"][0]))
    elif fault == "anchor":
        payload["decisions"][0]["anchor_ids"] = ["Dforeign:P0001"]
    elif fault == "authority":
        payload["grade_asserted"] = True
    elif fault == "fallback":
        updates["fallback_used"] = True
    elif fault == "unknown_finish":
        updates["finish_reason"] = "length"
    elif fault == "missing_run":
        updates["llm_run_id"] = None
    calls, _ = controlled_child(monkeypatch, case, content=json.dumps(payload), **updates)
    with pytest.raises(ValueError):
        await invoke(case)
    assert len(calls) == 1


async def test_child_identity_excludes_actor_and_new_workflow_ids(monkeypatch):
    case = context()
    calls, _ = controlled_child(monkeypatch, case)
    await invoke(case)
    first = calls[0].operation_id
    case.request.workflow_run_id = uuid4()
    case.request.metadata["actor"] = "a different fixture actor"
    case.request.metadata["at"] = "a different time"
    await invoke(case)
    assert len(calls) == 2 and calls[1].operation_id == first


@pytest.fixture
async def native_gateway(gateway_ledger, monkeypatch):  # noqa: F811
    settings = load_settings(env={})
    settings.llm.mock = False
    settings.llm.editor.model = "openai/coverage-fixture"
    settings.llm.editor.model_override = None
    settings.llm.editor.api_base = "https://gateway.test.invalid"
    settings.llm.editor.max_tokens = 512
    settings.llm.editor.stream = False
    settings.llm.retry.max_attempts = 1
    settings.llm.retry.rate_limit_fallback_enabled = False
    settings.budget.execution_max_calls = 5
    settings.budget.execution_max_tokens = 10000
    calls = []
    responses = {"child": raw_decisions()}

    async def wire(request, role):
        calls.append(request.model_copy(deep=True))
        await replay_reserved_http_send({"model": role.model, "messages": []})
        content = (
            raw_decisions(ids=IDS[:3])
            if request.prompt_template == "feedback_evidence_reconciliation"
            else responses["child"]
        )
        return content, 7, 9, "stop", None, {"role": "assistant", "content": content}

    monkeypatch.setattr(llm, "_call_litellm", wire)
    monkeypatch.setattr(service, "complete_text", llm.complete_text)
    llm._llm_breaker.record_success()
    factory = async_sessionmaker(gateway_ledger, expire_on_commit=False)
    case = context()
    async with factory() as s:
        case.request.project_id = await s.scalar(
            select(ProjectModel.id).where(ProjectModel.slug == "novel")
        )
        case.request.metadata["source_lineage"]["project_id"] = str(case.request.project_id)
        case.parent = await llm.complete_text(s, settings, case.request)
        # The ledger commits independently; persist its optional telemetry row
        # as fixture history too so the preservation check reads an actual row.
        await s.commit()
    assert len(calls) == 1 and case.parent.llm_run_id is not None
    return SimpleNamespace(
        case=case,
        settings=settings,
        factory=factory,
        calls=calls,
        responses=responses,
        scope=current_execution_scope(),
    )


async def test_repeated_native_gateway_uses_one_paid_child_and_preserves_parent(native_gateway):
    rig = native_gateway
    parent_key = rig.case.parent.metadata["execution_receipt"]["operation_key"]
    async with rig.factory() as s:
        parent = await s.get(ExecutionOperationModel, parent_key)
        before = deepcopy(
            {a.key: getattr(parent, a.key) for a in inspect(ExecutionOperationModel).column_attrs}
        )
        parent_run = await s.get(LlmRunModel, rig.case.parent.llm_run_id)
        assert parent_run is not None
        original_run = deepcopy(
            {a.key: getattr(parent_run, a.key) for a in inspect(LlmRunModel).column_attrs}
        )
        first = await invoke(rig.case, s, rig.settings)
        await s.rollback()
    async with rig.factory() as s:
        second = await invoke(rig.case, s, rig.settings)
    assert first[0] == second[0] and len(rig.calls) == 2
    assert sum(r.prompt_template == "feedback_evidence_coverage_repair" for r in rig.calls) == 1
    async with rig.factory() as s:
        budget = await s.get(ExecutionBudgetModel, rig.scope)
        assert budget.calls_used == 2 and budget.tokens_charged == 32
        assert len(list(await s.scalars(select(ExecutionAttemptModel)))) == 2
        parent = await s.get(ExecutionOperationModel, parent_key)
        assert {
            a.key: getattr(parent, a.key) for a in inspect(ExecutionOperationModel).column_attrs
        } == before
        parent_run = await s.get(LlmRunModel, rig.case.parent.llm_run_id)
        assert {
            a.key: getattr(parent_run, a.key) for a in inspect(LlmRunModel).column_attrs
        } == original_run


async def test_parent_raw_drift_keeps_child_identity_but_real_ledger_denies_new_input(
    native_gateway,
):
    rig = native_gateway
    async with rig.factory() as s:
        original = await invoke(rig.case, s, rig.settings)
    rig.case.parent.content += "\n"
    async with rig.factory() as s:
        with pytest.raises(ExecutionBudgetError, match="input"):
            await invoke(rig.case, s, rig.settings)
    assert len(rig.calls) == 2
    assert (
        original[2].operation_id
        == "knowledge:feedback-evidence-coverage:v1:"
        + hashlib.sha256(rig.case.request.operation_id.encode()).hexdigest()
    )
    async with rig.factory() as s:
        budget = await s.get(ExecutionBudgetModel, rig.scope)
        assert (
            budget.calls_used == 2
            and len(list(await s.scalars(select(ExecutionAttemptModel)))) == 2
        )


async def test_bad_child_is_paid_once_and_replayed_without_recursive_repair(native_gateway):
    rig = native_gateway
    rig.responses["child"] = raw_decisions(ids=IDS[:3])
    for _ in range(2):
        async with rig.factory() as s:
            with pytest.raises(ValueError, match=r"^feedback_evidence_item_coverage$"):
                await invoke(rig.case, s, rig.settings)
    assert len(rig.calls) == 2
    assert [r.prompt_template for r in rig.calls] == [
        "feedback_evidence_reconciliation",
        "feedback_evidence_coverage_repair",
    ]
    async with rig.factory() as s:
        budget = await s.get(ExecutionBudgetModel, rig.scope)
        assert budget.calls_used == 2 and budget.tokens_charged == 32
        assert len(list(await s.scalars(select(ExecutionAttemptModel)))) == 2


async def test_native_application_keeps_domain_operation_and_actual_child_lineage(db, monkeypatch):  # noqa: F811
    s, project, chapter, draft = db
    lineage, rejected = await feedback.validate_adopted_knowledge_source(
        s,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md,
    )
    assert rejected is None
    fact = {
        "subject": "钥匙",
        "predicate": "location",
        "value": "retained claim",
        "evidence": draft.content_md,
    }
    event = {"event_name": "放下钥匙", "evidence": draft.content_md}
    payload = {
        "canon_facts": [deepcopy(fact) for _ in range(9)],
        "timeline_events": [deepcopy(event) for _ in range(7)],
    }
    for index in (1, 8):
        payload["canon_facts"][index]["evidence"] = "retained paraphrase"
    for index in (5, 6):
        payload["timeline_events"][index]["evidence"] = "retained paraphrase"
    receipt = {"status": "ok", "source_lineage": lineage, "candidate_payload": payload}
    chapter.metadata_json = {"knowledge_feedback_receipts": [receipt], "protected": [1, 2]}
    await s.flush()
    calls = []
    parent = llm.LLMCompletionResult(
        content=raw_decisions(draft.content_md, IDS[:3]),
        provider="fixture",
        model_name="fixture-editor",
        finish_reason="stop",
        llm_run_id=uuid4(),
    )
    child = parent.model_copy(
        update={"content": raw_decisions(draft.content_md), "llm_run_id": uuid4()}
    )

    async def wire(session, settings, request):
        calls.append(request)
        return parent if len(calls) == 1 else child

    monkeypatch.setattr(service, "complete_text", wire)
    result = await service.reconcile_feedback_evidence(
        s,
        load_settings(env={}),
        chapter=chapter,
        chapter_md=draft.content_md,
        source_chapter_draft_version_id=draft.id,
        workflow_run_id=uuid4(),
    )
    assert len(calls) == 2 and result["status"] == "applied"
    assert result["source_lineage"]["operation_id"] == calls[0].operation_id
    assert result["source_lineage"]["paid_operation_id"] == calls[1].operation_id
    assert result["source_lineage"]["llm_run_id"] == str(child.llm_run_id)
    assert result["raw_model_response"] == child.content
    assert result["coverage_repair"]["original_raw_response"] == parent.content
    assert chapter.metadata_json["knowledge_feedback_receipts"] == [receipt]
    assert chapter.metadata_json["protected"] == [1, 2]
    assert service.feedback_evidence_complete(chapter, receipt, draft.content_md)
    assert (await s.get(ChapterDraftVersionModel, draft.id)).content_md == draft.content_md


async def test_native_cached_reprojection_keeps_child_paid_origin_after_source_restore(
    db,  # noqa: F811
    monkeypatch,
):
    from bestseller.infra.db.models import CanonFactModel, TimelineEventModel

    s, project, chapter, draft = db
    body_a = draft.content_md
    ids = ("canon_facts:0", "canon_facts:1", "timeline_events:0", "timeline_events:1")
    payload_a = {
        "canon_facts": [
            {"subject": "钥匙", "predicate": "location", "value": "retained", "evidence": "错引"},
            {"subject": "阿青", "predicate": "action", "value": "retained", "evidence": "错引"},
        ],
        "timeline_events": [
            {"event_name": "放下钥匙", "evidence": "错引"},
            {"event_name": "钥匙落桌", "evidence": "错引"},
        ],
    }
    calls = []

    async def wire(session, settings, request):
        calls.append(request.model_copy(deep=True))
        assert not session.in_nested_transaction()
        if request.prompt_template == "chapter_feedback_extraction":
            payload = (
                payload_a
                if draft.content_md == body_a
                else {
                    "canon_facts": [
                        {
                            "subject": "钥匙",
                            "predicate": "location",
                            "value": "柜里",
                            "evidence": draft.content_md,
                        }
                    ]
                }
            )
            raw = json.dumps(payload, ensure_ascii=False)
        elif request.prompt_template == "feedback_evidence_reconciliation":
            raw = raw_decisions(body_a, ids[:3])
        else:
            assert request.prompt_template == "feedback_evidence_coverage_repair"
            raw = raw_decisions(body_a, ids)
        return llm.LLMCompletionResult(
            content=raw,
            provider="fixture",
            model_name="fixture-editor",
            finish_reason="stop",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(feedback, "complete_text", wire)
    monkeypatch.setattr(service, "complete_text", wire)
    settings = load_settings(env={})

    async def extract():
        return await feedback.extract_chapter_feedback(
            s,
            settings,
            project_id=project.id,
            chapter=chapter,
            chapter_md=draft.content_md,
            source_chapter_draft_version_id=draft.id,
            workflow_run_id=uuid4(),
            step_run_id=uuid4(),
        )

    assert (await extract()).extraction_status == "ok"
    first_receipt = deepcopy(chapter.metadata_json["knowledge_feedback_receipts"][-1])
    original_evidence = deepcopy(chapter.metadata_json["knowledge_feedback_evidence_receipts"])
    first = original_evidence[-1]
    assert len(calls) == 3
    assert first["source_lineage"]["operation_id"] == calls[1].operation_id
    assert first["source_lineage"]["paid_operation_id"] == calls[2].operation_id
    original_rows = {}
    for model in (CanonFactModel, TimelineEventModel):
        rows = list(await s.scalars(select(model).where(model.project_id == project.id)))
        assert rows
        original_rows[model] = {
            row.id: deepcopy(row.value_json if model is CanonFactModel else row.metadata_json)
            for row in rows
        }

    # Native A -> B -> A restores retained feedback through a new application.
    # No receipt timestamps, closure flags or cached evidence are hand-edited.
    draft.content_md = "阿青把钥匙放在柜里。"
    await s.flush()
    assert (await extract()).extraction_status == "ok"
    assert len(calls) == 4
    before_restore = deepcopy(chapter.metadata_json)
    draft.content_md = body_a
    await s.flush()
    restored_result = await extract()
    assert restored_result.extraction_status == "ok"
    assert len(calls) == 4, "Cached reprojection must never enter the paid gateway"
    latest_receipt = chapter.metadata_json["knowledge_feedback_receipts"][-1]
    restored = chapter.metadata_json["knowledge_feedback_evidence_receipts"][-1]
    assert service.payload_digest(latest_receipt) == service.payload_digest(first_receipt)
    assert service.feedback_application_digest(
        latest_receipt
    ) != service.feedback_application_digest(first_receipt)
    assert restored["source_feedback_receipt_sha256"] == service.feedback_application_digest(
        latest_receipt
    )
    assert restored["source_body_sha256"] == first["source_body_sha256"]
    assert restored["source_payload_sha256"] == first["source_payload_sha256"]
    assert restored["coverage_repair"] == first["coverage_repair"]
    assert restored["raw_model_response"] == first["raw_model_response"]
    assert (
        chapter.metadata_json["knowledge_feedback_receipts"][:-1]
        == before_restore["knowledge_feedback_receipts"]
    )
    assert chapter.metadata_json["knowledge_feedback_evidence_receipts"][:-1] == original_evidence
    assert service.feedback_evidence_complete(chapter, latest_receipt, body_a)
    # Canonical domain operation and actual paid child must remain distinct.
    assert (
        restored["source_lineage"]["paid_operation_id"]
        == first["source_lineage"]["paid_operation_id"]
    )
    assert restored["source_lineage"] == first["source_lineage"]
    for model, original in original_rows.items():
        for row_id, data in original.items():
            row = await s.get(model, row_id)
            assert row is not None
            actual = row.value_json if model is CanonFactModel else row.metadata_json
            assert {key: actual[key] for key in data if key != "knowledge_lineage_status"} == {
                key: value for key, value in data.items() if key != "knowledge_lineage_status"
            }
        rows = list(await s.scalars(select(model).where(model.project_id == project.id)))
        current = [
            row
            for row in rows
            if (
                row.is_current
                if model is CanonFactModel
                else row.metadata_json.get("knowledge_lineage_status") == "current"
            )
        ]
        assert current
        for row in current:
            data = row.value_json if model is CanonFactModel else row.metadata_json
            assert data["source_lineage"] == restored["source_lineage"]
    assert (await s.get(ChapterDraftVersionModel, draft.id)).content_md == body_a
