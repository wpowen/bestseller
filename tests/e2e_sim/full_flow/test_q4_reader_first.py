"""Q4 RED contracts for main's serial offline run; never installs transport.

Only runtime structural tokens are used. Native pure prompt constructors define
the wire contract; these fixtures contain no manuscript or saved prompt dump.
The original-input binding is supplied by the coordinator, not inferred from a
generated premise. Scores, reasons and directions are explicit test opinions.
"""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import unittest
from uuid import uuid4

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
from scripts.full_flow.simulation.runtime_adapter import create_runtime_tape

from .test_contracts import ENDPOINTS, USAGE
from .test_quickstart_builders import blueprint

TOURNAMENT_BASES = (
    "concept_tournament_candidate",
    "concept_tournament_engine_kernel",
    "concept_tournament_finalist_judge",
    "concept_tournament_finalist_seriality_judge",
    "concept_tournament_judge",
    "concept_tournament_premise_judge",
    "concept_tournament_raw_idea_pool",
    "concept_tournament_raw_idea_rank",
    "concept_tournament_seriality",
    "concept_tournament_seriality_judge",
    "concept_tournament_story_judge",
)
PRODUCER_ALIASES = {
    "conception_finalize_reader_first_v1": "conception_finalize",
    "conception_finalize_reader_first_v1_repair": "conception_finalize_repair",
    "conception_story_spine_polish_reader_first_v1": "conception_story_spine_polish",
    "conception_story_spine_polish_reader_first_v1_repair": "conception_story_spine_polish_repair",
}
READER_PROTOCOLS = {
    "conception_reader_first_skeleton_v1": ("skeleton", "reader-first-review.v1"),
    "planner_reader_first_outline_review": ("outline", "rf-outline-review-v1"),
}
DIMENSIONS = (
    "character_emotion_drives_conflict",
    "reader_promise",
    "emotional_payoff",
    "character_distinctiveness",
)


def native_module():
    # Imported only when main executes these offline tests, never at collection.
    from bestseller.services import source_bound_planning_content

    return source_bound_planning_content


def binding():
    return native_module().new_reader_first_binding("original-input-token")


def tournament_material(base):
    """Explicit base-shaped materials; no schema traversal or generated scores."""
    materials = {
        "concept_tournament_candidate": {
            "concept": "candidate-token",
            "mechanism": "mechanism-token",
            "hook_question": "question-token?",
        },
        "concept_tournament_engine_kernel": {
            "protagonist_identity": "identity-token",
            "protagonist_private_desire": "desire-token",
            "protagonist_flaw": "flaw-token",
            "core_abnormality": "abnormality-token",
            "current_goal": "goal-token",
            "effective_resistance": "resistance-token",
            "failure_cost": "failure-cost-token",
            "success_cost": "success-cost-token",
            "irreversible_change": "change-token",
            "reader_promise": "promise-token",
            "scene_seeds": ["scene-token"],
            "post_reveal_scene_seeds": ["later-scene-token"],
            "deformable_loop": "loop-token",
            "expansion_axes": ["axis-token"],
            "story_architecture": "architecture-token",
        },
        "concept_tournament_raw_idea_pool": {"ideas": [{"concept": "idea-token"}]},
        "concept_tournament_raw_idea_rank": {"ranked": [{"idea_index": 0, "score": 0.2}]},
        "concept_tournament_seriality": {
            "repeatable_story_unit": "unit-token",
            "core_promise_invariant": "promise-token",
            "unit_families": ["family-token"],
            "progress_bar": "progress-token",
            "unit_frequency": "frequency-token",
            "unit_count_estimate": 4,
            "renewal_sources": ["renewal-token"],
            "accumulation_tracks": ["track-token"],
            "phase_transitions": ["transition-token"],
            "opposing_ecology": "opposition-token",
            "endgame_direction": "end-token",
        },
        "concept_tournament_premise_judge": {
            "verdicts": [{"passed": False, "reason": "authored-objection-token"}]
        },
        "concept_tournament_judge": {"freshness": 0.2, "reason": "authored-objection-token"},
        "concept_tournament_story_judge": {"score": 0.3, "reason": "authored-story-objection"},
        "concept_tournament_seriality_judge": {"score": 0.4, "reason": "authored-unit-objection"},
        "concept_tournament_finalist_judge": {"score": 0.2, "reason": "authored-final-objection"},
        "concept_tournament_finalist_seriality_judge": {
            "score": 0.1,
            "reason": "authored-final-unit-objection",
        },
    }
    return deepcopy(materials[base])


