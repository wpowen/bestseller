"""Bounded RUN2 policy REDs: pure helpers only, no dispatch or application entry."""

import json
from pathlib import Path

import pytest

from bestseller.services import llm
from bestseller.services import minimax_m31_protocol as m31
from bestseller.services import production_strategy as strategy
from bestseller.settings import LLMRoleSettings

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports/full-flow-2026-10-28"
POLICY = json.loads((ROOT / "config/run2_new_book_template_policy.json").read_text())
CAPTURE = json.loads((REPORTS / "RUN2-PRODUCTION-ROLE-CONFIG.json").read_text())
CENSUS = json.loads((REPORTS / "RUN2-PRIOR-CONCEPTION-TEMPLATE-CENSUS.json").read_text())
NATIVE = {(row["origin"]["prompt_template"], row["origin"]["logical_role"])
          for row in CENSUS["completed_templates"]}

# Reviewed finite 22, independent of the configuration under test. Native
# request caps below come from the captured config or the reviewed literals.
REVIEWED = [
    ("conception_finalize_reader_first_v1", "editor", None, "medium"),
    ("conception_finalize_reader_first_v1_repair", "editor", None, "medium"),
    ("conception_story_spine_polish_reader_first_v1", "planner", None, "medium"),
    ("conception_story_spine_polish_reader_first_v1_repair", "planner", None, "medium"),
    ("conception_reader_first_repair_v1", "editor", 8192, "medium"),
    ("fanqie_reader_first_field_roles", "planner", None, "medium"),
    ("planner_reader_first_full_outline_commercial_surgical_repair", "planner", None, "medium"),
    ("conception_reader_first_skeleton_v1", "critic", 4096, "low"),
    ("planner_reader_first_outline_review", "critic", 4096, "low"),
    ("reader_revision_anchors", "critic", None, "max"),
    ("reader_revision_preservation", "critic", None, "max"),
    ("concept_tournament_candidate_reader_first_v1", "planner", 900, "low"),
    ("concept_tournament_engine_kernel_reader_first_v1", "planner", 8000, "low"),
    ("concept_tournament_raw_idea_pool_reader_first_v1", "planner", 9000, "low"),
    ("concept_tournament_seriality_reader_first_v1", "planner", 2200, "low"),
    ("concept_tournament_judge_reader_first_v1", "critic", 1800, "low"),
    ("concept_tournament_story_judge_reader_first_v1", "critic", 1600, "low"),
    ("concept_tournament_seriality_judge_reader_first_v1", "critic", 1800, "low"),
    ("concept_tournament_finalist_judge_reader_first_v1", "critic", 5000, "low"),
    ("concept_tournament_finalist_seriality_judge_reader_first_v1", "critic", 7000, "low"),
    ("concept_tournament_raw_idea_rank_reader_first_v1", "critic", 8000, "low"),
    ("concept_tournament_premise_judge_reader_first_v1", "critic", 8000, "low"),
]


SAME_NAME_NEW_BOOK = [
    ("chapter_first_writer", "writer", None, "medium"),
    ("planner_fanqie_beat_sheet", "planner", None, "medium"),
    ("planner_structured_field_correction", "planner", 8192, "medium"),
    ("chapter_rewrite", "editor", None, "medium"),
    ("chapter_rewrite_repair", "editor", None, "medium"),
    ("chapter_rewrite_quality_retrofit_repair", "editor", None, "medium"),
    ("scene_writer", "writer", None, "medium"),
    ("reader_judge", "critic", None, "max"),
]


def _role(name):
    # Whitelisted saved production values only; never load settings or env.
    return LLMRoleSettings(**CAPTURE["roles"][name])


def _request(name, role, cap=None):
    return llm.LLMCompletionRequest(
        logical_role=role, prompt_template=name, max_tokens_override=cap,
        system_prompt="Synthetic policy boundary; never sent.",
        user_prompt="Synthetic policy boundary; never sent.", fallback_response="{}",
    )


def test_finite_table_is_exact_actual18_union_reviewed22_plus_named8():
    assert len(NATIVE) == 18
    assert len(REVIEWED) == 22
    expected = NATIVE | {(name, role) for name, role, _, _ in REVIEWED + SAME_NAME_NEW_BOOK}
    actual = {(name, role) for name, entry in POLICY["templates"].items()
              for role in entry["roles"]}
    assert len(expected) == 40
    assert actual == expected
    assert POLICY["schema"] == "run2-new-book-template-policy.v1"
    assert set(POLICY) == {"schema", "templates", "audit"}
    for name, entry in POLICY["templates"].items():
        for role, row in entry["roles"].items():
            assert set(row) == {"effort", "cap", "reason"}, (name, role)
            assert row["effort"] in {"medium", "bounded_generation", "bounded_review"}
            assert row["cap"] == "effective_request"
            assert isinstance(row["reason"], str) and row["reason"].strip()


