from __future__ import annotations

# ruff: noqa: ANN401,RUF001
import asyncio
from collections.abc import Mapping, Sequence
import contextlib
from functools import lru_cache
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import statistics
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
import yaml

from bestseller.domain.llm_quality_judge import (
    LLMQualityJudgeResult,
    quality_judge_result_from_mapping,
)
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.independent_quality_judge import (
    BlindJudgeInput,
    IndependentJudgeResult,
    run_independent_quality_judge,
)
from bestseller.services.judge_genre_context import (
    GENERIC_CORPUS_KEY,
    JudgeGenreContext,
    resolve_judge_genre_context,
)
from bestseller.services.judge_rubrics import get_judge_rubric
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.methodology_bridge import render_methodology_applicability
from bestseller.services.prompt_packs import PromptPack
from bestseller.services.word_targets import (
    model_output_token_ceiling,
    resolve_llm_role_max_tokens,
    resolve_llm_role_model,
)

logger = logging.getLogger(__name__)
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


async def judge_chapter_pair_advisory(
    session: AsyncSession,
    settings: AppSettings,
    *,
    genre: str,
    chapter_number: int,
    compact_contract: str,
    draft_a: str,
    draft_b: str,
    writer_model: str | None = None,
    editor_model: str | None = None,
) -> IndependentJudgeResult:
    """Explicit shadow adapter; never changes chapter promotion state."""

    return await run_independent_quality_judge(
        session,
        settings,
        BlindJudgeInput(
            genre=genre,
            chapter_number=chapter_number,
            compact_contract=compact_contract,
            draft_a=draft_a,
            draft_b=draft_b,
        ),
        writer_model=writer_model or settings.llm.writer.model,
        editor_model=editor_model or settings.llm.editor.model,
        strict=settings.llm.independent_judge_strict_model_family,
    )


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
    """Use source-grounded assessment; reference assets cannot prescribe a story."""
    return (
        "# ROLE\n评估当前章节的可读性、连贯性与商业吸引力，只输出严格 JSON。\n"
        "# SOURCE AUTHORITY\n"
        "创作者明确要求、已成立的身份与能力边界、已发生 Canon/Timeline 及当前明确批准的硬条款优先。"
        "AI 章纲、场次、节奏、tail_hook 和兑现时间若无明确 hard 来源，"
        "偏离是 editorial plan variance；"
        "不得仅因偏离而 blocking，也不能撤销符合上位来源的实际进展。"
        "题材分类、提示词包、参考样本、全局方法论、旧报告和数值评分均不能创造剧情义务。\n"
        "Source precedence: explicit creator requirements, established identity/ability limits and "
        "occurred canon/timeline outrank unmarked AI proposals. Established factual constraints "
        "remain hard without a hard label, but observations are scoped to "
        "the event/state observed.\n"
        "允许在已成立的故事边界内创作新的当下或未来行动、人物、物件、对白、发现与有因果的状态变化；"
        "这些发展不要求在前章已出现或预先逐项写入批准清单。"
        "Prior silence and open inventory observations are not closed sets or prohibitions. "
        "Missing approval fields do not forbid new causal fictional development. "
        "新正文不能据此改写已采用的过去，也不能违反有正面来源证明且实际适用的明确身份或世界不变量。\n"
        "首句/首段长度、倒叙次数、钩子数量、能力展示与代价、揭示节奏和反应人数，"
        "只有当前明确批准且适用的条款才能成为要求；须引用条款及来源。"
        "默认 opening gate、golden rules、语料与历史报错不构成授权，"
        "旧报告及旧整改建议均不构成授权。"
        "AI 提案不自动成为硬前提。Numeric opening limits "
        "require an explicitly approved current clause and source.\n"
        "以往观测值不是未来永久上限；新事件、练习、发现和状态变化可在真实不可变规则内继续发展。"
        "缺失或未批准的 secret/growth_curve/first_three_hook 等规划字段属于 Unknown，"
        "不是禁止新能力、"
        "新事件或推进。职业身份、角色和题材标签本身不构成知识禁令；叙述者/他人感受不等于主角知道。"
        "先核对当前文本已写出的解释，不得一边承认文本正确一边据同一理由硬拒绝。\n"
        "事实冲突须证明：同一历史事件或同一时点、同一对象状态出现互斥陈述，"
        "或者当前发展违反有明确来源的身份/世界不变量；不能把先前没有某状态当作永远不许变化。"
        "unexplained_transition 应指出前后状态之间缺少交代的必要因果环节，"
        "不得把过渡缺环写成永久能力禁令，也不得仅因没有进度表或审批字段而禁止进展。\n"
        "# ASSESSMENT\n"
        "在实际故事中检查人物动机、事件因果、可理解性与吸引力，允许不同叙事方式。"
        "不要预设主角有金手指、升级、复仇、羞辱、系统、异常物件或通话；不适用的维度中性记录，"
        "不得因未采用某技巧扣分或封顶。没有后果证据的技巧偏好只记 audit。"
        "身份矛盾、已建立能力违规、认知越界、既成事实冲突须给来源和正文证据；"
        "缺少来源应记 Unknown，不能补造。"
        "明确的语义缺陷须指出读者受到的具体影响，不能以句长/词频/场数单独证明。"
        "decision_intelligence：先按角色当时实际已知信息作第一人称决策复盘，比较正常人基线与角色基线。"
        "明显为剧情服务且没有动机依据的降智可标 PROTAGONIST_PLOT_SERVING_STUPIDITY，"
        "须引用具体选择及其实际后果；不能以职业或题材标签代替已知信息证据。\n"
        "# REPAIR\n"
        "只对有证据的缺陷给最小修复方向，可指出需要补足的功能性环节，例如起因、信息取得或行动后果，"
        "不要替作者编造具体的新人物、道具、能力、期限或模板剧情作为唯一解。"
        "这限制的是评审建议越权指定故事，不是禁止正文在明确不变量内发展新的有因果情节。"
        "建议不能伪称作者已授权、补造已经发生的历史或改写已采用事实。"
        "区分硬冲突、编辑计划差异和技巧建议；商业分数不代表真实榜单或市场验证。\n"
        "# OUTPUT — commercial-review-wire.v2\n"
        "仅调用submit_chapter_review一次提交报告，不执行其他工具；参数是严格JSON。顶层恰为 wire_version,pass,overall_score,dimension_scores,issues,rewrite_plan；"
        "wire_version 必须为 commercial-review-wire.v2。仅有真实适用RULE时另加checks。"
        "分数0..1，按全文真实质量评分；issues统一列表，优先聚焦3至5个真实问题，无问题则[]。"
        "不要按blocking/audit拆表；severity描述所评影响，不证明事实冲突或作者授权成立。"
        "最终执行动作由服务端 action policy 决定，模型不能仅凭severity宣告剧情必须被禁止或重写。"
        "每个issue恰为 {code,severity,assessment_kind,body_id,reason,required_fix}。"
        "assessment_kind为narrative_quality/current_fact_conflict/prior_fact_conflict/constraint_violation。"
        "body_id须为目录中真实current段落ID；不重复抄写原文、不输出quote或kind。"
        "事实冲突和条款违例另需source_id；current_fact_conflict只能引current，prior_fact_conflict只能引prior/observation，constraint_violation只能引RULE。"
        "事实冲突另需temporal_relation: same_event_or_state或unexplained_transition；不能把后续进展当旧观测的违例。"
        "引用ID表示该完整段落或来源记录；服务端从不可变目录精确取得原文并验证来源权限，模型不能制造原文。"
        "reason解释所引内容的具体语义缺陷、已知信息与阅读影响；不要把猜测、类型惯例或不存在的前文写成事实。"
        "narrative_quality用于阅读体验；任何关于前文发生过什么、人物能否知道什么的断言应使用事实类型并提供真实source_id。"
        "不得把前文缺失、作者是否授权、能力是否允许或库存是否排他的事实/权威断言改标为narrative_quality来绕过来源要求；"
        "即使在reason中手写SRC标识，也不能代替结构化source_id及对应类型的证据。"
        "不输出body/source对象、evidence、source_assertion、current_assertion、binary_checklist或旧列表字段。"
        "不可发明RULE，缺失字段不是禁令，未选择的方法/技巧不构成义务。"
        "rewrite_plan:{scope,preserve:[],change:[],instructions}；只为有证据的真实缺陷指出最小功能性修复需求，"
        "保留作者对具体剧情实现的选择，不把缺少旧记录误写成禁止新发展。\n"
        + render_methodology_applicability(language=language, phase="judge")
    )


