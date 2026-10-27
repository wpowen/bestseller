"""Future-only, source-bound intent review. No edits, grades or retry credits."""

from __future__ import annotations

from bestseller.services.tool_call_evidence import same_tool_call_evidence

from copy import deepcopy
import hashlib
import json
import math
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm import (
    LLMCompletionRequest,
    LLMCompletionResult,
    bind_execution_operation,
    complete_text,
)
from bestseller.services.prompt_compiler import estimate_prompt_tokens
from bestseller.settings import AppSettings

# Chinese editorial instructions are intentionally full-width.
# ruff: noqa: RUF001
INTENT_REVIEW_PROMPT_VERSION = "v3-concept-source-json"
SUBMISSION_POLICY = "intent-review-submission.v1"
TOOL_NAME = "submit_intent_review"
_BODY_FIELDS = {"premise", "synopsis", "story_spine"}
_CONTROL_FIELDS = {
    "genre",
    "sub_genre",
    "chapter_count",
    "language",
    "genre_intent_contract",
    "creation_intent_contract",
    "_user_intent_snapshot",
    "explicit_concept_seed",
}
_RUNTIME_FIELDS = {
    "conception_log",
    "last_failure",
    "llm_run_ids",
    "repair_quotas",
    "structure_repair_attempts",
    "execution_receipt",
    "paid_call_origin",
}
_COST_LABEL = "代价档（能力不带自损）"
_ASSESSMENT_SCOPE = {
    "stage": "conception",
    "judgment": "concept_compatibility_and_potential",
    "sources": ["premise", "story_spine", "synopsis"],
    "pass_meaning": "current_sources_structurally_support_explicit_direction",
    "fail_meaning": "current_sources_structurally_contradict_explicit_direction",
    "unknown_meaning": "current_sources_insufficient_or_ambiguous_explain_gap",
    "chapter_prose_required": False,
    "actual_reader_payoff_verified": False,
    "literary_quality_verified": False,
    "market_acceptance_verified": False,
}
_SYSTEM = (
    "你是只读创作意图审校员，当前阶段明确为 conception（构思），"
    "仅评估当前 premise、story_spine、synopsis 与人类明确方向的结构兼容性和概念潜力。"
    "user_controls 保留原始来源：题材默认值、推断标签和模型建议不是人类明确要求。"
    "pass 仅表示当前概念材料在结构上支持该方向，不能把标签出现当成结构支持。"
    "fail 必须引用当前实际设定并解释为何在结构上与该方向相抵触。"
    "来源不足、结构支持不明确或无法判断时必须 unknown，并在 reason 保留具体缺口；"
    "找不到引文不能直接判为失败。不得要求尚未写出的章节正文、逐页兑现或读者体验证据，"
    "不得仅因没有正文判 unknown；但概念来源本身不足时仍须 unknown。"
    "本审查不证明实际读者爽感、文学质量或市场验收，不得声称已实际兑现这些效果。"
    "只从 CATALOG 选择 source_path 与 anchor_id；引用由服务器还原原文，禁止拼接、改写引文。"
    "多段证据分别列出。引用命中仅证明来源，不证明语义结论正确；证据不是票数或分数。"
    "cost_style=standard 不包含无代价/无自损要求；只有 external/minimal 才检查能力使用导致的"
    "自身折损，普通风险、敌人、限制和时限不能自动等同自损。"
    "reason 和 revise_direction 是审查建议，不是作者命令，不得创作替代剧情、修改或批准故事。"
    "工具外层参数必须且只能是 assessment_json 一个英文字符串字段；"
    "字符串内容是符合 assessment_schema 的完整 JSON 对象，包含 items、counter_elements、"
    "revise_direction、cost_verdict。items 直接以所选标签为键，evidence 是数组，"
    "anchor_id 是 JSON 整数；不得添加 item 包装，不得把数字转为字符串。"
    "必须且仅调用一次 submit_intent_review；不要在 content 中提交另一份判决。"
)


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require(condition: object, code: str) -> None:
    if not condition:
        raise ValueError(f"intent_review:{code}")


def _clean(value: object) -> object:
    """Strict JSON only: no tuple coercion, nonfinite values or hidden ORM state."""
    if isinstance(value, dict):
        _require(all(isinstance(k, str) for k in value), "non_string_key")
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    _require(value is None or type(value) in (str, int, float, bool), "non_json_value")
    if isinstance(value, float):
        _require(math.isfinite(value), "nonfinite_value")
    return value


