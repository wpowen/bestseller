"""Production call boundaries for explicitly selected optional planning methods.

No PostgreSQL or provider is used. The SQLite case preserves real persisted
canon/draft rows while exercising the same planner applicability producers.
"""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from test_adopted_knowledge_lineage import db as knowledge_db  # noqa: F401
from test_emotion_driven_kernel import _valid_kernel_payload
from test_pipeline_services import FakeSession, build_chapter, build_project, build_scene
from test_public_emotion_kernel import _valid_public_emotion_kernel
from test_story_design_kernel import _kernel_payload

from bestseller.domain.context import SceneWriterContextPacket
from bestseller.infra.db.models import (
    CanonFactModel, ChapterDraftVersionModel, LlmRunModel,
    PlanningArtifactVersionModel, SceneDraftVersionModel,
)
from bestseller.services import drafts, pipelines, planner
from bestseller.services.emotion_driven_kernel import render_emotion_driven_kernel_prompt_block
from bestseller.services.entry_system_kernel import (
    build_fallback_entry_system_kernel,
    entry_system_kernel_from_dict,
    entry_system_kernel_to_dict,
    render_entry_system_kernel_prompt_block,
)
from bestseller.services.entry_registry import build_entry_coverage_matrix, build_fallback_entry_registry
from bestseller.services.planning_capability_selection import (
    CAPABILITIES,
    PlanningCapabilitySelectionError,
)
from bestseller.services.premium_genre_engine import build_premium_genre_engine_blocks
from bestseller.services.public_emotion_kernel import render_public_emotion_prompt_block
from bestseller.services.quality_levers.integrator import (
    WriterLeverContext,
    build_writer_quality_levers_block,
)
from bestseller.services.quality_levers.project_meta import extract_quality_levers_meta
from bestseller.services.story_design_kernel import render_story_design_kernel_prompt_block
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit

SENTINEL = "UNSELECTED_KERNEL_MUST_NEVER_REACH_PROMPT"
RENDERERS = {
    "story_design_kernel": render_story_design_kernel_prompt_block,
    "emotion_driven_kernel": render_emotion_driven_kernel_prompt_block,
    "entry_system_kernel": render_entry_system_kernel_prompt_block,
    "public_emotion_kernel": render_public_emotion_prompt_block,
}
WRAPPERS = {
    key: getattr(planner, "_" + key + "_prompt_block")
    for key in CAPABILITIES if key != "public_emotion_kernel"
}
WRAPPERS["public_emotion_kernel"] = planner._public_emotion_kernel_prompt_block
GATES = (
    ("reverse_outline", "reverse_outline_gate_report"),
    ("worldview_compliance", "worldview_compliance_gate_report"),
    ("story_principle", "story_principle_gate_report"),
    ("worldview_progression", "worldview_progression_gate_report"),
)


def _selection(*names, source="explicit"):
    return {"creation_intent_contract": {
        "field_sources": {"hook_spec": source},
        "hook_spec": {"planning_capabilities": {
            "schema": "planning-capabilities.v1",
            "selected": dict.fromkeys(names, True),
        }},
    }}


def _history(mode="absent"):
    data = {} if mode == "absent" else _selection(*CAPABILITIES, source="legacy_generated")
    data.update({key: {"invalid": SENTINEL} for key in CAPABILITIES})
    data.update({
        "entry_registry": {"invalid": SENTINEL},
        "entry_state_snapshot": {"invalid": SENTINEL},
        "story_engine_shadow": {"status": "available", "can_drive_generation": True},
        "author_story": {"unknown_future_field": {"_meta": {"fact": "author-owned"}}},
        "world_spec": {"unknown_story_field": {"_meta": {"immutable": "canon"}}},
        "canon": {"identity": {"name": "fixture-author-name", "birthplace": "fixture-town"}},
        "hype_satisfaction_engine": {"enabled": True},
    })
    return data


