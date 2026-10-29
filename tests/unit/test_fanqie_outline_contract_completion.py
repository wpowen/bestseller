"""Native SQLite custody/gate tests; only completion and Redis IO are inert.

No production database/provider/network is permitted. Selected foundations,
literal Beat projection, capacity admission and hard validators run unchanged.
"""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from test_fanqie_beat_paid_resume import LeaseIO, acquired
from test_fanqie_beat_recovery import case  # noqa: F401 - native SQLite fixture
from test_foundation_field_recovery import record_completion
from test_foundation_selection import store  # noqa: F401 - dependency of case

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterModel,
    CharacterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import fanqie_beat_recovery as beat
from bestseller.services import fanqie_outline_contract_completion as completion
from bestseller.services import foundation_selection, llm, planner
from bestseller.services.fanqie_short_planner import build_fanqie_segment_outline_batch
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.narrative_contracts import (
    build_identity_manifest,
    validate_chapter_plan_contract,
)
from bestseller.services.planning_readiness_gate import (
    evaluate_chapter_outline_batch_planning_readiness,
)
from bestseller.services.progress_context import TaskCancelledError
from bestseller.worker import tasks

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
NATIVE_COMPLETE_TEXT = llm.complete_text


def authored_fields(index):
    """Explicit prospective fixture answers, never production field inference."""
    number = index + 1
    return {
        "title": f"The brass lens {number}",
        "opening_situation": f"At the observatory bench, Mira faces the broken lens {number}.",
        "opening_pressure": f"The inspector demands lens {number} before dawn.",
        "main_conflict": f"The inspector refuses to release broken lens {number} to Mira.",
        "hook_description": f"Who removed the brass retaining pin from lens {number}?",
        "tail_hook": f"Who removed the brass retaining pin from lens {number}?",
        "protagonist_flaw": "Mira conceals mistakes until a repair becomes expensive.",
        "payoff": f"Mira recovers lens {number} and measures its true focal length.",
        "visible_action_or_reaction": f"Mira dismantles lens {number} on the repair bench.",
        "chapter_function": "recover a damaged instrument",
        "pressure": f"The inspector demands lens {number} before dawn.",
        "protagonist_choice": f"Mira chooses to dismantle lens {number} and forfeits her deposit.",
        "resistance": f"The inspector refuses to release lens {number} without a deposit.",
        "cost_or_tradeoff": f"Mira loses the deposit for lens {number}.",
        "gain_or_reveal": f"Mira discovers a missing retaining pin in lens {number}.",
        "state_change": f"Lens {number} moves from sealed storage to Mira's repair bench.",
        "next_reader_desire": f"Who removed the retaining pin from lens {number}?",
        "time_label": f"Night {number}, 02:15, observatory repair bench",
        "scene_type": "conflict",
        "participants": ["Mira"],
        "entry_state": {"instrument": f"Lens {number} remains in sealed storage."},
        "exit_state": {"instrument": f"Lens {number} lies dismantled on the repair bench."},
        "conflict_stakes": f"Failure to repair lens {number} costs Mira her deposit.",
        "information_control": f"Mira sees the empty retaining-pin socket in lens {number}.",
        "signature_image": f"Brass lens {number} split across a white repair cloth.",
        "cut_point": f"Mira finds the empty retaining-pin socket in lens {number}.",
        "action_sequence": [f"Mira opens the case and dismantles lens {number}."],
        "key_dialogue_beats": [f"Mira requests custody of lens {number}."],
        "sensory_anchors": {"touch": "Cold brass at the rim of the lens."},
        "story": f"Mira retrieves and dismantles lens {number}.",
        "emotion": "Anxiety gives way to determination.",
    }


def raw_sheet(*, complete=False, segments=4):
    raw = {
        "title": "The observatory lenses",
        "logline": "Mira repairs four damaged lenses before dawn.",
        "pov": "first_person",
        "unlock_milestone_segment": 2,
        "opaque_source": {"literal": "§0 $ref", "unset": None, "boolean": False},
        "beats": [],
    }
    for index in range(segments):
        fields = authored_fields(index)
        item = {
            "segment_number": index + 1,
            "beat_role": "recover a damaged instrument",
            "purpose": fields["story"],
            "payoff": fields["payoff"],
            "emotional_turn": fields["emotion"],
            # Match the observed raw shape: names/prose are not attendance.
            "opening_contract": {
                "required_opening_facts": [fields["opening_situation"]],
                "scene_drive": fields["story"],
            },
            "continuity_contract": {
                "chapter_end_hook": fields["hook_description"],
                "actions": fields["action_sequence"],
                "entry_state": fields["entry_state"],
                "exit_state": fields["exit_state"],
            },
            "ability_cost_contract": {"costs": [fields["cost_or_tradeoff"]]},
            "unknown_beat": {"exact": [None, False, 0, {"$ref": "authored data"}]},
        }
        if complete:
            item["opening_contract"].update(
                summary=fields["opening_situation"],
                public_pressure=fields["main_conflict"],
                resistance=fields["resistance"],
                immediate_stakes=fields["conflict_stakes"],
                participants=fields["participants"],
                scene_type=fields["scene_type"],
                signature_image=fields["signature_image"],
                cut_point=fields["cut_point"],
            )
            item["continuity_contract"].update(
                time_position=fields["time_label"],
                fact_boundary=fields["information_control"],
            )
            item["unlock_contract"] = {"next_segment_trigger": fields["hook_description"]}
            item["ability_cost_contract"]["cost"] = fields["cost_or_tradeoff"]
            item["closure_contract"] = {
                "irreversible_choice": fields["protagonist_choice"],
                "state_change": fields["state_change"],
            }
        raw["beats"].append(item)
    return raw


