"""Real SQL qualification/readback; physical-return construction is fixture-only."""

from copy import deepcopy
from datetime import datetime, UTC, timedelta
from uuid import UUID, uuid4
from sqlalchemy.schema import CreateTable
import pytest
from test_draft_continuity import sql as sql, BODY, SOURCES, response  # noqa:F401
from bestseller.infra.db import models as m
from bestseller.services import (
    draft_continuity as dc,
    draft_continuity_applicable_review as service,
)
from bestseller.services import draft_continuity_supplement as plan_service
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.single_dispatch_authorization import authorization_id
from bestseller.services.quality_report_claim import graded_text_fingerprint

REAL_VERIFY_PAID = service.verify_paid
pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def case(sql, monkeypatch):
    engine = sql.kw["bind"]
    async with engine.begin() as con:
        for model in (m.PlanningArtifactVersionModel, m.ExecutionBudgetModel):
            await con.exec_driver_sql(
                str(CreateTable(model.__table__).compile(dialect=engine.dialect)).replace(
                    "::jsonb", ""
                )
            )
    parent = deepcopy(SOURCES)
    parent["cache"] = {"not_authored": "runtime only"}

    async def sources(*args, **kwargs):
        return deepcopy(parent)

    monkeypatch.setattr(dc, "current_sources", sources)
    monkeypatch.setattr(dc, "hard_policy_hash", lambda: "a" * 64)
    async with sql() as s:
        project = m.ProjectModel(
            slug="unrelated-sealed-observatory",
            title="观测台",
            genre="科幻",
            target_chapters=3,
            target_word_count=6000,
            metadata_json={"execution_scope_id": "fixture-applicable"},
        )
        s.add(project)
        await s.flush()
        chapter = m.ChapterModel(
            project_id=project.id,
            chapter_number=1,
            chapter_goal="不打开匣子",
            status="revision",
            production_state="blocked",
            metadata_json={"chapter_first_local_repair_count": 2},
        )
        s.add(chapter)
        await s.flush()
        draft = m.ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=1,
            content_md=BODY,
            word_count=37,
            is_current=True,
            promotion_state="candidate",
            assembled_from_scene_draft_ids=[],
        )
        artifact = m.PlanningArtifactVersionModel(
            project_id=project.id,
            artifact_type="chapter_outline_batch",
            version_no=1,
            status="approved",
            schema_version="fixture",
            content={"chapters": [{"chapter_number": 1, "goal": "只观察不开匣"}]},
        )
        s.add_all([draft, artifact])
        await s.flush()
        approved = {
            "artifact_id": str(artifact.id),
            "project_id": str(project.id),
            "chapter_id": str(chapter.id),
            "chapter_number": 1,
        }

        async def current_plan(*args, **kwargs):
            return deepcopy(approved)

        monkeypatch.setattr(plan_service, "applicable_plan", current_plan)
        projected, manifest = service.make_projection(
            parent,
            exclusions=[
                {
                    "path": "/cache",
                    "value_sha256": content_hash(parent["cache"]),
                    "reason": "runtime evidence, not authored constraints",
                }
            ],
            approved_chapter=artifact.content["chapters"][0],
            expected_parent_sha256=content_hash(parent),
        )
        report = response(sources=projected)
        wid = uuid4()
        runid = uuid4()
        prior = uuid4()
        grantid = authorization_id("fixture-human", prior)
        physical = {
            "run_id": str(runid),
            "workflow_id": str(wid),
            "input_hash": "original-paid-input",
            "model": "openai/MiniMax-M3.1-Flash",
            "endpoint": "https://api.minimaxi.com/v1",
            "physical_receipt": "original-operation",
            "actual_attempt_ids": ["one-physical-attempt"],
            "grant_id": grantid,
        }

        async def paid(*args, **kwargs):
            return deepcopy(report), deepcopy(physical), "raw-arguments-hash"

        monkeypatch.setattr(service, "verify_paid", paid)
        tag = {
            "evaluation_id": str(uuid4()),
            "draft_id": str(draft.id),
            "body_sha256": dc.body_hash(BODY),
            "source_sha256": content_hash(parent),
        }
        qr = m.ChapterQualityReportModel(
            chapter_id=chapter.id,
            blocks_write=False,
            regen_attempts=0,
            report_json={
                "graded": graded_text_fingerprint(BODY),
                "draft_continuity_hard_safety": tag,
                "blocking_codes": [],
            },
        )
        s.add(qr)
        await s.flush()
        result = {
            "schema": service.POLICY,
            "policy_source_sha256": service.policy_hash(),
            "project_id": str(project.id),
            "chapter_id": str(chapter.id),
            "draft_id": str(draft.id),
            "body_sha256": dc.body_hash(BODY),
            "source_sha256": content_hash(parent),
            "projected_source_sha256": content_hash(projected),
            "manifest": manifest,
            "approved_current_task": approved,
            "report": report,
            "evidence": physical,
            "arguments_sha256": "raw-arguments-hash",
            "quality_report_id": str(qr.id),
            "deterministic_outcome": "ok",
            "deterministic_binding": tag,
            "hard_policy_sha256": "a" * 64,
            "duplicate_findings": [],
            "hard_safety_status": "verified",
            "excluded_sources_assessed": False,
            "qualification_granted": False,
            "formal_adoption": False,
        }
        workflow = m.WorkflowRunModel(
            id=wid,
            project_id=project.id,
            workflow_type="applicable_source_hard_safety",
            scope_type="chapter",
            scope_id=chapter.id,
            status="completed",
            current_step="verified",
            metadata_json={
                "result": result,
                "manifest": manifest,
                "policy_sha256": service.policy_hash(),
                "body_sha256": dc.body_hash(BODY),
                "source_sha256": content_hash(parent),
                "max_physical_sends": 1,
            },
        )
        binding = {
            "scope_id": "fixture-applicable",
            "workflow_id": str(wid),
            "operation_key": "original-operation",
            "input_hash": physical["input_hash"],
            "model": physical["model"],
            "endpoint": physical["endpoint"],
            "template": "draft_continuity_hard_safety",
            "version_prefix": service.VERSION,
            "dispatch_scope": "workflow",
            "user_message_id": "fixture-human",
            "prior_workflow_id": str(prior),
            "authority_evidence": {
                "body_sha256": result["body_sha256"],
                "parent_source_sha256": result["source_sha256"],
                "applicability_sha256": content_hash(manifest),
            },
        }
        budget = m.ExecutionBudgetModel(
            scope_id="fixture-applicable",
            max_calls=1,
            max_tokens=100000,
            calls_used=1,
            tokens_charged=123,
            deadline_at=datetime.now(UTC) + timedelta(hours=1),
            status="active",
            metadata_json={
                "single_dispatch_grants": {
                    grantid: {
                        "binding": binding,
                        "status": "spent",
                        "physical_attempt_cap": 1,
                        "used_attempt_id": "one-physical-attempt",
                    }
                }
            },
        )
        s.add_all([workflow, budget])
        await s.commit()
        yield sql, project.id, chapter.id, draft.id, wid, parent, approved


