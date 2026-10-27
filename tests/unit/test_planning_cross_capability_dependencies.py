"""Selecting one planning method cannot authorize a cached StoryDesign method."""

from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

from planning_capability_test_helpers import explicit_planning_metadata
import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.services import emotion_kernel_backfill as emotion
from bestseller.services import entry_system_backfill as entry
from bestseller.services import planner

pytestmark = pytest.mark.unit

STORY = {
    "reader_promise": "STORY_METHOD_ONLY_PROMISE",
    "premise_contract": {"unique_hook": "STORY_METHOD_ONLY_HOOK", "core_question": "STORY_METHOD_ONLY_QUESTION"},
    "beat_schedule": [{"payoff": "STORY_METHOD_ONLY_BEAT"}],
    "change_vectors": ["STORY_METHOD_ONLY_VECTOR"],
    "shape": {"ending_contract": "STORY_METHOD_ONLY_TRAGIC_BE"},
}
FACTS = {
    "premise": "Two observers calibrate their telescope before the rain.",
    "unique_hook": "AUTHOR_CONTROL_HOOK",
    "book_spec": {
        "reader_promise": "BOOK_FACT_READER_PROMISE",
        "unique_hook": "BOOK_FACT_HOOK", "central_conflict": "BOOK_FACT_CONFLICT",
        "protagonist": {"name": "Book observer", "goal": "Book goal"},
    },
    "world_spec": {
        "rules": [{"name": "WORLD_FACT_RULE", "visible_cost": "WORLD_FACT_COST"}],
        "power_system": {"name": "WORLD_FACT_INSTRUMENTS", "artifacts": [{"name": "WORLD_FACT_LENS"}]},
    },
    "cast_spec": {"protagonist": {"name": "CAST_FACT_OBSERVER", "goal": "CAST_FACT_GOAL"}},
    "author_story": {"unknown": {"_meta": "literal story information"}},
    "canonical_identity": {"name": "Original identity", "unknown": [1, {"literal": True}]},
}
CASES = (
    (emotion, "ensure_project_emotion_driven_kernel", "emotion_driven_kernel"),
    (entry, "ensure_project_entry_system_compat", "entry_system_kernel"),
)


def _project(capability="emotion_driven_kernel", *, story_selection="absent", cached_story=True, language="en"):
    data = explicit_planning_metadata(capability, metadata=FACTS)
    selected = data["creation_intent_contract"]["hook_spec"]["planning_capabilities"]["selected"]
    if story_selection != "absent":
        selected["story_design_kernel"] = story_selection == "selected"
    if cached_story:
        data["story_design_kernel"] = deepcopy(STORY)
    # Neither an old receipt nor a genre/effect cache is author selection.
    data["planning_capabilities"] = {"story_design_kernel": {"selected": True, "source": "user"}}
    data["hype_satisfaction_engine"] = {"enabled": True}
    return SimpleNamespace(
        id=uuid4(), slug="cross-capability-test", title="Observation", genre="contemporary",
        sub_genre=None, language=language, target_chapters=12, target_word_count=30000,
        audience="adult", dramatic_question="What did the instrument detect?",
        theme_statement="Cooperation", metadata_json=data,
    )


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _forbidden(*args, **kwargs):
    raise AssertionError("No model, database, or artifact write was authorized by this fixture")


@pytest.fixture(autouse=True)
def no_paid_or_artifact_writes(monkeypatch):
    from bestseller.services import llm

    monkeypatch.setattr(llm, "complete_text", _forbidden)
    monkeypatch.setattr(planner, "complete_text", _forbidden)
    for module, _, _ in CASES:
        monkeypatch.setattr(module, "import_planning_artifact", _forbidden)


@pytest.mark.parametrize("selection", ["absent", "unselected", "selected"])
@pytest.mark.parametrize("supplied", [False, True])
@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_actual_fallback_obeys_story_selection_for_argument_and_cache(selection, supplied, language):
    project = _project(story_selection=selection, language=language)
    before = deepcopy(project.metadata_json)
    supplied_story = {**deepcopy(STORY), "reader_promise": "SUPPLIED_STORY_METHOD_ONLY_PROMISE"}
    original_argument = deepcopy(supplied_story)
    result = planner._fallback_emotion_driven_kernel(
        project, FACTS["premise"], deepcopy(FACTS["book_spec"]), deepcopy(FACTS["world_spec"]),
        deepcopy(FACTS["cast_spec"]), story_design_kernel=supplied_story if supplied else None,
    )
    if selection == "selected":
        assert result["reader_emotion_promise"] == (
            supplied_story["reader_promise"] if supplied else STORY["reader_promise"])
        assert "STORY_METHOD_ONLY_HOOK" in _json(result)
        assert "STORY_METHOD_ONLY_BEAT" in _json(result)
        assert result["ending_texture_contract"]["ending_type"] == "BE"
    else:
        assert "STORY_METHOD_ONLY" not in _json(result)
        assert result["reader_emotion_promise"] == "BOOK_FACT_READER_PROMISE"
        assert "AUTHOR_CONTROL_HOOK" in _json(result)
        assert result["ending_texture_contract"]["ending_type"] == "HE"
    assert "CAST_FACT_OBSERVER" in _json(result)
    assert "CAST_FACT_GOAL" in _json(result)
    assert "WORLD_FACT_RULE" in _json(result)
    assert "WORLD_FACT_COST" in _json(result)
    assert "BOOK_FACT_CONFLICT" in _json(result)
    assert project.metadata_json == before
    assert supplied_story == original_argument


