"""Two-stage blind evaluation for source partition semantics.

Stage A asks only for contiguous segmentation. Stage B receives those fixed
segments and supplies labels. Reference rationales never enter either prompt;
the existing evaluator remains the sole scorer.
"""
# ruff: noqa: RUF001, ANN401 -- Exact Chinese prompts and bounded JSON values.

from __future__ import annotations

from collections.abc import Mapping
import copy
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.outline_llm_judge import _parse_json_object
from bestseller.services.world_authority import MAX_SOURCE_CHARS
from bestseller.services.world_partition_eval import (
    GRADER_VERSION,
    REFERENCE_STATUS,
    grade_eval,
    prepare_eval,
)
from bestseller.services.world_source_patch import SOURCE_PATCH_POLICY
from bestseller.settings import AppSettings

STAGED_VERSION = "1.1-segmentation-fail-fast"
_KINDS = {"keep", "remove_self_weakening", "unknown"}

STAGE_A_SYSTEM_PROMPT = (
    "你只做源文分段，不做keep/remove_self_weakening/unknown分类。"
    "按通用语义边界切分：触发与适用条件、效果或正常失败、能力有效窗与持续期、材料与制作等待、"
    "否定保障、身份阶梯等不同功能应分别成段；不要依据任何参考答案猜标签。"
    "每个clause_id将原句切成1至64个非空连续原文片段；不得改写、合并遗漏或返回偏移，"
    "终止标点单独成段，空格与连接标点原样保留。"
    '只输出严格JSON：{"complete":true,"source_hash":"输入hash",'
    '"clauses":[{"clause_id":"C001","segments":["原文片段"]}]}。'
)
STAGE_B_SYSTEM_PROMPT = (
    "你只对给定的固定原文片段分类，不改变片段、不合并片段、不返回文字。"
    "keep表示应保留的触发、对象、能力有效窗、正常失败、材料消耗、制作等待、否定保障、身份阶梯或明确非削弱内容；"
    "remove_self_weakening仅表示主角自身能力/身体/操作能力受损及仅从属其持续期；"
    "unknown表示主体、后果性质、适用条件或时限无法确定。末尾标点与条件连接必须按语义保留。"
    '只输出严格JSON：{"complete":true,"source_hash":"输入hash",'
    '"segmentation_hash":"分段hash","labels":[{"segment_id":"C001-S001",'
    '"kind":"keep"}]}。'
)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_segmentation(payload: object, *, prepared: Mapping[str, Any]) -> dict[str, Any]:
    """Validate model-owned text segmentation and compute executable offsets."""
    source_hash = prepared["source_hash"]
    targets = prepared["targets"]
    if not isinstance(payload, Mapping) or set(payload) != {"complete", "source_hash", "clauses"}:
        raise ValueError("STAGED_EVAL_INVALID_SEGMENT_ENVELOPE")
    if payload.get("complete") is not True or payload.get("source_hash") != source_hash:
        raise ValueError("STAGED_EVAL_STALE_SEGMENT_SOURCE")
    rows = payload.get("clauses")
    if not isinstance(rows, list) or len(rows) != len(targets):
        raise ValueError("STAGED_EVAL_SEGMENT_COVERAGE")
    seen: set[str] = set()
    clauses: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"clause_id", "segments"}:
            raise ValueError("STAGED_EVAL_INVALID_SEGMENT_CLAUSE")
        clause_id = row.get("clause_id")
        if not isinstance(clause_id, str) or clause_id not in targets or clause_id in seen:
            raise ValueError("STAGED_EVAL_SEGMENT_ID_COVERAGE")
        seen.add(clause_id)
        pieces = row.get("segments")
        if not isinstance(pieces, list) or not 1 <= len(pieces) <= 64:
            raise ValueError("STAGED_EVAL_SEGMENT_COUNT")
        quote = targets[clause_id]["quote"]
        offset = 0
        segments: list[dict[str, Any]] = []
        for index, piece in enumerate(pieces, 1):
            if not isinstance(piece, str) or not piece:
                raise ValueError("STAGED_EVAL_EMPTY_SEGMENT")
            if offset + len(piece) > len(quote):
                raise ValueError("STAGED_EVAL_SEGMENT_LENGTH")
            if quote[offset : offset + len(piece)] != piece:
                raise ValueError("STAGED_EVAL_SEGMENT_NOT_CONTIGUOUS")
            segments.append(
                {
                    "segment_id": f"{clause_id}-S{index:03d}",
                    "clause_id": clause_id,
                    "quote": piece,
                    "start": offset,
                    "end": offset + len(piece),
                }
            )
            offset += len(piece)
        if offset != len(quote):
            raise ValueError("STAGED_EVAL_SEGMENT_INCOMPLETE")
        if quote[-1] in "。；;.!?！？" and pieces[-1] != quote[-1]:
            raise ValueError("STAGED_EVAL_TERMINATOR_NOT_SEPARATED")
        clauses.append({"clause_id": clause_id, "quote": quote, "segments": segments})
    if seen != set(targets):
        raise ValueError("STAGED_EVAL_SEGMENT_INCOMPLETE_COVERAGE")
    canonical = {"source_hash": source_hash, "clauses": clauses}
    return {**canonical, "segmentation_hash": _hash(canonical)}


