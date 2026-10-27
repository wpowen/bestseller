"""Real planner/SQLite entry, inert completion boundary; no provider or PG.

Capacity/submission plumbing is not a semantic or literary acceptance grade.
"""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.schema import CreateTable
from test_source_bound_planning_content import _compile
from test_source_bound_planning_model_completion_entry import entry  # noqa: F401 -- fixture

from bestseller.infra.db.models import ChapterModel, LlmRunModel, VolumeModel
from bestseller.services import planner
from bestseller.services import source_bound_planning_content as content
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.planner_prompt_helpers import attach_planner_methodology
from bestseller.services.rolling_outline_source_transport import CLOSE, OPEN
from bestseller.services.source_complete_outline_json import PROMPT_VERSION
from bestseller.settings import LLMSettings, load_settings

pytestmark = pytest.mark.unit
VERSION = "3.0-source-complete-outline"
LOGICAL = "volume_1_chapter_outline"


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
async def dispatch(entry, monkeypatch):  # noqa: F811 -- injected shared SQLite fixture
    connection = await entry.session.connection()
    for model in (VolumeModel, ChapterModel):
        ddl = str(CreateTable(model.__table__).compile(dialect=entry.session.bind.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    await entry.session.commit()
    settings = load_settings(env={})
    settings.pipeline.planning_artifact_reuse_enabled = False
    # The reused foundation fixture forbids methodology there. Outline's real
    # direct/default and selected-loop behavior is explicitly exercised here.
    monkeypatch.setattr(planner, "attach_planner_methodology", attach_planner_methodology)
    state = SimpleNamespace(entry=entry, settings=settings, callbacks=[], requests=[])

    async def callback():
        state.callbacks.append("source_fence")

    state.callback = callback
    return state


def source_prompt():
    source = {
        "full_story": "BEGIN_FULL_STORY_" + "dated observatory records. " * 140 + "_END_FULL_STORY",
        "unknown": {"$ref": "/authored", "typed": [False, 1, None]},
    }
    return "Return outline JSON.", OPEN + json.dumps(source) + CLOSE + (
        "\n\n[ROLLING OUTLINE TASK]\nGenerate a ChapterOutlineBatch for chapter 1 only."
    )


def tool(description="The full typed outline submission."):
    return {
        "type": "function",
        "function": {
            "name": "submit_outline",
            "parameters": {
                "type": "object",
                "description": description,
                "properties": {"chapters": {"type": "array", "items": {"type": "object"}}},
                "required": ["chapters"],
            },
        },
    }


def transport(monkeypatch, state, *, first=None):
    async def complete(session, settings, request):
        assert session is state.entry.session and session.bind.dialect.name == "sqlite"
        assert len(state.callbacks) == len(state.requests) + 1
        state.requests.append(request)
        row = LlmRunModel(
            project_id=request.project_id,
            workflow_run_id=request.workflow_run_id,
            logical_role="planner",
            provider="inert-fixture",
            model_name="no-provider",
            prompt_template=request.prompt_template,
            prompt_version=request.prompt_version,
            finish_reason="stop",
            metadata_json={},
        )
        session.add(row)
        await session.flush()
        bad = len(state.requests) == 1 and first is not None
        return LLMCompletionResult(
            content="not JSON"
            if bad and first == "parse"
            else json.dumps(
                {
                    "batch_name": "fixture-only",
                    "chapters": [{"chapter_number": 1, "title": "Signal"}],
                }
            ),
            finish_reason="length" if bad and first == "length" else "stop",
            provider=row.provider,
            model_name=row.model_name,
            llm_run_id=row.id,
        )

    monkeypatch.setattr(planner, "complete_text", complete)

    def forbidden_four_kind_decoder(*args, **kwargs):
        pytest.fail("SOURCE-tagged outline must never enter the four-kind JSON decoder")

    from bestseller.services import planning_dispatch_transport

    monkeypatch.setattr(
        planning_dispatch_transport, "prepare_planning_dispatch", forbidden_four_kind_decoder
    )


async def invoke(state, *, system=None, user=None, output_tool=None):
    default_system, default_user = source_prompt()
    return await planner._generate_structured_artifact(
        state.entry.session,
        state.settings,
        project=state.entry.project,
        logical_name=LOGICAL,
        workflow_run_id=state.entry.workflow.id,
        system_prompt=default_system if system is None else system,
        user_prompt=default_user if user is None else user,
        fallback_payload={"chapters": []},
        prompt_version=VERSION,
        abort_on_fallback=True,
        merge_fallback=False,
        max_attempts=2,
        output_tool=tool() if output_tool is None else output_tool,
        before_dispatch=state.callback,
    )


def assert_capacity(request):
    receipt = request.metadata["rolling_outline_request_capacity"]
    assert receipt == content.assert_source_bound_planning_request_capacity(
        request.system_prompt,
        request.user_prompt,
        tools=request.tools,
        output_tokens=request.max_tokens_override,
    )
    assert receipt["output_reserve_tokens"] == request.max_tokens_override == 16384
    from bestseller.services.llm_capacity import INPUT_SAFETY_FACTOR
    assert receipt["total_budget"] == 65536 and receipt["safety_factor"] == INPUT_SAFETY_FACTOR == 2.2
    assert receipt["budget_passed"] and not receipt["truncated"]
    assert receipt["reserved_input_tokens"] + receipt["output_reserve_tokens"] <= 65536


@pytest.mark.asyncio
@pytest.mark.parametrize("first", [None, "length", "parse"])
async def test_real_generic_entry_admits_final_tools_output_and_entire_retry_source(
    dispatch,
    monkeypatch,
    first,
):
    transport(monkeypatch, dispatch, first=first)
    before = deepcopy(dispatch.entry.canonical.content)
    result, run_id = await invoke(dispatch)
    assert run_id and result["chapters"][0]["chapter_number"] == 1
    assert len(dispatch.requests) == (1 if first is None else 2)
    system, user = source_prompt()
    effective = attach_planner_methodology(
        user,
        stage=planner._methodology_stage_for_planner_artifact(LOGICAL),
        project_ctx=dispatch.entry.project,
        token_budget=800,
    ).rstrip()
    effective += planner._STRUCTURED_FUNCTION_SUFFIX
    assert dispatch.requests[0].user_prompt == effective
    expected_hash = planner._planner_artifact_input_hash(
        logical_name=LOGICAL,
        system_prompt=system,
        user_prompt=effective,
        fallback_payload={"chapters": []},
    )
    for index, request in enumerate(dispatch.requests, 1):
        assert request.project_id == dispatch.entry.project.id
        assert request.workflow_run_id == dispatch.entry.workflow.id
        assert request.prompt_version == VERSION and request.metadata["attempt"] == index
        assert request.metadata["input_hash"] == expected_hash
        assert request.tools == [tool()]
        assert request.tool_choice == {"type": "function", "function": {"name": "submit_outline"}}
        assert request.user_prompt.startswith(effective)
        assert request.user_prompt.count("BEGIN_FULL_STORY_") == 1
        assert "_END_FULL_STORY" in request.user_prompt
        assert_capacity(request)
        assert request.metadata["rolling_outline_request_capacity"]["tool_tokens"] > 0
    if first:
        assert dispatch.requests[1].user_prompt.startswith(
            effective + "\n\n[SOURCE-BOUND SUBMISSION CORRECTION]"
        )
        assert len(dispatch.entry.failures) == 1
        assert dispatch.requests[1].metadata["rolling_outline_request_capacity"][
            "user_chars"
        ] > len(effective)
    assert dispatch.entry.canonical.content == before


@pytest.mark.asyncio
@pytest.mark.parametrize("overflow", ["tool", "output", "methodology"])
async def test_capacity_failure_precedes_source_callback_and_zero_completion_dispatch(
    dispatch,
    monkeypatch,
    overflow,
):
    transport(monkeypatch, dispatch)
    system, user = source_prompt()
    output_tool = tool()
    if overflow == "tool":
        output_tool = tool("x" * 150000)
    elif overflow == "output":
        effective = attach_planner_methodology(
            user,
            stage=planner._methodology_stage_for_planner_artifact(LOGICAL),
            project_ctx=dispatch.entry.project,
            token_budget=800,
        ).rstrip()
        effective += planner._STRUCTURED_FUNCTION_SUFFIX
        baseline = content.assert_source_bound_planning_request_capacity(
            system, effective, tools=[tool("")], output_tokens=8192
        )
        output_tool = tool("x" * (4 * (23000 - baseline["estimated_input_tokens"])))
        # This request fits with 8192 output; the actual outline's 16384 must block.
        content.assert_source_bound_planning_request_capacity(
            system, effective, tools=[output_tool], output_tokens=8192
        )
    else:
        user = (
            OPEN
            + json.dumps({"full_story": "海" * 28000}, ensure_ascii=False)
            + CLOSE
            + ("\n\n[ROLLING OUTLINE TASK]\nGenerate a ChapterOutlineBatch.")
        )
        content.assert_source_bound_planning_request_capacity(
            system,
            user + planner._STRUCTURED_FUNCTION_SUFFIX,
            tools=[output_tool],
            output_tokens=16384,
        )

        def expanded_methodology(original, **kwargs):
            return attach_planner_methodology(original, **kwargs) + "\n" + "潮" * 7000

        monkeypatch.setattr(planner, "attach_planner_methodology", expanded_methodology)
    before_rows = await dispatch.entry.session.scalar(select(func.count()).select_from(LlmRunModel))
    with pytest.raises(content.SourceBoundPlanningCapacityError):
        await invoke(dispatch, system=system, user=user, output_tool=output_tool)
    assert dispatch.callbacks == dispatch.requests == dispatch.entry.failures == []
    assert (
        await dispatch.entry.session.scalar(select(func.count()).select_from(LlmRunModel))
        == before_rows
    )


@pytest.mark.asyncio
async def test_actual_selected_repair_loop_dispatch_is_exact_compiled_transport_no_second_prefix(
    dispatch,
    monkeypatch,
):
    book, world, cast, volumes = _compile(dispatch.entry.project)
    # Projection-only Cast intentionally contains an unresolved antagonist {}.
    # Supply a declared complete fixture, as the completed foundation caller does.
    cast = {
        "protagonist": {"name": "于岚", "role": "observer", "gender": "female", "age": 43},
        "supporting_cast": [],
        "unknown_retained": {"$ref": "/authored"},
    }
    book["unknown_full_archive"] = (
        "ARCHIVE_BEGIN:" + "".join(f"{i:08x};" for i in range(3500)) + ":ARCHIVE_END"
    )
    book["unknown_full_archive_copy"] = book["unknown_full_archive"]
    volume_entry = {"volume_number": 1, "chapter_count_target": 1, "chapter_range": [1, 1]}
    captured = []

    class BoundaryReached(BaseException):
        """Stop at inert dispatch; do not fabricate a generated semantic pass."""

    async def capture(session, settings, request):
        assert session.bind.dialect.name == "sqlite"
        captured.append(request)
        raise BoundaryReached

    monkeypatch.setattr(planner, "complete_text", capture)
    with pytest.raises(BoundaryReached):
        await planner._generate_volume_outline_with_repair_loop(
            dispatch.entry.session,
            dispatch.settings,
            project=dispatch.entry.project,
            workflow_run_id=dispatch.entry.workflow.id,
            logical_name=LOGICAL,
            book_spec=book,
            world_spec=world,
            cast_spec=cast,
            volume_plan=volumes,
            volume_entry=volume_entry,
            fallback_payload={"chapters": []},
            volume_number=1,
            expected_count=1,
            chapter_number_offset=1,
            revealed_ledger_block=None,
            base_constraints=["Keep the full source and exact selected chapter range."],
        )
    assert len(captured) == 1
    request = captured[0]
    report = dispatch.entry.project.metadata_json["planner_prompt_compiler_latest"]
    retained = report["source_transport"]
    assert retained["wire_system_sha256"] == sha(request.system_prompt)
    assert retained["wire_user_sha256"] == sha(request.user_prompt)
    assert request.prompt_version == PROMPT_VERSION
    assert report["required_complete"]
    assert report["budget_tokens"] == dispatch.settings.llm.planner_prompt_budget_tokens == 16000
    assert dispatch.settings.llm.planner_prompt_budget_tokens == 16000
    assert request.tools is None  # The normal outline route has no native schema yet.
    assert_capacity(request)
    encoded = json.loads(request.user_prompt.split(OPEN)[1].split(CLOSE)[0])
    restored = (
        decode_lossless_source(encoded)
        if encoded.get("schema", "").startswith("lossless-")
        else encoded
    )
    assert (
        restored["story_assets"]["book_spec"]["unknown_full_archive"]
        == book["unknown_full_archive"]
    )
    assert restored["project"]["id"] == str(dispatch.entry.project.id)
    assert request.metadata["input_hash"] == planner._planner_artifact_input_hash(
        logical_name=LOGICAL,
        system_prompt=request.system_prompt,
        user_prompt=request.user_prompt,
        fallback_payload={"chapters": []},
    )


def test_source_complete_ceiling_has_no_implicit_model_default_but_config_is_explicit():
    assert LLMSettings.model_fields["planner_source_complete_input_budget_tokens"].default is None
    settings = load_settings(env={})
    assert settings.llm.planner_prompt_budget_tokens == 16000
    assert settings.llm.planner_source_complete_input_budget_tokens == 32768
