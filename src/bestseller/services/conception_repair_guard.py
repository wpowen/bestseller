"""Fail-closed review of conception repair candidates before adoption."""

# Chinese prompt text intentionally uses full-width punctuation.
# ruff: noqa: E501, RUF001

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings

_FIELDS = ("premise", "synopsis", "story_spine")


def _hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _valid_input(value: object, name: str) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{name}_not_object"]
    issues = [f"{name}_missing_{field}" for field in _FIELDS if field not in value]
    if "premise" in value and (
        not isinstance(value["premise"], str) or not value["premise"].strip()
    ):
        issues.append(f"{name}_premise_invalid")
    if "synopsis" in value and (
        not isinstance(value["synopsis"], str) or not value["synopsis"].strip()
    ):
        issues.append(f"{name}_synopsis_invalid")
    if "story_spine" in value and (
        not isinstance(value["story_spine"], Mapping) or not value["story_spine"]
    ):
        issues.append(f"{name}_story_spine_invalid")
    return issues


def _field_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _parse_content(content: str) -> object:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


async def review_conception_repair(
    session: AsyncSession,
    settings: AppSettings,
    *,
    before: dict[str, Any],
    candidate: dict[str, Any],
    creation_intent: dict[str, Any],
    repair_reason: str,
    language: str = "zh-CN",
) -> dict[str, Any]:
    """Review one conception repair without mutating any input."""
    issues = _valid_input(before, "before") + _valid_input(candidate, "candidate")
    if not isinstance(creation_intent, Mapping) or not creation_intent:
        issues.append("creation_intent_invalid")
    if not isinstance(repair_reason, str) or not repair_reason.strip():
        issues.append("repair_reason_invalid")
    base: dict[str, Any] = {
        "passed": False,
        "status": "rejected",
        "issues": issues,
        "before_hash": _hash(before) if isinstance(before, Mapping) else None,
        "candidate_hash": _hash(candidate) if isinstance(candidate, Mapping) else None,
        "intent_hash": _hash(creation_intent) if isinstance(creation_intent, Mapping) else None,
        "llm_run_id": None,
        "changed_dimensions": [],
        "regressions": [],
        "preserved": False,
    }
    if issues:
        return base

    request = LLMCompletionRequest(
        logical_role="critic",
        model_tier="strong",
        system_prompt=(
            "你是创作意图修订的只读审计员。比较完整的 before 与 candidate，检查故事身份、"
            "角色目标、冲突递进、行动后果和用户明确要求是否被破坏。轻松语气不等于没有冲突。"
            "只输出 JSON：changed_dimensions(string[]), regressions(object[]), preserved(boolean)。"
            "每个 regression 必须含 field,before_quote,after_quote,reason；引文必须逐字来自对应字段。"
        ),
        user_prompt=json.dumps(
            {"before": before, "candidate": candidate, "creation_intent": creation_intent,
             "repair_reason": repair_reason, "language": language},
            ensure_ascii=False, sort_keys=True,
        ),
        fallback_response="{}",
        prompt_template="conception_repair_guard",
        prompt_version="v1",
        max_tokens_override=4096,
    )
    try:
        result = await complete_text(session, settings, request)
        base["llm_run_id"] = str(result.llm_run_id) if result.llm_run_id else None
        if result.fallback_used:
            base["issues"].append("model_fallback")
        if result.finish_reason not in ("stop", "end_turn"):
            base["issues"].append(f"abnormal_finish_reason:{result.finish_reason}")
        if result.tool_calls:
            base["issues"].append("tool_calls_present")
        if base["issues"]:
            return base
        if not result.content.strip():
            base["issues"].append("empty_model_output")
            return base
        payload = _parse_content(result.content)
        if not isinstance(payload, Mapping):
            raise ValueError("report_not_object")
        changed = payload.get("changed_dimensions")
        regressions = payload.get("regressions")
        preserved = payload.get("preserved")
        if not isinstance(changed, list) or any(not isinstance(x, str) for x in changed):
            raise ValueError("changed_dimensions_invalid")
        if not isinstance(regressions, list) or not isinstance(preserved, bool):
            raise ValueError("report_fields_invalid")
        checked: list[dict[str, Any]] = []
        for item in regressions:
            if not isinstance(item, Mapping) or any(
                not isinstance(item.get(key), str)
                for key in ("field", "before_quote", "after_quote", "reason")
            ):
                raise ValueError("regression_invalid")
            field = item["field"]
            if field not in _FIELDS:
                raise ValueError("regression_field_invalid")
            before_quote, after_quote, reason = (
                item["before_quote"], item["after_quote"], item["reason"]
            )
            if not before_quote.strip() or not reason.strip():
                base["issues"].append(f"empty_before_quote_or_reason:{field}")
                raise ValueError("regression_before_quote_or_reason_empty")
            if before_quote not in _field_text(before[field]):
                base["issues"].append(f"before_quote_missing:{field}")
            if after_quote:
                if after_quote not in _field_text(candidate[field]):
                    base["issues"].append(f"after_quote_missing:{field}")
            else:
                if before_quote in _field_text(candidate[field]):
                    base["issues"].append(f"deletion_unproven:{field}")
                else:
                    item = {**item, "deletion_evidence": True}
            checked.append(dict(item))
        if preserved and checked:
            base["issues"].append("report_contradictory_preserved_with_regressions")
        base.update({"changed_dimensions": changed, "regressions": checked, "preserved": preserved})
        base["passed"] = not base["issues"] and preserved is True
        base["status"] = "passed" if base["passed"] else "rejected"
        return base
    except Exception as exc:
        base["issues"].append(f"unparseable_or_exception:{type(exc).__name__}")
        return base


__all__ = ["review_conception_repair"]
