"""Real stamp/classifier and prefix hash; no database or model gateways."""
# ruff: noqa: RUF001 -- Preserve real Chinese classifier fixture punctuation.

from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ChapterModel, SceneCardModel
from bestseller.services import diversity_budget, hype_engine
from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash
from bestseller.services.drafts import stamp_chapter_hype

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
KEY = "deterministic_audit_hype_observation"
HIT = "他把令牌放在桌上。长老脸色铁青，当众打脸，满堂对手僵住，无人再敢拦路。"
OTHER_HIT = "新弟子境界突破，修为暴涨，晋级之后终于踏入下一层。"
QUIET = "她合上信封，把窗台上的花盆搬进屋里。雨滴顺着玻璃滑下来。" * 10


class NoGateway:
    def __getattr__(self, name):
        raise AssertionError(f"Unexpected database/model gateway: {name}")


def subject(*, selected=True, planned=False, title="海港来信", number=2):
    project = SimpleNamespace(id=uuid4(), language="zh-CN", title=title)
    chapter = ChapterModel(
        id=uuid4(),
        project_id=project.id,
        chapter_number=number,
        title=title,
        chapter_goal="完成约定的交接",
        target_word_count=2600,
        hype_type="status_jump" if planned else None,
        hype_intensity=8.0 if planned else None,
        hype_recipe_key="approved-recipe" if planned else None,
        metadata_json={"causal_contract": {"goal": "交接"}, "unrelated_history": ["keep"]},
    )
    scene = SceneCardModel(
        id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        scene_number=1,
        scene_type="dialogue",
        title="门前",
        participants=["来客"],
        purpose="交接",
        entry_state="持信",
        exit_state="信已交付",
        target_word_count=2600,
        metadata_json={"action_sequence": ["递信", "接信"]},
    )
    if selected:
        chapter.metadata_json = {
            **chapter.metadata_json,
            "outline_prefix_readiness": {
                "artifact_id": str(uuid4()),
                "workflow_run_id": str(uuid4()),
                "projection_hash": outline_prefix_projection_hash(chapter, [scene]),
            },
        }
    return project, chapter, scene


@pytest.fixture
def budget_io(monkeypatch):
    budgets, saves = {}, []

    async def load(_session, project_id):
        return budgets.setdefault(
            project_id, diversity_budget.DiversityBudget(project_id=project_id)
        )

    async def save(_session, budget):
        saves.append(budget.project_id)

    monkeypatch.setattr(diversity_budget, "load_diversity_budget", load)
    monkeypatch.setattr(diversity_budget, "save_diversity_budget", save)
    return budgets, saves


@pytest.mark.parametrize(
    "title,number,text",
    [
        ("海港来信", 2, HIT),
        ("山门新客", 9, OTHER_HIT),
        ("旧站的回声", 17, HIT),
    ],
)
@pytest.mark.parametrize("planned", [False, True])
async def test_selected_plans_hash_and_marker_stay_exact_with_actual_classification(
    title,
    number,
    text,
    planned,
    budget_io,
):
    project, chapter, scene = subject(title=title, number=number, planned=planned)
    original = deepcopy(chapter.metadata_json)
    fields = (chapter.hype_type, chapter.hype_intensity, chapter.hype_recipe_key)
    before = outline_prefix_projection_hash(chapter, [scene])
    expected = hype_engine.classify_hype(text, language=project.language, segment="tail")
    assert expected is not None
    await stamp_chapter_hype(
        NoGateway(), chapter=chapter, chapter_number=number, content_md=text, project=project
    )
    observation = chapter.metadata_json[KEY]
    assert (chapter.hype_type, chapter.hype_intensity, chapter.hype_recipe_key) == fields
    assert outline_prefix_projection_hash(chapter, [scene]) == before
    assert {k: v for k, v in chapter.metadata_json.items() if k != KEY} == original
    assert observation["body_sha256"] == sha256(text.encode()).hexdigest()
    assert observation["result"]["hype_type"] == expected[0].value
    assert observation["result"]["hype_intensity"] == expected[1]
    assert observation["planned_values"] == dict(
        zip(("hype_type", "hype_intensity", "hype_recipe_key"), fields, strict=True)
    )
    assert observation["status"] == "classified"
    assert observation["quality_verdict"] == "not_evaluated"
    budgets, saves = budget_io
    assert saves == [project.id]
    assert budgets[project.id].hype_moments[-1].hype_type == expected[0]


