"""RED contracts for main: two four-part planners and reader_judge/1.2.

No application, async producer, transport, DB or model is invoked here. Main
alone executes these tests. Native pure request builders supply current wire;
body/plot tokens exist only in memory. Explicit low scores are not approvals.
The existing registry remains unchanged until main returns this batch's RED.
"""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
import unittest
from uuid import UUID, uuid4

from scripts.full_flow.simulation.contracts import ScriptContractError, canonical, digest
from scripts.full_flow.simulation.quickstart_builders import BuilderContext, build_payload

from .test_q4_reader_first import binding
from .test_q4_short_reader_review import short_source

BEAT = "planner_fanqie_beat_sheet"
FIELDS = "planner_structured_field_correction"
READER = "reader_judge"
SHORT_INVALID = "SCRIPT_SHORT_PLANNER_REPLY_INVALID"
READER_INVALID = "SCRIPT_READER_JUDGE_REPLY_INVALID"
AXES = (
    "opening_pull",
    "payoff_density",
    "emotional_impact",
    "anti_abandon",
    "ai_taste",
    "human_voice",
)


def beat_material():
    # All optional native fields are explicitly authored as well. Missing
    # values must not become Pydantic defaults or a new schema-chosen story.
    return {
        "title": "title-token",
        "logline": "logline-token",
        "pov": "third_person",
        "unlock_milestone_segment": 2,
        "ideology_compact": {},
        "beats": [
            {
                "segment_number": number,
                "beat_role": "role-token-" + str(number),
                "purpose": "purpose-token-" + str(number),
                "payoff": "payoff-token",
                "emotional_turn": "turn-token",
                "thesis_vector": "",
                "opening_contract": {},
                "unlock_contract": {},
                "ability_cost_contract": {},
                "payoff_contract": {},
                "closure_contract": {},
                "continuity_contract": {},
            }
            for number in range(1, 5)
        ],
    }


def short_case(template, *, ordinal=91):
    from bestseller.services import fanqie_beat_recovery as beat
    from bestseller.services import fanqie_outline_contract_completion as fields

    marker = binding()
    source = short_source(marker)
    project = SimpleNamespace(
        id=UUID(source["selected_source"]["project_id"]),
        target_chapters=4,
        language="zh",
        metadata_json={"reader_first_planning": deepcopy(marker)},
    )
    if template == BEAT:
        request = beat.build_current_request(
            project, source["selected_source"], workflow_run_id=uuid4()
        )
        material, source_sha = beat_material(), request.metadata["beat_source_sha256"]
    else:
        candidate = deepcopy(source["base_outline"])
        candidate["chapters"][0]["causal_contract"]["pressure"] = ""
        target = "/chapters/0/causal_contract/pressure"
        targets = {target: fields._registered(target)}
        request = fields._request(
            project,
            source,
            candidate,
            [{"code": "CHAPTER_CAUSAL_PRESSURE_WEAK", "location": target}],
            targets,
            uuid4(),
            0,
            0,
            None,
        )
        material = {target: "explicit-private-pressure-token"}
        source_sha = request.metadata["outline_completion_source_sha256"]
    wire = {
        "messages": [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ]
    }
    context = BuilderContext(
        {},
        call_index=ordinal,
        wire_body=wire,
        expected_wire_sha256=digest(wire),
        protocol_subject={
            "logical_role": request.logical_role,
            "prompt_version": request.prompt_version,
            "reader_first_planning": deepcopy(marker),
            "reader_first_stage": request.metadata["reader_first_stage"],
            "source_sha256": source_sha,
        },
        submissions={template + ":" + str(ordinal): material},
    )
    return context


def reader_case(*, ordinal=95, cap=8000):
    from bestseller.services import reader_judge

    body = "  " + "\n".join("private-token-" + str(n) for n in range(64)) + "  "
    excerpt = body.strip()[:cap] if cap > 0 else body.strip()
    wire = {
        "messages": [
            {"role": "system", "content": reader_judge._SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"第1章正文：\n{excerpt}\n\n按 system 指定 JSON 输出评分。",  # noqa: RUF001 -- Native wire punctuation.
            },
        ]
    }
    return BuilderContext(
        {},
        call_index=ordinal,
        chapter_number=1,
        current_body=body,
        source_body_sha256=hashlib.sha256(body.encode()).hexdigest(),
        wire_body=wire,
        expected_wire_sha256=digest(wire),
        protocol_subject={"logical_role": "critic", "prompt_version": "1.2", "text_cap_chars": cap},
        submissions={
            READER + ":" + str(ordinal): {
                "opening_pull": 0.0,
                "payoff_density": 0.1,
                "emotional_impact": 0.2,
                "anti_abandon": 0.3,
                "ai_taste": 0.4,
                "human_voice": 0.5,
                "comment": "explicit-low-score",
            }
        },
    )


def material(context, template):
    return deepcopy(context.submissions[template + ":" + str(context.call_index)])


def with_material(context, template, value):
    return replace(context, submissions={template + ":" + str(context.call_index): value})


