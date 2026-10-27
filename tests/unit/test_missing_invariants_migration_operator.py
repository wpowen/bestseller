"""Offline operator contracts and real SQLite transaction/protected-row checks.

SQLite capture substitutes source assembly; actual PG normal renderer capture
is deliberately NOT claimed. The executable operator uses the existing real
assembly/capture functions, stopped before recovery or a provider.
"""
from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select
from test_chapter_review_admission_sqlite import admission_db, _commercial  # noqa: F401

PATH = Path(__file__).resolve().parents[2] / "reports/continuous-recovery-2026-10-16/initialize_missing_project_invariants.py"
SPEC = importlib.util.spec_from_file_location("missing_invariants_operator", PATH)
operator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operator)
pytestmark = pytest.mark.unit


def test_default_does_not_open_database_or_import_runtime(monkeypatch, capsys):
    monkeypatch.setattr(operator, "execute", lambda *a: pytest.fail("offline execution"))
    assert operator.main([]) == 0
    assert json.loads(capsys.readouterr().out)["database_opened"] is False


@pytest.mark.parametrize("argv", [["--apply"], ["--execute"], ["--execute", "--apply", "--chapter", "2", "--reason", "repair", "--authorization-ref", "existing"]])
def test_explicit_execution_and_reviewed_proof_required(argv):
    with pytest.raises(SystemExit):
        operator.main(argv)


def test_whole_commercial_sources_comparison_and_body_refusal():
    from bestseller.services import commercial_source_evidence as source
    from bestseller.services import commercial_protocol_successor_runtime as runtime
    old = source.assemble_commercial_sources(chapter_number=1, content_md="Aster locked the observatory.", source={}, previous_chapters=[])
    assert operator.source_relation(old, old, runtime, source)["relation"] == "exact"
    changed = source.assemble_commercial_sources(chapter_number=1, content_md="Aster opened the observatory.", source={}, previous_chapters=[])
    with pytest.raises(operator.OperatorBlocked):
        operator.source_relation(old, changed, runtime, source)


@pytest.mark.parametrize("case", ["sole_leaf", "other_leaf", "cited_audit_leaf"])
def test_exact_audit_leaf_allowance_never_promotes_changed_facts(case):
    from bestseller.services import commercial_source_evidence as source
    from bestseller.services import commercial_protocol_successor_runtime as runtime
    selection = {"policy": "typed-world-and-relevant-current-state.v1",
        "original_source_sha256": "a" * 64, "original_bible_sha256": "b" * 64,
        "retained_bible_sha256": "c" * 64, "original_bible_characters": 3,
        "retained_bible_characters": 3, "retained_bible_fields": ["setting"], "omitted_bible_fields": [],
        "authority": "unverified_context; adopted facts and explicit clauses remain separate"}
    old = source.assemble_commercial_sources(chapter_number=1, content_md="An astronomer sealed the lens.",
        source={"source_selection_receipt": selection}, previous_chapters=[])
    new_selection = {**selection, "original_source_sha256": "d" * 64}
    if case == "other_leaf":
        new_selection["original_bible_sha256"] = "e" * 64
    new = source.assemble_commercial_sources(chapter_number=1, content_md="An astronomer sealed the lens.",
        source={"source_selection_receipt": new_selection}, previous_chapters=[])
    if case == "cited_audit_leaf":
        def cite(value):
            payload = json.loads(value.text)
            payload["citation_catalog"][0]["source_ref"] = "#/source_context/source_selection_receipt/original_source_sha256"
            return source.CommercialSources(json.dumps(payload), value.catalog, value.receipt)
        old, new = cite(old), cite(new)
    if case != "sole_leaf":
        with pytest.raises(operator.OperatorBlocked):
            operator.source_relation(old, new, runtime, source)
    else:
        result = operator.source_relation(old, new, runtime, source)
        assert result["relation"] == "sole_approved_audit_leaf"
        assert not result["whole_sources_equal"] and not result["raw_bundle_equivalence_asserted"]
        assert not result["paid_request_bytes_equivalence_asserted"] and not result["grade_equivalence_asserted"]


