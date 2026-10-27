"""DB-free sixth-epoch staging, with immutable issued 1a5a as the trust root.

Archive checks use fixed historical bytes. Synthetic proofs/alias cycles below
are not production receipts, paid results, or permission to dispatch. Runtime
integration tests remain explicitly pending until all final pins are reviewed.
"""

import ast
from copy import deepcopy
import hashlib
from itertools import combinations
import json
from pathlib import Path

import pytest

from bestseller.services import chapter_review_policy_history_8y24 as archive
from bestseller.services import chapter_review_reconciliation as reconciliation

pytestmark = pytest.mark.unit

REVIEWS = "src/bestseller/services/reviews.py"
PIPELINES = "src/bestseller/services/pipelines.py"
PROMOTION = "src/bestseller/services/draft_promotion.py"
FACT_DISPOSITION = "src/bestseller/services/chapter_verified_fact_disposition.py"
FACT_REPAIR = "src/bestseller/services/chapter_review_fact_repair.py"
RECONCILIATION = "src/bestseller/services/chapter_review_reconciliation.py"
RETAINED_INPUT = "src/bestseller/services/retained_review_input_proof.py"
EXISTING_INVENTORY_DELTAS = {REVIEWS, PIPELINES, PROMOTION, RETAINED_INPUT}
NEW_INVENTORY = {FACT_DISPOSITION, FACT_REPAIR}
REVIEWED_PATHS = EXISTING_INVENTORY_DELTAS | NEW_INVENTORY | {RECONCILIATION}
CHANGED_DEFINITIONS = {
    REVIEWS: {"_promotion_evidence_from_review", "review_chapter_draft"},
    PIPELINES: {
        "_review_chapter_with_evidence_retry",
        "_promote_reviewed_chapter_draft",
        "chapter_review_recovery_source_hash",
    },
    PROMOTION: {"numeric_promotion_debt_receipt", "_eligible_row", "_advance_to_promoted"},
    RETAINED_INPUT: {"verify_retained_review_inputs"},
}
ISSUED_POLICY = "1a5a25074912ecd42191f4fdd5c0be4e80d00414cc6dc4e84526174e9ebf1530"
ISSUED_PINS = {
    "final.json": (
        "ISSUED_1A5A_SHA256",
        "98d4f5ed89927c5235eebcea7e42c92edc613ad2f19464b0a5ef0d18378db1bb",
    ),
    "__init__.py": (
        "ISSUED_1A5A_INITIALIZER_SHA256",
        "5e9988639c3ce2de09abe8f2d169d101815380862b5d0c2ec7b46edfe50709bc",
    ),
    "manifest.json": (
        "ISSUED_1A5A_INDEX_SHA256",
        "97bf006ef57336c55630119905d267bc735c034c5058bbae53f5d45724277dc9",
    ),
    "known-forward-edges.json": (
        "ISSUED_1A5A_EDGES_SHA256",
        "445739ebe11ec96cacb372fae7c4287de1c160868ebd9e61d8755169dd5f62f0",
    ),
}
PROJECTION_FLAGS = {
    "acceptance_semantics_identical": False,
    "current_policy_reprojection": "verified-fact-deny-only",
    "paid_inputs_unchanged": True,
    "model_requests_unchanged": True,
    "renderers_unchanged": True,
    "raw_grade_reports_attempts_immutable": True,
}
BINDING = {
    "project_id": "fixture-project",
    "chapter_id": "fixture-chapter",
    "draft_id": "fixture-draft",
    "body_sha256": "fixture-body",
}


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
        raise AssertionError("Epoch4 tests forbid actual DB, provider and network IO")

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


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _files(document):
    return {**document["inventory"], **document["additional_sources"]}


def _archive_only_reads(monkeypatch):
    original_open = Path.open

    def frozen_only(path, mode="r", *args, **kwargs):
        assert not set(mode) & set("wax+"), "Verification must not capture or rewrite files"
        assert path.resolve().is_relative_to(archive.HERE), "No live source fallback"
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", frozen_only)


