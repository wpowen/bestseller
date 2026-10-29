from copy import deepcopy
from functools import partial
import hashlib
import json
import unittest

from scripts.full_flow.simulation.contracts import ScriptContractError, digest
from scripts.full_flow.simulation.quickstart_builders import (
    BuilderContext,
    build_payload,
    builder_inventory,
    outline_batch,
    render_chapter,
)


def blueprint():
    # Structural atoms only, constructed at runtime; no committed manuscript.
    actor = {
        "name": "actor",
        "occupation": "trade",
        "background": "history",
        "fear": "loss",
        "flaw": "haste",
        "strength": "craft",
        "age": 31,
        "gender": "unknown",
    }
    value = {
        key: key
        for key in (
            "genre",
            "sub_genre",
            "platform",
            "setting",
            "goal",
            "trigger",
            "stakes",
            "advantage",
            "development",
            "content_mode",
            "reader_rationale",
            "career_ledger",
            "profession_boundary",
            "romance_mode",
            "relationship_tension",
            "era",
            "narrative_drive",
            "channel",
            "title",
            "premise",
            "synopsis",
            "theme",
            "logline",
        )
    }
    for key in ("protagonist", "antagonist", "ally"):
        value[key] = {**actor, "name": key}
    value.update(
        tags=["tag"],
        audiences=["audience"],
        power_tiers=[],
        tone_keywords=["tone"],
        review_adjudications=[],
        title_candidates=["one", "two"],
        rules=[{"name": "rule", "description": "description"}],
        locations=[{"name": "place"}],
    )
    value["chapters"] = [
        {
            "number": number,
            **{
                key: key + str(number)
                for key in (
                    "title",
                    "goal",
                    "obstacle",
                    "choice",
                    "consequence",
                    "hook",
                    "location",
                )
            },
            "reveals": ["reveal" + str(number)],
            "target_word_count": 1000,
            "causal_contract": {"choice": "choice" + str(number)},
            "methodology_contract": {"intent": "intent" + str(number)},
            "scenes": [{"scene_number": 1}],
            "prose_atoms": [
                {
                    "action": "action" + str(number),
                    "reaction": "reaction" + str(number),
                    "result": "result" + str(number),
                }
            ],
        }
        for number in range(1, 5)
    ]
    return value


class QuickstartBuilderTests(unittest.TestCase):
    def error(self, code, fn):
        with self.assertRaises(ScriptContractError) as raised:
            fn()
        self.assertEqual(raised.exception.code, code)

    def test_market_cast_world_and_final_share_one_runtime_blueprint(self):
        context = BuilderContext(blueprint())
        final = build_payload("conception_finalize", context)["payload"]
        self.assertEqual(final["story_spine"]["wants"], context.story["goal"])
        self.assertEqual(
            final["writing_profile"]["market"],
            build_payload("conception_market", context)["payload"],
        )
        self.assertEqual(final["writing_profile"]["character"]["protagonist_name"], "protagonist")
        self.assertNotIn("approved", final)
        self.assertNotIn("promoted", final)
        self.assertNotIn("grade", final)

    def test_foundation_objects_have_native_volume_wrapper_input_and_exact_cast(self):
        context = BuilderContext(blueprint())
        result = build_payload("planner_volume_plan", context)
        self.assertEqual(result["format"], "asset:volume_plan")
        self.assertEqual(result["payload"][0]["volume_title"], context.story["title"])
        self.assertEqual(result["payload"][0]["chapter_count_target"], 4)
        cast = build_payload("planner_cast_spec", context)["payload"]
        self.assertEqual(cast["protagonist"]["name"], context.story["protagonist"]["name"])

    def test_numeric_outline_family_is_finite_and_preserves_source_contracts(self):
        context = BuilderContext(blueprint())
        result = build_payload("planner_volume_1_chapter_outline_batch_2_3", context)["payload"]
        self.assertEqual([r["chapter_number"] for r in result["chapters"]], [2, 3])
        self.assertEqual(
            result["chapters"][0]["causal_contract"],
            context.story["chapters"][1]["causal_contract"],
        )
        self.error(
            "SCRIPT_VOLUME_PROFILE_UNCOVERED",
            lambda: build_payload("planner_volume_2_chapter_outline", context),
        )
        self.error(
            "SCRIPT_OUTLINE_RANGE_INVALID",
            lambda: build_payload("planner_volume_1_chapter_outline_batch_2_5", context),
        )

    def test_missing_outline_source_contract_is_not_replaced_with_approval(self):
        story = blueprint()
        story["chapters"][0].pop("causal_contract")
        self.error(
            "SCRIPT_OUTLINE_SOURCE_CONTRACT_REQUIRED", lambda: outline_batch(BuilderContext(story))
        )

    def test_primary_review_is_not_generated_from_missing_opinion_or_wrong_send(self):
        context = BuilderContext(blueprint())
        self.error(
            "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED", lambda: build_payload("chapter_review", context)
        )
        context = BuilderContext(
            blueprint(), call_index=2, submissions={"chapter_review:1": {"verdict": "rewrite"}}
        )
        self.error(
            "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED", lambda: build_payload("chapter_review", context)
        )

    def test_knowledge_requires_current_body_hash_and_explicit_payload(self):
        value = {"character_states": [], "canon_facts": [], "timeline_events": []}
        context = BuilderContext(
            blueprint(),
            current_body="runtime-body",
            source_body_sha256="0" * 64,
            submissions={"chapter_feedback_extraction:1": value},
        )
        self.error(
            "SCRIPT_CURRENT_BODY_BINDING_REQUIRED",
            lambda: build_payload("chapter_feedback_extraction", context),
        )
        context = BuilderContext(
            blueprint(),
            current_body="runtime-body",
            source_body_sha256=hashlib.sha256(b"runtime-body").hexdigest(),
            submissions={"chapter_feedback_extraction:1": value},
        )
        self.assertEqual(build_payload("chapter_feedback_extraction", context)["payload"], value)

    def test_prose_atoms_are_composed_once_and_never_padded(self):
        story = blueprint()
        body = render_chapter(BuilderContext(story))
        self.assertEqual(body, "action1。reaction1。result1。")
        story["chapters"][0]["prose_atoms"] *= 2
        self.error("SCRIPT_PROSE_REPEATED_ATOM", lambda: render_chapter(BuilderContext(story)))

    def test_blueprint_and_bound_replies_are_not_mutated(self):
        story = blueprint()
        before = deepcopy(story)
        build_payload("conception_finalize", BuilderContext(story))
        self.assertEqual(story, before)

    def test_unimplemented_template_never_defaults_to_json_or_pass(self):
        self.error(
            "SCRIPT_TEMPLATE_UNCOVERED",
            lambda: build_payload("never_registered_new_stage", BuilderContext(blueprint())),
        )
        self.assertFalse(builder_inventory()["normal_http_driver_included"])


