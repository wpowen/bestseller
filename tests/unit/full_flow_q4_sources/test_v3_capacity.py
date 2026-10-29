"""Capacity consumers verify complete v3 sources before allocation and compilation."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from bestseller.services import chapter_writer_delivery as delivery
from bestseller.services import writer_source_capacity as capacity
from bestseller.services.finite_book_budget import bind_run
from bestseller.services.llm_capacity import ModelRequestCapacityError
from bestseller.services.prompt_source_policy import content_hash

from .conftest import build_block, payload_of, replace_payload, rereview

pytestmark = pytest.mark.unit


def prepare(case, settings, blocks):
    return capacity.prepare_writer_source_capacity(
        settings,
        project=case.project,
        chapter=case.chapter,
        delivery_blocks=blocks,
        model_tier="standard",
        max_tokens_override=16384,
    )


def compile_block(block, context):
    return capacity.compile_writer_source_prompt(
        [block],
        integrity_keys={block.key},
        base_budget_tokens=128,
        safety_margin=0.1,
        capacity=context,
    )


@pytest.mark.parametrize("language", ["zh-CN", "en"])
@pytest.mark.parametrize("count,number", [(4, 1), (12, 7), (12, 12)])
def test_v3_complete_source_prepares_and_compiles_without_trimming(
    full_case,
    writer_settings,
    language,
    count,
    number,
):
    case = full_case(count=count, number=number, language=language)
    block = build_block(case)
    before = deepcopy((case.project.metadata_json, case.approved, case.receipt, block))
    context = prepare(case, writer_settings, [block])
    assert context.output_tokens == 16384 and context.n_candidates == 1
    compiled = compile_block(block, context)
    assert block.text in compiled.user
    assert not compiled.report.dropped and not compiled.report.truncated
    receipt = compiled.report.writer_source_capacity
    assert receipt["source_only"] is True and receipt["financial_authorization"] is False
    assert receipt["gateway_revalidation_required"] is True
    assert receipt["capacity"]["output_reserve_tokens"] == 16384
    assert receipt["capacity"]["budget_passed"] is True
    assert compiled.report.required_blocks_kept == (block.key,)
    assert (case.project.metadata_json, case.approved, case.receipt, block) == before


def test_v3_over_capacity_refuses_complete_source_without_trimming(full_case, writer_settings):
    case = full_case()
    block = build_block(case)
    with bind_run("synthetic-q4", project_id=case.project.id, stage="writer", capacity_limit=1024):
        context = prepare(case, writer_settings, [block])
        with pytest.raises(ModelRequestCapacityError) as caught:
            compile_block(block, context)
    assert caught.value.receipt["output_reserve_tokens"] == 16384
    assert caught.value.receipt["budget_passed"] is False
    assert build_block(case) == block


@pytest.mark.parametrize(
    "fault",
    [
        "project_marker_missing",
        "project_input_changed",
        "duplicate_delivery",
        "missing_delivery",
        "legacy_source_label",
        "legacy_payload_schema",
        "legacy_relabel",
        "wrong_project",
        "wrong_chapter",
        "wrong_number",
        "wrong_artifact",
        "missing_raw_review",
        "missing_review_source",
        "forged_reader_receipt",
        "failed_raw_review",
        "role_swap",
        "field_source_redirect",
        "optional_delivery",
        "wrong_provenance",
    ],
)
def test_capacity_refuses_invalid_v3_before_runtime_profile(
    full_case,
    writer_settings,
    monkeypatch,
    fault,
):
    case = full_case()
    block = build_block(case)
    payload = payload_of(block)
    if fault == "project_marker_missing":
        case.project.metadata_json.pop("reader_first_planning")
    elif fault == "project_input_changed":
        case.project.metadata_json["reader_first_planning"]["user_input_sha256"] = "0" * 64
    elif fault == "legacy_source_label":
        block = block.model_copy(update={"source": "chapter_writer_delivery.v2"})
    elif fault == "legacy_payload_schema":
        payload["schema"] = "approved-current-chapter-delivery.v2"
    elif fault == "legacy_relabel":
        block = block.model_copy(update={"source": "chapter_writer_delivery.v2"})
        payload["schema"] = "approved-current-chapter-delivery.v2"
    elif fault.startswith("wrong_") and fault != "wrong_provenance":
        key = {
            "project": "project_id",
            "chapter": "chapter_id",
            "number": "chapter_number",
            "artifact": "artifact_id",
        }[fault.removeprefix("wrong_")]
        payload[key] = 99 if key == "chapter_number" else "00000000-0000-0000-0000-000000000099"
    elif fault == "missing_raw_review":
        payload.pop("raw_review")
    elif fault == "missing_review_source":
        payload.pop("review_source")
    elif fault == "forged_reader_receipt":
        payload["reader_receipt"]["mean_score"] = 1.0
        payload["reader_receipt_sha256"] = content_hash(payload["reader_receipt"])
    elif fault == "failed_raw_review":
        first = next(iter(payload["raw_review"]["dimensions"].values()))
        first["score"] = 0.1  # Keep the previous passing parsed receipt to test recomputation.
    elif fault == "role_swap":
        payload["narrative_task"].append(payload["continuity_reference"].pop(0))
    elif fault == "field_source_redirect":
        payload["field_sources"]["/goal"]["review_source_pointer"] = "/outline/chapters/0/goal"
    elif fault == "optional_delivery":
        block = block.model_copy(update={"required": False})
    elif fault == "wrong_provenance":
        block = block.model_copy(
            update={
                "provenance": block.provenance.model_copy(update={"source_hash": "0" * 64}),
            }
        )
    block = replace_payload(block, payload)
    blocks = (
        [] if fault == "missing_delivery" else [block] * (2 if fault == "duplicate_delivery" else 1)
    )

    def profile_must_not_run(*args, **kwargs):
        pytest.fail("invalid delivery reached runtime profile before source verification")

    from bestseller.services import llm

    monkeypatch.setattr(llm, "apply_runtime_llm_profile", profile_must_not_run)
    with pytest.raises(ValueError):
        prepare(case, writer_settings, blocks)


@pytest.mark.parametrize(
    "fault",
    [
        "short_prefix",
        "extra_chapter",
        "duplicate_number",
        "other_chapter_role",
        "other_chapter_scene",
        "bool_number",
    ],
)
def test_capacity_recomputes_whole_book_census_even_with_rehashed_review(
    full_case,
    writer_settings,
    fault,
):
    case = full_case()
    chapters = case.artifact.content["chapters"]
    if fault == "short_prefix":
        chapters.pop()  # Selected chapter 7 remains intact.
    elif fault == "extra_chapter":
        row = deepcopy(chapters[-1])
        row["chapter_number"] = 13
        chapters.append(row)
    elif fault == "duplicate_number":
        chapters[-1]["chapter_number"] = 11
    elif fault == "other_chapter_role":
        chapters[-1]["_meta"]["reader_first_field_roles"].pop("/goal")
    elif fault == "other_chapter_scene":
        chapters[-1]["scenes"][0]["scene_number"] = 2
    elif fault == "bool_number":
        chapters[0]["chapter_number"] = True
    rereview(case)
    block = build_block(case)  # Existing constructor verifies only the selected chapter.
    with pytest.raises(ValueError):
        prepare(case, writer_settings, [block])


def test_compilation_rejects_delivery_changed_after_preparation(full_case, writer_settings):
    case = full_case()
    block = build_block(case)
    context = prepare(case, writer_settings, [block])
    changed = payload_of(block)
    changed["raw_review"]["dimensions"][next(iter(changed["raw_review"]["dimensions"]))][
        "score"
    ] = 0.2
    with pytest.raises(ValueError):
        compile_block(replace_payload(block, changed), context)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_unmarked_legacy_capacity_path_remains_available(full_case, writer_settings, version):
    case = full_case(count=4, number=1)
    case.project.metadata_json.pop("reader_first_planning")
    approved = deepcopy(case.approved)
    approved.pop("_meta")
    constructor = (
        delivery._source_protocol_delivery_block_v1 if version == "v1" else delivery.delivery_block
    )
    block = constructor(
        project_id=case.project.id,
        chapter_id=case.chapter.id,
        chapter_number=1,
        artifact_id=case.artifact.id,
        approved=approved,
        receipt={"chapter_numbers": [1]},
    )
    compiled = compile_block(block, prepare(case, writer_settings, [block]))
    assert block.text in compiled.user


def test_unscoped_legacy_helper_still_refuses_v3(full_case):
    with pytest.raises(ValueError, match="writer_source_capacity_verified_delivery_required"):
        capacity._delivery_binding(build_block(full_case()))


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_marked_project_cannot_use_old_approval(full_case, writer_settings, version):
    case = full_case(count=4, number=1)
    approved = deepcopy(case.approved)
    approved.pop("_meta")
    constructor = (
        delivery._source_protocol_delivery_block_v1 if version == "v1" else delivery.delivery_block
    )
    block = constructor(
        project_id=case.project.id,
        chapter_id=case.chapter.id,
        chapter_number=1,
        artifact_id=case.artifact.id,
        approved=approved,
        receipt={"chapter_numbers": [1]},
    )
    with pytest.raises(ValueError):
        prepare(case, writer_settings, [block])


@pytest.mark.parametrize("review_format", ["object", "retained_utf8"])
def test_capacity_receipt_binds_exact_source_bodies(full_case, writer_settings, review_format):
    case = full_case()
    if review_format == "retained_utf8":
        envelope = case.receipt["reader_first_source"]
        envelope["review"] = json.dumps(envelope["review"], ensure_ascii=False, indent=2)
    block = build_block(case)
    payload = payload_of(block)
    compiled = compile_block(block, prepare(case, writer_settings, [block]))
    receipt = compiled.report.writer_source_capacity
    evidence = receipt["reader_first_delivery_evidence"][block.key]
    assert evidence["block_text_sha256"] == sha256(block.text.encode()).hexdigest()
    assert evidence["block_text_sha256"] == receipt["source_block_sha256"][block.key]
    assert evidence["block_model_sha256"] == sha256(block.model_dump_json().encode()).hexdigest()
    assert evidence["approved_chapter_sha256"] == content_hash(case.approved)
    assert evidence["review_source_sha256"] == content_hash(payload["review_source"])
    review = payload["raw_review"]
    body = (
        review
        if isinstance(review, str)
        else json.dumps(
            review,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    assert evidence["raw_review_body_sha256"] == sha256(body.encode()).hexdigest()
    assert evidence["raw_review_body_encoding"] == (
        "retained_utf8" if review_format == "retained_utf8" else "canonical_json"
    )
    assert len(evidence["census"]) == case.project.target_chapters
    assert evidence["census_sha256"] == content_hash(evidence["census"])
    assert evidence["native_positive"] == "unknown"
    with bind_run(
        "synthetic-bodyhash-limit", project_id=case.project.id, stage="writer", capacity_limit=1024
    ):
        with pytest.raises(ModelRequestCapacityError) as caught:
            compile_block(block, prepare(case, writer_settings, [block]))
    assert caught.value.receipt["reader_first_delivery_evidence"][block.key] == evidence


@pytest.mark.parametrize("fault", ["review", "prefix", "scene", "roles", "legacy", "scope_flag"])
def test_matching_capacity_fingerprint_cannot_replace_source_validation(
    full_case,
    writer_settings,
    fault,
):
    case = full_case()
    block = build_block(case)
    context = prepare(case, writer_settings, [block])
    if fault == "scope_flag":
        with pytest.raises(ValueError):
            compile_block(block, replace(context, reader_first_scope=True))
        return
    if fault == "review":
        payload = payload_of(block)
        next(iter(payload["raw_review"]["dimensions"].values()))["score"] = 0.1
        bad = replace_payload(block, payload)
    elif fault == "legacy":
        approved = deepcopy(case.approved)
        approved.pop("_meta")
        bad = delivery.delivery_block(
            project_id=case.project.id,
            chapter_id=case.chapter.id,
            chapter_number=case.chapter.chapter_number,
            artifact_id=case.artifact.id,
            approved=approved,
            receipt={"chapter_numbers": [case.chapter.chapter_number]},
        )
    else:
        chapters = case.artifact.content["chapters"]
        if fault == "prefix":
            chapters.pop()
        elif fault == "scene":
            chapters[-1]["scenes"][0]["scene_number"] = 2
        else:
            chapters[-1]["_meta"]["reader_first_field_roles"].pop("/goal")
        rereview(case)
        bad = build_block(case)
    forged_fingerprint = (bad.key, sha256(bad.model_dump_json().encode()).hexdigest())
    context = replace(context, delivery_bindings=(forged_fingerprint,))
    with pytest.raises(ValueError):
        compile_block(bad, context)