def _no_runtime(value: object) -> None:
    if isinstance(value, dict):
        _require(not (_RUNTIME_FIELDS & value.keys()), "runtime_history_in_source")
        for child in value.values():
            _no_runtime(child)
    elif isinstance(value, list):
        for child in value:
            _no_runtime(child)


def _explicit_checks(controls: dict) -> tuple[set[str], set[str]]:
    """Use existing provenance contracts, never labels in model-authored context."""
    from bestseller.services.intent_alignment import user_pick_intent_items

    genre = controls.get("genre_intent_contract") or {}
    creation = controls.get("creation_intent_contract") or {}
    _require(isinstance(genre, dict) and isinstance(creation, dict), "control_contract_type")
    fields = creation.get("field_sources") or {}
    _require(isinstance(fields, dict), "control_provenance_type")
    allowed, costs = set(), {"standard"}
    if genre.get("tag_source") == "user":
        tags = genre.get("user_tags", [])
        _require(isinstance(tags, list) and all(isinstance(t, str) for t in tags), "user_tags_type")
        allowed.update(tags)
    # A known default field source takes precedence over the broad contract
    # source. A legacy/inferred contract never grants explicit enhancer power.
    selected = {}
    if genre.get("source") == "user":
        if not fields or fields.get("tone_preference") == "explicit":
            selected["tone_preference"] = genre.get("tone_preference")
        if not fields or fields.get("story_enhancers") == "explicit":
            selected["explicit_enhancers"] = genre.get("explicit_enhancers") or {}
    if fields.get("tone_preference") == "explicit":
        selected["tone_preference"] = creation.get("tone_preference")
    if fields.get("story_enhancers") == "explicit":
        selected["explicit_enhancers"] = creation.get("story_enhancers") or {}
    enhancers = selected.get("explicit_enhancers") or {}
    _require(isinstance(enhancers, dict), "enhancer_control_type")
    style = enhancers.get("cost_style")
    if style in {"standard", "minimal", "external"}:
        costs.add(style)
    allowed.update(user_pick_intent_items(selected))
    return allowed, costs


def _catalog(value: object, path: str = "/candidate") -> list[dict]:
    rows = []

    def walk(node: object, pointer: str) -> None:
        if isinstance(node, str) and node.strip():
            rows.append({"anchor_id": len(rows) + 1, "source_path": pointer, "text": node})
        elif isinstance(node, dict):
            for key in sorted(node):
                walk(node[key], pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, pointer + "/" + str(i))

    walk(value, path)
    return rows


def _assessment_schema(source: dict) -> dict:
    """The unchanged typed payload contract, carried inside assessment_json."""
    catalog, tags = source["catalog"], source["intent_tags"]
    evidence = {
        "type": "object",
        "properties": {
            "source_path": {"type": "string", "enum": [r["source_path"] for r in catalog]},
            "anchor_id": {"type": "integer", "enum": [r["anchor_id"] for r in catalog]},
        },
        "required": ["source_path", "anchor_id"],
        "additionalProperties": False,
    }
    entry = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "fail", "unknown"]},
            "reason": {"type": "string", "minLength": 1},
            "evidence": {"type": "array", "items": evidence},
        },
        "required": ["verdict", "reason", "evidence"],
        "additionalProperties": False,
    }
    against = list(tags) + (
        [_COST_LABEL] if source["cost_style"] in {"minimal", "external"} else []
    )
    props = {
        "items": {
            "type": "object",
            "properties": dict.fromkeys(tags, entry),
            "required": tags,
            "additionalProperties": False,
        },
        "counter_elements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    **evidence["properties"],
                    "against": {"type": "string", "enum": against},
                    "reason": {"type": "string", "minLength": 1},
                },
                "required": ["source_path", "anchor_id", "against", "reason"],
                "additionalProperties": False,
            },
        },
        "revise_direction": {"type": "string"},
        "cost_verdict": {"type": "string", "enum": ["pass", "fail", "unknown", "not_requested"]},
    }
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def intent_review_submission_tools(source: dict) -> list[dict]:
    """One scalar transport field; nested types are validated locally without coercion."""
    return [
        {
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": "Record one conception compatibility/potential assessment. "
                "assessment_json contains the complete JSON object defined by assessment_schema. "
                "No tool is executed; no actual reader payoff or market acceptance is certified.",
                "parameters": {
                    "type": "object",
                    "properties": {"assessment_json": {"type": "string"}},
                    "required": ["assessment_json"],
                    "additionalProperties": False,
                },
            },
        }
    ]


