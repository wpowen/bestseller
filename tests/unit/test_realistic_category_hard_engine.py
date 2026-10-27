from copy import deepcopy

from bestseller.services.category_hard_engines import (
    build_category_engine_fixture,
    evaluate_category_hard_engine,
    resolve_category_hard_engine_key,
)
from bestseller.services.premium_book_gate import _capability_findings, _capability_snapshot


def intent():
    return {"genre_key": "realistic", "prompt_pack_key": "realistic-life", "source": "user"}


def test_user_realistic_intent_overrides_stale_inferred_romance_category():
    metadata = {"genre_intent_contract": intent(), "category_key": "relationship-driven"}
    assert (
        resolve_category_hard_engine_key(metadata, genre="现实百态", sub_genre="关系选择")
        == "realistic-life"
    )
    assert (
        resolve_category_hard_engine_key({}, genre="言情", sub_genre="关系")
        == "relationship-driven"
    )


def test_event_producer_to_snapshot_to_premium_consumer():
    metadata = build_category_engine_fixture("realistic-life", good=True)
    metadata["genre_intent_contract"] = intent()
    result = _capability_snapshot(metadata, genre="现实百态", sub_genre="现实百态")
    assert result["category_hard_engine"]["passed"]
    metadata["premium_state_ledger"]["relationship_events"][0].pop("active_choice")
    failed = _capability_snapshot(metadata, genre="现实百态", sub_genre="现实百态")
    assert not failed["category_hard_engine"]["passed"]
    assert any(f.code.startswith("category_") for f in _capability_findings(failed))


def test_missing_realistic_history_and_stale_snapshot_still_block():
    metadata = build_category_engine_fixture("realistic-life", good=True)
    stale = deepcopy(metadata)
    stale["premium_state_snapshot"]["relationship_state"] = {"unrelated": {"axes": {}}}
    assert not evaluate_category_hard_engine(stale, category_key="realistic-life").passed
    metadata.pop("premium_state_ledger")
    assert not evaluate_category_hard_engine(metadata, category_key="realistic-life").passed


def test_realistic_events_do_not_satisfy_romance_contract():
    metadata = build_category_engine_fixture("realistic-life", good=True)
    report = evaluate_category_hard_engine(metadata, category_key="relationship-driven")
    assert not report.passed
    assert "intimacy_boundaries" in report.findings[0].missing_keys


async def test_realistic_hydration_reuses_events_without_manufacturing_history():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from bestseller.services.material_density import (
        _select_material_pack,
        hydrate_premium_capability_metadata,
    )

    metadata = build_category_engine_fixture("realistic-life", good=True)
    metadata["genre_intent_contract"] = intent()
    original = deepcopy(metadata["premium_state_ledger"])
    project = SimpleNamespace(metadata_json=metadata)
    session = SimpleNamespace(get=AsyncMock(return_value=project), flush=AsyncMock())
    pack, candidates = _select_material_pack("p", "", genre="现实百态", sub_genre="现实百态")
    assert pack == "category_realistic_life_zh" and not candidates
    result = await hydrate_premium_capability_metadata(session, project_id="p", pack_id=pack)
    assert result["state_source_status"] == "project_events"
    assert project.metadata_json["premium_state_ledger"] == original
    assert _capability_snapshot(project.metadata_json, genre="现实百态", sub_genre="现实百态")[
        "category_hard_engine"
    ]["passed"]


async def test_old_template_is_preserved_but_not_accepted_as_realistic_history():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from bestseller.services.material_density import (
        _initial_premium_state_ledger_for_pack,
        hydrate_premium_capability_metadata,
    )
    from bestseller.services.premium_state_ledger import materialize_premium_state_snapshot

    pack = "category_relationship_driven_zh"
    ledger = _initial_premium_state_ledger_for_pack(pack)
    metadata = {
        "genre_intent_contract": intent(),
        "premium_capability_pack": pack,
        "premium_state_ledger": ledger,
        "premium_state_ledger_report": {"passed": True},
        "premium_state_snapshot": materialize_premium_state_snapshot(ledger),
    }
    assert not evaluate_category_hard_engine(metadata, category_key="realistic-life").passed
    project = SimpleNamespace(metadata_json=deepcopy(metadata))
    session = SimpleNamespace(get=AsyncMock(return_value=project), flush=AsyncMock())
    result = await hydrate_premium_capability_metadata(session, project_id="p", pack_id=pack)
    assert result["state_source_status"] == "needs_replan"
    assert project.metadata_json["premium_state_ledger"] == ledger
    assert not evaluate_category_hard_engine(
        project.metadata_json, category_key="realistic-life"
    ).passed


