"""Retained v27 bytes with synthetic in-memory custody rows, never PG or a provider.

Real route/physical/attempt/transport verification functions execute over these
fixture rows. This does not claim production database custody or regrade history.
"""

# ruff: noqa: RUF001
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    WorkflowRunModel,
)
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import llm
from bestseller.services import retained_review_input_proof as proof
from bestseller.services.commercial_action_projection import project_commercial_actions
from bestseller.services.commercial_review_tool import submitted_review_payload
from bestseller.services.commercial_review_wire import parse_commercial_wire
from bestseller.services.commercial_source_evidence import (
    CommercialSources,
    resolve_commercial_source_pointer,
)
from bestseller.services.quality_gates_config import get_quality_gates_config
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.fixture
def actual(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    path = (
        root
        / "reports/continuous-recovery-2026-10-16"
        / ("20261017T154234685690Z-v27-current-review-unknown-diagnostic.json")
    )
    if not path.exists():
        pytest.skip("Optional retained local physical evidence absent")
    settings = load_settings().model_copy(deep=True)
    directory = Path(settings.artifact_store.local_dir)
    if not directory.is_absolute():
        directory = root / directory
    before_files = {path: path.read_bytes()}
    diagnostic = json.loads(before_files[path])
    rows = [
        r for r in diagnostic["runs"] if r["prompt_template"] == "chapter_commercial_quality_judge"
    ]
    settings.llm.critic.model = (
        rows[0]["model_name"] if "model_name" in rows[0] else "openai/MiniMax-M3"
    )
    settings.llm.critic.model_override = None
    settings.llm.mock = False
    monkeypatch.setattr(proof, "apply_runtime_llm_profile", lambda value: value)
    monkeypatch.setattr(judge, "resolve_commercial_judge_model_key", lambda _: None)
    monkeypatch.setenv("JUDGE_SAMPLES", "3")

    async def forbidden(*args, **kwargs):
        pytest.fail("No provider calls in retained proof checks")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    origin = rows[0]["metadata_json"]["paid_call_origin"]
    project = SimpleNamespace(id=UUID(origin["project_id"]), metadata_json={})
    chapter = SimpleNamespace(id=uuid4(), chapter_number=2, metadata_json={})
    draft = SimpleNamespace(id=UUID(diagnostic["draft_id"]), content_md=diagnostic["draft_content"])
    scope = rows[0]["metadata_json"]["execution_receipt"]["scope_id"]
    owner = SimpleNamespace(
        id=UUID(origin["workflow_run_id"]),
        project_id=project.id,
        scope_id=chapter.id,
        scope_type="chapter",
        metadata_json={},
    )
    storage = {(WorkflowRunModel, owner.id): owner}
    budget = SimpleNamespace(
        status="active", metadata_json={}, deadline_at=datetime.now(UTC) + timedelta(days=1)
    )
    storage[ExecutionBudgetModel, scope] = budget
    physical = {}
    for row in rows:
        paths = [directory / row[key] for key in ("request_payload_ref", "response_payload_ref")]
        for file in paths:
            before_files[file] = file.read_bytes()
        request, response = [json.loads(before_files[file]) for file in paths]
        user = request["messages"]["user"]
        assert not user["truncated"] and len(user["text"]) == user["original_chars"]
        marker = "完整来源包（JSON 数据，含本章全文、已提供前章和类型化引用目录）：\n"
        start = user["text"].index(marker) + len(marker)
        payload, length = json.JSONDecoder().raw_decode(user["text"][start:])
        text = user["text"][start : start + length]
        receipt = row["metadata_json"]["commercial_source_input_receipt"]
        assert hashlib.sha256(text.encode()).hexdigest() == receipt["assembled_source_sha256"]
        assert payload["current_body"] == draft.content_md
        catalog = {}
        for entry in payload["citation_catalog"]:
            value = resolve_commercial_source_pointer(payload, entry["source_ref"])
            if entry["kind"] in {"current", "prior"}:
                quote = value[entry["start"] : entry["end"]]
            elif entry["kind"] == "observation":
                quote = next(
                    (
                        value[k]
                        for k in ("text", "evidence", "value")
                        if isinstance(value, dict)
                        and isinstance(value.get(k), str)
                        and value[k].strip()
                    ),
                    encoded(value) if isinstance(value, dict) else str(value),
                )
            else:
                assert entry["kind"] == "constraint"
                quote = encoded(value)
            assert quote[:32] == entry["locator_hint"]
            catalog[entry["id"]] = {**entry, "text": quote}
        sources = CommercialSources(text=text, catalog=catalog, receipt=receipt)
        meta = deepcopy(row["metadata_json"])
        run = SimpleNamespace(
            id=UUID(row["id"]),
            project_id=project.id,
            workflow_run_id=UUID(meta["paid_call_origin"]["workflow_run_id"]),
            logical_role="critic",
            provider=response["provider"],
            model_name=response["model"],
            prompt_template=row["prompt_template"],
            prompt_version=row["prompt_version"],
            prompt_hash=meta["effective_input_receipt"]["hash"],
            finish_reason=response["finish_reason"],
            metadata_json=meta,
        )
        storage[LlmRunModel, run.id] = run
        assert not response["content"]["truncated"]
        assert response["raw_message"].get("tool_calls") == response["tool_calls"]
        paid = llm.LLMCompletionResult(
            content=response["content"]["text"],
            provider=run.provider,
            model_name=run.model_name,
            finish_reason=run.finish_reason,
            tool_calls=response["tool_calls"],
            raw_message=response["raw_message"],
            input_tokens=response["input_tokens"],
            output_tokens=response["output_tokens"],
            fallback_used=response["fallback_used"],
            fallback_source=response["fallback_source"],
            metadata=meta,
        )
        execution = meta["execution_receipt"]
        attempts = [a for a in execution["attempts"] if a["status"] == "completed"]
        assert len(attempts) == 1
        aid, opkey = attempts[0]["attempt_id"], execution["operation_key"]
        operation = SimpleNamespace(
            key=opkey,
            scope_id=scope,
            status="completed",
            input_hash=run.prompt_hash,
            result_json=paid.model_dump(mode="json"),
        )
        storage[ExecutionOperationModel, opkey] = operation
        storage[ExecutionAttemptModel, aid] = SimpleNamespace(
            id=aid,
            scope_id=scope,
            status="completed",
            operation_key=opkey,
            input_hash=run.prompt_hash,
            model_name=run.model_name,
        )
        transport_key = hashlib.sha256(f"transport-result.v1\n{scope}\n{aid}".encode()).hexdigest()
        storage[ExecutionOperationModel, transport_key] = SimpleNamespace(
            scope_id=scope,
            status="transport_result",
            result_json={
                "operation_key": opkey,
                "attempt_id": aid,
                "input_hash": run.prompt_hash,
                "result": deepcopy(operation.result_json),
            },
        )
        raw, submission, errors = submitted_review_payload(paid)
        _, _, wire_errors = parse_commercial_wire(raw, sources)
        physical[str(run.id)] = (run, raw, submission, [*errors, *wire_errors], sources)
    assert len(physical) == 5
    original = diagnostic["report"]["evidence_summary"]["llm_commercial_judge_original"]
    cohort = json.loads(original["raw_excerpt"])
    slots = {}
    for sample in cohort["samples"]:
        value = sample["report"]
        envelope = json.loads(value["raw_excerpt"])
        run, raw, submission, errors, sources = physical[value["llm_run_id"]]
        assert raw == envelope["raw_report"] and errors == envelope["validation_errors"]
        assert submission == envelope["adapter_receipt"]["submission"]
        slots[sample["sample_slot"]] = {
            "state": "completed",
            "report": deepcopy(value),
            "used_attempts": envelope["attempt"],
        }
    derived, projection = project_commercial_actions(
        original, sources=sources, gate_config=get_quality_gates_config().l6_gate, chapter_number=2
    )
    assert derived is not None and projection["status"] == "projected"
    # Build a candidate projected report in memory; the old unknown report stays untouched.
    report = SimpleNamespace(
        structured_output={
            "evidence_summary": {
                "llm_commercial_judge": derived,
                "llm_commercial_judge_original": deepcopy(original),
                "commercial_action_projection": projection,
            }
        }
    )
    route = proof._fresh_role(
        settings,
        project,
        llm.LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt="proof",
            user_prompt="proof",
            fallback_response="proof",
        ),
        {},
    )
    assert route.model == next(iter(physical.values()))[0].model_name
    binding = {
        "project_id": str(project.id),
        "chapter_id": str(chapter.id),
        "draft_id": str(draft.id),
        "scope_id": scope,
        "samples": 3,
        "effective_critic_sha256": proof._hash(route.model_dump(mode="json")),
        "source_binding": envelope["source_binding"],
        "evaluation_contract": envelope["evaluation_contract"],
    }
    journal = {
        "schema": "commercial-component-cohort.v1",
        "binding": binding,
        "slots": slots,
        "aggregate": {
            "report": LLMQualityJudgeResult.model_validate(original).model_dump(mode="json"),
            "slots_sha256": proof._hash({k: v["report"] for k, v in slots.items()}),
        },
    }
    key = proof._hash(
        {
            "project_id": str(project.id),
            "chapter_id": str(chapter.id),
            "body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest(),
        }
    )
    chapter.metadata_json = {
        "commercial_component_cohorts": {
            key: {"workflow_run_id": str(owner.id), "binding_sha256": proof._hash(binding)}
        }
    }
    owner.metadata_json = {"commercial_component_cohorts": {key: journal}}

    class Session:
        async def get(self, model, identity):
            return storage.get((model, identity))

    return SimpleNamespace(
        session=Session(),
        settings=settings,
        project=project,
        chapter=chapter,
        draft=draft,
        report=report,
        catalog={},
        scope=scope,
        sources=sources,
        journal=journal,
        storage=storage,
        physical=physical,
        files=before_files,
    )


