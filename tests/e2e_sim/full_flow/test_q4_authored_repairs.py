"""RED only: short field roles and two explicitly authored repair protocols.

Main runs these offline. Fixtures construct structural tokens in memory; no app,
DB, HTTP, SDK installation or native async repair workflow is invoked. Private
checkpoint files exist only inside an explicit private directory outside Git.
"""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from uuid import uuid4

from scripts.full_flow.simulation.checkpoint import PhysicalOrdinalStore
from scripts.full_flow.simulation.contracts import (
    ScriptContractError,
    TemplateCatalog,
    canonical,
    digest,
)
from scripts.full_flow.simulation.inventory import INVENTORY_SCHEMA
from scripts.full_flow.simulation.quickstart_builders import (
    BuilderContext,
    build_payload,
    builder_inventory,
)
from scripts.full_flow.simulation.runtime_adapter import (
    create_checkpointed_runtime_tape,
    create_runtime_tape,
)

from .test_contracts import ENDPOINTS, USAGE
from .test_q4_reader_first import binding, native_module, reader_case

ROLES = "fanqie_reader_first_field_roles"
CONCEPTION_REPAIR = "conception_reader_first_repair_v1"
OUTLINE_REPAIR = "planner_reader_first_full_outline_commercial_surgical_repair"
VERSIONS = {
    ROLES: "fanqie-rf-field-roles-v1",
    CONCEPTION_REPAIR: "reader-first-repair.v1",
    OUTLINE_REPAIR: "rf-outline-repair-v1",
}


def outline_material():
    chapters = []
    for number in (1, 2, 3, 4):
        chapters.append(
            {
                "chapter_number": number,
                "goal": "goal-token-" + str(number),
                "stakes": "fixed-reference-token-" + str(number),
                "opening_situation": "fixed-opening-token",
                "causal_contract": {"protagonist_choice": "choice-token"},
                "scenes": [
                    {
                        "scene_number": 1,
                        "concrete_goal": "scene-goal-token",
                        "action_sequence": ["action-token"],
                        "_meta": {"custody_token": "scene"},
                    }
                ],
                "empty_text": "",
                "null_value": None,
                "empty_array": [],
                "empty_object": {},
                "bool_value": False,
                "numeric_value": 2,
                "escaped/key": {"~leaf": "escaped-token"},
                "_meta": {"custody_token": "chapter"},
            }
        )
    return {
        "batch_name": "runtime-structural-tokens",
        "chapters": chapters,
        "_meta": {"custody_token": "outline"},
    }


def authored_roles():
    # Every classification is explicit. Never derive roles from types, words or
    # the native leaf index. The same finite map is authored for both fixtures.
    return {
        "/chapter_number": "continuity_reference",
        "/goal": "narrative_task",
        "/stakes": "continuity_reference",
        "/opening_situation": "continuity_reference",
        "/causal_contract/protagonist_choice": "narrative_task",
        "/scenes/0/scene_number": "continuity_reference",
        "/scenes/0/concrete_goal": "narrative_task",
        "/scenes/0/action_sequence/0": "narrative_task",
        "/empty_text": "continuity_reference",
        "/null_value": "continuity_reference",
        "/empty_array": "continuity_reference",
        "/empty_object": "continuity_reference",
        "/bool_value": "continuity_reference",
        "/numeric_value": "continuity_reference",
        "/escaped~1key/~0leaf": "continuity_reference",
    }


def context_for(template, marker, wire, material, *, ordinal=61, source_hash=None):
    subject = {"reader_first_planning": marker, "prompt_version": VERSIONS[template]}
    if source_hash is not None:
        subject["source_sha256"] = source_hash
    return BuilderContext(
        {},
        call_index=ordinal,
        wire_body=wire,
        expected_wire_sha256=digest(wire),
        protocol_subject=subject,
        submissions={template + ":" + str(ordinal): material},
    )


def wire_for(system, document):
    return {
        "model": "offline-q4-contract",
        "stream": True,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": canonical(document)},
        ],
    }


