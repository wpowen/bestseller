"""SQLite durability and real contract/parser/patch validation; no provider."""

from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from test_chapter_outline_readiness import rig as rig
from test_chapter_outline_readiness import sqlite_identity_store as sqlite_identity_store

from bestseller.infra.db.models import LlmRunModel, ProjectModel, WorkflowRunModel
from bestseller.services import chapter_outline_readiness as ready
from bestseller.services import planner
from bestseller.services import prefix_contract_repair as repair
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings

REAL_VALIDATE = planner._validate_generated_volume_outline_or_raise
pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def case(rig, sqlite_identity_store, monkeypatch):
    factory, _ = sqlite_identity_store
    async with factory() as session:
        engine = session.bind
    for model in (ProjectModel, LlmRunModel):
        table = model.__table__.to_metadata(MetaData())
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            if column.server_default is not None and "::" in str(column.server_default.arg):
                column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with engine.begin() as connection:
            await connection.run_sync(table.create)
    project = ProjectModel(**vars(rig.project))
    cast = {
        "protagonist": {"name": "Courier", "gender": "male", "role": "protagonist"},
        "supporting_cast": [{"name": "Guard", "gender": "female", "role": "ally"}],
    }
    rig.foundation["cast_spec"].content.update(cast)
    async with factory() as session:
        session.add(project)
        await session.commit()
        foundation, contents = await ready._foundation(session, project)
        source, volume = ready._source_record(project, rig.source)
        binding = ready._window_binding(project, rig.source, volume)
        state = {
            "identity": {
                "project_id": str(project.id),
                "source_hash": content_hash(rig.source),
                "binding": binding,
                "foundation": foundation,
                "max_repair_rounds": 2,
            },
            "source_payload": deepcopy(rig.source),
            "source_record": source,
            "binding": binding,
            "foundation": foundation,
            "chapter_numbers": [3],
            "hard_patch_attempts": [],
            "execution_scope_id": f"project:{project.id}",
            "phase": "initial_validate",
        }
        workflow = WorkflowRunModel(
            id=uuid4(),
            project_id=project.id,
            workflow_type=ready.WORKFLOW_TYPE,
            scope_type="project",
            scope_id=project.id,
            status="running",
            requested_by="offline-test",
            metadata_json=deepcopy(state),
            idempotency_key=content_hash(state["identity"]),
        )
        session.add(workflow)
        await session.commit()
    monkeypatch.setattr(planner, "_validate_generated_volume_outline_or_raise", REAL_VALIDATE)
    candidate = {"batch_name": "prefix", "chapters": [deepcopy(rig.source["chapters"][2])]}
    candidate["chapters"][0]["scenes"][0]["participants"] = [
        "Courier",
        "Unlisted observer (offscreen)",
    ]
    calls, outcomes = [], []

    async def completion(session, settings, request):
        async with factory() as observer:
            saved = (await observer.get(WorkflowRunModel, workflow.id)).metadata_json
            assert saved["hard_patch_attempts"][-1]["status"] == "pending"
            assert saved["hard_patch_attempts"][-1]["actual_contract_report"]["passed"] is False
        calls.append(request)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        run = LlmRunModel(
            id=uuid4(),
            project_id=project.id,
            workflow_run_id=workflow.id,
            logical_role="planner",
            provider="minimax",
            model_name="inert-fixture",
            prompt_template=repair.TEMPLATE,
            prompt_version="1.0",
            prompt_hash=content_hash(request.user_prompt),
            finish_reason="stop",
            metadata_json={"execution_receipt": {"scope_id": state["execution_scope_id"]}},
        )
        if isinstance(outcome, tuple):
            outcome, change = outcome
            setattr(run, *change)
        session.add(run)
        await session.commit()
        return LLMCompletionResult(
            content=outcome if isinstance(outcome, str) else json.dumps(outcome),
            provider="minimax",
            model_name="inert-fixture",
            llm_run_id=run.id,
            finish_reason="stop",
        )

    monkeypatch.setattr(repair, "complete_text", completion)
    return SimpleNamespace(
        factory=factory,
        project=project,
        workflow=workflow,
        state=state,
        cast=contents["cast_spec"],
        contents=contents,
        candidate=candidate,
        calls=calls,
        outcomes=outcomes,
    )


