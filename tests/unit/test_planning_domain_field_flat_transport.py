"""Offline flat IDs and full literal sources; no provider compatibility claims."""

from copy import deepcopy
import hashlib
import json
import re
from types import SimpleNamespace

import pytest
import test_planning_domain_field_protocol as old

from bestseller.services import planning_domain_field_flat_transport as flat
from bestseller.services import planning_domain_field_protocol as domain
from bestseller.services.lossless_source_encoding import (
    _bytes,
    _container,
    decode_lossless_source,
    encode_lossless_source,
)
from bestseller.services.source_bound_planning_content import SourceBoundPlanningCapacityError

pytestmark = pytest.mark.unit
BOUNDS = {"max_chars": 1_000_000, "max_estimated_tokens": 439650}


@pytest.fixture
def case():
    before, native = old.native_fixture()
    candidate, _ = domain.prepare_domain_candidate(before, native)
    report = old.source_report(candidate)
    _, contract = domain.tools_for_domain_corrections(candidate, report, old.MANIFEST)
    tools, wire = flat.tools_for_flat_domain_fields(candidate, report, old.MANIFEST, contract)
    corrections = {
        path: (
            "  Model-authored title " + path.split("/")[2] + "  "
            if path.endswith("/title")
            else deepcopy(contract["identity_format_mappings"][path]["expected_canonical_array"])
        )
        for path in contract["paths"]
    }
    return SimpleNamespace(
        candidate=candidate,
        report=report,
        contract=contract,
        wire=wire,
        tools=tools,
        corrections=corrections,
        manifest=old.MANIFEST,
    )


def fields(case):
    return {key: deepcopy(case.corrections[path]) for key, path in case.wire["id_to_path"].items()}


def completion(case, *, arguments=None):
    raw = old.canonical(fields(case)) if arguments is None else arguments
    call = {
        "id": "flat-native-call",
        "type": "function",
        "function": {
            "name": flat.TOOL,
            "arguments": raw,
        },
    }
    return SimpleNamespace(
        content="",
        tool_calls=[call],
        finish_reason="tool_calls",
        function_call=None,
        raw_message={"role": "assistant", "content": None, "tool_calls": [deepcopy(call)]},
    )


def parse(case, value):
    return flat.parse_flat_domain_fields(
        value,
        candidate=case.candidate,
        validation_report=case.report,
        identity_manifest=case.manifest,
        domain_contract=case.contract,
        wire_contract=case.wire,
    )


def prompt(case, *, source_context=None, **bounds):
    return flat.build_flat_domain_prompts(
        project_brief={"source": "Full approved source records.", "unknown": None},
        candidate=case.candidate,
        validation_report=case.report,
        domain_contract=case.contract,
        wire_contract=case.wire,
        source_context=source_context
        if source_context is not None
        else {
            "unknown_attribution": None,
            "full_old_native_candidate": case.candidate,
            "old_technical_proof": {
                "$ref": 0,
                "unusual.键/~": [False, 1, 1.0, None],
                "literal_pool_marker": "§0",
                "literal_escape": "§§0",
            },
        },
        **{**BOUNDS, **bounds},
    )


def test_flat_safe_top_level_ids_bijectively_retain_every_original_schema(case):
    original = deepcopy(case.contract)
    tools, wire = flat.tools_for_flat_domain_fields(
        case.candidate, case.report, case.manifest, case.contract
    )
    ids = list(wire["id_to_path"])
    assert ids == [f"f{i:04d}" for i in range(1, len(case.contract["paths"]) + 1)]
    assert all(re.fullmatch(r"f[0-9]{4,}", key) for key in ids)
    assert list(wire["id_to_path"].values()) == sorted(case.contract["paths"])
    assert len(set(wire["id_to_path"].values())) == len(ids)
    parameters = tools[0]["function"]["parameters"]
    assert tools[0]["function"]["name"] == flat.TOOL and tools[0]["function"]["strict"]
    assert parameters["type"] == "object" and parameters["required"] == ids
    assert parameters["additionalProperties"] is False
    assert "corrections" not in parameters["properties"]
    for key, path in wire["id_to_path"].items():
        assert (
            parameters["properties"][key]
            == original["parameters"]["properties"]["corrections"]["properties"][path]
        )
    assert case.contract == original and not wire["grade_asserted"]


