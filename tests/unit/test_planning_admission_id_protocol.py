"""Pure prospective v5 submission proofs; no database, gateway or provider."""

from copy import deepcopy
import hashlib
import json
import socket
from uuid import uuid4

from jsonschema import Draft202012Validator
import pytest

from bestseller.services import llm
from bestseller.services import planning_admission_id_protocol as ids
from bestseller.services import planning_admission_ref_protocol as v4
from bestseller.services import planning_draft_admission as admission

pytestmark = pytest.mark.unit
EVIDENCE = "/candidate_payload/chapters/0/scenes/0/action_sequence/0"
WORLD = "/approved_world_spec/requirement"


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Pure v5 tests forbid DB/network/provider dispatch")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)


def base(*, theme="ice", language="en"):
    candidate = {
        "chapters": [
            {
                "chapter_number": 3,
                "title": theme,
                "scenes": [
                    {
                        "entry_state": "The seal was shut",
                        "action_sequence": ["Mira opens the seal without touch"],
                        "unknown_authored": {
                            "中文.键/~": "§literal $ref preserved",
                            "null": None,
                            "empty": "",
                            "false": False,
                            "zero": 0,
                        },
                        "_meta": {"raw": "opaque source audit"},
                    }
                ],
            },
            {
                "chapter_number": 4,
                "title": "Following observation",
                "scenes": [
                    {
                        "action_sequence": ["The observer returns home"],
                    }
                ],
            },
        ],
        "_meta": {"retained": [False, None, {"raw": "untrusted old history"}]},
    }
    result = {
        "schema": admission.SCHEMA,
        "hard_admission_policy": admission.HARD_ADMISSION_POLICY,
        "project_id": str(uuid4()),
        "workflow_run_id": str(uuid4()),
        "language": language,
        "candidate_payload": candidate,
        "projection_audit_targets": admission.projection_audit_targets(candidate),
        "approved_creation_contracts": {"selected_clause": "Preserve the agreed boundary"},
        "approved_world_spec": {
            "requirement": "Touch is required before opening the seal",
            "unknown": None,
        },
        "approved_cast_spec": {
            "people": [{"name": "Mira", "arc_trajectory": "An optional suggestion"}]
        },
        "approved_book_spec": {
            "logline": "An unrelated journey",
            "_meta": {"raw": "untrusted technical audit"},
        },
        "untrusted_commercial_report": {
            "pass": False,
            "raw": "Do not reinterpret this old rejection",
        },
    }
    result["source_authority"] = admission._source_authority(result)
    result["source_authority_allowed_codes"] = v4._permissions(result["source_authority"])
    return result


def ref(current, pointer):
    return next(
        key
        for key, row in current["source_reference_catalog"]["catalog"].items()
        if row["pointer"] == pointer
    )


def payload(current, *, passed=False, code="ABILITY_RULE_CONFLICT"):
    sources = {
        "ABILITY_RULE_CONFLICT": (WORLD, "Touch is required"),
        "IDENTITY_CONFLICT": ("/approved_cast_spec/people/0/name", "Mira"),
        "EXPLICIT_CREATION_REQUIREMENT_CONFLICT": (
            "/approved_creation_contracts/selected_clause",
            "Preserve the agreed boundary",
        ),
        "CONTINUITY_CONFLICT": (
            "/candidate_payload/chapters/0/scenes/0/entry_state",
            "The seal was shut",
        ),
        "SCENE_NOT_EXECUTABLE": (WORLD, "Touch is required"),
    }
    pointer, requirement = sources[code]
    scope = "chapters[0].scenes[0]"
    return {
        "draft_eligible": passed,
        "hard_violations": []
        if passed
        else [
            {
                "code": code,
                "path": scope,
                "evidence": "opens the seal without touch",
                "source_requirement": requirement,
                "source_refs": [ref(current, pointer)],
                "evidence_refs": [ref(current, EVIDENCE)],
            }
        ],
        "editorial_notes": ["Untrusted aesthetic preference stays editorial"],
        "projection_checks": [
            {
                **target,
                "status": "conflict"
                if not passed and target["path"] in {"chapters[0]", scope}
                else "consistent",
                "explanation": "Full ordered projections inspected, not a score",
                **(
                    {"related_violation_paths": [scope]}
                    if not passed and target["path"] in {"chapters[0]", scope}
                    else {}
                ),
            }
            for target in current["projection_audit_targets"]
        ],
    }


