"""Main-run RED: real SQLite, retained files and native short reconstruction.

Only provider/Redis IO is inert. Beat/completion readers, annotation, patch
application, native validators, reader checkpoint and importer remain real.
The fixture creates synthetic evidence; no production/PG/provider is accessed.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import re
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from test_fanqie_beat_recovery import case as beat_case  # noqa: F401
from test_fanqie_outline_contract_completion import (
    authored_fields, complete, outline_case, physical_files, protected_rows,  # noqa: F401
)
from test_foundation_field_recovery import record_completion
from test_foundation_selection import store  # noqa: F401

from bestseller.infra.db.models import (
    ChapterModel, ExecutionAttemptModel, ExecutionOperationModel, LlmRunModel,
    PlanningArtifactVersionModel, WorkflowRunModel,
)
from bestseller.services import fanqie_outline_contract_completion as native
from bestseller.services import fanqie_outline_source_selection as selection
from bestseller.services import fanqie_short_planner as short
from bestseller.services import llm, materialized_outline_source_recovery, planner
from bestseller.services import source_bound_planning_content as reader_protocol
from bestseller.services.budget import bind_execution_scope, current_execution_scope
from bestseller.services.chapter_writer_delivery import ReaderFirstSourceError, _reader_source_equal
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retry_ledger import project_execution_scope
from bestseller.services.source_bound_planning_content import (
    READER_FIRST_DIMENSIONS, new_reader_first_binding,
)
from full_flow_q4_short.typed_leaf_differences import typed_summary

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
SENTINEL = "DO_NOT_ECHO_THIS_SYNTHETIC_PAYLOAD"


@pytest.fixture
async def case(beat_case):
    # Select the prospective protocol and real supported route BEFORE any
    # native paid row or retained path exists. Never relabel issued evidence.
    c = beat_case
    assert c.paths == {}
    assert await c.session.scalar(select(ExecutionOperationModel.key).limit(1)) is None
    assert await c.session.scalar(select(ExecutionAttemptModel.id).limit(1)) is None
    assert await c.session.scalar(select(LlmRunModel.id).limit(1)) is None
    c.reader_binding = new_reader_first_binding(c.sources["premise"], protocol="reader-first-planning.v2")
    c.project.metadata_json = {**c.project.metadata_json, "reader_first_planning": c.reader_binding}
    for role in (c.settings.llm.planner, c.settings.llm.critic):
        role.model = "openai/MiniMax-M3.1-Flash"
        role.model_override = None
        role.api_base = "https://api.minimaxi.com/v1"
        role.thinking_type = "enabled"
        role.reasoning_effort = "medium"
    await c.session.commit()
    return c


@pytest.fixture
def bound_native_scope(outline_case, request):
    """Bind in the caller's task; never rely on async-fixture context propagation."""
    c = outline_case

    @contextmanager
    def actual_scope():
        expected = project_execution_scope(c.project)
        _reader_source_equal("fixture.project.execution_scope_id", expected,
                             (c.project.metadata_json or {}).get("execution_scope_id"))
        _reader_source_equal("fixture.ledger.scope_id", expected, c.budget.scope_id)
        previous = current_execution_scope()
        before = ReaderFirstSourceError("current_execution_scope.before_binding",
                                        expected, previous).differences[0]
        try:
            with bind_execution_scope(expected):
                actual = current_execution_scope()
                _reader_source_equal("fixture.current_execution_scope", expected, actual)
                bound = ReaderFirstSourceError("current_execution_scope.bound",
                                               expected, actual).differences[0]
                request.node.user_properties.append(
                    ("native_scope_binding", json.dumps({"before": before, "bound": bound}))
                )
                yield
        finally:
            _reader_source_equal("fixture.current_execution_scope.restored", previous,
                                 current_execution_scope())

    return actual_scope


