"""Offline SQLite ledger proofs; transport, semantic verdicts and source reads are fixtures."""
import copy
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import (
    CanonFactModel, ChapterDraftVersionModel, ExecutionAttemptModel, ExecutionBudgetModel,
    ExecutionOperationModel, LlmRunModel, ProjectModel, TimelineEventModel, WorkflowRunModel,
)
from bestseller.services import llm, planning_draft_admission as admission
from bestseller.services.budget import bind_execution_scope
from test_execution_budget_core import gateway_settings

pytestmark = pytest.mark.unit
PASS = {"draft_eligible": True, "hard_violations": [], "editorial_notes": ["Pace remains an editorial concern."]}
FAIL = {"draft_eligible": False, "hard_violations": [{
    "code": "ABILITY_RULE_CONFLICT", "path": "chapters[0].scenes[0]",
    "evidence": "The power fires without touching the tool.",
    "source_requirement": "approved_world_spec: touching the tool is required.",
    "source_refs": ["/approved_world_spec/requirement"],
}], "editorial_notes": []}


def grade_payload(candidate, verdict=PASS):
    verdict = copy.deepcopy(verdict)
    for violation in verdict["hard_violations"]:
        violation.setdefault("source_refs", ["candidate:" + violation["path"]])
    return {**verdict, "projection_checks": [
        {**target, "status": "consistent", "explanation": "Full projection and ordered transitions checked."}
        for target in admission.projection_audit_targets(candidate)]}


@pytest.fixture
async def harness(tmp_path, monkeypatch):
    path = tmp_path / "planning_draft_admission_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    table = LlmRunModel.__table__.to_metadata(MetaData())
    for col in table.columns:
        if isinstance(col.type, JSONB):
            col.type = JSON()
    for constraint in list(table.constraints):
        if isinstance(constraint, ForeignKeyConstraint):
            table.constraints.remove(constraint)
    async with engine.begin() as connection:
        for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel):
            await connection.run_sync(model.__table__.create)
        await connection.run_sync(table.create)
    project = ProjectModel(id=uuid4(), slug="admission-fixture", title="An unrelated story",
        genre="fantasy", language="zh-CN", target_chapters=40, metadata_json={
            "book_design_snapshot_hash": "a" * 64,
            "genre_intent_contract": {"source": "user", "tag_source": "user",
                "user_tags": ["EXPLICIT_COMEDY", "EXPLICIT_HYPE"], "tags": ["TAXONOMY_DEFAULT"],
                "default_tags": ["TAXONOMY_DEFAULT"], "prompt_pack_key": "xuanhuan-power-fantasy"},
            "story_enhancers": {"effect_skills": ["EXPLICIT_COMEDY_EFFECT"]},
            "creation_intent_contract": {
                "field_sources": {"genre_intent": "explicit", "story_enhancers": "explicit"},
                "genre_intent": {"tag_source": "user", "user_tags": ["EXPLICIT_COMEDY", "EXPLICIT_HYPE"]},
                "story_enhancers": {"effect_skills": ["comedy_engine"]},
            },
        })
    workflow = SimpleNamespace(id=uuid4(), project_id=project.id)
    sources = {kind: SimpleNamespace(id=uuid4(), project_id=project.id, artifact_type=kind.value,
        version_no=1, status="approved", content={"requirement": text}) for kind, text in (
            (ArtifactType.BOOK_SPEC, "Immediate source promise; no forced humiliation."),
            (ArtifactType.WORLD_SPEC, "Touching the tool is required; ordinary refinement is allowed."),
            (ArtifactType.CAST_SPEC, "Mira is cautious but may choose a personable nonoptimal action."),
        )}
    sources[ArtifactType.WORLD_SPEC].content["_meta"] = {"source_snapshot_hash": "a" * 64}
    h = SimpleNamespace(project=project, workflow=workflow, sources=sources, sent=[], raw=json.dumps(PASS),
        history=None, settings=gateway_settings(calls=10))
    h.settings.llm.critic = h.settings.llm.planner.model_copy(update={
        "model": "openai/MiniMax-M3", "api_base": "https://api.minimaxi.com/v1"})

    async def source_read(session, *, project_id, artifact_type):
        assert project_id == project.id
        return sources[artifact_type]

    async def transport(request, role):
        h.sent.append(request)
        assert role.model == "openai/MiniMax-M3"
        raw, finish = h.replies.pop(0) if getattr(h, "replies", None) else (h.raw, "stop")
        return raw, 10, 5, finish, None, None

    async def no_override(*args):
        return None

    class Session:
        def __init__(self, actual):
            self.actual = actual
        def __getattr__(self, name):
            return getattr(self.actual, name)
        async def get(self, model, key, **kwargs):
            if model is WorkflowRunModel:
                return workflow if key == workflow.id else None
            return await self.actual.get(model, key, **kwargs)
        async def scalar(self, statement, **kwargs):
            entity = statement.column_descriptions[0].get("entity")
            if entity is ProjectModel:
                # This source fixture has a detached project, while the real
                # gateway now re-reads its model-selection metadata.
                assert list(statement.selected_columns)[0].name == "metadata"
                return copy.deepcopy(project.metadata_json)
            if entity in (CanonFactModel, TimelineEventModel, ChapterDraftVersionModel):
                return uuid4() if h.history is entity else None
            return await self.actual.scalar(statement, **kwargs)

    monkeypatch.setattr(admission, "get_latest_approved_planning_artifact", source_read)
    monkeypatch.setattr(llm, "_call_litellm", transport)
    monkeypatch.setattr(llm, "_resolve_project_model_override", no_override)
    h.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    h.wrap = Session
    h.kwargs = lambda: dict(project=project, workflow_run_id=workflow.id,
        candidate_payload={"chapters": [{"chapter_number": 1, "goal": "A modest first result",
            "scenes": [{"scene_number": 1, "action": "Mira touches the tool and tests it."}]}]},
        book_spec=copy.deepcopy(sources[ArtifactType.BOOK_SPEC].content),
        world_spec=copy.deepcopy(sources[ArtifactType.WORLD_SPEC].content),
        cast_spec=copy.deepcopy(sources[ArtifactType.CAST_SPEC].content))
    h.commercial = {"pass": False, "overall_score": 0.62, "blocking_issues": [{
        "code": "EDITORIAL_PREFERENCE", "required_fix": "Invent another power to improve market appeal."}],
        "full_diagnostic": "Preserved rejected commercial judgement. " * 500}
    h.raw = json.dumps(grade_payload(h.kwargs()["candidate_payload"]), ensure_ascii=False)
    try:
        yield h
    finally:
        await engine.dispose()


