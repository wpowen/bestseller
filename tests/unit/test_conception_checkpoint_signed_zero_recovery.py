"""Archive-backed signed-zero recovery, with strict legacy validation unchanged."""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable
from test_conception_checkpoints import _state

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services import conception_checkpoints as cp

pytestmark = pytest.mark.unit


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(type_, compiler, **kwargs):
    return "JSON"


def encoded(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def legacy_case():
    state = _state()
    state["conception_log"] = [
        {
            "synopsis_selection": [
                {"candidate": "first", "rank": [1.0, -0.0, -8.0]},
                {"candidate": "last", "rank": [0.0, -9.0, -0.0]},
            ]
        },
    ]
    state["last_failure"] = {"conception_log": deepcopy(state["conception_log"]), "keep": 17}
    state["typed_values"] = {
        "integer": 0,
        "bool": False,
        "float": 0.0,
        "other_float": 1.25,
        "text": "-0.0",
    }
    original = {
        "schema": cp.SCHEMA_VERSION,
        "stage": cp.CHECKPOINT_STAGE,
        "input_hash": "isolated-input",
        "state": state,
        "state_sha256": cp._state_hash(state),
    }
    archive = encoded({"conception_log": state["conception_log"], "producer_note": "unchanged"})
    # Controlled JSONB sign-loss simulation, not a live PostgreSQL test.
    stored = json.loads(encoded(original).replace(":-0.0", ":0.0").replace(",-0.0", ",0.0"))
    return original, stored, archive


def derive(stored, archive, input_hash="isolated-input"):
    return cp.derive_signed_zero_checkpoint_recovery(
        stored,
        input_hash=input_hash,
        retained_producer_json=archive,
    )


def test_future_build_normalizes_payload_only_and_does_not_change_strict_hash_or_input_hash():
    state = _state()
    state["nested"] = [
        {
            "minus": -0.0,
            "plus": 0.0,
            "integer": 0,
            "bool": False,
            "other_float": -1.25,
            "text": "-0.0",
        }
    ]
    original_bytes = encoded(state)
    checkpoint = cp.build_conception_checkpoint(state, input_hash="input")
    nested = checkpoint["state"]["nested"][0]
    assert math.copysign(1, nested["minus"]) == 1
    assert type(nested["minus"]) is float and type(nested["integer"]) is int
    assert type(nested["bool"]) is bool and nested["other_float"] == -1.25
    assert nested["text"] == "-0.0" and encoded(state) == original_bytes
    assert checkpoint["state_sha256"] == cp._state_hash(checkpoint["state"])
    assert cp._state_hash(state) != checkpoint["state_sha256"]
    assert cp.stable_conception_input_hash({"value": -0.0}) != cp.stable_conception_input_hash(
        {"value": 0.0}
    )
    assert cp.validate_conception_checkpoint(checkpoint, input_hash="input") == checkpoint["state"]


def test_archive_proof_reconstructs_old_hash_and_produces_jsonb_safe_separate_checkpoint():
    original, stored, archive = legacy_case()
    before = encoded(stored)
    with pytest.raises(ValueError, match="state_sha256 mismatch"):
        cp.validate_conception_checkpoint(stored, input_hash="isolated-input")
    with pytest.raises(ValueError, match="state_sha256 mismatch"):
        cp.select_conception_checkpoint_for_resume(stored, input_hash="isolated-input")
    proof = derive(stored, archive)
    assert len(proof["changed_paths"]) == 4
    assert proof["reconstructed_state_sha256"] == original["state_sha256"]
    assert proof["original_checkpoint_modified"] is False
    assert proof["retained_producer_json"] == archive and "-0.0" in archive
    assert proof["retained_producer_sha256"] == hashlib.sha256(archive.encode()).hexdigest()
    assert proof["original_checkpoint_sha256"] == cp._state_hash(stored)
    assert encoded(stored) == before
    selected = cp.select_conception_checkpoint_for_resume(
        stored,
        input_hash="isolated-input",
        signed_zero_recovery=proof,
    )
    assert selected == proof["derived_checkpoint"]
    # A JSONB-like zero-sign round trip changes no numeric proof payload; the
    # historical spelling remains inside the retained archive STRING.
    roundtrip = json.loads(encoded(proof))
    assert (
        cp.validate_signed_zero_checkpoint_recovery(
            roundtrip,
            checkpoint=stored,
            input_hash="isolated-input",
        )
        == selected
    )
    selected["state"]["title"] = "local mutation"
    assert proof["derived_checkpoint"]["state"]["title"] != "local mutation"


@pytest.mark.parametrize(
    "fault",
    [
        "late_string",
        "int_to_bool",
        "float_to_int",
        "other_float",
        "missing_row",
        "extra_field",
        "reverse_sign",
    ],
)
def test_any_other_producer_log_difference_rejects_entire_recovery(fault):
    _, stored, archive = legacy_case()
    producer = json.loads(archive)
    row = producer["conception_log"][-1]["synopsis_selection"][-1]
    if fault == "late_string":
        row["candidate"] = "tampered late text"
    elif fault == "int_to_bool":
        row["rank"][0] = False
    elif fault == "float_to_int":
        row["rank"][0] = 0
    elif fault == "other_float":
        row["rank"][1] = -8.0
    elif fault == "missing_row":
        producer["conception_log"][-1]["synopsis_selection"].pop()
    elif fault == "extra_field":
        row["added"] = "field"
    else:
        row["rank"][0] = 0.0
        stored["state"]["conception_log"][-1]["synopsis_selection"][-1]["rank"][0] = -0.0
    with pytest.raises(ValueError):
        derive(stored, encoded(producer))


@pytest.mark.parametrize("fault", ["title", "declared_hash", "input", "failure_copy"])
def test_whole_original_hash_and_input_must_match_before_any_derived_checkpoint(fault):
    _, stored, archive = legacy_case()
    if fault == "title":
        stored["state"]["title"] += "changed"
    elif fault == "declared_hash":
        stored["state_sha256"] = "0" * 64
    elif fault == "failure_copy":
        stored["state"]["last_failure"]["conception_log"][0]["synopsis_selection"][-1][
            "candidate"
        ] += "changed"
    with pytest.raises(ValueError):
        derive(stored, archive, input_hash="other" if fault == "input" else "isolated-input")


@pytest.mark.parametrize("unsafe", [("tuple",), float("nan"), float("inf")])
def test_unsafe_state_is_not_coerced_for_build_or_recovery(unsafe):
    state = _state()
    state["unsafe"] = unsafe
    with pytest.raises((TypeError, ValueError), match="JSON-safe"):
        cp.build_conception_checkpoint(state, input_hash="input")
    _, stored, archive = legacy_case()
    stored["state"]["unsafe"] = unsafe
    with pytest.raises((TypeError, ValueError), match="JSON-safe"):
        derive(stored, archive)


@pytest.mark.parametrize(
    "archive",
    [
        "{}",
        '{"conception_log":[],"conception_log":[]}',
        '{"conception_log":[NaN]}',
        '{"conception_log":[1e999]}',
    ],
)
def test_missing_ambiguous_or_nonfinite_archive_is_not_a_witness(archive):
    _, stored, _ = legacy_case()
    with pytest.raises(ValueError):
        derive(stored, archive)


@pytest.mark.parametrize("fault", ["paths", "source_hash", "derived_text", "extra"])
def test_receipt_tampering_cannot_pass_by_rehashing_only_the_derived_state(fault):
    _, stored, archive = legacy_case()
    proof = derive(stored, archive)
    if fault == "paths":
        proof["changed_paths"].pop()
    elif fault == "source_hash":
        proof["original_checkpoint_sha256"] = "f" * 64
    elif fault == "derived_text":
        proof["derived_checkpoint"]["state"]["title"] += "changed"
        proof["derived_checkpoint"]["state_sha256"] = cp._state_hash(
            proof["derived_checkpoint"]["state"]
        )
    else:
        proof["unauthorized_extra"] = True
    with pytest.raises(ValueError, match="receipt binding mismatch"):
        cp.validate_signed_zero_checkpoint_recovery(
            proof, checkpoint=stored, input_hash="isolated-input"
        )


@pytest.fixture
async def workflow_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'checkpoint_recovery_test.sqlite3'}"
    )
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            str(CreateTable(WorkflowRunModel.__table__).compile(dialect=conn.dialect))
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_real_sqlite_save_is_separate_idempotent_and_rollback_remains_caller_owned(
    workflow_db,
):
    _, stored, archive = legacy_case()
    proof = derive(stored, archive)
    wid = uuid4()
    metadata = {
        cp.CHECKPOINT_KEY: stored,
        cp.CHECKPOINT_INPUT_HASH_KEY: "isolated-input",
        "paid_calls": 141,
        "attempts": 3,
        "protected": {"receipt": "keep"},
    }
    async with workflow_db() as session:
        session.add(
            WorkflowRunModel(
                id=wid,
                workflow_type="conception_initial",
                status="failed",
                project_id=None,
                metadata_json=metadata,
            )
        )
        await session.commit()
    async with workflow_db() as session:
        with pytest.raises(ValueError, match="state_sha256 mismatch"):
            await cp.load_conception_checkpoint(
                session,
                workflow_run_id=wid,
                input_hash="isolated-input",
            )
        await cp.save_signed_zero_checkpoint_recovery(session, workflow_run_id=wid, recovery=proof)
        await session.rollback()
    async with workflow_db() as session:
        row = await session.get(WorkflowRunModel, wid)
        assert cp.SIGNED_ZERO_RECOVERY_KEY not in row.metadata_json
        assert encoded(row.metadata_json) == encoded(metadata)
        await cp.save_signed_zero_checkpoint_recovery(session, workflow_run_id=wid, recovery=proof)
        await session.commit()
    async with workflow_db() as session:
        row = await session.get(WorkflowRunModel, wid)
        before = encoded(row.metadata_json)
        assert row.status == "failed" and row.project_id is None
        assert encoded(
            {k: v for k, v in row.metadata_json.items() if k != cp.SIGNED_ZERO_RECOVERY_KEY}
        ) == encoded(metadata)
        saved = await cp.save_signed_zero_checkpoint_recovery(
            session, workflow_run_id=wid, recovery=proof
        )
        assert saved == proof and not session.dirty and encoded(row.metadata_json) == before
        assert (
            cp.select_conception_checkpoint_for_resume(
                row.metadata_json[cp.CHECKPOINT_KEY],
                input_hash="isolated-input",
                signed_zero_recovery=row.metadata_json[cp.SIGNED_ZERO_RECOVERY_KEY],
            )
            == proof["derived_checkpoint"]
        )
        assert (
            await cp.load_conception_checkpoint(
                session,
                workflow_run_id=wid,
                input_hash="isolated-input",
            )
            == proof["derived_checkpoint"]
        )