def native_cast_case(template, *, count=4, person_index=0, encoding=None, suffix=""):
    """Runtime structural material and the real native part contracts, no app run.

    Import only when the coordinator executes these offline tests. Schema
    constructors validate the explicit test material; they never generate it.
    """
    from bestseller.services import source_cast_parts as native
    from bestseller.services.source_bound_planning_content import CONTENT_POLICY
    from bestseller.services.source_bound_planning_submission import planning_submission_contract

    actors = [
        {
            "name": "source-actor-" + str(index),
            "role": "protagonist" if index == 0 else "supporting",
            "goal": "authored-goal-" + str(index),
            "age": 30 + index,
            "gender": "unknown",
            "pronoun_set_zh": None,
            "pronoun_set_en": None,
        }
        for index in range(count)
    ]
    projection = {
        "protagonist": actors[0],
        "antagonist": actors[1] if count > 1 else None,
        "supporting_cast": actors[2:],
    }
    people = native.roster(projection)
    packet = {"protocol": CONTENT_POLICY, "kind": "cast_spec", "projection": projection}
    document = {
        "schema": native.POLICY,
        "source_packet_sha256": digest(packet),
        "frozen_source_packet": packet,
        "reference_full_cast_contract": {
            "kind": "cast_spec",
            "expected_root": "object",
            "native_submission": planning_submission_contract("cast_spec"),
        },
    }
    if template == "planner_cast_person_part":
        person = people[person_index]
        document.update(
            part_contract=native.person_schema(person),
            current_part={
                "part_id": person["id"],
                "source_slot": person["source_slot"],
                "source_person": deepcopy(person["source"]),
                "required_name": person["name"],
                "instruction": "Generate only the current typed person.",
            },
            already_generated_people=[],
        )
        material = {"person": deepcopy(actors[person_index])}
    else:
        document.update(
            part_contract=native.links_schema(people),
            current_part={"instruction": "Select every existing person exactly once."},
            already_generated_people=[
                {"part_id": row["id"], "person": deepcopy(actor)}
                for row, actor in zip(people, actors, strict=True)
            ],
        )
        # The private author chooses the mapping explicitly from this wire's
        # people, including the choice of no antagonist. No schema enum picker.
        material = {
            "antagonist_forces": [],
            "conflict_map": [],
            "layout": {
                "protagonist": document["already_generated_people"][0]["part_id"],
                "antagonist": None,
                "supporting_cast": [
                    row["part_id"] for row in document["already_generated_people"][1:]
                ],
            },
        }
    wire_document = document
    if encoding is not None:
        from bestseller.services.cast_links_transport import NOTICE, POLICY
        from bestseller.services.lossless_source_encoding import encode_lossless_source

        wire_document = {
            "schema": POLICY,
            "expansion_instruction": NOTICE,
            "logical_document": encode_lossless_source(document, version=encoding),
            "current_part_contract": deepcopy(document["part_contract"]),
        }
    wire = {
        "model": "offline-cast",
        "messages": [
            {"role": "system", "content": "Strict JSON text for the current Cast part."},
            {"role": "user", "content": json.dumps(wire_document) + suffix},
        ],
        "stream": True,
    }
    return document, material, wire