def _project(data):
    return SimpleNamespace(
        id=uuid4(), slug="capability-boundary-test", title="Fixture", genre="science-fiction",
        sub_genre="mystery", target_chapters=3, language="zh-CN", metadata_json=deepcopy(data),
    )


def _forbidden(*args, **kwargs):
    raise AssertionError("Unselected/invalid capability crossed a work boundary")


class _NoWorkSession:
    def __getattr__(self, name):
        raise AssertionError(f"Unexpected database/transaction access: {name}")


@pytest.fixture(autouse=True)
def no_paid_calls(monkeypatch):
    from bestseller.services import llm

    for module in (llm, planner, drafts, pipelines):
        monkeypatch.setattr(module, "complete_text", _forbidden, raising=False)


async def _generate(name, project, settings, session):
    artifacts, runs = [], []
    common = dict(project=project, project_slug=project.slug, workflow_run_id=uuid4(),
                  step_order=1, artifact_records=artifacts)
    if name in {"story_design_kernel", "emotion_driven_kernel"}:
        result = await getattr(planner, "_generate_" + name)(
            session, settings, **common, premise="An author-owned premise",
            book_spec_payload={}, world_spec_payload={}, cast_spec_payload={},
            category_key=None, llm_run_ids=runs,
        )
    elif name == "entry_system_kernel":
        result = await planner._generate_entry_system_kernel_artifacts(
            session, **common, story_design_kernel=project.metadata_json["story_design_kernel"],
        )
    elif name == "public_emotion_kernel":
        result = await planner._generate_public_emotion_kernel_artifact(
            session, **common, premise="An author-owned premise", book_spec_payload={},
        )
    else:
        common.pop("step_order")
        common.pop("project_slug")
        result = await planner._persist_story_engine_shadow_advisory(
            session, settings=settings, **common,
            story_design_kernel=project.metadata_json["story_design_kernel"],
        )
    assert artifacts == []
    assert runs == []
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["absent", "unknown_provenance"])
@pytest.mark.parametrize("name", [*CAPABILITIES, "story_engine_shadow"])
async def test_unselected_direct_producer_never_falls_back_generates_or_persists(monkeypatch, mode, name):
    settings = load_settings()
    monkeypatch.setattr(planner, "get_settings", lambda: settings)
    for dependency in (
        "_fallback_story_design_kernel", "_fallback_emotion_driven_kernel",
        "_compile_source_bound_emotion_driven_kernel", "_persist_entry_system_kernel_metadata",
        "build_public_emotion_kernel_seed", "import_planning_artifact",
        "create_workflow_step_run", "persist_legacy_story_engine_shadow", "derive_ideology_kernel",
    ):
        monkeypatch.setattr(planner, dependency, _forbidden)
    project = _project(_history(mode))
    original = deepcopy(project.metadata_json)
    assert await _generate(name, project, settings, _NoWorkSession()) is None
    first = deepcopy(project.metadata_json)
    assert await _generate(name, project, settings, _NoWorkSession()) is None
    assert project.metadata_json == first
    assert {key: project.metadata_json[key] for key in original} == original
    capability = "story_design_kernel" if name == "story_engine_shadow" else name
    receipt = first["deterministic_audit_planning_capabilities"][capability]
    assert receipt["status"] == "not_applicable"
    assert receipt["selected"] is False


@pytest.mark.parametrize("mode", ["absent", "unknown_provenance"])
@pytest.mark.parametrize("capability", CAPABILITIES)
def test_core_and_planner_renderers_do_not_inject_unselected_legacy_cache(mode, capability):
    data = _history(mode)
    before = deepcopy(data)
    assert RENDERERS[capability](data[capability], metadata=data) == ""
    project = _project(data)
    assert WRAPPERS[capability](project) == ""
    assert SENTINEL not in planner._compact_outline_context_block(project)
    assert planner._entry_registry_prompt_block(project) == ""
    assert data == before
    assert project.metadata_json == before


