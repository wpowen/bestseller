import json
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services import outline_llm_judge, planner
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def _project() -> ProjectModel:
    return ProjectModel(
        id=uuid4(), slug="authority-test", title="A test book", genre="fantasy",
        language="en", target_word_count=7800, target_chapters=3,
        metadata_json={
            "premise": "Approved premise. " * 800,
            "creation_protagonist_name": "Mira",
            "story_enhancers": {"cost_style": "minimal"},
            "book_design_snapshot_status": "locked",
            "book_design_snapshot": {
                "snapshot_id": "current", "source_hash": "verified",
                "protagonist": {"name": "Mira"},
            },
            "concept_contract": {"hook_card": {"protagonist": "DiscardedAlias"}},
            "creation_intent_contract": {"tone": "light", "effects": ["comedy"]},
            "world_spec": {
                "power_system": {
                    "name": "Echo sense",
                    "acquisition_method": "Touching a shard reveals the holder's last fear.",
                    "hard_limits": "No memories or distant targets; shard must remain intact.",
                    "protagonist_starting_tier": "Listener",
                    "tiers": ["Listener", "Future oracle"],
                },
                "rules": [{"name": "Port charter", "description": "Only guild votes set fees."}],
                "locations": [{"name": "Unrelated large location material"}],
            },
        },
    )


def _brief(project):
    return planner._outline_judge_project_brief(
        project, metadata=project.metadata_json, semantic_candidates=[]
    )


def _assert_authority(prompt, project):
    world = project.metadata_json["world_spec"]
    for field in ("acquisition_method", "hard_limits", "protagonist_starting_tier"):
        assert world["power_system"][field] in prompt
    assert "Only guild votes set fees." in prompt
    assert "DiscardedAlias" not in prompt


def test_brief_uses_current_world_not_discarded_concept():
    project = _project()
    assert planner._source_bound_cast_enabled(project)
    brief = _brief(project)
    authority = brief["approved_world_authority"]
    assert "tiers" not in authority["power_system"]
    assert authority["progression_reference_not_current_authorization"]["tiers"] == [
        "Listener", "Future oracle"
    ]
    assert "locations" not in authority
    _assert_authority(json.dumps(brief), project)


@pytest.mark.parametrize("tag_source,user_tags,expected", [
    ("user", [], []), ("user", ["creator-chosen-tag"], ["creator-chosen-tag"]),
    ("legacy_inferred", ["unverified-tag"], []),
])
def test_taxonomy_defaults_are_references_not_approved_creator_obligations(tag_source, user_tags, expected):
    from copy import deepcopy

    project = _project()
    project.metadata_json["genre_intent_contract"] = {
        "source": "user", "tag_source": tag_source, "genre_label": "Fantasy",
        "tags": ["taxonomy-default", *user_tags], "default_tags": ["taxonomy-default"],
        "user_tags": user_tags, "prompt_pack_key": "taxonomy-routed-pack",
        "explicit_enhancers": {"effect_skills": ["comedy_engine"]},
    }
    project.metadata_json["creation_intent_contract"] = {
        "field_sources": {"genre_intent": "explicit"},
        "genre_intent": deepcopy(project.metadata_json["genre_intent_contract"]),
    }
    before = deepcopy(project.metadata_json)
    brief = _brief(project)
    approved = brief["approved_creation_contracts"]["genre_intent_contract"]
    assert approved["tags"] == expected
    assert "default_tags" not in approved and "prompt_pack_key" not in approved
    assert "explicit_enhancers" not in approved
    assert brief["genre_taxonomy_reference"]["default_tags"] == ["taxonomy-default"]
    assert brief["genre_taxonomy_reference"]["authority"].startswith("taxonomy_reference_only")
    assert project.metadata_json == before


def test_legacy_metadata_world_is_compatibility_input_not_current_authority():
    project = _project()
    authority = _brief(project)["approved_world_authority"]
    assert authority["source"] == "metadata_fallback_legacy_unverified"
    assert authority["authority_status"] == "unverified"
    assert "not current approved authority" in authority["authority_notice"]


def test_versioned_metadata_world_can_keep_current_lineage_label():
    project = _project()
    project.metadata_json["world_spec"]["_meta"] = {
        "artifact_id": "world-artifact-1",
        "version_no": 2,
    }
    authority = _brief(project)["approved_world_authority"]
    assert authority["source"] == "metadata_fallback_versioned_unverified"
    assert authority["authority_status"] == "unverified"
    assert "not current approved authority" in authority["authority_notice"]