def build_intent_review_request(
    *,
    candidate: dict,
    user_controls: dict,
    intent_tags: list[str],
    genre_label: str,
    cost_style: str = "standard",
    phase: str = "initial",
    sample_slot: int = 0,
    project_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
) -> LLMCompletionRequest:
    """Full current sources only. Overlarge or malformed inputs fail before dispatch."""
    _require(isinstance(candidate, dict) and set(candidate) == _BODY_FIELDS, "candidate_fields")
    _require(
        all(
            isinstance(candidate[k], str) and candidate[k].strip() for k in ("premise", "synopsis")
        ),
        "candidate_text",
    )
    _require(isinstance(candidate["story_spine"], dict) and candidate["story_spine"], "spine")
    _require(
        isinstance(user_controls, dict) and not (user_controls.keys() - _CONTROL_FIELDS),
        "control_fields",
    )
    _require(
        isinstance(intent_tags, list)
        and all(isinstance(t, str) and t.strip() for t in intent_tags)
        and len(set(intent_tags)) == len(intent_tags),
        "intent_tags",
    )
    _require(isinstance(genre_label, str), "genre_label")
    _require(cost_style in {"standard", "minimal", "external"}, "cost_style")
    _require(bool(intent_tags) or cost_style != "standard", "nothing_to_review")
    _require(phase in {"initial", "recheck"}, "phase")
    _require(type(sample_slot) is int and sample_slot in (0, 1), "sample_slot")
    _require(phase != "recheck" or sample_slot == 0, "recheck_slot")
    source = _clean(
        {
            "candidate": candidate,
            "user_controls": user_controls,
            "intent_tags": intent_tags,
            "genre_label": genre_label,
            "cost_style": cost_style,
            "phase": phase,
        }
    )
    _no_runtime(source)
    allowed, costs = _explicit_checks(source["user_controls"])
    _require(set(intent_tags) <= allowed, "intent_source_unverified")
    _require(cost_style in costs, "cost_source_unverified")
    source["catalog"] = _catalog(source["candidate"])
    source["assessment_scope"] = deepcopy(_ASSESSMENT_SCOPE)
    source["assessment_schema"] = _assessment_schema(source)
    request = LLMCompletionRequest(
        logical_role="critic",
        system_prompt=_SYSTEM,
        user_prompt=_json(source),
        fallback_response="{}",
        prompt_template="conception_intent_alignment",
        prompt_version=INTENT_REVIEW_PROMPT_VERSION,
        project_id=project_id,
        workflow_run_id=workflow_run_id,
        sample_slot=sample_slot,
        max_tokens_override=4096,
        tools=intent_review_submission_tools(source),
        tool_choice="required",
        metadata={
            "intent_review": {
                "schema": SUBMISSION_POLICY,
                "phase": phase,
                "sample_slot": sample_slot,
                "source_sha256": _sha(_json(source)),
                "body_sha256": _sha(_json(source["candidate"])),
                "assessment_scope": deepcopy(_ASSESSMENT_SCOPE),
            }
        },
    )
    serialized = _json(
        {
            "system": request.system_prompt,
            "user": request.user_prompt,
            "tools": request.tools,
            "tool_choice": request.tool_choice,
        }
    )
    _require(
        len(serialized) <= 100_000
        and math.ceil(1.5 * estimate_prompt_tokens(serialized)) <= 49_152,
        "input_budget_exceeded",
    )
    return request


def _unique(pairs: list[tuple[str, object]]) -> dict:
    out = {}
    for key, value in pairs:
        _require(key not in out, "duplicate_json_key")
        out[key] = value
    return out


def _bad_constant(value: str) -> None:
    raise ValueError(f"intent_review:nonfinite_json:{value}")