def completion(value):
    raw = value if type(value) is str else json.dumps(value, ensure_ascii=False, indent=2)
    calls = [
        {
            "id": "new-v5-native",
            "type": "function",
            "function": {"name": "submit_planning_admission", "arguments": raw},
        }
    ]
    return {
        "content": "",
        "finish_reason": "tool_calls",
        "function_call": None,
        "tool_calls": calls,
        "raw_message": {
            "role": "assistant",
            "content": None,
            "tool_calls": deepcopy(calls),
            "reasoning_content": "Never read this as a submission",
        },
    }


def parse(current, value=None, *, returned=None, contract=None):
    return ids.parse_reference_submission(
        returned or completion(value or payload(current)),
        supplied=current,
        contract=contract or ids.reference_contract(current),
    )


@pytest.mark.parametrize(
    "theme,language", [("glacier", "en"), ("海上救援", "zh-CN"), ("station mystery", "fr")]
)
@pytest.mark.parametrize("passed", [False, True])
def test_native_grade_and_complete_derived_custody_are_lossless(theme, language, passed):
    old = base(theme=theme, language=language)
    before = deepcopy(old)
    current = ids.v5_input(old)
    current_before = deepcopy(current)
    native = payload(current, passed=passed)
    returned = completion(native)
    normalized_raw, receipt = parse(current, returned=returned)
    expected = deepcopy(native)
    if not passed:
        expected["hard_violations"][0].pop("evidence_refs")
        expected["hard_violations"][0]["source_refs"] = [WORLD]
    assert json.loads(normalized_raw) == expected == receipt["normalized_payload"]
    assert (
        admission._grade(normalized_raw, current["candidate_payload"], current).draft_eligible
        is passed
    )
    assert receipt["original_payload"] == native
    assert receipt["raw_arguments"] == returned["tool_calls"][0]["function"]["arguments"]
    assert (
        receipt["raw_arguments_sha256"]
        == hashlib.sha256(receipt["raw_arguments"].encode()).hexdigest()
    )
    assert (
        receipt["normalized_raw_grade_sha256"]
        == hashlib.sha256(normalized_raw.encode()).hexdigest()
    )
    assert receipt["physical_submission"]["parsed_payload"] == native
    assert receipt["reference_contract"] == ids.reference_contract(current)
    assert (
        receipt["new_grade_or_adoption"] is False and receipt["old_receipts_reinterpreted"] is False
    )
    assert receipt["issues_dropped"] == 0 and receipt["text_modified"] is False
    if not passed:
        rows = receipt["resolved_violations"][0]
        assert rows["evidence_refs"][0]["value"] == "Mira opens the seal without touch"
        assert rows["source_refs"][0]["value"] == old["approved_world_spec"]["requirement"]
        for kind in ("evidence_refs", "source_refs"):
            for row in rows[kind]:
                assert row["value_sha256"] == ids._sha(row["value"])
    assert old == before and current == current_before
    for key in old.keys() - {
        "schema",
        "hard_admission_policy",
        "source_authority",
        "source_authority_allowed_codes",
    }:
        assert current[key] == old[key]


def test_ids_are_a_bijection_and_typed_unknown_values_and_unicode_keys_survive():
    current = ids.v5_input(base())
    contract = ids.reference_contract(current)
    catalog = contract["catalog"]
    assert list(catalog) == [f"r{i:06d}" for i in range(1, len(catalog) + 1)]
    assert len({row["pointer"] for row in catalog.values()}) == len(catalog)
    escaped = "/candidate_payload/chapters/0/scenes/0/unknown_authored/中文.键~1~0"
    row = catalog[ref(current, escaped)]
    assert row["role"] == "candidate_order"
    assert row["value_sha256"] == ids._sha("§literal $ref preserved")
    prefix = "/candidate_payload/chapters/0/scenes/0/unknown_authored/"
    assert (
        catalog[ref(current, prefix + "false")]["value_sha256"]
        != catalog[ref(current, prefix + "zero")]["value_sha256"]
    )
    reordered = {key: deepcopy(value) for key, value in reversed(list(current.items()))}
    assert ids.reference_contract(reordered) == contract