@pytest.mark.parametrize("raw_mode", ["present", "missing", "indexed"])
def test_complete_native_fields_exact_bytes_and_unchanged_original_apply(case, raw_mode):
    arguments = json.dumps(fields(case), ensure_ascii=False, indent=2)
    value = completion(case, arguments=arguments)
    if raw_mode == "missing":
        value.raw_message = None
    elif raw_mode == "indexed":
        value.raw_message["tool_calls"][0]["index"] = 0
    before = deepcopy(case.candidate)
    corrections, receipt = parse(case, value)
    assert corrections == case.corrections and case.candidate == before
    result, derivation = domain.apply_domain_corrections(
        case.candidate,
        corrections,
        validation_report=case.report,
        identity_manifest=case.manifest,
        contract=case.contract,
    )
    assert receipt["derivation"] == derivation and derivation["non_targets_unchanged"]
    assert result["chapters"][0]["title"] == case.corrections["/chapters/0/title"]
    assert receipt["raw_arguments"] == arguments
    assert receipt["raw_arguments_sha256"] == hashlib.sha256(arguments.encode()).hexdigest()
    assert receipt["raw_message_status"] == ("missing" if raw_mode == "missing" else "present")
    assert receipt["inverse_verified"] and not receipt["physical_custody_verified"]
    assert not receipt["grade_asserted"] and receipt["tools_executed"] == 0


@pytest.mark.parametrize(
    "kind",
    [
        "empty",
        "old_wrapper",
        "pathkeys",
        "missing",
        "extra",
        "blank",
        "null",
        "wrong_type",
        "identity_reorder",
        "identity_replace",
        "identity_decorate",
        "identity_alias",
        "overlong",
    ],
)
def test_invalid_or_partial_native_fields_never_salvaged(case, kind):
    submitted = fields(case)
    title_id = next(k for k, p in case.wire["id_to_path"].items() if p.endswith("/title"))
    identity_id = next(k for k, p in case.wire["id_to_path"].items() if p.endswith("/participants"))
    if kind == "empty":
        submitted = {}
    elif kind == "old_wrapper":
        submitted = {"corrections": {}}
    elif kind == "pathkeys":
        submitted = case.corrections
    elif kind == "missing":
        del submitted[title_id]
    elif kind == "extra":
        submitted["f9999"] = "extra"
    elif kind == "blank":
        submitted[title_id] = " \n"
    elif kind == "null":
        submitted[title_id] = None
    elif kind == "wrong_type":
        submitted[title_id] = True
    elif kind == "identity_reorder":
        submitted[identity_id].reverse()
    elif kind == "identity_replace":
        submitted[identity_id][0] = "Tao"
    elif kind == "identity_decorate":
        submitted[identity_id][1] = "Tao(supplier)"
    elif kind == "identity_alias":
        submitted[identity_id][1] = "T"
    elif kind == "overlong":
        submitted[title_id] = "x" * 4001
    value = completion(case, arguments=old.canonical(submitted))
    # Even a full, seemingly usable diagnostic string cannot become a submission.
    value.raw_message["reasoning_content"] = old.canonical(fields(case))
    before = deepcopy(case.candidate)
    with pytest.raises(ValueError):
        parse(case, value)
    assert case.candidate == before


@pytest.mark.parametrize(
    "kind",
    [
        "length",
        "unknown_finish",
        "multiple",
        "text_only",
        "wrong_tool",
        "raw_args",
        "raw_content",
        "raw_role",
        "legacy",
        "raw_legacy",
        "raw_index",
        "duplicate_json",
        "fence",
        "structured_commentary",
        "list",
        "nan",
        "nonfinite",
        "call_extra",
    ],
)
def test_incomplete_conflicting_or_ambiguous_native_evidence_rejects(case, kind):
    value = completion(case)
    if kind == "length":
        value.finish_reason = "length"
    elif kind == "unknown_finish":
        value.finish_reason = None
    elif kind == "multiple":
        value.tool_calls.append(deepcopy(value.tool_calls[0]))
    elif kind == "text_only":
        value.content, value.tool_calls = old.canonical(fields(case)), []
    elif kind == "wrong_tool":
        value.tool_calls[0]["function"]["name"] = domain.TOOL_NAME
        value.raw_message["tool_calls"] = deepcopy(value.tool_calls)
    elif kind == "raw_args":
        value.raw_message["tool_calls"][0]["function"]["arguments"] += " "
    elif kind == "raw_content":
        value.raw_message["content"] = "conflicting"
    elif kind == "raw_role":
        value.raw_message["role"] = "user"
    elif kind == "legacy":
        value.function_call = {"name": flat.TOOL}
    elif kind == "raw_legacy":
        value.raw_message["function_call"] = {"name": flat.TOOL}
    elif kind == "raw_index":
        value.raw_message["tool_calls"][0]["index"] = True
    elif kind == "duplicate_json":
        value = completion(case, arguments='{"f0001":1,"f0001":2}')
    elif kind == "fence":
        value = completion(case, arguments="```json\n{}\n```")
    elif kind == "structured_commentary":
        value.content = value.raw_message["content"] = "{}"
    elif kind == "list":
        value = completion(case, arguments="[]")
    elif kind == "nan":
        value = completion(case, arguments='{"f0001":NaN}')
    elif kind == "nonfinite":
        value = completion(case, arguments='{"f0001":1e9999}')
    elif kind == "call_extra":
        value.tool_calls[0]["extra"] = True
    with pytest.raises(ValueError):
        parse(case, value)


