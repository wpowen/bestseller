from __future__ import annotations

from collections.abc import Mapping

# ruff: noqa: ANN401, RUF001, RUF003
import hashlib
import json
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.llm_quality_judge import (
    LLMQualityJudgeResult,
    quality_judge_result_from_mapping,
)
from bestseller.services.chapter_llm_quality_judge import (
    resolve_commercial_judge_model_key,
)
from bestseller.services.judge_genre_context import (
    JudgeGenreContext,
    resolve_judge_genre_context,
)
from bestseller.services.judge_rubrics import get_judge_rubric
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.methodology_bridge import get_fragment
from bestseller.services.prompt_packs import PromptPack
from bestseller.services.protagonist_decision_agent import (
    render_outline_decision_agent_prompt,
)
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

# ── R17: judge verdict cache ─────────────────────────────────────────────────
# An LLM judge is non-deterministic: the same planning input judged twice can
# flip between pass and fail, so a book can be blocked by a verdict that a
# rerun would have passed. The fix: cache the parsed verdict keyed by
# (project, judge type, input-content hash) in the project's metadata. A
# repeat evaluation of byte-identical input reuses the recorded verdict
# instead of re-rolling the dice.
_JUDGE_CACHE_METADATA_KEY = "llm_judge_verdict_cache"
COMMERCIAL_PLANNING_JUDGE_TYPE = "commercial_planning_readiness"


def compute_judge_input_hash(payload: Any) -> str:
    """Stable content hash for judge inputs (canonical JSON, sha256)."""

    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _verdict_is_cacheable(parsed: Mapping[str, Any] | None) -> bool:
    """Only real verdicts are cached — never the unavailable-fallback shape."""

    if not isinstance(parsed, Mapping) or not parsed:
        return False
    for field in ("blocking_issues", "audit_issues"):
        for issue in parsed.get(field) or ():
            code = str(issue.get("code") or "") if isinstance(issue, Mapping) else ""
            if "UNAVAILABLE" in code.upper():
                return False
    return True


async def _load_project_for_judge_cache(session: AsyncSession, slug: str) -> Any:
    try:
        from sqlalchemy import select

        from bestseller.infra.db.models import ProjectModel

        return await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == slug)
        )
    except Exception:
        logger.debug("judge cache: project lookup failed for %s", slug, exc_info=True)
        return None


async def load_cached_judge_verdict(
    session: AsyncSession,
    *,
    project_slug: str,
    judge_type: str,
    input_hash: str,
) -> dict[str, Any] | None:
    """Return the cached parsed verdict for this exact input, or ``None``."""

    project = await _load_project_for_judge_cache(session, project_slug)
    if project is None:
        return None
    metadata = getattr(project, "metadata_json", None)
    cache = metadata.get(_JUDGE_CACHE_METADATA_KEY) if isinstance(metadata, dict) else None
    entry = cache.get(judge_type) if isinstance(cache, dict) else None
    if not isinstance(entry, dict) or entry.get("input_hash") != input_hash:
        return None
    verdict = entry.get("verdict")
    return dict(verdict) if isinstance(verdict, dict) else None


async def store_judge_verdict(
    session: AsyncSession,
    *,
    project_slug: str,
    judge_type: str,
    input_hash: str,
    verdict: Mapping[str, Any],
) -> None:
    """Record the parsed verdict for this input on the project metadata.

    Keeps one entry per judge type (latest input wins) so the cache stays
    bounded. Never raises — caching is an optimization, not a contract.
    """

    try:
        project = await _load_project_for_judge_cache(session, project_slug)
        if project is None:
            return
        metadata = getattr(project, "metadata_json", None)
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        cache = metadata.get(_JUDGE_CACHE_METADATA_KEY)
        cache = dict(cache) if isinstance(cache, dict) else {}
        cache[judge_type] = {
            "input_hash": input_hash,
            "verdict": dict(verdict),
        }
        metadata[_JUDGE_CACHE_METADATA_KEY] = cache
        project.metadata_json = metadata
    except Exception:
        logger.debug(
            "judge cache: failed to store verdict for %s/%s",
            project_slug,
            judge_type,
            exc_info=True,
        )


OUTLINE_JUDGE_DIMENSIONS: tuple[str, ...] = (
    "opening_pull",
    "front_ten_retention",
    "readability",
    "commercial_pull",
    "character_agency",
    "decision_intelligence",
    "scene_execution",
    "continuity",
    "logic_consistency",
    "knowledge_boundary",
    "real_world_plausibility",
    "scene_delta_quality",
    "methodology_compliance",
    "hook_strength",
)

