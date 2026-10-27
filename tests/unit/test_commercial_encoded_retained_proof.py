"""Encoded paid returns: actual SQLite custody plus physical request retention, no provider."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import inspect, select
from test_commercial_component_cohort import SCOPE, _history, _judge
from test_commercial_component_cohort import cohort as cohort  # noqa: F401
from test_commercial_review_transport_entrypoint import _input

from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel, LlmRunModel
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services import commercial_review_transport as transport
from bestseller.services import llm, model_catalog
from bestseller.services import retained_review_input_proof as proof
from bestseller.services.budget import bind_execution_scope
from bestseller.settings import apply_runtime_llm_profile

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def encoded(cohort, tmp_path, monkeypatch):
    rig = cohort
    rig.settings = apply_runtime_llm_profile(rig.settings)
    rig.settings.llm.critic.api_base = "https://fixture.invalid/v1"
    rig.settings.artifact_store.local_dir = str(tmp_path / "physical")
    rig.settings.artifact_store.retain_prompt_payloads = True
    entry = model_catalog.ModelCatalogEntry(
        id="retained-encoded", display_name="SQLite physical fixture", vendor="fixture",
        model=rig.settings.llm.critic.model, api_base=rig.settings.llm.critic.api_base,
    )
    disk = tmp_path / "fresh_catalog.yaml"
    disk.write_text(json.dumps({"models": [entry.model_dump()]}))
    monkeypatch.setattr(model_catalog, "_CATALOG_PATH", disk)
    rig.project.metadata_json = {"llm_model_id": entry.id}
    await rig.session.commit()
    monkeypatch.setattr(model_catalog, "get_model_catalog_entry", lambda key: entry if key == entry.id else None)
    rig.generation_input = _input(True)
    rig.generation_input.pop("approved_creation_contracts")
    gateway = judge.complete_text  # Existing real SQLite root/attempt/transport fixture.
    assemble = transport.prepare_commercial_review_transport
    captured = []

    def render(*args, **kwargs):
        wire, receipt = assemble(*args, **kwargs)
        captured.append((kwargs["sources"], deepcopy(receipt)))
        return wire, receipt

    async def physical(sess, cfg, request):
        value = await gateway(sess, cfg, request)
        run = await sess.get(LlmRunModel, value.llm_run_id)
        catalog, _ = proof._fresh_catalog()
        role = proof._fresh_role(cfg, rig.project, request, catalog)
        assert llm._effective_input_hash(request, role) == run.prompt_hash
        with llm.bind_project_prompt_audit(rig.project.id, reason="isolated encoded commercial proof"):
            request_ref, response_ref, reason, replayable = llm._persist_prompt_payloads(
                settings=cfg, request=request, llm_run_id=run.id,
                configured_model=role.model, effective_model=role.model, provider=value.provider,
                effective_temperature=role.temperature, effective_max_tokens=role.max_tokens,
                effective_timeout_seconds=role.timeout_seconds, effective_stream=role.stream,
                effective_thinking_type=role.thinking_type, effective_reasoning_effort=role.reasoning_effort,
                content=value.content, tool_call_count=len(value.tool_calls or []),
                tool_calls=value.tool_calls, raw_message=value.raw_message,
                finish_reason=value.finish_reason, fallback_used=False, fallback_source=None,
                input_tokens=value.input_tokens, output_tokens=value.output_tokens,
            )
        assert reason is None and replayable and request_ref and response_ref
        run.request_payload_ref, run.response_payload_ref = request_ref, response_ref
        run.metadata_json = {**run.metadata_json, "effective_input_receipt": {"hash": run.prompt_hash}}
        await sess.commit()
        return value

    monkeypatch.setattr(transport, "prepare_commercial_review_transport", render)
    async def visible_physical(*args):
        try:
            return await physical(*args)
        except Exception as exc:
            pytest.fail(f"physical fixture failure: {type(exc).__name__}: {exc}")

    monkeypatch.setattr(judge, "complete_text", visible_physical)
    _, _, _, report = await _history(rig)
    assert len(rig.calls) == 5, [json.loads(sample["report"]["raw_excerpt"]).get("validation_errors")
        for sample in json.loads(report.structured_output["evidence_summary"]["llm_commercial_judge"]["raw_excerpt"])["samples"]]
    sources, receipt = captured[0]
    assert receipt["encoding"] != "identity" and receipt["capacity"]["budget_passed"]
    aggregate = json.loads(report.structured_output["evidence_summary"]["llm_commercial_judge"]["raw_excerpt"])
    sample = aggregate["samples"][0]
    envelope = json.loads(sample["report"]["raw_excerpt"])
    run = await rig.session.get(LlmRunModel, UUID(sample["report"]["llm_run_id"]))
    path = Path(rig.settings.artifact_store.local_dir) / run.request_payload_ref
    yield SimpleNamespace(rig=rig, sources=sources, receipt=receipt, report=report,
                          sample=sample, envelope=envelope, run=run, path=path, catalog_path=disk)


async def slot(case, *, sources=None, settings=True):
    rig = case.rig
    with bind_execution_scope(SCOPE):
        return await recovery._verified_paid_slot(
            rig.session, report=case.sample["report"], sources=sources or case.sources,
            contract=case.envelope["evaluation_contract"], project_id=rig.project.id,
            scope=SCOPE, slot=case.sample["sample_slot"], expected_model=case.run.model_name,
            settings=rig.settings if settings else None,
        )


async def snapshot(rig):
    rows = {}
    for model in (LlmRunModel, ExecutionOperationModel, ExecutionAttemptModel, ExecutionBudgetModel):
        rows[model.__name__] = [
            {column.key: deepcopy(getattr(row, column.key)) for column in inspect(model).column_attrs}
            for row in await rig.session.scalars(select(model))
        ]
    return rows


async def test_encoded_actual_physical_request_slot_and_whole_cohort_verify_without_new_credit(encoded):
    case, rig = encoded, encoded.rig
    before = await snapshot(rig)
    raw = case.path.read_bytes()
    first, second = await slot(case), await slot(case)
    assert first[0].model_dump() == second[0].model_dump()
    assert first[0].overall_score == .71 and first[0].has_critical
    retained = await _judge(rig, bound=True, commercial_retained_only=True)
    assert not retained.infrastructure_failure and not retained.passed
    catalog, _ = proof._fresh_catalog()
    with bind_execution_scope(SCOPE):
        result = await proof._commercial_proof(
            rig.session, rig.settings, project=rig.project, chapter=rig.chapter, draft=rig.draft,
            report=case.report, catalog=catalog, scope=SCOPE, commercial_sources=case.sources,
        )
    assert len(result["llm_run_ids"]) == 3
    assert len(rig.calls) == 5 and await snapshot(rig) == before
    assert case.path.read_bytes() == raw


@pytest.mark.parametrize("fault", ["settings", "cross_body", "alias", "file", "missing_file", "unreplayable", "endpoint", "paid_root"])
async def test_encoded_paid_proof_rejects_missing_or_changed_physical_source_route_and_custody(encoded, fault):
    case, rig = encoded, encoded.rig
    sources = case.sources
    if fault == "cross_body":
        from bestseller.services.commercial_source_evidence import CommercialSources
        receipt = {**sources.receipt, "body_sha256": hashlib.sha256(b"other body").hexdigest()}
        sources = CommercialSources(text=sources.text, catalog=sources.catalog, receipt=receipt)
    elif fault in {"alias", "file", "unreplayable"}:
        payload = json.loads(case.path.read_text())
        if fault == "alias": payload["tools"] = []  # Conflicts with retained requested.tools.
        elif fault == "unreplayable": payload["replayable"] = False
        else:
            payload["messages"]["user"]["text"] += "tampered"
            payload["messages"]["user"]["original_chars"] += len("tampered")
        case.path.write_text(json.dumps(payload))
    elif fault == "missing_file": case.path.unlink()
    elif fault == "endpoint":
        catalog = json.loads(case.catalog_path.read_text())
        catalog["models"][0]["api_base"] = "https://changed.invalid/v1"
        case.catalog_path.write_text(json.dumps(catalog))
    elif fault == "paid_root":
        key = case.run.metadata_json["execution_receipt"]["operation_key"]
        operation = await rig.session.get(ExecutionOperationModel, key)
        changed = deepcopy(operation.result_json)
        changed["tool_calls"][0]["function"]["arguments"] += " "
        operation.result_json = changed
        await rig.session.commit()
    before = await snapshot(rig)
    with pytest.raises((ValueError, FileNotFoundError)):
        await slot(case, sources=sources, settings=fault != "settings")
    assert await snapshot(rig) == before and len(rig.calls) == 5


async def test_old_literal_paid_slot_does_not_require_new_physical_request_or_settings(cohort):
    rig = cohort
    _, _, _, latest = await _history(rig)
    aggregate = json.loads(latest.structured_output["evidence_summary"]["llm_commercial_judge"]["raw_excerpt"])
    sample = aggregate["samples"][0]
    envelope = json.loads(sample["report"]["raw_excerpt"])
    from test_commercial_judge_recovery import sources
    run = await rig.session.get(LlmRunModel, UUID(sample["report"]["llm_run_id"]))
    assert not run.request_payload_ref and "commercial_source_transport" not in run.metadata_json
    with bind_execution_scope(SCOPE):
        result, _, _ = await recovery._verified_paid_slot(
            rig.session, report=sample["report"], sources=sources(),
            contract=envelope["evaluation_contract"], project_id=rig.project.id, scope=SCOPE,
            slot=sample["sample_slot"], expected_model=run.model_name,
        )
    assert result.overall_score == .71 and len(rig.calls) == 5


@pytest.mark.parametrize("fault", ["current_source", "alias", "file"])
async def test_whole_review_encoded_commercial_proof_rechecks_current_source_and_physical_request(encoded, fault):
    case, rig = encoded, encoded.rig
    retained = await _judge(rig, bound=True, commercial_retained_only=True)
    assert not retained.infrastructure_failure
    sources = case.sources
    if fault == "current_source":
        from bestseller.services.commercial_source_evidence import CommercialSources
        packet = json.loads(sources.text)
        packet["source_context"]["new_current_fact"] = "Another causally established fact."
        text = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sources = CommercialSources(
            text=text, catalog=sources.catalog,
            receipt={**sources.receipt, "assembled_source_sha256": hashlib.sha256(text.encode()).hexdigest()},
        )
    else:
        payload = json.loads(case.path.read_text())
        if fault == "alias": payload["tool_choice"] = "auto"
        else:
            payload["messages"]["user"]["text"] += "changed encoded packet"
            payload["messages"]["user"]["original_chars"] += len("changed encoded packet")
        case.path.write_text(json.dumps(payload))
    before = await snapshot(rig)
    catalog, _ = proof._fresh_catalog()
    with bind_execution_scope(SCOPE), pytest.raises(ValueError):
        await proof._commercial_proof(
            rig.session, rig.settings, project=rig.project, chapter=rig.chapter, draft=rig.draft,
            report=case.report, catalog=catalog, scope=SCOPE, commercial_sources=sources,
        )
    assert await snapshot(rig) == before and len(rig.calls) == 5
