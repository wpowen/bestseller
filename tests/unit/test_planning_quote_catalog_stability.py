"""Stable quote ownership before bounded selection; no DB or model calls."""

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_source_bound_semantic_review_authority import setup_review

from bestseller.services import planner
from bestseller.services import source_bound_planning_content as content_policy
from bestseller.services.llm import _effective_input_hash, _effective_input_payload
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.planning_review_transport import prepare_planning_review_transport
from bestseller.settings import LLMRoleSettings


def _ordered(value, mode):
    if isinstance(value, Mapping):
        if mode == "jsonb":
            keys = sorted(value, key=lambda key: (len(key.encode()), key.encode()))
        else:
            keys = sorted(value, reverse=mode == "reverse")
        return {key: _ordered(value[key], mode) for key in keys}
    if isinstance(value, list):
        return [_ordered(child, mode) for child in value]
    return value


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _fixture():
    return (
        {
            "book_spec": {
                f"source_{i:02d}": {"long_clause": f"Archive authority {i}", "id": f"Source {i}"}
                for i in reversed(range(25))
            }
        },
        {
            "world_spec": {
                "locations": [
                    {f"detail_{i:02d}": f"Glacier proposal {i}" for i in reversed(range(25))},
                    {"name": "Second station", "purpose": "Return with measurements"},
                ]
            },
            "cast_spec": {"supporting_cast": [{"name": "Mira", "role": "Navigator"}]},
        },
        ["world.locations", "cast.supporting_cast"],
    )


def _data(base, candidate, fields):
    sources, candidates = planner._source_bound_evidence_options(base, candidate, fields)
    return {
        "authority_and_baseline": base,
        "derived_candidates": candidate,
        "changed_fields": fields,
        "source_quote_options": sources,
        "candidate_quote_options": candidates,
        "candidate_quote_field_bindings": planner._source_bound_candidate_quote_bindings(
            candidates
        ),
    }


@pytest.mark.parametrize("mode", ["sorted", "reverse", "jsonb"])
def test_nested_mapping_roundtrip_stable_before_limits_ids_and_bindings(mode):
    base, candidate, fields = _fixture()
    before = deepcopy((base, candidate, fields))
    data = _data(base, candidate, fields)
    restored_base = json.loads(json.dumps(_ordered(base, mode), ensure_ascii=False))
    restored_candidate = json.loads(json.dumps(_ordered(candidate, mode), ensure_ascii=False))
    restored = _data(restored_base, restored_candidate, fields)
    assert restored == data
    assert _digest(restored) == _digest(data)
    assert len(data["source_quote_options"]) == 32
    world = [item for item in data["candidate_quote_options"] if item["field"] == "world.locations"]
    assert len(world) == 16
    assert [item["quote_id"] for item in data["source_quote_options"]] == [
        f"s{i}" for i in range(32)
    ]
    assert [item["quote_id"] for item in data["candidate_quote_options"]] == [
        f"c{i}" for i in range(len(data["candidate_quote_options"]))
    ]
    assert (base, candidate, fields) == before


def test_dedup_uses_stable_first_source_path_and_array_order_stays_significant():
    base = {"z": {"quote": "Shared literal"}, "a": {"quote": "Shared literal"}}
    candidate = {"world_spec": {"locations": ["East pier", "West pier"]}}
    fields = ["world.locations"]
    data = _data(base, candidate, fields)
    assert data["source_quote_options"] == [
        {"quote_id": "s0", "source_path": "a.quote", "quote": "Shared literal"},
    ]
    assert _data(_ordered(base, "jsonb"), candidate, fields) == data
    changed = deepcopy(candidate)
    changed["world_spec"]["locations"].reverse()
    altered = _data(base, changed, fields)
    assert altered["candidate_quote_options"] != data["candidate_quote_options"]
    assert _digest(altered) != _digest(data)
    assert altered["candidate_quote_options"][0]["quote"] == "West pier"