def _no_file_access(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Unreviewed pins must fail before source discovery or capture")

    for method in ("open", "read_bytes", "read_text", "write_bytes", "write_text"):
        monkeypatch.setattr(Path, method, denied)


def _replace_bytes(monkeypatch, target, replacement):
    read = Path.read_bytes
    monkeypatch.setattr(
        Path, "read_bytes", lambda path: replacement if path == target else read(path)
    )


def _archived_five():
    return (
        archive._document("original.json", archive.ORIGINAL_SHA256),
        archive._document("current.json", archive.CURRENT_SHA256),
        archive._document("a296-final.json", archive.A296_SHA256),
        archive.verified_c90a_archive(),
        archive.verified_1a5a_archive(),
    )


def _historical_digests():
    return (
        archive.ORIGINAL_SHA256,
        archive.CURRENT_SHA256,
        archive.A296_SHA256,
        archive.C90A_SHA256,
        ISSUED_PINS["final.json"][1],
    )


def _fifth_selector_fixture(monkeypatch):
    """Keep real selectors; replace only runtime verification with frozen data."""
    documents = _archived_five()
    monkeypatch.setattr(archive, "EPOCH6_POLICY", None)
    monkeypatch.setattr(archive, "EPOCH6_FINAL_SHA256", None)
    monkeypatch.setattr(archive, "EPOCH5_POLICY", None)
    monkeypatch.setattr(archive, "EPOCH5_FINAL_SHA256", None)
    monkeypatch.setattr(archive, "EPOCH4_POLICY", None)
    monkeypatch.setattr(archive, "EPOCH4_FINAL_SHA256", None)
    monkeypatch.setattr(archive, "FINAL_SHA256", ISSUED_PINS["final.json"][1])
    monkeypatch.setattr(archive, "_verified_five_epochs", lambda: documents)
    return documents


def _candidate_document():
    """Synthetic manifest for proof construction, never a reviewed runtime pin."""
    previous = archive.verified_1a5a_archive()
    current = deepcopy(previous)
    current["policy_hash"] = "e" * 64
    for relative in EXISTING_INVENTORY_DELTAS | NEW_INVENTORY:
        current["inventory"][relative] = "a" * 64
    current["additional_sources"][RECONCILIATION] = "b" * 64
    return previous, current


def _reviewed_synthetic_module(monkeypatch, relative, tree):
    """Drive past SHA checks to test structural defenses on an explicit fixture.

    No runtime bytes are pinned here. The candidate is transformed issued code,
    never executed; matching fixture pins cannot exempt protected old ASTs.
    """
    previous = archive.issued_1a5a_bytes(relative)
    current = ast.unparse(ast.fix_missing_locations(tree)).encode()
    pins = dict.fromkeys(REVIEWED_PATHS, "a" * 64)
    pins[relative] = _sha(current)
    function_pins = dict(archive.EPOCH4_PREIMAGE_FUNCTION_AST_SHA256)
    for node in tree.body:
        if getattr(node, "name", None) in CHANGED_DEFINITIONS[relative]:
            function_pins[node.name] = _sha(ast.dump(node, include_attributes=False).encode())
    monkeypatch.setattr(archive, "REVIEWED_EPOCH4_SOURCE_SHA256", pins)
    monkeypatch.setattr(archive, "REVIEWED_EPOCH4_FUNCTION_AST_SHA256", function_pins)
    return previous, current


@pytest.fixture
def reviewed_six():
    if archive.EPOCH4_POLICY is None or archive.EPOCH4_FINAL_SHA256 is None:
        pytest.skip("Sixth endpoint final source/manifest pins are not reviewed")
    return archive._verified_six_epochs(runtime=False)


@pytest.fixture
def synthetic_six(monkeypatch):
    """Exercise real selectors/depth checks, replacing runtime verification only.

    These explicit synthetic endpoint pins never enter the package or a file.
    This fixture proves graph behavior, not final source/AST/runtime acceptance.
    """
    documents = _archived_five()
    monkeypatch.setattr(archive, "EPOCH6_POLICY", None)
    monkeypatch.setattr(archive, "EPOCH6_FINAL_SHA256", None)
    monkeypatch.setattr(archive, "EPOCH5_POLICY", None)
    monkeypatch.setattr(archive, "EPOCH5_FINAL_SHA256", None)
    _, current = _candidate_document()
    documents = (*documents, current)
    monkeypatch.setattr(archive, "EPOCH4_POLICY", current["policy_hash"])
    monkeypatch.setattr(archive, "EPOCH4_FINAL_SHA256", "f" * 64)
    monkeypatch.setattr(archive, "FINAL_SHA256", "f" * 64)
    monkeypatch.setattr(archive, "_verified_six_epochs", lambda: documents)
    _archive_only_reads(monkeypatch)
    return documents


@pytest.mark.parametrize("filename,pin_and_digest", ISSUED_PINS.items())
def test_issued_1a5a_uses_literal_trust_roots(monkeypatch, filename, pin_and_digest):
    _archive_only_reads(monkeypatch)
    constant, digest = pin_and_digest
    assert getattr(archive, constant) == digest
    assert _sha((archive.HERE / "issued-1a5a" / filename).read_bytes()) == digest
    assert archive.ISSUED_1A5A_POLICY == ISSUED_POLICY


def test_complete_268_file_archive_and_all_264_preimages_are_exact(monkeypatch):
    _archive_only_reads(monkeypatch)
    base = archive.HERE / "issued-1a5a"
    document = archive.verified_1a5a_archive()
    assert document == json.loads((base / "final.json").read_bytes())
    assert document["policy_hash"] == archive._aggregate(document["inventory"]) == ISSUED_POLICY
    assert len(document["inventory"]) == 263
    assert set(document["additional_sources"]) == {RECONCILIATION}
    assert NEW_INVENTORY.isdisjoint(_files(document))
    index = json.loads((base / "manifest.json").read_bytes())
    assert index["issued"] is True
    assert index["policy_hash"] == ISSUED_POLICY
    assert index["file_pins"] == _files(document)
    assert index["inventory_count"] == 263
    assert index["additional_source_count"] == 1
    assert index["archive_total_files"] == 268
    assert index["manifest_sha256"] == ISSUED_PINS["final.json"][1]
    assert index["initializer_sha256"] == ISSUED_PINS["__init__.py"][1]
    assert index["known_forward_edges_sha256"] == ISSUED_PINS["known-forward-edges.json"][1]
    assert index["historical_target_max_aliases"] == 3
    assert index["exact_fifth_target_max_aliases"] == 4
    assert index["forward_edges"] == 10
    assert index["sixth_endpoint_created"] is False
    assert index["attempts_or_fees_rewritten"] is False
    assert index["database_access"] is False
    assert index["provider_calls"] == index["network_calls"] == 0
    expected = {relative + ".source" for relative in _files(document)}
    physical = {
        path.relative_to(base).as_posix() for path in base.rglob("*") if path.is_file()
    }
    assert physical == {"bytes/" + relative for relative in expected} | set(ISSUED_PINS)
    assert set(index["archive_file_pins"]) == physical - {"manifest.json"}
    for relative, expected_sha in index["archive_file_pins"].items():
        assert _sha((base / relative).read_bytes()) == expected_sha, relative
    for relative, expected_sha in _files(document).items():
        frozen = (base / "bytes" / (relative + ".source")).read_bytes()
        assert archive.issued_1a5a_bytes(relative) == frozen
        assert _sha(frozen) == expected_sha, relative


@pytest.mark.parametrize("relative", [REVIEWS, PIPELINES, PROMOTION, RECONCILIATION])
def test_missing_issued_preimages_never_fall_back_to_live_sources(monkeypatch, relative):
    _archive_only_reads(monkeypatch)
    target = archive.HERE / "issued-1a5a/bytes" / (relative + ".source")
    read = Path.read_bytes

    def missing(path):
        if path == target:
            raise FileNotFoundError("Missing immutable issued-1a5a preimage")
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", missing)
    with pytest.raises((FileNotFoundError, ValueError)):
        archive.issued_1a5a_bytes(relative)
    with pytest.raises((FileNotFoundError, ValueError)):
        archive.verified_1a5a_archive()


@pytest.mark.parametrize(
    "relative",
    [
        *ISSUED_PINS,
        "bytes/config/quality_gates.yaml.source",
        *("bytes/" + relative + ".source" for relative in (
            REVIEWS, PIPELINES, PROMOTION, RECONCILIATION,
        )),
    ],
)
def test_archive_verification_rejects_corrupt_sources_index_initializer_and_edges(
    monkeypatch, relative,
):
    _archive_only_reads(monkeypatch)
    target = archive.HERE / "issued-1a5a" / relative
    _replace_bytes(monkeypatch, target, target.read_bytes() + b"\n")
    verifier = (
        archive.known_1a5a_transitions
        if relative == "known-forward-edges.json"
        else archive.verified_1a5a_archive
    )
    with pytest.raises(ValueError, match="native_review_policy_"):
        verifier()
    if relative.startswith("bytes/"):
        with pytest.raises(ValueError, match="native_review_policy_"):
            archive.issued_1a5a_bytes(relative.removeprefix("bytes/").removesuffix(".source"))


@pytest.mark.parametrize(
    "relative",
    ["", "unknown.py", FACT_REPAIR, FACT_DISPOSITION, "../final.json", "/etc/passwd",
     "src/bestseller/services/../services/reviews.py"],
)
def test_archive_reader_denies_unknown_new_and_traversal_paths(monkeypatch, relative):
    _archive_only_reads(monkeypatch)
    with pytest.raises(ValueError, match="native_review_policy_"):
        archive.issued_1a5a_bytes(relative)


def test_all_five_old_epochs_remain_263_plus_one_and_ten_exact_edges(monkeypatch):
    _archive_only_reads(monkeypatch)
    documents = _archived_five()
    digests = _historical_digests()
    assert len(documents) == 5
    for document in documents:
        assert len(document["inventory"]) == 263
        assert set(document["additional_sources"]) == {RECONCILIATION}
        assert NEW_INVENTORY.isdisjoint(_files(document))
    saved = json.loads((archive.HERE / "issued-1a5a/known-forward-edges.json").read_bytes())
    assert saved["issued_1a5a"] is True
    assert saved["synthetic_transition_dictionary_fixture_not_production_receipt_scan"] is True
    assert saved["historical_target_max_aliases"] == 3
    assert saved["exact_fifth_target_max_aliases"] == 4
    actual = archive.known_1a5a_transitions()
    before = deepcopy(actual)
    expected = tuple(
        archive._proof(documents[a], documents[b], digests[a], digests[b])
        for a, b in combinations(range(5), 2)
    )
    assert isinstance(actual, tuple)
    assert len(actual) == 10
    assert list(actual) == saved["transitions"]
    assert all(edge in actual for edge in expected)
    assert all(edge in actual for edge in archive.known_c90a_transitions())
    for edge in actual:
        assert edge["provider_calls"] == 0
        assert all(edge[key] is False for key in (
            "new_credit", "dispatch_authorized", "grade_recomputed",
        ))
        assert archive.UNISSUED_POLICY not in (
            edge["previous_policy_hash"], edge["current_policy_hash"],
        )
    assert archive.known_1a5a_transitions() == before


def test_five_epoch_archive_replay_never_needs_changed_live_runtime(monkeypatch):
    _archive_only_reads(monkeypatch)
    assert archive._verified_five_epochs(runtime=False) == _archived_five()


def test_pending_sixth_keeps_old_selector_values_and_depth_limits(monkeypatch):
    _archive_only_reads(monkeypatch)
    documents = _fifth_selector_fixture(monkeypatch)
    saved = archive.known_1a5a_transitions()

    def denied():
        raise AssertionError("An unreviewed sixth endpoint cannot enter old selectors")

    monkeypatch.setattr(archive, "_verified_six_epochs", denied)
    assert archive._verified_epochs() == (documents[0], documents[1], documents[4])
    for source in documents[:4]:
        expected = next(edge for edge in saved if (
            edge["previous_policy_hash"] == source["policy_hash"]
            and edge["current_policy_hash"] == ISSUED_POLICY
        ))
        assert archive.verified_transition_from(source["policy_hash"]) == expected
    assert archive.verified_transition() == archive.verified_transition_from(archive.OLD_POLICY)
    for edge in saved:
        before = deepcopy(edge)
        assert archive.verified_alias_transition(edge) is edge
        expected_limit = 4 if edge["current_policy_hash"] == ISSUED_POLICY else 3
        assert archive.verified_alias_depth_limit(edge) == expected_limit
        assert edge == before
    with pytest.raises(ValueError, match="unsupported_previous_policy"):
        archive.verified_transition_from(ISSUED_POLICY)


def test_sixth_review_inventory_and_nine_definition_fences_are_explicit():
    assert set(archive.REVIEWED_EPOCH4_SOURCE_SHA256) == REVIEWED_PATHS
    assert archive.EPOCH4_NEW_INVENTORY == NEW_INVENTORY
    assert archive.EPOCH4_CHANGED_DEFINITIONS == CHANGED_DEFINITIONS
    assert sum(map(len, CHANGED_DEFINITIONS.values())) == 9


def test_nine_preimage_function_pins_are_derived_only_from_issued_bytes(monkeypatch):
    _archive_only_reads(monkeypatch)
    expected = {}
    for relative, names in CHANGED_DEFINITIONS.items():
        nodes = ast.parse(archive.issued_1a5a_bytes(relative)).body
        for name in names:
            node = next(node for node in nodes if getattr(node, "name", None) == name)
            expected[name] = _sha(ast.dump(node, include_attributes=False).encode())
    assert len(expected) == 9
    assert archive.EPOCH4_PREIMAGE_FUNCTION_AST_SHA256 == expected


def test_retained_history_recipe_restores_every_original_guard_and_other_ast():
    previous = archive.issued_1a5a_bytes(RETAINED_INPUT)
    current = archive.issued_f157_bytes(RETAINED_INPUT)
    assert _sha(current) == "45f86cb82e02743576aa3df5a3e0fdeeb7542ae8fdd9eac8caf071cb7da35bc6"
    archive._epoch4_retained_recipe_boundary(previous, current)


@pytest.mark.parametrize("fault", ["loader", "reference", "pending", "guard", "physical"])
def test_retained_recipe_fence_rejects_any_recipe_or_physical_guard_change(fault):
    previous = archive.issued_1a5a_bytes(RETAINED_INPUT)
    tree = ast.parse(archive.issued_f157_bytes(RETAINED_INPUT))
    function = next(n for n in tree.body if getattr(n, "name", None)
                    == "verify_retained_review_inputs")
    if fault in {"loader", "physical", "guard"}:
        name = {"loader": "load_authorized_commercial_history", "physical": "_commercial_proof",
                "guard": "_require"}[fault]
        call = next(n for n in ast.walk(function) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name) and n.func.id == name)
        if fault == "guard":
            call.args[0] = ast.Constant(True)
        else:
            call.func.id = "untrusted_replacement"
    else:
        value = "recent_reference_prose" if fault == "reference" else "draft_manuscript_continuity"
        node = next(n for n in ast.walk(function)
                    if isinstance(n, ast.Constant) and n.value == value)
        node.value = "untrusted_history_channel"
    changed = ast.unparse(ast.fix_missing_locations(tree)).encode()
    with pytest.raises(ValueError, match="sixth_retained_"):
        archive._epoch4_retained_recipe_boundary(previous, changed)


