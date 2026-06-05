from __future__ import annotations

from collections.abc import Mapping, Sequence

# ruff: noqa: ANN401,RUF001
import json
import os
import re
import statistics
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.llm_quality_judge import (
    LLMQualityJudgeResult,
    quality_judge_result_from_mapping,
)
from bestseller.services.judge_genre_context import (
    GENERIC_CORPUS_KEY,
    JudgeGenreContext,
    resolve_judge_genre_context,
)
from bestseller.services.judge_rubrics import get_judge_rubric
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.methodology_bridge import get_fragment
from bestseller.services.prompt_packs import PromptPack
from bestseller.services.word_targets import (
    model_output_token_ceiling,
    resolve_llm_role_max_tokens,
    resolve_llm_role_model,
)
from bestseller.settings import AppSettings


def _neutral_genre_context() -> JudgeGenreContext:
    """Genre-neutral fallback context (no project genre/bible available)."""

    return resolve_judge_genre_context(genre=None)


def resolve_commercial_judge_model_key(settings: AppSettings) -> str | None:
    """Catalog id of the model the commercial judges should run on (R2).

    Prefers an explicit settings field (``settings.llm.commercial_judge_model_key``),
    falls back to env ``BESTSELLER__LLM__COMMERCIAL_JUDGE_MODEL_KEY``. ``None`` keeps
    the current behaviour (judge on the configured critic model). Point this at a
    Claude-tier ``config/model_catalog.yaml`` entry so ranking quality is judged by a
    capable model regardless of the (possibly budget) writer model — and pair it with
    a Claude-tier writer so :func:`chapter_commercial_thresholds` raises the floor to
    0.92/0.90 automatically.
    """

    key = getattr(getattr(settings, "llm", None), "commercial_judge_model_key", None)
    if not key:
        key = os.getenv("BESTSELLER__LLM__COMMERCIAL_JUDGE_MODEL_KEY") or None
    return str(key) if key else None


# ---------------------------------------------------------------------------
# System prompt assembly (7-段式)
# ---------------------------------------------------------------------------