@pytest.mark.asyncio
@pytest.mark.parametrize("module,ensure_name,capability", CASES)
@pytest.mark.parametrize("selection", ["absent", "unselected", "selected"])
@pytest.mark.parametrize("source", ["metadata", "artifact"])
@pytest.mark.parametrize("force", [False, True])
async def test_actual_backfill_gates_cross_method_reads_and_preserves_facts(
    monkeypatch, module, ensure_name, capability, selection, source, force,
):
    project = _project(capability, story_selection=selection, cached_story=source == "metadata")
    before = deepcopy(project.metadata_json)
    calls, forwarded = [], []
    artifacts = {
        ArtifactType.STORY_DESIGN_KERNEL: deepcopy(STORY),
        ArtifactType.BOOK_SPEC: deepcopy(FACTS["book_spec"]),
        ArtifactType.WORLD_SPEC: deepcopy(FACTS["world_spec"]),
        ArtifactType.CAST_SPEC: deepcopy(FACTS["cast_spec"]),
        ArtifactType.PREMISE: {"premise": FACTS["premise"]},
    }
    if source == "artifact" and module is emotion:
        for key in ("book_spec", "world_spec", "cast_spec"):
            project.metadata_json.pop(key)
        before = deepcopy(project.metadata_json)

    async def read_artifact(session, *, project_id, artifact_type):
        assert project_id == project.id
        calls.append(artifact_type)
        if artifact_type == ArtifactType.STORY_DESIGN_KERNEL:
            assert selection == "selected", "unselected StoryDesign artifact was queried"
        value = artifacts.get(artifact_type)
        return SimpleNamespace(content=deepcopy(value)) if value is not None else None

    monkeypatch.setattr(module, "get_latest_planning_artifact", read_artifact)
    dependency = ("build_emotion_driven_kernel_backfill_payload" if module is emotion
                  else "build_fallback_entry_system_kernel")
    original = getattr(module, dependency)

    def observe_real_dependency(*args, **kwargs):
        forwarded.append(deepcopy(kwargs.get("story_design_kernel")))
        return original(*args, **kwargs)

    monkeypatch.setattr(module, dependency, observe_real_dependency)
    result = await getattr(module, ensure_name)(object(), project, force=force)
    assert result.status == ("forced" if force else "created")
    assert result.kernel is not None
    assert forwarded == [STORY if selection == "selected" else {}]
    assert calls.count(ArtifactType.STORY_DESIGN_KERNEL) == int(selection == "selected" and source == "artifact")
    audit = project.metadata_json["emotion_driven_kernel_backfill" if module is emotion else "entry_system_backfill"]
    assert audit["used_metadata"]["story_design_kernel"] == (selection == "selected" and source == "metadata")
    assert bool(audit["used_artifact_fallback"]["story_design_kernel"]) == (
        selection == "selected" and source == "artifact")
    if selection != "selected":
        assert "STORY_METHOD_ONLY" not in _json(result.kernel)
    if module is emotion:
        assert result.kernel["reader_emotion_promise"] == (
            STORY["reader_promise"] if selection == "selected" else FACTS["book_spec"]["reader_promise"])
        assert "CAST_FACT_OBSERVER" in _json(result.kernel)
        assert "WORLD_FACT_RULE" in _json(result.kernel)
        assert "BOOK_FACT_CONFLICT" in _json(result.kernel)
        if source == "artifact":
            assert {ArtifactType.BOOK_SPEC, ArtifactType.WORLD_SPEC, ArtifactType.CAST_SPEC} <= set(calls)
    else:
        assert "WORLD_FACT_LENS" in _json(result.registry)
    # Changes are additive to the selected method; raw caches and independent
    # canon, unknown nested story fields and author controls remain exact.
    assert {key: project.metadata_json[key] for key in before} == before
    assert artifacts[ArtifactType.STORY_DESIGN_KERNEL] == STORY