def _valid_payload(capability):
    # These are established domain fixtures; fallback below is fixture setup,
    # never the planner generation path whose fallback is forbidden above.
    return {
        "story_design_kernel": _kernel_payload,
        "emotion_driven_kernel": _valid_kernel_payload,
        "public_emotion_kernel": _valid_public_emotion_kernel,
        "entry_system_kernel": lambda: entry_system_kernel_to_dict(
            build_fallback_entry_system_kernel({"genre": "science-fiction", "target_chapters": 3})),
    }[capability]()


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_explicit_selected_valid_asset_reaches_core_and_planner_wrapper(capability):
    data = {**_selection(capability), capability: _valid_payload(capability)}
    before = deepcopy(data)
    expected = RENDERERS[capability](data[capability], metadata=data)
    assert expected.strip()
    actual = WRAPPERS[capability](_project(data))
    assert actual.strip() == expected.strip()
    assert data == before


@pytest.mark.parametrize("capability", CAPABILITIES)
@pytest.mark.parametrize("asset", [None, {}, "malformed_known_field"])
def test_explicit_selected_missing_or_invalid_asset_is_not_swallowed(capability, asset):
    if asset == "malformed_known_field":
        asset = _valid_payload(capability)
        field = {"story_design_kernel": "reader_promise", "emotion_driven_kernel": "empathy_contracts",
                 "entry_system_kernel": "taxonomy", "public_emotion_kernel": "target_segments"}[capability]
        asset[field] = None if capability == "story_design_kernel" else [7]
    project = _project({**_selection(capability), capability: deepcopy(asset)})
    with pytest.raises(ValueError):
        WRAPPERS[capability](project)
    with pytest.raises(ValueError):
        planner._compact_outline_context_block(project)


@pytest.mark.parametrize("mode", ["absent", "unknown_provenance"])
def test_quality_levers_and_premium_entry_do_not_reactivate_old_cache(mode):
    data = _history(mode)
    original = deepcopy(data)
    meta = extract_quality_levers_meta(data)
    assert meta.emotion_driven_kernel is None
    assert meta.public_emotion_kernel is None
    block = build_writer_quality_levers_block(WriterLeverContext(
        chapter_number=1, project_metadata=data,
        emotion_driven_kernel=data["emotion_driven_kernel"],
        public_emotion_kernel=data["public_emotion_kernel"],
    ))
    assert SENTINEL not in block
    premium = build_premium_genre_engine_blocks(
        project_metadata=data, story_bible_context=data, genre="science-fiction")
    assert premium.entry_system_context_block == ""
    assert premium.entry_registry_context_block == ""
    assert premium.entry_state_ledger_block == ""
    assert data == original


@pytest.mark.parametrize("capability", ["emotion_driven_kernel", "public_emotion_kernel"])
def test_selected_writer_lever_carries_author_selection_to_actual_renderer(capability):
    data = {**_selection(capability), capability: _valid_payload(capability)}
    meta = extract_quality_levers_meta(data)
    assert getattr(meta, capability) == data[capability]
    expected = RENDERERS[capability](data[capability], metadata=data, **(
        {"chapter_number": 1} if capability == "emotion_driven_kernel" else {}))
    assert expected.strip()
    actual = build_writer_quality_levers_block(WriterLeverContext(
        chapter_number=1, project_metadata=data, **{capability: data[capability]},
    ))
    assert expected in actual


def test_selected_valid_premium_entry_renders_and_preserves_its_source():
    data = {**_selection("entry_system_kernel"), "entry_system_kernel": _valid_payload("entry_system_kernel")}
    before = deepcopy(data)
    result = build_premium_genre_engine_blocks(project_metadata=data, genre="science-fiction")
    expected = render_entry_system_kernel_prompt_block(
        data["entry_system_kernel"], metadata=data, max_types=4, max_ladders=2, max_axes=4)
    assert expected.strip()
    assert result.entry_system_context_block == expected
    assert data == before