# 维度未达阈值时驱动【针对性】大纲重修的阈值表。此前 build_outline_repair_directives
# 只看 blocking_issues(故事逻辑类),methodology_compliance 这类维度即便判官打回
# (min_dimension fail)也不会产出任何整改指令 → 重修"瞎修"。下表让维度失败也能生成
# 具体整改方向。全部喂给【已有界、保优、fail-open】的大纲重修闭环,不新增任何硬阻断。
OUTLINE_REPAIR_DIMENSION_THRESHOLDS: dict[str, float] = {
    "commercial_pull": 0.80,
    "opening_pull": 0.80,
    "front_ten_retention": 0.80,
    "scene_execution": 0.80,
    "logic_consistency": 0.82,
    "decision_intelligence": 0.84,
    "methodology_compliance": 0.80,
}

# 每个维度失败时给写手的【可执行】整改方向（方法论维最关键：把抽象方法论落到契约字段）。
_DIMENSION_REPAIR_HINTS: dict[str, str] = {
    "methodology_compliance": (
        "按本作写作方法论逐章兑现：开篇三章功能(钩子/人物/世界)、动作场 Goal-Conflict-"
        "Disaster 结构、契诃夫伏笔回收、弹簧式情绪压抑-释放节奏。每章/每场景的 "
        "methodology_contract 字段(stakes/hook_type/reveal_mode/cut_point/emotion_phase)"
        "必须写成具体内容而非占位。"
    ),
    "opening_pull": "前三章开篇换成具体可视的冲突动作+明确代价，删抽象旁白与设定倾倒。",
    "commercial_pull": "强化卖点与爽点兑现：说清目标读者图什么，每章给到具体情绪回报与钩子。",
    "scene_execution": "场景卡补具体人/物/动作/信息释放/章末钩子，杜绝抽象目的占位。",
    "logic_consistency": "修正前后设定、能力来源、时间线的矛盾。",
    "decision_intelligence": (
        "逐章按主角有限认知重做选择：列出正常人基线、角色基线、显而易见的低成本安全方案，"
        "若仍选择高风险行动，必须改变信息/压力/选项成本并给出试探、退路或后手；禁止只补心理描写。"
    ),
    "front_ten_retention": "前十章每章末留一个未解钩子，避免节奏走平。",
}


def _render_judge_methodology_reference(pack: PromptPack | None) -> str:
    methodology_refs: list[str] = []
    for key in (
        "opening_rules",
        "character_design",
        "reversal_design",
        "climax_design",
        "spring_model",
        "stakes_design",
    ):
        text = get_fragment(pack, phase="judge", fragment_key=key)
        if text:
            methodology_refs.append(f"【{key}】\n{text}")
    if not methodology_refs:
        return ""
    return (
        "\n\n## 评估时必须参照的方法论标准\n"
        "以下是本作类型的写作方法论原文。你对 methodology_compliance / opening_pull / "
        "commercial_pull 等维度的评分必须基于大纲是否遵循这些规则。在 blocking_issues 的 "
        "evidence 字段中引用具体违反的条款。\n\n"
        + "\n\n".join(methodology_refs)
    )