def alias_case(template, *, ordinal=37, language="zh", role=None):
    marker = binding()
    if template.startswith("concept_tournament_"):
        stage, native_role = "concept_tournament", "system"
        base = template.removesuffix("_reader_first_v1")
        material = tournament_material(base)
    else:
        stage = (
            "conception_finalize"
            if template.startswith("conception_finalize_")
            else "conception_story_spine_polish"
        )
        native_role, material = "user", None
    instruction = native_module().reader_first_instruction(marker, stage=stage, language=language)
    wire = {
        "model": "offline-q4",
        "messages": [{"role": role or native_role, "content": instruction + "\nrequest-token"}],
        "stream": True,
    }
    context = BuilderContext(
        blueprint(),
        call_index=ordinal,
        wire_body=wire,
        expected_wire_sha256=digest(wire),
        protocol_subject={"reader_first_planning": marker},
        submissions={} if material is None else {template + ":" + str(ordinal): material},
    )
    return context, material, wire


def reader_case(template, *, ordinal=43):
    checkpoint, version = READER_PROTOCOLS[template]
    marker = binding()
    if checkpoint == "skeleton":
        source = {
            "original_user_input": "original-input-token",
            "creation_inputs": {"mode": "runtime-token"},
            "candidate": {
                "tags": ["tag-token"],
                "title_profile": {},
                "title": "title-token",
                "premise": "source-A  source-B",
                "synopsis": "choice-token",
                "story_spine": {"who": "actor-token", "stakes": "relation-token"},
                "writing_profile": {"reader_first_planning": marker},
                "world_model": {"escaped/key": {"~leaf": "escaped-source-token"}},
                "concept_contract": {},
                "tournament_winner": None,
                "commercial_brief": {},
                "selected_hook_spec": None,
                "genre_intent_contract": None,
            },
        }
    else:
        # The current planner carries the original binding, not the raw input.
        source = {
            "book_spec": {"theme": "theme-token"},
            "world_spec": {"escaped/key": {"~leaf": "escaped-source-token"}},
            "cast_spec": {"characters": [{"name": "actor-token"}]},
            "volume_plan": [{"volume_number": 1}],
            "outline": {"chapters": [{"goal": "choice-token", "stakes": "relation-token"}]},
            "premise": "source-A  source-B",
            "original_input_binding": marker,
        }
    system, user = native_module().reader_first_review_prompts(
        marker, source=source, checkpoint=checkpoint
    )
    document = json.loads(user)
    # All four judgments are authored explicitly, including their low scores.
    material = {
        "schema": "reader-first-review.v1",
        "checkpoint": checkpoint,
        "source_sha256": document["source_sha256"],
        "config_sha256": marker["config_sha256"],
        "dimensions": {
            "character_emotion_drives_conflict": {
                "score": 0.2,
                "reason": "authored-conflict-objection",
                "evidence": [{"path": review_paths(template)[0], "quote": "source-A  source-B"}],
            },
            "reader_promise": {
                "score": 0.4,
                "reason": "authored-promise-objection",
                "evidence": [{"path": review_paths(template)[1], "quote": "choice-token"}],
            },
            "emotional_payoff": {
                "score": 0.3,
                "reason": "authored-payoff-objection",
                "evidence": [{"path": review_paths(template)[2], "quote": "relation-token"}],
            },
            "character_distinctiveness": {
                "score": 0.1,
                "reason": "authored-voice-objection",
                "evidence": [{"path": review_paths(template)[3], "quote": "escaped-source-token"}],
            },
        },
        "issues": [{"severity": "high", "reason": "authored-high-severity-objection"}],
        "options": ["authored-direction-one", "authored-direction-two"],
    }
    wire = {
        "model": "offline-q4",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": True,
    }
    return (
        BuilderContext(
            {},
            call_index=ordinal,
            wire_body=wire,
            expected_wire_sha256=digest(wire),
            protocol_subject={"reader_first_planning": marker, "prompt_version": version},
            submissions={template + ":" + str(ordinal): material},
        ),
        material,
        wire,
        source,
    )


