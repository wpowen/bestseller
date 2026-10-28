"""Q4 planner behavior only. Every transport and persistence boundary is fake."""
from __future__ import annotations

import copy
from contextlib import nullcontext
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner
from bestseller.services import source_bound_planning_content as protocol
from bestseller.services import workflows
from bestseller import settings as settings_module
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@pytest.fixture
def project():
    return ProjectModel(id=uuid4(), slug="q4-planner", title="回家吃饭", genre="都市",
                        target_chapters=12, target_word_count=31200,
                        metadata_json={"premise": "一家人决定重新一起吃晚饭。",
                                       "reader_first_planning": protocol.new_reader_first_binding(
                                           "一家人决定重新一起吃晚饭。")})


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    value = load_settings(config_path=Path("config/default.yaml"),
                          local_config_path=Path("config/does-not-exist.yaml"),
                          env={"BESTSELLER__DATABASE__URL": json.dumps("sqlite:///:memory:")})
    monkeypatch.setattr(settings_module, "get_settings", lru_cache()(lambda: value))
    monkeypatch.setattr(planner, "get_settings", lambda: value)
    return value


def outline(count=12):
    candidate = {"batch_name": "full", "chapters": [
        {"chapter_number": i, "volume_number": 1, "title": f"第{i}次晚饭",
         "chapter_goal": f"第{i}章她想等父亲回来，父亲不肯回答离家的原因。",
         "scenes": [{"scene_number": 1, "purpose": {
             "story": "她留下空椅子，父亲终于坐下。", "emotion": "想念又怕被拒绝。"}}]}
        for i in range(1, count + 1)]}
    for chapter in candidate["chapters"]:
        chapter["_meta"] = {
            "reader_first_planning": protocol.new_reader_first_binding("一家人决定重新一起吃晚饭。"),
            "writer_delivery_schema": "approved-current-chapter-delivery.v3-reader-first",
            "reader_first_field_roles": {pointer: "narrative_task" for pointer in fixture_leaves(chapter)},
        }
    return candidate


def fixture_leaves(value, path=""):
    if isinstance(value, dict) and value:
        return {p: v for key, child in value.items() if key != "_meta"
                for p, v in fixture_leaves(child, path + "/" + key.replace("~", "~0").replace("/", "~1")).items()}
    if isinstance(value, list) and value:
        return {p: v for i, child in enumerate(value) for p, v in fixture_leaves(child, path + f"/{i}").items()}
    return {path: value}


def source(candidate):
    return {"book_spec": {"reader_promise": "让家人重新开口"}, "world_spec": {},
            "cast_spec": {}, "volume_plan": [{"volume_number": 1, "chapter_count_target": 12}],
            "outline": copy.deepcopy(candidate)}


class Session:
    def __init__(self, project):
        self.project = project
        self.persisted = copy.deepcopy(project.metadata_json)
        self.commits = []
        self.no_autoflush = nullcontext()

    async def scalar(self, _query):
        return copy.deepcopy(self.persisted)

    async def commit(self):
        self.persisted = copy.deepcopy(self.project.metadata_json)
        self.commits.append(copy.deepcopy(self.persisted))

    async def flush(self):
        pass


