"""Behavioral RED for the short annotation/meta boundary; main runs selectors.

All transports, physical-custody reads and persistence are inert test doubles.
The native Pydantic, role coverage and artifact importer remain real.
"""
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import LlmRunModel, ProjectModel, WorkflowRunModel
from bestseller.services import fanqie_outline_contract_completion as completion
from bestseller.services import fanqie_short_planner as short
from bestseller.services import foundation_field_recovery, llm, planner, projects
from bestseller.services.prewrite_quality_profile import planning_artifact_meta
from bestseller.services.source_bound_planning_content import (
    READER_FIRST_DIMENSIONS, ReaderFirstDirectionRequired, new_reader_first_binding,
)
from support.database_safety import load_test_database_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def candidate():
    return {"batch_name": "synthetic-four-part-outline", "_meta": {
        "quality_profile": "explicit-producer-value", "producer_custody": {"preserve": True}},
        "chapters": [{
            "chapter_number": str(n), "volume_number": 1, "chapter_title": f"Control {n}",
            "goal": f"Control choice {n}", "_meta": {"chapter_custody": {"part": n}},
            "scenes": [{"scene_number": "1", "purpose": {
                "story": f"Control action {n}", "emotion": "Control hesitation",
                "_meta": {"purpose_custody": n}},
                "_meta": {"scene_custody": {"part": n, "unchanged": True}}}],
        } for n in range(1, 5)]}


def normalized(project, original):
    result = ChapterOutlineBatchInput.model_validate(deepcopy(original)).model_dump(mode="json")
    result["_meta"] = {**planning_artifact_meta(project), **deepcopy(original["_meta"])}
    for before, after in zip(original["chapters"], result["chapters"], strict=True):
        after["_meta"] = deepcopy(before["_meta"])
        for old_scene, new_scene in zip(before["scenes"], after["scenes"], strict=True):
            new_scene["_meta"] = deepcopy(old_scene["_meta"])
    return result


class Session:
    def __init__(self, project, workflow):
        self.project, self.workflow = project, workflow
        self.persisted = deepcopy(project.metadata_json)
        self.no_autoflush = nullcontext()
        self.commits, self.paid, self.added = [], {}, []

    async def get(self, model, identity, **kwargs):
        if model is ProjectModel and UUID(str(identity)) == self.project.id:
            return self.project
        if model is WorkflowRunModel and UUID(str(identity)) == self.workflow.id:
            return self.workflow
        if model is LlmRunModel:
            return self.paid[UUID(str(identity))][0]
        return None

    async def scalar(self, query):
        return deepcopy(self.persisted)

    async def commit(self):
        self.persisted = deepcopy(self.project.metadata_json)
        self.commits.append(deepcopy(self.persisted))

    async def flush(self):
        pass


