"""Author methodology is optional, exact, and independent from a cost preference."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.ideology import (
    ideology_kernel_from_dict,
    render_ideology_compact_block,
    render_ideology_kernel_prompt_block,
)
from bestseller.services.creation_intent_contract import build_creation_intent_contract
from bestseller.services.genre_intent_contract import contract_from_selection
from bestseller.services.ideology_kernel import (
    build_ideology_user_prompt,
    derive_ideology_kernel,
    fallback_ideology_kernel,
    parse_ideology_kernel,
)
from bestseller.services.ideology_methodology import (
    build_ideology_methodology_supersession,
    project_ideology_kernel,
    resolve_ideology_methodology_source,
)
from bestseller.services.story_enhancers import capture_story_enhancer_input


def legacy():
    return json.loads(
        (Path(__file__).parents[1] / "fixtures/legacy_ideology_kernel.json").read_text()
    )


def metadata(fields=None, *, style=None, layers=None):
    selection, sources = capture_story_enhancer_input({"cost_style": style} if style else {})
    genre = contract_from_selection({"channel": "female", "genre": "romance"}, enhancers=selection)
    hook = {"_story_enhancer_input_sources": sources}
    field_sources = {"story_enhancers": "explicit"} if style else {}
    if fields is not None or layers is not None:
        hook["ideology_contract"] = {
            "version": "ideology-author-contract.v1",
            "fields": fields or {},
            "required_layers": layers or [],
        }
        field_sources["hook_spec"] = "explicit"
    creation = build_creation_intent_contract(
        genre, story_enhancers=selection, hook_spec=hook, field_sources=field_sources
    )
    return {"creation_intent_contract": creation.model_dump(mode="json")}


@pytest.mark.parametrize("style", [None, "standard", "minimal", "external"])
def test_legacy_kernel_remains_readable_but_cost_cannot_authorize_methodology(style):
    raw = legacy()
    meta = metadata(style=style)
    before = deepcopy((raw, meta))
    typed = ideology_kernel_from_dict(raw)
    assert typed.cosmic_premise and typed.primary_motif and typed.hidden_endgame_motif
    receipt = resolve_ideology_methodology_source(raw, meta)
    assert receipt["authority"] == "unknown" and receipt["effective_fields"] == {}
    for render in (render_ideology_kernel_prompt_block, render_ideology_compact_block):
        block = render(raw, metadata=meta)
        assert raw["cosmic_premise"] not in block
        assert raw["forbidden_resolutions"][0] not in block
        assert "四层" not in block and "隐藏终局" not in block
        assert bool(block) == bool(style)
    assert (raw, meta) == before


@pytest.mark.parametrize("premise", ["退休伴侣协商照护分工", "木工学徒筹办社区修理日"])
def test_new_unknown_does_not_manufacture_a_story_structure(premise):
    fallback = fallback_ideology_kernel(premise=premise, genre="现实关系")
    parsed = parse_ideology_kernel(json.dumps(legacy(), ensure_ascii=False), premise=premise)
    assert parsed.primary_motif is None and parsed.belief_arc is None
    assert not parsed.cosmic_premise and not parsed.hidden_endgame_motif
    assert not parsed.cost_system and not parsed.forbidden_resolutions
    assert not parsed.covered_layers()
    assert ideology_kernel_from_dict(fallback).model_dump() == parsed.model_dump()
    prompt = build_ideology_user_prompt(premise=premise, fallback_payload=legacy())
    assert legacy()["cosmic_premise"] not in prompt
    assert "必须覆盖四层" not in prompt


def test_exact_author_realistic_clauses_preserved_without_unselected_cosmology():
    fields = {
        "thesis_statement": "体谅需要把分工说清楚",
        "core_question": "如何在照护压力下重新协商日常分工",
        "forbidden_resolutions": ["不能用突然继承财产解决照护争执"],
    }
    meta = metadata(fields)
    actual = fallback_ideology_kernel(premise="旧书摊", metadata=meta)
    parsed = parse_ideology_kernel('{"cosmic_premise":"AI invented cosmology"}', metadata=meta)
    assert parsed.thesis_statement == fields["thesis_statement"]
    assert parsed.forbidden_resolutions == fields["forbidden_resolutions"]
    assert parsed.primary_motif is None and not parsed.cosmic_premise
    assert actual["forbidden_resolutions"] == fields["forbidden_resolutions"]
    block = render_ideology_kernel_prompt_block(legacy(), metadata=meta)
    assert all(
        text in block
        for text in (
            fields["thesis_statement"],
            fields["core_question"],
            *fields["forbidden_resolutions"],
        )
    )
    assert "AI invented" not in block and legacy()["cosmic_premise"] not in block


def test_author_layer_request_is_not_permission_to_invent_literal_clauses():
    meta = metadata(layers=["subject_choice", "cosmic_order"])
    projected = project_ideology_kernel(legacy(), meta)
    assert "cosmic_premise" not in projected
    parsed = parse_ideology_kernel("{}", metadata=meta)
    assert parsed.primary_motif is None and not parsed.cosmic_premise
    block = render_ideology_compact_block(legacy(), metadata=meta)
    assert "subject_choice" in block and "cosmic_order" in block
    assert "ethical_reversal" not in block


@pytest.mark.parametrize("mutation", ["version", "unknown_field", "bad_layer", "bad_field_type"])
def test_malformed_author_contract_fails_instead_of_becoming_approved(mutation):
    meta = metadata({"thesis_statement": "互相信任"})
    contract = meta["creation_intent_contract"]["hook_spec"]["ideology_contract"]
    if mutation == "version":
        contract["version"] = "self-approved"
    elif mutation == "unknown_field":
        contract["fields"]["approved"] = True
    elif mutation == "bad_layer":
        contract["required_layers"] = ["all_books_must_be_tragic"]
    else:
        contract["fields"]["thesis_statement"] = ["wrong type"]
    with pytest.raises(ValueError):
        resolve_ideology_methodology_source(legacy(), meta)


def test_self_labels_and_default_hook_do_not_grant_authority():
    raw = legacy()
    raw["author_approved"] = True
    meta = metadata({"thesis_statement": "not confirmed"})
    meta["creation_intent_contract"]["field_sources"]["hook_spec"] = "default"
    meta["ideology_contract"] = {"source": "author", "approved": True}
    assert resolve_ideology_methodology_source(raw, meta)["effective_fields"] == {}


def test_current_cache_source_switch_rejected_for_raw_and_typed_inputs():
    original = legacy()
    meta = metadata({"thesis_statement": "互相信任"})
    meta["story_design_kernel"] = {"ideology_kernel": deepcopy(original)}
    assert (
        resolve_ideology_methodology_source(original, meta)["cache_binding"]
        == "current_cache_match"
    )
    assert (
        resolve_ideology_methodology_source(ideology_kernel_from_dict(original), meta)[
            "cache_binding"
        ]
        == "current_cache_match"
    )
    meta["story_design_kernel"]["ideology_kernel"]["thesis_statement"] = "new kernel"
    for old in (original, ideology_kernel_from_dict(original)):
        with pytest.raises(ValueError, match="current exact cache"):
            resolve_ideology_methodology_source(old, meta)


@pytest.mark.asyncio
async def test_no_model_needed_to_materialize_explicit_or_unknown_clauses():
    for meta in ({}, metadata({"thesis_statement": "互相信任"})):
        # Neither session nor settings is usable: touching a provider/DB would fail.
        result = await derive_ideology_kernel(None, None, premise="照护分工", metadata=meta)
        assert result.thesis_statement == ("互相信任" if meta else "")


def dry_run_records():
    raw = legacy()
    meta = {
        "story_design_kernel": {"ideology_kernel": raw},
        "canon": ["已完成照护安排"],
        "historical_scores": [0.61],
        "overrides": [{"id": "keep"}],
    }
    project = SimpleNamespace(id=uuid4(), metadata_json=meta)
    artifact = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        artifact_type="story_design_kernel",
        status="approved",
        content={"ideology_kernel": raw},
    )
    receipt = resolve_ideology_methodology_source(raw, meta)
    args = {
        "expected_source_sha256": receipt["source_kernel_sha256"],
        "expected_creation_sha256": receipt["source_creation_sha256"],
    }
    return project, artifact, args


def test_exact_binding_repeat_receipt_is_readonly_and_neutral():
    project, artifact, args = dry_run_records()
    before = deepcopy((project.__dict__, artifact.__dict__))
    one = build_ideology_methodology_supersession(project, artifact, **args)
    two = build_ideology_methodology_supersession(project, artifact, **args)
    assert one == two
    assert one["effective_projection"] == {"version": 2, "cost_style": ""}
    assert one["writes"] == [] and one["authority"] == "unknown"
    assert one["effective_prompt_full"] == one["effective_prompt_compact"] == ""
    assert (project.__dict__, artifact.__dict__) == before


@pytest.mark.parametrize("mutation", ["project", "source_hash", "creation_hash", "cache", "status"])
def test_migration_cannot_bind_wrong_or_old_source(mutation):
    project, artifact, args = dry_run_records()
    if mutation == "project":
        artifact.project_id = uuid4()
    elif mutation == "source_hash":
        args["expected_source_sha256"] = "0" * 64
    elif mutation == "creation_hash":
        args["expected_creation_sha256"] = "0" * 64
    elif mutation == "cache":
        project.metadata_json["story_design_kernel"]["ideology_kernel"] = {
            "thesis_statement": "new"
        }
    else:
        artifact.status = "candidate"
    with pytest.raises(ValueError):
        build_ideology_methodology_supersession(project, artifact, **args)


def test_readonly_operator_emits_bound_receipt(tmp_path):
    project, artifact, args = dry_run_records()
    capture = tmp_path / "capture.json"
    capture.write_text(
        json.dumps(
            {
                "project": {"id": str(project.id), "metadata_json": project.metadata_json},
                "artifact": {
                    **artifact.__dict__,
                    "id": str(artifact.id),
                    "project_id": str(artifact.project_id),
                },
            },
            ensure_ascii=False,
        )
    )
    before = hashlib.sha256(capture.read_bytes()).hexdigest()
    result = subprocess.run(
        [
            sys.executable,
            "scripts/ideology_methodology_source_dry_run.py",
            str(capture),
            "--expected-kernel-sha256",
            args["expected_source_sha256"],
            "--expected-creation-sha256",
            args["expected_creation_sha256"],
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    receipt = json.loads(result.stdout)
    assert receipt["effective_projection"] == {"version": 2, "cost_style": ""}
    assert receipt["writes"] == []
    assert hashlib.sha256(capture.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("genre", ["现实关系", "历史日常"])
@pytest.mark.parametrize("authored", [False, True])
def test_real_foundation_volume_and_compact_prompts_share_the_same_projection(genre, authored):
    from bestseller.infra.db.models import ProjectModel
    from bestseller.services import planner

    fields = {
        "thesis_statement": "照护分工需要协商",
        "forbidden_resolutions": ["不能以突然继承遗产解决分歧"],
    }
    meta = metadata(fields if authored else None)
    project = ProjectModel(
        slug="local-methodology-projection",
        title="旧书摊",
        genre=genre,
        sub_genre="伴侣日常",
        target_word_count=30000,
        target_chapters=12,
        language="zh-CN",
        audience="general",
        metadata_json=meta,
    )
    project.id = uuid4()
    premise = "退休伴侣因照护分工产生分歧,尝试协商重建信任。"
    book = planner._fallback_book_spec(project, premise)
    world = planner._fallback_world_spec(project, premise, book)
    cast = planner._fallback_cast_spec(project, premise, book, world)
    sdk = planner._fallback_story_design_kernel(
        project, premise, book, world, cast, category_key=None
    )
    raw = legacy()
    raw["cosmic_premise"] = "UNSELECTED_COSMIC_SENTINEL"
    raw["hidden_endgame_motif"]["book_thesis"] = "UNSELECTED_HIDDEN_SENTINEL"
    sdk["ideology_kernel"] = raw
    project.metadata_json = {**meta, "story_design_kernel": sdk}
    before = deepcopy(project.metadata_json)
    full = render_ideology_kernel_prompt_block(raw, metadata=project.metadata_json)
    _, foundation = planner._story_design_kernel_prompts(
        project, premise, book, world, cast, sdk, ideology_block=full
    )
    _, volume = planner._volume_plan_prompts(project, book, world, cast)
    compact = planner._compact_outline_context_block(project, max_chars=100000)
    for prompt in (foundation, volume, compact):
        assert "UNSELECTED_COSMIC_SENTINEL" not in prompt
        assert "UNSELECTED_HIDDEN_SENTINEL" not in prompt
        assert "worldview_kernel / plot_tree / beat_schedule 都必须服务它" not in prompt
        if authored:
            assert fields["thesis_statement"] in prompt
            assert fields["forbidden_resolutions"][0] in prompt
    assert project.metadata_json == before


def test_author_clauses_survive_absent_sdk_and_invalid_contract_never_silently_disappears():
    from bestseller.services import planner

    meta = metadata({"thesis_statement": "协商分工"})
    project = SimpleNamespace(metadata_json=meta)
    assert "协商分工" in planner._story_design_kernel_prompt_block(project)
    assert "协商分工" in planner._compact_outline_context_block(project)
    meta["creation_intent_contract"]["hook_spec"]["ideology_contract"]["version"] = "invalid"
    for render in (
        planner._story_design_kernel_prompt_block,
        planner._compact_outline_context_block,
    ):
        with pytest.raises(ValueError, match="version"):
            render(project)