async def assess(h, *, rollback=False):
    async with h.session_factory() as actual:
        with bind_execution_scope("audit:admission-fixture"):
            result = await admission.assess_planning_draft_admission(h.wrap(actual), h.settings,
                **h.kwargs(), commercial_report=h.commercial)
        await actual.rollback() if rollback else await actual.commit()
        return result


@pytest.mark.parametrize("genre,language", [("fantasy", "zh-CN"), ("romance", "en")])
async def test_actual_proof_preserves_false_commercial_and_explicit_effects(harness, genre, language):
    h = harness
    h.project.genre, h.project.language = genre, language
    before = copy.deepcopy(h.commercial)
    receipt = await assess(h)
    assert receipt["draft_eligible"] is True
    assert receipt["raw_grade"] == h.raw
    assert receipt["commercial_report"] == before == h.commercial
    assert receipt["commercial_report"]["pass"] is False
    payload = json.loads(h.sent[0].user_prompt)["input"]
    assert receipt["schema"] == admission.SCHEMA == "planning-draft-admission.v3"
    assert receipt["hard_admission_policy"] == payload["hard_admission_policy"] == admission.HARD_ADMISSION_POLICY
    assert payload["projection_audit_targets"] == admission.projection_audit_targets(payload["candidate_payload"])
    selected = payload["approved_creation_contracts"]["genre_intent_contract"]
    assert selected["tags"] == ["EXPLICIT_COMEDY", "EXPLICIT_HYPE"]
    assert "default_tags" not in selected and "prompt_pack_key" not in selected
    assert "comedy_engine" in json.dumps(payload["approved_creation_contracts"])
    assert "EXPLICIT_COMEDY_EFFECT" not in json.dumps(payload["approved_creation_contracts"])
    assert "不是商业评分员" in h.sent[0].system_prompt
    assert "非最优" in h.sent[0].system_prompt
    async with h.session_factory() as actual:
        await admission.verify_planning_draft_admission(h.wrap(actual), **h.kwargs(), receipt=receipt)


async def test_real_gateway_rollback_replay_reuses_paid_result_and_original_workflow(harness):
    first = await assess(harness, rollback=True)
    second = await assess(harness)
    assert first["graded_input_hash"] == second["graded_input_hash"]
    assert len(harness.sent) == 1
    async with harness.session_factory() as session:
        run = await session.get(LlmRunModel, UUID(second["llm_run_id"]))
        assert run.workflow_run_id == harness.workflow.id


async def test_actual_hard_rejection_is_retained_and_cannot_be_admitted(harness):
    h = harness
    h.raw = json.dumps(grade_payload(h.kwargs()["candidate_payload"], FAIL))
    receipt = await assess(h)
    assert receipt["draft_eligible"] is False and receipt["raw_grade"] == h.raw
    async with h.session_factory() as actual:
        with pytest.raises(ValueError, match="admission_hard_rejected"):
            await admission.verify_planning_draft_admission(h.wrap(actual), **h.kwargs(), receipt=receipt)


@pytest.mark.parametrize("raw", [
    '{"draft_eligible":"true","hard_violations":[],"editorial_notes":[]}',
    '{"draft_eligible":true,"hard_violations":[],"editorial_notes":[],"overall_score":0.9}',
    '{"draft_eligible":true,"draft_eligible":false,"hard_violations":[],"editorial_notes":[]}',
    '```json\n{"draft_eligible":true,"hard_violations":[],"editorial_notes":[]}\n```',
    json.dumps({**FAIL, "draft_eligible": True}),
    json.dumps({**PASS, "draft_eligible": False}),
])
async def test_strict_original_json_never_forges_a_grade(harness, raw):
    harness.raw = raw
    with pytest.raises(ValueError):
        await assess(harness)


@pytest.mark.parametrize("field,value", [
    ("graded_input_hash", "wrong"), ("effective_input_hash", "wrong"),
    ("candidate_hash", "wrong"), ("foundation_hashes", {}), ("draft_eligible", False),
    ("raw_grade", json.dumps({**PASS, "editorial_notes": []})),
    ("commercial_report", {"pass": True}), ("llm_run_id", str(uuid4())),
])
async def test_receipt_tampering_cannot_pass(harness, field, value):
    receipt = await assess(harness)
    receipt[field] = value
    async with harness.session_factory() as actual:
        with pytest.raises(ValueError):
            await admission.verify_planning_draft_admission(harness.wrap(actual), **harness.kwargs(), receipt=receipt)


@pytest.mark.parametrize("history", [CanonFactModel, TimelineEventModel, ChapterDraftVersionModel])
async def test_missing_continuity_context_fails_before_paid_call(harness, history):
    harness.history = history
    with pytest.raises(ValueError, match="admission_continuity_context_unsupported"):
        await assess(harness)
    assert not harness.sent


async def test_later_chapter_requires_adopted_context_before_paid_call(harness, monkeypatch):
    from bestseller.services import planning_continuity_context
    async def missing(*args, **kwargs):
        assert kwargs['before_chapter'] == 2
        raise ValueError('continuation_prior_chapter_gap')
    monkeypatch.setattr(planning_continuity_context, 'build_planning_continuity_context', missing)
    original = harness.kwargs
    def later():
        data = original()
        data["candidate_payload"]["chapters"][0]["chapter_number"] = 2
        return data
    harness.kwargs = later
    with pytest.raises(ValueError, match="continuation_prior_chapter_gap"):
        await assess(harness)
    assert not harness.sent


@pytest.mark.parametrize("history", [CanonFactModel, TimelineEventModel, ChapterDraftVersionModel])
async def test_historical_receipt_survives_own_adoption_but_new_assessment_does_not(harness, history):
    receipt = await assess(harness)
    harness.history = history
    async with harness.session_factory() as actual:
        await admission.verify_planning_draft_admission(harness.wrap(actual), **harness.kwargs(), receipt=receipt)
    with pytest.raises(ValueError, match="admission_continuity_context_unsupported"):
        await assess(harness)
    assert len(harness.sent) == 1


@pytest.mark.parametrize("change", ["candidate", "source_version", "source_body", "source_status", "user_tag"])
async def test_current_source_or_candidate_changes_invalidate_receipt(harness, change):
    h = harness
    receipt = await assess(h)
    kwargs = h.kwargs()
    if change == "candidate":
        kwargs["candidate_payload"]["chapters"][0]["goal"] = "Different first result"
    elif change == "source_version":
        h.sources[ArtifactType.CAST_SPEC].version_no += 1
    elif change == "source_body":
        h.sources[ArtifactType.CAST_SPEC].content["requirement"] = "A different identity"
    elif change == "source_status":
        h.sources[ArtifactType.CAST_SPEC].status = "needs_review"
    else:
        h.project.metadata_json["creation_intent_contract"]["genre_intent"]["user_tags"].append("ANOTHER_EXPLICIT_EFFECT")
    async with h.session_factory() as actual:
        with pytest.raises(ValueError):
            await admission.verify_planning_draft_admission(h.wrap(actual), **kwargs, receipt=receipt)