@pytest.mark.parametrize("missing", ["all", "policy", "final", *sorted(REVIEWED_PATHS)])
def test_missing_reviewed_pins_fail_before_any_source_read_or_autopin(monkeypatch, missing):
    pins = dict.fromkeys(REVIEWED_PATHS, "a" * 64)
    policy, final = "b" * 64, "c" * 64
    if missing == "all":
        pins, policy, final = dict.fromkeys(REVIEWED_PATHS), None, None
    elif missing == "policy":
        policy = None
    elif missing == "final":
        final = None
    else:
        pins[missing] = None
    monkeypatch.setattr(archive, "REVIEWED_EPOCH4_SOURCE_SHA256", pins)
    monkeypatch.setattr(archive, "EPOCH4_POLICY", policy)
    monkeypatch.setattr(archive, "EPOCH4_FINAL_SHA256", final)
    before = deepcopy(pins)
    _no_file_access(monkeypatch)
    with pytest.raises(ValueError, match=r"native_review_policy_.*not_reviewed"):
        archive._verified_six_epochs()
    assert archive.REVIEWED_EPOCH4_SOURCE_SHA256 == before
    assert archive.EPOCH4_POLICY == policy
    assert archive.EPOCH4_FINAL_SHA256 == final


@pytest.mark.parametrize("relative", sorted(REVIEWED_PATHS))
def test_pending_delta_boundary_never_accepts_unknown_candidate_bytes(monkeypatch, relative):
    monkeypatch.setattr(archive, "REVIEWED_EPOCH4_SOURCE_SHA256", dict.fromkeys(REVIEWED_PATHS))
    _no_file_access(monkeypatch)
    with pytest.raises(ValueError, match="native_review_policy_"):
        archive._epoch4_delta_boundary(b"", b"UNREVIEWED = True\n", relative)
    assert not any(archive.REVIEWED_EPOCH4_SOURCE_SHA256.values())