def test_actual_chapter_events_replace_only_exact_legacy_template_and_clear_gate():
    from types import SimpleNamespace

    from bestseller.services.material_density import _initial_premium_state_ledger_for_pack
    from bestseller.services.story_bible import _append_premium_state_ledger

    old = _initial_premium_state_ledger_for_pack("category_relationship_driven_zh")
    project = SimpleNamespace(
        metadata_json={
            "genre_intent_contract": intent(),
            "premium_capability_pack": "category_relationship_driven_zh",
            "premium_state_ledger": deepcopy(old),
            "premium_state_ledger_provenance": {"status": "needs_replan"},
        }
    )
    actual = build_category_engine_fixture("realistic-life", good=True)["premium_state_ledger"]
    assert (
        _append_premium_state_ledger(
            project, updates={"premium_engine_updates": actual}, chapter_number=1
        )
        == 1
    )
    assert project.metadata_json["premium_state_template_archive"][0]["ledger"] == old
    assert len(project.metadata_json["premium_state_ledger"]["relationship_events"]) == 1
    assert (
        project.metadata_json["premium_state_ledger"]["relationship_events"][0]["source"]
        == "chapter_1"
    )
    assert _capability_snapshot(project.metadata_json, genre="现实百态", sub_genre="现实百态")[
        "category_hard_engine"
    ]["passed"]


def test_incomplete_extraction_does_not_replace_template_or_clear_replan():
    from types import SimpleNamespace

    from bestseller.services.material_density import _initial_premium_state_ledger_for_pack
    from bestseller.services.story_bible import _append_premium_state_ledger

    metadata = {
        "genre_intent_contract": intent(),
        "premium_capability_pack": "category_relationship_driven_zh",
        "premium_state_ledger": _initial_premium_state_ledger_for_pack(
            "category_relationship_driven_zh"
        ),
    }
    project = SimpleNamespace(metadata_json=deepcopy(metadata))
    assert (
        _append_premium_state_ledger(
            project,
            updates={"premium_engine_updates": {"relationship_events": [{"after": "信任降低"}]}},
            chapter_number=1,
        )
        == 0
    )
    assert project.metadata_json == metadata


def test_mixed_existing_history_is_append_only():
    from types import SimpleNamespace

    from bestseller.services.material_density import _initial_premium_state_ledger_for_pack
    from bestseller.services.story_bible import _append_premium_state_ledger

    ledger = _initial_premium_state_ledger_for_pack("category_relationship_driven_zh")
    ledger["relationship_events"].append(
        {
            "character_a": "甲",
            "character_b": "乙",
            "axis": "trust",
            "active_choice": "拒绝担保",
            "after": "合作暂停",
            "chapter_number": 1,
            "source": "chapter_1",
        }
    )
    project = SimpleNamespace(
        metadata_json={
            "genre_intent_contract": intent(),
            "premium_capability_pack": "category_relationship_driven_zh",
            "premium_state_ledger": deepcopy(ledger),
        }
    )
    actual = build_category_engine_fixture("realistic-life", good=True)["premium_state_ledger"]
    assert (
        _append_premium_state_ledger(
            project, updates={"premium_engine_updates": actual}, chapter_number=2
        )
        == 1
    )
    assert (
        project.metadata_json["premium_state_ledger"]["relationship_events"][:2]
        == ledger["relationship_events"]
    )
    assert "premium_state_template_archive" not in project.metadata_json
