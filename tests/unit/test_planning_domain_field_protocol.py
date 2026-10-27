"""Offline exact field protocol; real strict applier and validation diagnostics."""

# ruff: noqa: RUF001 -- Explicit tests of ASCII/fullwidth parenthetical labels.
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
from test_source_bound_outline_integrity import source

from bestseller.domain.planning_readiness import PlanningReadinessFinding, PlanningReadinessReport
from bestseller.domain.workflow import ChapterOutlineBatchInput, ChapterOutlineInput
from bestseller.services import planning_domain_field_protocol as protocol
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.narrative_contracts import validate_chapter_plan_contract
from bestseller.services.source_bound_outline_integrity import (
    SourceBoundOutlineIntegrityError,
    prepare_source_bound_outline,
)
from bestseller.services.source_bound_planning_content import SourceBoundPlanningCapacityError

pytestmark = pytest.mark.unit
MANIFEST = [
    {"name": "Mira", "aliases": ["M"], "role": "protagonist"},
    {"name": "Tao", "aliases": ["T"], "role": "supplier"},
    {"name": "Rook", "aliases": ["R"], "role": "inspector"},
    {"name": "顾问", "aliases": [], "role": "consultant"},
]


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def native_fixture():
    before = source()
    before["unknown_root"]["unusual.键/~"] = {"§0": [None, False, 1, 1.0, {"$ref": 0}]}
    patches = []
    for i, chapter in enumerate(before["chapters"]):
        chapter["title"] = None
        for field in ("entry_state", "exit_state"):
            patches.append(
                {
                    "chapter_number": i + 1,
                    "scope": "scene",
                    "scene_number": 1,
                    "field": field,
                    "value": f"Exact {field} chapter {i + 1}.",
                }
            )
        patches.append(
            {
                "chapter_number": i + 1,
                "scope": "scene",
                "scene_number": 1,
                "field": "participants",
                "value": ["Mira", "T（supplier）", "Rook (inspector)", "顾问（咨询(远程)）"],
            }
        )
    patches.append(
        {
            "chapter_number": 1,
            "scope": "chapter",
            "field": "chapter_concrete_actions",
            "value": ["Mira opens the sealed box."],
        }
    )
    return before, {"patches": patches}


def source_report(candidate):
    with pytest.raises(SourceBoundOutlineIntegrityError) as caught:
        prepare_source_bound_outline(
            candidate,
            volume_number=1,
            expected_count=4,
            chapter_number_offset=1,
            identity_manifest=MANIFEST,
        )
    return caught.value.report


@pytest.fixture
def field_case():
    before, native = native_fixture()
    candidate, adapter = protocol.prepare_domain_candidate(before, native)
    report = source_report(candidate)
    tools, contract = protocol.tools_for_domain_corrections(candidate, report, MANIFEST)
    corrections = {}
    for path in contract["paths"]:
        if path.endswith("/title"):
            corrections[path] = "Model authored heading " + path.split("/")[2]
        else:
            corrections[path] = deepcopy(
                contract["identity_format_mappings"][path]["expected_canonical_array"]
            )
    return SimpleNamespace(
        before=before,
        native=native,
        candidate=candidate,
        adapter=adapter,
        report=report,
        tools=tools,
        contract=contract,
        corrections=corrections,
    )


def apply(case, corrections=None, **kwargs):
    return protocol.apply_domain_corrections(
        case.candidate,
        case.corrections if corrections is None else corrections,
        validation_report=kwargs.get("report", case.report),
        identity_manifest=kwargs.get("manifest", MANIFEST),
        contract=kwargs.get("contract", case.contract),
    )