# ---------------------------------------------------------------------------
# Reference corpus loader
# ---------------------------------------------------------------------------

_REFERENCE_CORPORA_DIR = Path(__file__).parent.parent.parent.parent / "config" / "reference_corpora"


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


def _render_reference_block(
    corpus: dict[str, Any] | None,
    *,
    max_chars: int = 4000,
    genre_context: JudgeGenreContext | None = None,
) -> str:
    """Render the reference corpus samples into a concise judge-readable block.

    Limits output to ``max_chars`` so it doesn't crowd out the main chapter
    text in the context window.
    """
    if not corpus:
        return ""
    samples = corpus.get("samples") or []
    if not samples:
        return ""

    display_genre = (genre_context.display_genre if genre_context else None) or "本类型"

    parts: list[str] = ["## 榜单级参考样本（校准用）\n"]
    parts.append(
        f"以下是同类型（{display_genre}）榜单级章节的代表性开篇片段。"
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


# Built-in chapter-1 checklist item — every genre corpus inherits it. A cold
# reader (never saw the blurb) must be onboarded inside chapter 1; the 《子时客》
# incident shipped a ch1 where the governing rules were never stated and no
# gate objected. Kept in code (not per-corpus yaml) so no genre can lose it.
_COLD_READER_CH1_CHECKLIST_ITEM: dict[str, Any] = {
    "id": "cold_reader_onboarding",
    "label": "冷读者五锚点（仅第1章）",
    "description": (
        "假设读者没看过简介。仅凭本章正文，读者必须能回答全部五问：\n"
        "①主角是谁、什么身份处境？②这个地方/这局是什么？\n"
        "③主宰本章生死的核心规则的完整内容是什么（必须在规则第一次生效之前"
        "就对读者完整可见，只提规则名不给内容=FAIL）？\n"
        "④主角的金手指/差异化优势是什么、有什么用（一句话能说清）？\n"
        "⑤章末主角赢了什么、付出了什么？\n"
        "五问中任何一问无法从正文引出证据句，本项即 FAIL。"
    ),
    "failing_examples": [
        "全章反复渲染规则可怕，但规则内容从头到尾没亮出来，读者只能猜",
        "金手指生效了三次，读者仍说不出它是什么、怎么用",
    ],
    "passing_examples": [
        "核心规则在第一次生效前被完整织进动作/对白，读者先懂规则再看它杀人",
        "章末读者能一句话盘点：签成首单+攒到好评，代价是赔掉一只耳朵的听觉",
    ],
}


def _render_binary_checklist(
    corpus: dict[str, Any] | None,
    *,
    chapter_number: int | None = None,
) -> str:
    """Render the binary checklist items for the judge."""
    if not corpus:
        return ""
    items = list(corpus.get("binary_checklist") or [])
    if chapter_number == 1 and not any(
        item.get("id") == "cold_reader_onboarding" for item in items
    ):
        items.insert(0, _COLD_READER_CH1_CHECKLIST_ITEM)
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
    "claude",
    "opus",
    "sonnet",
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "o1",
    "o3",
    "gemini-2",
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
            "decision_intelligence": max(gd, 0.84),
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
            "decision_intelligence": 0.84 + bump,
            "knowledge_boundary": 0.82 + bump,
            "real_world_plausibility": 0.80 + bump,
        }
    overall_floor = _PREMIUM_GENERAL_FLOOR if premium else float(cal.get("general_floor", 0.80))
    return overall_floor, {
        "decision_intelligence": 0.88 if premium else 0.84,
    }