def labels_to_partition(
    payload: object, *, segmentation: Mapping[str, Any], prepared: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate blind labels and convert fixed segments to the legacy grader shape."""
    if not isinstance(payload, Mapping) or set(payload) != {
        "complete",
        "source_hash",
        "segmentation_hash",
        "labels",
    }:
        raise ValueError("STAGED_EVAL_INVALID_LABEL_ENVELOPE")
    if payload.get("complete") is not True or payload.get("source_hash") != prepared["source_hash"]:
        raise ValueError("STAGED_EVAL_STALE_LABEL_SOURCE")
    if payload.get("segmentation_hash") != segmentation.get("segmentation_hash"):
        raise ValueError("STAGED_EVAL_STALE_SEGMENTATION")
    expected = [segment for clause in segmentation["clauses"] for segment in clause["segments"]]
    labels = payload.get("labels")
    if not isinstance(labels, list) or len(labels) != len(expected):
        raise ValueError("STAGED_EVAL_LABEL_COVERAGE")
    by_id: dict[str, str] = {}
    for row in labels:
        if not isinstance(row, Mapping) or set(row) != {"segment_id", "kind"}:
            raise ValueError("STAGED_EVAL_INVALID_LABEL")
        segment_id, kind = row.get("segment_id"), row.get("kind")
        if (
            not isinstance(segment_id, str)
            or segment_id in by_id
            or not isinstance(kind, str)
            or kind not in _KINDS
        ):
            raise ValueError("STAGED_EVAL_LABEL_ID_OR_KIND")
        by_id[segment_id] = kind
    if set(by_id) != {segment["segment_id"] for segment in expected}:
        raise ValueError("STAGED_EVAL_LABEL_ID_COVERAGE")
    clauses = []
    for clause in segmentation["clauses"]:
        clauses.append(
            {
                "clause_id": clause["clause_id"],
                "segments": [
                    {"quote": segment["quote"], "kind": by_id[segment["segment_id"]]}
                    for segment in clause["segments"]
                ],
            }
        )
    return {
        "complete": True,
        "source_hash": prepared["source_hash"],
        "clauses": clauses,
    }


def _base_report(prepared: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "version": STAGED_VERSION,
        "reference_status": REFERENCE_STATUS,
        "grader_version": GRADER_VERSION,
        "fixture_hash": prepared["fixture_hash"],
        "source_hash": prepared["source_hash"],
        "segmentation_hash": None,
        "status": "evaluation_failed",
        "production_ready": False,
        "promotion_allowed": False,
        "total_cases": len(prepared["case_ids"]),
        "passed_cases": None,
        "cases": [
            {"case_id": case_id, "passed": None, "status": "not_scored"}
            for case_id in prepared["case_ids"]
        ],
        "run_ids": [],
        "llm_run_ids": [],
        "stages": {},
        "stage_prompts": {
            "a_version": STAGED_VERSION,
            "a_hash": _hash(STAGE_A_SYSTEM_PROMPT),
            "b_version": STAGED_VERSION,
            "b_hash": _hash(STAGE_B_SYSTEM_PROMPT),
        },
    }


def _record_completion(report: dict[str, Any], stage: str, completion: Any) -> None:
    run_id = str(completion.llm_run_id) if completion.llm_run_id else None
    report["llm_run_ids"].append(run_id)
    report["run_ids"] = list(report["llm_run_ids"])
    report["stages"][stage] = {
        "llm_run_id": run_id,
        "raw_response": completion.content[:12000],
        "provider": completion.provider,
        "model_name": completion.model_name,
        "latency_ms": completion.latency_ms,
        "finish_reason": completion.finish_reason,
        "fallback_used": completion.fallback_used,
        "fallback_source": completion.fallback_source,
    }


async def run_staged_partition_eval(
    session: AsyncSession,
    settings: AppSettings,
    *,
    fixture: Mapping[str, Any],
    project_id: UUID,
) -> dict[str, Any]:
    prepared = prepare_eval(fixture)
    report = _base_report(prepared)
    stage_a_user_prompt = f"source_hash={prepared['source_hash']}\n{prepared['context']}"
    if len(stage_a_user_prompt) > MAX_SOURCE_CHARS:
        report["error"] = "STAGED_EVAL_STAGE_A_INPUT_BUDGET"
        report["failed_stage"] = "a"
        return report

    async def call(stage: str, system: str, context: str) -> Any:
        try:
            completion = await complete_text(
                session,
                settings,
                LLMCompletionRequest(
                    logical_role="critic",
                    system_prompt=system,
                    user_prompt=context,
                    fallback_response='{"complete":false}',
                    prompt_template=f"world_partition_staged_{stage}",
                    prompt_version=STAGED_VERSION,
                    project_id=project_id,
                    max_tokens_override=3000,
                    temperature_override=0.1,
                    metadata={"fixture_hash": prepared["fixture_hash"], "stage": stage},
                ),
            )
        except Exception as exc:
            report["error"] = "STAGED_EVAL_PROVIDER_ERROR"
            report["error_type"] = type(exc).__name__
            report["failed_stage"] = stage
            report["stages"][stage] = {"error_type": type(exc).__name__}
            return None
        _record_completion(report, stage, completion)
        if not completion.llm_run_id:
            report["error"] = "STAGED_EVAL_MISSING_RECEIPT"
            report["failed_stage"] = stage
            return None
        try:
            UUID(str(completion.llm_run_id))
        except (ValueError, TypeError, AttributeError):
            report["error"] = "STAGED_EVAL_INVALID_RECEIPT"
            report["failed_stage"] = stage
            return None
        if len(set(report["llm_run_ids"])) != len(report["llm_run_ids"]):
            report["error"] = "STAGED_EVAL_DUPLICATE_RECEIPT"
            report["failed_stage"] = stage
            return None
        if (
            completion.fallback_used
            or completion.finish_reason in {"fallback", "length"}
            or str(completion.provider).lower() in {"fallback", "mock", "placeholder"}
        ):
            report["error"] = "STAGED_EVAL_PROVIDER_UNAVAILABLE"
            report["failed_stage"] = stage
            return None
        try:
            parsed = _parse_json_object(completion.content)
        except ValueError as exc:
            report["error"] = str(exc)
            report["failed_stage"] = stage
            return None
        report["stages"][stage]["parsed_response"] = copy.deepcopy(parsed)
        return parsed

    stage_a_payload = await call("a", STAGE_A_SYSTEM_PROMPT, stage_a_user_prompt)
    if stage_a_payload is None:
        return report
    try:
        segmentation = build_segmentation(stage_a_payload, prepared=prepared)
    except ValueError as exc:
        report["error"] = str(exc)
        report["failed_stage"] = "a"
        return report
    report["segmentation_hash"] = segmentation["segmentation_hash"]
    report["segmentation"] = copy.deepcopy(segmentation)
    stage_b_context = json.dumps(
        {
            "sources_and_targets": json.loads(prepared["context"]),
            "fixed_segmentation": segmentation,
            "repair_policy": SOURCE_PATCH_POLICY,
        },
        ensure_ascii=False,
    )
    stage_b_user_prompt = (
        f"source_hash={prepared['source_hash']}\n"
        f"segmentation_hash={segmentation['segmentation_hash']}\n{stage_b_context}"
    )
    if len(stage_b_user_prompt) > MAX_SOURCE_CHARS:
        report["error"] = "STAGED_EVAL_STAGE_B_INPUT_BUDGET"
        report["failed_stage"] = "b"
        return report
    stage_b_payload = await call(
        "b",
        STAGE_B_SYSTEM_PROMPT,
        stage_b_user_prompt,
    )
    if stage_b_payload is None:
        return report
    try:
        graded_payload = labels_to_partition(
            stage_b_payload, segmentation=segmentation, prepared=prepared
        )
        report.update(grade_eval(graded_payload, prepared=prepared))
        report["response"] = stage_b_payload
        report["segmentation"] = segmentation
    except ValueError as exc:
        report["error"] = str(exc)
        report["failed_stage"] = "b"
    return report