async def protected_rows(c):
    result = {}
    with c.session.no_autoflush:
        for model in (
            ProjectModel,
            PlanningArtifactVersionModel,
            ChapterModel,
            CharacterModel,
            WorkflowRunModel,
            LlmRunModel,
            ExecutionBudgetModel,
            ExecutionOperationModel,
            ExecutionAttemptModel,
        ):
            rows = (await c.session.execute(select(*model.__table__.columns))).mappings()
            result[model.__tablename__] = deepcopy(list(rows))
    return result


def physical_files(c):
    return {str(path): path.read_bytes() for path in c.paths.values()}


@pytest.fixture
async def outline_case(case, monkeypatch, request):  # noqa: F811
    c = case
    options = getattr(request, "param", None)
    segments = (
        options
        if type(options) is int
        else (options.get("segments", 4) if type(options) is dict else 4)
    )
    assert c.session.bind.dialect.name == "sqlite"
    assert "test" in Path(c.session.bind.url.database).name
    for model in (ChapterModel, CharacterModel):
        table = model.__table__.to_metadata(MetaData())
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            if column.server_default is not None and "::" in str(column.server_default.arg):
                column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with c.session.bind.begin() as connection:
            await connection.run_sync(table.create)
    foundation_id = c.project.metadata_json[foundation_selection.POINTER]["workflow_id"]
    c.project.target_chapters, c.project.target_word_count = segments, segments * 2000
    if segments != 4:
        c.project.metadata_json = {**c.project.metadata_json, "segment_count": segments}
    c.project.status = "planning"
    c.project.project_type = "fanqie_short"
    c.settings.pipeline.require_chapter_plan_contract = True
    c.settings.pipeline.enable_chapter_causality_gate = True
    c.settings.pipeline.chapter_causality_gate_block_on_failure = True
    c.settings.pipeline.enable_methodology_planning_readiness_gate = True
    c.settings.pipeline.methodology_planning_readiness_block_on_failure = True
    cast = deepcopy(c.assets["cast_spec"].content)
    cast["protagonist"] = {
        "name": "Mira",
        "role": "protagonist",
        "gender": "female",
        "pronoun_set_zh": "她",
        "pronoun_set_en": "she/her",
        "flaw": authored_fields(0)["protagonist_flaw"],
        "social_network": {"mentors": [{"name": "Rook"}]},
    }
    c.assets["cast_spec"].content = cast
    # Keep unknown source fields in actual selected rows, not a synthetic source.
    c.assets["book_spec"].content = {
        **c.assets["book_spec"].content,
        "opaque_source": {"literal": "§0 $ref", "unset": None, "boolean": False},
    }
    canonical_id = c.project.metadata_json["creation_canonical_snapshot_id"]
    canonical = await c.session.get(PlanningArtifactVersionModel, UUID(canonical_id))
    detached = SimpleNamespace(
        id=c.project.id,
        language=c.project.language,
        target_chapters=segments,
        target_word_count=segments * 2000,
        metadata_json=deepcopy(c.project.metadata_json),
    )
    detached._source_bound_planning_canonical = {
        "id": canonical_id,
        "content_hash": c.project.metadata_json["creation_canonical_snapshot_hash"],
        "content": deepcopy(canonical.content),
    }
    projection = planner._compile_source_bound_volume_plan(
        detached,
        c.sources["premise"],
        *(c.assets[kind].content for kind in ("book_spec", "world_spec", "cast_spec")),
    )
    c.assets["volume_plan"].content = [
        {
            "volume_number": 1,
            "_meta": {"source_projection_sha256": planner._approved_payload_hash(projection)},
        }
    ]
    await c.session.commit()
    await foundation_selection.pin_completed_foundation(
        c.session,
        project=c.project,
        workflow_id=UUID(foundation_id),
        settings=c.settings,
    )
    await c.session.commit()
    c.redis = LeaseIO()
    monkeypatch.setattr("redis.asyncio.Redis.from_url", lambda *a, **kw: c.redis)
    c.raw = raw_sheet(
        complete=options == "complete"
        or (type(options) is dict and options.get("complete", False)),
        segments=segments,
    )
    c.requests, c.runs = [], []
    persist = llm._persist_prompt_payloads

    def native_physical_role(**kwargs):
        if "source_complete_retention" in kwargs["request"].metadata:
            from bestseller.services.planning_surgical_budget import resolve_request_role

            kwargs["effective_role_settings"] = resolve_request_role(
                c.settings, c.project, kwargs["request"]
            )
        return persist(**kwargs)

    monkeypatch.setattr(llm, "_persist_prompt_payloads", native_physical_role)

    async def beat_transport(session, settings, req):
        assert req.prompt_template == beat.TEMPLATE
        c.beat_request = req.model_copy(deep=True)
        paid, c.beat_run = await record_completion(c, req, content=beat._json(c.raw))
        c.budget.calls_used += 1
        c.budget.tokens_charged += paid.input_tokens + paid.output_tokens
        return paid

    monkeypatch.setattr(llm, "complete_text", beat_transport)
    async with acquired(c):
        c.selection = await beat.ensure_beat_sheet(
            c.session,
            c.settings,
            project_slug=c.project.slug,
            premise=c.sources["premise"],
            book_spec=c.assets["book_spec"].content,
            cast_spec=c.assets["cast_spec"].content,
        )
        await c.session.commit()
        # Exercise the real read-only cache before using the public projector.
        before = await protected_rows(c)
        cached = await beat.ensure_beat_sheet(
            c.session,
            c.settings,
            project_slug=c.project.slug,
            premise=c.sources["premise"],
            book_spec=c.assets["book_spec"].content,
            cast_spec=c.assets["cast_spec"].content,
        )
        assert cached.reused and cached.artifact_id == c.selection.artifact_id
        assert await protected_rows(c) == before
    c.base = build_fanqie_segment_outline_batch(
        c.project,
        c.selection.sheet,
        book_spec=c.assets["book_spec"].content,
        cast_spec=c.assets["cast_spec"].content,
    )
    c.original_rows, c.original_files = await protected_rows(c), physical_files(c)

    async def inert_transport(session, settings, req):
        assert session is c.session and settings.artifact_store.retain_prompt_payloads
        assert req.project_id == c.project.id and req.workflow_run_id
        assert req.logical_role == "planner"
        assert req.prompt_template == "planner_structured_field_correction"
        assert (
            req.prompt_version
            == ("fanqie-plan-fields-v1" if segments == 4 else "fanqie-plan-fields-v2")
            and len(req.prompt_version) <= 32
        )
        c.requests.append(req.model_copy(deep=True))
        # Decode the transmitted document through the actual lossless decoder.
        outer = json.loads(req.user_prompt[req.user_prompt.index("{") :])
        document = decode_lossless_source(outer["planning_document"])
        source = document["source"]
        assert source["original_beat"]["raw"] == c.raw
        assert source["base_outline"] == c.base
        selected = source["selected_source"]
        assert selected["canonical"]["content"] == canonical.content
        assert selected["complete_foundation_content"] == {
            kind: row.content for kind, row in c.assets.items()
        }
        assert all(
            key in selected
            for key in (
                "explicit_creation_contracts",
                "current_author_fields",
                "required_author_layers",
                "required_author_secondary_roles",
            )
        )
        assert document["native_blocking_diagnostics"] == completion._diagnose(
            document["current_candidate"],
            c.project,
            c.settings,
            c.assets["cast_spec"].content,
        )
        index = req.metadata["outline_completion_part"]
        assert document["part"] == index
        schema = outer["response_schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        values = authored_fields(index)
        patch = {
            pointer: deepcopy(values[pointer.rsplit("/", 1)[1]]) for pointer in schema["required"]
        }
        transform = getattr(c, "transform", None)
        content = beat._json(patch) if transform is None else transform(req, patch)
        if isinstance(content, BaseException):
            raise content
        paid, row = await record_completion(c, req, content=content)
        row.metadata_json = {**row.metadata_json, **deepcopy(req.metadata)}
        c.runs.append(row)
        c.budget.calls_used += 1
        c.budget.tokens_charged += paid.input_tokens + paid.output_tokens
        after_paid = getattr(c, "after_paid", None)
        if after_paid is not None:
            await after_paid(row)
        return paid

    monkeypatch.setattr(llm, "complete_text", inert_transport)
    return c


async def complete(c, payload=None):
    async with acquired(c):
        return await completion.complete_missing_outline_contract(
            c.session,
            c.settings,
            project_id=c.project.id,
            outline_payload=deepcopy(c.base if payload is None else payload),
            requested_by="synthetic-native-outline-test",
        )


async def test_four_parts_preserve_every_source_and_do_not_import(outline_case):
    c = outline_case
    original = deepcopy(c.base)
    assert all(not chapter["scenes"][0]["participants"] for chapter in c.base["chapters"])
    result = await complete(c)
    assert c.base == original
    assert len(c.requests) == 4
    assert [
        (r.metadata["outline_completion_part"], r.metadata["outline_completion_attempt"])
        for r in c.requests
    ] == [(i, 0) for i in range(4)]
    assert completion._diagnose(result, c.project, c.settings, c.assets["cast_spec"].content) == []
    batch = ChapterOutlineBatchInput.model_validate(result)
    identities = build_identity_manifest(c.assets["cast_spec"].content)
    assert {entry["name"] for entry in identities} == {"Mira", "Rook"}
    assert not validate_chapter_plan_contract(
        batch, identity_manifest=identities
    ).blocking_violations
    assert evaluate_chapter_outline_batch_planning_readiness(batch).passed
    for before, after in zip(original["chapters"], result["chapters"], strict=True):
        assert before["chapter_number"] == after["chapter_number"]
        assert before["chapter_goal"] == after["chapter_goal"]
        assert before["methodology_contract"] == after["methodology_contract"]
        assert (
            before["causal_contract"]["fanqie_short_v2"]
            == after["causal_contract"]["fanqie_short_v2"]
        )
        assert before["scenes"][0]["entry_state"] == after["scenes"][0]["entry_state"]
        assert before["scenes"][0]["exit_state"] == after["scenes"][0]["exit_state"]
        scene_before = before["scenes"][0]["methodology_contract"]
        scene_after = after["scenes"][0]["methodology_contract"]
        assert scene_before["conflict_stakes"] == ""
        assert scene_after["conflict_stakes"]
        assert {key: value for key, value in scene_before.items() if key != "conflict_stakes"} == {
            key: value for key, value in scene_after.items() if key != "conflict_stakes"
        }
    assert result["_meta"]["segment_projection"] == original["_meta"]["segment_projection"]
    receipt = result["_meta"]["outline_contract_completion"]
    assert len(receipt["parts"]) == 4
    assert receipt["generation_llm_run_ids"][0] == str(c.beat_run.id)
    assert set(receipt["generation_llm_run_ids"][1:]) == {str(row.id) for row in c.runs}
    assert all(
        receipt[key] is False
        for key in (
            "single_provider_payload_claimed",
            "grade_asserted",
            "adoption_granted",
            "credit_granted",
        )
    )
    await c.session.commit()
    current = await protected_rows(c)
    for table in ("projects", "planning_artifact_versions", "chapters", "characters"):
        assert current[table] == c.original_rows[table]
    for path, content in c.original_files.items():
        assert Path(path).read_bytes() == content
    assert c.budget.calls_used == 5 and c.budget.tokens_charged == 90


@pytest.mark.parametrize("outline_case", ["complete"], indirect=True)
async def test_noop_has_no_new_workflow_or_paid_call(outline_case):
    c = outline_case
    assert completion._diagnose(c.base, c.project, c.settings, c.assets["cast_spec"].content) == []
    before, files = await protected_rows(c), physical_files(c)
    assert await complete(c) == c.base
    assert not c.requests and await protected_rows(c) == before and physical_files(c) == files


async def test_repeat_replays_same_physical_rows_and_fees(outline_case):
    c = outline_case
    first = await complete(c)
    await c.session.commit()
    before, files = await protected_rows(c), physical_files(c)
    second = await complete(c)
    assert second == first and len(c.requests) == 4
    assert await protected_rows(c) == before and physical_files(c) == files


async def test_invalid_mapping_has_one_protocol_retry_then_replays(outline_case):
    c = outline_case

    def transform(req, patch):
        if (
            req.metadata["outline_completion_part"] == 0
            and req.metadata["outline_completion_attempt"] == 0
        ):
            patch["/chapters/0/chapter_goal"] = "A different authored event"
        return beat._json(patch)

    c.transform = transform
    first = await complete(c)
    await c.session.commit()
    assert len(c.requests) == 5
    assert [r.metadata["outline_completion_attempt"] for r in c.requests[:2]] == [0, 1]
    assert first["chapters"][0]["chapter_goal"] == c.base["chapters"][0]["chapter_goal"]
    assert len(first["_meta"]["outline_contract_completion"]["parts"]) == 5
    before = await protected_rows(c)
    assert await complete(c) == first and len(c.requests) == 5
    assert await protected_rows(c) == before


async def test_protocol_bound_survives_repeat_without_resetting_fees(outline_case):
    c = outline_case
    c.transform = lambda req, patch: "{}"
    with pytest.raises(completion.OutlineContractCompletionError) as caught:
        await complete(c)
    assert caught.value.candidate == c.base and caught.value.diagnostics
    assert "bounded_protocol_exhausted" in str(caught.value)
    await c.session.commit()
    before, files = await protected_rows(c), physical_files(c)
    with pytest.raises(
        completion.OutlineContractCompletionError, match="bounded_protocol_exhausted"
    ):
        await complete(c)
    assert len(c.requests) == 2 and c.budget.calls_used == 3
    assert await protected_rows(c) == before and physical_files(c) == files


async def test_unknown_transport_never_retries_or_resets_reservation(outline_case):
    c = outline_case
    c.transform = lambda req, patch: TimeoutError("inert transport outcome is unknown")
    with pytest.raises((TimeoutError, completion.OutlineContractCompletionError)):
        await complete(c)
    assert len(c.requests) == 1 and not c.runs
    await c.session.commit()
    before = await protected_rows(c)
    with pytest.raises(completion.OutlineContractCompletionError, match="unknown") as caught:
        await complete(c)
    assert caught.value.candidate == c.base and caught.value.diagnostics
    assert len(c.requests) == 1 and await protected_rows(c) == before


async def test_altered_passed_base_fails_before_dispatch(outline_case):
    c = outline_case
    changed = deepcopy(c.base)
    changed["chapters"][0]["chapter_goal"] = "A newly invented plot"
    before = await protected_rows(c)
    with pytest.raises(
        completion.OutlineContractCompletionError, match="passed_base_not_exact"
    ) as caught:
        await complete(c, changed)
    assert caught.value.candidate == changed
    assert not c.requests and await protected_rows(c) == before


async def test_known_field_and_contract_patch_rejected_atomically(outline_case):
    c = outline_case
    diagnostics = completion._diagnose(c.base, c.project, c.settings, c.assets["cast_spec"].content)
    targets = completion._targets(c.base, diagnostics, 0)
    assert "/chapters/0/main_conflict" in targets
    assert "/chapters/0/scenes/0/time_label" in targets
    assert "/chapters/0/scenes/0/participants" in targets
    patch = {path: authored_fields(0)[path.rsplit("/", 1)[1]] for path in targets}
    for path, value in (
        ("/chapters/0/chapter_goal", "New event"),
        ("/chapters/0/chapter_number", 2),
        ("/chapters/0/scenes/0/entry_state", {"forged": "state"}),
        ("/chapters/0/methodology_contract/fanqie_short_v2", {}),
        ("/chapters/0/body", "Invented chapter prose"),
    ):
        with pytest.raises(ValueError):
            completion._apply_patch(c.base, targets, beat._json({**patch, path: value}))
    result = completion._apply_patch(c.base, targets, beat._json(patch))
    assert result["chapters"][0]["main_conflict"] == patch["/chapters/0/main_conflict"]
    assert c.base["chapters"][0]["main_conflict"] == ""


async def test_targets_cover_current_native_codes_without_raw_inference(outline_case):
    c = outline_case
    diagnostics = completion._diagnose(c.base, c.project, c.settings, c.assets["cast_spec"].content)
    assert len(diagnostics) > 16
    assert {d["code"] for d in diagnostics} >= {
        "PLAN_CHAPTER_CONFLICT_MISSING",
        "PLAN_SCENE_TIME_MISSING",
        "PLAN_SCENE_PARTICIPANTS_MISSING",
        "CHAPTER_CAUSAL_NEXT_DESIRE_WEAK",
        "PLANNING_CHAPTER_FIELD_MISSING",
        "PLANNING_SCENE_FIELD_MISSING",
    }
    candidate = deepcopy(c.base)
    for index in range(4):
        targets = completion._targets(candidate, diagnostics, index)
        assert targets and all(pointer.startswith(f"/chapters/{index}/") for pointer in targets)
        stakes_path = f"/chapters/{index}/scenes/0/methodology_contract/conflict_stakes"
        assert stakes_path in targets
        assert f"/chapters/{index}/scenes/0/conflict_stakes" not in targets
        assert all(
            "fanqie_short_v2" not in pointer
            and ("methodology_contract" not in pointer or pointer == stakes_path)
            for pointer in targets
        )
        assert f"/chapters/{index}/scenes/0/entry_state" not in targets
        patch = {pointer: authored_fields(index)[pointer.rsplit("/", 1)[1]] for pointer in targets}
        candidate = completion._apply_patch(candidate, targets, beat._json(patch))
    remaining = completion._diagnose(
        candidate, c.project, c.settings, c.assets["cast_spec"].content
    )
    assert remaining == [], remaining


@pytest.mark.parametrize("outline_case", ["missing", "complete"], indirect=True)
@pytest.mark.parametrize("status", ["approved", "needs_review", "superseded"])
async def test_any_altered_existing_outline_blocks_before_paid(outline_case, status):
    c = outline_case
    changed = deepcopy(c.base)
    changed["chapters"][0]["chapter_goal"] = "A manual edit to an authored fact"
    c.session.add(
        PlanningArtifactVersionModel(
            project_id=c.project.id,
            artifact_type="chapter_outline_batch",
            status=status,
            version_no=1,
            schema_version="v1",
            content=changed,
        )
    )
    await c.session.commit()
    before = await protected_rows(c)
    with pytest.raises(completion.OutlineContractCompletionError, match="existing_outline"):
        await complete(c)
    assert not c.requests and await protected_rows(c) == before


async def test_physically_completed_derived_artifact_allows_repeat_without_paid(outline_case):
    c = outline_case
    first = await complete(c)
    receipt = first["_meta"]["outline_contract_completion"]
    c.session.add(
        PlanningArtifactVersionModel(
            project_id=c.project.id,
            artifact_type="chapter_outline_batch",
            status="approved",
            version_no=1,
            schema_version="v1",
            content=deepcopy(first),
            source_run_id=UUID(receipt["workflow_run_id"]),
        )
    )
    await c.session.commit()
    before, files = await protected_rows(c), physical_files(c)
    assert await complete(c) == first and len(c.requests) == 4
    assert await protected_rows(c) == before and physical_files(c) == files


async def test_known_registered_leaf_cannot_be_overwritten_even_if_requested(outline_case):
    c = outline_case
    candidate = deepcopy(c.base)
    candidate["chapters"][0]["main_conflict"] = "A known authored conflict"
    path = "/chapters/0/main_conflict"
    with pytest.raises(
        completion.OutlineContractCompletionError, match="known_field_change_forbidden"
    ):
        completion._apply_patch(
            candidate,
            {path: {"type": "string", "minLength": 1}},
            beat._json({path: "A different conflict"}),
        )
    state = "/chapters/0/scenes/0/entry_state"
    with pytest.raises(
        completion.OutlineContractCompletionError, match="known_field_change_forbidden"
    ):
        completion._apply_patch(
            candidate,
            {state: {"type": "object", "minProperties": 1}},
            beat._json({state: {"forged": "entry state"}}),
        )
    assert candidate["chapters"][0]["main_conflict"] == "A known authored conflict"


async def test_unknown_financial_result_is_not_a_protocol_retry(outline_case):
    c = outline_case

    async def unknown_result(row):
        key = row.metadata_json["execution_receipt"]["operation_key"]
        root = await c.session.get(ExecutionOperationModel, key)
        root.status = "unknown"

    c.after_paid = unknown_result
    with pytest.raises(completion.OutlineContractCompletionError) as caught:
        await complete(c)
    assert caught.value.candidate == c.base and len(c.requests) == 1
    await c.session.commit()
    before = await protected_rows(c)
    with pytest.raises(completion.OutlineContractCompletionError):
        await complete(c)
    assert len(c.requests) == 1 and c.budget.calls_used == 2
    assert await protected_rows(c) == before


async def test_replay_requires_original_physical_completion_file(outline_case):
    c = outline_case
    await complete(c)
    await c.session.commit()
    c.paths[f"{c.runs[0].id}:response"].unlink()
    before = await protected_rows(c)
    with pytest.raises(completion.OutlineContractCompletionError):
        await complete(c)
    assert len(c.requests) == 4 and await protected_rows(c) == before


@pytest.mark.parametrize("outline_case", ["missing", "complete"], indirect=True)
@pytest.mark.parametrize("kind", ["chapter", "character"])
async def test_existing_native_content_count_blocks_paid_and_noop(outline_case, kind):
    c = outline_case
    if kind == "chapter":
        row = ChapterModel(
            project_id=c.project.id,
            chapter_number=1,
            chapter_goal="A preserved existing chapter",
            status="planned",
        )
    else:
        row = CharacterModel(project_id=c.project.id, name="Mira", role="protagonist")
    c.session.add(row)
    await c.session.commit()
    before = await protected_rows(c)
    with pytest.raises(
        completion.OutlineContractCompletionError, match="existing_chapters_or_characters"
    ):
        await complete(c)
    assert not c.requests and await protected_rows(c) == before


@pytest.mark.parametrize("change", ["source", "manual_outline", "pause", "lease"])
async def test_after_paid_guard_blocks_publication_and_further_calls(outline_case, change):
    c = outline_case

    async def change_current_state(_row):
        assert len(c.requests) == 1
        if change == "source":
            c.assets["book_spec"].content = {
                **c.assets["book_spec"].content,
                "new_authored_fact": "Changed after dispatch",
            }
        elif change == "manual_outline":
            changed = deepcopy(c.base)
            changed["chapters"][0]["chapter_goal"] = "A manual change after dispatch"
            c.session.add(
                PlanningArtifactVersionModel(
                    project_id=c.project.id,
                    artifact_type="chapter_outline_batch",
                    status="approved",
                    version_no=1,
                    schema_version="v1",
                    content=changed,
                )
            )
        elif change == "pause":
            c.session.add(
                BookProductionControlModel(
                    project_id=c.project.id,
                    desired_state="pause",
                )
            )
        else:
            c.redis.values[tasks._pipeline_live_key(c.project.slug)] = uuid4().hex

    c.after_paid = change_current_state
    with pytest.raises((completion.OutlineContractCompletionError, TaskCancelledError)) as caught:
        await complete(c)
    if isinstance(caught.value, completion.OutlineContractCompletionError):
        assert caught.value.candidate == c.base and caught.value.diagnostics
    else:
        assert change == "lease"
    assert len(c.requests) == len(c.runs) == 1
    assert c.budget.calls_used == 2 and c.budget.tokens_charged == 36
    assert all(Path(path).read_bytes() == content for path, content in c.original_files.items())
    own_workflows = list(
        await c.session.scalars(
            select(WorkflowRunModel).where(
                WorkflowRunModel.workflow_type == completion.WORKFLOW_TYPE,
            )
        )
    )
    assert len(own_workflows) == 1
    assert own_workflows[0].status != "completed"
    assert "outline_contract_completion" not in own_workflows[0].metadata_json
    assert not list(await c.session.scalars(select(ChapterModel)))
    assert not list(await c.session.scalars(select(CharacterModel)))


async def persist_native_outline(c, payload, *, legacy=False):
    from bestseller.domain.enums import ArtifactType
    from bestseller.domain.planning import PlanningArtifactCreate
    from bestseller.services.fanqie_short_planner import persist_fanqie_chapter_outline
    from bestseller.services.projects import import_planning_artifact

    if legacy:
        # The historical helper submitted this exact typed dump to the ordinary
        # importer, which adds its own normal project lineage metadata.
        artifact = await import_planning_artifact(
            c.session,
            c.project.slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
                content=ChapterOutlineBatchInput.model_validate(payload).model_dump(mode="json"),
            ),
        )
        identity = artifact.id
    else:
        identity = await persist_fanqie_chapter_outline(c.session, c.project.slug, payload)
    await c.session.commit()
    return await c.session.get(PlanningArtifactVersionModel, identity, populate_existing=True)


