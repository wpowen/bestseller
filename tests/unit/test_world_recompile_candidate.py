"""Recompilation is a frozen diagnostic candidate, never world approval."""
# ruff: noqa: RUF001

from copy import deepcopy
from types import SimpleNamespace

import pytest

from bestseller.services.world_recompile_candidate import (
    build_world_recompile_candidate,
    verify_world_recompile_candidate,
)

pytestmark = pytest.mark.unit


def project():
    return SimpleNamespace(
        id="project",
        title="山城符铺",
        slug="book",
        genre="玄幻",
        sub_genre=None,
        language="zh-CN",
        audience="男频",
        target_chapters=50,
        metadata_json={
            "book_design_snapshot_status": "locked",
            "book_design_snapshot_hash": "current",
            "book_design_snapshot": {
                "snapshot_id": "current-id",
                "source_hash": "current",
                "schema_version": "book-design.v1",
                "protagonist": {"name": "纪年"},
                "reader_promise": "纪年开符铺",
                "core_story_engine": "卖符换来新生意",
            },
            "story_enhancers": {"cost_style": "minimal"},
            "writing_profile": {
                "character": {"power_tiers": ["初阶", "高阶"]},
                "world": {"power_system_style": "越阶后手抖；不会退阶。"},
            },
            "api_key": "must-not-be-copied",
        },
    )


def source():
    return SimpleNamespace(
        id="old-world",
        version_no=6,
        status="approved",
        schema_version="1.0",
        content={
            "_meta": {"source_snapshot_hash": "old"},
            "rules": [],
        },
    )


def build(p=None):
    return build_world_recompile_candidate(
        p or project(),
        premise="纪年在山城开符铺。",
        source_artifact=source(),
        premise_artifact_id="premise-v2",
    )


def test_candidate_freezes_actual_inputs_without_mutating_or_approving():
    p = project()
    before = deepcopy(vars(p))
    result = build(p)
    assert vars(p) == before
    assert result["promotion_allowed"] is False
    assert result["production_ready"] is False
    assert result["new_llm_calls"] == 0
    assert result["status"] == "needs_review"
    assert result["source_snapshot_matches_current"] is False
    assert "must-not-be-copied" not in str(result)
    assert result["frozen_inputs"]["metadata"]["writing_profile"]["world"]["power_system_style"] == "越阶后手抖；不会退阶。"
    assert result["candidate_world"]["power_system"] == {}
    assert result["candidate_world"]["_meta"]["source_gaps"] == ["full_canonical_source_unavailable"]
    verify_world_recompile_candidate(result)
    p.metadata_json["writing_profile"]["world"]["power_system_style"] = "changed"
    assert (
        result["frozen_inputs"]["metadata"]["writing_profile"]["world"]["power_system_style"]
        != "changed"
    )
    assert build(p)["input_hash"] != result["input_hash"]


@pytest.mark.parametrize("field", ["frozen_inputs", "candidate_world", "source_artifact"])
def test_receipt_rejects_tampering(field):
    result = build()
    result[field]["tampered"] = True
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        verify_world_recompile_candidate(result)


def test_unapproved_old_world_cannot_be_frozen_as_approved_source():
    old = source()
    old.status = "draft"
    with pytest.raises(ValueError, match="RECOMPILE_APPROVED_WORLD_REQUIRED"):
        build_world_recompile_candidate(
            project(), premise="原文", source_artifact=old, premise_artifact_id="premise"
        )


@pytest.mark.parametrize("mutation", ["missing", "unlocked", "mismatch"])
def test_candidate_requires_existing_locked_snapshot(mutation):
    p = project()
    if mutation == "missing":
        p.metadata_json.pop("book_design_snapshot")
    elif mutation == "unlocked":
        p.metadata_json["book_design_snapshot_status"] = "draft"
    else:
        p.metadata_json["book_design_snapshot_hash"] = "other"
    with pytest.raises(ValueError, match="RECOMPILE_SNAPSHOT"):
        build(p)