def test_literal_unicode_case_and_new_fields_are_not_normalized_or_dropped():
    literals = ["É", "E\u0301", "é", "CASE", "case", "未知来源条款"]
    candidate = {"world_spec": {"locations": [{"unknown_authored_clause": x} for x in literals]}}
    base = {"canonical": candidate}
    data = _data(base, candidate, ["world.locations"])
    assert [item["quote"] for item in data["source_quote_options"]] == literals
    assert [item["quote"] for item in data["candidate_quote_options"]] == literals
    changed = deepcopy(candidate)
    changed["world_spec"]["locations"][0]["new_authored_field"] = "Another literal"
    assert (
        _data(base, changed, ["world.locations"])["candidate_quote_options"]
        != (data["candidate_quote_options"])
    )


def test_real_retained_world_inputs_have_stable_future_catalog_and_full_codec_request():
    root = Path(__file__).resolve().parents[2] / "artifacts/runtime/llm_payloads"
    paths = [
        root / (rid + ".request.json")
        for rid in (
            "50934be1-fc24-4145-9c98-6f1bc34abbd5",
            "db748d48-97e9-4bfc-9e11-9487d7010f39",
        )
    ]
    if not all(path.is_file() for path in paths):
        pytest.skip("Optional retained physical request fixtures are local evidence")
    physical_before = [path.read_bytes() for path in paths]
    requests = [json.loads(raw) for raw in physical_before]
    logical = [
        decode_lossless_source(
            json.loads(
                request["messages"]["user"]["text"].split("source_review_data=", 1)[1],
            )
        )
        for request in requests
    ]
    assert logical[0]["authority_and_baseline"] == logical[1]["authority_and_baseline"]
    assert logical[0]["derived_candidates"] == logical[1]["derived_candidates"]
    assert logical[0]["candidate_quote_options"] != logical[1]["candidate_quote_options"]
    outputs = []
    for data, mode, request in zip(logical, ("reverse", "jsonb"), requests, strict=True):
        data = deepcopy(data)
        data["authority_and_baseline"] = _ordered(data["authority_and_baseline"], mode)
        data["derived_candidates"] = _ordered(data["derived_candidates"], mode)
        sources, candidates = planner._source_bound_evidence_options(
            data["authority_and_baseline"],
            data["derived_candidates"],
            data["changed_fields"],
        )
        data.update(
            source_quote_options=sources,
            candidate_quote_options=candidates,
            candidate_quote_field_bindings=planner._source_bound_candidate_quote_bindings(
                candidates
            ),
        )
        # Rebuild the complete future data block. Original physical prompts and
        # old quote ownership remain untouched; this creates no retained grade.
        prefix = request["messages"]["user"]["text"].split("Full source_review_data uses", 1)[0]
        block = "\n".join(f"{key}={planner._json_dumps(value)}" for key, value in data.items())
        system = request["messages"]["system"]["text"]
        rendered, receipt = prepare_planning_review_transport(
            system,
            prefix + block,
            data=data,
            data_block=block,
        )
        assert receipt["encoding"] == "json-pointer"
        encoded = json.loads(rendered.split("source_review_data=", 1)[1])
        assert decode_lossless_source(encoded) == data
        outputs.append(
            (
                data,
                system,
                rendered,
                _digest(
                    {
                        "system": system,
                        "user": rendered,
                        "effective": request["effective"],
                        "version": "4.2-stable-catalog",
                        "requested": request["requested"],
                    }
                ),
            )
        )
    assert outputs[0] == outputs[1]
    assert [path.read_bytes() for path in paths] == physical_before


class _Captured(BaseException):
    pass