def completion(case, *, arguments=None):
    raw = canonical({"corrections": case.corrections}) if arguments is None else arguments
    call = {
        "id": "native-call",
        "type": "function",
        "function": {
            "name": protocol.TOOL_NAME,
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
    return protocol.parse_domain_corrections(
        value,
        candidate=case.candidate,
        validation_report=case.report,
        identity_manifest=MANIFEST,
        contract=case.contract,
    )


def prompt(case, **kwargs):
    return protocol.build_domain_field_prompts(
        project_brief={"source": "full approved records", "unknown": None},
        candidate=case.candidate,
        validation_report=case.report,
        contract=case.contract,
        source_context={
            "unknown_paid_attribution": None,
            "approved_cast": MANIFEST,
            "old_raw": {"§0": "§literal", "$ref": 0},
            "full_candidate": case.candidate,
        },
        **kwargs,
    )


def test_actual_strict_applier_eight_wrappers_inverse_optional_field_and_unknowns(field_case):
    c = field_case
    old, original = native_fixture()
    assert c.before == old and c.native == original
    assert len(c.adapter["state_wrappers"]) == 8
    assert c.adapter["native_patch_sha256"] == c.adapter["inverse_patch_sha256"] == digest(original)
    adapted = deepcopy(original)
    for row in c.adapter["state_wrappers"]:
        adapted["patches"][row["patch_index"]]["value"] = {"summary": row["original_text"]}
        state = protocol._at(c.candidate, row["path"])
        assert state["summary"] == row["original_text"]
    assert c.adapter["adapted_patch_sha256"] == digest(adapted)
    assert c.adapter["candidate_sha256"] == digest(c.candidate)
    assert c.candidate["unknown_root"] == old["unknown_root"]
    assert (
        c.candidate["chapters"][0]["scenes"][0]["entry_state"]["unknown"]
        == (old["chapters"][0]["scenes"][0]["entry_state"]["unknown"])
    )
    assert "chapter_concrete_actions" not in old["chapters"][0]
    assert c.candidate["chapters"][0]["chapter_concrete_actions"] == ["Mira opens the sealed box."]
    assert not c.adapter["grade_asserted"] and c.adapter["non_targets_unchanged"]


def test_summary_exact_whitespace_preserved_when_applier_can_and_rejected_when_it_strips():
    before, native = native_fixture()
    native["patches"][0]["value"] = "  exact\nstate  "
    with pytest.raises(ValueError, match="summary_text_changed"):
        protocol.prepare_domain_candidate(before, native)
    del before["chapters"][0]["scenes"][0]["entry_state"]["summary"]
    candidate, receipt = protocol.prepare_domain_candidate(before, native)
    assert candidate["chapters"][0]["scenes"][0]["entry_state"]["summary"] == "  exact\nstate  "
    assert receipt["state_wrappers"][0]["original_text"] == "  exact\nstate  "


@pytest.mark.parametrize(
    "change",
    [
        "empty",
        "bool_owner",
        "scene_owner",
        "duplicate_owner",
        "duplicate_scene",
        "duplicate_target",
        "overlap",
        "title",
        "extra_key",
        "wrong_state",
        "nonobject_scene",
        "alias_drift",
    ],
)
def test_native_adapter_rejects_unsupported_without_mutating_inputs(change):
    before, native = native_fixture()
    if change == "empty":
        native["patches"][0]["value"] = " \n"
    elif change == "bool_owner":
        native["patches"][0]["chapter_number"] = True
    elif change == "scene_owner":
        native["patches"][0]["scene_number"] = 99
    elif change == "duplicate_owner":
        before["chapters"][1]["chapter_number"] = 1
    elif change == "duplicate_scene":
        before["chapters"][0]["scenes"].append(deepcopy(before["chapters"][0]["scenes"][0]))
    elif change == "duplicate_target":
        native["patches"].append(deepcopy(native["patches"][0]))
    elif change == "overlap":
        native["patches"].extend(
            [
                {
                    "chapter_number": 1,
                    "scope": "chapter",
                    "field": "causal_contract",
                    "value": {"state_change": "changed"},
                },
                {
                    "chapter_number": 1,
                    "scope": "causal_contract",
                    "field": "state_change",
                    "value": "changed",
                },
            ]
        )
    elif change == "title":
        native["patches"][0].update(scope="chapter", field="title", value="not registered")
        del native["patches"][0]["scene_number"]
    elif change == "extra_key":
        native["patches"][0]["authorization"] = True
    elif change == "wrong_state":
        native["patches"][0]["value"] = ["not state"]
    elif change == "nonobject_scene":
        before["chapters"][0]["scenes"].append(None)
    elif change == "alias_drift":
        before["chapters"][0]["chapter_goal"] = before["chapters"][0]["goal"]
        native["patches"].append(
            {
                "chapter_number": 1,
                "scope": "chapter",
                "field": "goal",
                "value": "Mira carries the lamp to the pier.",
            }
        )
    old, document = deepcopy(before), deepcopy(native)
    with pytest.raises((ValueError, RuntimeError)):
        protocol.prepare_domain_candidate(before, native)
    assert before == old and native == document


@pytest.mark.parametrize(
    "document",
    [
        '{"patches":[],"patches":[]}',
        '{"patches":[],"extra":0}',
        '{"patches": [NaN]}',
        "[]",
        "null",
        "true",
    ],
)
def test_native_document_strict_json(document):
    with pytest.raises(ValueError):
        protocol.prepare_domain_candidate(source(), document)


def test_real_sixteen_source_diagnostics_eight_paths_exact_typed_tool_and_mapping(field_case):
    c = field_case
    assert len(c.report["violations"]) == 16
    assert len(c.contract["paths"]) == 8
    assert "title" in ChapterOutlineInput.model_fields
    function = c.tools[0]["function"]
    assert function["name"] == protocol.TOOL_NAME and function["strict"]
    parameters = function["parameters"]
    assert parameters["required"] == ["corrections"] and not parameters["additionalProperties"]
    schema = parameters["properties"]["corrections"]
    assert schema["required"] == c.contract["paths"] and not schema["additionalProperties"]
    assert schema["properties"]["/chapters/0/title"]["maxLength"] == 4000
    for path, value in c.corrections.items():
        if path.endswith("participants"):
            assert value == ["Mira", "Tao", "Rook", "顾问"]
            assert schema["properties"][path]["enum"] == [value]
            mappings = c.contract["identity_format_mappings"][path]
            assert mappings["raw_labels"][1] == "T（supplier）"
            assert all(not r["qualifiers_are_author_facts"] for r in mappings["mappings"])


def test_real_typed_diagnostics_collected_despite_first_layer_failure_and_no_write(field_case):
    candidate = deepcopy(field_case.candidate)
    candidate["chapters"][2]["hook_description"] = ""
    before = deepcopy(candidate)
    integrity = source_report(candidate)
    view = ChapterOutlineBatchInput.model_validate(deepcopy(candidate))
    chapter_report = validate_chapter_plan_contract(
        view, identity_manifest=MANIFEST, require_identity_registry=True
    ).to_dict()
    assert any(v["code"] == "PLAN_CHAPTER_HOOK_GENERIC" for v in chapter_report["violations"])
    # The real validation view is diagnostic only; do not dump it into candidate.
    combined = protocol.combine_domain_validation_reports(integrity, chapter_report)
    assert combined["reports"] == [integrity, chapter_report] and candidate == before
    assert {v["code"] for v in chapter_report["violations"]} <= {
        "PLAN_CHAPTER_HOOK_GENERIC",
        "PLAN_SCENE_UNKNOWN_PARTICIPANT",
    }
    _, contract = protocol.tools_for_domain_corrections(candidate, combined, MANIFEST)
    assert "/chapters/2/hook_description" in contract["paths"]
    assert len(contract["paths"]) == 9


@pytest.mark.parametrize(
    "code,field",
    [
        ("PLAN_CHAPTER_CONFLICT_META", "main_conflict"),
        ("PLAN_CHAPTER_HOOK_GENERIC", "hook_description"),
    ],
)
@pytest.mark.parametrize("style", ["pointer", "typed"])
def test_dynamic_known_meaning_targets_exact_current_report_only(field_case, code, field, style):
    report = deepcopy(field_case.report)
    location = (
        f"/chapters/2/{field}"
        if style == "pointer"
        else f"chapter_outline_batch.chapters[2].{field}"
    )
    other = {
        "gate_name": "chapter_plan_contract",
        "passed": False,
        "violations": [{"code": code, "location": location, "severity": "critical"}],
        "warnings": [],
    }
    combined = protocol.combine_domain_validation_reports(report, other)
    _, contract = protocol.tools_for_domain_corrections(field_case.candidate, combined, MANIFEST)
    assert len(contract["paths"]) == 9 and f"/chapters/2/{field}" in contract["paths"]
    assert combined["reports"][0] == report and combined["reports"][1] == other


@pytest.mark.parametrize(
    "label",
    [
        "T",
        "Tao junior (supplier)",
        "new person（supplier）",
        "T（supplier",
        "T（supplier)花",
        "T()",
        "T(a) extra",
        "T(a)(b)",
        "T（a)）",
    ],
)
def test_unrecognized_identity_fails_before_tool_build(field_case, label):
    candidate = deepcopy(field_case.candidate)
    candidate["chapters"][0]["scenes"][0]["participants"][1] = label
    report = source_report(candidate)
    with pytest.raises(ValueError):
        protocol.tools_for_domain_corrections(candidate, report, MANIFEST)


def test_shared_alias_ambiguity_never_chooses_first_canonical(field_case):
    manifest = deepcopy(MANIFEST)
    manifest[-1]["aliases"] = ["T"]
    with pytest.raises(ValueError, match="ambiguous_or_unknown"):
        protocol.tools_for_domain_corrections(field_case.candidate, field_case.report, manifest)


@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "noncritical",
        "opening",
        "outofbounds",
        "leadingzero",
        "stale_title",
        "stale_name",
        "bad_metadata",
    ],
)
def test_bad_or_stale_reports_refuse_targets(field_case, change):
    candidate, report = deepcopy(field_case.candidate), deepcopy(field_case.report)
    if change == "unknown":
        report["violations"][0]["code"] = "UNKNOWN_CRITICAL"
    elif change == "noncritical":
        report["violations"][0]["severity"] = "warning"
    elif change == "opening":
        report["violations"][0]["location"] = "/chapters/0/opening_situation"
    elif change == "outofbounds":
        report["violations"][0]["location"] = "/chapters/99/title"
    elif change == "leadingzero":
        report["violations"][0]["location"] = "/chapters/00/title"
    elif change == "stale_title":
        candidate["chapters"][0]["title"] = "Already supplied"
    elif change == "stale_name":
        candidate["chapters"][0]["scenes"][0]["participants"][1] = "Tao"
    elif change == "bad_metadata":
        report["violations"] = [None]
    with pytest.raises(ValueError):
        protocol.tools_for_domain_corrections(candidate, report, MANIFEST)


