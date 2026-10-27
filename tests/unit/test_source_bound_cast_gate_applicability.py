"""Whole-book roster templates cannot expand approved current-stage cast."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services import planner

pytestmark = pytest.mark.unit

GATES = [
    ("foundation_richness", "_repair_cast_foundation_if_needed",
     "bestseller.services.foundation_richness.scan_cast_foundation_richness"),
    ("antagonist_lifecycle", "_repair_cast_spec_antagonist_lifecycle_if_needed",
     "bestseller.services.antagonist_lifecycle.scan_antagonist_lifecycle"),
    ("relationship_scaling", "_repair_cast_spec_relationship_scaling_if_needed",
     "bestseller.services.relationship_scaling.scan_relationship_scaling"),
]


def _project(genre, language, *, locked=True):
    return SimpleNamespace(
        id=uuid4(), genre=genre, language=language, target_chapters=500,
        metadata_json={
            "book_design_snapshot_status": "locked" if locked else "draft",
            "book_design_snapshot": {
                "snapshot_id": "approved-snapshot", "source_hash": "a" * 64,
                "protagonist": {"name": "Approved lead"},
            },
            "source_bound_expansion_review": {
                "decision": "pass", "candidate_hash": "approved-cast",
            },
        },
    )


async def _run(helper, project, session, cast):
    return await getattr(planner, helper)(
        session=session, settings=SimpleNamespace(), project=project,
        book_spec_payload={"protagonist": {"name": "Approved lead"}},
        world_spec_payload={}, cast_spec_payload=cast,
        volume_count=10, workflow_run_id=uuid4(),
    )


@pytest.mark.parametrize("genre", ["fantasy", "science-fiction", "romance"])
@pytest.mark.parametrize("language", ["zh-CN", "en"])
@pytest.mark.parametrize("has_current_cast", [False, True])
async def test_source_bound_current_cast_skips_all_roster_templates(
    monkeypatch, genre, language, has_current_cast,
):
    project = _project(genre, language)
    project_before = copy.deepcopy(project.metadata_json)
    # Even derived lifecycle-shaped fields are not an approved future contract.
    cast = {
        "protagonist": {"name": "Approved lead", "gender": "unknown"},
        "antagonist": None, "supporting_cast": [],
        "antagonists": [{"name": "Current pressure", "resolution_type": "ongoing",
                         "stages_of_relevance": [[1, 10]]}],
        "_meta": {"source_bound_design": True},
    }
    if has_current_cast:
        cast["antagonist"] = {"name": "Approved opponent", "gender": "female"}
        cast["supporting_cast"] = [{"name": "Approved ally", "gender": "male"}]
    cast_before = copy.deepcopy(cast)
    workflow = SimpleNamespace(metadata_json={
        "checkpoint": "retained", "cast_gate_applicability": {"other_gate": {"status": "retained"}},
    })
    session = SimpleNamespace(scalar=AsyncMock(return_value=workflow))
    paid = AsyncMock(side_effect=AssertionError("Inapplicable gates cannot call a model"))
    prompts = Mock(side_effect=AssertionError("No future-roster prompt should be constructed"))
    monkeypatch.setattr(planner, "_generate_structured_artifact", paid)
    monkeypatch.setattr(planner, "_cast_spec_prompts", prompts)

    for gate, helper, scanner_path in GATES:
        scanner = Mock(side_effect=AssertionError("Applicability must be decided before scanning"))
        monkeypatch.setattr(scanner_path, scanner)
        result, run_id = await _run(helper, project, session, cast)
        assert result is cast and run_id is None
        scanner.assert_not_called()
        assert workflow.metadata_json["cast_gate_applicability"][gate] == {
            "status": "not_applicable",
            "reason": "source_bound_current_cast_without_full_lifecycle_contract",
            "source_hash": "a" * 64, "snapshot_id": "approved-snapshot",
        }

    paid.assert_not_awaited()
    prompts.assert_not_called()
    assert cast == cast_before
    assert project.metadata_json == project_before
    assert workflow.metadata_json["checkpoint"] == "retained"
    assert workflow.metadata_json["cast_gate_applicability"]["other_gate"] == {"status": "retained"}


@pytest.mark.parametrize("gate,helper,scanner_path", GATES)
@pytest.mark.parametrize("language", ["zh-CN", "en"])
async def test_ordinary_cast_still_scans_repairs_and_returns_candidate(
    monkeypatch, gate, helper, scanner_path, language,
):
    project = _project("fantasy", language, locked=False)
    project_before = copy.deepcopy(project.metadata_json)
    baseline = {"protagonist": {"name": "Approved lead"}, "supporting_cast": []}
    candidate = {**baseline, "supporting_cast": [{"name": "New supporting role"}]}
    report_fields = {
        "critical_count": 1, "warning_count": 0, "force_count": 1, "forces_required": 2,
        "coverage_ratio": 0.5, "antagonist_count": 1, "resolution_distribution": {},
        "supporting_cast_count": 1, "supporting_bounds": SimpleNamespace(floor=3, ceiling=8),
        "distinct_role_buckets": 1, "role_distribution": {}, "findings": [],
        "to_prompt_block": lambda **kwargs: "repair required",
    }
    critical = SimpleNamespace(**report_fields, is_critical=True)
    repaired = SimpleNamespace(**{**report_fields, "critical_count": 0}, is_critical=False)
    scanner = Mock(side_effect=[critical, repaired])
    monkeypatch.setattr(scanner_path, scanner)
    monkeypatch.setattr(planner, "_planner_language", lambda p: p.language)
    monkeypatch.setattr(planner, "_cast_spec_prompts", lambda *args: ("system", "user"))
    run_id = uuid4()
    paid = AsyncMock(return_value=(candidate, run_id))
    monkeypatch.setattr(planner, "_generate_structured_artifact", paid)
    workflow = SimpleNamespace(metadata_json={"checkpoint": "retained"})
    result, returned_id = await _run(
        helper, project, SimpleNamespace(scalar=AsyncMock(return_value=workflow)), baseline,
    )
    assert result is candidate and returned_id == run_id
    assert scanner.call_count == 2
    paid.assert_awaited_once()
    assert "cast_gate_applicability" not in workflow.metadata_json
    assert workflow.metadata_json["checkpoint"] == "retained"
    assert project.metadata_json == project_before


@pytest.mark.parametrize("_gate,helper,scanner_path", GATES)
async def test_source_bound_skip_still_applies_if_workflow_audit_unavailable(
    monkeypatch, _gate, helper, scanner_path,
):
    scanner = Mock()
    monkeypatch.setattr(scanner_path, scanner)
    paid = AsyncMock()
    monkeypatch.setattr(planner, "_generate_structured_artifact", paid)
    cast = {"protagonist": {"name": "Approved lead"}}
    result, run_id = await _run(
        helper, _project("romance", "en"),
        SimpleNamespace(scalar=AsyncMock(side_effect=RuntimeError("audit unavailable"))), cast,
    )
    assert result is cast and run_id is None
    scanner.assert_not_called()
    paid.assert_not_awaited()


async def test_applicability_audit_survives_checkpoint_and_independent_db_read(tmp_path):
    from bestseller.services.pipelines import _checkpoint_commit

    path = tmp_path / "cast_gate_applicability_test.sqlite3"
    assert "test" in path.name
    engine = create_engine(f"sqlite:///{path}")
    # Adapt only this test's DDL; leave the production ORM mapping untouched.
    table = WorkflowRunModel.__table__.to_metadata(MetaData())
    table.c.metadata.type = JSON()
    class WorkflowSession:
        # Real ORM reads/writes through an async facade, using stdlib SQLite.
        def __init__(self, session):
            self.session = session

        async def scalar(self, query):
            return self.session.scalar(query)

        async def commit(self):
            self.session.commit()

    project = _project("science-fiction", "en")
    project_before = copy.deepcopy(project.metadata_json)
    workflow_id = uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))
        with Session(engine, expire_on_commit=False) as session:
            session.add(WorkflowRunModel(
                id=workflow_id, workflow_type="foundation_plan", status="running",
                metadata_json={"checkpoint": "retained", "antagonist_lifecycle_critical": True},
            ))
            session.commit()
            facade = WorkflowSession(session)
            for gate, _helper, _scanner in GATES:
                assert await planner._skip_source_bound_current_cast_gate(
                    session=facade, project=project, workflow_run_id=workflow_id, gate=gate,
                )
            await _checkpoint_commit(facade)

        with Session(engine) as reader:
            saved = reader.get(WorkflowRunModel, workflow_id)
            assert saved.metadata_json["checkpoint"] == "retained"
            # Historical findings stay available; explicit applicability is separate.
            assert saved.metadata_json["antagonist_lifecycle_critical"] is True
            audits = saved.metadata_json["cast_gate_applicability"]
            assert set(audits) == {gate for gate, _helper, _scanner in GATES}
            assert all(audit["status"] == "not_applicable" for audit in audits.values())
            assert all(audit["source_hash"] == "a" * 64 for audit in audits.values())
            assert all(audit["snapshot_id"] == "approved-snapshot" for audit in audits.values())
        assert project.metadata_json == project_before
    finally:
        engine.dispose()
