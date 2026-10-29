"""Main-run RED: three bounded first-chapter authoring contracts, no execution.

1. planner_book_spec consumes its exact physical-slot material, without filling
   required fields from story/schema. The native asset_json tool stays intact.
2. Its identity is checked against the current native projection, not private
   subject metadata. Native projection references remain local source data.
3. Source review reuses decode_source_review_wire. The proposed template-local
   $source_review selector accepts only the two echoed hashes or an explicitly
   matched catalog row's quote_id. No generic view, index, fallback or verdict
   binding is proposed. Zero/multiple matches fail with distinct fixed codes.

Only structural tokens are committed. Fixtures are built in memory when main
runs this file; no private manuscript, provider, DB, HTTP or app run is used.
Failures expose field/code/type/length/SHA and exception chains, not raw data.
These tests specify a prospective interface; implementation remains frozen.
"""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
import unittest
from uuid import uuid4

from scripts.full_flow.simulation.contracts import (
    ScriptContractError,
    TemplateCatalog,
    canonical,
    digest,
)
from scripts.full_flow.simulation.inventory import INVENTORY_SCHEMA
from scripts.full_flow.simulation.private_config import PrivateConfigResolver
from scripts.full_flow.simulation.quickstart_builders import (
    BuilderContext,
    build_payload,
    build_response,
    decode_source_review_wire,
)
from scripts.full_flow.simulation.wire_material import pointer

from .test_keyflow_protocols import native_source_review_case, render_source_review_wire
from .test_quickstart_builders import blueprint

BOOK = "planner_book_spec"
REVIEW = "planner_source_bound_expansion_semantic_review"
REQUIRED = (
    "logline", "theme_statement", "dramatic_question", "protagonist.name",
    "protagonist.external_goal", "series_engine.core_serial_engine",
)
IDENTITY = ("name", "gender", "pronoun_set_zh", "pronoun_set_en")
VERSIONS = ("4.2-stable-catalog", "4.3-field-presence")
# Existing decoder coverage is tested elsewhere; only representative binding
# carriers are repeated here, not a new transport/fault matrix.
CARRIERS = ("literal", "lossless-v3", "catalog-v2", "string-pool")
ORDINAL = 173


