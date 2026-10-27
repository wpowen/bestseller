"""Offline proofs for exact-source withdrawal and verified-prefix review reuse.

Review/transport records are fixtures. Both admission verifiers and workflow
control flow execute normally; materialization may not make new provider calls.
"""

import copy
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.sql.dml import Delete

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import (
    ExecutionAttemptModel, ExecutionOperationModel, LlmRunModel,
    PlanningArtifactVersionModel, WorkflowRunModel, WorkflowStepRunModel,
)
from bestseller.services import (
    chapter_outline_readiness as prefix, outline_llm_judge,
    outline_reader_experience_judge, planning_draft_admission as admission,
    workflows as service,
)
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.outline_recovery_checkpoint import recovery_binding
from bestseller.services.prompt_source_policy import content_hash
from test_workflow_approved_outline_source_guard import _Store, _run, rig as _rig
import test_chapter_outline_readiness as prefix_fixtures

rig = _rig

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# rig temporarily replaces prefix_fixtures.Store with a factory. Keep the
# existing class protocol stable while composing that fixture.
_PrefixStore = prefix_fixtures.Store


class _AuditStore(_Store, _PrefixStore):
    def __init__(self):
        super().__init__()
        # This fixture composes the materializer and prefix-preparation stores.
        # Reuse the latter's session.info, savepoint and committed refresh state.
        _PrefixStore.__init__(self)
        self.delete_attempts = []
        self.commits = []
        self.attempts = []

    async def scalar(self, statement):
        if statement.column_descriptions[0].get("entity") is ExecutionAttemptModel:
            return self.attempts[-1] if self.attempts else None
        return None

    async def execute(self, statement):
        if isinstance(statement, Delete):
            self.delete_attempts.append(str(statement))
            raise AssertionError("No planning version may be deleted by a quality rejection")
        raise AssertionError("Unexpected SQL mutation in isolated workflow store")

    async def delete(self, row):
        self.delete_attempts.append(row)
        raise AssertionError("No planning version may be deleted")

    async def refresh(self, row):
        pass

    async def get(self, model, identity, **kwargs):
        row = await super().get(model, identity, **kwargs)
        key = (model, identity)
        if row is not None and kwargs.get("populate_existing") and key in self.saved_fields:
            for field, value in self.saved_fields[key].items():
                setattr(row, field, copy.deepcopy(value))
        return row

    async def scalars(self, statement):
        return await _PrefixStore.scalars(self, statement)

    async def commit(self):
        await _PrefixStore.commit(self)


@pytest.fixture
def audit(rig):
    store = _AuditStore()
    store.rows.update(rig.store.rows)
    rig.store = store
    return rig


def _artifact_snapshot(store):
    return {str(identity): {"project_id": str(row.project_id), "type": row.artifact_type,
                           "status": row.status, "content": copy.deepcopy(row.content)}
            for (model, identity), row in store.rows.items() if model is PlanningArtifactVersionModel}


def _other_versions(h):
    for project_id, kind, version in (
        (h.project.id, "chapter_outline_batch", 1),
        (h.project.id, "chapter_outline_batch", 3),
        (h.project.id, "volume_chapter_outline", 2),
        (uuid4(), "chapter_outline_batch", 2),
    ):
        row = PlanningArtifactVersionModel(id=uuid4(), project_id=project_id,
            artifact_type=kind, version_no=version, status="approved", schema_version="1.0",
            content=copy.deepcopy(h.source.content))
        h.store.rows[(PlanningArtifactVersionModel, row.id)] = row


def _rejecting_judge(monkeypatch, *, unavailable=False):
    result = LLMQualityJudgeResult(scope="outline", passed=False, overall_score=0.42,
        llm_run_id=str(uuid4()), blocking_issues=[{
            "code": "OUTLINE_JUDGE_UNAVAILABLE" if unavailable else "SOURCE_ACTION_CONFLICT",
            "severity": "critical", "evidence": "Inspect the participant/source discrepancy.",
            "required_fix": "Check the approved source without inventing another actor.",
        }], audit_issues=[{"code": "AUDIT_CONTEXT", "evidence": "Keep this diagnostic."}])
    calls = []

    async def judge(*args, **kwargs):
        calls.append(kwargs)
        return result

    async def forbidden_reader(*args, **kwargs):
        raise AssertionError("Reader judge must not run after a blocking commercial rejection")

    monkeypatch.setattr(outline_llm_judge, "judge_outline_commercial_readiness", judge)
    monkeypatch.setattr(outline_reader_experience_judge, "judge_outline_reader_experience", forbidden_reader)
    settings = service.load_settings()
    settings.pipeline.enable_outline_llm_commercial_judge = True
    settings.pipeline.outline_llm_commercial_judge_block_on_failure = True
    settings.pipeline.enable_outline_reader_experience_judge = True
    return result, calls