def _paid_origin(result: LLMCompletionResult, request: LLMCompletionRequest) -> dict:
    meta = result.metadata
    origin, execution, effective = (
        meta.get(k) for k in ("paid_call_origin", "execution_receipt", "effective_input_receipt")
    )
    _require(
        all(isinstance(x, dict) for x in (origin, execution, effective)), "paid_origin_missing"
    )
    _require(
        origin.get("schema") == "paid-call-origin.v1" and origin.get("status") == "recorded",
        "paid_origin_unknown",
    )
    UUID(str(origin.get("workflow_run_id")))
    if origin.get("project_id") is not None:
        UUID(str(origin["project_id"]))
    _require(
        origin.get("logical_role") == "critic"
        and origin.get("prompt_template") == request.prompt_template
        and origin.get("prompt_version") == request.prompt_version,
        "paid_protocol_changed",
    )
    h = effective.get("hash")
    _require(
        isinstance(h, str) and len(h) == 64 and all(c in "0123456789abcdef" for c in h),
        "effective_hash_missing",
    )
    _require(
        effective.get("schema") == "effective-input.v1"
        and effective.get("tools_included") is True
        and effective.get("messages_source") == "system_user"
        and effective.get("legacy_prompt_hash")
        == _sha(f"{request.system_prompt}\n\n{request.user_prompt}")
        and origin.get("prompt_hash") == h,
        "effective_input_changed",
    )
    if request.project_id is not None:
        _require(origin.get("project_id") == str(request.project_id), "paid_project_changed")
    if request.workflow_run_id is not None and origin.get("workflow_run_id") != str(
        request.workflow_run_id
    ):
        recovery = meta.get("execution_recovery")
        _require(
            isinstance(recovery, dict)
            and recovery.get("schema") == "execution-recovery.v1"
            and recovery.get("workflow_run_id") == str(request.workflow_run_id)
            and recovery.get("prompt_hash") == h
            and recovery.get("project_id") == origin.get("project_id")
            and recovery.get("logical_role") == "critic"
            and recovery.get("prompt_template") == request.prompt_template
            and recovery.get("prompt_version") == request.prompt_version,
            "recovery_binding_changed",
        )
    _require(
        execution.get("policy") == "execution-budget.v1"
        and isinstance(execution.get("scope_id"), str)
        and execution["scope_id"].strip()
        and isinstance(execution.get("operation_key"), str)
        and execution["operation_key"].strip(),
        "execution_identity_missing",
    )
    attempts = execution.get("attempts")
    _require(isinstance(attempts, list) and attempts, "paid_attempts_missing")
    seen = set()
    for attempt in attempts:
        _require(
            isinstance(attempt, dict)
            and isinstance(attempt.get("attempt_id"), str)
            and attempt["attempt_id"].strip()
            and attempt["attempt_id"] not in seen,
            "paid_attempt_identity",
        )
        seen.add(attempt["attempt_id"])
    _require(attempts[-1].get("effective_input_hash") == h, "paid_attempt_input_changed")
    return {
        "paid_call_origin": deepcopy(origin),
        "execution_receipt": deepcopy(execution),
        "effective_input_receipt": deepcopy(effective),
        "operation_key": execution["operation_key"],
        "scope_id": execution["scope_id"],
        "effective_input_hash": h,
    }


