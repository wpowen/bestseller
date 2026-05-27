from __future__ import annotations

from collections.abc import Mapping, Sequence

# ruff: noqa: ANN401,RUF001
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.llm_quality_judge import (
    LLMQualityJudgeResult,
    quality_judge_result_from_mapping,
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


# ---------------------------------------------------------------------------
# System prompt assembly (7-段式)
# ---------------------------------------------------------------------------


def _render_chapter_judge_system_prompt(
    *,
    rubric: Any,
    reference_block: str,
    checklist_block: str,
    calibration_block: str,
) -> str:
    """Assemble the chapter_commercial_quality_judge system prompt in 7-段式.

    Replaces the previous 8-source string concatenation (rubric.system_prompt +
    4 hardcoded blocks + reference + checklist + calibration + rubric.render).
    Stable ordering = Anthropic prompt-cache friendly.
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
        "\n"
        "# CONTEXT · 商业留存的底层规律（黄金三章尤其重要）\n"
        "## 六项故事合理性 — 任一明显缺失 → 必判 blocking\n"
        "1. **主角召唤合理性**：读者凭什么信主角能解决？必须看到：家学 / 师承 / 前案口碑 / "
        "熟人转介 / 能力实证 之一。如果只靠「物业找不到办法就找主角」且主角身份模糊 → blocking。\n"
        "2. **委托人选择合理性**：为什么找主角而不是 110 / 物业 / 家人 / 120？"
        "必须有可信理由：之前认识 / 口碑听说 / 他人推荐 / 事件明显超出常规处理。\n"
        "3. **主角动机合理性**：主角凭什么接 / 卷入？钱 / 家族线索 / 职业惯性 / 旧账 之一。"
        "不能莫名其妙冒雨出现在现场。\n"
        "4. **现实流程链条**：电话 / 物业 / 警察 / 医院 / 快递的反应必须符合常理；"
        "若反常，正文必须明确让角色意识到「不可能」。\n"
        "5. **能力建立**：主角的特殊能力（阴阳眼 / 青囊 / 罗盘 / 铜钱等）本章必须有 ≥ 1 次可验证展示——"
        "读者要在合上书前看到这个人确实能干这行。\n"
        "6. **信息节奏**：高概念可铺垫但不应在第一章就术语堆砌；"
        "前 3 章靠现象 + 反应 + 怀疑驱动，不靠规则讲解。\n"
        "\n"
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

    Returns None if the file does not exist or fails to parse; callers
    should degrade gracefully rather than raising.
    """
    path = _REFERENCE_CORPORA_DIR / f"{genre_key}.yaml"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
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

def chapter_commercial_thresholds(
    chapter_number: int,
    corpus: dict[str, Any] | None = None,
) -> tuple[float, dict[str, float]]:
    """Return (overall_min, per_dimension_min) for chapter quality gating.

    When a reference corpus is provided, use its calibrated floors (which are
    higher than the legacy defaults).  Falls back gracefully when no corpus is
    available so existing projects are unaffected.
    """
    cal = (corpus or {}).get("calibration") or {}

    if chapter_number <= 3:
        overall_floor = float(cal.get("golden_three_floor", 0.92))
        return overall_floor, {
            "opening_pull": 0.90,
            "commercial_pull": 0.90,
            "readability": 0.88,
            "knowledge_boundary": 0.90,
            "real_world_plausibility": 0.88,
            "object_signal_logic": 0.88,
            "call_plausibility": 0.90,
            "capability_demonstrated": 0.90,
        }
    if chapter_number <= 10:
        overall_floor = float(cal.get("chapter_4_to_10_floor", 0.85))
        return overall_floor, {
            "hook_strength": 0.82,
            "continuity": 0.84,
            "knowledge_boundary": 0.82,
            "real_world_plausibility": 0.80,
        }
    overall_floor = float(cal.get("general_floor", 0.80))
    return overall_floor, {}


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
    reference_corpus_key: str = "suspense-mystery",
) -> LLMQualityJudgeResult:
    # Load reference corpus (genre-aware; degrades to empty block if missing)
    corpus = _load_reference_corpus(reference_corpus_key)

    min_overall, min_dimensions = chapter_commercial_thresholds(chapter_number, corpus)

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
                "\n硬性判定口径：\n"
                "1. 非专业角色不得像风水师一样理解或主动解释认账、入账、替认、镜债、账线等规则；"
                "除非正文明确写出被附身/被操控/刚被主角教会。\n"
                "2. 快递、外卖、配送、报警、门禁、监控、医院、警方等现实流程必须符合常识；"
                "若是超自然伪造或不可能证据，正文必须明确让角色意识到'不可能'。\n"
                "3. 铜钱、罗盘、青囊等物件信号必须有稳定边界：每次异常分别代表什么、"
                "能做什么、不能做什么。如果整章 3 次以上用'发烫/发热/滚烫'等单一感官捷径"
                "推进剧情，没有冷感、重感、裂缺、血点、影子错位等多样化变化，"
                "必须标 OBJECT_SIGNAL_SHORTCUT（audit）。但物件信号偶尔发烫是合理的写法。\n"
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