def patches(value=None):
    return {
        "patches": [
            {
                "chapter_number": 3,
                "scope": "scene",
                "scene_number": 1,
                "field": "participants",
                "value": ["Courier", "Guard"] if value is None else value,
            }
        ]
    }


async def invoke(case):
    async with case.factory() as session:
        workflow = await session.get(WorkflowRunModel, case.workflow.id)
        return await repair.validate_prefix_candidate(
            session,
            load_settings(env={}),
            project=case.project,
            workflow=workflow,
            state=case.state,
            candidate=case.candidate,
            cast=case.cast,
            contents=case.contents,
            continuity=None,
            max_repair_rounds=2,
        )


async def test_real_contract_repair_is_durable_and_final_lineage_current(case):
    with pytest.raises(planner.OutlineContractValidationError) as captured:
        ready._validate(case.candidate, project=case.project, state=case.state, cast=case.cast)
    assert any(
        v["code"] == "PLAN_SCENE_UNKNOWN_PARTICIPANT" for v in captured.value.report["violations"]
    )
    original = deepcopy(case.candidate)
    source_state = {
        key: deepcopy(case.state[key])
        for key in ("source_payload", "source_record", "binding", "foundation", "identity")
    }
    case.outcomes.append(patches())
    result = await invoke(case)
    digest = content_hash(result)
    attempt = case.state["hard_patch_attempts"][0]
    assert attempt["status"] == "validated"
    assert attempt["json_parse_receipt"]["status"] == "complete"
    assert attempt["json_parse_receipt"]["validated_candidate_hash"] == digest
    lineage = ready._contract_patch_lineage(case.state, result)
    assert lineage["llm_run_id"] == attempt["completion"]["llm_run_id"]
    assert lineage["source_admission_required"] is True and lineage["grade_created"] is False
    case.state["candidates"] = [
        {"candidate": deepcopy(result), "patch_llm_run_id": lineage["llm_run_id"]}
    ]
    assert ready.prefix_json_lineage_status(case.state)["status"] == "current"
    assert case.candidate == original
    approved = json.loads(case.calls[0].user_prompt)["approved_sources"]
    expected_source = {
        key: deepcopy(value)
        for key, value in source_state["source_payload"].items()
        if key != "_meta"
    }
    expected_source["chapters"] = [source_state["source_payload"]["chapters"][2]]
    assert approved["original_source_payload"] == expected_source
    selection = approved["source_selection_receipt"]
    assert selection["full_source_sha256"] == content_hash(source_state["source_payload"])
    assert selection["selected_source_sha256"] == content_hash(expected_source)
    assert selection["excluded_chapter_numbers"] == [1, 2, 4, 5, 6, 7, 8, 9]
    assert {key: case.state[key] for key in source_state} == source_state
    assert await invoke(case) == result
    assert len(case.calls) == 1
    async with case.factory() as observer:
        saved = (await observer.get(WorkflowRunModel, case.workflow.id)).metadata_json
    assert saved["contract_validation_results"][digest] == lineage


