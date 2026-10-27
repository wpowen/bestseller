"""Exact issued fifth-epoch contract, replayed from its immutable initializer.

The current sixth candidate is covered separately by epoch4 tests. This suite
keeps its literal five-endpoint/ten-edge and historical depth-three/four checks;
the issued 1a5a source context replaces mutable current source reads only.
Historical edges are synthetic dictionaries, not production receipt evidence.
"""

import ast
from copy import deepcopy
import hashlib
from itertools import combinations
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

from bestseller.services import chapter_review_policy_history_8y24 as archive
from bestseller.services import chapter_review_reconciliation as reconciliation

pytestmark = pytest.mark.unit

JUDGE = "src/bestseller/services/commercial_judge_recovery.py"
CONTROL = "src/bestseller/services/execution_control_purpose.py"
RECONCILIATION = "src/bestseller/services/chapter_review_reconciliation.py"
RECONCILIATION_SHA256 = "1af250591dfb6cbd1fb014a6633332e383111e4c17ad2738ba7757fef36f1560"
C90A_POLICY = "c90a6d32172332d754ca4d59d39cd109e42b0b8444fcfa456ea73522a8aa9796"
C90A_PINS = {
    "final.json": (
        "C90A_SHA256",
        "5a2697cc87cb330f9057160de77696d67eaab50d18aa67f91b2b0db5f2184d7d",
    ),
    "__init__.py": (
        "C90A_INITIALIZER_SHA256",
        "ab82c88d9b8eb793fcd1dcc8ec28e9fd355ad7fd1d35db1cad5af87d2c7808e0",
    ),
    "manifest.json": (
        "C90A_INDEX_SHA256",
        "dc30432eee11411ae979af3f5a85c4bc93c68fd847bba6304d4e4f724df196dd",
    ),
    "known-forward-edges.json": (
        "C90A_EDGES_SHA256",
        "a1f55ecb445dc7ff40e69dc508adbfafb6a1b681921be1273af7acbbadfde32f",
    ),
}
HISTORICAL = (
    (
        "original.json",
        "ORIGINAL_SHA256",
        "OLD_POLICY",
        "original_bytes",
        "318cde073c31d22cbc46c0782f014a9afe1c864abe2ec9b3d714792079a1525b",
        "1fd308264eae63f0f7ab761da701a50e971014d9e693c3c2bcec98181b111b3c",
    ),
    (
        "current.json",
        "CURRENT_SHA256",
        "PRIOR_POLICY",
        "prior_bytes",
        "8dd6490f0dcf9984b98484ee73b582ad4e7e80a2dca9b79dff1ba91b1b6a685c",
        "db1b38eba0e68ad0bc0b431329865aa55ec7ddd5cb0c37c426031a745a1fd634",
    ),
    (
        "a296-final.json",
        "A296_SHA256",
        "A296_POLICY",
        "a296_bytes",
        "ad84ee5bf4a0fcbee4a1d55ea39ef7d5039dc32718c2cd1ccf5c794bc55f3371",
        "a2962a25cf438282381bc1e432a46496a6e2315148895f477f1f2fbc51b6623a",
    ),
    (
        "unissued-3e51/final.json",
        "UNISSUED_SHA256",
        "UNISSUED_POLICY",
        "unissued_bytes",
        "78c202ca2204c2c12477558b2f590c294e2ee8f79d2e3de62dc19d1107e7e7c6",
        "3e51eb1808c2f9970d6b668a5e27643a096c7522b59cc98fa464cc313022f40f",
    ),
)


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    import socket
    import sqlite3

    import aiosqlite
    from sqlalchemy.engine import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.pool import Pool

    from bestseller.services import llm

    def denied(*args, **kwargs):
        raise AssertionError("Epoch3 tests forbid actual DB, provider and network IO")

    for cls in (Engine, AsyncEngine):
        for method in ("connect", "begin", "raw_connection"):
            monkeypatch.setattr(cls, method, denied)
    monkeypatch.setattr(Pool, "connect", denied)
    monkeypatch.setattr(sqlite3, "connect", denied)
    monkeypatch.setattr(sqlite3.dbapi2, "connect", denied)
    monkeypatch.setattr(aiosqlite, "connect", denied)
    monkeypatch.setattr(llm, "complete_text", denied)
    for owner, method in (
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
    ):
        monkeypatch.setattr(owner, method, denied)


