"""Shared v3 verifier contracts; main owns test execution and runtime integration."""

from asyncio import current_task
from copy import deepcopy
import json

import pytest

from bestseller.services import chapter_writer_delivery as delivery
from bestseller.services import chapter_writer_sources as sources
from bestseller.services import editable_plan_source
from bestseller.services.prompt_source_policy import content_hash

from .conftest import build_block, leaves, payload_of, replace_payload, rereview

pytestmark = pytest.mark.unit


def verify(case, block):
    # Resolve at test execution so the pre-implementation API absence is a
    # normal RED assertion, not a collection/import failure.
    verifier = getattr(sources, "verify_reader_first_delivery", None)
    assert callable(verifier), "Q4 shared source consumer verify_reader_first_delivery is missing"
    return verifier(project=case.project, chapter=case.chapter, block=block)


@pytest.mark.parametrize("language", ["zh-CN", "en"])
@pytest.mark.parametrize("count,number", [(4, 4), (12, 1), (12, 12)])
def test_shared_consumer_returns_exact_verified_payload_copy(full_case, language, count, number):
    case = full_case(count=count, number=number, language=language)
    block = build_block(case)
    before = deepcopy((block, case.receipt, case.project.metadata_json))
    result = verify(case, block)
    assert result == payload_of(block)
    assert len(result["review_source"]["outline"]["chapters"]) == count
    assert result["source_chapter_pointer"] == f"/outline/chapters/{number - 1}"
    assert "/precise~1fact~0" in result["continuity_reference"]
    assert "/scenes/0/action_sequence/0" in result["narrative_task"]
    result["approved_source"]["goal"] = "mutated returned data"
    assert (block, case.receipt, case.project.metadata_json) == before


@pytest.mark.parametrize(
    "fault",
    [
        "binding_missing",
        "binding_input",
        "binding_config",
        "source_binding_missing",
        "source_binding_extra",
        "source_pointer",
        "raw_review_missing",
        "raw_review_bad_quote",
        "raw_review_low_score",
        "raw_review_fallback",
        "raw_review_unknown",
        "raw_review_dimension",
        "receipt_forged_and_rehashed",
        "review_source_changed",
        "field_role_missing",
        "field_role_unknown",
        "field_role_extra",
        "role_channel_swap",
        "field_source_wrong_chapter",
        "provenance_field_missing",
        "source_schema_mismatch",
        "block_key",
        "block_channel",
    ],
)
def test_shared_consumer_rejects_missing_or_forged_source_evidence(full_case, fault):
    case = full_case()
    block = build_block(case)
    payload = payload_of(block)
    if fault == "binding_missing":
        payload.pop("reader_first_planning")
    elif fault in {"binding_input", "binding_config"}:
        key = "user_input_sha256" if fault == "binding_input" else "config_sha256"
        payload["reader_first_planning"][key] = "0" * 64
    elif fault == "source_binding_missing":
        payload.pop("source_binding")
    elif fault == "source_binding_extra":
        payload["source_binding"]["untrusted_approval"] = True
    elif fault == "source_pointer":
        payload["source_chapter_pointer"] = "/outline/chapters/0"
    elif fault == "raw_review_missing":
        payload.pop("raw_review")
    elif fault.startswith("raw_review_"):
        raw = payload["raw_review"]
        dimension = next(iter(raw["dimensions"]))
        if fault == "raw_review_bad_quote":
            raw["dimensions"][dimension]["evidence"][0]["quote"] = "Not in the reviewed source."
        elif fault == "raw_review_low_score":
            raw["dimensions"][dimension]["score"] = 0.1
        elif fault == "raw_review_fallback":
            raw["fallback"] = True
        elif fault == "raw_review_unknown":
            raw["status"] = "unknown"
        elif fault == "raw_review_dimension":
            raw["dimensions"].pop(dimension)
    elif fault == "receipt_forged_and_rehashed":
        payload["reader_receipt"]["mean_score"] = 1.0
        payload["reader_receipt_sha256"] = content_hash(payload["reader_receipt"])
    elif fault == "review_source_changed":
        payload["review_source"]["whole_book_payoff_index"] = "Unreviewed different ending."
        payload["review_source_sha256"] = content_hash(payload["review_source"])
    elif fault.startswith("field_role_"):
        roles = payload["approved_source"]["_meta"]["reader_first_field_roles"]
        if fault == "field_role_missing":
            roles.pop("/goal")
        elif fault == "field_role_unknown":
            roles["/goal"] = "ignored"
        else:
            roles["/not_authored"] = "narrative_task"
    elif fault == "role_channel_swap":
        payload["narrative_task"].append(payload["continuity_reference"].pop(0))
    elif fault == "field_source_wrong_chapter":
        payload["field_sources"]["/goal"]["review_source_pointer"] = "/outline/chapters/0/goal"
    elif fault == "provenance_field_missing":
        block = block.model_copy(
            update={
                "provenance": block.provenance.model_copy(update={"field_paths": ("/goal",)}),
            }
        )
    elif fault == "source_schema_mismatch":
        payload["schema"] = "approved-current-chapter-delivery.v2"
    elif fault == "block_key":
        block = block.model_copy(update={"key": "other.delivery"})
    elif fault == "block_channel":
        block = block.model_copy(update={"channel": "system"})
    with pytest.raises(ValueError):
        verify(case, replace_payload(block, payload))