def test_sixth_proof_admits_acceptance_change_without_credit_or_raw_mutation(monkeypatch):
    previous, current = _candidate_document()
    before = deepcopy((previous, current))
    _no_file_access(monkeypatch)
    proof = archive._epoch4_proof(previous, current, ISSUED_PINS["final.json"][1], "f" * 64)
    assert {key: proof[key] for key in PROJECTION_FLAGS} == PROJECTION_FLAGS
    assert proof["previous_policy_hash"] == ISSUED_POLICY
    assert proof["current_policy_hash"] == current["policy_hash"]
    assert proof["original_manifest_sha256"] == ISSUED_PINS["final.json"][1]
    assert proof["current_manifest_sha256"] == "f" * 64
    assert set(proof["reviewed_delta_files"]) == REVIEWED_PATHS
    assert type(proof["provider_calls"]) is int and proof["provider_calls"] == 0
    assert all(proof[key] is False for key in (
        "new_credit", "dispatch_authorized", "grade_recomputed",
    ))
    assert proof.get("adoption_authorized", False) is False
    assert proof["retained_available_result"] == (
        "derive_current_policy_disposition_before_acceptance"
    )
    assert (previous, current) == before


def test_synthetic_candidate_proof_cannot_enable_a_pending_target(monkeypatch):
    previous, current = _candidate_document()
    proof = archive._epoch4_proof(previous, current, ISSUED_PINS["final.json"][1], "f" * 64)
    _archive_only_reads(monkeypatch)
    _fifth_selector_fixture(monkeypatch)
    before = deepcopy(proof)
    with pytest.raises(ValueError, match="unknown_alias_transition"):
        archive.verified_alias_transition(proof)
    with pytest.raises(ValueError, match="unknown_alias_transition"):
        archive.verified_alias_depth_limit(proof)
    assert proof == before