def _render_outline_commercial_system_prompt(
    *,
    rubric: Any,
    methodology_reference: str,
    genre_context: JudgeGenreContext | None = None,
    language: str = "zh",
) -> str:
    """Assemble outline_commercial_judge system prompt in 7-段式.

    When ``genre_context`` is supplied, the 故事合理性 checks render for THIS book's
    genre (services/judge_genre_context.py) instead of the hardcoded commission/
    detective 6-项. Falls back to the legacy block when absent.
    """
    if genre_context is not None:
        story_logic_section = (
            genre_context.render_story_logic_block(language)
            + genre_context.render_own_terms_block(language)
            + "这些是商业网文留存的底层规律，按本书题材缺失会直接掉读者。\n"
        )
    else:
        story_logic_section = (
            "# CONTEXT · 故事合理性 — 黄金三章必查 6 项（任一缺失即 blocking）\n"
            "1. **主角召唤合理性**：读者凭什么信主角能解决（家学 / 师承 / 口碑 / 熟人 / 能力实证）\n"
            "2. **委托人选择动机**：为什么找主角而不是 110 / 物业 / 家人\n"
            "3. **主角入场动机**：钱 / 旧账 / 家族线索 / 职业惯性 之一\n"
            "4. **能力展示场景**：黄金三章必须有可见的能力实证，不能只在背景里说他厉害\n"
            "5. **现实流程合理性**：报警 / 物业 / 医院 / 快递的反应符合常识或被明确标记为异常\n"
            "6. **信息密度节奏**：第一章不能术语堆砌，靠现象 + 反应 + 怀疑铺垫\n"
            "这六项是商业网文留存的底层规律，缺失会直接掉读者。\n"
        )
    return (
        "# ROLE\n"
        "你是商业网文签约编辑，主审「大纲是否能撑起榜单级正文」。\n"
        "你看过 200+ 部签约长篇的大纲提案，最擅长在大纲阶段就嗅出「开篇会掉读者」的隐患。\n"
        "你的判断标准来自：起点 / 番茄 / 七猫的过往榜单大纲规律 + 阅读编辑培训手册 + 退稿经验。\n"
        "\n"
        "# CONTEXT\n"
        "你正在评审一份卷 / 章 / 场景级大纲。\n"
        "你的评分决定：这份大纲是 publish-to-write 还是 rework。\n"
        "不要改写正文——只裁判大纲的可执行性、商业吸引力、逻辑一致性、角色认知边界和方法论覆盖。\n"
        "\n"
        + story_logic_section
        + "\n"
        "# TASK\n"
        "对大纲打 14 个维度分（见 user 段维度列表），并产出 blocking_issues / audit_issues / rewrite_plan。\n"
        "\n"
        "# CONSTRAINTS · 硬性卡控（违反即 blocking，不可降为 audit）\n"
        "1. 黄金三章若只靠电话 / 短信 / 语音开局，且没有更强现场画面压力 → blocking。\n"
        "2. 认知边界：非专业 / 普通角色不得无来由地理解并主动讲出本题材的专业 / 超自然规则术语；"
        "除非正文已交代其来源（被传授 / 亲历 / 身份揭示）→ 否则 blocking。（用本书实际的设定术语判断，不要套用其它题材的词。）\n"
        "3. 现实流程合理性：本题材涉及的现实机构 / 流程（如报警、就医、平台、机构审批等）反应必须符合常识；"
        "若反常，正文必须明确标记为异常 → 否则 blocking。\n"
        "4. 关键道具 / 能力 / 信号逻辑：本书的核心金手指 / 法宝 / 系统 / 线索物等，每次表现都要有稳定含义、"
        "触发条件与限制，主角能据此合理推断；不能反复用单一感官捷径（如「发烫」「心头一跳」）替代推理或推进。"
        "（以本书设定中的道具为准，不要预设为某一特定题材的器物。）\n"
        "5. 场景卡必须含具体人 / 物 / 动作 / 代价 / 信息释放 / 章末钩子；只写抽象目的 → blocking。\n"
        "6. 主角决策必须经得起第一人称反事实：若同时劣于正常人基线与该角色基线，"
        "且大纲没有改变信息、压力或选项成本，只靠作者需要冲突发生 → blocking。\n"
        + render_outline_decision_agent_prompt(language=language)
        + "\n"
        "\n"
        "# CONSTRAINTS · 评分纪律\n"
        "- overall_score 与 dimension_scores 使用 0.0-1.0 小数。\n"
        "- 每个 issue 必须含 code / severity / evidence / required_fix 四字段。\n"
        "- evidence 必须引用大纲中具体字段或描述，不可用「整体」/「全章」占位。\n"
        "- 6 项故事合理性任一明显缺失 → blocking（不能降为 audit）。\n"
        "\n"
        "# THINKING（产出 JSON 前在脑内 5 步）\n"
        "1. 通读大纲结构，按「开篇 / 中段 / 卷末」标记节奏曲线。\n"
        "2. 对照 6 项故事合理性逐项判定。\n"
        "3. 对照 6 项硬性卡控逐项检查。\n"
        "4. 执行主角决策代理的正常人/角色双基线比较。\n"
        "5. Reconcile：若有 blocking → overall_score 不应 ≥ 0.75。\n"
        "\n"
        "# OUTPUT FORMAT（严格 JSON）\n"
        "{\n"
        '  "pass": bool,\n'
        '  "overall_score": <0.0-1.0>,\n'
        '  "dimension_scores": { ... 14 项 ... },\n'
        '  "blocking_issues": [{"code", "severity", "evidence", "required_fix"}],\n'
        '  "audit_issues": [...],\n'
        '  "rewrite_plan": {"scope", "preserve", "change", "instructions"}\n'
        "}\n"
        f"{methodology_reference}\n"
        "\n# RUBRIC（评分细则原文）\n"
        f"{rubric.render_prompt_block()}\n"
        "\n# RUBRIC · system 起源\n"
        f"{rubric.system_prompt}\n"
    )