def review_paths(template):
    if READER_PROTOCOLS[template][0] == "skeleton":
        return (
            "/candidate/premise",
            "/candidate/synopsis",
            "/candidate/story_spine/stakes",
            "/candidate/world_model/escaped~1key/~0leaf",
        )
    return (
        "/premise",
        "/outline/chapters/0/goal",
        "/outline/chapters/0/stakes",
        "/world_spec/escaped~1key/~0leaf",
    )


class Q4ReaderFirstAliasTests(unittest.TestCase):
    def error(self, code, template, context):
        with self.assertRaises(ScriptContractError) as raised:
            build_payload(template, context)
        self.assertEqual(raised.exception.code, code)

    def test_registry_covers_exact_seventeen_names(self):
        inventory = builder_inventory()
        covered = (
            set(inventory["producers"])
            | set(inventory["requires_explicit_physical_reply"])
            | set(inventory["keyflow_explicit_reply_protocols"])
        )
        expected = (
            {base + "_reader_first_v1" for base in TOURNAMENT_BASES}
            | set(PRODUCER_ALIASES)
            | set(READER_PROTOCOLS)
        )
        self.assertEqual(len(expected), 17)
        self.assertEqual(sorted(expected - covered), [])

    def test_eleven_aliases_reuse_only_explicit_base_payload_contracts(self):
        for base in TOURNAMENT_BASES:
            with self.subTest(base=base):
                template = base + "_reader_first_v1"
                context, authored, _ = alias_case(template)
                before = deepcopy(context)
                base_context = replace(context, submissions={base + ":37": authored})
                expected = build_payload(base, base_context)
                self.assertEqual(build_payload(template, context), expected)
                self.assertEqual(context, before)

    def test_new_template_slot_cannot_reuse_base_previous_ordinal_or_empty_reply(self):
        for base in TOURNAMENT_BASES:
            template = base + "_reader_first_v1"
            context, authored, _ = alias_case(template)
            for label, submissions in (
                ("base-slot", {base + ":37": authored}),
                ("previous-ordinal", {template + ":36": authored}),
                ("missing", {}),
                ("empty", {template + ":37": {}}),
            ):
                with self.subTest(base=base, material=label):
                    self.error(
                        "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED",
                        template,
                        replace(context, submissions=submissions),
                    )

    def test_alias_still_enforces_base_required_fields(self):
        template = "concept_tournament_candidate_reader_first_v1"
        context, authored, _ = alias_case(template)
        for field in ("concept", "mechanism", "hook_question"):
            material = deepcopy(authored)
            del material[field]
            with self.subTest(field=field):
                self.error(
                    "SCRIPT_REPLY_FIELDS_REQUIRED",
                    template,
                    replace(context, submissions={template + ":37": material}),
                )

    def test_finalize_spine_and_their_exact_json_repairs_reuse_blueprint(self):
        for template, base in PRODUCER_ALIASES.items():
            with self.subTest(template=template):
                context, _, _ = alias_case(template)
                self.assertEqual(build_payload(template, context), build_payload(base, context))

    def test_binding_is_required_and_must_equal_current_config_and_original_input(self):
        template = "concept_tournament_candidate_reader_first_v1"
        context, _, _ = alias_case(template)
        self.error(
            "SCRIPT_READER_FIRST_BINDING_REQUIRED",
            template,
            replace(context, protocol_subject=None),
        )
        for field, value in (
            ("protocol", "reader-first-planning.v99"),
            ("config_sha256", "0" * 64),
            ("user_input_sha256", "1" * 64),
        ):
            marker = {**context.protocol_subject["reader_first_planning"], field: value}
            with self.subTest(field=field):
                self.error(
                    "SCRIPT_READER_FIRST_BINDING_MISMATCH",
                    template,
                    replace(context, protocol_subject={"reader_first_planning": marker}),
                )

    def test_native_instruction_must_be_in_correct_role_and_stage(self):
        for template in ("concept_tournament_candidate_reader_first_v1", *PRODUCER_ALIASES):
            for corruption in ("missing", "wrong-stage", "quoted-in-assistant"):
                context, _, wire = alias_case(template)
                message = wire["messages"][0]
                if corruption == "missing":
                    message["content"] = "reader-first-planning.v1 request-token"
                elif corruption == "wrong-stage":
                    message["content"] = native_module().reader_first_instruction(
                        context.protocol_subject["reader_first_planning"], stage="unrelated-stage"
                    )
                else:
                    message["role"] = "assistant"
                with self.subTest(template=template, corruption=corruption):
                    self.error(
                        "SCRIPT_READER_FIRST_WIRE_INVALID",
                        template,
                        replace(context, expected_wire_sha256=digest(wire)),
                    )

    def test_tournament_accepts_native_developer_role_and_english_instruction(self):
        template = "concept_tournament_candidate_reader_first_v1"
        for role, language in (("developer", "zh"), ("system", "en")):
            with self.subTest(role=role, language=language):
                context, authored, _ = alias_case(template, role=role, language=language)
                self.assertEqual(build_payload(template, context)["payload"], authored)

    def test_actual_wire_hash_is_required_for_aliases_including_producers(self):
        for template in ("concept_tournament_candidate_reader_first_v1", *PRODUCER_ALIASES):
            context, _, wire = alias_case(template)
            wire["messages"][0]["content"] += "\nchanged-token"
            with self.subTest(template=template):
                self.error("SCRIPT_SOURCE_WIRE_MATCH_REQUIRED", template, context)

    def test_unknown_base_suffix_and_unannounced_annotation_stay_uncovered(self):
        context, _, _ = alias_case("concept_tournament_candidate_reader_first_v1")
        for template in (
            "concept_tournament_unknown_reader_first_v1",
            "concept_tournament_candidate_reader_first_v2",
            "concept_tournament_candidate_reader_first_v1_reader_first_v1",
            "concept_tournament_candidate_reader_first_v1_repair",
            "conception_finalize_reader_first_v1_repair_repair",
            "planner_unknown_reader_first_annotation",
        ):
            with self.subTest(template=template):
                self.error("SCRIPT_TEMPLATE_UNCOVERED", template, context)


