from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
)

QualityIssueSeverity = Literal["critical", "high", "medium", "low"]
QualityJudgeScope = Literal[
    "outline",
    "chapter",
    "window",
    "volume",
    "commercial_planning",
    "reader_experience",
]
_PASS_TRUE_DIMENSION_THRESHOLD_TOLERANCE = 0.015


class LLMQualityIssue(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    code: str = Field(validation_alias=AliasChoices("code", "type", "issue_type", "id"))
    severity: QualityIssueSeverity = "medium"
    evidence: str = Field(default="", validation_alias=AliasChoices("evidence", "detail", "description", "impact"))
    required_fix: str = Field(
        default="",
        validation_alias=AliasChoices(
            "required_fix",
            "suggestion",
            "fix",
            "repair_hint",
            "recommendation",
        ),
    )
    path: str = Field(default="", validation_alias=AliasChoices("path", "location"))

    @field_validator("code", "evidence", "required_fix", "path", mode="before")
    @classmethod
    def _coerce_string_field(cls, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, Mapping) or (
            isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray))
        ):
            try:
                return json.dumps(value, ensure_ascii=False, default=str)
            except TypeError:
                return str(value)
        return str(value).strip()

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_severity(cls, value: object) -> QualityIssueSeverity:
        normalized = str(value or "medium").lower()
        if normalized in {"block", "blocking", "blocker", "fatal"}:
            return "critical"
        if normalized in {"major", "severe"}:
            return "high"
        if normalized in {"minor", "warning", "warn", "audit"}:
            return "low"
        if normalized not in {"critical", "high", "medium", "low"}:
            return "medium"
        return normalized  # type: ignore[return-value]


class LLMRewritePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: str = ""
    preserve: tuple[str, ...] = ()
    change: tuple[str, ...] = ()
    instructions: str = ""

    @field_validator("preserve", "change", mode="before")
    @classmethod
    def _coerce_string_sequence(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return (str(value).strip(),) if str(value).strip() else ()

    @field_validator("instructions", mode="before")
    @classmethod
    def _coerce_instructions(cls, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, Mapping):
            return json.dumps(value, ensure_ascii=False, default=str)
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return "\n".join(str(item).strip() for item in value if str(item).strip())
        return str(value).strip()


class LLMQualityJudgeResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    scope: QualityJudgeScope = "chapter"
    passed: bool = Field(
        default=False,
        validation_alias=AliasChoices("passed", "pass"),
        serialization_alias="pass",
    )
    overall_score: float = Field(default=0.0, ge=0.0, le=1.0)
    dimension_scores: Mapping[str, float] = Field(default_factory=dict)
    blocking_issues: tuple[LLMQualityIssue, ...] = ()
    audit_issues: tuple[LLMQualityIssue, ...] = ()
    rewrite_plan: LLMRewritePlan = Field(default_factory=LLMRewritePlan)
    raw_excerpt: str = ""
    llm_run_id: str | None = None
    schema_version: str = "llm-quality-judge.v1"

    @field_validator("overall_score", mode="before")
    @classmethod
    def _coerce_overall_score(cls, value: object) -> float:
        return _coerce_score(value)

    @field_validator("rewrite_plan", mode="before")
    @classmethod
    def _coerce_rewrite_plan(cls, value: object) -> object:
        if isinstance(value, Mapping):
            data = dict(value)
            actions = (
                data.get("change")
                or data.get("actions")
                or data.get("edits")
                or data.get("required_changes")
                or ()
            )
            instructions = (
                data.get("instructions")
                or data.get("instruction")
                or data.get("summary")
                or data.get("章节结尾锚点")
                or data.get("acceptance")
                or ""
            )
            if "change" not in data:
                data["change"] = actions
            if "instructions" not in data:
                data["instructions"] = instructions
            return data
        if isinstance(value, str):
            return {"instructions": value}
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
            change: list[str] = []
            instructions: list[str] = []
            for item in value:
                if isinstance(item, Mapping):
                    action = (
                        item.get("action")
                        or item.get("change")
                        or item.get("required_fix")
                        or item.get("suggestion")
                        or item.get("instruction")
                    )
                    if action:
                        change.append(str(action).strip())
                    instructions.append(
                        ", ".join(f"{key}={raw}" for key, raw in item.items() if raw is not None)
                    )
                elif str(item).strip():
                    change.append(str(item).strip())
                    instructions.append(str(item).strip())
            return {
                "scope": "quality_judge_rewrite",
                "change": change,
                "instructions": "\n".join(text for text in instructions if text),
            }
        return {}

    @field_validator("dimension_scores", mode="before")
    @classmethod
    def _coerce_dimension_scores(cls, value: object) -> dict[str, float]:
        if not isinstance(value, Mapping):
            return {}
        scores: dict[str, float] = {}
        for key, raw in value.items():
            scores[str(key)] = _coerce_score(raw)
        return scores

    @field_validator("blocking_issues", "audit_issues", mode="before")
    @classmethod
    def _coerce_issue_sequence(cls, value: object) -> tuple[object, ...]:
        if value is None:
            return ()
        if isinstance(value, Mapping):
            value = (value,)
        if isinstance(value, str):
            return (
                {
                    "code": "LLM_QUALITY_ISSUE",
                    "severity": "medium",
                    "evidence": value,
                    "required_fix": value,
                },
            )
        if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
            return ()
        issues: list[object] = []
        for index, item in enumerate(value, start=1):
            if isinstance(item, str):
                text = item.strip()
                if text:
                    issues.append(
                        {
                            "code": f"LLM_QUALITY_ISSUE_{index}",
                            "severity": "medium",
                            "evidence": text,
                            "required_fix": text,
                        }
                    )
                continue
            if isinstance(item, Mapping):
                data = dict(item)
                if not str(
                    data.get("code")
                    or data.get("type")
                    or data.get("issue_type")
                    or data.get("id")
                    or ""
                ).strip():
                    code_source = (
                        data.get("dimension")
                        or data.get("category")
                        or data.get("location")
                        or data.get("path")
                        or f"LLM_QUALITY_ISSUE_{index}"
                    )
                    data["code"] = _normalize_issue_code(str(code_source))
                if not str(data.get("evidence") or "").strip():
                    for key in ("detail", "description", "impact", "message"):
                        if str(data.get(key) or "").strip():
                            data["evidence"] = str(data[key]).strip()
                            break
                issues.append(data)
        return tuple(issues)

    @computed_field(return_type=bool)
    @property
    def has_critical(self) -> bool:
        return any(issue.severity == "critical" for issue in self.blocking_issues)

    def meets_threshold(
        self,
        *,
        min_overall: float,
        min_dimensions: Mapping[str, float] | None = None,
    ) -> bool:
        if self.has_critical or not self.passed:
            return False
        if self.overall_score < min_overall:
            return False
        for key, threshold in (min_dimensions or {}).items():
            if float(self.dimension_scores.get(key, 0.0)) < float(threshold):
                return False
        return True


def quality_judge_result_from_mapping(
    payload: Mapping[str, Any],
    *,
    scope: QualityJudgeScope,
    min_overall: float,
    min_dimensions: Mapping[str, float] | None = None,
    llm_run_id: str | None = None,
    raw_excerpt: str = "",
) -> LLMQualityJudgeResult:
    data = dict(payload)
    data.setdefault("scope", scope)
    if llm_run_id:
        data["llm_run_id"] = llm_run_id
    if raw_excerpt:
        data["raw_excerpt"] = raw_excerpt
    result = LLMQualityJudgeResult.model_validate(data)
    synthetic_issues = _synthetic_threshold_issues(
        result,
        min_overall=min_overall,
        min_dimensions=min_dimensions,
    )
    # Normalise ``passed`` against the numeric thresholds only when we
    # actually had real blockers (from the LLM) or actionable synthetic
    # ones. Otherwise — the "judge was silent" case — leave the verdict
    # untouched so a borderline numeric miss can't single-handedly flip
    # a pass=true judgement into a pass=false block-on-failure loop.
    # See `_synthetic_threshold_issues` docstring for the loop incident
    # this guards against.
    have_actionable_blockers = bool(result.blocking_issues) or bool(synthetic_issues)
    updates: dict[str, Any] = {}
    if have_actionable_blockers:
        normalized_pass = result.meets_threshold(
            min_overall=min_overall,
            min_dimensions=min_dimensions,
        )
        if normalized_pass != result.passed:
            updates["passed"] = normalized_pass
    if synthetic_issues:
        updates["blocking_issues"] = (*result.blocking_issues, *synthetic_issues)
    if not updates:
        return result
    return result.model_copy(update=updates)


def _coerce_score(value: object) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score > 10.0:
        score = score / 100.0
    elif score > 1.0:
        score = score / 10.0
    return max(0.0, min(1.0, score))


def _normalize_issue_code(value: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in value.strip().upper())
    normalized = "_".join(part for part in normalized.split("_") if part)
    return normalized or "LLM_QUALITY_ISSUE"


def _has_actionable_rewrite_plan(plan: LLMRewritePlan) -> bool:
    """A rewrite plan is *actionable* iff the judge model gave the writer
    something concrete to do next — either a non-empty ``change`` list or
    non-trivial ``instructions``. A purely structural skeleton (just a
    ``scope`` with no change/instructions) is not actionable.
    """

    if any(item.strip() for item in plan.change):
        return True
    if plan.instructions and plan.instructions.strip():
        return True
    return False


def _synthetic_threshold_issues(
    result: LLMQualityJudgeResult,
    *,
    min_overall: float,
    min_dimensions: Mapping[str, float] | None = None,
) -> tuple[LLMQualityIssue, ...]:
    if result.blocking_issues:
        return ()

    # No-actionable-feedback short-circuit:
    # If the judge model gave us no ``blocking_issues`` *and* no actionable
    # rewrite plan, synthesising critical blockers from numeric thresholds
    # alone produces unfixable repair loops — the writer has nothing
    # concrete to fix, but the gate still blocks. This regressed badly on
    # 2026-05-25 (青囊不语问阴阳 ch1 reached 124 versions all blocked by
    # the fabricated ``LLM_SCORE_BELOW_THRESHOLD``). When the judge is
    # silent, we degrade gracefully: leave the verdict and let the
    # per-chapter budget surface the chapter for human review instead of
    # spinning.
    if not _has_actionable_rewrite_plan(result.rewrite_plan):
        return ()

    issues: list[LLMQualityIssue] = []
    if result.overall_score < min_overall:
        issues.append(
            LLMQualityIssue(
                code="LLM_SCORE_BELOW_THRESHOLD",
                # ``high`` (not ``critical``) — downstream severity_max
                # therefore degrades to ``major``, so a long-running gate
                # failure doesn't keep escalating into a critical loop.
                severity="high",
                evidence=(
                    f"{result.scope} overall_score={result.overall_score:.3f} "
                    f"is below threshold {min_overall:.3f}."
                ),
                required_fix=(
                    "根据 rewrite_plan 中给出的具体改写指令补强；"
                    "若裁判模型未给出具体问题点，请人工抽检后再决定是否重写。"
                ),
            )
        )
    for key, threshold in (min_dimensions or {}).items():
        actual = float(result.dimension_scores.get(key, 0.0))
        gap = float(threshold) - actual
        if (
            result.passed
            and not result.blocking_issues
            and 0.0 < gap <= _PASS_TRUE_DIMENSION_THRESHOLD_TOLERANCE
        ):
            continue
        if actual < float(threshold):
            issues.append(
                LLMQualityIssue(
                    code=f"LLM_DIMENSION_BELOW_THRESHOLD_{key.upper()}",
                    severity="high",
                    evidence=(
                        f"{key}={actual:.3f} is below threshold "
                        f"{float(threshold):.3f}."
                    ),
                    required_fix=(
                        f"按 rewrite_plan 修补 {key} 对应维度；"
                        "若 plan 未给出具体修复指令，本条不应直接阻塞。"
                    ),
                )
            )
    if not result.passed and not issues:
        issues.append(
            LLMQualityIssue(
                code="LLM_JUDGE_REPORTED_FAILURE",
                severity="high",
                evidence=f"{result.scope} judge returned pass=false without blocking issues.",
                required_fix="按 rewrite_plan 修订后重评；若 plan 缺失则补充具体问题点。",
            )
        )
    return tuple(issues)
