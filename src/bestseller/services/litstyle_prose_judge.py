"""Advisory LitStyle-100R 文采 judge.

A second, *parallel* judge to the 16-dimension commercial judge
(``chapter_llm_quality_judge``). Where the commercial judge scores "留住读者"
(retention), this one scores "打动读者" (literary craft) on LitStyle's 9 positive
dimensions + an AI腔 penalty.

**Hard design — this judge is advisory only.** It returns a
:class:`LitStyleJudgeResult` with no ``passed`` field; callers write it into
``evidence_summary`` and (optionally) drive a soft polish loop from it. It never
contributes to a chapter's ``verdict`` / ``blocking_issues`` / any gate.

It reuses the established judge machinery:

* ``complete_text`` with ``logical_role="critic"`` (and the same optional
  commercial-judge model override, so文采 can be judged by a Claude-tier model).
* the genre-neutral :class:`JudgeGenreContext` for a per-genre 文采 emphasis hint.
* the deterministic :func:`detect_ai_tone` pass as an ``ai_tone_penalty`` floor.
* multi-sample median (``judge_chapter_litstyle_stable``) to damp judge variance.
"""

# ruff: noqa: ANN401, RUF001, E501

from __future__ import annotations

import json
import os
import re
import statistics
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.litstyle_judge import (
    LitStyleJudgeResult,
    litstyle_result_from_mapping,
)
from bestseller.services.judge_genre_context import (
    JudgeGenreContext,
    resolve_judge_genre_context,
)
from bestseller.services.litstyle_prose import (
    AiToneResult,
    LitStyleConfig,
    detect_ai_tone,
    load_litstyle_config,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings

# ---------------------------------------------------------------------------
# Genre 文采 emphasis (orthogonal to the commercial judge's story-logic checks)
# ---------------------------------------------------------------------------

# Substring → emphasis hint. Keeps the文采 尺 genre-aware without inventing a new
# taxonomy: 古风 leans on imagery/blank-space, modern genres on colloquial bite /
# concreteness / contrast — the same anti-purple routing the writer levers use.
_GENRE_PROSE_EMPHASIS: tuple[tuple[str, str], ...] = (
    ("古风", "意象系统与留白优先；意象要具体到能成像，忌空泛大词。"),
    ("仙侠", "意象系统与气象优先，但具象度不能让位于辞藻。"),
    ("武侠", "节奏感与反差优先，动作里见文采。"),
    ("悬疑", "留白与以景结情优先，靠克制制造余味。"),
    ("恐怖", "感官密度与留白优先，氛围靠具体可感细节。"),
    ("言情", "感官密度与虚实相生优先，克制使用通感。"),
    ("都市", "口语锋利与具象度优先，忌古风滤镜与辞藻堆砌。"),
    ("职场", "白描克制与反差优先，金句靠判断的准。"),
    ("现实", "白描克制与具象度优先，力量来自准与短。"),
    ("科幻", "虚实相生与反差优先，概念落到可感画面。"),
)


def _genre_prose_emphasis(genre_context: JudgeGenreContext) -> str:
    haystack = " ".join(
        (genre_context.display_genre, genre_context.category_key, *genre_context.signal_keywords)
    )
    for needle, hint in _GENRE_PROSE_EMPHASIS:
        if needle in haystack:
            return hint
    return "先具象、后修辞；先气息、后金句；先意象系统、后局部华彩；先叙事适配、后风格炫耀。"


def _neutral_genre_context() -> JudgeGenreContext:
    return resolve_judge_genre_context(genre=None)


# ---------------------------------------------------------------------------
# Prompt assembly (pure, unit-testable without an LLM)
# ---------------------------------------------------------------------------


def _render_dimension_block(config: LitStyleConfig) -> str:
    lines: list[str] = []
    for dim in config.dimensions:
        lines.append(
            f"- {dim.key}（{dim.display_name}，0-{dim.max}）：{dim.definition}"
            f" 评分：{dim.scoring_rule}"
            f" 正例「{dim.pos_example}」 反例「{dim.neg_example}」"
        )
    return "\n".join(lines)


def _render_ai_tone_block(config: LitStyleConfig) -> str:
    lines = [
        f"AI腔扣分（0-{config.ai_tone_penalty_max}，单列，不并入正向维度）。"
        "只判语言症候，绝不判定作者是否使用 AI。逐项判其严重度后求和：",
    ]
    for marker in config.ai_tone_markers:
        lines.append(f"- {marker.marker_id}（≤{marker.penalty_max}）：{marker.problem}")
    return "\n".join(lines)


def _render_calibration_block(config: LitStyleConfig, *, max_anchors: int = 3) -> str:
    if not config.calibration_anchors:
        return ""
    parts = ["# 校准锚点（按这些已判样本对齐你的尺度，不要凭感觉打分）"]
    for anchor in config.calibration_anchors[:max_anchors]:
        scores = "，".join(f"{k}={v}" for k, v in anchor.scores.items())
        parts.append(
            f"\n## {anchor.level}（FinalScore={anchor.final}）\n"
            f"原文：{anchor.excerpt.strip()}\n"
            f"各维：{scores}\n"
            f"判语：{anchor.note}"
        )
    return "\n".join(parts)


def build_litstyle_system_prompt(
    *,
    config: LitStyleConfig | None = None,
    genre_context: JudgeGenreContext | None = None,
    language: str = "zh",
) -> str:
    """Assemble the文采 judge system prompt (stable ordering = cache-friendly)."""

    config = config or load_litstyle_config()
    genre_context = genre_context or _neutral_genre_context()
    emphasis = _genre_prose_emphasis(genre_context)
    dim_keys = "，".join(config.dimension_keys)
    return (
        "# ROLE\n"
        "你是中文文学风格评审器（LitStyle-100R）。你只评『文采 / 语言质感』——"
        "即文本是否把意义写成读者能感到、看到、记住并愿意回味的东西。你不评剧情对错、不评商业留存。\n"
        "\n"
        "# 核心立场\n"
        "- 文采 ≠ 辞藻华丽。最高级的语言常让读者忘记作者在用力，只记住某种气味、光影、动作或余震。\n"
        "- 词藻密、信息少 = 低分；语言朴素但细节准、节奏稳、意象成体系、主题有回响 = 高分。\n"
        f"- 本题材文采侧重：{emphasis}\n"
        "- 只判 AI腔语言症候，**绝不判定作者是否使用 AI**（这是风格风险项，不是来源证明）。\n"
        "\n"
        "# 九个正向维度（满分合计 100）\n"
        + _render_dimension_block(config)
        + "\n\n# "
        + _render_ai_tone_block(config)
        + "\n\n"
        + _render_calibration_block(config)
        + "\n\n# 输出格式（严格 JSON）\n"
        "返回一个 JSON 对象，含字段：\n"
        f"- 九维整数评分（各在其量程内）：{dim_keys}\n"
        "- ai_tone_penalty：整数，0-"
        f"{config.ai_tone_penalty_max}\n"
        "- evidence：list，≥3 条引用正文原句的证据（每条 ≤30 字）\n"
        "- top_issues：list，≤3 条最该修的问题\n"
        "- revision_priority：list，按优先级排序的具体修改动作（给写手照做）\n"
        "不要输出 final_score / level（由系统按公式核算）。只输出 JSON，不要解释。\n"
    )


def build_litstyle_user_prompt(
    *,
    chapter_number: int,
    content_md: str,
    ai_tone: AiToneResult | None = None,
    language: str = "zh",
    max_chars: int = 16000,
) -> str:
    hint = ""
    if ai_tone is not None and ai_tone.flagged:
        hint = (
            "\n确定性预扫提示（仅供参考，最终以你的判断为准）：疑似 AI腔——"
            + "、".join(ai_tone.flagged)
            + f"；建议 ai_tone_penalty 不低于 {round(ai_tone.deterministic_penalty)}。\n"
        )
    return (
        f"章节：第{chapter_number}章\n"
        "请严格按 LitStyle-100R 给下面正文打分，证据必须引用正文原句。\n"
        f"{hint}"
        "正文：\n"
        f"{content_md[:max_chars]}"
    )


def _litstyle_fallback_json() -> str:
    return json.dumps(
        {
            "ai_tone_penalty": 0,
            "evidence": [],
            "top_issues": ["LITSTYLE_JUDGE_UNAVAILABLE"],
            "revision_priority": [],
        },
        ensure_ascii=False,
    )


def _parse_json_object(text: str) -> dict[str, Any]:
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
        except Exception:  # noqa: S112 - best-effort JSON repair, try next candidate
            continue
        if isinstance(repaired, dict):
            return repaired
    return {}


# ---------------------------------------------------------------------------
# Judge entry points
# ---------------------------------------------------------------------------


async def judge_chapter_litstyle(
    session: AsyncSession,
    settings: AppSettings,
    *,
    chapter_number: int,
    content_md: str,
    genre_context: JudgeGenreContext | None = None,
    language: str = "zh",
    workflow_run_id: Any | None = None,
) -> LitStyleJudgeResult:
    """Score one chapter's 文采. Advisory — the result never gates anything."""

    config = load_litstyle_config()
    genre_context = genre_context or _neutral_genre_context()
    ai_tone = detect_ai_tone(content_md, config)

    # Optional Claude-tier judge override, shared with the commercial judge.
    try:
        from bestseller.services.chapter_llm_quality_judge import (
            resolve_commercial_judge_model_key,
        )

        model_catalog_key = resolve_commercial_judge_model_key(settings)
    except Exception:
        model_catalog_key = None

    try:
        from bestseller.services.word_targets import resolve_llm_role_max_tokens

        max_tokens = resolve_llm_role_max_tokens(settings, role="critic") or 4096
    except Exception:
        max_tokens = 4096

    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=build_litstyle_system_prompt(
                config=config, genre_context=genre_context, language=language
            ),
            user_prompt=build_litstyle_user_prompt(
                chapter_number=chapter_number, content_md=content_md, ai_tone=ai_tone,
                language=language,
            ),
            fallback_response=_litstyle_fallback_json(),
            prompt_template="litstyle_prose_judge",
            prompt_version="v1",
            model_catalog_key=model_catalog_key,
            workflow_run_id=workflow_run_id,
            metadata={
                "judge_scope": "litstyle_prose",
                "chapter_number": chapter_number,
                "advisory": True,
            },
            max_tokens_override=min(int(max_tokens), 4096),
        ),
    )
    return litstyle_result_from_mapping(
        _parse_json_object(completion.content),
        config=config,
        ai_tone_prior=ai_tone.deterministic_penalty,
        ai_tone_flagged=ai_tone.flagged,
        llm_run_id=str(completion.llm_run_id) if completion.llm_run_id else None,
        raw_excerpt=completion.content[:4000],
    )


