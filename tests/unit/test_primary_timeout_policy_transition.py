"""Exact transition proof: real frozen sources, isolated files, no DB/LLM IO."""

from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[2]
PREIMAGES = (
    ROOT / "reports/project-reliability-review-2026-10-24/implementation/primary-timeout-preimages"
)
FROZEN_RELEASE = PREIMAGES.parent / "primary-timeout-frozen-release"
SPEC = importlib.util.spec_from_file_location(
    "primary_timeout_policy_source",
    ROOT / "reports/framework-recovery-2026-10-21/primary_timeout_policy_source.py",
)
proof_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proof_module)


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="module")
def baseline():
    data = json.loads((PREIMAGES / "policy-files.json").read_bytes())
    data["sources"] = {
        name: (PREIMAGES / name).read_bytes().decode("utf-8")
        for name in proof_module.PREIMAGE_FILES
    }
    return data


@pytest.fixture(scope="module")
def source_template(tmp_path_factory, baseline):
    """Real 262-file baseline with only the specified four-file transition."""
    root = tmp_path_factory.mktemp("primary-timeout-source-template")
    for name in baseline["files"]:
        data = (
            baseline["sources"][name].encode("utf-8")
            if name in baseline["sources"]
            else (PREIMAGES / name).read_bytes()
        )
        assert sha(data) == baseline["files"][name], name
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for name, source in baseline["sources"].items():
        if name == proof_module.LLM:
            anchor = "    " + proof_module._RETAINED_IMPORT
            assert source.count(anchor) == 1
            source = source.replace(
                anchor,
                "    " + proof_module._IMPORT + "\n    " + proof_module._CALL + "\n" + anchor,
            )
        elif name == proof_module.PIPELINES:
            anchor = '"primary_review_submission.py", "primary_review_transport.py",'
            assert source.count(anchor) == 1
            source = source.replace(anchor, anchor + ' "chapter_primary_timeout_recovery.py",')
        else:
            anchor = '    "src/bestseller/services/runtime_provenance.py",'
            assert source.count(anchor) == 1
            source = source.replace(anchor, anchor + '\n    "' + proof_module.HELPER + '",')
        (root / name).write_bytes(source.encode("utf-8"))
    # A deterministic fixture hash is caller-frozen; helper behavior is outside
    # this source transition proof's remit and never imported or executed.
    (root / proof_module.HELPER).write_bytes(b'"""Frozen helper fixture; never imported."""\n')
    return root


@pytest.fixture
def source_root(tmp_path, source_template):
    return Path(shutil.copytree(source_template, tmp_path / "source"))


def verify(root, baseline, **kwargs):
    return proof_module.verify_source_transition(
        root,
        helper_sha256=sha((root / proof_module.HELPER).read_bytes()),
        baseline_data=baseline,
        **kwargs,
    )


def edit(root, name, before, after):
    path = root / name
    source = path.read_text()
    assert before in source
    path.write_text(source.replace(before, after, 1))


def test_embedded_baseline_reconstructs_old_and_actual_new_function(source_root, baseline):
    original_baseline = deepcopy(baseline)
    before = {
        str(p.relative_to(source_root)): sha(p.read_bytes())
        for p in source_root.rglob("*")
        if p.is_file()
    }
    proof = verify(source_root, baseline)
    assert proof["verified"] is True
    assert proof["old_policy"] == proof_module.OLD_POLICY
    assert proof["old_files"] == baseline["files"]
    assert proof["old_file_count"] == 262
    assert proof["new_file_count"] == 263
    assert proof["changed_files"] == sorted((*proof_module.PREIMAGE_FILES, proof_module.HELPER))
    assert proof["added_files"] == [proof_module.HELPER]
    assert proof["new_policy"] != proof["old_policy"]
    # Independently execute the *actual current* function with standard Path,
    # rather than sharing the verifier's overlay/recording implementation.
    tree = ast.parse((source_root / proof_module.PIPELINES).read_bytes())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "chapter_review_recovery_source_hash"
    )
    namespace = {
        "Path": Path,
        "hashlib": hashlib,
        "__file__": str(source_root / proof_module.PIPELINES),
    }
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "<actual-policy>", "exec"), namespace
    )
    assert proof["new_policy"] == namespace[function.name]()
    assert baseline == original_baseline
    assert before == {
        str(p.relative_to(source_root)): sha(p.read_bytes())
        for p in source_root.rglob("*")
        if p.is_file()
    }


def test_disk_baseline_matches_embedded_without_reports_under_root(source_root, baseline):
    assert not (source_root / "reports").exists()
    disk = proof_module.verify_source_transition(
        source_root,
        PREIMAGES,
        helper_sha256=sha((source_root / proof_module.HELPER).read_bytes()),
    )
    assert disk == verify(source_root, baseline)