@pytest.mark.parametrize(
    "fault", [None, "missing", "duplicate", "third", "old_filename", "body"],
)
def test_hash_inventory_fence_only_accepts_two_literal_additions(monkeypatch, fault):
    _archive_only_reads(monkeypatch)
    name = "chapter_review_recovery_source_hash"
    old = next(node for node in ast.parse(archive.issued_1a5a_bytes(PIPELINES)).body
               if getattr(node, "name", None) == name)
    new = deepcopy(old)
    names = next(node for node in ast.walk(new) if isinstance(node, ast.Tuple) and any(
        isinstance(item, ast.Constant) and item.value == "reviews.py" for item in node.elts
    ))
    additions = [ast.Constant(Path(relative).name) for relative in sorted(NEW_INVENTORY)]
    names.elts.extend(additions)
    if fault == "missing":
        names.elts.pop()
    elif fault == "duplicate":
        names.elts.append(deepcopy(additions[0]))
    elif fault == "third":
        names.elts.append(ast.Constant("unreviewed_policy.py"))
    elif fault == "old_filename":
        names.elts = [item for item in names.elts if not (
            isinstance(item, ast.Constant) and item.value == "reviews.py"
        )]
    elif fault == "body":
        new.body.append(ast.Pass())
    previous = ast.unparse(old).encode()
    current = ast.unparse(ast.fix_missing_locations(new)).encode()
    if fault is None:
        assert archive._epoch4_hash_inventory_boundary(previous, current) is None
    else:
        with pytest.raises(ValueError, match="sixth_source_hash_other_ast_changed"):
            archive._epoch4_hash_inventory_boundary(previous, current)