@pytest.mark.parametrize("legacy", [False, True])
async def test_real_typed_import_boundary_base_and_derived_are_replayed(outline_case, legacy):
    from full_flow_q4_short.typed_leaf_differences import complete_leaf_differences

    c = outline_case
    original = await persist_native_outline(c, c.base, legacy=legacy)
    assert "methodology_lineage" in original.content["_meta"]
    try:
        assert beat._same(
            {key: value for key, value in original.content.items() if key != "_meta"},
            ChapterOutlineBatchInput.model_validate(c.base).model_dump(mode="json"),
        )
    except AssertionError as error:
        error.code = "fanqie_typed_import_base_mismatch"
        error.differences = complete_leaf_differences(
            ChapterOutlineBatchInput.model_validate(c.base).model_dump(mode="json"),
            {key: value for key, value in original.content.items() if key != "_meta"},
        )
        error.args = (error.code,)
        raise
    first = await complete(c)
    await c.session.commit()
    derived = await persist_native_outline(c, first, legacy=legacy)
    assert derived.id != original.id
    if not legacy:
        assert (
            derived.content["_meta"]["outline_contract_completion"]
            == first["_meta"]["outline_contract_completion"]
        )
        assert (
            derived.content["_meta"]["segment_projection"] == c.base["_meta"]["segment_projection"]
        )
    before = await protected_rows(c)
    assert await complete(c) == first and len(c.requests) == 4
    assert await protected_rows(c) == before