@pytest.mark.parametrize("numbers", [[3], [2, 3]])
async def test_projection_retains_all_selected_nested_and_unknown_story_fields(numbers):
    chapters = [
        {
            "chapter_number": number,
            "volume_number": 1,
            "unknown_story_field": {"fact": f"parcel-{number}", "values": [False, 0, None]},
            "_meta": {"chapter_level_story_extension": "retain this nested root"},
            "scenes": [
                {
                    "scene_number": 1,
                    "participants": ["Courier"],
                    "unknown_scene_field": {"_meta": {"story_fact": "seal remains broken"}},
                }
            ],
        }
        for number in range(1, 5)
    ]
    original = {
        "chapters": chapters,
        "batch_name": "complete-window",
        "unknown_root_story_field": {"_meta": {"custom_story_fact": ["deadline", 0]}},
        "_meta": {"archived_volume_plan": {"chapters": ["uneditable producer history"]}},
    }
    state = {
        "source_payload": original,
        "chapter_numbers": numbers,
        "persisted_source_fence": {"full_original_sha256": content_hash(original)},
    }
    contents = {
        kind: {
            "_meta": {"producer_receipt": f"{kind}-archive"},
            "unknown_story_field": {"_meta": {"nested_fact": kind}, "facts": [1, None, False]},
            "story": [{"_meta": "authored nested extension", "literal": "unchanged"}],
        }
        for kind in ("book_spec", "world_spec", "cast_spec")
    }
    continuity = {
        "_meta": {"retained_predecessor_receipt": "full"},
        "context_hash": "exact-adopted-continuity",
        "recent_adopted_prose": [
            {"chapter_number": 1, "body": "Full predecessor one.\n" * 120},
            {"chapter_number": 2, "body": "Full predecessor two.\n" * 120},
        ],
        "unknown_continuity_field": {"_meta": {"facts": ["deadline", "injury"]}},
    }
    before = deepcopy((state, contents, continuity))
    result = repair._prompt_source_projection(state, contents, continuity)
    selected = {key: deepcopy(value) for key, value in original.items() if key != "_meta"}
    selected["chapters"] = [
        deepcopy(chapter) for chapter in chapters if chapter["chapter_number"] in numbers
    ]
    assert result["original_source_payload"] == selected
    assert result["full_adopted_continuity"] == continuity
    for kind, asset in contents.items():
        assert result[kind] == {key: value for key, value in asset.items() if key != "_meta"}
    receipt = result["source_selection_receipt"]
    assert receipt["schema"] == "prefix-contract-source-selection.v1"
    assert receipt["chapter_numbers"] == numbers
    assert receipt["excluded_chapter_numbers"] == [n for n in range(1, 5) if n not in numbers]
    assert receipt["full_source_sha256"] == content_hash(original)
    assert receipt["selected_source_sha256"] == content_hash(selected)
    assert receipt["original_asset_sha256"] == {
        kind: content_hash(asset) for kind, asset in contents.items()
    }
    assert receipt["story_asset_sha256"] == {kind: content_hash(result[kind]) for kind in contents}
    assert receipt["archive_metadata_is_authority"] is False
    assert receipt["selected_story_fields_complete"] is True
    assert (state, contents, continuity) == before
    # Prompt construction must not share mutable selected/continuity objects.
    result["original_source_payload"]["chapters"][0]["unknown_story_field"]["fact"] = "changed"
    result["full_adopted_continuity"]["recent_adopted_prose"][0]["body"] = "changed"
    for kind in contents:
        result[kind]["unknown_story_field"]["_meta"]["nested_fact"] = "changed"
    assert (state, contents, continuity) == before


@pytest.mark.parametrize("numbers", [[3], [2, 1]])
async def test_projection_rejects_missing_or_reordered_selected_range(numbers):
    state = {
        "source_payload": {"chapters": [{"chapter_number": 1}, {"chapter_number": 2}]},
        "chapter_numbers": numbers,
    }
    before = deepcopy(state)
    with pytest.raises(ready.OutlinePrefixReadinessError, match="selected_source_range_missing"):
        repair._prompt_source_projection(state, {}, None)
    assert state == before


@pytest.mark.parametrize(
    "outcome",
    [
        "{bad",
        {"patches": []},
        patches(["Courier", "Guard (offscreen)"]),
        {"patches": patches()["patches"], "full_outline": {}},
        {"patches": [{**patches()["patches"][0], "chapter_number": 99}]},
    ],
)
async def test_invalid_return_is_retained_consumes_one_and_never_replays(case, outcome):
    case.outcomes.append(outcome)
    with pytest.raises((ValueError, planner.PlannerFallbackError)):
        await invoke(case)
    assert len(case.state["hard_patch_attempts"]) == 1
    assert case.state["hard_patch_attempts"][0]["status"] == "invalid"
    assert case.state["hard_patch_attempts"][0]["completion"]["content"]
    with pytest.raises(ready.OutlinePrefixReadinessError, match="claimed_attempt_not_replayable"):
        await invoke(case)
    assert len(case.calls) == 1 and not case.state.get("contract_validation_results")


