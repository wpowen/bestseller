"""Exact surgical working branches; no database or provider connections."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.services.outline_recovery_checkpoint import (
    checkpoint_outline_candidate,
    recovery_binding,
    restore_active_surgical_repair_source,
    select_active_surgical_repair_source,
)
from bestseller.services.prompt_source_policy import content_hash

pytestmark = pytest.mark.unit


def _body(payload):
    return {k: copy.deepcopy(v) for k, v in payload.items() if k != "_meta"}


def _metadata():
    return {
        "book_design_snapshot": {"source_hash": "a" * 64},
        "derived_input_revision": {"candidate_hash": "revision-1"},
        "rolling_outline_plan": {"window_start": 1, "window_end": 3},
    }


def _grade(payload, score, metadata):
    binding = recovery_binding(metadata, 1, [1, 3])
    return {
        "passed": False,
        "pass": False,
        "overall_score": score,
        "graded_outline_hash": content_hash(payload),
        "outline_repair_dependency_hash": binding["dependency_hash"],
        "outline_repair_source_snapshot_hash": binding["source_snapshot_hash"],
        "blocking_issues": [{"code": "knowledge_misuse", "evidence": "Current exact finding"}],
    }


def _root(metadata):
    payload = {
        "batch_name": "first-window",
        "chapters": [
            {
                "chapter_number": n,
                "volume_number": 1,
                "goal": f"Recover evidence {n}",
                "participants": ["owner"],
            }
            for n in range(1, 4)
        ],
        "_meta": {"producer": "retained-original"},
    }
    return payload, _grade(payload, 0.79, metadata)


def _child(parent, report, metadata, *, score=0.55, label="witness"):
    payload = _body(parent)
    payload["chapters"][0]["participants"].append(label)
    payload["_meta"] = {
        "surgical_repair_receipt": {
            "before_candidate": _body(parent),
            "after_candidate_hash": content_hash(payload),
            "source_report": copy.deepcopy(report),
            "llm_run_id": str(uuid4()),
        }
    }
    return payload, _grade(payload, score, metadata)


def _select(best, grade, child, verdict):
    return select_active_surgical_repair_source(
        best_payload=best,
        best_report=grade,
        candidate_payload=child,
        candidate_report=verdict,
    )


def _record(payload, report, metadata):
    return {
        "candidate": copy.deepcopy(payload),
        "report": copy.deepcopy(report),
        "candidate_hash": content_hash(payload),
        "binding": recovery_binding(metadata, 1, [1, 3]),
        "workflow_run_id": str(uuid4()),
    }


def _bucket(metadata):
    return metadata["outline_commercial_judge_checkpoints"][
        content_hash(recovery_binding(metadata, 1, [1, 3]))
    ]


def _restore(metadata, best, grade):
    return restore_active_surgical_repair_source(
        metadata=metadata,
        volume_number=1,
        best_payload=best,
        best_report=grade,
    )


def test_lower_score_exact_child_is_working_source_without_mutating_or_approving():
    metadata = _metadata()
    best, grade = _root(metadata)
    child, verdict = _child(best, grade, metadata)
    original = copy.deepcopy((best, grade, child, verdict))
    chosen, report = _select(best, grade, child, verdict)
    assert (chosen, report) == (child, verdict)
    assert report["overall_score"] == 0.55 and report["passed"] is False
    chosen["chapters"][0]["participants"].append("local-edit")
    report["blocking_issues"].clear()
    assert (best, grade, child, verdict) == original


@pytest.mark.parametrize(
    "corruption",
    [
        "before",
        "after",
        "run",
        "grade",
        "source_grade",
        "body_source_hash",
        "no_receipt",
        "chapter_window",
        "volume",
        "passed",
        "unknown",
    ],
)
def test_missing_or_conflicting_lineage_falls_back_to_exact_best(corruption):
    metadata = _metadata()
    best, grade = _root(metadata)
    child, verdict = _child(best, grade, metadata)
    receipt = child["_meta"]["surgical_repair_receipt"]
    if corruption == "before":
        receipt["before_candidate"]["chapters"][0]["goal"] = "foreign parent"
    elif corruption == "after":
        receipt["after_candidate_hash"] = "wrong"
    elif corruption == "run":
        receipt["llm_run_id"] = " "
    elif corruption == "source_grade":
        receipt["source_report"]["graded_outline_hash"] = "wrong"
        receipt["source_report"]["source_hash"] = content_hash(best)
    elif corruption == "body_source_hash":
        receipt["source_report"].pop("graded_outline_hash")
        receipt["source_report"]["source_hash"] = content_hash(_body(best))
    elif corruption == "no_receipt":
        child["_meta"].clear()
    elif corruption == "chapter_window":
        child["chapters"].pop()
    elif corruption == "volume":
        child["chapters"][0]["volume_number"] = 2
    elif corruption == "passed":
        verdict["passed"] = True
    elif corruption == "unknown":
        verdict.pop("passed")
        verdict.pop("pass")
    verdict["graded_outline_hash"] = content_hash(child) if corruption != "grade" else "wrong"
    assert _select(best, grade, child, verdict) == (best, grade)


def test_exact_historical_source_hash_supported_without_inventing_grade_hash():
    metadata = _metadata()
    best, grade = _root(metadata)
    child, verdict = _child(best, grade, metadata)
    source = child["_meta"]["surgical_repair_receipt"]["source_report"]
    source["source_hash"] = source.pop("graded_outline_hash")
    verdict["graded_outline_hash"] = content_hash(child)
    chosen, report = _select(best, grade, child, verdict)
    assert (chosen, report) == (child, verdict)
    assert "graded_outline_hash" not in chosen["_meta"]["surgical_repair_receipt"]["source_report"]


def test_legacy_latest_restores_only_matching_binding_without_marker():
    metadata = _metadata()
    best, grade = _root(metadata)
    child, verdict = _child(best, grade, metadata)
    key = content_hash(recovery_binding(metadata, 1, [1, 3]))
    metadata["outline_commercial_judge_checkpoints"] = {
        key: {
            "best": _record(best, grade, metadata),
            "latest_candidate": _record(child, verdict, metadata),
        }
    }
    original = copy.deepcopy(metadata)
    assert _restore(metadata, best, grade) == (child, verdict)
    assert metadata == original and "active_repair_branch" not in _bucket(metadata)
    metadata["derived_input_revision"]["candidate_hash"] = "different-revision"
    assert _restore(metadata, best, grade) == (best, grade)


async def _persist(session, project, payload, report):
    await checkpoint_outline_candidate(
        session,
        project=project,
        workflow_run_id=uuid4(),
        volume_number=1,
        candidate=payload,
        report=report,
    )


@pytest.fixture
def session():
    # The real persister runs; only its storage boundary is inert. No DB engine.
    return SimpleNamespace(
        scalar=AsyncMock(return_value=None), flush=AsyncMock(), commit=AsyncMock()
    )


async def test_persister_keeps_best_and_restores_multi_hop_active_after_pending_restart(session):
    metadata = _metadata()
    best, grade = _root(metadata)
    project = SimpleNamespace(id=uuid4(), metadata_json=metadata)
    await _persist(session, project, best, grade)
    original_best = copy.deepcopy(_bucket(project.metadata_json)["best"])
    first, first_grade = _child(best, grade, metadata)
    await _persist(session, project, first, first_grade)
    second, second_grade = _child(first, first_grade, metadata, score=0.70, label="second-witness")
    await _persist(session, project, second, None)
    assert _restore(project.metadata_json, best, grade) == (first, first_grade)
    await _persist(session, project, second, second_grade)
    # One-hop alone fails; producer-marked continuity survives serialization/restart.
    assert _select(best, grade, second, second_grade) == (best, grade)
    import json

    restored_metadata = json.loads(json.dumps(project.metadata_json))
    original_metadata = copy.deepcopy(restored_metadata)
    assert _restore(restored_metadata, best, grade) == (second, second_grade)
    bucket = _bucket(restored_metadata)
    assert bucket["best"] == original_best
    branch = bucket["active_repair_branch"]
    assert branch["root_best_candidate_hash"] == content_hash(best)
    assert branch["parent_candidate_hash"] == content_hash(first)
    assert branch["report"] == second_grade and branch["report"]["passed"] is False
    assert branch["lineage_depth"] == 2
    assert [e["candidate_hash"] for e in branch["lineage_edges"]] == [
        content_hash(first),
        content_hash(second),
    ]
    assert all(e["llm_run_id"] and e["workflow_run_id"] for e in branch["lineage_edges"])
    assert restored_metadata == original_metadata


async def test_legacy_one_hop_is_seeded_before_next_pending_child_overwrites_latest(session):
    metadata = _metadata()
    best, grade = _root(metadata)
    first, first_grade = _child(best, grade, metadata)
    key = content_hash(recovery_binding(metadata, 1, [1, 3]))
    metadata["outline_commercial_judge_checkpoints"] = {
        key: {
            "best": _record(best, grade, metadata),
            "latest_candidate": _record(first, first_grade, metadata),
        }
    }
    project = SimpleNamespace(id=uuid4(), metadata_json=metadata)
    second, second_grade = _child(first, first_grade, metadata, score=0.70)
    await _persist(session, project, second, None)
    assert _restore(project.metadata_json, best, grade) == (first, first_grade)
    await _persist(session, project, second, second_grade)
    assert _restore(project.metadata_json, best, grade) == (second, second_grade)


@pytest.mark.parametrize(
    "corruption",
    ["root", "producer", "parent", "source", "edge", "run", "binding", "report", "passed"],
)
async def test_corrupt_producer_marker_cannot_restore_multi_hop(corruption, session):
    metadata = _metadata()
    best, grade = _root(metadata)
    project = SimpleNamespace(id=uuid4(), metadata_json=metadata)
    await _persist(session, project, best, grade)
    first, first_grade = _child(best, grade, metadata)
    await _persist(session, project, first, first_grade)
    second, second_grade = _child(first, first_grade, metadata, score=0.70)
    await _persist(session, project, second, second_grade)
    branch = _bucket(project.metadata_json)["active_repair_branch"]
    if corruption == "root":
        branch["root_best_candidate_hash"] = "wrong"
    elif corruption == "producer":
        branch["producer"] = "manual-claim"
    elif corruption == "parent":
        branch["parent_candidate_hash"] = "wrong"
    elif corruption == "source":
        branch["lineage_edges"][-1]["source_report_hash"] = "wrong"
    elif corruption == "edge":
        branch["lineage_edges"][-1]["parent_candidate_hash"] = "wrong"
    elif corruption == "run":
        branch["lineage_edges"][-1]["llm_run_id"] = "wrong"
    elif corruption == "binding":
        branch["binding"]["revision_key"] = "wrong"
    elif corruption == "report":
        branch["report"]["graded_outline_hash"] = "wrong"
    elif corruption == "passed":
        branch["report"]["passed"] = True
    assert _restore(project.metadata_json, best, grade) == (best, grade)


async def test_unrelated_candidate_does_not_replace_existing_active_branch(session):
    metadata = _metadata()
    best, grade = _root(metadata)
    project = SimpleNamespace(id=uuid4(), metadata_json=metadata)
    await _persist(session, project, best, grade)
    first, first_grade = _child(best, grade, metadata)
    await _persist(session, project, first, first_grade)
    unrelated, unrelated_grade = _child(best, grade, metadata, score=0.60, label="different-branch")
    original = copy.deepcopy(_bucket(project.metadata_json)["active_repair_branch"])
    await _persist(session, project, unrelated, unrelated_grade)
    assert _bucket(project.metadata_json)["active_repair_branch"] == original
    assert _restore(project.metadata_json, best, grade) == (first, first_grade)


async def test_lineage_receipts_are_bounded_while_working_source_advances(session):
    metadata = _metadata()
    best, grade = _root(metadata)
    project = SimpleNamespace(id=uuid4(), metadata_json=metadata)
    await _persist(session, project, best, grade)
    current, report = best, grade
    for n in range(11):
        current, report = _child(current, report, metadata, score=0.6, label=f"witness-{n}")
        await _persist(session, project, current, report)
    branch = _bucket(project.metadata_json)["active_repair_branch"]
    assert branch["lineage_depth"] == 11 and len(branch["lineage_edges"]) == 8
    assert _restore(project.metadata_json, best, grade) == (current, report)
    assert _bucket(project.metadata_json)["best"]["candidate"] == best


async def test_new_score_best_invalidates_old_root_and_can_start_its_own_branch(session):
    metadata = _metadata()
    best, grade = _root(metadata)
    project = SimpleNamespace(id=uuid4(), metadata_json=metadata)
    await _persist(session, project, best, grade)
    first, first_grade = _child(best, grade, metadata)
    await _persist(session, project, first, first_grade)
    improved, improved_grade = _child(first, first_grade, metadata, score=0.85)
    await _persist(session, project, improved, improved_grade)
    bucket = _bucket(project.metadata_json)
    assert bucket["best"]["candidate"] == improved
    assert "active_repair_branch" not in bucket
    assert _restore(project.metadata_json, improved, improved_grade) == (improved, improved_grade)
    next_child, next_grade = _child(improved, improved_grade, metadata, score=0.72)
    await _persist(session, project, next_child, next_grade)
    branch = _bucket(project.metadata_json)["active_repair_branch"]
    assert branch["root_best_candidate_hash"] == content_hash(improved)
    assert branch["lineage_depth"] == 1
    active, report = _restore(project.metadata_json, improved, improved_grade)
    assert (active, report) == (next_child, next_grade)
    active["chapters"].clear()
    report["blocking_issues"].clear()
    assert branch["candidate"] == next_child and branch["report"] == next_grade
