"""Original paid SQLite proof + current Cast candidate compatibility, no providers."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import test_retained_planning_generation as fixtures

from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionOperationModel, LlmRunModel
from bestseller.services import llm, planner
from bestseller.services import retained_planning_generation as reader
from bestseller.services import source_bound_planning_content as content

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def legacy_projection(value):
    result = deepcopy(value)
    if isinstance(result, dict) and isinstance(result.get("_meta"), dict):
        for key in ("workflow_run_id", "reused", "reused_artifact_id"):
            result["_meta"].pop(key, None)
    return result


def planner_hash(request, projection):
    return planner._planner_artifact_input_hash(
        logical_name="cast_spec",
        system_prompt=request.system_prompt,
        user_prompt=request.user_prompt,
        fallback_payload=projection,
    )


@pytest.fixture
async def compat_case(tmp_path, monkeypatch):
    real_builder = content.build_source_bound_planning_prompts
    source_args = {}

    def old_builder(**kwargs):
        world = kwargs["world_spec"]
        world["_meta"] = {
            "workflow_run_id": "original-world-wrapper",
            "reused": True,
            "reused_artifact_id": "world-original",
            "source_bound_expansion_review_receipt": {"review": "old", "literal": "kept on disk"},
            "unknown_producer_field": {"keep": True},
        }
        kwargs["projection"]["_meta"] = {
            "world_spec_hash": content._hash(legacy_projection(world)),
            "unknown_projection_field": "keep",
        }
        source_args.update(deepcopy(kwargs))
        # Reproduce the previous compiler's THREE-key projection, only while
        # constructing immutable ORIGINAL fixture request/ledger evidence.
        with monkeypatch.context() as previous:
            previous.setattr(content, "_dependency_projection", legacy_projection)
            return real_builder(**kwargs)

    monkeypatch.setattr(fixtures, "build_source_bound_planning_prompts", old_builder)
    generator = fixtures.case.__wrapped__(tmp_path, monkeypatch, SimpleNamespace(param="cast_spec"))
    async for case in generator:
        original = case.args["current_request"]
        assert planner_hash(original, source_args["projection"]) == case.args["planner_input_hash"]
        document = reader._prompt_document(original)[0]
        assert document["projection"] == source_args["projection"]
        case.original_request = original.model_copy(deep=True)
        case.source_args = source_args
        case.current_args = deepcopy(source_args)
        case.current_args["project"] = case.project
        case.current_args["world_spec"]["_meta"]["source_bound_expansion_review_receipt"] = {
            "review": "new",
            "literal": "independent review, not new world facts",
        }
        case.current_args["projection"]["_meta"]["world_spec_hash"] = content._hash(
            content._dependency_projection(case.current_args["world_spec"])
        )
        document, prefix, _body, suffix = reader._prompt_document(original)
        assert not prefix and document["kind"] == "cast_spec"
        case.suffix = suffix
        case.real_builder = real_builder
        rebuild(case)
        physical = reader.retained._retained_request(case.settings, case.run)
        assert planner_hash(physical, source_args["projection"]) == case.args["planner_input_hash"]
        current_document = reader._prompt_document(case.compat_args["current_request"])[0]
        assert current_document["projection"] == (case.current_args["projection"])
        yield case


def rebuild(case):
    system, user = case.real_builder(**case.current_args)
    request = case.original_request.model_copy(
        deep=True,
        update={
            "system_prompt": system,
            "user_prompt": user + case.suffix,
            "workflow_run_id": case.consumer.id,
        },
    )
    current_hash = planner_hash(request, case.current_args["projection"])
    request.metadata["input_hash"] = current_hash
    case.compat_args = {
        **case.args,
        "current_request": request,
        "planner_input_hash": current_hash,
        "allow_cast_dependency_review_compatibility": True,
        "source_llm_run_id": case.run.id,
    }


async def invoke(case, **overrides):
    return await reader.find_retained_planning_generation(
        case.session,
        case.settings,
        **{**case.compat_args, **overrides},
    )


async def test_fourth_key_projection_only_exact_root_leaf():
    value = {
        "world": "Observatory",
        "nested": {
            "_meta": {
                "source_bound_expansion_review_receipt": {"authored": "keep nested"},
            }
        },
        "_meta": {
            "workflow_run_id": "old",
            "reused": True,
            "reused_artifact_id": "old",
            "source_bound_expansion_review_receipt": {"old": "audit"},
            "source_bound_expansion_review_receipt_extra": "keep unknown",
            "unknown": {"literal": "retain"},
        },
    }
    before = deepcopy(value)
    projected = content._dependency_projection(value)
    assert value == before
    assert projected["nested"] == value["nested"]
    assert projected["_meta"] == {
        "source_bound_expansion_review_receipt_extra": "keep unknown",
        "unknown": {"literal": "retain"},
    }
    value["_meta"]["source_bound_expansion_review_receipt"] = {"new": "audit"}
    assert content._dependency_projection(value) == projected
    value["nested"]["_meta"]["source_bound_expansion_review_receipt"] = {"changed": True}
    assert content._dependency_projection(value) != projected


async def test_real_original_paid_proof_current_cast_revalidated_no_alias_or_writes(compat_case):
    case = compat_case
    before = await fixtures.snapshot(case)
    files = {key: path.read_bytes() for key, path in case.paths.items()}
    writes = len(case.writes)
    validations = []

    def current_validator(payload):
        case.args["validator"](payload)
        validations.append(deepcopy(payload))

    result = await invoke(case, validator=current_validator)
    proof = result.proof
    assert (
        result.payload == case.payload
        and validations
        and all(v == case.payload for v in validations)
    )
    assert result.original_workflow_run_id == case.original.id
    assert result.original_llm_run_id == result.completion.llm_run_id == case.run.id
    assert result.submission_receipt == case.submission
    assert proof["full_effective_input_exact"] is False
    assert proof["paid_request_bytes_equivalence_asserted"] is False
    assert (
        proof["original_effective_input_sha256"]
        == proof["effective_input_sha256"]
        == case.run.prompt_hash
    )
    assert (
        proof["original_planner_input_sha256"]
        == proof["planner_input_sha256"]
        == (case.run.metadata_json["input_hash"])
    )
    assert proof["current_planner_input_sha256"] == case.compat_args["planner_input_hash"]
    assert proof["current_effective_input_sha256"] != proof["original_effective_input_sha256"]
    assert proof["current_planner_input_sha256"] != proof["original_planner_input_sha256"]
    assert proof["approved_difference_paths"] == list(reader._CAST_COMPAT_PATHS)
    assert (
        proof["candidate_reuse_only"]
        and proof["semantic_review_required"]
        and proof["charges_retained"]
    )
    assert not proof["grade_reused"] and not proof["approval_asserted"]
    assert not proof["credit_released"] and not proof["new_allowance_granted"]
    assert await fixtures.snapshot(case) == before and len(case.writes) == writes
    assert files == {key: path.read_bytes() for key, path in case.paths.items()}
    assert case.denied == []


async def test_compatibility_disabled_keeps_exact_rejection(compat_case):
    with pytest.raises(reader.RetainedPlanningGenerationError):
        await invoke(compat_case, allow_cast_dependency_review_compatibility=False)


async def test_compatibility_cast_only(compat_case):
    with pytest.raises(reader.RetainedPlanningGenerationError, match="compatibility_cast_only"):
        await invoke(compat_case, kind="world_spec")


@pytest.mark.parametrize(
    "change",
    [
        "author",
        "source",
        "world_body",
        "unknown_metadata",
        "nested_metadata",
        "extra_path",
        "tools",
        "model",
        "system",
        "suffix",
        "world_hash",
        "capacity",
    ],
)
async def test_compatibility_rejects_unapproved_or_unproven_differences(compat_case, change):
    case = compat_case
    if change == "world_body":
        case.current_args["world_spec"]["world_premise"] = "A different world premise"
        case.current_args["projection"]["_meta"]["world_spec_hash"] = content._hash(
            content._dependency_projection(case.current_args["world_spec"])
        )
    elif change == "unknown_metadata":
        case.current_args["world_spec"]["_meta"]["unknown_producer_field"] = {"different": True}
        case.current_args["projection"]["_meta"]["world_spec_hash"] = content._hash(
            content._dependency_projection(case.current_args["world_spec"])
        )
    elif change == "nested_metadata":
        case.current_args["world_spec"]["authored"] = {
            "_meta": {
                "source_bound_expansion_review_receipt": "must not be removed",
            }
        }
        case.current_args["projection"]["_meta"]["world_spec_hash"] = content._hash(
            content._dependency_projection(case.current_args["world_spec"])
        )
    elif change == "world_hash":
        case.current_args["projection"]["_meta"]["world_spec_hash"] = "0" * 64
    rebuild(case)
    request = case.compat_args["current_request"]
    document, prefix, _body, suffix = reader._prompt_document(request)
    if change in {"author", "source", "extra_path", "capacity"}:
        if change == "author":
            document["approved_sources"]["explicit_controls"]["forged"] = "a new obligation"
        elif change == "source":
            document["approved_sources"]["canonical_snapshot"]["content"]["premise"] = "different"
        elif change == "extra_path":
            document["unlisted_path"] = "do not ignore"
        else:
            document["prompt_capacity"]["estimated_tokens"] += 1
        if change == "capacity":
            user = reader._json(document)
        else:
            user = content._with_capacity_receipt(request.system_prompt, document)
        request.user_prompt = prefix + user + suffix
    elif change == "tools":
        request.tools[0]["function"]["description"] += " changed"
    elif change == "model":
        request.model_catalog_key = "other-model"
    elif change == "system":
        request.system_prompt += " another instruction"
    elif change == "suffix":
        request.user_prompt += " another repair task"
    current_hash = planner_hash(request, case.current_args["projection"])
    request.metadata["input_hash"] = current_hash
    case.compat_args["planner_input_hash"] = current_hash
    with pytest.raises(reader.RetainedPlanningGenerationError):
        await invoke(case)
    assert case.denied == []


async def test_compatibility_requires_current_validator(compat_case):
    with pytest.raises(reader.RetainedPlanningGenerationError, match="current_validator_rejected"):
        await invoke(compat_case, validator=lambda _value: False)


@pytest.mark.parametrize(
    "opaque", ["extra_attempt", "current_pending", "physical_response", "missing_request"]
)
async def test_original_financial_and_physical_proof_not_relaxed(compat_case, opaque):
    case = compat_case
    if opaque == "extra_attempt":
        case.session.add(
            ExecutionAttemptModel(
                id=uuid4().hex,
                scope_id=case.original.metadata_json["execution_scope_id"],
                operation_key=case.run.metadata_json["execution_receipt"]["operation_key"],
                input_hash=case.run.prompt_hash,
                model_name=case.run.model_name,
                status="reserved",
                reserved_tokens=20,
                created_at=datetime.now(UTC),
            )
        )
        await case.session.commit()
    elif opaque == "current_pending":
        catalog, _ = reader.retained._fresh_catalog()
        role = reader.retained._fresh_role(
            case.settings, case.project, case.compat_args["current_request"], catalog
        )
        case.session.add(
            ExecutionOperationModel(
                key=uuid4().hex,
                scope_id=case.original.metadata_json["execution_scope_id"],
                input_hash=llm._effective_input_hash(case.compat_args["current_request"], role),
                status="running",
                result_json={},
                owner="fixture",
                lease_until=datetime.now(UTC) + timedelta(minutes=1),
            )
        )
        await case.session.commit()
    elif opaque == "physical_response":
        case.paths["response"].write_text("{}")
    else:
        case.paths["request"].unlink()
    with pytest.raises(reader.RetainedPlanningGenerationError):
        await invoke(case)
    assert case.denied == []


async def test_compatibility_without_explicit_source_id_cannot_discover_latest(compat_case):
    case = compat_case
    duplicate = LlmRunModel(
        **{
            key: deepcopy(value)
            for key, value in vars(case.run).items()
            if not key.startswith("_") and key != "id"
        },
        id=uuid4(),
    )
    case.session.add(duplicate)
    await case.session.commit()
    with pytest.raises(reader.RetainedPlanningGenerationError):
        await invoke(case, source_llm_run_id=None)
    assert case.denied == []


async def test_current_paid_row_without_operation_still_blocks_compatibility(compat_case):
    case = compat_case
    catalog, _ = reader.retained._fresh_catalog()
    role = reader.retained._fresh_role(
        case.settings,
        case.project,
        case.compat_args["current_request"],
        catalog,
    )
    case.session.add(
        LlmRunModel(
            project_id=case.project.id,
            workflow_run_id=case.consumer.id,
            logical_role="planner",
            provider="openai",
            model_name=case.run.model_name,
            prompt_template="planner_cast_spec",
            prompt_version=case.run.prompt_version,
            prompt_hash=llm._effective_input_hash(case.compat_args["current_request"], role),
            metadata_json={
                "execution_receipt": {
                    "scope_id": case.original.metadata_json["execution_scope_id"],
                }
            },
        )
    )
    await case.session.commit()
    with pytest.raises(
        reader.RetainedPlanningGenerationError, match="current_matching_generation_conflict"
    ):
        await invoke(case)


async def test_changed_receipt_not_only_removed_receipt_is_compatible(compat_case):
    case = compat_case
    request = case.compat_args["current_request"]
    document, prefix, _body, suffix = reader._prompt_document(request)
    document["existing_planning_assets"]["world_spec"]["_meta"][
        "source_bound_expansion_review_receipt"
    ] = {"review": "another current diagnostic", "approval_asserted": False}
    request.user_prompt = (
        prefix + content._with_capacity_receipt(request.system_prompt, document) + suffix
    )
    current_hash = planner_hash(request, case.current_args["projection"])
    request.metadata["input_hash"] = current_hash
    case.compat_args["planner_input_hash"] = current_hash
    result = await invoke(case)
    assert result.proof["full_effective_input_exact"] is False
    assert result.original_llm_run_id == case.run.id
