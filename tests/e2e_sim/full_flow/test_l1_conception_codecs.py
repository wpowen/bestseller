"""Main-run RED for the first two missing L1 calls and finite four-call routing.

Only native pure prompt constructors are used. No async producer, HTTP, model,
DB or application startup is invoked. No private manuscript is imported from
the authoring directory. Actual execution belongs to main after handoff.
"""

from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from uuid import uuid4

from scripts.full_flow.simulation.author_l1 import compile_private_bundle
from scripts.full_flow.simulation.checkpoint import PhysicalOrdinalStore
from scripts.full_flow.simulation.contracts import ScriptContractError, digest
from scripts.full_flow.simulation.private_config import PrivateConfigResolver
from scripts.full_flow.simulation.quickstart_builders import BuilderContext, build_payload

from .test_l1_authoring import authored
from .test_l1_missing_codecs import BEAT, READER, material, reader_case, short_case, with_material

METHOD = "concept_methodology"
EFFECT = "conception_source_effect_contract"
EFFECT_REPAIR = EFFECT + "_repair"
INVALID = "SCRIPT_CONCEPTION_AUX_REPLY_INVALID"
SOURCE_MISMATCH = "SCRIPT_CONCEPTION_AUX_SOURCE_MISMATCH"
WIRE_INVALID = "SCRIPT_CONCEPTION_AUX_WIRE_INVALID"
VERSION_INVALID = "SCRIPT_CONCEPTION_AUX_VERSION_UNCOVERED"


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def methodology_material():
    # Exactly the eight output keys in build_methodology_user_prompt. Heat and
    # generation provenance are native input/receipt fields, not SDK output.
    return {
        "audience_orientation": "neutral",
        "mindset": "authored-direction",
        "mechanism_types": ["authored-mechanism-type"],
        "reader_promise_axis": "authored-promise-axis",
        "shuangdian_cadence": ["authored-cadence"],
        "design_axes": ["authored-design-axis"],
        "anti_patterns": ["authored-anti-pattern"],
        "rationale": "authored-rationale-without-market-evidence-claim",
    }


def auxiliary_case(template, *, ordinal=101, applicability="not_applicable", source=None):
    from bestseller.services.llm import LLMCompletionRequest

    source = source if source is not None else "private-source-token-A  private-source-token-B"
    if template == METHOD:
        from bestseller.services.concept_methodology_agent import (
            ConceptMethodology,
            build_methodology_system_prompt,
            build_methodology_user_prompt,
        )

        reply = methodology_material()
        fallback = ConceptMethodology(
            **reply,
            market_signals=[],
            source="static_fallback",
            heat_source="missing",
            methodology_generation_source="static_fallback",
        )
        system = build_methodology_system_prompt(language="zh")
        user = build_methodology_user_prompt(
            genre="genre-token",
            sub_genre="subgenre-token",
            description="description-token",
            premise=source,
            audience_orientation="neutral",
            market_signals=[],
            trend_keywords=[],
            fallback=fallback,
            language="zh",
            allowed_modernity="modern",
            tone_preference="",
            effect_skills=(),
            cost_style="standard",
        )
        role = "planner"
    else:
        from bestseller.services.source_effect_contract import (
            render_source_effect_extraction_prompt,
        )

        system, user = render_source_effect_extraction_prompt(text=source, origin="source")
        role = "critic"
        reply = {
            "applicability": applicability,
            "applicability_reason": "explicit-author-extraction-reason",
            "mechanism_quote": "",
            "causal_mode": "unknown",
        }
        if applicability == "applicable":
            reply.update(
                mechanism_quote={"$wire": "/messages/1/content", "quote": source},
                causal_mode="causes_target_action",
            )
        if template == EFFECT_REPAIR:
            from bestseller.services.llm_closed_loop import LLMGateFinding, build_repair_user_prompt

            user = build_repair_user_prompt(
                original_user_prompt=user,
                findings=[
                    LLMGateFinding(
                        code="JSON_PARSE_ERROR",
                        severity="critical",
                        path="$",
                        message="private-diagnostic-token",
                        repair_action="Return the explicitly authored four-field object.",
                    )
                ],
                language="zh",
            )
    request = LLMCompletionRequest(
        logical_role=role,
        system_prompt=system,
        user_prompt=user,
        fallback_response="{}",
        prompt_template=template,
        # The actual two native callers omit prompt_version. Do not relabel
        # these requests as 1.0, an empty string, or a new synthetic version.
    )
    wire = {
        "messages": [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ]
    }
    return BuilderContext(
        {},
        call_index=ordinal,
        wire_body=wire,
        expected_wire_sha256=digest(wire),
        protocol_subject={
            "logical_role": request.logical_role,
            "prompt_version": request.prompt_version,
            "language": "zh",
            "source_text": source,
            "source_sha256": sha(source),
        },
        submissions={template + ":" + str(ordinal): reply},
    )


