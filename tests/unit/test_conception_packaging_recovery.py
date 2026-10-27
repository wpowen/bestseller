"""Retained text is a proposal, never an inherited grade or paid source proof."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from bestseller.services.conception_packaging_recovery import (
    collect_retained_packaging_proposals,
    packaging_recovery_source_identity,
)

pytestmark = pytest.mark.unit


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def state():
    return {
        "premise": "海港检修员发现灯塔账目与真实潮汐不符。",
        "writing_profile": {"character": {"occupation": "检修员"}, "world": {"tide": "daily"}},
        "story_spine": {"phases": ["发现误报", "追查伪账"]},
        "world_model_payload": {"port": "未命名海港"},
        "concept_contract": {"story_architecture": {"choice": "保全证据"}},
        "ctx": {"creation_intent_contract": {"field_sources": {"genre_intent": "explicit"}},
                "_user_intent_snapshot": "行业悬疑,不能超自然解决。"},
        "explicit_concept_seed": "",
        "genre_key": "urban",
        "genre_intent_contract": {"genre_key": "urban", "source": "user"},
        "_contract_body_fingerprint": "a" * 64,
        "repair_quotas": {"final_packaging": 3},
        "llm_run_ids": ["retained-unattributed-run"],
        "conception_log": [{
            "agent": "final_packaging_promotion_repair", "attempt": 2,
            "title_candidates": ["潮汐账", "灯塔检修日志"],
            "synopsis_candidates": ["  他看见假账。\n" + "完整后半段仍须保留。" * 80,
                                    "另一份完整提案。"],
            "candidate_title": "旧标题", "candidate_synopsis": "旧截取前缀。",
            "synopsis_selection": [{"candidate": "错误的截取前缀。", "selected": True}],
            "candidate_accepted": True,
            "report": {"passed": True, "grade": 0.99},
        }],
    }


def test_legacy_complete_proposals_are_explorable_but_never_generation_source_or_grade_proof():
    source = state()
    before = copy.deepcopy(source)
    pool, receipt = collect_retained_packaging_proposals(source)
    assert len(pool) == 1
    assert receipt["status"] == "ready_for_normal_audit"
    retained = receipt["retained_proposals"][0]
    assert retained["synopsis_candidates"] == source["conception_log"][0]["synopsis_candidates"]
    assert retained["status"] == "requires_normal_audit"
    assert retained["old_canonical_compatibility"] == "unknown"
    assert receipt["proposal_only"] is True
    assert receipt["fresh_full_audit_required"] is True
    assert receipt["grade_reused"] is False
    assert retained["source_checkpoint_state_sha256"] == digest(source)
    assert retained["log_entry_sha256"] == digest(source["conception_log"][0])
    assert retained["content_sha256"] == digest({
        "title_candidates": retained["title_candidates"],
        "synopsis_candidates": retained["synopsis_candidates"],
    })
    assert source == before
    assert "llm_run_id" not in json.dumps(receipt)
    json.dumps(receipt, allow_nan=False)


def test_complete_families_keep_bytes_and_order_without_prefix_or_cross_pairing():
    source = state()
    pool, receipt = collect_retained_packaging_proposals(source)
    assert receipt["status"] == "ready_for_normal_audit"
    assert len(pool) == 1
    assert pool[0]["synopsis_candidates"] == source["conception_log"][0]["synopsis_candidates"]
    assert pool[0]["title_candidates"] == ["潮汐账", "灯塔检修日志"]
    assert "report" not in pool[0] and "candidate_accepted" not in pool[0]
    assert pool[0]["status"] == "requires_normal_audit"
    assert receipt["limitations"]["normal_full_source_audit_required"] is True
    assert receipt["limitations"]["paid_row_linkage_proved"] is False
    assert receipt["limitations"]["new_credit"] is False
    pool[0]["synopsis_candidates"].append("仅修改返回值")
    assert len(source["conception_log"][0]["synopsis_candidates"]) == 2
    assert len(receipt["retained_proposals"][0]["synopsis_candidates"]) == 2


@pytest.mark.parametrize("field", ["premise", "writing_profile", "story_spine",
                                  "world_model_payload", "concept_contract", "ctx",
                                  "genre_intent_contract", "explicit_concept_seed"])
def test_changed_source_binds_new_exploration_but_old_compatibility_stays_unknown(field):
    source = state()
    old_identity = packaging_recovery_source_identity(source)
    if field == "concept_contract":
        source[field]["story_architecture"]["authored_addition"] = "必须保留的新要求"
    elif isinstance(source[field], dict):
        source[field]["authored_addition"] = "必须保留的新要求"
    else:
        source[field] += "新要求"
    pool, receipt = collect_retained_packaging_proposals(source)
    assert len(pool) == 1
    assert receipt["source_identity"] != old_identity
    assert receipt["old_canonical_compatibility"] == "unknown"
    assert receipt["fresh_full_audit_required"] is True


def test_log_change_changes_receipt_without_authorizing_new_credit():
    source = state()
    _, original = collect_retained_packaging_proposals(source)
    source["conception_log"][0]["synopsis_candidates"][0] = "被替换的提案"
    _, changed = collect_retained_packaging_proposals(source)
    assert original["source_checkpoint_state_sha256"] != changed["source_checkpoint_state_sha256"]
    assert (original["retained_proposals"][0]["content_sha256"]
            != changed["retained_proposals"][0]["content_sha256"])
    assert changed["limitations"]["new_credit"] is False


def test_collection_does_not_use_or_reset_attempt_allowance():
    source = state()
    source["repair_quotas"]["final_packaging"] = 4
    before = copy.deepcopy(source)
    pool, receipt = collect_retained_packaging_proposals(source)
    assert len(pool) == 1
    assert source == before
    assert receipt["limitations"]["quota_reset"] is False


@pytest.mark.parametrize(
    "malformation", ["no_full_text", "string_instead_of_list", "blank", "empty"],
)
def test_no_selected_diagnostic_fallback_and_no_manufactured_attempt(malformation):
    source = state()
    row = source["conception_log"][0]
    if malformation == "no_full_text":
        del row["title_candidates"]
        del row["synopsis_candidates"]
    elif malformation == "string_instead_of_list":
        row["synopsis_candidates"] = "不是完整候选数组"
    elif malformation == "blank":
        row["synopsis_candidates"] = [" "]
    else:
        row["title_candidates"] = []
        row["synopsis_candidates"] = []
    pool, receipt = collect_retained_packaging_proposals(source)
    assert pool == []
    assert receipt["status"] == "unsupported"
    assert receipt["retained_proposals"][0]["status"] == "invalid_proposal_family"


def test_only_repair_producer_full_arrays_are_considered():
    source = state()
    source["conception_log"][0]["agent"] = "unrelated_writer"
    pool, receipt = collect_retained_packaging_proposals(source)
    assert pool == [] and receipt["status"] == "no_retained_proposals"


def test_idempotent_output_preserves_history_counters_and_all_duplicate_origins():
    source = state()
    source["conception_log"].append(copy.deepcopy(source["conception_log"][0]))
    source["conception_log"][1]["attempt"] = 3
    before = copy.deepcopy(source)
    first = collect_retained_packaging_proposals(source)
    assert first == collect_retained_packaging_proposals(source)
    assert len(first[0]) == 2
    assert [row["log_index"] for row in first[0]] == [0, 1]
    assert source == before


@pytest.mark.parametrize("bad", [float("inf"), ("tuple",), object()])
def test_input_wire_remains_strict_json(bad):
    source = state()
    source["unrelated_metadata"] = bad
    with pytest.raises(ValueError, match="input_not_strict_json"):
        collect_retained_packaging_proposals(source)