def test_metadata_world_with_only_an_old_version_marker_is_not_current():
    project = _project()
    project.metadata_json["world_spec"]["_meta"] = {"version_no": 1}
    authority = _brief(project)["approved_world_authority"]
    assert authority["source"] == "metadata_fallback_legacy_unverified"
    assert authority["authority_status"] == "unverified"


def test_unverified_world_does_not_activate_source_bound_repair_rules():
    project = _project()
    brief = _brief(project)
    assert planner._has_current_world_authority(brief) is False


def test_unverified_world_is_rendered_as_legacy_reference_not_source_authority():
    rendered = outline_llm_judge.render_outline_source_brief(
        {"approved_world_authority": _brief(_project())["approved_world_authority"]}
    )
    assert '"legacy_world_reference"' in rendered
    assert '"approved_world_authority"' not in rendered


def test_missing_world_does_not_invent_or_recover_discarded_rules():
    project = _project()
    project.metadata_json.pop("world_spec")
    assert _brief(project)["approved_world_authority"] == {}


@pytest.mark.parametrize("cached", [None, {"power_system": {"name": "Stale power"}}])
def test_explicit_world_artifact_outweighs_missing_or_stale_metadata(cached):
    project = _project()
    current = project.metadata_json["world_spec"]
    project.metadata_json["world_spec"] = cached
    brief = planner._outline_judge_project_brief(
        project, metadata=project.metadata_json, semantic_candidates=[], world_spec=current
    )
    authority = brief["approved_world_authority"]
    assert authority["source"] == "current_planning_artifact"
    assert authority["power_system"]["name"] == "Echo sense"
    assert "Stale power" not in json.dumps(authority)


def test_explicit_empty_world_does_not_resurrect_cached_power():
    project = _project()
    brief = planner._outline_judge_project_brief(
        project, metadata=project.metadata_json, semantic_candidates=[], world_spec={}
    )
    assert brief["approved_world_authority"] == {}


@pytest.mark.parametrize("cached", [None, {"power_system": {"name": "Obsolete engine"}}])
def test_batched_generation_compiler_preserves_explicit_world(monkeypatch, cached):
    project = _project()
    project.metadata_json.pop("book_design_snapshot")
    project.metadata_json.pop("creation_intent_contract")
    project.metadata_json["selected_effect_skills"] = ["comedy"]
    project.metadata_json["creation_protagonist_source"] = "user"
    current = project.metadata_json["world_spec"]
    project.metadata_json["world_spec"] = cached
    entry = {"volume_number": 1, "chapter_count_target": 3, "chapter_range": [1, 3]}
    monkeypatch.setattr(planner, "_outline_constraint_headroom_chars", lambda *_: 0)
    directive = "【大纲商业评审整改·TRIGGER】必须修正：invent a new activation condition."  # noqa: RUF001 -- production marker
    system, user = planner._volume_outline_prompts(
        project, {}, {}, [entry], entry, world_spec=current,
        extra_constraints=[directive],
    )
    system, user = planner._compile_volume_outline_prompt(
        project, load_settings(env={}), system_prompt=system, user_prompt=user,
    )
    project.metadata_json["world_spec"] = current
    _assert_authority(user, project)
    assert "Obsolete engine" not in user
    assert "invent a new activation condition" not in user
    assert "不可信诊断引文" in user
    assert "整改建议不是设定授权" in system
    report = project.metadata_json["planner_prompt_compiler_latest"]
    assert report["required_complete"]
    assert report["total_tokens"] <= report["usable_budget_tokens"]


def test_batched_generation_empty_world_never_falls_back():
    project = _project()
    entry = {"volume_number": 1, "chapter_count_target": 3, "chapter_range": [1, 3]}
    _, user = planner._volume_outline_prompts(
        project, {}, {"protagonist": {"name": "Mira", "gender": "female", "age": 35,
                                     "role": "archivist"}}, [entry], entry, world_spec={},
    )
    assert "Echo sense" not in user


def test_oversized_world_authority_fails_before_generation_instead_of_truncating():
    project = _project()
    project.metadata_json.pop("book_design_snapshot")
    project.metadata_json.pop("creation_intent_contract")
    project.metadata_json["creation_protagonist_source"] = "user"
    world = {"power_system": {"hard_limits": "Never infer an extra power. " * 20000}}
    entry = {"volume_number": 1, "chapter_count_target": 3}
    system, user = planner._volume_outline_prompts(
        project, {}, {}, [entry], entry, world_spec=world,
    )
    assert world["power_system"]["hard_limits"] in user
    with pytest.raises(ValueError, match="budget"):
        planner._compile_volume_outline_prompt(
            project, load_settings(env={}), system_prompt=system, user_prompt=user,
        )


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_rejected_baseline_never_claims_authority(language):
    blocks = planner._previous_outline_batch_constraints(
        {"chapters": [{"chapter_number": 1, "goal": "candidate " * 80}]},
        chapter_start=1, chapter_end=1, language=language,
    )
    assert "SOURCE AUTHORITY" in blocks[0]
    assert "authoritative story facts" not in blocks[0]
    assert "权威剧情事实" not in blocks[0]
    assert "lossy summary" in blocks[0] or "有损摘要" in blocks[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["single", "batch", "shrink"])
