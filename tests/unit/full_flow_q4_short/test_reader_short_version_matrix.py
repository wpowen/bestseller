"""Pure version RED: native request construction only; main runs this module."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import fanqie_beat_recovery as beat
from bestseller.services import fanqie_outline_contract_completion as completion
from bestseller.services import source_bound_planning_content as protocol
from bestseller.services.llm import LLMCompletionRequest

pytestmark = pytest.mark.unit

# Every current short producer base and its distinct prospective marked wire.
# No slicing, schema widening, or relabeling of retained paid request bytes.
VERSIONS = (
    ("v1-source-exact-beats", "fq-beats-v1.reader-first-v1"),
    ("fanqie-plan-fields-v1", "fq-fields-v1.reader-first-v1"),
    ("fanqie-plan-fields-v2", "fq-fields-v2.reader-first-v1"),
)


def mapping():
    function = getattr(protocol, "reader_first_prompt_version", None)
    assert callable(function), "READER_FIRST_PROMPT_VERSION_HELPER_MISSING"
    return function


def project(marked, count):
    binding = protocol.new_reader_first_binding("An explicit synthetic relationship and its consequences.")
    return SimpleNamespace(id=uuid4(), target_chapters=count, language="en",
                           metadata_json={"reader_first_planning": binding} if marked else {})


def native_request(p, base, workflow_id):
    if base == beat.PROMPT_VERSION:
        return beat.build_current_request(p, {"effective_pov": {"beat_sheet_pov": "first_person"}},
                                          workflow_run_id=workflow_id)
    count = p.target_chapters
    source = {
        "selected_source": {"project_inputs": {"target_chapters": count}},
        "original_beat": {"raw": {"beats": [{"segment_number": n} for n in range(1, count + 1)]}},
        "base_outline": {"chapters": [{"chapter_number": n} for n in range(1, count + 1)]},
    }
    if count != 4:
        source["segment_contract"] = completion._segment_binding(count, project=p)
    return completion._request(p, source, {}, [], {"/chapters/0/title": {"type": "string"}},
                               workflow_id, 0, 0, None)


@pytest.mark.parametrize("base,expected", VERSIONS)
def test_bounded_mapping_preserves_distinct_versions_and_accepts_its_new_wire_name(base, expected):
    transform = mapping()
    actual = transform(base)
    assert actual == expected and len(actual) <= 32
    assert transform(actual) == actual
    assert transform(base) == actual
    request = LLMCompletionRequest(logical_role="planner", system_prompt="Synthetic system",
                                   user_prompt="{}", fallback_response="{}", prompt_version=actual)
    assert LLMCompletionRequest.model_validate(request.model_dump(mode="json")) == request


@pytest.mark.parametrize("base,expected", VERSIONS)
def test_native_producer_and_verifier_build_the_same_bounded_marked_request(base, expected):
    p = project(True, 6 if base == completion.PROMPT_VERSION_V2 else 4)
    metadata = deepcopy(p.metadata_json)
    workflow_id = uuid4()
    produced = native_request(p, base, workflow_id)
    # Native Beat/completion verifiers reconstruct through these same builders.
    reconstructed = native_request(p, base, workflow_id)
    reloaded = LLMCompletionRequest.model_validate(produced.model_dump(mode="json"))
    assert reloaded.prompt_version == expected
    assert completion._same_request(reloaded, reconstructed)
    assert produced.metadata == reconstructed.metadata
    assert produced.metadata["reader_first_planning"] == metadata["reader_first_planning"]
    assert p.metadata_json == metadata


@pytest.mark.parametrize("base,_expected", VERSIONS)
def test_unmarked_producer_and_wrapper_preserve_legacy_request_exactly(base, _expected):
    p = project(False, 6 if base == completion.PROMPT_VERSION_V2 else 4)
    request = native_request(p, base, uuid4())
    before = request.model_dump(mode="json")
    stage = "fanqie_beat" if base == beat.PROMPT_VERSION else "fanqie_contract_completion"
    assert protocol.planning_reader_first_request(request, p, stage=stage) is request
    assert request.model_dump(mode="json") == before
    assert request.prompt_version == base
    assert request.system_prompt == (beat.SYSTEM if stage == "fanqie_beat" else completion.SYSTEM)
    assert "reader_first_planning" not in request.metadata


def test_matrix_covers_all_current_native_short_bases_without_wire_collisions():
    assert {base for base, _ in VERSIONS} == {
        beat.PROMPT_VERSION, completion.PROMPT_VERSION, completion.PROMPT_VERSION_V2,
    }
    outputs = [mapping()(base) for base, _ in VERSIONS]
    assert len(set(outputs)) == len(VERSIONS)
    assert all(len(value) <= 32 for value in outputs)