def test_exact_apply_preserves_unknowns_nulls_order_and_no_grade(field_case):
    c = field_case
    old = deepcopy(c.candidate)
    corrected, receipt = apply(c)
    assert c.candidate == old
    expected = deepcopy(old)
    for path, value in c.corrections.items():
        protocol._set(expected, path, value)
    assert canonical(corrected) == canonical(expected)
    assert corrected["unknown_root"] == old["unknown_root"]
    assert receipt["paths"] == c.contract["paths"] and not receipt["grade_asserted"]
    assert receipt["full_domain_and_new_admission_required"] and receipt["non_targets_unchanged"]


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "extra",
        "blank",
        "overlong",
        "int_title",
        "null_title",
        "not_dict",
        "not_list",
        "add",
        "delete",
        "reorder",
        "alias",
        "decorate",
        "swap_valid",
        "bool",
    ],
)
def test_model_values_cannot_expand_contract_or_identities(field_case, kind):
    values = deepcopy(field_case.corrections)
    title, path = "/chapters/0/title", "/chapters/0/scenes/0/participants"
    if kind == "missing":
        del values[title]
    elif kind == "extra":
        values["/chapters/0/main_conflict"] = "not authorized"
    elif kind == "blank":
        values[title] = "  \n"
    elif kind == "overlong":
        values[title] = "x" * 4001
    elif kind == "int_title":
        values[title] = 7
    elif kind == "null_title":
        values[title] = None
    elif kind == "not_dict":
        values = []
    elif kind == "not_list":
        values[path] = "Mira"
    elif kind == "add":
        values[path].append("Mira")
    elif kind == "delete":
        values[path].pop()
    elif kind == "reorder":
        values[path].reverse()
    elif kind == "alias":
        values[path][1] = "T"
    elif kind == "decorate":
        values[path][1] = "Tao(supplier)"
    elif kind == "swap_valid":
        values[path][0] = "Tao"
    elif kind == "bool":
        values[path][0] = True
    old = deepcopy(field_case.candidate)
    with pytest.raises(ValueError):
        apply(field_case, values)
    assert field_case.candidate == old