@pytest.mark.asyncio
async def test_normal_loader_uses_later_valid_checkpoint_without_removing_old_recovery(workflow_db):
    _, stored, archive = legacy_case()
    proof = derive(stored, archive)
    wid = uuid4()
    async with workflow_db() as session:
        row = WorkflowRunModel(
            id=wid,
            workflow_type="conception_initial",
            status="failed",
            project_id=None,
            metadata_json={cp.CHECKPOINT_KEY: stored, cp.SIGNED_ZERO_RECOVERY_KEY: proof},
        )
        session.add(row)
        await session.commit()
        state = deepcopy(proof["derived_checkpoint"]["state"])
        state["synopsis"] = "A later genuinely selected candidate."
        fresh = cp.build_conception_checkpoint(state, input_hash="isolated-input")
        await cp.save_conception_checkpoint(session, workflow_run_id=wid, checkpoint=fresh)
        await session.commit()
    async with workflow_db() as session:
        loaded = await cp.load_conception_checkpoint(
            session,
            workflow_run_id=wid,
            input_hash="isolated-input",
        )
        assert loaded == fresh
        row = await session.get(WorkflowRunModel, wid)
        assert row.metadata_json[cp.SIGNED_ZERO_RECOVERY_KEY] == proof
        corrupt = deepcopy(row.metadata_json)
        corrupt[cp.CHECKPOINT_KEY]["state"]["title"] += "unproven drift"
        row.metadata_json = corrupt
        await session.commit()
    async with workflow_db() as session:
        with pytest.raises(ValueError):
            await cp.load_conception_checkpoint(
                session,
                workflow_run_id=wid,
                input_hash="isolated-input",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["source_drift", "project_bound", "wrong_workflow", "input_drift"]
)
async def test_real_sqlite_persist_rechecks_locked_current_binding(workflow_db, fault):
    _, stored, archive = legacy_case()
    proof = derive(stored, archive)
    metadata = {cp.CHECKPOINT_KEY: deepcopy(stored), cp.CHECKPOINT_INPUT_HASH_KEY: "isolated-input"}
    if fault == "source_drift":
        metadata[cp.CHECKPOINT_KEY]["state"]["title"] += "drift"
    if fault == "input_drift":
        metadata[cp.CHECKPOINT_INPUT_HASH_KEY] = "different"
    wid = uuid4()
    async with workflow_db() as session:
        session.add(
            WorkflowRunModel(
                id=wid,
                workflow_type="conception_revision"
                if fault == "wrong_workflow"
                else "conception_initial",
                status="failed",
                project_id=uuid4() if fault == "project_bound" else None,
                metadata_json=metadata,
            )
        )
        await session.commit()
        with pytest.raises(ValueError):
            await cp.save_signed_zero_checkpoint_recovery(
                session, workflow_run_id=wid, recovery=proof
            )
        row = await session.get(WorkflowRunModel, wid)
        assert cp.SIGNED_ZERO_RECOVERY_KEY not in row.metadata_json


