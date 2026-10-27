"""Fake original rows and local synthetic paid files; no live DB/provider."""

import base64
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.engine import Engine
from support.semantic_input_history_fixture import history_select

from test_native_publication_title import synthetic_native_rows

from bestseller.infra.db import models as m
from bestseller.services import fanqie_short_semantic_inputs as inputs
from bestseller.services import fanqie_short_semantic_review as semantic
from bestseller.services import fanqie_short_title_evidence as loader

STAGE = (
    Path(__file__).resolve().parents[2]
    / "reports/project-reliability-review-2026-10-24/implementation/whole-semantic-input-factory-stage"
)
pytestmark = pytest.mark.unit


def row(data, ids=()):
    data = deepcopy(data)
    for key in ids:
        data[key] = UUID(data[key])
    return SimpleNamespace(**data)


class FakeSession:
    execute = history_select
    def __init__(self):
        self.rows, self.lists = {}, {}
        self.new, self.dirty, self.reads = [], [], []
        self.disabled = 0

    @property
    @contextmanager
    def no_autoflush(self):
        self.disabled += 1
        try:
            yield
        finally:
            self.disabled -= 1

    async def get(self, model, key, **kwargs):
        assert self.disabled and kwargs == {"populate_existing": True}
        self.reads.append((model.__name__, str(key)))
        return self.rows.get((model, str(key)))

    async def scalars(self, statement):
        assert self.disabled and statement.is_select
        model = statement.column_descriptions[0]["entity"]
        self.reads.append((model.__name__, "SELECT_ALL"))
        # Ensure reads retain the necessary owner/root filters rather than
        # silently filtering an incomplete synthetic inventory in Python.
        sql = str(statement)
        assert "WHERE" in sql and "ORDER BY" in sql
        if model is m.WorkflowStepRunModel:
            assert "workflow_run_id" in sql
        else:
            assert "operation_key" in sql or "result_json" in sql
        return self.lists[model]

    def __getattr__(self, name):
        raise AssertionError("read-only native loader forbids " + name)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("live DB/network/provider forbidden")

    monkeypatch.setattr(Engine, "raw_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    # Original static JSON is retained as history; current-code paid proof is
    # synthetic and rebuilt from the actual packaged runtime producer.
    e, context = synthetic_native_rows()
    producer = loader.producer
    audit = e["paid_return"]
    audit["completion"] = producer.safe_completion(audit["completion"])
    e["ledger"]["root"]["result_json"] = producer.safe_completion(
        e["ledger"]["root"]["result_json"]
    )
    for t in e["ledger"]["transports"]:
        t["result"] = producer.safe_completion(t["result"])
    audit["ledger"] = deepcopy(e["ledger"])
    audit["producer_extension"] = {"preserve_original_object": [1, 2]}
    audit["audit_sha256"] = loader._digest({k: v for k, v in audit.items() if k != "audit_sha256"})
    for L in (
        e["project"]["metadata_json"]["title_repair_lineage"],
        e["project"]["metadata_json"]["title_revision_status"]["native_title_lineage"],
        e["workflow"]["metadata_json"]["title_repair_lineage"],
        e["step"]["output_ref"],
    ):
        L["paid_audit_sha256"] = audit["audit_sha256"]
    e["project"]["metadata_json"]["title_revision_history"][-1]["audit_sha256"] = audit[
        "audit_sha256"
    ]
    e["llm_run"]["metadata_json"]["source_binding_sha256"] = audit["plan"]["identity"]
    session = FakeSession()
    project = row(e["project"], ("id",))
    native = json.loads(
        (STAGE.parent / "original-native-creative-approval-readback.json").read_bytes()
    )
    meta = {
        **deepcopy(native["selected_contracts"]),
        **{
            k: deepcopy(native["native_approval_evidence"][k])
            for k in inputs.APPROVAL_FIELDS
            if k in native["native_approval_evidence"]
        },
        **project.metadata_json,
        "execution_scope_id": audit["plan"]["binding"]["execution_scope_id"],
        "methodology_contract_mode": "strict",
        "unlock_line_ratio": 0.30,
    }
    project.metadata_json = meta
    for k, v in context["manifest"]["project"].items():
        setattr(project, k, v)
    project.status, project.updated_at = "writing", "volatile"
    workflow = row(e["workflow"], ("id", "project_id", "scope_id"))
    run = row(e["llm_run"], ("id", "project_id", "workflow_run_id"))
    step = row(e["step"], ("id", "workflow_run_id"))
    root = row(e["ledger"]["root"])
    for model, obj in (
        (m.ProjectModel, project),
        (m.WorkflowRunModel, workflow),
        (m.LlmRunModel, run),
    ):
        session.rows[model, str(obj.id)] = obj
    session.rows[m.ExecutionOperationModel, root.key] = root
    session.lists[m.WorkflowStepRunModel] = [step]
    session.lists[m.ExecutionAttemptModel] = [row(a) for a in e["ledger"]["attempts"]]
    transports = []
    for rec in e["ledger"]["transports"]:
        transports.append(
            SimpleNamespace(
                key=loader._sha(
                    ("transport-result.v1\n" + root.scope_id + "\n" + rec["attempt_id"]).encode()
                ),
                scope_id=root.scope_id,
                input_hash=root.input_hash,
                status="transport_result",
                result_json=deepcopy(rec),
            )
        )
    session.lists[m.ExecutionOperationModel] = transports
    for name in ("canonical_snapshot", "intent_snapshot"):
        obj = row(native["native_approval_evidence"][name], ("id", "project_id"))
        session.rows[m.PlanningArtifactVersionModel, str(obj.id)] = obj
    for saved in json.loads((STAGE / "saved-draft-fixtures.json").read_bytes())["rows"]:
        obj = row(
            {k: v for k, v in saved.items() if k != "fixture_provenance"},
            ("id", "project_id", "chapter_id"),
        )
        session.rows[m.ChapterDraftVersionModel, str(obj.id)] = obj
    directory = tmp_path.resolve() / "artifact-root"
    settings = SimpleNamespace(artifact_store=SimpleNamespace(mode="local", local_dir=directory))
    path = directory / loader.AUDIT_DIRECTORY / audit["plan"]["identity"] / "paid-return.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    for value in e["physical_files"].values():
        p = directory / value["ref"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(base64.b64decode(value["bytes_b64"]))
    return SimpleNamespace(
        session=session,
        settings=settings,
        project=project,
        workflow=workflow,
        run=run,
        step=step,
        root=root,
        audit=audit,
        audit_path=path,
        assembly=SimpleNamespace(manifest=context["manifest"], full_text=context["full_text"]),
    )


async def load(r):
    return await loader.load_native_title_evidence(r.session, r.settings, project=r.project)


def test_projection_contract_matches_producer_required_fields():
    assert tuple(k for k in inputs.TITLE_METADATA_FIELDS if k != "title_paid_selection_lineage") == loader.producer.OLD_FIELDS
    assert set(inputs.TITLE_METADATA_FIELDS) - set(loader.producer.OLD_FIELDS) == {"title_paid_selection_lineage"}
    assert inputs.TITLE_METADATA_FIELDS.count("title_paid_selection_lineage") == 1


@pytest.mark.asyncio
async def test_original_audit_native_rows_and_full_factory_v4_chain(rig):
    before = deepcopy(rig.audit)
    evidence = await load(rig)
    assert evidence["paid_return"] == before
    assert evidence["paid_return"]["producer_extension"] == {"preserve_original_object": [1, 2]}
    assert set(evidence["project"]) == {"id", "title", "metadata_json"}
    assert set(evidence["project"]["metadata_json"]) <= set(inputs.TITLE_METADATA_FIELDS)
    for kind, f in evidence["physical_files"].items():
        raw = (rig.settings.artifact_store.local_dir / f["ref"]).read_bytes()
        assert base64.b64decode(f["bytes_b64"]) == raw
        assert f["sha256"] == loader._sha(raw)
    args = await inputs.build_current_inputs(
        rig.session,
        project=rig.project,
        assembly=rig.assembly,
        workflow_run_id=uuid4(),
        native_title_evidence=evidence,
    )
    payload = semantic.prepare_review(**args)
    assert payload["title_provenance_route"] == "native_publishing_title_revision"
    assert payload["title_revision_status"]["blocked"] == "title_revision_call_failed"
    assert payload["title_provenance_verified"] is True
    assert rig.audit == before


@pytest.mark.asyncio
async def test_volatile_export_metadata_does_not_invalidate_evidence(rig):
    before = await load(rig)
    rig.project.metadata_json.update(fanqie_whole_export_receipt={"new": True}, status="completed")
    rig.project.status, rig.project.updated_at = "completed", "later"
    assert await load(rig) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "wf_unknown",
        "wf_owner",
        "step_missing",
        "extra_step",
        "run_owner",
        "run_role",
        "missing_identity",
        "bad_identity",
        "conflicting_operation_id",
        "unknown_root",
        "extra_attempt",
        "unknown_attempt",
        "extra_transport",
        "transport_key",
        "lineage_hash",
        "run_content",
        "dirty",
    ],
)
async def test_native_counterexamples_fail_without_filling_or_recovery(rig, fault):
    if fault == "wf_unknown":
        rig.workflow.status = "unknown"
    elif fault == "wf_owner":
        rig.workflow.project_id = uuid4()
    elif fault == "step_missing":
        rig.session.lists[m.WorkflowStepRunModel].clear()
    elif fault == "extra_step":
        rig.session.lists[m.WorkflowStepRunModel].append(deepcopy(rig.step))
    elif fault == "run_owner":
        rig.run.project_id = uuid4()
    elif fault == "run_role":
        rig.run.logical_role = "critic"
    elif fault == "missing_identity":
        rig.run.metadata_json.pop("source_binding_sha256")
    elif fault == "bad_identity":
        rig.run.metadata_json["source_binding_sha256"] = "../elsewhere"
    elif fault == "conflicting_operation_id":
        rig.run.metadata_json["operation_id"] = "native-title-repair.v1:" + "0" * 64
    elif fault == "unknown_root":
        rig.root.status = "unknown"
    elif fault == "extra_attempt":
        rig.session.lists[m.ExecutionAttemptModel].append(
            deepcopy(rig.session.lists[m.ExecutionAttemptModel][0])
        )
    elif fault == "unknown_attempt":
        rig.session.lists[m.ExecutionAttemptModel][0].status = "unknown"
    elif fault == "extra_transport":
        rig.session.lists[m.ExecutionOperationModel].append(
            deepcopy(rig.session.lists[m.ExecutionOperationModel][0])
        )
    elif fault == "transport_key":
        rig.session.lists[m.ExecutionOperationModel][0].key = "0" * 64
    elif fault == "lineage_hash":
        rig.project.metadata_json["title_repair_lineage"]["paid_audit_sha256"] = "0" * 64
    elif fault == "run_content":
        rig.run.output_tokens += 1
    elif fault == "dirty":
        rig.session.dirty.append(rig.project)
    with pytest.raises(loader.TitleEvidenceError):
        await load(rig)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "audit_utf16",
        "duplicate_json",
        "changed_audit",
        "audit_symlink",
        "parent_symlink",
        "physical_missing",
        "physical_utf16",
        "physical_symlink",
        "physical_duplicate",
        "physical_changed",
        "root_symlink",
        "ref_traversal",
    ],
)
async def test_fixed_audit_and_physical_custody(rig, tmp_path, fault):
    response = rig.settings.artifact_store.local_dir / rig.run.response_payload_ref
    if fault == "missing":
        rig.audit_path.unlink()
    elif fault == "audit_utf16":
        rig.audit_path.write_bytes(json.dumps(rig.audit).encode("utf-16"))
    elif fault == "duplicate_json":
        rig.audit_path.write_text('{"schema":"a","schema":"b"}')
    elif fault == "changed_audit":
        value = deepcopy(rig.audit)
        value["producer_extension"] = {"tampered": True}
        rig.audit_path.write_text(json.dumps(value))
    elif fault in {"audit_symlink", "physical_symlink"}:
        path = rig.audit_path if fault == "audit_symlink" else response
        copy = tmp_path / "outside.json"
        copy.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(copy)
    elif fault == "parent_symlink":
        parent = rig.audit_path.parent
        moved = parent.with_name("moved")
        parent.rename(moved)
        parent.symlink_to(moved, target_is_directory=True)
    elif fault == "physical_missing":
        response.unlink()
    elif fault == "physical_utf16":
        response.write_bytes(json.dumps(rig.audit["retained_payloads"]["response"]).encode("utf-16"))
    elif fault == "physical_duplicate":
        response.write_text('{"kind":"response","kind":"response"}')
    elif fault == "physical_changed":
        response.write_text("{}")
    elif fault == "root_symlink":
        alias = tmp_path / "root-alias"
        alias.symlink_to(rig.settings.artifact_store.local_dir, target_is_directory=True)
        rig.settings.artifact_store.local_dir = alias
    elif fault == "ref_traversal":
        rig.run.response_payload_ref = "../outside.json"
    with pytest.raises(loader.TitleEvidenceError):
        await load(rig)


@pytest.mark.asyncio
async def test_absent_lineage_returns_none_but_missing_identity_never_scans(rig, monkeypatch):
    rig.run.metadata_json.pop("source_binding_sha256")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("must not open/scan guessed paths")

    monkeypatch.setattr(loader, "_read", forbidden)
    with pytest.raises(loader.TitleEvidenceError, match="missing_original_request_identity"):
        await load(rig)
    rig.project.metadata_json.pop("title_repair_lineage")
    assert await load(rig) is None