def with_document(context, change):
    from bestseller.services.lossless_source_encoding import (
        decode_lossless_source,
        encode_lossless_source,
    )
    from bestseller.services.source_bound_planning_content import OVERFLOW_V4_EXPANSION_INSTRUCTION

    wire = deepcopy(context.wire_body)
    prefix = OVERFLOW_V4_EXPANSION_INSTRUCTION + "\n"
    user = wire["messages"][1]["content"]
    assert user.startswith(prefix)
    envelope = json.loads(user[len(prefix) :])
    document = decode_lossless_source(envelope["planning_document"])
    change(document, envelope)
    envelope["planning_document"] = encode_lossless_source(document, version=4)
    wire["messages"][1]["content"] = prefix + canonical(envelope)
    return replace(context, wire_body=wire, expected_wire_sha256=digest(wire))


class ShortPlannerCodecRedTests(unittest.TestCase):
    def error(self, code, template, context):
        with self.assertRaises(ScriptContractError) as raised:
            build_payload(template, context)
        self.assertEqual(raised.exception.code, code)

    def test_exact_native_v4_requests_preserve_explicit_authored_material(self):
        for template, version in (
            (BEAT, "fq-beats-v1.reader-first-v1"),
            (FIELDS, "fq-fields-v1.reader-first-v1"),
        ):
            with self.subTest(template=template):
                context = short_case(template)
                before = deepcopy(context)
                self.assertEqual(context.protocol_subject["prompt_version"], version)
                result = build_payload(template, context)
                self.assertEqual(result, {"format": "json", "payload": material(context, template)})
                self.assertEqual(context, before)

    def test_missing_wire_and_stale_hash_are_not_template_or_default_success(self):
        for template in (BEAT, FIELDS):
            context = short_case(template)
            for value in (
                replace(context, wire_body=None),
                replace(context, expected_wire_sha256="0" * 64),
            ):
                with self.subTest(template=template, missing=value.wire_body is None):
                    self.error("SCRIPT_SOURCE_WIRE_MATCH_REQUIRED", template, value)

    def test_absent_reply_and_wrong_physical_slot_remain_missing(self):
        for template in (BEAT, FIELDS):
            context = short_case(template)
            for value in (replace(context, submissions={}), replace(context, call_index=92)):
                with self.subTest(template=template, ordinal=value.call_index):
                    self.error("SCRIPT_SOURCE_BOUND_REPLY_REQUIRED", template, value)

    def test_unknown_or_non_four_part_version_is_not_an_alias(self):
        for template in (BEAT, FIELDS):
            context = short_case(template)
            for version in ("unknown", "fq-fields-v2.reader-first-v1"):
                with self.subTest(template=template, version=version):
                    self.error(
                        "SCRIPT_SHORT_PLANNER_VERSION_UNCOVERED",
                        template,
                        replace(
                            context,
                            protocol_subject={
                                **context.protocol_subject,
                                "prompt_version": version,
                            },
                        ),
                    )

    def test_role_marker_and_source_hash_must_match_current_native_packet(self):
        for template in (BEAT, FIELDS):
            context = short_case(template)
            changes = [
                {"logical_role": "critic"},
                {"source_sha256": "f" * 64},
                {"reader_first_stage": "wrong-stage"},
                {
                    "reader_first_planning": {
                        **context.protocol_subject["reader_first_planning"],
                        "user_input_sha256": "e" * 64,
                    }
                },
            ]
            for change in changes:
                with self.subTest(template=template, field=next(iter(change))):
                    self.error(
                        "SCRIPT_SHORT_PLANNER_BINDING_MISMATCH",
                        template,
                        replace(context, protocol_subject={**context.protocol_subject, **change}),
                    )

    def test_reencoded_source_change_cannot_reuse_authored_source_hash(self):
        context = short_case(BEAT)
        changed = with_document(
            context, lambda doc, env: doc["source"].update(premise="changed-token")
        )
        self.error("SCRIPT_SHORT_PLANNER_BINDING_MISMATCH", BEAT, changed)

    def test_native_output_contract_cannot_be_replaced_by_permissive_schema(self):
        for template, field in ((BEAT, "current_output_contract"), (FIELDS, "response_schema")):
            with self.subTest(template=template):
                context = with_document(
                    short_case(template), lambda doc, env, field=field: env.update({field: {}})
                )
                self.error("SCRIPT_SHORT_PLANNER_WIRE_INVALID", template, context)

    def test_unknown_encoding_or_extra_tool_protocol_is_rejected(self):
        for template in (BEAT, FIELDS):
            context = short_case(template)
            wire = deepcopy(context.wire_body)
            wire["tools"] = [{"type": "function", "function": {"name": "unknown"}}]
            with self.subTest(template=template):
                self.error(
                    "SCRIPT_TOOL_PROTOCOL_UNCOVERED",
                    template,
                    replace(context, wire_body=wire, expected_wire_sha256=digest(wire)),
                )

    def test_beat_schema_defaults_cannot_complete_missing_authored_fields(self):
        context = short_case(BEAT)
        for field in ("pov", "unlock_milestone_segment", "ideology_compact"):
            with self.subTest(field=field):
                value = material(context, BEAT)
                del value[field]
                self.error(SHORT_INVALID, BEAT, with_material(context, BEAT, value))
        value = material(context, BEAT)
        del value["beats"][0]["payoff"]
        self.error(SHORT_INVALID, BEAT, with_material(context, BEAT, value))

    def test_beat_wrong_order_count_pov_and_authority_fields_are_rejected(self):
        context = short_case(BEAT)
        for change in (
            lambda v: v["beats"].pop(),
            lambda v: v["beats"][0].update(segment_number=2),
            lambda v: v.update(pov="first_person"),
            lambda v: v.update(approved=True),
        ):
            value = material(context, BEAT)
            change(value)
            self.error(SHORT_INVALID, BEAT, with_material(context, BEAT, value))

    def test_field_completion_requires_exact_targets_and_explicit_nonempty_value(self):
        context = short_case(FIELDS)
        key = next(iter(material(context, FIELDS)))
        for value in (
            {},
            {key: ""},
            {key: []},
            {key: "literal", "/chapters/1/goal": "extra"},
            {key: {"$wire": "/schema/default"}},
        ):
            with self.subTest(value_type=type(value.get(key)).__name__):
                self.error(SHORT_INVALID, FIELDS, with_material(context, FIELDS, value))

    def test_completion_cannot_change_already_authored_candidate_value(self):
        context = short_case(FIELDS)
        changed = with_document(
            context,
            lambda doc, env: doc["current_candidate"]["chapters"][0]["causal_contract"].update(
                pressure="existing-private-value"
            ),
        )
        self.error("SCRIPT_SHORT_PLANNER_WIRE_INVALID", FIELDS, changed)

    def test_unregistered_suffix_remains_unknown(self):
        for template in (BEAT, FIELDS):
            self.error("SCRIPT_TEMPLATE_UNCOVERED", template + "_repair", short_case(template))


