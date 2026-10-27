"""Deterministic epistemic abstentions; source identity never certifies truth."""
# ruff: noqa: RUF001 -- Unicode quotation syntax is intentional.

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from bestseller.services.critic_evidence_gate import build_critic_body_anchors
from bestseller.services.prompt_source_policy import content_hash

POLICY = "knowledge-epistemic-admission.v1"
SCHEMA = "knowledge-epistemic-item-disposition.v1"
LITERAL_SCHEMA = "knowledge-epistemic-literal-observations.v1"
PROMPT_SCHEMA = "knowledge-epistemic-prompt-view.v1"
VIEW_KEYS = frozenset(
    {
        "unverified_interpretations",
        "literal_source_observations",
        "epistemic_admission",
    }
)
_SOURCE_KEYS = (
    "schema",
    "project_id",
    "chapter_id",
    "source_chapter_draft_version_id",
    "source_version_no",
    "source_body_sha256",
    "llm_run_id",
    "operation_id",
    "provider",
    "model",
    "prompt_template",
    "prompt_version",
    "prompt_sha256",
    "workflow_run_id",
    "step_run_id",
    "raw_sha256",
)


def derive_item_disposition(
    *,
    item: object,
    item_path: str,
    source_lineage: Mapping,
    source_body: str | None,
    application_payload: Mapping,
    classification: str,
) -> dict[str, Any]:
    """Cover one unproved interpretation without authorizing its truth.

    Anchors describe the complete source context, not supporting witnesses.
    No candidate-supplied flag, matching quotation or speaker decides truth.
    Unknown/missing evidence is a completed abstention, never a model retry.
    """
    source = {key: deepcopy(source_lineage[key]) for key in _SOURCE_KEYS if key in source_lineage}
    raw_sha = source.get("raw_sha256")
    raw_bound = (
        isinstance(raw_sha, str)
        and len(raw_sha) == 64
        and all(char in "0123456789abcdef" for char in raw_sha)
        and application_payload.get("raw_sha256") == raw_sha
    )
    witness_keys = (
        "llm_run_id",
        "operation_id",
        "provider",
        "model",
        "prompt_template",
        "prompt_version",
        "prompt_sha256",
        "workflow_run_id",
        "step_run_id",
    )
    origins = source_lineage.get("llm_operations", [])
    origins = origins if isinstance(origins, list) else []
    witnesses = [
        {key: deepcopy(origin[key]) for key in witness_keys if key in origin}
        for origin in [source, *origins]
        if isinstance(origin, Mapping)
        and isinstance(origin.get("llm_run_id"), str)
        and origin["llm_run_id"]
    ]
    bound = isinstance(source_body, str) and source.get("source_body_sha256") == content_hash_body(
        source_body
    )
    anchors = list(build_critic_body_anchors(source_body)) if bound else []
    payload_sha = content_hash(application_payload)
    application_sha = content_hash(
        {"source_lineage": source, "candidate_payload": application_payload}
    )
    item_sha = content_hash(item)
    identity = {
        "policy": POLICY,
        "source_body_sha256": source.get("source_body_sha256"),
        "source_application_sha256": application_sha,
        "item_path": item_path,
        "item_sha256": item_sha,
        "classification": classification,
    }
    return {
        "schema": SCHEMA,
        **identity,
        "disposition_id": content_hash(identity),
        "source_lineage": source,
        "source_payload_sha256": payload_sha,
        "raw_sha256": raw_sha,
        "raw_binding_status": "digest_bound_to_application" if raw_bound else "unknown",
        "paid_origin_status": "recorded_reference_only" if witnesses else "unknown",
        "paid_origin_witnesses": witnesses,
        "interpretation": deepcopy(item),
        "disposition": "quarantined_model_interpretation",
        "reason": "independent_resolution_evidence_missing"
        if bound
        else "source_body_unavailable_or_mismatched",
        "body_anchor_ids": [anchor["id"] for anchor in anchors],
        "body_anchors_sha256": content_hash(anchors),
        "body_anchors": anchors,
        "source_relation": "complete_literal_context_not_entailment_or_resolution",
        "processing_complete": True,
        "truth_approved": False,
    }


def content_hash_body(body: str) -> str:
    """The adopted-body identity is the UTF-8 byte hash, not a JSON hash."""
    from hashlib import sha256

    return sha256(body.encode("utf-8")).hexdigest()