@pytest.mark.parametrize("change", ["provider", "stop", "workflow", "fallback", "prompt_hash", "prompt_version", "paid", "transport"])
async def test_missing_or_fake_provider_proof_is_not_admission(harness, change):
    h = harness
    receipt = await assess(h)
    async with h.session_factory() as actual:
        run = await actual.get(LlmRunModel, UUID(receipt["llm_run_id"]))
        if change == "provider": run.provider = "mock"
        elif change == "stop": run.finish_reason = "length"
        elif change == "workflow": run.workflow_run_id = uuid4()
        elif change == "fallback": run.metadata_json = {**run.metadata_json, "fallback_used": True}
        elif change == "prompt_hash": run.prompt_hash = "wrong"
        elif change == "prompt_version": run.prompt_version = "planning-draft-admission.v1"
        else:
            key = run.metadata_json["execution_receipt"]["operation_key"]
            operation = await actual.get(ExecutionOperationModel, key)
            if change == "paid": operation.status = "unknown"
            else:
                attempt = await actual.scalar(select(ExecutionAttemptModel).where(ExecutionAttemptModel.operation_key == key))
                attempt.status = "reconciled_charged"
        await actual.commit()
    async with h.session_factory() as actual:
        with pytest.raises(ValueError):
            await admission.verify_planning_draft_admission(h.wrap(actual), **h.kwargs(), receipt=receipt)


async def test_later_admission_binds_actual_continuity_and_rejects_changed_predecessor(harness, monkeypatch):
    from bestseller.services import planning_continuity_context
    state = {'context_hash': 'old-adopted-body', 'recent_adopted_prose': [{'body': 'Mira left the key on the boat.'}]}
    async def context(*args, **kwargs):
        assert kwargs['project_id'] == harness.project.id and kwargs['before_chapter'] == 2
        return copy.deepcopy(state)
    monkeypatch.setattr(planning_continuity_context, 'build_planning_continuity_context', context)
    original = harness.kwargs
    def later():
        data = original()
        data['candidate_payload']['chapters'][0]['chapter_number'] = 2
        return data
    harness.kwargs = later
    harness.raw = json.dumps(grade_payload(harness.kwargs()["candidate_payload"]))
    receipt = await assess(harness)
    actual_input = json.loads(harness.sent[0].user_prompt)['input']
    assert actual_input['adopted_continuity'] == state
    state['context_hash'] = 'changed-adopted-body'
    async with harness.session_factory() as actual:
        with pytest.raises(ValueError, match='admission_stale_input'):
            await admission.verify_planning_draft_admission(harness.wrap(actual), **harness.kwargs(), receipt=receipt)
    assert len(harness.sent) == 1


def test_policy_fingerprints_instructions_schema_and_audit_contract():
    assert admission.HARD_ADMISSION_POLICY == admission.content_hash({
        "schema": admission.SCHEMA, "system_prompt": admission.SYSTEM,
        "audit_contract": admission.PROJECTION_AUDIT_CONTRACT,
        "grade_schema": admission._Grade.model_json_schema(),
    })


@pytest.mark.parametrize("receipt,reason", [
    (None, "schema_mismatch"), ([], "schema_mismatch"), ({}, "schema_mismatch"),
    ({"schema": "planning-draft-admission.v1", "hard_admission_policy": "legacy"}, "schema_mismatch"),
    ({"schema": admission.SCHEMA}, "policy_mismatch"),
    ({"schema": admission.SCHEMA, "hard_admission_policy": "obsolete"}, "policy_mismatch"),
    ({"schema": admission.SCHEMA, "hard_admission_policy": admission.HARD_ADMISSION_POLICY}, "policy_current"),
])
def test_receipt_policy_status_is_synchronous_pure_and_fail_closed(receipt, reason):
    original = copy.deepcopy(receipt)
    status = admission.admission_receipt_status(receipt)
    assert status == {
        "status": "current" if reason == "policy_current" else "reassessment_required",
        "reason": reason, "current_policy": admission.HARD_ADMISSION_POLICY,
        "previous_policy": receipt.get("hard_admission_policy") if isinstance(receipt, dict) else None,
    }
    assert receipt == original
    # Even malformed stored metadata must not be returned by reference.
    malformed = {"schema": admission.SCHEMA, "hard_admission_policy": {"old": []}}
    admission.admission_receipt_status(malformed)["previous_policy"]["old"].append("changed")
    assert malformed["hard_admission_policy"] == {"old": []}


@pytest.mark.parametrize("change", ["v1", "no_schema", "no_policy", "old_policy"])
async def test_obsolete_receipt_rejected_before_input_or_model(harness, monkeypatch, change):
    receipt = await assess(harness)
    if change == "v1":
        receipt["schema"] = "planning-draft-admission.v1"
    elif change == "no_schema":
        del receipt["schema"]
    elif change == "no_policy":
        del receipt["hard_admission_policy"]
    else:
        receipt["hard_admission_policy"] = "obsolete-soft-admission"
    # The obsolete path must not even need report, sources, workflow or DB access.
    del receipt["commercial_report"]
    original = copy.deepcopy(receipt)
    async def forbidden(*args, **kwargs):
        pytest.fail("obsolete receipts must be rejected before input/model access")
    monkeypatch.setattr(admission, "_input", forbidden)
    monkeypatch.setattr(admission, "complete_text", forbidden)
    with pytest.raises(ValueError, match="admission_reassessment_required"):
        await admission.verify_planning_draft_admission(None, **harness.kwargs(), receipt=receipt)
    assert receipt == original and len(harness.sent) == 1


async def test_policy_change_uses_new_paid_operation_and_preserves_old_grade(harness, monkeypatch):
    h = harness
    first = await assess(h)
    historical = copy.deepcopy(first)
    old_raw = first["raw_grade"]
    monkeypatch.setattr(admission, "HARD_ADMISSION_POLICY", admission.content_hash({
        "schema": admission.SCHEMA, "system_prompt": admission.SYSTEM,
        "audit_contract": {**admission.PROJECTION_AUDIT_CONTRACT, "revision": 2},
    }))
    assert admission.admission_receipt_status(first)["status"] == "reassessment_required"
    h.raw = json.dumps(grade_payload(h.kwargs()["candidate_payload"], FAIL), indent=2)
    second = await assess(h)
    assert not second["draft_eligible"] and second["raw_grade"] == h.raw
    assert second["graded_input_hash"] != first["graded_input_hash"]
    assert second["hard_admission_policy"] != first["hard_admission_policy"]
    assert len(h.sent) == 2 and h.sent[0].operation_id != h.sent[1].operation_id
    assert first == historical
    async with h.session_factory() as actual:
        old_run = await actual.get(LlmRunModel, UUID(first["llm_run_id"]))
        old_paid = await actual.get(ExecutionOperationModel, old_run.metadata_json["execution_receipt"]["operation_key"])
        assert old_paid.result_json["content"] == old_raw
        with pytest.raises(ValueError, match="admission_reassessment_required"):
            await admission.verify_planning_draft_admission(h.wrap(actual), **h.kwargs(), receipt=first)
        # Relabelling a receipt cannot bless the old paid operation or change its raw result.
        relabelled = {**first, "hard_admission_policy": admission.HARD_ADMISSION_POLICY}
        with pytest.raises(ValueError, match="admission_stale_input"):
            await admission.verify_planning_draft_admission(h.wrap(actual), **h.kwargs(), receipt=relabelled)


