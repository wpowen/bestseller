"""Pinned editor transport compatibility; tmp policies and isolated SQLite only.

Physical production review inputs are not represented by these fixtures. Apply
must still invoke that verifier, including on an already reconciled cycle.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from test_chapter_review_current_cycle_compatibility import _rows, current_cycle  # noqa: F401
from test_chapter_review_reconciliation import migration  # noqa: F401

from bestseller.infra.db.models import RewriteTaskModel
from bestseller.services import chapter_review_reconciliation as recovery
from bestseller.services import pipelines

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
EDITOR = "lossless_rewrite_prompt.py"
OLD_SHA = "481367d4bcbb7e6436416410ba359696811f98d4c009e61a04d68b8e3768fda0"
NEW_SHA = "a705c7bd87fa10345d30ca554fa98786a98c7510e3fba8eb2208e0423ececd7e"
REAL_COMPATIBILITY = recovery.candidate_selection_policy_compatibility


def sources():
    old = (ROOT / "reports/framework-recovery-2026-10-21/phase8y7a-before-lossless_rewrite_prompt.py").read_bytes()
    new = (ROOT / "src/bestseller/services" / EDITOR).read_bytes()
    assert hashlib.sha256(old).hexdigest() == OLD_SHA
    assert hashlib.sha256(new).hexdigest() == NEW_SHA
    return old, new


def test_frozen_transition_changes_only_final_raise_and_unused_exception_alias():
    old, new = sources()
    trees = [ast.parse(data.decode()) for data in (old, new)]
    functions = []
    for tree in trees:
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "prepare_source_complete_prompt")
        tree.body.remove(function)
        functions.append(function)
    assert ast.dump(trees[0]) == ast.dump(trees[1])
    old_handler, new_handler = [function.body[1].handlers[0] for function in functions]
    assert old_handler.name == "original_error" and new_handler.name is None
    assert isinstance(old_handler.body[-1], ast.Raise)
    # The new tail has exactly import/delegate/derived-receipt/return statements.
    delegate_import, delegate, derived, returned = new_handler.body[-4:]
    assert ast.dump(delegate_import) == ast.dump(ast.parse(
        "from bestseller.services.primary_review_transport import "
        "prepare_source_complete_prompt as prepare_complete_source").body[0])
    assert ast.dump(delegate) == ast.dump(ast.parse(
        "system, user, receipt = prepare_complete_source("
        "system_prompt=system_prompt, user_prompt=user_prompt, "
        "source_context=source_context, output_tokens=output_tokens, tools=None)").body[0])
    assert isinstance(derived, ast.Assign) and isinstance(returned, ast.Return)
    new_handler.name = old_handler.name
    new_handler.body[-4:] = [old_handler.body[-1]]
    assert ast.dump(functions[0]) == ast.dump(functions[1])
    receipt = recovery._editor_source_transport_compatibility(EDITOR, old, new)
    assert receipt["previous_source_sha256"] == OLD_SHA
    assert receipt["current_source_sha256"] == NEW_SHA
    assert receipt["review_evaluator_changed"] is False
    assert receipt["grade_reused_across_input_changes"] is False
    assert receipt["new_review_credit"] is False


@pytest.fixture
def policy_case(tmp_path):
    old, new = sources()
    services = tmp_path / "src/bestseller/services"
    services.mkdir(parents=True)
    names = ("pipelines.py", EDITOR, "reviews.py", "primary_review_transport.py",
             "review_source_context.py", "prompt_compiler.py")
    pipeline = ("def chapter_review_recovery_source_hash():\n"
                f"    policy_files = {names!r}\n").encode()
    for name in names:
        data = (pipeline if name == "pipelines.py" else new if name == EDITOR
                else (ROOT / "src/bestseller/services" / name).read_bytes())
        (services / name).write_bytes(data)

    def policy(overrides=None):
        digest = hashlib.sha256()
        for name in sorted(names):
            data = (overrides or {}).get(name, (services / name).read_bytes())
            digest.update(f"src/bestseller/services/{name}".encode() + b"\0")
            digest.update(hashlib.sha256(data).digest())
        return digest.hexdigest()

    kwargs = dict(root=tmp_path, previous_pipeline_source=pipeline,
                  previous_editor_control_sources={EDITOR: old},
                  expected_previous_policy_hash=policy({EDITOR: old}),
                  expected_current_policy_hash=policy())
    return SimpleNamespace(services=services, old=old, new=new, policy=policy, kwargs=kwargs)


def test_exact_previous_editor_bytes_reconstruct_both_whole_policy_hashes(policy_case):
    case = policy_case
    receipt = REAL_COMPATIBILITY(**case.kwargs)
    assert receipt["previous_policy_hash"] == case.policy({EDITOR: case.old})
    assert receipt["current_policy_hash"] == case.policy()
    assert receipt["previous_pipeline_sha256"] == receipt["current_pipeline_sha256"]
    assert receipt["policy_file_count"] == 6
    assert receipt["unchanged_other_policy_files"] is True
    assert set(receipt["editor_source_compatibilities"]) == {EDITOR}
    assert receipt["editor_source_compatibilities"][EDITOR]["current_source_sha256"] == NEW_SHA
    with pytest.raises(ValueError, match="original_policy_bytes_unverified"):
        REAL_COMPATIBILITY(**{**case.kwargs, "previous_editor_control_sources": None})
    # Existing callers need no new parameter when no editor transition occurred.
    legacy = {key: value for key, value in case.kwargs.items()
              if key != "previous_editor_control_sources"}
    legacy["expected_previous_policy_hash"] = case.policy()
    assert "editor_source_compatibilities" not in REAL_COMPATIBILITY(**legacy)


@pytest.mark.parametrize("invalid", [
    {}, {"reviews.py": b""}, {"primary_review_transport.py": b""},
    {"../lossless_rewrite_prompt.py": b""}, {EDITOR: "not bytes"},
    [EDITOR], {EDITOR: b"wrong old body"},
])
def test_editor_map_rejects_unknown_files_empty_maps_and_wrong_old_bytes(policy_case, invalid):
    with pytest.raises(ValueError, match="review_compatibility_editor_source"):
        REAL_COMPATIBILITY(**{**policy_case.kwargs, "previous_editor_control_sources": invalid})


@pytest.mark.parametrize("before,after", [
    (b"source_context=source_context, output_tokens=output_tokens, tools=None",
     b"source_context={}, output_tokens=output_tokens, tools=None"),
    (b"output_tokens=output_tokens, tools=None", b"output_tokens=output_tokens + 1, tools=None"),
    (b"tools=None", b"tools={}"),
    (b"user_prompt=user_prompt,", b"user_prompt=user_prompt[:100],"),
    (b"system_prompt=system_prompt,", b"system_prompt='changed',"),
    (b"source_context=encoded,", b"source_context={},"),
    (b"return _prepare(", b"return changed_prepare("),
    (b"primary_review_transport", b"another_transport"),
    (b'"budget_increased": False', b'"budget_increased": True'),
    (b'"reviewer_inputs_changed": False', b'"reviewer_inputs_changed": True'),
    (b'POLICY = ', b'UNREVIEWED_CAP = 999999\nPOLICY = '),
])
def test_any_unreviewed_editor_body_protocol_or_budget_change_rejected(policy_case, before, after):
    case = policy_case
    mutated = case.new.replace(before, after)
    assert mutated != case.new
    (case.services / EDITOR).write_bytes(mutated)
    with pytest.raises(ValueError, match="editor_source_delta_unverified"):
        REAL_COMPATIBILITY(**{**case.kwargs, "expected_current_policy_hash": case.policy()})


def test_wrong_old_function_even_with_matching_old_policy_hash_is_rejected(policy_case):
    case = policy_case
    mutated = case.old.replace(b"source_context=encoded,", b"source_context={},")
    assert mutated != case.old
    with pytest.raises(ValueError, match="editor_source_delta_unverified"):
        REAL_COMPATIBILITY(**{**case.kwargs, "previous_editor_control_sources": {EDITOR: mutated},
                             "expected_previous_policy_hash": case.policy({EDITOR: mutated})})


@pytest.mark.parametrize("name", ["reviews.py", "primary_review_transport.py",
                                  "prompt_compiler.py", "review_source_context.py"])
def test_grader_transport_and_cap_changes_cannot_hide_behind_editor_map(policy_case, name):
    case = policy_case
    path = case.services / name
    path.write_bytes(path.read_bytes() + b"\nUNREVIEWED_POLICY_CHANGE = True\n")
    with pytest.raises(ValueError, match="original_policy_bytes_unverified"):
        REAL_COMPATIBILITY(**{**case.kwargs, "expected_current_policy_hash": case.policy()})


@pytest.mark.parametrize("field", ["expected_previous_policy_hash", "expected_current_policy_hash"])
def test_both_actual_policy_hashes_are_mandatory(policy_case, field):
    with pytest.raises(ValueError, match="review_compatibility_.*unverified"):
        REAL_COMPATIBILITY(**{**policy_case.kwargs, field: "0" * 64})


@pytest.mark.parametrize("change", ["removed", "unbound", "pipeline_grader_changed"])
def test_inventory_removal_unbound_editor_and_pipeline_grader_differences_rejected(policy_case, change):
    case = policy_case
    pipeline = case.kwargs["previous_pipeline_source"]
    if change in {"removed", "unbound"}:
        changed = pipeline.replace(repr(EDITOR).encode() + b", ", b"")
    else:
        changed = pipeline + b"\ndef evaluate_primary(): return 'changed'\n"
    (case.services / "pipelines.py").write_bytes(changed)
    kwargs = {**case.kwargs, "expected_current_policy_hash": case.policy()}
    if change == "unbound":
        kwargs["previous_pipeline_source"] = changed
    with pytest.raises(ValueError, match="review_compatibility_.*unverified|evaluation_code_changed"):
        REAL_COMPATIBILITY(**kwargs)


@pytest.fixture
async def editor_cycle(current_cycle, policy_case, monkeypatch):  # noqa: F811
    rig, case = current_cycle, policy_case
    metadata = deepcopy(rig.chapter.metadata_json)
    cycle = metadata["chapter_critic_cycles"][rig.cycle_id]
    cycle["policy_source_hash"] = case.kwargs["expected_previous_policy_hash"]
    task = RewriteTaskModel(project_id=rig.project.id, trigger_type="chapter_review",
        rewrite_strategy="editor", instructions="Keep the sealed chart and all source evidence.",
        status="pending", attempts=2, error_log="retained prior editor failure",
        metadata_json={"review_report_id": str(rig.report.id), "retained": [1, 2]})
    rig.session.add(task)
    await rig.session.flush()
    cycle["attempts"][-1]["rewrite_task_id"] = str(task.id)
    rig.chapter.metadata_json = metadata
    await rig.session.commit()
    monkeypatch.setattr(recovery, "candidate_selection_policy_compatibility", REAL_COMPATIBILITY)
    monkeypatch.setattr(pipelines, "__file__", str(case.services / "pipelines.py"))
    monkeypatch.setattr(pipelines, "chapter_review_recovery_source_hash", case.policy)
    return rig, case


async def apply_editor(rig, case):
    return await recovery.reconcile_retained_chapter_review(
        rig.session, SimpleNamespace(), project_id=rig.project.id, chapter_id=rig.chapter.id,
        expected_cycle_id=rig.cycle_id, expected_current_draft_id=rig.source.id,
        previous_pipeline_source=case.kwargs["previous_pipeline_source"],
        previous_editor_control_sources=case.kwargs["previous_editor_control_sources"],
        actor="isolated compatibility test", evidence_ref="frozen-editor-module-transition",
        allow_current_automatic_cycle=True)


@pytest.mark.asyncio
async def test_apply_forwards_map_and_stores_derived_receipt_without_regrading_or_commit(editor_cycle, monkeypatch):
    rig, case = editor_cycle
    before = await _rows(rig.session)
    proofs = []

    async def physical_proof(session, settings, **kwargs):
        proofs.append((kwargs["draft"].id, kwargs["report"].id))
        return {"synthetic_no_provider": True, "effective_input_unchanged": True}

    monkeypatch.setattr(sys.modules["bestseller.services.retained_review_input_proof"],
                        "verify_retained_review_inputs", physical_proof)

    async def forbidden_commit():
        pytest.fail("Reconciliation must leave commit to its caller")

    monkeypatch.setattr(rig.session, "commit", forbidden_commit)
    receipt = await apply_editor(rig, case)
    assert receipt["editor_source_compatibilities"][EDITOR]["previous_source_sha256"] == OLD_SHA
    assert receipt["working_pointer_changed"] is False and receipt["adoption_created"] is False
    assert receipt["attempts_unchanged"] is True and receipt["active_cycle_unchanged"] is True
    assert rig.session.in_transaction()
    after = await _rows(rig.session)
    old_metadata = before["chapters"][str(rig.chapter.id)]["metadata_json"]
    new_metadata = after["chapters"][str(rig.chapter.id)]["metadata_json"]
    new_cycle = new_metadata["chapter_critic_cycles"][rig.cycle_id]
    assert new_cycle["policy_source_hash"] == case.policy()
    assert new_cycle["candidate_selection_reconciliations"] == [receipt]
    new_cycle.pop("candidate_selection_reconciliations")
    new_cycle["policy_source_hash"] = old_metadata["chapter_critic_cycles"][rig.cycle_id]["policy_source_hash"]
    assert after == before  # Entire bodies, reports, scores, tasks, pointers and other cycles.
    assert await apply_editor(rig, case) == receipt
    assert proofs == [(rig.source.id, rig.report.id)] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["physical_source_changed", "world_data_migrated", "unknown_review"])
async def test_editor_compatibility_never_waives_source_data_or_available_review_guards(editor_cycle, monkeypatch, failure):
    rig, case = editor_cycle
    proof_calls = []

    async def physical_proof(*args, **kwargs):
        proof_calls.append(True)
        raise ValueError("physical_source_changed")

    monkeypatch.setattr(sys.modules["bestseller.services.retained_review_input_proof"],
                        "verify_retained_review_inputs", physical_proof)
    if failure == "world_data_migrated":
        async def changed(*args):
            return "different-world-data"
        monkeypatch.setattr(pipelines, "_chapter_critic_semantic_dependencies", changed)
    elif failure == "unknown_review":
        metadata = deepcopy(rig.chapter.metadata_json)
        metadata["chapter_critic_cycles"][rig.cycle_id]["attempts"][-1]["outcome"] = "unknown"
        rig.chapter.metadata_json = metadata
        await rig.session.commit()
    before = await _rows(rig.session)
    with pytest.raises(ValueError, match="physical_source_changed|semantic_dependencies_changed|available_review_required"):
        async with rig.session.begin_nested():
            await apply_editor(rig, case)
    assert await _rows(rig.session) == before
    assert proof_calls == ([True] if failure == "physical_source_changed" else [])