def _render_planning_readiness_system_prompt(
    *,
    rubric: Any,
    methodology_reference: str,
    genre_context: JudgeGenreContext | None = None,
    language: str = "zh",
) -> str:
    """7-段式 system prompt for commercial_planning_readiness_judge.

    When ``genre_context`` is supplied, the story-logic checks render for THIS
    book's genre (services/judge_genre_context.py) instead of the hardcoded
    commission/detective 6-项. Falls back to the legacy block when absent.
    """
    if genre_context is not None:
        story_logic_section = (
            genre_context.render_story_logic_block(language)
            + genre_context.render_own_terms_block(language)
        )
    else:
        story_logic_section = (
            "# CONTEXT · 故事合理性核心审视（6 项，任一缺失即 blocking）\n"
            "A. **主角召唤路径**：规划是否交代读者凭什么信主角能解决？\n"
            "   - 必须看到：家学传承 / 师承 / 前案口碑 / 熟人引介 / 行业身份 之一。\n"
            "   - 仅靠「物业修不好 / 警察不管」不充分。\n"
            "B. **委托人选择动机**：规划是否让读者相信委托人会主动找主角，而非 110/120/物业？\n"
            "   - 委托人和主角的关系链必须可被读者重构。\n"
            "C. **主角入场动机**：规划是否给了主角足够强的入场理由？\n"
            "   - 钱 / 家族旧账 / 职业惯性 / 旧债 / 好奇心 + 关键线索 之一。\n"
            "D. **能力可见性**：黄金三章规划是否安排了主角能力的具体展示场景？\n"
            "   - 不能只在背景设定里说他厉害，必须有「在场可见」的展示动作。\n"
            "E. **现实世界连接**：规划是否考虑了报警 / 物业 / 医院等现实流程？\n"
            "   - 完全无视这些流程或处理过于轻描淡写都不合理。\n"
            "F. **信息密度节奏**：第一章是否避免把核心规则术语全部抛给读者？\n"
            "   - 高概念词应通过现象铺垫，不应在 ch1 就堆砌术语。\n"
        )
    return (
        "# ROLE\n"
        "你是商业网文签约编辑，专审「黄金三章规划是否能撑起榜单级正文」。\n"
        "你做过 5 年商业网文规划诊断，能在大纲阶段就看出哪些故事「开篇就要掉读者」。\n"
        "你的判断标准来自：起点 / 番茄 / 七猫的过往签约规律 + 编辑培训手册 + 退稿经验。\n"
        "\n"
        "# CONTEXT\n"
        "你正在评判黄金三章（第 1-3 章）的章节规划是否足以支撑榜单级正文生成。\n"
        "你会收到两类信息：\n"
        "1. 章节规划原始数据（chapter planning data）\n"
        "2. 确定性关键词门禁的诊断发现（**仅供参考，可能误判，不作硬性结论**）\n"
        "\n"
        "你的职责是综合两类信息，基于你对商业网文规律的理解，给出最终结论。\n"
        "- 如果确定性门禁报告了问题但规划数据实质上合格 → 通过（pass=true）。\n"
        "- 如果规划数据存在实质性商业化缺陷（冲突空洞 / 无代价 / 无钩子 / 场景抽象）→ 不通过。\n"
        "\n"
        + story_logic_section
        + "\n"
        + render_outline_decision_agent_prompt(language=language)
        + "\n"
        "# TASK\n"
        "对黄金三章规划打 N 项维度分（见 user 段维度列表），并产出 blocking_issues / audit_issues / rewrite_plan。\n"
        "\n"
        "# CONSTRAINTS · 硬性卡控（以下任一成立才判 blocking）\n"
        "1. 三章主线冲突全部只有抽象目标（无具体对手 / 代价 / 压力），规划无法指导写作\n"
        "2. 章节规划完全缺失（scenes 为空或无任何字段）\n"
        "3. 三章中无一章有明确的章末钩子\n"
        "4. 主角关键行动明显劣于可见的低成本安全选项，且规划只靠作者便利解释该选择\n"
        "\n"
        "# CONSTRAINTS · 判 blocking 时务必避免误伤\n"
        "- 只要冲突落在本书自身题材的具体压力上即算**合格的具体冲突**，不应判 blocking。"
        "不同题材的合格冲突形态不同：升级流=实力差距 / 越级挑战 / 资源争夺；"
        "情感向=关系拉扯 / 身份错位 / 情感代价；悬疑向=心理博弈 / 怀疑试探 / 规则压力；"
        "都市现实=职位 / 合同 / 舆论 / 利益博弈；科幻向=技术失控 / 规则冲突等。"
        "**按本书题材判断,不要要求它必须写成悬疑/驱魔式的冲突。**\n"
        "- 即使确定性门禁报告 abstract_chapter_conflict，只要场景或钩子中存在具体的压力 / 代价 / 对手描述，"
        "应当通过。\n"
        "\n"
        "# THINKING（产出 JSON 前在脑内 5 步）\n"
        "1. 通读三章规划，画出每章的「冲突 → 代价 → 钩子」三角。\n"
        "2. 对照 6 项故事合理性逐项判定（A-F）。\n"
        "3. 对照 4 项硬性卡控逐项检查。\n"
        "4. 执行主角决策代理的正常人/角色双基线比较。\n"
        "5. Reconcile：若有 blocking → overall_score 不应 ≥ 0.75。\n"
        "\n"
        "# OUTPUT FORMAT（严格 JSON）\n"
        "{\n"
        '  "pass": bool,\n'
        '  "overall_score": <0.0-1.0>,\n'
        '  "dimension_scores": { ... },\n'
        '  "blocking_issues": [{"code", "severity", "evidence", "required_fix"}],\n'
        '  "audit_issues": [...],\n'
        '  "rewrite_plan": {"scope", "preserve", "change", "instructions"}\n'
        "}\n"
        f"{methodology_reference}\n"
        "\n# RUBRIC（评分细则原文）\n"
        f"{rubric.render_prompt_block()}\n"
        "\n# RUBRIC · system 起源\n"
        f"{rubric.system_prompt}\n"
    )