# Writer-facing directives for each scored hard dimension. Single-sourced here so
# the writer self-check block can never drift from what the gate actually scores
# (chapter_commercial_thresholds). Keyed by dimension key → (zh, en) directive.
_RANKING_DIMENSION_DIRECTIVES: dict[str, tuple[str, str]] = {
    "opening_pull": (
        "开篇拉力：结合本书明确意图评估是否吸引读者，不预设危险、异常物或固定行数。",
        "Opening pull: engage the reader according to the approved intent, without prescribed events or line counts.",
    ),
    "commercial_pull": (
        "商业拉力：评估已有内容的吸引力，不强制爽点、反转或固定钩子。",
        "Commercial pull: assess engagement without requiring a payoff, reversal or prescribed hook.",
    ),
    "decision_intelligence": (
        "决策智性：按第一人称比较正常人基线、角色基线和明摆着的安全方案；高风险选择必须有有限认知、强制压力、价值取舍、稀缺收益或可执行后手支撑。",
        "Decision intelligence: compare the normal-person baseline, character baseline, and obvious safer options; high-risk choices require bounded knowledge, coercive pressure, values, rare upside, or a concrete contingency.",
    ),
    "readability": (
        "可读性：句子干净、节奏有呼吸（长短交错）、零 AI 套话；两人对话不看标签可分辨；"
        "读者随时知道主角此刻想要什么/怕什么/赌什么（有可感的内心声音，不是全程生理症状）。",
        "Readability: clean sentences, varied rhythm, zero AI cliché; speakers distinguishable "
        "without tags; the reader always knows what the POV character wants/fears/wagers "
        "(a felt inner voice, not physiology-only).",
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
        "能力展示：仅按已批准的能力与本章要求核查；不得强制金手指显效或新增代价。",
        "Capability demonstrated: assess only approved powers and current requirements; do not require an effect or invent a cost.",
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
    return f"# 榜单级硬维度自检（门禁就按这些维度打分，本章交付前每一项都必须达标）\n{body}\n"


# ---------------------------------------------------------------------------
# Main judge entry point
# ---------------------------------------------------------------------------


def _commercial_source_projection(
    source: Mapping[str, Any] | None,
    *,
    chapter_number: int | None = None,
    content_md: str = "",
    previous_chapters: Sequence[Mapping[str, Any]] = (),
) -> Mapping[str, Any]:
    """Select typed source context; preserve facts, clauses and adopted prose."""
    from bestseller.services.commercial_review_context import project_commercial_source

    return project_commercial_source(
        source,
        chapter_number=chapter_number,
        content_md=content_md,
        previous_chapters=previous_chapters,
    )


async def judge_chapter_commercial_quality(
    session: AsyncSession,
    settings: AppSettings,
    *,
    chapter_number: int,
    content_md: str,
    generation_input: Mapping[str, Any] | None = None,
    previous_chapters: Sequence[Mapping[str, Any]] = (),
    workflow_run_id: Any | None = None,
    project_id: Any | None = None,
    pack: PromptPack | None = None,
    reference_corpus_key: str | None = None,
    genre_context: JudgeGenreContext | None = None,
    language: str = "zh",
    sample_slot: str | int | None = None,
    commercial_retry_context: Mapping[str, Any] | None = None,
    chapter_id: Any | None = None,
    draft_id: Any | None = None,
    commercial_cohort_samples: int = 1,
    commercial_retained_only: bool = False,
) -> LLMQualityJudgeResult:
    generation_input = _commercial_source_projection(
        generation_input,
        chapter_number=chapter_number,
        content_md=content_md,
        previous_chapters=previous_chapters,
    )
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

    from bestseller.services.commercial_source_evidence import (
        PROMPT_VERSION,
        assemble_commercial_sources,
        commercial_input_receipt,
        commercial_judge_unavailable,
        commercial_output_token_budget,
    )

    try:
        sources = assemble_commercial_sources(
            chapter_number=chapter_number,
            content_md=content_md,
            source=generation_input,
            previous_chapters=previous_chapters,
        )
    except (ValueError, TypeError) as exc:
        return commercial_judge_unavailable(
            diagnostic=f"commercial source assembly unavailable: {exc}"
        )

    from bestseller.services.commercial_review_wire import (
        ADAPTER_VERSION,
        WIRE_VERSION,
        parse_commercial_wire,
    )

    from bestseller.services.commercial_judge_recovery import (
        bind_commercial_slot_result,
        prepare_commercial_slot_retry,
    )

    fallback = json.dumps({"judge_unavailable": True}, ensure_ascii=False)

    methodology_section = render_methodology_applicability(
        language=language,
        phase="judge",
    )

    # Build reference corpus blocks
    reference_block = ""  # Reference prose is never automatic creative authority.
    # Corpus checklists are reference assets, not creator-approved chapter
    # obligations. Current task checks remain visible in generation_input.
    checklist_block = ""
    calibration_block = ""
    rubric = get_judge_rubric("chapter_commercial")

    rule_ids = [key for key, entry in sources.catalog.items() if entry["kind"] == "constraint"]
    rule_checks_prompt = (
        "\nchecks 必须恰以这些真实 RULE IDs 为键："
        + json.dumps(rule_ids, ensure_ascii=False)
        + "；每项仅 {result:'PASS'或'FAIL',body_id,reason}。"
        "body_id 是与该条款有关的真实当前段落ID；FAIL 的 reason 给出明确语义违例；服务端验证引文后派生缺失的 critical 问题，不必在列表重复。"
        if rule_ids
        else "\n当前无适用 RULE；不要返回 checks 或 binary_checklist。"
    )

    system_prompt = _render_chapter_judge_system_prompt(
        rubric=rubric,
        reference_block=reference_block,
        checklist_block=checklist_block,
        calibration_block=calibration_block,
        genre_context=genre_context,
        language=language,
    )
    user_prompt = (
        f"章节：第{chapter_number}章\n"
        f"通过阈值：overall >= {min_overall:.2f}；关键维度："
        f"{json.dumps(min_dimensions, ensure_ascii=False)}\n"
        "评测维度：opening_pull, readability, commercial_pull, "
        "character_agency, decision_intelligence, "
        "character_voice_distinction, scene_execution, continuity, "
        "methodology_compliance, hook_strength, knowledge_boundary, "
        "real_world_plausibility, object_signal_logic, "
        "call_plausibility, capability_demonstrated, material_advancement_score。\n"
        "\n核查当前来源条款与实际事件的因果、身份和认知边界。"
        "计划和规则评审快照是待核实输入，不是已发生事实；没有来源的技巧要求不得补造。\n"
        "\n必须返回 commercial-review-wire.v2 的新格式；字段 wire_version,pass,overall_score,dimension_scores,"
        "issues,rewrite_plan。\n"
        "每个 issue 必须使用系统所列类型化来源证据协议，选择完整来源目录中的真实ID，原文由服务端取回，不要复写引文。\n"
        f"{methodology_section}{rule_checks_prompt}\n"
        "完整来源包（JSON 数据，含本章全文、已提供前章和类型化引用目录）：\n"
        f"{sources.text}"
    )
    from bestseller.services.commercial_review_tool import (
        TOOL_POLICY,
        review_submission_tools,
        submitted_review_payload,
        review_evaluation_contract,
    )

    tools = review_submission_tools(sources)
    output_budget = commercial_output_token_budget(_critic_judge_max_tokens(settings))
    evaluation_contract = review_evaluation_contract(
        settings=settings,
        sources=sources,
        system_prompt=system_prompt,
        instruction_prefix=user_prompt.split(sources.text, 1)[0],
        tools=tools,
        min_overall=min_overall,
        min_dimensions=min_dimensions,
        model_catalog_key=resolve_commercial_judge_model_key(settings),
        output_tokens=output_budget,
    )
    from bestseller.services.commercial_review_transport import (
        CommercialReviewTransportError, prepare_commercial_review_transport,
        prepare_commercial_review_submission,
    )

    logical_user_prompt = user_prompt
    try:
        user_prompt, tools, source_transport = prepare_commercial_review_submission(
            system_prompt, logical_user_prompt, sources=sources,
            tools=tools, output_tokens=output_budget)
    except CommercialReviewTransportError as exc:
        return commercial_judge_unavailable(
            diagnostic="complete commercial source input exceeds admission budget; no paid judge call",
            raw=json.dumps(exc.receipt, ensure_ascii=False),
        )
    if "tool_schema_transport" in source_transport:
        evaluation_contract = review_evaluation_contract(
            settings=settings,
            sources=sources,
            system_prompt=system_prompt,
            instruction_prefix=logical_user_prompt.split(sources.text, 1)[0],
            tools=tools,
            min_overall=min_overall,
            min_dimensions=min_dimensions,
            model_catalog_key=resolve_commercial_judge_model_key(settings),
            output_tokens=output_budget,
        )
    if commercial_retained_only and (chapter_id is None or draft_id is None):
        return commercial_judge_unavailable(
            diagnostic="commercial retained probe requires exact chapter/draft binding"
        )
    component_call = None
    effective_critic = None
    prepared_input_hash = None
    if chapter_id is not None or draft_id is not None:
        try:
            from bestseller.services.commercial_judge_recovery import (
                prepare_commercial_component_slot,
            )

            effective_critic = await _commercial_effective_critic_configuration(
                session, settings, project_id
            )
            from bestseller.services.llm import _effective_input_hash
            from bestseller.settings import LLMRoleSettings

            prepared_input_hash = _effective_input_hash(
                LLMCompletionRequest(
                    logical_role="critic", system_prompt=system_prompt, user_prompt=user_prompt,
                    fallback_response=fallback, tools=tools, tool_choice="required",
                    max_tokens_override=output_budget,
                ), LLMRoleSettings.model_validate(effective_critic),
            )
            reusable, component_call = await prepare_commercial_component_slot(
                session,
                project_id=project_id,
                chapter_id=chapter_id,
                draft_id=draft_id,
                workflow_run_id=workflow_run_id,
                content_md=content_md,
                sources=sources,
                evaluation_contract=evaluation_contract,
                sample_slot=sample_slot,
                samples=commercial_cohort_samples,
                retry_context=commercial_retry_context,
                effective_critic=effective_critic,
                retained_only=commercial_retained_only,
                current_input_hash=prepared_input_hash, settings=settings,
            )
        except (ValueError, TypeError, KeyError) as exc:
            return commercial_judge_unavailable(
                diagnostic=f"commercial component recovery unavailable: {exc}"
            )
        if reusable is not None:
            return reusable
        commercial_retry_context = component_call.get("retry_context")
    recovery_prompt, recovery_metadata = "", None
    if commercial_retry_context is not None:
        try:
            reusable, recovery_prompt, recovery_metadata = prepare_commercial_slot_retry(
                commercial_retry_context,
                sample_slot=sample_slot,
                sources=sources,
                evaluation_contract=evaluation_contract,
            )
        except (ValueError, TypeError) as exc:
            return commercial_judge_unavailable(
                diagnostic=f"commercial slot recovery unavailable: {exc}",
            )
        if reusable is not None:
            return reusable

    if recovery_prompt:
        try:
            user_prompt, source_transport = prepare_commercial_review_transport(
                system_prompt, logical_user_prompt + recovery_prompt,
                sources=sources, tools=tools, output_tokens=output_budget)
        except CommercialReviewTransportError as exc:
            return commercial_judge_unavailable(
                diagnostic="complete commercial source retry input exceeds admission budget; no paid judge call",
                raw=json.dumps(exc.receipt, ensure_ascii=False),
            )
    input_receipt = commercial_input_receipt(
        sources,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        output_tokens=output_budget,
        tools=tools,
    )
    if not input_receipt["budget_passed"]:
        return commercial_judge_unavailable(
            diagnostic="complete commercial source input exceeds admission budget; "
            "no paid judge call",
            raw=json.dumps(input_receipt, ensure_ascii=False),
        )
    from bestseller.services.llm import bind_execution_operation

    completion_request = LLMCompletionRequest(
        logical_role="critic",
        model_tier="strong",
        tools=tools,
        tool_choice="required",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        fallback_response=fallback,
        prompt_template="chapter_commercial_quality_judge",
        prompt_version=PROMPT_VERSION,
        sample_slot=sample_slot,
        model_catalog_key=resolve_commercial_judge_model_key(settings),
        workflow_run_id=component_call["owner_id"] if component_call else workflow_run_id,
        project_id=project_id,
        metadata={
            **({"prepared_effective_input": {"schema": "effective-input.v1", "hash": prepared_input_hash}}
               if prepared_input_hash is not None and commercial_retry_context is None else {}),
            "commercial_source_input_receipt": input_receipt,
            **({"commercial_source_transport": source_transport}
               if (source_transport["encoding"] != "identity"
                   or "tool_schema_transport" in source_transport) else {}),
            "commercial_evidence_recovery": recovery_metadata,
            "commercial_wire_version": WIRE_VERSION,
            "commercial_adapter_version": ADAPTER_VERSION,
            "commercial_submission_policy": TOOL_POLICY,
            "commercial_evaluation_contract": evaluation_contract,
            "commercial_sample_slot": sample_slot,
            "commercial_effective_critic_sha256": hashlib.sha256(
                json.dumps(
                    effective_critic,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest()
            if effective_critic
            else None,
            "judge_scope": "chapter",
            "chapter_number": chapter_number,
            "threshold": min_overall,
            # Acceptance floor is bound to the writer tier — record which
            # tier set the line so a silent floor drop after a writer swap
            # is visible in llm_runs instead of only in behaviour.
            "writer_model": writer_model,
            "writer_tier": "premium" if is_premium_writer_model(writer_model) else "budget",
            "reference_corpus_key": reference_corpus_key,
            "rubric": rubric.name,
            "methodology_applicability": "approved-contracts-only.v1",
        },
        max_tokens_override=output_budget,
    )
    slot_source_receipt = sources.receipt
    if component_call:
        from bestseller.services.commercial_judge_recovery import (
            freeze_commercial_component_request,
        )

        completion_request, slot_source_receipt = await freeze_commercial_component_request(
            session, component_call, completion_request, sources.receipt
        )
    with (
        bind_execution_operation(component_call["namespace"])
        if component_call
        else contextlib.nullcontext()
    ):
        completion = await complete_text(session, settings, completion_request)

    raw_report, submission_receipt, submission_errors = submitted_review_payload(completion)

    async def record_slot(
        result: LLMQualityJudgeResult, errors: tuple[str, ...]
    ) -> LLMQualityJudgeResult:
        bound = bind_commercial_slot_result(
            result,
            raw_report=raw_report,
            validation_errors=errors,
            source_receipt=slot_source_receipt,
            sample_slot=sample_slot,
            attempt=component_call["attempt"]
            if component_call
            else (2 if commercial_retry_context is not None else 1),
            adapter_receipt={**adapter_receipt, "submission": submission_receipt},
            evaluation_contract=evaluation_contract,
        )
        if component_call:
            from bestseller.services.commercial_judge_recovery import save_commercial_component_slot

            await save_commercial_component_slot(session, component_call, bound)
        return bound

    adapter_receipt = {
        "adapter_version": ADAPTER_VERSION,
        "wire_version": WIRE_VERSION,
        "source_binding": dict(slot_source_receipt),
        "status": "not_run_output_truncated",
    }
    if completion.finish_reason in {"length", "max_tokens", "max_output_tokens"}:
        return await record_slot(
            commercial_judge_unavailable(
                diagnostic="commercial judge output truncated at structured-review reserve; judge-only repair",
                raw=completion.content,
                result=LLMQualityJudgeResult(
                    llm_run_id=str(completion.llm_run_id) if completion.llm_run_id else None,
                ),
            ),
            ("output: truncated at structured-review reserve",),
        )
    parsed, adapter_receipt, evidence_errors = parse_commercial_wire(raw_report, sources)
    if submission_errors:
        # Retain returned scores for audit without granting review authority.
        adapter_receipt["wire_validation_status"] = adapter_receipt["status"]
        adapter_receipt["status"] = "rejected_submission"
        evidence_errors = (*submission_errors, *evidence_errors)
    try:
        result = quality_judge_result_from_mapping(
            parsed,
            scope="chapter",
            min_overall=min_overall,
            min_dimensions=min_dimensions,
            llm_run_id=str(completion.llm_run_id) if completion.llm_run_id else None,
            raw_excerpt=raw_report[:6000],
        )
    except (ValueError, TypeError):
        return await record_slot(
            commercial_judge_unavailable(
                diagnostic="commercial judge response schema is unverified",
                raw=completion.content,
                result=LLMQualityJudgeResult(
                    llm_run_id=str(completion.llm_run_id) if completion.llm_run_id else None,
                ),
            ),
            ("report: response schema is unverified",),
        )
    if evidence_errors:
        return await record_slot(
            commercial_judge_unavailable(
                diagnostic="commercial source evidence unverified: " + "; ".join(evidence_errors),
                raw=completion.content,
                result=result,
            ),
            evidence_errors,
        )
    return await record_slot(result, ())


async def _commercial_effective_critic_configuration(
    session, settings, project_id
) -> dict[str, Any]:
    """Resolve the same concrete model/settings without dispatching a gateway call."""
    from bestseller.services.llm import _apply_model_override, _resolve_project_model_override
    from bestseller.settings import apply_runtime_llm_profile
    from bestseller.services.model_catalog import get_model_catalog_entry

    effective = apply_runtime_llm_profile(settings).llm.critic
    project_override = await _resolve_project_model_override(session, project_id)
    if project_override is not None:
        effective = _apply_model_override(effective, project_override)
    catalog_key = resolve_commercial_judge_model_key(settings)
    if catalog_key:
        entry = get_model_catalog_entry(catalog_key)
        if entry is None or not entry.available:
            raise ValueError("commercial_cohort_model_catalog_unavailable")
        effective = _apply_model_override(effective, entry)
    if effective.model_override:
        effective = effective.model_copy(update={"model": effective.model_override})
    return effective.model_dump(mode="json")


def _judge_samples_count() -> int:
    """榜单判官多采样次数(默认3取中位以消除单次方差);env JUDGE_SAMPLES 可调。"""
    try:
        return max(1, int(os.getenv("JUDGE_SAMPLES", "3") or 3))
    except ValueError:
        return 3


async def _serial_acceptance_samples(session: AsyncSession) -> bool:
    """Match the SDK's acceptance send policy without flushing or locking."""
    from types import SimpleNamespace

    from sqlalchemy import select

    from bestseller.infra.db.models import ExecutionBudgetModel
    from bestseller.services.budget import current_execution_scope
    from bestseller.services.independent_acceptance import policy

    scope = current_execution_scope()
    if scope is None:
        return False
    # Column values bypass stale/dirty ORM identities; never refresh caller data.
    with session.no_autoflush:
        row = (await session.execute(select(
            ExecutionBudgetModel.scope_id,
            ExecutionBudgetModel.max_calls,
            ExecutionBudgetModel.max_tokens,
            ExecutionBudgetModel.metadata_json,
        ).where(ExecutionBudgetModel.scope_id == scope))).mappings().first()
    return row is not None and policy(SimpleNamespace(**row)) is not None


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

    async def retain_aggregate(result):
        if kwargs.get("chapter_id") is None and kwargs.get("draft_id") is None:
            return result
        from bestseller.services.commercial_judge_recovery import (
            bind_commercial_component_aggregate,
        )
        from bestseller.services.commercial_source_evidence import commercial_judge_unavailable

        try:
            return await bind_commercial_component_aggregate(
                session,
                project_id=kwargs.get("project_id"),
                chapter_id=kwargs.get("chapter_id"),
                content_md=content_md,
                result=result,
                settings=settings,
                source_alias_inputs={
                    "chapter_number": chapter_number,
                    "generation_input": kwargs.get("generation_input"),
                    "previous_chapters": kwargs.get("previous_chapters", ()),
                    "reference_corpus_key": corpus_key,
                    "genre_context": genre_context,
                    "language": kwargs.get("language", "zh"),
                },
            )
        except (ValueError, TypeError, KeyError) as exc:
            unavailable = commercial_judge_unavailable(
                diagnostic=f"commercial cohort aggregation unavailable: {exc}"
            )
            # An unbound aggregate is still unavailable, but its individual
            # diagnostics remain evidence. Do not erase them with a new shell.
            return unavailable.model_copy(update={"raw_excerpt": result.raw_excerpt})

    async def _one_sample(sess: AsyncSession, slot: int) -> LLMQualityJudgeResult:
        return await judge_chapter_commercial_quality(
            sess,
            settings,
            chapter_number=chapter_number,
            content_md=content_md,
            reference_corpus_key=corpus_key,
            genre_context=genre_context,
            # A fixed policy version separates the corrected independent cohort
            # from legacy slot 0, whose paid operation may be quarantined.
            **{
                **kwargs,
                "sample_slot": f"chapter-quality.independent-v2:{slot}",
                "commercial_cohort_samples": n,
            },
        )

    if n == 1:
        return await retain_aggregate(await _one_sample(session, 0))

    # Keep concurrent owned sessions except where the SDK already serializes
    # acceptance sends. There, serialize preparation/transactions as well, using
    # scheduling only: re-entering the SDK's serialize lock would deadlock.
    serial_samples = await _serial_acceptance_samples(session)
    results: list[LLMQualityJudgeResult]
    from bestseller.infra.db.session import get_server_session

    stop = None
    async with contextlib.AsyncExitStack() as stack:
        try:
            sessions = [await stack.enter_async_context(get_server_session()) for _ in range(n)]
        except ExecutionBudgetError:
            raise
        except RuntimeError:
            # No draw has started: an unavailable pool may use the caller's
            # session sequentially. Paid failures never enter this fallback.
            sessions = None
        if sessions is not None:
            async def owned_sample(sess: AsyncSession, slot: int) -> LLMQualityJudgeResult:
                try:
                    result = await _one_sample(sess, slot)
                    # A caught proof error returns unavailable normally. Do not
                    # hold that sample's read/row locks until its siblings drain.
                    # Commit normal returns: unbound calls may have a flushed
                    # LlmRun that relies on the owned session's normal commit.
                    await sess.commit()
                    return result
                except BaseException:
                    # This session belongs only to this sample. The borrowed
                    # caller used by n=1 or fallback never enters this path.
                    await sess.rollback()
                    raise

            cancelled = None
            outcomes = []
            batches = (
                [[(slot, sess)] for slot, sess in enumerate(sessions)]
                if serial_samples else [list(enumerate(sessions))]
            )
            for batch in batches:
                pending = asyncio.gather(
                    *[owned_sample(sess, slot) for slot, sess in batch],
                    return_exceptions=True,
                )
                while not pending.done():
                    try:
                        await asyncio.shield(pending)
                    except asyncio.CancelledError as exc:
                        # Drain started draws, including their owned commit or
                        # rollback, but admit no further serial sample.
                        cancelled = exc
                outcomes.extend(pending.result())
                if serial_samples and (
                    cancelled or any(isinstance(o, BaseException) for o in outcomes)
                ):
                    break
            errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
            stop = next(
                (error for error in errors if isinstance(error, ExecutionBudgetError)), None
            )
            if stop is None:
                stop = cancelled or next(
                    (error for error in errors if isinstance(error, asyncio.CancelledError)), None
                )
            if stop is None and errors:
                stop = errors[0]
            results = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
    # Close/commit the drained sample sessions normally before propagating an
    # execution stop; one sample must not roll back its successful siblings.
    if stop is not None:
        raise stop
    if sessions is None:
        results = [await _one_sample(session, slot) for slot in range(n)]

    # Unavailable draws are not prose scores. Require a majority of the
    # requested cohort before taking a median; keep every original report.
    answered = [r for r in results if not r.infrastructure_failure]
    required_valid = n // 2 + 1
    receipt: dict[str, Any] = {
        "schema_version": "chapter-quality-aggregation.v1",
        "chapter_number": chapter_number,
        "requested_samples": n,
        "required_valid_samples": required_valid,
        "valid_samples": len(answered),
        "reference_corpus_key": corpus_key,
        "samples": [
            {
                "sample_slot": f"chapter-quality.independent-v2:{slot}",
                "valid": not result.infrastructure_failure,
                "report": result.model_dump(mode="json"),
            }
            for slot, result in enumerate(results)
        ],
    }
    if len(answered) < required_valid:
        logger.warning(
            "chapter %s: only %d/%d valid judge samples; majority %d required",
            chapter_number,
            len(answered),
            n,
            required_valid,
        )
        receipt["status"] = "insufficient_valid_samples"
        return await retain_aggregate(
            LLMQualityJudgeResult.model_validate(
                {
                    "scope": "chapter",
                    "passed": False,
                    "blocking_issues": [
                        {
                            "code": "CHAPTER_JUDGE_UNAVAILABLE",
                            "severity": "critical",
                            "evidence": f"Only {len(answered)}/{n} valid samples; {required_valid} required.",
                            "required_fix": "Judge-only re-evaluation; no prose verdict is available.",
                        }
                    ],
                    "rewrite_plan": {
                        "scope": "judge",
                        "instructions": "Insufficient valid judge samples; do not rewrite the chapter.",
                    },
                    "raw_excerpt": json.dumps(receipt, ensure_ascii=False),
                }
            )
        )
    if len(answered) < len(results):
        logger.info(
            "chapter %s: %d/%d judge samples failed to answer; aggregating the rest",
            chapter_number,
            len(results) - len(answered),
            len(results),
        )

    med_overall = statistics.median(r.overall_score for r in answered)
    dim_keys: set[str] = set()
    for r in answered:
        dim_keys.update(r.dimension_scores.keys())
    med_dims: dict[str, float] = {}
    for k in dim_keys:
        vals = [float(r.dimension_scores[k]) for r in answered if k in r.dimension_scores]
        if vals:
            med_dims[k] = statistics.median(vals)

    corpus = _load_reference_corpus(corpus_key)
    writer_model = resolve_llm_role_model(settings, role="writer")
    min_overall, min_dims = chapter_commercial_thresholds(chapter_number, corpus, writer_model)
    # _eps: median aggregation can yield 0.8999999999999999 for a logical 0.90;
    # only fail a floor when meaningfully below it (matches domain meets_threshold).
    _eps = 1e-6
    passed = med_overall >= min_overall - _eps and all(
        med_dims.get(k, 0.0) >= m - _eps for k, m in min_dims.items()
    )
    # Scores may aggregate; a source-grounded critical finding may not be
    # voted away by other scores or lost when selecting the representative.
    rep = min(answered, key=lambda r: abs(r.overall_score - med_overall))
    blocking = list(rep.blocking_issues)
    critical_slots = []
    for slot, result in enumerate(results):
        if result.infrastructure_failure:
            continue
        if result.has_critical:
            critical_slots.append(f"chapter-quality.independent-v2:{slot}")
        for issue in result.blocking_issues:
            if issue.severity == "critical" and issue not in blocking:
                blocking.append(issue)
    passed = passed and not critical_slots
    representative_slot = next(i for i, result in enumerate(results) if result is rep)
    receipt.update(
        {
            "status": "aggregated",
            "representative_sample_slot": f"chapter-quality.independent-v2:{representative_slot}",
            "critical_sample_slots": critical_slots,
            "min_overall": min_overall,
            "min_dimensions": min_dims,
            "median_overall": float(med_overall),
            "median_dimensions": med_dims,
            "passed": passed,
        }
    )
    return await retain_aggregate(
        rep.model_copy(
            update={
                "passed": passed,
                "overall_score": float(med_overall),
                "dimension_scores": med_dims,
                "blocking_issues": tuple(blocking),
                "raw_excerpt": json.dumps(receipt, ensure_ascii=False),
            }
        )
    )


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
    model_ceiling = model_output_token_ceiling(resolve_llm_role_model(settings, role="critic"))
    if model_ceiling and model_ceiling > 0:
        return model_ceiling
    return 8192