async def test_batch_dispatch_passes_explicit_world_in_all_lanes(monkeypatch, lane):
    project = _project()
    world = project.metadata_json.pop("world_spec")
    calls = []

    class CapturedError(Exception):
        pass

    async def capture(*args, **kwargs):
        calls.append(kwargs)
        if lane == "shrink" and len(calls) == 1:
            raise planner.OutlineBatchShrinkRequired("bounded output too large")
        raise CapturedError

    monkeypatch.setattr(planner, "_generate_volume_outline_with_repair_loop", capture)
    monkeypatch.setattr(planner, "is_strict_prewrite_project", lambda _: lane != "single")
    monkeypatch.setattr(planner, "strict_outline_shrink_size", lambda *_: 1)
    entry = {"volume_number": 1, "chapter_count_target": 3}
    with pytest.raises(CapturedError):
        await planner._generate_volume_outline_batched(
            None, load_settings(env={}), project=project, workflow_run_id=uuid4(),
            logical_name="volume_1", book_spec={}, cast_spec={}, volume_plan=[entry],
            volume_entry=entry, fallback_payload={"chapters": []}, volume_number=1,
            expected_count=3, chapter_number_offset=1, revealed_ledger_block=None,
            base_constraints=[], world_spec=world,
        )
    assert len(calls) == (2 if lane == "shrink" else 1)
    assert all(call["world_spec"] is world for call in calls)


@pytest.mark.asyncio
async def test_repair_loop_sends_world_in_final_generation_request(monkeypatch):
    project = _project()
    project.metadata_json.pop("book_design_snapshot")
    project.metadata_json.pop("creation_intent_contract")
    project.metadata_json["selected_effect_skills"] = ["comedy"]
    project.metadata_json["creation_protagonist_source"] = "user"
    world = project.metadata_json.pop("world_spec")
    requests = []

    class CapturedError(Exception):
        pass

    async def titles(*args, **kwargs):
        return []

    async def capture(*args, **kwargs):
        requests.append(kwargs)
        raise CapturedError

    monkeypatch.setattr(planner, "_fetch_existing_chapter_titles", titles)
    monkeypatch.setattr(planner, "_generate_structured_artifact", capture)
    entry = {"volume_number": 1, "chapter_count_target": 3, "chapter_range": [1, 3]}
    with pytest.raises(planner.PlannerFallbackError):
        await planner._generate_volume_outline_with_repair_loop(
            None, load_settings(env={}), project=project, workflow_run_id=uuid4(),
            logical_name="volume_1", book_spec={},
            cast_spec={"protagonist": {"name": "Mira", "gender": "female", "age": 35,
                                       "role": "archivist"}}, volume_plan=[entry],
            volume_entry=entry, fallback_payload={}, volume_number=1,
            expected_count=3, chapter_number_offset=1, revealed_ledger_block=None,
            base_constraints=[], world_spec=world,
        )
    project.metadata_json["world_spec"] = world
    _assert_authority(requests[0]["user_prompt"], project)


@pytest.mark.asyncio
async def test_effect_route_editor_receives_explicit_world(monkeypatch):
    project = _project()
    world = project.metadata_json.pop("world_spec")
    project.metadata_json["story_enhancers"]["effect_skills"] = ["comedy_engine"]
    project.metadata_json["creation_intent_contract"] = {
        "field_sources": {"story_enhancers": "explicit"},
        "story_enhancers": {"effect_skills": ["comedy_engine"]},
    }
    requests = []

    class CapturedError(Exception):
        pass

    async def capture(*args):
        requests.append(args[-1])
        raise CapturedError

    monkeypatch.setattr(planner, "complete_text", capture)
    with pytest.raises(CapturedError):
        await planner._repair_story_enhancer_distribution(
            None, load_settings(env={}), project=project, workflow_run_id=uuid4(),
            logical_name="volume_1", payload={"chapters": [{"chapter_number": 1}]},
            world_spec=world,
        )
    project.metadata_json["world_spec"] = world
    _assert_authority(requests[0].user_prompt, project)
    assert requests[0].prompt_version == "2.0-source-authority"