def annotation_case(*, attempt=0, ordinal=61):
    from bestseller.services.fanqie_short_planner import _short_roles_request

    marker, outline = binding(), outline_material()
    request = _short_roles_request(
        SimpleNamespace(id=uuid4()),
        marker,
        outline,
        workflow_run_id=uuid4(),
        slot=attempt,
        rejected=None if attempt == 0 else '{"schema":"rejected-annotation-token"}',
        requested_by="offline-test",
    )
    wire = wire_for(request.system_prompt, json.loads(request.user_prompt))
    material = {
        "schema": "fanqie-reader-first-field-roles.v1",
        "source_sha256": digest(outline),
        "chapters": [
            {"chapter_number": 1, "field_roles": authored_roles()},
            {"chapter_number": 2, "field_roles": authored_roles()},
            {"chapter_number": 3, "field_roles": authored_roles()},
            {"chapter_number": 4, "field_roles": authored_roles()},
        ],
    }
    return context_for(ROLES, marker, wire, material, ordinal=ordinal), material, wire


def conception_repair_case(*, ordinal=61):
    review_context, _review, _wire, source = reader_case("conception_reader_first_skeleton_v1")
    marker = review_context.protocol_subject["reader_first_planning"]
    source["candidate"]["world_model"]["cast"] = [{"name": "actor-token", "facts": ["fixed-token"]}]
    source["candidate"]["selected_hook_spec"] = {"hook": "selected-hook-token"}
    source["candidate"]["genre_intent_contract"] = {"genre": "selected-genre-token"}
    frozen = {name: deepcopy(source[name]) for name in ("original_user_input", "creation_inputs")}
    document = {
        "binding": marker,
        "source_sha256": digest(source),
        "config_sha256": marker["config_sha256"],
        "source": source,
        "frozen_controls": frozen,
        "review": {
            "status": "failed",
            "source_sha256": digest(source),
            "checkpoint": "skeleton",
            "reasons": [{"dimension": "reader_promise", "reason": "explicit-objection-token"}],
        },
    }
    wire = wire_for(
        native_module().reader_first_instruction(marker, stage="conception_repair"), document
    )
    revised = deepcopy(source["candidate"])
    revised["synopsis"] = "explicit-revised-synopsis-token"
    material = {
        "schema": "reader-first-conception-repair.v1",
        "source_sha256": digest(source),
        "config_sha256": marker["config_sha256"],
        "user_input_sha256": marker["user_input_sha256"],
        "frozen_controls": deepcopy(frozen),
        "candidate": revised,
    }
    return context_for(CONCEPTION_REPAIR, marker, wire, material, ordinal=ordinal), material, wire


def outline_repair_case(*, route="short-json", ordinal=61):
    from bestseller.services import planner

    marker, outline = binding(), outline_material()
    for chapter in outline["chapters"]:
        chapter["_meta"].update(
            reader_first_planning=deepcopy(marker),
            writer_delivery_schema="approved-current-chapter-delivery.v3-reader-first",
            reader_first_field_roles=authored_roles(),
        )
    if route == "short-json":
        # Match fanqie_outline_contract_completion._sources. No invented
        # top-level original_input_binding: native binding lives in chapter
        # custody and the request metadata supplied as protocol_subject.
        source = {
            "outline": outline,
            "selected_source": {
                "project_id": str(uuid4()),
                "project_inputs": {"target_chapters": 4},
                "complete_foundation_content": {"cast_spec": {"characters": ["actor-token"]}},
            },
            "original_beat": {
                "artifact_id": str(uuid4()),
                "raw": {"beats": ["fixed-beat-token"]},
                "receipt": {},
            },
            "base_outline": outline_material(),
        }
    else:
        source = {
            "book_spec": {"theme": "theme-token"},
            "world_spec": {"setting": "setting-token"},
            "cast_spec": {"characters": ["actor-token"]},
            "volume_plan": [{"volume_number": 1}],
            "outline": outline,
            "premise": "premise-token",
            "original_input_binding": marker,
        }
    protocol = planner._outline_patch_protocol(outline)
    review = {
        "status": "failed",
        "source_sha256": digest(source),
        "reasons": [{"dimension": "reader_promise", "reason": "explicit-objection-token"}],
    }
    if route == "short-json":
        document = {
            "schema": "fanqie-reader-first-outline-repair.v1",
            "source": source,
            "source_sha256": digest(source),
            "reader_review": review,
            "patch_protocol": protocol,
        }
        wire = wire_for("Return explicit patches for the supplied complete short source.", document)
    elif route == "normal-text":
        # These delimiters and JSON formatting are from the normal native repair
        # callsite. Complete source is the final JSON value, not a source prefix.
        text = (
            "必须遵守以下可编辑目标、字段和类型协议：\n"  # noqa: RUF001 -- Exact native delimiter.
            + json.dumps(protocol, ensure_ascii=False)
            + "\nCOMPLETE READER-FIRST SOURCE (immutable facts plus editable outline):\n"
            + json.dumps(source, ensure_ascii=False)
        )
        wire = wire_for(
            native_module().reader_first_instruction(marker, stage="outline_repair"), {}
        )
        wire["messages"][1]["content"] = text
    else:
        raise AssertionError("Unknown fixture route")
    material = {
        "patches": [
            {
                "chapter_number": 1,
                "scope": "chapter",
                "field": "goal",
                "value": "explicit-revised-goal-token",
            }
        ]
    }
    return (
        context_for(
            OUTLINE_REPAIR, marker, wire, material, ordinal=ordinal, source_hash=digest(source)
        ),
        material,
        wire,
    )