@pytest.mark.parametrize("kind", ["candidate", "report", "manifest", "contract", "aggregate"])
def test_contract_stale_or_tampered_rejects(field_case, kind):
    c = deepcopy(field_case)
    if kind == "candidate":
        c.candidate["unknown_root"]["new"] = None
    elif kind == "report":
        c.report["warnings"].append({"unknown": None})
    elif kind == "manifest":
        manifest = deepcopy(MANIFEST)
        manifest[0]["role"] = "changed"
        with pytest.raises(ValueError, match="contract_mismatch"):
            apply(c, manifest=manifest)
        return
    elif kind == "contract":
        c.contract["paths"] = c.contract["paths"][:-1]
    elif kind == "aggregate":
        c.report = protocol.combine_domain_validation_reports(c.report)
        c.report["violations"].pop()
    with pytest.raises(ValueError):
        apply(c)


def test_native_function_arguments_exact_bytes_raw_index_and_non_authoritative_commentary(
    field_case,
):
    arguments = json.dumps({"corrections": field_case.corrections}, ensure_ascii=False, indent=2)
    value = completion(field_case, arguments=arguments)
    value.raw_message["tool_calls"][0]["index"] = 0
    value.content = value.raw_message["content"] = "Submitted corrections."
    result, receipt = parse(field_case, value)
    assert result == field_case.corrections
    assert receipt["raw_arguments"] == arguments
    assert receipt["raw_arguments_sha256"] == hashlib.sha256(arguments.encode()).hexdigest()
    assert not receipt["grade_asserted"] and not receipt["physical_custody_verified"]
    assert receipt["tools_executed"] == 0