def changed_wire(context, mutate):
    wire = deepcopy(context.wire_body)
    mutate(wire)
    return replace(context, wire_body=wire, expected_wire_sha256=digest(wire))


class ConceptionAuxCodecRedTests(unittest.TestCase):
    def error(self, code, template, context):
        with self.assertRaises(ScriptContractError) as raised:
            build_payload(template, context)
        self.assertEqual(raised.exception.code, code)

    def test_methodology_preserves_exact_eight_authored_fields_without_provenance(self):
        context = auxiliary_case(METHOD)
        before = deepcopy(context)
        self.assertEqual(
            build_payload(METHOD, context),
            {"format": "json", "payload": methodology_material()},
        )
        self.assertEqual(context, before)

    def test_source_effect_not_applicable_and_unknown_are_distinct_authored_results(self):
        for verdict in ("not_applicable", "unknown"):
            with self.subTest(verdict=verdict):
                context = auxiliary_case(EFFECT, applicability=verdict)
                self.assertEqual(
                    build_payload(EFFECT, context),
                    {"format": "json", "payload": material(context, EFFECT)},
                )

    def test_source_effect_applicable_quote_is_bound_only_to_actual_source_text(self):
        context = auxiliary_case(EFFECT, applicability="applicable")
        reply = material(context, EFFECT)
        reply["mechanism_quote"] = context.protocol_subject["source_text"]
        self.assertEqual(build_payload(EFFECT, context)["payload"], reply)
        for mode in (
            "perceives_information",
            "changes_target_state",
            "communicates_information",
            "other",
        ):
            with self.subTest(mode=mode):
                value = material(context, EFFECT)
                value["causal_mode"] = mode
                self.assertEqual(
                    build_payload(EFFECT, with_material(context, EFFECT, value))["payload"][
                        "causal_mode"
                    ],
                    mode,
                )

    def test_only_one_literal_native_effect_repair_name_is_supported(self):
        context = auxiliary_case(EFFECT_REPAIR, applicability="unknown")
        self.assertEqual(
            build_payload(EFFECT_REPAIR, context)["payload"], material(context, EFFECT_REPAIR)
        )
        for name in (METHOD + "_repair", EFFECT_REPAIR + "_repair", EFFECT + "_candidate_v2_fake"):
            with self.subTest(template=name):
                self.error("SCRIPT_TEMPLATE_UNCOVERED", name, context)

    def test_explicit_null_version_is_not_missing_or_relabelled(self):
        for template in (METHOD, EFFECT, EFFECT_REPAIR):
            context = auxiliary_case(template)
            self.assertIsNone(context.protocol_subject["prompt_version"])
            for version in ("1.0", "", "null", 0):
                with self.subTest(template=template, version=version):
                    self.error(
                        VERSION_INVALID,
                        template,
                        replace(
                            context,
                            protocol_subject={
                                **context.protocol_subject,
                                "prompt_version": version,
                            },
                        ),
                    )
            subject = dict(context.protocol_subject)
            del subject["prompt_version"]
            self.error(VERSION_INVALID, template, replace(context, protocol_subject=subject))

    def test_wrong_role_system_origin_or_message_position_is_not_native_wire(self):
        for template in (METHOD, EFFECT, EFFECT_REPAIR):
            context = auxiliary_case(template)
            wrong_role = "critic" if template == METHOD else "planner"
            self.error(
                WIRE_INVALID,
                template,
                replace(
                    context,
                    protocol_subject={**context.protocol_subject, "logical_role": wrong_role},
                ),
            )
            for mutation in (
                lambda wire: wire["messages"][0].update(content="wrong-system-token"),
                lambda wire: wire["messages"][1].update(role="assistant"),
                lambda wire: wire["messages"].append(dict(wire["messages"][1])),
            ):
                with self.subTest(template=template):
                    self.error(WIRE_INVALID, template, changed_wire(context, mutation))
        from bestseller.services.source_effect_contract import (
            render_source_effect_extraction_prompt,
        )

        context = auxiliary_case(EFFECT)
        system, user = render_source_effect_extraction_prompt(
            text=context.protocol_subject["source_text"], origin="candidate"
        )
        changed = changed_wire(context, lambda wire: wire["messages"][1].update(content=user))
        self.assertEqual(system, context.wire_body["messages"][0]["content"])
        self.error(WIRE_INVALID, EFFECT, changed)

    def test_source_bytes_and_hash_are_not_normalized_or_reselected(self):
        for template in (METHOD, EFFECT, EFFECT_REPAIR):
            context = auxiliary_case(template)
            original = context.protocol_subject["source_text"]
            variants = (
                {"source_sha256": "0" * 64},
                {"source_text": original.replace("  ", " ")},
                {"source_text": "other-source", "source_sha256": sha("other-source")},
            )
            for change in variants:
                with self.subTest(template=template, field=next(iter(change))):
                    self.error(
                        SOURCE_MISMATCH,
                        template,
                        replace(context, protocol_subject={**context.protocol_subject, **change}),
                    )
            changed = changed_wire(
                context,
                lambda wire: wire["messages"][1].update(
                    content=wire["messages"][1]["content"].replace(original, "other-source")
                ),
            )
            self.error(SOURCE_MISMATCH, template, changed)

    def test_missing_wire_stale_wire_tools_and_wrong_slot_fail_before_reply(self):
        for template in (METHOD, EFFECT, EFFECT_REPAIR):
            context = auxiliary_case(template)
            for changed in (
                replace(context, wire_body=None),
                replace(context, expected_wire_sha256="0" * 64),
            ):
                self.error("SCRIPT_SOURCE_WIRE_MATCH_REQUIRED", template, changed)
            self.error(
                "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED", template, replace(context, submissions={})
            )
            self.error(
                "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED", template, replace(context, call_index=102)
            )
            self.error(
                "SCRIPT_TOOL_PROTOCOL_UNCOVERED",
                template,
                changed_wire(context, lambda wire: wire.update(tools=[{"type": "function"}])),
            )

    def test_each_methodology_field_is_explicit_and_cannot_be_native_default(self):
        context = auxiliary_case(METHOD)
        for field in methodology_material():
            value = material(context, METHOD)
            del value[field]
            with self.subTest(field=field):
                self.error(INVALID, METHOD, with_material(context, METHOD, value))
        for field in ("mindset", "reader_promise_axis", "rationale"):
            for bad in ("", None, True, [], {"$wire": "/messages/1/content"}):
                value = material(context, METHOD)
                value[field] = bad
                with self.subTest(field=field, kind=type(bad).__name__):
                    self.error(INVALID, METHOD, with_material(context, METHOD, value))
        for field in ("mechanism_types", "shuangdian_cadence", "design_axes", "anti_patterns"):
            for bad in ([], [""], [None], "coercible-string", [{"$wire": "/messages/1/content"}]):
                value = material(context, METHOD)
                value[field] = bad
                with self.subTest(field=field, kind=type(bad).__name__):
                    self.error(INVALID, METHOD, with_material(context, METHOD, value))

    def test_methodology_orientation_is_explicit_and_native_receipts_cannot_be_authored(self):
        context = auxiliary_case(METHOD)
        for orientation in ("male", "female", "neutral"):
            value = material(context, METHOD)
            value["audience_orientation"] = orientation
            self.assertEqual(
                build_payload(METHOD, with_material(context, METHOD, value))["payload"], value
            )
        for orientation in ("", "NEUTRAL", True, {"$wire": "/schema/enum/0"}):
            value = material(context, METHOD)
            value["audience_orientation"] = orientation
            self.error(INVALID, METHOD, with_material(context, METHOD, value))
        for field in (
            "market_signals", "source", "heat_source", "methodology_generation_source", "approved"
        ):
            value = material(context, METHOD)
            value[field] = "forbidden-output-field"
            self.error(INVALID, METHOD, with_material(context, METHOD, value))

    def test_effect_all_four_fields_and_literal_verdicts_are_required(self):
        context = auxiliary_case(EFFECT)
        for field in material(context, EFFECT):
            value = material(context, EFFECT)
            del value[field]
            with self.subTest(field=field):
                self.error(INVALID, EFFECT, with_material(context, EFFECT, value))
        for field, bad in (
            ("applicability", True),
            ("applicability", "pass"),
            ("applicability", {"$wire": "/schema/enum/0"}),
            ("applicability_reason", ""),
            ("causal_mode", "causes-anything"),
            ("causal_mode", {"$wire": "/schema/default"}),
            ("mechanism_quote", None),
        ):
            value = material(context, EFFECT)
            value[field] = bad
            with self.subTest(field=field, kind=type(bad).__name__):
                self.error(INVALID, EFFECT, with_material(context, EFFECT, value))
        for field in ("contract_id", "llm_run_id", "text_hash", "known", "operations", "grade"):
            value = material(context, EFFECT)
            value[field] = "forbidden-native-receipt"
            self.error(INVALID, EFFECT, with_material(context, EFFECT, value))

    def test_empty_quote_only_belongs_to_authored_unknown_or_not_applicable(self):
        for verdict in ("not_applicable", "unknown"):
            context = auxiliary_case(EFFECT, applicability=verdict)
            value = material(context, EFFECT)
            value["causal_mode"] = "causes_target_action"
            self.error(INVALID, EFFECT, with_material(context, EFFECT, value))
            value = material(auxiliary_case(EFFECT, applicability="applicable"), EFFECT)
            value["applicability"] = verdict
            value["causal_mode"] = "unknown"
            self.error(INVALID, EFFECT, with_material(context, EFFECT, value))
        context = auxiliary_case(EFFECT, applicability="applicable")
        value = material(context, EFFECT)
        value["mechanism_quote"] = ""
        self.error(INVALID, EFFECT, with_material(context, EFFECT, value))

    def test_effect_quote_cannot_be_literal_or_instruction_only(self):
        context = auxiliary_case(EFFECT, applicability="applicable")
        value = material(context, EFFECT)
        value["mechanism_quote"] = context.protocol_subject["source_text"]
        self.error(
            "SCRIPT_SOURCE_VALUE_REQUIRES_NATIVE_WIRE", EFFECT, with_material(context, EFFECT, value)
        )
        value = material(context, EFFECT)
        value["mechanism_quote"] = {"$wire": "/messages/1/content", "quote": "causes_target_action"}
        self.error(SOURCE_MISMATCH, EFFECT, with_material(context, EFFECT, value))
        value["mechanism_quote"]["quote"] = "absent-quote-token"
        self.error("SCRIPT_WIRE_QUOTE_MISMATCH", EFFECT, with_material(context, EFFECT, value))