class Q4ReaderFirstReviewTests(unittest.TestCase):
    def error(self, template, context, code="SCRIPT_READER_FIRST_REVIEW_INVALID"):
        with self.assertRaises(ScriptContractError) as raised:
            build_payload(template, context)
        self.assertEqual(raised.exception.code, code)

    def test_both_protocols_preserve_explicit_low_scores_and_return_raw_not_receipt(self):
        for template, (checkpoint, _) in READER_PROTOCOLS.items():
            with self.subTest(template=template):
                context, material, _, source = reader_case(template)
                before = deepcopy(context)
                result = build_payload(template, context)
                self.assertEqual(result, {"format": "json", "payload": material})
                self.assertEqual(context, before)
                receipt = native_module().parse_reader_first_review(
                    result["payload"],
                    source=source,
                    binding=context.protocol_subject["reader_first_planning"],
                    checkpoint=checkpoint,
                )
                self.assertEqual(receipt["status"], "failed")
                self.assertNotIn("status", result["payload"])
                self.assertNotIn("mean_score", result["payload"])

    def test_missing_top_level_field_never_gets_default_response(self):
        for template in READER_PROTOCOLS:
            context, material, _, _ = reader_case(template)
            for field in material:
                invalid = deepcopy(material)
                del invalid[field]
                with self.subTest(template=template, field=field):
                    self.error(template, replace(context, submissions={template + ":43": invalid}))

    def test_missing_dimension_or_any_score_reason_evidence_is_unknown(self):
        for template in READER_PROTOCOLS:
            context, material, _, _ = reader_case(template)
            for dimension in DIMENSIONS:
                for field in (None, "score", "reason", "evidence"):
                    invalid = deepcopy(material)
                    if field is None:
                        del invalid["dimensions"][dimension]
                    else:
                        del invalid["dimensions"][dimension][field]
                    with self.subTest(template=template, dimension=dimension, field=field):
                        self.error(
                            template, replace(context, submissions={template + ":43": invalid})
                        )

    def test_scores_must_be_authored_finite_numbers_and_evidence_nonempty(self):
        for template in READER_PROTOCOLS:
            context, material, _, _ = reader_case(template)
            for field, value in (
                ("score", True),
                ("score", "0.9"),
                ("score", -0.1),
                ("score", 1.1),
                ("reason", ""),
                ("reason", " "),
                ("evidence", []),
            ):
                invalid = deepcopy(material)
                invalid["dimensions"][DIMENSIONS[0]][field] = value
                with self.subTest(template=template, field=field, value=value):
                    self.error(template, replace(context, submissions={template + ":43": invalid}))
            for value in (float("nan"), float("inf"), -float("inf")):
                invalid = deepcopy(material)
                invalid["dimensions"][DIMENSIONS[0]]["score"] = value
                with self.subTest(template=template, nonfinite=repr(value)):
                    with self.assertRaises(ScriptContractError):
                        build_payload(
                            template, replace(context, submissions={template + ":43": invalid})
                        )

    def test_evidence_pointer_and_quote_are_exact_in_full_native_source(self):
        for template in READER_PROTOCOLS:
            context, material, _, _ = reader_case(template)
            for path, quote in (
                (review_paths(template)[0], "source-A source-B"),
                (review_paths(template)[1], "source-A  source-B"),
                ("/source/candidate/premise", "source-A  source-B"),
                ("/outline/chapters/00/goal", "choice-token"),
                ("/outline/chapters/1/goal", "choice-token"),
                ("/escaped~2key/~0leaf", "escaped-source-token"),
                ("/candidate", "source-A  source-B"),
                (review_paths(template)[0], ""),
            ):
                invalid = deepcopy(material)
                invalid["dimensions"][DIMENSIONS[0]]["evidence"] = [{"path": path, "quote": quote}]
                with self.subTest(template=template, path=path, quote=quote):
                    self.error(template, replace(context, submissions={template + ":43": invalid}))

    def test_checkpoint_schema_hashes_and_requested_version_cannot_be_substituted(self):
        for template in READER_PROTOCOLS:
            context, material, _, _ = reader_case(template)
            for field, value in (
                ("checkpoint", "other-checkpoint"),
                ("schema", "reader-first-review-receipt.v1"),
                ("source_sha256", "0" * 64),
                ("config_sha256", "1" * 64),
                ("fallback", True),
                ("status", "unknown"),
            ):
                invalid = {**material, field: value}
                with self.subTest(template=template, field=field):
                    self.error(template, replace(context, submissions={template + ":43": invalid}))
            subject = {**context.protocol_subject, "prompt_version": "rf-review-unknown-v99"}
            self.error(
                template,
                replace(context, protocol_subject=subject),
                "SCRIPT_READER_FIRST_VERSION_MISMATCH",
            )

    def test_options_and_issues_must_be_explicit_and_typed(self):
        for template in READER_PROTOCOLS:
            context, material, _, _ = reader_case(template)
            for field, value in (
                ("options", []),
                ("options", ["one"]),
                ("options", ["one", " "]),
                ("options", ["one", "two", "three", "four"]),
                ("issues", None),
                ("issues", [{"severity": "unknown", "reason": "reason-token"}]),
                ("issues", [{"severity": "high", "reason": ""}]),
            ):
                with self.subTest(template=template, field=field, value=value):
                    self.error(
                        template,
                        replace(
                            context, submissions={template + ":43": {**material, field: value}}
                        ),
                    )

    def test_judgments_reasons_and_directions_cannot_be_selected_from_wire(self):
        for template in READER_PROTOCOLS:
            context, material, wire, _ = reader_case(template)
            wire["authored-looking-data"] = {
                "score": 0.99,
                "reason": "copied-reason",
                "options": ["one", "two"],
            }
            context = replace(context, expected_wire_sha256=digest(wire))
            for field in ("score", "reason", "options", "issues"):
                invalid = deepcopy(material)
                selected = {"$wire": "/authored-looking-data/" + field}
                if field in ("score", "reason"):
                    invalid["dimensions"][DIMENSIONS[0]][field] = selected
                else:
                    invalid[field] = selected
                with self.subTest(template=template, field=field):
                    self.error(
                        template,
                        replace(context, submissions={template + ":43": invalid}),
                        "SCRIPT_VERDICT_MUST_BE_AUTHORED",
                    )

    def test_explicit_wire_source_quote_and_hash_bindings_are_allowed(self):
        for template in READER_PROTOCOLS:
            context, material, _, _ = reader_case(template)
            authored = deepcopy(material)
            authored["source_sha256"] = {
                "$wire": "/messages/1/content",
                "decode_json": True,
                "pointer": "/source_sha256",
            }
            authored["dimensions"][DIMENSIONS[0]]["evidence"][0]["quote"] = {
                "$wire": "/messages/1/content",
                "decode_json": True,
                "pointer": "/source" + review_paths(template)[0],
                "quote": "source-A  source-B",
            }
            with self.subTest(template=template):
                result = build_payload(
                    template, replace(context, submissions={template + ":43": authored})
                )
                self.assertEqual(result["payload"], material)

    def test_stale_complete_source_and_original_input_binding_are_rejected(self):
        for template in READER_PROTOCOLS:
            for corruption in ("source", "original-input", "binding"):
                context, material, wire, _ = reader_case(template)
                document = json.loads(wire["messages"][1]["content"])
                if corruption == "source":
                    if READER_PROTOCOLS[template][0] == "skeleton":
                        document["source"]["candidate"]["premise"] += " changed-token"
                    else:
                        document["source"]["premise"] += " changed-token"
                elif corruption == "original-input":
                    if READER_PROTOCOLS[template][0] == "skeleton":
                        document["source"]["original_user_input"] = "different-input-token"
                    else:
                        document["source"]["original_input_binding"] = {
                            **document["binding"],
                            "user_input_sha256": "3" * 64,
                        }
                    document["source_sha256"] = digest(document["source"])
                    material["source_sha256"] = document["source_sha256"]
                else:
                    document["binding"]["user_input_sha256"] = "2" * 64
                wire["messages"][1]["content"] = canonical(document)
                with self.subTest(template=template, corruption=corruption):
                    self.error(template, replace(context, expected_wire_sha256=digest(wire)))

    def test_review_uses_exact_new_template_and_physical_ordinal(self):
        for template in READER_PROTOCOLS:
            context, material, _, _ = reader_case(template)
            for submissions in ({}, {template + ":42": material}):
                with self.subTest(template=template, keys=tuple(submissions)):
                    self.error(
                        template,
                        replace(context, submissions=submissions),
                        "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED",
                    )