async def test_complete_paid_report_needs_explicit_qualification_and_fresh_normal_selector(case):
    factory, pid, cid, did, wid, _, _ = case
    async with factory() as s:
        p = await s.get(m.ProjectModel, pid)
        c = await s.get(m.ChapterModel, cid)
        d = await s.get(m.ChapterDraftVersionModel, did)
        original = deepcopy((await s.get(m.WorkflowRunModel, wid)).metadata_json)
        assert await dc.select_reference(s, None, project=p, chapter=c) is None
        with pytest.raises(ValueError, match="explicit_authority"):
            await service.qualify_reference(
                s,
                None,
                project=p,
                chapter=c,
                draft=d,
                workflow_id=wid,
                actor="operator",
                authorization_ref="fixture-human",
            )
        receipt = await service.qualify_reference(
            s,
            None,
            project=p,
            chapter=c,
            draft=d,
            workflow_id=wid,
            actor="operator",
            authorization_ref="fixture-human",
            explicit_user_approval=True,
        )
        await s.commit()
    async with factory() as s:
        p = await s.get(m.ProjectModel, pid)
        c = await s.get(m.ChapterModel, cid)
        d = await s.get(m.ChapterDraftVersionModel, did)
        selected = await dc.select_reference(s, None, project=p, chapter=c)
        assert (
            selected[0].id == did
            and selected[1]["receipt_id"] == receipt["receipt_id"]
            and selected[1]["formal_adoption"] is False
        )
        assert (
            d.promotion_state == "candidate"
            and c.production_state == "blocked"
            and c.metadata_json["chapter_first_local_repair_count"] == 2
        )
        assert (await s.get(m.WorkflowRunModel, wid)).metadata_json == original
        same = await service.qualify_reference(
            s,
            None,
            project=p,
            chapter=c,
            draft=d,
            workflow_id=wid,
            actor="operator",
            authorization_ref="fixture-human",
            explicit_user_approval=True,
        )
        assert same == receipt