@pytest.fixture
async def native_reader_case(outline_case, bound_native_scope, monkeypatch, request):
    c = outline_case
    assert c.reader_binding["protocol"] == "reader-first-planning.v2"
    assert c.beat_request.metadata["reader_first_planning"] == c.reader_binding
    c.bound_native_scope = bound_native_scope
    c.want_repair = getattr(request, "param", False)

    async def record_retained(req, content):
        # The shared fixture recorder stages rows before validating retention.
        # Keep its real request/response/operation construction unchanged, but
        # make a failed record atomic so checkpoint save cannot flush half rows.
        # Failed physical files remain evidence; no replayable/grade flag is set.
        async with c.session.begin_nested():
            try:
                return await record_completion(c, req, content=content)
            except Exception as error:
                fields = ("id", "operation_key", "scope_id", "model_name", "input_hash",
                          "reserved_tokens", "actual_tokens", "status", "created_at")
                attempts = [{field: typed_summary(getattr(row, field)) for field in fields}
                            for row in c.session.new if isinstance(row, ExecutionAttemptModel)]
                request.node.user_properties.append(("native_paid_record_failure", json.dumps({
                    "template": req.prompt_template,
                    "exception_type": type(error).__name__, "exception": typed_summary(str(error)),
                    "pending_attempt_operands": attempts,
                    "outcome": "record_rejected; no successful custody claimed",
                })))
                raise

    async def completion_transport(session, settings, req):
        # The inherited legacy fixture asserts the old wire version. Accept
        # the actual marked request unchanged, never relabel a physical input.
        assert req.prompt_template == native.TEMPLATE
        assert req.metadata["reader_first_planning"] == c.reader_binding
        assert req.project_id == c.project.id and req.workflow_run_id
        outer = json.loads(req.user_prompt[req.user_prompt.index("{"):])
        document = decode_lossless_source(outer["planning_document"])
        assert document["source"]["original_beat"]["raw"] == c.raw
        values = authored_fields(document["part"])
        answer = {pointer: deepcopy(values[pointer.rsplit("/", 1)[1]])
                  for pointer in outer["response_schema"]["required"]}
        c.requests.append(req.model_copy(deep=True))
        paid, row = await record_retained(req, json.dumps(answer, ensure_ascii=False))
        row.metadata_json = {**row.metadata_json, **deepcopy(req.metadata)}
        c.runs.append(row)
        c.budget.calls_used += 1
        c.budget.tokens_charged += paid.input_tokens + paid.output_tokens
        return paid

    monkeypatch.setattr(llm, "complete_text", completion_transport)
    with bound_native_scope():
        c.completed_outline = await complete(c)
        await c.session.commit()
        _, c.original_source = await native._sources(c.session, c.settings, c.project.id)
    c.completion_runs = list(c.runs)
    c.role_runs, c.reader_runs, c.repair_runs, c.reader_requests = [], [], [], []
    c.patch_value = "Mira admits the missing pin to Rook and asks him to witness her repair."

    async def retained_transport(session, settings, req):
        assert session is c.session and req.project_id == c.project.id and req.workflow_run_id
        assert settings.llm.retry.max_attempts == 1
        c.reader_requests.append(req.model_copy(deep=True))
        document = json.loads(req.user_prompt)
        if req.prompt_template == "fanqie_reader_first_field_roles":
            # This is an explicit fixture author, not application inference.
            chapters = []
            for chapter in document["outline"]["chapters"]:
                leaves = document["chapter_leaf_values"][str(chapter["chapter_number"])]
                roles = dict.fromkeys(leaves, "continuity_reference")
                roles["/chapter_goal"] = "narrative_task"
                roles["/scenes/0/purpose/story"] = "narrative_task"
                chapters.append({"chapter_number": chapter["chapter_number"], "field_roles": roles})
            answer = {"schema": "fanqie-reader-first-field-roles.v1",
                      "source_sha256": document["source_sha256"], "chapters": chapters}
            bucket = c.role_runs
        elif req.prompt_template == "planner_reader_first_outline_review":
            source = document["source"]
            first_failure = c.want_repair and not c.reader_runs
            answer = {
                "schema": "reader-first-review.v1", "checkpoint": "outline",
                "source_sha256": content_hash(source), "config_sha256": c.reader_binding["config_sha256"],
                "dimensions": {name: {
                    "score": .4 if first_failure else .9,
                    "reason": "Mira must let Rook witness the cost of her choice." if first_failure
                              else "Mira's visible choice connects the promise and its consequence.",
                    "evidence": [{"path": "/outline/chapters/0/chapter_goal",
                                  "quote": source["outline"]["chapters"][0]["chapter_goal"]}],
                } for name in READER_FIRST_DIMENSIONS},
                "issues": ([{"severity": "high", "reason": "Rook cannot yet witness Mira's choice."}]
                           if first_failure else []),
                "options": ["Let Mira ask Rook to witness the repair.",
                            "Let Mira admit the missing pin before beginning the repair."],
            }
            bucket = c.reader_runs
        else:
            assert req.prompt_template == "planner_reader_first_full_outline_commercial_surgical_repair"
            assert c.want_repair and not c.repair_runs
            assert document["source"]["original_beat"] == c.original_source["original_beat"]
            answer = {"patches": [{"chapter_number": 1, "scope": "chapter",
                                    "field": "chapter_goal", "value": c.patch_value}]}
            bucket = c.repair_runs
        paid, row = await record_retained(req, json.dumps(answer, ensure_ascii=False))
        row.metadata_json = {**row.metadata_json, **deepcopy(req.metadata)}
        bucket.append(row)
        c.budget.calls_used += 1
        c.budget.tokens_charged += paid.input_tokens + paid.output_tokens
        return paid

    monkeypatch.setattr(short, "complete_text", retained_transport)
    monkeypatch.setattr(planner, "complete_text", retained_transport)
    monkeypatch.setattr(llm, "complete_text", retained_transport)
    # The previous complete-Beat setup stopped before retaining a critic
    # request. Capture the actual estimator operand without reconstructing or
    # guessing that missing source, and without changing capacity admission.
    actual_estimator = reader_protocol.estimate_prompt_tokens
    capacity_limit = reader_protocol._reader_config(protocol=c.reader_binding["protocol"])[0]["outline_input_tokens"]

    def record_actual_reader_capacity(text):
        tokens = actual_estimator(text)
        difference = ReaderFirstSourceError(
            "reader_first.outline_input_tokens", capacity_limit, tokens,
        ).differences[0]
        operand = ReaderFirstSourceError("reader_first.full_wire", "Unknown", text).differences[0]["actual"]
        request.node.user_properties.append(("native_reader_input_capacity", json.dumps({
            "maximum_input_tokens": capacity_limit, "estimated_input_tokens": tokens,
            "within_limit": tokens <= capacity_limit, "operand": operand,
            "differences": [difference],
        })))
        return tokens

    monkeypatch.setattr(reader_protocol, "estimate_prompt_tokens", record_actual_reader_capacity)
    producer = UUID(c.selection.receipt["producer_workflow_id"])
    with bound_native_scope():
        c.prepared = await short.prepare_reader_first_short_outline(
            c.session, c.settings, project_id=c.project.id,
            outline_payload=deepcopy(c.completed_outline), workflow_run_id=producer,
            requested_by="native-admission-fixture",
        )
    artifact_id = await short.persist_fanqie_chapter_outline(c.session, c.project.slug, c.prepared)
    await c.session.commit()
    c.artifact = await c.session.get(PlanningArtifactVersionModel, artifact_id)
    c.chapter = ChapterModel(project_id=c.project.id, chapter_number=1,
                             chapter_goal=c.prepared["chapters"][0]["chapter_goal"], status="planned")
    c.materializer = WorkflowRunModel(project_id=c.project.id,
        workflow_type="materialize_chapter_outline_batch", scope_type="planning_artifact",
        scope_id=artifact_id, status="completed", metadata_json={"source_artifact_id": str(artifact_id)})
    c.session.add_all([c.chapter, c.materializer])
    await c.session.commit()
    c.final_source = deepcopy(c.project.metadata_json["reader_first_outline_checkpoint"]["history"][-1]["source"])
    assert c.artifact.content == c.prepared == c.final_source["outline"]
    assert len(c.role_runs) == 1 and len(c.reader_runs) == (2 if c.want_repair else 1)
    assert len(c.repair_runs) == int(c.want_repair)

    async def no_new_call(*args, **kwargs):
        pytest.fail("ADMISSION_MUST_NOT_PREPARE_OR_DISPATCH")
    for module in (short, planner, llm):
        monkeypatch.setattr(module, "complete_text", no_new_call)
    monkeypatch.setattr(short, "prepare_reader_first_short_outline", no_new_call)
    return c