class Q4ReaderFirstRuntimeAdapterTests(unittest.TestCase):
    def catalog(self, template, *, includes_template):
        return TemplateCatalog(
            {
                "schema": INVENTORY_SCHEMA,
                "parse_failures": [],
                "dynamic_sites": [],
                "templates": [{"template": template, "historical_only": False}]
                if includes_template
                else [],
            }
        )

    def test_seventeen_exact_names_work_with_old_and_new_catalogues_without_identity_rewrite(self):
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        templates = [base + "_reader_first_v1" for base in TOURNAMENT_BASES]
        templates.extend(PRODUCER_ALIASES)
        templates.extend(READER_PROTOCOLS)
        for template in templates:
            for includes_template in (False, True):
                with self.subTest(template=template, catalogue_includes_template=includes_template):
                    context, _, wire, *_ = (
                        reader_case(template)
                        if template in READER_PROTOCOLS
                        else alias_case(template)
                    )
                    catalog = self.catalog(template, includes_template=includes_template)
                    before = deepcopy(catalog.inventory)
                    tape = create_runtime_tape(
                        catalog=catalog,
                        context_resolver=lambda observed, body, ctx=context: replace(
                            ctx, wire_body=body, call_index=observed.call_index
                        ),
                        usage_resolver=lambda observed, body: USAGE,
                        allowed_endpoints=ENDPOINTS,
                    )
                    observed = NativeObservation(
                        template, context.call_index, str(uuid4()), str(uuid4())
                    )
                    selected = tape._choose(wire, wire_fingerprints(wire), observed)
                    self.assertEqual(
                        json.loads(selected["content"]), build_payload(template, context)["payload"]
                    )
                    self.assertEqual(selected["finish_reason"], "stop")
                    self.assertFalse(selected.get("tool_calls"))
                    match = tape.document["entries"][0]["match"]
                    self.assertEqual(match["prompt_template"], template)
                    self.assertEqual(match["call_index"], context.call_index)
                    self.assertEqual(match["project_id"], observed.project_id)
                    self.assertEqual(match["workflow_run_id"], observed.workflow_run_id)
                    self.assertEqual(match["body_sha256"], digest(wire))
                    self.assertEqual(catalog.inventory, before)

    def test_invalid_wire_or_tool_channel_never_appends_script_entry(self):
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        for template in ("concept_tournament_candidate_reader_first_v1", *READER_PROTOCOLS):
            for failure in ("wire-hash", "tool-channel"):
                context, _, wire, *_ = (
                    reader_case(template) if template in READER_PROTOCOLS else alias_case(template)
                )
                if failure == "wire-hash":
                    context = replace(context, expected_wire_sha256="0" * 64)
                else:
                    wire["tools"] = [
                        {
                            "type": "function",
                            "function": {
                                "name": "submit_unknown",
                                "parameters": {"type": "object"},
                            },
                        }
                    ]
                    context = replace(context, expected_wire_sha256=digest(wire))
                tape = create_runtime_tape(
                    catalog=self.catalog(template, includes_template=False),
                    context_resolver=lambda observed, body, ctx=context: replace(
                        ctx, wire_body=body
                    ),
                    usage_resolver=lambda observed, body: USAGE,
                    allowed_endpoints=ENDPOINTS,
                )
                with self.subTest(template=template, failure=failure):
                    observed = NativeObservation(
                        template, context.call_index, str(uuid4()), str(uuid4())
                    )
                    with self.assertRaises(ScriptContractError) as raised:
                        tape._choose(wire, wire_fingerprints(wire), observed)
                    self.assertEqual(
                        raised.exception.code,
                        "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED"
                        if failure == "wire-hash"
                        else "SCRIPT_TOOL_PROTOCOL_UNCOVERED",
                    )
                    self.assertEqual(tape.document["entries"], [])
                    self.assertEqual(tape.builder_send_count, 0)

    def test_test_fixture_original_input_hash_is_not_generated_candidate_hash(self):
        context, _, _, source = reader_case("conception_reader_first_skeleton_v1")
        original_hash = hashlib.sha256(source["original_user_input"].encode()).hexdigest()
        self.assertEqual(
            context.protocol_subject["reader_first_planning"]["user_input_sha256"], original_hash
        )
        self.assertNotEqual(original_hash, digest(source["candidate"]))