@pytest.fixture
def case(monkeypatch):
    binding = new_reader_first_binding("Synthetic family choice with fixed consequences.")
    project = ProjectModel(id=uuid4(), slug="synthetic-short-roles", title="Synthetic short",
        project_type="fanqie_short", language="en", genre="fiction",
        target_chapters=4, target_word_count=8000, metadata_json={
            "reader_first_planning": binding, "methodology_contract_mode": "off",
            "execution_scope_id": str(uuid4()), "premise": "Synthetic premise"})
    workflow = WorkflowRunModel(id=uuid4(), project_id=project.id,
        workflow_type="fanqie_short_pipeline", status="running", metadata_json={
            "execution_scope_id": project.metadata_json["execution_scope_id"]})
    settings = load_test_database_settings()
    settings.pipeline.require_chapter_plan_contract = False
    settings.pipeline.enable_methodology_planning_readiness_gate = False
    session = Session(project, workflow)
    c = SimpleNamespace(project=project, workflow=workflow, settings=settings, session=session,
        binding=binding, original=candidate(), requests=[], reviewed=[], role_maps={}, custody_reads=[])

    async def selected_sources(*args, **kwargs):
        return project, {"selected_source": {"project_inputs": {"target_chapters": 4},
            "complete_foundation_content": {"book_spec": {}, "world_spec": {}, "cast_spec": {}}},
            "original_beat": {"raw": {"beats": [{"segment_number": n} for n in range(1, 5)]}},
            "base_outline": deepcopy(c.original)}

    async def paid_source(_session, _settings, p, run_id):
        assert p.id == project.id
        c.custody_reads.append(str(run_id))
        return session.paid[UUID(str(run_id))]

    async def author(_session, selected_settings, request):
        assert session.commits, "annotation reservation must precede dispatch"
        state = session.persisted["reader_first_short_field_roles"]
        assert len(state["dispatches"]) == len(c.requests) + 1 <= 2
        assert request.logical_role == "planner"
        assert request.project_id == project.id and request.workflow_run_id == workflow.id
        assert request.prompt_template == "fanqie_reader_first_field_roles"
        assert request.prompt_version == "fanqie-rf-field-roles-v1"
        assert request.metadata["reader_first_stage"] == "fanqie_short_field_roles"
        assert request.metadata["reader_first_planning"] == binding
        assert selected_settings.llm.retry.max_attempts == 1
        document = json.loads(request.user_prompt)
        assert document["schema"] == "fanqie-reader-first-field-roles-request.v1"
        assert document["source_sha256"] == digest(document["outline"])
        assert request.metadata["reader_first_source_sha256"] == document["source_sha256"]
        assert document["annotation_attempt"] == request.metadata["reader_first_field_roles_slot"]
        expected = normalized(project, c.original)
        assert document["outline"] == expected
        maps = {}
        for chapter in expected["chapters"]:
            number = str(chapter["chapter_number"])
            leaves = planner._reader_first_leaf_values(chapter)
            assert document["chapter_leaf_values"][number] == leaves
            # The synthetic author explicitly returns both roles. Application
            # code must preserve this return, not infer a role from field names.
            maps[number] = {p: "continuity_reference" for p in leaves}
            maps[number]["/chapter_goal"] = "narrative_task"
            maps[number]["/scenes/0/purpose/story"] = "narrative_task"
        c.role_maps = deepcopy(maps)
        raw = {"schema": "fanqie-reader-first-field-roles.v1",
            "source_sha256": document["source_sha256"], "chapters": [
                {"chapter_number": int(number), "field_roles": roles} for number, roles in maps.items()]}
        transform = getattr(c, "transform", None)
        if transform:
            transform(raw)
        c.requests.append(request)
        if getattr(c, "interrupt", False):
            raise TimeoutError("synthetic unknown dispatch; no actual IO")
        result = llm.LLMCompletionResult(content=json.dumps(raw), provider="offline-unit-provider",
            model_name="offline-unit-model", llm_run_id=uuid4(), finish_reason="stop",
            input_tokens=10, output_tokens=10)
        row = SimpleNamespace(id=result.llm_run_id, project_id=project.id, workflow_run_id=workflow.id,
            prompt_template=request.prompt_template, prompt_version=request.prompt_version,
            logical_role="planner", metadata_json=deepcopy(request.metadata))
        proof = {"llm_run_id": str(row.id), "effective_input_sha256": digest(request.model_dump(mode="json")),
            "request_file_sha256": digest(document), "response_file_sha256": digest(raw),
            "llm_row_sha256": digest(str(row.id)), "attempt_rows_sha256": digest([str(row.id)]),
            "operation_key": f"fanqie-reader-first-field-roles:{project.id}:{len(c.requests) - 1}"}
        session.paid[row.id] = row, request, result, proof
        return result

    async def reader(_session, _settings, *, project, workflow_run_id, source, repair_candidate=None):
        assert workflow_run_id == workflow.id and callable(repair_candidate)
        planner._reader_first_outline_coverage(source, project, binding)
        c.reviewed.append(deepcopy(source))
        return {"status": "passed", "dimensions": {
            name: {"score": .9, "reason": "Synthetic reviewed relation"}
            for name in READER_FIRST_DIMENSIONS}}

    monkeypatch.setattr(completion, "_sources", selected_sources)
    monkeypatch.setattr(foundation_field_recovery, "_paid", paid_source)
    monkeypatch.setattr(short, "complete_text", author)
    monkeypatch.setattr(llm, "complete_text", author)
    monkeypatch.setattr(planner, "_run_reader_first_outline_checkpoint", reader)
    return c


async def prepare(c):
    return await short.prepare_reader_first_short_outline(c.session, c.settings,
        project_id=c.project.id, outline_payload=c.original, workflow_run_id=c.workflow.id)


async def test_unmarked_returns_same_object_without_annotation_or_reader_dispatch(case):
    case.project.metadata_json = {}
    case.session.persisted = {}
    result = await prepare(case)
    assert result is case.original and not case.requests and not case.reviewed


