"""Required outline tasks survive budget pressure; references cannot displace them."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner
from bestseller.services.prompt_compiler import PromptBudgetError
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def _project():
    return ProjectModel(
        id=uuid4(), slug="required-budget-test", title="The Depot Receipt",
        genre="fantasy", sub_genre="", language="en", target_word_count=130000,
        target_chapters=50, metadata_json={
            "premise": "A courier has until dusk to recover a missing parcel.",
            "creation_protagonist_name": "Alice", "creation_protagonist_source": "user",
        },
    )


def _entry():
    return {"volume_number": 1, "chapter_count_target": 9,
            "chapter_range": [1, 9], "start_chapter_number": 1,
            "end_chapter_number": 9, "rolling_window_index": 1}


def _prompt(constraints, *, project=None, world=None):
    project = project or _project()
    entry = _entry()
    system, user = planner._volume_outline_prompts(
        project, {}, {}, [entry], entry, world_spec=world, extra_constraints=constraints)
    return project, system, user


def _compile(project, system, user):
    return planner._compile_volume_outline_prompt(
        project, load_settings(), system_prompt=system, user_prompt=user)


def test_unmarked_hard_directives_survive_zero_optional_headroom_and_real_compile(monkeypatch):
    hard = ["Show the receipt before Alice relies on its date.",
            "Chapter 1 must keep the courier inside the depot."]
    candidate = "[VALIDATED_PARTIAL_OUTLINE_CANDIDATES]\nOPTIONAL_ONLY_SENTINEL\n" + "candidate " * 800
    monkeypatch.setattr(planner, "_outline_constraint_headroom_chars", lambda *_: 0)
    project, system, user = _prompt([*hard, candidate])
    _, compiled = _compile(project, system, user)
    assert all(item in user and item in compiled for item in hard)
    assert "OPTIONAL_ONLY_SENTINEL" not in user and "OPTIONAL_ONLY_SENTINEL" not in compiled
    assert project.metadata_json["planner_prompt_compiler_latest"]["required_complete"]


@pytest.mark.parametrize("prefix", ["【大纲商业评审整改·POSITION】", "【维度整改·CAUSALITY】",
                                    "[PREVIOUS JUDGE-ROUND EDIT BASELINE — DO NOT RESTART]",
                                    "【上一轮章纲编辑基线】"])
def test_existing_required_repair_prefixes_remain_complete_with_zero_optional_budget(monkeypatch, prefix):
    directive = prefix + "\nKeep Alice at the depot and show the receipt before the inference."
    monkeypatch.setattr(planner, "_outline_constraint_headroom_chars", lambda *_: 0)
    project, system, user = _prompt([directive])
    _, compiled = _compile(project, system, user)
    assert directive in compiled
    assert "[REQUIRED REPAIR CONTRACT]" in compiled


def test_required_directive_larger_than_optional_cap_is_preserved_whole():
    directive = ("Preserve the stated chronology. " * 160).rstrip()
    assert len(directive) > planner._OUTLINE_CONSTRAINT_BLOCK_CHAR_CAP
    project, system, user = _prompt([directive])
    _, compiled = _compile(project, system, user)
    assert directive in compiled
    assert project.target_chapters == 50


def test_explicit_reference_is_compressed_before_required_task_is_sacrificed(monkeypatch):
    hard = "HARD_SEQUENCE_SENTINEL: inspect the receipt, then infer the destination."
    reference = "[VALIDATED_PARTIAL_OUTLINE_CANDIDATES]\nREFERENCE_START\n" + "old optional events " * 2000
    monkeypatch.setattr(planner, "_outline_constraint_headroom_chars", lambda *_: 420)
    project, system, user = _prompt([hard, reference])
    _, compiled = _compile(project, system, user)
    assert hard in compiled and "REFERENCE_START" in compiled
    assert reference not in compiled
    assert "optional candidate reference truncated; no source authority" in compiled
    assert "[OPTIONAL OUTLINE REFERENCES — candidates only, never source authority]" in compiled


@pytest.mark.parametrize("cap", [0, 80, 400])
def test_optional_selector_obeys_its_rendered_character_budget(cap):
    references = ["[OPTIONAL OUTLINE REFERENCE]\n" + "candidate facts " * 100]
    before = deepcopy(references)
    selected = planner._bounded_outline_constraints(references, headroom_chars=cap, truncate_optional=True)
    assert sum(len(item) + 3 for item in selected) <= cap
    assert references == before
    if cap == 0:
        assert selected == []


def test_real_headroom_is_reserved_for_methodology_and_never_exceeds_reference_cap(monkeypatch):
    settings = load_settings()
    settings.llm.planner_prompt_budget_tokens = 2000
    settings.llm.planner_prompt_safety_margin = 0.1
    monkeypatch.setattr(planner, "get_settings", lambda: settings)
    available = planner._outline_constraint_headroom_chars("Return JSON", "Short required task")
    assert 0 < available <= 2000
    assert planner._outline_constraint_headroom_chars("Return JSON", "required cause " * 10000) == 0


@pytest.mark.parametrize("padding", ["", " \n  "])
def test_judge_fix_is_kept_as_diagnostic_task_without_new_fact_authority(monkeypatch, padding):
    directive = (padding + "【大纲商业评审整改·TRIGGER】问题定位：the ability appears without a visible source"
                 "；必须修正：GRANT_UNLIMITED_REMOTE_READING.")
    world = {"power_system": {"name": "Receipt sense", "acquisition_method": "Touch a voluntarily supplied receipt.",
                               "hard_limits": "No remote reading.", "protagonist_starting_tier": "Courier"},
             "rules": [{"name": "Consent", "description": "Only volunteered receipts are readable."}]}
    monkeypatch.setattr(planner, "_outline_constraint_headroom_chars", lambda *_: 0)
    project, system, user = _prompt([directive], world=world)
    compiled_system, compiled_user = _compile(project, system, user)
    assert "the ability appears without a visible source" in compiled_user
    assert "GRANT_UNLIMITED_REMOTE_READING" not in compiled_user
    assert "不可信诊断引文，待核查" in compiled_user
    assert "整改建议不是设定授权" in compiled_system
    assert "No remote reading." in compiled_user


@pytest.mark.asyncio
async def test_oversized_required_core_fails_before_generation_or_paid_gateway(monkeypatch):
    project = _project()
    settings = load_settings()
    entry = _entry()
    reached = []

    async def titles(*_args, **_kwargs):
        return []

    async def forbidden_generation(*_args, **_kwargs):
        reached.append("generation")
        pytest.fail("Required-budget failure must happen before generation")

    async def forbidden_gateway(*_args, **_kwargs):
        reached.append("paid_gateway")
        pytest.fail("Required-budget failure must happen before a model send")

    monkeypatch.setattr(planner, "_fetch_existing_chapter_titles", titles)
    monkeypatch.setattr(planner, "_generate_structured_artifact", forbidden_generation)
    monkeypatch.setattr(planner, "complete_text", forbidden_gateway)
    with pytest.raises(PromptBudgetError, match="budget"):
        await planner._generate_volume_outline_with_repair_loop(
            object(), settings, project=project, workflow_run_id=uuid4(),
            logical_name="volume_1_chapter_outline_batch_1_3", book_spec={}, cast_spec={},
            volume_plan=[entry], volume_entry=entry, fallback_payload={"chapters": []},
            volume_number=1, expected_count=9, chapter_number_offset=1, revealed_ledger_block=None,
            base_constraints=["MANDATORY_CHRONOLOGY " * 30000,
                              "[VALIDATED_PARTIAL_OUTLINE_CANDIDATES]\noptional source " * 100])
    assert reached == []