def case_for(template, *, ordinal=61):
    if template == ROLES:
        return annotation_case(ordinal=ordinal)
    if template == CONCEPTION_REPAIR:
        return conception_repair_case(ordinal=ordinal)
    if template == OUTLINE_REPAIR:
        return outline_repair_case(ordinal=ordinal)
    raise AssertionError("Unlisted fixture template")


def with_material(template, context, material):
    return replace(context, submissions={template + ":" + str(context.call_index): material})


def rewrite_document(context, wire, document):
    wire["messages"][1]["content"] = canonical(document)
    return replace(context, expected_wire_sha256=digest(wire))


class ContractAssertions:
    def error(self, template, context, code):
        with self.assertRaises(ScriptContractError) as raised:
            build_payload(template, context)
        self.assertEqual(raised.exception.code, code)


class Q4ShortFieldRolesTests(ContractAssertions, unittest.TestCase):
    def test_exact_three_names_are_registered_without_suffix_fallback(self):
        inventory = builder_inventory()
        names = (
            set(inventory["producers"])
            | set(inventory["requires_explicit_physical_reply"])
            | set(inventory["keyflow_explicit_reply_protocols"])
        )
        self.assertEqual(sorted(set(VERSIONS) - names), [])
        context, _, _ = annotation_case()
        for template in (
            ROLES + "_repair",
            CONCEPTION_REPAIR + "_repair",
            OUTLINE_REPAIR + "_repair",
        ):
            with self.subTest(template=template):
                self.error(template, context, "SCRIPT_TEMPLATE_UNCOVERED")

    def test_both_native_attempts_preserve_authored_roles_without_applying_metadata(self):
        for attempt in (0, 1):
            with self.subTest(attempt=attempt):
                context, material, wire = annotation_case(attempt=attempt)
                before = deepcopy((context, material, wire))
                document = json.loads(wire["messages"][1]["content"])
                self.assertEqual(set(document["chapter_leaf_values"]["1"]), set(authored_roles()))
                self.assertEqual(
                    build_payload(ROLES, context), {"format": "json", "payload": material}
                )
                self.assertEqual((context, material, wire), before)
                self.assertNotIn("_meta", material)

    def test_no_leaf_is_filled_implicitly_including_empty_null_boolean_number(self):
        context, material, _ = annotation_case()
        for path in authored_roles():
            invalid = deepcopy(material)
            del invalid["chapters"][0]["field_roles"][path]
            with self.subTest(path=path):
                self.error(
                    ROLES,
                    with_material(ROLES, context, invalid),
                    "SCRIPT_READER_FIRST_ANNOTATION_INVALID",
                )

    def test_annotation_schema_source_chapter_identity_and_extra_fields_are_strict(self):
        context, material, _ = annotation_case()
        variants = []
        for key in material:
            value = deepcopy(material)
            del value[key]
            variants.append(("missing-" + key, value))
        variants.extend(
            (
                ("wrong-hash", {**material, "source_sha256": "0" * 64}),
                ("wrong-schema", {**material, "schema": "unknown"}),
                ("extra-outline", {**material, "outline": {}}),
                ("missing-chapter", {**material, "chapters": material["chapters"][:1]}),
                ("duplicate-chapter", {**material, "chapters": [material["chapters"][0]] * 4}),
            )
        )
        for chapter_number in (True, "1", 5):
            value = deepcopy(material)
            value["chapters"][0]["chapter_number"] = chapter_number
            variants.append(("chapter-" + repr(chapter_number), value))
        for label, value in variants:
            with self.subTest(case=label):
                self.error(
                    ROLES,
                    with_material(ROLES, context, value),
                    "SCRIPT_READER_FIRST_ANNOTATION_INVALID",
                )

    def test_unknown_roles_and_invented_or_custody_paths_are_rejected(self):
        context, material, _ = annotation_case()
        for path, role in (
            ("/goal", "unknown"),
            ("/goal", True),
            ("/new_field", "narrative_task"),
            ("/_meta/custody_token", "continuity_reference"),
            ("/escaped/key/~leaf", "continuity_reference"),
        ):
            invalid = deepcopy(material)
            invalid["chapters"][0]["field_roles"][path] = role
            with self.subTest(path=path, role=role):
                self.error(
                    ROLES,
                    with_material(ROLES, context, invalid),
                    "SCRIPT_READER_FIRST_ANNOTATION_INVALID",
                )

    def test_role_values_must_be_private_authored_not_selected_from_wire(self):
        context, material, _ = annotation_case()
        invalid = deepcopy(material)
        invalid["chapters"][0]["field_roles"]["/goal"] = {
            "$wire": "/messages/1/content",
            "decode_json": True,
            "pointer": "/chapter_leaf_values/1/goal",
        }
        self.error(ROLES, with_material(ROLES, context, invalid), "SCRIPT_VERDICT_MUST_BE_AUTHORED")

    def test_hash_can_bind_native_wire_but_rejected_response_never_supplies_missing_roles(self):
        context, material, wire = annotation_case(attempt=1)
        authored = deepcopy(material)
        authored["source_sha256"] = {
            "$wire": "/messages/1/content",
            "decode_json": True,
            "pointer": "/source_sha256",
        }
        self.assertEqual(
            build_payload(ROLES, with_material(ROLES, context, authored))["payload"], material
        )
        document = json.loads(wire["messages"][1]["content"])
        document["rejected_response"] = canonical(material)
        context = rewrite_document(context, wire, document)
        self.error(ROLES, replace(context, submissions={}), "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED")

    def test_leaf_catalogue_is_reproved_from_full_outline_and_excludes_nested_custody(self):
        for failure in (
            "missing-leaf",
            "changed-value",
            "custody-leaf",
            "stale-source",
            "bad-binding",
        ):
            context, _, wire = annotation_case()
            document = json.loads(wire["messages"][1]["content"])
            if failure == "missing-leaf":
                del document["chapter_leaf_values"]["1"]["/null_value"]
            elif failure == "changed-value":
                document["chapter_leaf_values"]["1"]["/goal"] = "different-token"
            elif failure == "custody-leaf":
                document["chapter_leaf_values"]["1"]["/scenes/0/_meta/custody_token"] = "scene"
            elif failure == "stale-source":
                document["outline"]["chapters"][0]["goal"] = "different-token"
            else:
                document["reader_first_planning"]["user_input_sha256"] = "2" * 64
            with self.subTest(failure=failure):
                self.error(
                    ROLES,
                    rewrite_document(context, wire, document),
                    "SCRIPT_READER_FIRST_ANNOTATION_INVALID",
                )

    def test_native_attempt_is_finite_and_version_is_exact(self):
        for attempt in (-1, 2, True, "1"):
            context, _, wire = annotation_case()
            document = json.loads(wire["messages"][1]["content"])
            document["annotation_attempt"] = attempt
            with self.subTest(attempt=attempt):
                self.error(
                    ROLES,
                    rewrite_document(context, wire, document),
                    "SCRIPT_READER_FIRST_ANNOTATION_INVALID",
                )
        context, _, _ = annotation_case()
        self.error(
            ROLES,
            replace(
                context, protocol_subject={**context.protocol_subject, "prompt_version": "unknown"}
            ),
            "SCRIPT_READER_FIRST_VERSION_MISMATCH",
        )


