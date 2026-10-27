"""Optional method caches cannot authorize prose or manufacture missing assets."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_kernel_composer import _culture
from test_story_design_kernel import _kernel_payload

from bestseller.domain.enums import ArtifactType
from bestseller.services import public_emotion_backfill as backfill
from bestseller.services.compliance_boundary_kernel import (
    build_compliance_boundary_kernel_seed,
    compliance_boundary_kernel_from_dict,
    compliance_boundary_kernel_to_dict,
)
from bestseller.services.kernel_composer import (
    narrative_richness_context_from_metadata,
    render_narrative_richness_prompt_block,
)
from bestseller.services.planning_capability_selection import PlanningCapabilitySelectionError
from bestseller.services.public_emotion_kernel import (
    build_public_emotion_kernel_seed,
    public_emotion_kernel_from_dict,
    public_emotion_kernel_to_dict,
)


def _selection(**selected):
    return {
        "creation_intent_contract": {
            "field_sources": {"hook_spec": "explicit"},
            "hook_spec": {
                "planning_capabilities": {
                    "schema": "planning-capabilities.v1",
                    "selected": selected,
                }
            },
        }
    }


def _compliance():
    return compliance_boundary_kernel_to_dict(
        compliance_boundary_kernel_from_dict(
            build_compliance_boundary_kernel_seed(platform="general"),
        )
    )


def _public():
    return public_emotion_kernel_to_dict(
        public_emotion_kernel_from_dict(
            build_public_emotion_kernel_seed(
                book_spec={
                    "title": "A distant observatory",
                    "genre": "science-fiction",
                    "premise": "Two navigators compare their measurements",
                },
                commercial_brief={},
                project_metadata={},
            ),
        )
    )


class _Project:
    def __init__(self, metadata):
        self._metadata = deepcopy(metadata)
        self.writes = 0
        self.id = uuid4()
        self.slug = "unrelated-observatory"
        self.title = "A distant observatory"
        self.genre = "science-fiction"
        self.audience = "adult"

    @property
    def metadata_json(self):
        return self._metadata

    @metadata_json.setter
    def metadata_json(self, value):
        self.writes += 1
        self._metadata = value


class _NoSession:
    def __getattr__(self, name):
        raise AssertionError(f"Unexpected database access: {name}")


def _deny_public_work(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unselected public cache/generation must not be used")

    for name in (
        "_existing_public_kernel",
        "build_public_emotion_kernel_seed",
        "_latest_artifact_content",
    ):
        monkeypatch.setattr(backfill, name, forbidden)


@pytest.mark.parametrize("provenance", ["unknown", "explicit_false"])
def test_unselected_nested_story_culture_not_rendered_and_cache_preserved(provenance):
    metadata = _selection(story_design_kernel=False) if provenance == "explicit_false" else {}
    metadata["story_design_kernel"] = {
        "worldview_kernel": {
            "cultural_texture_module": _culture().model_dump(mode="json"),
        }
    }
    before = deepcopy(metadata)
    assert narrative_richness_context_from_metadata(metadata) is None
    assert metadata == before


def test_selected_valid_story_culture_renders_without_mutating_full_kernel():
    kernel = _kernel_payload()
    kernel["worldview_kernel"]["cultural_texture_module"] = _culture().model_dump(mode="json")
    metadata = {**_selection(story_design_kernel=True), "story_design_kernel": kernel}
    before = deepcopy(metadata)
    context = narrative_richness_context_from_metadata(metadata)
    assert context is not None
    assert "Cultural Texture Module" in render_narrative_richness_prompt_block(
        context, chapter_no=2
    )
    assert metadata == before


@pytest.mark.parametrize("kernel", [None, {}, "invalid", {"invalid": True}])
def test_selected_required_story_dependency_missing_invalid_raises_typed(kernel):
    metadata = {**_selection(story_design_kernel=True), "story_design_kernel": kernel}
    before = deepcopy(metadata)
    with pytest.raises(PlanningCapabilitySelectionError):
        narrative_richness_context_from_metadata(metadata)
    assert metadata == before


def test_invalid_selected_story_culture_raises_typed():
    kernel = _kernel_payload()
    kernel["worldview_kernel"]["cultural_texture_module"] = {"palette": []}
    metadata = {**_selection(story_design_kernel=True), "story_design_kernel": kernel}
    with pytest.raises(PlanningCapabilitySelectionError):
        narrative_richness_context_from_metadata(metadata)


@pytest.mark.parametrize("independent_key", ["worldview_kernel", "narrative_richness_context"])
def test_independent_world_and_canon_inputs_survive_with_unselected_bad_story_cache(
    independent_key,
):
    metadata = {
        **_selection(story_design_kernel=False),
        independent_key: {"cultural_texture_module": _culture().model_dump(mode="json")},
        "story_design_kernel": {"worldview_kernel": {"cultural_texture_module": {"invalid": True}}},
        "approved_world_spec": {"rules": ["The canal freezes in winter"]},
        "canon": [{"fact": "The expedition departed"}],
        "timeline": [{"chapter": 1}],
    }
    before = deepcopy(metadata)
    context = narrative_richness_context_from_metadata(metadata)
    assert context is not None
    assert "Cultural Texture Module" in render_narrative_richness_prompt_block(
        context, chapter_no=2
    )
    assert metadata == before


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_contract", [False, None])
async def test_public_unselected_existing_compliance_means_zero_cache_work_query_or_write(
    monkeypatch,
    selected_contract,
):
    metadata = _selection(public_emotion_kernel=False) if selected_contract is False else {}
    metadata.update(
        public_emotion_kernel={"historical_invalid": [1, 2, 3]},
        public_emotion_kernel_backfill={"old_receipt": "keep"},
        compliance_boundary_kernel=_compliance(),
        canon={"fact": "Keep"},
        timeline=[{"event": "Keep"}],
    )
    project = _Project(metadata)
    _deny_public_work(monkeypatch)
    result = await backfill.ensure_project_public_emotion_kernels(
        _NoSession(),
        project,
        persist_artifact=True,
    )
    assert result.status == "not_applicable" and not result.changed
    assert result.public_emotion_kernel is None
    assert result.compliance_boundary_kernel == metadata["compliance_boundary_kernel"]
    assert project.metadata_json == metadata and project.writes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("persist", [False, True])
async def test_public_unselected_compliance_only_creation_persistence_and_idempotency(
    monkeypatch,
    force,
    persist,
):
    metadata = {
        **_selection(public_emotion_kernel=False),
        "public_emotion_kernel": {"raw_history": "Never normalize or overwrite"},
        "public_emotion_kernel_backfill": {"old_paid_receipt": "Keep"},
        "canon": {"fact": "Keep"},
        "timeline": [{"event": "Keep"}],
    }
    project = _Project(metadata)
    _deny_public_work(monkeypatch)
    imports = []

    async def persist_artifact(session, slug, payload):
        assert isinstance(session, _NoSession) and slug == project.slug
        imports.append(payload)
        return SimpleNamespace(id=uuid4(), version_no=7)

    monkeypatch.setattr(backfill, "import_planning_artifact", persist_artifact)
    result = await backfill.ensure_project_public_emotion_kernels(
        _NoSession(),
        project,
        force=force,
        persist_artifact=persist,
    )
    assert result.changed and result.public_emotion_kernel is None
    assert result.source == "independent_compliance"
    assert project.metadata_json == {**metadata, "compliance_boundary_kernel": _compliance()}
    assert project.writes == 1
    assert len(imports) == int(persist)
    if imports:
        assert imports[0].artifact_type == ArtifactType.COMPLIANCE_BOUNDARY_KERNEL
        assert imports[0].content == result.compliance_boundary_kernel
    second = await backfill.ensure_project_public_emotion_kernels(_NoSession(), project)
    assert second.status == "not_applicable" and not second.changed
    assert project.writes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw", [None, {}, {"invalid": True}, {"target_segments": [{"invalid": True}]}]
)
@pytest.mark.parametrize("force", [False, True])
async def test_selected_public_missing_invalid_cannot_be_repaired_to_pass(monkeypatch, raw, force):
    metadata = {**_selection(public_emotion_kernel=True), "public_emotion_kernel": raw}
    project = _Project(metadata)

    def forbidden(*args, **kwargs):
        raise AssertionError("Missing selected asset cannot be silently seeded")

    monkeypatch.setattr(backfill, "build_public_emotion_kernel_seed", forbidden)
    monkeypatch.setattr(backfill, "_latest_artifact_content", forbidden)
    with pytest.raises(PlanningCapabilitySelectionError):
        await backfill.ensure_project_public_emotion_kernels(
            _NoSession(),
            project,
            force=force,
            persist_artifact=True,
        )
    assert project.metadata_json == metadata and project.writes == 0


@pytest.mark.asyncio
async def test_selected_public_valid_existing_contract_and_receipts_remain_usable():
    metadata = {
        **_selection(public_emotion_kernel=True),
        "public_emotion_kernel": _public(),
        "compliance_boundary_kernel": _compliance(),
        "public_emotion_kernel_backfill": {"old_receipt": "Keep"},
    }
    project = _Project(metadata)
    result = await backfill.ensure_project_public_emotion_kernels(_NoSession(), project)
    assert result.status == "existing" and not result.changed
    assert result.public_emotion_kernel == metadata["public_emotion_kernel"]
    assert project.metadata_json == metadata


@pytest.mark.asyncio
async def test_malformed_explicit_selection_is_not_an_implicit_opt_out():
    metadata = _selection(public_emotion_kernel="yes")
    project = _Project(metadata)
    with pytest.raises(PlanningCapabilitySelectionError):
        await backfill.ensure_project_public_emotion_kernels(_NoSession(), project)
    with pytest.raises(PlanningCapabilitySelectionError):
        narrative_richness_context_from_metadata(metadata)
    assert project.writes == 0 and project.metadata_json == metadata