@pytest.mark.parametrize(
    "kind",
    [
        "length",
        "unknown_finish",
        "multiple",
        "content_only",
        "wrong_tool",
        "raw_args",
        "raw_content",
        "raw_role",
        "legacy",
        "raw_legacy",
        "raw_index",
        "duplicate_json",
        "extra_root",
        "fence",
        "structured_commentary",
        "malformed_root",
        "missing_path",
    ],
)
def test_invalid_native_evidence_rejected(field_case, kind):
    value = completion(field_case)
    if kind in {"length", "unknown_finish"}:
        value.finish_reason = "length" if kind == "length" else None
    elif kind == "multiple":
        value.tool_calls.append(deepcopy(value.tool_calls[0]))
    elif kind == "content_only":
        value.content, value.tool_calls = value.tool_calls[0]["function"]["arguments"], []
    elif kind == "wrong_tool":
        value.tool_calls[0]["function"]["name"] = "submit_patches"
        value.raw_message["tool_calls"] = deepcopy(value.tool_calls)
    elif kind == "raw_args":
        value.raw_message["tool_calls"][0]["function"]["arguments"] += " "
    elif kind == "raw_content":
        value.raw_message["content"] = "conflicting"
    elif kind == "raw_role":
        value.raw_message["role"] = "user"
    elif kind == "legacy":
        value.function_call = {"name": protocol.TOOL_NAME}
    elif kind == "raw_legacy":
        value.raw_message["function_call"] = {"name": protocol.TOOL_NAME}
    elif kind == "raw_index":
        value.raw_message["tool_calls"][0]["index"] = True
    elif kind == "duplicate_json":
        value = completion(field_case, arguments='{"corrections":{},"corrections":{}}')
    elif kind == "extra_root":
        value = completion(
            field_case,
            arguments=canonical({"corrections": field_case.corrections, "approved": True}),
        )
    elif kind == "fence":
        value = completion(field_case, arguments="```json\n{}\n```")
    elif kind == "structured_commentary":
        value.content = value.raw_message["content"] = '{"approved":true}'
    elif kind == "malformed_root":
        value = completion(field_case, arguments="[]")
    elif kind == "missing_path":
        value = completion(field_case, arguments='{"corrections":{}}')
    with pytest.raises(ValueError):
        parse(field_case, value)


def test_full_five_fields_v4_bare_packet_offsets_and_no_old_protocol(field_case):
    system, user, receipt = prompt(field_case)
    logical = protocol.decode_domain_field_prompts(
        system_prompt=system, user_prompt=user, transport_receipt=receipt
    )
    assert set(logical) == {
        "project_brief",
        "candidate_payload",
        "validation_report",
        "correction_contract",
        "source_context",
    }
    assert canonical(logical["candidate_payload"]) == canonical(field_case.candidate)
    assert logical["source_context"]["unknown_paid_attribution"] is None
    assert logical["source_context"]["old_raw"] == {"§0": "§literal", "$ref": 0}
    packet = user[receipt["packet_start"] : receipt["packet_end"]]
    assert decode_lossless_source(json.loads(packet)) == logical
    assert hashlib.sha256(packet.encode()).hexdigest() == receipt["packet_sha256"]
    assert receipt["offset_unit"] == "unicode_code_points"
    assert receipt["prompt_version"] == "3.6-domain-fields"
    assert receipt["prompt_template"] == "planner_outline_domain_field_corrections"
    assert 'Return only {"patches"' not in system
    assert protocol.TOOL_NAME in system and not receipt["grade_asserted"]