@pytest.fixture(autouse=True)
def exact_issued_fifth_context(monkeypatch, tmp_path, request):
    """Run the original contract against exact issued code and all preimages.

    In particular, EPOCH3=None tests exercise the archived five-epoch loader,
    rather than trying to select a four-epoch branch in a six-epoch loader.
    Nothing is captured from current runtime or silently skipped.
    """
    active = request.module.archive
    base = active.HERE / "issued-1a5a"
    source = (base / "__init__.py").read_bytes()
    assert hashlib.sha256(source).hexdigest() == (
        "5e9988639c3ce2de09abe8f2d169d101815380862b5d0c2ec7b46edfe50709bc"
    )
    assert hashlib.sha256((base / "final.json").read_bytes()).hexdigest() == (
        "98d4f5ed89927c5235eebcea7e42c92edc613ad2f19464b0a5ef0d18378db1bb"
    )
    historical = ModuleType(active.__name__)
    historical.__file__ = str(active.HERE / "__init__.py")
    exec(compile(source, str(base / "__init__.py"), "exec"), historical.__dict__)
    historical.ROOT = tmp_path / "issued-1a5a-runtime"
    read = Path.read_bytes

    def issued_source(path):
        if path == active.HERE / "final.json":
            return read(base / "final.json")
        if path.is_relative_to(historical.ROOT):
            relative = path.relative_to(historical.ROOT).as_posix()
            return read(base / "bytes" / (relative + ".source"))
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", issued_source)
    monkeypatch.setattr(request.module, "archive", historical)
    # Reconciliation resolves these selectors inside its functions, so its
    # native alias checks use the same exact issued policy context.
    monkeypatch.setitem(sys.modules, active.__name__, historical)


def _files(manifest):
    return {**manifest["inventory"], **manifest["additional_sources"]}


def _archive_only_reads(monkeypatch):
    original_open = Path.open

    def frozen_only(path, mode="r", *args, **kwargs):
        assert not set(mode) & set("wax+"), "Archive verification must never capture files"
        assert path.resolve().is_relative_to(archive.HERE), "No live runtime source reads"
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", frozen_only)


def _replace_bytes(monkeypatch, target, replacement):
    read = Path.read_bytes
    monkeypatch.setattr(
        Path, "read_bytes", lambda path: replacement if path == target else read(path)
    )


def _frozen_four():
    documents = tuple(archive._document(row[0], row[4]) for row in HISTORICAL[:3])
    return (*documents, archive.verified_c90a_archive())


@pytest.mark.parametrize("filename,pin,policy_pin,reader,digest,policy", HISTORICAL)
def test_previous_trust_roots_and_all_264_preimages_stay_exact(
    monkeypatch,
    filename,
    pin,
    policy_pin,
    reader,
    digest,
    policy,
):
    _archive_only_reads(monkeypatch)
    assert getattr(archive, pin) == digest
    assert getattr(archive, policy_pin) == policy
    document = archive._document(filename, digest)
    assert document["policy_hash"] == archive._aggregate(document["inventory"]) == policy
    assert len(document["inventory"]) == 263
    assert len(document["additional_sources"]) == 1
    for relative, expected in _files(document).items():
        assert hashlib.sha256(getattr(archive, reader)(relative)).hexdigest() == expected, relative
    assert (archive.HERE / "prior-current.json").read_bytes() == (
        archive.HERE / "current.json"
    ).read_bytes()


def test_unissued_3e51_keeps_audit_only_authority(monkeypatch):
    _archive_only_reads(monkeypatch)
    audit = archive.verified_unissued_runtime_audit()
    assert audit["runtime_audit_only"] is True
    assert audit["alias_authority"] is False
    assert audit["policy_hash"] == HISTORICAL[3][5]
    assert audit["manifest_sha256"] == HISTORICAL[3][4]
    assert len(audit["counts"]) == 5
    assert all(type(count) is int and count == 0 for count in audit["counts"].values())