def gateway(monkeypatch, session, scores=(.9,), mutate=None):
    calls = []

    async def complete(_session, _settings, request):
        data = json.loads(request.user_prompt)
        assert session.commits, "quota must be durable before dispatch"
        state = session.persisted["reader_first_outline_checkpoint"]
        assert state["review_dispatches"] == len(calls) + 1
        assert request.project_id == session.project.id and request.workflow_run_id
        assert request.max_tokens_override == 4096
        assert _settings.llm.retry.max_attempts == 1
        assert _settings.llm.retry.rate_limit_max_attempts == 1
        assert not _settings.llm.retry.rate_limit_fallback_enabled
        index = min(len(calls), len(scores) - 1)
        calls.append(request)
        candidate = data["source"]
        chapter = candidate["outline"]["chapters"][-1]
        goal_key = "chapter_goal" if "chapter_goal" in candidate["outline"]["chapters"][0] else "goal"
        raw = {"schema": "reader-first-review.v1", "checkpoint": "outline",
               "source_sha256": digest(candidate), "config_sha256": data["config_sha256"],
               "dimensions": {name: {"score": scores[index], "reason": "人物选择改变关系。",
                   "evidence": [{"path": "/outline/chapters/0/" + goal_key,
                                 "quote": candidate["outline"]["chapters"][0][goal_key]}]}
                              for name in protocol.READER_FIRST_DIMENSIONS},
               "issues": [], "options": ["保留家人关系，让父亲先回答离家的原因。", "保留结局，重排女儿选择开口的时机。"]}
        assert chapter["chapter_number"] == session.project.target_chapters
        if mutate:
            mutate(raw)
        return SimpleNamespace(content=json.dumps(raw, ensure_ascii=False), provider="test-gateway",
                               llm_run_id=uuid4(), fallback_used=False, finish_reason="stop")

    async def repair(_session, _settings, **kwargs):
        assert session.persisted["reader_first_outline_checkpoint"]["repairs_used"] == 1
        value = copy.deepcopy(kwargs["outline_payload"])
        value["chapters"][-1]["chapter_goal"] += "她先选择向父亲开口。"
        return value, uuid4()
    monkeypatch.setattr(planner, "_repair_outline_commercial_findings_surgically", repair)
    monkeypatch.setattr(planner, "complete_text", complete)
    return calls


async def checkpoint(session, settings, project, candidate, **kwargs):
    return await planner._run_reader_first_outline_checkpoint(
        session, settings, project=project, workflow_run_id=uuid4(),
        source=source(candidate), **kwargs)


@pytest.mark.asyncio
async def test_complete_range_pass_is_bound_and_old_pass_cannot_survive_edit(monkeypatch, project, settings):
    session = Session(project)
    calls = gateway(monkeypatch, session)
    candidate = outline()
    original = copy.deepcopy(candidate)
    first = await checkpoint(session, settings, project, candidate)
    assert first["status"] == "passed" and first["thresholds_calibrated"] is False
    assert candidate == original
    await checkpoint(session, settings, project, candidate)
    assert len(calls) == 1
    candidate["chapters"][-1]["chapter_goal"] = "她选择把那张空椅子搬到父亲身边。"
    second = await checkpoint(session, settings, project, candidate)
    assert len(calls) == 2 and second["source_sha256"] != first["source_sha256"]
    assert "第12章" in calls[0].user_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["failed", "missing_dimension", "bad_quote", "nan", "truncated", "fallback", "error"])