def full_candidate():
    return {"chapters": [
        {"chapter_number": 1, "_meta": {"cache": "ignored only at chapter root"},
         "goal": "Deliver the instrument", "opening": {"place": "workshop"},
         "causal": ["collect", "travel", "deliver"], "methodology": {"opening": "collect at workshop"},
         "scenes": [{"scene_number": 1, "purpose": "collect the instrument",
             "entry": {"place": "workshop", "time": "morning"},
             "action_sequence": ["collect instrument", "walk to the station"],
             "dialogue": ["I will take the morning train."],
             "exit": {"place": "station"}, "image": "instrument packed in a case",
             "selected_effect": {"beat": "anticipation"}, "_meta": {"visible_projection": True}},
             {"scene_number": 2, "entry": "later, station", "action_sequence": ["board train"],
              "exit": "arrive at observatory"}]},
        {"chapter_number": 2, "goal": "Hand over the instrument", "scenes": [
            {"scene_number": 1, "entry": "next day, observatory", "action_sequence": ["deliver case"]}]}]}


def test_projection_targets_bind_complete_chapters_and_complete_scenes_without_mutation():
    candidate = full_candidate()
    original = copy.deepcopy(candidate)
    targets = admission.projection_audit_targets(candidate)
    assert targets == [
        {"path": "chapters[0]", "projection_hash": admission.content_hash(
            {k: v for k, v in candidate["chapters"][0].items() if k != "_meta"})},
        {"path": "chapters[0].scenes[0]", "projection_hash": admission.content_hash(candidate["chapters"][0]["scenes"][0])},
        {"path": "chapters[0].scenes[1]", "projection_hash": admission.content_hash(candidate["chapters"][0]["scenes"][1])},
        {"path": "chapters[1]", "projection_hash": admission.content_hash(candidate["chapters"][1])},
        {"path": "chapters[1].scenes[0]", "projection_hash": admission.content_hash(candidate["chapters"][1]["scenes"][0])},
    ]
    assert candidate == original
    candidate["chapters"][0]["_meta"]["cache"] = "new metadata"
    assert admission.projection_audit_targets(candidate) == targets
    candidate["chapters"][0]["scenes"][0]["_meta"]["visible_projection"] = False
    after = admission.projection_audit_targets(candidate)
    assert after[0] != targets[0] and after[1] != targets[1]
    assert after[2:] == targets[2:]


@pytest.mark.parametrize("scope,field", [
    ("chapter", "goal"), ("chapter", "opening"), ("chapter", "causal"), ("chapter", "methodology"),
    ("scene", "purpose"), ("scene", "entry"), ("scene", "action_sequence"), ("scene", "dialogue"),
    ("scene", "exit"), ("scene", "image"), ("scene", "selected_effect"), ("scene", "unlisted_nested_field"),
])
def test_any_projection_change_invalidates_grade_including_exit_only_patch(scope, field):
    candidate = full_candidate()
    raw = json.dumps(grade_payload(candidate))
    assert admission._grade(raw, candidate).draft_eligible
    target = candidate["chapters"][0]
    if scope == "scene":
        target = target["scenes"][0]
    target[field] = {"changed_projection": "inherited from previous scene"}
    with pytest.raises(ValueError, match="admission_projection_hash_mismatch"):
        admission._grade(raw, candidate)


@pytest.mark.parametrize("change", ["missing_chapter", "missing_scene", "duplicate", "extra_scope", "wrong_hash", "no_checks"])
async def test_incomplete_or_unbound_projection_audit_cannot_be_assessed(harness, change):
    candidate = full_candidate()
    harness.kwargs = lambda original=harness.kwargs: {**original(), "candidate_payload": candidate}
    grade = grade_payload(candidate)
    if change == "missing_chapter": grade["projection_checks"].pop(0)
    elif change == "missing_scene": grade["projection_checks"].pop(2)
    elif change == "duplicate": grade["projection_checks"].append(copy.deepcopy(grade["projection_checks"][0]))
    elif change == "extra_scope": grade["projection_checks"].append({**grade["projection_checks"][0], "path": "chapters[9]"})
    elif change == "wrong_hash": grade["projection_checks"][1]["projection_hash"] = "0" * 64
    else: del grade["projection_checks"]
    harness.raw = json.dumps(grade)
    before = copy.deepcopy(grade)
    with pytest.raises(ValueError):
        await assess(harness)
    assert json.loads(harness.raw) == before


@pytest.mark.parametrize("genre,scene,code", [
    ("science-fiction", {"purpose": "repair antenna", "entry": "At noon, alone inside the sealed capsule",
        "action_sequence": ["At the same noon instant, physically outside the capsule repairing the antenna"],
        "exit": "inherits prior state"}, "SCENE_NOT_EXECUTABLE"),
    ("mystery", {"purpose": "open the exhibit", "entry": "The glass case was already opened in the prior scene",
        "dialogue": ["This is the first time the case has ever been opened"],
        "image": "the same case being opened for the first time", "exit": "inherits prior state"}, "CONTINUITY_CONFLICT"),
    ("slice-of-life", {"purpose": "serve bread", "entry": "The baker is alone at the oven at dawn",
        "methodology": {"opening": "At that exact dawn instant the baker is on the distant farm"},
        "selected_effect": "show both simultaneous locations", "exit": "inherits prior state"}, "SCENE_NOT_EXECUTABLE"),
])
async def test_unrelated_execution_conflicts_require_hard_rejection(harness, genre, scene, code):
    h = harness
    h.project.genre = genre
    candidate = {"chapters": [{"chapter_number": 1, "goal": scene["purpose"], "scenes": [scene]}]}
    h.kwargs = lambda original=h.kwargs: {**original(), "candidate_payload": candidate}
    verdict = {"draft_eligible": False, "hard_violations": [{"code": code,
        "path": "chapters[0].scenes[0]", "evidence": json.dumps(scene),
        "source_requirement": "Full projections must be executable and respect already-established actions."}],
        "editorial_notes": ["Pacing can be improved later."]}
    grade = grade_payload(candidate, verdict)
    for check in grade["projection_checks"]:
        check.update(status="conflict", explanation="The complete scene and chapter contain incompatible projections.",
            related_violation_paths=[verdict["hard_violations"][0]["path"]])
    h.raw = " \n" + json.dumps(grade, indent=2, ensure_ascii=False) + "\n"
    before = copy.deepcopy(candidate)
    receipt = await assess(h)
    assert not receipt["draft_eligible"] and receipt["raw_grade"] == h.raw
    async with h.session_factory() as actual:
        with pytest.raises(ValueError, match="admission_hard_rejected"):
            await admission.verify_planning_draft_admission(h.wrap(actual), **h.kwargs(), receipt=receipt)
    assert candidate == before