def _litstyle_samples_count() -> int:
    try:
        return max(1, int(os.getenv("LITSTYLE_JUDGE_SAMPLES", "1") or 1))
    except ValueError:
        return 1


async def judge_chapter_litstyle_stable(
    session: AsyncSession,
    settings: AppSettings,
    *,
    chapter_number: int,
    content_md: str,
    samples: int | None = None,
    genre_context: JudgeGenreContext | None = None,
    language: str = "zh",
    workflow_run_id: Any | None = None,
) -> LitStyleJudgeResult:
    """Multi-sample median文采 judge — damps single-call variance.

    文采 is subjective, so the same draft can wobble across calls. Median-aggregate
    each dimension + the penalty, then recompute FinalScore/level deterministically.
    ``samples`` defaults to ``LITSTYLE_JUDGE_SAMPLES`` (1 — cheap; advisory). Set 3
    for tighter convergence on a real run.
    """

    config = load_litstyle_config()
    genre_context = genre_context or _neutral_genre_context()
    n = max(1, int(samples if samples is not None else _litstyle_samples_count()))
    results: list[LitStyleJudgeResult] = []
    for _ in range(n):
        results.append(
            await judge_chapter_litstyle(
                session, settings,
                chapter_number=chapter_number, content_md=content_md,
                genre_context=genre_context, language=language,
                workflow_run_id=workflow_run_id,
            )
        )
    if n == 1:
        return results[0]

    med_dims: dict[str, int] = {}
    for dim in config.dimensions:
        vals = [int(r.dimension_scores.get(dim.key, 0)) for r in results]
        med_dims[dim.key] = round(statistics.median(vals))
    med_penalty = round(statistics.median([r.ai_tone_penalty for r in results]))

    # Rebuild deterministically from the median dims + penalty (no double-flooring:
    # each sample already floored by the deterministic prior).
    payload: dict[str, Any] = dict(med_dims)
    payload["ai_tone_penalty"] = med_penalty
    # Keep the representative sample's qualitative fields (closest to median final).
    med_final = statistics.median([r.final_score for r in results])
    rep = min(results, key=lambda r: abs(r.final_score - med_final))
    payload["evidence"] = list(rep.evidence)
    payload["top_issues"] = list(rep.top_issues)
    payload["revision_priority"] = list(rep.revision_priority)
    return litstyle_result_from_mapping(
        payload,
        config=config,
        ai_tone_prior=0.0,  # already reflected in each sample's penalty
        ai_tone_flagged=rep.ai_tone_flagged,
        llm_run_id=rep.llm_run_id,
        raw_excerpt=rep.raw_excerpt,
    )


__all__ = [
    "build_litstyle_system_prompt",
    "build_litstyle_user_prompt",
    "judge_chapter_litstyle",
    "judge_chapter_litstyle_stable",
]