@pytest.mark.parametrize("exhausted", [False, True])
async def test_rejection_withdraws_only_exact_source_without_deleting_any_version(audit, monkeypatch, exhausted):
    h = audit
    _other_versions(h)
    if exhausted:
        h.project.metadata_json = {**h.project.metadata_json,
            "outline_commercial_repair_round": service._MAX_OUTLINE_COMMERCIAL_REPAIR_ROUNDS}
    before = _artifact_snapshot(h.store)
    result, calls = _rejecting_judge(monkeypatch)
    with pytest.raises(ValueError, match="blocked by outline_llm_commercial_judge"):
        await _run(h)
    after = _artifact_snapshot(h.store)
    assert h.store.delete_attempts == [] and before.keys() == after.keys()
    target = str(h.source.id)
    assert after[target] == {**before[target], "status": "needs_review"}
    assert all(after[key] == value for key, value in before.items() if key != target)
    receipt = h.project.metadata_json["outline_source_withdrawals"][-1]
    workflow = next(row for row in h.store.added if isinstance(row, WorkflowRunModel))
    assert receipt == {
        "artifact_id": target, "previous_status": "approved",
        "content_hash": content_hash(before[target]["content"]),
        "workflow_run_id": str(workflow.id), "reason": "current_source_commercial_rejection",
        "report": result.model_dump(mode="json", by_alias=True),
    }
    assert h.store.commits and len(calls) == 1
    assert h.chapters == [] and h.scenes == []


async def test_raw_rejection_cannot_touch_artifacts_or_prior_withdrawal_receipts(audit, monkeypatch):
    h = audit
    _other_versions(h)
    history = [{"artifact_id": "older-withdrawal", "report": {"pass": False}}]
    h.project.metadata_json = {**h.project.metadata_json, "outline_source_withdrawals": history}
    before = _artifact_snapshot(h.store)
    _rejecting_judge(monkeypatch)
    with pytest.raises(ValueError, match="blocked by outline_llm_commercial_judge"):
        await _run(h, source=False)
    assert _artifact_snapshot(h.store) == before
    assert h.project.metadata_json["outline_source_withdrawals"] == history
    assert h.store.delete_attempts == []


async def test_unavailable_review_never_withdraws_approved_source(audit, monkeypatch):
    before = _artifact_snapshot(audit.store)
    _rejecting_judge(monkeypatch, unavailable=True)
    with pytest.raises(Exception, match="unavailable|UNAVAILABLE|unproven"):
        await _run(audit)
    assert _artifact_snapshot(audit.store) == before
    assert "outline_source_withdrawals" not in audit.project.metadata_json
    assert audit.store.delete_attempts == []


def _historical_admission_transport(h, monkeypatch):
    """Seed transport proof rows locally; exercise the real assessor/verifier."""
    monkeypatch.setattr(admission, "get_latest_approved_planning_artifact",
                        prefix.get_latest_approved_planning_artifact)

    async def transport(session, settings, request):
        raw = json.dumps({"draft_eligible": True, "hard_violations": [],
                          "editorial_notes": ["Commercial quality debt remains open."]})
        scope, prompt_hash = "offline-prefix-history", "historical-admission-prompt"
        key = hashlib.sha256(f"{scope}\n{request.operation_id}".encode()).hexdigest()
        attempt = SimpleNamespace(id=uuid4(), scope_id=scope)
        run = SimpleNamespace(id=uuid4(), project_id=request.project_id,
            workflow_run_id=request.workflow_run_id, prompt_template=admission.TEMPLATE,
            finish_reason="stop", provider="minimax", model_name="historical-model",
            prompt_hash=prompt_hash,
            metadata_json={"execution_receipt": {"scope_id": scope, "operation_key": key}})
        operation = SimpleNamespace(scope_id=scope, status="completed", result_json={
            "content": raw, "finish_reason": "stop", "fallback_used": False,
            "provider": run.provider, "model_name": run.model_name,
            "metadata": {"effective_input_receipt": {"hash": prompt_hash}},
        })
        transport_key = hashlib.sha256(f"transport-result.v1\n{scope}\n{attempt.id}".encode()).hexdigest()
        proof = SimpleNamespace(status="transport_result", result_json={
            "operation_key": key, "input_hash": prompt_hash, "result": {"content": raw},
        })
        h.store.rows.update({(LlmRunModel, run.id): run,
                            (ExecutionOperationModel, key): operation,
                            (ExecutionOperationModel, transport_key): proof})
        h.store.attempts.append(attempt)
        return LLMCompletionResult(content=raw, provider=run.provider, model_name=run.model_name,
            llm_run_id=run.id, finish_reason="stop",
            metadata={"effective_input_receipt": {"hash": prompt_hash}})

    monkeypatch.setattr(admission, "complete_text", transport)