@pytest.mark.parametrize("code", sorted(admission._SOURCE_ROLES_BY_CODE))
def test_code_discriminated_tools_and_parser_keep_original_role_policy(code):
    current = ids.v5_input(base())
    tool, contract = ids.reference_tool(admission._Grade.model_json_schema(), current)
    assert contract == ids.reference_contract(current)
    branches = tool["function"]["parameters"]["properties"]["hard_violations"]["items"]["oneOf"]
    branch = next(branch for branch in branches if branch["properties"]["code"]["enum"] == [code])
    assert (
        set(branch["required"]) == set(branch["properties"])
        and branch["additionalProperties"] is False
    )
    allowed = branch["properties"]["source_refs"]["items"]["enum"]
    assert set(allowed) == {
        key for key, row in contract["catalog"].items() if code in row["allowed_codes"]
    }
    witness = branch["properties"]["evidence_refs"]["items"]["enum"]
    assert all(contract["catalog"][key]["role"] == "candidate_order" for key in witness)
    assert (ref(current, EVIDENCE) in allowed) is (
        code in {"CONTINUITY_CONFLICT", "SCENE_NOT_EXECUTABLE"}
    )
    native = payload(current, code=code)
    Draft202012Validator.check_schema(tool["function"]["parameters"])
    Draft202012Validator(tool["function"]["parameters"]).validate(native)
    assert json.loads(parse(current, native)[0])["hard_violations"][0]["code"] == code
    assert "Mira opens" not in json.dumps(tool) and "Touch is required" not in json.dumps(tool)


def test_cross_scope_candidate_evidence_is_retained_with_a_local_witness():
    current = ids.v5_input(base())
    native = payload(current)
    extra = ref(current, "/candidate_payload/chapters/1/scenes/0/action_sequence/0")
    native["hard_violations"][0]["evidence_refs"].append(extra)
    _, receipt = parse(current, native)
    assert [row["id"] for row in receipt["resolved_violations"][0]["evidence_refs"]] == native[
        "hard_violations"
    ][0]["evidence_refs"]
    assert (
        receipt["resolved_violations"][0]["evidence_refs"][1]["value"]
        == "The observer returns home"
    )


def test_unicode_dot_slash_and_tilde_leaf_is_submitted_only_by_its_exact_id():
    current = ids.v5_input(base())
    native = payload(current)
    pointer = "/candidate_payload/chapters/0/scenes/0/unknown_authored/中文.键~1~0"
    native["hard_violations"][0]["evidence_refs"] = [ref(current, pointer)]
    native["hard_violations"][0]["evidence"] = "§literal $ref preserved"
    _, receipt = parse(current, native)
    witness = receipt["resolved_violations"][0]["evidence_refs"][0]
    assert witness["pointer"] == pointer and witness["value"] == "§literal $ref preserved"


@pytest.mark.parametrize(
    "code,allowed", [("CONTINUITY_CONFLICT", True), ("ABILITY_RULE_CONFLICT", False)]
)
def test_prior_occurrence_keeps_its_original_code_permissions(code, allowed):
    old = base()
    old["adopted_continuity"] = {
        "recent_adopted_prose": [{"body": "The seal was closed yesterday"}]
    }
    old["source_authority"] = admission._source_authority(old)
    old["source_authority_allowed_codes"] = v4._permissions(old["source_authority"])
    current = ids.v5_input(old)
    native = payload(current, code=code)
    native["hard_violations"][0]["source_refs"] = [
        ref(current, "/adopted_continuity/recent_adopted_prose/0/body")
    ]
    native["hard_violations"][0]["source_requirement"] = "closed yesterday"
    if allowed:
        parse(current, native)
    else:
        with pytest.raises(ValueError, match="source_authority"):
            parse(current, native)


def test_native_tool_schema_itself_rejects_candidate_as_ability_authority():
    current = ids.v5_input(base())
    tool, _ = ids.reference_tool(admission._Grade.model_json_schema(), current)
    native = payload(current)
    native["hard_violations"][0]["source_refs"].append(ref(current, EVIDENCE))
    assert list(Draft202012Validator(tool["function"]["parameters"]).iter_errors(native))
    with pytest.raises(ValueError) as error:
        parse(current, native)
    assert error.value.receipt["status"] == "rejected"
    assert "normalized_payload" not in error.value.receipt