class ReaderJudgeCodecRedTests(unittest.TestCase):
    def error(self, code, context):
        with self.assertRaises(ScriptContractError) as raised:
            build_payload(READER, context)
        self.assertEqual(raised.exception.code, code)

    def test_six_explicit_low_dimensions_are_preserved_without_neutral_fallback(self):
        context = reader_case()
        result = build_payload(READER, context)
        self.assertEqual(result, {"format": "json", "payload": material(context, READER)})
        self.assertEqual(result["payload"]["opening_pull"], 0.0)
        self.assertNotIn("prose_quality_score", result["payload"])
        self.assertNotIn("used_llm", result["payload"])

    def test_current_body_hash_and_exact_native_capped_excerpt_are_both_checked(self):
        context = reader_case(cap=31)
        self.assertEqual(build_payload(READER, context)["payload"], material(context, READER))
        for value in (
            replace(context, source_body_sha256="0" * 64),
            replace(context, current_body=context.current_body + "changed"),
        ):
            self.error("SCRIPT_CURRENT_BODY_BINDING_REQUIRED", value)
        wire = deepcopy(context.wire_body)
        wire["messages"][1]["content"] += " changed-suffix"
        self.error(
            "SCRIPT_READER_JUDGE_BODY_MISMATCH",
            replace(context, wire_body=wire, expected_wire_sha256=digest(wire)),
        )

    def test_version_and_role_are_explicit_and_not_revision_aliases(self):
        context = reader_case()
        self.error(
            "SCRIPT_READER_JUDGE_VERSION_UNCOVERED",
            replace(
                context, protocol_subject={**context.protocol_subject, "prompt_version": "1.0"}
            ),
        )
        self.error(
            "SCRIPT_READER_JUDGE_WIRE_INVALID",
            replace(
                context, protocol_subject={**context.protocol_subject, "logical_role": "planner"}
            ),
        )

    def test_missing_reply_wrong_slot_or_missing_wire_stays_error(self):
        context = reader_case()
        self.error("SCRIPT_SOURCE_BOUND_REPLY_REQUIRED", replace(context, submissions={}))
        self.error("SCRIPT_SOURCE_BOUND_REPLY_REQUIRED", replace(context, call_index=96))
        self.error("SCRIPT_SOURCE_WIRE_MATCH_REQUIRED", replace(context, wire_body=None))

    def test_all_six_scores_are_authored_numbers_not_booleans_clamps_or_defaults(self):
        context = reader_case()
        for key in AXES:
            value = material(context, READER)
            del value[key]
            with self.subTest(field=key, kind="missing"):
                self.error(READER_INVALID, with_material(context, READER, value))
        for score in (True, "0.9", -0.1, 1.01, None, {"$wire": "/schema/default"}):
            value = material(context, READER)
            value["opening_pull"] = score
            with self.subTest(kind=type(score).__name__):
                self.error(READER_INVALID, with_material(context, READER, value))

    def test_comment_is_explicit_and_no_native_receipt_fields_are_added(self):
        context = reader_case()
        for change in (
            lambda v: v.pop("comment"),
            lambda v: v.update(comment="x" * 31),
            lambda v: v.update(grade="pass"),
            lambda v: v.update(used_llm=True),
        ):
            value = material(context, READER)
            change(value)
            self.error(READER_INVALID, with_material(context, READER, value))
