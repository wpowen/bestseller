"""Advisory ideology (母题) judge — scores whether an OUTLINE expresses its core
ideology versus merely filling genre tropes.

A parallel judge to the commercial outline judge (``outline_llm_judge``): where
that one scores "能不能撑起榜单正文" (retention/executability), this one scores
"有没有灵魂 / 思想深度" — does the outline make the thesis a story engine?

**Advisory only** (mirrors ``litstyle_prose_judge``): returns an
:class:`IdeologyJudgeResult` with no ``passed`` field; callers write it into the
evidence summary and may drive a soft regen loop from ``revision_priority``. It
never contributes to an outline's hard verdict / blocking_issues / any gate.

Reuses the established judge machinery: ``complete_text`` (logical_role="critic",
strong tier), the genre-neutral :class:`JudgeGenreContext`, the deterministic
:func:`audit_ideology_outline_grounding` pass as a penalty floor, and split
prompt builders so the pilot's standalone runner can reuse them.
"""

# ruff: noqa: RUF001, E501, ANN401, S112

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import re
import statistics
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.ideology import IdeologyKernel
from bestseller.domain.ideology_judge import (
    IdeologyJudgeResult,
    ideology_result_from_mapping,
)
from bestseller.services.ideology_coherence_gate import (
    IdeologyGroundingResult,
    audit_ideology_outline_grounding,
)
from bestseller.services.judge_genre_context import (
    JudgeGenreContext,
    resolve_judge_genre_context,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.quality_levers._loader import (
    as_dict,
    as_int,
    as_str,
    load_yaml,
)
from bestseller.settings import AppSettings

_CONFIG_FILENAME = "ideology_judge.yaml"


# ---------------------------------------------------------------------------
# Typed config view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdeologyDimension:
    key: str
    display_name: str
    max: int
    definition: str
    observable: str
    scoring_rule: str
    pos_example: str
    neg_example: str


@dataclass(frozen=True)
class IdeologyPenaltyMarker:
    marker_id: str
    problem: str
    penalty_max: int
    deterministic: bool


@dataclass(frozen=True)
class IdeologyLevel:
    min: int
    level: str
    desc: str


@dataclass(frozen=True)
class IdeologyJudgeConfig:
    version: str
    dimensions: tuple[IdeologyDimension, ...]
    penalty_max: int
    penalty_high_risk_threshold: int
    penalty_strong_ceiling: int
    penalty_markers: tuple[IdeologyPenaltyMarker, ...]
    levels: tuple[IdeologyLevel, ...]
    target_premium: float
    target_budget: float

    @property
    def base_score_max(self) -> int:
        return sum(d.max for d in self.dimensions)

    @property
    def dimension_keys(self) -> tuple[str, ...]:
        return tuple(d.key for d in self.dimensions)


def _flt(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _parse_dimensions(raw: object) -> tuple[IdeologyDimension, ...]:
    out: list[IdeologyDimension] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            data = as_dict(entry)
            key = as_str(data.get("key"))
            if not key:
                continue
            out.append(
                IdeologyDimension(
                    key=key,
                    display_name=as_str(data.get("display_name"), default=key),
                    max=as_int(data.get("max"), default=10),
                    definition=as_str(data.get("definition")),
                    observable=as_str(data.get("observable")),
                    scoring_rule=as_str(data.get("scoring_rule")),
                    pos_example=as_str(data.get("pos_example")),
                    neg_example=as_str(data.get("neg_example")),
                )
            )
    return tuple(out)


def _parse_markers(raw: object) -> tuple[IdeologyPenaltyMarker, ...]:
    out: list[IdeologyPenaltyMarker] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            data = as_dict(entry)
            marker_id = as_str(data.get("id"))
            if not marker_id:
                continue
            out.append(
                IdeologyPenaltyMarker(
                    marker_id=marker_id,
                    problem=as_str(data.get("problem")),
                    penalty_max=as_int(data.get("penalty_max"), default=4),
                    deterministic=bool(data.get("deterministic", False)),
                )
            )
    return tuple(out)


def _parse_levels(raw: object) -> tuple[IdeologyLevel, ...]:
    out: list[IdeologyLevel] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            data = as_dict(entry)
            out.append(
                IdeologyLevel(
                    min=as_int(data.get("min"), default=0),
                    level=as_str(data.get("level"), default="题材堆砌"),
                    desc=as_str(data.get("desc")),
                )
            )
    return tuple(sorted(out, key=lambda lvl: lvl.min, reverse=True))


@lru_cache(maxsize=1)
def load_ideology_judge_config() -> IdeologyJudgeConfig:
    """Return the cached, typed view over ``ideology_judge.yaml``."""

    raw = load_yaml(_CONFIG_FILENAME)
    penalty = as_dict(raw.get("penalty"))
    targets = as_dict(raw.get("targets"))
    return IdeologyJudgeConfig(
        version=as_str(raw.get("version")),
        dimensions=_parse_dimensions(raw.get("dimensions")),
        penalty_max=as_int(penalty.get("penalty_max"), default=16),
        penalty_high_risk_threshold=as_int(penalty.get("high_risk_threshold"), default=12),
        penalty_strong_ceiling=as_int(penalty.get("strong_ceiling"), default=4),
        penalty_markers=_parse_markers(penalty.get("markers")),
        levels=_parse_levels(raw.get("levels")),
        target_premium=_flt(targets.get("premium_writer"), 78.0),
        target_budget=_flt(targets.get("budget_writer"), 68.0),
    )


def ideology_level_for_score(
    final_score: float, config: IdeologyJudgeConfig | None = None
) -> str:
    config = config or load_ideology_judge_config()
    for level in config.levels:  # sorted descending
        if final_score >= level.min:
            return level.level
    return config.levels[-1].level if config.levels else "题材堆砌"


# ---------------------------------------------------------------------------
# Deterministic penalty prior (from the grounding audit)
# ---------------------------------------------------------------------------


def _deterministic_penalty(
    grounding: IdeologyGroundingResult, config: IdeologyJudgeConfig
) -> tuple[float, tuple[str, ...]]:
    """A soft floor on the sloganization penalty from the grounding flags."""

    flagged: list[str] = []
    penalty = 0.0
    if "thesis_absent_from_outline" in grounding.flagged:
        penalty += 2.0
        flagged.append("theme_homogenization")
    if "low_symbol_grounding" in grounding.flagged:
        penalty += 2.0
        flagged.append("sloganization")
    if "possible_forbidden_resolution" in grounding.flagged:
        penalty += 3.0
        flagged.append("forbidden_resolution")
    return min(penalty, float(config.penalty_max)), tuple(dict.fromkeys(flagged))


# ---------------------------------------------------------------------------
# Prompt assembly (pure — reusable by the standalone pilot)
# ---------------------------------------------------------------------------


def _render_dimension_block(config: IdeologyJudgeConfig) -> str:
    lines: list[str] = []
    for dim in config.dimensions:
        lines.append(
            f"- {dim.key}（{dim.display_name}, 0-{dim.max}）：{dim.definition}"
            f" 评分：{dim.scoring_rule}"
            f" 正例「{dim.pos_example}」 反例「{dim.neg_example}」"
        )
    return "\n".join(lines)


def _render_penalty_block(config: IdeologyJudgeConfig) -> str:
    lines = [
        f"反口号化扣分（0-{config.penalty_max}, 单列, 不并入正向维度）。逐项判其严重度后求和：",
    ]
    for marker in config.penalty_markers:
        lines.append(f"- {marker.marker_id}（≤{marker.penalty_max}）：{marker.problem}")
    return "\n".join(lines)


def build_ideology_judge_system_prompt(
    *,
    config: IdeologyJudgeConfig | None = None,
    genre_context: JudgeGenreContext | None = None,
) -> str:
    config = config or load_ideology_judge_config()
    genre = genre_context.display_genre if genre_context else "(题材中立)"
    dim_keys = "，".join(config.dimension_keys)
    return (
        "# ROLE\n"
        "你是『核心理念(母题)评审器』。你只评一件事：这份大纲有没有『灵魂』——"
        "即一句贯穿全书、能生长出世界观与走向的核心理念(如《诛仙》的「天地不仁」), "
        "并且它是被故事真正戏剧化的发动机, 而不是贴在简介上的标签。\n"
        "你不评商业留存(另有评审器), 但要确认深度没有把书写成纯文青(commercial_compatibility 维度)。\n"
        f"本书题材：{genre}（按本书自身题材判断, 不要套用其它题材的母题)。\n"
        "\n# 核心立场\n"
        "- 母题 ≠ 把主题词写进简介。最高级的做法是删掉所有点题句, 读者仍能亲历那个主题。\n"
        "- 评的是『大纲是否让理念长成故事』：宇宙前提是否一致、信念弧是否落地、代价是否绑定、四层是否都有戏。\n"
        "- 警惕『同题材通用主题』：换一本同类书也成立的主题宣言, 不算本书的灵魂。\n"
        "\n# 九个正向维度（满分合计 100）\n"
        + _render_dimension_block(config)
        + "\n\n# "
        + _render_penalty_block(config)
        + "\n\n# 输出格式（严格 JSON）\n"
        "返回一个 JSON 对象, 含字段：\n"
        f"- 九维整数评分（各在量程内）：{dim_keys}\n"
        f"- sloganization_penalty：整数, 0-{config.penalty_max}\n"
        "- evidence：list, ≥3 条引用大纲具体字段/描述的证据（每条 ≤40 字）\n"
        "- top_issues：list, ≤3 条最该修的问题\n"
        "- revision_priority：list, 按优先级排序的具体修改动作（给规划者照做, 引用要改的卷/章/场景）\n"
        "不要输出 final_score / level（由系统按公式核算）。只输出 JSON, 不要解释。\n"
    )


def build_ideology_judge_user_prompt(
    *,
    kernel: IdeologyKernel | dict[str, Any] | None,
    outline_text: str,
    grounding: IdeologyGroundingResult | None = None,
    max_chars: int = 16000,
) -> str:
    from bestseller.domain.ideology import (
        ideology_kernel_from_dict,
        render_ideology_kernel_prompt_block,
    )

    kernel_obj: IdeologyKernel | None
    if isinstance(kernel, dict):
        try:
            kernel_obj = ideology_kernel_from_dict(kernel)
        except Exception:
            kernel_obj = None
    else:
        kernel_obj = kernel

    kernel_block = render_ideology_kernel_prompt_block(kernel_obj) if kernel_obj else "(无理念内核, 仅按大纲反推应有的理念深度评分)"
    hint = ""
    if grounding is not None and grounding.flagged:
        hint = (
            "\n确定性预扫提示（仅供参考, 最终以你的判断为准）：" + "、".join(grounding.flagged) + "。\n"
        )
    return (
        "# 本书声明的核心理念内核（评分基准 — 大纲是否兑现了它）\n"
        f"{kernel_block}\n\n"
        "# 待评大纲\n"
        f"{hint}"
        f"{(outline_text or '')[:max_chars]}\n\n"
        "请按 system 的九维 + 反口号化扣分给这份大纲打分, 证据必须引用大纲里的具体内容。"
    )


def _ideology_fallback_json(config: IdeologyJudgeConfig) -> str:
    return json.dumps(
        {
            "sloganization_penalty": 0,
            "evidence": [],
            "top_issues": ["IDEOLOGY_JUDGE_UNAVAILABLE"],
            "revision_priority": [],
        },
        ensure_ascii=False,
    )


def parse_ideology_judge_json(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    unfenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I | re.S).strip()
    candidates = [stripped, unfenced]
    match = re.search(r"\{.*\}", unfenced, flags=re.S)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    for candidate in candidates:
        try:
            from json_repair import repair_json

            repaired = repair_json(candidate, return_objects=True)
        except Exception:
            continue
        if isinstance(repaired, dict):
            return repaired
    return {}


def score_ideology_from_judge_json(
    raw_json: str,
    *,
    kernel: IdeologyKernel | dict[str, Any] | None,
    outline_text: str,
    config: IdeologyJudgeConfig | None = None,
    llm_run_id: str | None = None,
) -> IdeologyJudgeResult:
    """Pure scoring path: judge JSON + deterministic grounding floor → result.

    Used by both the production judge and the standalone pilot runner.
    """

    config = config or load_ideology_judge_config()
    grounding = (
        audit_ideology_outline_grounding(kernel, outline_text) if kernel is not None else None
    )
    prior, flagged = (
        _deterministic_penalty(grounding, config) if grounding is not None else (0.0, ())
    )
    return ideology_result_from_mapping(
        parse_ideology_judge_json(raw_json),
        config=config,
        penalty_prior=prior,
        penalty_flagged=flagged,
        llm_run_id=llm_run_id,
        raw_excerpt=(raw_json or "")[:4000],
    )


# ---------------------------------------------------------------------------
# Production entry point
# ---------------------------------------------------------------------------


async def judge_outline_ideology(
    session: AsyncSession,
    settings: AppSettings,
    *,
    outline_text: str,
    kernel: IdeologyKernel | dict[str, Any] | None,
    genre: str | None = None,
    sub_genre: str | None = None,
    story_bible: dict[str, Any] | None = None,
    genre_context: JudgeGenreContext | None = None,
    workflow_run_id: Any | None = None,
) -> IdeologyJudgeResult:
    """Score one outline's ideology depth. Advisory — never gates anything."""

    config = load_ideology_judge_config()
    if genre_context is None:
        genre_context = resolve_judge_genre_context(
            genre=genre, sub_genre=sub_genre, story_bible=story_bible
        )
    grounding = (
        audit_ideology_outline_grounding(kernel, outline_text) if kernel is not None else None
    )

    try:
        from bestseller.services.chapter_llm_quality_judge import (
            resolve_commercial_judge_model_key,
        )

        model_catalog_key = resolve_commercial_judge_model_key(settings)
    except Exception:
        model_catalog_key = None

    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=build_ideology_judge_system_prompt(
                config=config, genre_context=genre_context
            ),
            user_prompt=build_ideology_judge_user_prompt(
                kernel=kernel, outline_text=outline_text, grounding=grounding
            ),
            fallback_response=_ideology_fallback_json(config),
            prompt_template="ideology_judge",
            prompt_version="v1",
            model_catalog_key=model_catalog_key,
            workflow_run_id=workflow_run_id,
            metadata={"judge_scope": "ideology_outline", "advisory": True},
            max_tokens_override=3000,
        ),
    )
    return score_ideology_from_judge_json(
        completion.content,
        kernel=kernel,
        outline_text=outline_text,
        config=config,
        llm_run_id=str(completion.llm_run_id) if completion.llm_run_id else None,
    )