@pytest.mark.parametrize("field", ["source_refs", "evidence_refs"])
@pytest.mark.parametrize(
    "bad",
    [None, "", [], ["r999999"], ["R000001"], [EVIDENCE], ["candidate:chapters[0].payoff"], [True]],
)
def test_missing_unknown_alias_or_wrong_type_reference_rejects_entire_submission(field, bad):
    current = ids.v5_input(base())
    native = payload(current)
    native["hard_violations"][0][field] = bad
    with pytest.raises(ValueError):
        parse(current, native)


@pytest.mark.parametrize("field", ["source_refs", "evidence_refs"])
def test_duplicate_ids_are_rejected(field):
    current = ids.v5_input(base())
    native = payload(current)
    native["hard_violations"][0][field] *= 2
    with pytest.raises(ValueError, match="duplicate_reference"):
        parse(current, native)


@pytest.mark.parametrize(
    "field", ["source_refs", "evidence_refs", "evidence", "source_requirement"]
)
def test_all_native_violation_fields_are_required(field):
    current = ids.v5_input(base())
    native = payload(current)
    del native["hard_violations"][0][field]
    with pytest.raises(ValueError):
        parse(current, native)


@pytest.mark.parametrize(
    "pointer",
    [
        EVIDENCE,
        "/approved_book_spec/logline",
        "/approved_cast_spec/people/0/arc_trajectory",
        "/approved_book_spec/_meta/raw",
        "/candidate_payload/chapters/0/scenes/0/unknown_authored/null",
        "/candidate_payload/chapters/0/scenes/0/unknown_authored/empty",
    ],
)
def test_ability_source_rejects_candidate_advisory_provenance_and_unasserted(pointer):
    current = ids.v5_input(base())
    native = payload(current)
    native["hard_violations"][0]["source_refs"].append(ref(current, pointer))
    with pytest.raises(ValueError, match="source_authority"):
        parse(current, native)


@pytest.mark.parametrize(
    "pointer",
    [
        WORLD,
        "/candidate_payload/chapters/0/scenes/0/_meta/raw",
        "/candidate_payload/chapters/0/scenes/0/unknown_authored/null",
        "/candidate_payload/chapters/0/scenes/0/unknown_authored/empty",
    ],
)
def test_evidence_rejects_non_candidate_or_unknown_technical_values(pointer):
    current = ids.v5_input(base())
    native = payload(current)
    native["hard_violations"][0]["evidence_refs"].append(ref(current, pointer))
    with pytest.raises(ValueError, match="candidate_evidence_role"):
        parse(current, native)


@pytest.mark.parametrize(
    "fault",
    ["nonlocal", "evidence_quote", "source_quote", "scope_alias", "extra_violation", "one_bad_id"],
)
def test_invalid_binding_never_yields_a_partial_grade(fault):
    current = ids.v5_input(base())
    native = payload(current)
    item = native["hard_violations"][0]
    if fault == "nonlocal":
        item["evidence_refs"] = [
            ref(current, "/candidate_payload/chapters/1/scenes/0/action_sequence/0")
        ]
    elif fault == "evidence_quote":
        item["evidence"] = "A fabricated description"
    elif fault == "source_quote":
        item["source_requirement"] = "A new unauthorized ban"
    elif fault == "scope_alias":
        item["path"] = "chapters[00].scenes[0]"
    elif fault == "extra_violation":
        item["unknown"] = None
    else:
        native["hard_violations"].append(deepcopy(item))
        native["hard_violations"][1]["evidence_refs"].append("r999999")
    with pytest.raises(ValueError):
        parse(current, native)