def summary(value):
    raw = canonical(value).encode()
    return {
        "type": type(value).__name__,
        "length": len(value) if isinstance(value, (dict, list, str)) else None,
        "utf8_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def exception_chain(error):
    rows, seen = [], set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        rows.append({
            "type": type(error).__name__,
            "code": error.code if isinstance(error, ScriptContractError) else "Unknown",
            "message": summary(str(error)),
        })
        error = error.__cause__ or error.__context__
    return rows


class OperandAssertions(unittest.TestCase):
    def equal_operand(self, template, field, expected, actual):
        if canonical(actual) != canonical(expected):
            self.fail(canonical({
                "template": template, "field": field,
                "expected": summary(expected), "actual": summary(actual),
            }))

    def returns(self, template, field, expected, invoke):
        try:
            actual = invoke()
        except Exception as error:
            raise AssertionError(canonical({
                "template": template, "field": field, "expected": summary(expected),
                "actual": {"type": "exception", "chain": exception_chain(error)},
            })) from error
        self.equal_operand(template, field, expected, actual)
        return actual

    def rejects(self, template, field, code, expected_operand, actual_operand, invoke):
        try:
            result = invoke()
        except ScriptContractError as error:
            if error.code == code:
                return
            actual = {"code": error.code, "chain": exception_chain(error)}
            cause = error
        except Exception as error:
            actual = {"code": "Unknown", "chain": exception_chain(error)}
            cause = error
        else:
            actual = {"code": "NO_EXCEPTION", "returned": summary(result)}
            cause = None
        raise AssertionError(canonical({
            "template": template, "field": field, "expected_code": code,
            "expected": summary(expected_operand), "actual": summary(actual_operand),
            "observed": actual,
        })) from cause


def authored_book():
    # Literal authored fields, never a schema walk or a quality assertion.
    return {
        "logline": "authored-logline-token",
        "theme_statement": "authored-theme-token",
        "dramatic_question": "authored-question-token",
        "protagonist": {
            "name": "authored-actor-token", "external_goal": "authored-goal-token",
            "gender": "female", "pronoun_set_zh": "她", "pronoun_set_en": "she/her",
            "secret": "",
        },
        "series_engine": {
            "core_serial_engine": "authored-engine-token", "effect_skills": [],
        },
        "expected_character_count": 3,
        "naming_pool": ["authored-actor-token", "authored-relative-A", "authored-relative-B"],
        "reader_promise": "authored-promise-token",
    }


def book_case(*, ordinal=ORDINAL, suffix=""):
    from bestseller.services.source_bound_planning_content import (
        build_source_bound_planning_prompts,
    )
    from bestseller.services.source_bound_planning_submission import planning_submission_tool

    material = authored_book()
    identity = {key: material["protagonist"][key] for key in IDENTITY}
    source = {"fixture_source_token": "detached-offline-native-input"}
    project = SimpleNamespace(
        id=uuid4(), language="zh-CN", target_chapters=4, target_word_count=10000,
        metadata_json={"book_design_snapshot": {"protagonist": identity}},
        # Detached fixture input to pure native constructors, not a DB receipt.
        _source_bound_planning_canonical={
            "id": str(uuid4()), "content": source, "content_hash": digest(source),
        },
    )
    projection = {"protagonist": deepcopy(identity)}
    system, user = build_source_bound_planning_prompts(
        kind="book_spec", project=project, premise="source-premise-token", projection=projection,
    )
    wire = {
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user + suffix}],
        "tools": [planning_submission_tool("book_spec")],
        "tool_choice": {"type": "function", "function": {"name": "submit_book_spec"}},
    }
    context = BuilderContext(
        blueprint(), call_index=ordinal, wire_body=wire, expected_wire_sha256=digest(wire),
        submissions={BOOK + ":" + str(ordinal): deepcopy(material)},
    )
    return context, material, project, projection


def with_material(context, template, material):
    return replace(context, submissions={template + ":" + str(context.call_index): deepcopy(material)})


def edit_book_wire(context, edit):
    wire = deepcopy(context.wire_body)
    text = wire["messages"][1]["content"]
    document, end = json.JSONDecoder().raw_decode(text)
    edit(document)
    wire["messages"][1]["content"] = canonical(document) + text[end:]
    return replace(context, wire_body=wire, expected_wire_sha256=digest(wire))