async def judge_outline_commercial_readiness(
    session: AsyncSession,
    settings: AppSettings,
    *,
    outline_payload: Mapping[str, Any],
    project_brief: Mapping[str, Any] | None = None,
    threshold: float = 0.82,
    workflow_run_id: Any | None = None,
    pack: PromptPack | None = None,
    genre_context: JudgeGenreContext | None = None,
) -> LLMQualityJudgeResult:
    brief = project_brief or {}
    if genre_context is None:
        genre_context = resolve_judge_genre_context(
            genre=brief.get("genre"),
            sub_genre=brief.get("sub_genre"),
            story_bible=brief.get("story_bible"),
        )
    _judge_language = (
        "en" if str(brief.get("language") or "").lower().startswith("en") else "zh"
    )
    payload_text = json.dumps(
        _compact_outline_payload(outline_payload),
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    payload_for_prompt = (
        payload_text
        if len(payload_text) <= 90000
        else payload_text[:90000] + "\n...TRUNCATED_AFTER_90000_CHARS..."
    )
    brief_text = json.dumps(project_brief or {}, ensure_ascii=False, indent=2, default=str)
    fallback = json.dumps(
        {
            "pass": False,
            "overall_score": 0.0,
            "dimension_scores": {},
            "blocking_issues": [
                {
                    "code": "OUTLINE_JUDGE_UNAVAILABLE",
                    "severity": "critical",
                    "evidence": "LLM outline judge returned fallback content.",
                    "required_fix": "重新运行评测，或人工补齐大纲商业化评估。",
                }
            ],
            "rewrite_plan": {
                "scope": "outline",
                "preserve": [],
                "change": ["outline commercial readiness"],
                "instructions": "补齐开篇压力、人物选择、场景画面、爽点兑现和尾钩后重评。",
            },
        },
        ensure_ascii=False,
    )
    methodology_reference = _render_judge_methodology_reference(pack)
    rubric = get_judge_rubric("outline_commercial")
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=_render_outline_commercial_system_prompt(
                rubric=rubric,
                methodology_reference=methodology_reference,
                genre_context=genre_context,
                language=_judge_language,
            ),
            user_prompt=(
                "## 任务参数\n"
                f"- 阈值（overall_score）：{threshold:.2f}\n"
                f"- 评测维度：{'、'.join(OUTLINE_JUDGE_DIMENSIONS)}\n"
                "- 通过标准：overall_score ≥ 阈值，且无 critical blocking。\n"
                "\n## 项目摘要\n"
                f"```\n{brief_text[:6000]}\n```\n"
                "\n## 大纲数据（已压缩为评测关键字段）\n"
                f"```json\n{payload_for_prompt}\n```\n"
                "\n## 立即开始\n"
                "按 system 中的 THINKING 步骤思考后，输出严格 JSON。"
                "每个 issue 必须包含 code / severity / evidence / required_fix；"
                "overall_score 与 dimension_scores 使用 0.0-1.0 小数。"
            ),
            fallback_response=fallback,
            prompt_template="outline_commercial_judge",
            prompt_version="v1",
            model_catalog_key=resolve_commercial_judge_model_key(settings),
            workflow_run_id=workflow_run_id,
            metadata={"judge_scope": "outline", "threshold": threshold, "rubric": rubric.name},
            max_tokens_override=4096,
        ),
    )
    parsed = _parse_json_object(completion.content)
    return quality_judge_result_from_mapping(
        parsed,
        scope="outline",
        min_overall=threshold,
        min_dimensions={
            "commercial_pull": threshold - 0.02,
            "opening_pull": threshold - 0.02,
            "logic_consistency": 0.82,
            "decision_intelligence": 0.84,
            "knowledge_boundary": 0.82,
            "real_world_plausibility": 0.80,
            "methodology_compliance": 0.80,
            "scene_execution": 0.80,
        },
        llm_run_id=str(completion.llm_run_id) if completion.llm_run_id else None,
        raw_excerpt=completion.content[:6000],
    )


def _compact_outline_payload(outline_payload: Mapping[str, Any]) -> dict[str, Any]:
    chapters = outline_payload.get("chapters")
    if not isinstance(chapters, list):
        return dict(outline_payload)
    compact: dict[str, Any] = {
        "batch_name": outline_payload.get("batch_name"),
        "chapters": [],
    }
    for chapter in chapters:
        if not isinstance(chapter, Mapping):
            continue
        compact_chapter = _pick_fields(
            chapter,
            (
                "chapter_number",
                "title",
                "chapter_goal",
                "goal",
                "opening_pressure",
                "protagonist_flaw",
                "required_payoff",
                "tail_hook",
                "opening_situation",
                "main_conflict",
                "hook_type",
                "hook_description",
                "chapter_event_role",
                "information_gap_mode",
                "information_revealed",
                "information_withheld",
                "world_rule_refs",
                "world_rule_landing",
                "world_state_deltas",
                "causal_contract",
                "event_cycle_contract",
                "object_signal_contract",
            ),
        )
        methodology_contract = chapter.get("methodology_contract")
        if isinstance(methodology_contract, Mapping):
            compact_chapter["methodology_contract"] = _pick_fields(
                methodology_contract,
                (
                    "conflict_stakes",
                    "visible_action_or_reaction",
                    "pacing_mode",
                    "emotion_phase",
                    "loop_position",
                    "hooks_to_resolve",
                    "hooks_to_plant",
                    "relationship_debts",
                    "decision_protocol",
                    "relationship_debt_protocol",
                    "agency_contract",
                ),
            )
        compact_chapter["scenes"] = [
            _compact_scene(scene)
            for scene in chapter.get("scenes", [])
            if isinstance(scene, Mapping)
        ]
        compact["chapters"].append(compact_chapter)
    return compact