async def verify(c):
    with c.bound_native_scope():
        return await selection.verify_reader_first_short_outline_source(
            c.session, c.settings, project=c.project, artifact=c.artifact, chapter=c.chapter,
        )


def assert_safe_difference(error):
    assert str(error) == error.code and SENTINEL not in str(error)
    assert isinstance(error.differences, list) and error.differences
    for difference in error.differences:
        assert set(difference) == {"field", "expected", "actual"}
        assert isinstance(difference["field"], str) and difference["field"]
        for side in ("expected", "actual"):
            summary = difference[side]
            assert set(summary) == {"type", "length", "sha256"}
            assert isinstance(summary["type"], str)
            assert type(summary["length"]) is int and summary["length"] >= 0
            assert re.fullmatch(r"[0-9a-f]{64}", summary["sha256"])


def alter_response(c, run):
    path = c.paths[f"{run.id}:response"]
    payload = json.loads(path.read_text())
    payload["content"]["text"] += SENTINEL
    path.write_text(json.dumps(payload))


async def unknown_operation(c, run):
    operation = await c.session.get(ExecutionOperationModel,
                                   run.metadata_json["execution_receipt"]["operation_key"])
    operation.status = "unknown"


async def test_marked_native_completion_wire_can_be_physically_reloaded(outline_case, bound_native_scope):
    """Direct RED for the upstream version-length seam, not a fixture failure."""
    c = outline_case
    with bound_native_scope():
        project, source = await native._sources(c.session, c.settings, c.project.id)
    diagnostics = native._diagnose(c.base, project, c.settings, c.assets["cast_spec"].content)
    targets = native._targets(c.base, diagnostics, 0)
    req = native._request(project, source, c.base, diagnostics, targets,
                          UUID(c.selection.receipt["producer_workflow_id"]), 0, 0, None)
    assert llm.LLMCompletionRequest.model_validate(req.model_dump(mode="json")) == req