@pytest.mark.parametrize("change", ["editorial_only", "other_scene", "ancestor_only", "path_prefix_collision"])
def test_confirmed_conflicts_cannot_be_editorial_notes_or_unrelated_violations(change):
    candidate = full_candidate()
    verdict = {"draft_eligible": False, "hard_violations": [{"code": "SCENE_NOT_EXECUTABLE",
        "path": "chapters[0].scenes[0]", "evidence": "Explicit simultaneous locations conflict.",
        "source_refs": ["candidate:chapters[0].scenes[0].entry"],
        "source_requirement": "A physical actor cannot occupy mutually exclusive locations at the same instant."}],
        "editorial_notes": []}
    grade = grade_payload(candidate, verdict)
    grade["projection_checks"][1]["status"] = "conflict"
    assert not admission._grade(json.dumps(grade), candidate).draft_eligible
    if change == "editorial_only":
        grade.update(draft_eligible=True, hard_violations=[], editorial_notes=["Known conflict; let drafting fix it."])
    elif change == "other_scene": grade["hard_violations"][0]["path"] = "chapters[0].scenes[1]"
    elif change == "ancestor_only": grade["hard_violations"][0]["path"] = "chapters[0]"
    else: grade["hard_violations"][0]["path"] = "chapters[0].scenes[0]suffix"
    error = "admission_violation_scope_invalid" if change == "path_prefix_collision" else "admission_projection_conflict"
    with pytest.raises(ValueError, match=error):
        admission._grade(json.dumps(grade), candidate)


async def test_ordered_legal_transitions_and_plans_are_eligible_and_replay_is_immutable(harness):
    h = harness
    candidate = full_candidate()
    h.kwargs = lambda original=h.kwargs: {**original(), "candidate_payload": candidate}
    grade = grade_payload(candidate)
    # Check order is not semantic order: audit paths, not response order, bind coverage.
    grade["projection_checks"].reverse()
    h.raw = "\n" + json.dumps(grade, ensure_ascii=False, indent=3) + " \n"
    before = copy.deepcopy((candidate, h.project.metadata_json, h.commercial))
    first = await assess(h, rollback=True)
    original = copy.deepcopy(first)
    second = await assess(h)
    assert first["graded_input_hash"] == second["graded_input_hash"] and len(h.sent) == 1
    assert first["raw_grade"] == second["raw_grade"] == h.raw
    h.history = CanonFactModel
    async with h.session_factory() as actual:
        for _ in range(2):
            verified = await admission.verify_planning_draft_admission(h.wrap(actual), **h.kwargs(), receipt=second)
            assert verified == second and verified is not second
        verified["assessment"]["projection_checks"].clear()
        assert second["assessment"]["projection_checks"]
    assert first == original and (candidate, h.project.metadata_json, h.commercial) == before


async def test_relabelled_legacy_raw_grade_cannot_be_upgraded_to_pass(harness):
    receipt = await assess(harness)
    legacy_raw = json.dumps(PASS, indent=2)
    receipt.update(raw_grade=legacy_raw, assessment=copy.deepcopy(PASS))
    before = copy.deepcopy(receipt)
    async with harness.session_factory() as actual:
        with pytest.raises(ValueError):
            await admission.verify_planning_draft_admission(harness.wrap(actual), **harness.kwargs(), receipt=receipt)
    assert receipt == before and receipt["raw_grade"] == legacy_raw


def test_system_requires_full_semantic_audit_without_invented_transitions():
    for instruction in ("逐章、逐场景并跨投影", "goal", "opening", "causal", "methodology",
                        "purpose", "entry", "action_sequence", "dialogue", "exit", "image", "selected_effect",
                        "已发生动作不能重新当作首次发生", "规划不是发生事实", "同一时点同一人物位置互斥",
                        "不可用编造过渡消解明确同时冲突", "已确认冲突不能用editorial_notes软放行"):
        assert instruction in admission.SYSTEM


@pytest.mark.parametrize("code", ["ABILITY_RULE_CONFLICT", "IDENTITY_CONFLICT",
    "EXPLICIT_CREATION_REQUIREMENT_CONFLICT", "CONTINUITY_CONFLICT", "SCENE_NOT_EXECUTABLE"])
def test_all_hard_codes_and_explicit_cross_scene_dependencies_can_explain_projection_conflict(code):
    candidate = full_candidate()
    violation = {"code": code, "path": "chapters[0].scenes[0]",
        "evidence": "The proposed result contradicts the cited source.",
        "source_requirement": "The cited constraint is hard.", "source_refs": (
            ["candidate:chapters[0].scenes[0].action_sequence[0]"]
            if code in {"CONTINUITY_CONFLICT", "SCENE_NOT_EXECUTABLE"}
            else ["/approved_world_spec/requirement"])}
    grade = grade_payload(candidate, {**FAIL, "hard_violations": [violation]})
    grade["projection_checks"][0]["status"] = "conflict"
    grade["projection_checks"][1]["status"] = "conflict"
    grade["projection_checks"][2].update(status="conflict", related_violation_paths=[violation["path"]])
    assert not admission._grade(json.dumps(grade), candidate).draft_eligible
    grade["projection_checks"][2]["related_violation_paths"] = []
    with pytest.raises(ValueError, match="conflict_unexplained"):
        admission._grade(json.dumps(grade), candidate)


@pytest.mark.parametrize("refs,status", [(["chapters[99]"], "conflict"),
    (["chapters[0].scenes[0]"] * 2, "conflict"), (["chapters[0].scenes[0]"], "consistent")])
def test_projection_dependency_cannot_reference_missing_or_duplicate_violation_or_soft_pass(refs, status):
    candidate = full_candidate()
    grade = grade_payload(candidate, FAIL)
    grade["projection_checks"][2].update(status=status, related_violation_paths=refs)
    with pytest.raises(ValueError, match="dependency_invalid"):
        admission._grade(json.dumps(grade), candidate)