def _compact_scene(scene: Mapping[str, Any]) -> dict[str, Any]:
    compact = _pick_fields(
        scene,
        (
            "scene_number",
            "title",
            "scene_type",
            "time_label",
            "participants",
            "purpose",
            "entry_state",
            "exit_state",
            "hook_requirement",
            "target_word_count",
            "information_control_mode",
            "signature_image",
            "cut_point",
        ),
    )
    methodology_contract = scene.get("methodology_contract")
    if isinstance(methodology_contract, Mapping):
        compact["methodology_contract"] = _pick_fields(
            methodology_contract,
            (
                "conflict_stakes",
                "information_control_mode",
                "signature_image",
                "cut_point",
                "hook_type",
                "focus_character",
                "relationship_debts",
            ),
        )
    return compact


def _pick_fields(source: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {
        key: _compact_value(source[key])
        for key in keys
        if key in source and source[key] not in (None, "", [])
    }


def _compact_value(value: Any, *, max_string: int = 180) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if len(stripped) <= max_string:
            return stripped
        return stripped[:max_string].rstrip() + "..."
    if isinstance(value, Mapping):
        return {
            str(key): _compact_value(raw, max_string=max_string)
            for key, raw in value.items()
            if raw not in (None, "", [])
        }
    if isinstance(value, list):
        return [_compact_value(item, max_string=max_string) for item in value[:8]]
    return value


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


# ─────────────────────────────────────────────────────────────────────────────
# Commercial planning readiness LLM judge
#
# Architecture:
#   1. Deterministic gate runs first → produces structured findings (fast, no
#      LLM cost).  Those findings are passed here as *context/reference*.
#   2. This LLM judge reads the actual chapter planning content and the
#      deterministic findings together, then gives a holistic verdict.
#   3. Only this judge's result is used as the final pass/fail signal —
#      the deterministic gate is advisory / diagnostic, not a hard block.
# ─────────────────────────────────────────────────────────────────────────────

COMMERCIAL_PLANNING_JUDGE_DIMENSIONS: tuple[str, ...] = (
    "opening_pull",          # 开篇有没有让读者继续的动力
    "concrete_conflict",     # 冲突是否具体、有代价、有对手
    "protagonist_agency",    # 主角是否需要做出选择/行动
    "decision_intelligence", # 选择是否符合有限认知、人格与趋利避害
    "hook_quality",          # 章末钩子是否有悬念
    "scene_executability",   # 场景是否可执行（不只是抽象目标）
    "commercial_retention",  # 整体是否符合榜单留存标准
)


async def judge_commercial_planning_readiness(
    session: AsyncSession,
    settings: AppSettings,
    *,
    chapters_payload: list[Mapping[str, Any]],
    deterministic_findings: Mapping[str, Any] | None = None,
    project_brief: Mapping[str, Any] | None = None,
    threshold: float = 0.75,
    workflow_run_id: Any | None = None,
    pack: PromptPack | None = None,
    genre_context: JudgeGenreContext | None = None,
) -> LLMQualityJudgeResult:
    """LLM judge for the commercial planning readiness gate.

    The deterministic findings from ``evaluate_commercial_planning_readiness``
    are included as reference context so the LLM can weigh them alongside the
    actual chapter planning content.  The LLM's verdict is the authoritative
    final result — the deterministic gate is diagnostic only.
    """
    # Compact only the golden-three chapters (ch1-3)
    golden = [
        ch for ch in chapters_payload
        if int(ch.get("chapter_number") or ch.get("number") or 0) in (1, 2, 3)
    ]
    chapters_text = json.dumps(
        [_compact_outline_payload({"chapters": [ch]})["chapters"][0] for ch in golden],
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    if len(chapters_text) > 60000:
        chapters_text = chapters_text[:60000] + "\n...TRUNCATED..."

    # Format deterministic findings as a concise reference block
    det_text = ""
    if deterministic_findings:
        findings = deterministic_findings.get("findings") or []
        if findings:
            lines = []
            for f in findings:
                if not isinstance(f, dict):
                    continue
                sev = f.get("severity", "?")
                code = f.get("code", "?")
                msg = f.get("message", "")
                scope = f.get("scope", "")
                lines.append(f"  [{sev}] {code} ({scope}): {msg}")
            det_text = "\n".join(lines)

    brief_text = json.dumps(project_brief or {}, ensure_ascii=False, indent=2, default=str)

    def _finalize(
        parsed_verdict: Mapping[str, Any],
        *,
        llm_run_id: str | None,
        raw_excerpt: str,
    ) -> LLMQualityJudgeResult:
        return quality_judge_result_from_mapping(
            parsed_verdict,
            scope="commercial_planning",
            min_overall=threshold,
            min_dimensions={
                "concrete_conflict": threshold - 0.05,
                "hook_quality": threshold - 0.10,
                "scene_executability": threshold - 0.05,
                "decision_intelligence": max(0.80, threshold),
            },
            llm_run_id=llm_run_id,
            raw_excerpt=raw_excerpt,
        )

    # R17: same project + same input → reuse the recorded verdict instead of
    # re-rolling a non-deterministic judge.
    project_slug = str((project_brief or {}).get("slug") or "").strip()
    input_hash = compute_judge_input_hash(
        {
            "judge_type": COMMERCIAL_PLANNING_JUDGE_TYPE,
            "chapters": chapters_text,
            "deterministic_findings": det_text,
            "project_brief": brief_text,
            "threshold": round(float(threshold), 4),
        }
    )
    if project_slug:
        cached_verdict = await load_cached_judge_verdict(
            session,
            project_slug=project_slug,
            judge_type=COMMERCIAL_PLANNING_JUDGE_TYPE,
            input_hash=input_hash,
        )
        if cached_verdict is not None:
            logger.info(
                "commercial planning judge cache hit: project=%s hash=%s — "
                "reusing recorded verdict",
                project_slug,
                input_hash[:12],
            )
            return _finalize(
                cached_verdict,
                llm_run_id=None,
                raw_excerpt="(cached verdict — judge not re-invoked)",
            )

    fallback = json.dumps(
        {
            "pass": True,  # safe fallback — let planning proceed; prose gate will catch issues
            "overall_score": 0.76,
            "dimension_scores": {},
            "blocking_issues": [],
            "audit_issues": [
                {
                    "code": "COMMERCIAL_PLANNING_JUDGE_UNAVAILABLE",
                    "severity": "high",
                    "evidence": "LLM commercial planning judge returned fallback content.",
                    "required_fix": "重新运行评测，或人工复核黄金三章规划质量。",
                }
            ],
            "rewrite_plan": {
                "scope": "commercial_planning",
                "preserve": [],
                "change": [],
                "instructions": "",
            },
        },
        ensure_ascii=False,
    )

    det_section = (
        f"\n\n## 确定性门禁参考发现（仅供参考，不作为硬性结论）\n{det_text}"
        if det_text
        else "\n\n## 确定性门禁参考发现\n（无发现）"
    )
    methodology_reference = _render_judge_methodology_reference(pack)
    rubric = get_judge_rubric("commercial_planning")

    _brief = project_brief or {}
    if genre_context is None:
        genre_context = resolve_judge_genre_context(
            genre=_brief.get("genre"),
            sub_genre=_brief.get("sub_genre"),
            story_bible=_brief.get("story_bible"),
        )
    _judge_language = (
        "en" if str(_brief.get("language") or "").lower().startswith("en") else "zh"
    )

    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=_render_planning_readiness_system_prompt(
                rubric=rubric,
                methodology_reference=methodology_reference,
                genre_context=genre_context,
                language=_judge_language,
            ),
            user_prompt=(
                "## 任务参数\n"
                f"- 阈值（overall_score）：{threshold:.2f}\n"
                f"- 评测维度：{'、'.join(COMMERCIAL_PLANNING_JUDGE_DIMENSIONS)}\n"
                "- 通过标准：overall_score ≥ 阈值，且无 critical blocking。\n"
                "\n## 项目摘要\n"
                f"```\n{brief_text[:3000]}\n```\n"
                "\n## 黄金三章规划数据\n"
                f"```json\n{chapters_text}\n```\n"
                f"{det_section}\n"
                "\n## 立即开始\n"
                "按 system 中的 6 项故事合理性 + 4 项硬性卡控 + THINKING 步骤思考，"
                "输出严格 JSON（schema 见 system OUTPUT FORMAT 段）。"
            ),
            fallback_response=fallback,
            prompt_template="commercial_planning_readiness_judge",
            prompt_version="v1",
            model_catalog_key=resolve_commercial_judge_model_key(settings),
            workflow_run_id=workflow_run_id,
            metadata={"judge_scope": "commercial_planning", "threshold": threshold, "rubric": rubric.name},
            max_tokens_override=3000,
        ),
    )
    parsed = _parse_json_object(completion.content)
    if project_slug and _verdict_is_cacheable(parsed):
        await store_judge_verdict(
            session,
            project_slug=project_slug,
            judge_type=COMMERCIAL_PLANNING_JUDGE_TYPE,
            input_hash=input_hash,
            verdict=parsed,
        )
    return _finalize(
        parsed,
        llm_run_id=str(completion.llm_run_id) if completion.llm_run_id else None,
        raw_excerpt=completion.content[:4000],
    )


