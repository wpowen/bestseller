"""Isolated SQLite paid evidence; no model, production database, or mutation."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.schema import CreateTable
from test_commercial_component_cohort import SCOPE, _history, _judge, cohort  # noqa: F401

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
    ExecutionAttemptModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    SceneCardModel,
)
from bestseller.services import llm, model_catalog
from bestseller.services import retained_review_input_proof as proof
from bestseller.services.budget import bind_execution_scope
from bestseller.services.commercial_review_context import project_commercial_source

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _native_effective_receipts(request, role):
    """Match settle's compact receipt and finish's request-derived expansion."""
    input_hash = llm._effective_input_hash(request, role)
    origin = llm._paid_call_origin(request, input_hash)
    compact = {"schema": "effective-input.v1", "hash": input_hash, "model": role.model}
    expanded = {
        **compact,
        "messages_source": "override" if request.messages_override is not None else "system_user",
        "tools_included": bool(request.tools),
        "legacy_prompt_hash": llm._hash_prompt(request.system_prompt, request.user_prompt),
    }
    return input_hash, origin, compact, expanded


@pytest.fixture
async def retained(cohort, tmp_path, monkeypatch):  # noqa: F811
    rig = cohort
    async with rig.engine.begin() as connection:
        ddl = str(CreateTable(SceneCardModel.__table__).compile(dialect=connection.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    catalog_path = tmp_path / "model_catalog.yaml"
    entry = model_catalog.ModelCatalogEntry(
        id="selected",
        display_name="test",
        vendor="fixture",
        model=rig.settings.llm.critic.model,
        api_base="https://fixture.invalid/v1",
    )
    window_entry = entry.model_copy(
        update={"id": "window", "api_base": "https://window.fixture.invalid/v1"}
    )
    catalog_path.write_text(json.dumps({"models": [entry.model_dump(), window_entry.model_dump()]}))
    monkeypatch.setattr(model_catalog, "_CATALOG_PATH", catalog_path)
    # Deliberately leave the ordinary cached lookup frozen at the old route.
    monkeypatch.setattr(
        model_catalog, "get_model_catalog_entry", lambda key: entry if key == entry.id else None
    )
    rig.project.metadata_json = {"llm_model_id": entry.id}
    await rig.session.commit()
    from bestseller.services import chapter_llm_quality_judge as commercial_judge

    synthetic_complete = commercial_judge.complete_text
    synthetic_inputs = []

    async def capture_synthetic_input(session, settings, request):
        retained_request = request.model_copy(deep=True)
        retained_role = settings.llm.critic.model_copy(deep=True)
        result = await synthetic_complete(session, settings, request)
        synthetic_inputs.append((result.llm_run_id, retained_request, retained_role))
        return result

    monkeypatch.setattr(commercial_judge, "complete_text", capture_synthetic_input)
    _, _, _, report = await _history(rig)
    await _judge(rig, bound=True, commercial_retained_only=True)
    await rig.session.refresh(rig.chapter)
    await rig.session.refresh(rig.owner)
    # These rows were produced only by cohort's synthetic provider above.
    # Preserve every identity/hash/body and correct metadata from its captured
    # original request/role; never read or patch an actual paid payload file.
    for run_id, retained_request, retained_role in synthetic_inputs:
        run = await rig.session.get(LlmRunModel, run_id)
        input_hash, origin, compact, expanded = _native_effective_receipts(
            retained_request, retained_role
        )
        assert run.prompt_hash == input_hash
        execution = deepcopy(run.metadata_json["execution_receipt"])
        operation = await rig.session.get(ExecutionOperationModel, execution["operation_key"])
        attempt_id = execution["attempts"][0]["attempt_id"]
        transport_key = hashlib.sha256(
            f"transport-result.v1\n{SCOPE}\n{attempt_id}".encode()
        ).hexdigest()
        transport = await rig.session.get(ExecutionOperationModel, transport_key)
        assert operation.input_hash == transport.input_hash == input_hash
        completed = deepcopy(operation.result_json)
        completed["metadata"] = {
            **completed["metadata"], "paid_call_origin": deepcopy(origin),
            "effective_input_receipt": deepcopy(expanded),
        }
        wire = deepcopy(transport.result_json)
        wire["result"]["metadata"] = {
            **wire["result"]["metadata"], "paid_call_origin": deepcopy(origin),
            "effective_input_receipt": deepcopy(compact),
        }
        operation.result_json = completed
        transport.result_json = wire
        run.metadata_json = {
            **run.metadata_json,
            "paid_call_origin": deepcopy(origin),
            "effective_input_receipt": deepcopy(expanded),
        }

    source = {
        "schema": proof.SOURCE_POLICY,
        "adopted_continuity": {"prose": "完整前文及末尾事件。"},
        "current_source_channels": project_commercial_source(
            {
                "project": {"id": str(rig.project.id), "slug": rig.project.slug},
                "chapter": {"chapter_number": 2, "title": "fixture"},
                "established_facts": {"rules": ["有效知识。"]},
                "story_bible": {"world_rules": ["有效规则。"]},
            },
            chapter_number=2,
            content_md=rig.draft.content_md,
        ),
        "authority": proof._SOURCE_AUTHORITY,
    }
    packet = SimpleNamespace(full_source=True)

    async def context(*args):
        return packet

    async def sources(*args, **kwargs):
        assert kwargs["context_packet"] is packet
        return deepcopy(source)

    async def forbidden(*args, **kwargs):
        pytest.fail("no provider call allowed")

    monkeypatch.setattr(proof, "build_chapter_writer_context", context)
    monkeypatch.setattr(proof, "load_review_source_context", sources)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    rig.settings.artifact_store.local_dir = str(tmp_path / "artifacts")
    root = tmp_path / "artifacts"
    root.mkdir()
    primary = None
    paths = {}
    for template, tier, cap in [
        ("chapter_review", "standard", 8192),
        ("chapter_window_quality_judge", "strong", 4096),
    ]:
        run_id = uuid4()
        request = llm.LLMCompletionRequest(
            logical_role="critic",
            model_tier=tier,
            max_tokens_override=cap,
            system_prompt="Full retained system.",
            user_prompt="Full retained input\n"
            + json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            fallback_response="unused",
            project_id=rig.project.id,
            workflow_run_id=rig.owner.id,
            prompt_template=template,
            prompt_version="fixture-v1",
            model_catalog_key="window" if template == "chapter_window_quality_judge" else None,
        )
        role = proof._fresh_role(
            rig.settings, rig.project, request, {entry.id: entry, window_entry.id: window_entry}
        )
        effective_hash, origin, compact, expanded = _native_effective_receipts(request, role)
        operation_key, attempt_id = uuid4().hex, uuid4().hex
        execution = {
            "scope_id": SCOPE,
            "operation_key": operation_key,
            "attempts": [{"attempt_id": attempt_id, "status": "completed"}],
            "policy": "execution-budget.v1",
        }
        metadata = {
            "execution_receipt": execution,
            "paid_call_origin": deepcopy(origin),
            "effective_input_receipt": deepcopy(expanded),
        }
        if template == "chapter_review":
            metadata["review_source_receipt"] = {
                "source_complete": True,
                "source_sha256": proof._hash(source),
            }
        transport_paid = llm.LLMCompletionResult(
            content="Retained provider return",
            provider="minimax",
            model_name=role.model,
            finish_reason="stop",
            input_tokens=3,
            output_tokens=4,
            metadata={
                "llm_run_flushed": False,
                "paid_call_origin": deepcopy(origin),
                "effective_input_receipt": deepcopy(compact),
                "execution_receipt": deepcopy(execution),
            },
        ).model_dump(mode="json")
        completed_paid = deepcopy(transport_paid)
        completed_paid["metadata"]["effective_input_receipt"] = deepcopy(expanded)
        path = root / f"{run_id}.request.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "llm-payload.v1",
                    "kind": "request",
                    "replayable": True,
                    "llm_run_id": str(run_id),
                    "project_id": str(rig.project.id),
                    "workflow_run_id": str(rig.owner.id),
                    "logical_role": "critic",
                    "prompt_template": template,
                    "prompt_version": request.prompt_version,
                    "requested": {
                        key: getattr(request, key)
                        for key in (
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
            )
        )
        paths[template] = path
        run = LlmRunModel(
            id=run_id,
            project_id=rig.project.id,
            workflow_run_id=rig.owner.id,
            logical_role="critic",
            provider="minimax",
            model_name=role.model,
            prompt_template=template,
            prompt_version=request.prompt_version,
            prompt_hash=effective_hash,
            finish_reason="stop",
            metadata_json=metadata,
            request_payload_ref=path.name,
        )
        rig.session.add(run)
        rig.session.add(
            ExecutionOperationModel(
                key=operation_key,
                scope_id=SCOPE,
                input_hash=effective_hash,
                status="completed",
                owner="fixture",
                lease_until=datetime.now(UTC) + timedelta(hours=1),
                result_json=completed_paid,
            )
        )
        rig.session.add(
            ExecutionAttemptModel(
                id=attempt_id,
                operation_key=operation_key,
                scope_id=SCOPE,
                input_hash=effective_hash,
                model_name=role.model,
                status="completed",
                reserved_tokens=7,
                actual_tokens=7,
                created_at=datetime.now(UTC),
            )
        )
        key = hashlib.sha256(f"transport-result.v1\n{SCOPE}\n{attempt_id}".encode()).hexdigest()
        rig.session.add(
            ExecutionOperationModel(
                key=key,
                scope_id=SCOPE,
                input_hash=effective_hash,
                status="transport_result",
                owner="fixture",
                lease_until=datetime.now(UTC) + timedelta(hours=1),
                result_json={
                    "schema": "transport-result.v1",
                    "operation_key": operation_key,
                    "attempt_id": attempt_id,
                    "input_hash": effective_hash,
                    "result": transport_paid,
                },
            )
        )
        if template == "chapter_review":
            primary = run
        else:
            payload = deepcopy(report.structured_output)
            payload["evidence_summary"]["llm_window_quality_judge"] = {"llm_run_id": str(run.id)}
            report.structured_output = payload
    report.llm_run_id = primary.id
    await rig.session.commit()
    cycle = {
        "cycle_id": "fixture-retained-cycle",
        "state": "completed",
        "source_draft_id": str(rig.draft.id),
        "source_body_hash": hashlib.sha256(rig.draft.content_md.encode()).hexdigest(),
        "review_workflow_run_id": str(rig.owner.id),
    }
    return SimpleNamespace(
        rig=rig,
        report=report,
        cycle=cycle,
        source=source,
        primary=primary,
        paths=paths,
        catalog_path=catalog_path,
    )


async def verify(case):
    rig = case.rig
    with bind_execution_scope(SCOPE):
        return await proof.verify_retained_review_inputs(
            rig.session,
            rig.settings,
            project=rig.project,
            chapter=rig.chapter,
            draft=rig.draft,
            report=case.report,
            cycle=case.cycle,
        )


@pytest.mark.parametrize(
    "side,field",
    [
        ("transport", "schema"),
        ("operation", "messages_source"),
        ("operation", "tools_included"),
        ("operation", "legacy_prompt_hash"),
    ],
)
async def test_native_paid_identity_rejects_missing_effective_receipt_field(side, field):
    from bestseller.services import recorded_review_sources
    from bestseller.settings import load_settings

    role = load_settings(env={}).llm.critic.model_copy(update={"model": "minimax/offline-fixture"})
    request = llm.LLMCompletionRequest(
        logical_role="critic", project_id=uuid4(), workflow_run_id=uuid4(),
        prompt_template="chapter_review", prompt_version="fixture-v1",
        system_prompt="Synthetic system", user_prompt="Synthetic full input",
        fallback_response="unused",
    )
    input_hash, origin, compact, expanded = _native_effective_receipts(request, role)
    transport = llm.LLMCompletionResult(
        content="Synthetic complete return", provider="minimax", model_name=role.model,
        finish_reason="stop", input_tokens=3, output_tokens=4,
        metadata={"paid_call_origin": origin, "effective_input_receipt": compact},
    ).model_dump(mode="json")
    completed = deepcopy(transport)
    completed["metadata"]["effective_input_receipt"] = deepcopy(expanded)
    selected = transport if side == "transport" else completed
    selected["metadata"]["effective_input_receipt"].pop(field)
    with pytest.raises(recorded_review_sources.RecordedReviewSourceError,
                       match="paid_identity_metadata") as stopped:
        recorded_review_sources._strict_paid_pair(
            completed, transport, request=request, effective_hash=input_hash, model=role.model,
        )
    pointer = f"{side}.metadata.effective_input_receipt.{field}"
    assert any(item["field"] == pointer and item["actual"]["type"] == "missing"
               for item in stopped.value.differences)


async def test_exact_primary_window_and_three_commercial_slots_are_readonly_idempotent(retained):
    case = retained
    rig = case.rig
    before = deepcopy(
        (rig.chapter.metadata_json, rig.owner.metadata_json, case.report.structured_output)
    )
    calls = len(rig.calls)
    first, second = await verify(case), await verify(case)
    assert first == second and first["provider_calls"] == first["writes"] == 0
    assert first["source_compatibility"] is None
    assert first["source_sha256"] == first["retained_source_sha256"]
    assert len(first["paid_rows"]) == 2 and len(first["commercial"]["llm_run_ids"]) == 3
    assert (
        rig.chapter.metadata_json,
        rig.owner.metadata_json,
        case.report.structured_output,
    ) == before
    assert not rig.session.new and not rig.session.dirty and not rig.session.deleted
    assert len(rig.calls) == calls
    encoded = json.dumps(first)
    assert "Full retained" not in encoded and "fixture.invalid" not in encoded


@pytest.mark.parametrize(
    "fault",
    [
        "endpoint",
        "model",
        "temperature",
        "missing_payload",
        "truncated",
        "not_replayable",
        "missing_requested",
        "changed_prompt",
        "foreign_payload",
        "source",
        "source_only_metadata",
        "operation_unknown",
        "transport",
        "origin",
        "window_endpoint",
        "window_missing",
        "commercial_route",
        "commercial_raw",
        "commercial_pointer",
        "scope",
    ],
)
async def test_missing_changed_or_forged_proof_fails_without_calls_or_cache_writes(
    retained, monkeypatch, fault
):
    case = retained
    rig = case.rig
    if fault in {"endpoint", "model"}:
        data = json.loads(case.catalog_path.read_text())
        data["models"][0]["api_base" if fault == "endpoint" else "model"] = (
            "https://changed.invalid" if fault == "endpoint" else "minimax/changed"
        )
        case.catalog_path.write_text(json.dumps(data))
    elif fault == "temperature":
        rig.settings.llm.critic.temperature += 0.125
    elif fault in {
        "missing_payload",
        "truncated",
        "not_replayable",
        "missing_requested",
        "changed_prompt",
        "foreign_payload",
    }:
        path = case.paths["chapter_review"]
        if fault == "missing_payload":
            path.unlink()
        else:
            payload = json.loads(path.read_text())
            if fault == "truncated":
                payload["messages"]["user"]["truncated"] = True
            if fault == "not_replayable":
                payload["replayable"] = False
            if fault == "missing_requested":
                payload.pop("requested")
            if fault == "changed_prompt":
                payload["messages"]["user"] = llm._bounded_text("Changed prompt")
            if fault == "foreign_payload":
                payload["project_id"] = str(uuid4())
            path.write_text(json.dumps(payload))
    elif fault in {"source", "source_only_metadata"}:
        case.source["adopted_continuity"]["prose"] += "changed"
        if fault == "source_only_metadata":
            metadata = deepcopy(case.primary.metadata_json)
            metadata["review_source_receipt"]["source_sha256"] = proof._hash(case.source)
            case.primary.metadata_json = metadata
    elif fault in {"operation_unknown", "transport", "origin"}:
        operation = await rig.session.get(
            ExecutionOperationModel,
            case.primary.metadata_json["execution_receipt"]["operation_key"],
        )
        if fault == "operation_unknown":
            operation.status = "unknown"
        else:
            attempt = case.primary.metadata_json["execution_receipt"]["attempts"][0]["attempt_id"]
            key = hashlib.sha256(f"transport-result.v1\n{SCOPE}\n{attempt}".encode()).hexdigest()
            row = await rig.session.get(ExecutionOperationModel, key)
            payload = deepcopy(row.result_json)
            if fault == "transport":
                payload["result"]["content"] = "changed"
            else:
                payload["result"]["metadata"]["paid_call_origin"]["workflow_run_id"] = str(uuid4())
            row.result_json = payload
    elif fault == "window_endpoint":
        payload = json.loads(case.catalog_path.read_text())
        payload["models"][1]["api_base"] = "https://window-changed.invalid/v1"
        case.catalog_path.write_text(json.dumps(payload))
    elif fault == "window_missing":
        payload = deepcopy(case.report.structured_output)
        payload["evidence_summary"]["llm_window_quality_judge"]["llm_run_id"] = str(uuid4())
        case.report.structured_output = payload
    elif fault in {"commercial_route", "commercial_raw"}:
        if fault == "commercial_route":
            monkeypatch.setenv("BESTSELLER__LLM__COMMERCIAL_JUDGE_MODEL_KEY", "missing-route")
        else:
            run = await rig.session.scalar(
                select(LlmRunModel).where(
                    LlmRunModel.prompt_template == "chapter_commercial_quality_judge"
                )
            )
            operation = await rig.session.get(
                ExecutionOperationModel, run.metadata_json["execution_receipt"]["operation_key"]
            )
            value = deepcopy(operation.result_json)
            value["tool_calls"][0]["function"]["arguments"] = "{}"
            operation.result_json = value
    elif fault == "commercial_pointer":
        rig.chapter.metadata_json = {}
    elif fault == "scope":
        metadata = deepcopy(case.primary.metadata_json)
        metadata["execution_receipt"]["scope_id"] = "foreign"
        case.primary.metadata_json = metadata
    await rig.session.commit()
    before = deepcopy(
        (rig.chapter.metadata_json, rig.owner.metadata_json, case.report.structured_output)
    )
    calls = len(rig.calls)
    with pytest.raises((ValueError, FileNotFoundError)):
        await verify(case)
    assert (
        rig.chapter.metadata_json,
        rig.owner.metadata_json,
        case.report.structured_output,
    ) == before
    assert not rig.session.new and not rig.session.dirty and len(rig.calls) == calls


async def test_context_index_rebuild_is_refused_without_mutating_caller(retained, monkeypatch):
    async def rebuilding(session, *args):
        await session.execute(update(LlmRunModel).values(model_name="forbidden"))

    monkeypatch.setattr(proof, "build_chapter_writer_context", rebuilding)
    with pytest.raises(ValueError, match="readonly_query_required"):
        await verify(retained)
    assert not retained.rig.session.dirty


async def test_primary_source_error_attaches_only_verified_hashes_and_leaf_pointers(retained):
    case = retained
    old_source = deepcopy(case.source)
    case.source["current_source_channels"]["established_facts"]["rules"][0] = (
        "SECRET NEW CREATIVE TEXT"
    )
    with pytest.raises(ValueError, match="primary_source_changed") as caught:
        await verify(case)
    diagnostic = caught.value.source_comparison
    assert diagnostic["original_source_verified"] is True
    assert diagnostic["old_source_sha256"] == proof._hash(old_source)
    assert diagnostic["new_source_sha256"] == proof._hash(case.source)
    assert diagnostic["differences"] == [
        {
            "pointer": "/current_source_channels/established_facts/rules/0",
            "old_present": True,
            "new_present": True,
            "old_sha256": proof._hash(
                old_source["current_source_channels"]["established_facts"]["rules"][0]
            ),
            "new_sha256": proof._hash(
                case.source["current_source_channels"]["established_facts"]["rules"][0]
            ),
        }
    ]
    serialized = json.dumps(diagnostic, ensure_ascii=False)
    assert "SECRET" not in serialized and "有效知识" not in serialized
    assert "完整前文" not in serialized and not case.rig.session.dirty


async def test_source_diagnostic_selects_only_canonical_policy_object_matching_retained_hash():
    original = {"schema": proof.SOURCE_POLICY, "data": {"a/b~c": [True, None]}}
    current = {"schema": proof.SOURCE_POLICY, "data": {"a/b~c": [1, None, "SECRET VALUE"]}}
    foreign = json.dumps({"schema": proof.SOURCE_POLICY, "data": "foreign"})
    canonical = json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    diagnostic = proof._source_comparison(
        "unrelated {broken JSON " + foreign + "\nSOURCE\n" + canonical + "\nEnd",
        {"source_sha256": proof._hash(original), "source_complete": True},
        current,
    )
    assert diagnostic["original_source_verified"]
    assert [item["pointer"] for item in diagnostic["differences"]] == [
        "/data/a~1b~0c/0",
        "/data/a~1b~0c/2",
    ]
    assert diagnostic["differences"][0]["old_sha256"] != diagnostic["differences"][0]["new_sha256"]
    assert diagnostic["differences"][1]["old_present"] is False
    assert "SECRET VALUE" not in json.dumps(diagnostic)


@pytest.mark.parametrize("count,truncated", [(64, False), (70, True)])
async def test_source_diagnostic_limits_leaf_differences_to_64(count, truncated):
    original = {"schema": proof.SOURCE_POLICY, "data": {f"k{i:03}": i for i in range(count)}}
    current = {"schema": proof.SOURCE_POLICY, "data": {f"k{i:03}": i + 1 for i in range(count)}}
    canonical = json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    diagnostic = proof._source_comparison(
        canonical, {"source_sha256": proof._hash(original)}, current
    )
    assert diagnostic["original_source_verified"]
    assert len(diagnostic["differences"]) == 64 and diagnostic["truncated"] is truncated


@pytest.mark.parametrize("fault", ["hash", "schema", "noncanonical", "missing"])
async def test_unverified_original_source_never_gets_speculative_leaf_diff(fault):
    original = {"schema": proof.SOURCE_POLICY, "data": "SECRET OLD TEXT"}
    expected = proof._hash(original)
    if fault == "hash":
        expected = "0" * 64
    if fault == "schema":
        original["schema"] = "foreign"
    prompt = json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if fault == "noncanonical":
        prompt = json.dumps(original, ensure_ascii=False, indent=2)
    if fault == "missing":
        prompt = "No retained source object"
    diagnostic = proof._source_comparison(
        prompt, {"source_sha256": expected}, {"current": "secret"}
    )
    assert diagnostic["original_source_verified"] is False and diagnostic["differences"] == []
    assert diagnostic["old_source_sha256"] is None
    assert "SECRET OLD TEXT" not in json.dumps(diagnostic)


@pytest.mark.parametrize("nested_failure", [False, True])
async def test_readonly_core_savepoint_preserves_real_snapshot_and_unflushed_caller(
    cohort,  # noqa: F811
    nested_failure,
):
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from bestseller.services import continuity
    from bestseller.services.context import _safe_load_previous_snapshot
    from bestseller.services.feedback import KNOWLEDGE_LINEAGE_SCHEMA

    rig = cohort
    async with rig.engine.begin() as connection:
        ddl = str(
            CreateTable(ChapterStateSnapshotModel.__table__).compile(dialect=connection.dialect)
        )
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    previous = ChapterModel(
        project_id=rig.project.id, chapter_number=1, chapter_goal="previous", metadata_json={}
    )
    rig.session.add(previous)
    await rig.session.flush()
    previous_draft = ChapterDraftVersionModel(
        project_id=rig.project.id,
        chapter_id=previous.id,
        version_no=1,
        content_md="上一章已经发生的事件。计时器显示剩余3天。",
        is_current=True,
        promotion_state="promoted",
        promoted_at=datetime.now(UTC),
    )
    rig.session.add(previous_draft)
    await rig.session.flush()
    facts = [{"name": "remaining", "value": "3", "kind": "countdown", "unit": "days",
              "source_quote": "计时器显示剩余3天"}]
    raw = json.dumps({"facts": facts}, ensure_ascii=False)
    rig.session.add(
        ChapterStateSnapshotModel(
            project_id=rig.project.id,
            chapter_id=previous.id,
            chapter_number=1,
            extraction_status="ok_promoted",
            raw_extraction=raw,
            facts={
                "facts": facts,
                "snapshot_contract": {
                    "schema": KNOWLEDGE_LINEAGE_SCHEMA,
                    "is_usable": True,
                    "project_id": str(rig.project.id),
                    "chapter_id": str(previous.id),
                    "source_chapter_draft_version_id": str(previous_draft.id),
                    "source_version_no": 1,
                    "source_body_sha256": hashlib.sha256(
                        previous_draft.content_md.encode()
                    ).hexdigest(),
                },
            },
        )
    )
    await rig.session.commit()
    # A legacy literal snapshot must fail the real read guard until an
    # explicit same-body validation and application publishes its current view.
    with pytest.raises(RuntimeError, match="source_context_unavailable"):
        await _safe_load_previous_snapshot(
            proof._ReadOnlySession(rig.session), project_id=rig.project.id,
            current_chapter_number=2,
        )
    source = {
        "project_id": rig.project.id, "chapter_id": previous.id,
        "source_chapter_draft_version_id": previous_draft.id,
        "chapter_md": previous_draft.content_md,
    }
    checked = await continuity.validate_snapshot_epistemic_view(rig.session, **source)
    applied = await continuity.apply_snapshot_epistemic_view(
        rig.session, **source, expected_snapshot_sha256=checked["snapshot_sha256"],
        admission_receipt=checked["admission_receipt"], actor="offline-fixture-operator",
        reason="Publish a current same-body view before testing the read-only caller",
    )
    assert applied["status"] == "applied"
    row = await rig.session.scalar(select(ChapterStateSnapshotModel).where(
        ChapterStateSnapshotModel.chapter_id == previous.id,
    ))
    assert row.raw_extraction == raw and row.facts["facts"] == facts
    assert row.facts["snapshot_contract"]["source_body_sha256"] == hashlib.sha256(
        previous_draft.content_md.encode()
    ).hexdigest()
    view_sha = row.facts[continuity._EPISTEMIC_VIEW]["view_sha256"]
    await rig.session.commit()
    project_id, chapter_id = rig.project.id, rig.chapter.id
    old_title, old_words = rig.project.title, rig.chapter.current_word_count
    old_metadata = deepcopy(rig.project.metadata_json)
    rig.chapter.current_word_count = 999
    await (
        rig.session.flush()
    )  # Caller-owned outer SQL transaction must survive the inner savepoint.
    rig.project.title = "unflushed caller work"
    caller_metadata = {**old_metadata, "caller_unflushed": {"literal": "preserve this work"}}
    rig.project.metadata_json = deepcopy(caller_metadata)
    flushes = []
    event.listen(rig.session.sync_session, "before_flush", lambda *args: flushes.append(True))
    ro = proof._ReadOnlySession(rig.session)
    snapshot = await _safe_load_previous_snapshot(
        ro, project_id=project_id, current_chapter_number=2
    )
    assert snapshot is not None and snapshot.chapter_number == 1
    assert snapshot.facts[0].name == "remaining" and snapshot.facts[0].value == "3"
    assert snapshot.epistemic_view_sha256 == view_sha
    if nested_failure:
        with pytest.raises(ValueError, match="fixture read failure"):
            async with ro.begin_nested():
                await ro.scalar(select(ProjectModel.title).where(ProjectModel.id == project_id))
                raise ValueError("fixture read failure")
    assert flushes == [] and rig.project.title == "unflushed caller work"
    assert rig.project.metadata_json == caller_metadata
    assert rig.project in rig.session.dirty and rig.session.in_transaction()
    assert (
        await ro.scalar(select(ProjectModel.title).where(ProjectModel.id == project_id))
        == old_title
    )
    assert await ro.scalar(select(ProjectModel.metadata_json).where(
        ProjectModel.id == project_id,
    )) == old_metadata
    assert rig.project.metadata_json == caller_metadata and rig.project in rig.session.dirty
    assert (
        await ro.scalar(
            select(ChapterModel.current_word_count).where(ChapterModel.id == chapter_id)
        )
        == 999
    )
    await rig.session.rollback()
    async with async_sessionmaker(rig.engine)() as other:
        assert (
            await other.scalar(select(ProjectModel.title).where(ProjectModel.id == project_id))
            == old_title
        )
        assert await other.scalar(select(ProjectModel.metadata_json).where(
            ProjectModel.id == project_id,
        )) == old_metadata
        assert (
            await other.scalar(
                select(ChapterModel.current_word_count).where(ChapterModel.id == chapter_id)
            )
            == old_words
        )


async def test_explicit_audit_compatibility_keeps_original_paid_hashes_and_no_new_attempts(
    retained,
):
    case = retained
    rig = case.rig
    old_full = proof._hash(case.source)
    old_opaque = case.source["current_source_channels"]["source_selection_receipt"][
        "original_source_sha256"
    ]
    payloads = {name: path.read_bytes() for name, path in case.paths.items()}
    protected = deepcopy(
        (rig.chapter.metadata_json, rig.owner.metadata_json, case.report.structured_output)
    )
    original_rows = {
        run.id: (run.prompt_hash, deepcopy(run.metadata_json))
        for run in await rig.session.scalars(select(LlmRunModel))
    }
    first = None
    for audit in ("0" * 64, "1" * 64):
        case.source["current_source_channels"]["source_selection_receipt"][
            "original_source_sha256"
        ] = audit
        result = await verify(case)
        receipt = result["source_compatibility"]
        assert receipt["schema"] == "retained-review-source-audit-compatibility.v1"
        assert receipt["old_full_source_sha256"] == result["retained_source_sha256"] == old_full
        assert (
            receipt["current_full_source_sha256"]
            == result["source_sha256"]
            == proof._hash(case.source)
        )
        assert receipt["old_opaque_audit_sha256"] == old_opaque
        assert receipt["current_opaque_audit_sha256"] == audit
        assert receipt["changed_pointer"] == proof._AUDIT_POINTER
        assert receipt["raw_bundle_equivalence_asserted"] is False
        assert receipt["paid_request_bytes_equivalence_asserted"] is False
        exact = deepcopy(case.source)
        exact["current_source_channels"]["source_selection_receipt"].pop("original_source_sha256")
        assert receipt["selected_semantic_sha256"] == proof._hash(exact)
        if first:
            assert (
                receipt["selected_semantic_sha256"]
                == first["source_compatibility"]["selected_semantic_sha256"]
            )
            assert result["paid_rows"] == first["paid_rows"]
        first = result
        assert result == await verify(case)
    assert protected == (
        rig.chapter.metadata_json,
        rig.owner.metadata_json,
        case.report.structured_output,
    )
    assert payloads == {name: path.read_bytes() for name, path in case.paths.items()}
    assert original_rows == {
        run.id: (run.prompt_hash, run.metadata_json)
        for run in await rig.session.scalars(select(LlmRunModel))
    }
    assert not rig.session.new and not rig.session.dirty and len(rig.calls) == 5


def _audit_pair():
    original = {
        "schema": proof.SOURCE_POLICY,
        "authority": proof._SOURCE_AUTHORITY,
        "adopted_continuity": {"facts": ["same fact"]},
        "current_source_channels": project_commercial_source(
            {
                "story_bible": {"world_rules": ["same rule"]},
                "established_facts": {"facts": ["same occurred event"]},
            }
        ),
    }
    current = deepcopy(original)
    current["current_source_channels"]["source_selection_receipt"]["original_source_sha256"] = (
        "0" * 64
    )
    return original, current


@pytest.mark.parametrize("side", ["original", "current"])
@pytest.mark.parametrize(
    "field",
    [
        "policy",
        "original_bible_sha256",
        "retained_bible_sha256",
        "original_bible_characters",
        "retained_bible_characters",
        "retained_bible_fields",
        "omitted_bible_fields",
        "authority",
        "extra_key",
        "missing_key",
        "invalid_audit_hash",
        "missing_receipt",
        "source_schema",
        "projection_schema",
        "unknown_projection_field",
        "facts",
        "adopted",
        "creator",
        "schema_extra",
        "target_words",
    ],
)
async def test_only_one_known_opaque_audit_field_is_compatible(side, field):
    original, current = _audit_pair()
    source = original if side == "original" else current
    selected = source["current_source_channels"]
    receipt = selected["source_selection_receipt"]
    if field in {"original_bible_sha256", "retained_bible_sha256"}:
        receipt[field] = "f" * 64
    elif field in {"original_bible_characters", "retained_bible_characters"}:
        receipt[field] += 1
    elif field in {"retained_bible_fields", "omitted_bible_fields"}:
        receipt[field].append("new-field")
    elif field in {"policy", "authority"}:
        receipt[field] += "changed"
    elif field == "extra_key":
        receipt["unknown"] = "extra"
    elif field == "missing_key":
        receipt.pop("original_bible_sha256")
    elif field == "invalid_audit_hash":
        receipt["original_source_sha256"] = "invalid"
    elif field == "missing_receipt":
        selected.pop("source_selection_receipt")
    elif field == "source_schema":
        source["schema"] = "unknown"
    elif field == "projection_schema":
        selected["schema_version"] = "unknown"
    elif field == "unknown_projection_field":
        selected["unknown"] = "new"
    elif field == "facts":
        selected["established_facts"]["facts"][0] = "different event"
    elif field == "adopted":
        source["adopted_continuity"]["facts"].append("new event")
    elif field == "creator":
        selected["explicit_creator_requirements"]["rule"] = "new creator clause"
    elif field == "schema_extra":
        source["unknown"] = "new"
    elif field == "target_words":
        selected["quality_targets"]["target_word_count"] = 999
    prompt = json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    retained = {"source_complete": True, "source_sha256": proof._hash(original)}
    assert proof._audit_source_compatibility(prompt, retained, current) is None


@pytest.mark.parametrize(
    "fault", ["wrong_expected_hash", "noncanonical", "incomplete_receipt", "not_embedded"]
)
async def test_audit_compatibility_requires_exact_original_paid_source(fault):
    original, current = _audit_pair()
    prompt = json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    receipt = {"source_complete": True, "source_sha256": proof._hash(original)}
    if fault == "wrong_expected_hash":
        receipt["source_sha256"] = "1" * 64
    elif fault == "noncanonical":
        prompt = json.dumps(original, ensure_ascii=False, indent=2)
    elif fault == "incomplete_receipt":
        receipt["source_complete"] = False
    elif fault == "not_embedded":
        prompt = "No source object"
    assert proof._audit_source_compatibility(prompt, receipt, current) is None


async def test_other_source_change_with_audit_change_still_raises_with_safe_diagnostic(retained):
    case = retained
    case.source["current_source_channels"]["source_selection_receipt"]["original_source_sha256"] = (
        "0" * 64
    )
    case.source["adopted_continuity"]["prose"] += "new occurrence"
    with pytest.raises(ValueError, match="primary_source_changed") as caught:
        await verify(case)
    assert caught.value.source_comparison["original_source_verified"]
    assert len(caught.value.source_comparison["differences"]) == 2
    assert not case.rig.session.dirty and len(case.rig.calls) == 5