@pytest.mark.parametrize(
    "fault",
    [
        "project",
        "workflow",
        "candidate",
        "foundation",
        "authority",
        "catalog",
        "policy",
        "contract",
    ],
)
def test_current_input_and_all_catalog_bindings_must_match(fault):
    current = ids.v5_input(base())
    native = payload(current)
    contract = ids.reference_contract(current)
    if fault in {"project", "workflow"}:
        current[fault + "_id"] = str(uuid4())
    elif fault == "candidate":
        current["candidate_payload"]["chapters"][0]["title"] += " changed"
    elif fault == "foundation":
        current["approved_world_spec"]["requirement"] += " changed"
    elif fault == "authority":
        current["source_authority"]["canonical_author"] = {}
    elif fault == "catalog":
        current["source_reference_catalog"]["catalog"][ref(current, WORLD)]["role"] = (
            "canonical_author"
        )
    elif fault == "policy":
        current["hard_admission_policy"] = v4.POLICY
    else:
        contract["input_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        parse(current, native, contract=contract)


def test_new_semantic_input_requires_a_new_matching_catalog_not_an_old_contract():
    old = base()
    first = ids.v5_input(old)
    changed = deepcopy(old)
    changed["candidate_payload"]["chapters"][0]["scenes"][0]["action_sequence"][0] += (
        " after a new decision"
    )
    changed["projection_audit_targets"] = admission.projection_audit_targets(
        changed["candidate_payload"]
    )
    second = ids.v5_input(changed)
    assert (
        first["source_reference_catalog"]["candidate_sha256"]
        != second["source_reference_catalog"]["candidate_sha256"]
    )
    with pytest.raises(ValueError, match="stale_contract"):
        parse(second, contract=ids.reference_contract(first))
    parse(second)


@pytest.mark.parametrize(
    "fault",
    [
        "no_native",
        "two_calls",
        "wrong_tool",
        "raw_args",
        "raw_content",
        "raw_role",
        "legacy_function",
        "structured_content",
        "length",
        "duplicate_json",
    ],
)
def test_native_boundary_and_conflicting_physical_evidence_are_strict(fault):
    current = ids.v5_input(base())
    returned = completion(payload(current))
    if fault == "no_native":
        returned["content"] = returned["tool_calls"][0]["function"]["arguments"]
        returned["tool_calls"] = None
        returned["raw_message"] = None
    elif fault == "two_calls":
        returned["tool_calls"] *= 2
    elif fault == "wrong_tool":
        returned["tool_calls"][0]["function"]["name"] = "another_tool"
        returned["raw_message"]["tool_calls"] = deepcopy(returned["tool_calls"])
    elif fault == "raw_args":
        returned["raw_message"]["tool_calls"][0]["function"]["arguments"] = "{}"
    elif fault == "raw_content":
        returned["raw_message"]["content"] = "competing content"
    elif fault == "raw_role":
        returned["raw_message"]["role"] = "user"
    elif fault == "legacy_function":
        returned["function_call"] = {"name": "submit_planning_admission", "arguments": "{}"}
    elif fault == "structured_content":
        returned["content"] = "{}"
        returned["raw_message"]["content"] = "{}"
    elif fault == "length":
        returned["finish_reason"] = "length"
    else:
        raw = returned["tool_calls"][0]["function"]["arguments"].replace(
            '"draft_eligible": false', '"draft_eligible": false, "draft_eligible": false'
        )
        returned = completion(raw)
    with pytest.raises(ValueError):
        parse(current, returned=returned)


def test_honest_missing_raw_message_and_plain_commentary_preserve_native_return():
    current = ids.v5_input(base())
    returned = completion(payload(current))
    returned["raw_message"] = None
    returned["content"] = "Native arguments contain the complete submission."
    _, receipt = parse(current, returned=returned)
    assert receipt["physical_submission"]["raw_message_status"] == "missing"
    assert receipt["physical_submission"]["original_content"] == returned["content"]


@pytest.mark.parametrize(
    "fault", ["coverage", "projection_hash", "inconsistent_grade", "related_path", "extra_root"]
)
def test_original_grade_invariants_remain_mandatory(fault):
    current = ids.v5_input(base())
    native = payload(current)
    if fault == "coverage":
        native["projection_checks"].pop()
    elif fault == "projection_hash":
        native["projection_checks"][0]["projection_hash"] = "f" * 64
    elif fault == "inconsistent_grade":
        native["draft_eligible"] = True
    elif fault == "related_path":
        native["projection_checks"][0]["related_violation_paths"] = ["chapters[99]"]
    else:
        native["unknown"] = None
    with pytest.raises(ValueError):
        parse(current, native)


def test_legacy_protocols_and_historical_unknown_are_not_accepted_or_modified():
    old = base()
    legacy = v4.v4_input(old)
    before = deepcopy(legacy)
    current = ids.v5_input(old)
    assert ids.SCHEMA == "planning-draft-admission.v5"
    assert ids.POLICY != v4.POLICY != admission.HARD_ADMISSION_POLICY
    with pytest.raises(ValueError, match="explicit_opt_in_required"):
        ids.reference_contract(legacy)
    with pytest.raises(ValueError, match="base_schema"):
        ids.v5_input(legacy)
    with pytest.raises(ValueError):
        v4.parse_reference_submission(
            completion(payload(current)), supplied=current, contract=ids.reference_contract(current)
        )
    legacy_native = payload(current)
    legacy_native["hard_violations"][0].pop("evidence_refs")
    legacy_native["hard_violations"][0]["source_refs"] = [WORLD, EVIDENCE]
    with pytest.raises(ValueError):
        parse(current, legacy_native)
    assert legacy == before


def test_tools_refuse_replaced_grade_schema():
    current = ids.v5_input(base())
    altered = admission._Grade.model_json_schema()
    altered["properties"].pop("projection_checks")
    with pytest.raises(ValueError, match="grade_schema_changed"):
        ids.reference_tool(altered, current)


def test_feedback_collects_late_mixed_roles_bad_ids_scope_and_both_quotes_once():
    current = ids.v5_input(base())
    native = payload(current)
    first = native["hard_violations"][0]
    first["source_refs"] += [ref(current, EVIDENCE), "r999999"]
    first["source_requirement"] = "Invented source requirement"
    first["evidence_refs"].append(
        ref(current, "/candidate_payload/chapters/0/scenes/0/unknown_authored/null")
    )
    first["evidence"] = "Invented candidate evidence"
    second = deepcopy(first)
    second["code"] = "CONTINUITY_CONFLICT"
    second["source_refs"] = [ref(current, WORLD), ref(current, EVIDENCE), "r999998"]
    second["source_requirement"] = "opens the seal"
    second["evidence_refs"] = [
        ref(current, "/candidate_payload/chapters/1/scenes/0/action_sequence/0")
    ]
    native["hard_violations"].append(second)
    returned = completion(native)
    before = deepcopy((current, returned))
    feedback = ids.protocol_feedback(returned, current)
    context, *issues = feedback
    assert context["code"] == "current_reference_id_context"
    positions = {tuple(row["loc"]): row["code"] for row in issues}
    assert positions == {
        ("hard_violations", 0, "source_refs", 1): "source_authority",
        ("hard_violations", 0, "source_refs", 2): "reference_not_in_catalog",
        ("hard_violations", 0, "evidence_refs", 1): "candidate_evidence_role",
        ("hard_violations", 0, "source_requirement"): "source_requirement_unbound",
        ("hard_violations", 0, "evidence"): "candidate_evidence_unbound",
        ("hard_violations", 1, "source_refs", 0): "source_authority",
        ("hard_violations", 1, "source_refs", 2): "reference_not_in_catalog",
        ("hard_violations", 1, "evidence_refs"): "candidate_evidence_scope",
    }
    allowed = context["allowed_id_sets"]["source:ABILITY_RULE_CONFLICT"]
    assert allowed["values"] == [
        key
        for key, row in ids.reference_contract(current)["catalog"].items()
        if "ABILITY_RULE_CONFLICT" in row["allowed_codes"]
    ]
    assert allowed["sha256"] == ids._sha(allowed["values"])
    for row in issues:
        assert (
            row["diagnostic_only"] is True
            and row["auto_fix"] is False
            and row["grade_asserted"] is False
        )
        assert (
            row["allowed_ids_sha256"] == context["allowed_id_sets"][row["allowed_id_set"]]["sha256"]
        )
    actual = next(
        row["actual_binding"] for row in issues if row.get("value") == ref(current, EVIDENCE)
    )
    assert actual["pointer"] == EVIDENCE and actual["value"] == "Mira opens the seal without touch"
    assert actual["value_sha256"] == ids._sha(actual["value"])
    assert (current, returned) == before


def test_feedback_missing_evidence_has_exact_loc_and_full_current_id_set():
    current = ids.v5_input(base())
    native = payload(current)
    del native["hard_violations"][0]["evidence_refs"]
    context, issue = ids.protocol_feedback(completion(native), current)
    assert (
        issue["loc"] == ["hard_violations", 0, "evidence_refs"] and issue["code"] == "missing_field"
    )
    allowed = context["allowed_id_sets"][issue["allowed_id_set"]]
    assert allowed["values"] == [
        key
        for key, row in ids.reference_contract(current)["catalog"].items()
        if row["role"] == "candidate_order"
    ]
    assert issue["allowed_ids_sha256"] == ids._sha(allowed["values"])


@pytest.mark.parametrize("fault", ["raw_conflict", "duplicate_json", "no_native", "length"])
def test_feedback_respects_native_and_strict_json_before_any_leaf_analysis(fault):
    current = ids.v5_input(base())
    native = payload(current)
    native["hard_violations"][0]["source_refs"] = ["r999999"]
    returned = completion(native)
    if fault == "raw_conflict":
        returned["raw_message"]["tool_calls"][0]["function"]["arguments"] = "{}"
    elif fault == "duplicate_json":
        raw = returned["tool_calls"][0]["function"]["arguments"]
        returned = completion(
            raw.replace(
                '"draft_eligible": false', '"draft_eligible": false, "draft_eligible": false'
            )
        )
    elif fault == "no_native":
        returned["tool_calls"] = None
    else:
        returned["finish_reason"] = "length"
    _, issue = ids.protocol_feedback(returned, current)
    assert issue["code"] in {
        "native_submission_invalid",
        "native_submission_required",
        "incomplete_completion",
    }
    assert "actual_binding" not in issue and "reference_not_in_catalog" not in str(issue)


@pytest.mark.parametrize("passed", [True, False])
def test_valid_submission_has_no_repair_feedback_or_grade_mutation(passed):
    current = ids.v5_input(base())
    returned = completion(payload(current, passed=passed))
    before = deepcopy((current, returned))
    assert ids.protocol_feedback(returned, current) == []
    assert (current, returned) == before


def test_feedback_keeps_wrong_role_false_value_distinct_from_numeric_zero():
    current = ids.v5_input(base())
    native = payload(current)
    pointer = "/candidate_payload/chapters/0/scenes/0/unknown_authored/false"
    native["hard_violations"][0]["source_refs"] = [ref(current, pointer)]
    _, issue = ids.protocol_feedback(completion(native), current)
    assert issue["actual_binding"]["value"] is False
    assert issue["actual_binding"]["value_sha256"] == ids._sha(False) != ids._sha(0)


def test_feedback_never_resolves_pointer_alias_or_reuses_v4_authority_diagnostics():
    current = ids.v5_input(base())
    native = payload(current)
    native["hard_violations"][0]["source_refs"] = [WORLD]
    _, issue = ids.protocol_feedback(completion(native), current)
    assert issue["code"] == "reference_not_in_catalog" and issue["value"] == WORLD
    assert "actual_binding" not in issue and "allowed_authority_prefixes" not in issue


@pytest.mark.parametrize(
    "fault",
    [
        "unknown_code",
        "malformed_code",
        "root_extra",
        "missing_source",
        "invalid_catalog",
        "projection",
    ],
)
def test_feedback_other_strict_boundaries_remain_non_authorizing(fault):
    current = ids.v5_input(base())
    native = payload(current)
    if fault == "unknown_code":
        native["hard_violations"][0]["code"] = "NEW_HARD_CATEGORY"
    elif fault == "malformed_code":
        native["hard_violations"][0]["code"] = []
    elif fault == "root_extra":
        native["unexpected"] = None
    elif fault == "missing_source":
        del native["hard_violations"][0]["source_refs"]
    elif fault == "invalid_catalog":
        current["source_reference_catalog"]["policy"] = "invalid"
    else:
        native["projection_checks"][0]["projection_hash"] = "0" * 64
    feedback = ids.protocol_feedback(completion(native), current)
    assert feedback and all(row["grade_asserted"] is False for row in feedback)