async def test_refresh_clears_only_observation_and_retains_exact_history(budget_io):
    project, chapter, scene = subject(planned=True)
    before = outline_prefix_projection_hash(chapter, [scene])
    await stamp_chapter_hype(
        NoGateway(), chapter=chapter, chapter_number=2, content_md=HIT, project=project
    )
    first = deepcopy(chapter.metadata_json[KEY])
    await stamp_chapter_hype(
        NoGateway(),
        chapter=chapter,
        chapter_number=2,
        content_md=QUIET,
        project=project,
        refresh=True,
    )
    current = chapter.metadata_json[KEY]
    assert current["status"] == "unclassified"
    assert all(value is None for value in current["result"].values())
    assert current["quality_verdict"] == "not_evaluated"
    assert current["history"][0] == first["history"][0]
    assert len(current["history"]) == 2
    assert (chapter.hype_type, chapter.hype_intensity, chapter.hype_recipe_key) == (
        "status_jump",
        8.0,
        "approved-recipe",
    )
    assert outline_prefix_projection_hash(chapter, [scene]) == before
    assert "hype_regressions" not in chapter.metadata_json
    await stamp_chapter_hype(
        NoGateway(),
        chapter=chapter,
        chapter_number=2,
        content_md=QUIET,
        project=project,
        refresh=True,
    )
    assert chapter.metadata_json[KEY] == current
    assert len(budget_io[1]) == 1


async def test_selected_assignment_is_not_substituted_for_observed_prose(budget_io, monkeypatch):
    project, chapter, _scene = subject(planned=True)
    chapter.metadata_json = {
        **chapter.metadata_json,
        "assigned_hype": {"type": "status_jump", "intensity": 9.0, "recipe_key": "proposal"},
    }
    calls = []
    actual = hype_engine.classify_hype

    def observed(text, **kw):
        calls.append(text)
        return actual(text, **kw)

    monkeypatch.setattr(hype_engine, "classify_hype", observed)
    await stamp_chapter_hype(
        NoGateway(),
        chapter=chapter,
        chapter_number=2,
        content_md=QUIET,
        project=project,
        scene_drafts=[SimpleNamespace(generation_params={"assigned_hype_type": "face_slap"})],
    )
    assert calls == [QUIET]
    assert chapter.metadata_json[KEY]["status"] == "unclassified"
    assert chapter.hype_type == "status_jump"
    assert not budget_io[1]


async def test_classifier_error_is_unavailable_not_success(budget_io, monkeypatch):
    project, chapter, scene = subject(planned=True)
    before = outline_prefix_projection_hash(chapter, [scene])

    def broken(*_args, **_kw):
        raise RuntimeError("secret diagnostic")

    monkeypatch.setattr(hype_engine, "classify_hype", broken)
    await stamp_chapter_hype(
        NoGateway(),
        chapter=chapter,
        chapter_number=2,
        content_md=HIT,
        project=project,
        refresh=True,
    )
    observation = chapter.metadata_json[KEY]
    assert observation["status"] == "unavailable"
    assert observation["error_type"] == "RuntimeError"
    assert "secret" not in str(observation)
    assert observation["quality_verdict"] == "not_evaluated"
    assert outline_prefix_projection_hash(chapter, [scene]) == before


async def test_unselected_legacy_still_stamps_and_refresh_clears(budget_io):
    project, chapter, _scene = subject(selected=False)
    await stamp_chapter_hype(
        NoGateway(), chapter=chapter, chapter_number=2, content_md=HIT, project=project
    )
    assert chapter.hype_type == hype_engine.classify_hype(HIT, segment="tail")[0].value
    assert KEY not in chapter.metadata_json
    await stamp_chapter_hype(
        NoGateway(),
        chapter=chapter,
        chapter_number=2,
        content_md=QUIET,
        project=project,
        refresh=True,
    )
    assert chapter.hype_type is None and chapter.hype_intensity is None
    assert chapter.metadata_json["hype_regressions"]


async def test_migration_observation_is_preserved_before_new_body(budget_io):
    project, chapter, _scene = subject()
    prior = {"raw_observed_before_restore": {"hype_type": "face_slap", "hype_intensity": 1.0}}
    chapter.metadata_json = {**chapter.metadata_json, KEY: deepcopy(prior)}
    await stamp_chapter_hype(
        NoGateway(), chapter=chapter, chapter_number=2, content_md=HIT, project=project
    )
    assert chapter.metadata_json[KEY]["history"][0] == prior


async def test_null_plan_repeated_resume_classifies_without_duplicate_budget(
    budget_io, monkeypatch
):
    project, chapter, _scene = subject()
    actual = hype_engine.classify_hype
    calls = []

    def observed(text, **kw):
        calls.append(text)
        return actual(text, **kw)

    monkeypatch.setattr(hype_engine, "classify_hype", observed)
    for _ in range(2):
        await stamp_chapter_hype(
            NoGateway(),
            chapter=chapter,
            chapter_number=2,
            content_md=HIT,
            project=project,
            refresh=False,
        )
    assert calls == [HIT, HIT]
    assert chapter.hype_type is None
    assert len(chapter.metadata_json[KEY]["history"]) == 1
    assert len(budget_io[0][project.id].hype_moments) == len(budget_io[1]) == 1