def test_selected_invalid_premium_entry_is_not_silently_downgraded_to_warning():
    payload = _valid_payload("entry_system_kernel")
    payload["taxonomy"] = [7]
    data = {**_selection("entry_system_kernel"), "entry_system_kernel": payload}
    with pytest.raises(ValueError):
        build_premium_genre_engine_blocks(project_metadata=data, genre="science-fiction")


@pytest.mark.parametrize("core", [None, {}, [], "not-an-entry-kernel"])
def test_selected_premium_entry_missing_or_nonobject_core_raises_typed_error(core):
    data = {**_selection("entry_system_kernel"), "entry_system_kernel": deepcopy(core)}
    before = deepcopy(data)
    with pytest.raises(PlanningCapabilitySelectionError, match="selected_entry_system_kernel_missing_or_invalid"):
        build_premium_genre_engine_blocks(project_metadata=data, genre="science-fiction")
    assert data == before


def _premium_data():
    payload = _valid_payload("entry_system_kernel")
    kernel = entry_system_kernel_from_dict(payload)
    registry = build_fallback_entry_registry(
        kernel, build_entry_coverage_matrix(kernel, target_chapters=3))
    return {**_selection("entry_system_kernel"), "entry_system_kernel": payload,
            "entry_registry": registry.model_dump(mode="json")}


INVALID_OPTIONAL_ASSETS = (
    ("entry_registry", {"entries": [7]}, "selected_entry_registry_invalid"),
    ("entry_state_snapshot", {"current_chapter": "not-a-number", "entry_states": {}},
     "selected_entry_state_snapshot_invalid"),
    ("entry_state_ledger", {"events": [7]}, "selected_entry_state_ledger_invalid"),
)


@pytest.mark.parametrize("asset,invalid,error", INVALID_OPTIONAL_ASSETS)
@pytest.mark.parametrize("channel", ["metadata", "story_bible"])
def test_selected_supplied_invalid_optional_entry_asset_is_typed_and_not_mutated(asset, invalid, error, channel):
    data = _premium_data()
    story = {}
    if channel == "story_bible":
        data.pop(asset, None)
        story[asset] = deepcopy(invalid)
    else:
        data[asset] = deepcopy(invalid)
    before_data, before_story = deepcopy(data), deepcopy(story)
    with pytest.raises(PlanningCapabilitySelectionError, match=error):
        build_premium_genre_engine_blocks(
            project_metadata=data, story_bible_context=story, genre="science-fiction")
    assert data == before_data
    assert story == before_story