async def test_existing_shared_budget_exhaustion_has_no_provider(case):
    case.state["hard_patch_attempts"] = [
        {"status": "invalid", "raw": "old1"},
        {"status": "invalid", "raw": "old2"},
    ]
    async with case.factory() as session:
        row = await session.get(WorkflowRunModel, case.workflow.id)
        row.metadata_json = deepcopy(case.state)
        await session.commit()
    with pytest.raises(planner.OutlineContractValidationError, match="budget exhausted"):
        await invoke(case)
    assert not case.calls and len(case.state["hard_patch_attempts"]) == 2


async def test_capacity_failure_does_not_claim(case, monkeypatch):
    def reject(system, user):
        assert json.loads(user)["locked_cast_manifest"]
        raise ValueError("capacity-fixture")

    monkeypatch.setattr(repair, "assert_source_bound_planning_prompt_capacity", reject)
    with pytest.raises(ValueError, match="capacity-fixture"):
        await invoke(case)
    assert not case.calls and not case.state["hard_patch_attempts"]


async def test_schema_failure_never_enters_contract_repair(case):
    case.candidate["chapters"][0]["scenes"] = "invalid-schema"
    with pytest.raises(Exception) as captured:
        await invoke(case)
    assert not isinstance(captured.value, planner.OutlineContractValidationError)
    assert not case.calls and not case.state["hard_patch_attempts"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", uuid4()),
        ("workflow_run_id", uuid4()),
        ("prompt_template", "other-template"),
        ("metadata_json", {"execution_receipt": {"scope_id": "wrong"}}),
    ],
)
async def test_wrong_actual_run_binding_cannot_validate(case, field, value):
    case.outcomes.append((patches(), (field, value)))
    with pytest.raises(ready.OutlinePrefixReadinessError):
        await invoke(case)
    assert case.state["hard_patch_attempts"][0]["status"] == "invalid"
    assert not case.state.get("contract_validation_results")


async def test_failed_workflow_claim_restores_running_and_archives_failure(case):
    prior = {"code": "original_contract_rejected", "raw": "retained old evidence"}
    case.state["failure"] = deepcopy(prior)
    async with case.factory() as session:
        row = await session.get(WorkflowRunModel, case.workflow.id)
        row.status = "failed"
        row.error_message = "old contract failure"
        row.metadata_json = deepcopy(case.state)
        await session.commit()
    original_completion = repair.complete_text

    async def observe(session, settings, request):
        async with case.factory() as observer:
            row = await observer.get(WorkflowRunModel, case.workflow.id)
            assert row.status == "running" and row.error_message is None
            assert "failure" not in row.metadata_json
            history = row.metadata_json["contract_validation_history"]
            assert len(history) == 1 and history[0]["failure"] == prior
            assert history[0]["workflow_status"] == "failed"
            assert history[0]["error_message"] == "old contract failure"
        return await original_completion(session, settings, request)

    # Observe the real committed row at the inert completion boundary.
    case.outcomes.append(patches())
    from unittest.mock import patch

    with patch.object(repair, "complete_text", observe):
        await invoke(case)
    await invoke(case)
    assert len(case.state["contract_validation_history"]) == 1