@pytest.mark.parametrize(
    "kind",
    [
        "system",
        "user",
        "offset",
        "roles",
        "logicalhash",
        "receiptbound",
        "contract",
        "duplicate_json",
    ],
)
def test_transport_tamper_fails(field_case, kind):
    system, user, receipt = prompt(field_case)
    wire = json.loads(user[len(protocol.MARKER) :])
    if kind == "system":
        system += " "
    elif kind == "user":
        user += " "
    elif kind == "offset":
        receipt["packet_start"] += 1
    elif kind == "roles":
        wire["source_roles"]["validation_report"] = "authoritative"
        user = protocol.MARKER + canonical(wire)
    elif kind == "logicalhash":
        wire["logical_sha256"] = "0" * 64
        user = protocol.MARKER + canonical(wire)
    elif kind == "receiptbound":
        receipt["max_estimated_tokens"] += 1
    elif kind == "contract":
        field_case.contract["candidate_sha256"] = "0" * 64
        with pytest.raises(ValueError, match="binding"):
            prompt(field_case)
        return
    elif kind == "duplicate_json":
        user = protocol.MARKER + '{"schema":"x",' + user[len(protocol.MARKER) + 1 :]
    with pytest.raises(ValueError):
        protocol.decode_domain_field_prompts(
            system_prompt=system, user_prompt=user, transport_receipt=receipt
        )


@pytest.mark.parametrize("bound", [True, False, 0, -1, 1_000_001, "60000", 60000.0, None])
@pytest.mark.parametrize("name", ["max_chars", "max_estimated_tokens"])
def test_capacity_bounds_are_explicit_positive_nonbool_ints(field_case, name, bound):
    with pytest.raises(ValueError, match="invalid"):
        prompt(field_case, **{name: bound})
    system, user, _ = prompt(field_case)
    with pytest.raises(ValueError, match="invalid"):
        protocol.decode_domain_field_prompts(
            system_prompt=system, user_prompt=user, **{name: bound}
        )


def test_explicit_capacity_not_inferred_from_receipt_and_no_truncation(field_case):
    with pytest.raises(SourceBoundPlanningCapacityError):
        prompt(field_case, max_estimated_tokens=100)
    system, user, receipt = prompt(field_case, max_chars=1_000_000, max_estimated_tokens=439650)
    assert receipt["max_chars"] == 1_000_000 and receipt["max_estimated_tokens"] == 439650
    assert not receipt["prompt_capacity"]["truncated"]
    with pytest.raises(ValueError, match="receipt_mismatch"):
        protocol.decode_domain_field_prompts(
            system_prompt=system, user_prompt=user, transport_receipt=receipt
        )
    restored = protocol.decode_domain_field_prompts(
        system_prompt=system,
        user_prompt=user,
        transport_receipt=receipt,
        max_chars=1_000_000,
        max_estimated_tokens=439650,
    )
    assert restored["candidate_payload"] == field_case.candidate


def test_already_object_state_patch_is_not_reclassified_as_representation():
    before, native = native_fixture()
    for patch in native["patches"]:
        if patch["field"] in {"entry_state", "exit_state"}:
            patch["value"] = {"summary": patch["value"]}
    result, receipt = protocol.prepare_domain_candidate(before, native)
    assert receipt["state_wrappers"] == []
    assert receipt["native_patch_sha256"] == receipt["adapted_patch_sha256"]
    assert result != before


def readiness_report(*paths, unknown=False):
    blocking = [
        PlanningReadinessFinding(
            code="UNKNOWN_BLOCKING" if unknown else "PLANNING_SCENE_FIELD_MISSING",
            severity="critical",
            message="Actual missing field diagnostic.",
            path=path,
            blocking=True,
        )
        for path in paths
    ]
    audit = [
        PlanningReadinessFinding(
            code="DECISION_PROTOCOL_INCOMPLETE",
            severity="high",
            message="Advisory only.",
            path="methodology_contract",
            blocking=False,
        )
    ]
    return PlanningReadinessReport.from_findings(
        blocking_findings=blocking, audit_findings=audit
    ).model_dump(mode="json")


