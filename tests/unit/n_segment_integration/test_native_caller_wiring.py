"""Native entrypoints must reach, and must never bypass, physical proof.

Native rows are local synthetic exports. A sentinel stops exactly at custody
acquisition/verification; no fake paid pass, DB or provider result is supplied.
"""

from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest

from bestseller.services import fanqie_short_semantic_selection_adapter as adapter
from bestseller.services import native_publication_title as publication
from bestseller.services.native_title_operator import title_operator as op

pytestmark = pytest.mark.unit


class PhysicalProofRequired(RuntimeError):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("count", range(4, 9))
async def test_native_loader_uses_actual_project_before_acquiring_paid_proof(source_bundle, monkeypatch, count):
    bundle = source_bundle(count)
    stored = deepcopy(vars(bundle.project))
    caller = deepcopy(bundle.project)
    caller.target_chapters = 4  # A caller's count cannot override the stored N.
    context = bundle.context
    actual_counts = []

    async def one(session, model, *criteria):
        if model is adapter.native.ProjectModel:
            return stored
        if model is adapter.native.PlanningArtifactVersionModel:
            return context["parent_artifact"]
        if model is adapter.native.WorkflowRunModel:
            return context["parent_workflow"]
        raise AssertionError("unexpected native read")

    async def plan(session, settings, *, project):
        actual_counts.append(project.target_chapters)
        return deepcopy(bundle.native_plan)

    async def physical(*args, **kwargs):
        raise PhysicalProofRequired("source checks never substitute for physical paid proof")

    monkeypatch.setattr(adapter.native, "_one", one)
    monkeypatch.setattr(adapter, "_read_current_parent_plan", plan)
    monkeypatch.setattr(adapter.native, "_load_artifact_native", physical)
    monkeypatch.setattr(adapter, "frozen_v5_validator", lambda: None)
    session = SimpleNamespace(no_autoflush=nullcontext())
    assembly = SimpleNamespace(full_text=bundle.full_text, manifest=bundle.manifest)
    before = deepcopy(context)
    with pytest.raises(PhysicalProofRequired):
        await adapter.load_selection_parent(session, None, project=caller, assembly=assembly,
            parent_artifact_id=context["parent_artifact"]["id"])
    assert actual_counts == [count]
    assert context == before


@pytest.mark.asyncio
async def test_native_loader_rejects_caller_eight_when_actual_project_is_four(source_bundle, monkeypatch):
    bundle = source_bundle(8)
    stored = deepcopy(vars(bundle.project))
    stored["target_chapters"] = 4
    context = bundle.context

    async def one(session, model, *criteria):
        return {adapter.native.ProjectModel: stored,
                adapter.native.PlanningArtifactVersionModel: context["parent_artifact"],
                adapter.native.WorkflowRunModel: context["parent_workflow"]}[model]

    async def plan(*args, **kwargs):
        return bundle.native_plan

    async def physical(*args, **kwargs):
        raise AssertionError("an inconsistent native count must stop before custody acquisition")

    monkeypatch.setattr(adapter.native, "_one", one)
    monkeypatch.setattr(adapter, "_read_current_parent_plan", plan)
    monkeypatch.setattr(adapter.native, "_load_artifact_native", physical)
    monkeypatch.setattr(adapter, "frozen_v5_validator", lambda: None)
    with pytest.raises(ValueError):
        await adapter.load_selection_parent(SimpleNamespace(no_autoflush=nullcontext()), None,
            project=bundle.project, assembly=SimpleNamespace(full_text=bundle.full_text, manifest=bundle.manifest),
            parent_artifact_id=context["parent_artifact"]["id"])


def test_parent_context_native_recheck_still_requires_original_paid_proof(source_bundle, monkeypatch):
    bundle = source_bundle()
    context, inputs = bundle.context, bundle.current_inputs
    p = context["parent_report"]["request_payload"]
    for document in (p, inputs):
        document.update(protagonist="值班员", current_scope="whole_story", unlock_line_ratio=0.3,
                        methodology_contract_mode="strict", approved_contract={"approval_evidence": {}})
    context["parent_artifact"]["content"] = deepcopy(context["parent_report"])
    context["actual_project"] = deepcopy(vars(bundle.project))
    context["native_plan"] = deepcopy(bundle.native_plan)
    context["parent_evidence"] = {
        "request_payload": deepcopy(p),
        "completion": {"content": context["parent_report"]["raw_response"]},
        "native": {}, "retained_payloads": {}, "physical_files": {},
    }

    def proof(*args, **kwargs):
        raise PhysicalProofRequired("source-consistent context still needs native physical verification")

    monkeypatch.setattr(adapter.consumer, "_native", proof)
    with pytest.raises(PhysicalProofRequired):
        adapter.verify_parent_context(context, current_inputs=inputs)


def test_frozen_title_replay_uses_archived_operator_and_platform_dependencies():
    from bestseller.services.fanqie_short_semantic_protocol_recovery import V4_SOURCES

    frozen = publication._frozen_title_consumer()
    assert frozen.OPERATOR_CODE_HASH == op.fingerprint({
        "title_operator.py": V4_SOURCES["native_title_operator/title_operator.py"],
        "title_operator_native.py": V4_SOURCES["native_title_operator/title_operator_native.py"],
    })
    assert "whole_semantic_history_v5" in frozen.op.__file__
    assert "whole_semantic_history_v5" in frozen.build_platform_title_workflow.__code__.co_filename
    assert frozen.op.PROJECT_ID == op.PROJECT_ID
    assert frozen.op.SCOPE == op.SCOPE