@pytest.mark.parametrize(
    "fault",
    [
        "prefix",
        "extra",
        "duplicate",
        "gap",
        "other_roles",
        "other_binding",
        "whole_source_binding",
        "other_schema",
        "other_scenes",
        "bool_number",
    ],
)
def test_shared_consumer_rechecks_census_and_all_chapter_roles(full_case, fault):
    case = full_case()
    chapters = case.artifact.content["chapters"]
    if fault == "prefix":
        chapters.pop()
    elif fault == "extra":
        chapters.append(deepcopy(chapters[-1]))
        chapters[-1]["chapter_number"] = 13
    elif fault == "duplicate":
        chapters[-1]["chapter_number"] = 11
    elif fault == "gap":
        chapters[-1]["chapter_number"] = 13
    elif fault == "other_roles":
        chapters[-1]["_meta"]["reader_first_field_roles"].pop("/goal")
    elif fault == "other_binding":
        chapters[-1]["_meta"]["reader_first_planning"]["user_input_sha256"] = "0" * 64
    elif fault == "whole_source_binding":
        source = case.receipt["reader_first_source"]["source"]
        source["reader_first_planning"]["user_input_sha256"] = "0" * 64
    elif fault == "other_schema":
        chapters[-1]["_meta"]["writer_delivery_schema"] = "approved-current-chapter-delivery.v2"
    elif fault == "other_scenes":
        chapters[-1]["scenes"] = []
    else:
        chapters[0]["chapter_number"] = True
    rereview(case)
    block = build_block(case)
    with pytest.raises(ValueError):
        verify(case, block)


@pytest.mark.parametrize("target", [None, 0, True, "12", 11, 13])
def test_shared_consumer_requires_exact_project_census(full_case, target):
    case = full_case()
    block = build_block(case)
    case.project.target_chapters = target
    with pytest.raises(ValueError):
        verify(case, block)


@pytest.mark.parametrize("fault", ["missing", "schema", "binding", "scope", "review", "artifact"])
async def test_loader_rejects_incomplete_original_source_envelope(full_case, monkeypatch, fault):
    case = full_case()
    if fault == "missing":
        case.receipt.pop("reader_first_source")
    elif fault == "schema":
        case.receipt["reader_first_source"]["schema"] = "legacy-unreviewed-source.v1"
    elif fault == "binding":
        case.receipt["reader_first_source"]["binding"]["user_input_sha256"] = "0" * 64
    elif fault == "scope":
        case.receipt["reader_first_source"]["source_binding"]["artifact_id"] = "0" * 64
    elif fault == "review":
        case.receipt["reader_first_source"].pop("review")
    else:
        case.artifact.content = deepcopy(case.artifact.content)
        case.artifact.content["unreviewed_appendix"] = "Not in the reviewed source."

    async def selected(*args, **kwargs):
        return case.artifact, case.approved, case.receipt

    async def retained_receipt(*args, **kwargs):
        # This NoneSession unit checks transport; native custody has its own tests.
        return deepcopy(case.receipt)

    monkeypatch.setattr(editable_plan_source, "load_selected_plan_source", selected)
    monkeypatch.setattr(delivery, "load_reader_first_source_receipt", retained_receipt)
    with pytest.raises(ValueError):
        await delivery.load_delivery_blocks(
            None,
            None,
            project=case.project,
            chapter=case.chapter,
            scenes=[],
        )


@pytest.mark.parametrize("fault", ["duplicate", "gap", "bool", "role_missing"])
def test_shared_consumer_checks_later_scene_in_other_chapter(full_case, fault):
    case = full_case()
    last = case.artifact.content["chapters"][-1]
    last["scenes"].append(deepcopy(last["scenes"][0]))
    last["scenes"][-1]["scene_number"] = 2
    roles = last["_meta"]["reader_first_field_roles"]
    roles.update(dict.fromkeys(leaves(last), "continuity_reference"))
    rereview(case)
    assert (
        verify(case, build_block(case))["review_source"]
        == case.receipt["reader_first_source"]["source"]
    )
    if fault == "role_missing":
        roles.pop("/scenes/1/exit_state/choice")
    else:
        last["scenes"][-1]["scene_number"] = {"duplicate": 1, "gap": 3, "bool": True}[fault]
    rereview(case)
    with pytest.raises(ValueError):
        verify(case, build_block(case))


@pytest.mark.parametrize("fault", ["duplicate_outer", "duplicate_review", "nan", "overflow"])
def test_shared_consumer_rejects_noncanonical_json_evidence(full_case, fault):
    case = full_case()
    block = build_block(case)
    if fault == "duplicate_outer":
        bad = block.model_copy(
            update={
                "text": block.text.replace('{"schema":', '{"schema":"untrusted","schema":', 1),
            }
        )
    else:
        payload = payload_of(block)
        raw = json.dumps(payload["raw_review"], ensure_ascii=False, separators=(",", ":"))
        if fault == "duplicate_review":
            raw = '{"schema":"untrusted",' + raw[1:]
        else:
            raw = raw[:-1] + ',"untrusted":' + ("NaN" if fault == "nan" else "1e999") + "}"
        payload["raw_review"] = raw
        bad = replace_payload(block, payload)
    with pytest.raises(ValueError):
        verify(case, bad)


@pytest.mark.parametrize("legacy_schema", ["v1", "v2"])
async def test_historical_delivery_context_cannot_bypass_v3_consumer(full_case, legacy_schema):
    case = full_case()
    block = build_block(case)
    payload = payload_of(block)
    payload["reader_receipt"]["mean_score"] = 1.0
    payload["reader_receipt_sha256"] = content_hash(payload["reader_receipt"])
    bad = replace_payload(block, payload)
    token = delivery._REFERENCE_DELIVERY_SCHEMA.set(
        (
            current_task(),
            "approved-current-chapter-delivery." + legacy_schema,
            case.project.id,
            case.chapter.id,
        )
    )
    try:
        with pytest.raises(ValueError):
            verify(case, bad)
    finally:
        delivery._REFERENCE_DELIVERY_SCHEMA.reset(token)