def build_outline_repair_directives(
    result: LLMQualityJudgeResult, *, max_issues: int = 12
) -> list[str]:
    """Turn a failed outline commercial-judge result into concrete regeneration
    directives, so the next outline generation fixes exactly the flagged scenes
    instead of blindly retrying the same low-quality output."""
    directives: list[str] = []
    for issue in (result.blocking_issues or ())[:max_issues]:
        evidence = str(getattr(issue, "evidence", "") or "").strip()
        required_fix = str(getattr(issue, "required_fix", "") or "").strip()
        code = str(getattr(issue, "code", "") or "").strip()
        if not required_fix and not evidence:
            continue
        directives.append(
            f"【大纲商业评审整改·{code}】问题定位：{evidence[:220]}；"
            f"必须修正：{required_fix[:320] or '按评审维度补齐具体人/物/动作/代价/信息释放/章末钩子。'}"
        )
    # 维度级整改：methodology_compliance 等维度即便没进 blocking_issues，只要低于阈值
    # 就生成针对性整改方向，让重修真正修方法论而非盲目重试。
    scores = result.dimension_scores or {}
    for dim, threshold_value in OUTLINE_REPAIR_DIMENSION_THRESHOLDS.items():
        raw = scores.get(dim)
        try:
            score_value = float(raw)
        except (TypeError, ValueError):
            continue
        if score_value < threshold_value:
            hint = _DIMENSION_REPAIR_HINTS.get(dim, f"提升 {dim} 维度表现。")
            directives.append(
                f"【维度整改·{dim} {score_value:.2f}<{threshold_value:.2f}】{hint}"
            )
    plan = getattr(result, "rewrite_plan", None)
    instructions = ""
    if isinstance(plan, Mapping):
        instructions = str(plan.get("instructions") or "").strip()
    elif plan is not None:
        instructions = str(getattr(plan, "instructions", "") or "").strip()
    if instructions:
        directives.append(f"【大纲整改总纲】{instructions[:400]}")
    return directives