@pytest.mark.asyncio
async def test_surgical_request_migrates_persisted_unsafe_advice(monkeypatch):
    project = _project()
    world = project.metadata_json.pop("world_spec")
    requests = []

    async def complete(session, settings, request):
        requests.append(request)
        return LLMCompletionResult(
            content='{"patches":[]}', provider="mock", model_name="mock", llm_run_id=None,
        )

    monkeypatch.setattr(planner, "complete_text", complete)
    await planner._repair_outline_commercial_findings_surgically(
        None, load_settings(env={}), project=project, workflow_run_id=uuid4(),
        logical_name="volume_1", outline_payload={"chapters": []},
        directives=[
            "【大纲商业评审整改·SCENE_POSITION】问题定位：ch2 actor is too far away"  # noqa: RUF001 -- production delimiter
            "；必须修正：allow remote memory reading",  # noqa: RUF001 -- production delimiters
            "【大纲整改总纲】Grant an unlimited new power.",
        ],
        world_spec=world,
    )
    assert "ch2 actor is too far away" in requests[0].user_prompt
    assert "不可信诊断引文" in requests[0].user_prompt
    assert "allow remote memory reading" not in requests[0].user_prompt
    assert "Grant an unlimited new power" not in requests[0].user_prompt
    assert requests[0].prompt_version == "3.4-window-complete-repair"


@pytest.mark.asyncio
async def test_surgical_request_does_not_source_bind_legacy_metadata_world(monkeypatch):
    project = _project()
    requests = []
    sanitized = []

    async def complete(session, settings, request):
        requests.append(request)
        return LLMCompletionResult(
            content='{"patches":[]}', provider="mock", model_name="mock", llm_run_id=None,
        )

    def should_not_run(directives):
        sanitized.append(directives)
        return directives

    monkeypatch.setattr(planner, "complete_text", complete)
    monkeypatch.setattr(
        outline_llm_judge, "source_bound_outline_repair_directives", should_not_run
    )
    await planner._repair_outline_commercial_findings_surgically(
        None, load_settings(env={}), project=project, workflow_run_id=uuid4(),
        logical_name="volume_1", outline_payload={"chapters": []},
        directives=["legacy advice"],
    )
    assert requests
    assert sanitized == []


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["outline", "planning", "surgical"])
async def test_real_requests_preserve_authority_after_long_brief(monkeypatch, consumer):
    project = _project()
    requests = []

    async def complete(session, settings, request):
        requests.append(request)
        if consumer == "surgical":
            payload = {"patches": []}
        elif "adjudication" in request.prompt_template:
            payload = {"adjudication_complete": True, "decisions": [
                {"index": 1, "decision": "CONFIRM", "reason": "New power has no source.",
                 "required_fix": "Remove the unsupported memory reading."}
            ]}
        else:
            payload = {"pass": False, "overall_score": 0.5, "dimension_scores": {},
                       "blocking_issues": [{"code": "KNOWLEDGE_BOUNDARY_VIOLATION",
                           "severity": "critical", "evidence": "Reads all memories remotely.",
                           "required_fix": "Remove unsupported power."}], "rewrite_plan": {}}
        return LLMCompletionResult(content=json.dumps(payload), provider="mock",
                                   model_name="mock", llm_run_id=None)

    monkeypatch.setattr(outline_llm_judge, "complete_text", complete)
    monkeypatch.setattr(planner, "complete_text", complete)
    settings = load_settings(env={})
    brief = _brief(project)
    # This prompt-contract test represents a caller that has already supplied
    # and validated the current world artifact. Legacy metadata fallback is
    # intentionally excluded from SOURCE AUTHORITY by the framework.
    brief["approved_world_authority"]["authority_status"] = "current"
    # Do not involve persisted judge caches in a prompt-contract test.
    brief["slug"] = ""
    if consumer == "outline":
        result = await outline_llm_judge.judge_outline_commercial_readiness(
            None, settings, outline_payload={"chapters": []}, project_brief=brief
        )
        assert not result.passed
        assert len(requests) == 2
    elif consumer == "planning":
        result = await outline_llm_judge.judge_commercial_planning_readiness(
            None, settings, chapters_payload=[], project_brief=brief
        )
        assert not result.passed
    else:
        current_world = project.metadata_json.pop("world_spec")
        await planner._repair_outline_commercial_findings_surgically(
            None, settings, project=project, workflow_run_id=uuid4(), logical_name="volume_1",
            outline_payload={"chapters": []}, directives=["Fix the unsupported power."],
            world_spec=current_world,
        )
        project.metadata_json["world_spec"] = current_world
    for request in requests:
        _assert_authority(request.user_prompt, project)
        assert "SOURCE AUTHORITY" in request.user_prompt
        assert "未到达的境界" in request.system_prompt
        assert "仍须阻断" in request.system_prompt