def config_row(template, context):
    row = {
        "template": template,
        "template_ordinal": 1,
        "chapter_number": context.chapter_number,
        "wire_sha256": digest(context.wire_body),
        "protocol_subject": deepcopy(context.protocol_subject),
        "material": material(context, template),
    }
    if context.current_body is not None:
        row.update(current_body=context.current_body, source_body_sha256=context.source_body_sha256)
    return row


class L1RequiredCodecDispatchRedTests(unittest.TestCase):
    def test_explicit_native_binding_recipes_need_no_preinvented_source_identity(self):
        cases = (
            (METHOD, auxiliary_case(METHOD), "concept-methodology-input.v1"),
            (EFFECT, auxiliary_case(EFFECT), "source-effect-source.v1"),
            (EFFECT_REPAIR, auxiliary_case(EFFECT_REPAIR), "source-effect-source.v1"),
            (BEAT, short_case(BEAT), "fanqie-beat-source.v1"),
            (READER, reader_case(), "reader-judge-body.v1"),
        )
        for name, context, binding in cases:
            with self.subTest(template=name):
                row = config_row(name, context)
                row["protocol_subject"] = {
                    "native_wire_binding": binding,
                    "logical_role": context.protocol_subject["logical_role"],
                    "prompt_version": context.protocol_subject["prompt_version"],
                }
                if name == READER:
                    row["protocol_subject"]["text_cap_chars"] = 8000
                resolver = PrivateConfigResolver(
                    {"schema": "normal-quickstart-private.v1", "story": {}, "replies": [row]}
                )
                ticket = {
                    "global_ordinal": 19,
                    "template_ordinal": 1,
                    "native_operation_key": sha("operation"),
                    "native_input_hash": sha("input"),
                }
                rebound = resolver(
                    SimpleNamespace(prompt_template=name, call_index=19), context.wire_body, ticket
                )
                self.assertEqual(build_payload(name, rebound), build_payload(name, context))
                # This opt-in selects a named native decoder, not a schema field,
                # score, enum, verdict or unbounded template wildcard.
                self.assertEqual(rebound.submissions[name + ":19"], material(context, name))
                for unknown in ("auto", "*", "future-v9"):
                    with self.assertRaises(ScriptContractError) as raised:
                        build_payload(
                            name,
                            replace(rebound, protocol_subject={
                                **rebound.protocol_subject, "native_wire_binding": unknown,
                            }),
                        )
                    self.assertEqual(
                        raised.exception.code, "SCRIPT_NATIVE_SUBJECT_BINDING_UNCOVERED"
                    )

    def test_four_main_path_payloads_are_selected_by_durable_slot_not_process_counter(self):
        cases = [
            (METHOD, auxiliary_case(METHOD)),
            (EFFECT, auxiliary_case(EFFECT)),
            (BEAT, short_case(BEAT)),
            (READER, reader_case()),
        ]
        config = {
            "schema": "normal-quickstart-private.v1",
            "story": {},
            "replies": [config_row(name, context) for name, context in cases],
        }
        # Explicit /private/tmp avoids main's pytest basetemp inside checkout.
        with tempfile.TemporaryDirectory(
            prefix="l1-four-codecs-red-", dir="/private/tmp"
        ) as directory:
            base = Path(directory)
            private, repo = base / "private", base / "repo"
            private.mkdir(mode=0o700)
            repo.mkdir()
            resolver = PrivateConfigResolver(config)
            for ordinal, (name, context) in enumerate(cases, 1):
                store = PhysicalOrdinalStore(
                    private / "ordinals.json",
                    private_root=private,
                    repository_root=repo,
                    run_id="unit-l1-four-codecs",
                    config_sha256=resolver.config_sha256,
                )
                store.initialize()
                ticket = store.claim_send(
                    template=name,
                    native_operation_key=sha("operation-" + name),
                    native_input_hash=sha("input-" + name),
                    native_attempt_id=uuid4().hex,
                    wire_sha256=digest(context.wire_body),
                )
                self.assertEqual(ticket["global_ordinal"], ordinal)
                self.assertEqual(ticket["template_ordinal"], 1)
                observed = SimpleNamespace(prompt_template=name, call_index=ordinal)
                rebound = resolver(observed, context.wire_body, ticket)
                self.assertEqual(build_payload(name, rebound), build_payload(name, context))
            self.assertEqual(store.inspect()["unresolved_ordinals"], [1, 2, 3, 4])
            # No transport was consumed or business acceptance produced.
            ticket = store.claim_send(
                template=READER,
                native_operation_key=sha("operation-" + READER),
                native_input_hash=sha("input-" + READER),
                native_attempt_id=uuid4().hex,
                wire_sha256=digest(cases[-1][1].wire_body),
            )
            with self.assertRaises(ScriptContractError) as raised:
                resolver(
                    SimpleNamespace(prompt_template=READER, call_index=5),
                    cases[-1][1].wire_body,
                    ticket,
                )
            self.assertEqual(raised.exception.code, "SCRIPT_AUTHORED_REPLY_NOT_UNIQUE")
            self.assertEqual(store.inspect()["unresolved_ordinals"], [1, 2, 3, 4, 5])

    def test_source_effect_repair_has_one_explicit_slot_and_no_repeating_default(self):
        context = auxiliary_case(EFFECT_REPAIR, applicability="unknown")
        config = {
            "schema": "normal-quickstart-private.v1", "story": {},
            "replies": [config_row(EFFECT_REPAIR, context)],
        }
        resolver = PrivateConfigResolver(config)
        ticket = {
            "global_ordinal": 23, "template_ordinal": 1,
            "native_operation_key": sha("operation"), "native_input_hash": sha("input"),
        }
        bound = resolver(
            SimpleNamespace(prompt_template=EFFECT_REPAIR, call_index=23), context.wire_body, ticket
        )
        self.assertEqual(
            build_payload(EFFECT_REPAIR, bound)["payload"]["applicability"], "unknown"
        )
        for changed in ({"template_ordinal": 2}, {"global_ordinal": 24}):
            with self.subTest(field=next(iter(changed))):
                with self.assertRaises(ScriptContractError) as raised:
                    resolver(
                        SimpleNamespace(prompt_template=EFFECT_REPAIR, call_index=23),
                        context.wire_body, {**ticket, **changed},
                    )
                self.assertEqual(
                    raised.exception.code,
                    "SCRIPT_AUTHORED_REPLY_NOT_UNIQUE" if "template_ordinal" in changed
                    else "SCRIPT_OBSERVATION_CONTEXT_MISMATCH",
                )