async def judge_commercial_planning_readiness_stable(
    session: AsyncSession,
    settings: AppSettings,
    *,
    chapters_payload: list[Mapping[str, Any]],
    samples: int = 3,
    judge_fn: Any = None,
    **kwargs: Any,
) -> LLMQualityJudgeResult:
    """Majority-vote wrapper for the commercial planning readiness judge.

    This judge's verdict is TERMINAL for a whole book creation — and a single
    temperature-sampled draw was exercising that authority alone (real run
    2026-07-16: one sample killed a book whose deterministic gate passed and
    whose golden-3 outline was demonstrably solid). Blocking now requires a
    strict majority of samples to independently block; sample errors abstain,
    so a flaky judge can neither kill nor wave through on its own. The
    representative result returned is a blocking sample (so the gate's error
    message carries real evidence/required_fix) when the majority blocks,
    else a passing sample.
    """

    import asyncio  # noqa: PLC0415

    if judge_fn is None:
        async def judge_fn(**call_kwargs: Any) -> LLMQualityJudgeResult:  # type: ignore[misc]
            return await judge_commercial_planning_readiness(
                session, settings, **call_kwargs
            )

    n = max(1, int(samples))
    call_kwargs = {"chapters_payload": chapters_payload, **kwargs}
    if n == 1:
        return await judge_fn(**call_kwargs)

    async def _one() -> LLMQualityJudgeResult | None:
        try:
            return await judge_fn(**call_kwargs)
        except Exception:
            logger.warning(
                "commercial planning readiness judge sample failed; abstaining",
                exc_info=True,
            )
            return None

    # Samples share the caller's session sequentially-unsafe? judge_fn defaults
    # to complete_text on one AsyncSession — run sequentially to stay
    # session-safe; the judge is called once per book so latency is bounded.
    results: list[LLMQualityJudgeResult | None] = []
    for _ in range(n):
        results.append(await _one())

    cast = [r for r in results if r is not None]
    if not cast:
        raise RuntimeError("all readiness judge samples failed")

    def _blocks(r: LLMQualityJudgeResult) -> bool:
        return (not r.passed) and bool(r.blocking_issues)

    blockers = [r for r in cast if _blocks(r)]
    if len(blockers) * 2 > len(cast):
        blockers.sort(key=lambda r: len(r.blocking_issues), reverse=True)
        logger.info(
            "readiness judge stable: %d/%d samples block — blocking",
            len(blockers), len(cast),
        )
        return blockers[0]
    passing = [r for r in cast if not _blocks(r)]
    if blockers:
        logger.info(
            "readiness judge stable: %d/%d samples block — no majority, passing "
            "(dissent codes: %s)",
            len(blockers), len(cast),
            [i.code for r in blockers for i in r.blocking_issues][:6],
        )
    return passing[0]