class Q4ConceptionRepairTests(ContractAssertions, unittest.TestCase):
    def test_explicit_complete_candidate_returns_raw_proposal_and_preserves_inputs(self):
        context, material, wire = conception_repair_case()
        before = deepcopy((context, material, wire))
        self.assertEqual(
            build_payload(CONCEPTION_REPAIR, context), {"format": "json", "payload": material}
        )
        self.assertEqual((context, material, wire), before)
        self.assertNotIn("status", material)

    def test_identity_frozen_controls_and_selected_choices_cannot_change(self):
        context, material, _ = conception_repair_case()
        variants = []
        for key in ("schema", "source_sha256", "config_sha256", "user_input_sha256"):
            variants.append((key, {**material, key: "wrong-token"}))
        for field in (
            "frozen_controls",
            "genre_intent_contract",
            "selected_hook_spec",
            "reader_first_planning",
        ):
            value = deepcopy(material)
            if field == "frozen_controls":
                value[field]["creation_inputs"] = {"changed": True}
            elif field == "reader_first_planning":
                value["candidate"]["writing_profile"][field]["user_input_sha256"] = "2" * 64
            else:
                value["candidate"][field] = {"changed": True}
            variants.append((field, value))
        for label, value in variants:
            with self.subTest(field=label):
                self.error(
                    CONCEPTION_REPAIR,
                    with_material(CONCEPTION_REPAIR, context, value),
                    "SCRIPT_READER_FIRST_REPAIR_INVALID",
                )

    def test_full_bundle_has_no_missing_nested_records_type_changes_or_blank_required_text(self):
        context, material, _ = conception_repair_case()
        for failure in (
            "missing-top",
            "missing-profile",
            "missing-cast",
            "missing-record",
            "wrong-type",
            "blank-title",
            "extra-authority",
        ):
            invalid = deepcopy(material)
            candidate = invalid["candidate"]
            if failure == "missing-top":
                del candidate["synopsis"]
            elif failure == "missing-profile":
                del candidate["writing_profile"]["reader_first_planning"]
            elif failure == "missing-cast":
                del candidate["world_model"]["cast"][0]["facts"]
            elif failure == "missing-record":
                candidate["world_model"]["cast"] = []
            elif failure == "wrong-type":
                candidate["world_model"]["cast"] = {}
            elif failure == "blank-title":
                candidate["title"] = " "
            else:
                invalid["approval"] = True
            with self.subTest(failure=failure):
                self.error(
                    CONCEPTION_REPAIR,
                    with_material(CONCEPTION_REPAIR, context, invalid),
                    "SCRIPT_READER_FIRST_REPAIR_INVALID",
                )

    def test_unchanged_candidate_and_copy_from_wire_are_not_authored_repair(self):
        context, material, wire = conception_repair_case()
        original = json.loads(wire["messages"][1]["content"])["source"]["candidate"]
        self.error(
            CONCEPTION_REPAIR,
            with_material(CONCEPTION_REPAIR, context, {**material, "candidate": original}),
            "SCRIPT_READER_FIRST_REPAIR_INVALID",
        )
        self.error(
            CONCEPTION_REPAIR,
            with_material(
                CONCEPTION_REPAIR,
                context,
                {
                    **material,
                    "candidate": {
                        "$wire": "/messages/1/content",
                        "decode_json": True,
                        "pointer": "/source/candidate",
                    },
                },
            ),
            "SCRIPT_READER_FIRST_REPAIR_MUST_BE_AUTHORED",
        )

    def test_repair_wire_binds_current_failed_review_frozen_input_and_native_stage(self):
        for failure in (
            "passed-review",
            "unknown-review",
            "old-review",
            "source-drift",
            "frozen-drift",
            "wrong-stage",
        ):
            context, _, wire = conception_repair_case()
            document = json.loads(wire["messages"][1]["content"])
            if failure in ("passed-review", "unknown-review"):
                document["review"]["status"] = failure.split("-")[0]
            elif failure == "old-review":
                document["review"]["source_sha256"] = "0" * 64
            elif failure == "source-drift":
                document["source"]["candidate"]["premise"] = "different-token"
            elif failure == "frozen-drift":
                document["frozen_controls"]["original_user_input"] = "different-input-token"
            else:
                wire["messages"][0]["content"] = native_module().reader_first_instruction(
                    binding(), stage="unrelated-stage"
                )
            with self.subTest(failure=failure):
                self.error(
                    CONCEPTION_REPAIR,
                    rewrite_document(context, wire, document),
                    "SCRIPT_READER_FIRST_WIRE_INVALID"
                    if failure == "wrong-stage"
                    else "SCRIPT_READER_FIRST_REPAIR_INVALID",
                )


