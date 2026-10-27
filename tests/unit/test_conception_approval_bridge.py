from __future__ import annotations

import pytest

from bestseller.services.concept_contract import build_concept_contract
from bestseller.services.conception_approval_bridge import (
    assert_conception_approved,
    build_conception_options,
    build_planning_handoff_payload,
    canonical_snapshot_content_hash,
    canonical_snapshot_premise,
    validate_canonical_approval_request,
)


def _snapshot():
    contract = build_concept_contract(
        winner={
            "concept": "电话谜案",
            "mechanism": "接到未来电话",
            "hook_question": "谁会死",
            "protagonist_identity": "接线员",
            "protagonist_private_desire": "查明真相",
            "opening_crisis": "死者明日来电",
            "opponent_system": "时间限制",
            "decision_proof": "公开追查",
            "emotional_promise": "逆转命运",
            "repeatable_story_unit": "追查未来来电",
            "unit_families": ["案件"],
            "unit_frequency": "每卷",
            "unit_count_estimate": 10,
            "renewal_sources": ["线索"],
            "accumulation_tracks": ["证据"],
            "phase_transitions": ["升级"],
            "opposing_ecology": ["幕后者"],
            "question_ladder": ["谁来电"],
            "endgame_direction": "阻止死亡",
            "core_abnormality": "未来电话",
            "opening_event": "接到电话",
        },
        story_spine={
            "who": "接线员",
            "wants": "查真相",
            "why_now": "接到电话",
            "against": "时间",
            "question": "谁会死",
        },
        target_chapters=12,
        genre="玄幻",
        sub_genre="悬疑",
    )
    return {
        "id": "snap-1",
        "status": "pending_user_approval",
        "version_no": 1,
        "source_run_id": "run-1",
        "content": {
            "attempt_id": "task:initial",
            "premise": "接线员追查明日来电",
            "authoritative_seed": "接线员接到死者明天才会拨出的电话",
            "story_spine": {"reader_promise": "阻止尚未发生的死亡"},
            "concept_contract": contract,
        },
    }


def test_options_are_snapshot_bound_and_rejected_candidates_are_excluded():
    log = {
        "conception_log": [
            {
                "agent": "concept_tournament",
                "attempt": 2,
                "candidates": [
                    {
                        "concept_contract": {"hook_card": {"one_liner": "坏"}},
                        "rejected_reason": "seed_fidelity",
                    },
                    {
                        "concept_contract": {"hook_card": {"one_liner": "不完整"}},
                        "rejected_reason": None,
                    },
                ],
            }
        ]
    }
    result = build_conception_options(
        project={"slug": "book", "lock_version": 4, "target_chapters": 12},
        snapshot=_snapshot(),
        conception_log=log,
    )
    assert result["canonical"]["source"]["snapshot_id"] == "snap-1"
    assert result["alternatives"] == []
    assert "/api/hook-candidates" not in str(result)


def test_option_hash_and_id_are_required_for_approval():
    result = build_conception_options(
        project={"slug": "book", "lock_version": 4, "target_chapters": 12}, snapshot=_snapshot()
    )
    canonical = result["canonical"]
    validate_canonical_approval_request(
        options=result,
        snapshot_id="snap-1",
        source_hash=canonical["source"]["snapshot_hash"],
        option_id=canonical["option_id"],
    )
    with pytest.raises(ValueError, match="stale"):
        validate_canonical_approval_request(
            options=result,
            snapshot_id="snap-1",
            source_hash="wrong",
            option_id=canonical["option_id"],
        )


def test_authoritative_seed_never_falls_back_to_generated_premise():
    result = build_conception_options(
        project={"slug": "book", "lock_version": 4, "target_chapters": 12},
        snapshot=_snapshot(),
    )
    assert result["authoritative_seed"] == {
        "text": "",
        "hash": None,
        "available": False,
        "source": "unavailable",
    }
    result = build_conception_options(
        project={"slug": "book", "lock_version": 4, "target_chapters": 12},
        snapshot=_snapshot(),
        authoritative_seed="原始用户种子",
    )
    assert result["authoritative_seed"]["text"] == "原始用户种子"
    assert result["authoritative_seed"]["source"] == "creation_intent"


def test_non_v2_or_missing_target_contract_never_passes():
    snapshot = _snapshot()
    snapshot["content"]["concept_contract"] = {
        **snapshot["content"]["concept_contract"],
        "schema_version": "legacy",
    }
    with pytest.raises(ValueError, match="complete concept contract"):
        build_conception_options(
            project={"slug": "book", "lock_version": 4, "target_chapters": 12},
            snapshot=snapshot,
        )
    snapshot = _snapshot()
    snapshot["content"]["concept_contract"] = {
        key: value
        for key, value in snapshot["content"]["concept_contract"].items()
        if key != "target_chapters"
    }
    with pytest.raises(ValueError, match="complete concept contract"):
        build_conception_options(project={"slug": "book", "lock_version": 4}, snapshot=snapshot)


def test_unapproved_conception_is_blocked_but_approved_project_is_allowed():
    with pytest.raises(PermissionError, match="approval is required"):
        assert_conception_approved(
            type("Project", (), {"metadata_json": {"conception_only": True}})()
        )
    assert_conception_approved(
        type(
            "Project",
            (),
            {"metadata_json": {"conception_only": True, "conception_approved": True}},
        )()
    )


def test_canonical_premise_requires_canonical_complete_snapshot():
    snapshot = _snapshot()
    snapshot["status"] = "canonical"
    assert canonical_snapshot_premise(snapshot) == "接线员追查明日来电"
    snapshot["status"] = "pending_user_approval"
    with pytest.raises(ValueError, match="not canonical"):
        canonical_snapshot_premise(snapshot)


def test_planning_handoff_rebases_task_on_approved_snapshot_without_prewrite_bypass():
    snapshot = _snapshot()
    snapshot["status"] = "canonical"
    project = type(
        "Project",
        (),
        {
            "metadata_json": {
                "conception_approved": True,
                "planning_status": "conception_approved",
                "creation_canonical_snapshot_id": "snap-1",
                "creation_canonical_snapshot_hash": canonical_snapshot_content_hash(snapshot),
                "concept_contract": {"one_liner": "污染旧值"},
                "story_spine": {"reader_promise": "污染旧值"},
            }
        },
    )()
    payload = build_planning_handoff_payload(
        {
            "premise": "stale form premise",
            "_run_conception": True,
            "prewrite_review_approved": True,
        },
        project=project,
        canonical_snapshot=snapshot,
    )
    assert payload["premise"] == "接线员追查明日来电"
    assert payload["metadata"]["concept_contract"] == snapshot["content"]["concept_contract"]
    assert (
        payload["metadata"]["story_spine"] == snapshot["content"]["concept_contract"]["story_spine"]
    )
    assert payload["_run_conception"] is False
    assert payload["planning_handoff"] is True
    assert "prewrite_review_approved" not in payload
