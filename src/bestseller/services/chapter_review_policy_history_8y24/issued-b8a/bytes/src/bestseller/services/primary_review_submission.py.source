"""Future-only, non-executable primary critique submission and body binding.

No old free-text response repair, grading, storage, model dispatch or adoption.
The caller supplies the exact body used in the request and retains the physical
completion alongside this derived validation receipt.
"""

from __future__ import annotations

from bestseller.services.tool_call_evidence import same_tool_call_evidence

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
from typing import Any

from bestseller.domain.review import ChapterReviewScores
from bestseller.services.critic_evidence_gate import (
    CRITIC_CITATION_TUPLE_POLICY,
    _source_coverage_receipt,
    build_critic_body_anchors,
)

TOOL_NAME = "submit_chapter_critique"
SUBMISSION_POLICY = "primary-review-submission.v2"
LEGACY_SUBMISSION_POLICY = "primary-review-submission.v1"
SCORE_FIELDS = tuple(ChapterReviewScores.model_fields)
_FIELDS = {"verdict", "scores", "rewrite_direction", "commentary", "citations"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require(condition: Any, code: str) -> None:
    if not condition:
        raise ValueError(code)


def _catalog(chapter_text: str) -> list[dict]:
    _require(isinstance(chapter_text, str), "body_type")
    catalog = list(build_critic_body_anchors(chapter_text))
    _require(catalog, "body_catalog_empty")
    return catalog


def primary_review_submission_tools(
    chapter_text: str, *, submission_policy: str = SUBMISSION_POLICY,
) -> list[dict]:
    """Build future tools, or exact v1 tools only for historical input proof."""
    _require(submission_policy in (SUBMISSION_POLICY, LEGACY_SUBMISSION_POLICY),
             "submission_policy_unknown")
    catalog = _catalog(chapter_text)
    props = {
        "verdict": {"type": "string", "enum": ["pass", "rewrite"]},
        "scores": {
            "type": "object",
            "properties": {name: {"type": "number", "minimum": 0, "maximum": 1}
                           for name in SCORE_FIELDS},
            "required": list(SCORE_FIELDS), "additionalProperties": False,
        },
        "rewrite_direction": {"type": "string"},
        "commentary": {"type": "string"},
        "citations": {
            "type": "array", "minItems": 1, "maxItems": len(catalog),
            "items": {
                "type": "object",
                "properties": {
                    "paragraph_number": {"type": "integer", "enum": [
                        int(item["id"].rsplit(":P", 1)[1]) for item in catalog
                    ]},
                    "dimension": {"type": "string", "minLength": 2},
                    "reason": {"type": "string", "minLength": 2},
                },
                "required": ["paragraph_number", "dimension", "reason"],
                "additionalProperties": False,
            },
        },
    }
    if submission_policy == SUBMISSION_POLICY:
        props["citations"].update(
            uniqueItems=True,
            description="Retain every distinct issue in order. The same paragraph may support "
                        "different dimensions or reasons; identical rows are forbidden. "
                        "Citations identify sources, not votes or score multipliers.",
        )
    return [{"type": "function", "function": {
        "name": TOOL_NAME, "strict": True,
        "description": "Submit exactly one critique of the supplied CURRENT_DRAFT. "
        "Paragraph numbers select its exact BODY_ANCHOR_CATALOG. This records a "
        "proposal only; it cannot execute tools, rewrite prose, adopt drafts or spend credits.",
        "parameters": {"type": "object", "properties": props,
                       "required": list(props), "additionalProperties": False},
    }}]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    obj = {}
    for key, value in pairs:
        _require(key not in obj, "duplicate_json_key")
        obj[key] = value
    return obj


def _nonfinite(_value: str) -> None:
    raise ValueError("nonfinite_json_number")


def _field(completion: Any, name: str) -> Any:
    return completion.get(name) if isinstance(completion, Mapping) else getattr(completion, name, None)


def _canonical_value(value: Any) -> str:
    # Escape only the section token inside serialized VALUES, not JSON
    # punctuation or anchor IDs. Decoding restores every original character.
    return _json(value).replace("EVIDENCE_ANCHORS:", r"EVIDENCE_ANCHORS\u003a")


def parse_primary_review_submission(
    completion: Any, chapter_text: str,
) -> tuple[str, dict, tuple[str, ...]]:
    """Strictly validate one physical submission; errors never yield a grade.

    raw_message=None is explicitly recorded and permitted. If present, its
    tool calls must equal the top-level physical calls, including raw arguments.
    No field is defaulted or taken from content/free-text as a fallback.
    """
    receipt = {"schema": SUBMISSION_POLICY, "status": "rejected", "tools_executed": 0,
               "raw_arguments": None, "raw_arguments_sha256": None}
    try:
        content = _field(completion, "content")
        calls = _field(completion, "tool_calls")
        raw_message = _field(completion, "raw_message")
        _require(isinstance(content, str), "original_content_type")
        receipt.update(
            original_content=deepcopy(content), raw_message_present=raw_message is not None,
            raw_message_status="missing" if raw_message is None else "present",
            raw_message_sha256=_sha(_json(raw_message)), tool_calls_sha256=_sha(_json(calls)),
            calls_received=len(calls) if isinstance(calls, list) else 0,
        )
        catalog = _catalog(chapter_text)
        receipt.update(body_sha256=_sha(chapter_text), catalog=deepcopy(catalog),
                       catalog_sha256=_sha(_json(catalog)))
        _require(isinstance(calls, list) and len(calls) == 1, "single_submission_required")
        call = calls[0]
        _require(isinstance(call, dict) and set(call) == {"id", "type", "function"}, "tool_call_shape")
        _require(isinstance(call["id"], str) and bool(call["id"].strip()), "tool_call_id")
        _require(call["type"] == "function", "tool_call_type")
        function = call["function"]
        _require(isinstance(function, dict) and set(function) == {"name", "arguments"}, "function_shape")
        _require(function["name"] == TOOL_NAME, "wrong_tool")
        raw = function["arguments"]
        _require(isinstance(raw, str) and bool(raw.strip()), "arguments_string_required")
        receipt["raw_arguments"] = raw
        receipt["raw_arguments_sha256"] = _sha(raw)
        if raw_message is not None:
            _require(isinstance(raw_message, dict), "raw_message_shape")
            _require(raw_message.get("role", "assistant") == "assistant", "raw_message_role")
            _require(same_tool_call_evidence(raw_message.get("tool_calls"), calls), "raw_message_tool_conflict")
            raw_content = raw_message.get("content")
            _require(raw_content == content or (raw_content is None and content == ""),
                     "raw_message_content_conflict")
        payload = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_nonfinite)
        # JSON's exponent syntax may decode to inf without invoking
        # parse_constant. Validate the WHOLE tree before retaining any payload.
        _json(payload)
        receipt["parsed_payload"] = deepcopy(payload)
        _require(isinstance(payload, dict) and set(payload) == _FIELDS, "payload_fields")
        _require(payload["verdict"] in ("pass", "rewrite"), "verdict")
        scores = payload["scores"]
        _require(isinstance(scores, dict) and set(scores) == set(SCORE_FIELDS), "score_fields")
        for name in SCORE_FIELDS:
            score = scores[name]
            _require(type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1,
                     f"score_value:{name}")
        _require(isinstance(payload["rewrite_direction"], str), "rewrite_direction_type")
        _require(isinstance(payload["commentary"], str), "commentary_type")
        items = payload["citations"]
        _require(isinstance(items, list) and 1 <= len(items) <= len(catalog), "citation_count")
        by_number = {int(item["id"].rsplit(":P", 1)[1]): item for item in catalog}
        seen, citations = set(), []
        for index, item in enumerate(items):
            _require(isinstance(item, dict) and set(item) == {
                "paragraph_number", "dimension", "reason"}, f"citation_fields:{index}")
            number = item["paragraph_number"]
            _require(type(number) is int and number in by_number, f"citation_selector:{index}")
            _require(all(isinstance(item[k], str) and len(item[k].strip()) >= 2
                         for k in ("dimension", "reason")), f"citation_rationale:{index}")
            identity = (number, item["dimension"], item["reason"])
            _require(identity not in seen, f"citation_duplicate:{index}")
            seen.add(identity)
            citations.append({"id": by_number[number]["id"],
                              "dimension": item["dimension"], "reason": item["reason"]})
        canonical = (
            f"VERDICT: {payload['verdict']}\nSCORES: {_json(scores)}\n"
            f"REWRITE_DIRECTION: {_canonical_value(payload['rewrite_direction'])}\n"
            f"COMMENTARY: {_canonical_value(payload['commentary'])}\n"
            f"EVIDENCE_ANCHORS:\n{_canonical_value(citations)}"
        )
        receipt.update(status="verified", canonical_text_sha256=_sha(canonical),
                       selected_paragraph_numbers=[item["paragraph_number"] for item in items],
                       citation_identity_policy=CRITIC_CITATION_TUPLE_POLICY,
                       source_coverage=_source_coverage_receipt(
                           len(catalog), tuple((item["id"],) for item in citations),
                       ),
                       issues_dropped=0, scores_modified=False)
        return canonical, receipt, ()
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        # Preserve the physical evidence accumulated above; no salvage or grade.
        code = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__
        return "", receipt, (f"primary_submission:{code}",)