@pytest.mark.parametrize(
    "bad", ["body", "parent", "task", "raw", "unknown", "manifest", "grant", "quality"]
)
async def test_changed_or_unknown_evidence_never_installs_reference(case, bad):
    factory, pid, cid, did, wid, parent, approved = case
    async with factory() as s:
        p = await s.get(m.ProjectModel, pid)
        c = await s.get(m.ChapterModel, cid)
        d = await s.get(m.ChapterDraftVersionModel, did)
        w = await s.get(m.WorkflowRunModel, wid)
        meta = deepcopy(w.metadata_json)
        if bad == "body":
            d.content_md += "正文已变"
        if bad == "parent":
            parent["new_fact"] = "新的硬来源"
        if bad == "task":
            approved["revision"] = "new"
        if bad == "raw":
            meta["result"]["arguments_sha256"] = "other"
        if bad == "unknown":
            meta["result"]["hard_safety_status"] = "unknown_or_blocked"
        if bad == "manifest":
            meta["result"]["manifest"]["exclusions"][0]["reason"] = "different scope"
        if bad == "grant":
            budget = await s.get(m.ExecutionBudgetModel, "fixture-applicable")
            data = deepcopy(budget.metadata_json)
            next(iter(data["single_dispatch_grants"].values()))["status"] = "available"
            budget.metadata_json = data
        if bad == "quality":
            meta["result"]["deterministic_binding"]["evaluation_id"] = "other"
        w.metadata_json = meta
        await s.commit()
        with pytest.raises(ValueError):
            await service.qualify_reference(
                s,
                None,
                project=p,
                chapter=c,
                draft=d,
                workflow_id=wid,
                actor="operator",
                authorization_ref="fixture-human",
                explicit_user_approval=True,
            )
        assert c.metadata_json.get(dc.POINTER) is None and d.promotion_state == "candidate"