async def test_actual_budget_only_missing_field_repairs_without_old_plot_or_story_mutation(case,monkeypatch,rig):
    from test_source_bound_outline_integrity import source
    raw=json.loads(json.dumps(source()).replace("Mira","Courier"))
    case.candidate={"batch_name":raw["batch_name"],"chapters":[raw["chapters"][2]]}
    from bestseller.services.book_design import ensure_project_book_design_snapshot
    metadata=deepcopy(case.project.metadata_json)
    for key in ['book_design_snapshot','book_design_snapshot_hash','book_design_snapshot_status']:
        metadata.pop(key,None)
    metadata.update(premise='Courier inspects the observatory.',creation_protagonist_name='Courier',creation_protagonist_source='user')
    case.project.metadata_json=metadata
    ensure_project_book_design_snapshot(case.project)
    from bestseller.services.rolling_outline import build_macro_plan,build_rolling_outline_plan
    macro=build_macro_plan([{'chapter_number':n,'anchor':f'Anchor {n}'} for n in range(1,51)])
    case.project.metadata_json['rolling_outline_plan']=build_rolling_outline_plan(macro,current_state_snapshot={'current_chapter':0},next_macro_anchor=macro.slots[9].to_dict(),source_snapshot_hash=case.project.metadata_json['book_design_snapshot_hash'],window_size=9).to_dict()
    from bestseller.services.outline_recovery_checkpoint import recovery_binding
    source_record=case.project.metadata_json['outline_commercial_judge_checkpoints']['window']['best']
    source_record['binding']=recovery_binding(case.project.metadata_json,1,[1,9])
    source_record['report']['outline_repair_dependency_hash']=source_record['binding']['dependency_hash']
    case.state['source_record']=deepcopy(source_record)
    binding=ready._window_binding(case.project,case.state['source_payload'],1)
    case.state['binding']=deepcopy(binding);case.state['identity']['binding']=deepcopy(binding)
    for artifact in rig.foundation.values():artifact.content['_meta']['source_snapshot_hash']=case.project.metadata_json['book_design_snapshot_hash']
    async with case.factory() as session:
        foundation,contents=await ready._foundation(session,case.project)
        case.contents=contents;case.cast=contents['cast_spec'];case.state['foundation']=foundation;case.state['identity']['foundation']=deepcopy(foundation)
        project=await session.get(ProjectModel,case.project.id);project.metadata_json=deepcopy(case.project.metadata_json)
        workflow=await session.get(WorkflowRunModel,case.workflow.id);workflow.metadata_json=deepcopy(case.state)
        await session.commit()
    scene=case.candidate['chapters'][0]['scenes'][0]
    scene['participants']=['Courier','Guard']
    scene.pop('target_word_count')
    before=deepcopy(case.candidate)
    target=case.candidate['chapters'][0]['target_word_count']
    case.outcomes.append({'patches':[{'chapter_number':3,'scope':'scene','scene_number':1,'field':'target_word_count','value':target}]})
    result=await invoke(case)
    assert len(case.calls)==1
    request=case.calls[0];data=json.loads(request.user_prompt)
    assert isinstance(data,dict)
    assert request.prompt_version=='1.4-typed-scene-budget' and request.max_tokens_override==1024
    assert 'original_source_payload' not in request.user_prompt and 'locked_cast_manifest' not in request.user_prompt
    assert result['chapters'][0]['scenes'][0].pop('target_word_count')==target
    assert result==before and case.candidate==before
    assert case.state['hard_patch_attempts'][0]['status']=='validated'


async def test_whole_effective_capacity_rejects_before_durable_attempt(case,monkeypatch):
    from bestseller.services import retained_review_input_proof as retained,llm_capacity
    from bestseller.services.llm_capacity import ModelRequestCapacityError
    settings=load_settings(env={});settings.llm.mock=False
    monkeypatch.setattr(retained,'_fresh_role',lambda *a:settings.llm.planner)
    def reject(payload):
        assert payload['max_tokens']==12000
        raise ModelRequestCapacityError({'budget_passed':False,'test_only':True})
    monkeypatch.setattr(llm_capacity,'assert_effective_request_capacity',reject)
    async with case.factory() as session:
        workflow=await session.get(WorkflowRunModel,case.workflow.id)
        with pytest.raises(ModelRequestCapacityError):
            await repair.validate_prefix_candidate(session,settings,project=case.project,workflow=workflow,state=case.state,candidate=case.candidate,cast=case.cast,contents=case.contents,continuity=None,max_repair_rounds=2)
    assert not case.calls and case.state['hard_patch_attempts']==[]