@pytest.mark.parametrize("change", ["unknown_scene_key", "bool_number", "unknown_meta", "lineage"])
async def test_typed_persistence_does_not_normalize_away_manual_changes(outline_case, change):
    from sqlalchemy.orm.attributes import flag_modified

    c = outline_case
    artifact = await persist_native_outline(c, c.base, legacy=True)
    altered = deepcopy(artifact.content)
    if change == "unknown_scene_key":
        altered["chapters"][0]["scenes"][0]["unregistered_manual_field"] = "Do not discard"
    elif change == "bool_number":
        altered["chapters"][0]["scenes"][0]["scene_number"] = True
    elif change == "unknown_meta":
        altered["_meta"]["manual_approval"] = True
    else:
        altered["_meta"]["methodology_lineage"]["prompt_pack_key"] = "different source"
    artifact.content = altered
    # Python considers True == 1; make the intended physical JSON mutation
    # explicit so SQLAlchemy cannot suppress this negative fixture write.
    flag_modified(artifact, "content")
    await c.session.commit()
    before = await protected_rows(c)
    with pytest.raises(completion.OutlineContractCompletionError, match="existing_outline"):
        await complete(c)
    assert not c.requests and await protected_rows(c) == before


async def test_exact_new_pair_retains_large_complete_source_and_replays_physical_result(
    outline_case,
    monkeypatch,
):
    import hashlib

    from bestseller.domain.enums import WorkflowStatus
    from bestseller.services.foundation_field_recovery import _paid
    from bestseller.services.model_context_capacity import bind_model_capacity
    from bestseller.services.planning_surgical_budget import resolve_request_role
    from bestseller.services.source_complete_retention import MAX_WIRE_CHARACTERS, retention_limit
    from bestseller.services.workflows import create_workflow_run

    c = outline_case
    async with acquired(c):
        project, source = await completion._sources(c.session, c.settings, c.project.id)
        source = deepcopy(source)
        source["unique_retention_probe"] = "".join(
            hashlib.sha256(f"complete-source-{index}".encode()).hexdigest() for index in range(2200)
        )
        workflow = await create_workflow_run(
            c.session,
            project_id=c.project.id,
            workflow_type=completion.WORKFLOW_TYPE,
            status=WorkflowStatus.RUNNING,
            scope_type="project",
            scope_id=c.project.id,
            requested_by="large-retention-sqlite",
            metadata={"retention_test": True},
        )
        await c.session.commit()
        diagnostics = completion._diagnose(
            c.base, project, c.settings, c.assets["cast_spec"].content
        )
        targets = completion._targets(c.base, diagnostics, 0)
        req = completion._request(
            project, source, c.base, diagnostics, targets, workflow.id, 0, 0, None
        )
        assert 120_000 < len(req.user_prompt) < MAX_WIRE_CHARACTERS
        c.settings.llm.planner.model = "openai/MiniMax-M3.1-Flash"
        c.settings.llm.planner.api_base = "https://api.minimaxi.com/v1"
        c.settings.llm.planner.thinking_type = "enabled"
        with bind_model_capacity(c.settings.llm.planner.model, c.settings.llm.planner.api_base):
            capacity = completion._prepare_capacity(req, c.settings, project)
        assert capacity["budget_passed"] is True
        assert req.max_tokens_override <= c.settings.llm.planner.max_tokens
        role = resolve_request_role(c.settings, project, req)
        with llm.bind_project_prompt_audit(c.project.id, reason=completion.SCHEMA):
            assert (
                retention_limit(c.settings, req, audit=llm._project_prompt_audit.get(), role=role)
                == MAX_WIRE_CHARACTERS
            )
        receipt = req.metadata["source_complete_retention"]
        packet = json.loads(req.user_prompt[receipt["packet_start"] : receipt["packet_end"]])
        decoded = decode_lossless_source(packet)
        assert decoded["source"] == source and decoded["current_candidate"] == c.base
        assert receipt["roundtrip_verified"] is receipt["source_complete"] is True
        patch = {path: authored_fields(0)[path.rsplit("/", 1)[1]] for path in targets}
        assert "require_source_complete_capacity" not in req.metadata
        sent = []

        async def wire_only(actual_request, actual_role):
            sent.append(actual_request)
            assert actual_role.model == "openai/MiniMax-M3.1-Flash"
            content = beat._json(patch)
            return content, 13, 7, "stop", None, {"role": "assistant", "content": content}

        monkeypatch.setattr(llm, "_call_litellm", wire_only)
        with llm.bind_project_prompt_audit(c.project.id, reason=completion.SCHEMA):
            result = await NATIVE_COMPLETE_TEXT(c.session, c.settings, req)
        run = await c.session.get(LlmRunModel, result.llm_run_id)
        assert run is not None and sent == [req]
        await c.session.commit()
        original_files = physical_files(c)
        with bind_model_capacity(c.settings.llm.planner.model, c.settings.llm.planner.api_base):
            for _ in range(2):
                paid_run, retained, paid, proof = await _paid(
                    c.session, c.settings, project, run.id
                )
                assert paid_run.id == run.id and paid.content == result.content
                assert retained.user_prompt == req.user_prompt
                assert retained.max_tokens_override == req.max_tokens_override
                assert proof["llm_run_id"] == str(run.id)
        assert physical_files(c) == original_files and not c.requests


