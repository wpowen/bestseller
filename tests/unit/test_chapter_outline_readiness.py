"""Prefix admission and bounded repair with inert DB/provider boundaries."""

from __future__ import annotations

import copy
import json
from contextlib import asynccontextmanager, nullcontext
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.engine import make_url

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import (
    ChapterDraftVersionModel, ChapterModel, LlmRunModel, PlanningArtifactVersionModel, WorkflowRunModel,
)
from bestseller.services import chapter_outline_readiness as service
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.outline_recovery_checkpoint import recovery_binding
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.rolling_outline import (
    build_macro_plan,
    build_rolling_outline_plan,
    rolling_window_schedule_hash,
)
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("text", [
    '{"patches":[]}', '```json\n{"patches":[]}\n```', '```\n{"patches":[]}\n```',
])
def test_prefix_strict_parser_accepts_only_complete_objects_or_fences(text):
    parsed, receipt = service._parse_prefix_json(text)
    assert parsed == {"patches": []}
    assert receipt["mode"] == "strict" and receipt["patch_count"] == 0
    assert receipt["inserted_closers"] == []


@pytest.mark.parametrize("text", [
    '{"patches":[],"patches":[1]}', '{"a":{"x":1,"x":2}}',
    '```json\n{"patches":[]}', '```json\n{"patches":[]}\n``` extra',
    'prefix {"patches":[]}', '{"patches":[]} trailing', '{"patches":[]} {"extra":1}',
    '{"patches":[{"field":"goal","value":"done"}, {"value":"unfinished',
    '{"patches":[{"field":"goal","value":"done"}', '{"patches":[]',
    '{"patches":[{"value":"done"},]}', '{"patches":[{"value":"done"}]"}',
    '{"patches":[{"value":"bad\\q"}]}', '{"x":NaN}', '[]',
])
def test_prefix_parser_never_salvages_truncation_fragments_duplicates_or_scalars(text):
    with pytest.raises(ValueError):
        service._parse_prefix_json(text)