@pytest.mark.parametrize("asset", [item[0] for item in INVALID_OPTIONAL_ASSETS])
def test_selected_supplied_nonobject_optional_entry_asset_is_not_treated_as_absent(asset):
    data = _premium_data()
    data[asset] = "corrupt-retained-asset"
    with pytest.raises(PlanningCapabilitySelectionError):
        build_premium_genre_engine_blocks(project_metadata=data, genre="science-fiction")


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["writer", "pipeline"])
@pytest.mark.parametrize("failure", ["missing_core", "invalid_registry"])
async def test_actual_scene_entry_propagates_typed_premium_error_before_draft_or_model(
    monkeypatch, tmp_path, entrypoint, failure,
):
    from bestseller.services import imagery_system_design, ranking_capability_profile

    project = build_project()
    project.language = "en"
    project.metadata_json = _premium_data()
    if failure == "missing_core":
        project.metadata_json.pop("entry_system_kernel")
        error = "selected_entry_system_kernel_missing_or_invalid"
    else:
        project.metadata_json["entry_registry"] = {"entries": [7]}
        error = "selected_entry_registry_invalid"
    original = deepcopy(project.metadata_json)
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    settings = load_settings().model_copy(deep=True)
    settings.output.base_dir = str(tmp_path)
    settings.pipeline.require_pre_draft_scene_contract = False
    settings.pipeline.enable_truth_version_guard = False
    packet = SceneWriterContextPacket(
        project_id=project.id, project_slug=project.slug, chapter_id=chapter.id, scene_id=scene.id,
        chapter_number=1, scene_number=1, query_text="Fixture action", story_bible={},
    )

    async def noop(*args, **kwargs):
        return None

    async def lookup(*args, **kwargs):
        return project

    async def identifiers(*args, **kwargs):
        return project, chapter, scene

    async def context(*args, **kwargs):
        return packet

    monkeypatch.setattr(drafts, "get_project_by_slug", lookup)
    monkeypatch.setattr(drafts, "optimize_project_character_profiles", noop)
    monkeypatch.setattr(imagery_system_design, "ensure_book_imagery_system", noop)
    monkeypatch.setattr(ranking_capability_profile, "apply_ranking_capability_profile_to_context", lambda *a, **k: False)
    monkeypatch.setattr(pipelines, "_load_scene_identifiers", identifiers)
    monkeypatch.setattr(pipelines, "_load_current_scene_draft", noop)
    monkeypatch.setattr(pipelines, "build_scene_writer_context_from_models", context)
    for name in ("_ensure_emotion_kernel_backfill_for_pipeline", "_ensure_public_emotion_kernel_backfill_for_pipeline",
                 "_ensure_entry_system_backfill_for_pipeline", "_enforce_truth_version_guard"):
        monkeypatch.setattr(pipelines, name, noop)
    monkeypatch.setattr(pipelines, "generate_scene_draft", _forbidden)
    monkeypatch.setattr(pipelines, "review_scene_draft", _forbidden)
    monkeypatch.setattr(pipelines, "refresh_scene_knowledge", _forbidden)
    session = FakeSession(scalar_results=[chapter, scene])
    with pytest.raises(PlanningCapabilitySelectionError, match=error):
        if entrypoint == "writer":
            await drafts.generate_scene_draft(
                session, project.slug, 1, 1, settings=settings, context_packet=packet)
        else:
            await pipelines.run_scene_pipeline.__wrapped__(session, settings, project.slug, 1, 1)
    assert project.metadata_json == original
    assert not any(isinstance(item, (
        ChapterDraftVersionModel, SceneDraftVersionModel, PlanningArtifactVersionModel, LlmRunModel,
    )) for item in session.added)


ENTRYPOINTS = ("foundation", "novel", "chapter_writer", "scene_writer", "chapter_pipeline")


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
@pytest.mark.parametrize("capability", CAPABILITIES[:3])
async def test_actual_entry_rejects_selected_unavailable_before_secondary_db_or_paid_work(
    monkeypatch, entrypoint, capability,
):
    settings = load_settings().model_copy(deep=True)
    setattr(settings.pipeline, "enable_" + capability, False)
    project = _project(_selection(capability))
    before = deepcopy(project.metadata_json)
    lookups = []

    async def lookup(session, slug):
        assert isinstance(session, _NoWorkSession)
        assert slug == project.slug
        lookups.append(slug)
        return project

    monkeypatch.setattr(planner, "_assert_plan_writer_not_locked", lookup)
    monkeypatch.setattr(drafts, "get_project_by_slug", lookup)
    monkeypatch.setattr(pipelines, "get_project_by_slug", lookup)
    session = _NoWorkSession()
    with pytest.raises(PlanningCapabilitySelectionError, match="selected_but_unavailable"):
        if entrypoint in {"foundation", "novel"}:
            await getattr(planner, "generate_" + entrypoint + "_plan")(
                session, settings, project.slug, "An explicit premise")
        elif entrypoint == "chapter_writer":
            await drafts.generate_chapter_draft_once(session, project.slug, 1, settings=settings)
        elif entrypoint == "scene_writer":
            await drafts.generate_scene_draft(session, project.slug, 1, 1, settings=settings)
        else:
            await pipelines.run_chapter_pipeline.__wrapped__(session, settings, project.slug, 1)
    assert lookups == [project.slug]
    assert project.metadata_json == before


