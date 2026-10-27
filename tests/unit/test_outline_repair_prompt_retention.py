from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def _prompt(constraints: list[str]) -> tuple[ProjectModel, str, str]:
    project = ProjectModel(
        id=uuid4(),
        slug="repair-retention",
        title="Test",
        genre="fantasy",
        language="en",
        target_word_count=7800,
        target_chapters=3,
        metadata_json={"creation_protagonist_name": "Alice", "creation_protagonist_source": "user"},
    )
    entry = {"volume_number": 1, "chapter_count_target": 3, "chapter_range": [1, 3]}
    system, user = planner._volume_outline_prompts(
        project,
        {},
        {},
        [entry],
        entry,
        extra_constraints=constraints,
    )
    return project, system, user


def test_oversized_reference_does_not_erase_earlier_fitting_directive() -> None:
    kept = planner._bounded_outline_constraints(["repair-choice", "x" * 5000, "batch-range"])
    assert "repair-choice" in kept
    assert "batch-range" in kept
    assert sum(len(item) + 3 for item in kept) <= 4000


def test_repair_and_baseline_survive_real_prompt_compilation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directive = "【大纲商业评审整改·KNOWLEDGE_BOUNDARY】必须修正：show how Alice learns the secret."  # noqa: RUF001 -- production marker
    baseline = (
        "[PREVIOUS JUDGE-ROUND EDIT BASELINE — DO NOT RESTART]\n"
        + ('{"fact":"preserve-me"}\n' * 210).rstrip()
    )
    monkeypatch.setattr(planner, "_outline_constraint_headroom_chars", lambda *_: 0)
    project, system, user = _prompt([directive, baseline, "[OPTIONAL OUTLINE REFERENCE]\nauxiliary-hint"])
    assert directive in user
    assert baseline in user
    assert "auxiliary-hint" not in user
    settings = load_settings(env={})
    _, compiled = planner._compile_volume_outline_prompt(
        project, settings, system_prompt=system, user_prompt=user
    )
    assert directive in compiled
    assert baseline in compiled
    report = project.metadata_json["planner_prompt_compiler_latest"]
    assert report["required_complete"]
    assert report["total_tokens"] <= report["usable_budget_tokens"]


def test_impossible_required_repair_budget_fails_instead_of_dropping() -> None:
    directive = "【大纲商业评审整改·X】" + "fix this cause. " * 30000
    project, system, user = _prompt([directive])
    assert directive in user
    settings = load_settings(env={})
    with pytest.raises(ValueError, match="budget"):
        planner._compile_volume_outline_prompt(
            project, settings, system_prompt=system, user_prompt=user
        )


def test_joined_chinese_repair_and_english_context_respect_total_budget() -> None:
    project, _, _ = _prompt([])
    task = "请仅生成第1卷。" + "修正人物行动并保留知识边界。" * 900
    _, compiled = planner._compile_volume_outline_prompt(
        project,
        load_settings(env={}),
        system_prompt="Return JSON.",
        user_prompt="reference context " * 1000 + task,
    )
    assert task in compiled
    report = project.metadata_json["planner_prompt_compiler_latest"]
    assert report["total_tokens"] <= report["usable_budget_tokens"]