async def test_failure_is_retained_and_resume_cannot_dispatch_again(monkeypatch, project, settings, kind):
    session = Session(project)
    def mutate(raw):
        if kind == "missing_dimension":
            raw["dimensions"].pop(protocol.READER_FIRST_DIMENSIONS[-1])
        elif kind == "bad_quote":
            raw["dimensions"][protocol.READER_FIRST_DIMENSIONS[0]]["evidence"][0]["quote"] = "没有发生过的话"
        elif kind == "nan":
            raw["dimensions"][protocol.READER_FIRST_DIMENSIONS[0]]["score"] = float("nan")
    calls = gateway(monkeypatch, session, scores=(.2,), mutate=mutate)
    if kind in {"truncated", "fallback", "error"}:
        initial = planner.complete_text
        async def invalid(*args):
            result = await initial(*args)
            if kind == "error":
                raise RuntimeError("transport unavailable")
            result.finish_reason = "length" if kind == "truncated" else "stop"
            result.fallback_used = kind == "fallback"
            return result
        monkeypatch.setattr(planner, "complete_text", invalid)
    with pytest.raises(protocol.ReaderFirstDirectionRequired) as raised:
        await checkpoint(session, settings, project, outline())
    payload = raised.value.payload
    assert payload["status"] == ("failed" if kind == "failed" else "unknown")
    assert set(payload["scores"]) == set(protocol.READER_FIRST_DIMENSIONS)
    assert payload["reasons"] and 2 <= len(payload["options"]) <= 3
    state = session.persisted["reader_first_outline_checkpoint"]
    assert state["history"][0]["source"] == source(outline())
    project.metadata_json = copy.deepcopy(session.persisted)
    with pytest.raises(protocol.ReaderFirstDirectionRequired):
        await checkpoint(session, settings, project, outline())
    assert len(calls) == (2 if kind == "failed" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing_chapter", "duplicate", "scene_missing", "capacity"])
async def test_no_partial_or_truncated_outline_review(monkeypatch, project, settings, kind):
    session = Session(project)
    calls = gateway(monkeypatch, session)
    candidate = outline()
    if kind == "missing_chapter":
        candidate["chapters"].pop()
    elif kind == "duplicate":
        candidate["chapters"][-1]["chapter_number"] = 1
    elif kind == "scene_missing":
        candidate["chapters"][-1]["scenes"] = []
    else:
        candidate["chapters"][-1]["chapter_goal"] = "不能删去的完整来源事实。" * 30000
    with pytest.raises(protocol.ReaderFirstDirectionRequired) as raised:
        await checkpoint(session, settings, project, candidate)
    assert raised.value.payload["status"] == "unknown" and not calls
    assert raised.value.payload["reasons"]
    assert session.persisted["reader_first_outline_checkpoint"]["history"][-1]["source"] == source(candidate)


@pytest.mark.asyncio
async def test_repair_quota_precedes_callback_and_survives_changed_source(monkeypatch, project, settings):
    session = Session(project)
    calls = gateway(monkeypatch, session, scores=(.2, .2))
    repairs = []
    async def repair(candidate, receipt):
        assert session.persisted["reader_first_outline_checkpoint"]["repairs_used"] == 1
        assert receipt["status"] == "failed"
        repairs.append(True)
        value = copy.deepcopy(candidate)
        value["outline"]["chapters"][-1]["chapter_goal"] += "她最终决定开口。"
        return value
    with pytest.raises(protocol.ReaderFirstDirectionRequired):
        await checkpoint(session, settings, project, outline(), repair_candidate=repair)
    assert len(calls) == 2 and len(repairs) == 1
    changed = outline()
    changed["chapters"][0]["chapter_goal"] += "这次父亲先坐下。"
    project.metadata_json = copy.deepcopy(session.persisted)
    with pytest.raises(protocol.ReaderFirstDirectionRequired):
        await checkpoint(session, settings, project, changed, repair_candidate=repair)
    assert len(calls) == 2 and len(repairs) == 1


@pytest.mark.asyncio
async def test_legacy_checkpoint_never_calls_transport_or_persistence(monkeypatch, project, settings):
    project.metadata_json.pop("reader_first_planning")
    session = Session(project)
    monkeypatch.setattr(planner, "complete_text", lambda *_: pytest.fail("legacy transport"))
    assert await checkpoint(session, settings, project, outline()) is None
    assert not session.commits


@pytest.mark.parametrize("language", ["zh", "en"])
def test_prompts_keep_user_source_and_add_only_bound_system_guidance(project, language):
    project.metadata_json["writing_profile"] = {"language": language}
    binding = project.metadata_json.pop("reader_first_planning")
    legacy = planner._book_spec_prompts(project, "既定事实14:37，父亲已答应回家。", {})
    project.metadata_json["reader_first_planning"] = binding
    current = planner._book_spec_prompts(project, "既定事实14:37，父亲已答应回家。", {})
    assert current[1] == legacy[1]
    assert current[0].startswith(legacy[0]) and current[0] != legacy[0]
    assert "reader-first-planning.v1" in current[0]
    project.metadata_json.pop("reader_first_planning")
    assert planner._book_spec_prompts(project, "既定事实14:37，父亲已答应回家。", {}) == legacy


def test_bound_outline_derivation_copies_facts_and_never_invents_procedure(project):
    candidate = outline(1)
    chapter = candidate["chapters"][0]
    chapter["reader_first_planning"] = project.metadata_json["reader_first_planning"]
    chapter["required_payoff"] = "父亲第一次主动回答女儿。"
    chapter["scenes"][0].update(time_label="14:37", participants=["女儿", "父亲"],
                                  scene_type="relationship", entry_state={"chair": "空着"},
                                  exit_state={"chair": "父亲坐下"})
    batch = ChapterOutlineBatchInput.model_validate(candidate)
    workflows._repair_chapter_outline_contract_inputs(batch, identity_manifest=[])
    contract = batch.chapters[0].methodology_contract
    assert contract.get("required_payoff") == chapter["required_payoff"]
    assert not contract.get("cost") and not contract.get("protagonist_choice")
    assert not batch.chapters[0].scenes[0].methodology_contract.get("relationship_debts")
    assert batch.chapters[0].scenes[0].time_label == "14:37"


def test_input_identity_is_versioned_without_changing_legacy_hash():
    args = dict(logical_name="book_spec", system_prompt="system", user_prompt="user", fallback_payload={})
    assert planner._planner_artifact_input_hash(**args) == hashlib.sha256(planner._json_dumps(args).encode()).hexdigest()
    binding = protocol.new_reader_first_binding("raw source")
    assert planner._planner_artifact_input_hash(**args, reader_first=binding) != planner._planner_artifact_input_hash(**args)


@pytest.mark.asyncio
async def test_pending_reservation_blocks_a_different_source_worker(monkeypatch, project, settings):
    session = Session(project)
    session.persisted["reader_first_outline_checkpoint"] = {
        "schema": "reader-first-outline-checkpoint.v1", "review_dispatches": 1,
        "repairs_used": 1, "revision": 7, "status": "repair_reserved",
        "lease_workflow_run_id": str(uuid4()), "history": []}
    frozen = copy.deepcopy(session.persisted)
    calls = gateway(monkeypatch, session)
    with pytest.raises(protocol.ReaderFirstDirectionRequired) as raised:
        await checkpoint(session, settings, project, outline())
    assert any("pending" in reason.get("code", "") for reason in raised.value.payload["reasons"])
    assert not calls and not session.commits and session.persisted == frozen


@pytest.mark.asyncio
async def test_every_save_merges_latest_metadata_and_rejects_checkpoint_revision_race(monkeypatch, project, settings):
    session = Session(project)
    gateway(monkeypatch, session)
    initial = planner.complete_text
    async def concurrent(*args):
        result = await initial(*args)
        session.persisted["unrelated_parent_metadata"] = {"keep": "fresh"}
        session.persisted["reader_first_outline_checkpoint"]["revision"] += 1
        session.persisted["reader_first_outline_checkpoint"]["repairs_used"] = 1
        return result
    monkeypatch.setattr(planner, "complete_text", concurrent)
    with pytest.raises(protocol.ReaderFirstDirectionRequired):
        await checkpoint(session, settings, project, outline())
    assert session.persisted["unrelated_parent_metadata"] == {"keep": "fresh"}
    assert session.persisted["reader_first_outline_checkpoint"]["repairs_used"] == 1
    assert len(session.commits) == 1


@pytest.mark.asyncio
async def test_unrelated_metadata_update_is_preserved_on_success(monkeypatch, project, settings):
    session = Session(project)
    gateway(monkeypatch, session)
    initial = planner.complete_text
    async def concurrent(*args):
        result = await initial(*args)
        session.persisted["unrelated_parent_metadata"] = ["fresh"]
        return result
    monkeypatch.setattr(planner, "complete_text", concurrent)
    await checkpoint(session, settings, project, outline())
    assert session.persisted["unrelated_parent_metadata"] == ["fresh"]


@pytest.mark.parametrize("broken", ["missing_role", "extra_role", "null_role", "nested_empty_role"])
def test_complete_authored_role_coverage_is_not_inferred(project, broken):
    value = source(outline())
    chapter = value["outline"]["chapters"][0]
    roles = chapter["_meta"]["reader_first_field_roles"]
    if broken == "missing_role":
        roles.pop("/chapter_goal")
    elif broken == "extra_role":
        roles["/nonexistent"] = "narrative_task"
    elif broken == "null_role":
        chapter["explicit_unknown"] = None
    else:
        chapter["context"] = {"empty": []}
    with pytest.raises(ValueError, match="authored_field_roles"):
        planner._reader_first_outline_coverage(value, project, project.metadata_json["reader_first_planning"])


def test_normal_outline_roles_survive_real_pydantic_and_native_patch(project):
    original = outline()
    # Exercise the actual schema used by native outline validation, including
    # nested metadata which remains retained but is excluded from role leaves.
    original["chapters"][0]["scenes"][0]["_meta"] = {"source_note": "不可删的来源"}
    model = ChapterOutlineBatchInput.model_validate(original)
    result = model.model_dump(mode="json", by_alias=True)
    assert result["chapters"][0]["_meta"] == original["chapters"][0]["_meta"]
    # SceneCardInput drops unknown keys in its *view*. The marked native
    # validator must therefore return the untouched original candidate below.
    assert "_meta" not in result["chapters"][0]["scenes"][0]
    repaired = planner._apply_outline_commercial_patches(original, [{"chapter_number": 12,
        "scope": "chapter", "field": "chapter_goal", "value": "她决定等父亲自己开口，父亲终于坐下。"}], strict=True)
    assert repaired["chapters"][0]["_meta"] == original["chapters"][0]["_meta"]
    planner._reader_first_outline_coverage(source(repaired), project, project.metadata_json["reader_first_planning"])


@pytest.mark.asyncio
async def test_exact_reviewed_outline_survives_real_import_without_database(monkeypatch, project):
    from bestseller.domain.enums import ArtifactType
    from bestseller.domain.planning import PlanningArtifactCreate
    from bestseller.services import projects
    value = outline()
    value["_meta"] = planner.planning_artifact_meta(project)
    class ImportSession:
        def __init__(self):
            self.added = []
        async def scalar(self, _query):
            return None
        def add(self, item):
            self.added.append(item)
        async def flush(self):
            pass
    session = ImportSession()
    async def get_project(*_):
        return project
    monkeypatch.setattr(projects, "get_project_by_slug", get_project)
    artifact = await projects.import_planning_artifact(session, project.slug, PlanningArtifactCreate(
        artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH, content=copy.deepcopy(value)))
    assert artifact.content == value
    assert artifact.content["chapters"][0]["_meta"] == value["chapters"][0]["_meta"]


@pytest.mark.parametrize("language", ["zh", "en"])
def test_six_legacy_prompt_pairs_are_byte_equal_to_pinned_head(project, language):
    import ast
    import subprocess
    # Fixed local revision, scoped to the owned file; no network or git writes.
    old_source = subprocess.check_output(["git", "show",
        "f7cc7dc380907669fa7afd8891c17e6927e365e5:src/bestseller/services/planner.py"], text=True)
    names = {"_book_spec_prompts", "_world_spec_prompts", "_cast_spec_prompts",
             "_volume_plan_prompts", "_outline_prompts", "_volume_outline_prompts"}
    nodes = [node for node in ast.parse(old_source).body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = dict(vars(planner))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<pinned-planner>", "exec"), namespace)
    project.metadata_json = {"writing_profile": {"language": language}}
    args = {"_book_spec_prompts": (project, "既定事实14:37。", {}),
            "_world_spec_prompts": (project, "既定事实14:37。", {}),
            "_cast_spec_prompts": (project, {}, {}),
            "_volume_plan_prompts": (project, {}, {}, {}),
            "_outline_prompts": (project, {}, {}, [{"volume_number": 1, "chapter_count_target": 12}]),
            "_volume_outline_prompts": (project, {}, {}, [{"volume_number": 1, "chapter_count_target": 12}],
                                        {"volume_number": 1, "chapter_count_target": 12})}
    for name, values in args.items():
        assert getattr(planner, name)(*values) == namespace[name](*values), name


def test_source_bound_rolling_early_return_has_the_new_system_contract(monkeypatch, project):
    from bestseller.services import source_bound_rolling_outline_prompt as rolling
    monkeypatch.setattr(planner, "_source_bound_cast_enabled", lambda _: True)
    monkeypatch.setattr(planner, "_outline_judge_project_brief", lambda *_, **__: {})
    monkeypatch.setattr(rolling, "build_source_bound_rolling_outline_prompt",
                        lambda **_: ("original system", "complete raw source14:37"))
    system, user = planner._volume_outline_prompts(project, {}, {}, [], {})
    assert system.startswith("original system") and "reader-first-planning.v1" in system
    assert user == "complete raw source14:37"


def test_real_normal_native_validator_preserves_authored_candidate_and_nested_metadata(project):
    binding = project.metadata_json.pop("reader_first_planning")
    book = planner._fallback_book_spec(project, "一家人决定重新一起吃晚饭。")
    world = planner._fallback_world_spec(project, "一家人决定重新一起吃晚饭。", book)
    cast = planner._fallback_cast_spec(project, "一家人决定重新一起吃晚饭。", book, world)
    volumes = [{"volume_number": 1, "chapter_count_target": 12, "volume_goal": "让家人重新开口"}]
    raw = planner._fallback_chapter_outline_batch(project, book, cast, volumes)
    # The historical fixture producer supplies a complete schema. This does
    # not claim the generated story is good, or authorize it in production.
    normalized = planner._validate_generated_volume_outline_or_raise(raw, project=project,
        logical_name="volume_1_chapter_outline", volume_number=1, expected_count=12,
        chapter_number_offset=1, cast_spec=cast, strict_story_effects=False,
        strict_field_degeneracy=False)
    project.metadata_json["reader_first_planning"] = binding
    normalized["chapters"][0]["scenes"][0]["_meta"] = {"source_note": "不可删的来源"}
    for chapter in normalized["chapters"]:
        chapter["_meta"] = {"reader_first_planning": binding,
            "writer_delivery_schema": "approved-current-chapter-delivery.v3-reader-first",
            "reader_first_field_roles": {p: "narrative_task" for p in fixture_leaves(chapter)}}
    original = copy.deepcopy(normalized)
    accepted = planner._validate_generated_volume_outline_or_raise(normalized, project=project,
        logical_name="volume_1_chapter_outline", volume_number=1, expected_count=12,
        chapter_number_offset=1, cast_spec=cast, strict_story_effects=False,
        strict_field_degeneracy=False)
    assert accepted == original
    planner._reader_first_outline_coverage(source(accepted), project, binding)


@pytest.mark.asyncio
async def test_actual_native_repair_and_full_source_reevaluation_pass(monkeypatch, project, settings):
    native_repair = planner._repair_outline_commercial_findings_surgically
    value = complete_native_source(project)
    session = Session(project)
    async def titles(*_, **__):
        return []
    monkeypatch.setattr(planner, "_fetch_existing_chapter_titles", titles)
    calls = gateway(monkeypatch, session, scores=(.2, .9))
    monkeypatch.setattr(planner, "_repair_outline_commercial_findings_surgically", native_repair)
    initial = planner.complete_text
    native_requests = []
    async def complete(_session, bounded, request):
        if request.logical_role == "critic":
            return await initial(_session, bounded, request)
        state = session.persisted["reader_first_outline_checkpoint"]
        assert state["repairs_used"] == 1 and state["review_dispatches"] == 1
        assert request.project_id == project.id and request.workflow_run_id
        assert "COMPLETE READER-FIRST SOURCE" in request.user_prompt
        assert value["outline"]["chapters"][-1]["goal"] in request.user_prompt
        assert bounded.llm.retry.max_attempts == 1
        native_requests.append(request)
        return SimpleNamespace(content=json.dumps({"patches": [{"chapter_number": 1,
            "scope": "chapter", "field": "goal", "value": "她决定先向父亲开口，父亲终于坐下。"}]}),
            provider="test-gateway", llm_run_id=uuid4(), fallback_used=False, finish_reason="stop")
    monkeypatch.setattr(planner, "complete_text", complete)
    original = copy.deepcopy(value)
    receipt = await planner._run_reader_first_outline_checkpoint(session, settings,
        project=project, workflow_run_id=uuid4(), source=value)
    assert len(calls) == 2 and len(native_requests) == 1 and receipt["status"] == "passed"
    assert value["outline"]["chapters"][-1]["goal"] != original["outline"]["chapters"][-1]["goal"]
    assert {k: v for k, v in value.items() if k != "outline"} == {k: v for k, v in original.items() if k != "outline"}
    state = session.persisted["reader_first_outline_checkpoint"]
    assert state["history"][0]["receipt"]["status"] == "failed"
    assert state["history"][-1]["source"] == value
    assert state["history"][-1]["receipt"] == receipt
    assert state["repairs_used"] == 1 and state["review_dispatches"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("used", [0, 1])
async def test_crash_after_durable_failed_review_resumes_only_unused_repair(monkeypatch, project, settings, used):
    class Crash(BaseException):
        pass
    class CrashSession(Session):
        crash = True
        async def commit(self):
            await super().commit()
            state = self.persisted.get("reader_first_outline_checkpoint", {})
            if self.crash and state.get("status") == "failed":
                self.crash = False
                raise Crash()
    session = CrashSession(project)
    calls = gateway(monkeypatch, session, scores=(.2, .9))
    with pytest.raises(Crash):
        await checkpoint(session, settings, project, outline())
    state = session.persisted["reader_first_outline_checkpoint"]
    assert state["status"] == "failed" and state["review_dispatches"] == 1 and state["repairs_used"] == 0
    if used:
        state["repairs_used"] = 1
        project.metadata_json = copy.deepcopy(session.persisted)
        with pytest.raises(protocol.ReaderFirstDirectionRequired):
            await checkpoint(session, settings, project, outline())
        assert len(calls) == 1
    else:
        receipt = await checkpoint(session, settings, project, outline())
        assert receipt["status"] == "passed" and len(calls) == 2
        assert session.persisted["reader_first_outline_checkpoint"]["repairs_used"] == 1


@pytest.mark.asyncio
async def test_native_reader_repair_runs_real_full_candidate_domain_validation(monkeypatch, project, settings):
    """A reader score cannot waive the existing cast/scene/volume contracts."""
    native = planner._repair_outline_commercial_findings_surgically
    session = Session(project)
    gateway(monkeypatch, session)
    monkeypatch.setattr(planner, "_repair_outline_commercial_findings_surgically", native)
    validated = []
    original_validate = planner._validate_generated_volume_outline_or_raise
    def validate(value, **kwargs):
        validated.append(kwargs)
        return original_validate(value, **kwargs)
    monkeypatch.setattr(planner, "_validate_generated_volume_outline_or_raise", validate)
    async def titles(*_, **kwargs):
        assert kwargs["exclude_volume_number"] == 1
        return [(2, "别卷已用的标题")]
    monkeypatch.setattr(planner, "_fetch_existing_chapter_titles", titles)
    async def complete(*_args):
        return SimpleNamespace(content=json.dumps({"patches": [{"chapter_number": 12,
            "scope": "chapter", "field": "chapter_goal", "value": "她决定先向父亲开口。"}]}),
            provider="test-gateway", llm_run_id=uuid4(), fallback_used=False, finish_reason="stop")
    monkeypatch.setattr(planner, "complete_text", complete)
    from bestseller.services.title_dedup import TitleCollisionError
    with pytest.raises(TitleCollisionError):
        await native(session, settings, project=project, workflow_run_id=uuid4(),
            logical_name="reader_first_full_outline", outline_payload=outline(),
            directives=["修复人物选择"], reader_first_source=source(outline()))
    assert validated and validated[0]["cast_spec"] == source(outline())["cast_spec"]
    assert validated[0]["existing_titles"] == [(2, "别卷已用的标题")]
    assert validated[0]["expected_count"] == 12 and validated[0]["chapter_number_offset"] == 1


def complete_native_source(project):
    """Complete native schema fixture, prepared without any model or database."""
    project.target_chapters = 1
    project.target_word_count = 2600
    binding = project.metadata_json.pop("reader_first_planning")
    book = planner._fallback_book_spec(project, "一家人决定重新一起吃晚饭。")
    world = planner._fallback_world_spec(project, "一家人决定重新一起吃晚饭。", book)
    cast = planner._fallback_cast_spec(project, "一家人决定重新一起吃晚饭。", book, world)
    volumes = [{"volume_number": 1, "chapter_count_target": 1, "volume_goal": "让家人重新开口"}]
    raw = planner._fallback_chapter_outline_batch(project, book, cast, volumes)
    normalized = planner._validate_generated_volume_outline_or_raise(raw, project=project,
        logical_name="volume_1_chapter_outline", volume_number=1, expected_count=1,
        chapter_number_offset=1, cast_spec=cast, strict_story_effects=False,
        strict_field_degeneracy=False)
    project.metadata_json["reader_first_planning"] = binding
    for chapter in normalized["chapters"]:
        chapter["_meta"] = {"reader_first_planning": binding,
            "writer_delivery_schema": "approved-current-chapter-delivery.v3-reader-first",
            "reader_first_field_roles": {p: "narrative_task" for p in fixture_leaves(chapter)}}
    return {"book_spec": {"reader_promise": "让家人重新开口"}, "world_spec": {},
            "cast_spec": cast, "volume_plan": volumes, "outline": normalized}


@pytest.mark.asyncio
async def test_full_candidate_native_validation_covers_every_volume_and_peer_titles(monkeypatch, project):
    project.target_chapters = 2
    value = source(outline(2))
    value["outline"]["chapters"][1]["volume_number"] = 2
    value["outline"]["chapters"][1]["title"] = "他终于回头"
    value["volume_plan"] = [
        {"volume_number": 1, "chapter_count_target": 1},
        {"volume_number": 2, "chapter_count_target": 1}]
    called = []
    async def titles(*_, **__):
        return []
    def validate(candidate, **kwargs):
        called.append((copy.deepcopy(candidate), kwargs))
        return copy.deepcopy(candidate)
    monkeypatch.setattr(planner, "_fetch_existing_chapter_titles", titles)
    monkeypatch.setattr(planner, "_validate_generated_volume_outline_or_raise", validate)
    result = await planner._validate_reader_first_native_outline(Session(project), project=project,
        logical_name="reader_first_full_outline", source=value, outline_payload=value["outline"])
    assert result == value["outline"]
    assert [item[1]["chapter_number_offset"] for item in called] == [1, 2]
    assert [item[1]["expected_count"] for item in called] == [1, 1]
    assert called[0][1]["existing_titles"] == [(2, "他终于回头")]
    assert called[1][1]["existing_titles"] == [(1, "第1次晚饭")]