class Q4OutlineRepairTests(ContractAssertions, unittest.TestCase):
    def test_normal_text_and_short_json_return_same_explicit_patch_without_applying_it(self):
        for route in ("normal-text", "short-json"):
            context, material, wire = outline_repair_case(route=route)
            before = deepcopy((context, material, wire))
            with self.subTest(route=route):
                self.assertEqual(
                    build_payload(OUTLINE_REPAIR, context), {"format": "json", "payload": material}
                )
                self.assertEqual((context, material, wire), before)

    def test_empty_duplicate_unchanged_and_wrong_typed_patches_are_invalid(self):
        for route in ("normal-text", "short-json"):
            context, material, _ = outline_repair_case(route=route)
            patch = material["patches"][0]
            for label, value in (
                ("empty", {"patches": []}),
                ("duplicate", {"patches": [patch, patch]}),
                ("unchanged", {"patches": [{**patch, "value": "goal-token-1"}]}),
                ("wrong-type", {"patches": [{**patch, "value": {"text": "value-token"}}]}),
                ("extra-outline", {**material, "outline": {}}),
                (
                    "missing-value",
                    {"patches": [{key: val for key, val in patch.items() if key != "value"}]},
                ),
            ):
                with self.subTest(route=route, failure=label):
                    self.error(
                        OUTLINE_REPAIR,
                        with_material(OUTLINE_REPAIR, context, value),
                        "SCRIPT_READER_FIRST_REPAIR_INVALID",
                    )

    def test_patch_targets_are_native_authored_leaves_not_aliases_custody_or_continuity(self):
        for route in ("normal-text", "short-json"):
            context, material, _ = outline_repair_case(route=route)
            patch = material["patches"][0]
            for update in (
                {"chapter_number": 5},
                {"chapter_number": True},
                {"scope": "book"},
                {"field": "chapter_goal"},
                {"field": "stakes"},
                {"field": "opening_situation"},
                {"field": "_meta", "value": {}},
                {"scope": "scene", "scene_number": 2, "field": "concrete_goal"},
                {"scope": "scene", "scene_number": 1, "field": "action_sequence", "value": []},
                {
                    "scope": "scene",
                    "scene_number": 1,
                    "field": "action_sequence",
                    "value": ["one", "two"],
                },
            ):
                with self.subTest(route=route, update=update):
                    self.error(
                        OUTLINE_REPAIR,
                        with_material(OUTLINE_REPAIR, context, {"patches": [{**patch, **update}]}),
                        "SCRIPT_READER_FIRST_REPAIR_INVALID",
                    )

    def test_patch_values_must_be_explicit_private_material(self):
        context, material, _ = outline_repair_case()
        material["patches"][0]["value"] = {
            "$wire": "/messages/1/content",
            "decode_json": True,
            "pointer": "/source/outline/chapters/0/goal",
        }
        self.error(OUTLINE_REPAIR, context, "SCRIPT_READER_FIRST_REPAIR_MUST_BE_AUTHORED")

    def test_short_json_rejects_forged_protocol_source_review_or_binding(self):
        for failure in (
            "schema",
            "source-hash",
            "patch-protocol",
            "review-hash",
            "unknown-review",
            "binding",
        ):
            context, _, wire = outline_repair_case()
            document = json.loads(wire["messages"][1]["content"])
            if failure == "schema":
                document["schema"] = "unknown"
            elif failure == "source-hash":
                document["source_sha256"] = "0" * 64
            elif failure == "patch-protocol":
                document["patch_protocol"]["fields"]["chapter"].append("invented_field")
            elif failure == "review-hash":
                document["reader_review"]["source_sha256"] = "0" * 64
            elif failure == "unknown-review":
                document["reader_review"]["status"] = "unknown"
            else:
                document["source"]["outline"]["chapters"][0]["_meta"]["reader_first_planning"][
                    "user_input_sha256"
                ] = "2" * 64
                document["source_sha256"] = digest(document["source"])
                document["reader_review"]["source_sha256"] = document["source_sha256"]
                context = replace(
                    context,
                    protocol_subject={
                        **context.protocol_subject,
                        "source_sha256": document["source_sha256"],
                    },
                )
            with self.subTest(failure=failure):
                self.error(
                    OUTLINE_REPAIR,
                    rewrite_document(context, wire, document),
                    "SCRIPT_READER_FIRST_REPAIR_INVALID",
                )

    def test_normal_text_requires_complete_tail_source_and_current_context_hash(self):
        for failure in ("missing-tail", "truncated-json", "stale-context-hash"):
            context, _, wire = outline_repair_case(route="normal-text")
            if failure == "missing-tail":
                wire["messages"][1]["content"] = "Return patches without complete source."
            elif failure == "truncated-json":
                wire["messages"][1]["content"] = wire["messages"][1]["content"][:-1]
            else:
                context = replace(
                    context,
                    protocol_subject={**context.protocol_subject, "source_sha256": "0" * 64},
                )
            with self.subTest(failure=failure):
                self.error(
                    OUTLINE_REPAIR,
                    replace(context, expected_wire_sha256=digest(wire)),
                    "SCRIPT_READER_FIRST_REPAIR_INVALID",
                )