def test_c90a_full_archive_is_exact_issued_and_independent_of_live_runtime(monkeypatch):
    _archive_only_reads(monkeypatch)
    base = archive.HERE / "issued-c90a"
    for filename, (constant, digest) in C90A_PINS.items():
        assert getattr(archive, constant) == digest
        assert hashlib.sha256((base / filename).read_bytes()).hexdigest() == digest
    assert archive.C90A_POLICY == C90A_POLICY
    manifest = archive.verified_c90a_archive()
    assert manifest == json.loads((base / "final.json").read_bytes())
    assert hashlib.sha256((base / "final.json").read_bytes()).hexdigest() == archive.C90A_SHA256
    assert archive.C90A_SHA256 == C90A_PINS["final.json"][1]
    assert manifest["policy_hash"] == archive._aggregate(manifest["inventory"]) == C90A_POLICY
    assert len(manifest["inventory"]) == 263
    assert len(manifest["additional_sources"]) == 1
    index = json.loads((base / "manifest.json").read_bytes())
    assert index["file_pins"] == _files(manifest)
    assert index["inventory_count"] == 263 and index["additional_source_count"] == 1
    assert index["issued"] is True
    assert index["policy_hash"] == C90A_POLICY
    assert index["manifest_sha256"] == archive.C90A_SHA256
    assert index["initializer_sha256"] == archive.C90A_INITIALIZER_SHA256
    assert index["provider_calls"] == index["network_calls"] == 0
    assert index["database_access"] is False
    for relative, expected in _files(manifest).items():
        frozen = (base / "bytes" / (relative + ".source")).read_bytes()
        assert archive.c90a_bytes(relative) == frozen
        assert hashlib.sha256(frozen).hexdigest() == expected, relative


@pytest.mark.parametrize("relative", [JUDGE, CONTROL, RECONCILIATION])
def test_missing_c90a_preimage_never_falls_back_to_live_or_prior_sources(monkeypatch, relative):
    _archive_only_reads(monkeypatch)
    target = archive.HERE / "issued-c90a/bytes" / (relative + ".source")
    read = Path.read_bytes

    def missing(path):
        if path == target:
            raise FileNotFoundError("missing frozen c90a source")
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", missing)
    with pytest.raises((FileNotFoundError, ValueError)):
        archive.c90a_bytes(relative)
    with pytest.raises((FileNotFoundError, ValueError)):
        archive.verified_c90a_archive()


@pytest.mark.parametrize(
    "relative",
    [
        "final.json",
        "manifest.json",
        "__init__.py",
        "known-forward-edges.json",
        "bytes/config/quality_gates.yaml.source",
        "bytes/" + JUDGE + ".source",
        "bytes/" + CONTROL + ".source",
        "bytes/" + RECONCILIATION + ".source",
    ],
)
def test_c90a_archive_and_old_edges_reject_tampered_bytes(monkeypatch, relative):
    _archive_only_reads(monkeypatch)
    target = archive.HERE / "issued-c90a" / relative
    _replace_bytes(monkeypatch, target, target.read_bytes() + b"\n")
    verifier = (
        archive.known_c90a_transitions
        if relative == "known-forward-edges.json"
        else archive.verified_c90a_archive
    )
    with pytest.raises(ValueError, match="native_review_policy_"):
        verifier()
    if relative.startswith("bytes/"):
        with pytest.raises(ValueError, match="native_review_policy_"):
            archive.c90a_bytes(relative.removeprefix("bytes/").removesuffix(".source"))


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "unknown.py",
        "../final.json",
        "../../../../etc/passwd",
        "/etc/passwd",
        "src/bestseller/services/../services/commercial_judge_recovery.py",
    ],
)
def test_c90a_reader_rejects_unknown_and_traversal_paths(monkeypatch, relative):
    _archive_only_reads(monkeypatch)
    with pytest.raises(ValueError, match="native_review_policy_"):
        archive.c90a_bytes(relative)


