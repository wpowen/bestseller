"""Exact runtime epochs and trusted old parser bytes, without database IO."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from types import ModuleType

import pytest

from bestseller.services import chapter_review_policy_history_8y24 as history
from bestseller.services import chapter_review_reconciliation as reconciliation

pytestmark = pytest.mark.unit
WIRE = "src/bestseller/services/commercial_review_wire.py"
RECOVERY = "src/bestseller/services/commercial_judge_recovery.py"
RECONCILIATION = "src/bestseller/services/chapter_review_reconciliation.py"
ADMISSION = "src/bestseller/services/chapter_review_admission.py"
CONSUMERS = tuple(
    "src/bestseller/services/" + name
    for name in (
        "waiting_chapter_review_resume.py",
        "retained_review_input_proof.py",
        "chapter_llm_quality_judge.py",
        "commercial_action_projection.py",
        "chapter_verified_fact_disposition.py",
        "chapter_draft_candidate_eligibility.py",
    )
)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def issued_b8a_runtime(tmp_path_factory):
    """Exercise the real issued seventh selector against its actual frozen bytes.

    The new eighth node has separate current-runtime tests. No validator is
    replaced here: the authenticated original initializer runs unchanged.
    """
    current = history
    manifest = current.verified_issued_b8a_archive()
    root = tmp_path_factory.mktemp("issued_b8a_policy_test")
    package = root / "src/bestseller/services/chapter_review_policy_history_8y24"
    shutil.copytree(current.HERE, package, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("__init__.py", "final.json"):
        (package / name).write_bytes((current.HERE / "issued-b8a" / name).read_bytes())
    for name in {**manifest["inventory"], **manifest["additional_sources"]}:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(current.issued_b8a_bytes(name))
    issued = ModuleType("bestseller.services._issued_b8a_policy_test")
    issued.__file__ = str(package / "__init__.py")
    issued.__package__ = "bestseller.services"
    exec(compile((package / "__init__.py").read_bytes(), issued.__file__, "exec"),
         vars(issued))
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(globals(), "history", issued)
        yield


def test_f157_complete_archive_and_original_forward_receipts_remain_exact():
    manifest = history.verified_issued_f157_archive()
    assert manifest["policy_hash"] == history.EPOCH4_POLICY
    assert len(manifest["inventory"]) == 265
    assert set(manifest["additional_sources"]) == {RECONCILIATION}
    assert history._aggregate(manifest["inventory"]) == history.EPOCH4_POLICY
    for path, expected in {**manifest["inventory"], **manifest["additional_sources"]}.items():
        assert digest(history.issued_f157_bytes(path)) == expected
    assert digest(history.issued_f157_bytes(WIRE)) == history.ISSUED_F157_WIRE_SHA256
    assert history.legacy_source_alias_wire_parser().__module__.endswith("._issued_f157_wire")
    old = history._verified_six_epochs(runtime=False)
    assert len(old) == 6 and old[-1] == manifest
    assert history.known_f157_transitions()


def test_current_alias_epoch_has_only_explicit_wire_cache_consumer_and_admission_deltas():
    documents = history._verified_seven_epochs()
    previous, current = documents[-2:]
    assert len(documents) == 7 and previous["policy_hash"] == history.EPOCH4_POLICY
    assert len(current["inventory"]) == 265
    old = {**previous["inventory"], **previous["additional_sources"]}
    new = {**current["inventory"], **current["additional_sources"]}
    assert {name for name in new if old.get(name) != new[name]} == {
        WIRE,
        RECOVERY,
        RECONCILIATION,
        ADMISSION,
        *CONSUMERS,
    }
    assert set(current["inventory"]) == set(previous["inventory"])
    assert set(current["additional_sources"]) == set(previous["additional_sources"]) | {ADMISSION}
    proof = history.verified_transition_from(history.EPOCH4_POLICY)
    assert proof == history.verified_alias_transition(deepcopy(proof))
    assert proof["current_policy_hash"] == current["policy_hash"]
    assert proof["provider_calls"] == 0
    assert all(
        proof[key] is False
        for key in (
            "new_credit",
            "dispatch_authorized",
            "grade_recomputed",
            "acceptance_semantics_identical",
        )
    )
    assert history.verified_alias_depth_limit(proof) == 6
    for saved in history.known_f157_transitions():
        assert history.verified_alias_transition(deepcopy(saved)) == saved


@pytest.mark.parametrize("claim", ["unknown", history.UNISSUED_POLICY, "0" * 64])
def test_unknown_or_unissued_epoch_never_acquires_a_transition(claim):
    with pytest.raises(ValueError, match="unsupported_previous_policy"):
        history.verified_transition_from(claim)


@pytest.mark.parametrize(
    "part", ["wire", "initializer", "manifest", "index", "forward", "other_source"]
)
def test_trusted_legacy_loader_rejects_any_changed_archive_bytes(tmp_path, monkeypatch, part):
    manifest = history.verified_issued_f157_archive()
    original = history.HERE
    target = tmp_path / "history"
    archive = target / "issued-f157"
    files = ["manifest.json", "final.json", "__init__.py", "known-forward-edges.json"]
    files.extend(
        "bytes/" + path + ".source"
        for path in {
            **manifest["inventory"],
            **manifest["additional_sources"],
        }
    )
    for name in files:
        out = archive / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes((original / "issued-f157" / name).read_bytes())
    damaged = {
        "wire": "bytes/" + WIRE + ".source",
        "initializer": "__init__.py",
        "manifest": "final.json",
        "index": "manifest.json",
        "forward": "known-forward-edges.json",
        "other_source": "bytes/src/bestseller/services/feedback.py.source",
    }[part]
    path = archive / damaged
    path.write_bytes(path.read_bytes() + b"\n")
    monkeypatch.setattr(history, "HERE", target)
    with pytest.raises(ValueError, match="manifest_not_frozen|issued_f157_source_changed"):
        history.legacy_source_alias_wire_parser()


def test_legacy_archive_never_resolves_an_external_or_unlisted_code_path():
    with pytest.raises(ValueError, match="unknown_issued_f157_source"):
        history.issued_f157_bytes("../../reports/arbitrary.py")


@pytest.mark.parametrize("path", [WIRE, RECOVERY, "src/bestseller/services/feedback.py"])
def test_current_epoch_requires_every_actual_runtime_source_byte(tmp_path, monkeypatch, path):
    current = history._verified_seven_epochs()[-1]
    for name in {**current["inventory"], **current["additional_sources"]}:
        out = tmp_path / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes((history.ROOT / name).read_bytes())
    changed = tmp_path / path
    changed.write_bytes(changed.read_bytes() + b"\n")
    monkeypatch.setattr(history, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="unlisted_runtime_delta"):
        history.verified_source_alias_runtime()


@pytest.mark.parametrize("change", ["wire_version", "paid_guard"])
def test_whole_file_pins_do_not_replace_the_independent_ast_boundary(monkeypatch, change):
    old_wire = history.issued_f157_bytes(WIRE)
    old_recovery = history.issued_f157_bytes(RECOVERY)
    current_wire = (history.ROOT / WIRE).read_bytes()
    current_recovery = (history.ROOT / RECOVERY).read_bytes()
    if change == "wire_version":
        needle = b'WIRE_VERSION = "commercial-review-wire.v2"'
        assert current_wire.count(needle) == 1
        current_wire = current_wire.replace(needle, b'WIRE_VERSION = "unsafe-wire"', 1)
        name = "commercial_review_wire.py"
        raw = current_wire
    else:
        needle = b'operation.status != "completed"'
        assert current_recovery.count(needle) >= 1
        current_recovery = current_recovery.replace(needle, b'operation.status != "unknown"', 1)
        name = "commercial_judge_recovery.py"
        raw = current_recovery
    old_pin = reconciliation._SOURCE_RECORD_ALIAS_PINS[name][0]
    monkeypatch.setitem(reconciliation._SOURCE_RECORD_ALIAS_PINS, name, (old_pin, digest(raw)))
    with pytest.raises(ValueError, match="other_wire_ast_changed|other_recovery_ast_changed"):
        reconciliation._source_record_alias_policy_compatibility(
            previous_wire=old_wire,
            current_wire=current_wire,
            previous_recovery=old_recovery,
            current_recovery=current_recovery,
            previous_consumers={p: history.issued_f157_bytes(p) for p in CONSUMERS},
            current_consumers={p: (history.ROOT / p).read_bytes() for p in CONSUMERS},
        )


def test_explicit_runtime_archive_file_list_cannot_grow_from_untrusted_paths():
    manifest = history.verified_issued_f157_archive()
    index = json.loads((history.HERE / "issued-f157/manifest.json").read_text())
    expected = {
        "bytes/" + path + ".source"
        for path in {
            **manifest["inventory"],
            **manifest["additional_sources"],
        }
    }
    assert set(index["source_files"]) == expected
    assert len(expected) == 266
    assert (
        len(expected | {"manifest.json", "final.json", "__init__.py", "known-forward-edges.json"})
        == 270
    )
    assert all(not Path(path).is_absolute() and ".." not in Path(path).parts for path in expected)