@pytest.mark.parametrize("bad", [None, "operation", "duplicate_grant", "unspent"])
async def test_actual_retained_request_shape_through_real_verify_and_qualification(
    case, tmp_path, monkeypatch, bad
):
    import json, hashlib
    from bestseller.services import llm, model_catalog, retained_review_input_proof as retained
    from bestseller.settings import load_settings

    factory, pid, cid, did, wid, parent, _ = case
    settings = load_settings(env={})
    settings.llm.mock = False
    settings.artifact_store.local_dir = tmp_path
    settings.artifact_store.retain_prompt_payloads = True
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "id": "selected",
                        "display_name": "fixture",
                        "vendor": "fixture",
                        "model": "openai/MiniMax-M3.1-Flash",
                        "api_base": "https://api.minimaxi.com/v1",
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(model_catalog, "_CATALOG_PATH", catalog)
    monkeypatch.setattr(model_catalog, "_resolve_availability", lambda entry: (True, None))
    monkeypatch.setattr(service, "verify_paid", REAL_VERIFY_PAID)
    async with factory() as s:
        p = await s.get(m.ProjectModel, pid)
        p.metadata_json = {**p.metadata_json, "llm_model_id": "selected"}
        c = await s.get(m.ChapterModel, cid)
        d = await s.get(m.ChapterDraftVersionModel, did)
        w = await s.get(m.WorkflowRunModel, wid)
        data = deepcopy(w.metadata_json)
        result = data["result"]
        manifest = result["manifest"]
        artifact = await s.get(
            m.PlanningArtifactVersionModel, UUID(result["approved_current_task"]["artifact_id"])
        )
        projected, _ = service.make_projection(
            parent,
            exclusions=manifest["exclusions"],
            approved_chapter=artifact.content["chapters"][0],
            expected_parent_sha256=content_hash(parent),
        )
        request = service.request_for(
            project=p, workflow_id=wid, draft=d, sources=projected, manifest=manifest
        )
        entries, _ = retained._fresh_catalog()
        role = retained._fresh_role(settings, p, request, entries)
        digest = llm._effective_input_hash(request, role)
        request.operation_id += ":" + digest
        request.metadata = {"single_dispatch_authorization_id": result["evidence"]["grant_id"]}
        arguments = json.dumps(result["report"], ensure_ascii=False)
        calls = [
            {
                "id": "actual-fixture-native-tool",
                "type": "function",
                "function": {"name": dc.SUBMIT, "arguments": arguments},
            }
        ]
        runid = UUID(result["evidence"]["run_id"])
        with llm.bind_project_prompt_audit(
            pid, reason="Offline actual retained request shape; no provider"
        ):
            request_ref, _, error, replayable = llm._persist_prompt_payloads(
                settings=settings,
                request=request,
                llm_run_id=runid,
                configured_model=role.model,
                effective_model=role.model,
                provider="minimax",
                effective_temperature=role.temperature,
                effective_max_tokens=6500,
                effective_timeout_seconds=120,
                effective_stream=False,
                effective_thinking_type=None,
                effective_reasoning_effort="max",
                content="",
                tool_call_count=1,
                finish_reason="tool_calls",
                fallback_used=False,
                fallback_source=None,
                input_tokens=10,
                output_tokens=20,
                tool_calls=calls,
                raw_message={"role": "assistant", "content": None, "tool_calls": calls},
            )
        assert error is None and replayable and request_ref
        run = m.LlmRunModel(
            id=runid,
            project_id=pid,
            workflow_run_id=wid,
            logical_role="critic",
            provider="minimax",
            model_name=role.model,
            prompt_template=request.prompt_template,
            prompt_version=request.prompt_version,
            prompt_hash=digest,
            request_payload_ref=request_ref,
            finish_reason="tool_calls",
            metadata_json={
                "effective_input_receipt": {
                    "schema": "effective-input.v1",
                    "model": role.model,
                    "hash": digest,
                }
            },
        )
        s.add(run)
        await s.flush()
        reconstructed = retained._retained_request(settings, run)
        assert reconstructed.operation_id is None and reconstructed.metadata == {}
        operation = hashlib.sha256(
            ("fixture-applicable\n" + request.operation_id).encode()
        ).hexdigest()
        physical = {**result["evidence"], "input_hash": digest, "physical_receipt": operation}
        data["result"]["evidence"] = physical
        data["result"]["arguments_sha256"] = dc.body_hash(arguments)
        w.metadata_json = data
        budget = await s.get(m.ExecutionBudgetModel, "fixture-applicable")
        ledger = deepcopy(budget.metadata_json)
        grant = ledger["single_dispatch_grants"][physical["grant_id"]]
        grant["binding"].update(input_hash=digest, operation_key=operation)
        if bad == "duplicate_grant":
            ledger["single_dispatch_grants"]["duplicate"] = deepcopy(grant)
        if bad == "unspent":
            grant["status"] = "available"
        budget.metadata_json = ledger
        paid = llm.LLMCompletionResult(
            content="",
            provider="minimax",
            model_name=role.model,
            llm_run_id=runid,
            input_tokens=10,
            output_tokens=20,
            finish_reason="tool_calls",
            tool_calls=calls,
            raw_message={"role": "assistant", "content": None, "tool_calls": calls},
            metadata={
                "execution_receipt": {
                    "attempts": [{"attempt_id": "one-physical-attempt", "status": "completed"}]
                }
            },
        )

        async def physical_return(session, actual, *, scope, expected_hash, expected_model):
            assert (
                scope == "fixture-applicable"
                and expected_hash == digest
                and expected_model == role.model
                and actual.id == runid
            )
            return paid, ("wrong-operation" if bad == "operation" else operation)

        monkeypatch.setattr(retained, "_physical_return", physical_return)
        await s.commit()
        if bad:
            with pytest.raises(ValueError):
                await service.qualify_reference(
                    s,
                    settings,
                    project=p,
                    chapter=c,
                    draft=d,
                    workflow_id=wid,
                    actor="operator",
                    authorization_ref="fixture-human",
                    explicit_user_approval=True,
                )
            assert c.metadata_json.get(dc.POINTER) is None
        else:
            receipt = await service.qualify_reference(
                s,
                settings,
                project=p,
                chapter=c,
                draft=d,
                workflow_id=wid,
                actor="operator",
                authorization_ref="fixture-human",
                explicit_user_approval=True,
            )
            await s.commit()
    async with factory() as reopened:
        p = await reopened.get(m.ProjectModel, pid)
        c = await reopened.get(m.ChapterModel, cid)
        if not bad:
            selected = await dc.select_reference(reopened, settings, project=p, chapter=c)
            assert (
                selected[1]["receipt_id"] == receipt["receipt_id"]
                and selected[0].promotion_state == "candidate"
            )