@pytest.mark.parametrize("outline_case", ["complete", "missing"], indirect=True)
async def test_native_beat_and_completion_then_authored_roles_reconstruct_exact_stored_source(native_reader_case):
    c = native_reader_case
    before, files = await protected_rows(c), physical_files(c)
    result = await verify(c)
    assert result["schema"] == "fanqie-reader-first-outline-custody.v1"
    assert result["artifact_id"] == str(c.artifact.id)
    assert result["source_content_sha256"] == content_hash(c.artifact.content)
    assert result["source"] == c.final_source
    assert result["generation_llm_run_ids"] == [str(row.id) for row in
                                                [c.beat_run, *c.completion_runs, *c.role_runs]]
    assert result["reader_first_source"]["native_custody"]["llm_run_id"] == str(c.reader_runs[-1].id)
    assert not {"approved", "adoption_granted", "dispatch_authorized"}.intersection(result)
    assert await protected_rows(c) == before and physical_files(c) == files
    result["source"]["outline"]["chapters"].clear()
    assert len((await verify(c))["source"]["outline"]["chapters"]) == 4


@pytest.mark.parametrize("outline_case", ["complete"], indirect=True)
async def test_complete_reader_unknown_model_or_route_still_refuses_65536(native_reader_case, request):
    """Pure capacity negatives on the full wire; paid rows/requests stay intact."""
    from bestseller.services.llm_capacity import ModelRequestCapacityError, assert_effective_request_capacity
    from bestseller.services.model_context_capacity import payload_capacity
    from bestseller.services.planning_surgical_budget import resolve_request_role

    c = native_reader_case
    critic = next(req for req in c.reader_requests
                  if req.prompt_template == "planner_reader_first_outline_review")
    before, files = await protected_rows(c), physical_files(c)
    original_request = critic.model_dump(mode="json")
    role = resolve_request_role(c.settings, c.project, critic)
    payload = llm._effective_input_payload(critic, role)
    assert len(critic.user_prompt) > 120000
    assert payload_capacity(payload) == 1000000
    for boundary in ("unknown_model", "unknown_route"):
        # This detached capacity operand is never submitted, retained or used
        # to replace a native model, operation or request in the fixture.
        operand = deepcopy(payload)
        if boundary == "unknown_model":
            operand["model"] = "openai/integrated-reader-fixture"
        else:
            operand["endpoint_hash"] = hashlib.sha256(b"https://unverified.invalid/v1").hexdigest()
        assert payload_capacity(operand) == 65536
        with pytest.raises(ModelRequestCapacityError) as caught:
            assert_effective_request_capacity(operand)
        receipt = caught.value.receipt
        request.node.user_properties.append(("native_unknown_route_capacity", json.dumps({
            "boundary": boundary, "receipt": receipt,
            "difference": {"field": "model_request.reserved_total_tokens",
                           "expected": typed_summary(65536),
                           "actual": typed_summary(receipt["reserved_total_tokens"])},
        })))
        assert receipt["total_budget"] == 65536
        assert receipt["reserved_total_tokens"] > 65536
        assert receipt["output_reserve_tokens"] == 4096
        assert receipt["budget_passed"] is receipt["truncated"] is False
        assert receipt["provider_usage_claimed"] is False
    assert critic.model_dump(mode="json") == original_request
    assert await protected_rows(c) == before and physical_files(c) == files


@pytest.mark.parametrize("native_reader_case", [True], indirect=True)
async def test_native_full_patch_and_rereview_are_replayed_instead_of_cached_candidate(native_reader_case):
    c = native_reader_case
    before, files = await protected_rows(c), physical_files(c)
    result = await verify(c)
    assert c.completed_outline["chapters"][0]["chapter_goal"] != c.patch_value
    assert result["source"]["outline"]["chapters"][0]["chapter_goal"] == c.patch_value
    assert result["source"] == c.final_source
    assert result["generation_llm_run_ids"] == [str(row.id) for row in
        [c.beat_run, *c.completion_runs, *c.role_runs, *c.repair_runs]]
    assert await protected_rows(c) == before and physical_files(c) == files


