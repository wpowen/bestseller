"""Exact producer re-projection plus the real scene-entry injection boundary."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_pipeline_services import (
    FakeSession,
    build_chapter,
    build_project,
    build_scene,
    build_settings,
)

from bestseller.domain.context import SceneWriterContextPacket
from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services import identity_guard, pipelines
from bestseller.services.planning_capability_selection import (
    CAPABILITIES,
    PlanningCapabilitySelectionError,
)
from bestseller.services.planning_kernel import _DIRECTIVE_TEMPLATES_EN, _DIRECTIVE_TEMPLATES_ZH
from bestseller.services.prewrite_review import project_scene_prewrite_directives

pytestmark = pytest.mark.unit


def selected(*caps):
    return {
        "creation_intent_contract": {
            "field_sources": {"hook_spec": "explicit"},
            "hook_spec": {
                "planning_capabilities": {
                    "schema": "planning-capabilities.v1",
                    "selected": {key: key in caps for key in CAPABILITIES},
                }
            },
        }
    }


@pytest.fixture(params=["zh-CN", "en"])
def producer(request):
    return (
        request.param,
        _DIRECTIVE_TEMPLATES_EN if request.param == "en" else _DIRECTIVE_TEMPLATES_ZH,
    )


@pytest.mark.parametrize(
    "code,capability",
    [
        ("story_design_contract_invalid", "story_design_kernel"),
        ("story_design_contract_thin", "story_design_kernel"),
        ("distilled_worldview_not_bound", "story_design_kernel"),
        ("empathy_contract_missing", "emotion_driven_kernel"),
        ("bomb_contract_not_consumed", "emotion_driven_kernel"),
        ("antagonist_moral_contract_thin", "emotion_driven_kernel"),
        ("ending_texture_missing", "emotion_driven_kernel"),
        ("public_emotion_bridge_missing", "public_emotion_kernel"),
    ],
)
def test_cached_exact_method_template_requires_live_selection(producer, code, capability):
    language, templates = producer
    method = templates[code]
    authored = "Canon: gate remains locked. Timeline: ship already departed. " + code
    near_match = method + " Author addition: preserve this exact instruction."
    data = {
        **selected(),
        "prewrite_repair_directives": [method, authored, near_match],
        "prewrite_directive_receipt": {"selected": True, "source": "author"},
    }
    before = deepcopy(data)
    result = project_scene_prewrite_directives(data, language=language)
    assert result["directives"] == [authored, near_match]
    assert result["receipt"]["excluded"][0]["codes"] == [code]
    assert result["receipt"]["current_selections"][capability]["status"] == "not_applicable"
    assert data == before
    data.update(selected(capability))
    result = project_scene_prewrite_directives(
        SimpleNamespace(metadata_json=data), language=language
    )
    assert result["directives"] == [method, authored, near_match]
    assert result["receipt"]["excluded"] == []


def test_fallback_report_filters_codes_before_producer_limit_and_preserves_raw(producer):
    language, templates = producer
    report = {
        "passed": False,
        "blocking_findings": [
            {"code": code, "message": "retained old diagnostic"}
            for code in (
                "empathy_contract_missing",
                "bomb_contract_not_consumed",
                "ending_texture_missing",
                "story_design_contract_invalid",
                "public_emotion_bridge_missing",
                "story_capacity_insufficient",
                "rule_engine_missing",
                "compliance_boundary_high_risk",
            )
        ],
        "warnings": [{"code": "UNKNOWN_AUTHOR_NOTE", "repair_action": "Unchanged raw author note"}],
        "capability_snapshot": {"planning_capabilities": {"selected": True}},
    }
    data = {**selected(), "prewrite_readiness_report": report}
    before = deepcopy(data)
    result = project_scene_prewrite_directives(data, language=language)
    assert templates["rule_engine_missing"] in result["directives"]
    assert templates["compliance_boundary_high_risk"] in result["directives"]
    assert templates["story_capacity_insufficient"] in result["directives"]
    assert len(result["receipt"]["excluded"]) == 5
    assert data == before and report["passed"] is False


def test_unknown_codes_authored_text_and_other_capabilities_are_not_purged(producer):
    language, templates = producer
    unknown = "EMPATHY_CONTRACT_MISSING: author says keep the debt and secret unchanged."
    data = {
        **selected("emotion_driven_kernel"),
        "prewrite_repair_directives": [
            templates["empathy_contract_missing"],
            templates["story_design_contract_invalid"],
            templates["volume_primary_force_repeats"],
            templates["distilled_strategy_copy_risk"],
            unknown,
        ],
    }
    result = project_scene_prewrite_directives(data, language=language)
    assert result["directives"] == [data["prewrite_repair_directives"][i] for i in (0, 2, 3, 4)]


def test_cached_precedence_does_not_regenerate_other_report_directives(producer):
    language, templates = producer
    data = {
        **selected(),
        "prewrite_repair_directives": [templates["empathy_contract_missing"]],
        "prewrite_readiness_report": {"blocking_findings": [{"code": "rule_engine_missing"}]},
    }
    result = project_scene_prewrite_directives(data, language=language)
    assert result["directives"] == []
    assert result["receipt"]["origin"] == "prewrite_repair_directives"


def test_explicit_malformed_selection_raises_without_mutating_history():
    data = selected()
    data["creation_intent_contract"]["hook_spec"]["planning_capabilities"]["selected"][
        "story_design_kernel"
    ] = "false"
    before = deepcopy(data)
    with pytest.raises(PlanningCapabilitySelectionError):
        project_scene_prewrite_directives(data)
    assert data == before


@pytest.mark.parametrize("shape", ["string", "mapping"])
def test_unknown_cache_shape_cannot_claim_producer_contract(producer, shape):
    language, templates = producer
    text = templates["empathy_contract_missing"]
    cached = text if shape == "string" else {text: "author note", "Canon: preserve lock.": True}
    data = {**selected(), "prewrite_repair_directives": cached}
    before = deepcopy(data)
    result = project_scene_prewrite_directives(data, language=language)
    assert result["directives"] == ([text] if shape == "string" else list(cached))
    assert result["receipt"]["excluded"] == [] and data == before


class StopAfterDirectives(BaseException):
    """Stop at the next real boundary, before any writer/reviewer can run."""


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["cache", "report"])
@pytest.mark.parametrize("chosen", [False, True])
@pytest.mark.parametrize("language", ["en", "zh-CN"])
async def test_actual_scene_entry_preserves_canon_and_timeline_and_projects_methods(
    monkeypatch,
    origin,
    chosen,
    language,
):
    templates = _DIRECTIVE_TEMPLATES_EN if language == "en" else _DIRECTIVE_TEMPLATES_ZH
    data = selected(*(["emotion_driven_kernel"] if chosen else []))
    if origin == "cache":
        data["prewrite_repair_directives"] = [
            templates["empathy_contract_missing"],
            "Author: preserve the map.",
        ]
    else:
        data["prewrite_readiness_report"] = {
            "blocking_findings": [
                {"code": "empathy_contract_missing"},
                {"code": "story_capacity_insufficient"},
            ]
        }
    project, context, session = await setup_entry(monkeypatch, data, language)
    before = deepcopy(project.metadata_json)
    with pytest.raises(StopAfterDirectives):
        await pipelines.run_scene_pipeline.__wrapped__(
            session, build_settings(), project.slug, 1, 1
        )
    assert (
        templates["empathy_contract_missing"] in "\n".join(context.contradiction_warnings)
    ) is chosen
    assert context.contradiction_warnings[-2:] == [
        "Canon: unchanged exact fact.",
        "Timeline: prior event already occurred.",
    ]
    if origin == "cache":
        assert any("Author: preserve the map." in value for value in context.contradiction_warnings)
    else:
        assert any(
            templates["story_capacity_insufficient"] in value
            for value in context.contradiction_warnings
        )
    workflow = next(row for row in session.added if isinstance(row, WorkflowRunModel))
    receipt = workflow.metadata_json["prewrite_directive_projection"]
    assert receipt["current_selections"]["emotion_driven_kernel"]["selected"] is chosen
    assert bool(receipt["excluded"]) is (not chosen)
    assert project.metadata_json == before


async def setup_entry(monkeypatch, data, language):
    project = build_project()
    project.language = language
    project.metadata_json = deepcopy(data)
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    context = SceneWriterContextPacket(
        project_id=project.id,
        project_slug=project.slug,
        chapter_id=chapter.id,
        scene_id=scene.id,
        chapter_number=1,
        scene_number=1,
        query_text="generic",
        story_bible={},
        contradiction_warnings=[
            "Canon: unchanged exact fact.",
            "Timeline: prior event already occurred.",
        ],
    )

    async def identifiers(*args, **kwargs):
        return project, chapter, scene

    async def nothing(*args, **kwargs):
        return None

    async def build_context(*args, **kwargs):
        return context

    async def stop(*args, **kwargs):
        raise StopAfterDirectives()

    async def forbidden(*args, **kwargs):
        raise AssertionError("No provider or later pipeline stage may execute")

    monkeypatch.setattr(pipelines, "_load_scene_identifiers", identifiers)
    monkeypatch.setattr(pipelines, "_load_current_scene_draft", nothing)
    for name in (
        "_ensure_emotion_kernel_backfill_for_pipeline",
        "_ensure_public_emotion_kernel_backfill_for_pipeline",
        "_ensure_entry_system_backfill_for_pipeline",
        "_enforce_truth_version_guard",
    ):
        monkeypatch.setattr(pipelines, name, nothing)
    monkeypatch.setattr(pipelines, "build_scene_writer_context_from_models", build_context)
    monkeypatch.setattr(identity_guard, "load_identity_registry", stop)
    monkeypatch.setattr(pipelines, "generate_scene_draft", forbidden)
    monkeypatch.setattr(pipelines, "review_scene_draft", forbidden)
    return project, context, FakeSession()


@pytest.mark.asyncio
async def test_actual_scene_entry_does_not_swallow_explicit_selection_error(monkeypatch):
    data = selected()
    data["creation_intent_contract"]["hook_spec"]["planning_capabilities"]["schema"] = "invalid"
    project, context, session = await setup_entry(monkeypatch, data, "en")
    with pytest.raises(PlanningCapabilitySelectionError):
        await pipelines.run_scene_pipeline.__wrapped__(
            session, build_settings(), project.slug, 1, 1
        )
    assert context.contradiction_warnings == [
        "Canon: unchanged exact fact.",
        "Timeline: prior event already occurred.",
    ]