@pytest.mark.parametrize(
    "version,template,role_name",
    [
        ("v1-native-field-recovery", completion.TEMPLATE, "planner"),
        ("fanqie-plan-fields-v2", completion.TEMPLATE, "planner"),
        (completion.PROMPT_VERSION, "planner_structured_field_correction_other", "planner"),
        (completion.PROMPT_VERSION, completion.TEMPLATE, "critic"),
    ],
)
async def test_old_and_near_miss_pairs_keep_legacy_retention_and_reject_optin(
    outline_case,
    version,
    template,
    role_name,
):
    from bestseller.services import planning_surgical_budget as budget
    from bestseller.services.source_complete_retention import (
        LEGACY_LIMIT,
        SourceCompleteRetentionError,
        retention_limit,
        source_complete_retention_metadata,
    )

    c = outline_case
    req = llm.LLMCompletionRequest(
        logical_role=role_name,
        project_id=c.project.id,
        workflow_run_id=uuid4(),
        prompt_template=template,
        prompt_version=version,
        system_prompt="Original generic physical retention.",
        user_prompt='{"source":"complete"}',
        fallback_response="{}",
    )
    assert budget.applies(req) is False
    role = c.settings.llm.planner
    assert budget.apply_role(req, role) == role
    with llm.bind_project_prompt_audit(c.project.id, reason="legacy-near-miss-sqlite"):
        assert (
            retention_limit(c.settings, req, audit=llm._project_prompt_audit.get(), role=role)
            == LEGACY_LIMIT
            == 120_000
        )
        with pytest.raises(SourceCompleteRetentionError, match="request_contract"):
            source_complete_retention_metadata(
                req, role=role, logical_source={}, packet_start=0, packet_end=len(req.user_prompt)
            )
    assert not c.requests