def test_frozen_helper_hash_is_required(source_root, baseline):
    frozen = sha((source_root / proof_module.HELPER).read_bytes())
    (source_root / proof_module.HELPER).write_bytes(b"CHANGED = True\n")
    with pytest.raises(ValueError, match="frozen_helper_hash_mismatch"):
        proof_module.verify_source_transition(
            source_root, helper_sha256=frozen, baseline_data=baseline
        )


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("policy", "baseline_policy_not_cycle_policy"),
        ("missing_inventory", "complete_262_file_inventory_required"),
        ("inventory_hash", "baseline_inventory_not_cycle_policy"),
        ("preimage", "preimage_hash_mismatch"),
        ("extra_preimage", "three_exact_preimages_required"),
    ],
)
def test_tampered_embedded_baseline_fails_closed(source_root, baseline, mutation, reason):
    data = deepcopy(baseline)
    if mutation == "policy":
        data["policy"] = "0" * 64
    elif mutation == "missing_inventory":
        data["files"].pop(next(iter(data["files"])))
    elif mutation == "inventory_hash":
        data["files"][proof_module.LLM] = "0" * 64
    elif mutation == "preimage":
        data["sources"][proof_module.RUNTIME] += "\n# forged preimage\n"
    else:
        data["sources"]["extra.py"] = ""
    with pytest.raises(ValueError, match=reason):
        verify(source_root, data)


@pytest.mark.parametrize(
    "name,before,after,reason",
    [
        (
            proof_module.LLM,
            proof_module._IMPORT,
            proof_module._IMPORT + "\n    import os",
            "exact_guard_call_required",
        ),
        (
            proof_module.LLM,
            proof_module._CALL,
            proof_module._CALL.replace("settings,", "role_settings,"),
            "exact_guard_call_required",
        ),
        (
            proof_module.LLM,
            proof_module._CALL,
            "request = await guard_primary_timeout_request("
            "session, settings, request, prompt_hash, semantic_namespace)",
            "exact_guard_call_required",
        ),
        (
            proof_module.LLM,
            proof_module._CALL,
            proof_module._CALL.replace("role_settings=role_settings", "role_settings=settings"),
            "exact_guard_call_required",
        ),
        (
            proof_module.LLM,
            proof_module._CALL,
            proof_module._CALL.replace(
                "effective_payload=_effective_input_payload(request, role_settings)",
                "effective_payload=_effective_input_payload(request, settings)",
            ),
            "exact_guard_call_required",
        ),
        (
            proof_module.LLM,
            proof_module._CALL,
            proof_module._CALL.replace(
                ", effective_payload=_effective_input_payload(request, role_settings)", ""
            ),
            "exact_guard_call_required",
        ),
        (
            proof_module.LLM,
            proof_module._IMPORT,
            proof_module._IMPORT.replace(" as guard_primary_timeout_request", ""),
            "exact_guard_import_required",
        ),
        (
            proof_module.LLM,
            proof_module._CALL,
            proof_module._CALL.replace("request = ", ""),
            "exact_guard_call_required",
        ),
        (
            proof_module.LLM,
            proof_module._CALL,
            proof_module._CALL + "\n    request = request.model_copy()",
            "guard_position_required",
        ),
        (
            proof_module.LLM,
            "await guard_retained_request(session, request, role_settings, prompt_hash)",
            "await guard_retained_request(session, request, settings, prompt_hash)",
            "llm_ast_changed_outside_exact_hook",
        ),
        (
            proof_module.LLM,
            "_warn_language_system_mismatch(request)",
            "pass",
            "llm_ast_changed_outside_exact_hook",
        ),
        (
            proof_module.PIPELINES,
            '"chapter_primary_timeout_recovery.py",',
            '"chapter_primary_timeout_recovery.py", "other_helper.py",',
            "inventory_ast_changed_outside_helper_literal",
        ),
        (
            proof_module.PIPELINES,
            '"chapter_primary_timeout_recovery.py",',
            '"chapter_primary_timeout_recovery.py", "chapter_primary_timeout_recovery.py",',
            "exactly_one_helper_literal_required",
        ),
        (
            proof_module.PIPELINES,
            "digest = hashlib.sha256()",
            "digest = hashlib.sha512()",
            "inventory_ast_changed_outside_helper_literal",
        ),
        (
            proof_module.RUNTIME,
            '"' + proof_module.HELPER + '",',
            '"' + proof_module.HELPER + '", "src/bestseller/services/other.py",',
            "inventory_ast_changed_outside_helper_literal",
        ),
        (
            proof_module.RUNTIME,
            "import hashlib",
            "import hashlib\nimport socket",
            "inventory_ast_changed_outside_helper_literal",
        ),
    ],
)
def test_only_exact_ast_deltas_allowed(source_root, baseline, name, before, after, reason):
    edit(source_root, name, before, after)
    with pytest.raises(ValueError, match=reason):
        verify(source_root, baseline)


