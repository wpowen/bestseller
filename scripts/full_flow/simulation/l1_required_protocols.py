"""Literal L1 SDK contracts with private authored values and native wire identity.

Only detached native prompt/schema/decoding helpers are used. No workflow,
transport, model, database, authority row or automatic positive judgment.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202, RUF001 -- Strict JSON boundary/native delimiters.
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import math

from . import reader_first_protocols as reader
from .contracts import canonical, digest, need
from .wire_material import bind_authored_material

METHOD = "concept_methodology"
EFFECT = "conception_source_effect_contract"
EFFECT_REPAIR = "conception_source_effect_contract_repair"
BEAT = "planner_fanqie_beat_sheet"
FIELDS = "planner_structured_field_correction"
READER = "reader_judge"
UNSET_VERSION_TEMPLATES = frozenset({METHOD, EFFECT, EFFECT_REPAIR})
VERSIONS = {
    METHOD: None, EFFECT: None, EFFECT_REPAIR: None,
    BEAT: "fq-beats-v1.reader-first-v1",
    FIELDS: "fq-fields-v1.reader-first-v1", READER: "1.2",
}
BINDINGS = {
    METHOD: "concept-methodology-input.v1",
    EFFECT: "source-effect-source.v1", EFFECT_REPAIR: "source-effect-source.v1",
    BEAT: "fanqie-beat-source.v1", READER: "reader-judge-body.v1",
}
_METHOD_FIELDS = {
    "audience_orientation", "mindset", "mechanism_types", "reader_promise_axis",
    "shuangdian_cadence", "design_axes", "anti_patterns", "rationale",
}
_EFFECT_FIELDS = {"applicability", "applicability_reason", "mechanism_quote", "causal_mode"}
_AXES = {"opening_pull", "payoff_density", "emotional_impact", "anti_abandon", "ai_taste",
         "human_voice"}


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _text(value):
    return type(value) is str and bool(value.strip())


def _wire(context, code):
    wire = context.wire_body
    need(isinstance(wire, Mapping) and context.expected_wire_sha256 == digest(wire),
         "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED")
    need(not wire.get("tools"), "SCRIPT_TOOL_PROTOCOL_UNCOVERED")
    messages = wire.get("messages")
    need(type(messages) is list and len(messages) == 2
         and all(type(m) is dict and type(m.get("content")) is str for m in messages)
         and [m.get("role") for m in messages] == ["system", "user"], code)
    need(len(canonical(wire).encode()) <= 32_000_000, "SCRIPT_RESPONSE_UNBOUNDED")
    return messages[0]["content"], messages[1]["content"]


def _material(template, context):
    value = context.submissions.get(template + ":" + str(context.call_index))
    need(isinstance(value, Mapping), "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED")
    need(len(canonical(value).encode()) <= 512_000, "SCRIPT_RESPONSE_UNBOUNDED")
    return deepcopy(dict(value))


def _subject(template, context):
    subject = context.protocol_subject
    need(isinstance(subject, Mapping), "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED")
    subject = deepcopy(dict(subject))
    if "native_wire_binding" in subject:
        need(template in BINDINGS and subject["native_wire_binding"] == BINDINGS[template],
             "SCRIPT_NATIVE_SUBJECT_BINDING_UNCOVERED")
    return subject


def _json(text, code):
    try:
        value = reader._decoder().decode(text)
    except (ValueError, TypeError, RecursionError):
        need(False, code)
    need(type(value) is dict, code)
    return value


def _aux_source(template, context, subject, system, user):
    code = "SCRIPT_CONCEPTION_AUX_WIRE_INVALID"
    if template == METHOD:
        from bestseller.services.concept_methodology_agent import build_methodology_system_prompt

        matches = [language for language in ("zh", "en")
                   if system == build_methodology_system_prompt(language=language)]
        need(len(matches) == 1, code)
        language = matches[0]
        need(subject.get("language", language) == language, code)
        start, end = (("\nPremise: ", "\nReader orientation: ") if language == "en"
                      else ("\n前提：", "\n读者取向："))
        need(user.count(start) == 1 and user.count(end) == 1
             and "Creation controls are authoritative: " in user, code)
        before, rest = user.split(start)
        source, after = rest.split(end)
        need(bool(before) and bool(after), code)
    else:
        from bestseller.services.source_effect_contract import render_source_effect_extraction_prompt

        expected_system, prefix = render_source_effect_extraction_prompt(text="", origin="source")
        need(system == expected_system and user.startswith(prefix), code)
        source = user[len(prefix):]
        if template == EFFECT_REPAIR:
            separator = "\n\n---\n"
            need(source.count(separator) == 1, code)
            source, suffix = source.split(separator)
            need(suffix.startswith(("【系统修复诊断 - 上一轮输出未通过校验】",
                                    "[SYSTEM REPAIR DIAGNOSTICS - previous output failed validation]"))
                 and suffix.endswith("\n"), code)
    need(_text(source), "SCRIPT_CONCEPTION_AUX_SOURCE_MISMATCH")
    if "native_wire_binding" not in subject:
        need(subject.get("source_text") == source and subject.get("source_sha256") == _sha(source),
             "SCRIPT_CONCEPTION_AUX_SOURCE_MISMATCH")
    else:
        # Explicit optional pins are never silently replaced by decoder output.
        need(subject.get("source_text", source) == source
             and subject.get("source_sha256", _sha(source)) == _sha(source),
             "SCRIPT_CONCEPTION_AUX_SOURCE_MISMATCH")
    return source


def auxiliary_submission(template, context):
    system, user = _wire(context, "SCRIPT_CONCEPTION_AUX_WIRE_INVALID")
    subject = _subject(template, context)
    need("prompt_version" in subject and subject["prompt_version"] is None,
         "SCRIPT_CONCEPTION_AUX_VERSION_UNCOVERED")
    need(subject.get("logical_role") == ("planner" if template == METHOD else "critic"),
         "SCRIPT_CONCEPTION_AUX_WIRE_INVALID")
    source = _aux_source(template, context, subject, system, user)
    value = _material(template, context)
    code = "SCRIPT_CONCEPTION_AUX_REPLY_INVALID"
    if template == METHOD:
        need(set(value) == _METHOD_FIELDS, code)
        need(type(value["audience_orientation"]) is str
             and value["audience_orientation"] in {"male", "female", "neutral"}, code)
        need(all(_text(value[key]) for key in ("mindset", "reader_promise_axis", "rationale")), code)
        need(all(type(value[key]) is list and bool(value[key]) and all(_text(v) for v in value[key])
                 for key in ("mechanism_types", "shuangdian_cadence", "design_axes", "anti_patterns")),
             code)
        return value
    need(set(value) == _EFFECT_FIELDS, code)
    need(type(value["applicability"]) is str
         and value["applicability"] in {"applicable", "not_applicable", "unknown"}
         and _text(value["applicability_reason"]) and type(value["causal_mode"]) is str
         and value["causal_mode"] in {"causes_target_action", "perceives_information",
                                     "changes_target_state", "communicates_information", "other",
                                     "unknown"}, code)
    if value["applicability"] != "applicable":
        need(value["mechanism_quote"] == "" and value["causal_mode"] == "unknown", code)
        return value
    quote = value["mechanism_quote"]
    need((type(quote) is dict and "$wire" in quote) or _text(quote), code)
    need(value["causal_mode"] != "unknown", code)
    payload, _ = bind_authored_material(value, context.wire_body,
                                        expected_wire_sha256=context.expected_wire_sha256)
    need(_text(payload["mechanism_quote"]) and payload["mechanism_quote"] in source,
         "SCRIPT_CONCEPTION_AUX_SOURCE_MISMATCH")
    return payload


def _short_document(user):
    from bestseller.services.source_bound_planning_content import OVERFLOW_V4_EXPANSION_INSTRUCTION
    from bestseller.services.lossless_source_encoding import decode_lossless_source

    code = "SCRIPT_SHORT_PLANNER_WIRE_INVALID"
    prefix = OVERFLOW_V4_EXPANSION_INSTRUCTION + "\n"
    need(user.startswith(prefix), code)
    envelope = _json(user[len(prefix):], code)
    try:
        document = decode_lossless_source(envelope["planning_document"])
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        need(False, code)
    need(type(document) is dict, code)
    return document, envelope


def _short_binding(template, context, subject, system, document):
    from bestseller.services import source_bound_planning_content as native
    from bestseller.services import fanqie_beat_recovery as beat
    from bestseller.services import fanqie_outline_contract_completion as fields

    code = "SCRIPT_SHORT_PLANNER_BINDING_MISMATCH"
    stage = "fanqie_beat" if template == BEAT else "fanqie_contract_completion"
    base_system = beat.SYSTEM if template == BEAT else fields.SYSTEM
    marker = subject.get("reader_first_planning")
    if "native_wire_binding" in subject:
        offset = system.find('{"binding":')
        need(offset >= 0 and system.find('{"binding":', offset + 1) < 0, code)
        try:
            envelope, _ = reader._decoder().raw_decode(system[offset:])
            observed = envelope["binding"]
        except (ValueError, TypeError, KeyError, RecursionError):
            need(False, code)
        need(marker is None or marker == observed, code)
        marker = observed
    try:
        checked = native.reader_first_binding({"reader_first_planning": marker})
        systems = [native.reader_first_instruction(checked, language=lang, stage=stage)
                   + "\n" + base_system for lang in ("zh", "en")]
    except (ValueError, TypeError, KeyError, OSError):
        need(False, code)
    need(system in systems and subject.get("logical_role") == "planner", code)
    source = document.get("source")
    need(type(source) is dict and bool(source), code)
    if "native_wire_binding" in subject:
        need(subject.get("reader_first_stage", stage) == stage
             and subject.get("source_sha256", digest(source)) == digest(source), code)
    else:
        need(subject.get("reader_first_stage") == stage
             and subject.get("source_sha256") == digest(source), code)
    return checked


def short_submission(template, context):
    system, user = _wire(context, "SCRIPT_SHORT_PLANNER_WIRE_INVALID")
    subject = _subject(template, context)
    need(subject.get("prompt_version") == VERSIONS[template],
         "SCRIPT_SHORT_PLANNER_VERSION_UNCOVERED")
    document, envelope = _short_document(user)
    _short_binding(template, context, subject, system, document)
    code = "SCRIPT_SHORT_PLANNER_WIRE_INVALID"
    from bestseller.domain.fanqie_short import FanqieShortBeat, FanqieShortBeatSheet
    from bestseller.services import fanqie_outline_contract_completion as fields
    from bestseller.services.source_bound_planning_submission import _validate_wire

    if template == BEAT:
        schema = FanqieShortBeatSheet.model_json_schema()
        requirements = document.get("requirements")
        need(set(envelope) == {"planning_document", "current_output_contract"}
             and document.get("schema") == "fanqie-beat-source.v1"
             and envelope["current_output_contract"] == schema and document.get("output_schema") == schema
             and type(requirements) is dict and requirements.get("exact_segment_count") == 4
             and type(requirements.get("pov")) is str
             and requirements["pov"] == document["source"].get("effective_pov", {}).get("beat_sheet_pov"),
             code)
    else:
        targets, candidate = document.get("required_pointer_values"), document.get("current_candidate")
        need(document.get("schema") == fields.SCHEMA and type(candidate) is dict
             and type(targets) is dict and bool(targets)
             and len(document.get("source", {}).get("base_outline", {}).get("chapters", [])) == 4
             and set(envelope) == {"planning_document", "response_schema"}, code)
        expected = {"type": "object", "properties": targets, "required": list(targets),
                    "additionalProperties": False}
        need(envelope["response_schema"] == expected, code)
        for path, target_schema in targets.items():
            try:
                valid = fields._registered(path) == target_schema and fields._empty(fields._leaf(candidate, path))
            except (ValueError, TypeError, KeyError, IndexError):
                valid = False
            need(valid, code)
    value = _material(template, context)
    code = "SCRIPT_SHORT_PLANNER_REPLY_INVALID"
    need(not reader._has_binding(value), code)
    if template == BEAT:
        need(set(value) == set(FanqieShortBeatSheet.model_fields), code)
        beats = value.get("beats")
        need(type(beats) is list and len(beats) == 4
             and all(type(b) is dict and set(b) == set(FanqieShortBeat.model_fields) for b in beats), code)
        need([b.get("segment_number") for b in beats] == [1, 2, 3, 4]
             and all(type(b.get("segment_number")) is int for b in beats)
             and value["pov"] == requirements["pov"]
             and type(value["unlock_milestone_segment"]) is int
             and 1 <= value["unlock_milestone_segment"] <= 4, code)
        try:
            _validate_wire(value, schema, schema)
        except (ValueError, TypeError, KeyError, IndexError, RecursionError):
            need(False, code)
    else:
        need(set(value) == set(targets), code)
        try:
            # Applies explicit values to a detached copy solely for validation.
            fields._apply_patch(candidate, targets, canonical(value))
        except (ValueError, TypeError, KeyError, IndexError, RecursionError):
            need(False, code)
    return value


def reader_submission(context):
    from bestseller.services import reader_judge as native

    system, user = _wire(context, "SCRIPT_READER_JUDGE_WIRE_INVALID")
    subject = _subject(READER, context)
    need(subject.get("prompt_version") == "1.2", "SCRIPT_READER_JUDGE_VERSION_UNCOVERED")
    need(subject.get("logical_role") == "critic" and system == native._SYSTEM_PROMPT,
         "SCRIPT_READER_JUDGE_WIRE_INVALID")
    body = context.current_body
    need(_text(body) and context.source_body_sha256 == _sha(body), "SCRIPT_CURRENT_BODY_BINDING_REQUIRED")
    cap = subject.get("text_cap_chars")
    need(type(cap) is int and 0 <= cap <= 1_000_000, "SCRIPT_READER_JUDGE_WIRE_INVALID")
    excerpt = body.strip()[:cap] if cap > 0 else body.strip()
    expected = f"第{context.chapter_number}章正文：\n{excerpt}\n\n按 system 指定 JSON 输出评分。"
    need(user == expected, "SCRIPT_READER_JUDGE_BODY_MISMATCH")
    value = _material(READER, context)
    code = "SCRIPT_READER_JUDGE_REPLY_INVALID"
    need(set(value) == _AXES | {"comment"}, code)
    need(all(type(value[k]) in (int, float) and math.isfinite(value[k]) and 0 <= value[k] <= 1
             for k in _AXES) and _text(value["comment"]) and len(value["comment"]) <= 30, code)
    return value


def authored_submission(template, context):
    need(template in VERSIONS, "SCRIPT_TEMPLATE_UNCOVERED")
    if template in UNSET_VERSION_TEMPLATES:
        return auxiliary_submission(template, context)
    if template in {BEAT, FIELDS}:
        return short_submission(template, context)
    return reader_submission(context)