def test_all_readiness_blocking_targets_use_actual_storage_no_phantom_field(field_case):
    candidate = deepcopy(field_case.candidate)
    scene = candidate["chapters"][1]["scenes"][0]
    scene.update(signature_image=None, cut_point="", conflict_stakes="opaque direct extra")
    scene["methodology_contract"] = {"conflict_stakes": "", "unknown.键/~": [None, False]}
    report = source_report(candidate)
    readiness = readiness_report(
        "chapters[1].scenes[0].conflict_stakes",
        "chapters[1].scenes[0].signature_image",
        "chapters[1].scenes[0].cut_point",
    )
    combined = protocol.combine_domain_validation_reports(report, readiness)
    assert combined["reports"] == [report, readiness]
    assert len(combined["warnings"]) == 1 and combined["warnings"][0]["blocking"] is False
    _, contract = protocol.tools_for_domain_corrections(candidate, combined, MANIFEST)
    assert len(contract["paths"]) == 11
    storage = "/chapters/1/scenes/0/methodology_contract/conflict_stakes"
    assert storage in contract["paths"]
    assert "/chapters/1/scenes/0/conflict_stakes" not in contract["paths"]
    assert {
        "code": "PLANNING_SCENE_FIELD_MISSING",
        "diagnostic_path": "chapters[1].scenes[0].conflict_stakes",
        "storage_path": storage,
    } in contract["diagnostic_to_storage_paths"]
    corrections = {
        **field_case.corrections,
        storage: "The harbor permit expires at sunset.",
        "/chapters/1/scenes/0/signature_image": "A brass pin rests beside the wet chart.",
        "/chapters/1/scenes/0/cut_point": "The marked landing is now empty.",
    }
    result, receipt = protocol.apply_domain_corrections(
        candidate,
        corrections,
        validation_report=combined,
        identity_manifest=MANIFEST,
        contract=contract,
    )
    revised = result["chapters"][1]["scenes"][0]
    assert revised["conflict_stakes"] == "opaque direct extra"
    assert revised["methodology_contract"]["conflict_stakes"] == corrections[storage]
    assert revised["methodology_contract"]["unknown.键/~"] == [None, False]
    view = ChapterOutlineBatchInput.model_validate(deepcopy(result))
    assert (
        view.chapters[1].scenes[0].methodology_contract["conflict_stakes"] == corrections[storage]
    )
    assert not receipt["grade_asserted"]


@pytest.mark.parametrize(
    "kind",
    [
        "unknown",
        "undeclared_field",
        "badpath",
        "nonblocking",
        "missing_parent",
        "nonnull_stakes",
        "nonnull_cutpoint",
    ],
)
def test_readiness_unknown_or_stale_never_triggers_model_work(field_case, kind):
    candidate = deepcopy(field_case.candidate)
    scene = candidate["chapters"][0]["scenes"][0]
    scene["methodology_contract"] = {"conflict_stakes": ""}
    path = "chapters[0].scenes[0].conflict_stakes"
    if kind == "undeclared_field":
        path = "chapters[0].scenes[0].new_plot_obligation"
    elif kind == "badpath":
        path = "chapters[0].scenes[00].conflict_stakes"
    elif kind == "missing_parent":
        del scene["methodology_contract"]
    elif kind == "nonnull_stakes":
        scene["methodology_contract"]["conflict_stakes"] = "Already concrete."
    elif kind == "nonnull_cutpoint":
        path = "chapters[0].scenes[0].cut_point"
        scene["cut_point"] = "Already concrete."
        scene["methodology_contract"]["cut_point"] = None
    readiness = readiness_report(path, unknown=kind == "unknown")
    if kind == "nonblocking":
        readiness["blocking_findings"][0]["blocking"] = False
    with pytest.raises(ValueError):
        combined = protocol.combine_domain_validation_reports(source_report(candidate), readiness)
        protocol.tools_for_domain_corrections(candidate, combined, MANIFEST)


def test_incomplete_participant_diagnostics_and_typed_metadata_fail(field_case):
    report = deepcopy(field_case.report)
    victim = next(
        i
        for i, row in enumerate(report["violations"])
        if row["location"] == "/chapters/0/scenes/0/participants/1"
    )
    del report["violations"][victim]
    with pytest.raises(ValueError, match="diagnostics_incomplete"):
        protocol.tools_for_domain_corrections(field_case.candidate, report, MANIFEST)
    typed = {
        "gate_name": "chapter_plan_contract",
        "passed": False,
        "violations": [
            {
                "code": "PLAN_SCENE_UNKNOWN_PARTICIPANT",
                "severity": "critical",
                "location": "chapter_outline_batch.chapters[0].scenes[0].participants",
                "metadata": {"participant": "not present", "chapter_number": 1, "scene_number": 1},
            }
        ],
    }
    combined = protocol.combine_domain_validation_reports(field_case.report, typed)
    with pytest.raises(ValueError, match="diagnostic_stale"):
        protocol.tools_for_domain_corrections(field_case.candidate, combined, MANIFEST)


def test_nullable_raw_message_is_honestly_missing_not_fabricated(field_case):
    value = completion(field_case)
    value.raw_message = None
    corrections, receipt = parse(field_case, value)
    assert corrections == field_case.corrections
    assert not receipt["raw_message_present"] and receipt["raw_message_status"] == "missing"
    assert receipt["raw_message_sha256"] == digest(None)
    assert not receipt["physical_custody_verified"] and not receipt["grade_asserted"]