def test_all_six_old_transition_dictionaries_keep_their_exact_manifest_pointers(monkeypatch):
    _archive_only_reads(monkeypatch)
    raw = (archive.HERE / "issued-c90a/known-forward-edges.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == C90A_PINS["known-forward-edges.json"][1]
    saved = json.loads(raw)
    assert saved["issued_c90a"] is True
    assert saved["synthetic_transition_dictionary_fixture_not_production_receipt_scan"] is True
    actual = archive.known_c90a_transitions()
    assert isinstance(actual, (tuple, list))
    assert list(actual) == saved["transitions"]
    before = deepcopy(actual)
    documents = _frozen_four()
    digests = (*[row[4] for row in HISTORICAL[:3]], C90A_PINS["final.json"][1])
    expected = [
        archive._proof(documents[a], documents[b], digests[a], digests[b])
        for a, b in combinations(range(4), 2)
    ]
    assert len(actual) == len(expected) == 6
    assert all(edge in actual for edge in expected)
    for edge in actual:
        assert edge["provider_calls"] == 0
        assert all(
            edge[key] is False for key in ("new_credit", "dispatch_authorized", "grade_recomputed")
        )
        assert archive.UNISSUED_POLICY not in (
            edge["previous_policy_hash"],
            edge["current_policy_hash"],
        )
        if edge["current_policy_hash"] == C90A_POLICY:
            assert edge["current_manifest_sha256"] == C90A_PINS["final.json"][1]
    assert actual == before


def test_fifth_epoch_stays_pending_with_only_reconciliation_reviewed(monkeypatch):
    monkeypatch.setattr(archive, "EPOCH3_POLICY", None)
    monkeypatch.setattr(archive, "EPOCH3_FINAL_SHA256", None)
    monkeypatch.setattr(archive, "REVIEWED_EPOCH3_SOURCE_SHA256", {
        JUDGE: None, CONTROL: None, RECONCILIATION: RECONCILIATION_SHA256,
    })
    assert archive.EPOCH3_POLICY is None
    assert archive.EPOCH3_FINAL_SHA256 is None
    assert archive.REVIEWED_EPOCH3_SOURCE_SHA256 == {
        JUDGE: None,
        CONTROL: None,
        RECONCILIATION: RECONCILIATION_SHA256,
    }


@pytest.mark.parametrize("missing", ["all", "final", JUDGE, CONTROL, RECONCILIATION])
def test_pending_fifth_epoch_fails_before_any_auto_capture(monkeypatch, missing):
    # Synthetic partial pins only test the precondition; none authorize a source.
    pins = {JUDGE: "a" * 64, CONTROL: "e" * 64, RECONCILIATION: "b" * 64}
    final = "c" * 64
    if missing == "all":
        pins, final = {JUDGE: None, CONTROL: None, RECONCILIATION: None}, None
    elif missing == "final":
        final = None
    else:
        pins[missing] = None
    monkeypatch.setattr(archive, "EPOCH3_FINAL_SHA256", final)
    monkeypatch.setattr(archive, "REVIEWED_EPOCH3_SOURCE_SHA256", pins)
    monkeypatch.setattr(archive, "EPOCH3_POLICY", "d" * 64)
    before = deepcopy(pins)

    def no_capture(*args, **kwargs):
        raise AssertionError("Pending fifth review must fail before reading or writing files")

    for method in ("open", "read_bytes", "read_text", "write_bytes", "write_text"):
        monkeypatch.setattr(Path, method, no_capture)
    with pytest.raises(ValueError, match="native_review_policy_fifth_epoch_not_reviewed"):
        archive._verified_five_epochs()
    assert archive.EPOCH3_FINAL_SHA256 == final
    assert archive.REVIEWED_EPOCH3_SOURCE_SHA256 == before


def test_pending_stage_preserves_existing_four_epoch_selector_contract(monkeypatch):
    monkeypatch.setattr(archive, "EPOCH3_POLICY", None)
    monkeypatch.setattr(archive, "EPOCH3_FINAL_SHA256", None)
    monkeypatch.setattr(archive, "FINAL_SHA256", archive.C90A_SHA256)
    _archive_only_reads(monkeypatch)
    documents = _frozen_four()
    saved = archive.known_c90a_transitions()

    def no_fifth():
        raise AssertionError("Existing selectors cannot enable a pending fifth epoch")

    # Supply frozen documents only at the runtime-verifier boundary. Selectors
    # and proof construction stay real; this is not current-runtime acceptance.
    monkeypatch.setattr(archive, "_verified_four_epochs", lambda: documents)
    monkeypatch.setattr(archive, "_verified_five_epochs", no_fifth)
    assert archive._verified_epochs() == (documents[0], documents[1], documents[3])
    for document in documents[:3]:
        expected = next(
            edge
            for edge in saved
            if edge["previous_policy_hash"] == document["policy_hash"]
            and edge["current_policy_hash"] == C90A_POLICY
        )
        assert archive.verified_transition_from(document["policy_hash"]) == expected
    assert archive.verified_transition() == archive.verified_transition_from(archive.OLD_POLICY)
    for edge in saved:
        before = deepcopy(edge)
        assert archive.verified_alias_transition(edge) is edge
        assert archive.verified_alias_depth_limit(edge) == 3
        assert edge == before
    with pytest.raises(ValueError, match="unsupported_previous_policy"):
        archive.verified_transition_from(C90A_POLICY)
    with pytest.raises(ValueError, match="unknown_alias_transition"):
        archive.verified_alias_transition(
            {**saved[0], "previous_policy_hash": archive.UNISSUED_POLICY}
        )
    with pytest.raises(ValueError, match="unknown_alias_transition"):
        archive.verified_alias_depth_limit({**saved[0], "current_policy_hash": "f" * 64})


def test_old_runtime_verification_still_rejects_unreviewed_source_bytes(monkeypatch):
    relative = "config/quality_gates.yaml"
    target = archive.ROOT / relative
    _replace_bytes(monkeypatch, target, target.read_bytes() + b"\n# synthetic unreviewed delta\n")
    with pytest.raises(ValueError, match="unlisted_runtime_delta:" + relative):
        archive._verified_four_epochs()


@pytest.mark.parametrize(
    "depth,reason",
    [(0, "alias_shape"), (2, "alias_shape"), (3, "alias_chain_depth"), (4, "alias_chain_depth")],
)
def test_pending_reconciliation_keeps_depth_three_limit(depth, reason):
    with pytest.raises(ValueError, match=reason):
        reconciliation._validate_native_alias_chain(
            {},
            project_id="project",
            chapter_id="chapter",
            draft_id="draft",
            body_sha256="body",
            depth=depth,
        )


def test_reconciliation_root_rejects_caller_supplied_depth_context():
    context = {"current_policy_hash": "f" * 64}
    before = deepcopy(context)
    with pytest.raises(ValueError, match="alias_chain_root_context"):
        reconciliation._validate_native_alias_chain(
            {},
            project_id="project",
            chapter_id="chapter",
            draft_id="draft",
            body_sha256="body",
            depth=0,
            _root_transition=context,
        )
    assert context == before


@pytest.mark.parametrize("relative", [JUDGE, CONTROL, RECONCILIATION])
def test_staged_candidate_has_only_its_authorized_structural_delta(relative):
    reviewed = archive.REVIEWED_EPOCH3_SOURCE_SHA256[relative]
    if reviewed is None:
        pytest.skip("Commercial source/helper/proof AST review is pending")
    current = (archive.ROOT / relative).read_bytes()
    assert hashlib.sha256(current).hexdigest() == reviewed
    assert archive._epoch3_delta_boundary(archive.c90a_bytes(relative), current, relative) is None


@pytest.mark.parametrize("relative", [JUDGE, CONTROL, RECONCILIATION])
@pytest.mark.parametrize("fault", ["module", "old_function", "preimage"])
def test_staged_structural_fence_rejects_unrelated_changes(relative, fault):
    if archive.REVIEWED_EPOCH3_SOURCE_SHA256[relative] is None:
        pytest.skip("Commercial source/helper/proof AST review is pending")
    previous = archive.c90a_bytes(relative)
    current = (archive.ROOT / relative).read_bytes()
    if fault == "module":
        current += b"\nUNREVIEWED_POLICY_CHANGE = True\n"
    elif fault == "preimage":
        previous += b"\n"
    else:
        tree = ast.parse(current)
        old_name = next(
            node.name
            for node in ast.parse(previous).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        function = next(node for node in tree.body if getattr(node, "name", None) == old_name)
        function.body.append(ast.Pass())
        current = ast.unparse(tree).encode()
    with pytest.raises(ValueError, match=r"native_review_policy_fifth_.*changed"):
        archive._epoch3_delta_boundary(previous, current, relative)


@pytest.mark.skipif(archive.EPOCH3_FINAL_SHA256 is None, reason="Fifth final pins pending review")
def test_reviewed_fifth_epoch_recognizes_ten_exact_edges_and_only_its_target_gets_four():
    documents = archive._verified_five_epochs()
    assert len(documents) == 5
    assert documents[-1]["policy_hash"] == archive.EPOCH3_POLICY != C90A_POLICY
    issued, current = documents[3], documents[4]
    assert {name for name in current["inventory"]
            if current["inventory"][name] != issued["inventory"][name]} == {JUDGE, CONTROL}
    assert {name for name in current["additional_sources"]
            if current["additional_sources"][name]
            != issued["additional_sources"][name]} == {RECONCILIATION}
    assert current["epoch_delta_files"] == sorted((JUDGE, CONTROL, RECONCILIATION))
    digests = (
        *[row[4] for row in HISTORICAL[:3]],
        archive.C90A_SHA256,
        archive.EPOCH3_FINAL_SHA256,
    )
    saved = archive.known_c90a_transitions()
    before = deepcopy(saved)
    edges = [
        archive._proof(documents[a], documents[b], digests[a], digests[b])
        for a, b in combinations(range(5), 2)
    ]
    assert len(edges) == 10
    assert all(edge in edges for edge in saved)
    for edge in edges:
        assert archive.verified_alias_transition(edge) is edge
        expected = 4 if edge["current_policy_hash"] == archive.EPOCH3_POLICY else 3
        assert archive.verified_alias_depth_limit(edge) == expected
    assert archive.known_c90a_transitions() == before


@pytest.mark.skipif(archive.EPOCH3_FINAL_SHA256 is None, reason="Fifth final pins pending review")
def test_four_actual_aliases_require_the_reviewed_fifth_target_and_cannot_gain_a_fifth():
    documents = archive._verified_five_epochs()
    assert documents[-1]["policy_hash"] == archive.EPOCH3_POLICY != C90A_POLICY
    digests = (
        *[row[4] for row in HISTORICAL[:3]],
        archive.C90A_SHA256,
        archive.EPOCH3_FINAL_SHA256,
    )
    saved = deepcopy(archive.known_c90a_transitions())
    cycle = {
        "cycle_id": "fixture",
        "source_draft_id": "draft",
        "source_body_hash": "body",
        "policy_source_hash": documents[0]["policy_hash"],
        "review_workflow_run_id": "owner",
        "semantic_dependencies_sha256": "semantic",
        "state": "prepared",
        "attempts": [
            {
                "attempt": 1,
                "operation_namespace": "chapter-critic:fixture:1",
                "state": "prepared",
                "infrastructure_history": [{"review_report_id": "report"}],
            }
        ],
    }
    for index in range(1, 5):
        transition = archive._proof(
            documents[index - 1], documents[index], digests[index - 1], digests[index]
        )
        alias = {
            "schema": reconciliation._NATIVE_ALIAS,
            "project_id": "project",
            "chapter_id": "chapter",
            "draft_id": "draft",
            "body_sha256": "body",
            "cycle_id": "fixture",
            "review_workflow_run_id": "owner",
            "transition": transition,
            "original_cycle": deepcopy(cycle),
            "provider_calls": 0,
            "new_credit": False,
            "dispatch_authorized": False,
            "grade_recomputed": False,
            "adoption_authorized": False,
        }
        alias["receipt_sha256"] = reconciliation._native_digest(alias)
        cycle["policy_source_hash"] = transition["current_policy_hash"]
        cycle["native_runtime_policy_alias"] = alias
    binding = {
        "project_id": "project",
        "chapter_id": "chapter",
        "draft_id": "draft",
        "body_sha256": "body",
    }
    before = deepcopy(cycle)
    assert reconciliation.validate_native_policy_alias(cycle, **binding) is alias
    assert cycle == before
    with pytest.raises(ValueError, match="alias_chain_depth"):
        reconciliation._validate_native_alias_chain(cycle, **binding, depth=3)
    with pytest.raises(ValueError, match="alias_chain_depth"):
        reconciliation._validate_native_alias_chain(
            cycle, **binding, depth=4, _root_transition=alias["transition"]
        )
    assert cycle == before
    assert archive.known_c90a_transitions() == saved