async def judge_outline_ideology_stable(
    session: AsyncSession,
    settings: AppSettings,
    *,
    outline_text: str,
    kernel: IdeologyKernel | dict[str, Any] | None,
    samples: int = 1,
    **kwargs: Any,
) -> IdeologyJudgeResult:
    """Multi-sample median ideology judge — damps single-call variance."""

    config = load_ideology_judge_config()
    n = max(1, int(samples))
    results = [
        await judge_outline_ideology(
            session, settings, outline_text=outline_text, kernel=kernel, **kwargs
        )
        for _ in range(n)
    ]
    if n == 1:
        return results[0]
    med_dims: dict[str, int] = {}
    for dim in config.dimensions:
        vals = [int(r.dimension_scores.get(dim.key, 0)) for r in results]
        med_dims[dim.key] = round(statistics.median(vals))
    med_penalty = round(statistics.median([r.sloganization_penalty for r in results]))
    payload: dict[str, Any] = dict(med_dims)
    payload["sloganization_penalty"] = med_penalty
    med_final = statistics.median([r.final_score for r in results])
    rep = min(results, key=lambda r: abs(r.final_score - med_final))
    payload["evidence"] = list(rep.evidence)
    payload["top_issues"] = list(rep.top_issues)
    payload["revision_priority"] = list(rep.revision_priority)
    return ideology_result_from_mapping(
        payload,
        config=config,
        penalty_prior=0.0,
        penalty_flagged=rep.penalty_flagged,
        llm_run_id=rep.llm_run_id,
        raw_excerpt=rep.raw_excerpt,
    )


def build_ideology_repair_directives(
    result: IdeologyJudgeResult, *, max_items: int = 8
) -> list[str]:
    """Turn a weak ideology reading into concrete outline-regen directives."""

    directives: list[str] = []
    for action in result.revision_priority[:max_items]:
        text = str(action).strip()
        if text:
            directives.append(f"【理念深度整改】{text[:320]}")
    if not directives:
        for issue in result.top_issues[:max_items]:
            text = str(issue).strip()
            if text and "UNAVAILABLE" not in text:
                directives.append(f"【理念深度整改】修正：{text[:320]}")
    return directives


__all__ = [
    "IdeologyDimension",
    "IdeologyJudgeConfig",
    "build_ideology_judge_system_prompt",
    "build_ideology_judge_user_prompt",
    "build_ideology_repair_directives",
    "ideology_level_for_score",
    "judge_outline_ideology",
    "judge_outline_ideology_stable",
    "load_ideology_judge_config",
    "parse_ideology_judge_json",
    "score_ideology_from_judge_json",
]