async def _prepare_verified_prefix(h, monkeypatch, *, commercial_passed, hard_failure=None):
    # Reuse the existing offline prefix fixture, including real binding hashes,
    # persisted grade/run records and current-foundation reads.
    monkeypatch.setattr(prefix_fixtures, "Store", lambda: h.store)
    context = prefix_fixtures.rig.__wrapped__(monkeypatch)
    context.project.metadata_json.update({key: copy.deepcopy(h.project.metadata_json[key])
        for key in ("identity_manifest", "identity_manifest_status")})
    if hard_failure == "identity":
        h.batch.chapters[0].scenes[0].participants.append("Unregistered actor")
    elif hard_failure == "causality":
        chapter = h.batch.chapters[0]
        chapter.causal_contract = chapter.methodology_contract = {}
        chapter.main_conflict = "Blue ink lies on a clean page."
        chapter.opening_situation = "A blue page rests on a desk."
        chapter.chapter_goal = "Read a blue page."
        chapter.hook_description = "The blue page remains plain."
        scene = chapter.scenes[0]
        scene.purpose = {"story": "Mara reads an ordinary blue page."}
        scene.entry_state = scene.exit_state = {"summary": "A blue page."}
        scene.methodology_contract = {}
        scene.hook_requirement = None
    context.source["chapters"][0] = h.batch.chapters[0].model_dump(mode="json")
    metadata = context.project.metadata_json
    record = metadata["outline_commercial_judge_checkpoints"]["window"]["best"]
    record["candidate"] = copy.deepcopy(context.source)
    record["candidate_hash"] = record["report"]["graded_outline_hash"] = content_hash(context.source)
    record["binding"] = recovery_binding(metadata, 1, [1, 9])
    record["report"]["outline_repair_dependency_hash"] = record["binding"]["dependency_hash"]
    context.judges[:] = [commercial_passed]
    if not commercial_passed:
        _historical_admission_transport(h, monkeypatch)
    result = await prefix_fixtures._approve(context, through_chapter=1,
        max_repair_rounds=0, allow_editorial_debt=not commercial_passed)
    assert result["draft_eligible"] is True, result
    assert result["passed"] is commercial_passed
    h.project = context.project
    h.source = h.store.rows[(PlanningArtifactVersionModel, UUID(result["artifact_id"]))]
    h.batch = ChapterOutlineBatchInput.model_validate(copy.deepcopy(h.source.content))

    async def get_project(*args, **kwargs):
        return h.project

    monkeypatch.setattr(service, "get_project_by_slug", get_project)
    calls = []
    actual_verify = prefix.verify_outline_prefix_readiness

    async def counted_verify(*args, **kwargs):
        calls.append(kwargs)
        return await actual_verify(*args, **kwargs)

    monkeypatch.setattr(prefix, "verify_outline_prefix_readiness", counted_verify)
    judges = []

    async def forbidden(*args, **kwargs):
        judges.append(kwargs)
        raise AssertionError("Verified prefix must reuse the source review without provider sends")

    monkeypatch.setattr(outline_llm_judge, "judge_outline_commercial_readiness", forbidden)
    monkeypatch.setattr(outline_reader_experience_judge, "judge_outline_reader_experience", forbidden)
    monkeypatch.setattr(prefix, "complete_text", forbidden)
    monkeypatch.setattr(admission, "complete_text", forbidden)
    settings = service.load_settings()
    settings.pipeline.enable_outline_llm_commercial_judge = True
    settings.pipeline.enable_outline_reader_experience_judge = True
    settings.pipeline.require_chapter_plan_contract = True
    settings.pipeline.enable_chapter_causality_gate = True
    settings.pipeline.chapter_causality_gate_block_on_failure = True
    return context, calls, judges


