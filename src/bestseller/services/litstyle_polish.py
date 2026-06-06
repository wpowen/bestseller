"""文采定点润色器 — the *improvement* mechanism of the LitStyle closed loop.

The advisory judge (``litstyle_prose_judge``) only *measures* 文采. This module
turns a judge reading into a **targeted polish instruction**: it maps the low
dimensions + the judge's own ``revision_priority`` onto concrete rewrite
directives (the LitStyle creative prompts: 具象改写 / 感官增厚 / 节奏调音 / 留白裁剪
/ 意象回返 / 去AI腔), under strict anti-regression constraints (preserve plot,
length, continuity).

It is a **pure prompt builder** — generation + keep-better selection live in the
caller (the A/B harness now, the in-pipeline P3 loop later). The loop is only
*safe* because the caller keeps the higher-scoring of {original, polished}; this
module just produces the candidate.
"""

# ruff: noqa: RUF001, E501

from __future__ import annotations

from bestseller.domain.litstyle_judge import LitStyleJudgeResult
from bestseller.services.litstyle_prose import LitStyleConfig, load_litstyle_config

# Per-dimension fix directive (the LitStyle creative prompts, distilled to an
# imperative the writer applies in place — techniques, not phrases).
_DIMENSION_FIX_DIRECTIVES: dict[str, str] = {
    "concrete": "具象度：把抽象判断与情绪标签改写成「人物动作＋物件＋空间关系」，每段至少落到一个看得见的具体物或动作，删掉形容词。",
    "visuality": "画面感：给关键段补「人物/动作/空间/光影方位」中至少三项，让读者能把这段拍成镜头。",
    "sensory": "感官密度：在不改情节的前提下补至少 1 种非视觉感官（听觉/嗅觉/触觉），克制不堆砌。",
    "rhythm": "节奏感：调句长波动与停顿——长短句交错、关键句前后做「收—放—收」，只改断句/标点，不改信息。",
    "imagery_system": "意象系统：让已经出现过的主意象再现一次并推进它的含义（别换新意象、别堆砌 ≥3 个意象）。",
    "blank_space": "留白：删掉直接解释主题/情绪的句子，把判断留给读者；情绪到顶时用一个具体动作或物的特写收尾，不点破。",
    "originality": "原创度：把陈词套语（如『悲伤如潮水』）换成本故事世界里的具体物或动作；比喻要新而贴切。",
    "theme_unity": "主题统一度：让华彩句回扣本章主题核，删掉与主题无关的炫技描写。",
    "narrative_fit": "叙事适配度：每处描写都要服务推进/塑造/造境/映照主题；删掉『好看但剧情停住』的句子。",
}

_AI_TONE_FIX_DIRECTIVE = (
    "去AI腔：删对称句式（『不是…而是』『他终于明白』）、删情感标签直陈（『震惊/痛苦/无助』），"
    "删段末『道理』收束；把这些改写成可见的动作、物件变化或被逼出来的台词。"
)

_POLISH_SYSTEM_PROMPT = (
    "你是中文小说语言润色师。你只做『定点文采润色』，不是重写、不是续写、不是改故事。\n"
    "铁律（违反任何一条都算失败）：\n"
    "1. 不改剧情、人物、设定、信息量、对话内容与先后顺序；不增删情节、不新造与正文冲突的专名。\n"
    "2. 字数与原文相当（控制在 ±12% 以内）；段落结构大体保留。\n"
    "3. 只针对下方诊断指出的薄弱维度做润色，不要为了炫技而堆砌辞藻——文采靠具体、不靠华丽。\n"
    "4. 只输出润色后的正文，不要任何解释、标题、标签或诊断回显。"
)


def _low_dimension_keys(
    result: LitStyleJudgeResult,
    config: LitStyleConfig,
    *,
    ratio_threshold: float,
    max_dims: int,
) -> list[str]:
    """Dimensions scoring below ``ratio_threshold`` of their max, weakest first."""

    ranked: list[tuple[float, str]] = []
    for dim in config.dimensions:
        score = float(result.dimension_scores.get(dim.key, 0))
        ratio = score / dim.max if dim.max else 1.0
        if ratio < ratio_threshold:
            ranked.append((ratio, dim.key))
    ranked.sort()
    return [key for _, key in ranked[:max_dims]]


def build_litstyle_polish_prompt(
    *,
    draft: str,
    result: LitStyleJudgeResult,
    config: LitStyleConfig | None = None,
    ratio_threshold: float = 0.75,
    max_dims: int = 4,
    max_chars: int = 16000,
) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for a targeted 文采 polish.

    The fixes are chosen from the dimensions that scored below
    ``ratio_threshold`` of their cap (plus an AI腔 fix when the penalty is high),
    capped at ``max_dims`` to avoid overload, and paired with the judge's own
    ``revision_priority`` for chapter-specific guidance.
    """

    config = config or load_litstyle_config()
    low_keys = _low_dimension_keys(
        result, config, ratio_threshold=ratio_threshold, max_dims=max_dims
    )
    directives = [_DIMENSION_FIX_DIRECTIVES[k] for k in low_keys if k in _DIMENSION_FIX_DIRECTIVES]
    if result.ai_tone_penalty > config.ai_tone_mature_ceiling:
        directives.append(_AI_TONE_FIX_DIRECTIVE)

    if not directives:
        # Already clean on every dimension — fall back to a light, generic pass
        # rather than fabricating fixes (the caller's keep-better guards regressions).
        directives = ["整体已较好：只在不改信息的前提下做轻微的节奏与留白微调，不得堆砌辞藻。"]

    fix_block = "\n".join(f"- {d}" for d in directives)
    priority_block = ""
    if result.revision_priority:
        priority_block = "\n本章具体修改优先级（裁判给出，按此照做）：\n" + "\n".join(
            f"- {action}" for action in result.revision_priority[:5]
        )

    user_prompt = (
        "请对下面这段正文做定点文采润色。\n"
        f"当前文采诊断：FinalScore={result.final_score}/100（{result.level}），"
        f"AI腔扣分={result.ai_tone_penalty}。\n"
        "需要修复的薄弱维度：\n"
        f"{fix_block}"
        f"{priority_block}\n"
        "\n原文（保剧情、保字数、保连续性，只润色语言）：\n"
        f"{draft[:max_chars]}"
    )
    return _POLISH_SYSTEM_PROMPT, user_prompt


__all__ = ["build_litstyle_polish_prompt"]
