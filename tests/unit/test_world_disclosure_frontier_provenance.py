"""Offline provenance regressions: future frontier goals cannot become history."""

from __future__ import annotations

from copy import deepcopy

import pytest

from bestseller.services.planner import _merge_volume_world_disclosure_into_world_spec as merge
from bestseller.services.world_disclosure_migration import (
    apply_world_disclosure_repair,
    audit_world_disclosure_history,
    content_hash,
    propose_world_disclosure_repair,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    import socket

    from bestseller.services import llm, planner

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline provenance checks must not call DB/models/network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(planner, "complete_text", forbidden)


def row(identifier, kind, version, content, at, **extra):
    return {
        "id": identifier,
        "project_id": "fixture-project",
        "artifact_type": kind,
        "scope_ref_id": None,
        "version_no": version,
        "status": "approved",
        "created_at": f"2026-01-01T00:00:{at:02d}+00:00",
        "content": deepcopy(content),
        **extra,
    }


def event(summary, volume=2):
    import re

    return {
        "event": summary,
        "relevance": f"Volume {volume} frontier update"
        if re.search(r"[A-Za-z]", summary)
        else f"第{volume}卷世界边界更新",
    }


def snapshot(summary="Reach the outer relay"):
    past = {"event": "The old relay closed fifty years ago", "relevance": "founding history"}
    base = {
        "world_name": "Science fiction",
        "history_key_events": [past],
        "locations": [{"name": "Relay", "atmosphere": "quiet"}],
        "rules": [{"name": "Fuel is finite"}],
        "factions": [{"name": "Couriers"}],
        "_meta": {
            "source_bound_expansion_review": "passed",
            "source_bound_expansion_review_receipt": {"decision": "pass"},
            "user_key": {"preserve": True},
        },
    }
    source = {
        "frontier_summary": summary,
        "new_locations": [],
        "_meta": {"volume_entry_hash": "approved-entry"},
    }
    v2 = deepcopy(base)
    v2["history_key_events"].append(event(summary))
    v3 = deepcopy(v2)
    v3["history_key_events"].append(event(summary))
    resumed_source = deepcopy(source)
    resumed_source["_meta"]["volume_entry_hash"] = "approved-shorter-entry"
    return {
        "project_id": "fixture-project",
        "world_versions": [
            row("w1", "world_spec", 1, base, 1),
            row("w2", "world_spec", 2, v2, 3),
            row("w3", "world_spec", 3, v3, 5),
        ],
        # PostgreSQL now() is constant in a transaction: D and W timestamps tie.
        "disclosures": [
            row("d1", "volume_world_disclosure", 1, source, 3, volume_number=2),
            row("d2", "volume_world_disclosure", 2, resumed_source, 5, volume_number=2),
        ],
        "canon_facts": [],
        "timeline_events": [],
        "authored_history": [],
    }


@pytest.mark.parametrize(
    "genre,summary",
    [
        ("科幻", "Reach the outer relay"),
        ("都市", "下阶段将取得餐车经营许可"),
        ("奇幻", "下一卷打开灵界边界"),
        ("mystery", "Dévoiler le mobile du suspect"),
        ("historical", "Пересечь северную границу"),
    ],
)
def test_frontiers_are_idempotent_planning_notes_with_real_past_preserved(genre, summary):
    world = {
        "world_name": genre,
        "history_key_events": [{"event": "An actual past event", "relevance": "authored history"}],
        "_meta": {"unrelated": [1, 2]},
    }
    disclosure = {"frontier_summary": summary, "_meta": {"volume_entry_hash": "entry-2"}}
    original = deepcopy((world, disclosure))
    first = merge(world, disclosure, volume_number=2)
    assert merge(first, disclosure, volume_number=2) == first
    assert first["history_key_events"] == world["history_key_events"]
    (receipt,) = first["_meta"]["planned_frontiers"].values()
    assert receipt["epistemic_status"] == "planned" and not receipt["historical_fact"]
    assert receipt["frontier_summary"] == summary
    assert receipt["volume_entry_hash"] == "entry-2"
    assert first["_meta"]["unrelated"] == [1, 2]
    assert (world, disclosure) == original


def test_receipts_bind_volume_entry_and_content_including_no_entry_hash():
    world = {"world_name": "Another world"}
    for volume, summary, entry_hash in [
        (1, "advance", "a"),
        (2, "advance", "a"),
        (2, "advance", "b"),
        (2, "new goal", "b"),
        (3, "fallback hash", None),
    ]:
        disclosure = {"frontier_summary": summary}
        if entry_hash:
            disclosure["_meta"] = {"volume_entry_hash": entry_hash}
        world = merge(world, disclosure, volume_number=volume)
        assert merge(world, deepcopy(disclosure), volume_number=volume) == world
    assert len(world["_meta"]["planned_frontiers"]) == 5
    assert "history_key_events" not in world


@pytest.mark.parametrize("entry_hash", [None, "approved-window"])
def test_imported_metadata_variants_keep_original_receipt_and_full_source_hash(entry_hash):
    bare = {
        "frontier_summary": "下一阶段前往港口",
        "new_locations": [{"name": "Harbor", "atmosphere": "busy"}],
        "new_rules_revealed": [],
        "faction_movements": [],
        "_meta": {"source_compiler": "approved-design-world-disclosure.v1"},
    }
    if entry_hash:
        bare["_meta"]["volume_entry_hash"] = entry_hash
    imported = deepcopy(bare)
    imported["_meta"].update(policy_versions={"world": "prior-policy"}, import_receipt="first")
    world = {"world_name": "Coastal mystery", "_meta": {"unrelated": "keep"}}
    first = merge(world, imported, volume_number=3)
    first_receipts = deepcopy(first["_meta"]["planned_frontiers"])
    assert len(first_receipts) == 1
    (receipt,) = first_receipts.values()
    assert receipt["disclosure_content_hash"] == content_hash(imported)
    for variant in (
        bare,
        imported,
        {
            **deepcopy(bare),
            "_meta": {
                **bare["_meta"],
                "policy_versions": {"world": "new-policy"},
                "source_run_id": "different-run",
                "import_receipt": "later",
            },
        },
    ):
        second = merge(first, variant, volume_number=3)
        assert second == first
        assert second["_meta"]["planned_frontiers"] == first_receipts
    assert world == {"world_name": "Coastal mystery", "_meta": {"unrelated": "keep"}}


@pytest.mark.parametrize(
    "changed_field",
    [
        "new_locations",
        "new_rules_revealed",
        "faction_movements",
        "volume_entry_hash",
    ],
)
def test_real_disclosure_body_or_window_changes_get_new_receipts_and_merge(changed_field):
    original = {
        "frontier_summary": "Reach the island",
        "new_locations": [{"name": "Harbor", "atmosphere": "quiet"}],
        "new_rules_revealed": [{"name": "Fuel limit"}],
        "faction_movements": [{"name": "Guild", "goal": "trade"}],
        "_meta": {"volume_entry_hash": "full-window", "policy_versions": {"world": "v1"}},
    }
    first = merge({"world_name": "Ocean expedition"}, original, volume_number=2)
    changed = deepcopy(original)
    if changed_field == "new_locations":
        changed[changed_field][0]["atmosphere"] = "busy"
    elif changed_field == "new_rules_revealed":
        changed[changed_field].append({"name": "Tide limit"})
    elif changed_field == "faction_movements":
        changed[changed_field][0]["goal"] = "sail"
    else:
        changed["_meta"]["volume_entry_hash"] = "shorter-window"
    second = merge(first, changed, volume_number=2)
    assert len(second["_meta"]["planned_frontiers"]) == 2
    for key, receipt in first["_meta"]["planned_frontiers"].items():
        assert second["_meta"]["planned_frontiers"][key] == receipt
    assert merge(second, changed, volume_number=2) == second
    if changed_field == "new_locations":
        assert second["locations"][0]["atmosphere"] == "busy"
    elif changed_field == "new_rules_revealed":
        assert [r["name"] for r in second["rules"]] == ["Fuel limit", "Tide limit"]
    elif changed_field == "faction_movements":
        assert second["factions"][0]["goal"] == "sail"
    else:
        for key in ("locations", "rules", "factions"):
            assert second[key] == first[key]


def test_legacy_full_hash_receipt_is_preserved_and_semantic_transition_stabilizes():
    imported = {
        "frontier_summary": "Investigate the harbor",
        "new_locations": [{"name": "Harbor"}],
        "_meta": {"volume_entry_hash": "window", "policy_versions": {"world": "prior"}},
    }
    legacy_hash = content_hash(imported)
    legacy_key = f"2:window:{legacy_hash}"
    legacy_receipt = {
        "policy": "world-disclosure-frontier-provenance.v1",
        "epistemic_status": "planned",
        "historical_fact": False,
        "volume_number": 2,
        "volume_entry_hash": "window",
        "disclosure_content_hash": legacy_hash,
        "frontier_summary": imported["frontier_summary"],
    }
    world = {
        "world_name": "Harbor mystery",
        "locations": [{"name": "Harbor"}],
        "_meta": {"planned_frontiers": {legacy_key: deepcopy(legacy_receipt)}},
    }
    bare = deepcopy(imported)
    bare["_meta"].pop("policy_versions")
    transition = merge(world, bare, volume_number=2)
    assert transition["_meta"]["planned_frontiers"][legacy_key] == legacy_receipt
    assert len(transition["_meta"]["planned_frontiers"]) == 2
    assert merge(transition, imported, volume_number=2) == transition
    assert {k: v for k, v in transition.items() if k != "_meta"} == {
        k: v for k, v in world.items() if k != "_meta"
    }
    assert world["_meta"]["planned_frontiers"] == {legacy_key: legacy_receipt}


def test_multiple_legacy_receipts_transition_once_preserve_body_and_stay_stable():
    bare = {
        "frontier_summary": "Obtain the ferry license",
        "new_locations": [{"name": "Pier", "type": "port"}],
        "new_rules_revealed": [],
        "faction_movements": [],
        "_meta": {
            "source_compiler": "approved-design-world-disclosure.v1",
            "volume_entry_hash": "current-window",
        },
    }
    imported = deepcopy(bare)
    imported["_meta"]["policy_versions"] = {"world": "prior"}
    earlier = deepcopy(imported)
    earlier["_meta"]["volume_entry_hash"] = "earlier-window"
    legacy_receipts = {}
    for source in (earlier, imported, bare):
        digest = content_hash(source)
        window = source["_meta"]["volume_entry_hash"]
        legacy_receipts[f"3:{window}:{digest}"] = {
            "policy": "world-disclosure-frontier-provenance.v1",
            "epistemic_status": "planned",
            "historical_fact": False,
            "volume_number": 3,
            "volume_entry_hash": window,
            "disclosure_content_hash": digest,
            "frontier_summary": source["frontier_summary"],
        }
    world = {
        "world_name": "Riverside mystery",
        "locations": [{"name": "Pier", "type": "port"}],
        "history_key_events": [{"event": "The pier opened last century", "relevance": "past"}],
        "_meta": {"planned_frontiers": deepcopy(legacy_receipts), "keep": [1, 2]},
    }
    original = deepcopy(world)
    body_hash = content_hash({k: v for k, v in world.items() if k != "_meta"})
    transition = merge(world, bare, volume_number=3)
    assert len(transition["_meta"]["planned_frontiers"]) == len(legacy_receipts) + 1
    for key, receipt in legacy_receipts.items():
        assert transition["_meta"]["planned_frontiers"][key] == receipt
    assert content_hash({k: v for k, v in transition.items() if k != "_meta"}) == body_hash
    after_first_repeat = merge(transition, bare, volume_number=3)
    after_second_repeat = merge(after_first_repeat, imported, volume_number=3)
    assert after_first_repeat == after_second_repeat == transition
    assert content_hash(after_second_repeat) == content_hash(transition)
    assert world == original


def test_real_locations_rules_and_factions_still_merge():
    world = {
        "locations": [{"name": "Harbor", "type": "port"}],
        "rules": [{"name": "Existing", "rule_id": "R1"}],
        "factions": [{"name": "Guild", "goal": "trade"}],
    }
    disclosure = {
        "frontier_summary": "Will sail",
        "new_locations": [{"name": "Harbor", "atmosphere": "busy"}, {"name": "Island"}],
        "new_rules_revealed": [{"name": "Existing"}, {"name": "New rule"}],
        "faction_movements": [
            {"name": "Guild", "goal": "sail"},
            {"name": "Watch", "goal": "guard"},
        ],
    }
    out = merge(world, disclosure, volume_number=4)
    assert out["locations"] == [
        {"name": "Harbor", "type": "port", "atmosphere": "busy"},
        {"name": "Island"},
    ]
    assert out["rules"] == [
        {"name": "Existing", "rule_id": "R1"},
        {"name": "New rule", "rule_id": "VR04-02"},
    ]
    assert out["factions"][0]["goal"] == "sail"
    assert out["factions"][1]["name"] == "Watch"
    assert merge(out, disclosure, volume_number=4) == out


@pytest.mark.parametrize(
    "summary", ["Reach the outer relay", "查清码头事故", "妖界开门", "Find a lost will"]
)
def test_legacy_exact_producer_entries_archived_with_immutable_versions(summary):
    data = snapshot(summary)
    original = deepcopy(data)
    audit = audit_world_disclosure_history(**data)
    assert audit["removable_count"] == 2 and audit["unknown_count"] == 0
    proposal = propose_world_disclosure_repair(**data)
    assert proposal == propose_world_disclosure_repair(**data)
    out = apply_world_disclosure_repair(
        proposal,
        expected_source_hash=audit["source_hash"],
        actor="fixture-technical-reviewer",
        evidence={"test": "exact append and source hashes inspected"},
        **data,
    )
    assert out["applied"] and not out["persisted"]
    candidate = out["candidate"]
    assert candidate["status"] == "pending_review" and candidate["requires_source_bound_review"]
    content = candidate["content"]
    assert (
        content["history_key_events"]
        == original["world_versions"][0]["content"]["history_key_events"]
    )
    for key in ("locations", "rules", "factions", "world_name"):
        assert content[key] == original["world_versions"][-1]["content"][key]
    archive = content["_meta"]["world_disclosure_migration"]
    assert len(archive["archived_generated_entries"]) == 2
    assert archive["source_hash"] == content_hash(original["world_versions"][-1]["content"])
    assert archive["historical_review_metadata"]["source_bound_expansion_review"] == "passed"
    assert (
        content["_meta"]["source_bound_expansion_review"] == "independent_semantic_review_required"
    )
    assert archive["prior_outline_grades"] == "historical_only"
    assert (
        archive["recovered_outline_review_requirement"]
        == "fresh_review_against_current_approved_world"
    )
    assert out["operator_receipt"]["semantic_approval"] is False
    assert len(content["_meta"]["planned_frontiers"]) == 2
    assert data == original
    # A parent-created pending candidate is a fixed point, not a new approval.
    data["world_versions"].append(row("w4", "world_spec", 4, content, 6, status="pending_review"))
    assert not propose_world_disclosure_repair(**data)["changed"]


@pytest.mark.parametrize(
    "change",
    [
        "no_previous",
        "version_gap",
        "not_approved",
        "other_project",
        "wrong_type",
        "wrong_scope",
        "missing_volume_binding",
        "wrong_volume_binding",
        "future_source",
        "previous_timestamp",
        "missing_timezone",
        "different_text",
        "extra_field",
        "wrong_language",
        "workflow_mismatch",
        "prefix_changed",
        "multi_append",
    ],
)
def test_unsupported_attribution_is_unknown_never_deleted(change):
    data = snapshot()
    data["world_versions"] = data["world_versions"][:2]
    target = data["world_versions"][-1]
    source = data["disclosures"][0]
    last_event = target["content"]["history_key_events"][-1]
    if change == "no_previous":
        data["world_versions"] = [target]
    elif change == "version_gap":
        target["version_no"] = 4
    elif change == "not_approved":
        source["status"] = "pending_review"
    elif change == "other_project":
        source["project_id"] = "different-project"
    elif change == "wrong_type":
        source["artifact_type"] = "volume_plan"
    elif change == "wrong_scope":
        source["scope_ref_id"] = "another-volume-scope"
    elif change == "missing_volume_binding":
        del source["volume_number"]
    elif change == "wrong_volume_binding":
        source["volume_number"] = 1
    elif change == "future_source":
        source["created_at"] = "2026-01-01T00:00:04+00:00"
    elif change == "previous_timestamp":
        source["created_at"] = data["world_versions"][0]["created_at"]
    elif change == "missing_timezone":
        source["created_at"] = "2026-01-01T00:00:02"
    elif change == "different_text":
        source["content"]["frontier_summary"] += " differently"
    elif change == "extra_field":
        last_event["author"] = "someone"
    elif change == "wrong_language":
        last_event["relevance"] = "第2卷世界边界更新"
    elif change == "workflow_mismatch":
        source["source_run_id"], target["source_run_id"] = "workflow-a", "workflow-b"
    elif change == "prefix_changed":
        target["content"]["history_key_events"][0]["event"] = "Changed authored past"
    elif change == "multi_append":
        target["content"]["history_key_events"].append(deepcopy(last_event))
    before = deepcopy(data)
    proposal = propose_world_disclosure_repair(**data)
    assert not proposal["changed"]
    assert proposal["candidate"]["content"] == target["content"]
    assert data == before


@pytest.mark.parametrize("protection", ["canon_facts", "timeline_events", "authored_history"])
def test_exact_independent_history_protected_and_token_overlap_does_not_rewrite(protection):
    data = snapshot()
    text = data["disclosures"][0]["content"]["frontier_summary"]
    data[protection] = [{"event": text}]
    assert not propose_world_disclosure_repair(**data)["changed"]
    data[protection] = [{"event": "The outer relay was built in year ten"}]
    # Token overlap cannot erase unrelated past facts or canon/timeline records.
    unrelated = {"event": "Reach the outer relay was an old oath", "relevance": "authored"}
    for world in data["world_versions"]:
        world["content"]["history_key_events"].insert(0, deepcopy(unrelated))
    proposal = propose_world_disclosure_repair(**data)
    assert proposal["audit"]["removable_count"] == 2
    assert proposal["candidate"]["content"]["history_key_events"][0] == unrelated


def test_baseline_exact_collision_remains_unknown_while_later_generated_duplicates_archive():
    data = snapshot()
    collision = event(data["disclosures"][0]["content"]["frontier_summary"])
    for world in data["world_versions"]:
        world["content"]["history_key_events"].insert(0, deepcopy(collision))
    proposal = propose_world_disclosure_repair(**data)
    assert proposal["audit"]["unknown_count"] == 1
    assert proposal["audit"]["removable_count"] == 2
    assert proposal["candidate"]["content"]["history_key_events"][0] == collision


@pytest.mark.parametrize("change", ["source", "source_id", "knowledge", "disclosure", "candidate"])
def test_apply_rejects_stale_sources_changed_evidence_or_tampered_candidate(change):
    data = snapshot()
    proposal = propose_world_disclosure_repair(**data)
    expected_hash = proposal["audit"]["source_hash"]
    if change == "source":
        data["world_versions"][-1]["content"]["world_name"] = "changed"
    elif change == "source_id":
        data["world_versions"][-1]["id"] = "different-id-same-content"
    elif change == "knowledge":
        data["canon_facts"].append({"value_json": "Reach the outer relay"})
    elif change == "disclosure":
        data["disclosures"][0]["status"] = "rejected"
    else:
        proposal["candidate"]["status"] = "approved"
    with pytest.raises(ValueError, match="changed"):
        apply_world_disclosure_repair(
            proposal,
            expected_source_hash=expected_hash,
            actor="operator",
            evidence={"inspected": True},
            **data,
        )


@pytest.mark.parametrize("actor,evidence", [("", {"checked": True}), ("operator", {})])
def test_apply_requires_real_operator_inspection_fields(actor, evidence):
    data = snapshot()
    proposal = propose_world_disclosure_repair(**data)
    with pytest.raises(ValueError, match="operator"):
        apply_world_disclosure_repair(
            proposal,
            expected_source_hash=proposal["audit"]["source_hash"],
            actor=actor,
            evidence=evidence,
            **data,
        )


def test_duplicate_artifact_ids_cannot_substitute_unapproved_foreign_content():
    data = snapshot()
    substitute = deepcopy(data["disclosures"][0])
    substitute.update(project_id="foreign-project", status="rejected")
    substitute["content"]["frontier_summary"] = "Unrelated forged frontier"
    data["disclosures"].append(substitute)
    with pytest.raises(ValueError, match="Duplicate artifact identities"):
        propose_world_disclosure_repair(**data)


def test_conflicting_approved_producers_remain_unknown():
    data = snapshot()
    data["world_versions"] = data["world_versions"][:2]
    ambiguous = deepcopy(data["disclosures"][0])
    ambiguous["id"] = "different-source"
    ambiguous["content"]["_meta"]["volume_entry_hash"] = "different-entry-same-goal"
    data["disclosures"].append(ambiguous)
    proposal = propose_world_disclosure_repair(**data)
    assert not proposal["changed"]
    assert proposal["audit"]["unknown_count"] == 1


@pytest.mark.parametrize("valid_reuse", [False, True])
def test_reused_artifact_can_cross_runs_only_with_exact_reference(valid_reuse):
    data = snapshot()
    data["disclosures"] = data["disclosures"][:1]
    source = data["disclosures"][0]
    source["source_run_id"] = "original-run"
    data["world_versions"][1]["source_run_id"] = "original-run"
    data["world_versions"][2]["source_run_id"] = "resumed-run"
    source["reuse_receipts"] = [
        {
            "workflow_run_id": "resumed-run",
            "artifact_id": source["id"],
            "content_hash": content_hash(source["content"]) if valid_reuse else "wrong-hash",
        }
    ]
    proposal = propose_world_disclosure_repair(**data)
    assert proposal["audit"]["removable_count"] == (2 if valid_reuse else 1)
    assert proposal["audit"]["unknown_count"] == (0 if valid_reuse else 1)


@pytest.mark.parametrize(
    "summary", ["Recover a missing letter", "取得行医许可", "遺跡の封印を解く"]
)
def test_pg_transaction_timestamp_ties_attribute_each_append_to_current_producer(summary):
    data = snapshot(summary)
    original = deepcopy(data)
    audit = audit_world_disclosure_history(**data)
    assert audit["removable_count"] == 2 and audit["unknown_count"] == 0
    for finding, producer, world in zip(
        audit["findings"], data["disclosures"], data["world_versions"][1:], strict=True
    ):
        assert producer["created_at"] == world["created_at"]
        provenance = finding["provenance"]
        assert provenance["introduced_in"]["id"] == world["id"]
        (source,) = provenance["sources"]
        assert source["id"] == producer["id"]
        assert source["content_hash"] == content_hash(producer["content"])
    corrected = propose_world_disclosure_repair(**data)["candidate"]["content"]

    def body(content):
        return {key: value for key, value in content.items() if key != "_meta"}

    assert body(corrected) == body(data["world_versions"][0]["content"])
    assert data == original


def test_older_unrelated_matching_source_cannot_shadow_unique_current_producer():
    data = snapshot()
    older = deepcopy(data["disclosures"][0])
    older["id"] = "older-unrelated-source"
    older["created_at"] = "2025-12-31T23:59:59+00:00"
    older["content"]["_meta"]["volume_entry_hash"] = "unrelated-entry-with-same-goal"
    data["disclosures"].insert(0, older)
    audit = audit_world_disclosure_history(**data)
    assert audit["removable_count"] == 2 and audit["unknown_count"] == 0
    assert [f["provenance"]["sources"][0]["id"] for f in audit["findings"]] == ["d1", "d2"]


@pytest.mark.parametrize("source_when", ["2025-12-31T23:59:59+00:00", "2026-01-01T00:00:03+00:00"])
def test_source_at_or_before_previous_world_is_unknown_without_explicit_reuse(source_when):
    data = snapshot()
    data["disclosures"] = data["disclosures"][:1]
    data["disclosures"][0]["created_at"] = source_when
    audit = audit_world_disclosure_history(**data)
    assert audit["findings"][-1]["classification"] == "Unknown"
    assert audit["findings"][-1]["provenance"] is None


@pytest.mark.parametrize("valid_reuse", [False, True])
def test_old_source_reuse_needs_exact_receipt_even_within_same_run(valid_reuse):
    data = snapshot()
    data["disclosures"] = data["disclosures"][:1]
    source = data["disclosures"][0]
    source["source_run_id"] = "same-run"
    for world in data["world_versions"]:
        world["source_run_id"] = "same-run"
    source["reuse_receipts"] = [
        {
            "workflow_run_id": "same-run",
            "artifact_id": source["id"],
            "content_hash": content_hash(source["content"]) if valid_reuse else "wrong-hash",
        }
    ]
    audit = audit_world_disclosure_history(**data)
    assert audit["removable_count"] == (2 if valid_reuse else 1)