def test_source_roles_distinguish_authority_from_ai_schedules_at_any_depth():
    supplied = {"approved_book_spec": {"phase_transitions": [{"chapter": 12}],
        "power_system": {"limits": "tool contact", "phase_transitions": [25]}},
        "approved_world_spec": {"power_system": {"trigger": "touch", "limits": "rest after use",
            "phase_transitions": [12, 25]}},
        "approved_cast_spec": {"protagonist": {"starting_power": "dormant",
            "evolution_arc": "awaken in later volume", "ending_power_vol4": "ascended"}},
        "approved_creation_contracts": {"creation_intent_contract": {
            "creative_direction": "The sealed archive must remain sealed until chapter seven."}},
        "adopted_continuity": {"recent_adopted_prose": [{"body": "The first cartridge was spent."}]}}
    original = copy.deepcopy(supplied)
    roles = admission._authority_roles(admission._source_authority(supplied))
    assert roles["/approved_book_spec/phase_transitions/0/chapter"] == "advisory"
    assert roles["/approved_book_spec/power_system/phase_transitions/0"] == "advisory"
    assert roles["/approved_world_spec/power_system/phase_transitions/0"] == "advisory"
    assert roles["/approved_cast_spec/protagonist/evolution_arc"] == "advisory"
    assert roles["/approved_cast_spec/protagonist/ending_power_vol4"] == "advisory"
    assert roles["/approved_world_spec/power_system/trigger"] == "world_rule"
    assert roles["/approved_cast_spec/protagonist/starting_power"] == "character_identity"
    assert roles["/approved_creation_contracts/creation_intent_contract/creative_direction"] == "canonical_author"
    assert roles["/adopted_continuity/recent_adopted_prose/0/body"] == "prior_occurrence"
    assert supplied == original


@pytest.mark.parametrize("code,ref", [
    ("EXPLICIT_CREATION_REQUIREMENT_CONFLICT", "/approved_book_spec/phase_transitions/0"),
    ("ABILITY_RULE_CONFLICT", "/approved_world_spec/power_system/phase_transitions/0"),
    ("CONTINUITY_CONFLICT", "/approved_cast_spec/protagonist/evolution_arc"),
    ("EXPLICIT_CREATION_REQUIREMENT_CONFLICT", "/approved_world_spec/requirement"),
    ("ABILITY_RULE_CONFLICT", "/approved_world_spec"),
    ("SCENE_NOT_EXECUTABLE", "candidate:chapters[0].scenes[99]"),
])
def test_untrusted_or_missing_source_cannot_be_used_as_hard_rejection(code, ref):
    candidate = full_candidate()
    supplied = {"approved_book_spec": {"phase_transitions": [12]},
        "approved_world_spec": {"requirement": "touch the tool", "power_system": {"phase_transitions": [25]}},
        "approved_cast_spec": {"protagonist": {"evolution_arc": "late discovery"}}}
    supplied["source_authority"] = admission._source_authority(supplied)
    grade = grade_payload(candidate, {**FAIL, "hard_violations": [{**FAIL["hard_violations"][0],
        "code": code, "source_refs": [ref]}]})
    with pytest.raises(ValueError, match="source_authority_invalid"):
        admission._grade(json.dumps(grade), candidate, supplied)


async def test_canonical_author_clause_remains_literal_hard_source(harness):
    h = harness
    literal = "Keep the observatory sealed until the seventh chapter; do not reveal the note early."
    creation = h.project.metadata_json["creation_intent_contract"]
    creation["field_sources"]["creative_direction"] = "explicit"
    creation["creative_direction"] = literal
    bad = grade_payload(h.kwargs()["candidate_payload"], {**FAIL, "hard_violations": [{
        **FAIL["hard_violations"][0], "code": "EXPLICIT_CREATION_REQUIREMENT_CONFLICT",
        "source_refs": ["/approved_creation_contracts/creation_intent_contract/creative_direction"],
        "source_requirement": literal}]})
    h.raw = json.dumps(bad)
    receipt = await assess(h)
    assert not receipt["draft_eligible"] and len(h.sent) == 1
    supplied = json.loads(h.sent[0].user_prompt)["input"]
    assert supplied["approved_creation_contracts"]["creation_intent_contract"]["creative_direction"] == literal
    assert receipt["assessment"]["hard_violations"][0]["source_requirement"] == literal


@pytest.mark.parametrize("genre,entry,actions,exit_state", [
    ("science-fiction", "Cartridge one is used, two unused cartridges remain.",
        ["Use original cartridge two", "Use original cartridge three"], "All three cartridges are spent"),
    ("mystery", "Only the first of three sealed envelopes has been opened.",
        ["Open the second envelope", "Open the third envelope"], "All envelopes have been examined"),
])
async def test_temporal_contract_preserves_causal_new_actions_and_prior_occurrences(harness, monkeypatch,
    genre, entry, actions, exit_state):
    from bestseller.services import planning_continuity_context
    h = harness
    h.project.genre = genre
    prior = {"before_chapter": 2, "recent_adopted_prose": [{"body": entry}]}
    async def context(*args, **kwargs): return copy.deepcopy(prior)
    monkeypatch.setattr(planning_continuity_context, "build_planning_continuity_context", context)
    candidate = {"chapters": [{"chapter_number": 2, "scenes": [{"entry": entry,
        "action_sequence": actions, "exit": exit_state}]}]}
    h.kwargs = lambda original=h.kwargs: {**original(), "candidate_payload": candidate}
    h.raw = json.dumps(grade_payload(candidate))
    receipt = await assess(h)
    supplied = json.loads(h.sent[0].user_prompt)["input"]
    assert supplied["candidate_payload"] == candidate and supplied["adopted_continuity"] == prior
    assert admission._authority_roles(supplied["source_authority"])["/adopted_continuity/recent_adopted_prose/0/body"] == "prior_occurrence"
    for rule in ("余量与对象序号不是同一概念", "entry→action_sequence→exit", "不同时点", "新动作的结果",
                 "角色违背劝告并承担后果", "phase_transitions", "canonical作者明确来源"):
        assert rule in h.sent[0].system_prompt
    assert receipt["draft_eligible"]


async def _assess_protocol(h, state, persist=None):
    async with h.session_factory() as actual:
        async def save():
            await actual.commit()
            if persist:
                await persist()
        with bind_execution_scope("audit:admission-fixture"):
            receipt = await admission.assess_planning_draft_admission(h.wrap(actual), h.settings,
                **h.kwargs(), commercial_report=h.commercial, protocol_state=state, persist_protocol=save)
        await actual.commit()
        return receipt