class NativeCastPartsTests(unittest.TestCase):
    def context(self, template, material, wire, *, ordinal=17):
        return BuilderContext(
            {},
            call_index=ordinal,
            wire_body=wire,
            expected_wire_sha256=digest(wire),
            submissions={template + ":" + str(ordinal): deepcopy(material)},
        )

    def error(self, code, fn):
        with self.assertRaises(ScriptContractError) as raised:
            fn()
        self.assertEqual(raised.exception.code, code)

    def test_cast_parts_register_only_two_explicit_text_json_reply_protocols(self):
        inventory = builder_inventory()
        for template in ("planner_cast_person_part", "planner_cast_links_part"):
            self.assertEqual(inventory["requires_explicit_physical_reply"].get(template), "json")
            self.assertNotIn(template, inventory["producers"])
        self.error(
            "SCRIPT_TEMPLATE_UNCOVERED",
            lambda: build_payload("planner_cast_person_part_repair", BuilderContext({})),
        )

    def test_person_preserves_private_material_and_native_optional_fields_without_defaults(self):
        from bestseller.services.source_cast_parts import validate_part

        template = "planner_cast_person_part"
        for index in (0, 2):
            with self.subTest(person_index=index):
                document, material, wire = native_cast_case(template, person_index=index)
                material["person"].pop("pronoun_set_en")
                before = deepcopy((material, wire))
                result = build_payload(template, self.context(template, material, wire))
                self.assertEqual(result, {"format": "json", "payload": material})
                validate_part(result["payload"], document["part_contract"])
                self.assertNotIn("pronoun_set_en", result["payload"]["person"])
                self.assertEqual((material, wire), before)

    def test_cast_parts_require_private_material_current_wire_hash_and_exact_physical_slot(self):
        for template in ("planner_cast_person_part", "planner_cast_links_part"):
            with self.subTest(template=template):
                _, material, wire = native_cast_case(template)
                context = self.context(template, material, wire)
                context.submissions.clear()
                self.error(
                    "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED", partial(build_payload, template, context)
                )
                context.submissions[template + ":16"] = material
                self.error(
                    "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED", partial(build_payload, template, context)
                )
                self.error(
                    "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED",
                    partial(
                        build_payload,
                        template,
                        BuilderContext({}, submissions={template + ":1": material}),
                    ),
                )
                context = self.context(template, material, wire)
                wire["model"] = "different-physical-wire"
                self.error(
                    "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED", partial(build_payload, template, context)
                )

    def test_person_rejects_wrong_name_missing_goal_and_type_coercion(self):
        template = "planner_cast_person_part"
        _, material, wire = native_cast_case(template)
        for key, value, code in (
            ("name", "different-person", "SCRIPT_CAST_PART_IDENTITY_MISMATCH"),
            ("goal", "", "SCRIPT_CAST_PART_SCHEMA_MISMATCH"),
            ("age", "31", "SCRIPT_CAST_PART_SCHEMA_MISMATCH"),
        ):
            with self.subTest(field=key):
                invalid = deepcopy(material)
                invalid["person"][key] = value
                self.error(
                    code, partial(build_payload, template, self.context(template, invalid, wire))
                )
        material["person"].pop("goal")
        self.error(
            "SCRIPT_CAST_PART_SCHEMA_MISMATCH",
            lambda: build_payload(template, self.context(template, material, wire)),
        )

    def test_native_part_contract_and_person_identity_cannot_be_replaced_by_schema_claims(self):
        template = "planner_cast_person_part"
        document, material, wire = native_cast_case(template)
        for target in ("part_contract", "required_name", "part_id"):
            with self.subTest(target=target):
                invalid = deepcopy(document)
                if target == "part_contract":
                    invalid[target] = {"type": "object"}
                    code = "SCRIPT_CAST_PART_CONTRACT_MISMATCH"
                else:
                    invalid["current_part"][target] = "invented-identity"
                    code = "SCRIPT_CAST_PART_IDENTITY_MISMATCH"
                changed_wire = deepcopy(wire)
                changed_wire["messages"][1]["content"] = json.dumps(invalid)
                self.error(
                    code,
                    partial(
                        build_payload, template, self.context(template, material, changed_wire)
                    ),
                )

    def test_links_preserve_explicit_layout_for_every_native_person_without_role_inference(self):
        from bestseller.services.source_cast_parts import assemble, roster

        template = "planner_cast_links_part"
        for count in (1, 4, 6):
            with self.subTest(people=count):
                document, material, wire = native_cast_case(template, count=count)
                before = deepcopy((material, wire))
                result = build_payload(template, self.context(template, material, wire))
                self.assertEqual(result, {"format": "json", "payload": material})
                people = roster(document["frozen_source_packet"]["projection"])
                persons = {
                    row["part_id"]: row["person"] for row in document["already_generated_people"]
                }
                assembled = assemble(people, persons, result["payload"])
                self.assertIsNone(assembled["antagonist"])
                self.assertEqual(len(assembled["supporting_cast"]), count - 1)
                self.assertEqual((material, wire), before)

    def test_links_reject_foreign_duplicate_omitted_ids_and_missing_authored_fields(self):
        template = "planner_cast_links_part"
        _, material, wire = native_cast_case(template)
        for mode in ("foreign", "duplicate", "omitted", "missing_layout", "missing_conflict_map"):
            with self.subTest(mode=mode):
                invalid = deepcopy(material)
                if mode == "foreign":
                    invalid["layout"]["supporting_cast"][0] = "not-on-native-wire"
                elif mode == "duplicate":
                    invalid["layout"]["supporting_cast"][0] = invalid["layout"]["protagonist"]
                elif mode == "omitted":
                    invalid["layout"]["supporting_cast"].pop()
                else:
                    invalid.pop(mode.removeprefix("missing_"))
                code = (
                    "SCRIPT_CAST_PART_SCHEMA_MISMATCH"
                    if mode.startswith("missing_")
                    else "SCRIPT_CAST_PART_LAYOUT_MISMATCH"
                )
                self.error(
                    code, partial(build_payload, template, self.context(template, invalid, wire))
                )

    def test_links_bind_explicit_layout_ids_from_wire_people_not_schema_enums(self):
        template = "planner_cast_links_part"
        _, material, wire = native_cast_case(template)
        expected = deepcopy(material)

        def part_id(index):
            return {
                "$wire": "/messages/1/content",
                "decode_json": True,
                "pointer": "/already_generated_people/" + str(index) + "/part_id",
            }

        material["layout"] = {
            "protagonist": part_id(0),
            "antagonist": None,
            "supporting_cast": [part_id(index) for index in (1, 2, 3)],
        }
        result = build_payload(template, self.context(template, material, wire))
        self.assertEqual(result["payload"], expected)

    def test_links_decode_native_lossless_v1_v2_v3_and_keep_retry_suffix_bound(self):
        from bestseller.services.cast_links_transport import decode

        template = "planner_cast_links_part"
        suffix = "\nRetry the same part; preserve its source people."
        for version in (1, 2, 3):
            with self.subTest(version=version):
                document, material, wire = native_cast_case(
                    template, encoding=version, suffix=suffix
                )
                self.assertEqual(decode(wire["messages"][1]["content"]), (document, suffix))
                result = build_payload(template, self.context(template, material, wire))
                self.assertEqual(result["payload"], material)
                context = self.context(template, material, wire)
                wire["messages"][1]["content"] += "changed suffix"
                self.error(
                    "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED", partial(build_payload, template, context)
                )

    def test_cast_wire_rejects_duplicate_json_keys_and_changed_source_packet_hash(self):
        for template in ("planner_cast_person_part", "planner_cast_links_part"):
            with self.subTest(template=template):
                document, material, wire = native_cast_case(template)
                document["source_packet_sha256"] = "0" * 64
                wire["messages"][1]["content"] = json.dumps(document)
                self.error(
                    "SCRIPT_CAST_PART_CONTRACT_MISMATCH",
                    partial(build_payload, template, self.context(template, material, wire)),
                )
                wire["messages"][1]["content"] = '{"schema":"one","schema":"two"}'
                self.error(
                    "SCRIPT_CAST_PART_WIRE_INVALID",
                    partial(build_payload, template, self.context(template, material, wire)),
                )