def parse_intent_review_submission(
    result: LLMCompletionResult,
    request: LLMCompletionRequest,
) -> dict[str, Any]:
    """All-or-nothing validation. No invalid old/free-text result is regraded."""
    receipt = {
        "schema": SUBMISSION_POLICY,
        "status": "unknown",
        "tools_executed": 0,
        **deepcopy(request.metadata.get("intent_review", {})),
        "llm_run_id": str(result.llm_run_id) if result.llm_run_id else None,
        "request": request.model_dump(mode="json"),
        "request_sha256": _sha(_json(request.model_dump(mode="json"))),
        "original": {
            "content": deepcopy(result.content),
            "tool_calls": deepcopy(result.tool_calls),
            "raw_message": deepcopy(result.raw_message),
            "finish_reason": result.finish_reason,
            "provider": result.provider,
            "model_name": result.model_name,
            "fallback_used": result.fallback_used,
            "fallback_source": result.fallback_source,
        },
    }
    unknown = {
        "status": "unknown",
        "items": {},
        "failed_tags": [],
        "counter_elements": [],
        "cost_violations": [],
        "revise_direction": "",
        "receipt": receipt,
    }
    try:
        source = json.loads(
            request.user_prompt, object_pairs_hook=_unique, parse_constant=_bad_constant
        )
        expected = build_intent_review_request(
            **{
                k: source[k]
                for k in (
                    "candidate",
                    "user_controls",
                    "intent_tags",
                    "genre_label",
                    "cost_style",
                    "phase",
                )
            },
            sample_slot=request.sample_slot,
            project_id=request.project_id,
            workflow_run_id=request.workflow_run_id,
        )
        _require(request.model_dump() == expected.model_dump(), "request_changed")
        _require(
            result.llm_run_id is not None
            and not result.fallback_used
            and not result.fallback_source
            and result.provider.lower() not in {"mock", "fallback", "fixture", "test", ""}
            and result.model_name
            and not result.model_name.lower().startswith(("mock", "fallback")),
            "not_real_return",
        )
        _require(result.finish_reason in {"stop", "end_turn", "tool_calls"}, "finish_reason")
        receipt.update(_paid_origin(result, request))
        calls = result.tool_calls
        _require(isinstance(calls, list) and len(calls) == 1, "single_submission_required")
        call = calls[0]
        _require(
            isinstance(call, dict)
            and set(call) == {"id", "type", "function"}
            and isinstance(call["id"], str)
            and call["id"].strip()
            and call["type"] == "function",
            "tool_shape",
        )
        function = call["function"]
        _require(
            isinstance(function, dict)
            and set(function) == {"name", "arguments"}
            and function["name"] == TOOL_NAME
            and isinstance(function["arguments"], str),
            "tool_function",
        )
        if result.raw_message is not None:
            raw = result.raw_message
            _require(
                raw.get("role", "assistant") == "assistant"
                and same_tool_call_evidence(raw.get("tool_calls"), calls)
                and (
                    raw.get("content") == result.content
                    or (raw.get("content") is None and result.content == "")
                ),
                "raw_message_conflict",
            )
        receipt["raw_message_missing"] = result.raw_message is None
        arguments = function["arguments"]
        receipt.update(raw_arguments=arguments, raw_arguments_sha256=_sha(arguments))
        outer = json.loads(arguments, object_pairs_hook=_unique, parse_constant=_bad_constant)
        _clean(outer)
        _require(
            isinstance(outer, dict)
            and set(outer) == {"assessment_json"}
            and isinstance(outer["assessment_json"], str),
            "assessment_json_field",
        )
        inner = outer["assessment_json"]
        receipt.update(
            raw_assessment_json=inner,
            raw_assessment_json_sha256=_sha(inner),
            raw_assessment_json_utf8_length=len(inner.encode("utf-8")),
        )
        payload = json.loads(inner, object_pairs_hook=_unique, parse_constant=_bad_constant)
        _clean(payload)
        receipt["parsed_payload"] = deepcopy(payload)
        _require(
            isinstance(payload, dict)
            and set(payload) == {"items", "counter_elements", "revise_direction", "cost_verdict"},
            "payload_fields",
        )
        _require(
            isinstance(payload["items"], dict)
            and set(payload["items"]) == set(source["intent_tags"]),
            "item_coverage",
        )
        catalog = {row["anchor_id"]: row for row in source["catalog"]}

        def evidence(item: dict) -> dict:
            number = item.get("anchor_id")
            _require(type(number) is int and number in catalog, "anchor_id")
            row = catalog[number]
            _require(item.get("source_path") == row["source_path"], "source_path_changed")
            return {"anchor_id": number, "field": row["source_path"], "quote": row["text"]}

        items, failed, selected = {}, [], []
        has_unknown = False
        for tag in source["intent_tags"]:
            item = payload["items"][tag]
            _require(
                isinstance(item, dict) and set(item) == {"verdict", "reason", "evidence"},
                "item_fields",
            )
            _require(
                item["verdict"] in {"pass", "fail", "unknown"}
                and isinstance(item["reason"], str)
                and item["reason"].strip()
                and isinstance(item["evidence"], list),
                "item_verdict",
            )
            rows = []
            for ref in item["evidence"]:
                _require(
                    isinstance(ref, dict) and set(ref) == {"source_path", "anchor_id"},
                    "evidence_fields",
                )
                rows.append(evidence(ref))
            _require(rows or item["verdict"] == "unknown", "verdict_evidence_missing")
            selected.extend(row["anchor_id"] for row in rows)
            items[tag] = {
                "pass": {"pass": True, "fail": False, "unknown": None}[item["verdict"]],
                "quote": rows[0]["quote"] if rows else "",
                "evidence": rows,
                "reason": item["reason"],
            }
            if item["verdict"] == "fail":
                failed.append(tag)
            has_unknown |= item["verdict"] == "unknown"
        _require(
            isinstance(payload["counter_elements"], list)
            and isinstance(payload["revise_direction"], str),
            "counter_shape",
        )
        counters = []
        for item in payload["counter_elements"]:
            _require(
                isinstance(item, dict)
                and set(item) == {"source_path", "anchor_id", "against", "reason"},
                "counter_fields",
            )
            against = item["against"]
            _require(
                against in source["intent_tags"]
                or (against == _COST_LABEL and source["cost_style"] in {"minimal", "external"}),
                "counter_against",
            )
            _require(isinstance(item["reason"], str) and item["reason"].strip(), "counter_reason")
            _require(
                against == _COST_LABEL or items[against]["pass"] is not True,
                "counter_verdict_conflict",
            )
            row = evidence(item)
            selected.append(row["anchor_id"])
            counters.append({**row, "against": against, "reason": item["reason"]})
        for tag in failed:
            _require(any(c["against"] == tag for c in counters), "failed_intent_counter_missing")
        cost_verdict = payload["cost_verdict"]
        cost_counters = [deepcopy(c) for c in counters if c["against"] == _COST_LABEL]
        if source["cost_style"] == "standard":
            _require(cost_verdict == "not_requested" and not cost_counters, "cost_not_requested")
        else:
            _require(cost_verdict in {"pass", "fail", "unknown"}, "cost_verdict")
            _require(cost_verdict != "pass" or not cost_counters, "cost_verdict_conflict")
            _require(cost_verdict != "fail" or bool(cost_counters), "cost_failure_evidence_missing")
            has_unknown |= cost_verdict == "unknown"
        receipt.update(
            source_coverage={
                "catalog_anchor_count": len(catalog),
                "distinct_anchor_count": len(set(selected)),
                "citation_count": len(selected),
            },
            issues_dropped=0,
            catalog_sha256=_sha(_json(source["catalog"])),
            source_grounding_verified=True,
            semantic_judgment_verified=False,
            rewrite_authorized=False,
            adoption_authorized=False,
        )
        if has_unknown:
            receipt["errors"] = ["intent_review:semantic_unknown"]
            return unknown
        receipt["status"] = "verified"
        return {
            "status": "verified",
            "items": items,
            "failed_tags": failed,
            "counter_elements": counters,
            "cost_violations": cost_counters,
            "revise_direction": payload["revise_direction"],
            "receipt": receipt,
        }
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        receipt["errors"] = [
            str(exc)
            if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError)
            else type(exc).__name__
        ]
        return unknown


