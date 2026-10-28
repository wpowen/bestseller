"""RED contracts for local source integrity, never native paid authorization."""

from __future__ import annotations

import ast
from copy import deepcopy
import inspect
import json
import os
import shutil

from issuer_archive_test_support import (
    RELATIVE_MODULE,
    RELATIVE_PIPELINE,
    aggregate,
    canonical,
    files_below,
    sha,
    tree_state,
)
import pytest

pytestmark = pytest.mark.unit

CAPSULE_KEYS = {
    "schema",
    "archive_ref",
    "archive_sha256",
    "runtime_policy_sha256",
    "python_ast_sha256",
    "non_python_sha256",
}
DENIALS = (
    "grade_granted",
    "new_credit_allowed",
    "dispatch_authorized",
    "adoption_authorized",
    "unknown_reconciled",
    "paid_binding_verified",
    "issued_report_verified",
    "current_applicability_checked",
)
PYTHON_SOURCE = "src/bestseller/services/chapter_draft_candidate_eligibility.py"
NON_PYTHON_SOURCE = "config/quality_gates.yaml"


def assert_denials(receipt):
    for key in DENIALS:
        assert key in receipt and receipt[key] is False, key


def reject(case, code, operation):
    with pytest.raises(
        case.module.IssuerArchiveError, match="^review_issuer_archive:" + code + "$"
    ) as caught:
        operation()
    assert caught.value.code == code


def permit_fixture_tampering(case, path):
    """An adversarial fixture may chmod its temp copy; production never repairs it."""
    boundary = case.artifact_root.parent
    assert path.is_relative_to(boundary)
    for parent in reversed(path.parents):
        if parent.is_relative_to(boundary) and parent.exists() and not parent.is_symlink():
            parent.chmod(parent.stat().st_mode | 0o700)
    if path.exists() and not path.is_symlink():
        path.chmod(path.stat().st_mode | 0o600 | (0o100 if path.is_dir() else 0))


def repack_manifest(case, capsule, change):
    """Construct hostile, self-consistently addressed metadata; never a positive."""
    previous = case.archive(capsule)
    manifest = json.loads((previous / "manifest.json").read_bytes())
    change(manifest)
    raw = canonical(manifest)
    digest = sha(raw)
    destination = case.artifact_root / "review-issuer-archives" / "sha256" / digest
    permit_fixture_tampering(case, destination.parent)
    shutil.copytree(previous, destination)
    permit_fixture_tampering(case, destination / "manifest.json")
    (destination / "manifest.json").write_bytes(raw)
    forged = deepcopy(capsule)
    forged.update(
        archive_sha256=digest, archive_ref=destination.relative_to(case.artifact_root).as_posix()
    )
    return forged


def add_inventory_statement(case, statement):
    path = case.root / RELATIVE_PIPELINE
    original = path.read_text()
    anchor = "    digest = hashlib.sha256()\n"
    assert original.count(anchor) == 1
    path.write_text(original.replace(anchor, "    " + statement + "\n" + anchor, 1))


def test_native_inventory_must_cover_the_archive_primitive(archive_case):
    assert RELATIVE_MODULE in archive_case.originals, (
        "A source capsule cannot attest a verifier omitted from its trusted inventory."
    )