async def test_protocol_repair_is_bounded_same_input_paid_and_preserves_invalid_raw(harness):
    h = harness
    malformed = '{"draft_eligible":true,'
    valid_rejection = json.dumps(grade_payload(h.kwargs()["candidate_payload"], FAIL))
    h.replies = [(malformed, "stop"), (valid_rejection, "stop")]
    state, snapshots = {}, []
    async def persist(): snapshots.append(copy.deepcopy(state))
    receipt = await _assess_protocol(h, state, persist)
    assert not receipt["draft_eligible"] and len(h.sent) == 2
    assert receipt["protocol_attempts"][0]["completion"]["content"] == malformed
    assert receipt["protocol_attempts"][0]["error"]["type"] == "PlanningAdmissionSubmissionError"
    assert "JSONDecodeError" in receipt["protocol_attempts"][0]["error"]["detail"]
    first, second = [json.loads(request.user_prompt) for request in h.sent]
    assert first["input"] == second["input"] and first["graded_input_hash"] == second["graded_input_hash"]
    assert h.sent[0].operation_id != h.sent[1].operation_id
    feedback=second["protocol_repair"]["previous_attempts"][0]
    assert feedback["raw_submission_sha256"]==admission.content_hash(malformed) and "raw_grade" not in feedback
    assert feedback["original_raw_and_replay_metadata_retained_in_audit"]
    assert snapshots[0]["attempts"] == []
    assert snapshots[0]["graded_input_hash"] == receipt["graded_input_hash"]
    assert snapshots[1]["attempts"][0]["completion"]["content"] == malformed
    before = copy.deepcopy(state)
    assert await _assess_protocol(h, state) == receipt and state == before and len(h.sent) == 2
    async with h.session_factory() as actual:
        with pytest.raises(ValueError, match="admission_hard_rejected"):
            await admission.verify_planning_draft_admission(h.wrap(actual), **h.kwargs(), receipt=receipt)


async def test_protocol_exhaustion_replays_same_paid_results_without_new_rounds(harness):
    h = harness
    h.raw = '{"draft_eligible":true,"draft_eligible":false}'
    state = {}
    for _ in range(2):
        with pytest.raises(admission.PlanningDraftAdmissionProtocolError) as failure:
            await _assess_protocol(h, state)
        assert failure.value.receipt["status"] == "protocol_exhausted"
        assert len(failure.value.receipt["attempts"]) == 2
        assert all(item["completion"]["content"] == h.raw and item["error"] for item in state["attempts"])
    assert len(h.sent) == 2
    async with h.session_factory() as session:
        budget = await session.get(ExecutionBudgetModel, "audit:admission-fixture")
        assert budget.calls_used == 2


async def test_crash_after_return_keeps_paid_raw_and_resumes_only_remaining_protocol_slot(harness):
    h = harness
    h.replies = [('not json', "stop"), (h.raw, "stop")]
    state = {}
    async def crash():
        if state["attempts"]:
            raise RuntimeError("crash after paid return persisted")
    with pytest.raises(RuntimeError, match="persisted"):
        await _assess_protocol(h, state, crash)
    assert len(h.sent) == 1 and state["attempts"][0]["completion"]["content"] == 'not json'
    receipt = await _assess_protocol(h, state)
    assert receipt["draft_eligible"] and len(h.sent) == 2
    assert len(receipt["protocol_attempts"]) == 2


async def test_incomplete_finish_cannot_be_fresh_and_valid_hard_rejection_never_retries(harness):
    h = harness
    h.replies = [(h.raw, "length"), (json.dumps(grade_payload(h.kwargs()["candidate_payload"], FAIL)), "stop")]
    receipt = await _assess_protocol(h, {})
    assert not receipt["draft_eligible"] and len(h.sent) == 2
    assert receipt["protocol_attempts"][0]["completion"]["finish_reason"] == "length"
    assert receipt["protocol_attempts"][0]["error"]["detail"] == "admission_incomplete_completion"


async def test_repaired_receipt_requires_immutable_prior_paid_proof(harness):
    h = harness
    h.replies = [('invalid raw', "stop"), (h.raw, "stop")]
    receipt = await _assess_protocol(h, {})
    receipt["protocol_attempts"][0]["completion"]["content"] = 'changed raw'
    async with h.session_factory() as actual:
        with pytest.raises(ValueError, match="paid_grade_mismatch"):
            await admission.verify_planning_draft_admission(h.wrap(actual), **h.kwargs(), receipt=receipt)


async def test_protocol_input_or_scope_changes_cannot_reset_attempts(harness):
    h = harness
    state = {}
    h.raw = 'invalid'
    with pytest.raises(admission.PlanningDraftAdmissionProtocolError):
        await _assess_protocol(h, state)
    original = copy.deepcopy(state)
    async with h.session_factory() as actual:
        with bind_execution_scope("audit:different-scope"):
            with pytest.raises(ValueError, match="scope_changed"):
                await admission.assess_planning_draft_admission(h.wrap(actual), h.settings,
                    **h.kwargs(), commercial_report=h.commercial, protocol_state=state)
    kwargs = h.kwargs()
    kwargs["candidate_payload"]["chapters"][0]["goal"] = "Another goal"
    async with h.session_factory() as actual:
        with bind_execution_scope("audit:admission-fixture"):
            with pytest.raises(ValueError, match="input_changed"):
                await admission.assess_planning_draft_admission(h.wrap(actual), h.settings,
                    **kwargs, commercial_report=h.commercial, protocol_state=state)
    assert state == original and len(h.sent) == 2


def test_grouped_authority_round_trips_every_leaf_and_metadata_cannot_gain_hard_authority():
    supplied = {
        "approved_world_spec": {"power_system": {"trigger": "contact", "limit": "one attempt"},
            "_meta": {"planned_frontiers": {"a" * 64: {"disclosure_content_hash": "b" * 64}}},
            "story/a~b": "A setting detail", "content_hash": "c" * 64},
        "approved_creation_contracts": {"creation_intent_contract": {"creative_direction": "Keep the moon visible",
            "_meta": {"disclosure": "This cache is not an author clause"}}},
        "adopted_continuity": {"recent_adopted_prose": [{"body": "The bell rang.",
            "metadata": {"generated_hash": "d" * 64}}]},
    }
    before = copy.deepcopy(supplied)
    catalog = admission._source_authority(supplied)
    decoded = admission._authority_roles(catalog)
    assert len(decoded) == 9
    assert decoded["/approved_world_spec/power_system/trigger"] == "world_rule"
    assert decoded["/approved_world_spec/story~1a~0b"] == "world_rule"
    assert decoded["/approved_world_spec/_meta/planned_frontiers/" + "a" * 64 + "/disclosure_content_hash"] == "provenance"
    assert decoded["/approved_world_spec/content_hash"] == "provenance"
    assert decoded["/approved_creation_contracts/creation_intent_contract/_meta/disclosure"] == "provenance"
    assert decoded["/approved_creation_contracts/creation_intent_contract/creative_direction"] == "canonical_author"
    assert decoded["/adopted_continuity/recent_adopted_prose/0/metadata/generated_hash"] == "provenance"
    assert supplied == before
    assert sum(len(suffixes) for groups in catalog.values() for suffixes in groups.values()) == len(decoded)
    assert not any(role == "provenance" for roles in admission._SOURCE_ROLES_BY_CODE.values() for role in roles)


