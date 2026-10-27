"""Materialization projects approved typed source; it cannot author missing facts."""

import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel, WorkflowRunModel
from bestseller.services import chapter_outline_readiness, outline_field_enrichment
from bestseller.services import workflows as service
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _Store:
    def __init__(self):
        self.rows = {}
        self.added = []

    async def get(self, model, identity, **kwargs):
        # Accept AsyncSession lookup options; this in-memory identity store
        # does not claim to exercise database row locks or concurrent refresh.
        return self.rows.get((model, identity))

    async def scalar(self, statement):
        return None

    async def scalars(self, statement):
        return []

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid4()


def _batch():
    return ChapterOutlineBatchInput.model_validate({"batch_name": "approved-window", "chapters": [{
        "chapter_number": 1, "title": "The sealed register",
        "goal": "Mara compares the two dated receipts before the depot closes.",
        "opening_situation": "The steward holds a receipt against the register.",
        "main_conflict": "The steward refuses access until Mara pays the unpaid fee.",
        "hook_description": "The second receipt carries an earlier date.",
        "key_reveals": ["The calibration record belongs to a separate investigation."],
        "target_emotion": "unease", "protagonist_inner_state": "Mara fears losing the receipt.",
        "causal_contract": {
            "chapter_function": "action", "pressure": "The depot closes at dusk.",
            "protagonist_desire": "Mara wants to recover the disputed parcel.",
            "protagonist_choice": "Mara chooses to pay the fee and inspect the register.",
            "visible_action_or_reaction": "Mara places the coins beside the receipt.",
            "resistance": "The steward keeps the register behind the counter.",
            "cost_or_tradeoff": "Mara spends her remaining fare to obtain access.",
            "gain_or_reveal": "The two receipts record different dates for the same parcel.",
            "state_change": "The steward grants Mara access to the sealed register.",
            "next_reader_desire": "Who changed the recorded delivery date?",
        },
        "scenes": [{
            "scene_number": 1, "scene_type": "confrontation", "title": "The counter",
            "time_label": "At dusk, before the depot closes",
            "participants": ["Mara", "Steward"],
            "purpose": {"story": "Mara pays the fee; Steward opens the register.",
                        "emotion": "Mara fears the parcel has already been sold."},
            "entry_state": {"summary": "The register is sealed."},
            "exit_state": {"summary": "The two dates contradict each other."},
            "hook_requirement": "Show the altered date without explaining its origin.",
            "methodology_contract": {"conflict_stakes": "The depot closes at dusk.",
                "signature_image": "Two wet receipts lie beneath a dry seal.",
                "cut_point": "The steward recognizes the altered date.",
                "information_control_mode": "Reveal the date, withhold its author."},
            "information_held_back": ["The person who altered the date remains unknown."],
        }],
    }]})


@pytest.fixture
def rig(monkeypatch, isolate_settings_environment):
    settings = load_settings()
    url = make_url(settings.database.url)
    assert url.get_backend_name() == "sqlite"
    assert url.database == ":memory:" or "test" in (url.database or "").rsplit("/", 1)[-1]
    settings.pipeline.enable_outline_llm_commercial_judge = False
    settings.pipeline.enable_outline_reader_experience_judge = False
    monkeypatch.setattr(service, "load_settings", lambda: settings)
    project = ProjectModel(id=uuid4(), slug="approved-source-test", title="Test",
        genre="general-fiction", target_chapters=9, target_word_count=20000,
        metadata_json={"identity_manifest_status": "locked", "identity_manifest": [
            {"name": "Mara", "role": "protagonist", "gender": "female"},
            {"name": "Steward", "role": "supporting", "gender": "unknown"},
        ]})
    store, batch = _Store(), _batch()
    source = PlanningArtifactVersionModel(id=uuid4(), project_id=project.id,
        artifact_type="chapter_outline_batch", version_no=2, status="approved",
        schema_version="1.0", content=batch.model_dump(mode="json"))
    store.rows[(PlanningArtifactVersionModel, source.id)] = source
    chapters, scenes = [], []

    async def get_project(*args, **kwargs):
        return project

    async def create_chapter(session, slug, payload):
        row = SimpleNamespace(id=uuid4(), metadata_json={}, **payload.model_dump())
        chapters.append(row)
        return row

    async def create_scene(session, slug, number, payload):
        data = payload.model_dump()
        row = SimpleNamespace(id=uuid4(), metadata_json=data.pop("metadata", {}), **data)
        scenes.append(row)
        return row

    async def no_model_calls(*args, **kwargs):
        raise AssertionError("This regression must never call a model")

    from bestseller.services import llm
    monkeypatch.setattr(llm, "complete_text", no_model_calls)
    monkeypatch.setattr(service, "get_project_by_slug", get_project)
    monkeypatch.setattr(service, "create_chapter", create_chapter)
    monkeypatch.setattr(service, "create_scene_card", create_scene)
    return SimpleNamespace(project=project, store=store, batch=batch, source=source,
                           chapters=chapters, scenes=scenes)