def _quotation_spans(body: str) -> tuple[list[tuple[int, int]], bool]:
    """Quotation syntax only; never infer speakers, reliability or truth.

    Apostrophe exceptions cover ASCII Latin words, not Unicode word characters:
    a CJK character immediately before a single quote must not hide dialogue.
    Any unbalanced quote conservatively makes the whole source ambiguous.
    """
    pairs = {'"': '"', "'": "'", "“": "”", "‘": "’", "「": "」", "『": "』", "«": "»"}
    closing = set(pairs.values())
    stack: list[tuple[str, int]] = []
    spans = []
    escaped = False
    malformed = False
    for index, char in enumerate(body):
        if char == "\\" and not escaped:
            escaped = True
            continue
        if escaped:
            escaped = False
            continue
        if char in {"'", "‘", "’"} and index:
            previous = body[index - 1]
            following = body[index + 1 : index + 2]
            latin_before = "a" <= previous.lower() <= "z"
            latin_after = bool(following) and "a" <= following.lower() <= "z"
            possessive_tail = (
                previous in {"s", "S"}
                and bool(following)
                and following.isspace()
                and bool(body[index + 1 :].lstrip())
                and "a" <= body[index + 1 :].lstrip()[0].lower() <= "z"
                and (not stack or stack[-1][0] != char)
            )
            if latin_before and (latin_after or possessive_tail):
                continue  # Latin contraction or possessive, not a quote boundary.
        if stack and char == stack[-1][0]:
            _end, start = stack.pop()
            spans.append((start, index + 1))
        elif char in pairs:
            stack.append((pairs[char], index))
        elif char in closing:
            malformed = True
    return spans, malformed or bool(stack)


def derive_snapshot_item_disposition(
    *,
    item: object,
    item_path: str,
    source_lineage: Mapping,
    source_body: str | None,
    application_payload: Mapping,
    classification: str = "snapshot_fact",
) -> dict[str, Any]:
    """Retain typed existing unquoted facts; quoted claims become observations.

    This classifies quotation overlap, not semantic entailment. Missing,
    repeated, clipped-to-ambiguous or malformed evidence safely abstains.
    The original candidate is never rewritten or numerically reinterpreted.
    """
    result = derive_item_disposition(
        item=item,
        item_path=item_path,
        source_lineage=source_lineage,
        source_body=source_body,
        application_payload=application_payload,
        classification=classification,
    )
    result.update(disposition="abstain", reason="missing_or_untyped_source_evidence")
    quote = item.get("source_quote") if isinstance(item, Mapping) else None
    typed = (
        isinstance(item, Mapping)
        and type(item.get("value")) in (str, int, float)
        and bool(str(item["value"]).strip())
        and isinstance(item.get("name", item.get("field")), str)
        and bool(item.get("name", item.get("field")).strip())
        and (
            classification != "snapshot_fact"
            or (isinstance(item.get("kind"), str) and bool(item["kind"].strip()))
        )
    )
    if not result["body_anchors"] or not typed or not isinstance(quote, str) or not quote.strip():
        return result
    first = source_body.find(quote)
    if first < 0 or source_body.find(quote, first + 1) >= 0:
        result["reason"] = "missing_or_ambiguous_source_quote"
        return result
    spans, malformed = _quotation_spans(source_body)
    if malformed:
        result["reason"] = "ambiguous_quotation_structure"
        return result
    end = first + len(quote)
    overlapping = [(start, stop) for start, stop in spans if start < end and stop > first]
    result["source_quote_span"] = {"start": first, "end": end, "offset_unit": "unicode-codepoint"}
    if not overlapping:
        if result["raw_binding_status"] != "digest_bound_to_application":
            result["reason"] = "raw_application_binding_unknown"
            return result
        result.update(
            disposition="retain_existing_fact", reason="existing_unquoted_source_bound_fact"
        )
        result["admitted_item"] = deepcopy(item)
        return result
    start = min(first, *(span[0] for span in overlapping))
    stop = max(end, *(span[1] for span in overlapping))
    context = [
        anchor
        for anchor in result["body_anchors"]
        if anchor["start"] < stop and anchor["end"] > start
    ]
    result.update(disposition="reported_observation", reason="source_quote_overlaps_utterance")
    result["resolved_observation"] = {
        "body_anchor_ids": [anchor["id"] for anchor in context],
        "body_anchors": deepcopy(context),
        "text": source_body[context[0]["start"] : context[-1]["end"]],
        "speaker_resolution": "not_inferred",
        "embedded_proposition_truth": "not_established",
    }
    return result