def test_same_two_hook_nodes_in_wrong_position_rejected(source_root, baseline):
    path = source_root / proof_module.LLM
    tree = ast.parse(path.read_bytes())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_complete_text_impl"
    )
    index = next(
        i
        for i, node in enumerate(function.body)
        if isinstance(node, ast.ImportFrom)
        and node.module == "bestseller.services.chapter_primary_timeout_recovery"
    )
    nodes = function.body[index : index + 2]
    del function.body[index : index + 2]
    function.body[index - 1 : index - 1] = nodes
    path.write_text(ast.unparse(tree))
    with pytest.raises(ValueError, match="exact_guard_import_required"):
        verify(source_root, baseline)


def test_other_policy_source_bytes_cannot_drift_even_if_ast_same(source_root, baseline):
    # A comment is harmless AST-wise, but is still an unapproved fifth source
    # delta in the policy inventory and must invalidate the old reconstruction.
    name = "src/bestseller/services/chapter_review_preflight.py"
    path = source_root / name
    path.write_bytes(path.read_bytes() + b"\n# unapproved fifth file\n")
    with pytest.raises(ValueError, match="old_actual_inventory_or_source_mismatch"):
        verify(source_root, baseline)


def test_extra_globbed_policy_file_cannot_hide_outside_manifest(source_root, baseline):
    path = source_root / "src/bestseller/services/quality_levers/unapproved.py"
    path.write_text("UNAPPROVED = True\n")
    with pytest.raises(ValueError, match="old_actual_inventory_or_source_mismatch"):
        verify(source_root, baseline)


def test_missing_helper_is_failure(source_root, baseline):
    frozen = sha((source_root / proof_module.HELPER).read_bytes())
    (source_root / proof_module.HELPER).unlink()
    with pytest.raises(ValueError, match="unreadable_or_invalid_source"):
        proof_module.verify_source_transition(
            source_root, helper_sha256=frozen, baseline_data=baseline
        )


@pytest.mark.parametrize("frozen", [None, "", "0" * 63, "G" * 64])
def test_invalid_caller_frozen_hash_rejected(source_root, baseline, frozen):
    with pytest.raises(ValueError, match="invalid_frozen_helper_hash"):
        proof_module.verify_source_transition(
            source_root, helper_sha256=frozen, baseline_data=baseline
        )


def test_source_symlink_cannot_escape_root(source_root, baseline, tmp_path):
    name = "src/bestseller/services/chapter_review_preflight.py"
    path = source_root / name
    external = tmp_path / "outside.py"
    external.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(ValueError, match="source_path_escapes_root"):
        verify(source_root, baseline)


def test_baseline_inputs_are_mutually_exclusive(source_root, baseline):
    frozen = sha((source_root / proof_module.HELPER).read_bytes())
    with pytest.raises(ValueError, match="select_exactly_one_baseline_input"):
        proof_module.verify_source_transition(
            source_root, PREIMAGES, helper_sha256=frozen, baseline_data=baseline
        )
    with pytest.raises(ValueError, match="select_exactly_one_baseline_input"):
        proof_module.verify_source_transition(source_root, helper_sha256=frozen)


def test_frozen_release_exact_ast_and_actual_old_policy_without_helper(baseline):
    old = {name: ast.parse(source) for name, source in baseline["sources"].items()}
    current = {name: ast.parse((FROZEN_RELEASE / name).read_bytes()) for name in proof_module.PREIMAGE_FILES}
    proof_module._strip_exact_hook(old[proof_module.LLM], current[proof_module.LLM])
    proof_module._strip_single_literal(
        old[proof_module.PIPELINES], current[proof_module.PIPELINES], runtime=False
    )
    proof_module._strip_single_literal(
        old[proof_module.RUNTIME], current[proof_module.RUNTIME], runtime=True
    )
    old_policy, files = proof_module._run_policy(
        FROZEN_RELEASE,
        old[proof_module.PIPELINES],
        {name: source.encode("utf-8") for name, source in baseline["sources"].items()},
    )
    assert old_policy == proof_module.OLD_POLICY
    assert files == baseline["files"]


def test_frozen_integrated_source_transition(baseline):
    helper = FROZEN_RELEASE / proof_module.HELPER
    assert helper.is_file()
    proof = verify(FROZEN_RELEASE, baseline)
    assert proof["old_policy"] == proof_module.OLD_POLICY
    # Run the deployed release's real policy function with standard Path/globals;
    # importing pipelines would unnecessarily load the application graph.
    tree = ast.parse((FROZEN_RELEASE / proof_module.PIPELINES).read_bytes())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "chapter_review_recovery_source_hash"
    )
    namespace = {"Path": Path, "hashlib": hashlib, "__file__": str(FROZEN_RELEASE / proof_module.PIPELINES)}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "<actual-policy>", "exec"), namespace
    )
    assert proof["new_policy"] == namespace[function.name]()
    assert proof["new_policy"] == json.loads((FROZEN_RELEASE / "RELEASE.json").read_text())["chapter_review_policy"]