@pytest.mark.parametrize("commercial_passed", [True, False])
async def test_real_verified_prefix_reuses_review_without_new_calls_and_keeps_debt(audit, monkeypatch, commercial_passed):
    context, verifies, judges = await _prepare_verified_prefix(
        audit, monkeypatch, commercial_passed=commercial_passed)
    old_source = copy.deepcopy(audit.source.content)
    old_review = copy.deepcopy(next(row.metadata_json["final_report"]
        for (model, _), row in audit.store.rows.items() if model is WorkflowRunModel))
    old_debt = copy.deepcopy(audit.project.metadata_json.get("outline_quality_debt"))
    await _run(audit)
    assert len(verifies) == 1 and verifies[0]["artifact_id"] == audit.source.id
    assert judges == [] and len(context.judge_inputs) == 1
    assert audit.source.content == old_source and audit.source.status == "approved"
    steps = [row for row in audit.store.added if isinstance(row, WorkflowStepRunModel)]
    reuse = next(row for row in steps if row.step_name == "reuse_verified_outline_prefix")
    assert reuse.output_ref["commercial_passed"] is commercial_passed
    assert reuse.output_ref["editorial_debt_retained"] is (not commercial_passed)
    assert reuse.output_ref["source_receipt"] == old_source["_meta"][prefix.RECEIPT_KEY]
    assert reuse.output_ref["review_reused_without_new_provider_call"] is True
    assert not {"outline_llm_commercial_judge", "outline_reader_experience_judge"} & {row.step_name for row in steps}
    assert audit.project.metadata_json.get("outline_quality_debt") == old_debt
    assert old_review["pass"] is commercial_passed
    assert audit.store.delete_attempts == []


@pytest.mark.parametrize("failure,match", [("identity", "chapter_plan_contract"),
                                          ("causality", "chapter_causality_contract")])
async def test_real_verified_receipt_cannot_waive_identity_or_causality(audit, monkeypatch, failure, match):
    _, verifies, judges = await _prepare_verified_prefix(
        audit, monkeypatch, commercial_passed=True, hard_failure=failure)
    with pytest.raises(ValueError, match=match):
        await _run(audit)
    assert len(verifies) == 1 and judges == []
    assert audit.chapters == [] and audit.scenes == []
    assert audit.source.status == "approved" and audit.store.delete_attempts == []


async def test_changed_foundation_version_requires_fresh_preparation_before_materialize(audit, monkeypatch):
    """Inert judge fixtures; actual preparation/readiness/materializer control flow."""
    context, _, judges = await _prepare_verified_prefix(
        audit, monkeypatch, commercial_passed=True)
    old_source = audit.source
    old_content = copy.deepcopy(old_source.content)
    old_workflow = audit.store.rows[(WorkflowRunModel, old_source.source_run_id)]
    old_review = copy.deepcopy(old_workflow.metadata_json)
    context.foundation['cast_spec'].version_no += 1

    with pytest.raises(ValueError, match='prefix_foundation_stale'):
        await prefix.verify_outline_prefix_readiness(
            audit.store, project=audit.project, artifact_id=old_source.id)
    with pytest.raises(ValueError, match='prefix_foundation_stale'):
        await _run(audit)
    assert audit.chapters == [] and audit.scenes == []

    # Create a separate preparation under the current version, rather than
    # repainting the old receipt. Only the existing local judge stub runs.
    context.judges.append(True)
    prepared = await prefix_fixtures._approve(context, through_chapter=1)
    current = audit.store.rows[(PlanningArtifactVersionModel, UUID(prepared['artifact_id']))]
    assert current.id != old_source.id and current.source_run_id != old_source.source_run_id
    audit.source = current
    audit.batch = ChapterOutlineBatchInput.model_validate(copy.deepcopy(current.content))
    result = await _run(audit)
    assert result.source_artifact_id == current.id
    assert result.chapters_created == result.scenes_created == 1
    assert judges == []
    assert old_source.content == old_content and old_workflow.metadata_json == old_review
    assert old_source.status == 'approved'  # Historical approval is preserved, not made current.
    with pytest.raises(ValueError, match='prefix_foundation_stale'):
        await prefix.verify_outline_prefix_readiness(
            audit.store, project=audit.project, artifact_id=old_source.id)
    current_proof = await prefix.verify_outline_prefix_readiness(
        audit.store, project=audit.project, artifact_id=current.id)
    assert current_proof['foundation']['cast_spec']['version_no'] == context.foundation['cast_spec'].version_no
    assert audit.store.delete_attempts == []