async def recover_completion_error(c, error):
    from bestseller.infra.db.session import _rollback_and_save_failures

    receipts = error.workflow_failures
    assert len(receipts) == 1 and receipts[0]["workflow_type"] == completion.WORKFLOW_TYPE
    identity = UUID(receipts[0]["workflow_id"])
    await _rollback_and_save_failures(c.factory, c.session, error)
    for row in (c.project, c.budget, *c.assets.values()):
        await c.session.refresh(row)
    current = await c.session.get(WorkflowRunModel, identity, populate_existing=True)
    assert current.status == "failed"
    assert current.metadata_json["execution_failure_receipts"]
    return current


@pytest.mark.parametrize("failure", ["unknown", "exhausted"])
async def test_native_rollback_attaches_final_error_and_failed_replay_never_rearms_slots(
    outline_case,
    failure,
):
    c = outline_case
    c.transform = (
        (lambda req, patch: TimeoutError("Unknown transport result"))
        if failure == "unknown"
        else (lambda req, patch: "{}")
    )
    with pytest.raises(completion.OutlineContractCompletionError) as caught:
        await complete(c)
    # The native rollback path reads the final escaping wrapper's receipt.
    workflow = await recover_completion_error(c, caught.value)
    old_id = workflow.id
    metadata = deepcopy(workflow.metadata_json)
    slots = deepcopy(metadata["dispatches"])
    fee_rows = (await protected_rows(c))["execution_budgets"]
    calls = len(c.requests)
    expected = (
        "unknown_or_changed_dispatch_never_retry"
        if failure == "unknown"
        else "bounded_protocol_exhausted"
    )
    with pytest.raises(completion.OutlineContractCompletionError, match=expected) as repeated:
        await complete(c)
    repeated_workflow = await recover_completion_error(c, repeated.value)
    assert repeated_workflow.id == old_id
    assert len(c.requests) == calls
    assert repeated_workflow.metadata_json["dispatches"] == slots
    for key in ("execution_owner", "native_pipeline_owner", "execution_scope_id"):
        assert repeated_workflow.metadata_json[key] == metadata[key]
    history = repeated_workflow.metadata_json["completion_failure_history"]
    assert len(history) == 1 and history[0]["status"] == "failed"
    assert (await protected_rows(c))["execution_budgets"] == fee_rows