async def test_normalized_meta_and_authored_roles_precede_reader_and_paid_replay(case):
    before = deepcopy(case.original)
    result = await prepare(case)
    assert case.original == before
    assert result == case.reviewed[-1]["outline"]
    assert len(case.requests) == 1 and case.custody_reads
    for old, chapter in zip(before["chapters"], result["chapters"], strict=True):
        roles = chapter["_meta"]["reader_first_field_roles"]
        assert roles == case.role_maps[str(chapter["chapter_number"])]
        assert set(roles.values()) == {"narrative_task", "continuity_reference"}
        assert chapter["_meta"]["chapter_custody"] == old["_meta"]["chapter_custody"]
        assert chapter["scenes"][0]["_meta"] == old["scenes"][0]["_meta"]
        assert chapter["scenes"][0]["purpose"]["_meta"] == old["scenes"][0]["purpose"]["_meta"]
    assert await prepare(case) == result
    assert len(case.requests) == 1 and len(case.custody_reads) >= 2


@pytest.mark.parametrize("bad", ["missing_role", "story_payload"])
async def test_bad_authored_roles_never_reach_reader_and_stop_after_two_sends(case, bad):
    def corrupt(raw):
        if bad == "missing_role":
            raw["chapters"][0]["field_roles"].pop("/chapter_goal")
        else:
            raw["outline"] = {"chapters": []}
    case.transform = corrupt
    with pytest.raises(ReaderFirstDirectionRequired) as stopped:
        await prepare(case)
    assert len(case.requests) == 2 and not case.reviewed
    receipt = stopped.value.payload
    assert receipt["status"] != "passed" and len(receipt["options"]) >= 2
    assert all(receipt["dimensions"][name]["reason"] for name in READER_FIRST_DIMENSIONS)


async def test_real_importer_roundtrip_preserves_reviewed_top_chapter_scene_and_nested_meta(monkeypatch, case, request):
    from full_flow_q4_short.typed_leaf_differences import complete_leaf_differences

    class ImportSession:
        def __init__(self):
            self.added = []

        async def scalar(self, query):
            return None

        def add(self, row):
            if row.id is None:
                row.id = uuid4()
            self.added.append(row)

        async def flush(self):
            pass

    async def get_project(*args, **kwargs):
        return case.project
    monkeypatch.setattr(projects, "get_project_by_slug", get_project)

    # Reproduce the previous fixture's unmarked input and record its complete
    # actual leaf delta. This is a serialization control, never a reviewed or
    # admitted native prefix. Only safe summaries enter the main-run JUnit.
    unmarked = normalized(case.project, case.original)
    assert all("reader_first_planning" not in ch["_meta"] for ch in unmarked["chapters"])
    control = ImportSession()
    control_id = await short.persist_fanqie_chapter_outline(control, case.project.slug, unmarked)
    control_artifact = next(row for row in control.added if row.id == control_id)
    differences = complete_leaf_differences(unmarked, control_artifact.content)
    request.node.user_properties.append(("unmarked_roundtrip_leaf_differences", json.dumps(differences)))
    assert differences

    # The existing controlled author/reader transport supplies real exhaustive
    # role metadata through prepare. It remains a component fixture, not paid
    # provenance; the separate native admission suite owns that proof.
    reviewed = await prepare(case)
    planner._reader_first_outline_coverage({"outline": reviewed}, case.project, case.binding)
    assert all(ch["_meta"]["reader_first_planning"] == case.binding for ch in reviewed["chapters"])
    before = deepcopy(reviewed)
    session = ImportSession()
    artifact_id = await short.persist_fanqie_chapter_outline(session, case.project.slug, reviewed)
    artifact = next(row for row in session.added if row.id == artifact_id)
    try:
        assert reviewed == before and artifact.content == before
    except AssertionError as error:
        error.code = "marked_roundtrip_content_mismatch"
        error.differences = complete_leaf_differences(
            {"reviewed": before, "stored": before}, {"reviewed": reviewed, "stored": artifact.content},
        )
        error.args = (error.code,)
        raise
    for old, saved in zip(before["chapters"], artifact.content["chapters"], strict=True):
        assert saved["_meta"] == old["_meta"]
        assert saved["scenes"][0]["_meta"] == old["scenes"][0]["_meta"]
        assert saved["scenes"][0]["purpose"]["_meta"] == old["scenes"][0]["purpose"]["_meta"]


async def test_unknown_annotation_is_durable_and_resume_cannot_pay_again(case):
    case.interrupt = True
    with pytest.raises(ReaderFirstDirectionRequired):
        await prepare(case)
    durable = deepcopy(case.session.persisted["reader_first_short_field_roles"])
    with pytest.raises(ReaderFirstDirectionRequired):
        await prepare(case)
    assert len(case.requests) == 1 and not case.reviewed
    assert case.session.persisted["reader_first_short_field_roles"] == durable