def _render_chapter_judge_system_prompt(
    *,
    rubric: Any,
    reference_block: str,
    checklist_block: str,
    calibration_block: str,
    genre_context: JudgeGenreContext,
    language: str = "zh",
) -> str:
    """Assemble the chapter_commercial_quality_judge system prompt in 7-段式.

    The "story-logic checks" and the book's own rule terms are now supplied by
    :class:`JudgeGenreContext` (services/judge_genre_context.py) instead of being
    hardcoded to one detective book's jargon (青囊/罗盘/铜钱/认账/镜债). The judge
    therefore scores each book against ITS OWN genre's retention fundamentals and
    ITS OWN setting terms. Stable ordering = Anthropic prompt-cache friendly.
    """
    return (
        "# ROLE\n"
        "你是商业网文榜单主审编辑。\n"
        "你审过 200+ 本签约小说，能从单章里判断这本书签约后能拿什么档次的推送资源。\n"
        "你的判断标准来自三种参照：\n"
        "- 起点 / 番茄 / 七猫的过往榜单作品规律\n"
        "- 阅读编辑培训手册（开篇留存、付费转化、读者画像）\n"
        "- 你自己 5 年退稿经验里的「签约死线」\n"
        "\n"
        "# CONTEXT\n"
        "你正在评审单章是否达到商业榜单可用标准。\n"
        "你的评分直接决定：这一章是 publish 还是 rewrite。\n"
        "评分不是「感觉打」，必须基于参考样本对比 + 二元检查项 + 原文引用 evidence。\n"
        "评分尺度必须贴合**本书所属题材**的留存规律，不要拿其它题材的套路硬套。\n"
        "\n"
        "# CONTEXT · 商业留存的底层规律（黄金三章尤其重要）\n"
        "## 故事合理性 — 任一明显缺失 → 必判 blocking\n"
        + genre_context.render_story_logic_block(language)
        + genre_context.render_own_terms_block(language)
        + "\n"
        "# TASK\n"
        "对本章打 16 个维度分（见 OUTPUT），并产出 blocking_issues / audit_issues / rewrite_plan。\n"
        "对二元检查项逐项判定 PASS / FAIL（见 checklist 段）。\n"
        "\n"
        "# CONSTRAINTS · 评分纪律（必须严格执行）\n"
        "- 不要给「感觉上还不错」的章节打 0.85+，除非它通过了参考样本对比。\n"
        "- 0.90+ 只给在参考样本水准上能正面竞争的章节。\n"
        "- 任意二元检查项 FAIL → 对应维度封顶（见 checklist 说明）。\n"
        "- 每个 issue 必须含 evidence 字段，引用正文原句（≥ 1 句，禁止用「全章」/「整体」占位）。\n"
        "- 上述 6 项故事合理性任一明显缺失 → 必判 blocking_issues（不能降为 audit）。\n"
        "- 出现现实常识硬伤 / 角色认知越界 / 物件规则无边界 / 与生成输入前提冲突 → 必判 blocking。\n"
        "\n"
        "# THINKING（产出 JSON 前在脑内 5 步）\n"
        "1. 先读正文，标记你直觉上的「亮点段」和「卡顿段」。\n"
        "2. 对照 16 个维度逐项内心打分。\n"
        "3. 检查 6 项故事合理性 + checklist 二元项，任一缺失立即升级 blocking。\n"
        "4. 检查 evidence：你的每个 issue 能引用原文 ≤ 30 字吗？不能引用 → 弃用。\n"
        "5. Reconcile：overall_score 是否与 blocking_issues 数量一致？（≥1 blocking → overall ≤ 0.75）\n"
        "\n"
        "# OUTPUT FORMAT · 16 维度评分\n"
        "返回严格 JSON，必含字段：\n"
        "- `pass`: bool\n"
        "- `overall_score`: 0.0-1.0\n"
        "- `dimension_scores`: 含 16 项 0.0-1.0 评分（见 user 段维度列表）\n"
        "- `binary_checklist`: 见 checklist 段 schema\n"
        "- `blocking_issues`: list[{code, severity, evidence(原文≤30字), required_fix}]\n"
        "- `audit_issues`: list[同 schema]\n"
        "- `rewrite_plan`: {scope, preserve[], change[], instructions}\n"
        "\n"
        "# REFERENCE CORPUS（评分校准 — 实际样本）\n"
        + reference_block
        + checklist_block
        + calibration_block
        + "\n# RUBRIC（评分细则原文）\n"
        + rubric.render_prompt_block()
        + "\n# RUBRIC · system 起源\n"
        + rubric.system_prompt
    )


# ---------------------------------------------------------------------------
# Reference corpus loader
# ---------------------------------------------------------------------------

_REFERENCE_CORPORA_DIR = (
    Path(__file__).parent.parent.parent.parent
    / "config"
    / "reference_corpora"
)


@lru_cache(maxsize=16)
def _load_reference_corpus(genre_key: str) -> dict[str, Any] | None:
    """Load a reference corpus YAML by genre key.

    When the genre-specific file is missing, fall back to the genre-NEUTRAL
    ``generic.yaml`` rather than ``None``. Returning ``None`` previously let the
    caller keep its ``suspense-mystery`` default, so a 言情/科幻 book was scored
    against detective samples. Falling back to ``generic`` keeps calibrated floors
    while injecting zero genre bias. Returns ``None`` only if even ``generic`` is
    unreadable.
    """

    def _read(key: str) -> dict[str, Any] | None:
        path = _REFERENCE_CORPORA_DIR / f"{key}.yaml"
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    data = _read(genre_key)
    if data is not None:
        return data
    if genre_key != GENERIC_CORPUS_KEY:
        return _read(GENERIC_CORPUS_KEY)
    return None


def _render_reference_block(corpus: dict[str, Any] | None, *, max_chars: int = 4000) -> str:
    """Render the reference corpus samples into a concise judge-readable block.

    Limits output to ``max_chars`` so it doesn't crowd out the main chapter
    text in the context window.
    """
    if not corpus:
        return ""
    samples = corpus.get("samples") or []
    if not samples:
        return ""

    parts: list[str] = ["## 榜单级参考样本（校准用）\n"]
    parts.append(
        "以下是同类型（悬疑/驱魔）榜单级章节的代表性开篇片段。"
        "评分时请将被评章节与这些样本对比，而不是凭感觉打分。\n"
    )

    total = 0
    for sample in samples[:4]:  # max 4 samples to stay within budget
        label = sample.get("label", "")
        excerpt = (sample.get("excerpt") or "").strip()
        why = sample.get("why_bestseller_quality") or []
        if not excerpt:
            continue

        block = f"\n### {label}\n\n**原文片段：**\n{excerpt}\n\n**榜单级理由：**\n"
        block += "\n".join(f"- {r}" for r in why[:4])
        block += "\n"

        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)

    return "".join(parts)