@pytest.mark.parametrize(
    "relative,function_name,call_name",
    [
        *((REVIEWS, "review_chapter_draft", name) for name in (
            "LLMCompletionRequest", "complete_text", "build_chapter_review_prompts",
            "load_review_source_context", "prepare_source_complete_prompt",
        )),
        *((PIPELINES, "_review_chapter_with_evidence_retry", name) for name in (
            "_chapter_critic_semantic_dependencies", "_retained_chapter_critic_result",
            "bind_execution_operation", "prerequisite_wait_receipt", "wait_binding",
            "waiting_history",
        )),
    ],
)
def test_protected_paid_prompt_source_semantic_and_waiting_calls_cannot_change(
    monkeypatch, relative, function_name, call_name,
):
    _archive_only_reads(monkeypatch)
    tree = ast.parse(archive.issued_1a5a_bytes(relative))
    function = next(node for node in tree.body if getattr(node, "name", None) == function_name)
    call = next(node for node in ast.walk(function) if (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == call_name
    ))
    call.keywords.append(ast.keyword(arg="synthetic_unreviewed_change", value=ast.Constant(True)))
    previous, current = _reviewed_synthetic_module(monkeypatch, relative, tree)
    with pytest.raises(ValueError, match="sixth_paid_input_or_infrastructure_call_changed"):
        archive._epoch4_delta_boundary(previous, current, relative)


@pytest.mark.parametrize("relative", sorted(EXISTING_INVENTORY_DELTAS))
def test_even_matching_fixture_pins_cannot_change_any_other_old_function(monkeypatch, relative):
    _archive_only_reads(monkeypatch)
    tree = ast.parse(archive.issued_1a5a_bytes(relative))
    exact = {
        REVIEWS: "rewrite_chapter_from_task",
        PIPELINES: "_retained_chapter_critic_result",
    }.get(relative)
    function = next(node for node in tree.body if (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name not in CHANGED_DEFINITIONS[relative]
        and (exact is None or node.name == exact)
    ))
    function.body.append(ast.Pass())
    previous, current = _reviewed_synthetic_module(monkeypatch, relative, tree)
    with pytest.raises(ValueError, match="sixth_other_module_ast_changed"):
        archive._epoch4_delta_boundary(previous, current, relative)