async def test_new_native_lease_replays_paid_prefix_then_continues_original_bounded_workflow(
    outline_case,
):
    c = outline_case

    async def pause_after_first_paid(_row):
        assert len(c.requests) == 1
        c.session.add(BookProductionControlModel(project_id=c.project.id, desired_state="pause"))

    c.after_paid = pause_after_first_paid
    with pytest.raises(completion.OutlineContractCompletionError) as caught:
        await complete(c)
    first_run = c.runs[0].id
    workflow = await recover_completion_error(c, caught.value)
    identity = workflow.id
    metadata = deepcopy(workflow.metadata_json)
    original_paid = next(
        row for row in (await protected_rows(c))["llm_runs"] if row["id"] == first_run
    )
    c.after_paid = None
    control = await c.session.get(BookProductionControlModel, c.project.id)
    control.desired_state = "run"
    await c.session.commit()
    result = await complete(c)
    await c.session.commit()
    assert len(c.requests) == 4
    receipt = result["_meta"]["outline_contract_completion"]
    assert receipt["workflow_run_id"] == str(identity)
    assert receipt["parts"][0]["paid_proof"]["llm_run_id"] == str(first_run)
    saved = await c.session.get(WorkflowRunModel, identity, populate_existing=True)
    assert saved.status == "completed"
    assert len(saved.metadata_json["completion_failure_history"]) == 1
    assert saved.metadata_json["dispatches"]["0:0"] == metadata["dispatches"]["0:0"]
    for key in ("execution_owner", "native_pipeline_owner", "execution_scope_id"):
        assert saved.metadata_json[key] == metadata[key]
    after = await protected_rows(c)
    assert next(row for row in after["llm_runs"] if row["id"] == first_run) == original_paid
    assert c.budget.calls_used == 5 and c.budget.tokens_charged == 90