class L1UnsetVersionAuthoringRedTests(unittest.TestCase):
    def test_native_unset_versions_compile_without_invented_one_point_zero(self):
        for name in (METHOD, EFFECT, EFFECT_REPAIR):
            with self.subTest(template=name):
                value = authored()
                context = auxiliary_case(name)
                value["execution_plan"] = [{
                    "template": name, "template_ordinal": 1,
                    "prompt_version": None, "source_site": "native-pure-source",
                }]
                value["replies"] = [config_row(name, context)]
                bundle = compile_private_bundle(value)
                self.assertIsNone(bundle["execution_plan"][0]["prompt_version"])
                self.assertIsNone(
                    bundle["config"]["replies"][0]["protocol_subject"]["prompt_version"]
                )
                self.assertFalse(
                    any(row.get("template") == name for row in bundle["report"]["blockers"])
                )
                # Other absent L1 slots must still block the full plan.
                self.assertEqual(bundle["report"]["state"], "BLOCKED")

    def test_unset_version_is_not_a_general_exception_for_versioned_templates(self):
        for name in (BEAT, READER, "unknown_future_template"):
            value = authored()
            value["execution_plan"][0].update(template=name, prompt_version=None)
            value["replies"][0]["template"] = name
            with self.subTest(template=name):
                with self.assertRaises(ScriptContractError) as raised:
                    compile_private_bundle(value)
                self.assertEqual(raised.exception.code, "SCRIPT_AUTHORING_PROTOCOL_VERSION_INVALID")

    def test_plan_version_and_reply_version_must_agree_for_all_four_required_calls(self):
        cases = (
            (METHOD, auxiliary_case(METHOD)), (EFFECT, auxiliary_case(EFFECT)),
            (BEAT, short_case(BEAT)), (READER, reader_case()),
        )
        for name, context in cases:
            with self.subTest(template=name):
                value = authored()
                value["execution_plan"] = [{
                    "template": name, "template_ordinal": 1,
                    "prompt_version": context.protocol_subject["prompt_version"],
                    "source_site": "native-pure-source",
                }]
                row = config_row(name, context)
                row["protocol_subject"]["prompt_version"] = "wrong-version"
                value["replies"] = [row]
                bundle = compile_private_bundle(value)
                self.assertTrue(any(
                    block["code"] == "SCRIPT_AUTHORING_PROTOCOL_VERSION_MISMATCH"
                    and block.get("template") == name
                    for block in bundle["report"]["blockers"]
                ))

    def test_absent_default_conception_calls_are_declared_in_full_plan_blockers(self):
        bundle = compile_private_bundle(authored())
        missing = {row.get("template") for row in bundle["report"]["blockers"]
                   if row["code"] == "SCRIPT_AUTHORING_REQUIRED_TEMPLATE_SLOT_MISSING"}
        self.assertTrue({METHOD, EFFECT} <= missing)