@pytest.mark.parametrize("relative", sorted(REVIEWED_PATHS))
def test_reviewed_sources_have_exact_pins_and_bounded_structural_deltas(reviewed_six, relative):
    previous = b"" if relative in NEW_INVENTORY else archive.issued_1a5a_bytes(relative)
    current = archive.issued_f157_bytes(relative)
    assert _sha(current) == archive.REVIEWED_EPOCH4_SOURCE_SHA256[relative]
    assert archive._epoch4_delta_boundary(previous, current, relative) is None


def test_hash_inventory_function_only_adds_the_two_exact_literal_names(reviewed_six):
    name = "chapter_review_recovery_source_hash"
    previous = next(node for node in ast.parse(archive.issued_1a5a_bytes(PIPELINES)).body
                    if getattr(node, "name", None) == name)
    current = next(node for node in ast.parse(archive.issued_f157_bytes(PIPELINES)).body
                   if getattr(node, "name", None) == name)
    added = {Path(relative).name for relative in NEW_INVENTORY}
    removed = []

    class StripExactNames(ast.NodeTransformer):
        def visit_Tuple(self, node):
            self.generic_visit(node)
            keep = []
            for element in node.elts:
                if isinstance(element, ast.Constant) and element.value in added:
                    removed.append(element.value)
                else:
                    keep.append(element)
            node.elts = keep
            return node

    normalized = StripExactNames().visit(deepcopy(current))
    assert sorted(removed) == sorted(added)
    assert ast.dump(previous, include_attributes=False) == ast.dump(
        normalized, include_attributes=False,
    )


@pytest.mark.parametrize("relative", sorted(REVIEWED_PATHS))
def test_reviewed_fences_deny_unrelated_module_mutation(reviewed_six, relative):
    previous = b"" if relative in NEW_INVENTORY else archive.issued_1a5a_bytes(relative)
    current = archive.issued_f157_bytes(relative) + b"\nUNREVIEWED_POLICY_CHANGE = True\n"
    with pytest.raises(ValueError, match="native_review_policy_"):
        archive._epoch4_delta_boundary(previous, current, relative)


def test_reviewed_sixth_is_exact_265_plus_one_with_only_seven_authorized_paths(reviewed_six):
    assert len(reviewed_six) == 6
    old_documents = _archived_five()
    assert reviewed_six[:5] == old_documents
    issued, current = reviewed_six[-2:]
    assert current["policy_hash"] == archive.EPOCH4_POLICY != ISSUED_POLICY
    assert current["policy_hash"] == archive._aggregate(current["inventory"])
    assert len(current["inventory"]) == 265
    assert set(current["inventory"]) == set(issued["inventory"]) | NEW_INVENTORY
    assert set(current["additional_sources"]) == {RECONCILIATION}
    assert len(_files(current)) == 266
    changed = {relative for relative in issued["inventory"]
               if issued["inventory"][relative] != current["inventory"][relative]}
    assert changed == EXISTING_INVENTORY_DELTAS
    assert current["additional_sources"][RECONCILIATION] != (
        issued["additional_sources"][RECONCILIATION]
    )
    assert current["epoch_delta_files"] == sorted(REVIEWED_PATHS)
    assert current["prior_policy_hash"] == ISSUED_POLICY


def _sixth_edges(documents):
    digests = (*_historical_digests(), archive.EPOCH4_FINAL_SHA256)
    return tuple(
        (archive._epoch4_proof if b == 5 else archive._proof)(
            documents[a], documents[b], digests[a], digests[b]
        )
        for a, b in combinations(range(6), 2)
    )