@pytest.mark.asyncio
async def test_actual_fitting_review_records_new_version_without_making_a_grade(monkeypatch):
    rig = setup_review()
    calls = []

    async def capture(_session, _settings, request):
        calls.append(request)
        raise _Captured

    monkeypatch.setattr(planner, "complete_text", capture)
    with pytest.raises(_Captured):
        await planner._review_source_bound_expansion(
            None,
            SimpleNamespace(llm=SimpleNamespace(mock=False)),
            project=rig.project,
            book_spec=rig.book,
            world_baseline=rig.world_base,
            cast_baseline=rig.cast_base,
            world_derived=rig.world,
            cast_derived=rig.cast,
            workflow_run_id=rig.workflow_id,
        )
    assert len(calls) == 1
    assert calls[0].prompt_version == "4.2-stable-catalog"
    assert calls[0].metadata["source_transport"]["encoding"] == "identity"


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["identity", "json-pointer"])
async def test_actual_review_key_order_roundtrip_has_exact_request_hash_and_capacity(
    monkeypatch,
    encoding,
):
    rig = setup_review()
    if encoding == "json-pointer":
        literal = "Complete tide-station records retain every observation and final clause. " * 500
        binding = rig.project._source_bound_planning_canonical
        binding["content"]["complete_appendix"] = literal
        binding["content_hash"] = _digest(binding["content"])
        rig.book["complete_appendix"] = literal
        rig.world_base["world_premise"] = literal
        rig.world["world_premise"] = literal
        rig.world["locations"][0]["complete_appendix"] = literal
    metadata = deepcopy(rig.project.metadata_json)
    canonical = deepcopy(rig.project._source_bound_planning_canonical)
    inputs = {
        "book_spec": rig.book,
        "world_baseline": rig.world_base,
        "cast_baseline": rig.cast_base,
        "world_derived": rig.world,
        "cast_derived": rig.cast,
    }
    before = deepcopy(inputs)
    calls, capacities, per_call_capacity = [], [], []
    original_capacity = content_policy.assert_source_bound_planning_prompt_capacity

    def capacity(system, user):
        try:
            receipt = original_capacity(system, user)
        except content_policy.SourceBoundPlanningCapacityError as exc:
            capacities.append((system, user, deepcopy(exc.receipt)))
            raise
        capacities.append((system, user, deepcopy(receipt)))
        return receipt

    async def capture(_session, _settings, request):
        assert _session is None
        calls.append(request)
        raise _Captured

    monkeypatch.setattr(content_policy, "assert_source_bound_planning_prompt_capacity", capacity)
    monkeypatch.setattr(planner, "complete_text", capture)
    for mode in ("reverse", "jsonb"):
        rig.project.metadata_json = _ordered(metadata, mode)
        rig.project._source_bound_planning_canonical = _ordered(canonical, mode)
        kwargs = _ordered(inputs, mode)
        kwargs_before = deepcopy(kwargs)
        capacities.clear()
        with pytest.raises(_Captured):
            await planner._review_source_bound_expansion(
                None,
                SimpleNamespace(llm=SimpleNamespace(mock=False)),
                project=rig.project,
                workflow_run_id=rig.workflow_id,
                **kwargs,
            )
        per_call_capacity.append(deepcopy(capacities))
        assert kwargs == kwargs_before
        assert rig.project.metadata_json == metadata
        assert rig.project._source_bound_planning_canonical == canonical
    first, second = calls
    assert first.system_prompt == second.system_prompt
    assert first.user_prompt == second.user_prompt
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.metadata["source_transport"] == second.metadata["source_transport"]
    assert first.metadata["source_transport"]["encoding"] == encoding
    assert per_call_capacity[0] == per_call_capacity[1]
    assert per_call_capacity[0][-1][:2] == (first.system_prompt, first.user_prompt)
    role = LLMRoleSettings(
        model="openai/offline-fixture",
        api_base="http://offline.invalid/v1",
        temperature=0.25,
        max_tokens=5000,
        timeout_seconds=180,
        thinking_type="disabled",
        stream=False,
    )
    assert _effective_input_payload(first, role) == _effective_input_payload(second, role)
    assert _effective_input_hash(first, role) == _effective_input_hash(second, role)
    if encoding == "json-pointer":
        data = decode_lossless_source(
            json.loads(first.user_prompt.split("source_review_data=", 1)[1])
        )
    else:
        data = {}
        for name in (
            "authority_and_baseline",
            "derived_candidates",
            "changed_fields",
            "source_quote_options",
            "candidate_quote_options",
            "candidate_quote_field_bindings",
        ):
            data[name] = json.JSONDecoder().raw_decode(
                first.user_prompt.split("\n" + name + "=", 1)[1],
            )[0]
    assert data["authority_and_baseline"]["canonical_source"] == canonical
    assert data["derived_candidates"] == {"world_spec": rig.world, "cast_spec": rig.cast}
    sources, candidates = planner._source_bound_evidence_options(
        data["authority_and_baseline"],
        data["derived_candidates"],
        data["changed_fields"],
    )
    assert data["source_quote_options"] == sources
    assert data["candidate_quote_options"] == candidates
    assert data["candidate_quote_field_bindings"] == (
        planner._source_bound_candidate_quote_bindings(candidates)
    )
    assert inputs == before