def admit_cognitive_update(
    *,
    update: Mapping,
    item_path: str,
    source_lineage: Mapping,
    source_body: str | None,
    application_payload: Mapping,
) -> dict[str, Any]:
    """Preserve ordinary knowledge; isolate unproved model false classifications.

    Authored/manual baselines are not inputs to this producer-delta admission.
    Legacy string-only false_beliefs have no independent resolution witnesses.
    Retain their bytes for audit and narrative uncertainty, never upgrade them.
    """
    admitted = deepcopy(dict(update))
    values = update.get("false_beliefs", [])
    items = (
        [(f"{item_path}/false_beliefs/{index}", item) for index, item in enumerate(values)]
        if isinstance(values, list)
        else [(f"{item_path}/false_beliefs", values)]
    )
    dispositions = [
        derive_item_disposition(
            item=item,
            item_path=path,
            source_lineage=source_lineage,
            source_body=source_body,
            application_payload=application_payload,
            classification="model_false_belief",
        )
        for path, item in items
    ]
    if "false_beliefs" in admitted:
        admitted["false_beliefs"] = []
    interpretations = [
        {key: value for key, value in disposition.items() if key != "body_anchors"}
        for disposition in dispositions
    ]
    observations = []
    if dispositions:
        first = dispositions[0]
        observations.append(
            {
                "schema": LITERAL_SCHEMA,
                "source_lineage": first["source_lineage"],
                "source_application_sha256": first["source_application_sha256"],
                "source_body_sha256": first["source_body_sha256"],
                "body_anchors_sha256": first["body_anchors_sha256"],
                "body_anchors": first["body_anchors"],
                "source_relation": first["source_relation"],
            }
        )
    result = {
        "policy": POLICY,
        "admitted_update": admitted,
        "item_dispositions": interpretations,
        "unverified_interpretations": interpretations,
        "literal_source_observations": observations,
        "processing_complete": True,
        "truth_approved": False,
    }
    return {**result, "view_sha256": content_hash(result)}


def merge_cognitive_admission(knowledge: Mapping, view: Mapping) -> dict[str, Any]:
    """Merge semantic derived coverage deterministically; no audit timestamps."""
    result = deepcopy(dict(knowledge))
    if not view["item_dispositions"]:
        return result
    for key in ("unverified_interpretations", "literal_source_observations"):
        values = list(result.get(key, []))
        for item in view[key]:
            if item not in values:
                values.append(deepcopy(item))
        result[key] = values
    semantic = {
        key: result[key]
        for key in (
            "unverified_interpretations",
            "literal_source_observations",
        )
    }
    result["epistemic_admission"] = {
        "policy": POLICY,
        "view_sha256": content_hash({"policy": POLICY, **semantic}),
        "processing_complete": True,
        "truth_approved": False,
    }
    return result