def test_reviewed_file_exact_hash_and_completed_preflight_required(tmp_path):
    import hashlib
    path = tmp_path / "reviewed.json"
    proof = {"closed_set": "only the reviewed snapshot"}
    document = {"schema": operator.SCHEMA, "status": "preflight_passed", "rolled_back": True,
        "committed": False, "independent_readback_verified": True, "provider_calls_performed": 0,
        "blocked_gateway_attempts": 0, "proof": proof, "proof_sha256": operator.digest(proof)}
    path.write_text(json.dumps(document))
    hashed = hashlib.sha256(path.read_bytes()).hexdigest()
    assert operator.prepared_proof(path, hashed) == proof
    path.write_text(json.dumps({**document, "proof": {"expanded": "unreviewed"}}))
    with pytest.raises(operator.OperatorBlocked):
        operator.prepared_proof(path, hashed)


@pytest.mark.asyncio
async def test_capture_delegates_both_existing_real_entry_helpers(monkeypatch):
    shared = operator.support()
    calls = []
    async def inputs(*args):
        calls.append("real_judge_inputs")
        return {"normal": "kwargs"}, {"source": "hash"}
    async def prepared(judge, recovery, session, settings, kwargs):
        assert kwargs == {"normal": "kwargs"}
        calls.append("capture_prepared")
        return {"captured": True}
    monkeypatch.setattr(shared, "real_judge_inputs", inputs)
    monkeypatch.setattr(shared, "capture_prepared", prepared)
    deps = SimpleNamespace(judge=object(), recovery=object())
    assert await operator.capture(object(), object(), deps, None, None, None, None) == ({"captured": True}, {"source": "hash"})
    assert calls == ["real_judge_inputs", "capture_prepared"]


@pytest.fixture
async def migration_db(admission_db, monkeypatch):
    d = admission_db
    d.settings.output.base_dir = str(d.settings.output.base_dir)
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    sources, _ = await _commercial(d, monkeypatch)
    deps = operator.support().services()
    deps.initialization = importlib.import_module("bestseller.services.project_invariant_initialization")
    d.project.target_chapters = 50
    d.project.metadata_json = {**d.project.metadata_json, "execution_scope_id": d.budget.scope_id}
    d.workflow.status = "completed"
    # Retain two paid slots and a missing third; do not turn migration into a
    # completed cohort or fabricate the absent commercial draw.
    meta = deepcopy(d.workflow.metadata_json)
    key, journal = next(iter(meta["commercial_component_cohorts"].items()))
    journal["slots"]["chapter-quality.independent-v2:2"] = {"state": "pending", "used_attempts": 0}
    journal.pop("aggregate", None)
    d.workflow.metadata_json = meta
    for run in list(await d.session.scalars(select(deps.models.LlmRunModel))):
        if (run.metadata_json or {}).get("commercial_sample_slot") == "chapter-quality.independent-v2:2":
            execution = run.metadata_json["execution_receipt"]
            root = execution["operation_key"]
            for attempt in list(await d.session.scalars(select(deps.models.ExecutionAttemptModel).where(
                    deps.models.ExecutionAttemptModel.operation_key == root))):
                await d.session.delete(attempt)
            for operation in list(await d.session.scalars(select(deps.models.ExecutionOperationModel))):
                if operation.key == root or (operation.result_json or {}).get("operation_key") == root:
                    await d.session.delete(operation)
            await d.session.delete(run)
    await d.session.commit()
    binding = deepcopy(journal["binding"])
    async def capture(session, settings, loaded, project, chapter, draft, owner):
        result = {"project_id": project.id, "chapter_id": chapter.id, "draft_id": draft.id,
            "sources": sources, "evaluation_contract": binding["evaluation_contract"],
            "effective_critic": await loaded.judge._commercial_effective_critic_configuration(session, settings, project.id),
            "samples": 3, "sample_slot": "chapter-quality.independent-v2:0", "retained_only": True,
            "current_input_hash": "e" * 64}
        return result, {"fixture_capture_sha256": operator.digest(sources.receipt)}
    monkeypatch.setattr(operator, "capture", capture)
    d.deps = deps
    d.selected = {"project_id": str(d.project.id), "slug": d.project.slug, "scope": d.budget.scope_id}
    d.args = SimpleNamespace(chapter=1, cohort_key=key, apply=False, actor="test-operator",
        reason="Initialize only absent technical contract", authorization_ref="existing-scoped-repair-authority",
        allow_approved_audit_leaf=False)
    d.audit = lambda: {"schema": operator.SCHEMA, "blocked_gateway_attempts": 0, "committed": False}
    yield d