async def verify(case, **kwargs):
    return await proof._commercial_proof(
        case.session,
        case.settings,
        project=case.project,
        chapter=case.chapter,
        draft=case.draft,
        report=case.report,
        catalog=case.catalog,
        scope=case.scope,
        commercial_sources=kwargs.get("commercial_sources", case.sources),
    )


async def test_actual_five_returns_original_aggregate_and_invalid_abstention_verify_readonly(
    actual,
):
    before = deepcopy((actual.report.structured_output, actual.journal, actual.storage))
    first = await verify(actual)
    assert first == await verify(actual) and len(first["llm_run_ids"]) == 3
    projection = actual.report.structured_output["evidence_summary"]["commercial_action_projection"]
    assert [s["status"] for s in projection["slots"]] == ["verified", "verified", "unknown"]
    invalid = projection["slots"][2]["original_report"]
    assert LLMQualityJudgeResult.model_validate(invalid).infrastructure_failure
    assert invalid == actual.journal["slots"]["chapter-quality.independent-v2:2"]["report"]
    assert actual.report.structured_output == before[0] and actual.journal == before[1]
    assert actual.storage == before[2]
    assert all(path.read_bytes() == raw for path, raw in actual.files.items())


@pytest.mark.parametrize(
    "fault",
    [
        "missing_sources",
        "missing_original",
        "missing_derived",
        "original",
        "projection",
        "derived",
        "gate",
        "combined_errors",
        "adapter",
        "physical_root",
        "transport",
        "attempt",
        "source",
        "missing_run",
        "missing_transport",
        "slot_report",
        "aggregate_extra",
        "cohort_substitution",
    ],
)
async def test_projected_majority_never_waives_forged_physical_source_or_projection_proof(
    actual, fault
):
    case = actual
    evidence = case.report.structured_output["evidence_summary"]
    entry = case.journal["slots"]["chapter-quality.independent-v2:2"]
    value = entry["report"]
    run = case.physical[value["llm_run_id"]][0]
    execution = run.metadata_json["execution_receipt"]
    aid = next(a["attempt_id"] for a in execution["attempts"] if a["status"] == "completed")
    transport_key = hashlib.sha256(f"transport-result.v1\n{case.scope}\n{aid}".encode()).hexdigest()
    if fault == "missing_original":
        evidence.pop("llm_commercial_judge_original")
    elif fault == "missing_derived":
        evidence["llm_commercial_judge"] = {}
        case.settings.pipeline.enable_chapter_llm_commercial_judge = False
    elif fault == "original":
        evidence["llm_commercial_judge_original"]["overall_score"] = 0.99
    elif fault == "projection":
        evidence["commercial_action_projection"]["scores_modified"] = True
    elif fault == "derived":
        evidence["llm_commercial_judge"]["overall_score"] = 0.99
    elif fault == "gate":
        evidence["commercial_action_projection"]["gate_config"] = {"forged": True}
    elif fault == "physical_root":
        case.storage[ExecutionOperationModel, execution["operation_key"]].result_json[
            "content"
        ] += "forged"
    elif fault == "transport":
        case.storage[ExecutionOperationModel, transport_key].result_json["result"]["content"] += (
            "forged"
        )
    elif fault == "attempt":
        case.storage[ExecutionAttemptModel, aid].status = "unknown"
    elif fault == "missing_run":
        case.storage.pop((LlmRunModel, run.id))
    elif fault == "missing_transport":
        case.storage.pop((ExecutionOperationModel, transport_key))
    elif fault == "source":
        case.sources.receipt["body_sha256"] = "0" * 64
    elif fault == "aggregate_extra":
        case.journal["aggregate"]["report"]["forged"] = True
    elif fault == "cohort_substitution":
        v = case.journal["aggregate"]["report"]
        raw = json.loads(v["raw_excerpt"])
        raw["samples"][2]["report"]["llm_run_id"] = str(uuid4())
        v["raw_excerpt"] = json.dumps(raw)
        evidence["llm_commercial_judge_original"] = LLMQualityJudgeResult.model_validate(
            v
        ).model_dump(mode="json", by_alias=True)
    elif fault in {"combined_errors", "adapter", "slot_report"}:
        env = json.loads(value["raw_excerpt"])
        if fault == "combined_errors":
            env["validation_errors"].pop(0)
        elif fault == "adapter":
            env["adapter_receipt"]["wire_validation_status"] = "verified"
        else:
            value["rewrite_plan"]["scope"] = "chapter"
        value["raw_excerpt"] = json.dumps(env)
        case.journal["aggregate"]["slots_sha256"] = proof._hash(
            {k: v["report"] for k, v in case.journal["slots"].items()}
        )
        aggregate = case.journal["aggregate"]["report"]
        raw = json.loads(aggregate["raw_excerpt"])
        raw["samples"][2]["report"] = deepcopy(value)
        aggregate["raw_excerpt"] = json.dumps(raw)
        evidence["llm_commercial_judge_original"] = LLMQualityJudgeResult.model_validate(
            aggregate
        ).model_dump(mode="json", by_alias=True)
    with pytest.raises(ValueError, match="retained_review_input_"):
        await verify(case, commercial_sources=None if fault == "missing_sources" else case.sources)
    assert all(path.read_bytes() == raw for path, raw in case.files.items())