def test_candidate_graph_preserves_old_ten_and_only_sixth_gets_five(synthetic_six):
    old = archive.known_1a5a_transitions()
    before = deepcopy(old)
    edges = _sixth_edges(synthetic_six)
    assert len(edges) == 15
    assert all(edge in edges for edge in old)
    for edge in edges:
        assert archive.verified_alias_transition(edge) is edge
        if edge["current_policy_hash"] == archive.EPOCH4_POLICY:
            expected_limit = 5
            assert {key: edge[key] for key in PROJECTION_FLAGS} == PROJECTION_FLAGS
        else:
            expected_limit = 4 if edge["current_policy_hash"] == ISSUED_POLICY else 3
        assert archive.verified_alias_depth_limit(edge) == expected_limit
        assert all(edge[key] is False for key in (
            "new_credit", "dispatch_authorized", "grade_recomputed",
        ))
    for document in synthetic_six[:5]:
        expected = next(edge for edge in edges if (
            edge["previous_policy_hash"] == document["policy_hash"]
            and edge["current_policy_hash"] == archive.EPOCH4_POLICY
        ))
        assert archive.verified_transition_from(document["policy_hash"]) == expected
    assert archive.known_1a5a_transitions() == before
    assert archive.verified_transition() == archive.verified_transition_from(archive.OLD_POLICY)


@pytest.mark.parametrize("fault", ["unknown", "reverse", "unissued", "claim_identical", "credit"])
def test_candidate_graph_denies_unknown_reverse_or_authority_changed_proofs(synthetic_six, fault):
    edge = deepcopy(_sixth_edges(synthetic_six)[-1])
    if fault == "unknown":
        edge["current_policy_hash"] = "f" * 64
    elif fault == "reverse":
        edge["previous_policy_hash"], edge["current_policy_hash"] = (
            edge["current_policy_hash"], edge["previous_policy_hash"],
        )
    elif fault == "unissued":
        edge["previous_policy_hash"] = archive.UNISSUED_POLICY
    elif fault == "claim_identical":
        edge["acceptance_semantics_identical"] = True
    else:
        edge["new_credit"] = True
    with pytest.raises(ValueError, match="unknown_alias_transition"):
        archive.verified_alias_transition(edge)
    with pytest.raises(ValueError, match="unknown_alias_transition"):
        archive.verified_alias_depth_limit(edge)


@pytest.mark.parametrize("state", ["completed", "waiting_infrastructure", "prepared"])
def test_candidate_five_aliases_preserve_raw_waiting_and_attempt_identity(synthetic_six, state):
    digests = (*_historical_digests(), archive.EPOCH4_FINAL_SHA256)
    cycle = {
        "cycle_id": "fixture-cycle",
        "source_draft_id": BINDING["draft_id"],
        "source_body_hash": BINDING["body_sha256"],
        "policy_source_hash": synthetic_six[0]["policy_hash"],
        "review_workflow_run_id": "fixture-owner",
        "semantic_dependencies_sha256": "fixture-semantic",
        "state": state,
        "attempts": [{
            "attempt": 1,
            "operation_namespace": "chapter-critic:fixture-cycle:1",
            "state": state,
            "raw_grade": "PASS",
            "review_report_id": "fixture-report",
            "quality_score_id": "fixture-quality",
            "infrastructure_history": [{"review_report_id": "fixture-waiting-report"}],
        }],
        "infrastructure_resumptions": [{"fixture": "original-resumption"}],
    }
    original = deepcopy(cycle)
    for index in range(1, 6):
        proof = archive._epoch4_proof if index == 5 else archive._proof
        transition = proof(synthetic_six[index - 1], synthetic_six[index],
                           digests[index - 1], digests[index])
        alias = {
            "schema": reconciliation._NATIVE_ALIAS,
            **BINDING,
            "cycle_id": cycle["cycle_id"],
            "review_workflow_run_id": cycle["review_workflow_run_id"],
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
    before = deepcopy(cycle)
    assert reconciliation.validate_native_policy_alias(cycle, **BINDING) is alias
    assert cycle == before
    cursor = cycle
    for _ in range(5):
        assert cursor["attempts"] == original["attempts"]
        cursor = cursor["native_runtime_policy_alias"]["original_cycle"]
    assert cursor == original
    with pytest.raises(ValueError, match="alias_chain_root_context"):
        reconciliation._validate_native_alias_chain(
            cycle, **BINDING, depth=0, _root_transition=alias["transition"],
        )
    with pytest.raises(ValueError, match="alias_chain_depth"):
        reconciliation._validate_native_alias_chain(
            cycle, **BINDING, depth=5, _root_transition=alias["transition"],
        )
    for old_edge in archive.known_1a5a_transitions():
        old_limit = 4 if old_edge["current_policy_hash"] == ISSUED_POLICY else 3
        with pytest.raises(ValueError, match="alias_chain_depth"):
            reconciliation._validate_native_alias_chain(
                cycle, **BINDING, depth=old_limit, _root_transition=old_edge,
            )
    assert cycle == before
