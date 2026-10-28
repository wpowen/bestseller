"""RED source-binding checks; paid physical proof remains a separate required gate."""

import ast
from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess

import pytest

from bestseller.services import fanqie_short_semantic_selection_adapter as adapter
from bestseller.services.native_title_operator import title_operator as op

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[3]


def verify(bundle):
    verifier = getattr(adapter, "verify_current_parent_binding", None)
    assert callable(verifier), "missing current native parent binding; original_four is historical identity"
    return verifier(bundle.context, current_inputs=bundle.current_inputs,
                    actual_project=bundle.project, native_plan=bundle.native_plan)


@pytest.mark.parametrize("count", range(4, 9))
def test_general_semantic_parent_binds_all_native_segments_without_relabelling(source_bundle, count):
    bundle = source_bundle(count)
    before = deepcopy(vars(bundle))
    assert verify(bundle) is None
    assert vars(bundle) == before
    assert bundle.context["parent_workflow"]["workflow_type"] == "fanqie_whole_semantic_review"


@pytest.mark.parametrize("fault", [
    "historical_type", "caller_only_n", "foreign_workflow", "foreign_scope", "parent_run",
    "parent_content", "source_plan_binding", "tail_plan_binding", "plan_prefix",
    "tail_manifest", "body_changed", "unchanged_title", "completed_parent", "wrong_scope_id",
])
def test_current_parent_cannot_borrow_old_four_identity_or_stale_source_binding(source_bundle, fault):
    bundle = source_bundle()
    context, inputs = bundle.context, bundle.current_inputs
    workflow = context["parent_workflow"]
    if fault == "historical_type":
        workflow["workflow_type"] = "original_four_whole_semantic_review"
    elif fault == "caller_only_n":
        bundle.project.target_chapters = 4
        inputs["segment_count"] = 8
    elif fault == "foreign_workflow":
        workflow["project_id"] = op.PROJECT_ID
    elif fault == "foreign_scope":
        workflow["metadata_json"]["execution_scope_id"] = op.SCOPE
    elif fault == "parent_run":
        context["parent_artifact"]["source_run_id"] = bundle.canonical["id"]
    elif fault == "parent_content":
        context["parent_report"]["raw_response"] += "changed"
    elif fault == "source_plan_binding":
        workflow["metadata_json"]["source_plan_binding"] = {}
    elif fault == "tail_plan_binding":
        tail = inputs["manifest"]["sources"][-1]["chapter_id"]
        inputs["source_plan_binding"][tail]["source_content_sha256"] = "0" * 64
    elif fault == "plan_prefix":
        bundle.native_plan["content"]["chapters"] = bundle.native_plan["content"]["chapters"][:4]
    elif fault == "tail_manifest":
        inputs["manifest"]["sources"][-1]["raw_body_sha256"] = "0" * 64
    elif fault == "body_changed":
        inputs["full_text"] += "new body"
    elif fault == "unchanged_title":
        inputs["title"] = context["parent_report"]["request_payload"]["title"]
    elif fault == "completed_parent":
        context["parent_artifact"]["status"] = "validated"
        workflow["status"] = "completed"
    else:
        workflow["scope_id"] = bundle.canonical["id"]
    with pytest.raises(ValueError):
        verify(bundle)


@pytest.mark.parametrize("count", [4, 6, 8])
def test_original_four_parent_never_becomes_a_current_generic_authorization(source_bundle, count):
    bundle = source_bundle(count, historical=True)
    with pytest.raises(ValueError):
        verify(bundle)


def test_frozen_paid_parent_dependencies_keep_their_pinned_original_bytes():
    root = ROOT / "src/bestseller/services/whole_semantic_history_v5"
    for name, expected in adapter.V5_SOURCES.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("name", ["prepare", "verify_witness", "validate_paid"])
def test_original_title_request_and_paid_guards_remain_baseline_source(name):
    path = "src/bestseller/services/native_title_operator/title_operator.py"
    baseline = subprocess.check_output(["git", "show", f"refs/tags/e2e17:{path}"], cwd=ROOT, text=True)
    current = (ROOT / path).read_text()
    def function(source):
        node = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
        return ast.get_source_segment(source, node)
    assert function(current) == function(baseline)


def test_original_title_identity_constants_are_not_replaced_by_a_dynamic_n():
    assert op.SCHEMA == "native-title-repair.v1"
    assert op.PROJECT_ID == "fa14e681-2bd9-44f5-93d1-7f2ef9900d69"
    assert op.TASK_ID == "796682cb-9685-54ff-af4a-c6d1ae6340fe"
    assert op.SCOPE == "conception-continuation:" + op.TASK_ID + ":6dd06a4b-825f-5d94-8b4c-0229bf9604af"