def _render_binary_checklist(corpus: dict[str, Any] | None) -> str:
    """Render the binary checklist items for the judge."""
    if not corpus:
        return ""
    items = corpus.get("binary_checklist") or []
    if not items:
        return ""

    parts = [
        "\n## 强制二元检查项（黄金三章必须全部通过）\n\n"
        "以下每项只有 PASS / FAIL 两种结果，不打分。你必须针对每项从正文中"
        "引用具体证据句子（≥1句），否则视为该项无法验证，记为 FAIL。\n\n"
        "**缺2项 → 总分上限0.75；缺3项 → 总分上限0.65；缺4项+ → 总分上限0.55**\n"
    ]
    for item in items:
        item_id = item.get("id", "")
        label = item.get("label", "")
        desc = (item.get("description") or "").strip()
        parts.append(f"\n**{item_id}** ｜ {label}\n{desc}\n")

    return "".join(parts)


def _render_calibration_anchors(corpus: dict[str, Any] | None) -> str:
    if not corpus:
        return ""
    cal = corpus.get("calibration") or {}
    anchors = cal.get("score_anchors") or []
    if not anchors:
        return ""

    parts = ["\n## 分数锚点（校准用）\n"]
    for a in anchors:
        parts.append(f"- **{a['score']}** — {a['label']}：{a['description']}\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Threshold resolution
# ---------------------------------------------------------------------------

# Claude-tier "榜单" floors. When the configured writer is a frontier model that
# can actually reach this band, the gate is held to the original 0.92/0.90 line
# instead of the MiniMax-calibrated corpus band — so the acceptance line tracks the
# writer's real ceiling automatically rather than via a manual YAML toggle.
_PREMIUM_GOLDEN_THREE_FLOOR = 0.92
_PREMIUM_GOLDEN_THREE_DIMENSION_FLOOR = 0.90
_PREMIUM_FRONT_TEN_FLOOR = 0.90
_PREMIUM_GENERAL_FLOOR = 0.88

_PREMIUM_WRITER_MODEL_TAGS: tuple[str, ...] = (
    "claude", "opus", "sonnet", "gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "gemini-2",
)


def is_premium_writer_model(model: str | None) -> bool:
    """Whether the writer model is a frontier (Claude-tier) model.

    Such models can reach the 0.92/0.90 ranking band, so the gate should hold them
    to it. MiniMax / DeepSeek / other budget tiers use the corpus-calibrated band.
    """

    if not model:
        return False
    m = str(model).lower()
    return any(tag in m for tag in _PREMIUM_WRITER_MODEL_TAGS)


def chapter_commercial_thresholds(
    chapter_number: int,
    corpus: dict[str, Any] | None = None,
    writer_model: str | None = None,
) -> tuple[float, dict[str, float]]:
    """Return (overall_min, per_dimension_min) for chapter quality gating.

    The floor is **bound to the writer model tier**:
    * Claude-tier writer → the original 0.92/0.90 ranking line.
    * MiniMax / budget writer → the corpus-calibrated acceptance band (defaults
      0.85/0.82), which is that model's measured ranking-tier ceiling.

    This removes the manual "降到 0.85 因为写手弱 / 升级后记得调回 0.92" toggle:
    swapping the writer model automatically moves the acceptance line. Both bands
    remain corpus-overridable per genre.
    """
    cal = (corpus or {}).get("calibration") or {}
    premium = is_premium_writer_model(writer_model)

    if chapter_number <= 3:
        if premium:
            overall_floor = _PREMIUM_GOLDEN_THREE_FLOOR
            gd = _PREMIUM_GOLDEN_THREE_DIMENSION_FLOOR
        else:
            overall_floor = float(cal.get("golden_three_floor", 0.85))
            gd = float(cal.get("golden_three_dimension_floor", 0.82))
        return overall_floor, {
            "opening_pull": gd,
            "commercial_pull": gd,
            "readability": gd,
            "knowledge_boundary": gd,
            "real_world_plausibility": gd,
            "object_signal_logic": gd,
            "call_plausibility": gd,
            "capability_demonstrated": gd,
        }
    if chapter_number <= 10:
        overall_floor = (
            _PREMIUM_FRONT_TEN_FLOOR if premium else float(cal.get("chapter_4_to_10_floor", 0.85))
        )
        bump = 0.08 if premium else 0.0
        return overall_floor, {
            "hook_strength": 0.82 + bump,
            "continuity": 0.84 + bump,
            "knowledge_boundary": 0.82 + bump,
            "real_world_plausibility": 0.80 + bump,
        }
    overall_floor = (
        _PREMIUM_GENERAL_FLOOR if premium else float(cal.get("general_floor", 0.80))
    )
    return overall_floor, {}


# Writer-facing directives for each scored hard dimension. Single-sourced here so
# the writer self-check block can never drift from what the gate actually scores
# (chapter_commercial_thresholds). Keyed by dimension key → (zh, en) directive.
_RANKING_DIMENSION_DIRECTIVES: dict[str, tuple[str, str]] = {
    "opening_pull": (
        "开篇拉力：前 3 行就抛出具体危险 / 异常物 / 反常状态，逼读者必须读下去，不靠铺垫。",
        "Opening pull: a concrete danger / anomaly lands in the first 3 lines — no warm-up.",
    ),
    "commercial_pull": (
        "商业拉力：本章必须兑现一个爽点 / 反转 / 强钩子，给读者明确的追更理由。",
        "Commercial pull: this chapter must deliver one payoff / reversal / strong hook.",
    ),
    "readability": (
        "可读性：句子干净、节奏有呼吸（长短交错）、零 AI 套话；两人对话不看标签可分辨。",
        "Readability: clean sentences, varied rhythm, zero AI cliché; speakers distinguishable without tags.",
    ),
    "knowledge_boundary": (
        "认知边界：每个角色只知道他此刻该知道的，禁止越界知道未发生 / 未被告知的信息。",
        "Knowledge boundary: each character knows only what they could know — no leaking future/untold info.",
    ),
    "real_world_plausibility": (
        "现实合理性：涉及职业 / 规程 / 常识处给真实细节与因果链，禁止硬伤与想当然。",
        "Real-world plausibility: real detail + causal chain for any profession/procedure/common sense.",
    ),
    "object_signal_logic": (
        "物件信号逻辑：异常物件每次表现都有稳定规则与来由，主角能据此合理推断，不随手发动。",
        "Object-signal logic: the anomalous object follows stable, inferable rules — never ad hoc.",
    ),
    "call_plausibility": (
        "通话合理性：电话 / 对讲机 / 消息的内容与时机贴合情境，不为推进剧情强行通话。",
        "Call plausibility: calls/messages fit the situation in content and timing — never plot-convenient.",
    ),
    "capability_demonstrated": (
        "能力展示：主角的金手指 / 能力在本章有一次清晰的现实层作用，并付出可见代价。",
        "Capability demonstrated: the protagonist's power has one clear real-world effect at a visible cost.",
    ),
    "hook_strength": (
        "章末钩子：结尾留一个具体未解物 / 倒计时 / 新威胁，禁止抽象感叹收尾。",
        "Hook strength: end on a concrete unresolved object / countdown / new threat — no abstract sigh.",
    ),
    "continuity": (
        "连续性：与前文人物状态、伏笔、时间线一致，承接自然，不与已写设定冲突。",
        "Continuity: consistent with prior state, foreshadowing, and timeline — no contradictions.",
    ),
    "commercial_potential": (
        "商业潜力：题材爽感与卖点在本章可感知，符合目标平台读者口味。",
        "Commercial potential: the hook/selling point is felt and fits the target platform's readers.",
    ),
}


def render_ranking_self_check_block(
    chapter_number: int,
    language: str = "zh",
    corpus: dict[str, Any] | None = None,
) -> str:
    """Render a writer-facing 'ranking-tier self-check' block whose dimensions are
    derived from :func:`chapter_commercial_thresholds` — so the writer is told the
    exact rubric (and floors) the gate will score it against. Single-sourced: if the
    gate's hard dimensions change, this block changes with them. Returns '' when the
    chapter has no hard dimensions (later chapters)."""

    _, min_dims = chapter_commercial_thresholds(chapter_number, corpus)
    if not min_dims:
        return ""
    is_en = str(language or "").lower().startswith("en")
    lines: list[str] = []
    for key, floor in min_dims.items():
        directive = _RANKING_DIMENSION_DIRECTIVES.get(key)
        text = directive[1] if (directive and is_en) else (directive[0] if directive else key)
        lines.append(f"- [{key} ≥ {floor:.2f}] {text}")
    body = "\n".join(lines)
    if is_en:
        return (
            "# RANKING-TIER SELF-CHECK (the gate scores these exact dimensions — "
            "every one must clear its floor before this chapter passes)\n"
            f"{body}\n"
        )
    return (
        "# 榜单级硬维度自检（门禁就按这些维度打分，本章交付前每一项都必须达标）\n"
        f"{body}\n"
    )


# ---------------------------------------------------------------------------
# Main judge entry point
# ---------------------------------------------------------------------------

async def judge_chapter_commercial_quality(
    session: AsyncSession,
    settings: AppSettings,
    *,
    chapter_number: int,
    content_md: str,
    generation_input: Mapping[str, Any] | None = None,
    previous_chapters: Sequence[Mapping[str, Any]] = (),
    workflow_run_id: Any | None = None,
    pack: PromptPack | None = None,
    reference_corpus_key: str | None = None,
    genre_context: JudgeGenreContext | None = None,
    language: str = "zh",
) -> LLMQualityJudgeResult:
    # Genre-neutral context: scores this book against ITS OWN genre + setting terms
    # rather than a hardcoded detective rubric. Falls back to neutral when absent.
    if genre_context is None:
        genre_context = _neutral_genre_context()
    # Corpus selection: explicit key wins, else the genre's own corpus, else generic.
    # NEVER silently defaults to suspense-mystery for a non-detective book.
    corpus_key = reference_corpus_key or genre_context.corpus_key or GENERIC_CORPUS_KEY
    corpus = _load_reference_corpus(corpus_key)

    # Bind the acceptance floor to the configured writer model tier (F7).
    writer_model = resolve_llm_role_model(settings, role="writer")
    min_overall, min_dimensions = chapter_commercial_thresholds(
        chapter_number, corpus, writer_model
    )

    # This book's own rule terms / key objects, for the genre-neutral hard rules.
    own_terms = "、".join(genre_context.specialist_terms) or "本书设定中的专业/规则术语"
    own_objects = "、".join(genre_context.key_objects) or "本书的关键道具/能力/信号物"

    generation_input_text = json.dumps(
        generation_input or {},
        ensure_ascii=False,
        indent=2,
        default=str,
    )[:12000]
    previous_chapters_text = json.dumps(
        list(previous_chapters),
        ensure_ascii=False,
        indent=2,
        default=str,
    )[:6000]

    fallback = json.dumps(
        {
            "pass": False,
            "overall_score": 0.0,
            "dimension_scores": {},
            "binary_checklist": {},
            "blocking_issues": [
                {
                    "code": "CHAPTER_JUDGE_UNAVAILABLE",
                    "severity": "critical",
                    "evidence": "LLM chapter quality judge returned fallback content.",
                    "required_fix": "重新运行商业质量评测，不能在无评测状态下置为完成。",
                }
            ],
            "rewrite_plan": {
                "scope": "chapter",
                "preserve": [],
                "change": ["commercial quality validation"],
                "instructions": "重新评测并基于具体维度补强正文。",
            },
        },
        ensure_ascii=False,
    )

    # Build methodology injection block
    methodology_refs: list[str] = []
    if chapter_number <= 3:
        for key in ("opening_rules", "character_design"):
            text = get_fragment(pack, phase="judge", fragment_key=key)
            if text:
                methodology_refs.append(f"【{key}】\n{text}")
    for key in ("spring_model", "stakes_design"):
        text = get_fragment(pack, phase="judge", fragment_key=key)
        if text:
            methodology_refs.append(f"【{key}】\n{text}")
    hook_design = get_fragment(pack, phase="judge", fragment_key="hook_design")
    if hook_design:
        methodology_refs.append(f"【hook_design】\n{hook_design}")
    methodology_section = (
        "\n\n## 评估时必须参照的方法论标准\n\n"
        "以下是本作类型的写作方法论原文。你的 methodology_compliance 评分必须基于本章是否遵循这些规则，"
        "而不是凭感觉打分。打分时请在 audit_issues 或 blocking_issues 的 evidence 字段引用具体违反的方法论条款。\n\n"
        + "\n\n".join(methodology_refs)
        if methodology_refs
        else ""
    )

    # Build reference corpus blocks
    reference_block = _render_reference_block(corpus)
    checklist_block = _render_binary_checklist(corpus)
    calibration_block = _render_calibration_anchors(corpus)
    rubric = get_judge_rubric("chapter_commercial")

    # Build the binary_checklist response schema description
    checklist_items = (corpus or {}).get("binary_checklist") or []
    checklist_ids = [item.get("id", "") for item in checklist_items if item.get("id")]
    binary_checklist_schema = (
        "binary_checklist: {"
        + ", ".join(
            f'"{cid}": {{"result": "PASS"|"FAIL", "evidence": "<正文原句>"}}'
            for cid in checklist_ids
        )
        + "}"
        if checklist_ids
        else ""
    )

    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=_render_chapter_judge_system_prompt(
                rubric=rubric,
                reference_block=reference_block,
                checklist_block=checklist_block,
                calibration_block=calibration_block,
                genre_context=genre_context,
                language=language,
            ),
            user_prompt=(
                f"章节：第{chapter_number}章\n"
                f"通过阈值：overall >= {min_overall:.2f}；关键维度："
                f"{json.dumps(min_dimensions, ensure_ascii=False)}\n"
                "评测维度：opening_pull, readability, commercial_pull, character_agency, "
                "character_voice_distinction, scene_execution, continuity, "
                "methodology_compliance, hook_strength, knowledge_boundary, "
                "real_world_plausibility, object_signal_logic, "
                "call_plausibility, capability_demonstrated, material_advancement_score。\n"
                "\n硬性判定口径（以本书自有设定为准，不要套用其它题材的器物/术语）：\n"
                "1. 非专业 / 普通角色不得无来由地理解或主动讲出本书设定中的专业 / 规则术语"
                f"（本书自有术语：{own_terms}）；除非正文明确写出被传授 / 附身 / 操控 / 刚被主角教会。\n"
                "2. 报警 / 门禁 / 监控 / 医院 / 警方 / 快递 / 平台等现实流程必须符合常识；"
                "若刻意反常或为不可能证据，正文必须明确让角色意识到'不可能 / 异常'。\n"
                f"3. 本书的关键道具 / 能力 / 信号（{own_objects}）必须有稳定边界：每次表现分别代表什么、"
                "能做什么、不能做什么，主角能据此合理推断。若整章 3 次以上用单一感官捷径"
                "（如'发烫 / 心头一跳 / 一阵眩晕'）推进剧情而无多样化变化，"
                "必须标 OBJECT_SIGNAL_SHORTCUT（audit）；偶尔使用是合理的写法。\n"
                "4. 开场媒介：如果整章主体是电话/短信/微信单一媒介，没有任何现场画面或"
                "物理动作支撑，必须标 OPENING_MEDIUM_WEAK（audit）。"
                "但电话/短信作为信息传递手段配合现场行动是合理的写法。\n"
                "5. 如果生成输入摘要包含 Material obligation packet、rule-ledger、"
                "reveal-schedule、clue-ledger 或 required_evidence，必须判断正文是否真正推进"
                "对应规则/揭示/证据，并在 material_advancement_score 中给分；未兑现的必演物料"
                "必须进入 blocking_issues 或 audit_issues。\n"
                "\n必须返回字段：pass, overall_score, dimension_scores, "
                + (f"{binary_checklist_schema}, " if binary_checklist_schema else "")
                + "blocking_issues, audit_issues, rewrite_plan。\n"
                "每个 blocking_issue 和 audit_issue 必须包含 evidence 字段，"
                "引用正文原句（≥1句，不能只写'全章'或'整体'）。\n"
                "生成输入摘要：\n"
                f"{generation_input_text}\n"
                "最近章节：\n"
                f"{previous_chapters_text}\n"
                f"{methodology_section}\n"
                "正文：\n"
                f"{content_md[:18000]}"
            ),
            fallback_response=fallback,
            prompt_template="chapter_commercial_quality_judge",
            prompt_version="v2",
            model_catalog_key=resolve_commercial_judge_model_key(settings),
            workflow_run_id=workflow_run_id,
            metadata={
                "judge_scope": "chapter",
                "chapter_number": chapter_number,
                "threshold": min_overall,
                "reference_corpus_key": reference_corpus_key,
                "rubric": rubric.name,
            },
            max_tokens_override=_critic_judge_max_tokens(settings),
        ),
    )
    return quality_judge_result_from_mapping(
        _parse_json_object(completion.content),
        scope="chapter",
        min_overall=min_overall,
        min_dimensions=min_dimensions,
        llm_run_id=str(completion.llm_run_id) if completion.llm_run_id else None,
        raw_excerpt=completion.content[:6000],
    )


