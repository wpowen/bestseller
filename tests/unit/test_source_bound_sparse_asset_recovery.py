"""Sparse asset recovery must use final content without bypassing review."""

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_planner_services import FakeSession, build_project, build_settings

from bestseller.services import planner

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "place,faction",
    [("菜市场", "合作社"), ("星港", "航运公会"), ("鹤鸣山", "丹师盟")],
)
def test_same_named_world_entities_gain_missing_details_only(place, faction):
    baseline = {
        "rules": [{"description": "approved rule"}],
        "locations": [{"name": place, "atmosphere": "approved atmosphere"}],
        "factions": [{"name": faction, "goal": "approved goal", "method": ""}],
    }
    original = copy.deepcopy(baseline)
    candidate = planner._rebind_source_bound_world_expansion(
        baseline,
        {
            "locations": [
                {"name": place, "atmosphere": "overwrite", "story_role": "first negotiation",
                 "history": "unlicensed history"},
                {"name": place, "story_role": "overwrite again"},
            ],
            "factions": [{"name": faction, "goal": "overwrite", "method": "bid on cargo"}],
            "rules": [{"description": "overwrite rule"}],
        },
    )
    assert baseline == original
    assert candidate["locations"] == [
        {"name": place, "atmosphere": "approved atmosphere", "story_role": "first negotiation"}
    ]
    assert candidate["factions"] == [
        {"name": faction, "goal": "approved goal", "method": "bid on cargo"}
    ]
    assert candidate["rules"] == baseline["rules"]
    assert candidate["_meta"]["source_bound_expansion"] == "derived_pending_review"
    assert candidate["_meta"]["source_bound_expansion_fields"] == ["locations", "factions"]


def _assets(asset, name):
    if asset == "world":
        baseline = {"locations": [{"name": name}], "factions": []}
        candidate = planner._rebind_source_bound_world_expansion(baseline, {})
        candidate["locations"][0]["story_role"] = "first negotiation"
    else:
        baseline = {"protagonist": {"name": "Protagonist"}, "supporting_cast": []}
        candidate = planner._rebind_source_bound_cast_expansion(baseline, {})
        candidate["supporting_cast"] = [
            {"name": name, "goal": "win the contract", "gender": "female",
             "pronoun_set_zh": "她", "pronoun_set_en": "she/her"}
        ]
    assert candidate["_meta"]["source_bound_expansion"] == "unavailable"
    return baseline, candidate


async def _review(asset, baseline, candidate):
    return await planner._review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=SimpleNamespace(
            id=uuid4(), language="en", metadata_json={"book_design_snapshot": {}}
        ),
        book_spec={"unique_hook": "approved hook"},
        world_baseline=baseline if asset == "world" else {},
        world_derived=candidate if asset == "world" else {},
        cast_baseline=baseline if asset == "cast" else {},
        cast_derived=candidate if asset == "cast" else {},
        workflow_run_id=uuid4(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("asset,name", [("world", "Orbital Dock"), ("cast", "林岚")])
@pytest.mark.parametrize("response", ["pass", "wrong_hash", "fallback", "core_conflict"])
async def test_repaired_assets_with_stale_marker_require_independent_review(
    monkeypatch, asset, name, response,
):
    baseline, candidate = _assets(asset, name)
    calls = []

    async def complete(_session, _settings, request):
        calls.append(request)
        fields = request.metadata["changed_fields"]
        assert fields == ["world.locations" if asset == "world" else "cast.supporting_cast"]
        return SimpleNamespace(
            content=json.dumps({
                "decision": "pass", "core_preserved": response != "core_conflict",
                "core_conflicts": ["replaced core"] if response == "core_conflict" else [],
                "authority_snapshot_hash": request.metadata["authority_snapshot_hash"],
                "candidate_hash": (
                    "wrong" if response == "wrong_hash" else request.metadata["candidate_hash"]
                ),
                "source_evidence": [
                    {"field": field, "quote": "approved hook", "source_relation": "approved action"}
                    for field in fields
                ],
                "candidate_evidence": [
                    {"field": field, "quote": name, "source_relation": "derived action asset"}
                    for field in fields
                ],
            }),
            provider="litellm", model_name="fixture", finish_reason="stop", llm_run_id=uuid4(),
            fallback_used=response == "fallback",
        )

    monkeypatch.setattr(planner, "complete_text", complete)
    report = await _review(asset, baseline, candidate)
    assert len(calls) == 1, report
    if response == "pass":
        assert report["decision"] == "pass"
        assert report["draft_eligible"] is True
        assert report["evidence_verified"] is True
    else:
        assert report["draft_eligible"] is False
        assert planner._source_bound_report_is_draft_eligible(report) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["empty", "voice_only", "name_only", "placeholder", "rules_only"])
async def test_unavailable_marker_cannot_be_cleared_by_non_assets(monkeypatch, case):
    asset = "world" if case in {"placeholder", "rules_only"} else "cast"
    baseline, candidate = _assets(asset, "Depot" if asset == "world" else "Mira")
    candidate = {**copy.deepcopy(baseline), "_meta": candidate["_meta"]}
    if case == "voice_only":
        candidate["protagonist"]["voice_profile"] = {"speech_register": "terse"}
    elif case == "name_only":
        candidate["supporting_cast"] = [{"name": "Mira"}]
    elif case == "placeholder":
        candidate["factions"] = [{"name": "current direct resistance", "goal": "oppose"}]
    elif case == "rules_only":
        candidate["rules"] = [{"description": "new rule"}]

    async def unexpected(*args, **kwargs):
        pytest.fail("Missing concrete assets must be blocked before model review")

    monkeypatch.setattr(planner, "complete_text", unexpected)
    report = await _review(asset, baseline, candidate)
    assert report["reason"] == "sparse_asset_expansion_unavailable"
    assert report["draft_eligible"] is False