def test_local_archive_proves_original_or_rejects_appended_producer_without_mutation():
    root = Path(__file__).resolve().parents[2]
    evidence = (
        root
        / "reports/continuous-recovery-2026-10-16"
        / "20261017T123927305629Z-fresh-N-runtime-evidence.json"
    )
    archive = root / "output/conception-logs/3c4a386e-0e10-4aaf-a8c3-a671d48e2a01.json"
    if not evidence.exists() or not archive.exists():
        pytest.skip("local historical physical artifacts are not distributed as fixtures")
    original_bytes = (evidence.read_bytes(), archive.read_bytes())
    data = json.loads(original_bytes[0])
    checkpoints = [
        w["metadata_json"][cp.CHECKPOINT_KEY]
        for w in data["workflows"]
        if cp.CHECKPOINT_KEY in w.get("metadata_json", {})
    ]
    bad = [
        value for value in checkpoints if cp._state_hash(value["state"]) != value["state_sha256"]
    ]
    assert len(bad) == 1
    producer = json.loads(original_bytes[1])
    if len(producer["conception_log"]) != len(bad[0]["state"]["conception_log"]):
        # This physical archive is a live append-only application artifact,
        # not an immutable test fixture. A later log must stay ineligible.
        with pytest.raises(ValueError, match="producer log length changed"):
            cp.derive_signed_zero_checkpoint_recovery(
                bad[0], input_hash=bad[0]["input_hash"],
                retained_producer_json=original_bytes[1].decode(),
            )
        assert (evidence.read_bytes(), archive.read_bytes()) == original_bytes
        return
    receipt = cp.derive_signed_zero_checkpoint_recovery(
        bad[0],
        input_hash=bad[0]["input_hash"],
        retained_producer_json=original_bytes[1].decode(),
    )
    assert len(receipt["changed_paths"]) == 18
    assert receipt["reconstructed_state_sha256"] == (
        "ae967bfc9d0614339b7750dd5dd7bef49e823ad44b5c85dc1d60684e82a47fc7"
    )
    cp.validate_signed_zero_checkpoint_recovery(
        receipt, checkpoint=bad[0], input_hash=bad[0]["input_hash"]
    )
    assert (evidence.read_bytes(), archive.read_bytes()) == original_bytes