@pytest.mark.parametrize("fault", ["beat_response", "completion_response", "role_response",
                                    "role_request", "role_operation", "reader_operation"])
async def test_each_physical_link_is_required_despite_cached_pass_flags(native_reader_case, fault):
    c = native_reader_case
    if fault.endswith("response"):
        row = {"beat_response": c.beat_run, "completion_response": c.completion_runs[0],
               "role_response": c.role_runs[0]}[fault]
        alter_response(c, row)
    elif fault == "role_request":
        path = c.paths[f"{c.role_runs[0].id}:request"]
        raw = json.loads(path.read_text())
        raw["messages"]["user"]["text"] += SENTINEL
        path.write_text(json.dumps(raw))
    else:
        await unknown_operation(c, c.role_runs[0] if fault == "role_operation" else c.reader_runs[-1])
    await c.session.commit()
    before, files = await protected_rows(c), physical_files(c)
    with pytest.raises(ValueError) as stopped:
        await verify(c)
    assert_safe_difference(stopped.value)
    assert await protected_rows(c) == before and physical_files(c) == files


@pytest.mark.parametrize("fault", ["body", "chapter_meta", "scene_meta", "json_type", "checkpoint_source"])
async def test_exact_artifact_and_checkpoint_comparison_never_ignores_metadata_or_json_types(native_reader_case, fault):
    c = native_reader_case
    if fault == "checkpoint_source":
        metadata = deepcopy(c.project.metadata_json)
        metadata["reader_first_outline_checkpoint"]["history"][-1]["source"]["outline"]["chapters"][0]["chapter_goal"] = SENTINEL
        c.project.metadata_json = metadata
        flag_modified(c.project, "metadata_json")
    else:
        changed = deepcopy(c.artifact.content)
        chapter = changed["chapters"][0]
        if fault == "body":
            chapter["chapter_goal"] = SENTINEL
        elif fault == "chapter_meta":
            chapter["_meta"]["unproved_custody"] = SENTINEL
        elif fault == "scene_meta":
            chapter["scenes"][0]["_meta"] = {"unproved_custody": SENTINEL}
        else:
            chapter["chapter_number"] = True
        c.artifact.content = changed
        flag_modified(c.artifact, "content")
    await c.session.commit()
    before = await protected_rows(c)
    with pytest.raises(ValueError) as stopped:
        await verify(c)
    assert_safe_difference(stopped.value)
    assert await protected_rows(c) == before


@pytest.mark.parametrize("native_reader_case", [True], indirect=True)
@pytest.mark.parametrize("fault", ["response", "operation", "receipt_only"])
async def test_repair_candidate_or_receipt_cannot_replace_physical_patch_custody(native_reader_case, fault):
    c = native_reader_case
    if fault == "response":
        alter_response(c, c.repair_runs[0])
    elif fault == "operation":
        await unknown_operation(c, c.repair_runs[0])
    else:
        c.repair_runs[0].request_payload_ref = None
    await c.session.commit()
    before, files = await protected_rows(c), physical_files(c)
    with pytest.raises(ValueError) as stopped:
        await verify(c)
    assert_safe_difference(stopped.value)
    assert await protected_rows(c) == before and physical_files(c) == files


async def test_native_selection_calls_source_guard_before_any_admission_dispatch(native_reader_case, monkeypatch):
    c = native_reader_case
    calls = []

    class GuardProbe(ValueError):
        pass

    async def guarded(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs["artifact"].id == c.artifact.id
        assert kwargs["chapter"].project_id == c.project.id
        raise GuardProbe("READER_SOURCE_GUARD_REACHED")

    async def owner(*args):
        pass

    async def assessor(*args, **kwargs):
        pytest.fail("ASSESSOR_REACHED_BEFORE_READER_SOURCE_GUARD")

    monkeypatch.setattr(selection, "verify_reader_first_short_outline_source", guarded)
    monkeypatch.setattr(selection, "_owner", owner)
    monkeypatch.setattr(materialized_outline_source_recovery, "assess_materialized_outline_source", assessor)
    with c.bound_native_scope(), pytest.raises(GuardProbe):
        await selection.admit_and_select_outline_source(
            c.session, c.settings, project_id=c.project.id, artifact_id=c.artifact.id,
            materialization_workflow_id=c.materializer.id,
        )
    assert len(calls) == 1