@pytest.mark.asyncio
async def test_cast_repair_cannot_clear_unavailable_world(monkeypatch):
    cast_baseline, cast_candidate = _assets("cast", "Mira")
    world_baseline = {"locations": [], "factions": []}
    world_candidate = planner._rebind_source_bound_world_expansion(world_baseline, {})

    async def unexpected(*args, **kwargs):
        pytest.fail("A repaired cast cannot resolve missing world assets")

    monkeypatch.setattr(planner, "complete_text", unexpected)
    report = await planner._review_source_bound_expansion(
        None, SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=SimpleNamespace(language="en", metadata_json={}), book_spec={},
        world_baseline=world_baseline, world_derived=world_candidate,
        cast_baseline=cast_baseline, cast_derived=cast_candidate, workflow_run_id=uuid4(),
    )
    assert report["changed_fields"] == ["cast.supporting_cast"]
    assert report["reason"] == "sparse_asset_expansion_unavailable"


@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_world_expansion_prompt_requires_assets_without_requiring_literal_names(language):
    project = build_project()
    project.language = language
    _, prompt = planner._source_bound_world_expansion_prompts(
        project, "A repair worker meets a customer", {"unique_hook": "approved action"}, {}
    )
    if language == "en":
        assert "not two empty arrays" in prompt
        assert "does not require every derived" in prompt
        assert "faction is optional" in prompt
        assert "empty arrays are valid" not in prompt
    else:
        assert "不能两个数组都为空" in prompt
        assert "不等于派生实体的名称必须在原文逐字出现" in prompt
        assert "可以只补地点" in prompt
        assert "可以返回空数组" not in prompt


@pytest.mark.parametrize("title", ["开局缺上门量,我靠手艺翻身", "重回故城"])
def test_world_geography_does_not_extract_marketing_title(title):
    project = build_project()
    project.title = title
    project.language = "zh-CN"
    world = planner._compile_source_bound_world_spec(
        project, "一名修理工要决定是否让徒弟独立接活。", {"protagonist": {"name": "林岚"}}
    )
    assert all("开局缺上门" not in item["name"] for item in world["locations"])
    assert all("故城" not in item["name"] for item in world["locations"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["empty_then_valid", "always_empty", "fallback"])
async def test_world_expansion_empty_retry_retains_narrow_contract_and_fails_closed(
    monkeypatch, mode,
):
    project = build_project()
    calls = []
    valid = {"locations": [{"name": "Workshop", "story_role": "first job"}], "factions": []}

    async def complete(_session, _settings, request):
        calls.append(request)
        assert "【题材方法论·" not in request.user_prompt
        if len(calls) > 1:
            assert "Both arrays are empty" in request.user_prompt
            assert "Return only locations/factions" in request.user_prompt
        return SimpleNamespace(
            content=json.dumps(valid if mode == "empty_then_valid" and len(calls) == 2 else {
                "locations": [], "factions": [],
            }),
            llm_run_id=uuid4(), finish_reason="stop",
            provider="fallback" if mode == "fallback" else "litellm",
        )

    monkeypatch.setattr(planner, "complete_text", complete)
    monkeypatch.setattr(planner, "_persist_failing_planner_output", lambda **kwargs: None)

    async def generate():
        return await planner._generate_structured_artifact(
            FakeSession(), build_settings(), project=project,
            logical_name="world_spec_source_bound_expansion", system_prompt="JSON",
            user_prompt="Return only locations/factions for the approved action.",
            fallback_payload={"locations": [], "factions": []}, workflow_run_id=uuid4(),
            validator=planner._validate_source_bound_world_expansion_payload,
            merge_fallback=False,
        )

    if mode == "empty_then_valid":
        payload, run_id = await generate()
        assert len(calls) == 2
        assert payload["locations"] == valid["locations"]
        assert run_id is not None
    else:
        with pytest.raises(planner.PlannerFallbackError, match="world_spec_source_bound_expansion"):
            await generate()
        assert len(calls) == (1 if mode == "fallback" else 4)
    assert all(request.metadata["fail_closed"] for request in calls)


@pytest.mark.parametrize("asset", ["world", "cast"])
def test_source_bound_asset_prompts_do_not_acquire_whole_book_methodology(asset):
    assert planner._methodology_stage_for_planner_artifact(
        f"{asset}_spec_source_bound_expansion"
    ) is None
    assert planner._methodology_stage_for_planner_artifact(f"{asset}_spec") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("logical_name", ["world_spec", "world_spec_source_bound_expansion"])
async def test_complete_text_receives_methodology_only_for_regular_planning(
    monkeypatch, logical_name,
):
    calls = []
    marker = "GENERIC_WHOLE_BOOK_METHOD"
    monkeypatch.setattr(
        planner, "attach_planner_methodology", lambda prompt, **kwargs: marker + prompt
    )

    async def complete(_session, _settings, request):
        calls.append(request)
        assert (marker in request.user_prompt) == (logical_name == "world_spec")
        return SimpleNamespace(
            content=json.dumps({"locations": [{"name": "Harbor", "story_role": "arrival"}],
                                "factions": []}),
            provider="litellm", finish_reason="stop", llm_run_id=uuid4(),
        )

    monkeypatch.setattr(planner, "complete_text", complete)
    await planner._generate_structured_artifact(
        FakeSession(), build_settings(), project=build_project(), logical_name=logical_name,
        system_prompt="JSON", user_prompt="approved action", fallback_payload={},
        workflow_run_id=uuid4(), merge_fallback=False,
    )
    assert len(calls) == 1