@pytest.mark.asyncio
async def test_sqlite_preflight_apply_second_apply_and_partial_paid_cohort_unchanged(migration_db):
    d = migration_db
    before = deepcopy(d.workflow.metadata_json)
    preview = d.audit()
    with operator.deny_providers(d.deps, preview):
        await operator.migrate(d.factory, d.settings, d.deps, d.selected, d.args, preview)
    assert preview["status"] == "preflight_passed" and preview["rolled_back"]
    assert preview["independent_readback_verified"] and not preview["committed"]
    assert preview["proof"]["source_relation"]["whole_sources_equal"]
    async with d.factory() as reader:
        assert (await reader.get(d.deps.models.ProjectModel, d.project.id)).invariants_json is None
    d.args.apply = True
    applied = d.audit()
    with operator.deny_providers(d.deps, applied):
        await operator.migrate(d.factory, d.settings, d.deps, d.selected, d.args, applied, preview["proof"])
    assert applied["status"] == "applied" and applied["committed"]
    repeated = d.audit()
    with operator.deny_providers(d.deps, repeated):
        await operator.migrate(d.factory, d.settings, d.deps, d.selected, d.args, repeated, preview["proof"])
    assert repeated["status"] == "already_applied" and repeated["writes"] == 0
    async with d.factory() as reader:
        assert (await reader.get(d.deps.models.WorkflowRunModel, d.workflow.id)).metadata_json == before
        assert len(list(await reader.scalars(select(d.deps.models.LlmRunModel)))) == 3  # primary + two retained
        assert (await reader.get(d.deps.models.ExecutionBudgetModel, d.budget.scope_id)).calls_used == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["protected_grade", "adopted_effect", "source_change"])
async def test_sqlite_source_or_protected_changes_fail_and_rollback(migration_db, monkeypatch, case):
    d = migration_db
    preview = d.audit()
    await operator.migrate(d.factory, d.settings, d.deps, d.selected, d.args, preview)
    if case == "protected_grade":
        d.quality.score_goal = .99
        await d.session.commit()
    elif case == "adopted_effect":
        d.project.metadata_json = {**d.project.metadata_json, "adopted_effect_configuration": {"chosen": "effect"}}
        await d.session.commit()
    else:
        original = operator.capture
        async def changed(*args):
            result, inputs = await original(*args)
            if args[3].invariants_json is not None:
                from bestseller.services.commercial_source_evidence import assemble_commercial_sources
                result["sources"] = assemble_commercial_sources(chapter_number=1, content_md="Changed source body.", source={}, previous_chapters=[])
            return result, inputs
        monkeypatch.setattr(operator, "capture", changed)
    d.args.apply = case == "protected_grade"
    with pytest.raises((ValueError, operator.OperatorBlocked)):
        await operator.migrate(d.factory, d.settings, d.deps, d.selected, d.args, d.audit(),
            preview["proof"] if d.args.apply else None)
    async with d.factory() as reader:
        assert (await reader.get(d.deps.models.ProjectModel, d.project.id)).invariants_json is None
        workflows = list(await reader.scalars(select(d.deps.models.WorkflowRunModel)))
        assert len(workflows) == 1


@pytest.mark.asyncio
async def test_transport_and_imported_alias_deny_before_any_send(migration_db):
    d = migration_db
    audit = d.audit()
    import litellm
    with operator.deny_providers(d.deps, audit):
        with pytest.raises(operator.ProviderDenied):
            await d.deps.llm.complete_text(None, None, None)
        with pytest.raises(operator.ProviderDenied):
            await litellm.acompletion(model="never-send")
    assert audit["blocked_gateway_attempts"] == 2