async def _run(rig, *, source=True):
    return await service.materialize_chapter_outline_batch(
        rig.store, rig.project.slug, rig.batch,
        source_artifact_id=rig.source.id if source else None,
    )


@pytest.mark.parametrize("fault", ["missing", "project", "type", "draft", "needs_review", "rejected"])
async def test_source_identity_type_project_and_approval_are_checked_before_workflow_writes(rig, fault):
    if fault == "missing":
        rig.store.rows.clear()
    elif fault == "project":
        rig.source.project_id = uuid4()
    elif fault == "type":
        rig.source.artifact_type = "world_spec"
    else:
        rig.source.status = fault
    with pytest.raises(ValueError, match="approved outline source from this project"):
        await _run(rig)
    assert rig.store.added == [] and rig.chapters == [] and rig.scenes == []


@pytest.mark.parametrize("field,value", [
    ("title", "Different title"), ("chapter_number", 2),
    ("causal_contract", {"pressure": "A different pressure"}),
    ("target_emotion", "triumph"), ("key_reveals", ["An invented revelation"]),
])
async def test_any_changed_typed_chapter_field_is_rejected_before_materialization(rig, field, value):
    setattr(rig.batch.chapters[0], field, value)
    with pytest.raises(ValueError, match="differs from its approved source"):
        await _run(rig)
    assert rig.store.added == [] and rig.chapters == []


@pytest.mark.parametrize("field,value", [
    ("participants", ["Mara"]), ("scene_number", 2),
    ("purpose", {"story": "Read minds without a source."}),
    ("methodology_contract", {"new_rule": "Remote visions are allowed."}),
    ("information_held_back", ["A different withheld fact"]),
])
async def test_any_changed_typed_scene_field_is_rejected_before_materialization(rig, field, value):
    setattr(rig.batch.chapters[0].scenes[0], field, value)
    with pytest.raises(ValueError, match="differs from its approved source"):
        await _run(rig)
    assert rig.store.added == [] and rig.scenes == []


@pytest.mark.parametrize("scope", ["chapter", "scene"])
async def test_source_order_cannot_be_changed_before_projection(rig, scope):
    if scope == "chapter":
        second = rig.batch.chapters[0].model_copy(deep=True)
        second.chapter_number = 2
        rig.batch.chapters.append(second)
    else:
        second = rig.batch.chapters[0].scenes[0].model_copy(deep=True)
        second.scene_number = 2
        rig.batch.chapters[0].scenes.append(second)
    rig.source.content = rig.batch.model_dump(mode="json")
    if scope == "chapter":
        rig.batch.chapters.reverse()
    else:
        rig.batch.chapters[0].scenes.reverse()
    with pytest.raises(ValueError, match="differs from its approved source"):
        await _run(rig)
    assert rig.store.added == [] and rig.chapters == [] and rig.scenes == []