async def _gate(name, session, project, settings):
    args = dict(project=project, workflow_run_id=uuid4(), step_order=1)
    if name == "story_principle":
        return await planner._run_story_principle_gate(session, **args, outline_payload={})
    args["story_design_kernel"] = project.metadata_json["story_design_kernel"]
    args["volume_plan_payload" if name == "worldview_progression" else "outline_payload"] = {}
    return await getattr(planner, "_run_" + name + "_gate")(session, settings, **args)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,report_key", GATES)
async def test_method_gate_archives_original_grade_once_without_claiming_pass(monkeypatch, name, report_key):
    from bestseller.services import (
        reverse_outline_gate, story_principle_gate, worldview_compliance_gate, worldview_progression_gate,
    )

    for module, function in (
        (reverse_outline_gate, "evaluate_reverse_outline_gate"),
        (story_principle_gate, "evaluate_story_principle_contract"),
        (worldview_compliance_gate, "evaluate_worldview_compliance_gate"),
        (worldview_progression_gate, "evaluate_worldview_progression_gate"),
    ):
        monkeypatch.setattr(module, function, _forbidden)
    monkeypatch.setattr(planner, "create_workflow_step_run", _forbidden)
    original_report = {"passed": False, "raw": "original paid grade", "findings": [{"code": "OLD"}]}
    earlier_report = {"passed": True, "raw": "older untouched grade"}
    data = {**_history(), report_key: original_report, report_key + "_history": [earlier_report]}
    project = _project(data)
    result = await _gate(name, _NoWorkSession(), project, load_settings())
    assert result["status"] == "not_applicable"
    assert result["passed"] is None
    assert result["required"] is False
    assert result["grade_created"] is False
    assert result["independent_canon_identity_world_checks_required"] is True
    assert project.metadata_json[report_key + "_history"] == [earlier_report, original_report]
    first = deepcopy(project.metadata_json)
    assert await _gate(name, _NoWorkSession(), project, load_settings()) == result
    assert project.metadata_json == first
    assert {key: first[key] for key in data if key != report_key and key != report_key + "_history"} == {
        key: value for key, value in data.items() if key != report_key and key != report_key + "_history"}


def _row(row):
    return deepcopy({column.key: getattr(row, column.key) for column in inspect(type(row)).columns})


@pytest.mark.asyncio
async def test_real_sqlite_applicability_write_preserves_original_canon_and_adopted_draft(knowledge_db):  # noqa: F811
    session, project, chapter, draft = knowledge_db
    assert session.bind.dialect.name == "sqlite"
    project.metadata_json = {**_history(), "reverse_outline_gate_report": {
        "passed": False, "raw": "original retained review"}}
    fact = CanonFactModel(
        project_id=project.id, subject_type="character", subject_id=uuid4(),
        subject_label="original author identity", predicate="birthplace", fact_type="identity",
        value_json={"place": "explicit-canon-town", "unknown_nested": {"_meta": "story fact"}},
        source_type="explicit", source_chapter_id=chapter.id, valid_from_chapter_no=1,
        is_current=True, tags=["original"],
    )
    session.add(fact)
    await session.commit()
    before_fact, before_draft = _row(fact), _row(draft)
    before_story = deepcopy(project.metadata_json)
    for capability in CAPABILITIES:
        assert planner._planning_capability_enabled(project, capability) is False
    result = await _gate("reverse_outline", session, project, load_settings())
    assert result["passed"] is None
    await session.commit()
    fact = await session.get(CanonFactModel, fact.id, populate_existing=True)
    draft = await session.get(ChapterDraftVersionModel, draft.id, populate_existing=True)
    await session.refresh(project)
    assert _row(fact) == before_fact
    assert _row(draft) == before_draft
    assert len(list(await session.scalars(select(CanonFactModel)))) == 1
    for key, value in before_story.items():
        if key != "reverse_outline_gate_report":
            assert project.metadata_json[key] == value
    assert project.metadata_json["reverse_outline_gate_report_history"] == [
        before_story["reverse_outline_gate_report"]]