async def evaluate_intent_alignment_sample(
    session: AsyncSession,
    settings: AppSettings,
    *,
    candidate: dict,
    user_controls: dict,
    intent_tags: list[str],
    genre_label: str,
    cost_style: str = "standard",
    phase: str = "initial",
    sample_slot: int = 0,
    project_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
) -> dict[str, Any]:
    """One logical slot. Gateway owns settlement/replay; caller owns bounded review lifecycle."""
    request = build_intent_review_request(
        candidate=candidate,
        user_controls=user_controls,
        intent_tags=intent_tags,
        genre_label=genre_label,
        cost_style=cost_style,
        phase=phase,
        sample_slot=sample_slot,
        project_id=project_id,
        workflow_run_id=workflow_run_id,
    )
    namespace = "intent-review:" + _sha(
        _json(
            {
                "system": request.system_prompt,
                "user": request.user_prompt,
                "tools": request.tools,
                "version": request.prompt_version,
            }
        )
    )
    with bind_execution_operation(namespace):
        result = await complete_text(session, settings, request)
    return parse_intent_review_submission(result, request)


__all__ = [
    "INTENT_REVIEW_PROMPT_VERSION",
    "SUBMISSION_POLICY",
    "TOOL_NAME",
    "build_intent_review_request",
    "evaluate_intent_alignment_sample",
    "intent_review_submission_tools",
    "parse_intent_review_submission",
]
