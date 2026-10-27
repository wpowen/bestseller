"""Finite-book compiler allowance preserves full unrelated sources; no SEND."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from test_source_bound_rolling_outline_prompt import args as args
from bestseller.services import planner, planning_draft_admission as admission
from bestseller.services.finite_book_budget import bind_run
from bestseller.services.prompt_compiler import PromptBudgetError
from bestseller.services.source_bound_rolling_outline_prompt import (
    build_source_bound_rolling_outline_prompt,
)
from bestseller.services.rolling_outline_source_transport import _split
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def test_matching_finite_book_keeps_unknown_full_sources_without_changing_default(
    args, monkeypatch
):
    args["world_spec"]["unclassified_authored"] = "".join(
        f"观测站原始记录{i:05d}：保留每一处限定与顺序。\n" for i in range(2100)
    )
    p = args["project"]
    original_meta = p.metadata_json.copy()
    system, user = build_source_bound_rolling_outline_prompt(**args)
    monkeypatch.setattr(planner, "_source_bound_cast_enabled", lambda p: True)
    from bestseller.services import book_design

    monkeypatch.setattr(
        book_design,
        "ensure_project_book_design_snapshot",
        lambda p: NS(
            snapshot_id="fixture-snapshot",
            source_hash="a" * 64,
            creation_intent=NS(contract_hash=lambda: "b" * 64),
        ),
    )
    settings = load_settings()
    settings.llm.planner_prompt_budget_tokens = 16000
    before = _split(system, user)[3]
    with pytest.raises(PromptBudgetError):
        planner._compile_volume_outline_prompt(p, settings, system_prompt=system, user_prompt=user)
    with bind_run(uuid4(), project_id=uuid4(), stage="13:plan", capacity_limit=196608):
        with pytest.raises(PromptBudgetError):
            planner._compile_volume_outline_prompt(
                p, settings, system_prompt=system, user_prompt=user
            )
    with bind_run(uuid4(), project_id=p.id, stage="13:plan", capacity_limit=196608):
        compiled_system, compiled_user = planner._compile_volume_outline_prompt(
            p, settings, system_prompt=system, user_prompt=user
        )
        assert _split(compiled_system, "\n" + compiled_user)[3] == before
        receipt = p.metadata_json["planner_prompt_compiler_latest"]["finite_book_compiler_capacity"]
        assert receipt["capacity_limit"] == 196608
        assert receipt["output_reserve"] == planner._PLANNER_MAX_OUTPUT_TOKENS
        assert receipt["authorization_claimed"] is False
    assert settings.llm.planner_prompt_budget_tokens == 16000
    p.metadata_json = original_meta
    with pytest.raises(PromptBudgetError):
        planner._compile_volume_outline_prompt(p, settings, system_prompt=system, user_prompt=user)


def test_native_admission_whole_input_receipt_uses_active_cap_without_prompt_changes():
    user = "记" * 40000
    old = admission._input_capacity_receipt(user)
    assert not old["budget_passed"] and old["total_token_budget"] == 65536
    with bind_run(uuid4(), project_id=uuid4(), stage="20:admit", capacity_limit=196608):
        scoped = admission._input_capacity_receipt(user)
        assert scoped["budget_passed"] and scoped["total_token_budget"] == 196608
    assert old["user_sha256"] == scoped["user_sha256"] == content_hash(user)
    assert old["reserved_total_tokens"] == scoped["reserved_total_tokens"]
    assert not admission._input_capacity_receipt(user)["budget_passed"]


@pytest.mark.parametrize("start,end", [(7, 9), (10, 12), (13, 15), (16, 18), (19, 20)])
@pytest.mark.parametrize(
    "suffix,expected",
    [
        ("", "primary"),
        ("_story_enhancer_route_repair", "hard_repair"),
        ("_commercial_surgical_repair", "hard_repair"),
    ],
)
def test_real_normal_batch_families_are_bounded_by_exact_stage_and_template(
    start, end, suffix, expected
):
    from bestseller.services.book_request_purpose import classify, primary_occurrences

    template = f"planner_volume_1_chapter_outline_batch_{start}_{end}{suffix}"
    assert classify("13:plan", template) == expected
    assert primary_occurrences("13:plan", template) == 1
    with pytest.raises(ValueError, match="finite_unknown_request_family"):
        classify("13:review", template)


@pytest.mark.parametrize(
    "template",
    [
        "planner_volume_1_chapter_outline_batch_9_7",
        "planner_volume_1_chapter_outline_batch_0_3",
        "planner_volume_1_chapter_outline_batch_7_9_unknown",
        "planner_volume_1_world_disclosure_batch_7_9",
        "planner_volume_1_chapter_outline_batch_7_9_extra_commercial_surgical_repair",
    ],
)
def test_unknown_or_malformed_batch_request_family_stays_denied(template):
    from bestseller.services.book_request_purpose import classify

    with pytest.raises(ValueError, match="finite_unknown_request_family"):
        classify("7:plan", template)