def native_projection_person(document):
    """Edit the native factory's actual referenced target, not a new wire shape."""
    def resolve(value):
        seen = set()
        while type(value) is dict and set(value) == {"$ref"}:
            ref = value["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen or len(seen) >= 64:
                raise AssertionError("fixture_native_projection_reference_invalid")
            seen.add(ref)
            value = pointer(document, ref[1:])
        return value

    projection = resolve(document["projection"])
    return resolve(projection["protagonist"])


def change_field(value, dotted, replacement, *, remove=False):
    parts = dotted.split(".")
    parent = value
    for part in parts[:-1]:
        parent = parent[part]
    if remove:
        parent.pop(parts[-1])
    else:
        parent[parts[-1]] = replacement


class FirstChapterBookSpecMaterialTests(OperandAssertions):
    def test_current_physical_slot_material_is_consumed_losslessly(self):
        context, material, project, projection = book_case()
        before = deepcopy(context)
        result = self.returns(BOOK, "/payload", {"format": "asset:book_spec", "payload": material},
                              lambda: build_payload(BOOK, context))
        self.equal_operand(BOOK, "context", vars(before), vars(context))
        from bestseller.services.source_bound_planning_content import validate_source_bound_planning_payload

        validate_source_bound_planning_payload(
            result["payload"], kind="book_spec", project=project, projection=projection,
        )

    def test_resolver_consumes_complete_material_without_legacy_story_fields(self):
        context, material, _, _ = book_case()
        config = {
            "schema": "normal-quickstart-private.v1", "story": {},
            "replies": [{"template": BOOK, "global_ordinal": ORDINAL,
                         "wire_sha256": digest(context.wire_body), "material": material}],
        }
        ticket = {"global_ordinal": ORDINAL, "template_ordinal": 1,
                  "native_operation_key": "1" * 64, "native_input_hash": "2" * 64}
        resolved = PrivateConfigResolver(config)(
            SimpleNamespace(prompt_template=BOOK, call_index=ORDINAL), context.wire_body, ticket,
        )
        self.returns(BOOK, "/payload", material, lambda: build_payload(BOOK, resolved)["payload"])

    def test_missing_or_other_physical_slot_never_uses_story_fallback(self):
        context, material, _, _ = book_case()
        for slots in ({}, {BOOK + ":172": material}):
            with self.subTest(field="submissions", slot_keys=list(slots)):
                changed = replace(context, submissions=slots)
                self.rejects(BOOK, "submissions/" + BOOK + ":173",
                             "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED", material, None,
                             lambda: build_payload(BOOK, changed))

    def test_native_six_required_fields_and_types_have_no_schema_defaults(self):
        context, material, _, _ = book_case()
        document = json.loads(context.wire_body["messages"][1]["content"])
        self.equal_operand(BOOK, "native/mandatory_exact_dotted_fields", list(REQUIRED),
                           document["current_output_contract"]["mandatory_exact_dotted_fields"])
        for field in REQUIRED:
            for mode, value in (("missing", None), ("empty", ""), ("whitespace", " "),
                                ("null", None), ("bool", True), ("array", ["token"])):
                with self.subTest(field=field, mode=mode):
                    invalid = deepcopy(material)
                    change_field(invalid, field, value, remove=mode == "missing")
                    changed = with_material(context, BOOK, invalid)
                    self.rejects(BOOK, field, "SCRIPT_BOOK_SPEC_MATERIAL_INVALID",
                                 "explicit nonempty string", value,
                                 lambda: build_payload(BOOK, changed))

    def test_parent_objects_cannot_be_replaced_by_strings_or_arrays(self):
        context, material, _, _ = book_case()
        for field in ("protagonist", "series_engine"):
            for value in ("legacy-string-token", [], None):
                with self.subTest(field=field, value_type=type(value).__name__):
                    invalid = deepcopy(material)
                    invalid[field] = value
                    changed = with_material(context, BOOK, invalid)
                    self.rejects(BOOK, field, "SCRIPT_BOOK_SPEC_MATERIAL_INVALID",
                                 material[field], value, lambda: build_payload(BOOK, changed))

    def test_optional_empty_fields_and_absent_naming_pool_are_not_invented(self):
        context, material, _, _ = book_case()
        material.pop("naming_pool")
        changed = with_material(context, BOOK, material)
        self.returns(BOOK, "/payload", material, lambda: build_payload(BOOK, changed)["payload"])

    def test_native_asset_json_submission_roundtrips_complete_authored_object(self):
        from bestseller.services.source_bound_planning_submission import parse_planning_submission

        context, material, _, _ = book_case()
        catalog = TemplateCatalog({
            "schema": INVENTORY_SCHEMA, "parse_failures": [], "dynamic_sites": [],
            "templates": [{"template": BOOK, "historical_only": False}],
        })
        response = build_response(BOOK, context, catalog=catalog, wire_body=context.wire_body,
                                  usage={"prompt_tokens": 3, "completion_tokens": 4})
        arguments = json.loads(response["tool_calls"][0]["function"]["arguments"])
        self.equal_operand(BOOK, "tool/arguments/keys", ["asset_json"], sorted(arguments))
        self.equal_operand(BOOK, "tool/arguments/asset_json/type", "str", type(arguments["asset_json"]).__name__)
        self.equal_operand(BOOK, "tool/arguments/asset_json", material, json.loads(arguments["asset_json"]))
        call = {**response["tool_calls"][0], "id": "call_" + uuid4().hex}
        payload, _, _ = parse_planning_submission({"content": "", "tool_calls": [call]}, kind="book_spec")
        self.equal_operand(BOOK, "native/parsed_payload", material, payload)


class FirstChapterBookSpecIdentityTests(OperandAssertions):
    def test_each_known_projection_identity_field_is_exact_not_normalized(self):
        context, material, _, _ = book_case()
        for field in IDENTITY:
            for mode in ("changed", "space", "missing", "wrong_type"):
                with self.subTest(field=field, mode=mode):
                    invalid = deepcopy(material)
                    original = material["protagonist"][field]
                    changed_value = {"changed": "different-identity-token", "space": original + " ",
                                     "missing": None, "wrong_type": [original]}[mode]
                    change_field(invalid, "protagonist." + field, changed_value, remove=mode == "missing")
                    changed = with_material(context, BOOK, invalid)
                    # Required name shape precedes identity comparison; the
                    # other three missing/type changes are identity mismatches.
                    code = ("SCRIPT_BOOK_SPEC_MATERIAL_INVALID"
                            if field == "name" and mode in {"missing", "wrong_type"}
                            else "SCRIPT_BOOK_SPEC_IDENTITY_MISMATCH")
                    self.rejects(BOOK, "protagonist." + field, code, original, changed_value,
                                 lambda: build_payload(BOOK, changed))

    def test_current_native_projection_wins_over_stale_private_subject(self):
        context, material, _, projection = book_case()
        changed = edit_book_wire(context, lambda doc: native_projection_person(doc).update(
            gender="male"))
        changed = replace(changed, protocol_subject={"projection": projection})
        self.rejects(BOOK, "projection/protagonist/gender", "SCRIPT_BOOK_SPEC_IDENTITY_MISMATCH",
                     "male", material["protagonist"]["gender"], lambda: build_payload(BOOK, changed))

    def test_native_local_projection_reference_is_resolved_before_identity_check(self):
        context, material, _, _ = book_case(suffix="\nRetain the same complete native source.")
        document, _ = json.JSONDecoder().raw_decode(context.wire_body["messages"][1]["content"])
        # The native factory already emits a root projection reference. Adding
        # a sibling protagonist would corrupt that valid {$ref: ...} envelope.
        self.equal_operand(BOOK, "native/projection/keys", ["$ref"], sorted(document["projection"]))
        self.equal_operand(BOOK, "native/projection/identity", material["protagonist"]["gender"],
                           native_projection_person(document)["gender"])
        self.returns(BOOK, "projection/$ref", material, lambda: build_payload(BOOK, context)["payload"])
        material["protagonist"]["gender"] = "male"
        changed = with_material(context, BOOK, material)
        self.rejects(BOOK, "projection/$ref/gender", "SCRIPT_BOOK_SPEC_IDENTITY_MISMATCH",
                     "female", "male", lambda: build_payload(BOOK, changed))

    def test_unknown_native_identity_does_not_erase_explicit_authored_identity(self):
        context, material, _, _ = book_case()
        def unknown(doc):
            person = native_projection_person(doc)
            person.update(gender="unknown", pronoun_set_zh=None)
            person.pop("pronoun_set_en")
        context = edit_book_wire(context, unknown)
        self.returns(BOOK, "identity/unknown_source", material,
                     lambda: build_payload(BOOK, context)["payload"])

    def test_missing_projection_and_unresolvable_reference_are_not_identity_authority(self):
        context, _, _, _ = book_case()
        for mode in ("missing", "unknown_ref", "external_ref", "cycle"):
            with self.subTest(field="projection", mode=mode):
                def edit(doc):
                    if mode == "missing":
                        doc.pop("projection")
                    else:
                        doc["projection"]["protagonist"] = {"$ref": {
                            "unknown_ref": "#/no_such_source", "external_ref": "https://invalid.test/source",
                            "cycle": "#/projection/protagonist",
                        }[mode]}
                changed = edit_book_wire(context, edit)
                self.rejects(BOOK, "projection/" + mode, "SCRIPT_BOOK_SPEC_WIRE_INVALID",
                             "current resolvable native projection", mode, lambda: build_payload(BOOK, changed))

    def test_actual_physical_wire_hash_including_suffix_is_mandatory(self):
        context, _, _, _ = book_case()
        for mode in ("absent", "stale_suffix"):
            with self.subTest(field="expected_wire_sha256", mode=mode):
                changed = replace(context, expected_wire_sha256=None)
                if mode == "stale_suffix":
                    wire = deepcopy(context.wire_body)
                    wire["messages"][1]["content"] += "\ncurrent-send-suffix"
                    changed = replace(context, wire_body=wire)
                self.rejects(BOOK, "expected_wire_sha256", "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED",
                             digest(changed.wire_body), changed.expected_wire_sha256,
                             lambda: build_payload(BOOK, changed))


def decoded_selector(path, match=None):
    value = {"$source_review": path}
    if match is not None:
        value.update(match=deepcopy(match), take="quote_id")
    return value


def review_selectors(version, literal):
    material = deepcopy(literal)
    for key in ("authority_snapshot_hash", "candidate_hash"):
        material[key] = decoded_selector("/hashes/" + key)
    source = [
        {"source_path": "authority_snapshot.engine", "quote": "source-engine-0"},
        {"source_path": "cast_spec.protagonist.goal", "quote": "source-goal-0"},
    ]
    candidate = [
        {"field": "world.locations", "quote": "candidate-place-0"},
        {"field": "cast.protagonist", "quote": "candidate-goal-0"},
    ]
    if version == "4.3-field-presence":
        # Explicit author selection of these states; native codec supplies IDs.
        # Empty/null/absent are not interchangeable and imply no good verdict.
        source[0] = {
            "schema": "source-bound-field-presence.v1", "side": "source",
            "field": "world.locations", "present": True, "value_type": "array",
            "value_hash": digest([{"name": "source-place-0"}]),
        }
        candidate[0] = {
            "schema": "source-bound-field-presence.v1", "side": "candidate",
            "field": "world.locations", "present": True, "value_type": "array",
            "value_hash": digest([]),
        }
    for side, selections in (("source", source), ("candidate", candidate)):
        for row, selection in zip(material[side + "_evidence"], selections, strict=True):
            row["quote_id"] = decoded_selector("/data/" + side + "_quote_options", selection)
    return material


def review_context(material, wire, version):
    return BuilderContext(
        {}, call_index=ORDINAL, wire_body=wire, expected_wire_sha256=digest(wire),
        protocol_subject={"prompt_version": version},
        submissions={REVIEW + ":" + str(ORDINAL): deepcopy(material)},
    )


class FirstChapterSourceReviewBindingTests(OperandAssertions):
    def test_explicit_decoded_view_works_with_existing_native_carriers_and_versions(self):
        for version in VERSIONS:
            for carrier in CARRIERS:
                with self.subTest(version=version, carrier=carrier):
                    data, literal, _ = native_source_review_case(version, codec=carrier)
                    wire = render_source_review_wire(data, codec=carrier, suffix="\nphysical-suffix-token")
                    selected = review_selectors(version, literal)
                    before = deepcopy((selected, wire))
                    # Existing decoder is the oracle; this test adds binding,
                    # not an alternative decoder or synthesized source IDs.
                    decoded = decode_source_review_wire(wire, expected_wire_sha256=digest(wire))
                    self.equal_operand(REVIEW, "decoded/data", data, decoded["data"])
                    context = review_context(selected, wire, version)
                    self.returns(REVIEW, "/payload", literal, lambda: build_payload(REVIEW, context)["payload"])
                    self.equal_operand(REVIEW, "input_mutation", before, (selected, wire))

    def test_false_uncertain_and_explicit_quality_findings_are_never_upgraded(self):
        for decision, core in (("revise", False), ("uncertain", None)):
            with self.subTest(decision=decision):
                _, literal, wire = native_source_review_case(VERSIONS[0], identity=True)
                literal.update(decision=decision, core_preserved=core)
                context = review_context(review_selectors(VERSIONS[0], literal), wire, VERSIONS[0])
                self.returns(REVIEW, "/payload", literal, lambda: build_payload(REVIEW, context)["payload"])

    def test_current_native_ids_are_selected_by_authored_match_not_order_or_cached_id(self):
        data, literal, _ = native_source_review_case(VERSIONS[0])
        selected = review_selectors(VERSIONS[0], literal)
        for side in ("source", "candidate"):
            rows = data[side + "_quote_options"]
            old_to_new = {row["quote_id"]: "native-fixture-" + uuid4().hex for row in rows}
            for row in rows:
                row["quote_id"] = old_to_new[row["quote_id"]]
            rows.reverse()
            for row in literal[side + "_evidence"]:
                row["quote_id"] = old_to_new[row["quote_id"]]
            if side == "candidate":
                data["candidate_quote_field_bindings"] = {
                    field: [old_to_new[value] for value in values]
                    for field, values in data["candidate_quote_field_bindings"].items()}
        wire = render_source_review_wire(data, codec="string-pool")
        context = review_context(selected, wire, VERSIONS[0])
        self.returns(REVIEW, "native/quote_ids", literal, lambda: build_payload(REVIEW, context)["payload"])

    def test_unknown_and_ambiguous_authored_catalog_matches_have_distinct_errors(self):
        for side in ("source", "candidate"):
            for mode in ("unknown", "ambiguous"):
                with self.subTest(side=side, mode=mode):
                    data, literal, _ = native_source_review_case(VERSIONS[0])
                    selected = review_selectors(VERSIONS[0], literal)
                    if mode == "unknown":
                        selected[side + "_evidence"][0]["quote_id"]["match"]["quote"] = "not-on-wire"
                    else:
                        duplicate = deepcopy(data[side + "_quote_options"][0])
                        duplicate["quote_id"] = "native-fixture-duplicate-id"
                        data[side + "_quote_options"].append(duplicate)
                        if side == "candidate":
                            data["candidate_quote_field_bindings"]["world.locations"].append(duplicate["quote_id"])
                    wire = render_source_review_wire(data)
                    context = review_context(selected, wire, VERSIONS[0])
                    code = ("SCRIPT_SOURCE_REVIEW_SELECTION_MISSING" if mode == "unknown"
                            else "SCRIPT_SOURCE_REVIEW_SELECTION_AMBIGUOUS")
                    self.rejects(REVIEW, side + "_evidence/0/quote_id", code, 1,
                                 0 if mode == "unknown" else 2, lambda: build_payload(REVIEW, context))

    def test_unknown_view_paths_indices_empty_match_and_fallbacks_are_rejected(self):
        _, literal, wire = native_source_review_case(VERSIONS[0])
        invalid_selectors = (
            decoded_selector("/unknown_view"),
            decoded_selector("/data/source_quote_options/0/quote_id"),
            decoded_selector("/data/source_quote_options", {}),
            {**decoded_selector("/data/source_quote_options", {"quote": "source-engine-0"}), "fallback": "s0"},
            {"$source_review": "/hashes/authority_snapshot_hash", "decode_json": True},
        )
        for index, invalid in enumerate(invalid_selectors):
            with self.subTest(selector=index):
                selected = review_selectors(VERSIONS[0], literal)
                selected["source_evidence"][0]["quote_id"] = invalid
                context = review_context(selected, wire, VERSIONS[0])
                self.rejects(REVIEW, "source_evidence/0/quote_id", "SCRIPT_SOURCE_REVIEW_BINDING_INVALID",
                             "explicit allowed catalog selection", invalid, lambda: build_payload(REVIEW, context))

    def test_decoded_binding_cannot_supply_authored_judgments_or_relations(self):
        _, literal, wire = native_source_review_case(VERSIONS[0], identity=True)
        for field in ("decision", "core_preserved", "core_conflicts", "quality_findings",
                      "feedback", "protagonist_identity_supported", "source_relation"):
            with self.subTest(field=field):
                selected = review_selectors(VERSIONS[0], literal)
                invalid = decoded_selector("/hashes/candidate_hash")
                if field == "source_relation":
                    selected["source_evidence"][0][field] = invalid
                else:
                    selected[field] = invalid
                context = review_context(selected, wire, VERSIONS[0])
                self.rejects(REVIEW, field, "SCRIPT_VERDICT_MUST_BE_AUTHORED",
                             "literal private author judgment", invalid, lambda: build_payload(REVIEW, context))

    def test_empty_to_null_presence_change_requires_new_authored_selection(self):
        _, literal, _ = native_source_review_case(VERSIONS[1], field_state="empty")
        selected = review_selectors(VERSIONS[1], literal)
        _, _, wire = native_source_review_case(VERSIONS[1], field_state="null")
        context = review_context(selected, wire, VERSIONS[1])
        self.rejects(REVIEW, "candidate_evidence/0/quote_id/match/value_type",
                     "SCRIPT_SOURCE_REVIEW_SELECTION_MISSING", "array", "null",
                     lambda: build_payload(REVIEW, context))

    def test_valid_selector_for_wrong_evidence_field_cannot_satisfy_coverage(self):
        _, literal, wire = native_source_review_case(VERSIONS[0])
        selected = review_selectors(VERSIONS[0], literal)
        selected["candidate_evidence"][0]["quote_id"] = deepcopy(selected["candidate_evidence"][1]["quote_id"])
        context = review_context(selected, wire, VERSIONS[0])
        self.rejects(REVIEW, "candidate_evidence/0/field", "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH",
                     "world.locations", "cast.protagonist", lambda: build_payload(REVIEW, context))

    def test_current_wire_hash_and_literal_echo_hash_checks_remain_enforced(self):
        _, literal, wire = native_source_review_case(VERSIONS[0])
        context = review_context(review_selectors(VERSIONS[0], literal), wire, VERSIONS[0])
        changed_wire = deepcopy(wire)
        changed_wire["messages"][1]["content"] += "\nnew-physical-suffix"
        changed = replace(context, wire_body=changed_wire)
        self.rejects(REVIEW, "expected_wire_sha256", "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED",
                     digest(changed_wire), context.expected_wire_sha256, lambda: build_payload(REVIEW, changed))
        for field in ("authority_snapshot_hash", "candidate_hash"):
            with self.subTest(field=field):
                selected = review_selectors(VERSIONS[0], literal)
                selected[field] = "0" * 64
                changed = review_context(selected, wire, VERSIONS[0])
                self.rejects(REVIEW, field, "SCRIPT_SOURCE_REVIEW_HASH_MISMATCH", literal[field],
                             selected[field], lambda: build_payload(REVIEW, changed))

    def test_unknown_template_version_and_native_codec_remain_explicit_errors(self):
        _, literal, wire = native_source_review_case(VERSIONS[0], codec="lossless-v3")
        selected = review_selectors(VERSIONS[0], literal)
        context = review_context(selected, wire, VERSIONS[0])
        self.rejects(REVIEW + "_unknown", "template", "SCRIPT_TEMPLATE_UNCOVERED", REVIEW,
                     REVIEW + "_unknown", lambda: build_payload(REVIEW + "_unknown", context))
        changed = replace(context, protocol_subject={"prompt_version": "4.99-unknown"})
        self.rejects(REVIEW, "prompt_version", "SCRIPT_SOURCE_REVIEW_VERSION_UNCOVERED",
                     list(VERSIONS), "4.99-unknown", lambda: build_payload(REVIEW, changed))
        changed_wire = deepcopy(wire)
        prefix, raw = changed_wire["messages"][1]["content"].split("source_review_data=", 1)
        carrier = json.loads(raw)
        carrier["schema"] = "lossless-source-encoding.v99"
        changed_wire["messages"][1]["content"] = prefix + "source_review_data=" + canonical(carrier)
        changed = review_context(selected, changed_wire, VERSIONS[0])
        self.rejects(REVIEW, "source_review_data/schema", "SCRIPT_SOURCE_REVIEW_WIRE_INVALID",
                     "lossless-source-encoding.v3", carrier["schema"], lambda: build_payload(REVIEW, changed))