def test_internal_closing_insertion_preserves_all_original_tokens_and_patch_items():
    import hashlib
    raw = ('{"patches":[{"chapter_number":2,"scope":"scene","scene_number":1,'
        '"field":"entry_state","value":{"summary":"intact 星空"},'
        '{"chapter_number":2,"scope":"scene","scene_number":2,'
        '"field":"exit_state","value":{"summary":"all items retained"}}]}')
    parsed, receipt = service._parse_prefix_json(raw)
    assert len(parsed["patches"]) == receipt["patch_count"] == 2
    assert parsed["patches"][0]["value"] == {"summary": "intact 星空"}
    assert receipt["mode"] == "internal_closers_only"
    repaired, shift = raw, 0
    for insertion in receipt["inserted_closers"]:
        assert insertion["token"] in {"}", "]"}
        assert insertion["raw_offset"] < len(raw.rstrip())
        position = insertion["raw_offset"] + shift
        repaired = repaired[:position] + insertion["token"] + repaired[position:]
        shift += 1
    assert json.loads(repaired) == parsed
    assert receipt["raw_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert receipt["repaired_sha256"] == hashlib.sha256(repaired.encode()).hexdigest()
    assert [t[0] for t in service._json_tokens(raw)] == [t[0] for t in service._json_tokens(repaired)
        if t[1] not in {i["raw_offset"] + n for n, i in enumerate(receipt["inserted_closers"])}]
    assert service._parse_prefix_json(raw)[1] == receipt
    with pytest.raises(ValueError, match="prefix_json_protocol_unknown"):
        service._parse_prefix_json(raw, allow_internal_closers=False)


def test_parse_receipt_does_not_mutate_paid_raw_or_finish_reason():
    raw = '{"patches":[{"field":"goal"}, {"field":"exit_state","value":"cut off'
    attempt = {"completion": {"content": raw, "finish_reason": "stop", "llm_run_id": str(uuid4())}}
    paid = copy.deepcopy(attempt["completion"])
    with pytest.raises(ValueError):
        service._parse_prefix_attempt(attempt)
    assert attempt["completion"] == paid
    assert attempt["json_parse_receipt"]["status"] == "unknown"


def test_complete_internal_closure_proof_cannot_relabel_a_partial_old_grade():
    run = str(uuid4())
    raw = '{"patches":[{"value":{"summary":"kept"},{"value":"also kept"}]}'
    attempt = {"completion": {"content": raw, "finish_reason": "stop", "llm_run_id": run}}
    candidate = {"patches": [{"value": {"summary": "kept"}}]}
    state = {"candidates": [{"patch_llm_run_id": run, "candidate": candidate}], "patch_attempts": [attempt]}
    assert service.prefix_json_lineage_status(state)["status"] == "non_fresh"
    parsed = service._parse_prefix_attempt(attempt)
    assert len(parsed["patches"]) == 2
    assert service.prefix_json_lineage_status(state)["status"] == "non_fresh"
    state["candidates"][0]["candidate"] = parsed
    attempt["json_parse_receipt"]["validated_candidate_hash"] = content_hash(parsed)
    assert service.prefix_json_lineage_status(state)["status"] == "current"
    attempt["json_parse_receipt"]["patch_count"] = 1
    assert service.prefix_json_lineage_status(state)["status"] == "non_fresh"


def test_only_used_non_strict_legacy_lineage_is_non_fresh_and_grades_stay_intact():
    run = str(uuid4())
    raw = '{"patches":[{"field":"goal","value":"complete"}, {"value":"unfinished'
    candidate = {"patch_llm_run_id": run, "validated": True, "report": {"pass": True},
        "source_admission": {"raw_grade": "original paid grade"}}
    state = {"candidates": [candidate], "patch_attempts": [{"completion": {
        "content": raw, "finish_reason": "stop", "llm_run_id": run}, "status": "validated"}]}
    before = copy.deepcopy(state)
    status = service.prefix_json_lineage_status(state)
    assert status["status"] == "non_fresh" and state == before
    state["candidates"] = []
    assert service.prefix_json_lineage_status(state)["status"] == "current"
    state["candidates"] = [candidate]
    state["patch_attempts"][0]["completion"]["content"] = '{"patches":[]}'
    assert service.prefix_json_lineage_status(state)["status"] == "current"


@pytest.mark.parametrize("genre,old_plot", [
    ("science-fiction", "AI demands a second first landing"),
    ("romance", "AI mandates an unselected wedding"),
    ("historical", "AI orders an unapproved duel"),
])
def test_continuation_projection_omits_ai_plot_and_preserves_literal_authority(rig, genre, old_plot):
    candidate = copy.deepcopy(rig.source)
    for chapter in candidate["chapters"]:
        chapter.update(goal=old_plot, methodology_contract={"required": old_plot},
            selected_effect_skills={"expected_contracts": old_plot}, target_word_count=2300)
        chapter["scenes"][0]["purpose"] = {"story": old_plot, "source": "user"}
    clauses = {"source": "user", "chapter_requirements": [{"chapter_number": 2,
        "text": "Keep the observatory closed until noon; do not replace this clause."}],
        "scene_requirements": [{"chapter_number": 2, "scene_number": 1,
            "text": "The courier speaks through the window."}]}
    brief = {"genre": genre, "premise": old_plot,
        "approved_creation_contracts": {"creation_intent_contract": clauses},
        "approved_world_authority": {"authority_status": "current", "rules": ["No teleportation"]}}
    before = copy.deepcopy(candidate)
    projection, receipt = service._continuation_projection(candidate, brief=brief)
    encoded = json.dumps(projection, ensure_ascii=False)
    assert old_plot not in encoded and "No teleportation" in encoded
    assert clauses["chapter_requirements"][0]["text"] in encoded
    assert clauses["scene_requirements"][0]["text"] in encoded
    assert projection["targets"][1] == {"chapter_number": 2, "volume_number": 1, "target_word_count": 2300}
    assert receipt["omitted_plan_hash"] == content_hash(candidate) and candidate == before
    candidate["chapters"][0]["goal"] = "A different unadopted AI plot"
    other, other_receipt = service._continuation_projection(candidate, brief=brief)
    assert other == projection and other_receipt["omitted_plan_hash"] != receipt["omitted_plan_hash"]


class Store:
    def __init__(self):
        self.rows = {}
        self.commits = []
        self.promoted_draft = None
        self.info = {}
        self.no_autoflush = nullcontext()
        self.saved_fields = {}

    async def scalar(self, statement):
        return self.promoted_draft

    async def scalars(self, statement):
        if statement.column_descriptions[0].get("entity") is WorkflowRunModel:
            rows = [row for (model, _), row in self.rows.items() if model is WorkflowRunModel]
            for criterion in statement._where_criteria:
                rows = [row for row in rows if getattr(row, criterion.left.key) == criterion.right.value]
            return rows
        if self.promoted_draft is not None:
            entity = statement.column_descriptions[0].get("entity")
            bindings = {criterion.left.key: criterion.right.value
                        for criterion in statement._where_criteria
                        if hasattr(criterion, "left") and hasattr(criterion, "right")}
            if entity is ChapterModel:
                self.adopted_chapter_id = uuid4()
                return [SimpleNamespace(id=self.adopted_chapter_id,
                    project_id=bindings["project_id"], metadata_json={})]
            if entity is ChapterDraftVersionModel:
                return [SimpleNamespace(id=self.promoted_draft, is_current=False, promoted_at=None,
                    project_id=bindings["project_id"], chapter_id=bindings["chapter_id"],
                    promotion_state="promoted", content_md="Retained fixture adoption.")]
        return []

    async def get(self, model, identity, **kwargs):
        key = (model, UUID(str(identity))) if identity else None
        row = self.rows.get(key)
        if row is not None and kwargs.get("populate_existing") and key in self.saved_fields:
            for field, value in self.saved_fields[key].items():
                setattr(row, field, copy.deepcopy(value))
        return row

    async def flush(self):
        pass

    async def commit(self):
        self.saved_fields = {
            key: {field: copy.deepcopy(getattr(row, field, None))
                  for field in ("metadata_json", "status", "current_step", "idempotency_key",
                                "error_message", "project_id", "workflow_type", "scope_type",
                                "scope_id", "requested_by")}
            for key, row in self.rows.items() if key[0] is WorkflowRunModel
        }
        self.commits.append(
            copy.deepcopy(
                {
                    str(k[1]): v.metadata_json
                    for k, v in self.rows.items()
                    if k[0] == WorkflowRunModel
                }
            )
        )

    async def rollback(self):
        for key, fields in self.saved_fields.items():
            for field, value in fields.items():
                setattr(self.rows[key], field, copy.deepcopy(value))

    @asynccontextmanager
    async def begin_nested(self):
        rows = copy.deepcopy(self.rows)
        try:
            yield
        except BaseException:
            self.rows = rows
            raise

    async def refresh(self, value):
        pass


@pytest.fixture
def rig(monkeypatch):
    url = make_url(load_settings().database.url)
    assert url.get_backend_name() == "sqlite" and "test" in url.database
    macro = build_macro_plan([{"chapter_number": n, "anchor": f"Anchor {n}"} for n in range(1, 51)])
    rolling = build_rolling_outline_plan(
        macro,
        current_state_snapshot={"current_chapter": 0},
        next_macro_anchor=macro.slots[9].to_dict(),
        source_snapshot_hash="a" * 64,
        window_size=9,
    )
    schedule = [{"volume_number": 1, "window_index": 1, "window_start": 1, "window_end": 9}]
    metadata = {
        "book_design_snapshot": {"source_hash": "a" * 64},
        "book_design_snapshot_hash": "a" * 64,
        "derived_input_revision": {"candidate_hash": "revision-1"},
        "macro_outline_plan": macro.to_dict(),
        "rolling_outline_plan": rolling.to_dict(),
        "rolling_outline_windows": schedule,
        "rolling_outline_windows_hash": rolling_window_schedule_hash(schedule),
    }
    project = SimpleNamespace(
        id=uuid4(),
        slug="prefix-test",
        title="Parcel ledger",
        genre="mystery",
        sub_genre=None,
        language="en",
        target_chapters=50,
        target_word_count=130000,
        reader_contract_json={},
        hype_scheme_json={},
        metadata_json=metadata,
    )
    source = {
        "batch_name": "window-1",
        "chapters": [
            {
                "chapter_number": n,
                "volume_number": 1,
                "title": f"Parcel {n}",
                "goal": f"Inspect parcel {n} before closing.",
                "opening_situation": "A courier reaches the depot.",
                "main_conflict": "The guard needs proof.",
                "hook_description": "A seal is missing.",
                "key_reveals": ["A missing seal"],
                "chapter_information_introduced": ["A damaged register"],
                "scenes": [
                    {
                        "scene_number": 1,
                        "participants": ["Courier", "Guard"],
                        "purpose": {"story": "Check the register"},
                        "entry_state": {"summary": "Sealed"},
                        "exit_state": {"summary": "A date is missing"},
                        "action_sequence": ["Compare dates"],
                    }
                ],
            }
            for n in range(1, 10)
        ],
        "_meta": {"historical_source": True},
    }
    grade = {
        "passed": False,
        "overall_score": 0.79,
        "graded_outline_hash": content_hash(source),
        "outline_repair_dependency_hash": recovery_binding(metadata, 1, [1, 9])["dependency_hash"],
    }
    record = {
        "candidate": copy.deepcopy(source),
        "candidate_hash": content_hash(source),
        "report": grade,
        "binding": recovery_binding(metadata, 1, [1, 9]),
        "workflow_run_id": str(uuid4()),
    }
    metadata["outline_commercial_judge_checkpoints"] = {"window": {"best": record}}
    store = Store()
    store.rows[(service.ProjectModel, project.id)] = project
    foundation = {}
    for kind in service._FOUNDATION_TYPES:
        foundation[kind.value] = SimpleNamespace(
            id=uuid4(),
            project_id=project.id,
            status="approved",
            source_run_id=uuid4(),
            version_no=1,
            content={"_meta": {"source_snapshot_hash": "a" * 64}},
        )

    async def approved(_session, *, project_id, artifact_type):
        assert project_id == project.id
        return foundation[artifact_type.value]

    monkeypatch.setattr(service, "get_latest_approved_planning_artifact", approved)

    async def create(_session, **kwargs):
        for (kind, _), row in store.rows.items():
            if kind == WorkflowRunModel and row.idempotency_key == kwargs.get("idempotency_key"):
                return row
        row = SimpleNamespace(
            id=uuid4(),
            project_id=kwargs["project_id"],
            workflow_type=kwargs["workflow_type"],
            scope_type=kwargs["scope_type"], scope_id=kwargs["scope_id"], requested_by=kwargs["requested_by"],
            status=kwargs["status"].value,
            current_step=kwargs["current_step"],
            metadata_json=kwargs["metadata"],
            idempotency_key=kwargs.get("idempotency_key"),
        )
        store.rows[(WorkflowRunModel, row.id)] = row
        return row

    monkeypatch.setattr(service, "create_workflow_run", create)
    validations = []

    def typed(payload, **kwargs):
        validations.append(copy.deepcopy(kwargs))
        assert kwargs["expected_count"] == len(payload["chapters"])
        assert kwargs["chapter_number_offset"] == payload["chapters"][0]["chapter_number"]
        assert kwargs["strict_story_effects"] is True and kwargs["strict_field_degeneracy"] is True
        return ChapterOutlineBatchInput.model_validate(payload).model_dump(
            mode="json", by_alias=True
        )

    monkeypatch.setattr(service.planner, "_validate_generated_volume_outline_or_raise", typed)
    judges, patches, judge_inputs, patch_inputs, rebase_inputs = [True], [], [], [], []

    def real_run(workflow, template):
        row = SimpleNamespace(
            id=uuid4(),
            project_id=project.id,
            workflow_run_id=workflow,
            provider="minimax",
            metadata_json={},
            finish_reason="stop",
            prompt_template=template,
            prompt_hash="exact-prompt",
        )
        store.rows[(LlmRunModel, row.id)] = row
        return row.id

    async def judge(_session, _settings, **kwargs):
        assert kwargs["project_id"] == project.id
        assert len(kwargs["outline_payload"]["chapters"]) == len(
            kwargs["project_brief"]["prefix_evaluation_scope"]["approved_scope_only"]
        )
        # Candidate is committed before its model boundary.
        wf = await store.get(WorkflowRunModel, kwargs["workflow_run_id"])
        assert store.commits[-1][str(wf.id)]["candidates"][-1]["validated"]
        judge_inputs.append(copy.deepcopy(kwargs))
        outcome = judges.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        run = real_run(wf.id, "outline_commercial_judge")
        return LLMQualityJudgeResult(
            scope="outline",
            passed=outcome,
            overall_score=0.95 if outcome else 0.72,
            llm_run_id=str(run),
            blocking_issues=()
            if outcome
            else (
                {
                    "code": "MISSING_VISIBLE_ACTION",
                    "severity": "critical",
                    "evidence": "The guard is absent.",
                    "required_fix": "Show an ordinary action.",
                },
            ),
        )

    monkeypatch.setattr(service, "judge_outline_commercial_readiness", judge)

    async def patch(_session, _settings, request):
        assert request.project_id == project.id and request.workflow_run_id
        if request.prompt_template == 'continuation_outline_rebase':
            rebase_inputs.append(request)
            supplied = json.loads(request.user_prompt)
            targets = supplied['continuation_input']['targets']
            outcome = {"chapters": [copy.deepcopy(source["chapters"][target["chapter_number"] - 1])
                for target in targets]}
            outcome['chapters'][0]['opening_situation'] = 'Continue from the adopted ending.'
            run = real_run(request.workflow_run_id, request.prompt_template)
            return LLMCompletionResult(content=json.dumps(outcome), provider='minimax',
                model_name='test-model', llm_run_id=run, finish_reason='stop')
        assert request.fallback_response == '{"patches":[]}'
        patch_inputs.append(request)
        outcome = patches.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        run = real_run(request.workflow_run_id, "chapter_outline_prefix_surgical_repair")
        return LLMCompletionResult(
            content=json.dumps(outcome),
            provider="minimax",
            model_name="test-model",
            llm_run_id=run,
            finish_reason="stop",
        )

    monkeypatch.setattr(service, "complete_text", patch)
    from bestseller.services import planning_draft_admission
    async def source_admission(*args, **kwargs):
        return {'schema': planning_draft_admission.SCHEMA,
            'hard_admission_policy': planning_draft_admission.HARD_ADMISSION_POLICY,
            'draft_eligible': True, 'assessment': {'draft_eligible': True,
            'hard_violations': [], 'editorial_notes': []}}
    monkeypatch.setattr(planning_draft_admission, 'assess_planning_draft_admission', source_admission)
    async def verify_admission(*args, **kwargs):
        assert kwargs['receipt']['draft_eligible'] is True
        return kwargs['receipt']
    monkeypatch.setattr(planning_draft_admission, 'verify_planning_draft_admission', verify_admission)
    imports = []

    async def import_artifact(_session, slug, payload):
        imports.append(copy.deepcopy(payload))
        row = SimpleNamespace(
            id=uuid4(),
            project_id=project.id,
            status="approved",
            source_run_id=payload.source_run_id,
            artifact_type=payload.artifact_type.value,
            content=copy.deepcopy(payload.content),
        )
        store.rows[(PlanningArtifactVersionModel, row.id)] = row
        return row

    monkeypatch.setattr(service, "import_planning_artifact", import_artifact)
    return SimpleNamespace(
        project=project,
        source=source,
        store=store,
        foundation=foundation,
        judges=judges,
        patches=patches,
        judge_inputs=judge_inputs,
        patch_inputs=patch_inputs,
        rebase_inputs=rebase_inputs,
        imports=imports,
        validations=validations,
    )


async def _approve(rig, **kwargs):
    return await service.approve_chapter_outline_prefix(
        rig.store,
        load_settings(),
        project=rig.project,
        source_payload=rig.source,
        through_chapter=kwargs.pop("through_chapter", 3),
        **kwargs,
    )


def _workflow(rig):
    return next(row for (model, _), row in rig.store.rows.items() if model == WorkflowRunModel)


async def test_pass_approves_only_exact_prefix_and_preserves_full_sources(rig):
    before = copy.deepcopy(rig.project.metadata_json)
    result = await _approve(rig)
    assert result["passed"] and result["chapter_numbers"] == [1, 2, 3]
    assert rig.project.metadata_json == before
    assert len(rig.imports) == 1 and len(rig.imports[0].content["chapters"]) == 3
    wf = _workflow(rig)
    assert wf.metadata_json["source_payload"] == rig.source
    assert wf.metadata_json["source_record"]["report"]["overall_score"] == 0.79
    receipt = await service.verify_outline_prefix_readiness(
        rig.store, project=rig.project, artifact_id=UUID(result["artifact_id"])
    )
    assert receipt["full_window_approved"] is False and receipt["full_volume_complete"] is False
    assert receipt["binding"]["window"] == [1, 9] and receipt["binding"]["target_chapters"] == 50
    assert await _approve(rig) == result and len(rig.judge_inputs) == 1


async def test_two_paid_empty_patch_failures_are_durable_and_never_approved(rig):
    rig.judges[:] = [False]
    rig.patches[:] = [{"patches": []}, {"patches": []}]
    result = await _approve(rig)
    assert result["passed"] is False and not rig.imports
    state = _workflow(rig).metadata_json
    assert len(state["candidates"]) == 1 and state["phase"] == "rejected"
    assert len(state["patch_attempts"]) == 2
    assert all(p["completion"]["content"] == '{"patches": []}' for p in state["patch_attempts"])
    assert all("empty" in p["error"]["message"] for p in state["patch_attempts"])
    assert "empty" in rig.patch_inputs[1].user_prompt
    assert await _approve(rig) == result and len(rig.patch_inputs) == 2


async def test_invalid_target_then_valid_patch_rejudges_new_source_only(rig):
    rig.judges[:] = [False, True]
    rig.patches[:] = [
        {
            "patches": [
                {"chapter_number": 12, "scope": "chapter", "field": "goal", "value": "Foreign"}
            ]
        },
        {
            "patches": [
                {
                    "chapter_number": 1,
                    "scope": "chapter",
                    "field": "goal",
                    "value": "Trade the pass for the ledger.",
                }
            ]
        },
    ]
    result = await _approve(rig)
    assert result["passed"] and len(rig.patch_inputs) == 2 and len(rig.judge_inputs) == 2
    assert rig.patch_inputs[0].user_prompt != rig.patch_inputs[1].user_prompt
    state = _workflow(rig).metadata_json
    assert len(state["candidates"]) == 2 and len(state["patch_attempts"]) == 2
    assert (
        state["candidates"][0]["candidate"]["chapters"][0]["goal"]
        != state["candidates"][1]["candidate"]["chapters"][0]["goal"]
    )


async def test_provider_budget_stop_is_not_retried_and_retains_failed_state(rig):
    rig.judges[:] = [ExecutionBudgetError("execution_paused")]
    with pytest.raises(ExecutionBudgetError):
        await _approve(rig)
    assert not rig.imports and not rig.patch_inputs and len(rig.judge_inputs) == 1
    assert _workflow(rig).metadata_json["failure"]["exception_type"] == "ExecutionBudgetError"
    assert rig.store.commits[-1][str(_workflow(rig).id)]["source_payload"] == rig.source


@pytest.mark.parametrize(
    "corruption",
    [
        "source",
        "dependency",
        "snapshot",
        "foundation",
        "artifact",
        "workflow",
        "run_project",
        "finish",
    ],
)
async def test_admission_rejects_stale_or_forged_proofs(rig, corruption):
    result = await _approve(rig)
    wf = _workflow(rig)
    artifact = await rig.store.get(PlanningArtifactVersionModel, result["artifact_id"])
    if corruption == "source":
        wf.metadata_json["source_payload"]["chapters"][0]["goal"] = "Changed"
    elif corruption == "dependency":
        rig.project.metadata_json["derived_input_revision"]["candidate_hash"] = "new"
    elif corruption == "snapshot":
        rig.project.metadata_json["book_design_snapshot_hash"] = "new"
    elif corruption == "foundation":
        rig.foundation["cast_spec"].id = uuid4()
    elif corruption == "artifact":
        artifact.content["chapters"][0]["goal"] = "Changed"
    elif corruption == "workflow":
        wf.status = "running"
    else:
        run = await rig.store.get(LlmRunModel, wf.metadata_json["final_report"]["llm_run_id"])
        if corruption == "run_project":
            run.project_id = uuid4()
        else:
            run.finish_reason = "tool_calls"
    with pytest.raises(ValueError):
        await service.verify_outline_prefix_readiness(
            rig.store, project=rig.project, artifact_id=artifact.id
        )


async def test_wrong_full_source_fails_before_any_workflow_or_model(rig):
    rig.source["chapters"].pop()
    with pytest.raises(ValueError, match="checkpoint"):
        await _approve(rig)
    assert not any(model is WorkflowRunModel for model, _ in rig.store.rows) and not rig.judge_inputs


async def test_unknown_judge_run_cannot_approve(rig, monkeypatch):
    original = service.judge_outline_commercial_readiness

    async def unknown(*args, **kwargs):
        verdict = await original(*args, **kwargs)
        return verdict.model_copy(update={"llm_run_id": str(uuid4())})

    monkeypatch.setattr(service, "judge_outline_commercial_readiness", unknown)
    with pytest.raises(ValueError, match="scope_mismatch"):
        await _approve(rig)
    assert not rig.imports and "report" in _workflow(rig).metadata_json["candidates"][0]


def _materialized(approved):
    from bestseller.services.workflows import (
        _sync_chapter_causality_metadata,
        _sync_chapter_outline_semantics,
        _sync_scene_methodology_metadata,
    )

    typed = ChapterOutlineBatchInput.model_validate({"chapters": [approved]}).chapters[0]
    chapter = SimpleNamespace(
        **{
            name: getattr(typed, name)
            for name in (
                "chapter_number",
                "title",
                "chapter_goal",
                "opening_situation",
                "main_conflict",
                "hook_type",
                "hook_description",
                "target_word_count",
            )
        },
        metadata_json={},
        primary_emotion=None,
        information_revealed=[],
    )
    _sync_chapter_causality_metadata(chapter, typed)
    _sync_chapter_outline_semantics(chapter, typed)
    scenes = []
    for row in typed.scenes:
        scene = SimpleNamespace(**row.model_dump(mode="python"), metadata_json={})
        _sync_scene_methodology_metadata(scene, row)
        scenes.append(scene)
    return chapter, scenes


def test_projection_ignores_dynamic_status_but_detects_writer_plan_changes(rig):
    chapter, scenes = _materialized(rig.source["chapters"][0])
    original = service.outline_prefix_projection_hash(chapter, scenes)
    chapter.metadata_json.update(
        review_count=9, outline_prefix_readiness={"projection_hash": original}
    )
    scenes[0].metadata_json["repair_attempts"] = 5
    assert service.outline_prefix_projection_hash(chapter, scenes) == original
    service._verify_materialized_content(chapter, scenes, rig.source["chapters"][0])
    scenes[0].participants.append("Not present")
    assert service.outline_prefix_projection_hash(chapter, scenes) != original
    with pytest.raises(ValueError, match="scene_changed"):
        service._verify_materialized_content(chapter, scenes, rig.source["chapters"][0])


def test_materializer_clearing_approved_reveals_is_not_saved_by_new_hash(rig):
    approved = copy.deepcopy(rig.source["chapters"][0])
    chapter, scenes = _materialized(approved)
    chapter.metadata_json["key_reveals"] = []
    chapter.metadata_json["outline_prefix_readiness"] = {
        "projection_hash": service.outline_prefix_projection_hash(chapter, scenes)
    }
    with pytest.raises(ValueError, match="metadata_changed"):
        service._verify_materialized_content(chapter, scenes, approved)


@pytest.mark.parametrize("through", [1, 3, 9])
async def test_only_partial_prefix_defers_window_palette_distribution(rig, through):
    result = await _approve(rig, through_chapter=through)
    assert result["passed"] and result["chapter_numbers"] == list(range(1, through + 1))
    validation = rig.validations[0]
    assert validation["strict_story_effects"] is True
    assert validation["strict_field_degeneracy"] is True
    assert validation["enforce_window_effect_distribution"] is (through == 9)
    scope = _workflow(rig).metadata_json["receipt"]["phase_scope"]
    assert scope["window_effect_distribution"] == ("enforced" if through == 9 else "deferred")
    assert scope["full_window"] == [1, 9]
    assert scope["full_window_approved"] is False


async def test_save_exposes_the_actual_durable_phase(rig):
    workflow = SimpleNamespace(id=uuid4(), project_id=rig.project.id, workflow_type=service.WORKFLOW_TYPE,
        scope_type="project", scope_id=rig.project.id, status="running", idempotency_key="old-key",
        metadata_json={}, current_step="validate_prefix")
    rig.store.rows[(WorkflowRunModel, workflow.id)] = workflow
    await service._save(rig.store, workflow, {"phase": "repair", "identity": {"source": "durable"}})
    assert workflow.current_step == "repair"


async def test_real_judge_forwards_project_and_workflow_to_both_provider_requests(monkeypatch):
    from bestseller.services import outline_llm_judge

    project_id, workflow_id = uuid4(), uuid4()
    requests = []

    async def capture(_session, _settings, request):
        requests.append(request)
        payload = (
            {
                "pass": False,
                "overall_score": 0.72,
                "blocking_issues": [
                    {
                        "code": "MISSING_VISIBLE_ACTION",
                        "severity": "critical",
                        "evidence": "No action is shown",
                        "required_fix": "Show an ordinary action",
                    }
                ],
            }
            if len(requests) == 1
            else {
                "adjudication_complete": True,
                "decisions": [
                    {
                        "index": 1,
                        "decision": "CONFIRM",
                        "reason": "The action is absent",
                        "required_fix": "Show an ordinary action",
                    }
                ],
            }
        )
        return LLMCompletionResult(
            content=json.dumps(payload),
            provider="minimax",
            model_name="test-model",
            llm_run_id=uuid4(),
            finish_reason="stop",
        )

    monkeypatch.setattr(outline_llm_judge, "complete_text", capture)
    result = await outline_llm_judge.judge_outline_commercial_readiness(
        object(),
        load_settings(),
        outline_payload={"chapters": []},
        workflow_run_id=workflow_id,
        project_id=project_id,
    )
    assert result.passed is False and len(requests) == 2
    assert all(r.project_id == project_id and r.workflow_run_id == workflow_id for r in requests)


async def test_pending_second_patch_resumes_without_rejudging_or_losing_budget_slot(rig):
    rig.judges[:] = [False, True]
    rig.patches[:] = [{"patches": []}, ExecutionBudgetError("execution_paused")]
    with pytest.raises(ExecutionBudgetError):
        await _approve(rig)
    wf = _workflow(rig)
    assert len(wf.metadata_json["patch_attempts"]) == 2
    assert wf.metadata_json["patch_attempts"][-1]["status"] == "pending"
    assert len(rig.judge_inputs) == 1
    rig.patches[:] = [
        {
            "patches": [
                {
                    "chapter_number": 1,
                    "scope": "chapter",
                    "field": "goal",
                    "value": "Trade the pass for the depot ledger.",
                }
            ]
        }
    ]
    result = await _approve(rig, workflow_run_id=wf.id)
    assert result["passed"] and len(rig.judge_inputs) == 2
    assert len(wf.metadata_json["patch_attempts"]) == 2
    assert len(wf.metadata_json["candidates"]) == 2


@pytest.mark.parametrize("crash_at", ["judge", "patch"])
async def test_internal_operation_identity_survives_paid_return_before_save(
    rig, monkeypatch, crash_at
):
    from bestseller.services.llm import _semantic_operation_namespace, bind_execution_operation

    class DispatcherCrash(BaseException):
        pass

    if crash_at == "patch":
        rig.judges[:] = [False, True]
        rig.patches[:] = [
            {
                "patches": [
                    {
                        "chapter_number": 1,
                        "scope": "chapter",
                        "field": "goal",
                        "value": "Trade a pass for the sealed ledger.",
                    }
                ]
            }
        ]
    judge, patch, save = (
        service.judge_outline_commercial_readiness,
        service.complete_text,
        service._save,
    )
    cached, observed, physical = {}, {"judge": [], "patch": []}, {"judge": 0, "patch": 0}

    async def cached_call(kind, callback, *args, **kwargs):
        binding = _semantic_operation_namespace.get()
        assert binding is not None
        observed[kind].append(binding)
        assert binding[0].startswith(f"outline-prefix:{_workflow(rig).id}:")
        durable = rig.store.commits[-1][str(_workflow(rig).id)]
        assert durable["operation_binding_version"] == "workflow-candidate-v1"
        if binding not in cached:
            physical[kind] += 1
            cached[binding] = await callback(*args, **kwargs)
        return cached[binding]

    async def judge_boundary(*args, **kwargs):
        return await cached_call("judge", judge, *args, **kwargs)

    async def patch_boundary(*args, **kwargs):
        return await cached_call("patch", patch, *args, **kwargs)

    crashed = False

    async def interrupt_before_save(session, workflow, state):
        nonlocal crashed
        returned = (
            "report" in state["candidates"][-1]
            if crash_at == "judge"
            else bool(state["patch_attempts"] and "completion" in state["patch_attempts"][-1])
        )
        if returned and not crashed:
            crashed = True
            raise DispatcherCrash
        await save(session, workflow, state)

    monkeypatch.setattr(service, "judge_outline_commercial_readiness", judge_boundary)
    monkeypatch.setattr(service, "complete_text", patch_boundary)
    monkeypatch.setattr(service, "_save", interrupt_before_save)
    with bind_execution_operation("operator-outer-v1"):
        with pytest.raises(DispatcherCrash):
            await _approve(rig)
        assert _semantic_operation_namespace.get() == ("operator-outer-v1", 0)
        workflow = _workflow(rig)
        result = await _approve(rig, workflow_run_id=workflow.id)
        assert _semantic_operation_namespace.get() == ("operator-outer-v1", 0)
    assert result["passed"]
    assert observed[crash_at][0] == observed[crash_at][1]
    assert physical == (
        {"judge": 1, "patch": 0} if crash_at == "judge" else {"judge": 2, "patch": 1}
    )
    if crash_at == "patch":
        assert observed["patch"][0][0].endswith(":1")
        assert observed["judge"][0] != observed["judge"][-1]


def test_operation_namespaces_separate_workflows_attempts_and_keep_legacy_binding():
    from bestseller.services.llm import _semantic_operation_namespace, bind_execution_operation

    state = {"operation_binding_version": "workflow-candidate-v1"}
    first, second = uuid4(), uuid4()
    identities = []
    with bind_execution_operation("legacy-operator-prefix-v1"):
        with service._prefix_operation({}, first, "judge", "same-candidate"):
            assert _semantic_operation_namespace.get() == ("legacy-operator-prefix-v1", 0)
        for workflow, kind, attempt in [
            (first, "judge", None),
            (second, "judge", None),
            (first, "repair", 1),
            (first, "repair", 2),
        ]:
            with service._prefix_operation(state, workflow, kind, "same-candidate", attempt):
                identities.append(_semantic_operation_namespace.get())
        assert _semantic_operation_namespace.get() == ("legacy-operator-prefix-v1", 0)
    assert len(set(identities)) == 4
    with pytest.raises(ValueError, match="binding_unknown"):
        service._prefix_operation(
            {"operation_binding_version": "unsupported"}, first, "judge", "candidate"
        )


@pytest.mark.asyncio
async def test_continuation_approves_only_unwritten_range_and_binds_prior_context(rig, monkeypatch):
    from bestseller.services import planning_continuity_context
    async def context(*args, **kwargs):
        return {'context_hash': 'current-adopted-body', 'before_chapter': kwargs['before_chapter'],
                'recent_adopted_prose': [{'body': 'A previously adopted, unrelated story.'}]}
    monkeypatch.setattr(planning_continuity_context, 'build_planning_continuity_context', context)
    original = copy.deepcopy(rig.source)
    result = await _approve(rig, from_chapter=2, through_chapter=3)
    assert result['passed'] and result['chapter_numbers'] == [2, 3]
    assert rig.source == original
    assert len(rig.rebase_inputs) == 1
    assert json.loads(rig.rebase_inputs[0].user_prompt)['scope'] == [2, 3]
    workflow = _workflow(rig)
    assert workflow.metadata_json['receipt']['continuity_binding'] == {
        'before_chapter': 2, 'context_hash': 'current-adopted-body'}
    assert [c['chapter_number'] for c in workflow.metadata_json['candidates'][-1]['candidate']['chapters']] == [2, 3]


@pytest.mark.asyncio
async def test_continuation_readiness_cannot_reuse_a_changed_adopted_context(rig, monkeypatch):
    from bestseller.services import planning_continuity_context
    state = {'context_hash': 'old-adopted-body'}
    async def context(*args, **kwargs):
        return copy.deepcopy(state)
    monkeypatch.setattr(planning_continuity_context, 'build_planning_continuity_context', context)
    result = await _approve(rig, from_chapter=2, through_chapter=2)
    state['context_hash'] = 'changed-adopted-body'
    with pytest.raises(ValueError, match='prefix_continuity_stale'):
        await service.verify_outline_prefix_readiness(rig.store, project=rig.project, artifact_id=UUID(result['artifact_id']))


@pytest.mark.asyncio
async def test_hard_repair_resumes_paid_completion_without_rebase_or_resend(rig, monkeypatch):
    from bestseller.services import planning_continuity_context, planning_draft_admission
    async def context(*args, **kwargs):
        return {'context_hash': 'adopted-frontier'}
    monkeypatch.setattr(planning_continuity_context, 'build_planning_continuity_context', context)
    grades = [False, True]
    async def assess(*args, **kwargs):
        allowed = grades.pop(0)
        return {'schema': planning_draft_admission.SCHEMA,
            'hard_admission_policy': planning_draft_admission.HARD_ADMISSION_POLICY,
            'draft_eligible': allowed, 'assessment': {'draft_eligible': allowed,
            'hard_violations': [] if allowed else [{'code':'CONTINUITY_CONFLICT','evidence':'actual old result'}],
            'editorial_notes': []}}
    monkeypatch.setattr(planning_draft_admission, 'assess_planning_draft_admission', assess)
    original_complete = service.complete_text
    sends = []
    async def complete(session, settings, request):
        if request.prompt_template != 'continuation_source_hard_repair':
            return await original_complete(session, settings, request)
        sends.append(request)
        run = SimpleNamespace(id=uuid4(), project_id=rig.project.id, workflow_run_id=request.workflow_run_id,
            provider='minimax',metadata_json={},finish_reason='stop',prompt_template=request.prompt_template,prompt_hash='exact')
        rig.store.rows[(LlmRunModel,run.id)] = run
        return LLMCompletionResult(content=json.dumps({'patches':[{'chapter_number':2,'scope':'chapter',
            'field':'opening_situation','value':'Continue from the actual previous result, without reset.'}]}),
            provider='minimax',model_name='test-model',llm_run_id=run.id,finish_reason='stop')
    monkeypatch.setattr(service,'complete_text',complete)
    original_proof = service._real_run
    fail_once = [True]
    async def proof(*args, **kwargs):
        if kwargs['template']=='continuation_source_hard_repair' and fail_once and fail_once.pop():
            raise ExecutionBudgetError('execution_ledger_unavailable','test')
        return await original_proof(*args, **kwargs)
    monkeypatch.setattr(service,'_real_run',proof)
    with pytest.raises(ExecutionBudgetError):
        await _approve(rig,from_chapter=2,through_chapter=2)
    wf = _workflow(rig)
    assert wf.metadata_json['hard_patch_attempts'][0]['completion']
    result = await _approve(rig,from_chapter=2,through_chapter=2,workflow_run_id=wf.id)
    assert result['passed'] and len(sends)==1 and len(rig.rebase_inputs)==1


def test_batch_items_envelope_is_lossless_and_does_not_mutate_requested_fields(rig):
    items = copy.deepcopy(rig.source["chapters"][1:2])
    items[0]["requested_extra"] = {"keep": ["verbatim"]}
    items[0]["scenes"][0]["methodology_contract"] = {"action_sequence": "keep this exact order"}
    wrapped = {"batch": {"slug": "unrelated-observatory", "source_chapter": 1,
        "source_chapter_id": str(uuid4()), "items": items}}
    before = copy.deepcopy(wrapped)
    normalized, receipt = service._normalize_prefix_envelope(wrapped)
    assert normalized == {"batch_name": "unrelated-observatory", "chapters": items}
    assert receipt["chapters_hash"] == content_hash(items)
    assert receipt["source_payload_hash"] == content_hash(wrapped)
    assert receipt["source_envelope"] == {k: v for k, v in wrapped["batch"].items() if k != "items"}
    assert receipt["normalized_payload_hash"] == content_hash(normalized)
    assert wrapped == before
    normalized["chapters"][0]["requested_extra"]["keep"].append("local edit")
    assert wrapped == before
    native, native_receipt = service._normalize_prefix_envelope(rig.source)
    assert native == rig.source and native_receipt is None


@pytest.mark.parametrize("malformation", [
    "root_chapters", "root_items", "second_wrapper", "nested_batch", "batch_chapters",
    "unknown_requested_field", "missing_items", "nonlist_items", "name_conflict",
])
def test_envelope_adapter_rejects_ambiguous_or_lossy_wrappers(rig, malformation):
    wrapped = {"batch": {"slug": "window", "items": copy.deepcopy(rig.source["chapters"][1:2])}}
    if malformation == "root_chapters": wrapped["chapters"] = []
    elif malformation == "root_items": wrapped["items"] = []
    elif malformation == "second_wrapper": wrapped["result"] = {"items": []}
    elif malformation == "nested_batch": wrapped = {"batch": wrapped}
    elif malformation == "batch_chapters": wrapped["batch"]["chapters"] = []
    elif malformation == "unknown_requested_field": wrapped["batch"]["requested_story_field"] = "must not disappear"
    elif malformation == "missing_items": del wrapped["batch"]["items"]
    elif malformation == "nonlist_items": wrapped["batch"]["items"] = {"chapter_number": 2}
    else: wrapped["batch"]["batch_name"] = "conflicts with slug"
    before = copy.deepcopy(wrapped)
    with pytest.raises(ValueError, match="prefix_candidate_"):
        service._normalize_prefix_envelope(wrapped)
    assert wrapped == before


@pytest.mark.parametrize("change", ["range", "order", "missing_number", "volume"])
def test_wrapped_outline_keeps_original_range_and_volume_guards(rig, change):
    items = copy.deepcopy(rig.source["chapters"][1:3])
    if change == "range": items[0]["chapter_number"] = 1
    elif change == "order": items.reverse()
    elif change == "missing_number": del items[0]["chapter_number"]
    else: items[0]["volume_number"] = 2
    before = copy.deepcopy(items)
    with pytest.raises(ValueError, match="prefix_candidate_(range|volume)_changed"):
        service._validate({"batch": {"items": items}}, project=rig.project,
            state={"chapter_numbers": [2, 3], "binding": {"volume_number": 1, "window": [1, 9]}}, cast={})
    assert items == before and not rig.validations


async def test_paid_wrapped_rebase_resumes_same_completion_without_resend(rig, monkeypatch):
    from bestseller.services import planning_continuity_context
    async def context(*args, **kwargs):
        return {"context_hash": "adopted-observatory-history", "before_chapter": 2}
    monkeypatch.setattr(planning_continuity_context, "build_planning_continuity_context", context)
    original_complete, original_validate = service.complete_text, service._validate
    sent = []
    async def wrapped_complete(session, settings, request):
        result = await original_complete(session, settings, request)
        assert request.prompt_template == "continuation_outline_rebase"
        sent.append(request)
        native = json.loads(result.content)
        result.content = json.dumps({"batch": {"slug": "paid-observatory-batch", "source_chapter": 1,
            "source_chapter_id": str(uuid4()), "items": native["chapters"]}})
        return result
    fail_once = [True]
    def previous_range_failure(payload, **kwargs):
        if "batch" in payload and fail_once and fail_once.pop():
            raise service.OutlinePrefixReadinessError("prefix_candidate_range_changed")
        return original_validate(payload, **kwargs)
    monkeypatch.setattr(service, "complete_text", wrapped_complete)
    monkeypatch.setattr(service, "_validate", previous_range_failure)
    with pytest.raises(ValueError, match="prefix_candidate_range_changed"):
        await _approve(rig, from_chapter=2, through_chapter=2)
    workflow = _workflow(rig)
    paid_completion = copy.deepcopy(workflow.metadata_json["continuation_rebase"]["completion"])
    operation_binding = copy.deepcopy(workflow.metadata_json["identity"])
    async def no_resend(*args, **kwargs):
        pytest.fail("paid rebase must be replayed from its persisted completion")
    monkeypatch.setattr(service, "complete_text", no_resend)
    result = await _approve(rig, from_chapter=2, through_chapter=2, workflow_run_id=workflow.id)
    assert result["draft_eligible"] and result["chapter_numbers"] == [2] and len(sent) == 1
    state = workflow.metadata_json
    assert state["continuation_rebase"]["completion"] == paid_completion
    assert state["identity"] == operation_binding
    receipt = state["continuation_rebase"]["payload_normalization"]
    parsed = json.loads(paid_completion["content"])
    assert receipt["source_payload_hash"] == content_hash(parsed)
    candidate = state["candidates"][-1]["candidate"]
    assert candidate["_meta"]["prefix_payload_normalization"] == receipt
    assert rig.judge_inputs[0]["outline_payload"]["chapters"] == candidate["chapters"]
    assert await _approve(rig, from_chapter=2, through_chapter=2, workflow_run_id=workflow.id) == result
    assert len(sent) == 1


async def test_source_aware_request_uses_shared_literal_authority_and_private_parser(rig, monkeypatch):
    from bestseller.services import planning_continuity_context
    literal = "Chapter 2 scene 1: the archivist must speak through the closed window."
    rig.project.metadata_json["creation_intent_contract"] = {
        "field_sources": {"creative_direction": "explicit"}, "creative_direction": literal}
    rig.project.metadata_json["story_enhancers"] = {"effect_skills": ["comedy_engine"]}
    record = rig.project.metadata_json["outline_commercial_judge_checkpoints"]["window"]["best"]
    record["binding"] = recovery_binding(rig.project.metadata_json, 1, [1, 9])
    record["report"]["outline_repair_dependency_hash"] = record["binding"]["dependency_hash"]
    async def context(*args, **kwargs):
        return {"context_hash": "adopted-archive", "recent_adopted_prose": [{"body": "The window was sealed."}]}
    monkeypatch.setattr(planning_continuity_context, "build_planning_continuity_context", context)
    def global_parser_forbidden(*args, **kwargs):
        pytest.fail("prefix must not use global planner salvage")
    monkeypatch.setattr(service.planner, "_extract_json_payload", global_parser_forbidden)
    rig.judges[:] = [False, True]
    rig.patches[:] = [{"patches": [{"chapter_number": 2, "scope": "chapter", "field": "goal",
        "value": "Compare the dates through the closed window before the courier leaves."}]}]
    result = await _approve(rig, from_chapter=2, through_chapter=2)
    assert result["draft_eligible"]
    request = json.loads(rig.rebase_inputs[0].user_prompt)
    assert literal in request["continuation_input"]["approved_sources"]
    assert "comedy_engine" not in request["continuation_input"]["approved_sources"]
    assert request["adopted_continuity"]["recent_adopted_prose"][0]["body"] == "The window was sealed."
    assert len(rig.patch_inputs) == 1


def test_projection_repairs_can_replace_stale_effect_details_without_touching_other_chapters(rig):
    candidate = copy.deepcopy(rig.source)
    candidate["chapters"][1]["selected_effect_skills"] = {
        "primary": "investigation", "expected_contracts": {"result": "Repeat the already broken seal"}}
    before = copy.deepcopy(candidate)
    patched = service._apply_projection_patches(candidate, [{
        "chapter_number": 2, "scope": "chapter", "field": "selected_effect_skills",
        "value": {"primary": "investigation", "expected_contracts": {"result": "Trace the exposed date"}},
    }])
    assert candidate == before
    assert patched["chapters"][0] == before["chapters"][0]
    assert "already broken" not in json.dumps(patched["chapters"][1])
    assert "selected_effect_skills" in service._projection_patch_protocol(candidate)["fields"]["chapter"]
    with pytest.raises(ValueError, match="no_new_candidate"):
        service._apply_projection_patches(patched, [{"chapter_number": 2, "scope": "chapter",
            "field": "selected_effect_skills", "value": patched["chapters"][1]["selected_effect_skills"]}])


@pytest.mark.parametrize("patches", [
    [{"chapter_number": 30, "scope": "chapter", "field": "selected_effect_skills", "value": {}}],
    [{"chapter_number": 2, "scope": "chapter", "field": "selected_effect_skills", "value": []}],
    [{"chapter_number": 2, "scope": "chapter", "field": "selected_effect_skills", "value": {}, "arbitrary": True}],
    [{"chapter_number": 2, "scope": "chapter", "field": "selected_effect_skills", "value": {}}] * 2,
])
def test_extended_projection_patches_keep_strict_targets_and_shapes(rig, patches):
    before = copy.deepcopy(rig.source)
    with pytest.raises(ValueError):
        service._apply_projection_patches(rig.source, patches)
    assert rig.source == before


async def _legacy_continuation(rig, monkeypatch):
    from bestseller.services import planning_continuity_context
    async def context(*args, **kwargs):
        return {"context_hash": "adopted-earlier-prose", "before_chapter": 2}
    monkeypatch.setattr(planning_continuity_context, "build_planning_continuity_context", context)
    result = await _approve(rig, from_chapter=2, through_chapter=2)
    workflow = await rig.store.get(WorkflowRunModel, result["workflow_run_id"])
    state = workflow.metadata_json
    state["identity"].pop("hard_admission_policy")
    workflow.idempotency_key = content_hash(state["identity"])
    old_admission = {"schema": "planning-draft-admission.v1", "draft_eligible": True,
        "raw_grade": '{"draft_eligible":true,"hard_violations":[],"editorial_notes":["legacy soft pass"]}'}
    state["receipt"]["source_admission"] = copy.deepcopy(old_admission)
    state["candidates"][-1]["source_admission"] = copy.deepcopy(old_admission)
    artifact = await rig.store.get(PlanningArtifactVersionModel, result["artifact_id"])
    artifact.content["_meta"][service.RECEIPT_KEY] = copy.deepcopy(state["receipt"])
    state["artifact_hash"] = content_hash(artifact.content)
    return workflow, artifact


async def _legacy_rejected_producer(rig, monkeypatch):
    workflow, artifact = await _legacy_continuation(rig, monkeypatch)
    state = workflow.metadata_json
    state["identity"].pop("producer_policy")
    workflow.idempotency_key = content_hash(state["identity"])
    workflow.status = "machine_blocked"
    state.update(phase="rejected", reason="source_hard_admission_rejected")
    state["candidates"][-1]["candidate"]["chapters"][0]["goal"] = "UNSELECTED_AI_FUTURE_PLOT"
    state["hard_patch_attempts"] = []
    for _ in range(2):
        run_id = str(uuid4())
        state["hard_patch_attempts"].append({"status": "validated", "completion": {
            "llm_run_id": run_id, "finish_reason": "stop", "provider": "minimax",
            "content": '{"patches":[{"field":"goal","value":"complete"}, {"value":"truncated'}})
        derived = copy.deepcopy(state["candidates"][-1])
        derived["patch_llm_run_id"] = run_id
        state["candidates"].append(derived)
    return workflow, artifact


@pytest.mark.parametrize("new_hard_eligible", [True, False])
async def test_source_aware_policy_migration_carries_paid_slots_and_does_not_reopen_same_policy(
    rig, monkeypatch, new_hard_eligible,
):
    from bestseller.services import planning_draft_admission
    old, artifact = await _legacy_rejected_producer(rig, monkeypatch)
    old_body = copy.deepcopy(old.metadata_json["candidates"][-1]["candidate"])
    old_grade = copy.deepcopy(old.metadata_json["candidates"][-1]["source_admission"])
    old_artifact = copy.deepcopy(artifact.content)
    paid = [copy.deepcopy(a["completion"]) for a in old.metadata_json["hard_patch_attempts"]]
    from bestseller.domain.enums import WorkflowStatus
    old.status = WorkflowStatus.MACHINE_BLOCKED.value
    await rig.store.commit()
    async def admission(*args, **kwargs):
        return {"schema": planning_draft_admission.SCHEMA,
            "hard_admission_policy": planning_draft_admission.HARD_ADMISSION_POLICY,
            "draft_eligible": new_hard_eligible, "assessment": {"draft_eligible": new_hard_eligible,
                "hard_violations": [] if new_hard_eligible else [{"code": "CONTINUITY_CONFLICT"}],
                "editorial_notes": []}}
    monkeypatch.setattr(planning_draft_admission, "assess_planning_draft_admission", admission)
    if new_hard_eligible:
        rig.judges.append(True)
    with pytest.raises(ValueError, match="prefix_producer_policy_reassessment_required"):
        await _approve(rig, from_chapter=2, through_chapter=2, workflow_run_id=old.id)
    result = await service.reassess_outline_prefix_admission(rig.store, load_settings(),
        project=rig.project, workflow_run_id=old.id)
    assert result["draft_eligible"] is new_hard_eligible and result["workflow_run_id"] != str(old.id)
    successor = await rig.store.get(WorkflowRunModel, result["workflow_run_id"])
    assert successor.metadata_json["identity"]["producer_policy"] == service.CONTINUATION_PRODUCER_POLICY
    assert len(successor.metadata_json["hard_patch_attempts"]) == 2
    assert [a["completion"] for a in successor.metadata_json["hard_patch_attempts"]] == paid
    assert successor.metadata_json["reassessment"]["previous_json_lineage"]["status"] == "non_fresh"
    assert old.metadata_json["json_protocol_freshness"]["status"] == "non_fresh"
    assert old.metadata_json["candidates"][-1]["candidate"] == old_body
    assert old.metadata_json["candidates"][-1]["source_admission"] == old_grade
    assert artifact.content == old_artifact and old.status == WorkflowStatus.MACHINE_BLOCKED.value
    new_request = rig.rebase_inputs[-1]
    assert "UNSELECTED_AI_FUTURE_PLOT" not in new_request.user_prompt
    assert "unadopted_future_plan" not in new_request.user_prompt
    assert new_request.prompt_version == service.CONTINUATION_PRODUCER_VERSION
    calls = len(rig.rebase_inputs), len(rig.judge_inputs), len(rig.patch_inputs)
    assert await service.reassess_outline_prefix_admission(rig.store, load_settings(),
        project=rig.project, workflow_run_id=old.id) == result
    assert await service.reassess_outline_prefix_admission(rig.store, load_settings(),
        project=rig.project, workflow_run_id=successor.id) == result
    assert (len(rig.rebase_inputs), len(rig.judge_inputs), len(rig.patch_inputs)) == calls


async def test_completed_salvaged_prefix_is_not_fresh_for_read_only_admission(rig, monkeypatch):
    workflow, artifact = await _legacy_continuation(rig, monkeypatch)
    run_id = workflow.metadata_json["candidates"][-1]["rebase_llm_run_id"]
    attempt = workflow.metadata_json["continuation_rebase"]
    attempt.pop("json_parse_receipt", None)
    attempt["completion"]["content"] = '{"chapters":[{"chapter_number":2,"volume_number":1,"scenes":[{"scene_number":1}],"goal":"unfinished'
    assert str(attempt["completion"]["llm_run_id"]) == run_id
    before = copy.deepcopy((workflow.metadata_json, artifact.content))
    with pytest.raises(ValueError, match="prefix_candidate_protocol_non_fresh"):
        await service.verify_outline_prefix_readiness(rig.store, project=rig.project, artifact_id=artifact.id)
    assert (workflow.metadata_json, artifact.content) == before


async def test_scoped_legacy_reassessment_keeps_original_and_reuses_successor(rig, monkeypatch):
    workflow, artifact = await _legacy_continuation(rig, monkeypatch)
    old_state, old_artifact = copy.deepcopy(workflow.metadata_json), copy.deepcopy(artifact.content)
    rig.judges.append(True)
    result = await service.reassess_outline_prefix_admission(rig.store, load_settings(),
        project=rig.project, workflow_run_id=workflow.id)
    assert result["draft_eligible"] and result["admission_status"] == "current"
    assert result["workflow_run_id"] != str(workflow.id) and result["artifact_id"] != str(artifact.id)
    successor = await rig.store.get(WorkflowRunModel, result["workflow_run_id"])
    assert successor.metadata_json["candidates"][-1]["candidate"] == old_state["candidates"][-1]["candidate"]
    assert successor.metadata_json["continuation_rebase"] == old_state["continuation_rebase"]
    assert len(rig.rebase_inputs) == 1
    assert successor.metadata_json["reassessment"]["previous_receipt"] == old_state["receipt"]
    assert workflow.metadata_json == old_state and artifact.content == old_artifact
    assert result["chapter_numbers"] == [2]
    rebase_count, judge_count = len(rig.rebase_inputs), len(rig.judge_inputs)
    again = await service.reassess_outline_prefix_admission(rig.store, load_settings(),
        project=rig.project, workflow_run_id=workflow.id)
    assert again == result
    assert (len(rig.rebase_inputs), len(rig.judge_inputs)) == (rebase_count, judge_count)
    assert workflow.metadata_json == old_state and artifact.content == old_artifact


async def test_legacy_reassessment_rejects_adopted_range_before_any_paid_work(rig, monkeypatch):
    workflow, artifact = await _legacy_continuation(rig, monkeypatch)
    before = copy.deepcopy((workflow.metadata_json, artifact.content, list(rig.store.rows)))
    count = len(rig.rebase_inputs), len(rig.judge_inputs)
    rig.store.promoted_draft = uuid4()
    with pytest.raises(ValueError, match="prefix_adopted_range_immutable"):
        await service.reassess_outline_prefix_admission(rig.store, load_settings(),
            project=rig.project, workflow_run_id=workflow.id)
    assert (len(rig.rebase_inputs), len(rig.judge_inputs)) == count
    assert (workflow.metadata_json, artifact.content, list(rig.store.rows)) == before


async def test_policy_refresh_archives_raw_receipt_without_forging_current_grade():
    old = {"schema": "planning-draft-admission.v1", "draft_eligible": True,
        "raw_grade": "untouched paid response"}
    candidate = {"source_admission": copy.deepcopy(old), "report": {"pass": False}}
    status = service._admission_refresh(candidate)
    assert status["status"] == "reassessment_required"
    assert candidate["source_admission_history"] == [old]
    assert "source_admission" not in candidate and candidate["report"] == {"pass": False}
    assert service._admission_refresh(candidate) is None
    assert candidate["source_admission_history"] == [old]


@pytest.mark.parametrize("case", ["missing_times", "equal_times", "bad_numeric"])
async def test_reassessment_ambiguous_adoption_stops_before_successor_or_paid_work(rig, monkeypatch, case):
    from datetime import UTC, datetime

    workflow, artifact = await _legacy_continuation(rig, monkeypatch)
    before = copy.deepcopy((workflow.metadata_json, artifact.content, list(rig.store.rows)))
    calls = len(rig.rebase_inputs), len(rig.judge_inputs)
    chapter = SimpleNamespace(id=uuid4(), project_id=rig.project.id, metadata_json={})
    promoted = []
    if case == "bad_numeric":
        chapter.metadata_json["numeric_promotion_debt"] = {"draft_id": "bad"}
    else:
        stamp = None if case == "missing_times" else datetime(2026, 1, 1, tzinfo=UTC)
        promoted = [SimpleNamespace(id=uuid4(), is_current=False, promoted_at=stamp,
            project_id=chapter.project_id, chapter_id=chapter.id, promotion_state="promoted")
            for _ in range(2)]
    async def scalars(statement):
        return [chapter] if statement.column_descriptions[0].get("entity") is ChapterModel else promoted
    async def scalar(statement):
        return promoted[0].id if promoted else None
    monkeypatch.setattr(rig.store, "scalars", scalars)
    monkeypatch.setattr(rig.store, "scalar", scalar)
    with pytest.raises(ValueError, match=r"^materializer_adoption_ambiguous$"):
        await service.reassess_outline_prefix_admission(rig.store, load_settings(),
            project=rig.project, workflow_run_id=workflow.id)
    assert (len(rig.rebase_inputs), len(rig.judge_inputs)) == calls
    assert (workflow.metadata_json, artifact.content, list(rig.store.rows)) == before


async def test_policy_refresh_does_not_invalidate_unrelated_commercial_only_approval(rig, monkeypatch):
    from bestseller.services import planning_draft_admission
    result = await _approve(rig)
    workflow = await rig.store.get(WorkflowRunModel, result["workflow_run_id"])
    before = copy.deepcopy(workflow.metadata_json)
    assert workflow.metadata_json["receipt"]["source_admission"] is None
    monkeypatch.setattr(planning_draft_admission, "HARD_ADMISSION_POLICY", "another-policy")
    verified = await service.verify_outline_prefix_readiness(rig.store, project=rig.project,
        artifact_id=UUID(result["artifact_id"]))
    assert verified == before["receipt"]
    assert await _approve(rig, workflow_run_id=workflow.id) == result
    assert len(rig.judge_inputs) == 1 and workflow.metadata_json == before


@pytest.mark.parametrize("case", [
    "historical_promoted", "missing_times", "equal_times", "bad_numeric", "candidate_only_ok",
])
async def test_adopted_guard_protects_noncurrent_promoted_draft_in_sqlite(tmp_path, case):
    from datetime import UTC, datetime
    from sqlalchemy import JSON, ForeignKeyConstraint, MetaData
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel

    path = tmp_path / "adopted_range_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    metadata = MetaData()
    tables = {}
    for model in (ChapterModel, ChapterDraftVersionModel):
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        for index in list(table.indexes):
            if index.name == "uq_chapter_draft_promoted":
                # Reproduce pre-constraint legacy rows only in this SQLite copy.
                table.indexes.remove(index)
                continue
            if index.dialect_options["sqlite"].get("where") is None:
                index.dialect_options["sqlite"]["where"] = index.dialect_options["postgresql"].get("where")
        tables[model] = table
    project_id, chapter_id = uuid4(), uuid4()
    try:
        async with engine.begin() as conn:
            for table in tables.values():
                await conn.run_sync(table.create)
            await conn.execute(tables[ChapterModel].insert().values(id=chapter_id,
                project_id=project_id, chapter_number=2, chapter_goal="Study a distant comet",
                production_state="ok", metadata={"numeric_promotion_debt": {"draft_id": "bad"}}
                if case == "bad_numeric" else {}))
            if case in {"historical_promoted", "missing_times", "equal_times"}:
                for version in range(1, 2 if case == "historical_promoted" else 3):
                    await conn.execute(tables[ChapterDraftVersionModel].insert().values(
                        id=uuid4(), project_id=project_id, chapter_id=chapter_id, version_no=version,
                        content_md="The observatory received the signal.", is_current=False,
                        promotion_state="promoted", promoted_at=datetime(2026, 1, 1, tzinfo=UTC)
                        if case == "equal_times" else None))
            await conn.execute(tables[ChapterDraftVersionModel].insert().values(
                id=uuid4(), project_id=project_id, chapter_id=chapter_id, version_no=9,
                content_md="Working candidate preserved.", is_current=True, promotion_state="candidate"))
        async with async_sessionmaker(engine)() as session:
            if case == "candidate_only_ok":
                await service._assert_unadopted_range(session, project_id=project_id, numbers=[2])
            else:
                code = ("prefix_adopted_range_immutable" if case == "historical_promoted"
                    else "materializer_adoption_ambiguous")
                with pytest.raises(ValueError, match=f"^{code}$"):
                    await service._assert_unadopted_range(session, project_id=project_id, numbers=[2])
            await service._assert_unadopted_range(session, project_id=project_id, numbers=[3])
    finally:
        await engine.dispose()


@pytest.mark.parametrize("eligible", [True, False])
async def test_hard_policy_only_failed_migration_reuses_paid_v3_rebase_candidate_and_patch_slots(rig, monkeypatch, eligible):
    from bestseller.services import planning_draft_admission
    workflow, artifact = await _legacy_continuation(rig, monkeypatch)
    state = workflow.metadata_json
    state.pop("artifact_id")
    state.pop("receipt")
    workflow.status = "failed"
    state.update(phase="source_hard_admission", failure={"code": "admission_projection_conflict_unexplained"})
    current = state["candidates"][-1]
    current.pop("source_admission")
    current.pop("report")
    paid = [{"status": "validated", "completion": {"content": "paid old patch " + str(i)}} for i in range(2)]
    state["hard_patch_attempts"] = copy.deepcopy(paid)
    rebase = copy.deepcopy(state["continuation_rebase"])
    body, artifact_before = copy.deepcopy(current["candidate"]), copy.deepcopy(artifact.content)
    producer = state["identity"]["producer_policy"]
    old_workflow_id = workflow.id
    await rig.store.commit()
    calls = []
    async def admission(*args, **kwargs):
        assert kwargs["candidate_payload"] == body
        calls.append(kwargs)
        return {"schema": planning_draft_admission.SCHEMA,
            "hard_admission_policy": planning_draft_admission.HARD_ADMISSION_POLICY,
            "draft_eligible": eligible, "assessment": {"draft_eligible": eligible, "hard_violations": [], "editorial_notes": []}}
    monkeypatch.setattr(planning_draft_admission, "assess_planning_draft_admission", admission)
    if eligible:
        rig.judges.append(True)
    result = await service.reassess_outline_prefix_admission(rig.store, load_settings(),
        project=rig.project, workflow_run_id=workflow.id)
    assert result["workflow_run_id"] == str(old_workflow_id)
    assert result["draft_eligible"] is eligible
    assert workflow.metadata_json["continuation_rebase"] == rebase
    assert workflow.metadata_json["continuation_rebased"] is True
    assert workflow.metadata_json["identity"]["producer_policy"] == producer
    assert workflow.metadata_json["hard_patch_attempts"] == paid
    assert workflow.metadata_json["candidates"][-1]["candidate"] == body
    assert artifact.content == artifact_before and len(rig.rebase_inputs) == 1 and len(calls) == 1
    again = await service.reassess_outline_prefix_admission(rig.store, load_settings(),
        project=rig.project, workflow_run_id=workflow.id)
    assert again == result and len(calls) == 1 and len(rig.rebase_inputs) == 1


async def test_exhausted_admission_protocol_machineblocks_and_resume_never_rebases_or_patches(rig, monkeypatch):
    from bestseller.services import planning_continuity_context, planning_draft_admission
    async def context(*args, **kwargs):
        return {"context_hash": "unchanged-adopted-source", "before_chapter": 2}
    monkeypatch.setattr(planning_continuity_context, "build_planning_continuity_context", context)
    calls = []
    async def admission(*args, **kwargs):
        protocol = kwargs["protocol_state"]
        if not protocol:
            protocol.update(hard_admission_policy=planning_draft_admission.HARD_ADMISSION_POLICY,
                status="protocol_exhausted", attempts=[{"raw": "malformed paid grade", "error": "invalid"}] * 2)
        calls.append(copy.deepcopy(protocol))
        await kwargs["persist_protocol"]()
        raise planning_draft_admission.PlanningDraftAdmissionProtocolError(protocol)
    monkeypatch.setattr(planning_draft_admission, "assess_planning_draft_admission", admission)
    result = await _approve(rig, from_chapter=2, through_chapter=2)
    workflow = await rig.store.get(WorkflowRunModel, result["workflow_run_id"])
    assert workflow.status == "machine_blocked"
    assert result["reason"] == "admission_protocol_exhausted" and result["admission_status"] == "protocol_unknown"
    assert not result["draft_eligible"] and not rig.imports and not rig.patch_inputs and not rig.judge_inputs
    assert len(rig.rebase_inputs) == 1
    assert len(workflow.metadata_json["candidates"]) == 2
    assert "source_admission" not in workflow.metadata_json["candidates"][-1]
    again = await _approve(rig, from_chapter=2, through_chapter=2, workflow_run_id=workflow.id)
    assert again == result and calls[-1] == calls[0] and len(rig.rebase_inputs) == 1


async def _failed_after_authorized_receipt(rig, monkeypatch):
    from bestseller.services import planning_continuity_context, planning_draft_admission
    async def context(*args, **kwargs):
        return {"context_hash": "fixed-adopted-context", "before_chapter": 2}
    monkeypatch.setattr(planning_continuity_context, "build_planning_continuity_context", context)
    rig.judges[:] = [False]
    result = await _approve(rig, from_chapter=2, through_chapter=2, max_repair_rounds=0,
        allow_editorial_debt=True, requested_by="original-human-operator")
    assert result["draft_eligible"] and not result["passed"]
    previous = await rig.store.get(WorkflowRunModel, result["workflow_run_id"])
    previous.metadata_json["identity"]["hard_admission_policy"] = "previous-hard-policy"
    previous.idempotency_key = content_hash(previous.metadata_json["identity"])
    previous_receipt = copy.deepcopy(previous.metadata_json["receipt"])
    current = copy.deepcopy(previous)
    current.id, current.status = uuid4(), "machine_blocked"
    state = current.metadata_json
    state["identity"]["hard_admission_policy"] = planning_draft_admission.HARD_ADMISSION_POLICY
    # Emulate the real historical defect: metadata migrated but old key was retained.
    current.idempotency_key = "historical-unsynced-key"
    for key in ("approval_policy", "editorial_quality_debt", "receipt", "artifact_id", "artifact_hash"):
        state.pop(key, None)
    state.update(phase="rejected", reason="prefix_judge_rejected", reassessment={
        "previous_receipt": previous_receipt, "previous_workflow_run_id": str(previous.id)})
    cached_report = state["candidates"][-1]["report"]
    old_run = await rig.store.get(LlmRunModel, cached_report["llm_run_id"])
    run = copy.deepcopy(old_run)
    run.id, run.workflow_run_id = uuid4(), current.id
    rig.store.rows[(LlmRunModel, run.id)] = run
    cached_report.update(overall_score=0.62, llm_run_id=str(run.id))
    state["final_report"] = copy.deepcopy(cached_report)
    state["candidates"][-1]["source_admission"]["raw_grade"] = "unchanged current hard PASS"
    state["hard_patch_attempts"] = [{"status": "validated", "completion": {"content": "old hard patch"}}] * 2
    state["patch_attempts"] = [{"status": "validated", "consumed": True,
        "completion": {"content": "old editorial patch"}}] * 2
    rig.store.rows[(WorkflowRunModel, current.id)] = current
    return current, previous


async def test_permission_is_persisted_before_first_paid_failure_without_a_debt_result(rig, monkeypatch):
    from bestseller.services import planning_continuity_context
    async def context(*args, **kwargs): return {"context_hash": "source", "before_chapter": 2}
    monkeypatch.setattr(planning_continuity_context, "build_planning_continuity_context", context)
    async def stop(*args, **kwargs): raise RuntimeError("first transport failed")
    monkeypatch.setattr(service, "complete_text", stop)
    with pytest.raises(RuntimeError, match="first transport failed"):
        await _approve(rig, from_chapter=2, allow_editorial_debt=True, requested_by="authorized-human")
    state = _workflow(rig).metadata_json
    assert state["approval_policy"]["allow_editorial_debt"] is True
    assert state["approval_policy"]["authorization"]["actor"] == "authorized-human"
    assert state["approval_policy"]["authorization"]["source"] == "explicit_call"
    assert "editorial_quality_debt" not in state


@pytest.mark.parametrize("mode", ["verified_previous_receipt", "explicit_migration"])
async def test_permission_recovery_publishes_cached_hard_pass_without_model_calls_or_candidate_changes(rig, monkeypatch, mode):
    from bestseller.services import planning_draft_admission
    current, previous = await _failed_after_authorized_receipt(rig, monkeypatch)
    old_previous = copy.deepcopy(previous.metadata_json)
    old_candidate_bodies = [copy.deepcopy(c["candidate"]) for c in current.metadata_json["candidates"]]
    old_report = copy.deepcopy(current.metadata_json["candidates"][-1]["report"])
    old_grade = copy.deepcopy(current.metadata_json["candidates"][-1]["source_admission"])
    old_rebase = copy.deepcopy(current.metadata_json["continuation_rebase"])
    old_slots = copy.deepcopy((current.metadata_json["patch_attempts"], current.metadata_json["hard_patch_attempts"]))
    calls = len(rig.rebase_inputs), len(rig.judge_inputs), len(rig.patch_inputs)
    async def forbidden(*args, **kwargs): pytest.fail("cached decision cannot call any model or admission")
    monkeypatch.setattr(service, "complete_text", forbidden)
    monkeypatch.setattr(service, "judge_outline_commercial_readiness", forbidden)
    monkeypatch.setattr(planning_draft_admission, "assess_planning_draft_admission", forbidden)
    args = {} if mode == "verified_previous_receipt" else {"allow_editorial_debt": True,
        "authorization_actor": "original-human-operator", "authorization_reason": "Original scoped editorial-debt permission reaffirmed"}
    result = await service.reassess_outline_prefix_admission(rig.store, load_settings(),
        project=rig.project, workflow_run_id=current.id, decision_only=True, **args)
    assert result["draft_eligible"] and not result["passed"] and result["workflow_run_id"] == str(current.id)
    assert result["report"] == old_report and result["report"]["overall_score"] == 0.62
    state = current.metadata_json
    assert state["approval_policy"]["allow_editorial_debt"] is True
    assert state["approval_policy"]["authorization"]["source"] == mode
    assert state["approval_policy"]["authorization"]["actor"] == "original-human-operator"
    assert state["receipt"]["approval_policy"] == state["approval_policy"]
    assert [c["candidate"] for c in state["candidates"]] == old_candidate_bodies
    assert state["candidates"][-1]["source_admission"] == old_grade
    assert state["continuation_rebase"] == old_rebase
    assert (state["patch_attempts"], state["hard_patch_attempts"]) == old_slots
    assert previous.metadata_json == old_previous
    assert current.idempotency_key == content_hash(state["identity"])
    assert state["idempotency_key_history"][-1]["previous_key"] == "historical-unsynced-key"
    assert calls == (len(rig.rebase_inputs), len(rig.judge_inputs), len(rig.patch_inputs))
    before = copy.deepcopy(state)
    assert await service.reassess_outline_prefix_admission(rig.store, load_settings(),
        project=rig.project, workflow_run_id=current.id, decision_only=True) == result
    assert current.metadata_json == before


@pytest.mark.parametrize("bad", ["copied_receipt", "wrong_project", "not_completed", "wrong_range", "artifact_unapproved", "artifact_receipt"])
async def test_legacy_permission_requires_actual_matching_completed_receipt_and_artifact(rig, monkeypatch, bad):
    current, previous = await _failed_after_authorized_receipt(rig, monkeypatch)
    if bad == "copied_receipt": current.metadata_json["reassessment"]["previous_receipt"]["approval_basis"] = "source_hard_admission_with_editorial_debt"; previous.metadata_json["receipt"]["judge_report_hash"] = "changed"
    elif bad == "wrong_project": previous.project_id = uuid4()
    elif bad == "not_completed": previous.status = "failed"
    elif bad == "wrong_range": current.metadata_json["reassessment"]["previous_receipt"]["chapter_numbers"] = [3]
    else:
        artifact = await rig.store.get(PlanningArtifactVersionModel, previous.metadata_json["artifact_id"])
        if bad == "artifact_unapproved": artifact.status = "pending"
        else: artifact.content["_meta"][service.RECEIPT_KEY]["judge_report_hash"] = "changed"
    before = copy.deepcopy(current.metadata_json)
    calls = len(rig.rebase_inputs), len(rig.judge_inputs), len(rig.patch_inputs)
    with pytest.raises(ValueError, match="legacy_approval_authorization_invalid"):
        await service.reassess_outline_prefix_admission(rig.store, load_settings(),
            project=rig.project, workflow_run_id=current.id, decision_only=True)
    assert current.metadata_json == before
    assert calls == (len(rig.rebase_inputs), len(rig.judge_inputs), len(rig.patch_inputs))


async def test_debt_result_alone_never_grants_permission_and_explicit_grant_requires_actor_reason(rig, monkeypatch):
    current, previous = await _failed_after_authorized_receipt(rig, monkeypatch)
    current.metadata_json.pop("reassessment")
    current.metadata_json["editorial_quality_debt"] = {"status": "open", "allow_editorial_debt": True}
    with pytest.raises(ValueError, match="approval_authorization_missing"):
        await service.reassess_outline_prefix_admission(rig.store, load_settings(), project=rig.project,
            workflow_run_id=current.id, decision_only=True, allow_editorial_debt=True)
    result = await service.reassess_outline_prefix_admission(rig.store, load_settings(), project=rig.project,
        workflow_run_id=current.id, decision_only=True)
    assert not result["draft_eligible"]
    assert current.metadata_json["approval_policy"]["allow_editorial_debt"] is False
    assert previous.status == "completed"


async def test_same_policy_ordinary_dispatch_recovers_legacy_key_and_never_opens_attempts(rig, monkeypatch):
    current, _ = await _failed_after_authorized_receipt(rig, monkeypatch)
    state = current.metadata_json
    state["hard_patch_attempts"] = [{"status": "validated", "completion": {"content": "spent"}}] * 2
    state["candidates"][-1]["source_admission"]["draft_eligible"] = False
    before = copy.deepcopy(state)
    count = sum(model is WorkflowRunModel for model, _ in rig.store.rows)
    calls = len(rig.rebase_inputs), len(rig.judge_inputs), len(rig.patch_inputs)
    result = await _approve(rig, from_chapter=2, through_chapter=2, max_repair_rounds=0)
    assert result["workflow_run_id"] == str(current.id) and not result["draft_eligible"]
    assert sum(model is WorkflowRunModel for model, _ in rig.store.rows) == count
    assert current.idempotency_key == content_hash(state["identity"])
    assert current.metadata_json["hard_patch_attempts"] == before["hard_patch_attempts"]
    assert [c["candidate"] for c in current.metadata_json["candidates"]] == [c["candidate"] for c in before["candidates"]]
    assert calls == (len(rig.rebase_inputs), len(rig.judge_inputs), len(rig.patch_inputs))


@pytest.mark.parametrize("collision", ["key_active", "empty_key_active", "exact_identity_duplicate"])
async def test_identity_or_new_key_collision_fails_closed_without_overwrite_or_paid_work(rig, monkeypatch, collision):
    current, _ = await _failed_after_authorized_receipt(rig, monkeypatch)
    other = copy.deepcopy(current)
    other.id, other.status = uuid4(), "running"
    if collision == "exact_identity_duplicate": other.idempotency_key = "other-old-key"
    else:
        other.idempotency_key = content_hash(current.metadata_json["identity"])
        other.metadata_json = {} if collision == "empty_key_active" else {"identity": {"unrelated": True}}
    rig.store.rows[(WorkflowRunModel, other.id)] = other
    before = copy.deepcopy((current.metadata_json, other.metadata_json, current.idempotency_key, other.idempotency_key))
    calls = len(rig.rebase_inputs), len(rig.judge_inputs), len(rig.patch_inputs)
    with pytest.raises(ValueError, match="prefix_workflow_(identity_ambiguous|key_collision)"):
        await service.reassess_outline_prefix_admission(rig.store, load_settings(), project=rig.project,
            workflow_run_id=current.id, decision_only=True)
    assert (current.metadata_json, other.metadata_json, current.idempotency_key, other.idempotency_key) == before
    assert calls == (len(rig.rebase_inputs), len(rig.judge_inputs), len(rig.patch_inputs))


async def test_typed_admission_block_uses_specific_code_without_rewrite(rig, monkeypatch):
    from bestseller.services import planning_continuity_context, planning_draft_admission
    async def context(*args, **kwargs): return {"context_hash": "adopted", "before_chapter": 2}
    monkeypatch.setattr(planning_continuity_context, "build_planning_continuity_context", context)
    async def blocked(*args, **kwargs):
        exc = planning_draft_admission.PlanningDraftAdmissionProtocolError({"attempts": []})
        exc.code = "admission_input_budget_exceeded"
        raise exc
    monkeypatch.setattr(planning_draft_admission, "assess_planning_draft_admission", blocked)
    result = await _approve(rig, from_chapter=2, through_chapter=2)
    workflow = await rig.store.get(WorkflowRunModel, result["workflow_run_id"])
    assert result["reason"] == "admission_input_budget_exceeded" and not result["draft_eligible"]
    assert workflow.status == "machine_blocked" and workflow.metadata_json["admission_status"] == "protocol_unknown"
    assert not rig.patch_inputs and not rig.judge_inputs and not rig.imports


@pytest.mark.parametrize("missing", ["hard", "commercial", "rebase", "stale_commercial"])
async def test_decision_only_never_pays_to_replace_missing_or_stale_proofs(rig, monkeypatch, missing):
    from bestseller.services import planning_draft_admission
    current, _ = await _failed_after_authorized_receipt(rig, monkeypatch)
    state = current.metadata_json
    if missing == "hard": state["candidates"][-1].pop("source_admission")
    elif missing == "commercial": state["candidates"][-1].pop("report")
    elif missing == "rebase": state["continuation_rebased"] = False
    else: state["candidates"][-1]["report"]["graded_outline_hash"] = "stale-candidate"
    async def forbidden(*args, **kwargs): pytest.fail("decision_only must never replace a missing paid proof")
    monkeypatch.setattr(service, "complete_text", forbidden)
    monkeypatch.setattr(service, "judge_outline_commercial_readiness", forbidden)
    monkeypatch.setattr(planning_draft_admission, "assess_planning_draft_admission", forbidden)
    with pytest.raises(ValueError, match="prefix_decision_"):
        await service.reassess_outline_prefix_admission(rig.store, load_settings(), project=rig.project,
            workflow_run_id=current.id, decision_only=True)
    assert len(rig.imports) == 1  # Only the historical artifact exists.


@pytest.fixture
async def sqlite_identity_store(tmp_path):
    from sqlalchemy import JSON, ForeignKeyConstraint, MetaData
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    path = tmp_path / "prefix_workflow_identity_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    table = WorkflowRunModel.__table__.to_metadata(MetaData())
    for column in table.columns:
        if isinstance(column.type, JSONB): column.type = JSON()
    for constraint in list(table.constraints):
        if isinstance(constraint, ForeignKeyConstraint): table.constraints.remove(constraint)
    async with engine.begin() as connection:
        await connection.run_sync(table.create)
    class Session:
        def __init__(self, actual): self.actual = actual
        def __getattr__(self, name): return getattr(self.actual, name)
        async def get(self, model, identity, **kwargs):
            if model is service.ProjectModel:
                assert kwargs.get("with_for_update") is True
                return SimpleNamespace(id=identity)  # SQLite cannot exercise PostgreSQL row locks.
            return await self.actual.get(model, identity, **kwargs)
    yield async_sessionmaker(engine, expire_on_commit=False), Session
    await engine.dispose()


async def test_sqlite_scoped_identity_fallback_syncs_key_and_metadata_without_resetting_attempts(sqlite_identity_store):
    factory, wrap = sqlite_identity_store
    project_id = uuid4()
    identity = {"project_id": str(project_id), "policy": "same-policy", "source": "same-source"}
    metadata = {"identity": identity, "hard_patch_attempts": [{"raw": "first paid result"}, {"raw": "second paid result"}],
        "candidates": [{"candidate": {"body": "all old body bytes"}}], "phase": "rejected"}
    row_id = uuid4()
    async with factory() as session:
        session.add(WorkflowRunModel(id=row_id, project_id=project_id, workflow_type=service.WORKFLOW_TYPE,
            scope_type="project", scope_id=project_id, status="machine_blocked", metadata_json=metadata,
            idempotency_key="old-policy-key", requested_by="operator"))
        # Same metadata in a different scope is never selected.
        session.add(WorkflowRunModel(id=uuid4(), project_id=project_id, workflow_type=service.WORKFLOW_TYPE,
            scope_type="chapter", scope_id=uuid4(), status="running", metadata_json=metadata,
            idempotency_key="other-scope-key", requested_by="operator"))
        await session.commit()
    async with factory() as session:
        row = await service._find_current_workflow(wrap(session), project_id=project_id,
            identity=dict(reversed(list(identity.items()))))
        assert row.id == row_id
        await service._save(wrap(session), row, copy.deepcopy(row.metadata_json))
    async with factory() as session:
        row = await session.get(WorkflowRunModel, row_id)
        assert row.idempotency_key == content_hash(identity)
        assert row.metadata_json["hard_patch_attempts"] == metadata["hard_patch_attempts"]
        assert row.metadata_json["candidates"] == metadata["candidates"]
        assert row.metadata_json["idempotency_key_history"] == [{"previous_key": "old-policy-key",
            "current_key": content_hash(identity), "previous_identity": identity, "reason": "identity_binding_sync"}]
        assert (await service._find_current_workflow(wrap(session), project_id=project_id, identity=identity)).id == row_id


@pytest.mark.parametrize("case", ["duplicate_identity", "cross_project_key", "active_empty_key"])
async def test_sqlite_duplicate_or_active_key_never_silently_selects_latest(sqlite_identity_store, case):
    factory, wrap = sqlite_identity_store
    project_id = uuid4()
    identity = {"project_id": str(project_id), "source": "exact"}
    async with factory() as session:
        session.add(WorkflowRunModel(id=uuid4(), project_id=project_id, workflow_type=service.WORKFLOW_TYPE,
            scope_type="project", scope_id=project_id, status="machine_blocked", metadata_json={"identity": identity},
            idempotency_key="historical-key", requested_by="operator"))
        other_project = uuid4() if case == "cross_project_key" else project_id
        session.add(WorkflowRunModel(id=uuid4(), project_id=other_project, workflow_type=service.WORKFLOW_TYPE,
            scope_type="project", scope_id=other_project, status="running",
            metadata_json={"identity": identity} if case == "duplicate_identity" else {},
            idempotency_key="duplicate-old-key" if case == "duplicate_identity" else content_hash(identity), requested_by="operator"))
        await session.commit()
    async with factory() as session:
        with pytest.raises(ValueError, match="prefix_workflow_(identity_ambiguous|key_collision)"):
            await service._find_current_workflow(wrap(session), project_id=project_id, identity=identity)