async def test_model_input_shares_permissions_without_repeating_hashes_and_binds_full_sources(harness):
    h = harness
    # Root producer metadata is excluded; nested qualifiers remain complete
    # story data without acquiring hard-source authority from their key name.
    h.sources[ArtifactType.WORLD_SPEC].content["nested_story"] = {
        "_meta": {"annotation": "An unresolved in-story qualification, not a creator clause."}}
    receipt = await assess(h)
    supplied = json.loads(h.sent[0].user_prompt)["input"]
    full_world = h.kwargs()["world_spec"]
    assert supplied["approved_world_spec"] == {key: value for key, value in full_world.items() if key != "_meta"}
    assert supplied["foundation"][ArtifactType.WORLD_SPEC.value]["content_hash"] == admission.content_hash(full_world)
    assert supplied["foundation_source_selection"]["full_sources"] == supplied["foundation"]
    assert supplied["source_authority_allowed_codes"]["world_rule"] == sorted(
        code for code, roles in admission._SOURCE_ROLES_BY_CODE.items() if "world_rule" in roles)
    assert supplied["source_authority_allowed_codes"]["provenance"] == []
    assert "value_hash" not in json.dumps(supplied["source_authority"])
    assert "allowed_codes" not in json.dumps(supplied["source_authority"])
    changed = copy.deepcopy(supplied)
    changed["approved_world_spec"]["requirement"] = "A changed rule"
    assert admission.content_hash(changed) != receipt["graded_input_hash"]


@pytest.mark.parametrize("code", ["IDENTITY_CONFLICT", "ABILITY_RULE_CONFLICT", "EXPLICIT_CREATION_REQUIREMENT_CONFLICT"])
def test_metadata_hash_or_frontier_is_never_a_hard_story_requirement(code):
    candidate = full_candidate()
    supplied = {"approved_world_spec": {"_meta": {"planned_frontiers": {
        "z" * 64: {"disclosure_content_hash": "x" * 64}}}}}
    supplied["source_authority"] = admission._source_authority(supplied)
    grade = grade_payload(candidate, {**FAIL, "hard_violations": [{**FAIL["hard_violations"][0],
        "code": code, "source_refs": ["/approved_world_spec/_meta/planned_frontiers/" + "z" * 64 + "/disclosure_content_hash"]}]})
    with pytest.raises(ValueError, match="source_authority_invalid"):
        admission._grade(json.dumps(grade), candidate, supplied)


def test_protocol_feedback_preserves_exact_raw_but_not_completion_metadata_or_duplicated_validation_input():
    raw = '{"draft_eligible":"unexpected"}'
    candidate = full_candidate()
    error = admission._protocol_error({"content": raw, "finish_reason": "stop"}, candidate, {})
    assert error["type"] == "PlanningAdmissionSubmissionError" and error["detail"] and error["feedback"]
    assert any(issue["code"] == "admission_schema_invalid" for issue in error["feedback"])
    assert all("input" not in issue and "url" not in issue for issue in error["feedback"])
    journal = [{"completion": {"content": raw, "finish_reason": "stop", "metadata": {"giant": "x" * 1000000}},
        "error": error}]
    before = copy.deepcopy(journal)
    prompt = admission._admission_prompt({"original_source": "Complete original source"}, "h" * 64, journal)
    decoded = json.loads(prompt)
    feedback = decoded["protocol_repair"]["previous_attempts"][0]
    assert feedback == {"raw_grade": raw, "finish_reason": "stop", "error": {
        "type": "PlanningAdmissionSubmissionError", "detail": error["feedback"]}}
    assert len(prompt) < 3000 and "giant" not in prompt
    assert decoded["input"] == {"original_source": "Complete original source"}
    assert journal == before and journal[0]["error"]["detail"] == error["detail"]


async def test_schema_error_repair_receipt_remains_valid_after_json_journal_round_trip(harness):
    h = harness
    malformed = '{"draft_eligible":"invalid"}'
    h.replies = [(malformed, "stop"), (h.raw, "stop")]
    state = {}
    receipt = await _assess_protocol(h, state)
    persisted = json.loads(json.dumps(receipt))
    async with h.session_factory() as actual:
        assert await admission.verify_planning_draft_admission(h.wrap(actual), **h.kwargs(), receipt=persisted) == persisted
    assert await _assess_protocol(h, json.loads(json.dumps(state))) == receipt
    assert len(h.sent) == 2


async def test_oversized_complete_source_stops_before_paid_send_without_source_truncation(harness):
    h = harness
    h.sources[ArtifactType.WORLD_SPEC].content['requirement'] = '完整世界规则不能被截断。' * 50000
    before = copy.deepcopy(h.sources[ArtifactType.WORLD_SPEC].content)
    state, saved = {}, []
    async def persist(): saved.append(copy.deepcopy(state))
    with pytest.raises(admission.PlanningDraftAdmissionProtocolError) as failure:
        await _assess_protocol(h, state, persist)
    assert failure.value.code == 'admission_input_budget_exceeded'
    assert saved[-1]['status'] == 'input_budget_blocked'
    assert not state['input_capacity']['budget_passed']
    assert state['input_capacity']['truncated'] is False
    assert state['attempts'] == [] and h.sent == []
    assert h.sources[ArtifactType.WORLD_SPEC].content == before
    async with h.session_factory() as session:
        assert await session.get(ExecutionBudgetModel, 'audit:admission-fixture') is None


async def test_oversized_raw_is_audited_not_duplicated_and_existing_protocol_limit_is_preserved(harness):
    h = harness
    h.raw = 'invalid raw returned by provider ' * 30000
    state = {}
    for _ in range(2):
        with pytest.raises(admission.PlanningDraftAdmissionProtocolError) as failure:
            await _assess_protocol(h, state)
        assert failure.value.code == 'admission_protocol_exhausted'
        assert len(h.sent) == 2 and len(state['attempts']) == 2
        assert all(item['completion']['content']==h.raw for item in state['attempts'])
        assert h.raw not in h.sent[1].user_prompt and len(h.sent[1].user_prompt)<len(h.raw)
    async with h.session_factory() as session:
        budget = await session.get(ExecutionBudgetModel, 'audit:admission-fixture')
        assert budget.calls_used == 2