class Q4AuthoredRepairRuntimeTests(ContractAssertions, unittest.TestCase):
    def catalog(self, template, include):
        return TemplateCatalog(
            {
                "schema": INVENTORY_SCHEMA,
                "parse_failures": [],
                "dynamic_sites": [],
                "templates": [{"template": template, "historical_only": False}] if include else [],
            }
        )

    def test_all_three_use_actual_template_global_slot_and_uuids_with_old_or_new_catalogue(self):
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        for template in VERSIONS:
            for include in (False, True):
                context, material, wire = case_for(template)
                catalog = self.catalog(template, include)
                before = deepcopy(catalog.inventory)
                tape = create_runtime_tape(
                    catalog=catalog,
                    context_resolver=lambda obs, body, ctx=context: replace(ctx, wire_body=body),
                    usage_resolver=lambda obs, body: USAGE,
                    allowed_endpoints=ENDPOINTS,
                )
                observation = NativeObservation(
                    template, context.call_index, str(uuid4()), str(uuid4())
                )
                with self.subTest(template=template, catalogue=include):
                    selected = tape._choose(wire, wire_fingerprints(wire), observation)
                    self.assertEqual(json.loads(selected["content"]), material)
                    match = tape.document["entries"][0]["match"]
                    self.assertEqual(
                        (
                            match["prompt_template"],
                            match["call_index"],
                            match["project_id"],
                            match["workflow_run_id"],
                        ),
                        (
                            template,
                            context.call_index,
                            observation.project_id,
                            observation.workflow_run_id,
                        ),
                    )
                    self.assertEqual(catalog.inventory, before)
                    self.assertFalse(selected.get("tool_calls"))

    def test_each_protocol_requires_new_material_slot_current_wire_and_exact_version(self):
        for template in VERSIONS:
            context, material, _ = case_for(template)
            for label, changed, code in (
                ("missing", replace(context, submissions={}), "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED"),
                (
                    "old-slot",
                    replace(context, submissions={template + ":60": material}),
                    "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED",
                ),
                (
                    "wire",
                    replace(context, expected_wire_sha256="0" * 64),
                    "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED",
                ),
                (
                    "version",
                    replace(
                        context,
                        protocol_subject={**context.protocol_subject, "prompt_version": "unknown"},
                    ),
                    "SCRIPT_READER_FIRST_VERSION_MISMATCH",
                ),
            ):
                with self.subTest(template=template, failure=label):
                    self.error(template, changed, code)

    def test_pending_or_faulted_reply_survives_restart_and_never_reuses_old_private_slot(self):
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        for template in VERSIONS:
            for fault in (False, True):
                with (
                    self.subTest(template=template, transport_fault=fault),
                    tempfile.TemporaryDirectory(
                        dir="/private/tmp", prefix="q4-authored-repair-"
                    ) as directory,
                ):
                    base = Path(directory).resolve()
                    private, repo = base / "private", base / "repo"
                    private.mkdir(mode=0o700)
                    repo.mkdir()
                    outside_checkout = not any(
                        (parent / ".git").exists() for parent in (private, *private.parents)
                    )
                    self.assertTrue(
                        outside_checkout,
                        canonical(
                            {
                                "field": "checkpoint.private_root.outside_checkout",
                                "expected": True,
                                "actual": outside_checkout,
                                "actual_path_sha256": hashlib.sha256(
                                    str(private).encode()
                                ).hexdigest(),
                                "template": template,
                                "transport_fault": fault,
                            }
                        ),
                    )
                    context, material, wire = case_for(template, ordinal=1)
                    options = {
                        "private_root": private,
                        "repository_root": repo,
                        "run_id": "q4-offline",
                        "config_sha256": digest(material),
                    }
                    store = PhysicalOrdinalStore(private / "ordinals.json", **options)
                    store.initialize()

                    def make_tape(checkpoint, template_name=template, saved_context=context):
                        return create_checkpointed_runtime_tape(
                            checkpoint=checkpoint,
                            catalog=self.catalog(template_name, False),
                            context_resolver=lambda obs, body, ticket: replace(
                                saved_context, call_index=obs.call_index, wire_body=body
                            ),
                            usage_resolver=lambda observed, body: USAGE,
                            allowed_endpoints=ENDPOINTS,
                        )

                    fields = {
                        **wire_fingerprints(wire),
                        "native_operation_key": digest(str(uuid4())),
                        "native_input_hash": digest(wire),
                        "native_attempt_id": uuid4().hex,
                    }
                    observed = NativeObservation(template, 1, str(uuid4()), str(uuid4()))
                    make_tape(store)._choose(wire, fields, observed)
                    if fault:
                        store.record_transport(1, outcome="transport_fault")
                    before = store.inspect()["sends"][0]
                    restarted = PhysicalOrdinalStore(private / "ordinals.json", **options)
                    restarted.initialize()
                    tape = make_tape(restarted)
                    with self.assertRaises(ScriptContractError) as duplicate:
                        tape._choose(wire, fields, observed)
                    self.assertEqual(duplicate.exception.code, "SCRIPT_PHYSICAL_ATTEMPT_REUSED")
                    # A fresh native attempt still needs an explicit ordinal-2
                    # material. This low-level check grants no native retry quota.
                    with self.assertRaises(ScriptContractError) as missing:
                        tape._choose(wire, {**fields, "native_attempt_id": uuid4().hex}, observed)
                    self.assertEqual(missing.exception.code, "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED")
                    state = restarted.inspect()
                    self.assertEqual(state["sends"][0], before)
                    self.assertEqual(state["next_global_ordinal"], 3)
                    self.assertEqual(state["sends"][1]["state"], "builder_blocked")
                    self.assertFalse(state["native_authority"])
                    self.assertEqual(tape.document["entries"], [])
