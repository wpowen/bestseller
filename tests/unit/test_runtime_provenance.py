from __future__ import annotations

import json

import pytest

import bestseller.services.runtime_provenance as provenance
from bestseller.services.runtime_provenance import (
    RUNTIME_PROVENANCE_SCHEMA,
    disk_runtime_provenance,
    process_runtime_provenance,
    process_runtime_reference,
    task_runtime_provenance,
)
from bestseller.web.server import WebTaskState

pytestmark = pytest.mark.unit


def test_process_and_disk_provenance_are_distinct_snapshots() -> None:
    process = process_runtime_provenance()
    disk = disk_runtime_provenance()

    assert process["schema_version"] == RUNTIME_PROVENANCE_SCHEMA
    assert process["source"] == "process_start"
    assert disk["source"] == "disk_current"
    assert process["process_started_at"] is None
    assert process["provenance_initialized_at"]
    assert "unavailable" in process["process_started_at_basis"]
    assert process["pid"] == disk["pid"]
    assert "key_file_sha256" in process
    assert set(process["key_file_sha256"]) == set(disk["key_file_sha256"])
    assert {
        "src/bestseller/domain/book_design_snapshot.py",
        "src/bestseller/services/genre_intent_contract.py",
        "src/bestseller/services/narrative_contracts.py",
        "src/bestseller/services/derived_revision_projection.py",
        "src/bestseller/services/llm.py",
        "src/bestseller/services/commercial_slot_quarantine.py",
        "src/bestseller/services/commercial_action_projection.py",
        "src/bestseller/services/waiting_chapter_review_resume.py",
        "src/bestseller/services/chapter_failed_review_recovery.py",
        "src/bestseller/services/chapter_review_recovery_lineage.py",
        "src/bestseller/services/chapter_review_preflight.py",
        "src/bestseller/services/chapter_plan_observation_recovery.py",
        "src/bestseller/services/chapter_pending_review_policy_recovery.py",
        "src/bestseller/services/legacy_book_state_bootstrap.py",
        "src/bestseller/services/book_closure.py",
        "src/bestseller/services/book_delivery_readiness.py",
        "src/bestseller/services/chapter_continuity_admission.py",
        "src/bestseller/services/draft_promotion.py",
        "src/bestseller/services/repair.py",
        "src/bestseller/services/story_engine_review.py",
        "src/bestseller/services/historical_binding_unknown_recovery.py",
        "src/bestseller/services/execution_phase_handoff.py",
        "src/bestseller/services/conception_continuation.py",
        "src/bestseller/services/chapter_llm_quality_judge.py",
        "src/bestseller/services/conception_approval_bridge.py",
        "src/bestseller/services/projects.py",
        "src/bestseller/services/progress_context.py",
        "config/story_appeal.yaml",
        "src/bestseller/services/blurb_appeal_gate.py",
        "src/bestseller/services/source_effect_contract.py",
        "src/bestseller/services/intent_alignment.py",
        "src/bestseller/web/novel_quickstart.html",
        "src/bestseller/services/adopted_chapter_source.py",
        "src/bestseller/services/adopted_knowledge_resume.py",
        "src/bestseller/services/chapter_knowledge_state.py",
        "src/bestseller/services/conception_checkpoints.py",
        "src/bestseller/services/continuity.py",
        "src/bestseller/services/execution_windows.py",
        "src/bestseller/services/fanqie_short_pipeline.py",
        "src/bestseller/services/snapshot_dependency_recovery.py",
        "src/bestseller/services/story_bible.py",
        "src/bestseller/services/story_bible_projection.py",
        "src/bestseller/infra/db/models.py",
        "migrations/versions/0044_character_state_binding.py",
        "src/bestseller/services/character_snapshot_sources.py",
        "src/bestseller/services/feedback.py",
        "src/bestseller/services/knowledge.py",
        "src/bestseller/services/death_ripple.py",
        "src/bestseller/services/context.py",
        "src/bestseller/services/character_evolution.py",
        "src/bestseller/services/planning_context.py",
        "src/bestseller/services/consistency.py",
        "src/bestseller/services/bible_gate.py",
        "src/bestseller/services/contradiction.py",
        "src/bestseller/services/volume_audit.py",
        "src/bestseller/services/chapter_generation_input_builder.py",
        "src/bestseller/services/category_hard_engines.py",
        "src/bestseller/services/premium_book_gate.py",
        "src/bestseller/services/material_density.py",
        "src/bestseller/services/pipeline_flow_overview.py",
        "src/bestseller/services/pipeline_flow_schema.py",
        "src/bestseller/web/novel_pipeline_flow.html",
    } <= set(process["key_file_sha256"])
    assert process["loaded_module_evidence"]
    assert disk["loaded_module_evidence"] == {}
    assert "database" not in json.dumps(process, ensure_ascii=False).lower()


def test_task_provenance_binds_task_start_to_process_snapshot() -> None:
    receipt = task_runtime_provenance("task-123", "2026-09-06T01:02:03+00:00")

    assert receipt["schema_version"] == RUNTIME_PROVENANCE_SCHEMA
    assert receipt["task_id"] == "task-123"
    assert receipt["task_started_at"] == "2026-09-06T01:02:03+00:00"
    assert receipt["process"]["source"] == "process_start"


def test_process_runtime_reference_is_compact_and_joinable() -> None:
    reference = process_runtime_reference()

    assert len(reference["process_fingerprint"]) == 64
    assert reference["process_pid"] > 0
    assert reference["source_snapshot_basis"] == "process_import_snapshot"
    assert "loaded_module_evidence" not in reference


def test_web_task_state_persists_runtime_provenance() -> None:
    receipt = task_runtime_provenance("task-456", "2026-09-06T01:02:03+00:00")
    state = WebTaskState(
        task_id="task-456",
        task_type="autowrite",
        status="queued",
        created_at="2026-09-06T01:02:03+00:00",
        updated_at="2026-09-06T01:02:03+00:00",
        runtime_provenance=receipt,
    )

    persisted = state.to_dict()
    assert persisted["runtime_provenance"] == receipt


def test_process_snapshot_is_deep_copied() -> None:
    first = process_runtime_provenance()
    first["git"]["commit"] = "tampered"
    first["key_file_sha256"]["config/default.yaml"] = "tampered"

    second = process_runtime_provenance()
    assert second["git"]["commit"] != "tampered"
    assert second["key_file_sha256"]["config/default.yaml"] != "tampered"


def test_unavailable_git_is_unknown_not_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provenance, "_git_command", lambda *_args: (False, ""))

    snapshot = provenance._git_snapshot()

    assert snapshot == {
        "available": False,
        "commit": None,
        "dirty": None,
        "dirty_status_hash": None,
        "dirty_patch_hash": None,
        "untracked_file_sha256": None,
    }