@pytest.mark.parametrize(
    "kind",
    [
        "candidate",
        "report",
        "manifest",
        "domain",
        "id_swap",
        "id_alias",
        "schema",
        "binding",
        "wire_hash",
    ],
)
def test_stale_or_tampered_source_and_bijection_fail(case, kind):
    changed = deepcopy(case)
    if kind == "candidate":
        changed.candidate["unknown_root"]["new"] = None
    elif kind == "report":
        changed.report["warnings"].append({"new": None})
    elif kind == "manifest":
        changed.manifest = deepcopy(case.manifest)
        changed.manifest[0]["role"] = "changed"
    elif kind == "domain":
        changed.contract["candidate_sha256"] = "0" * 64
    elif kind == "id_swap":
        keys = list(changed.wire["id_to_path"])
        changed.wire["id_to_path"][keys[0]], changed.wire["id_to_path"][keys[1]] = (
            changed.wire["id_to_path"][keys[1]],
            changed.wire["id_to_path"][keys[0]],
        )
    elif kind == "id_alias":
        changed.wire["id_to_path"]["f0001"] = "candidate:guessed.path"
    elif kind == "schema":
        changed.wire["parameters"]["properties"]["f0001"] = {"type": "string"}
    elif kind == "binding":
        changed.wire["bindings"][0]["before_present"] = False
    elif kind == "wire_hash":
        changed.wire["bijection_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        parse(changed, completion(case))


@pytest.mark.parametrize("field", ["conflict_stakes", "signature_image", "cut_point"])
def test_flat_wire_preserves_missing_leaf_presence_and_existing_parent_rule(field):
    candidate, report, path = old.absent_leaf_case(field)
    _, contract = domain.tools_for_domain_corrections(candidate, report, old.MANIFEST)
    _, wire = flat.tools_for_flat_domain_fields(candidate, report, old.MANIFEST, contract)
    assert wire["bindings"][0]["before_present"] is False
    c = SimpleNamespace(
        candidate=candidate,
        report=report,
        manifest=old.MANIFEST,
        contract=contract,
        wire=wire,
        corrections={path: "Exact model value."},
    )
    corrections, receipt = parse(c, completion(c))
    assert corrections == c.corrections
    assert receipt["derivation"]["added_paths"] == [path]
    assert receipt["derivation"]["absent_leaf_inverse_verified"]


def test_literal_v1_complete_five_fields_bare_packet_offsets_and_no_reference_transform(case):
    from bestseller.services.source_complete_retention import _packet

    system, user, receipt = prompt(case)
    logical = flat.decode_flat_domain_prompts(
        system_prompt=system, user_prompt=user, transport_receipt=receipt, **BOUNDS
    )
    assert set(logical) == set(flat.SOURCE_ROLES)
    assert logical["candidate_payload"] == case.candidate
    assert logical["correction_contract"] == {
        "domain_contract": case.contract,
        "wire_contract": case.wire,
    }
    assert logical["source_context"]["old_technical_proof"]["$ref"] == 0
    assert logical["source_context"]["old_technical_proof"]["literal_pool_marker"] == "§0"
    packet_text = user[receipt["packet_start"] : receipt["packet_end"]]
    packet = json.loads(packet_text)
    assert packet["schema"] == "lossless-source-encoding.v1" and packet["encoding"] == "identity"
    assert packet["references"] == [] and packet["value"] == logical
    assert decode_lossless_source(packet) == logical
    retained_text, retained_packet, retained_logical = _packet(
        SimpleNamespace(user_prompt=user), receipt["packet_start"], receipt["packet_end"]
    )
    assert retained_text == packet_text and retained_packet == packet
    assert retained_logical == logical
    assert old.canonical(packet) == old.canonical(
        _container(deepcopy(logical), [], _bytes(logical), version=1)
    )
    assert receipt["packet_sha256"] == hashlib.sha256(packet_text.encode()).hexdigest()
    assert receipt["field_sha256"] == {k: old.digest(v) for k, v in logical.items()}
    assert receipt["offset_unit"] == "unicode_code_points" and receipt["references_count"] == 0
    assert receipt["prompt_version"] == flat.VERSION and receipt["prompt_template"] == flat.TEMPLATE
    assert flat.TOOL in system and domain.TOOL_NAME not in system
    assert 'Return only {"patches"' not in system
    assert not receipt["prompt_capacity"]["whole_request_model_capacity_verified"]
    assert not receipt["grade_asserted"] and not receipt["source_authority_granted"]


@pytest.mark.parametrize(
    "kind",
    [
        "system",
        "user",
        "role",
        "hash",
        "offset",
        "receipt_bounds",
        "v2_identity",
        "v4",
        "references",
        "carrier_receipt",
        "duplicate",
    ],
)
def test_flat_transport_tamper_and_nonliteral_carriers_fail(case, kind):
    system, user, receipt = prompt(case)
    wire = json.loads(user[len(flat.MARKER) :])
    if kind == "system":
        system += " "
    elif kind == "user":
        user += " "
    elif kind == "role":
        wire["source_roles"]["source_context"] = "authoritative"
    elif kind == "hash":
        wire["logical_sha256"] = "0" * 64
    elif kind == "offset":
        receipt["packet_start"] += 1
    elif kind == "receipt_bounds":
        receipt["max_estimated_tokens"] += 1
    elif kind == "v2_identity":
        value = wire["payload"]["value"]
        wire["payload"] = _container(value, [], _bytes(value), version=2)
    elif kind == "v4":
        wire["payload"] = encode_lossless_source(wire["payload"]["value"], version=4)
    elif kind == "references":
        wire["payload"]["references"] = [{"path": "/x", "target": "/y"}]
    elif kind == "carrier_receipt":
        wire["payload"]["receipt"]["original_sha256"] = "0" * 64
    elif kind == "duplicate":
        user = flat.MARKER + '{"schema":"x",' + user[len(flat.MARKER) + 1 :]
    if kind not in {"system", "user", "offset", "receipt_bounds", "duplicate"}:
        user = flat.MARKER + old.canonical(wire)
    with pytest.raises(ValueError):
        flat.decode_flat_domain_prompts(
            system_prompt=system, user_prompt=user, transport_receipt=receipt, **BOUNDS
        )


@pytest.mark.parametrize("bound", [True, False, 0, -1, 1_000_001, 60000.0, "60000", None])
@pytest.mark.parametrize("name", ["max_chars", "max_estimated_tokens"])
def test_independent_explicit_bounds_validated_at_writer_and_reader(case, name, bound):
    with pytest.raises(ValueError):
        prompt(case, **{name: bound})
    system, user, _ = prompt(case)
    with pytest.raises(ValueError):
        flat.decode_flat_domain_prompts(
            system_prompt=system, user_prompt=user, **{**BOUNDS, name: bound}
        )


def test_full_large_literal_source_fits_only_explicit_bounds_and_never_truncates(case):
    context = {
        "old_technical_proof": None,
        "full": ("Complete source with unknown key/ref §0. " * 6500),
    }
    before = deepcopy(context)
    with pytest.raises(SourceBoundPlanningCapacityError):
        prompt(case, source_context=context, max_chars=120000, max_estimated_tokens=60000)
    system, user, receipt = prompt(case, source_context=context)
    assert receipt["prompt_capacity"]["combined_chars"] > 120000
    assert not receipt["prompt_capacity"]["truncated"]
    logical = flat.decode_flat_domain_prompts(
        system_prompt=system, user_prompt=user, transport_receipt=receipt, **BOUNDS
    )
    assert logical["source_context"] == context == before
    with pytest.raises(ValueError):
        flat.decode_flat_domain_prompts(
            system_prompt=system,
            user_prompt=user,
            transport_receipt=receipt,
            **{**BOUNDS, "max_estimated_tokens": 439649},
        )
    with pytest.raises(SourceBoundPlanningCapacityError):
        prompt(case, source_context={"full": "X" * 1_000_000})