def absent_leaf_case(field):
    candidate = source()
    scene = candidate["chapters"][0]["scenes"][0]
    scene["methodology_contract"] = {}
    scene["unrelated"] = {"keep_null": None, "typed": [False, 1, 1.0], "unknown/~": {"$ref": 0}}
    parent = scene["methodology_contract"] if field == "conflict_stakes" else scene
    parent.pop(field, None)
    path = "/chapters/0/scenes/0/"
    path += "methodology_contract/" if field == "conflict_stakes" else ""
    path += field
    report = protocol.combine_domain_validation_reports(
        readiness_report("chapters[0].scenes[0]." + field)
    )
    return candidate, report, path


@pytest.mark.parametrize("field", ["conflict_stakes", "signature_image", "cut_point"])
def test_reported_absent_leaf_with_existing_parent_adds_only_exact_target(field):
    candidate, report, path = absent_leaf_case(field)
    before = deepcopy(candidate)
    _, contract = protocol.tools_for_domain_corrections(candidate, report, MANIFEST)
    assert contract["paths"] == [path]
    target = contract["targets"][0]
    assert target["before_present"] is False
    assert target["before_sha256"] != digest(None)
    corrections = {path: "  Exact new model-authored field value.\n"}
    result, receipt = protocol.apply_domain_corrections(
        candidate,
        corrections,
        validation_report=report,
        identity_manifest=MANIFEST,
        contract=contract,
    )
    assert candidate == before and protocol._at(result, path) == corrections[path]
    restored = deepcopy(result)
    scene = restored["chapters"][0]["scenes"][0]
    parent = scene["methodology_contract"] if field == "conflict_stakes" else scene
    del parent[field]
    assert canonical(restored) == canonical(before)
    assert receipt["added_paths"] == [path] and receipt["absent_leaf_inverse_verified"]
    assert receipt["non_targets_unchanged"] and not receipt["grade_asserted"]
    with pytest.raises(ValueError):
        protocol.apply_domain_corrections(
            candidate,
            {**corrections, "/chapters/0/scenes/0/unrelated": {}},
            validation_report=report,
            identity_manifest=MANIFEST,
            contract=contract,
        )
    system, user, transport = protocol.build_domain_field_prompts(
        project_brief={},
        candidate=candidate,
        validation_report=report,
        contract=contract,
        source_context={"unknown": None},
    )
    decoded = protocol.decode_domain_field_prompts(
        system_prompt=system,
        user_prompt=user,
        transport_receipt=transport,
    )
    assert decoded["correction_contract"] == contract and decoded["candidate_payload"] == before


@pytest.mark.parametrize("field", ["conflict_stakes", "signature_image", "cut_point"])
@pytest.mark.parametrize("added", [None, "", "Already added externally."])
def test_absent_contract_cannot_replay_after_leaf_was_added(field, added):
    candidate, report, path = absent_leaf_case(field)
    _, contract = protocol.tools_for_domain_corrections(candidate, report, MANIFEST)
    fresh = deepcopy(candidate)
    scene = fresh["chapters"][0]["scenes"][0]
    parent = scene["methodology_contract"] if field == "conflict_stakes" else scene
    parent[field] = added
    before = deepcopy(fresh)
    if added is None:
        _, null_contract = protocol.tools_for_domain_corrections(fresh, report, MANIFEST)
        assert null_contract["targets"][0].get("before_present", True) is True
        assert null_contract["targets"][0]["before_sha256"] == digest(None)
        assert (
            null_contract["targets"][0]["before_sha256"] != contract["targets"][0]["before_sha256"]
        )
    with pytest.raises(ValueError):
        protocol.apply_domain_corrections(
            fresh,
            {path: "New field text."},
            validation_report=report,
            identity_manifest=MANIFEST,
            contract=contract,
        )
    assert fresh == before


@pytest.mark.parametrize("parent_value", [None, [], "", False, "missing"])
def test_absent_leaf_never_invents_or_replaces_parent(parent_value):
    candidate, report, _ = absent_leaf_case("conflict_stakes")
    scene = candidate["chapters"][0]["scenes"][0]
    if parent_value == "missing":
        del scene["methodology_contract"]
    else:
        scene["methodology_contract"] = parent_value
    before = deepcopy(candidate)
    with pytest.raises(ValueError):
        protocol.tools_for_domain_corrections(candidate, report, MANIFEST)
    assert candidate == before
