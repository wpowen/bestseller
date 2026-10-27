# ruff: noqa: E501, RUF001
"""Bounded semantic review for keyword findings from the Fanqie long-form gate.

The deterministic gate remains the source of the original report.  This module
only asks one real critic call whether *critical keyword findings* are actually
supported by the prose in their declared scope.  It never changes the source
report's ``passed`` value and fails closed when a provider receipt or an
auditable JSON answer is missing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ProjectModel
from bestseller.services.fanqie_long_ranking_gate import _slice
from bestseller.services.llm import LLMCompletionRequest, LLMCompletionResult, complete_text
from bestseller.settings import AppSettings

ReviewVerdict = Literal["confirmed", "not_supported", "uncertain"]
_VALID_FINISH_REASONS = frozenset({"stop", "end_turn", "completed"})


class FanqieSemanticFindingReview(BaseModel):
    code: str = Field(min_length=1)
    target: str = ""
    verdict: ReviewVerdict
    evidence: str = ""
    reason: str = ""


class FanqieLongSemanticReviewReport(BaseModel):
    source_critical_codes: list[str] = Field(default_factory=list)
    reviews: list[FanqieSemanticFindingReview] = Field(default_factory=list)
    blocking_findings: list[FanqieSemanticFindingReview] = Field(default_factory=list)
    dismissed_codes: list[str] = Field(default_factory=list)
    provider_receipt: dict[str, Any] = Field(default_factory=dict)
    called: bool = False


def _value(obj: object, key: str, default: object = "") -> object:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _critical_findings(findings: Sequence[Any]) -> list[Any]:
    return [finding for finding in findings if _value(finding, "severity") == "critical"]


def _normalize_chapters(chapter_texts: Mapping[int, str] | Sequence[str]) -> dict[int, str]:
    if isinstance(chapter_texts, Mapping):
        return {
            int(number): str(text or "")
            for number, text in sorted(chapter_texts.items())
            if str(text or "").strip()
        }
    return {
        index: str(text or "")
        for index, text in enumerate(chapter_texts, start=1)
        if str(text or "").strip()
    }


def _scope_text(target: str, chapters: Mapping[int, str]) -> str:
    """Resolve the smallest prose scope named by a gate finding."""
    normalized = str(target or "").strip()
    opening = re.fullmatch(r"opening\.first_(\d+)", normalized)
    if opening:
        return _slice(chapters.get(1, ""), int(opening.group(1)))
    chapter = re.fullmatch(r"chapters\.(\d+)", normalized)
    if chapter:
        return chapters.get(int(chapter.group(1)), "")
    if normalized == "chapters.exposition_streak":
        return "\n".join(f"第{number}章：{text}" for number, text in chapters.items())
    if normalized == "book":
        return "\n".join(f"第{number}章：{text}" for number, text in chapters.items())
    return ""


def _strip_json_fence(raw: str) -> str:
    text = str(raw or "").strip()
    # Models sometimes wrap otherwise valid JSON in exactly one Markdown code
    # fence.  Remove only that outer fence; do not repair, unescape, or rewrite
    # evidence strings because evidence validation must remain literal.
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    return text


def _parse_reviews(raw: str) -> list[dict[str, Any]]:
    text = _strip_json_fence(raw)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    rows = payload.get("reviews") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        return []
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        verdict = row.get("verdict")
        if verdict not in {"confirmed", "not_supported", "uncertain"}:
            continue
        parsed.append(
            {
                "code": row.get("code") if isinstance(row.get("code"), str) else "",
                "target": row.get("target") if isinstance(row.get("target"), str) else "",
                "verdict": verdict,
                "evidence": row.get("evidence") if isinstance(row.get("evidence"), str) else "",
                "reason": row.get("reason") if isinstance(row.get("reason"), str) else "",
            }
        )
    return parsed


def _receipt(completion: LLMCompletionResult) -> dict[str, Any]:
    return {
        "llm_run_id": str(getattr(completion, "llm_run_id", "") or "") or None,
        "model_name": str(getattr(completion, "model_name", "") or ""),
        "provider": str(getattr(completion, "provider", "") or ""),
        "finish_reason": str(getattr(completion, "finish_reason", "") or ""),
        "fallback_used": bool(getattr(completion, "fallback_used", False)),
    }


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _uncertain_reviews(findings: Sequence[object], reason: str) -> list[FanqieSemanticFindingReview]:
    return [
        FanqieSemanticFindingReview(
            code=str(_value(finding, "code")),
            target=str(_value(finding, "target")),
            verdict="uncertain",
            evidence="",
            reason=reason,
        )
        for finding in findings
    ]


async def review_fanqie_long_semantic_findings(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel | None = None,
    genre: str = "",
    profile: str = "",
    chapter_texts: Mapping[int, str] | Sequence[str],
    findings: Sequence[object],
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> FanqieLongSemanticReviewReport:
    """Review all critical keyword findings with one real critic call.

    A finding is dismissible only when the provider returns a valid receipt,
    valid JSON, and a verbatim quote from that finding's target scope.  Any
    omitted, malformed, or unverifiable row remains ``uncertain`` and blocks.
    """
    critical = _critical_findings(findings)
    source_codes = [str(_value(finding, "code")) for finding in critical]
    if not critical:
        return FanqieLongSemanticReviewReport(source_critical_codes=[], called=False)

    chapters = _normalize_chapters(chapter_texts)
    scopes = {
        (str(_value(finding, "code")), str(_value(finding, "target"))): _scope_text(
            str(_value(finding, "target")), chapters
        )
        for finding in critical
    }
    serialized_findings = [
        {
            "code": str(_value(finding, "code")),
            "message": str(_value(finding, "message")),
            "target": str(_value(finding, "target")),
            "detector_evidence": str(_value(finding, "evidence")),
            "repair_hint": str(_value(finding, "repair_hint")),
            "question": "该缺失/不足指控是否成立？若正文已有证据满足要求，必须返回 not_supported。",
            "scope_text": scopes[
                (str(_value(finding, "code")), str(_value(finding, "target")))
            ],
        }
        for finding in critical
    ]
    project_genre = genre or str(_value(project, "genre", "") or "")
    project_profile_value = profile or _value(project, "profile", "") or ""
    project_profile = (
        json.dumps(project_profile_value, ensure_ascii=False, sort_keys=True)
        if isinstance(project_profile_value, Mapping)
        else str(project_profile_value)
    )
    system_prompt = (
        "你是番茄长篇网文的语义复核编辑。自动门禁只按关键词报出了 critical finding，"
        "你只能判断正文是否已经用上下文解释了该 finding，不能替正文补设定。"
        "现实题材中可见动作、日常限时任务、工作流程、关系变化和处境压力都可以成立，"
        "不要求打脸、超能力或固定爽点。每条 finding 必须原样覆盖一次。"
        "confirmed 表示该缺失/不足指控确实成立，not_supported 表示正文已有证据满足要求、关键词误报，"
        "uncertain 表示证据不足。reason 必须以‘缺失成立：’、‘缺失不成立：’或‘无法判断：’之一开头，"
        "并与 verdict 方向一致；不要把‘存在压力’当成‘缺少压力’。"
        "evidence 必须是对应 target scope 内连续10-60字的短句，逐字摘录，不能整段复制、改写或转写换行；"
        "没有逐字证据就只能 uncertain。只输出 JSON。"
    )
    user_prompt = json.dumps(
        {
            "genre": project_genre,
            "profile": project_profile,
            "critical_findings": serialized_findings,
            "output_schema": {
                "reviews": [
                    {
                        "code": "输入 finding code",
                        "target": "输入 target",
                        "verdict": "confirmed|not_supported|uncertain",
                        "evidence": "对应 target scope 的连续原文",
                        "reason": "基于正文的简短理由",
                    }
                ]
            },
        },
        ensure_ascii=False,
    )
    request = LLMCompletionRequest(
        logical_role="critic",
        model_tier="strong",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        fallback_response="{}",
        prompt_template="fanqie_long_semantic_review",
        prompt_version="1.1",
        max_tokens_override=2500,
        project_id=_value(project, "id", None),
        workflow_run_id=workflow_run_id,
        step_run_id=step_run_id,
        metadata={
            "project_slug": _value(project, "slug", ""),
            "critical_finding_count": len(critical),
            "provider_requirement": "real_provider_receipt_required",
        },
    )
    try:
        completion = await complete_text(session, settings, request)
    except Exception as exc:  # fail closed; no fallback verdict
        reviews = _uncertain_reviews(critical, f"语义复核调用失败: {type(exc).__name__}")
        return FanqieLongSemanticReviewReport(
            source_critical_codes=source_codes,
            reviews=reviews,
            blocking_findings=reviews,
            provider_receipt={"error": type(exc).__name__, "fallback_used": False},
            called=True,
        )

    receipt = _receipt(completion)
    valid_receipt = (
        bool(receipt["llm_run_id"])
        and not receipt["fallback_used"]
        and receipt["finish_reason"] in _VALID_FINISH_REASONS
        and receipt["provider"].strip().lower() not in {"", "mock", "fixture", "unknown"}
        and receipt["model_name"].strip().lower() not in {"", "mock", "fixture", "unknown"}
    )
    parsed = _parse_reviews(getattr(completion, "content", "") or "") if valid_receipt else []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    malformed_or_extra = False
    try:
        raw_payload = json.loads(_strip_json_fence(getattr(completion, "content", "") or ""))
        raw_rows = raw_payload.get("reviews") if isinstance(raw_payload, Mapping) else None
        malformed_or_extra = not isinstance(raw_rows, list) or len(raw_rows) != len(critical)
    except (TypeError, ValueError, json.JSONDecodeError):
        malformed_or_extra = True
    for row in parsed:
        code = row["code"]
        key = (code, row["target"])
        if not code or key in by_key:
            malformed_or_extra = True
        else:
            by_key[key] = row

    reviews: list[FanqieSemanticFindingReview] = []
    for finding in critical:
        code = str(_value(finding, "code"))
        target = str(_value(finding, "target"))
        row = by_key.get((code, target))
        scope = scopes.get((code, target), "")
        if not valid_receipt or row is None:
            reviews.append(
                FanqieSemanticFindingReview(
                    code=code, target=target, verdict="uncertain", reason="provider receipt or JSON coverage invalid"
                )
            )
            continue
        evidence = row["evidence"]
        target_matches = row["target"] == target
        evidence_valid = bool(evidence) and bool(scope) and evidence in scope
        verdict: ReviewVerdict = (
            row["verdict"] if target_matches and evidence_valid and not malformed_or_extra else "uncertain"
        )
        reason = row["reason"] if verdict != "uncertain" else "target or verbatim evidence could not be validated"
        reason_prefix = {
            "confirmed": "缺失成立：",
            "not_supported": "缺失不成立：",
            "uncertain": "无法判断：",
        }[verdict]
        if verdict != "uncertain" and not reason.startswith(reason_prefix):
            verdict = "uncertain"
            reason = "无法判断：verdict 与 reason 的方向前缀不一致"
        reviews.append(
            FanqieSemanticFindingReview(
                code=code, target=target, verdict=verdict, evidence=evidence if evidence_valid else "", reason=reason
            )
        )
    blocking = [review for review in reviews if review.verdict != "not_supported"]
    return FanqieLongSemanticReviewReport(
        source_critical_codes=source_codes,
        reviews=reviews,
        blocking_findings=blocking,
        dismissed_codes=[review.code for review in reviews if review.verdict == "not_supported"],
        provider_receipt={
            **receipt,
            "input_sha256": _hash_text(user_prompt),
            "output_sha256": _hash_text(getattr(completion, "content", "") or ""),
            "raw_review": getattr(completion, "content", "") or "",
        },
        called=True,
    )


__all__ = [
    "FanqieLongSemanticReviewReport",
    "FanqieSemanticFindingReview",
    "review_fanqie_long_semantic_findings",
]