def cognitive_prompt_view(knowledge: Mapping) -> dict[str, Any]:
    """Compact only this policy's records; keep every literal paragraph intact.

    The source catalog represents original paragraph strings, not reconstructed
    body bytes. Full offsets/provenance remain in the untouched persisted view.
    Unknown fields and records are copied verbatim, never treated as audit noise.
    """
    result = deepcopy(dict(knowledge))
    observations = knowledge.get("literal_source_observations")
    interpretations = knowledge.get("unverified_interpretations")
    if not isinstance(observations, list) or not isinstance(interpretations, list):
        return result
    observation_keys = {
        "schema",
        "source_lineage",
        "source_application_sha256",
        "source_body_sha256",
        "body_anchors_sha256",
        "body_anchors",
        "source_relation",
    }
    anchor_keys = {"id", "start", "end", "offset_unit", "body_sha256", "text"}
    catalogs: dict[str, list[dict[str, str]]] = {}
    anchor_hashes: dict[str, str] = {}
    recognized: dict[int, str] = {}
    conflicting = set()
    for index, observation in enumerate(observations):
        if not isinstance(observation, Mapping) or set(observation) != observation_keys:
            continue
        body_sha, anchors = observation["source_body_sha256"], observation["body_anchors"]
        source = observation["source_lineage"]
        if (
            observation["schema"] != LITERAL_SCHEMA
            or observation["source_relation"]
            != "complete_literal_context_not_entailment_or_resolution"
            or not isinstance(source, Mapping)
            or not set(source) <= set(_SOURCE_KEYS)
            or source.get("source_body_sha256") != body_sha
            or not isinstance(body_sha, str)
            or len(body_sha) != 64
            or any(char not in "0123456789abcdef" for char in body_sha)
            or not isinstance(anchors, list)
            or observation["body_anchors_sha256"] != content_hash(anchors)
        ):
            continue
        paragraphs = []
        previous_end = 0
        for paragraph_no, anchor in enumerate(anchors, 1):
            if (
                not isinstance(anchor, Mapping)
                or set(anchor) != anchor_keys
                or anchor["id"] != f"D{body_sha[:16]}:P{paragraph_no:04d}"
                or anchor["body_sha256"] != body_sha
                or anchor["offset_unit"] != "unicode-codepoint"
                or type(anchor["start"]) is not int
                or type(anchor["end"]) is not int
                or not isinstance(anchor["text"], str)
                or anchor["start"] < previous_end
                or anchor["end"] - anchor["start"] != len(anchor["text"])
            ):
                break
            paragraphs.append({"id": anchor["id"], "text": anchor["text"]})
            previous_end = anchor["end"]
        else:
            recognized[index] = body_sha
            if body_sha in catalogs and catalogs[body_sha] != paragraphs:
                conflicting.add(body_sha)
            catalogs[body_sha] = paragraphs
            anchor_hashes[body_sha] = observation["body_anchors_sha256"]
    compact_observations, references = [], {}
    for index, observation in enumerate(observations):
        body_sha = recognized.get(index)
        if body_sha is None or body_sha in conflicting:
            compact_observations.append(deepcopy(observation))
        elif body_sha not in references:
            references[body_sha] = len(compact_observations)
            compact_observations.append(
                {
                    "schema": PROMPT_SCHEMA,
                    "source_body_sha256": body_sha,
                    "paragraphs": catalogs[body_sha],
                    "truth_approved": False,
                }
            )
    item_keys = {
        "schema",
        "policy",
        "source_body_sha256",
        "source_application_sha256",
        "item_path",
        "item_sha256",
        "classification",
        "disposition_id",
        "source_lineage",
        "source_payload_sha256",
        "raw_sha256",
        "raw_binding_status",
        "paid_origin_status",
        "paid_origin_witnesses",
        "interpretation",
        "disposition",
        "reason",
        "body_anchor_ids",
        "body_anchors_sha256",
        "source_relation",
        "processing_complete",
        "truth_approved",
    }
    compact_interpretations = []
    for item in interpretations:
        body_sha = item.get("source_body_sha256") if isinstance(item, Mapping) else None
        if (
            not isinstance(item, Mapping)
            or set(item) != item_keys
            or item["schema"] != SCHEMA
            or item["policy"] != POLICY
            or item["classification"] != "model_false_belief"
            or item["disposition"] != "quarantined_model_interpretation"
            or item["truth_approved"] is not False
            or item["processing_complete"] is not True
            or not isinstance(body_sha, str)
            or not isinstance(item["source_lineage"], Mapping)
            or not set(item["source_lineage"]) <= set(_SOURCE_KEYS)
            or not isinstance(item["paid_origin_witnesses"], list)
            or any(
                not isinstance(witness, Mapping) or not set(witness) <= set(_SOURCE_KEYS)
                for witness in item["paid_origin_witnesses"]
            )
            or body_sha not in references
            or item["body_anchors_sha256"] != anchor_hashes[body_sha]
            or item["body_anchor_ids"] != [p["id"] for p in catalogs[body_sha]]
        ):
            compact_interpretations.append(deepcopy(item))
            continue
        compact_interpretations.append(
            {
                **{
                    key: deepcopy(item[key])
                    for key in (
                        "interpretation",
                        "classification",
                        "disposition",
                        "reason",
                        "truth_approved",
                        "disposition_id",
                        "item_path",
                    )
                },
                "source_index": references[body_sha],
            }
        )
    result["unverified_interpretations"] = compact_interpretations
    result["literal_source_observations"] = compact_observations
    admission = knowledge.get("epistemic_admission")
    if (
        isinstance(admission, Mapping)
        and set(admission) == {"policy", "view_sha256", "processing_complete", "truth_approved"}
        and admission["policy"] == POLICY
        and admission["truth_approved"] is False
        and admission["processing_complete"] is True
    ):
        semantic = {
            "schema": PROMPT_SCHEMA,
            "policy": POLICY,
            "unverified_interpretations": compact_interpretations,
            "literal_source_observations": compact_observations,
        }
        result["epistemic_admission"] = {
            "schema": PROMPT_SCHEMA,
            "policy": POLICY,
            "view_sha256": content_hash(semantic),
            "processing_complete": True,
            "truth_approved": False,
        }
    return result