def test_public_api_has_no_inventory_or_authority_override(issuer_module):
    capture = inspect.signature(issuer_module.capture_current_issuer_archive)
    verify = inspect.signature(issuer_module.verify_current_issuer_archive)
    assert list(capture.parameters) == ["settings"]
    assert list(verify.parameters) == ["settings", "capsule"]
    assert verify.parameters["capsule"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("key", ["paths", "root", "epoch", "approved", "runtime_policy_sha256"])
def test_caller_cannot_supply_inventory_epoch_or_permission(archive_case, key):
    with pytest.raises(TypeError):
        archive_case.module.capture_current_issuer_archive(archive_case.settings, **{key: True})
    with pytest.raises(TypeError):
        archive_case.module.verify_current_issuer_archive(
            archive_case.settings, capsule={}, **{key: True}
        )


def test_capture_contains_the_complete_actual_inventory_and_original_bytes(captured):
    case, receipt, capsule = captured
    assert receipt["status"] == "captured"
    assert_denials(receipt)
    assert set(capsule) == CAPSULE_KEYS
    assert capsule["schema"] == "review-issuer-source-capsule.v1"
    assert capsule["runtime_policy_sha256"] == case.runtime_policy
    assert capsule["archive_ref"] == "review-issuer-archives/sha256/" + capsule["archive_sha256"]
    archive = case.archive(capsule)
    raw_manifest = (archive / "manifest.json").read_bytes()
    assert sha(raw_manifest) == capsule["archive_sha256"]
    manifest = json.loads(raw_manifest)
    assert raw_manifest == canonical(manifest)
    assert set(manifest) == {
        "schema",
        "runtime_policy_sha256",
        "python_ast_sha256",
        "non_python_sha256",
        "files",
    }
    assert manifest["schema"] == "review-issuer-source-archive.v1"
    entries = manifest["files"]
    assert [entry["path"] for entry in entries] == sorted(case.originals)
    python_digests, other_digests, raw_digests = {}, {}, {}
    for entry in entries:
        assert set(entry) == {"path", "kind", "sha256", "ast_sha256"}
        relative = entry["path"]
        raw = (archive / "sources" / relative).read_bytes()
        assert raw == case.originals[relative]
        assert sha(raw) == entry["sha256"]
        raw_digests[relative] = entry["sha256"]
        if relative.endswith(".py"):
            normalized = ast.dump(ast.parse(raw, type_comments=True), include_attributes=False)
            assert entry["kind"] == "python"
            assert entry["ast_sha256"] == sha(normalized.encode())
            python_digests[relative] = entry["ast_sha256"]
        else:
            assert entry["kind"] == "non_python" and entry["ast_sha256"] is None
            other_digests[relative] = entry["sha256"]
    assert aggregate(raw_digests) == capsule["runtime_policy_sha256"]
    assert aggregate(python_digests) == capsule["python_ast_sha256"]
    assert aggregate(other_digests) == capsule["non_python_sha256"]
    for key in ("runtime_policy_sha256", "python_ast_sha256", "non_python_sha256"):
        assert manifest[key] == capsule[key]
    assert set(files_below(archive)) == {"manifest.json"} | {
        "sources/" + relative for relative in case.originals
    }


def test_capture_reuses_identical_content_without_rewriting_originals(captured):
    case, receipt, _ = captured
    before = files_below(case.artifact_root)
    times = {p: p.stat().st_mtime_ns for p in case.artifact_root.rglob("*") if p.is_file()}
    again = case.capture()
    assert again == receipt
    assert files_below(case.artifact_root) == before
    assert {p: p.stat().st_mtime_ns for p in times} == times


def test_verification_is_read_only_and_grants_no_native_authority(captured):
    case, _, capsule = captured
    before = tree_state(case.artifact_root), tree_state(case.root)
    result = case.verify(capsule)
    assert result["status"] == "equivalent"
    assert result["byte_identical"] is True
    assert result["issued_runtime_policy_sha256"] == case.runtime_policy
    assert result["current_runtime_policy_sha256"] == case.runtime_policy
    assert_denials(result)
    assert (tree_state(case.artifact_root), tree_state(case.root)) == before


def test_comment_upgrade_preserves_ast_but_keeps_both_full_runtime_hashes(captured):
    case, _, capsule = captured
    path = case.root / PYTHON_SOURCE
    path.write_bytes(path.read_bytes() + b"\n# issuer source archive comment-only upgrade\n")
    current = {p: sha((case.root / p).read_bytes()) for p in case.originals}
    assert aggregate(current) != case.runtime_policy
    before = files_below(case.artifact_root)
    result = case.verify(capsule)
    assert result["status"] == "equivalent" and result["byte_identical"] is False
    assert result["issued_runtime_policy_sha256"] == case.runtime_policy
    assert result["current_runtime_policy_sha256"] == aggregate(current)
    assert result["python_ast_sha256"] == capsule["python_ast_sha256"]
    assert result["non_python_sha256"] == capsule["non_python_sha256"]
    assert_denials(result)
    assert files_below(case.artifact_root) == before


@pytest.mark.parametrize(
    "addition", [b"\nISSUER_TEST_MUTATION = True\n", b'\n"changed docstring constant"\n']
)
def test_any_python_ast_change_is_rejected(captured, addition):
    case, _, capsule = captured
    path = case.root / PYTHON_SOURCE
    path.write_bytes(path.read_bytes() + addition)
    reject(case, "python_ast_changed", lambda: case.verify(capsule))


@pytest.mark.parametrize("addition", [b"\n# a YAML comment is still a byte change\n", b"\n"])
def test_non_python_requires_exact_bytes_even_for_comments(captured, addition):
    case, _, capsule = captured
    path = case.root / NON_PYTHON_SOURCE
    path.write_bytes(path.read_bytes() + addition)
    reject(case, "non_python_changed", lambda: case.verify(capsule))


@pytest.mark.parametrize("change", ["added", "removed"])
def test_actual_pipeline_inventory_path_set_changes_are_rejected(captured, change):
    case, _, capsule = captured
    if change == "added":
        (case.root / "src/bestseller/services/issuer_unlisted_extra.py").write_text("VALUE = 1\n")
        add_inventory_statement(case, 'policy_files.add(services / "issuer_unlisted_extra.py")')
    else:
        path = case.root / RELATIVE_PIPELINE
        original = path.read_text()
        entry = '"chapter_writer_delivery.py", '
        assert original.count(entry) == 1
        path.write_text(original.replace(entry, "", 1))
    reject(case, "inventory_changed", lambda: case.verify(capsule))


@pytest.mark.parametrize("name", [".py", "issuer_new_dependency.py"])
def test_native_fixed_quality_glob_does_not_ignore_matching_new_files(captured, name):
    case, _, capsule = captured
    (case.root / "src/bestseller/services/quality_levers" / name).write_text("VALUE = 1\n")
    reject(case, "inventory_changed", lambda: case.verify(capsule))


@pytest.mark.parametrize("name", [".py", "issuer_directory.py"])
def test_native_fixed_quality_glob_does_not_ignore_matching_directories(archive_case, name):
    case = archive_case
    (case.root / "src/bestseller/services/quality_levers" / name).mkdir()
    reject(case, "source_missing", case.capture)
    assert files_below(case.artifact_root) == {}


def test_unknown_inventory_expression_is_not_executed_or_silently_dropped(archive_case):
    case = archive_case
    marker = case.root / "must-not-execute"
    add_inventory_statement(
        case,
        f'policy_files.update(__import__("pathlib").Path({str(marker)!r}).write_text("bad"))',
    )
    reject(case, "inventory_unsupported", case.capture)
    assert not marker.exists()
    assert files_below(case.artifact_root) == {}


def test_changed_pipeline_hash_algorithm_is_not_claimed_as_native_identity(archive_case):
    case = archive_case
    path = case.root / RELATIVE_PIPELINE
    raw = path.read_text()
    start = raw.index("def chapter_review_recovery_source_hash()")
    end = raw.index("\n\nasync def ", start)
    function = raw[start:end]
    assert function.count("return digest.hexdigest()") == 1
    altered = function.replace("return digest.hexdigest()", 'return "not-the-native-policy-hash"')
    path.write_text(raw[:start] + altered + raw[end:])
    reject(case, "inventory_unsupported", case.capture)
    assert files_below(case.artifact_root) == {}


def test_hidden_conditional_inventory_mutation_is_not_ignored(archive_case):
    case = archive_case
    add_inventory_statement(
        case, 'if True:\n        policy_files.add(services / "conditional_extra.py")'
    )
    reject(case, "inventory_unsupported", case.capture)
    assert files_below(case.artifact_root) == {}


def test_native_inventory_cannot_read_a_parent_path(archive_case):
    case = archive_case
    add_inventory_statement(case, 'policy_files.add(root / "../must-not-read.env")')
    reject(case, "inventory_unsupported", case.capture)
    assert files_below(case.artifact_root) == {}


def test_genre_completeness_guard_runs_before_later_inventory_additions(archive_case):
    case = archive_case
    path = case.root / "config/genre_profile_thresholds/issuer_late.yaml"
    path.write_text("fixture: true\n")
    add_inventory_statement(
        case, 'policy_files.add(root / "config/genre_profile_thresholds/issuer_late.yaml")'
    )
    reject(case, "inventory_unsupported", case.capture)
    assert files_below(case.artifact_root) == {}


def test_capture_refuses_an_inventory_that_omits_the_primitive(archive_case):
    case = archive_case
    path = case.root / RELATIVE_PIPELINE
    raw = path.read_text()
    # The native baseline is currently uncovered. If main registers it later,
    # this NEGATIVE removes only that actual entry; no positive adds an entry.
    # Replacing the literal with an already-required path covers both a tuple
    # entry and a native policy_files.add(...) registration without syntax edits.
    raw = raw.replace("review_issuer_archive.py", "pipelines.py")
    path.write_text(raw)
    reject(case, "inventory_self_uncovered", case.capture)
    assert files_below(case.artifact_root) == {}


def test_missing_native_source_is_rejected_before_archive_publication(archive_case):
    case = archive_case
    (case.root / PYTHON_SOURCE).unlink()
    reject(case, "source_missing", case.capture)
    assert files_below(case.artifact_root) == {}


@pytest.mark.parametrize("component", ["file", "parent", "pipeline", "root"])
def test_source_symlinks_are_rejected_even_when_target_bytes_match(archive_case, component):
    case = archive_case
    if component == "file":
        path = case.root / PYTHON_SOURCE
    elif component == "pipeline":
        path = case.root / RELATIVE_PIPELINE
    elif component == "root":
        path = case.root
    else:
        path = case.root / "config/genre_profile_thresholds"
    target = path.with_name(path.name + ".real")
    path.rename(target)
    path.symlink_to(target, target_is_directory=target.is_dir())
    reject(case, "symlink", case.capture)
    assert files_below(case.artifact_root) == {}


def test_capture_does_not_publish_through_a_symbolic_artifact_store(archive_case):
    case = archive_case
    target = case.artifact_root.with_name("real-artifacts")
    target.mkdir()
    case.artifact_root.symlink_to(target, target_is_directory=True)
    reject(case, "symlink", case.capture)
    assert files_below(target) == {}


def test_unparseable_python_is_rejected_without_publishing_partial_archive(archive_case):
    case = archive_case
    (case.root / PYTHON_SOURCE).write_bytes(b"def broken(:\n")
    reject(case, "source_invalid", case.capture)
    assert files_below(case.artifact_root) == {}


@pytest.mark.parametrize("key", ["api_key", "password", "access_token"])
def test_secret_config_values_are_not_archived_or_echoed(archive_case, key):
    case = archive_case
    sentinel = "issuer-fixture-credential-must-never-be-retained"
    path = case.root / NON_PYTHON_SOURCE
    path.write_bytes(
        path.read_bytes() + f'\nfixture_credentials:\n  {key}: "{sentinel}"\n'.encode()
    )
    with pytest.raises(
        case.module.IssuerArchiveError, match="^review_issuer_archive:secret_config$"
    ) as caught:
        case.capture()
    assert sentinel not in str(caught.value)
    assert sentinel not in repr(getattr(caught.value, "differences", None))
    assert files_below(case.artifact_root) == {}


def test_duplicate_inline_yaml_cannot_hide_a_secret_before_archival(archive_case):
    case = archive_case
    sentinel = "fixture-secret-hidden-by-yaml-overwrite"
    path = case.root / NON_PYTHON_SOURCE
    path.write_bytes(
        path.read_bytes()
        + f'\nfixture_credentials: {{api_key: "{sentinel}", api_key: null}}\n'.encode()
    )
    with pytest.raises(case.module.IssuerArchiveError) as caught:
        case.capture()
    assert caught.value.code in {"secret_config", "source_invalid"}
    assert sentinel not in str(caught.value)
    assert files_below(case.artifact_root) == {}


@pytest.mark.parametrize(
    "fragment",
    [
        'fixture_credentials: {api_key: !!null "fixture-secret-null-tag"}',
        'fixture_credentials: {<<: {api_key_env: "fixture-secret-in-merge"}, '
        "api_key_env: MINIMAX_API_KEY}",
    ],
)
def test_original_yaml_tags_and_merges_cannot_hide_credentials(archive_case, fragment):
    case = archive_case
    path = case.root / NON_PYTHON_SOURCE
    path.write_bytes(path.read_bytes() + ("\n" + fragment + "\n").encode())
    reject(case, "secret_config", case.capture)
    assert files_below(case.artifact_root) == {}


def test_application_env_in_actual_inventory_is_rejected_not_redacted(archive_case):
    case = archive_case
    (case.root / "application.env").write_text("API_KEY=fixture-not-a-real-key\n")
    add_inventory_statement(case, 'policy_files.add(root / "application.env")')
    reject(case, "secret_config", case.capture)
    assert files_below(case.artifact_root) == {}


@pytest.mark.parametrize("fault", ["missing", "changed", "extra", "extra_empty_directory"])
def test_missing_changed_or_extra_archive_material_is_rejected(captured, fault):
    case, _, capsule = captured
    archive = case.archive(capsule)
    path = archive / "sources" / PYTHON_SOURCE
    permit_fixture_tampering(case, path)
    permit_fixture_tampering(case, archive)
    if fault == "missing":
        path.unlink()
    elif fault == "changed":
        path.write_bytes(path.read_bytes() + b"\n# same AST, wrong original bytes\n")
    elif fault == "extra":
        (archive / "unlisted-extra.txt").write_text("not covered by the manifest")
    else:
        (archive / "unlisted-empty-directory").mkdir()
    reject(case, "archive_integrity", lambda: case.verify(capsule))


@pytest.mark.parametrize("component", ["source", "manifest", "archive_root", "artifact_root"])
def test_archive_symlinks_are_rejected(captured, component):
    case, _, capsule = captured
    archive = case.archive(capsule)
    paths = {
        "source": archive / "sources" / PYTHON_SOURCE,
        "manifest": archive / "manifest.json",
        "archive_root": archive,
        "artifact_root": case.artifact_root,
    }
    path = paths[component]
    permit_fixture_tampering(case, path)
    target = path.with_name(path.name + ".real")
    path.rename(target)
    path.symlink_to(target, target_is_directory=target.is_dir())
    reject(case, "symlink", lambda: case.verify(capsule))


@pytest.mark.parametrize(
    "fault", ["unknown_field", "missing_file_entry", "duplicate_path", "wrong_raw_digest"]
)
def test_self_consistently_readdressed_hostile_manifest_is_rejected(captured, fault):
    case, _, capsule = captured

    def change(manifest):
        if fault == "unknown_field":
            manifest["approved"] = True
        elif fault == "missing_file_entry":
            manifest["files"].pop()
        elif fault == "duplicate_path":
            manifest["files"].append(deepcopy(manifest["files"][0]))
        else:
            manifest["files"][0]["sha256"] = "0" * 64

    forged = repack_manifest(case, capsule, change)
    reject(case, "archive_integrity", lambda: case.verify(forged))


@pytest.mark.parametrize(
    "key",
    [
        "runtime_policy_sha256",
        "python_ast_sha256",
        "non_python_sha256",
        "approved",
        "grade_granted",
    ],
)
def test_caller_capsule_digest_or_authority_claim_is_not_trusted(captured, key):
    case, _, capsule = captured
    forged = {**capsule, key: "0" * 64 if key.endswith("sha256") else True}
    reject(case, "capsule_invalid", lambda: case.verify(forged))


@pytest.mark.parametrize(
    "reference",
    [
        "../escape",
        "/absolute/archive",
        "https://example.invalid/archive",
        "review-issuer-archives/sha256/../escape",
    ],
)
def test_archive_reference_cannot_escape_local_content_address(captured, reference):
    case, _, capsule = captured
    reject(case, "capsule_invalid", lambda: case.verify({**capsule, "archive_ref": reference}))


def test_corrupt_original_is_never_repaired_or_overwritten_by_recapture(captured):
    case, _, capsule = captured
    path = case.archive(capsule) / "sources" / PYTHON_SOURCE
    permit_fixture_tampering(case, path)
    path.write_bytes(path.read_bytes() + b"\n# corruption must remain visible\n")
    before = files_below(case.artifact_root)
    reject(case, "archive_integrity", case.capture)
    assert files_below(case.artifact_root) == before


def test_archive_python_bytes_are_never_imported_or_executed(captured):
    case, _, capsule = captured
    marker = case.artifact_root.parent / "archive-python-executed"
    path = case.archive(capsule) / "sources" / PYTHON_SOURCE
    permit_fixture_tampering(case, path)
    path.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n')
    reject(case, "archive_integrity", lambda: case.verify(capsule))
    assert not marker.exists()


def test_validly_readdressed_archive_code_is_data_until_ast_rejection(captured):
    case, _, capsule = captured
    marker = case.artifact_root.parent / "valid-archive-python-executed"
    payload = f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n'.encode()

    def change(manifest):
        entry = next(e for e in manifest["files"] if e["path"] == PYTHON_SOURCE)
        entry["sha256"] = sha(payload)
        entry["ast_sha256"] = sha(
            ast.dump(ast.parse(payload, type_comments=True), include_attributes=False).encode()
        )
        manifest["runtime_policy_sha256"] = aggregate(
            {e["path"]: e["sha256"] for e in manifest["files"]}
        )
        manifest["python_ast_sha256"] = aggregate(
            {e["path"]: e["ast_sha256"] for e in manifest["files"] if e["kind"] == "python"}
        )

    forged = repack_manifest(case, capsule, change)
    archive = case.archive(forged)
    path = archive / "sources" / PYTHON_SOURCE
    permit_fixture_tampering(case, path)
    path.write_bytes(payload)
    manifest = json.loads((archive / "manifest.json").read_bytes())
    for key in ("runtime_policy_sha256", "python_ast_sha256", "non_python_sha256"):
        forged[key] = manifest[key]
    reject(case, "python_ast_changed", lambda: case.verify(forged))
    assert not marker.exists()


def test_publication_never_replaces_a_foreign_empty_content_address(archive_case, monkeypatch):
    case = archive_case
    original = os.mkdir
    foreign = []

    def competitor(path, *args, **kwargs):
        name = os.fspath(path)
        if len(name) == 64 and all(c in "0123456789abcdef" for c in name):
            original(path, *args, **kwargs)
            foreign.append((name, os.stat(path, dir_fd=kwargs.get("dir_fd")).st_ino))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", competitor)
    reject(case, "archive_integrity", case.capture)
    assert len(foreign) == 1
    path = case.artifact_root / "review-issuer-archives/sha256" / foreign[0][0]
    assert path.stat().st_ino == foreign[0][1]
    assert list(path.iterdir()) == []


@pytest.mark.parametrize("operation", ["capture", "verify"])
def test_non_local_store_never_falls_back_to_network(captured, operation):
    case, _, capsule = captured
    before = files_below(case.artifact_root)
    case.settings.artifact_store.mode = "s3"
    call = case.capture if operation == "capture" else lambda: case.verify(capsule)
    reject(case, "non_local_store", call)
    assert files_below(case.artifact_root) == before
