"""Frozen unissued audit and fail-closed selectors, without DB or provider IO.

The 3e51 copy is audit evidence only. These tests establish neither a reviewed
current endpoint nor permission to issue aliases from an unreviewed main tree.
"""

import ast
from copy import deepcopy
import hashlib
from itertools import pairwise
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest
from test_native_policy_epoch2_transition import (
    _cycle,
    _validate,
)
from test_native_policy_epoch2_transition import (
    test_prepared_once_old_binding_is_not_rebound_for_another_upgrade as _prepared_once_check,
)

from bestseller.services import chapter_review_policy_history_8y24 as archive
from bestseller.services import chapter_review_reconciliation as service

pytestmark = pytest.mark.unit

UNISSUED_POLICY = "3e51eb1808c2f9970d6b668a5e27643a096c7522b59cc98fa464cc313022f40f"
UNISSUED_SHA256 = "78c202ca2204c2c12477558b2f590c294e2ee8f79d2e3de62dc19d1107e7e7c6"
UNISSUED_FILE_PINS = {
    "final.json": UNISSUED_SHA256,
    "__init__.py": "e52f4c79033f381033f7381d49dda0e1cec606dfbfda5ee78e6254faa2ccc1b4",
    "src/bestseller/services/fanqie_short_plan_resume.py": (
        "cc66105fc0cb43a7185464d25630981833c18ade5e6247b849bd61ad58491cf5"
    ),
    "src/bestseller/services/fanqie_short_pipeline.py": (
        "f22c3cac22e7070c4771a789f64e169e9486575ca0401cad88ae660aaa46da82"
    ),
    "src/bestseller/services/fanqie_beat_recovery.py": (
        "e3982c8d3152ef51c3903492a99bad54a32f6299ed47b8b8a6c835fc56e83dc4"
    ),
    "src/bestseller/services/chapter_review_reconciliation.py": (
        "2d6c6f3027f4483082c6858f4fbd2c8c2d43a8ecfd6952f9a65c88a0730c7df1"
    ),
}
UNISSUED_CONSUMER_INDEX_SHA256 = (
    "1e6fc8cdfb1cd49a2bf4807f799a34c90c76eb4136862d05792fee4ea7661539"
)
UNISSUED_CONSUMER_FILE_PINS = {
    "src/bestseller/services/materialized_outline_admission_read.py": (
        "37eadff37ee6101e0d591f2f655321f8cfca24df7f765ad43cf7efd601ae632d"
    ),
    "src/bestseller/services/outline_source_entry.py": (
        "28029611429eb3561bc4780a4d3650b05c10e20d096f908444662a03826276a1"
    ),
    "src/bestseller/services/editable_plan_source.py": (
        "46570e8ea27a3cc5884619cd930fdcc43e99790d26d8afbf6a0f605ac08ec656"
    ),
    "src/bestseller/services/chapter_writer_delivery.py": (
        "2ab77454cdf8de2c7e5ade7d4eb82cd3fb20669b889935b5c4d967d338cd7eb7"
    ),
    "src/bestseller/services/chapter_scene_contract_materializer.py": (
        "5479a8a365f81889cd3bdaf2bbab9c23d71ff2805d3138696cbb96f453b1e596"
    ),
    "src/bestseller/services/drafts.py": (
        "243f4f9d280919455e6f7e2e97cc0f33f69418d6f0e55f79d6a866f9af2a9da1"
    ),
}
CONSUMER_SNAPSHOTS = tuple(UNISSUED_CONSUMER_FILE_PINS)
SHORT_PLANNER = "src/bestseller/services/fanqie_short_planner.py"
UNISSUED_PLANNER_SHA256 = "2e3475f459af2dd622cec591f8ff66eb57e38d259415d9f776f338d573c844c3"
UNISSUED_PLANNER_INDEX_SHA256 = (
    "dc5bbffdd85d3d1b4aed1ad3b498514a5c66651e16fce806aa6b104e089de264"
)
SURGICAL_BUDGET = "src/bestseller/services/planning_surgical_budget.py"
UNISSUED_BUDGET_SHA256 = "cecd04653c244f9c4013730aef948df11d5a4c7a17d20b289301d50ace2cd13c"
UNISSUED_BUDGET_INDEX_SHA256 = (
    "14722dca0522d9ac25133e3e858e1ca163717ec9f8273a91f4fe1013bd481173"
)
SUPPLEMENTAL_SNAPSHOTS = (*CONSUMER_SNAPSHOTS, SHORT_PLANNER, SURGICAL_BUDGET)
ALL_UNISSUED_FILE_PINS = (
    UNISSUED_FILE_PINS | UNISSUED_CONSUMER_FILE_PINS
    | {SHORT_PLANNER: UNISSUED_PLANNER_SHA256, SURGICAL_BUDGET: UNISSUED_BUDGET_SHA256}
)
FINAL_SOURCE_PATHS = (
    archive._HELPER, archive._SHORT_PIPELINE, archive._BEAT, *SUPPLEMENTAL_SNAPSHOTS,
)
READER_AST_SHA256 = "e67077251b467c8d5b97af2981ba4a8d5246274c4357faa8262dfab2e6831ac0"
REVIEWED_CANDIDATE_AST_PINS = {
    "src/bestseller/services/materialized_outline_admission_read.py": READER_AST_SHA256,
    "src/bestseller/services/outline_source_entry.py": (
        "f1e53d10065acee7f74cb7b78143a0c1c449aa1af9fe9bae3991989b7c9dedd4"
    ),
    "src/bestseller/services/editable_plan_source.py": (
        "11503e8a089c33bf185628a5202c6eda4f46388a3ea518c05af3c5e35cbdabfc"
    ),
    "src/bestseller/services/chapter_writer_delivery.py": (
        "b57e983798074b5b3d462f07fcad724594acf5218f2d93350a9e95ff69058ad9"
    ),
    "src/bestseller/services/chapter_scene_contract_materializer.py": (
        "9a0000e0253a551ea2fa4655e6d0529624f5256fd9daaeaf4dda6e97bf7bb2af"
    ),
    "src/bestseller/services/drafts.py": (
        "dac311a89f21da2a574539fe0825e694f87adfbf63a544f7ba537d534ea61773"
    ),
}
PLANNER_METADATA_INSERTION = '''
content = batch.model_dump(mode="json")
if "_meta" in outline_payload:
    from copy import deepcopy
    if not isinstance(outline_payload["_meta"], dict):
        raise ValueError("fanqie_outline_metadata_must_be_object")
    content["_meta"] = deepcopy(outline_payload["_meta"])
'''
BUDGET_PROSPECTIVE_BRANCH = '''
if (request.logical_role == "planner"
        and request.prompt_template == FANQIE_FIELDS_TEMPLATE
        and request.prompt_version == FANQIE_FIELDS_VERSION):
    return True
'''
BUDGET_REGISTRATION = '''
FANQIE_FIELDS_TEMPLATE = "planner_structured_field_correction"
FANQIE_FIELDS_VERSION = "fanqie-plan-fields-v1"
'''
HELPER_SELECTION_INSERTION = '''
from bestseller.services.fanqie_outline_source_selection import SELECTION_KEY
full_selected = any(MARKER_KEY in (c.metadata_json or {}) for c in chapters)
ordinary_selected = any(SELECTION_KEY in (c.metadata_json or {}) for c in chapters)
_need(not (full_selected and ordinary_selected), "conflicting_selection_kinds")
selection_key = SELECTION_KEY if ordinary_selected else MARKER_KEY
markers = [(c.metadata_json or {}).get(selection_key) for c in chapters]
if ordinary_selected:
    _need(not any("outline_prefix_readiness" in (c.metadata_json or {})
                  for c in chapters), "conflicting_selection_kinds")
'''
COMPLETION_INSERTION = '''
from bestseller.services.fanqie_outline_contract_completion import complete_missing_outline_contract
outline_payload = await complete_missing_outline_contract(
    session, settings, project_id=project.id, outline_payload=outline_payload,
    requested_by=requested_by,
)
'''
ADMISSION_INSERTION = '''
await _checkpoint_commit(session)
from bestseller.services.fanqie_outline_source_selection import admit_and_select_outline_source
await admit_and_select_outline_source(
    session, settings, project_id=project.id,
    artifact_id=outline_result.source_artifact_id,
    materialization_workflow_id=outline_result.workflow_run_id,
    requested_by=requested_by,
)
await _checkpoint_commit(session)
'''
ACTUAL_FINAL_ONLY = pytest.mark.skipif(
    archive.FINAL_SHA256 is None,
    reason="Future-final evidence only: actual current endpoint is not frozen",
)
SNAPSHOTS = tuple(name for name in UNISSUED_FILE_PINS if name.startswith("src/"))
COUNT_KEYS = (
    "llm_runs_since_e3_runtime",
    "execution_attempts_since_e3_runtime",
    "quality_scores_since_e3_runtime",
    "review_reports_since_e3_runtime",
    "chapter_metadata_epoch_mentions",
)
HISTORICAL_EPOCHS = (
    (
        "original.json", "ORIGINAL_SHA256", "original_bytes",
        "318cde073c31d22cbc46c0782f014a9afe1c864abe2ec9b3d714792079a1525b",
        "1fd308264eae63f0f7ab761da701a50e971014d9e693c3c2bcec98181b111b3c",
    ),
    (
        "current.json", "CURRENT_SHA256", "prior_bytes",
        "8dd6490f0dcf9984b98484ee73b582ad4e7e80a2dca9b79dff1ba91b1b6a685c",
        "db1b38eba0e68ad0bc0b431329865aa55ec7ddd5cb0c37c426031a745a1fd634",
    ),
    (
        "a296-final.json", "A296_SHA256", "a296_bytes",
        "ad84ee5bf4a0fcbee4a1d55ea39ef7d5039dc32718c2cd1ccf5c794bc55f3371",
        "a2962a25cf438282381bc1e432a46496a6e2315148895f477f1f2fbc51b6623a",
    ),
)


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    import socket
    import sqlite3

    from sqlalchemy.engine import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine

    from bestseller.services import llm

    def denied(*args, **kwargs):
        raise AssertionError("Unissued endpoint tests forbid DB, provider and network IO")

    for cls, names in (
        (Engine, ("connect", "begin", "raw_connection")),
        (AsyncEngine, ("connect", "begin", "raw_connection")),
    ):
        for name in names:
            monkeypatch.setattr(cls, name, denied)
    monkeypatch.setattr(sqlite3, "connect", denied)
    monkeypatch.setattr(sqlite3.dbapi2, "connect", denied)
    monkeypatch.setattr(llm, "complete_text", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture(autouse=True)
def exact_historical_negative_contract(monkeypatch, tmp_path, request):
    """Keep the original staged-pin assertions on their exact issued loader.

    Actual-current selectors and aliases below still execute the live sixth
    loader. A historical negative must not fail at a newer outer epoch fence
    before reaching the original contract it was written to exercise.
    """
    name = request.node.originalname
    epoch = None
    if name == "test_selectors_fail_closed_for_each_missing_final_pin_without_automatic_pinning":
        epoch = "issued-c90a"
    elif name == "test_audited_unissued_manifest_never_qualifies_as_the_reviewed_current":
        epoch = "issued-1a5a"
    if epoch is None:
        return
    active = request.module.archive
    base = active.HERE / epoch
    pins = {
        "issued-c90a": (
            "ab82c88d9b8eb793fcd1dcc8ec28e9fd355ad7fd1d35db1cad5af87d2c7808e0",
            "5a2697cc87cb330f9057160de77696d67eaab50d18aa67f91b2b0db5f2184d7d",
        ),
        "issued-1a5a": (
            "5e9988639c3ce2de09abe8f2d169d101815380862b5d0c2ec7b46edfe50709bc",
            "98d4f5ed89927c5235eebcea7e42c92edc613ad2f19464b0a5ef0d18378db1bb",
        ),
    }
    source = (base / "__init__.py").read_bytes()
    assert hashlib.sha256(source).hexdigest() == pins[epoch][0]
    assert hashlib.sha256((base / "final.json").read_bytes()).hexdigest() == pins[epoch][1]
    historical = ModuleType(active.__name__)
    historical.__file__ = str(active.HERE / "__init__.py")
    exec(compile(source, str(base / "__init__.py"), "exec"), historical.__dict__)
    historical.ROOT = tmp_path / (epoch + "-runtime")
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
    monkeypatch.setitem(sys.modules, active.__name__, historical)


def _edge(source, target):
    """Expected wire fields, independent of the selector under test."""
    documents = archive._verified_four_epochs()
    shas = (archive.ORIGINAL_SHA256, archive.CURRENT_SHA256,
            archive.A296_SHA256, archive.FINAL_SHA256)
    previous, current = documents[source], documents[target]
    expected = archive._proof(previous, current, shas[source], shas[target])
    if target == 3 and current["policy_hash"] == archive.EPOCH4_POLICY:
        old, new = _files(previous), _files(current)
        expected.update(
            schema="native-review-policy-compatibility.verified-fact-deny-epoch4.v1",
            reviewed_delta_files=sorted(name for name in new if old.get(name) != new[name]),
            acceptance_semantics_identical=False,
            current_policy_reprojection="verified-fact-deny-only",
            paid_inputs_unchanged=True, model_requests_unchanged=True,
            renderers_unchanged=True, raw_grade_reports_attempts_immutable=True,
            retained_available_result="derive_current_policy_disposition_before_acceptance",
        )
    return expected


def _wrap(cycle, source, target):
    transition = _edge(source, target)
    assert cycle["policy_source_hash"] == transition["previous_policy_hash"]
    alias = dict(schema=service._NATIVE_ALIAS, project_id="project", chapter_id="chapter",
                 draft_id="draft", body_sha256="body", cycle_id="fixture",
                 review_workflow_run_id="owner", transition=transition,
                 original_cycle=deepcopy(cycle), input_proof={"synthetic_history": True},
                 ledger_sha256="fixture-ledger", control_command_serial=3,
                 provider_calls=0, new_credit=False, dispatch_authorized=False,
                 grade_recomputed=False, adoption_authorized=False)
    alias["receipt_sha256"] = service._native_digest(alias)
    result = deepcopy(cycle)
    result["policy_source_hash"] = transition["current_policy_hash"]
    result["native_runtime_policy_alias"] = alias
    return result


def _files(document):
    return {**document["inventory"], **document["additional_sources"]}


def _unissued_manifest():
    return archive._document("unissued-3e51/final.json", UNISSUED_SHA256)


def _deny_live_reads(monkeypatch):
    read = Path.read_bytes

    def frozen_only(path):
        if path.is_relative_to(archive.ROOT) and not path.is_relative_to(archive.HERE):
            raise AssertionError("Historical reconstruction must not read live source files")
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", frozen_only)


def _replace_bytes(monkeypatch, target, replacement):
    read = Path.read_bytes
    monkeypatch.setattr(
        Path, "read_bytes", lambda path: replacement if path == target else read(path)
    )


def _historical_claim():
    return archive._proof(
        archive._document("original.json", archive.ORIGINAL_SHA256),
        archive._document("current.json", archive.CURRENT_SHA256),
        archive.ORIGINAL_SHA256,
        archive.CURRENT_SHA256,
    )


@pytest.mark.parametrize("name,constant,reader,digest,policy", HISTORICAL_EPOCHS)
def test_original_prior_and_a296_full_archives_stay_exact(
    monkeypatch, name, constant, reader, digest, policy
):
    _deny_live_reads(monkeypatch)
    assert getattr(archive, constant) == digest
    document = archive._document(name, digest)
    assert document["policy_hash"] == policy
    assert len(document["inventory"]) == 263
    assert len(document["additional_sources"]) == 1
    assert archive._aggregate(document["inventory"]) == policy
    for relative, expected in _files(document).items():
        assert hashlib.sha256(getattr(archive, reader)(relative)).hexdigest() == expected, relative
    assert (archive.HERE / "prior-current.json").read_bytes() == (
        archive.HERE / "current.json"
    ).read_bytes()


def test_unissued_literal_identity_and_six_complete_file_pins():
    assert archive.UNISSUED_POLICY == UNISSUED_POLICY
    assert archive.UNISSUED_SHA256 == UNISSUED_SHA256
    assert archive.UNISSUED_FILE_PINS == UNISSUED_FILE_PINS
    base = archive.HERE / "unissued-3e51"
    for relative, expected in UNISSUED_FILE_PINS.items():
        path = base / "bytes" / (relative + ".source") if relative in SNAPSHOTS else base / relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, relative


def test_unissued_full_reconstruction_uses_initial_and_supplemental_snapshots_and_a296(
    monkeypatch,
):
    _deny_live_reads(monkeypatch)
    document = _unissued_manifest()
    previous = archive._document("a296-final.json", archive.A296_SHA256)
    assert document["policy_hash"] == UNISSUED_POLICY
    assert len(document["inventory"]) == 263
    assert archive._aggregate(document["inventory"]) == UNISSUED_POLICY
    assert set(document["inventory"]) == set(previous["inventory"])
    assert set(document["additional_sources"]) == set(previous["additional_sources"])
    assert {name for name, digest in _files(document).items()
            if digest != _files(previous)[name]} == set(SNAPSHOTS)
    base = archive.HERE / "unissued-3e51" / "bytes"
    assert {p.relative_to(base).as_posix() for p in base.rglob("*.source")} == {
        name + ".source" for name in SNAPSHOTS
    }
    for relative, expected in _files(document).items():
        raw = archive.unissued_bytes(relative)
        assert hashlib.sha256(raw).hexdigest() == expected, relative
        if relative in SNAPSHOTS:
            frozen = (base / (relative + ".source")).read_bytes()
        elif relative in SUPPLEMENTAL_SNAPSHOTS:
            frozen = (base.parent / "consumer-bytes" / (relative + ".source")).read_bytes()
        else:
            frozen = archive.a296_bytes(relative)
        assert raw == frozen, relative


def test_supplemental_consumer_index_and_inventory_are_exact_frozen_sources(monkeypatch):
    _deny_live_reads(monkeypatch)
    assert archive.UNISSUED_CONSUMER_FILE_PINS == UNISSUED_CONSUMER_FILE_PINS
    assert archive.UNISSUED_CONSUMER_INDEX_SHA256 == UNISSUED_CONSUMER_INDEX_SHA256
    assert archive.UNISSUED_FILE_PINS == UNISSUED_FILE_PINS
    assert set(archive.REVIEWED_BEAT_RESUME_SHA256) == set(FINAL_SOURCE_PATHS)
    assert set(archive.REVIEWED_ENDPOINT_AST_SHA256) == set(CONSUMER_SNAPSHOTS)
    assert archive._CURRENT_DELTA_FILES == set(FINAL_SOURCE_PATHS) | {archive._RECONCILIATION}
    base = archive.HERE / "unissued-3e51"
    index = archive._document("unissued-3e51/consumer-preimages.json",
                              UNISSUED_CONSUMER_INDEX_SHA256)
    assert index["file_pins"] == UNISSUED_CONSUMER_FILE_PINS
    assert index["policy_hash"] == UNISSUED_POLICY
    assert index["manifest_sha256"] == UNISSUED_SHA256
    assert index["runtime_audit_only"] is True
    assert index["alias_authority"] is False
    assert {p.relative_to(base / "consumer-bytes").as_posix()
            for p in (base / "consumer-bytes").rglob("*.source")} == {
        name + ".source" for name in SUPPLEMENTAL_SNAPSHOTS
    }
    documents = [archive._document(name, digest) for name, digest in (
        ("original.json", archive.ORIGINAL_SHA256),
        ("current.json", archive.CURRENT_SHA256),
        ("a296-final.json", archive.A296_SHA256),
        ("unissued-3e51/final.json", UNISSUED_SHA256),
    )]
    for relative, expected in UNISSUED_CONSUMER_FILE_PINS.items():
        assert all(d["inventory"][relative] == expected for d in documents), relative
        frozen = (base / "consumer-bytes" / (relative + ".source")).read_bytes()
        assert hashlib.sha256(frozen).hexdigest() == expected, relative
        assert (archive.original_bytes(relative) == archive.a296_bytes(relative)
                == archive.unissued_bytes(relative) == frozen), relative


@pytest.mark.parametrize("relative,path_name,source_pin,index_pin,index_filename,expected_index", [
    (SHORT_PLANNER, "_SHORT_PLANNER", "UNISSUED_PLANNER_SHA256", "UNISSUED_PLANNER_INDEX_SHA256",
     "planner-preimage.json", UNISSUED_PLANNER_INDEX_SHA256),
    (SURGICAL_BUDGET, "_SURGICAL_BUDGET", "UNISSUED_BUDGET_SHA256", "UNISSUED_BUDGET_INDEX_SHA256",
     "budget-preimage.json", UNISSUED_BUDGET_INDEX_SHA256),
])
def test_planner_and_budget_preimages_and_separate_indexes_preserve_original_trusted_bytes(
    monkeypatch, relative, path_name, source_pin, index_pin, index_filename, expected_index,
):
    _deny_live_reads(monkeypatch)
    expected_source = ALL_UNISSUED_FILE_PINS[relative]
    assert getattr(archive, path_name) == relative
    assert getattr(archive, source_pin) == expected_source
    assert getattr(archive, index_pin) == expected_index
    index = archive._document("unissued-3e51/" + index_filename, expected_index)
    assert index["file_pins"] == {relative: expected_source}
    assert index["policy_hash"] == UNISSUED_POLICY
    assert index["manifest_sha256"] == UNISSUED_SHA256
    assert index["runtime_audit_only"] is True
    assert index["alias_authority"] is False
    assert relative not in archive.UNISSUED_CONSUMER_FILE_PINS
    frozen = (archive.HERE / "unissued-3e51/consumer-bytes"
              / (relative + ".source")).read_bytes()
    assert hashlib.sha256(frozen).hexdigest() == expected_source
    for name, digest in (
        ("original.json", archive.ORIGINAL_SHA256),
        ("current.json", archive.CURRENT_SHA256),
        ("a296-final.json", archive.A296_SHA256),
        ("unissued-3e51/final.json", UNISSUED_SHA256),
    ):
        document = archive._document(name, digest)
        assert document["inventory"][relative] == expected_source
    assert (archive.original_bytes(relative) == archive.a296_bytes(relative)
            == archive.unissued_bytes(relative) == frozen)


@pytest.mark.parametrize("relative", SUPPLEMENTAL_SNAPSHOTS)
def test_supplemental_preimages_cannot_fall_back_to_a296_or_live_source(monkeypatch, relative):
    _deny_live_reads(monkeypatch)
    target = archive.HERE / "unissued-3e51/consumer-bytes" / (relative + ".source")
    read = Path.read_bytes

    def missing_snapshot(path):
        if path == target:
            raise FileNotFoundError("Required frozen supplemental preimage is absent")
        return read(path)

    def forbidden_fallback(*args, **kwargs):
        raise AssertionError("Missing supplemental snapshots must never use a fallback")

    monkeypatch.setattr(Path, "read_bytes", missing_snapshot)
    monkeypatch.setattr(archive, "a296_bytes", forbidden_fallback)
    with pytest.raises(FileNotFoundError, match="Required frozen supplemental preimage"):
        archive.unissued_bytes(relative)


@pytest.mark.parametrize("relative", ["../untrusted.py", "missing.py", "final.json"])
def test_unissued_reconstruction_rejects_sources_outside_inventory(relative):
    with pytest.raises(ValueError):
        archive.unissued_bytes(relative)


def test_unissued_audit_is_zero_count_evidence_without_alias_authority(monkeypatch):
    _deny_live_reads(monkeypatch)
    result = archive.verified_unissued_runtime_audit()
    assert type(result) is dict
    assert result["runtime_audit_only"] is True
    assert result["alias_authority"] is False
    assert result["policy_hash"] == UNISSUED_POLICY
    assert result["manifest_sha256"] == UNISSUED_SHA256
    assert result["file_pins"] == UNISSUED_FILE_PINS
    assert result["counts"] == dict.fromkeys(COUNT_KEYS, 0)
    audit_path = archive.HERE / "unissued-3e51" / "consumer-audit.json"
    audit_bytes = audit_path.read_bytes()
    assert hashlib.sha256(audit_bytes).hexdigest() == (
        "8069a95be024d4d6c33f2b3dab421b6e136286d173491830da7c7073be49ad00"
    )
    audit = json.loads(audit_bytes)
    assert audit["epoch"] == UNISSUED_POLICY
    assert audit["read_only"] is True
    assert audit["counts"] == result["counts"]
    assert audit["provider_calls"] == audit["database_writes"] == 0


@pytest.mark.parametrize(
    "relative",
    ["manifest.json", "consumer-audit.json", "consumer-preimages.json", "planner-preimage.json",
     "budget-preimage.json", "final.json", "__init__.py"]
    + ["bytes/" + name + ".source" for name in SNAPSHOTS]
    + ["consumer-bytes/" + name + ".source" for name in SUPPLEMENTAL_SNAPSHOTS],
)
def test_unissued_audit_rejects_every_modified_copy_file(monkeypatch, relative):
    archive.verified_unissued_runtime_audit()
    target = archive.HERE / "unissued-3e51" / relative
    _replace_bytes(monkeypatch, target, target.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        archive.verified_unissued_runtime_audit()


@pytest.mark.parametrize(
    "relative,reader,source",
    [
        ("original.json", "original_bytes", SNAPSHOTS[0]),
        ("current.json", "prior_bytes", SNAPSHOTS[0]),
        ("prior-current.json", "prior_bytes", SNAPSHOTS[0]),
        ("a296-final.json", "a296_bytes", SNAPSHOTS[0]),
        ("bytes/config/model_catalog.yaml.source", "original_bytes", "config/model_catalog.yaml"),
        ("prior-bytes/src/bestseller/services/pipelines.py.source", "prior_bytes",
         "src/bestseller/services/pipelines.py"),
    ] + [("a296-bytes/" + name + ".source", "a296_bytes", name) for name in SNAPSHOTS],
)
def test_historical_archive_manifest_and_source_tamper_rejected(
    monkeypatch, relative, reader, source
):
    getattr(archive, reader)(source)
    target = archive.HERE / relative
    _replace_bytes(monkeypatch, target, target.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        getattr(archive, reader)(source)


@pytest.mark.parametrize("relative", (*SNAPSHOTS, *SUPPLEMENTAL_SNAPSHOTS))
def test_unissued_reader_rejects_modified_fixed_snapshot(monkeypatch, relative):
    archive.unissued_bytes(relative)
    directory = "consumer-bytes" if relative in SUPPLEMENTAL_SNAPSHOTS else "bytes"
    target = archive.HERE / "unissued-3e51" / directory / (relative + ".source")
    _replace_bytes(monkeypatch, target, target.read_bytes() + b"\n# altered\n")
    with pytest.raises(ValueError):
        archive.unissued_bytes(relative)


@pytest.mark.parametrize("fault", ["missing_member", "extra_member", "source_digest",
                                  "policy_hash", "manifest_sha256", "runtime_audit_only",
                                  "alias_authority"])
@pytest.mark.parametrize("filename,pin_name,member", [
    ("consumer-preimages.json", "UNISSUED_CONSUMER_INDEX_SHA256", CONSUMER_SNAPSHOTS[0]),
    ("planner-preimage.json", "UNISSUED_PLANNER_INDEX_SHA256", SHORT_PLANNER),
    ("budget-preimage.json", "UNISSUED_BUDGET_INDEX_SHA256", SURGICAL_BUDGET),
])
def test_supplemental_index_semantics_reject_tamper_even_with_synthetic_index_hash(
    monkeypatch, fault, filename, pin_name, member,
):
    _deny_live_reads(monkeypatch)
    target = archive.HERE / "unissued-3e51" / filename
    changed = json.loads(target.read_bytes())
    if fault == "missing_member":
        changed["file_pins"].pop(member)
    elif fault == "extra_member":
        changed["file_pins"]["src/bestseller/services/untrusted.py"] = "f" * 64
    elif fault == "source_digest":
        changed["file_pins"][member] = "f" * 64
    elif fault in {"policy_hash", "manifest_sha256"}:
        changed[fault] = "f" * 64
    else:
        changed[fault] = not changed[fault]
    raw = json.dumps(changed).encode()
    _replace_bytes(monkeypatch, target, raw)
    # A synthetic index digest bypasses only the outer checksum, not semantic checks.
    monkeypatch.setattr(archive, pin_name, hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match="index_changed"):
        archive.verified_unissued_runtime_audit()


def test_unissued_reader_rejects_modified_a296_fallback(monkeypatch):
    relative = "config/model_catalog.yaml"
    archive.unissued_bytes(relative)
    target = archive.HERE / "bytes" / (relative + ".source")
    _replace_bytes(monkeypatch, target, target.read_bytes() + b"\n# altered\n")
    with pytest.raises(ValueError):
        archive.unissued_bytes(relative)


@pytest.mark.parametrize("count", COUNT_KEYS)
@pytest.mark.parametrize("copy", ["manifest.json", "consumer-audit.json"])
def test_unissued_audit_rejects_any_nonzero_consumer_count(monkeypatch, copy, count):
    archive.verified_unissued_runtime_audit()
    target = archive.HERE / "unissued-3e51" / copy
    changed = json.loads(target.read_bytes())
    changed["counts"][count] = 1
    _replace_bytes(monkeypatch, target, json.dumps(changed).encode())
    with pytest.raises(ValueError):
        archive.verified_unissued_runtime_audit()


@pytest.mark.parametrize("target", ["historical_four", "fifth_pin", "fifth_policy"])
def test_audited_unissued_manifest_never_qualifies_as_the_reviewed_current(monkeypatch, target):
    selected = archive
    if target == "historical_four":
        from types import ModuleType

        path = archive.HERE / "issued-c90a/__init__.py"
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == (
            "ab82c88d9b8eb793fcd1dcc8ec28e9fd355ad7fd1d35db1cad5af87d2c7808e0")
        selected = ModuleType("frozen_c90a_negative_fixture")
        selected.__file__ = str(path)
        exec(compile(raw, str(path), "exec"), selected.__dict__)
        selected.HERE, selected.ROOT = archive.HERE, archive.ROOT
        monkeypatch.setattr(selected, "REVIEWED_BEAT_RESUME_SHA256", {
            name: ALL_UNISSUED_FILE_PINS[name] for name in FINAL_SOURCE_PATHS
        })
    monkeypatch.setattr(selected, "FINAL_SHA256", UNISSUED_SHA256)
    frozen = (archive.HERE / "unissued-3e51/final.json").read_bytes()
    _replace_bytes(monkeypatch, archive.HERE / "final.json", frozen)
    _deny_live_reads(monkeypatch)
    if target == "historical_four":
        expected = "historical_policy"
    elif target == "fifth_policy":
        monkeypatch.setattr(archive, "EPOCH3_FINAL_SHA256", UNISSUED_SHA256)
        monkeypatch.setattr(archive, "EPOCH3_POLICY", UNISSUED_POLICY)
        expected = "fifth_policy_changed"
    else:
        expected = "fifth_final_pin_mismatch"
    with pytest.raises(ValueError, match=expected):
        selected.verified_transition()


@pytest.mark.parametrize("missing", ["final", *FINAL_SOURCE_PATHS])
def test_selectors_fail_closed_for_each_missing_final_pin_without_automatic_pinning(
    monkeypatch, missing
):
    # Supplying the old audit digest here is synthetic negative input only;
    # every case still lacks one mandatory reviewed endpoint pin.
    monkeypatch.setattr(archive, "FINAL_SHA256", None if missing == "final" else UNISSUED_SHA256)
    pins = {name: ALL_UNISSUED_FILE_PINS[name] for name in FINAL_SOURCE_PATHS}
    if missing != "final":
        pins[missing] = None
    monkeypatch.setattr(archive, "REVIEWED_BEAT_RESUME_SHA256", pins)
    before = deepcopy((archive.FINAL_SHA256, pins, archive.UNISSUED_FILE_PINS,
                       archive.UNISSUED_CONSUMER_FILE_PINS))
    # This copy may be audited successfully; that success grants no selector authority.
    archive.verified_unissued_runtime_audit()
    claim = _historical_claim()
    original_claim = deepcopy(claim)
    selectors = [archive.verified_transition,
                 lambda: archive.verified_alias_transition(claim)]
    for previous in (archive.OLD_POLICY, archive.PRIOR_POLICY, archive.A296_POLICY):
        selectors.append(lambda previous=previous: archive.verified_transition_from(previous))
    for select in selectors:
        with pytest.raises(ValueError, match="final_epoch_not_reviewed"):
            select()
        assert (archive.FINAL_SHA256, archive.REVIEWED_BEAT_RESUME_SHA256,
                archive.UNISSUED_FILE_PINS, archive.UNISSUED_CONSUMER_FILE_PINS) == before
        assert claim == original_claim


@pytest.mark.parametrize("previous", [UNISSUED_POLICY, "f" * 64])
def test_unissued_and_unknown_sources_rejected_before_endpoint_verification(monkeypatch, previous):
    def forbidden_endpoint():
        raise AssertionError("Unsupported sources must not reach endpoint verification")

    monkeypatch.setattr(archive, "_verified_four_epochs", forbidden_endpoint)
    with pytest.raises(ValueError, match="unsupported_previous_policy"):
        archive.verified_transition_from(previous)


@pytest.mark.parametrize("name,constant,reader,digest,policy", HISTORICAL_EPOCHS)
@pytest.mark.parametrize("direction", ["source", "target"])
def test_manufactured_unissued_source_and_target_proofs_never_authorize_aliases(
    monkeypatch, name, constant, reader, digest, policy, direction
):
    historical = archive._document(name, digest)
    unissued = _unissued_manifest()
    if direction == "source":
        claim = archive._proof(unissued, historical, UNISSUED_SHA256, digest)
    else:
        claim = archive._proof(historical, unissued, digest, UNISSUED_SHA256)
    before = deepcopy(claim)

    def forbidden_endpoint():
        raise AssertionError("Unissued proofs must be rejected before endpoint verification")

    monkeypatch.setattr(archive, "_verified_four_epochs", forbidden_endpoint)
    with pytest.raises(ValueError, match="unknown_alias_transition"):
        archive.verified_alias_transition(claim)
    assert claim == before


def _helper_prefix_candidate():
    # Build the entire expected candidate from the trusted archive, never live source.
    previous = archive.unissued_bytes(archive._HELPER)
    old = ('not any(a.artifact_type in {"chapter_outline_batch", "volume_chapter_outline"}'
           '\n                        for a in artifacts)')
    new = ('not any(a.artifact_type == "volume_chapter_outline"'
           '\n                        for a in artifacts)')
    text = previous.decode()
    assert text.count(old) == 1
    module = ast.parse(text.replace(old, new, 1))
    function = next(n for n in module.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "read_existing_complete_fanqie_plan")
    block = next(n for n in function.body if isinstance(n, ast.With))
    old_markers = ast.parse(
        "markers = [(c.metadata_json or {}).get(MARKER_KEY) for c in chapters]"
    ).body[0]
    matches = [i for i, n in enumerate(block.body) if ast.dump(n) == ast.dump(old_markers)]
    assert len(matches) == 1
    index = matches[0]
    block.body[index:index + 1] = ast.parse(HELPER_SELECTION_INSERTION).body
    return previous, ast.unparse(module).encode()


def test_helper_boundary_accepts_only_bounded_prefix_change_without_changing_pins(monkeypatch):
    _deny_live_reads(monkeypatch)
    before = deepcopy((archive.FINAL_SHA256, archive.REVIEWED_BEAT_RESUME_SHA256))
    previous, candidate = _helper_prefix_candidate()
    assert previous != candidate
    archive._exact_delta_boundary(previous, candidate, archive._HELPER)
    assert (archive.FINAL_SHA256, archive.REVIEWED_BEAT_RESUME_SHA256) == before


@pytest.mark.parametrize("fault", ["materialized_tail", "other_definition", "module_statement",
                                  "prefix_return", "additional_artifact_relaxation"])
def test_helper_boundary_rejects_materialized_tail_and_other_ast_changes(monkeypatch, fault):
    _deny_live_reads(monkeypatch)
    previous, candidate = _helper_prefix_candidate()
    archive._exact_delta_boundary(previous, candidate, archive._HELPER)
    text = candidate.decode()
    if fault == "materialized_tail":
        old = "target = list(range(1, current.target_chapters + 1))"
        assert text.count(old) == 1
        text = text.replace(old, "target = list(range(1, current.target_chapters + 2))", 1)
    elif fault == "other_definition":
        module = ast.parse(text)
        other = next(node for node in module.body
                     if isinstance(node, ast.FunctionDef) and node.name == "_need")
        other.body.append(ast.Pass())
        text = ast.unparse(module)
    elif fault == "module_statement":
        text += "\nUNRELATED_POLICY_CHANGE = True\n"
    elif fault == "prefix_return":
        old = "return await read_selected_foundation_stage(session, settings, project=current)"
        assert text.count(old) == 1
        text = text.replace(old, "return None", 1)
    else:
        old = "a.artifact_type == 'volume_chapter_outline'"
        assert text.count(old) == 1
        text = text.replace(old, 'a.artifact_type == "ignored_artifact"', 1)
    with pytest.raises(ValueError, match="endpoint_helper_nonprefix_changed"):
        archive._exact_delta_boundary(previous, text.encode(), archive._HELPER)


@pytest.mark.parametrize("fault", ["selection_import", "conflict_guard", "key_choice",
                                  "prefix_conflict_guard"])
def test_helper_boundary_requires_exact_selection_import_key_and_conflict_guards(
    monkeypatch, fault,
):
    _deny_live_reads(monkeypatch)
    previous, candidate = _helper_prefix_candidate()
    module = ast.parse(candidate)
    function = next(n for n in module.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "read_existing_complete_fanqie_plan")
    block = next(n for n in function.body if isinstance(n, ast.With))
    insertion = ast.parse(HELPER_SELECTION_INSERTION).body
    offset = {"selection_import": 0, "conflict_guard": 3,
              "key_choice": 4, "prefix_conflict_guard": 6}[fault]
    index = next(i for i, n in enumerate(block.body)
                 if ast.dump(n) == ast.dump(insertion[offset]))
    if fault == "key_choice":
        block.body[index] = ast.parse("selection_key = MARKER_KEY").body[0]
    else:
        del block.body[index]
    with pytest.raises(ValueError, match="endpoint_helper_nonprefix_changed"):
        archive._exact_delta_boundary(previous, ast.unparse(module).encode(), archive._HELPER)


def test_helper_boundary_rejects_nonfrozen_preimage(monkeypatch):
    _deny_live_reads(monkeypatch)
    previous, candidate = _helper_prefix_candidate()
    with pytest.raises(ValueError, match="endpoint_preimage_changed"):
        archive._exact_delta_boundary(previous + b"\n", candidate, archive._HELPER)


def test_pipeline_boundary_stays_closed_until_literal_admission_api_is_reviewed(monkeypatch):
    _deny_live_reads(monkeypatch)
    before = deepcopy((archive.FINAL_SHA256, archive.REVIEWED_BEAT_RESUME_SHA256))
    monkeypatch.setattr(archive, "REVIEWED_ADMISSION_INSERTION", None)
    previous = archive.unissued_bytes(archive._SHORT_PIPELINE)
    with pytest.raises(ValueError, match="outline_admission_api_not_reviewed"):
        archive._exact_delta_boundary(previous, previous, archive._SHORT_PIPELINE)
    assert archive.REVIEWED_ADMISSION_INSERTION is None
    assert (archive.FINAL_SHA256, archive.REVIEWED_BEAT_RESUME_SHA256) == before


def _pipeline_parts(module):
    function = next(n for n in module.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "run_fanqie_short_pipeline")
    projection = ast.parse("outline_payload = build_fanqie_segment_outline_batch()").body[0]
    stage = next(n for n in function.body if isinstance(n, ast.If) and any(
        isinstance(v, ast.Assign) and isinstance(v.value, ast.Call)
        and ast.dump(v.value.func) == ast.dump(projection.value.func) for v in n.body
    ))
    return function, stage


def _pipeline_insertion_candidate():
    previous = archive.unissued_bytes(archive._SHORT_PIPELINE)
    module = ast.parse(previous)
    _, stage = _pipeline_parts(module)
    projection = next(i for i, n in enumerate(stage.body)
                      if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                      and isinstance(n.value.func, ast.Name)
                      and n.value.func.id == "build_fanqie_segment_outline_batch")
    stage.body[projection + 1:projection + 1] = ast.parse(COMPLETION_INSERTION).body
    materialization = next(i for i, n in enumerate(stage.body)
                           if isinstance(n, ast.Assign)
                           and any(isinstance(t, ast.Name) and t.id == "outline_result"
                                   for t in n.targets))
    stage.body[materialization + 1:materialization + 1] = ast.parse(ADMISSION_INSERTION).body
    return previous, ast.unparse(module).encode()


def test_pipeline_boundary_accepts_exact_frozen_completion_and_reviewed_selection_insertions(
    monkeypatch,
):
    _deny_live_reads(monkeypatch)
    before = deepcopy((archive.FINAL_SHA256, archive.REVIEWED_BEAT_RESUME_SHA256,
                       archive.REVIEWED_ENDPOINT_AST_SHA256))
    assert ast.dump(ast.parse(archive.REVIEWED_ADMISSION_INSERTION)) == ast.dump(
        ast.parse(ADMISSION_INSERTION)
    )
    previous, candidate = _pipeline_insertion_candidate()
    assert previous != candidate
    archive._exact_delta_boundary(previous, candidate, archive._SHORT_PIPELINE)
    assert (archive.FINAL_SHA256, archive.REVIEWED_BEAT_RESUME_SHA256,
            archive.REVIEWED_ENDPOINT_AST_SHA256) == before


@pytest.mark.parametrize("fault", ["duplicate_completion", "duplicate_selection",
                                  "completion_call_order", "selection_call_order",
                                  "missing_checkpoint_before", "missing_checkpoint_after",
                                  "selection_before_materialization", "writer_body"])
def test_pipeline_boundary_rejects_duplicate_reordered_incomplete_or_writer_changes(
    monkeypatch, fault,
):
    _deny_live_reads(monkeypatch)
    previous, candidate = _pipeline_insertion_candidate()
    module = ast.parse(candidate)
    _, stage = _pipeline_parts(module)
    completion = ast.parse(COMPLETION_INSERTION).body
    selection = ast.parse(ADMISSION_INSERTION).body
    completion_index = next(i for i, n in enumerate(stage.body)
                            if ast.dump(n) == ast.dump(completion[0]))
    selection_index = next(i for i, n in enumerate(stage.body)
                           if ast.dump(n) == ast.dump(selection[1])) - 1
    if fault == "duplicate_completion":
        stage.body[completion_index:completion_index] = deepcopy(completion)
    elif fault == "duplicate_selection":
        stage.body[selection_index:selection_index] = deepcopy(selection)
    elif fault == "completion_call_order":
        stage.body[completion_index:completion_index + 2] = list(reversed(completion))
    elif fault == "selection_call_order":
        stage.body[selection_index + 1:selection_index + 3] = [selection[2], selection[1]]
    elif fault == "missing_checkpoint_before":
        del stage.body[selection_index]
    elif fault == "missing_checkpoint_after":
        del stage.body[selection_index + 3]
    elif fault == "selection_before_materialization":
        del stage.body[selection_index:selection_index + 4]
        stage.body[selection_index - 1:selection_index - 1] = selection
    else:
        writer = next(n for n in module.body if isinstance(n, ast.AsyncFunctionDef)
                      and n.name == "_run_fanqie_segments")
        writer.body.append(ast.Pass())
    with pytest.raises(ValueError, match="endpoint_pipeline_nonplanning_changed"):
        archive._exact_delta_boundary(
            previous, ast.unparse(module).encode(), archive._SHORT_PIPELINE,
        )


def test_pipeline_boundary_rejects_nonfrozen_preimage(monkeypatch):
    _deny_live_reads(monkeypatch)
    previous, candidate = _pipeline_insertion_candidate()
    with pytest.raises(ValueError, match="endpoint_preimage_changed"):
        archive._exact_delta_boundary(previous + b"\n", candidate, archive._SHORT_PIPELINE)


def _planner_metadata_candidate():
    previous = archive.unissued_bytes(SHORT_PLANNER)
    text = previous.decode()
    typed_validation = "    batch = ChapterOutlineBatchInput.model_validate(outline_payload)\n"
    typed_content = 'content=batch.model_dump(mode="json")'
    assert text.count(typed_validation) == text.count(typed_content) == 1
    insertion = "\n".join("    " + line for line in PLANNER_METADATA_INSERTION.strip().splitlines())
    text = text.replace(typed_validation, typed_validation + insertion + "\n", 1)
    return previous, text.replace(typed_content, "content=content", 1).encode()


def test_planner_boundary_accepts_only_literal_typed_body_and_deepcopied_metadata(monkeypatch):
    _deny_live_reads(monkeypatch)
    before = deepcopy((archive.FINAL_SHA256, archive.REVIEWED_BEAT_RESUME_SHA256,
                       archive.REVIEWED_ENDPOINT_AST_SHA256))
    previous, candidate = _planner_metadata_candidate()
    assert previous != candidate
    archive._exact_delta_boundary(previous, candidate, SHORT_PLANNER)
    assert (archive.FINAL_SHA256, archive.REVIEWED_BEAT_RESUME_SHA256,
            archive.REVIEWED_ENDPOINT_AST_SHA256) == before


@pytest.mark.parametrize("fault", ["nonobject_guard_bypass", "deepcopy_bypass", "typed_body",
                                  "source_run_id", "import_content", "other_function",
                                  "module_statement"])
def test_planner_boundary_rejects_metadata_bypasses_and_all_unrelated_ast_changes(
    monkeypatch, fault,
):
    _deny_live_reads(monkeypatch)
    previous, candidate = _planner_metadata_candidate()
    module = ast.parse(candidate)
    function = next(n for n in module.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "persist_fanqie_chapter_outline")
    metadata = next(n for n in function.body if isinstance(n, ast.If))
    if fault == "nonobject_guard_bypass":
        metadata.body = [n for n in metadata.body if not isinstance(n, ast.If)]
    elif fault == "deepcopy_bypass":
        metadata.body[-1] = ast.parse('content["_meta"] = outline_payload["_meta"]').body[0]
    elif fault == "typed_body":
        function.body[1] = ast.parse("content = outline_payload").body[0]
    elif fault == "source_run_id":
        metadata.body.extend(ast.parse('content["_meta"]["source_run_id"] = "forged"').body)
    elif fault == "import_content":
        create = next(n for n in ast.walk(function) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Name) and n.func.id == "PlanningArtifactCreate")
        content = next(keyword for keyword in create.keywords if keyword.arg == "content")
        content.value = ast.Name(id="outline_payload", ctx=ast.Load())
    elif fault == "other_function":
        other = next(n for n in module.body if isinstance(n, ast.FunctionDef)
                     and n.name == "build_fanqie_segment_outline_batch")
        other.body.extend(ast.parse('raise RuntimeError("unreviewed producer change")').body)
    else:
        module.body.extend(ast.parse("UNRELATED_PLANNER_CHANGE = True").body)
    with pytest.raises(ValueError, match="planner_nonmetadata_changed"):
        archive._exact_delta_boundary(previous, ast.unparse(module).encode(), SHORT_PLANNER)


def test_planner_boundary_rejects_nonfrozen_preimage(monkeypatch):
    _deny_live_reads(monkeypatch)
    previous, candidate = _planner_metadata_candidate()
    with pytest.raises(ValueError, match="endpoint_preimage_changed"):
        archive._exact_delta_boundary(previous + b"\n", candidate, SHORT_PLANNER)


def _budget_registered_applies_candidate():
    previous = archive.unissued_bytes(SURGICAL_BUDGET)
    module = ast.parse(previous)
    function = next(n for n in module.body
                    if isinstance(n, ast.FunctionDef) and n.name == "applies")
    index = module.body.index(function)
    module.body[index:index] = ast.parse(BUDGET_REGISTRATION).body
    function.body[0:0] = ast.parse(BUDGET_PROSPECTIVE_BRANCH).body
    return previous, ast.unparse(module).encode()


def test_budget_boundary_stays_closed_without_reviewed_prospective_branch(monkeypatch):
    _deny_live_reads(monkeypatch)
    monkeypatch.setattr(archive, "REVIEWED_BUDGET_BRANCH", None)
    previous, candidate = _budget_registered_applies_candidate()
    with pytest.raises(ValueError, match="prospective_budget_api_not_reviewed"):
        archive._exact_delta_boundary(previous, candidate, SURGICAL_BUDGET)
    assert archive.REVIEWED_BUDGET_BRANCH is None


def test_budget_boundary_accepts_frozen_pair_registration_and_applies_without_final_pinning(
    monkeypatch,
):
    _deny_live_reads(monkeypatch)
    before = deepcopy((archive.FINAL_SHA256, archive.REVIEWED_BEAT_RESUME_SHA256))
    assert ast.dump(ast.parse(archive.REVIEWED_BUDGET_BRANCH)) == ast.dump(
        ast.parse(BUDGET_PROSPECTIVE_BRANCH)
    )
    assert ast.dump(ast.parse(archive.REVIEWED_BUDGET_REGISTRATION)) == ast.dump(
        ast.parse(BUDGET_REGISTRATION)
    )
    previous, candidate = _budget_registered_applies_candidate()
    archive._exact_delta_boundary(previous, candidate, SURGICAL_BUDGET)
    assert (archive.FINAL_SHA256, archive.REVIEWED_BEAT_RESUME_SHA256) == before


@pytest.mark.parametrize("fault", ["pair", "duplicate_branch", "old_applies_tail",
                                  "other_function", "module_constant"])
def test_budget_boundary_rejects_pair_relaxation_and_changes_outside_added_branch(
    monkeypatch, fault,
):
    _deny_live_reads(monkeypatch)
    previous, candidate = _budget_registered_applies_candidate()
    module = ast.parse(candidate)
    function = next(n for n in module.body
                    if isinstance(n, ast.FunctionDef) and n.name == "applies")
    if fault == "pair":
        function.body[0].test.values[-1] = ast.Constant(value=True)
    elif fault == "duplicate_branch":
        function.body.insert(0, deepcopy(function.body[0]))
    elif fault == "old_applies_tail":
        function.body[-1] = ast.parse("return True").body[0]
    elif fault == "other_function":
        other = next(n for n in module.body if isinstance(n, ast.FunctionDef)
                     and n.name == "output_budget")
        other.body.extend(ast.parse('raise RuntimeError("unreviewed output cap change")').body)
    else:
        module.body.extend(ast.parse('FANQIE_FIELDS_VERSION = "fanqie-plan-fields-v1"').body)
    with pytest.raises(ValueError, match="budget_nonprospective_changed"):
        archive._exact_delta_boundary(previous, ast.unparse(module).encode(), SURGICAL_BUDGET)


@pytest.mark.parametrize("relative,expected", REVIEWED_CANDIDATE_AST_PINS.items())
def test_current_reader_and_five_reviewed_consumers_match_whole_ast_without_final_approval(
    relative, expected,
):
    previous = archive.unissued_bytes(relative)
    current = (archive.ROOT / relative).read_bytes()
    assert archive.REVIEWED_ENDPOINT_AST_SHA256[relative] == expected
    structural_bytes = ast.dump(ast.parse(current), include_attributes=False).encode()
    assert hashlib.sha256(structural_bytes).hexdigest() == expected
    before = deepcopy((archive.FINAL_SHA256, archive.REVIEWED_BEAT_RESUME_SHA256))
    archive._exact_delta_boundary(previous, current, relative)
    assert (archive.FINAL_SHA256, archive.REVIEWED_BEAT_RESUME_SHA256) == before


@pytest.mark.parametrize("fault", ["module_statement", "function_body"])
@pytest.mark.parametrize("relative", REVIEWED_CANDIDATE_AST_PINS)
def test_reader_and_five_reviewed_consumers_whole_ast_rejects_unrelated_changes(fault, relative):
    previous = archive.unissued_bytes(relative)
    module = ast.parse((archive.ROOT / relative).read_bytes())
    if fault == "module_statement":
        module.body.extend(ast.parse("UNRELATED_READER_CHANGE = True").body)
    else:
        function = next(n for n in module.body
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
        function.body.append(ast.Pass())
    with pytest.raises(ValueError, match="endpoint_consumer_ast_changed"):
        archive._exact_delta_boundary(previous, ast.unparse(module).encode(), relative)


@pytest.mark.parametrize("relative", CONSUMER_SNAPSHOTS)
def test_missing_reader_or_consumer_ast_pin_fails_closed(monkeypatch, relative):
    _deny_live_reads(monkeypatch)
    pins = {**archive.REVIEWED_ENDPOINT_AST_SHA256, relative: None}
    monkeypatch.setattr(archive, "REVIEWED_ENDPOINT_AST_SHA256", pins)
    previous = archive.unissued_bytes(relative)
    with pytest.raises(ValueError, match="endpoint_ast_not_reviewed"):
        archive._exact_delta_boundary(previous, previous, relative)
    assert archive.REVIEWED_ENDPOINT_AST_SHA256 == pins


def test_source_entry_actual_candidate_is_rejected_without_reviewed_ast_pin(monkeypatch):
    # Explicit source approval cannot bypass a deliberately missing structural pin.
    relative = "src/bestseller/services/outline_source_entry.py"
    previous = archive.unissued_bytes(relative)
    current = (archive.ROOT / relative).read_bytes()
    pins = {**archive.REVIEWED_ENDPOINT_AST_SHA256, relative: None}
    monkeypatch.setattr(archive, "REVIEWED_ENDPOINT_AST_SHA256", pins)
    with pytest.raises(ValueError, match="endpoint_ast_not_reviewed"):
        archive._exact_delta_boundary(previous, current, relative)
    assert archive.REVIEWED_ENDPOINT_AST_SHA256 == pins


@ACTUAL_FINAL_ONLY
def test_issued_fifth_epoch_preserves_historical_epochs_and_authorized_delta_inventory():
    # Preserve the exact issued fifth endpoint's original 263-file/13-delta
    # assertions. Current sixth-runtime additions are checked separately below.
    archive._verified_four_epochs()  # Full live runtime must still verify.
    original, prior, a296, _, current = archive._verified_five_epochs(runtime=False)
    for document, name, digest in (
        (original, "original.json", archive.ORIGINAL_SHA256),
        (prior, "current.json", archive.CURRENT_SHA256),
        (a296, "a296-final.json", archive.A296_SHA256),
    ):
        assert document == archive._document(name, digest)
        assert len(document["inventory"]) == 263
        assert archive._aggregate(document["inventory"]) == document["policy_hash"]
    authorized = (archive._A296_SNAPSHOTS | UNISSUED_CONSUMER_FILE_PINS.keys()
                  | {SHORT_PLANNER, SURGICAL_BUDGET})
    assert authorized == set(FINAL_SOURCE_PATHS) | {archive._RECONCILIATION}
    issued = archive.verified_c90a_archive()
    assert {name for name in issued["inventory"]
            if issued["inventory"][name] != a296["inventory"][name]} == (
        authorized - {archive._RECONCILIATION}
    )
    assert issued["epoch_delta_files"] == sorted(authorized)
    additions = {
        "src/bestseller/services/commercial_judge_recovery.py":
            "0c929fff84e3075996c25cc11b589df2c42bc619e6bd53acda1ef4e7d5a65fd7",
        "src/bestseller/services/execution_control_purpose.py":
            "70e09a76e170c9ba139c3135d597dfaabc05a018cc1013de0cb2c543bbc0a3c8",
    }
    assert {name for name in current["inventory"]
            if current["inventory"][name] != issued["inventory"][name]} == set(additions)
    assert all(current["inventory"][name] == digest for name, digest in additions.items())
    authorized = authorized | additions.keys()
    assert set(current["inventory"]) == set(original["inventory"])
    assert set(current["additional_sources"]) == set(original["additional_sources"])
    assert {name for name in current["inventory"]
            if current["inventory"][name] != a296["inventory"][name]} == (
        authorized - {archive._RECONCILIATION}
    )
    assert len(authorized & current["inventory"].keys()) == 13
    assert {name for name in current["additional_sources"]
            if current["additional_sources"][name] != a296["additional_sources"][name]} == {
        archive._RECONCILIATION
    }
    assert {name for name in _files(current)
            if _files(current)[name] != _files(a296)[name]} == authorized
    assert current["epoch_delta_files"] == sorted({*additions, archive._RECONCILIATION})


@ACTUAL_FINAL_ONLY
def test_actual_sixth_epoch_has_only_four_changed_policy_modules_and_two_new_modules():
    if archive.EPOCH4_FINAL_SHA256 is None:
        # This test remains valid during a fifth-only staged contract replay.
        assert archive._verified_four_epochs()[-1]["policy_hash"] == archive.EPOCH3_POLICY
        return
    original, _, _, _, fifth, current = archive._verified_six_epochs()
    added = {
        "src/bestseller/services/chapter_verified_fact_disposition.py",
        "src/bestseller/services/chapter_review_fact_repair.py",
    }
    changed = {
        "src/bestseller/services/reviews.py",
        "src/bestseller/services/pipelines.py",
        "src/bestseller/services/draft_promotion.py",
        "src/bestseller/services/retained_review_input_proof.py",
    }
    assert len(current["inventory"]) == 265
    assert set(current["inventory"]) == set(fifth["inventory"]) | added
    assert {name for name in fifth["inventory"]
            if fifth["inventory"][name] != current["inventory"][name]} == changed
    assert set(current["additional_sources"]) == {archive._RECONCILIATION}
    assert current["additional_sources"] != fifth["additional_sources"]
    assert current["prior_policy_hash"] == fifth["policy_hash"] == archive.EPOCH3_POLICY
    assert current["epoch_delta_files"] == sorted(added | changed | {archive._RECONCILIATION})
    assert {name: _files(current)[name] for name in current["epoch_delta_files"]} == (
        archive.REVIEWED_EPOCH4_SOURCE_SHA256
    )
    assert current["reviewed_delta_files"] == sorted(
        name for name in _files(current)
        if _files(current)[name] != _files(original).get(name)
    )


@ACTUAL_FINAL_ONLY
@pytest.mark.parametrize("source", [0, 1, 2])
def test_future_final_original_prior_a296_select_actual_current_without_selector_patches(source):
    documents = archive._verified_four_epochs()
    transition = archive.verified_transition_from(documents[source]["policy_hash"])
    assert transition == _edge(source, 3)
    assert transition["current_policy_hash"] == documents[3]["policy_hash"]
    assert transition["current_manifest_sha256"] == archive.FINAL_SHA256
    assert transition["provider_calls"] == 0
    assert transition["new_credit"] is transition["dispatch_authorized"] is False
    if source == 0:
        assert archive.verified_transition() == transition


@ACTUAL_FINAL_ONLY
@pytest.mark.parametrize("source,target", [(0, 1), (0, 2), (1, 2), (0, 3), (1, 3), (2, 3)])
def test_future_final_all_six_real_known_edges_replay_without_rewriting_claim(source, target):
    claim = _edge(source, target)
    before = deepcopy(claim)
    assert archive.verified_alias_transition(claim) is claim
    assert claim == before


@ACTUAL_FINAL_ONLY
@pytest.mark.parametrize("path", [(0, 1), (0, 2), (1, 2), (0, 3), (1, 3), (2, 3),
                                 (0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3), (0, 1, 2, 3)])
def test_future_final_up_to_three_real_aliases_preserve_nested_history(path):
    cycle = _cycle(record={"review_report_id": "report", "waiting_receipt": {"immutable": True}})
    if path[0] == 1:
        cycle["policy_source_hash"] = archive.PRIOR_POLICY
    elif path[0] == 2:
        cycle["policy_source_hash"] = archive.A296_POLICY
    for source, target in pairwise(path):
        before = deepcopy(cycle)
        cycle = _wrap(cycle, source, target)
        assert cycle["native_runtime_policy_alias"]["original_cycle"] == before
    before = deepcopy(cycle)
    _validate(cycle)
    assert cycle == before


def test_depth_three_rejected_before_transition_selection_without_mutating_history(monkeypatch):
    def forbidden_selector(*args, **kwargs):
        raise AssertionError("Depth limit must reject before selecting an endpoint")

    monkeypatch.setattr(archive, "verified_alias_transition", forbidden_selector)
    cycle = _cycle()
    before = deepcopy(cycle)
    with pytest.raises(ValueError, match="alias_chain_depth"):
        service._validate_native_alias_chain(
            cycle, project_id="project", chapter_id="chapter", draft_id="draft",
            body_sha256="body", depth=3,
        )
    assert cycle == before


def test_prepared_once_binding_stays_unchanged_and_rejects_another_policy():
    # Reuse only this pure check; its module's live-current tests are not collected.
    _prepared_once_check()
