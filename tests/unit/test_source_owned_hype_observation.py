"""Full and prefix source plans survive the real stamp/classifier consumer."""


from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest
from test_selected_prefix_hype_observation import (
    HIT,
    KEY,
    OTHER_HIT,
    QUIET,
    NoGateway,
    subject,
)
from test_selected_prefix_hype_observation import (
    budget_io as budget_io,
)

from bestseller.services import hype_engine
from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash
from bestseller.services.drafts import stamp_chapter_hype
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
FIELDS = ("hype_type", "hype_intensity", "hype_recipe_key")
MARKERS = ("outline_source_admission", "outline_prefix_readiness")


def source_bound(*, markers=MARKERS[:1], planned=False, title="港口来信", number=2):
    project, chapter, scene = subject(
        selected=False, planned=planned, title=title, number=number
    )
    source = {
        "chapters": [{
            "chapter_number": number,
            "title": title,
            "chapter_goal": chapter.chapter_goal,
            **{key: getattr(chapter, key) for key in FIELDS},
            "scenes": [{"scene_number": scene.scene_number, "purpose": scene.purpose}],
        }],
    }
    projection = outline_prefix_projection_hash(chapter, [scene])
    marker = {
        "artifact_id": "9b80c8c1-dcc1-4f54-98c2-2851c03b45de",
        "workflow_run_id": "c9da2396-2f43-4e8a-9252-97d466d51ff7",
        "projection_hash": projection,
        "source_content_sha256": content_hash(source),
    }
    chapter.metadata_json = {
        **chapter.metadata_json,
        **{key: deepcopy(marker) for key in markers},
    }
    return project, chapter, scene, source


@pytest.mark.parametrize("markers", [MARKERS[:1], MARKERS[1:], MARKERS])
@pytest.mark.parametrize("planned", [False, True])
@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize(
    "title,number,text",
    [("旧港调查", 1, HIT), ("山门来客", 9, OTHER_HIT), ("雨夜交接", 17, HIT)],
)
async def test_source_plan_and_original_source_hash_remain_exact(
    markers, planned, refresh, title, number, text, budget_io
):
    project, chapter, scene, source = source_bound(
        markers=markers, planned=planned, title=title, number=number
    )
    old_source, old_meta = deepcopy(source), deepcopy(chapter.metadata_json)
    fields = {key: getattr(chapter, key) for key in FIELDS}
    projection, source_sha = outline_prefix_projection_hash(chapter, [scene]), content_hash(source)
    expected = hype_engine.classify_hype(text, language=project.language, segment="tail")
    assert expected is not None
    for _ in range(2):
        await stamp_chapter_hype(
            NoGateway(), chapter=chapter, chapter_number=number,
            content_md=text, project=project, refresh=refresh,
        )
    observation = chapter.metadata_json[KEY]
    assert {key: getattr(chapter, key) for key in FIELDS} == fields
    assert outline_prefix_projection_hash(chapter, [scene]) == projection
    assert source == old_source and content_hash(source) == source_sha
    assert {key: value for key, value in chapter.metadata_json.items() if key != KEY} == old_meta
    assert observation["planned_values"] == fields
    assert observation["body_sha256"] == sha256(text.encode()).hexdigest()
    assert observation["result"] == {
        "hype_type": expected[0].value,
        "hype_intensity": expected[1],
        "hype_recipe_key": None,
    }
    assert observation["quality_verdict"] == "not_evaluated"
    assert observation["adoption_authorized"] is False
    assert len(observation["history"]) == 1
    budgets, saves = budget_io
    assert saves == ([] if refresh else [project.id])
    if not refresh:
        assert len(budgets[project.id].hype_moments) == 1


@pytest.mark.parametrize("marker_key", MARKERS)
@pytest.mark.parametrize("marker_value", [None, {}, [], False, "broken"])
async def test_malformed_marker_presence_still_protects_null_plan(
    marker_key, marker_value, budget_io
):
    project, chapter, scene, _source = source_bound(markers=())
    chapter.metadata_json = {**chapter.metadata_json, marker_key: deepcopy(marker_value)}
    before = outline_prefix_projection_hash(chapter, [scene])
    await stamp_chapter_hype(
        NoGateway(), chapter=chapter, chapter_number=2, content_md=HIT, project=project
    )
    assert all(getattr(chapter, key) is None for key in FIELDS)
    assert outline_prefix_projection_hash(chapter, [scene]) == before
    assert chapter.metadata_json[marker_key] == marker_value
    assert chapter.metadata_json[KEY]["adoption_authorized"] is False


async def test_full_plan_refresh_observation_history_and_budget_remain_independent(budget_io):
    project, chapter, scene, _source = source_bound(planned=True)
    chapter.metadata_json = {
        **chapter.metadata_json,
        "assigned_hype": {"type": "face_slap", "intensity": 10.0, "recipe_key": "proposal"},
    }
    before = outline_prefix_projection_hash(chapter, [scene])
    calls = [(HIT, False), (OTHER_HIT, True), (QUIET, True), (QUIET, True)]
    for text, refresh in calls:
        await stamp_chapter_hype(
            NoGateway(), chapter=chapter, chapter_number=2, content_md=text,
            project=project, refresh=refresh,
            scene_drafts=[SimpleNamespace(generation_params={"assigned_hype_type": "face_slap"})],
        )
    observation = chapter.metadata_json[KEY]
    assert outline_prefix_projection_hash(chapter, [scene]) == before
    assert tuple(getattr(chapter, key) for key in FIELDS) == ("status_jump", 8.0, "approved-recipe")
    assert observation["status"] == "unclassified"
    assert all(value is None for value in observation["result"].values())
    assert len(observation["history"]) == 3
    assert [item["body_sha256"] for item in observation["history"]] == [
        sha256(text.encode()).hexdigest() for text in (HIT, OTHER_HIT, QUIET)
    ]
    assert "hype_regressions" not in chapter.metadata_json
    assert budget_io[1] == [project.id]


async def test_full_plan_classifier_failure_preserves_plan_and_prior_evidence(
    budget_io, monkeypatch
):
    project, chapter, scene, _source = source_bound(planned=True)
    prior = {"migration": {"old_observed_hype_type": "golden_finger_reveal"}}
    chapter.metadata_json = {**chapter.metadata_json, KEY: deepcopy(prior)}
    before = outline_prefix_projection_hash(chapter, [scene])

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("private diagnostic")

    monkeypatch.setattr(hype_engine, "classify_hype", unavailable)
    await stamp_chapter_hype(
        NoGateway(), chapter=chapter, chapter_number=2, content_md=HIT,
        project=project, refresh=True,
    )
    observation = chapter.metadata_json[KEY]
    assert observation["status"] == "unavailable"
    assert observation["history"][0] == prior
    assert observation["error_type"] == "RuntimeError"
    assert "private diagnostic" not in str(observation)
    assert outline_prefix_projection_hash(chapter, [scene]) == before
    assert not budget_io[1]
