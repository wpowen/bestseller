"""Source-only allowance preserves exact data; financial/provider gates stay closed."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_chapter_first_prompt_compiler_integration import _inputs
from test_first_candidate_working_pointer import db as db

from bestseller.services import drafts, llm, prompt_compiler
from bestseller.services import writer_source_capacity as source
from bestseller.services.chapter_writer_delivery import delivery_block
from bestseller.services.finite_book_budget import bind_run
from bestseller.services.llm_capacity import ModelRequestCapacityError
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def settings_for(model="openai/MiniMax-M3"):
    settings = load_settings().model_copy(deep=True)
    settings.llm.writer = settings.llm.writer.model_copy(
        update={
            "model": model,
            "api_base": "https://api.minimaxi.com/v1",
            "max_tokens": 32768,
            "model_override": None,
            "stream": False,
            "n_candidates": 1,
            "thinking_type": None if "M3.1" in model else "disabled",
            "reasoning_effort": "medium" if "M3.1" in model else None,
        }
    )
    return settings


def typed(project, chapter, **fields):
    return delivery_block(
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=chapter.chapter_number,
        artifact_id=uuid4(),
        receipt={"chapter_numbers": [chapter.chapter_number]},
        approved={
            "chapter_number": chapter.chapter_number,
            "scenes": [
                {
                    "scene_number": 1,
                    "entry_state": {"锁": "已换"},
                    "exit_state": {"凭证": "已核对"},
                    "action_sequence": ["保留作者给出的先后顺序及否定条件。"],
                }
            ],
            **fields,
        },
    )


def capacity(project, chapter, blocks, *, settings=None, override=16384):
    return source.prepare_writer_source_capacity(
        settings or settings_for(),
        project=project,
        chapter=chapter,
        delivery_blocks=blocks,
        model_tier="standard",
        max_tokens_override=override,
    )


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Source allowance tests forbid network/provider execution")

    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(drafts, "complete_text", forbidden)


@pytest.fixture
def actual_full():
    path = (
        Path(__file__).resolve().parents[2]
        / "reports/framework-recovery-2026-10-21/phase8s-writer-full-delivery-input.json"
    )
    if not path.exists():
        pytest.skip("Requires archived read-only full-delivery capture")
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "acf3f262a56fb5333d0b59635237bd36ab4550cc93d7ab96f1fd99017cf5b5fb"
    )
    kwargs = deepcopy(json.loads(raw)["compiler_kwargs"])
    kwargs["additional_blocks"] = [
        prompt_compiler.PromptBlock.model_validate(block) for block in kwargs["additional_blocks"]
    ]
    block = kwargs["additional_blocks"][0]
    payload = json.loads(block.text[block.text.index("{") :])
    project = SimpleNamespace(id=UUID(payload["project_id"]), metadata_json={})
    chapter = SimpleNamespace(
        id=UUID(payload["chapter_id"]),
        chapter_number=payload["chapter_number"],
    )
    return kwargs, project, chapter


def test_actual_full_source_uses_minimum_allowance_and_actual_16k_output(actual_full):
    kwargs, project, chapter = actual_full
    original = deepcopy(kwargs)
    with pytest.raises(prompt_compiler.PromptBudgetError):
        drafts._compile_rendered_writer_prompt(**kwargs)  # Pure constructors remain static.
    context = capacity(project, chapter, kwargs["additional_blocks"])
    assert context.output_tokens == 16384 and context.n_candidates == 1
    assert context.capacity_limit == 65536
    compiled = drafts._compile_rendered_writer_prompt(**kwargs, source_capacity=context)
    receipt = compiled.report.writer_source_capacity
    assert receipt["base_budget_tokens"] == 14800
    assert receipt["core_input_tokens"] == 16817
    assert receipt["minimum_core_budget_tokens"] == receipt["input_allowance_tokens"] == 18686
    assert receipt["expanded"] and receipt["source_only"]
    assert receipt["financial_authorization"] is False and receipt["gateway_revalidation_required"]
    assert receipt["capacity"]["output_reserve_tokens"] == 16384
    assert receipt["capacity"]["reserved_input_tokens"] == 43395
    assert receipt["capacity"]["reserved_total_tokens"] == 59779
    assert receipt["capacity"]["budget_passed"] and receipt["capacity"]["safety_factor"] == 2.2
    assert compiled.report.source_integrity_complete
    for name in compiled.report.source_integrity_blocks:
        key = name.split(".section.", 1)[1]
        text = kwargs["section_texts"].get(key, kwargs["system_section_texts"].get(key))
        assert text.strip() in compiled.user or text.strip() in compiled.system
    assert kwargs["additional_blocks"][0].text in compiled.user
    assert kwargs == original


@pytest.mark.parametrize("base_budget", [14800, 20000, 22000])
def test_actual_feasible_core_survives_larger_base_optional_capacity(
    actual_full, base_budget, monkeypatch
):
    kwargs, project, chapter = actual_full
    kwargs["total_input_budget_tokens"] = base_budget
    original = deepcopy(kwargs)
    context = capacity(project, chapter, kwargs["additional_blocks"])
    measurements = []
    measure = source.measure_request_capacity

    def observed(messages, **options):
        result = measure(messages, **options)
        measurements.append((options, result))
        return result

    monkeypatch.setattr(source, "measure_request_capacity", observed)
    compiled = drafts._compile_rendered_writer_prompt(**kwargs, source_capacity=context)
    receipt = compiled.report.writer_source_capacity
    assert receipt["base_budget_tokens"] == base_budget
    assert receipt["input_allowance_tokens"] == max(base_budget, 18686)
    assert receipt["minimum_core_budget_tokens"] == 18686
    assert receipt["core_capacity"]["reserved_total_tokens"] == 59779
    assert receipt["capacity"]["reserved_total_tokens"] <= 65536
    assert receipt["capacity"]["output_reserve_tokens"] == 16384
    assert receipt["capacity"]["n_candidates"] == 1
    assert receipt["financial_authorization"] is False
    assert compiled.report.source_integrity_complete
    assert kwargs["additional_blocks"][0].text in compiled.user
    for name in compiled.report.source_integrity_blocks:
        key = name.split(".section.", 1)[1]
        text = kwargs["section_texts"].get(key, kwargs["system_section_texts"].get(key))
        assert text.strip() in compiled.user or text.strip() in compiled.system
    if base_budget == 22000:
        assert any(result["reserved_total_tokens"] == 67765 for _, result in measurements)
        assert "physical_request_capacity" in compiled.report.drop_reasons.values()
    elif base_budget == 20000:
        assert receipt["capacity"]["reserved_total_tokens"] == 62975
    assert measurements and all(
        options == {"tools": None, "output_tokens": 16384, "n_candidates": 1, "total_budget": 65536}
        for options, _ in measurements
    )
    assert kwargs == original


@pytest.mark.parametrize("ceiling", [4096, 59778])
@pytest.mark.parametrize("base_budget", [14800, 20000, 22000])
def test_actual_core_capacity_failure_never_shrinks_sources_or_output(
    actual_full, ceiling, base_budget
):
    kwargs, project, chapter = actual_full
    kwargs["total_input_budget_tokens"] = base_budget
    with bind_run("local-fixture", project_id=project.id, stage="writer", capacity_limit=ceiling):
        context = capacity(project, chapter, kwargs["additional_blocks"])
        with pytest.raises(ModelRequestCapacityError) as caught:
            drafts._compile_rendered_writer_prompt(**kwargs, source_capacity=context)
    assert caught.value.receipt["reserved_total_tokens"] == 59779
    assert caught.value.receipt["output_reserve_tokens"] == 16384
    assert caught.value.receipt["total_budget"] == ceiling


def test_optional_style_cannot_steal_atomic_legacy_source_space():
    project = SimpleNamespace(id=uuid4(), metadata_json={})
    chapter = SimpleNamespace(id=uuid4(), chapter_number=2)
    delivery = typed(project, chapter)
    fact = "作者的否定、时序与必要原句均须完整保留。" * 60
    context = capacity(project, chapter, [delivery])
    intruder = prompt_compiler.PromptBlock(
        key="aaa.optional_style",
        channel="user",
        layer="scene_spec",
        authority=100,
        source="optional.fixture",
        instruction_family="optional.style",
        text="可丢弃的风格例子。" * 100,
        provenance=prompt_compiler.PromptProvenance(kind="legacy", source_id="optional"),
    )
    compiled = drafts._compile_rendered_writer_prompt(
        path="chapter_first",
        system_prompt="Write prose.",
        user_prompt=fact,
        section_texts={"story_state": fact},
        additional_blocks=[delivery, intruder],
        total_input_budget_tokens=300,
        prompt_safety_margin=0.1,
        source_capacity=context,
    )
    assert fact in compiled.user and delivery.text in compiled.user
    assert intruder.key in compiled.report.dropped
    assert compiled.report.writer_source_capacity["expanded"]


def test_unrecognized_author_fields_and_original_reference_authority_survive(monkeypatch):
    project, chapter, scene, packet = _inputs("zh-CN", 2)
    author = "作者原句『不可提前开门；必须先确认印章，再交还原件。』" * 40  # noqa: RUF001
    packet.chapter_contract = {"unknown_author_clause": {"author_original": author}}
    delivery = typed(project, chapter)
    context = capacity(project, chapter, [delivery])
    captured = []
    original = source.compile_prompt

    def observe(blocks, **kwargs):
        captured.extend(blocks)
        return original(blocks, **kwargs)

    monkeypatch.setattr(source, "compile_prompt", observe)
    compiled = drafts.build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        packet,
        target_word_count=2600,
        prompt_mode="compiled",
        prose_prompt_profile="lean",
        total_input_budget_tokens=1000,
        compiler_additional_blocks=[delivery],
        source_capacity=context,
    )
    assert author in compiled.user and "unknown_author_clause" in compiled.user
    references = [block for block in captured if block.provenance.kind == "legacy"]
    assert references and all(
        not block.required and block.channel == "user" and block.layer != "hard_canon"
        for block in references
    )
    measured = compiled.report.writer_source_capacity["capacity"]
    assert context.output_tokens == measured["effective_max_output_tokens"]


def test_base_success_records_capacity_without_expanding_config():
    project = SimpleNamespace(id=uuid4(), metadata_json={})
    chapter = SimpleNamespace(id=uuid4(), chapter_number=2)
    block = typed(project, chapter)
    context = capacity(project, chapter, [block])
    compiled = drafts._compile_rendered_writer_prompt(
        path="chapter_first",
        system_prompt="Write prose.",
        user_prompt="Keep source.",
        section_texts={"chapter_task": "Keep source."},
        additional_blocks=[block],
        total_input_budget_tokens=14800,
        prompt_safety_margin=0.1,
        source_capacity=context,
    )
    receipt = compiled.report.writer_source_capacity
    assert receipt["base_budget_tokens"] == receipt["input_allowance_tokens"] == 14800
    assert not receipt["expanded"] and receipt["capacity"]["budget_passed"]


@pytest.mark.parametrize("change", ["body", "scope", "authority", "story_flag"])
@pytest.mark.parametrize("base_budget", [14800, 22000])
def test_changed_or_unverified_delivery_cannot_enable_capacity(change, base_budget):
    project = SimpleNamespace(id=uuid4(), metadata_json={})
    chapter = SimpleNamespace(id=uuid4(), chapter_number=2)
    block = typed(project, chapter)
    context = capacity(project, chapter, [block])
    if change == "scope":
        with pytest.raises(ValueError, match="scope_changed"):
            capacity(SimpleNamespace(id=uuid4(), metadata_json={}), chapter, [block])
        return
    if change == "authority":
        block = block.model_copy(update={"required": False})
        with pytest.raises(ValueError, match="verified_delivery"):
            capacity(project, chapter, [block])
        return
    if change == "story_flag":
        context = {"source_capacity": True, "capacity_limit": 1_000_000}
    else:
        block = block.model_copy(update={"text": block.text + " "})
    with pytest.raises(ValueError, match=r"context_invalid|binding_changed"):
        source.compile_writer_source_prompt(
            [block],
            integrity_keys=set(),
            base_budget_tokens=base_budget,
            safety_margin=0.1,
            capacity=context,
        )


def test_output_floor_candidates_and_context_ceiling_use_existing_role_rules():
    project = SimpleNamespace(id=uuid4(), metadata_json={})
    chapter = SimpleNamespace(id=uuid4(), chapter_number=2)
    block = typed(project, chapter)
    settings = settings_for("openai/gpt-4o")
    settings.llm.writer.n_candidates = 3
    context = capacity(project, chapter, [block], settings=settings, override=1000)
    request = llm.LLMCompletionRequest(
        logical_role="writer",
        system_prompt="capacity",
        user_prompt="capacity",
        fallback_response="not dispatched",
        max_tokens_override=1000,
    )
    actual = llm._effective_input_payload(request, settings.llm.writer)
    assert context.output_tokens == actual["max_tokens"]
    assert context.n_candidates == actual["n_candidates"] == 3
    settings = settings_for()
    assert capacity(project, chapter, [block], settings=settings, override=1).output_tokens == (
        llm._effective_request_max_tokens(
            settings.llm.writer,
            request.model_copy(
                update={
                    "max_tokens_override": 1,
                }
            ),
        )
    )
    assert capacity(project, chapter, []) is None


def test_unknown_model_cannot_borrow_a_larger_bound_model_context():
    from bestseller.services.model_context_capacity import bind_model_capacity

    project = SimpleNamespace(id=uuid4(), metadata_json={})
    chapter = SimpleNamespace(id=uuid4(), chapter_number=2)
    block = typed(project, chapter)
    settings = settings_for("openai/unlisted-fixture-model")
    with bind_run("local-fixture", project_id=project.id, stage="writer", capacity_limit=1_000_000):
        with bind_model_capacity("MiniMax-M3.1-Flash", "https://api.minimaxi.com/v1"):
            context = capacity(project, chapter, [block], settings=settings)
    assert context.capacity_limit == 65536
    unbound = capacity(project, chapter, [block], settings=settings_for("MiniMax-M3.1-Flash"))
    assert unbound.capacity_limit == 65536


def test_captured_legacy_role_reserves_its_actual_floor_and_candidates(tmp_path):
    from bestseller.services.model_catalog import LEGACY_MODEL_DEFAULTS_FILE
    from bestseller.services.prompt_source_policy import content_hash

    project = SimpleNamespace(id=uuid4(), metadata_json={})
    chapter = SimpleNamespace(id=uuid4(), chapter_number=2)
    settings = settings_for("openai/gpt-4o")
    settings.artifact_store.local_dir = str(tmp_path)
    historical = settings_for().llm.writer
    roles = {"writer": historical.model_dump(mode="json")}
    path = tmp_path / "runtime" / LEGACY_MODEL_DEFAULTS_FILE
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "schema": "legacy-project-model-defaults.v1",
                "projects": {
                    str(project.id): {"roles": roles, "roles_sha256": content_hash(roles)},
                },
            }
        )
    )
    context = capacity(project, chapter, [typed(project, chapter)], settings=settings, override=1)
    request = llm.LLMCompletionRequest(
        logical_role="writer",
        system_prompt="capacity",
        user_prompt="capacity",
        fallback_response="not dispatched",
        max_tokens_override=1,
    )
    effective = llm._effective_input_payload(request, historical)
    assert context.model == historical.model
    assert context.output_tokens == effective["max_tokens"]
    assert context.n_candidates == effective["n_candidates"]
    broken = json.loads(path.read_text())
    broken["projects"][str(project.id)]["roles_sha256"] = "0" * 64
    path.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="legacy_project_model_defaults_changed"):
        capacity(project, chapter, [typed(project, chapter)], settings=settings, override=1)
    # An explicit model selection has the gateway's existing fallback policy;
    # it must not silently borrow an unrelated captured default on lookup miss.
    project.metadata_json["llm_model_id"] = "missing-catalog-fixture"
    current = capacity(project, chapter, [typed(project, chapter)], settings=settings, override=1)
    assert current.model == settings.llm.writer.model
    assert current.output_tokens == llm._effective_request_max_tokens(settings.llm.writer, request)


@pytest.mark.asyncio
async def test_initial_production_writer_plumbs_capacity_and_source_complete_guard(db, monkeypatch):
    session, project, chapter, _scene, _ = db
    _, _, _, packet = _inputs("zh-CN", chapter.chapter_number)
    packet.query_text = "fixture"
    settings = settings_for()
    delivery = typed(project, chapter)

    async def nothing(*args, **kwargs):
        return None

    async def verified_loader(*args, **kwargs):
        return [delivery]

    from bestseller.services import chapter_writer_delivery

    monkeypatch.setattr(chapter_writer_delivery, "load_delivery_blocks", verified_loader)
    for name in (
        "_enrich_chapter_first_context",
        "_render_chapter_first_character_safety_block",
        "_prepare_quality_uplift_prompt_blocks",
    ):
        monkeypatch.setattr(drafts, name, nothing)
    monkeypatch.setattr(drafts, "_maybe_write_chapter_prompt_trace", lambda *a, **k: None)
    seen = []

    class CapturedError(Exception):
        pass

    async def capture(_session, _settings, request):
        seen.append(request)
        raise CapturedError

    monkeypatch.setattr(drafts, "complete_text", capture)
    with pytest.raises(CapturedError):
        await drafts.generate_chapter_draft_once(
            session,
            project.slug,
            chapter.chapter_number,
            settings=settings,
            context_packet=packet,
        )
    request = seen[0]
    report = request.metadata["prompt_compiler_report"]["writer_source_capacity"]
    assert request.max_tokens_override == 16384
    assert report["capacity"]["effective_max_output_tokens"] == 16384
    assert request.metadata["require_source_complete_capacity"] is True
    assert request.metadata["regeneration_feedback_source"] is None
    assert settings.llm.writer_total_input_budget_tokens == 14800