def test_finite_audit_resolves_explicit_policy_and_native_effective_caps():
    reviewed = {(name, role) for name, role, _, _ in REVIEWED}
    cases = REVIEWED + SAME_NAME_NEW_BOOK + [(name, role, None, "max" if role == "critic" else "medium")
                        for name, role in sorted(NATIVE - reviewed)]
    assert len(cases) == 40
    for name, logical_role, cap, expected in cases:
        role, request = _role(logical_role), _request(name, logical_role, cap)
        before = role.model_dump()
        result = strategy.resolve_run2_new_book_template_policy(request, role)
        assert result == {
            "effort": expected,
            "max_completion_tokens": llm._effective_request_max_tokens(role, request),
            "reason": POLICY["templates"][name]["roles"][logical_role]["reason"],
        }, (name, logical_role, cap)
        assert role.model_dump() == before, (name, logical_role)


@pytest.mark.parametrize("name", [
    "conception_story_spine_polish_reader_first_v1",
    "conception_story_spine_polish_reader_first_v1_repair",
])
def test_projectless_reader_first_spine_alias_no_longer_falls_into_max(name):
    role, request = _role("planner"), _request(name, "planner")
    result = strategy.apply_production_stream_settings(request, role, ambient_complete=True)
    cap = llm._effective_request_max_tokens(result, request)
    assert cap == 32768
    assert m31.reasoning_effort(result, max_completion_tokens=cap) == "medium", name
    assert result.model_dump() == role.model_copy(
        update={"reasoning_effort": "medium", "stream": True},
    ).model_dump()


def test_correct_narrow_and_independent_review_wires_keep_raw_role_identity():
    for name, logical_role, cap, expected in REVIEWED:
        if expected not in {"low", "max"}:
            continue
        role, request = _role(logical_role), _request(name, logical_role, cap)
        before = role.model_copy(update={"stream": True})
        result = strategy.apply_production_stream_settings(request, role, ambient_complete=True)
        assert result.model_dump() == before.model_dump(), name
        assert llm._effective_input_payload(request, result) == llm._effective_input_payload(
            request, before,
        ), name
        assert m31.reasoning_effort(
            result, max_completion_tokens=llm._effective_request_max_tokens(result, request),
        ) == expected, name


def test_native_project_reader_repair_keeps_medium_and_editor_floor():
    request = _request("conception_reader_first_repair_v1", "editor", 8192)
    role = _role("editor")
    result = strategy.apply_production_stream_settings(request, role, profile="complete")
    assert request.max_tokens_override == 8192
    assert result.max_tokens == role.max_tokens == 32768
    assert llm._effective_request_max_tokens(result, request) == 16000
    assert m31.reasoning_effort(result, max_completion_tokens=16000) == "medium"


def test_explicit_effort_takes_priority_in_audit_and_alias_apply():
    role = _role("planner").model_copy(update={"reasoning_effort": "high"})
    request = _request("conception_story_spine_polish_reader_first_v1", "planner")
    assert strategy.resolve_run2_new_book_template_policy(request, role)["effort"] == "high"
    result = strategy.apply_production_stream_settings(request, role, ambient_complete=True)
    assert result.reasoning_effort == "high"


def test_unknown_and_wrong_role_are_outside_audit_not_global_denials():
    for name, logical_role in [
        ("outside_run2_finite_table", "planner"),
        ("conception_story_spine_polish_reader_first_v1", "critic"),
    ]:
        role, request = _role(logical_role), _request(name, logical_role)
        assert strategy.resolve_run2_new_book_template_policy(request, role) is None
        result = strategy.apply_production_stream_settings(request, role, ambient_complete=True)
        assert result.model_dump() == role.model_copy(update={"stream": True}).model_dump()


@pytest.mark.parametrize("profile", [None, "research"])
def test_legacy_and_research_keep_exact_alias_inputs(profile):
    role = _role("planner")
    request = _request("conception_story_spine_polish_reader_first_v1", "planner")
    result = strategy.apply_production_stream_settings(request, role, profile=profile)
    assert result.model_dump() == role.model_dump()
    assert llm._effective_input_payload(request, result) == llm._effective_input_payload(request, role)


def test_actual_generic_identities_keep_prior_ambient_effective_inputs():
    reviewed = {(name, role) for name, role, _, _ in REVIEWED}
    generic = NATIVE - reviewed
    assert len(generic) == 10
    for name, logical_role in sorted(generic):
        role, request = _role(logical_role), _request(name, logical_role)
        before = role.model_copy(update={"stream": True})
        result = strategy.apply_production_stream_settings(request, role, ambient_complete=True)
        assert result.model_dump() == before.model_dump(), name
        assert llm._effective_input_payload(request, result) == llm._effective_input_payload(
            request, before,
        ), name
