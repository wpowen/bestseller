"""Actual SQLite consumption and prepare branch; synthetic retained returns only."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from sqlalchemy import select
from test_commercial_component_cohort import SCOPE, _judge, _new_workflow, cohort  # noqa: F401

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionOperationModel,
    LlmRunModel,
)
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services import commercial_protocol_successor_runtime as runtime
from bestseller.services import commercial_source_evidence as source_module
from bestseller.services import llm
from bestseller.services.budget import bind_execution_scope
from bestseller.services.commercial_protocol_successor import _hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class Captured(BaseException):
    pass


@pytest.fixture
async def successor_rig(cohort, tmp_path, monkeypatch):  # noqa: F811
    rig = cohort
    rig.settings.llm.critic.api_base = "https://fixture.invalid/v1"
    rig.settings.artifact_store.local_dir = str(tmp_path / "artifacts")
    rig.settings.artifact_store.retain_prompt_payloads = True
    for key in rig.outcomes:
        rig.outcomes[key] = [True]
    monkeypatch.setattr(source_module, "PROMPT_VERSION", "v11-structured-review-tool")
    await _judge(rig, bound=True)
    await rig.session.refresh(rig.chapter)
    pointer_key, pointer = next(iter(rig.chapter.metadata_json[runtime.RUNTIME_KEY].items()))
    old_journal = deepcopy(rig.owner.metadata_json[runtime.RUNTIME_KEY][pointer_key])
    effective = await judge._commercial_effective_critic_configuration(
        rig.session, rig.settings, rig.project.id
    )
    runs = list(await rig.session.scalars(select(LlmRunModel)))
    for run, request in zip(runs, rig.calls, strict=True):
        # Complete synthetic canonical metadata missing from the older fixture.
        run.metadata_json = {
            **run.metadata_json,
            "effective_input_receipt": {
                "schema": "effective-input.v1",
                "hash": run.prompt_hash,
                "model": run.model_name,
            },
        }
        operation = await rig.session.get(
            ExecutionOperationModel, run.metadata_json["execution_receipt"]["operation_key"]
        )
        paid = operation.result_json
        with llm.bind_project_prompt_audit(rig.project.id, reason="SQLite physical fixture"):
            request_ref, response_ref, error, replayable = llm._persist_prompt_payloads(
                settings=rig.settings,
                request=request,
                llm_run_id=run.id,
                configured_model=run.model_name,
                effective_model=run.model_name,
                provider=run.provider,
                effective_temperature=rig.settings.llm.critic.temperature,
                effective_max_tokens=8192,
                effective_timeout_seconds=90,
                effective_stream=False,
                effective_thinking_type=None,
                effective_reasoning_effort=None,
                content=paid["content"],
                tool_call_count=len(paid["tool_calls"]),
                finish_reason=paid["finish_reason"],
                fallback_used=False,
                fallback_source=None,
                input_tokens=paid["input_tokens"],
                output_tokens=paid["output_tokens"],
                tool_calls=paid["tool_calls"],
                raw_message=paid["raw_message"],
            )
        assert error is None and replayable
        run.request_payload_ref, run.response_payload_ref = request_ref, response_ref
    rig.budget.metadata_json = {
        "provider_authorization": {
            "mode": "completion_bound",
            "authorization_ref": "fixture-explicit-authorization",
            "objective": "Bounded same-source protocol repair",
            "status": "active",
            "models": [effective["model"]],
            "endpoint_hosts": [urlparse(effective["api_base"]).hostname],
        }
    }
    await rig.session.commit()
    from bestseller.settings import reset_settings_cache

    monkeypatch.setenv(
        "BESTSELLER__ARTIFACT_STORE__LOCAL_DIR", rig.settings.artifact_store.local_dir
    )
    reset_settings_cache()
    monkeypatch.setattr(source_module, "PROMPT_VERSION", "v12-source-authority")
    monkeypatch.setattr(runtime, "PROMPT_VERSION", "v12-source-authority")
    old_render = judge._render_chapter_judge_system_prompt
    monkeypatch.setattr(
        judge,
        "_render_chapter_judge_system_prompt",
        lambda **kw: old_render(**kw) + "\nNew causal progress is permitted.",
    )
    new_owner = await _new_workflow(rig)
    new_owner.metadata_json = {"execution_scope_id": SCOPE}
    await rig.session.commit()
    captured = {}

    async def capture(_session, **kwargs):
        captured.update(kwargs)
        raise Captured()

    with monkeypatch.context() as local:
        local.setattr(recovery, "prepare_commercial_component_slot", capture)
        with bind_execution_scope(SCOPE), pytest.raises(Captured):
            await judge.judge_chapter_commercial_quality(
                rig.session,
                rig.settings,
                chapter_number=2,
                content_md=rig.draft.content_md,
                project_id=rig.project.id,
                chapter_id=rig.chapter.id,
                draft_id=rig.draft.id,
                workflow_run_id=new_owner.id,
                sample_slot="chapter-quality.independent-v2:0",
                commercial_cohort_samples=3,
            )
    binding = {
        "project_id": str(rig.project.id),
        "chapter_id": str(rig.chapter.id),
        "draft_id": str(rig.draft.id),
        "source_binding": {
            key: captured["sources"].receipt[key]
            for key in old_journal["binding"]["source_binding"]
        },
        "evaluation_contract": captured["evaluation_contract"],
        "samples": 3,
        "scope_id": SCOPE,
        "effective_critic_sha256": _hash(captured["effective_critic"]),
    }
    args = {
        "chapter": rig.chapter,
        "old_owner": rig.owner,
        "key": pointer_key,
        "pointer": deepcopy(pointer),
        "old_journal": old_journal,
        "binding": binding,
        "sources": captured["sources"],
        "current_input_hash": captured["current_input_hash"],
        "effective_critic": captured["effective_critic"],
    }
    operator = {
        **args,
        "actor": "fixture-operator",
        "reason": "Explicit v12 successor",
        "source_manifest": runtime.successor_source_manifest(),
    }
    return SimpleNamespace(
        rig=rig,
        args=args,
        operator=operator,
        new_owner=new_owner,
        captured=captured,
        old_metadata=deepcopy(rig.owner.metadata_json),
        old_calls=len(rig.calls),
    )


async def persist(rig):
    with bind_execution_scope(SCOPE):
        row = await runtime.persist_successor_authorization(rig.rig.session, **rig.operator)
    await rig.rig.session.commit()
    return row


async def test_consumes_once_repeats_same_owner_and_preserves_old_history(successor_rig):
    case = successor_rig
    auth = await persist(case)
    before_budget = (case.rig.budget.calls_used, case.rig.budget.tokens_charged)
    with bind_execution_scope(SCOPE):
        owner, journals, journal = await runtime.consume_authorized_successor(
            case.rig.session, **case.args, workflow_run_id=case.new_owner.id
        )
        await case.rig.session.commit()
        repeated = await runtime.consume_authorized_successor(
            case.rig.session, **case.args, workflow_run_id=case.new_owner.id
        )
    assert repeated[0].id == owner.id == case.new_owner.id
    assert journals[case.args["key"]] == journal and journal["slots"] == {}
    assert auth.metadata_json["consumed_owner_id"] == str(owner.id)
    await case.rig.session.refresh(case.rig.owner)
    assert case.rig.owner.metadata_json == case.old_metadata
    assert before_budget == (case.rig.budget.calls_used, case.rig.budget.tokens_charged)
    assert len(case.rig.calls) == case.old_calls


async def test_normal_prepare_branch_consumes_existing_receipt_only(successor_rig):
    case = successor_rig
    await persist(case)
    with bind_execution_scope(SCOPE):
        result, call = await recovery.prepare_commercial_component_slot(
            case.rig.session,
            project_id=case.rig.project.id,
            chapter_id=case.rig.chapter.id,
            draft_id=case.rig.draft.id,
            workflow_run_id=case.new_owner.id,
            content_md=case.rig.draft.content_md,
            sources=case.args["sources"],
            evaluation_contract=case.args["binding"]["evaluation_contract"],
            sample_slot="chapter-quality.independent-v2:0",
            samples=3,
            retry_context=None,
            effective_critic=case.args["effective_critic"],
            current_input_hash=case.args["current_input_hash"],
        )
    assert result is None and str(call["owner_id"]) == str(case.new_owner.id)
    assert _hash(case.args["binding"]["evaluation_contract"]) in call["namespace"]
    assert len(case.rig.calls) == case.old_calls
    await case.rig.session.refresh(case.rig.owner)
    assert case.rig.owner.metadata_json == case.old_metadata


async def test_without_persisted_receipt_no_automatic_authorization(successor_rig):
    case = successor_rig
    with bind_execution_scope(SCOPE):
        assert (
            await runtime.consume_authorized_successor(
                case.rig.session, **case.args, workflow_run_id=case.new_owner.id
            )
            is None
        )
    assert (
        case.rig.chapter.metadata_json[runtime.RUNTIME_KEY][case.args["key"]]
        == case.args["pointer"]
    )


@pytest.mark.parametrize(
    "problem", ["noauth", "actual_unknown", "root_pending", "old_changed", "physical_changed"]
)
async def test_changed_or_unsettled_preimages_fail_before_pointer_mutation(successor_rig, problem):
    case = successor_rig
    await persist(case)
    if problem == "noauth":
        case.rig.budget.metadata_json = {}
    elif problem == "actual_unknown":
        actual = await case.rig.session.scalar(select(ExecutionAttemptModel))
        actual.status = "unknown"
    elif problem == "root_pending":
        operation = await case.rig.session.scalar(
            select(ExecutionOperationModel).where(ExecutionOperationModel.status == "completed")
        )
        operation.status = "pending"
    elif problem == "old_changed":
        meta = deepcopy(case.rig.owner.metadata_json)
        meta[runtime.RUNTIME_KEY][case.args["key"]]["audit_change"] = "changed"
        case.rig.owner.metadata_json = meta
    else:
        run = await case.rig.session.scalar(select(LlmRunModel))
        Path(case.rig.settings.artifact_store.local_dir, run.response_payload_ref).write_text("{}")
    await case.rig.session.commit()
    with bind_execution_scope(SCOPE), pytest.raises(ValueError):
        await runtime.consume_authorized_successor(
            case.rig.session, **case.args, workflow_run_id=case.new_owner.id
        )
    await case.rig.session.refresh(case.rig.chapter)
    assert (
        case.rig.chapter.metadata_json[runtime.RUNTIME_KEY][case.args["key"]]
        == case.args["pointer"]
    )


async def test_third_owner_cannot_refresh_consumed_authorization(successor_rig):
    case = successor_rig
    await persist(case)
    with bind_execution_scope(SCOPE):
        await runtime.consume_authorized_successor(
            case.rig.session, **case.args, workflow_run_id=case.new_owner.id
        )
    await case.rig.session.commit()
    third = await _new_workflow(case.rig)
    with bind_execution_scope(SCOPE), pytest.raises(ValueError, match="already_consumed"):
        await runtime.consume_authorized_successor(
            case.rig.session, **case.args, workflow_run_id=third.id
        )


async def test_source_diagnostic_proves_whole_physical_inputs_without_writes(successor_rig):
    case = successor_rig
    before = deepcopy(case.rig.chapter.metadata_json)
    with bind_execution_scope(SCOPE):
        diagnostic = await runtime.diagnose_successor_source_compatibility(
            case.rig.session,
            old_journal=case.args["old_journal"],
            sources=case.args["sources"],
            effective_critic=case.args["effective_critic"],
        )
    assert diagnostic["status"] == "diagnostic_only"
    assert diagnostic["coverage_complete"] is True
    assert len(diagnostic["slot_rounds"]) == 3
    for row in diagnostic["slot_rounds"]:
        assert row["original_request_verified"] is True
        assert row["old_source_payload"] == row["current_source_payload"]
        assert row["comparison_receipt"]["differences"] == []
        assert row["comparison_receipt"]["compatibility_authorized"] is False
        assert all(item["equal"] for item in row["receipt_field_comparison"])
    assert case.rig.chapter.metadata_json == before
    assert case.rig.owner.metadata_json == case.old_metadata
    assert not case.rig.session.new and not case.rig.session.dirty


def _audit_payload_pair():
    old_selection = {
        "policy": "typed-world-and-relevant-current-state.v1",
        "original_source_sha256": "a" * 64,
        "original_bible_sha256": "b" * 64,
        "retained_bible_sha256": "c" * 64,
        "original_bible_characters": 3,
        "retained_bible_characters": 3,
        "retained_bible_fields": ["setting"],
        "omitted_bible_fields": [],
        "authority": "unverified_context; adopted facts and explicit clauses remain separate",
    }
    old = {
        "source_context": {
            "source_selection_receipt": old_selection,
            "explicit_creator_requirements": {"rule": "Keep the coastline."},
        },
        "current_body": "The pilot reads a new signal.",
        "citation_catalog": [{"id": "local:P0001", "kind": "current"}],
        "previous_chapters": [{"body": "The light was unlit."}],
    }
    current = deepcopy(old)
    current["source_context"]["source_selection_receipt"]["original_source_sha256"] = "d" * 64
    receipt = {
        "body_sha256": "e" * 64,
        "semantic_source_sha256": "f" * 64,
        "previous_sha256": "9" * 64,
        "source_selection": deepcopy(old_selection),
    }
    current_receipt = deepcopy(receipt)
    current_receipt["source_selection"] = deepcopy(
        current["source_context"]["source_selection_receipt"]
    )
    return old, current, receipt, current_receipt


async def test_exact_known_audit_leaf_diagnostic_is_not_an_admission_or_old_grade():
    old, current, receipt, current_receipt = _audit_payload_pair()
    original = deepcopy((old, current, receipt, current_receipt))
    comparison = runtime._source_comparison_diagnostic(old, current, receipt, current_receipt)
    assert comparison["known_audit_leaf_only"] is True
    assert comparison["normalized_full_payload_equal"] is True
    assert comparison["compatibility_authorized"] is False
    assert comparison["grade_reused"] is False
    assert comparison["differences"][0]["pointer"] == (
        "/source_context/source_selection_receipt/original_source_sha256"
    )
    assert (old, current, receipt, current_receipt) == original


@pytest.mark.parametrize(
    "path", ["current_body", "citation_catalog", "previous_chapters", "creator", "policy"]
)
async def test_any_additional_source_or_authority_difference_is_not_audit_leaf_only(path):
    old, current, receipt, current_receipt = _audit_payload_pair()
    if path == "creator":
        current["source_context"]["explicit_creator_requirements"]["rule"] = "Changed requirement."
    elif path == "policy":
        current["source_context"]["source_selection_receipt"]["policy"] = "unknown-policy"
    else:
        current[path] = "Changed full source."
    result = runtime._source_comparison_diagnostic(old, current, receipt, current_receipt)
    assert result["known_audit_leaf_only"] is False
    assert result["compatibility_authorized"] is False


async def test_paid_source_extraction_requires_unique_exact_whole_payload_hash():
    raw = json.dumps(
        {
            "schema_version": "commercial-source-evidence.v3",
            "source_context": {},
            "current_body": "The pilot opens the hatch.",
            "previous_chapters": [],
            "citation_catalog": [],
        },
        ensure_ascii=False,
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()
    assert runtime._commercial_payload_from_request("header\n" + raw, digest)[0] == raw
    for prompt, expected in ((raw + "\n" + raw, digest), (raw, "a" * 64), (raw[:-1], digest)):
        with pytest.raises(ValueError, match="unique_paid_source_payload"):
            runtime._commercial_payload_from_request(prompt, expected)


def _current_audit_leaf_change(case):
    """A new current render; never modify retained runs, requests or old receipts."""
    original = case.args["sources"]
    payload = json.loads(original.text)
    selected = payload["source_context"]["source_selection_receipt"]
    selected["original_source_sha256"] = hashlib.sha256(b"unrelated runtime audit").hexdigest()
    receipt = deepcopy(original.receipt)
    receipt["source_selection"] = deepcopy(selected)
    receipt["source_sha256"] = source_module._hash(source_module._json(payload["source_context"]))
    text = source_module._json(payload)
    receipt["assembled_source_sha256"] = source_module._hash(text)
    receipt["assembled_characters"] = len(text)
    changed = source_module.CommercialSources(text, deepcopy(original.catalog), receipt)
    case.args["sources"] = case.operator["sources"] = changed
    return changed


async def test_audit_leaf_successor_uses_all_old_physical_sources_and_persists_proof(successor_rig):
    case = successor_rig
    changed = _current_audit_leaf_change(case)
    old_rows = [runtime._row(r) for r in await case.rig.session.scalars(select(LlmRunModel))]
    old_files = {
        ref: Path(case.rig.settings.artifact_store.local_dir, ref).read_bytes()
        for row in old_rows
        for ref in (row["request_payload_ref"], row["response_payload_ref"])
    }
    before_budget = (case.rig.budget.calls_used, case.rig.budget.tokens_charged)
    auth = await persist(case)
    proofs = deepcopy(auth.metadata_json["source_compatibility_proofs"])
    assert len(proofs) == 3
    for proof in proofs:
        assert proof["schema"] == "commercial-successor-audit-leaf-compatibility.v1"
        assert proof["compatibility_authorized"] is True
        assert proof["normalized_full_payload_equal"] is True
        assert proof["normalized_old_payload_sha256"] == proof["normalized_current_payload_sha256"]
        assert (
            proof["current_assembled_source_sha256"] == changed.receipt["assembled_source_sha256"]
        )
        assert proof["old_assembled_source_sha256"] != proof["current_assembled_source_sha256"]
        assert proof["differences"] == [
            {
                "pointer": "/source_context/source_selection_receipt/original_source_sha256",
                "old_present": True,
                "current_present": True,
                "old_value": proof["old_source_policy"]["source_selection"][
                    "original_source_sha256"
                ],
                "current_value": changed.receipt["source_selection"]["original_source_sha256"],
            }
        ]
        assert (
            proof["old_protocol_manifest"]["evaluation_contract"]
            == case.args["old_journal"]["binding"]["evaluation_contract"]
        )
        assert proof["current_protocol_manifest"] == runtime.successor_source_manifest()
        assert proof["grade_reused"] is proof["new_grade_created"] is proof["slot_reset"] is False
    with bind_execution_scope(SCOPE):
        # Actual normal prepare branch, before any provider call.
        result, call = await recovery.prepare_commercial_component_slot(
            case.rig.session,
            project_id=case.rig.project.id,
            chapter_id=case.rig.chapter.id,
            draft_id=case.rig.draft.id,
            workflow_run_id=case.new_owner.id,
            content_md=case.rig.draft.content_md,
            sources=changed,
            evaluation_contract=case.args["binding"]["evaluation_contract"],
            sample_slot="chapter-quality.independent-v2:0",
            samples=3,
            retry_context=None,
            effective_critic=case.args["effective_critic"],
            current_input_hash=case.args["current_input_hash"],
        )
    assert result is None and str(call["owner_id"]) == str(case.new_owner.id)
    assert len(case.rig.calls) == case.old_calls
    assert before_budget == (case.rig.budget.calls_used, case.rig.budget.tokens_charged)
    assert old_rows == [
        runtime._row(r) for r in await case.rig.session.scalars(select(LlmRunModel))
    ]
    assert all(
        Path(case.rig.settings.artifact_store.local_dir, ref).read_bytes() == data
        for ref, data in old_files.items()
    )
    await case.rig.session.refresh(case.rig.owner)
    assert case.rig.owner.metadata_json == case.old_metadata
    assert auth.metadata_json["source_compatibility_proofs"] == proofs


@pytest.mark.parametrize(
    "problem", ["body", "prior", "authority", "catalog", "catalog_text", "request_hash"]
)
async def test_audit_leaf_cannot_hide_other_changes_or_unverified_physical_input(
    successor_rig, problem
):
    case = successor_rig
    changed = _current_audit_leaf_change(case)
    payload, catalog = json.loads(changed.text), deepcopy(changed.catalog)
    if problem == "body":
        payload["current_body"] += " A new signal."
    elif problem == "prior":
        payload["previous_chapters"].append({"body": "Invented prior event."})
    elif problem == "authority":
        payload["source_context"]["source_selection_receipt"]["authority"] = "approved"
    elif problem == "catalog":
        payload["citation_catalog"][0]["locator_hint"] = "changed"
    elif problem == "catalog_text":
        next(iter(catalog.values()))["text"] = "changed"
    else:
        run = await case.rig.session.scalar(select(LlmRunModel))
        path = Path(case.rig.settings.artifact_store.local_dir, run.request_payload_ref)
        data = json.loads(path.read_text())
        data["messages"]["user"]["text"] += " changed actual request"
        data["messages"]["user"]["original_chars"] = len(data["messages"]["user"]["text"])
        path.write_text(json.dumps(data))
    text, receipt = source_module._json(payload), deepcopy(changed.receipt)
    receipt["assembled_source_sha256"] = source_module._hash(text)
    case.args["sources"] = case.operator["sources"] = source_module.CommercialSources(
        text, catalog, receipt
    )
    with bind_execution_scope(SCOPE), pytest.raises(ValueError):
        await runtime.persist_successor_authorization(case.rig.session, **case.operator)
    assert (
        case.rig.chapter.metadata_json[runtime.RUNTIME_KEY][case.args["key"]]
        == case.args["pointer"]
    )
    assert len(case.rig.calls) == case.old_calls


async def test_persisted_source_compatibility_is_reverified_before_consumption(successor_rig):
    case = successor_rig
    _current_audit_leaf_change(case)
    auth = await persist(case)
    metadata = deepcopy(auth.metadata_json)
    metadata["source_compatibility_proofs"][0]["normalized_old_payload_sha256"] = "0" * 64
    auth.metadata_json = metadata
    await case.rig.session.commit()
    with bind_execution_scope(SCOPE), pytest.raises(ValueError, match="authorization_changed"):
        await runtime.consume_authorized_successor(
            case.rig.session, **case.args, workflow_run_id=case.new_owner.id
        )
    assert (
        case.rig.chapter.metadata_json[runtime.RUNTIME_KEY][case.args["key"]]
        == case.args["pointer"]
    )