async def test_approved_projection_skips_all_three_narrative_mutators_and_preserves_source(rig, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Approved source must not enter narrative synthesis or ngram clearing")
    monkeypatch.setattr(outline_field_enrichment, "enrich_outline_batch_fields", forbidden)
    monkeypatch.setattr(service, "_apply_metadata_narrative_coherence", forbidden)
    monkeypatch.setattr(service, "_repair_chapter_outline_contract_inputs", forbidden)
    before = copy.deepcopy(rig.source.content)
    result = await _run(rig)
    assert result.source_artifact_id == rig.source.id
    assert result.chapters_created == result.scenes_created == 1
    assert rig.source.content == before
    assert rig.scenes[0].participants == before["chapters"][0]["scenes"][0]["participants"]
    assert rig.scenes[0].purpose == before["chapters"][0]["scenes"][0]["purpose"]
    assert rig.chapters[0].metadata_json["key_reveals"] == before["chapters"][0]["key_reveals"]
    workflow = next(row for row in rig.store.added if isinstance(row, WorkflowRunModel))
    assert workflow.status == "completed"
    assert not {"outline_field_enrichment", "metadata_coherence_cleared_fields",
                "chapter_plan_contract_input_repair"} & workflow.metadata_json.keys()


async def test_approval_does_not_bypass_unknown_identity_gate(rig):
    rig.batch.chapters[0].scenes[0].participants.append("Unregistered actor")
    rig.source.content = rig.batch.model_dump(mode="json")
    with pytest.raises(ValueError, match="PLAN_SCENE_UNKNOWN_PARTICIPANT|chapter_plan_contract"):
        await _run(rig)
    assert rig.chapters == [] and rig.scenes == []


async def test_approval_does_not_bypass_causality_gate(rig):
    settings = service.load_settings()
    settings.pipeline.enable_chapter_causality_gate = True
    settings.pipeline.chapter_causality_gate_block_on_failure = True
    chapter = rig.batch.chapters[0]
    chapter.causal_contract = {}
    chapter.methodology_contract = {}
    chapter.main_conflict = "Blue ink lies on a clean page."
    chapter.opening_situation = "A blue page rests on a desk."
    chapter.chapter_goal = "Read a blue page."
    chapter.hook_description = "The blue page remains plain."
    scene = chapter.scenes[0]
    scene.purpose = {"story": "Mara reads an ordinary blue page."}
    scene.entry_state = {"summary": "A blue page."}
    scene.exit_state = {"summary": "A blue page."}
    scene.methodology_contract = {}
    scene.hook_requirement = None
    rig.source.content = rig.batch.model_dump(mode="json")
    assert service.evaluate_chapter_causality_contract(rig.batch).blocking_findings
    with pytest.raises(ValueError, match="chapter_causality_contract"):
        await _run(rig)
    assert rig.chapters == [] and rig.scenes == []


async def test_prefix_uses_real_receipt_verifier_and_stops_before_workflow_writes(rig):
    rig.source.content["_meta"] = {chapter_outline_readiness.RECEIPT_KEY: {
        "schema": chapter_outline_readiness.SCHEMA, "workflow_run_id": str(uuid4()),
    }}
    with pytest.raises(ValueError, match="prefix_workflow_not_completed"):
        await _run(rig)
    assert rig.store.added == [] and rig.chapters == [] and rig.scenes == []


async def test_raw_entry_keeps_legacy_structural_helpers(rig, monkeypatch):
    calls = []
    for owner, name in ((outline_field_enrichment, "enrich_outline_batch_fields"),
                        (service, "_apply_metadata_narrative_coherence"),
                        (service, "_repair_chapter_outline_contract_inputs")):
        original = getattr(owner, name)

        def wrapped(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)
        monkeypatch.setattr(owner, name, wrapped)
    await _run(rig, source=False)
    assert calls == ["enrich_outline_batch_fields", "_apply_metadata_narrative_coherence",
                     "_repair_chapter_outline_contract_inputs"]


async def test_typed_alias_comparison_never_mutates_original_approved_json(rig, monkeypatch):
    # A minimal valid historical export uses an alias and omits optional lists.
    raw = {"chapters": [{"chapter_number": 1, "title": "The register", "goal": "Read it.",
                         "scenes": [{"scene_number": 1, "participants": ["Mara"]}]}]}
    rig.source.content = copy.deepcopy(raw)
    rig.batch = ChapterOutlineBatchInput.model_validate(copy.deepcopy(raw))

    async def reached_workflow_boundary(*args, **kwargs):
        raise RuntimeError("typed source comparison passed")

    monkeypatch.setattr(service, "create_workflow_run", reached_workflow_boundary)
    with pytest.raises(RuntimeError, match="typed source comparison passed"):
        await _run(rig)
    assert rig.source.content == raw


@pytest.mark.parametrize("targets", [(2371, [1237, 1134]), (1717, [437, 769])])
async def test_approved_capacity_and_all_late_word_passes_are_diagnostic_only(rig, targets):
    chapter_target, scene_targets = targets
    chapter = rig.batch.chapters[0]
    chapter.target_word_count = chapter_target
    second = chapter.scenes[0].model_copy(deep=True)
    second.scene_number = 2
    chapter.scenes.append(second)
    for scene, target in zip(chapter.scenes, scene_targets, strict=True):
        scene.target_word_count = target
        scene.key_dialogue_beats = [f"Required beat {n}" for n in range(20)]
    rig.source.content = rig.batch.model_dump(mode="json")
    before = copy.deepcopy(rig.source.content)
    await _run(rig)
    assert rig.batch.model_dump(mode="json") == rig.source.content == before
    assert rig.chapters[0].target_word_count == chapter_target
    assert [scene.target_word_count for scene in rig.scenes] == scene_targets
    workflow = next(row for row in rig.store.added if isinstance(row, WorkflowRunModel))
    assert workflow.metadata_json["scene_capacity_diagnostic_only"] is True
    assert workflow.metadata_json["scene_capacity_warnings"]


async def test_approved_chapter_numbers_are_not_renumbered(rig):
    second = rig.batch.chapters[0].model_copy(deep=True)
    second.chapter_number = 3
    second.chapter_goal = "A different depot closes before the evidence is delivered."
    rig.batch.chapters.append(second)
    rig.source.content = rig.batch.model_dump(mode="json")
    before = copy.deepcopy(rig.source.content)
    await _run(rig)
    assert [row.chapter_number for row in rig.chapters] == [1, 3]
    assert rig.batch.model_dump(mode="json") == rig.source.content == before