def _judge_samples_count() -> int:
    """榜单判官多采样次数(默认3取中位以消除单次方差);env JUDGE_SAMPLES 可调。"""
    try:
        return max(1, int(os.getenv("JUDGE_SAMPLES", "3") or 3))
    except ValueError:
        return 3


async def judge_chapter_commercial_quality_stable(
    session: AsyncSession,
    settings: AppSettings,
    *,
    chapter_number: int,
    content_md: str,
    samples: int | None = None,
    reference_corpus_key: str | None = None,
    genre_context: JudgeGenreContext | None = None,
    **kwargs: Any,
) -> LLMQualityJudgeResult:
    """稳定版榜单判官:多采样取中位,消除单次 critic 评分方差(同稿曾 0.80~0.92)。

    判官 LLM 单次输出方差可达 0.1+,使门禁变成"会乱跳的线"、无法收敛。
    本函数对同一稿判 N 次,对 overall 与每个维度取中位,再按阈值重新判定 pass,
    把方差压到 <0.03,让门禁可信、rewrite 反馈可用。samples=1 即退化为原行为。

    题材中立:corpus 与故事合理性来自 ``genre_context``(按本书题材),不再默认探案。
    """
    if genre_context is None:
        genre_context = _neutral_genre_context()
    corpus_key = reference_corpus_key or genre_context.corpus_key or GENERIC_CORPUS_KEY
    n = samples if samples is not None else _judge_samples_count()
    n = max(1, int(n))
    results: list[LLMQualityJudgeResult] = []
    for _ in range(n):
        results.append(
            await judge_chapter_commercial_quality(
                session, settings,
                chapter_number=chapter_number, content_md=content_md,
                reference_corpus_key=corpus_key, genre_context=genre_context, **kwargs,
            )
        )
    if n == 1:
        return results[0]

    med_overall = statistics.median(r.overall_score for r in results)
    dim_keys: set[str] = set()
    for r in results:
        dim_keys.update(r.dimension_scores.keys())
    med_dims: dict[str, float] = {}
    for k in dim_keys:
        vals = [float(r.dimension_scores[k]) for r in results if k in r.dimension_scores]
        if vals:
            med_dims[k] = statistics.median(vals)

    corpus = _load_reference_corpus(corpus_key)
    writer_model = resolve_llm_role_model(settings, role="writer")
    min_overall, min_dims = chapter_commercial_thresholds(
        chapter_number, corpus, writer_model
    )
    # _eps: median aggregation can yield 0.8999999999999999 for a logical 0.90;
    # only fail a floor when meaningfully below it (matches domain meets_threshold).
    _eps = 1e-6
    passed = med_overall >= min_overall - _eps and all(
        med_dims.get(k, 0.0) >= m - _eps for k, m in min_dims.items()
    )
    # 取最接近中位的那次作为代表(保留其 issues/rewrite_plan),覆盖聚合分与 pass
    rep = min(results, key=lambda r: abs(r.overall_score - med_overall))
    return rep.model_copy(update={
        "passed": passed,
        "overall_score": float(med_overall),
        "dimension_scores": med_dims,
    })


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
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


def _critic_judge_max_tokens(settings: AppSettings) -> int:
    configured = resolve_llm_role_max_tokens(settings, role="critic")
    if configured and configured > 0:
        return configured
    model_ceiling = model_output_token_ceiling(
        resolve_llm_role_model(settings, role="critic")
    )
    if model_ceiling and model_ceiling > 0:
        return model_ceiling
    return 8192
