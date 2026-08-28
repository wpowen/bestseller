"""概念淘汰赛（2026-07-09）——高概念先行，反题材均值回归。

真机根因（《谁敢动我山头》，custom-xianxia-1783586500）：概念层是 finalize
一锤子买卖，LLM 被要求"写个古典仙侠"时输出必然是该题材语料的众数——废脉藏宝/
破宗门重建/债主逼门，读者一句话就能自动补全全书。表达层的淘汰赛
（blurb_copywriter）救不了它：话说得再漂亮，事本身不新鲜。

榜单四铁律（从 config/appeal_reference_blurbs.yaml 真实爆款提炼）：
  ①不可自动补全——一句话里必有"等等，这怎么成立？"的认知缺口；
  ②反共识而非反处境——反的是"苟是最强/工业是金手指"这类共识；
  ③杂交产新物种——两个成熟类型的交点上没人写过的位置；
  ④概念自带几百章引擎——进度条和问题梯内置在概念里。

本工序把已验证的"N 候选→确定性筛→判官淘汰赛"方法上移到概念层，跑在构思
多 agent 展开【之前】：

  1. 反俗套禁用先行——per-题材俗套清单（config）作为负面约束写进生成 prompt，
     同时作确定性出局筛（命中即废）。
  2. 杂交算子出 N 路候选——"题材 × 异质维度（随机抽取）"强制杂交 + 1 路
     纯题材对照组（防强行猎奇总是赢）。
  3. 轻量种子淘汰——候选阶段只交付 CoreStorySeed，避免 500 章说明反向污染一句话。
  4. 判官淘汰赛——四轴（新颖度/想点欲/不可预测性/人物决策可信度）分别设硬门，
     任何单轴失败都不能被平均分掩盖。
  5. 长篇决赛——仅对通过钩子门的前两名扩展 SerialityProof，再做容量与质量门禁。
  6. 冠军注入 ctx["description"]——它是全部下游 prompt（商业定位/市场/角色/
     世界观）的共同源头，零侵入全覆盖。

零依赖 conception.py（它反过来调用本模块）。本模块自身以无冠军显式返回失败；
新建长篇由 conception 的 ConceptContract 门禁 fail-closed，短篇与旧项目保留兼容路径。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import json
import logging
import math
from pathlib import Path
import random
import re
from typing import Any, Protocol

import yaml

from bestseller.services.anti_default_motif import (
    default_debt_family_hits,
    is_debt_dominated,
)
from bestseller.services.naming_normalizer import render_protagonist_name_ban
from bestseller.services.progress_context import emit_activity

logger = logging.getLogger(__name__)

# ruff: noqa: RUF001, RUF002 — Chinese prompts/fixtures are intentional.

_CONTROL_DIMENSION = "纯题材对照"
_CHARACTER_CONTROL_DIMENSION = "纯题材人物困局对照"
_SEED_REFINEMENT_DIMENSIONS = (
    "原句克制重写",
    "标志性反常事件",
    "主角决策因果",
    "碎片汇聚成单一答案",
    "成果永久改写下一轮规则",
    "经营盘面与势力扩张",
    "读者点击与大白话",
    "可再生行动循环",
    "对手生态升级",
    "阶段跃迁",
    "题材兑现",
    "长期谜团与终局",
)
_TARGETED_REPAIR_DIMENSIONS = (
    "定向修复最佳近失",
    "定向反转最佳近失",
)
_NATIVE_STORY_LANES = (
    "人际困局",
    "世界规则",
    "成长道路",
    "世界扩张",
    "势力选择",
    "身份变化",
    "职业处境",
    "资源分配",
    "纯题材直觉",
)
_NATIVE_STORY_LANE_BRIEFS = {
    "人际困局": "从一段无法轻易割舍的人际关系和两难选择起步，用人物当下的目标与行动推动。",
    "世界规则": "从一个能被场景直接证明的自然或超凡现象起步，让主角通过行动认识并利用它。",
    "成长道路": "从一门具体本事如何学、练、用、犯错和改变处境起步，让能力与角色共同变化。",
    "世界扩张": "从探索、迁徙、建设、经营或夺取生存空间起步，让扩张来自既有行动的后果。",
    "势力选择": "从多个聪明势力都需要主角、但目标互斥的选择起步，让每方都有可理解的利益。",
    "身份变化": "从身份秘密、角色冲突或被迫承担的新位置起步，让变化产生新的职责与选择。",
    "职业处境": "从题材内一项具体工作、客户、工具和现场事故起步，让专业行动持续产生故事。",
    "资源分配": "从一种稀缺资源被谁生产、分配和争夺起步，用具体物件、场景与行动表达。",
    "纯题材直觉": "忘掉框架术语，先写一个该题材读者会立刻想看的具体人、事和反转。",
}


# 有 brief 时用来区分候选的轴。这些是**故事自身的维度**，不是题材指令——
# 「主角的社会位置」要求这个候选和别的不同，「资源分配」则是在规定故事写什么。
# 九条框架路线属于后者：候选按固定轮转分配，用户选轻松＋喜剧＋爽感时，世界规则／
# 势力选择／资源分配照样占掉一半候选，产出必然沉重，判官再正确地判它们不喜剧。
# 加权仍是在那九个桶里选，框架还是在替用户决定故事写什么。
#
# 但差异性不能跟着一起丢：它此前完全靠那九个标签撑着，六个候选共享一份 brief 会
# 退化成同一个故事的多种措辞（2026-07-28《东方玄幻》六个候选全是「杂役掏沟挖出
# 戴木镯的腕骨」，6/6 挂新颖度）。所以换成只约束彼此不同、不干涉题材的轴。
_GROWTH_DIFFERENTIATION_AXES: tuple[str, ...] = (
    "主角的社会位置",
    "异常的来源",
    "压力来自谁",
    "主角与对手的关系结构",
    "故事发生的舞台",
    "主角最初想要的东西",
    "第一个不可逆选择的性质",
    "谁最先发现主角不对劲",
)

_GROWTH_LANE_PREFIX = "自然生长"


class GeneratorFn(Protocol):
    """(system_prompt, user_prompt) -> (raw_text, llm_run_id)."""

    def __call__(self, system_prompt: str, user_prompt: str) -> Awaitable[tuple[str, Any]]: ...


@dataclass(frozen=True)
class ConceptCandidate:
    """一路高概念候选 + 审计/判官证据。"""

    dimension: str
    concept: str = ""
    mechanism: str = ""
    hook_question: str = ""
    protagonist_identity: str = ""
    protagonist_private_desire: str = ""
    protagonist_flaw: str = ""
    core_abnormality: str = ""
    opening_crisis: str = ""
    opponent_system: str = ""
    decision_proof: str = ""
    emotional_promise: str = ""
    core_promise_invariant: str = ""
    # 2026-08-27：prompt 从 2026-08-19 起就在要 constraint_ladder（「无代价≠无
    # 限制」的落点），并写明「若所有场景都在同一层次打转，项目不成立」——
    # 但数据类**从来没有这个字段**，解析层于是整块丢弃（真机 12/12 候选零携带），
    # 全仓零消费方。规则写了、没实现，「限制」这一维度从概念层到成书从未被审过。
    constraint_ladder: tuple[str, ...] = ()
    role_ladder: tuple[str, ...] = ()
    world_ladder: tuple[str, ...] = ()
    repeatable_story_unit: str = ""
    unit_families: tuple[str, ...] = ()
    progress_bar: str = ""
    unit_frequency: str = ""
    unit_count_estimate: int = 0
    question_ladder: tuple[str, ...] = ()
    ch50: str = ""
    renewal_sources: tuple[str, ...] = ()
    accumulation_tracks: tuple[str, ...] = ()
    phase_transitions: tuple[str, ...] = ()
    opposing_ecology: tuple[str, ...] = ()
    endgame_direction: str = ""
    seriality_report: dict[str, Any] = field(default_factory=dict)
    seriality_judge: dict[str, Any] = field(default_factory=dict)
    judge_freshness: float | None = None
    judge_click: float | None = None
    judge_predictable: float | None = None
    judge_character_logic: float | None = None
    judge_mechanism_causality: float | None = None
    judge_genre_fidelity: float | None = None
    judge_plain_language: float | None = None
    judge_story_motion: float | None = None
    judge_protagonist_agency: float | None = None
    judge_reason: str = ""
    composite: float | None = None
    rejected_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "concept": self.concept,
            "mechanism": self.mechanism,
            "hook_question": self.hook_question,
            "protagonist_identity": self.protagonist_identity,
            "protagonist_private_desire": self.protagonist_private_desire,
            "protagonist_flaw": self.protagonist_flaw,
            "core_abnormality": self.core_abnormality,
            "opening_crisis": self.opening_crisis,
            "opponent_system": self.opponent_system,
            "decision_proof": self.decision_proof,
            "emotional_promise": self.emotional_promise,
            "core_promise_invariant": self.core_promise_invariant,
            "constraint_ladder": list(self.constraint_ladder),
            "role_ladder": list(self.role_ladder),
            "world_ladder": list(self.world_ladder),
            "repeatable_story_unit": self.repeatable_story_unit,
            "unit_families": list(self.unit_families),
            "progress_bar": self.progress_bar,
            "unit_frequency": self.unit_frequency,
            "unit_count_estimate": self.unit_count_estimate,
            "question_ladder": list(self.question_ladder),
            "ch50": self.ch50,
            "renewal_sources": list(self.renewal_sources),
            "accumulation_tracks": list(self.accumulation_tracks),
            "phase_transitions": list(self.phase_transitions),
            "opposing_ecology": list(self.opposing_ecology),
            "endgame_direction": self.endgame_direction,
            "seriality_report": dict(self.seriality_report),
            "seriality_judge": dict(self.seriality_judge),
            "judge_freshness": self.judge_freshness,
            "judge_click": self.judge_click,
            "judge_predictable": self.judge_predictable,
            "judge_character_logic": self.judge_character_logic,
            "judge_mechanism_causality": self.judge_mechanism_causality,
            "judge_genre_fidelity": self.judge_genre_fidelity,
            "judge_plain_language": self.judge_plain_language,
            "judge_story_motion": self.judge_story_motion,
            "judge_protagonist_agency": self.judge_protagonist_agency,
            "judge_reason": self.judge_reason[:200],
            "composite": self.composite,
            "rejected_reason": self.rejected_reason,
        }


@dataclass
class ConceptTournamentResult:
    winner: ConceptCandidate | None = None
    candidates: list[ConceptCandidate] = field(default_factory=list)
    banned_cliches: tuple[str, ...] = ()
    llm_run_ids: list[Any] = field(default_factory=list)
    generation_model_key: str | None = None
    judge_model_key: str | None = None
    finalist_judge_model_key: str | None = None
    raw_idea_judge_model_key: str | None = None
    premise_judge_model_key: str | None = None
    candidate_prompt_mode: str = "current"
    raw_idea_prompt_arm: str | None = None
    candidate_prompt_chars: int = 0
    candidate_generation_calls: int = 0
    engine_rejections: list[dict[str, Any]] = field(default_factory=list)
    raw_idea_ranking: list[dict[str, Any]] = field(default_factory=list)
    raw_idea_rank_coverage: dict[str, Any] = field(default_factory=dict)
    raw_ideas: list[dict[str, str]] = field(default_factory=list)
    premise_cards: list[dict[str, Any]] = field(default_factory=list)
    # 2026-08-25：追读性阶段的回执。恒非空——"跑了/只留痕/跳过" 三态可区分，
    # 此前 seriality_judge=={} 既可能是"评了没发现"也可能是"压根没跑"。
    seriality_stage: dict[str, Any] = field(default_factory=dict)
    # 2026-08-27：「无代价≠无限制」的验收回执。恒非空——没有回执就无法
    # 区分「查了没问题」与「压根没查」，这正是 constraint_ladder 被埋了
    # 八天的原因（prompt 从 2026-08-19 起就在要它）。
    constraint_ladder_audit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seriality_stage": dict(self.seriality_stage),
            "constraint_ladder_audit": dict(self.constraint_ladder_audit),
            "winner_dimension": self.winner.dimension if self.winner else None,
            "winner_concept": self.winner.concept if self.winner else None,
            "winner": self.winner.to_dict() if self.winner else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "banned_cliches": list(self.banned_cliches),
            "llm_run_ids": [str(run_id) for run_id in self.llm_run_ids],
            "generation_model_key": self.generation_model_key,
            "judge_model_key": self.judge_model_key,
            "finalist_judge_model_key": self.finalist_judge_model_key,
            "raw_idea_judge_model_key": self.raw_idea_judge_model_key,
            "premise_judge_model_key": self.premise_judge_model_key,
            "candidate_prompt_mode": self.candidate_prompt_mode,
            "raw_idea_prompt_arm": self.raw_idea_prompt_arm,
            "candidate_prompt_chars": self.candidate_prompt_chars,
            "candidate_generation_calls": self.candidate_generation_calls,
            "engine_rejections": list(self.engine_rejections),
            "raw_idea_ranking": list(self.raw_idea_ranking),
            "raw_idea_rank_coverage": dict(self.raw_idea_rank_coverage),
            "raw_ideas": list(self.raw_ideas),
            "premise_cards": list(self.premise_cards),
            "schema_version": "concept-tournament.v2",
        }


def _config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "concept_tournament.yaml"


@lru_cache(maxsize=1)
def load_concept_tournament_config() -> dict[str, Any]:
    """Load ``config/concept_tournament.yaml`` (cached). Empty dict if missing/bad."""

    path = _config_path()
    if not path.exists():
        logger.warning("concept_tournament config not found at %s", path)
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.warning("Failed to load concept_tournament config", exc_info=True)
        return {}


def resolve_tournament_config(
    *, wild: bool = False, base: dict[str, Any] | None = None
) -> dict[str, Any]:
    """基线 config +（wild=True 时）深合并 ``wild_mode`` 覆盖块。

    非 wild → 返回题材原生安全基线。旧 ``current`` 只有显式设置
    ``allow_legacy_cross_domain`` 才会保留。深合并只对 dict 值（judge_weights）
    生效，标量直接覆盖。永不修改被 lru_cache 缓存的基线对象。
    """

    cfg = base if base is not None else load_concept_tournament_config()
    # ``current`` is the historical cross-domain experiment.  It is unsafe as
    # a production default because it samples the global dimension pool before
    # the selected genre has established its ontology.  Keep it available for
    # isolated A/B tests only; runtime configuration must opt in explicitly.
    if (
        isinstance(cfg, dict)
        and str(cfg.get("candidate_prompt_mode") or "").strip().lower() == "current"
    ):
        if not bool(cfg.get("allow_legacy_cross_domain", False)):
            cfg = {**cfg, "candidate_prompt_mode": "engine_first"}
    if not wild or not isinstance(cfg, dict):
        return cfg
    overrides = cfg.get("wild_mode")
    if not isinstance(overrides, dict):
        return cfg
    merged: dict[str, Any] = dict(cfg)
    for key, value in overrides.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = {**current, **value}
        else:
            merged[key] = value
    return merged


def resolve_banned_cliches(
    genre: str | None, sub_genre: str | None, config: dict[str, Any] | None = None
) -> tuple[str, ...]:
    """generic 俗套 + canonical 题材俗套合并去重。fail-open 到 generic。"""

    cfg = config if config is not None else load_concept_tournament_config()
    seeds = cfg.get("cliche_seeds") if isinstance(cfg, dict) else {}
    if not isinstance(seeds, dict):
        seeds = {}
    merged: list[str] = [str(x) for x in (seeds.get("generic") or [])]
    canonical = ""
    try:
        from bestseller.services.genre_taxonomy import canonicalize

        canonical = str(canonicalize(genre, sub_genre) or "")
    except Exception:
        logger.debug("cliche genre canonicalize failed", exc_info=True)
    if canonical and isinstance(seeds.get(canonical), list):
        merged.extend(str(x) for x in seeds[canonical])
    return tuple(dict.fromkeys(x for x in merged if x.strip()))


def _cliche_hits(candidate: ConceptCandidate, banned: tuple[str, ...]) -> list[str]:
    """概念/机制文本命中的俗套关键词（按禁用短语的 2+ 字连续子串宽松匹配）。

    禁用短语是"废脉其实是宝脉"这类概括语，候选不会逐字复述——按短语切出的
    词元计数撞车。阈值分档（2026-07-09 真机校准）：长短语(>4词元)要求≥3 命中
    ——首轮真机里"修真账房做假功德账"这个不错的对照组候选被"老祖飞升前留下
    传承"以【老祖+飞升】两个散词误毙；短短语("退婚打脸"类)保持≥2。
    """

    text = f"{candidate.concept} {candidate.mechanism} {candidate.hook_question}"
    hits: list[str] = []
    for phrase in banned:
        tokens = [t for t in _phrase_tokens(phrase) if len(t) >= 2]
        if not tokens:
            continue
        matched = sum(1 for t in tokens if t in text)
        required = 3 if len(tokens) > 4 else 2 if len(tokens) > 1 else 1
        if matched >= required:
            hits.append(phrase)
    return hits


def _phrase_tokens(phrase: str) -> list[str]:
    """把禁用短语切成词元：连续 2-4 字滑窗太糙，这里按常见分隔+双字滑窗。"""

    phrase = phrase.strip()
    if not phrase:
        return []
    # 双字滑窗：中文短语无空格，双字词元是俗套识别的最小稳定单元。
    return [phrase[i : i + 2] for i in range(0, len(phrase) - 1)]


def _seed_audit(candidate: ConceptCandidate) -> str | None:
    """候选阶段只审最小完整故事，不让长篇规划字段干扰一句话竞争。"""

    if not candidate.concept.strip() or not candidate.mechanism.strip():
        return "concept/mechanism 为空"
    one_liner = candidate.concept.strip()
    if "\n" in one_liner or not 18 <= len(one_liner) <= 120:
        return "一句话钩子必须是18-120字的单句，不能靠标题或解释段补足"
    missing_seed_fields = [
        label
        for label, value in (
            ("主角身份", candidate.protagonist_identity),
            ("私人欲望", candidate.protagonist_private_desire),
            ("核心异常", candidate.core_abnormality),
            ("第一危机", candidate.opening_crisis),
            ("对手系统", candidate.opponent_system),
            ("决策证明", candidate.decision_proof),
            ("情绪承诺", candidate.emotional_promise),
        )
        if not value.strip()
    ]
    if missing_seed_fields:
        return "CoreStorySeed 不完整: " + "/".join(missing_seed_fields)
    return None


def _deterministic_anti_pattern(candidate: ConceptCandidate) -> str | None:
    """Reject a few high-confidence failure shapes before an LLM can rationalize them."""

    text = "；".join(
        part.strip()
        for part in (candidate.concept, candidate.mechanism, candidate.decision_proof)
        if part.strip()
    )
    if any(term in text for term in ("资本真身", "复活死路", "未来庄家")):
        return "abstract_formula"
    if (
        ("随机" in text or "每使用一次" in text or "每发动一次" in text)
        and any(term in text for term in ("折寿", "失忆", "寿命扣", "器官衰竭"))
    ):
        return "external_cost"
    if (
        any(
            term in text
            for term in ("每用一次", "每递一", "每送一", "每渡一", "每完成一")
        )
        and any(
            term in text
            for term in (
                "离死越近",
                "离死亡越近",
                "寿元越短",
                "寿命越短",
                "记忆越少",
                "自我越少",
            )
        )
    ):
        return "external_cost"
    if (
        any(term in text for term in ("每继承一次", "每完成一次", "每使用一次"))
        and any(
            term in text
            for term in ("存在就被抹去", "没人记得", "失去自我", "记忆被扣除")
        )
    ):
        return "external_cost"
    if (
        any(
            term in text
            for term in (
                "每渡一",
                "每送一",
                "每救一",
                "每点一",
                "每多一",
                "灯愈明",
            )
        )
        and any(
            term in text
            for term in (
                "记忆便愈",
                "记忆稀薄",
                "记忆剥落",
                "自我剥落",
                "失去当下",
                "忘记自己",
                "人格消失",
            )
        )
    ):
        return "external_cost"
    if (
        any(
            term in text
            for term in (
                "每揭穿一条",
                "每揭开一条",
                "每改写一次",
                "每窥见一次",
                "每动用一次",
            )
        )
        and any(
            term in text
            for term in (
                "讨走一块肉",
                "肉身残缺",
                "寿命",
                "气运",
                "人格磨损",
                "记忆被吞噬",
            )
        )
    ):
        return "external_cost"
    if (
        any(term in text for term in ("报警即可", "求助即可", "可以安全解决"))
        and any(term in text for term in ("独自赴死", "主动送死", "为了故事"))
    ):
        return "irrational_protagonist"
    if (
        any(term in text for term in ("每接一个", "每换一个", "每找到一个"))
        and any(term in text for term in ("然后遇到更强", "再来一次", "再被封杀", "破解一次"))
    ):
        return "parallel_repetition"
    return None


def _seriality_audit(candidate: ConceptCandidate, *, target_chapters: int) -> str | None:
    """决赛阶段审可续写性；这里失败不会回头修改一句话，只会淘汰候选。"""

    if not candidate.repeatable_story_unit.strip():
        return "缺可再生故事单元"
    if target_chapters >= 200 and not candidate.core_promise_invariant.strip():
        return "缺跨阶段不变的核心读者承诺"
    if target_chapters >= 200 and len(candidate.unit_families) < 4:
        return "冲突家族不足4类，仍可能靠同案换皮"
    if not candidate.progress_bar.strip():
        return "缺进度条(读者看什么在涨)"
    ladder = [q.strip() for q in candidate.question_ladder if q.strip()]
    if len(dict.fromkeys(ladder)) < 3:
        return "问题梯不足3级互异问题"
    if not candidate.ch50.strip():
        return "答不出第50章在打什么"
    if candidate.ch50.strip() == candidate.progress_bar.strip():
        return "第50章与进度条同文(敷衍)"
    if target_chapters >= 200:
        from bestseller.services.seriality_capacity import evaluate_seriality_capacity

        report = evaluate_seriality_capacity(
            {
                "repeatable_story_unit": candidate.repeatable_story_unit,
                "unit_families": candidate.unit_families,
                "unit_frequency": candidate.unit_frequency,
                "unit_count_estimate": candidate.unit_count_estimate,
                "renewal_sources": candidate.renewal_sources,
                "accumulation_tracks": candidate.accumulation_tracks,
                "phase_transitions": candidate.phase_transitions,
                "opposing_ecology": candidate.opposing_ecology,
                "mystery_ladder": candidate.question_ladder,
                "endgame_direction": candidate.endgame_direction,
            },
            target_chapters=target_chapters,
            require_phase_coverage=True,
        )
        if not report.passed:
            return (
                f"容量证明不足({report.estimated_chapter_ceiling}章上限): "
                + "/".join(report.blocking_codes[:3])
            )
    return None


def _build_candidate_messages(
    *,
    genre: str,
    sub_genre: str,
    dimension: str,
    chapter_count: int,
    banned: tuple[str, ...],
    avoid_mechanisms_block: str,
    seed_concept: str = "",
    retry_feedback: str = "",
    audience_orientation: str = "",
) -> tuple[str, str]:
    system = (
        "你是顶级网文制作人，专出'榜单编辑看到会立刻加价买断'的高概念。"
        "你深知平庸的本质是可预测：读者一句话能自动补全全书的概念一文不值。"
        "你的每个概念必须让人产生'等等，这怎么成立？'的认知缺口。"
    )
    if seed_concept.strip():
        hybrid_directive = (
            "本路是已有核心创意的同源补强，不是另起炉灶。必须保留原种子的主角职业、"
            "核心能力/发现、主要对手和产业冲突，不得嫁接考古、殡葬、法医、戏班等无关"
            "外部领域。允许彻底重建持续行动：原句若是‘每换一个对象再重复一次’，必须"
            f"丢掉这层弱机制。本路只聚焦【{dimension}】：可以压缩表达、补足因果或长篇机制，"
            "但改完后必须仍明显是同一本书。"
        )
    elif dimension == _CONTROL_DIMENSION:
        hybrid_directive = (
            "本路为纯题材对照组：不引入外部领域，但必须在题材内部找到一个"
            "反共识的切入角（反的是读者共识，不是主角处境）。"
        )
    elif dimension == _CHARACTER_CONTROL_DIMENSION:
        hybrid_directive = (
            "本路为纯题材人物对照组：不引入外部领域，不先造能力和代价。先站在一个"
            "具体主角的第一人称，设计一场正常聪明人也会被迫认真权衡的困局；高概念"
            "必须从他的职业、欲望、关系和理性选择中长出来。"
        )
    else:
        hybrid_directive = (
            f"本路强制杂交：把【{genre}】与【{dimension}】这个异质领域硬性融合，"
            "在两者交点上找没人写过的位置。杂交必须是概念级的（用该领域重塑机制与"
            "冲突的运转方式），不是把该领域词汇当皮肤贴上去；但故事的主冲突与核心"
            f"读者契约必须仍然明显属于【{genre}】——题材保真是钩子硬门（不达标即废稿），"
            "异质领域是手段，绝不许让它变成题材本身。"
            "只对本路使用一条压缩脑洞原则：第一眼意外，解释后必然；新奇点必须压在"
            "人物核心与现实行动上，并真实改变关系、资源、暴露风险、制度压力或未来选择；"
            "若去掉这个脑洞主线仍能成立，就说明只是装饰，必须重做。"
        )
    seed_block = (
        f"【用户已选核心创意——必须保留其故事身份，只能补强长篇机制】\n{seed_concept}\n\n"
        if seed_concept.strip()
        else ""
    )
    scale_directive = ""
    if chapter_count >= 500:
        scale_directive = (
            "目标是500章以上：底层机制必须含三层因果，但不要把三层说明硬塞进钩子——"
            "高频选择会留下状态，中层争夺由这些状态结算，阶段跃迁再改变下一轮题型。"
            "只有单案、单剑、单次揭谜、一年一次仪式、不断换地图，或‘换一个对象再做"
            "同一件事’的机制直接废稿；这里只说机制，不展开卷纲。\n"
        )
    elif chapter_count >= 200:
        scale_directive = (
            "目标是200章以上：核心机制必须同时有可反复发生的小循环和会改变规则、"
            "关系或势力格局的中层积累，不能只靠同类案件换皮。\n"
        )
    retry_block = (
        "【上一轮真实失败——本轮必须修复根因，不得只换措辞】\n"
        f"{retry_feedback.strip()}\n"
        "若反馈指出平行重复或外部投喂，必须更换故事发动方式；不得继续写成‘换一个X，"
        "再完成一次Y/再被阻止一次’。\n\n"
        if retry_feedback.strip()
        else ""
    )
    audience_line = (
        f"【频道/受众】{audience_orientation} —— 主角设定、爽点形态与情绪承诺必须写给"
        "该频道的目标读者，频道错位（如男频写文艺女主向）即废稿。\n"
        if str(audience_orientation or "").strip()
        else ""
    )
    user = (
        f"【题材】{genre}（{sub_genre}）｜目标体量：{chapter_count}章起步、可扩展到几百章\n"
        f"{audience_line}\n"
        f"{seed_block}"
        f"{retry_block}"
        f"{hybrid_directive}\n\n"
        f"{render_cliche_avoidance_block(banned)}\n"
        f"{avoid_mechanisms_block}\n"
        f"{scale_directive}"
        "【硬性要求】\n"
        "①概念一句话建议40-90字、硬上限120字，最多两个分句，只写标志性发现/行动和"
        "立即后果，必须含认知缺口（读者无法自动补全后续）。不要把第一危机、决策证明、"
        "三层长篇结构和完整人物小传重复塞进概念，它们各有独立字段；\n"
        "一句话优先尝试两种干净结构之一：独特规则+一个具体到令人不安的反常实例，或"
        "标志性行动+立刻改变处境的后果。不要为了证明故事会动而硬塞集团、资本或幕后组织；\n"
        "②核心机制必须能自然反复运转（每次运转都会遇到新的人、新利益和新选择），不是一次性信息"
        "（'主角知道哪里有宝'这类先知型金手指=废稿）；\n"
        "③主角的优势必须反共识（把某个被轻视的东西做成最强，或把某个共识证伪）。\n\n"
        "核心机制必须写成因果飞轮：主角本轮主动选择→改变资源/关系/证据/规则状态→"
        "对手、客户或团队基于新状态聪明反应→制造下一轮不同问题。‘每找到一条路线就"
        "被封杀一次’这种只有对象变化、局面不变化的平行重复直接废稿。若种子包含多个"
        "对象，优先让碎片汇成同一个增长中的答案，或让上一轮成果永久改写下一轮规则，"
        "而不是把对象当成可无限替换的任务清单。\n\n"
        "④把一句话背后的最小完整故事同时交付：主角的具体身份/私人欲望/缺陷、"
        "核心异常、第一场迫使行动的危机、会聪明反制的对手系统、为什么冒险是当时局部最优、"
        "读者持续获得的核心情绪。不得留给后续 Agent 临时拼接。\n"
        "决策证明必须明确比较至少一个更安全、更便宜或更直接的方案，并说明它为何不可行；"
        "禁止用任意倒计时、强塞代价或‘别无选择’四字代替因果。\n\n"
        "机制不得用与核心动作没有因果关系的固定惩罚制造戏剧性；"
        "风险必须是主角行为改变名额、资源、关系、证据或制度之后自然产生的后果。"
        "如果没有额外代价故事也成立，就不要硬塞代价。\n\n"
        "一句话必须是普通目标读者一遍就懂的大白话：专业领域可以决定冲突，但禁止把"
        "精算残差、算法名、机构缩写等需要搜索解释的术语塞进钩子。杂交维度只能改造"
        "原题材的玩法，不能把原题材替换掉；读者必须仍能一眼认出这是所选题材。"
        "少用‘复活死路、资本真身、未来庄家’等抽象隐喻，改写成主角具体做出什么产品、"
        "对手如何反制；题材受众熟悉的职业常识词可以保留。\n\n"
        "此轮禁止写卷纲、阶段表、第50章和终局说明；先把一句话和人物因果做对，"
        "长篇承载力只会在入围后单独验证。\n\n"
        "只输出 JSON：\n"
        "{\"concept\": \"一句话高概念\", \"mechanism\": \"核心机制一句话\", "
        "\"hook_question\": \"读者的认知缺口疑问\", "
        "\"protagonist_identity\": \"具体身份与处境\", "
        "\"protagonist_private_desire\": \"不等于拯救世界的私人欲望\", "
        "\"protagonist_flaw\": \"会影响选择的缺陷\", "
        "\"core_abnormality\": \"异常/能力的可执行规则\", "
        "\"opening_crisis\": \"第一场迫使行动的具体危机\", "
        "\"opponent_system\": \"会学习和反制的具体对手或系统\", "
        "\"decision_proof\": \"安全替代方案为何失败以及冒险为何局部最优\", "
        "\"emotional_promise\": \"读者持续追读获得的核心情绪\"}"
    )
    return system, user


def _build_lean_candidate_messages(
    *,
    genre: str,
    sub_genre: str,
    dimension: str,
    chapter_count: int,
    banned: tuple[str, ...],
    avoid_mechanisms_block: str,
    seed_concept: str = "",
    retry_feedback: str = "",
    audience_orientation: str = "",
) -> tuple[str, str]:
    """Build the experimental compact StoryPackage arm.

    This round deliberately solves only the attractive, coherent story seed.
    SerialityProof remains a separate finalist operation, so chapter-count
    scaffolding cannot leak into the one-line hook.
    """

    system = (
        "你是有判断力的商业小说主编。先像作者一样找到一个具体、可信、会自己运动的故事，"
        "再把它压成一句话；不要用字段堆砌、随机代价或术语制造高概念。只输出JSON。"
    )
    seed_block = (
        f"原始种子（保留职业、核心发现和题材身份；弱机制可重建）：{seed_concept.strip()}\n"
        if seed_concept.strip()
        else ""
    )
    retry_block = (
        f"上一轮失败（修根因，不换措辞）：{retry_feedback.strip()}\n"
        if retry_feedback.strip()
        else ""
    )
    dimension_instruction = (
        "在题材内部寻找反共识的人物困局"
        if dimension in {_CONTROL_DIMENSION, _CHARACTER_CONTROL_DIMENSION}
        else f"只把【{dimension}】作为破题视角，若它只是换皮就舍弃"
    )
    audience_line = (
        f"频道/受众：{audience_orientation}（主角与爽点必须写给该频道读者，频道错位即废稿）。\n"
        if str(audience_orientation or "").strip()
        else ""
    )
    user = (
        "【精简故事包】\n"
        f"题材：{genre}（{sub_genre}）；目标体量：{chapter_count}章，但本轮禁止规划章节。\n"
        f"{audience_line}"
        f"{seed_block}{retry_block}"
        f"探索方向：{dimension_instruction}。\n"
        "一句话必须是目标读者一遍就懂的大白话，生造术语/需要解释的机构名不得进钩子。\n"
        f"{render_cliche_avoidance_block(banned)}"
        f"{avoid_mechanisms_block}"
        "\n先在内部完成三项检查，不输出分析过程：\n"
        "1. 人物：站在主角第一人称，正常聪明人会这样选吗？至少比较一个更安全、"
        "更便宜或更直接的方案；"
        "冒险必须是当时的局部最优，不能靠‘别无选择’或降智。\n"
        "2. 对手：对手有自己的目标，会学习主角的方法并作出聪明反制；下一轮问题来自本轮造成的"
        "资源、关系、证据、规则或暴露变化，而非再换一个对象重复任务。\n"
        "3. 脑洞：只保留一条原则——第一眼意外，解释后必然。新奇点必须改变人物行动；"
        "与核心动作没有因果关系的外置代价一律删除；风险必须是行为的必然后果。\n\n"
        "一句话职责：40-90字为佳，硬上限120字，最多两个分句；只写主角最有辨识度的发现/行动"
        "及立即后果，让普通目标读者一遍看懂并想追问后续。不要写世界观说明、阶段表、卷纲或终局。\n\n"
        "优先使用‘独特规则+一个具体悖论’或‘标志性行动+立即后果’，不要硬塞幕后集团。\n\n"
        "只输出JSON："
        "{\"concept\":\"一句话钩子\",\"mechanism\":\"主角行动如何改变局面并引发下一轮不同问题\","
        "\"hook_question\":\"读者自然产生的问题\","
        "\"protagonist_identity\":\"具体身份与处境\","
        "\"protagonist_private_desire\":\"私人欲望\","
        "\"protagonist_flaw\":\"影响选择的缺陷\","
        "\"core_abnormality\":\"可执行的异常或优势\","
        "\"opening_crisis\":\"第一场具体危机\","
        "\"opponent_system\":\"会学习和反制的对手\","
        "\"decision_proof\":\"安全方案为何不成立、冒险为何局部最优\","
        "\"emotional_promise\":\"持续追读情绪\"}"
    )
    return system, user


def _build_native_candidate_messages(
    *,
    genre: str,
    sub_genre: str,
    dimension: str,
    chapter_count: int,
    banned: tuple[str, ...],
    avoid_mechanisms_block: str,
    seed_concept: str = "",
    retry_feedback: str = "",
    audience_orientation: str = "",
) -> tuple[str, str]:
    """Minimal control arm that preserves the model's native story judgment."""

    del banned, avoid_mechanisms_block
    story_lane = dimension if dimension in _NATIVE_STORY_LANES else "纯题材直觉"
    system = (
        "你是一位真正会讲故事的商业小说作者。先从正常人的欲望和选择出发，"
        "想清楚一本书为什么值得追，再压成一句话。只输出JSON。"
    )
    seed = (
        f"用户已有创意：{seed_concept.strip()}\n"
        "保留它的故事身份；如果其中的循环很弱，可以重建玩法。\n"
        if seed_concept.strip()
        else ""
    )
    retry = (
        f"上次失败：{retry_feedback.strip()}。这次修故事，不要只换说法。\n"
        if retry_feedback.strip()
        else ""
    )
    audience_line = (
        f"频道/受众：{audience_orientation}（主角与爽点必须写给该频道读者，频道错位即废稿）。\n"
        if str(audience_orientation or "").strip()
        else ""
    )
    user = (
        "【原生故事基线】\n"
        f"请为{genre}（{sub_genre}）想一个有吸引力、适合约{chapter_count}章的原创故事。\n"
        f"{audience_line}"
        f"{seed}{retry}"
        f"本次从“{story_lane}”起步，但不要套公式。先想正常人的欲望和选择、具体困境"
        "和会不断变化的局面。\n"
        "不要为了显得新奇硬加代价、系统、幕后集团或行业术语；一句话可以用一个具体"
        "反常事件让人想追问。不要写卷纲和阶段表。\n"
        "只输出JSON："
        "{\"concept\":\"一句话钩子\",\"mechanism\":\"故事为何会继续变化\","
        "\"hook_question\":\"读者自然想问什么\","
        "\"protagonist_identity\":\"主角身份与处境\","
        "\"protagonist_private_desire\":\"私人欲望\","
        "\"protagonist_flaw\":\"影响选择的缺陷\","
        "\"core_abnormality\":\"核心异常或优势\","
        "\"opening_crisis\":\"开局具体危机\","
        "\"opponent_system\":\"会主动应对的对手\","
        "\"decision_proof\":\"为何这样做比安全方案更合理\","
        "\"emotional_promise\":\"持续追读情绪\"}"
    )
    return system, user


# 建书页那四档调性。只认这四个键，未知值忽略而不是原样塞进 prompt——一个拼错的值
# 不该变成模型眼里的写作指令。
_TONE_DIRECTIVES: dict[str, str] = {
    "light": "调性：轻松。日常质感、能自嘲，冲突要真但不压抑；禁止把苦难当深度。",
    "epic": "调性：宏大。格局与阶段跨度要撑得起长篇，代价落在具体人身上而非抽象天下。",
    "dark": "调性：暗黑。代价不可逆、选择有污点；但仍要给读者继续读下去的牵引。",
    "hot": "调性：热血。行动密度高、正面对撞，情绪往上走而不是往下压。",
}


def _tone_directive_line(tone_preference: str) -> str:
    directive = _TONE_DIRECTIVES.get(str(tone_preference or "").strip().lower())
    return f"{directive}\n" if directive else ""


def _effect_skills_directive_line(effect_skills: tuple[str, ...] | list[str]) -> str:
    """Name the ticked story skills so the premise is built to deliver them."""

    keys = [str(item).strip() for item in (effect_skills or []) if str(item).strip()]
    if not keys:
        return ""
    try:
        from bestseller.services.story_effect_skills import story_effect_skill_labels

        labels = story_effect_skill_labels(keys)
    except Exception:
        labels = []
    if not labels:
        return ""
    return (
        f"故事技能（建书页勾选，必须在概念层就能兑现）：{'、'.join(labels)}。"
        "概念本身要给这些技能留出发挥空间，而不是等后面章节再硬加。\n"
    )


_LIGHT_TONE_MARKERS: tuple[str, ...] = (
    "轻松", "幽默", "喜剧", "诙谐", "明快", "自嘲", "吐槽", "荒诞", "好笑", "反差",
)
_HEAVY_TONE_MARKERS: tuple[str, ...] = (
    "黑暗", "暗黑", "压抑", "沉重", "阴冷", "冷峻", "惨烈", "绝望", "尸首", "尸体", "收尸",
)

# 胁迫式生死赌注（2026-08-13 真机定罪《摸一摸，救我妹》）：情绪词表测不出
# **用事件写的沉重**——该书 premise 零情绪词，却是「妹妹被扣人质+限期一夜
# +否则天亮沉河」的最重开局，在 tone=light 下原样通过。结构不看心情看事件：
# ①人质扣押 ②限期处刑句（否则/不然/逾期 + 死亡后果）。只在 tone=light 时
# 参与判定；悬疑/末世等重压题材不选轻松调性时不受影响。
_COERCION_STAKE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"人质"),
    re.compile(
        r"(?:否则|不然|要不然|逾期|到期)[^，。；！？\n]{0,14}"
        r"(?:死|沉[河江湖海]|杀|活埋|活不|没命|偿命|陪葬|撕票|喂鱼)"
    ),
)


def _coercion_stake_hits(text: str) -> tuple[str, ...]:
    blob = str(text or "")
    return tuple(
        pattern.pattern[:24]
        for pattern in _COERCION_STAKE_PATTERNS
        if pattern.search(blob)
    )
def _creation_intent_content_violations(
    text: str,
    *,
    tone_preference: str = "",
    effect_skills: tuple[str, ...] | list[str] = (),
    cost_style: str = "",
) -> tuple[str, ...]:
    """Deterministic proof that explicit creation choices shaped the concept.

    Prompt presence is not option adherence.  The final candidate must expose
    the selected tone/effect promise in its own story-bearing fields; otherwise
    downstream planners receive a contradictory concept and can only decorate
    it after the fact.
    """

    blob = str(text or "")
    if not blob:
        return ()
    violations: list[str] = []
    tone = str(tone_preference or "").strip().lower()
    del effect_skills
    heavy_hits = sum(blob.count(token) for token in _HEAVY_TONE_MARKERS)
    # Positive style labels are control-plane metadata, not evidence that the
    # story surface is light.  Never let a trailing ``轻松/幽默`` tag cancel
    # repeated corpse/bleak imagery in the actual premise or opening.
    if tone == "light" and heavy_hits >= 2:
        violations.append("轻松调性被沉重/阴冷/尸体叙事覆盖")
    if tone == "light" and _coercion_stake_hits(blob):
        violations.append(
            "轻松调性与胁迫式生死赌注冲突（人质扣押/限期处刑式开局，"
            "事件层面的沉重不因缺少情绪词而豁免）"
        )
    # (2026-08-02) The minimal-cost vocabulary rejection was removed. 纯爽 is a
    # pacing preference — payoff lands fast and the protagonist keeps winning —
    # not a ban on the words a cultivation novel uses for its own costs.
    del cost_style
    # Effect skills are whole-book preferences, not literal vocabulary
    # requirements. Requiring “喜剧/打脸/爽” in a one-line premise made an
    # optional control decide whether the entire book could exist and rejected
    # semantically valid concepts that expressed the effect through scenes.
    # Keep only the high-confidence contradiction gate here. Selected skills
    # still reach generation and the outline/writer contracts, where they are
    # evaluated against actual beats instead of keyword presence.
    return tuple(violations)


def _candidate_story_text(candidate: ConceptCandidate) -> str:
    payload = candidate.to_dict()
    control_fields = {
        "seriality_report", "seriality_judge", "judge_reason", "rejected_reason",
        "constraint_ladder",
        "judge_freshness", "judge_click", "judge_predictable", "judge_character_logic",
        "judge_mechanism_causality", "judge_genre_fidelity", "judge_plain_language",
        "judge_story_motion", "judge_protagonist_agency", "composite", "dimension",
    }

    def _texts(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            out: list[str] = []
            for key, item in value.items():
                if key not in control_fields:
                    out.extend(_texts(item))
            return out
        if isinstance(value, (list, tuple)):
            out = []
            for item in value:
                out.extend(_texts(item))
            return out
        return []

    return "\n".join(_texts(payload))


def _demote_default_family_cards(
    approved: list[tuple[str, str]],
    *,
    prebuilt_kernels: dict[tuple[str, str], dict[str, Any]],
    seed_concept: str,
    result: ConceptTournamentResult,
) -> list[tuple[str, str]]:
    """展开位让给不在默认族里的卡片——降权，不是杀权。

    2026-08-24 真机（书9，零创意种子）逐层量出的覆盖空缺：

        一句话胚子   55字  子族0 命中  0 → 胚子层沉底判据放行
        展开后卡片 1247字  子族3 命中 39 → **此处**，母题刚可测
        构思终稿   8861字  子族3 命中183 → advisory，只挣一次重生

    冠军胚子写的是「借力/还力」，零个该族词；是展开把它翻译成了账本与
    债主。胚子层的判据结构上不可能抓到——它量的是母题出生之前的那一刻。

    卡片层曾有一道硬门，2026-08-02 退役，理由至今成立（「选了仙侠不等于
    禁止出现葬礼」）。所以这里恢复的不是那道门：
      * 只在**同一池里存在干净卡片**时才让位（比较式，不是清单式）
      * 全池同族 → 原样放行，绝不清空池（2026-08-06 定案）
      * 用户自己点名该族 → 完全跳过，那是用户的选择
      * 读不到内核的卡片按干净处理——未知不等于有罪
      * 不向任何 prompt 写一个该族的词（否定式指令点名母题词=种词）
    """

    if not approved:
        return approved
    if bool(default_debt_family_hits(str(seed_concept or ""))):
        return approved

    dominated: list[tuple[str, str]] = []
    clean: list[tuple[str, str]] = []
    for key in approved:
        kernel = prebuilt_kernels.get(key)
        if not isinstance(kernel, dict):
            clean.append(key)
            continue
        blob = _flatten_kernel_text(kernel)
        (dominated if is_debt_dominated(blob) else clean).append(key)

    if not dominated or not clean:
        return approved

    for dimension, premise_seed in dominated:
        kernel = prebuilt_kernels.get((dimension, premise_seed)) or {}
        result.engine_rejections.append(
            {
                "dimension": dimension,
                "scores": {},
                "reason": (
                    "展开后落进框架实测过度复用的默认主题族（用户未要求），"
                    "展开位让给同池中未落进该族的卡片"
                ),
                "failed_axes": ["default_family_after_expansion"],
                "family_hits": list(
                    default_debt_family_hits(_flatten_kernel_text(kernel))
                ),
                "seed_was_clean": not bool(
                    default_debt_family_hits(str(premise_seed or ""))
                ),
            }
        )
    return clean


def _flatten_kernel_text(kernel: object) -> str:
    """项目卡里的**故事文字**拼平，供确定性母题判据使用。"""

    def _walk(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, Mapping):
            out: list[str] = []
            for v in value.values():
                out.extend(_walk(v))
            return out
        if isinstance(value, (list, tuple)):
            out = []
            for v in value:
                out.extend(_walk(v))
            return out
        return []

    return "\n".join(_walk(kernel))


def _candidate_hard_rejection_reason(
    candidate: ConceptCandidate,
    *,
    seed_concept: str,
    tone_preference: str,
    effect_skills: tuple[str, ...] | list[str],
    cost_style: str = "",
    allow_debt_theme: bool | None = None,
    allow_death_theme: bool | None = None,
) -> str | None:
    """Reject a candidate that contradicts an EXPLICIT user選択 — nothing else.

    (2026-08-02) The debt-dominated / death-motif rejections were removed. They
    threw away candidates for containing ordinary story material the user had
    not pre-authorised by name, which is not how story ideas work: a reader who
    picks 仙侠 has not thereby forbidden a funeral. What remains here enforces
    the creation form's own switches (tone, cost pacing, effect skills) — that
    is executing the user's choice, not the framework's taste.
    """

    del seed_concept, allow_debt_theme, allow_death_theme

    text = _candidate_story_text(candidate)
    reasons = list(
        _creation_intent_content_violations(
            text,
            tone_preference=tone_preference,
            effect_skills=effect_skills,
            cost_style=cost_style,
        )
    )
    return "；".join(reasons) if reasons else None


def audit_constraint_ladder(
    candidate: "ConceptCandidate",
    *,
    chapter_count: int,
    cost_style: str,
) -> dict[str, Any]:
    """「无代价≠无限制」的**验收**（2026-08-27）。

    prompt 自 2026-08-19 起就要求纯爽/外置档写出 ``constraint_ladder``，
    并写明「若所有场景都在同一层次打转，项目不成立」。但 ``ConceptCandidate``
    从来没有这个字段——解析层整块丢弃（真机 custom-xuanhuan-1787757487
    12/12 候选零携带），全仓零消费方。**规则写了、没实现**，于是「限制」
    这一维度从概念层到成书从未被审过一次；用户看到的「舔一口就升级、
    没有任何限制、故事跑不起来」正是这条的产物。

    判据全部确定性，且**只留痕不发杀权**（本仓库对新检测器的规矩）：
      * 纯爽/外置档才要求——standard 档代价本身就是发动机，不强求；
      * 阶数不足目标值即记；
      * 各阶互相包含或逐字重复（「同一层次打转」的可判形态）即记。

    Returns 回执 dict，恒非空——没有回执就无法区分「查了没问题」与
    「压根没查」，这正是本案埋了八天的原因。
    """

    ladder = [str(x).strip() for x in (candidate.constraint_ladder or ()) if str(x).strip()]
    target = constraint_ladder_tier_target(chapter_count)
    style = str(cost_style or "standard").strip().lower()
    required = style in ("minimal", "external")
    findings: list[str] = []
    if required:
        if not ladder:
            findings.append("constraint_ladder_missing")
        elif len(ladder) < target:
            findings.append(f"constraint_ladder_short:{len(ladder)}/{target}")
        # 同一层次打转的可判形态：去重后剩不到两阶，或任一阶是另一阶的子串。
        if ladder:
            deduped = {x for x in ladder}
            nested = any(
                a != b and (a in b or b in a) for a in ladder for b in ladder
            )
            if len(deduped) < 2 or nested:
                findings.append("constraint_ladder_flat")
    return {
        "required": required,
        "cost_style": style,
        "tiers": len(ladder),
        "tier_target": target,
        "findings": findings,
        "passed": not findings,
    }


def constraint_ladder_tier_target(chapter_count: int) -> int:
    """目标章节数 → 概念层必须给出的限制阶梯层数（卷级）。

    2026-08-19 用户定案：构思必须支撑不同章节数——限制阶梯即卷结构，
    50 章书 3 阶够用，1000 章书需要 8 阶的世界纵深。与 seriality 微单元
    密度（每 2-4 章一单元）是两个粒度：这里数的是「能力/地图/对手整体
    升一层」的卷级台阶。
    """

    return max(3, min(8, 2 + int(chapter_count or 0) // 150))


def _build_engine_kernel_messages(
    *,
    genre: str,
    sub_genre: str,
    lane: str,
    chapter_count: int,
    seed_concept: str = "",
    seed_support: dict[str, Any] | None = None,
    audience_orientation: str = "",
    banned: tuple[str, ...] = (),
    cost_style: str = "standard",
    tone_preference: str = "",
    effect_skills: tuple[str, ...] | list[str] = (),
    creation_intent_block: str = "",
) -> tuple[str, str]:
    """Build a minimal premise card before any marketing sentence."""

    system = (
        "你是小说选题编辑。先判断一个人物是否能自然产生许多不同场景，不写宣传文案、"
        "卷纲、体系说明或500章阶段表。禁止把提示词里的禁用词和示例当故事素材。"
        "禁止用与核心动作没有因果关系的固定惩罚来假装张力。"
        "只输出JSON。"
    )
    seed = (
        f"原始创意（事实锚点）：{seed_concept.strip()}\n"
        "其中的主角身份、核心异常和关键关系不可替换；只能补足目标、阻力、选择后果"
        "和可变形循环。即使你想到另一个更好故事，也不得偷换。\n"
        if seed_concept.strip()
        else ""
    )
    support = ""
    if seed_support:
        support_payload = {
            key: seed_support.get(key)
            for key in ("graft", "opening", "why_it_keeps_moving", "future_situations")
            if seed_support.get(key)
        }
        if support_payload:
            support = (
                "作者在提炼一句话前已做过的故事探索（只用于保持原构思的因果与场面，"
                "不得借机替换一句话中的主角、异常或关系）："
                f"{json.dumps(support_payload, ensure_ascii=False)}\n"
            )
    lane_head, _, lane_axis = lane.partition("#")
    if lane_head == _GROWTH_LANE_PREFIX:
        # 不规定写什么，只要求这一个候选在指定维度上与其他候选不同。
        lane_brief = _NATIVE_STORY_LANE_BRIEFS["纯题材直觉"]
        if lane_axis.strip():
            lane_brief += (
                f"本候选的区分维度是【{lane_axis.strip()}】：这一维必须与同批其他"
                "候选明显不同，其余全部顺着上面的建书要求自然长出来，不要为了凑"
                "维度去改题材或调性。"
            )
    else:
        lane_brief = _NATIVE_STORY_LANE_BRIEFS.get(
            lane_head, _NATIVE_STORY_LANE_BRIEFS["纯题材直觉"]
        )
    audience_line = (
        f"频道/受众：{audience_orientation}。主角设定、reader_promise 和 emotional_promise "
        "必须写给该频道的目标读者；频道错位（如男频给出文艺女主向项目卡）即项目不成立。\n"
        if str(audience_orientation or "").strip()
        else ""
    )
    # 纯爽/外置代价档来自建书页勾选；standard 为空串保持 prompt 逐字节不变。
    from bestseller.services.ideology_kernel import cost_style_directive

    cost_line = cost_style_directive(cost_style, is_en=False).strip()
    cost_line = f"{cost_line}\n" if cost_line else ""
    # 限制≠代价（2026-08-19 用户定案）：纯爽档去掉的是**代价**（主角自身
    # 折损），不是**限制**——限制是剧情发动机（榜单实证：「只能在县城花」
    # 类限制条款本身就是全书剧情）。纯爽/外置档的概念必须仍有完整的
    # 规则-限制-条件设计，张力全部由限制形状与外部对手承担。限制四型是
    # 抽象形状不是母题词表，列举不构成种词。
    if cost_line and str(cost_style or "").strip().lower() in ("external", "minimal"):
        cost_line += (
            "但无代价≠无限制：核心能力必须仍带完整的限制设计，从这四种形状"
            "里取——范围限制（只对某类事/某个圈子生效）、条件限制（特定"
            "时机/频次/触发方式才能用）、资格限制（要攒到某种资源或地位才能"
            "解锁更大范围——这本身就是升级阶梯）、对象限制（只对某类人/物"
            "生效）。限制负责生成剧情与对手，主角本人始终不折损。\n"
        )
    # 限制锁当下不锁世界（2026-08-19 用户定案）：为自圆其说把限制钉死成
    # 永久窄边界（能力=单一场景）撑不起长篇——能力本体要大、开局表现形态
    # 要小，范围限制必须写成随资格解锁的阶梯，每解锁一层=一卷新地图。
    # 阶数随目标章节数伸缩（构思必须支撑不同章节数）。
    _ladder_tiers = constraint_ladder_tier_target(chapter_count)
    cost_line += (
        f"长篇容量铁律（本书目标 {chapter_count} 章）：限制只锁『当前阶段』，"
        "不锁世界。核心能力的范围限制必须设计成**随资格逐级解锁的阶梯**——"
        "开局只是能力的最低阶表现形态（第一卷的场景），每解锁一阶，能力可"
        "作用的事、面对的对手和所在的地图都升一层。"
        f"constraint_ladder 字段写出 {_ladder_tiers} 阶：每阶一句"
        "『解锁什么范围+该阶的一个卷级场景』，各阶必须分属不同层次；"
        "若所有场景都在同一层次打转（能力被钉死在单一行当/单一场景），"
        "项目不成立。\n"
    )
    # 建书页勾的调性与故事技能此前只进「商业定位 brief」——那是市场／角色／世界观
    # 那批 agent 的输入，而淘汰赛跑在它们之前。用户要的「轻松＋喜剧＋爽感」于是
    # 从未到达概念生成：真机四个候选全是沉重路子，判官正确判它们不想点、不好懂，
    # 干涸，书死，而用户的要求从头到尾没被任何模型看见（2026-07-29 玄幻）。
    # 未选时为空串，prompt 逐字节不变——与 cost_style 同一约定。
    tone_line = _tone_directive_line(tone_preference)
    skills_line = _effect_skills_directive_line(effect_skills)
    # 入参集整体在场：表单每加一个影响故事的选项都自动到达这里，不需要
    # 记得再补一次接线。逐字段补是治症状——2026-07-30 审计时叙事规模、
    # 反常识方向（界面自称「决定全书冲突轴」）、脑洞引擎全部到不了这一层。
    intent_line = (
        f"{creation_intent_block.strip()}\n"
        if str(creation_intent_block or "").strip()
        else ""
    )
    user = (
        "【PREMISE_CARD】本轮不写一句话钩子，也不规划卷章。\n"
        f"题材：{genre}（{sub_genre}）；目标形态：约{chapter_count}章长篇；"
        f"破题路线：{lane.split('#', 1)[0]}。\n"
        f"{audience_line}"
        f"{cost_line}"
        f"{tone_line}"
        f"{skills_line}"
        f"{intent_line}"
        f"路线边界：{lane_brief}\n"
        f"{seed}"
        f"{support}"
        "只做最小项目判断：谁的正常生活已无法维持；他眼下要完成什么可观察行动；谁有"
        "能力让他失败；失败会失去什么；成功又会伤害、暴露或放弃什么；选择后什么不能"
        # 2026-08-03：删掉「寿命、身份、记忆、家底、亲情」的点名列举——它把这五样
        # 直接摆进每一次概念生成的 prompt。只留正向判据：这两个字段解释开局一次决定。
        "复原。failure_cost和success_cost只解释开局这一次决定，不要把它们扩写成"
        "每次使用能力都要结算的固定收费表，也不得写进长期读者承诺。\n"
        "scene_seeds 必须给出5个彼此不同的具体场面，每个都写主角动作、当场阻力和选择，"
        "且至少覆盖关系、技能/行动、公开冲突、探索/发现、建设/改变中的4类。若只能把"
        "同一案件换人换地，项目不成立。至少3个场面必须使用目标题材原生的行动、资源和"
        "冲突；若删掉题材名词后换成都市、科幻或悬疑仍完全成立，项目不成立。"
        "post_reveal_scene_seeds 另写3个发生在开局异常已经被解释或第一次目标已经完成"
        "之后的场面，必须仍兑现同一读者承诺，并使用三类不同主角行动；如果只能继续"
        "查同一个真相、找同类证据或等新事件上门，项目不成立。"
        "deformable_loop 只写一个会因前轮后果而变形的"
        "循环；expansion_axes 只写未来可向哪三种方向深化，不做章数承诺。\n"
        "只输出JSON：{"
        "\"protagonist_identity\":\"身份与失衡处境\","
        "\"protagonist_private_desire\":\"真正想保住或得到什么\","
        "\"protagonist_flaw\":\"影响选择的缺陷\","
        "\"core_abnormality\":\"异常、优势或独特入口\","
        "\"current_goal\":\"眼下可观察行动\",\"effective_resistance\":\"有效阻力\","
        "\"failure_cost\":\"失败或不行动的自然后果\","
        "\"success_cost\":\"成功带来的伤害、暴露或放弃\","
        "\"irreversible_change\":\"不能复原的变化\","
        "\"reader_promise\":\"主要体验与持续追读问题\","
        "\"difference_point\":\"相对同类只改变了什么\","
        "\"scene_seeds\":[\"动作/阻力/选择1\",\"动作/阻力/选择2\","
        "\"动作/阻力/选择3\",\"动作/阻力/选择4\",\"动作/阻力/选择5\"],"
        "\"post_reveal_scene_seeds\":[\"揭晓后场面1\",\"揭晓后场面2\",\"揭晓后场面3\"],"
        "\"deformable_loop\":\"前轮后果会改变下一轮的核心循环\","
        "\"expansion_axes\":[\"深化方向1\",\"深化方向2\",\"深化方向3\"],"
        "\"constraint_ladder\":[\"第一阶：解锁范围+该阶卷级场景\",\"…（按容量铁律的阶数给足，各属不同层次）\"],"
        "\"opposing_ecology\":[\"自主阻力1\",\"自主阻力2\"],"
        "\"opening_crisis\":\"开局具体危机\",\"emotional_promise\":\"持续情绪\"}"
        # opening_crisis + core_abnormality are where the death/relic default
        # gets baked; steer them here, upstream of the hook sentence.
        + "\n" + render_cliche_avoidance_block(banned)
        # 主角名也是在这里铸死的（protagonist_identity 常带具名），cast 层的
        # 烂名禁令管不到已进书名/前提/简介的名字——同一份黑名单在此下沉
        # （2026-08-18《九姓井口只认我》：陆沉复发定罪）。
        + "\n" + render_protagonist_name_ban(compact=True)
        # The pre-planning logline gate hard-kills on story-logic axes
        # (protagonist_rationality / cost_integrity / causal_coherence) that are
        # decided HERE, in current_goal / opening_crisis / failure_cost — and
        # cannot be repaired later: the logline rescue rewriter is forbidden
        # from inventing story facts. Three consecutive real books died on
        # these axes (2026-07-21/22) because the kernel never saw the contract.
        + "\n" + _render_story_logic_gate_rules()
    )
    return system, user


def _render_story_logic_gate_rules() -> str:
    """Late import: logline_gate imports settings/llm; keep module load light."""

    from bestseller.services.logline_gate import render_story_logic_writer_rules

    return render_story_logic_writer_rules()


def _build_raw_idea_pool_messages(
    *,
    genre: str,
    sub_genre: str,
    count: int,
    seed_concept: str = "",
    prompt_arm: str = "enhanced",
    focus_hint: str = "",
    audience_orientation: str = "",
    tone_preference: str = "",
    effect_skills: tuple[str, ...] | list[str] = (),
    creation_intent_block: str = "",
) -> tuple[str, str]:
    """Minimal baseline: concrete person plus abnormal situation, nothing else.

    The channel anchor rides on the SYSTEM message so every prompt arm gets it
    with one injection point. 蒸钩 is the layer that mints the seeds everything
    downstream expands — before 2026-07-24 it had no channel anchor at all
    (the documented contract says 内核/蒸钩/候选 all three), so a 男频 request
    could seed female-lead ideas that the later anchors then fought uphill.
    """

    channel = (
        f"频道/受众：{audience_orientation}。每个创意的主角设定与爽点形态都必须"
        "写给该频道读者，频道错位的创意直接作废。"
        if str(audience_orientation or "").strip()
        else ""
    )
    intent = (
        _tone_directive_line(tone_preference)
        + _effect_skills_directive_line(effect_skills)
        + (f"{creation_intent_block.strip()}\n" if str(creation_intent_block or "").strip() else "")
    )
    system = (
        "你是小说作者。只负责想故事，不解释方法。只输出JSON。"
        + channel
        # 场域多样性硬约束（2026-08-09）。真机探针：同参数下模型自发把 62%
        # (25/40) 的创意押在同一个题材场域（丧葬/冥界一族），四次建书三次撞车。
        # prompt 面全程干净——这是模型自己对「新颖玄幻」的先验塌缩。约束只说
        # 类别与要求，不点名任何场域（点名即种词，见《雾街债主》案）。
        + "这一批创意必须来自彼此不同的生活场域与行当：任何两个创意不得共享"
        "同一职业、同一场所类型或同一核心题材元素；发现两个创意是同一类故事"
        "换皮时，必须把其中一个换成完全不同的场域重想。"
        + (
            intent
            + "以上建书约束与故事起点从原始创意这一层就必须成立；不得先生成相反调性、"
            "相反技能或无关故事的种子再交给下游补救。"
            if intent
            else ""
        )
    )
    # seed 锚定之罪（2026-08-12 四臂对照定案）：旧措辞「保留其职业或核心
    # 发现」让模型把 seed 的**句法骨架**复印成整批创意——同 seed 两臂
    # 24/24 全是「天生X命+师父+每X一劫」克隆，同 prompt 去掉 seed 立刻
    # 四条四种骨架。seed 只许当方向罗盘，不许当句式模板。
    seed = (
        "题材方向参考（只取它指向的世界和读者想要的爽感方向；"
        "**禁止沿用它的句式、开头词、人物身份和能力设定**——"
        f"每个创意必须换一种完全不同的切入骨架）：{seed_concept.strip()}\n"
        if seed_concept.strip()
        else ""
    )
    focus = f"本批优先从{focus_hint.strip()}寻找，不必覆盖其他方向。" if focus_hint.strip() else ""
    output_contract = (
        "只输出JSON："
        '{"ideas":[{"lane":"人际困局|世界规则|成长道路|世界扩张|势力选择|身份变化|职业处境|资源分配|纯题材直觉",'
        '"seed":"一句原始创意"}]}'
    )
    if prompt_arm == "author_pitch":
        # 2026-08-24 提示词工程重构（docs/一句话创意提示词工程分析-20260824.md）。
        # 三处结构性修复，各自的证据：
        # ① 判据双载→生成端瘦身：旧版七条铁律与排序判官的 12 条 cap 一一同构，
        #   换来的是防御性写作（《废丹成神》logline 用 400 字向 character_logic
        #   轴答辩）。规则书留给判官，生成端只留三条硬约束+「好的样子」。
        # ② 单句槽位超载→义务分字段：旧版五条铁律都写「这一句话里必须…」，
        #   7 个语义槽挤 40-80 字，模型把「后果」和「机制演示」并成一格=
        #   开局战力膨胀。现在 seed 只装三槽，机制因果落 graft（并新增因果桥
        #   义务——「搬进新场景靠什么仍成立」正是断层处缺的那根梁）。
        # ③ 冠军分布≠生成规则：嫁接从「12/12 必须、否则作废」降为默认策略
        #   +逃生门（榜单 57% 是嫁接，把幸存者模式写成铁律=制度化拼凑）。
        # 另：铁律二+四合并（被动开局+补一句「偏要」的表面合规是两条独立
        # 陈述的最省力解，《别人借力我替他们还债》被动开局仍加冕的直接根因）；
        # 悬疑段按题材条件渲染（纯玄幻里是 180 字死重）；「宁缺毋滥」替代硬凑
        # ——短产兜底 topup 本来就在，凑数换皮才是真损失。批量 12 整池一次
        # 生成不动（2026-08-10 单变量消融：batch=1 同族塌缩 60.4% vs batch=12
        # 7.5%，分批=同质化复辟）。
        _genre_blob = f"{genre}{sub_genre}"
        _is_mystery = any(
            token in _genre_blob
            for token in ("悬疑", "灵异", "怪谈", "惊悚", "恐怖", "推理", "诡秘")
        )
        _mystery_exception = "（悬疑品类例外：悬念钩合法）" if _is_mystery else ""
        _mystery_spread = (
            # 异常来源多样性（2026-08-12 真机定罪：悬疑池两轮 5/5 候选同一
            # 内容族）。纯正向类别列举，不点任何族名 token（种词铁律）。
            "整批的**异常来源也必须彼此不同**，"
            "从这些方向各取其一：住的地方不对劲、每天打交道的活人不对劲、"
            "一件旧物不对劲、一条人人默守的规矩不对劲、时间或记忆不对劲、"
            "自己的身体不对劲、一门老手艺不对劲——恐怖可以来自任何日常，"
            "整批不许挤在同一种来源上。\n"
            if _is_mystery
            else ""
        )
        return system, (
            f"为{genre}（{sub_genre}）认真构思{count}个不同的长篇小说创意。{seed}"
            f"{focus}"
            "像真正准备写书的作者一样，先把人物、开篇和故事为什么会继续想通，再提炼"
            "一句话；不要从流行设定词或反转模板开始拼装。\n"
            # 措辞去种词（2026-08-24 两轮真机定罪）：初版示例句里的「亲手」被
            # 11/12、11/11 两轮整批复印成句式骨架，「他要的不是…是…」被 8/12
            # 照抄——示例即模板、指令词即种词（同款已定案两次：seed 句法复印、
            # 「卖点1：」标签照抄）。同一语义在三处必须各用不同措辞，且句式
            # 多样性用可数规则表达（对齐场域令的集合式写法）。
            "【一句好创意的样子】一个具体的人，为了自己想要的东西已经做成了一件"
            "具体的事，后果当场兑现，读者立刻想看他接下来赢什么/翻什么身/兑现什么"
            "优势/清算什么账。这是要素清单，不是句式模板。\n"
            "整批句式令：同一个开头方式、同一个标志性短语或同一种收尾转折，"
            "在整批里至多出现两条；把同样的要素装进彼此不同的句子里。\n"
            "【字段分工】seed 只装三样：主角主动做出的那件已发生的事、它当场兑现的"
            "后果、由此点燃的读者渴望。机制原理写进 graft，开篇细节写进 opening，"
            "长期动力写进 why_it_keeps_moving——不要把所有东西塞进一句话。\n"
            "铁律（仅此三条，违反即作废）：\n"
            "铁律一【欲望钩】：目标频道读者必须能一秒说出想看主角接下来赢什么"
            f"{_mystery_exception}；seed 里要能读出他想赢得、夺回、兑现或做大的"
            "具体东西——只有阻止坏事、慷慨赴死式的自毁义举不算渴望；说不出即无"
            "渴望，作废。\n"
            "铁律二【主角主动·事件先行】：seed 里那件已发生的事必须出自主角自己的"
            "决定和行动；被逼、被卷入、被砸中而没有自己目标的开局作废——先写被动"
            "事件、再补一句主角『偏要如何』不算主动。\n"
            "铁律三【人话·不写机制句】：动词用最平实准确的说法（发现、接手、得到、"
            "继承），东西只做它物理上真会做的事；不把创意写成『谁A，谁就B』"
            "『X多了怎样、X少了怎样』式规则条款；读起来不像中国人说话的句子作废。\n"
            "【新鲜感的默认做法是嫁接】拿一个读者早已熟悉、不需要解释的机制或行当"
            "逻辑，放进一个意想不到的场景或身份里。graft 必须写两句：这次嫁接是什么"
            "（熟机制×意外场景，各几个字），以及机制搬进新场景后靠什么仍然成立——"
            "写成读者一眼能懂的因果链：做什么→为什么因此得什么→所以越做越强。"
            "这是机制可信的底线，"
            "写不出第二句的嫁接不成立。个别创意若有比嫁接更强的成立方式也可以，"
            "graft 里同样要写清『做什么→得什么→受什么限制』；一个创意堆两处以上"
            "新变量作废（认知过载）。\n"
            # 欲望形态多样性（2026-08-12 四批终审）：悬疑池整批押在同一种
            # 欲望上。集合层面的令，正向列举；「至多两次」解掉 12 条 vs 8 种
            # 具名形态的算术死结（旧版逼模型为后 4 条生造形态）。
            "整批多样性令：这一批创意的**欲望形态必须彼此不同**——活下来、"
            "破解规则、镇压收服、揭开身边人的不对劲、逃出去、兑现优势、"
            "夺回属于自己的东西、把小生意做大……确实想不出新形态时，同一种"
            "欲望形态在整批里至多出现两次。\n"
            f"{_mystery_spread}"
            # 产量下限（2026-08-24 真机：无下限的宁缺毋滥一轮只回 4 条，
            # 12→4 的坑比凑数还贵）。下限=判官取样面 8，上限仍是 count。
            f"宁缺毋滥：确实想不出{count}个真正不同的创意时可以少写，但不得少于"
            f"8条；不要为凑数硬造换皮或生僻行当，缺的由系统另行补齐。\n"
            "每个创意都把 seed、graft、opening、why_it_keeps_moving 和三个彼此"
            "不同的未来场面写全；这些字段会被后续选题工序采信，不写卷纲、体系表"
            "或章数计划。"
            "只输出JSON："
            '{"ideas":[{"lane":"人际困局|世界规则|成长道路|世界扩张|势力选择|身份变化|职业处境|资源分配|纯题材直觉",'
            '"seed":"一句话故事","graft":"熟机制×意外场景＋机制为何仍成立",'
            '"opening":"具体开篇",'
            '"why_it_keeps_moving":"开篇后仍持续行动的自然原因",'
            '"future_situations":["未来场面1","未来场面2","未来场面3"]}]}'
        )
    if prompt_arm == "minimal":
        return system, (
            f"为{genre}（{sub_genre}）想{count}个不同的小说创意。{seed}"
            f"{focus}"
            "每个只写一句：一个具体的人，遇到一个让人立刻想追问的异常处境。"
            f"{output_contract}"
        )
    if prompt_arm == "methodology":
        return system, (
            f"为{genre}（{sub_genre}）想{count}个不同的小说选题。{seed}"
            f"{focus}"
            "每个只写一句具体人物加异常处境。异常被公开或第一次使用之后，主角仍会因它"
            "持续做出不同选择。"
            f"{output_contract}"
        )
    if prompt_arm == "consequence":
        return system, (
            f"为{genre}（{sub_genre}）想{count}个不同的小说选题。{seed}"
            f"{focus}"
            "每个只写一句具体人物加异常处境。异常被公开或第一次使用之后，主角仍会因它"
            "持续做出不同选择。设定应当第一眼意外，明白人物处境与世界因果后又觉得必然；"
            "它会自然改变角色、关系、资源、暴露风险或未来选择中的至少两项。"
            f"{output_contract}"
        )
    if prompt_arm == "guarded":
        return system, (
            f"为{genre}（{sub_genre}）想{count}个不同的小说选题。{seed}"
            f"{focus}"
            "每个只写一句具体人物加异常处境。异常被公开或第一次使用之后，主角仍会因它"
            "持续做出不同选择；一句里必须看见主角会做什么。不要用与核心动作无因果"
            "关系的固定惩罚或强制倒计时推进。"
            f"{output_contract}"
        )
    if prompt_arm != "enhanced":
        raise ValueError(f"unsupported raw idea prompt arm: {prompt_arm}")
    user = (
        f"为{genre}（{sub_genre}）想{count}个不同的小说原始创意。\n"
        f"{seed}"
        f"{focus}"
        "每个创意只写一句‘一个具体的人，遇到一个让人立刻想追问的异常处境’。"
        "对长篇而言，异常不能只是等一个谜底揭晓的开局事故；第一次解释或使用之后，"
        "它仍应长期改变主角的身份、关系或生存方式，让主角自然需要做许多不同的事。"
        "至少一半创意必须是稳定的故事场：即使第20章公开所有开局秘密，主角仍因新的"
        "职业位置、移动世界、建设目标、多势力夹缝、成长技艺或关系网络而持续行动。"
        "这些创意的一句里必须出现主角将长期做的可见动词。不要用与核心动作无因果"
        "关系的按次固定惩罚来制造张力。"
        "题材必须长在故事骨头里：至少三分之二的创意要由该题材特有的身份道路、社会关系、"
        "资源争夺或行动方式发动；只把地名和名词换成仙门、灵气、法器不算。"
        "先追求故事本身有意思，不写大纲、世界观说明、系统字段、长篇规划或能力收费表。"
        f"主角和异常都必须具体，不能只写主题。{output_contract}"
    )
    return system, user


def _parse_raw_idea_records(raw: str, *, limit: int) -> list[dict[str, Any]]:
    payload = _parse_json_object(raw)
    ideas = (payload or {}).get("ideas")
    if not isinstance(ideas, list):
        return []
    parsed: list[dict[str, Any]] = []
    for item in ideas:
        if not isinstance(item, dict):
            continue
        seed = str(item.get("seed") or "").strip()
        if not seed:
            continue
        lane = str(item.get("lane") or "纯题材直觉").strip()
        if lane not in _NATIVE_STORY_LANES:
            lane = "纯题材直觉"
        future_situations = item.get("future_situations")
        parsed.append(
            {
                "lane": lane,
                "seed": seed,
                # 机制因果的义务落在 graft（字段分工，2026-08-24）——判官与
                # 项目卡展开都要看它，解析层丢掉=义务白分。
                "graft": str(item.get("graft") or "").strip(),
                "opening": str(item.get("opening") or "").strip(),
                "why_it_keeps_moving": str(
                    item.get("why_it_keeps_moving") or ""
                ).strip(),
                "future_situations": [
                    str(value).strip()
                    for value in future_situations
                    if isinstance(value, str) and value.strip()
                ]
                if isinstance(future_situations, list)
                else [],
            }
        )
        if len(parsed) >= limit:
            break
    return parsed


def _parse_raw_idea_pool(raw: str, *, limit: int) -> list[tuple[str, str]]:
    return [
        (str(item["lane"]), str(item["seed"]))
        for item in _parse_raw_idea_records(raw, limit=limit)
    ]


def _build_raw_idea_rank_messages(
    *,
    genre: str,
    sub_genre: str,
    ideas: list[tuple[str, str]],
    audience_orientation: str = "",
    pitch_by_seed: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Rank raw ideas in one independent call before expensive card expansion.

    The ranker needs the channel anchor too: without it, a channel-mismatched
    seed can outrank a fitting one and the mismatch propagates into every
    downstream expansion.

    ``pitch_by_seed`` carries the author_pitch support record per seed. The
    mechanism-causality obligation moved from the seed sentence into ``graft``
    (2026-08-24 field split), so the judge must see graft — judging the seed
    alone would score the very field the obligation just left.
    """

    channel = (
        f"频道/受众：{audience_orientation}。频道错位的胚子直接排到末位。"
        if str(audience_orientation or "").strip()
        else ""
    )
    system = (
        "你是严苛的商业长篇选题编辑。只审原始故事胚子，不替它补设定。"
        "表达长短不加分，只输出JSON。" + channel
    )
    rows = []
    for index, (lane, seed) in enumerate(ideas):
        row: dict[str, Any] = {"index": index, "lane": lane, "seed": seed}
        graft = str(((pitch_by_seed or {}).get(seed) or {}).get("graft") or "").strip()
        if graft:
            row["graft"] = graft
        rows.append(row)
    user = (
        f"题材={genre}（{sub_genre}）\n候选={json.dumps(rows, ensure_ascii=False)}\n\n"
        # C 层判据（2026-08-18 榜单 70 本蒸馏）：合法的新=熟机制×异场景一处
        # 嫁接；孤立新异常（无读者已知框架可挂载又无可复述规则）是怪不是新。
        "逐项评分0-10：freshness 核心组合是否区别于常见同类——注意：合法的新"
        "是『读者熟悉的机制×意外场景』的一处嫁接；若异常既没有读者已知框架"
        "可挂载（穿越/重生/系统面板/全民降临/获得传承/民俗或历史体系等），"
        "又复述不出一条『输入→输出→限制』的规则，那是孤立怪象不是新鲜，"
        "freshness 不得超过4；对题材默认套路的偏离≥2处=认知过载，"
        "freshness 不得超过5；候选若带 graft（作者自报的嫁接与机制说明），"
        "核对它与 seed 是否自洽——机制因果断裂或 graft 与 seed 对不上，"
        "freshness 不得超过5；"
        "click_seed 只问一件事：目标频道读者能否一秒说出自己想看主角接下来"
        "赢什么/翻什么身/兑现什么（悬疑品类可用悬念代替）——说不出即无渴望，"
        "处境再巧妙 click_seed 不得超过4；"
        # 2026-08-19 用户终审冤案：「醒来成死刑犯+31天死线」拿了8分——危机
        # 和死线本身不是爽点，读者要看的是他**拿什么打这局**。
        "危机、死线或身份坠落本身不构成爽点：若创意里主角没有一件当场"
        "可用的优势、可玩的规则或能兑现的筹码去打这局，click_seed 不得"
        "超过5；同时你必须能用一个词指认这个创意"
        "承诺的爽的类型（数值碾压/身份反差/规则玩弄/见证兑现/囤积独活/收集"
        "成长之一），指认不出=只有怪事没有承诺，click_seed 不得超过4；"
        "主角全程被动（被逼着/被迫/被卷入而无"
        "自己的目标）不得超过4；钩子止于反讽或荒诞处境、没有读者在乎的赌注，"
        "不得超过4；写成对称机制条款（谁A谁就B、X多了怎样X少了怎样）不得超过4；"
        "character_logic 正常聪明人是否会作出原句暗示的选择；action_seed "
        "是否已经看得见主角要做什么；promise_survival 开局异常"
        "被揭晓或第一次使用后，是否仍能持续产生同类但不同的选择与场景。若点子依赖"
        "会归零的次数、一次性谜底、不断来新委托或同一能力重复使用，promise_survival"
        "不得超过4分；genre_fidelity 是否由该题材原生的身份、行动、资源和冲突成立，"
        "若删掉题材名词后换成别的题材仍完全成立，genre_fidelity 不得超过5分。"
        "另给 ai_assembly 0-10，越高越像用职业、残魂、天道、器官、制度名词和收费代价"
        "强行拼成新奇；dumb_cost=true 表示依赖按次折寿、失忆、伤身、扣身份/人格/命数"
        "或无因果死亡倒计时。若主角只是被作者逼着做明显不合理的事，character_logic"
        "不得超过4；dumb_cost 必须淘汰，不能用其他高分抵消。"
        "对每项必须给三条直接证据，不能替原始创意补设定：after_opening_promise 用一个"
        "陈述句写第一次"
        "异常被解释或使用后仍存在的故事承诺；action_families 写由原句直接推出的至少3类"
        "不同主角行动；growth_surface 写会因这些行动持续积累或扩大的关系、能力、事业、"
        "地盘、势力或世界变化。after_opening_promise 禁止写问号、为何、能否、谁是；"
        "growth_surface 禁止写可能、也许、或将。任一证据只能靠新增设定才能成立，则留空且"
        "promise_survival不得超过4分。不要因缺少大纲扣分。"
        # domain 标签（2026-08-09）：下游据此保证被展开的候选不全来自同一场域
        # （真机探针：模型自发把六成创意押在同一场域）。标签由裁判自拟，
        # 框架不提供词表；同场域必须同标签，是唯一的格式要求。
        "另给每项 domain：2-6字的核心场域/行当标签（如主角职业或故事发生的行当），"
        "本次候选里属于同一场域的必须给完全相同的标签。只返回综合最强的8项，"
        "按强到弱排序。只输出JSON：{\"ranked\":["
        "{\"index\":0,\"freshness\":0-10,\"click_seed\":0-10,"
        "\"character_logic\":0-10,\"action_seed\":0-10,\"promise_survival\":0-10,"
        "\"genre_fidelity\":0-10,\"ai_assembly\":0-10,\"dumb_cost\":false,"
        "\"domain\":\"场域标签\","
        "\"after_opening_promise\":\"持续承诺或空字符串\","
        "\"action_families\":[\"行动1\",\"行动2\",\"行动3\"],"
        "\"growth_surface\":\"持续积累面或空字符串\"}]}"
    )
    return system, user


def _parse_raw_idea_ranking(raw: str) -> list[dict[str, Any]]:
    payload = _parse_json_object(raw)
    ranked = (payload or {}).get("ranked")
    if not isinstance(ranked, list):
        return []
    parsed: list[dict[str, Any]] = []
    for item in ranked:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
            scores = {
                key: max(0.0, min(10.0, float(item.get(key, 0))))
                for key in (
                    "freshness",
                    "click_seed",
                    "character_logic",
                    "action_seed",
                    "promise_survival",
                    "genre_fidelity",
                    "ai_assembly",
                )
            }
        except (TypeError, ValueError):
            continue
        parsed.append(
            {
                "index": index,
                **scores,
                "dumb_cost": bool(item.get("dumb_cost")),
                "domain": str(item.get("domain") or "").strip(),
                "after_opening_promise": str(
                    item.get("after_opening_promise") or ""
                ).strip(),
                "action_families": [
                    str(action).strip()
                    for action in (item.get("action_families") or [])
                    if str(action).strip()
                ]
                if isinstance(item.get("action_families"), list)
                else [],
                "growth_surface": str(item.get("growth_surface") or "").strip(),
                "reason": str(item.get("reason") or ""),
            }
        )
    return parsed


def _select_raw_ideas_for_expansion(
    ranking: list[dict[str, Any]],
    *,
    raw_floor: float,
    progression_floor: float,
    limit: int,
) -> list[dict[str, Any]]:
    """Select premises worth expanding without pretending they already passed.

    The raw rank is a cheap triage stage. Requiring every raw one-liner to meet
    the production floor creates a false-negative trap: the premise card is the
    stage that makes a stable story field explicit. We therefore prefer strict
    passes, then fill remaining slots with evidence-complete near passes. Final
    hook and seriality judges remain unchanged and decide production approval.
    """

    # A judge can repeat an index inside a batch. Do not let one seed consume
    # several expensive premise-card slots.
    deduped: dict[int, dict[str, Any]] = {}
    score_axes = (
        "freshness",
        "click_seed",
        "character_logic",
        "action_seed",
        "promise_survival",
        "genre_fidelity",
    )
    for item in ranking:
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        previous = deduped.get(index)
        item_total = sum(float(item.get(axis, 0)) for axis in score_axes)
        previous_total = (
            sum(float(previous.get(axis, 0)) for axis in score_axes)
            if previous is not None
            else -1.0
        )
        if previous is None or item_total > previous_total:
            deduped[index] = item

    evidence_complete = [
        item
        for item in deduped.values()
        if not bool(item.get("dumb_cost"))
        and float(item.get("ai_assembly", 10)) <= 4.0
        and float(item.get("character_logic", 0)) >= progression_floor
        and bool(item.get("after_opening_promise"))
        and not any(
            token in str(item.get("after_opening_promise") or "")
            for token in ("?", "？", "为何", "能否", "谁是")
        )
        and len(item.get("action_families") or []) >= 3
        and bool(item.get("growth_surface"))
        and not any(
            token in str(item.get("growth_surface") or "")
            for token in ("可能", "也许", "或将")
        )
    ]

    axes = (
        "freshness",
        "click_seed",
        "character_logic",
        "promise_survival",
        "genre_fidelity",
    )

    def score(item: dict[str, Any]) -> tuple[float, float, float]:
        values = [float(item.get(axis, 0)) for axis in axes]
        # Long-form survival and click are the two claims the raw seed must make.
        return (min(values), values[1] + values[2], sum(values))

    strict = [
        item
        for item in evidence_complete
        if all(float(item.get(axis, 0)) >= raw_floor for axis in axes)
    ]
    near = [
        item
        for item in evidence_complete
        if item not in strict
        and all(float(item.get(axis, 0)) >= progression_floor for axis in axes)
        and float(item.get("click_seed", 0)) >= progression_floor + 1
        and float(item.get("action_seed", 0)) >= progression_floor + 1
        and float(item.get("promise_survival", 0)) >= progression_floor
    ]
    strict.sort(key=score, reverse=True)
    near.sort(key=score, reverse=True)
    # Domain cap (2026-08-09): at most ONE expansion slot per rank-judge domain
    # label. Live probe with the ledger book's exact params: the model spent 62%
    # (25/40) of the pool on a single domain family and ranking kept that share
    # (65% of selected) — 3 of 4 same-parameter conceptions crowned the same
    # family. Expansion slots are the scarce resource; a skewed pool must not
    # buy several of them for one domain. Ideas the judge left unlabeled are
    # never grouped (fail-open), and the cap relaxes back to pure score order
    # when distinct domains cannot fill the limit.
    # Ideas the market already has go to the back of the queue (2026-08-10).
    # Demotion, not rejection: an expansion slot should prefer a concept the
    # board does not already carry, but if every candidate collides we still
    # expand the best of them rather than returning nothing — this codebase has
    # killed enough books with hard gates.
    # 定罪结构（谁A谁就B/对称机制条款）沉底比撞车更深：撞车只是市场巧合，
    # 定罪结构是用户逐条终审判死的形态；调性冲突（用户选轻松却是胁迫式
    # 生死赌注）沉得同样深——选题必须服从用户选项。都不过滤——池永远不清空。
    ordered = sorted(
        strict + near,
        key=lambda item: (
            bool(item.get("condemned_structure")),
            bool(item.get("tone_conflict")),
            bool(item.get("default_family")),
            bool(item.get("market_collision")),
        ),
    )

    capped: list[dict[str, Any]] = []
    seen_domains: set[str] = set()
    overflow: list[dict[str, Any]] = []
    for item in ordered:
        domain = str(item.get("domain") or "").strip()
        if domain and domain in seen_domains:
            overflow.append(item)
            continue
        if domain:
            seen_domains.add(domain)
        capped.append(item)
    capped.extend(overflow)
    return capped[: max(0, limit)]


# ── plain_language: one contract, rendered to BOTH the writer and the judge ──
#
# Root cause this exists to fix (2026-07-21, real book creation blocked):
# the judge scored every candidate 4.0 on plain_language and the hard floor
# killed the whole tournament — yet the production hook prompt
# (``_build_hook_from_engine_messages``) never contained the rule the judge
# was enforcing. It said only "一遍就懂" while simultaneously demanding
# "只有本书才有的异常事实" and "删除抽象词，除非是场景中摸得到的物件" —
# a combination whose only compliant solution is an invented concrete proper
# noun (缉牒队 / 引魂针), which is exactly what the judge caps at 4.
#
# The detailed rule did exist, but in ``_build_lean_candidate_messages`` —
# dead code under the production ``engine_first`` route. Same failure shape as
# the anti-AI discipline before it got a single source: the generator is judged
# against a contract it was never shown.
# NOTE: keep this text channel-neutral. "划走" is the marker that channel
# priming (男频/女频) has been injected, and a paired test asserts a
# channel-less judge prompt never contains it — a shared block that smuggles
# persona framing in unconditionally would bias neutral judging.
_PLAIN_LANGUAGE_CORE = (
    "普通目标读者要能一遍形成清楚画面。需要搜索解释的术语、算法名、机构缩写，"
    "以及多层抽象因果，都会让读者读不下去。"
    "题材目标读者本来就懂的常识词不算术语，例如仙侠里的灵材/天庭/渡劫、"
    "民俗里的祠堂/禁忌、都市黑科技里的芯片/报废晶圆/设计/产品/供应商/基金。"
    "校准示例：“芯片工程师从报废晶圆里复原被放弃的设计，再把它做成产品”"
    "对都市黑科技目标读者是好懂的；“修仙界快递员发现自己送的不是货，是活人”"
    "也是好懂的；“用COTRS精算残差重校再保险时间序列”是看不懂的。"
)


def render_plain_language_writer_rule() -> str:
    """Generation-side phrasing: paired with what to write instead.

    Bare prohibitions prime what they forbid (2026-07-18 arena), so this states
    the substitution rather than only the ban.
    """

    return (
        "【一句话必须是大白话】"
        + _PLAIN_LANGUAGE_CORE
        + "所以：**不要为了独特而生造机构名、门派名、法器名、组织缩写**。"
        "独特性要落在“这个人此刻要做的那件事”上，不要落在名词上——"
        "把生造专名换成题材读者本来就懂的通用说法（“缉牒队”→“官府的缉捕队”、"
        "“引魂针”→“三根银针”），故事照样成立，读者却不用停下来猜。"
    )


def render_plain_language_judge_rule() -> str:
    """Judge-side phrasing: same contract plus the scoring instruction."""

    return (
        "7. plain_language 大白话：只评上面‘概念：’这一行，必须忽略后附机制、认知"
        "缺口、人物和决策证明。"
        + _PLAIN_LANGUAGE_CORE
        + "若概念一句话含必须搜索解释的术语、算法名、机构缩写或多层抽象因果，"
        "本项不得超过4分。校准分数：好懂的应为8-10分，看不懂的应为0-3分。"
        "此轴只评一遍能否看懂，不评你个人是否喜欢这个创意。\n"
    )


def render_cliche_avoidance_block(banned: tuple[str, ...]) -> str:
    """Render a positive anti-cliche instruction without quoting the corpus.

    The ban bank remains the single source for deterministic post-generation
    screening. It must not become generation material: negative examples are
    prompt tokens too, and repeated exact phrases made new books absorb the
    motifs the gate was meant to reject.
    """

    items = [b.strip() for b in banned if b.strip()]
    if not items:
        return ""
    return (
        "【原创开局约束】\n"
        "俗套库将在生成后由程序独立检查，具体禁用文本不进入本轮提示词。"
        "请从在世主角此刻要完成的具体行动、可见阻力、手中独有资源和当场选择出发；"
        "开局事件必须由本题材当前世界状态触发，而不是照搬任何预设模板。\n"
    )


def _build_hook_from_engine_messages(
    *,
    genre: str,
    sub_genre: str,
    kernel: dict[str, Any],
    seed_concept: str = "",
    audience_orientation: str = "",
    retry_feedback: str = "",
    banned: tuple[str, ...] = (),
) -> tuple[str, str]:
    """Distill a human-facing story seed from an already designed engine."""

    system = (
        "你是商业小说主编。候选项目卡已经冻结，你只负责找到其中最有人味、最想点的"
        "开局表达；不得添加无因果的固定惩罚、幕后集团或新主线。只输出JSON。"
    )
    audience_line = (
        f"频道/受众：{audience_orientation}（三条钩子都写给该频道读者，用他们要的爽点角度切入）\n"
        if str(audience_orientation or "").strip()
        else ""
    )
    user = (
        "【HOOK_DISTILL】\n"
        f"题材：{genre}（{sub_genre}）\n"
        f"{audience_line}"
        f"原始种子：{seed_concept.strip() or '无'}\n"
        f"冻结项目卡：{json.dumps(kernel, ensure_ascii=False)}\n\n"
        "不要套promise/paradox/scene模板。像真正的小说作者一样独立写3条最想让人点开的"
        "一句话故事，每条都必须让人看见：一个具体主角、只有本书才有的异常事实、主角"
        "接下来会做的事或马上形成的困局。三条可以分别偏身份反转、独特行动或核心矛盾，"
        "但都必须是完整故事，不能只有气氛或一个片段。每条30-75字、单句。\n"
        + render_plain_language_writer_rule()
        + "\n"
        "概念只使用读者能在开局场景直接看见或理解的人、物、行动与结果；"
        "抽象机制必须改写成主角当场能做的具体事情。"
        # 负例种词修复（2026-08-24 全链排查）：此前这里逐字引用被禁词
        # （一步步/命运齿轮/随着真相浮现）和被禁句式（他只能/他必须/否则）——
        # 与 render_cliche_avoidance_block 自己写明的铁律相悖（负例也是
        # prompt token，会被复印）。确定性执法在 blurb_appeal_gate 的正则里，
        # 生成端只说类别。
        "删除叙述总结腔：不概括过程、不预告命运走向、不替读者下结论，"
        "只写当场发生的事。不要解释500章、"
        "不要用剥夺主角选择的强制式措辞，不要照抄字段名。\n"
        # The judge KOs these before scoring; show them here so the generator
        # does not keep proposing the openings it is about to be eliminated for.
        + render_cliche_avoidance_block(banned)
        # Without this the retry loop is decorative under engine_first: the
        # caller threads retry_feedback only into the non-production branch, so
        # a tournament could fail the same axis three rounds running and the
        # generator would never learn why.
        + (
            f"【上一轮为什么被拒，这一轮必须改掉】\n{retry_feedback.strip()}\n"
            if retry_feedback.strip()
            else ""
        )
        + "项目卡中的人物、异常、目标、阻力、选择理由和持续机制都已冻结，不要在输出中"
        "改变事实或另加设定；允许选择并压缩其中事实。只输出JSON："
        "{\"hooks\":[{\"angle\":\"identity\",\"concept\":\"一句话1\"},"
        "{\"angle\":\"action\",\"concept\":\"一句话2\"},"
        "{\"angle\":\"conflict\",\"concept\":\"一句话3\"}]}"
    )
    return system, user


def _attach_engine_kernel(
    candidate: ConceptCandidate,
    kernel: dict[str, Any],
) -> ConceptCandidate:
    from dataclasses import replace

    def _tuple(key: str) -> tuple[str, ...]:
        value = kernel.get(key)
        return (
            tuple(str(item).strip() for item in value if str(item).strip())
            if isinstance(value, list)
            else ()
        )

    try:
        unit_count = max(0, int(kernel.get("unit_count_estimate") or 0))
    except (TypeError, ValueError):
        unit_count = 0

    def _text(key: str) -> str:
        return str(kernel.get(key) or "").strip()

    resistance = _text("effective_resistance")
    failure_cost = _text("failure_cost")
    success_cost = _text("success_cost")
    decision_proof = "；".join(
        part
        for part in (
            f"眼下必须完成：{_text('current_goal')}" if _text("current_goal") else "",
            f"安全退让仍会失败：{resistance}" if resistance else "",
            f"不行动会失去：{failure_cost}" if failure_cost else "",
            f"即使成功也要承受：{success_cost}" if success_cost else "",
        )
        if part
    )
    return replace(
        candidate,
        mechanism=candidate.mechanism or _text("deformable_loop"),
        hook_question=candidate.hook_question or _text("reader_promise"),
        protagonist_identity=candidate.protagonist_identity
        or _text("protagonist_identity"),
        protagonist_private_desire=candidate.protagonist_private_desire
        or _text("protagonist_private_desire"),
        protagonist_flaw=candidate.protagonist_flaw or _text("protagonist_flaw"),
        core_abnormality=candidate.core_abnormality or _text("core_abnormality"),
        opening_crisis=candidate.opening_crisis or _text("opening_crisis"),
        opponent_system=candidate.opponent_system
        or "；".join(_tuple("opposing_ecology")),
        decision_proof=candidate.decision_proof or decision_proof,
        emotional_promise=candidate.emotional_promise
        or _text("emotional_promise"),
        core_promise_invariant=_text("reader_promise"),
        constraint_ladder=_tuple("constraint_ladder"),
        role_ladder=_tuple("role_ladder"),
        world_ladder=_tuple("world_ladder"),
        repeatable_story_unit=str(kernel.get("deformable_loop") or "").strip(),
        unit_families=_tuple("unit_families"),
        unit_frequency=str(kernel.get("unit_frequency") or "").strip(),
        unit_count_estimate=unit_count,
        progress_bar=str(kernel.get("progress_bar") or "").strip(),
        question_ladder=_tuple("mystery_ladder"),
        ch50=str(kernel.get("ch50") or "").strip(),
        renewal_sources=_tuple("expansion_axes"),
        accumulation_tracks=_tuple("accumulation_tracks"),
        phase_transitions=_tuple("phase_transitions"),
        opposing_ecology=_tuple("opposing_ecology"),
        endgame_direction=str(kernel.get("endgame_direction") or "").strip(),
    )


def _candidate_message_builder(
    config: dict[str, Any],
) -> Callable[..., tuple[str, str]]:
    mode = str(config.get("candidate_prompt_mode") or "current").strip().lower()
    if mode == "current":
        return _build_candidate_messages
    if mode == "lean_story_package":
        return _build_lean_candidate_messages
    if mode == "native_baseline":
        return _build_native_candidate_messages
    if mode == "engine_first":
        return _build_native_candidate_messages
    raise ValueError(f"unsupported candidate_prompt_mode: {mode}")


def _build_seriality_messages(
    *, candidate: ConceptCandidate, genre: str, chapter_count: int
) -> tuple[str, str]:
    """Expand a frozen seed; seriality may prove it, but may not rewrite it."""

    from bestseller.services.seriality_capacity import required_seriality_unit_count

    phase_min = (
        9
        if chapter_count >= 1500
        else 5
        if chapter_count >= 1000
        else 4
        if chapter_count >= 500
        else 3
    )
    family_min = 6 if chapter_count >= 1500 else 4
    renewal_min = 5 if chapter_count >= 1500 else 3
    ecology_min = 5 if chapter_count >= 1500 else 2
    mystery_min = 9 if chapter_count >= 1500 else 5 if chapter_count >= 1000 else 3
    required_units = required_seriality_unit_count(chapter_count)
    system = (
        "你是长篇网文结构总编。你不能修改已经通过的故事种子，只负责判断它是否能"
        "自然续写，并给出可检验的长篇承载证明。禁止靠重复副本、无限地图、强塞倒计时"
        "或每卷换皮来伪造长度。只输出JSON。"
    )
    user = (
        f"【冻结故事种子】题材={genre}，目标={chapter_count}章\n"
        f"一句话：{candidate.concept}\n机制：{candidate.mechanism}\n"
        f"主角：{candidate.protagonist_identity}\n私人欲望：{candidate.protagonist_private_desire}\n"
        f"核心异常：{candidate.core_abnormality}\n开局危机：{candidate.opening_crisis}\n"
        f"对手系统：{candidate.opponent_system}\n人物决策证明：{candidate.decision_proof}\n\n"
        "证明要求（每个字符串不超过80个汉字，禁止解释性散文）：\n"
        "0. core_promise_invariant 写出从开篇到终局都不变的读者承诺；阶段升级只能改变"
        "范围、角色和题型，不能换掉这件事。\n"
        "1. repeatable_story_unit 必须写成三级发动机：2-4章内生微单元（主角主动发现/"
        "选择/试错/交易/反制）→8-20章中期成果弧→跨卷产业或阵营阶段；不是能力说明，"
        "也不能只写每隔十章捡到一个新素材；\n"
        "1.1 unit_frequency 写清2-4章内主角会作出的选择、试错或遭遇的反制。"
        f"unit_count_estimate 填不少于{required_units}的节奏预算；少于该数说明2-4章一次"
        "与目标篇幅自相矛盾。裁判不会因数字高而加分，容量仍由行动家族、阶段变化和"
        "后果链证明。每个单元必须由既有行动、关系或规则变化自然触发；\n"
        f"1.2 unit_families 至少{family_min}类不同冲突语法，例如发现、交易、关系选择、公开博弈、"
        "建设、反制、内部裂变；每类都必须由核心机制触发，不是外部随机投喂。\n"
        "1.3 role_ladder 与 world_ladder 各至少4级。前者写每阶段主角的新职责和新动作，"
        "后者写资源、关系网络、规则与利益相关者如何变化；不得只升官、换地图或成神。\n"
        f"2. renewal_sources 至少含{renewal_min}种相互独立的来源，并至少两种来自主角既有行动的"
        "后果、团队新发现、对手反制、客户/关系变化或制度反馈；各种来源必须能回溯到"
        "主角已经做出的选择和逐步扩大的世界关系；\n"
        "3. accumulation_tracks 每轮都必须留下不可逆变化，禁止打完复位；\n"
        f"4. phase_transitions 至少{phase_min}阶段，每项必须写明确连续章号范围，"
        f"从第1章连续覆盖到第{chapter_count}章；最后一项未到第{chapter_count}章即失败，"
        "且每个阶段至少一种新的主角动作、冲突家族和不可逆产物；不能只是从个人案升级"
        "为城市案、全国案却仍做同一件事；\n"
        f"5. opposing_ecology 至少{ecology_min}个会学习、结盟、背叛的自主势力；\n"
        f"6. mystery_ladder 至少{mystery_min}级，答案会扩大而不是终止故事；\n"
        "7. ch50 必须仍在兑现同一个故事承诺，不能已经解决总问题；\n"
        "8. endgame_direction 由前述积累自然汇聚，不能另起炉灶。\n\n"
        "只输出JSON：{\"core_promise_invariant\":\"全书不变承诺\","
        "\"role_ladder\":[\"角色/动作1\",\"角色/动作2\",\"角色/动作3\",\"角色/动作4\"],"
        "\"world_ladder\":[\"盘面1\",\"盘面2\",\"盘面3\",\"盘面4\"],"
        "\"repeatable_story_unit\":\"每轮动作循环\","
        "\"unit_families\":[\"发现\",\"交易\",\"关系选择\",\"公开博弈\"],"
        "\"progress_bar\":\"读者可感知的增长\","
        "\"unit_frequency\":\"单元自然发生频率\","
        f"\"unit_count_estimate\":{required_units},"
        "\"renewal_sources\":[\"来源1\",\"来源2\",\"来源3\"],"
        "\"accumulation_tracks\":[\"积累1\",\"积累2\",\"积累3\"],"
        f"\"phase_transitions\":[\"第1-X章阶段1\",\"第X+1-Y章阶段2\","
        f"\"第Y+1-Z章阶段3\",\"第Z+1-{chapter_count}章阶段4\"],"
        "\"opposing_ecology\":[\"势力1\",\"势力2\"],"
        "\"mystery_ladder\":[\"问题1\",\"问题2\",\"问题3\"],"
        "\"ch50\":\"第50章冲突\",\"endgame_direction\":\"终局汇聚方向\"}"
    )
    return system, user


def _build_seriality_judge_messages(
    *, candidate: ConceptCandidate, chapter_count: int
) -> tuple[str, str]:
    system = (
        "你是极其苛刻的长篇连载总编。只审承载力，不替作者补设定。字段写满不等于能写长。"
        "只输出JSON。"
    )
    user = (
        f"目标={chapter_count}章\n一句话={candidate.concept}\n"
        f"核心承诺={candidate.core_promise_invariant}\n"
        f"循环单元={candidate.repeatable_story_unit}\n冲突家族={list(candidate.unit_families)}\n"
        f"角色行动梯={list(candidate.role_ladder)}\n盘面变化梯={list(candidate.world_ladder)}\n"
        f"发生频率={candidate.unit_frequency}\n"
        f"预计单元数={candidate.unit_count_estimate}\n更新来源={list(candidate.renewal_sources)}\n"
        f"不可逆积累={list(candidate.accumulation_tracks)}\n阶段变化={list(candidate.phase_transitions)}\n"
        f"势力生态={list(candidate.opposing_ecology)}\n悬念梯={list(candidate.question_ladder)}\n"
        f"第50章={candidate.ch50}\n终局={candidate.endgame_direction}\n\n"
        "六轴0-10：renewability 新单元是否真正可再生；escalation 阶段是否换玩法而非涨数值；"
        "anti_reset 每轮是否留下不可逆变化；coherence 长篇是否始终兑现同一句话；"
        "promise_survival 一句话里的核心发现/矛盾在开篇兑现后是否仍持续产出同类故事，"
        "若揭晓后只能换主线不得超过4分；unit_density 不采信自报总数，只看冲突家族能否"
        "组合、角色新动作与盘面变化能否在章节级持续制造选择和反制；若一年一次事件被"
        "硬拉数百章不得超过4分。"
        "任一项低于6都应判失败。只输出JSON："
        '{"renewability":0-10,"escalation":0-10,"anti_reset":0-10,'
        '"coherence":0-10,"promise_survival":0-10,"unit_density":0-10,'
        '"reason":"30字内"}'
    )
    return system, user


def creation_intent_judge_axes(
    *, tone_preference: str = "", cost_style: str = ""
) -> tuple[tuple[str, str], ...]:
    """Per-option fit axes for the engine judge, derived from creation choices.

    Why the judge and not a detector (2026-08-09, live A/B): a 爽文无代价 book
    shipped a golden finger whose core loop was 「每说一个重字，消耗一日寿元」,
    and a 轻松 request shipped 殡仪馆/缝尸 openings that dodge any word list by
    paraphrase (缝尸 is not 尸体). The acceptance question — "is per-use
    self-cost the ENGINE?", "is the whole card tonally heavy?" — is semantic,
    so it belongs on the judge that already reads every card, as a floored
    axis. `_creation_intent_content_violations` deliberately dropped its
    cost-vocabulary check in 2026-08-02 (words are not violations); this is the
    structural replacement it never got. Instructions stay category-level:
    naming specific props here would seed them (《雾街债主》 lesson).
    """

    axes: list[tuple[str, str]] = []
    if str(tone_preference or "").strip().lower() == "light":
        axes.append((
            "tone_fit",
            "tone_fit 用户建书时明确选择了轻松明快基调：看项目卡的核心处境、"
            "场面与意象整体是否兑现这个基调；若通篇沉重压抑、以死亡仪式或阴森"
            "氛围为主体验，本轴不得超过4分；轻松、幽默、烟火气或热血爽快的按"
            "贴合度给分",
        ))
    if str(cost_style or "").strip().lower() == "minimal":
        axes.append((
            "cost_style_fit",
            "cost_style_fit 用户建书时明确选择了爽文无代价（金手指不向主角收费）："
            "外部风险、交换条件与选择后果都合法；但若能力每次使用都让主角自身"
            "付出持续损耗，或这种自损构成核心循环/卖点，本轴不得超过4分",
        ))
    return tuple(axes)


def _render_intent_axis_schema(intent_axes: tuple[tuple[str, str], ...]) -> tuple[str, str]:
    """(instruction sentences, JSON schema fragment) for the fit axes."""

    if not intent_axes:
        return "", ""
    instructions = "".join(f"{text}；" for _, text in intent_axes)
    schema = "".join(f'"{key}":0-10,' for key, _ in intent_axes)
    return instructions, schema


def _build_engine_judge_messages(
    *,
    kernel: dict[str, Any],
    genre: str,
    sub_genre: str,
    chapter_count: int,
    seed_concept: str,
    intent_axes: tuple[tuple[str, str], ...] = (),
) -> tuple[str, str]:
    """Judge a premise card before hook copy can hide a weak project."""

    intent_text, intent_schema = _render_intent_axis_schema(intent_axes)
    system = (
        "你是极其苛刻的小说选题编辑。此时还没有宣传钩子，只审项目卡。字段写满、"
        "解释很长或声称能写500章都不能加分。只输出JSON。"
    )
    user = (
        f"题材={genre}（{sub_genre}）；目标={chapter_count}章\n"
        f"原始创意={seed_concept}\n"
        f"项目卡={json.dumps(kernel, ensure_ascii=False)}\n\n"
        f"{intent_text}"
        "十一轴0-10：seed_fidelity 项目卡是否保留原始创意中的主角身份、核心异常与关键"
        "关系；只要替换了其中任一核心事实，seed_fidelity不得超过4分。freshness 人物与"
        "异常处境的核心组合是否明显区别于常见同类；"
        "click_seed 不看文案技巧，只看这个故事胚子是否让目标读者立刻想追问；"
        "action_conflict 眼前目标、有效阻力与双向代价是否咬合；"
        "reader_promise 主要体验与持续追读问题是否具体；character_choice 主角是否会"
        "不断面对正常聪明人也难解的选择；scene_generation 五个场面是否具体、彼此不同"
        "且由同一项目自然生出；promise_survival 开局异常首次兑现后是否仍持续产出核心"
        "承诺，若次数会耗尽或谜底揭开后只能换主线不得超过4分；deformable_loop 前轮后果"
        "是否会改变下一轮，而非接一单"
        "再用同一能力；post_reveal_engine 三个揭晓后场面是否仍兑现同一承诺、使用不同"
        "行动并产生积累，若只是继续查同一谜底或项目卡临时新增另一条主线不得超过4分；"
        "genre_fidelity 是否兑现题材。声称能写500章不加分。若五个场面"
        "只是换人换地，scene_generation和deformable_loop不得超过4分。只输出JSON："
        '{"seed_fidelity":0-10,"freshness":0-10,"click_seed":0-10,'
        '"action_conflict":0-10,'
        '"reader_promise":0-10,"character_choice":0-10,'
        '"scene_generation":0-10,"promise_survival":0-10,'
        '"deformable_loop":0-10,"post_reveal_engine":0-10,"genre_fidelity":0-10,'
        f'{intent_schema}'
        '"reason":"40字内"}'
    )
    return system, user


def _build_engine_batch_judge_messages(
    *,
    cards: list[tuple[str, str, dict[str, Any]]],
    genre: str,
    sub_genre: str,
    chapter_count: int,
    intent_axes: tuple[tuple[str, str], ...] = (),
) -> tuple[str, str]:
    """Judge several premise cards in one independent call to avoid serial latency."""

    intent_text, intent_schema = _render_intent_axis_schema(intent_axes)
    system = (
        "你是极其苛刻的长篇小说选题编辑。批量独立审项目卡，不替它们补设定。"
        "字段写满、解释很长或声称能写500章不能加分。只输出JSON。"
    )
    rows = [
        {"index": index, "lane": lane, "seed": seed, "card": card}
        for index, (lane, seed, card) in enumerate(cards)
    ]
    user = (
        f"题材={genre}（{sub_genre}）；目标={chapter_count}章\n"
        f"候选={json.dumps(rows, ensure_ascii=False)}\n\n"
        f"{intent_text}"
        "逐项评分0-10：seed_fidelity保留原始主角/异常/关系；freshness核心组合新鲜；"
        "click_seed故事胚子想点；action_conflict目标阻力与自然后果咬合；reader_promise"
        "持续体验具体；character_choice能持续产生聪明人的两难；scene_generation五个"
        "场面不同且由同一项目生出；promise_survival开局兑现后承诺仍在；"
        "deformable_loop前轮后果改变下一轮；post_reveal_engine三个揭晓后场面仍兑现"
        "同一承诺、使用不同动作并留下积累；genre_fidelity由目标题材原生行动与资源"
        "成立。替换原始事实、一次性谜底、接单换皮、继续查同一真相、临时换新主线，"
        "对应轴不得超过4分。只输出JSON：{\"verdicts\":[{\"index\":0,"
        "\"seed_fidelity\":0-10,\"freshness\":0-10,\"click_seed\":0-10,"
        "\"action_conflict\":0-10,\"reader_promise\":0-10,"
        "\"character_choice\":0-10,\"scene_generation\":0-10,"
        "\"promise_survival\":0-10,\"deformable_loop\":0-10,"
        "\"post_reveal_engine\":0-10,\"genre_fidelity\":0-10,"
        f"{intent_schema}"
        "\"reason\":\"40字内\"}]}"
    )
    return system, user


def _premise_card_audit(card: dict[str, Any]) -> list[str]:
    """Deterministic completeness check before a model can rationalize a blank card."""

    missing: list[str] = []
    for key in (
        "protagonist_identity",
        "protagonist_private_desire",
        "protagonist_flaw",
        "core_abnormality",
        "current_goal",
        "effective_resistance",
        "failure_cost",
        "success_cost",
        "irreversible_change",
        "reader_promise",
        "difference_point",
        "deformable_loop",
        "opening_crisis",
        "emotional_promise",
    ):
        if not str(card.get(key) or "").strip():
            missing.append(key)
    for key, minimum in (
        ("scene_seeds", 5),
        ("post_reveal_scene_seeds", 3),
        ("expansion_axes", 3),
        ("opposing_ecology", 2),
    ):
        value = card.get(key)
        valid_items = (
            [item for item in value if isinstance(item, str) and item.strip()]
            if isinstance(value, list)
            else []
        )
        if (
            not isinstance(value, list)
            or len(valid_items) < minimum
            or len(valid_items) != len(value)
        ):
            missing.append(key)
    return missing


_STORY_LAYER_AXES = (
    "s1_wants_aggression",
    "s2_stakes_upside",
    "s3_exclusivity",
    "s4_promise_survival",
    "s5_three_second_pitch",
)


def _build_story_layer_judge_messages(
    card: dict[str, Any],
    *,
    genre: str,
    sub_genre: str,
    audience_orientation: str = "",
) -> tuple[str, str]:
    """故事层判卡（P0，2026-08-18 接线）。

    背景：概念层判据只看「概念一句话」（click 轴原文），项目卡展开层的
    wants/stakes/系列引擎**零业务判据**——《丑石》的防守型欲望、两头输赌注、
    金手指自我稀释全部漏过，成书后被用户判差；下一本直接被榜单达标线拦截
    （简介 78<80，模拟读者 0/3 会点，划走原因全是「看不懂/不够爽」）。
    判据来源=爽文方法论 v2 的读者三段律与分档表，写成结构判定、**不种词**；
    输出强制逐项证据引文（裸分数=注意力稀释，hook_pull_judge 46 次榜单书
    零冤案用的正是引文模式）。零杀权：结果只用于一次带方向的重展开与留痕，
    **不淘汰任何卡**。
    """

    subset = {
        key: card.get(key)
        for key in (
            "protagonist_identity",
            "protagonist_private_desire",
            "protagonist_flaw",
            "current_goal",
            "core_abnormality",
            "deformable_loop",
            "failure_cost",
            "success_cost",
            "reader_promise",
            "emotional_promise",
            "opening_crisis",
            "difference_point",
        )
    }
    audience_line = (
        f"目标读者：{audience_orientation}频主流读者。"
        if str(audience_orientation or "").strip()
        else ""
    )
    system = (
        "你是商业长篇选题的故事层审稿人，只审项目卡文本本身。"
        "每一项判定都必须引用卡片原文作证据；按各项的引文规则执行，"
        "引不出规定的证据就按该项规则判否。只输出JSON。"
    )
    user = (
        f"{audience_line}题材：{genre}（{sub_genre}）\n"
        f"【项目卡】{json.dumps(subset, ensure_ascii=False)}\n\n"
        "五项判定（每项给 pass 布尔值 + 规定的引文字段）：\n"
        "1. s1_wants_aggression 欲望攻性：protagonist_private_desire 与 current_goal 里，"
        "是否至少有一件是『要拿到此刻还不属于他的东西』？quote=逐字引出那句话。"
        "注意：挣回颜面、抵清欠款、洗清冤屈这类**恢复原状**的目标只是回到零，"
        "不算拿到新东西；但『连本带利夺回并让对方付出代价』越过了原状线，算。"
        "引不出（全是保住/守住/不被怎样/恢复原状）→ pass=false。\n"
        "2. s2_stakes_upside 赌注有赢面：主角若达成目标，净得是否为正？"
        "win_quote=引出『赢了得到什么』的原句，cost_quote=引出 success_cost 原句；"
        "若成功的代价吞掉或反超收益（赢了也是灾难）→ pass=false。\n"
        "3. s3_exclusivity 能力排他：core_abnormality 与 deformable_loop 里，"
        "主角核心能力是否保持排他？若机制描述能力会扩散/复制/传染/转移给他人"
        "→ pass=false，quote=该句。\n"
        "4. s4_promise_survival 承诺可持续：deformable_loop 能否持续产生"
        "同类但不同的局面？依赖一次性谜底、会耗尽的次数、或同一动作原样重复"
        "→ pass=false，quote=依据句。\n"
        "5. s5_three_second_pitch 三秒可述：用一句大白话写出『主角接下来要赢什么』，"
        "这句话里不得出现卡片里的生造名词；写得出 → pass=true 并给 pitch 字段；"
        "写不出或必须先解释设定才能说清 → pass=false。\n\n"
        "输出JSON：{\"s1_wants_aggression\":{\"pass\":true,\"quote\":\"…\"},"
        "\"s2_stakes_upside\":{\"pass\":true,\"win_quote\":\"…\",\"cost_quote\":\"…\"},"
        "\"s3_exclusivity\":{\"pass\":true,\"quote\":\"…\"},"
        "\"s4_promise_survival\":{\"pass\":true,\"quote\":\"…\"},"
        "\"s5_three_second_pitch\":{\"pass\":true,\"pitch\":\"…\"},"
        "\"revise_direction\":\"任一false时给修正方向，只给方向不给措辞，≤40字；全过则空串\"}"
    )
    return system, user


def _parse_story_layer_verdict(
    raw: str,
    axes: tuple[str, ...] = _STORY_LAYER_AXES,
) -> dict[str, Any] | None:
    payload = _parse_json_object(raw)
    if payload is None:
        return None
    verdict: dict[str, Any] = {"axes": {}, "failed_axes": []}
    for axis in axes:
        entry = payload.get(axis)
        if not isinstance(entry, dict):
            # 判官漏轴不视为 fail（无杀权精神：缺失≠定罪），只标 unknown
            verdict["axes"][axis] = {"pass": None}
            continue
        passed = entry.get("pass")
        verdict["axes"][axis] = {
            k: v for k, v in entry.items() if isinstance(k, str)
        }
        if passed is False:
            verdict["failed_axes"].append(axis)
    verdict["revise_direction"] = str(payload.get("revise_direction") or "").strip()
    return verdict


# ── E 层编辑判官（2026-08-18 榜单市场调研蒸馏，docs/concept-quality-system-
# redesign-20260818.md）────────────────────────────────────────────────────
# 判据来源：70 本在榜男频逐本细读。定罪本尊：《九姓井口只认我》按 12 条判据
# 1.5/12（榜单头部 8-11），系统性缺失通行证/规则/循环/爽型四大件——全链没有
# 任何判官在判这些。E 轴审「展开后的设定质量」，与 S 轴（故事结构）互补，
# 分开调用（缺陷清单塞进同一评分调用=注意力稀释，已定案）。
_EXPANSION_EDITOR_AXES: tuple[str, ...] = (
    "e1_rule_demonstrable",
    "e2_constraint_plot",
    "e3_paradox_engine",
    "e4_world_rule_first",
    "e5_witness_slot",
    "e6_goal_quantified",
)
# 只有承重轴参与定罪（e3/e6 是加分项：40% 在榜执行流没有它们照样活；
# 拿加分项定罪会把整个池打死）。e4 是「无法自圆其说」的形式化判定。
_EXPANSION_EDITOR_CONVICTION_AXES: frozenset[str] = frozenset(
    {"e1_rule_demonstrable", "e2_constraint_plot", "e4_world_rule_first"}
)


def _build_expansion_editor_messages(
    card: dict[str, Any],
    *,
    genre: str,
    sub_genre: str,
) -> tuple[str, str]:
    """编辑视角设定质量判官——判据锚定真实榜单书（判官读例证≠写手读例证，
    例证只进判官 prompt、绝不进生成 prompt，不违种词铁律）。"""

    subset = {
        key: card.get(key)
        for key in (
            "protagonist_identity",
            "core_abnormality",
            "current_goal",
            "deformable_loop",
            "failure_cost",
            "success_cost",
            "reader_promise",
            "opening_crisis",
            "difference_point",
            "scene_seeds",
        )
    }
    system = (
        "你是网文平台资深选题编辑，用在榜书的标准审一张项目卡的设定质量。"
        "每项判定必须按规定给出引文或改写句作证据；给不出就按该项规则判否。"
        "只输出JSON。"
    )
    user = (
        f"题材：{genre}（{sub_genre}）\n"
        f"【项目卡】{json.dumps(subset, ensure_ascii=False)}\n\n"
        "六项判定（每项给 pass 布尔值 + 规定的证据字段）：\n"
        "1. e1_rule_demonstrable 规则可演示：核心异常/金手指能否用第一章内"
        "一个≤500字的事件演示，且演示有明确的输入→输出差"
        "（参照《聚宝仙盆》：丹药放入→一日后两颗且升品）？quote=引出卡里"
        "写明输入输出差的句子；引不出（异常只有名词或氛围、演示无收益差）"
        "→ pass=false。\n"
        "2. e2_constraint_plot 限制生剧情：核心能力是否带至少一条限制/代价，"
        "且从这条限制能推出≥3个不同的情节？plots=列出这3个情节各一句话"
        "（参照《每天六千万，只能在县城花》：限制条款本身就是全书剧情）；"
        "列不满3个 → pass=false。注意「独占/别人用不了」不是限制。\n"
        "3. e3_paradox_engine 悖论引擎（加分项）：设定是否含『越X越Y』"
        "反直觉引擎或可叠加复利（参照《洪武苟神》越不想升官越被当肱骨）？"
        "quote=该机制句。没有 → pass=false（不定罪，只记录）。\n"
        "4. e4_world_rule_first 世界规则先行：能否把设定改写成两句——"
        "第一句是**不含主角**的世界规则，第二句是主角的例外位置"
        "（参照《凡骨》：世界=灵骨四品方可修行/主角=凡骨誓登仙）？"
        "world_rule=你写出的第一句，exception=第二句；写不出不含主角的"
        "世界规则句（异常凭空只属于主角、在世界体系里没有位置）"
        "→ pass=false。这是『无法自圆其说』的形式化判定。\n"
        "5. e5_witness_slot 见证槽位：卡里是否指明了**谁**将目睹主角的"
        "收益兑现（具名角色/群体）？quote=该句；只有主角自知自爽 → pass=false。\n"
        "6. e6_goal_quantified 目标数值化（加分项）：是否有带数字的目标或"
        "可核验终点？quote=该句。没有 → pass=false（不定罪，只记录）。\n\n"
        "输出JSON：{\"e1_rule_demonstrable\":{\"pass\":true,\"quote\":\"…\"},"
        "\"e2_constraint_plot\":{\"pass\":true,\"plots\":[\"…\",\"…\",\"…\"]},"
        "\"e3_paradox_engine\":{\"pass\":false,\"quote\":\"\"},"
        "\"e4_world_rule_first\":{\"pass\":true,\"world_rule\":\"…\",\"exception\":\"…\"},"
        "\"e5_witness_slot\":{\"pass\":true,\"quote\":\"…\"},"
        "\"e6_goal_quantified\":{\"pass\":false,\"quote\":\"\"},"
        "\"revise_direction\":\"e1/e2/e4/e5任一false时给修正方向，"
        "只给方向不给措辞，≤40字；否则空串\"}"
    )
    return system, user


def _build_engine_kernel_repair_messages(
    *,
    genre: str,
    sub_genre: str,
    lane: str,
    chapter_count: int,
    seed_concept: str,
    card: dict[str, Any],
    missing_fields: list[str],
    seed_support: dict[str, Any] | None = None,
    audience_orientation: str = "",
    cost_style: str = "standard",
    tone_preference: str = "",
    effect_skills: tuple[str, ...] | list[str] = (),
    creation_intent_block: str = "",
) -> tuple[str, str]:
    """Repair malformed structure once without changing the premise itself.

    "Without changing the premise" includes who the book is FOR: this rebuild
    used to drop ``audience_orientation``, so any card that needed JSON repair
    regenerated with no channel anchor — a 男频 request came back female-lead,
    the judge killed it, and the tournament went dry (2026-07-24,
    custom-xuanhuan-1784899694, attempt 2's second kernel call was exactly this
    repair). Every rebuilt prompt must carry the same anchors as the original.
    """

    system, base = _build_engine_kernel_messages(
        genre=genre,
        sub_genre=sub_genre,
        lane=lane,
        chapter_count=chapter_count,
        seed_concept=seed_concept,
        seed_support=seed_support,
        audience_orientation=audience_orientation,
        cost_style=cost_style,
        tone_preference=tone_preference,
        effect_skills=effect_skills,
        creation_intent_block=creation_intent_block,
    )
    repair = (
        "\n\n【PREMISE_CARD_REPAIR】上次项目卡的JSON结构不完整。"
        "这不是重想故事的机会；人物、异常、关系、目标、阻力和后果全部冻结。\n"
        f"缺失或类型错误字段：{'/'.join(missing_fields)}\n"
        f"上次项目卡：{json.dumps(card, ensure_ascii=False)}\n"
        "返回一份完整的新JSON对象，必须含模板中的全部字段。所有数组只能直接包含"
        "非空字符串，禁止把后续字段嵌进scene_seeds或其他数组；不要输出说明文字。"
    )
    return system, base + repair


def _build_hook_copy_repair_messages(
    *, candidate: ConceptCandidate, feedback: str
) -> tuple[str, str]:
    """Repair only the one-line copy; every story fact remains frozen."""

    system = (
        "你是小说文案编辑。只修一句话的清晰度和点击欲，不能新增人物、能力、代价、"
        "对手或主线。只输出JSON。"
    )
    user = (
        f"原句：{candidate.concept}\n"
        f"主角：{candidate.protagonist_identity}\n异常：{candidate.core_abnormality}\n"
        f"开局：{candidate.opening_crisis}\n机制：{candidate.mechanism}\n"
        f"裁判反馈：{feedback}\n"
        "保留原故事事实，改成35-80字单句。优先使用普通动词和具体名词，删除抽象解释、"
        "多层修饰和读者必须回读的指代。只输出JSON：{\"concept\":\"修复后一句话\"}"
    )
    return system, user


def _build_seriality_repair_messages(
    *,
    candidate: ConceptCandidate,
    genre: str,
    chapter_count: int,
    feedback: str,
) -> tuple[str, str]:
    system, base = _build_seriality_messages(
        candidate=candidate,
        genre=genre,
        chapter_count=chapter_count,
    )
    current = {
        "core_promise_invariant": candidate.core_promise_invariant,
        "constraint_ladder": list(candidate.constraint_ladder),
        "role_ladder": list(candidate.role_ladder),
        "world_ladder": list(candidate.world_ladder),
        "repeatable_story_unit": candidate.repeatable_story_unit,
        "unit_families": list(candidate.unit_families),
        "progress_bar": candidate.progress_bar,
        "unit_frequency": candidate.unit_frequency,
        "unit_count_estimate": candidate.unit_count_estimate,
        "renewal_sources": list(candidate.renewal_sources),
        "accumulation_tracks": list(candidate.accumulation_tracks),
        "phase_transitions": list(candidate.phase_transitions),
        "opposing_ecology": list(candidate.opposing_ecology),
        "mystery_ladder": list(candidate.question_ladder),
        "ch50": candidate.ch50,
        "endgame_direction": candidate.endgame_direction,
    }
    repair = (
        "\n\n【上次承载证明未通过，只修证明，严禁修改冻结故事种子】\n"
        f"失败反馈：{feedback}\n"
        f"上次证明：{json.dumps(current, ensure_ascii=False)}\n"
        "针对反馈重写完整JSON。优先重建三级发动机：让主角当前行动内生地产生下一轮"
        "微单元，让对手学习后改变题型，再由多个微单元汇成产品/案件/关系成果弧。"
        "不得只补一句，也不得通过虚报单元数量、拉长年份、依赖外部无限投喂，或把"
        "后半部换成另一种故事来过门。"
    )
    return system, base + repair


# 双层地板(2026-07-17,八轮真机取证):判官打分天然聚在6-7,八轴同时≥7.0的联合
# 通过率趋近于零(30+候选零晋级;mech 9.0/click 8.0 的候选死于 plain 单轴差1分),
# 逐轴下调是打地鼠。灾难线以下任何一轴=一票死(拦 genre 2.0/plain 3.0 真灾难);
# 灾难线以上容忍恰好 1 根软轴未达标——下游 logline/persona 门仍是终审。
_FLOOR_CATASTROPHE = 5.0
_PREDICTABLE_CATASTROPHE = 7.5
_SOFT_MISS_ALLOWANCE = 1

_FLOOR_AXIS_LABELS: tuple[tuple[str, str, float], ...] = (
    ("freshness", "新颖度", 7.0),
    ("click", "想点欲", 7.5),
    ("character_logic", "人物决策", 7.0),
    ("mechanism_causality", "机制因果", 7.0),
    ("genre_fidelity", "题材保真", 7.0),
    ("plain_language", "大白话", 7.0),
    ("story_motion", "故事运动", 7.5),
)


_PROMPT_FAMILY_VERSION = "v2"


def prompt_version_stamp(system_prompt: str, user_prompt: str) -> str:
    """``v2+<内容指纹>``——版本号随 prompt 内容变化。

    2026-08-25：这里原本是写死的 ``prompt_version="v2"``。当天真机上，
    一句话创意池 prompt 做了 v2d 重构（七条铁律砍到三条、示例词全部除名），
    ``llm_runs`` 里报的**仍然是 v2**——于是「这本书到底用的新 prompt 还是旧
    prompt」在数据层根本查不出来，只能去比文件 mtime 和容器启动时间。

    一个内容变了却不变的版本号，比没有版本号更坏：它让部署核查得出错误结论。
    指纹取 system+user 的 sha256 前 8 位，同 prompt 恒定、改一个字就变。
    """

    digest = hashlib.sha256(
        f"{system_prompt}\x00{user_prompt}".encode("utf-8")
    ).hexdigest()[:8]
    return f"{_PROMPT_FAMILY_VERSION}+{digest}"


def seriality_stage_mode(
    chapter_count: int,
    cfg: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """追读性阶段跑成哪一档：``enforcing`` / ``advisory`` / ``skipped``。

    2026-08-25：此前是写死的 ``if chapter_count >= 200``。判官评的六条轴
    （renewability / escalation / anti_reset / coherence / promise_survival /
    unit_density）全是「读者要不要追下去」的判据，而 200 章的书在真机上几乎
    不存在——于是它对所有正常长度的书完全空转，``seriality_judge`` 恒为 ``{}``，
    「评了没发现」与「压根没跑」不可区分。

    分档而不是一刀放开：≥ ``seriality_min_chapters`` 维持既有行为（判官带杀权）；
    低于它但 ≥ ``seriality_advisory_min_chapters`` 时只跑判官**留痕、不发否决**。
    config 里 2026-07-17 记着收紧概念层阈值 → 淘汰赛干涸 → 回落保底概念的教训，
    而本仓库对新检测器的规矩就是「只挣重生和留痕，不发杀权」。
    """

    cfg = cfg or {}
    full = int(cfg.get("seriality_min_chapters", 200))
    advisory = int(cfg.get("seriality_advisory_min_chapters", 8))
    # 2026-08-28：杀权默认关闭，因为这个判官已被实测证明与现实**反相关**。
    # 证据（scripts/seriality_judge_validation.py，两轮独立跑）：拿真榜单书
    # 走生产展开 + 生产判官打分，
    #     强侧 ≥500 章且 ≥30 万在读（确实写下去了）
    #     弱侧 <120 章且 <2 万在读（确实没写下去）
    #     排序能力 AUC = 0.37 / 0.38（0.5 才是抛硬币，低于 0.5 = 判反了）
    #     六轴无一例外全为负；强侧判失败率 86% / 92%
    # 也就是说：它给真跑到 500 章的书打的分，比给断更扑街书打的还低，而
    # 它在 ≥200 章时握有否决权——真机日志里已能看到它的后果：
    #     "concept tournament: no seriality-qualified finalists"
    # 整轮候选被清空，随后回落保底概念（比任何被拒候选都差，见 2026-07-17）。
    #
    # 按本仓库既定规矩（新检测器只挣重生和留痕，不发杀权），一个反相关的
    # 判官更不该握杀权。改为默认只留痕：judge 照常跑、判词照常入回执，
    # 但不否决候选。config 显式置 True 才恢复杀权——等它先过效度线。
    #
    # 不删判官、不改它的 prompt：信息不是它一个人弄丢的（展开层把成对判官
    # 在同批书上的 sustain 区分力从 80% 削到 62%），两层要分别修，
    # 而这一步只做「止血」——把已证明会伤人的那把刀收起来。
    enforcing_allowed = bool(cfg.get("seriality_enforcing_enabled", False))
    if chapter_count >= full and enforcing_allowed:
        mode = "enforcing"
    elif chapter_count >= advisory:
        mode = "advisory"
    else:
        mode = "skipped"
    return mode, {
        "mode": mode,
        "chapter_count": chapter_count,
        "enforcing_min_chapters": full,
        "advisory_min_chapters": advisory,
        # 回执要能区分「本可杀但被收了刀」和「本来就够不着杀权档」——
        # 否则下次又要重新查一遍它到底有没有开过火。
        "enforcing_allowed": enforcing_allowed,
        "veto_withheld": chapter_count >= full and not enforcing_allowed,
    }


def _hard_floor_failed_axes(
    scores: dict[str, float],
    hard_floors: dict[str, Any],
) -> list[str]:
    """Two-tier floor verdict. Returns the axes that make the candidate fail
    (empty list = qualified). Catastrophes always fail; soft misses fail only
    when they exceed the configured allowance, in which case every miss is
    named so the retry feedback / near-miss selector sees the full picture."""

    catastrophe_floor = float(
        hard_floors.get("catastrophe_floor", _FLOOR_CATASTROPHE)
    )
    predictable_catastrophe = float(
        hard_floors.get(
            "predictable_catastrophe",
            _PREDICTABLE_CATASTROPHE,
        )
    )
    soft_miss_allowance = max(
        0,
        int(hard_floors.get("soft_miss_allowance", _SOFT_MISS_ALLOWANCE)),
    )
    catastrophes: list[str] = []
    soft: list[str] = []
    for key, label, default in _FLOOR_AXIS_LABELS:
        value = float(scores.get(key, 0.0))
        if value < catastrophe_floor:
            catastrophes.append(label)
        elif value < float(hard_floors.get(key, default)):
            soft.append(label)
    predictable = float(scores.get("predictable", 10.0))
    if predictable > predictable_catastrophe:
        catastrophes.append("可预测性")
    elif predictable > float(hard_floors.get("predictable_max", 5.5)):
        soft.append("可预测性")
    if catastrophes:
        return catastrophes + soft
    if len(soft) > soft_miss_allowance:
        return soft
    return []


def _build_judge_messages(
    *,
    candidate: ConceptCandidate,
    genre: str,
    sub_genre: str = "",
    references: list[dict[str, str]],
    audience_orientation: str = "",
) -> tuple[str, str]:
    ref_lines = "\n".join(
        f"- 《{r.get('title', '')}》：{str(r.get('blurb', '')).strip()[:80]}"
        for r in references[:4]
    ) or "（无参照）"
    # 频道读者定义在 click 分诞生处：中性编辑口味放过的黑话概念,下游 persona
    # 判官 0/3 否决时已无重试可救(真机第6轮)。click/plain_language 必须按频道
    # 主流读者判。
    audience_line = (
        f"【目标读者】{audience_orientation}主流读者：三秒看不懂主角是谁、要干嘛、"
        "爽在哪就划走；生造名词堆叠、设定绕、'又是X又是Y'式复杂开局＝直接不点。"
        "click 与 plain_language 两轴按这个读者判，不按编辑口味判。\n"
        if str(audience_orientation or "").strip()
        else ""
    )
    system = (
        "你是挑剔的网文榜单主编，每天毙掉几十个平庸选题。你只回答 JSON，"
        "评分严格：见过类似的就是不新鲜，能猜到后续就是可预测，不想点就是不想点。"
    )
    genre_label = f"{genre}（{sub_genre}）" if sub_genre.strip() else genre
    user = (
        f"{audience_line}"
        f"【待评概念】（{genre_label}）\n"
        f"概念：{candidate.concept}\n机制：{candidate.mechanism}\n"
        f"认知缺口：{candidate.hook_question}\n\n"
        f"主角：{candidate.protagonist_identity}；私人欲望：{candidate.protagonist_private_desire}\n"
        f"第一危机：{candidate.opening_crisis}\n对手：{candidate.opponent_system}\n"
        f"决策证明：{candidate.decision_proof}\n情绪承诺：{candidate.emotional_promise}\n\n"
        f"【榜单在售参照（对撞用）】\n{ref_lines}\n\n"
        "八轴打分（0-10，整数或一位小数）：\n"
        "1. freshness 新颖度：与参照集和你见过的全部网文对撞，这个概念的核心组合"
        "有没有人写过？换皮不算新。这里只评‘概念：’这一行的标志性规则或发现，"
        "后附机制写得再完整也不能救分。若可压缩成‘做一个产品，资本再封杀一次’、"
        "‘接一个任务，再解决一次’等平行重复，新颖度不得超过6分。\n"
        "2. click 想点欲：只看这个概念一句话，目标读者3秒内想不想点进去？\n"
        "3. predictable 可预测性：你能不能从这一句直接猜完主要对手、阶段升级、关键"
        "反转和终局答案？能猜完=高分=坏事。注意：读者能看懂主角会反复做什么是清晰"
        "的连载承诺，不是可预测缺陷；只有后续变量、升级方式和结局也能被自动补全才扣分。\n"
        "能力类型见过不等于后续可预测，那属于freshness；本轴禁止因题材相似或前作影子"
        "重复扣分。校准：一个主角能听见尚未完成的剑招，读者虽能看懂能力，却无法直接"
        "猜出谁在利用这件事、为何必须由他行动、如何破局和终局，predictable应为2-4分；"
        "只有‘每接一案解决一案，最后打败幕后组织’才应为7-10分。\n"
        "4. character_logic 人物决策可信度：站在主角第一人称，他是否有充分理由进入"
        "这场故事并持续行动？如果必须靠降智、忘记常识、强塞倒计时或无因果代价才能推进，"
        "本项不得超过4分。\n"
        "5. mechanism_causality 机制因果：能力/异常发现、主角持续行动、收益与对手反制"
        "是否因果咬合？不要求每个好故事都必须有自损代价；没有显式代价不得扣分。只有"
        "候选主动声明代价时，才检查它是否由行动自然改变名额、资源、关系、证据或制度而"
        "产生。随机折寿、失忆、器官衰竭等外置惩罚不得超过3分。\n"
        f"6. genre_fidelity 题材保真：目标题材是【{genre_label}】。必须同时按大类和子类型"
        "判断；硬核科技、创业、职业操作等若属于子类型核心承诺，应当加分，不能因为像"
        "职业文而扣分。只有候选实质换成了别的类型才低分。尤其警惕把现代职业流程搬进"
        "超凡世界后只替换名词：必须说明该流程在本世界为何必要、原生常识为什么不能替代、"
        "主角为何拥有不可替代的行动入口。若依赖任何外部职业流程，必须先证明它为何在"
        "本题材世界不可替代、为何不是贴皮；缺少这些因果而直接套用现代报告/数据库/评级/"
        "客户流程，genre_fidelity不得超过3分。\n"
        + render_plain_language_judge_rule()
        + "8. story_motion 故事运动：一句话是否给出会持续产出故事的核心承诺。可以是"
        "主角反复执行的标志性行动，也可以是‘独特规则+一个具体到令人不安的反常实例’，"
        "只要读者自然追问下一次会发生什么；不强制把对手塞进一句话，对手理性改看"
        "opponent_system。若只是俏皮反差、静态世界设定、身份+秘密，必须依赖后文才知道"
        "故事怎么动，本项不得超过4分。若只是换对象平行重复、局面不积累，不得超过7分。\n"
        + "9. protagonist_agency 主角能动性：这一句话的推进，是主角**做出取舍**"
        "带来的，还是外部压力施加在他身上、他只做出反应？判据是能否指出他放弃了"
        "什么、在两条都要付代价的路里选了哪条。若整句只有别人对他做了什么、"
        "他被迫应对，本项不得超过4分；若他有行动但没有取舍、每一步都是唯一选项，"
        "不得超过7分。\n"
        '只输出 JSON：{"freshness": 0-10, "click": 0-10, "predictable": 0-10, '
        '"character_logic": 0-10, '
        '"mechanism_causality": 0-10, '
        '"genre_fidelity": 0-10, "plain_language": 0-10, "story_motion": 0-10, '
        '"protagonist_agency": 0-10, '
        '"reason": "20字内评语"}'
    )
    return system, user


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        payload = None
        if start != -1 and end != -1 and end > start:
            try:
                payload = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                payload = None
    if not isinstance(payload, dict) and text:
        try:
            from json_repair import repair_json

            repaired = repair_json(text, return_objects=True)
            payload = repaired if isinstance(repaired, dict) else None
        except Exception:
            payload = None
    return payload if isinstance(payload, dict) else None


def _parse_complete_axis_scores(
    verdict: dict[str, Any] | None,
    axes: tuple[str, ...],
) -> tuple[dict[str, float] | None, list[str]]:
    """Parse a judge verdict without turning omitted axes into fake zero scores."""

    if not isinstance(verdict, dict):
        return None, list(axes)
    scores: dict[str, float] = {}
    missing: list[str] = []
    for axis in axes:
        value = verdict.get(axis)
        try:
            score = float(value)
        except (TypeError, ValueError):
            missing.append(axis)
            continue
        if not math.isfinite(score):
            missing.append(axis)
            continue
        scores[axis] = max(0.0, min(10.0, score))
    if missing:
        return None, missing
    return scores, []


def _parse_candidate(raw: str, dimension: str) -> ConceptCandidate | None:
    payload = _parse_json_object(raw)
    if payload is None:
        return None
    ladder_raw = payload.get("question_ladder")
    ladder = (
        tuple(str(x).strip() for x in ladder_raw if str(x).strip())
        if isinstance(ladder_raw, list)
        else ()
    )
    def _tuple_field(key: str) -> tuple[str, ...]:
        value = payload.get(key)
        return (
            tuple(str(x).strip() for x in value if str(x).strip())
            if isinstance(value, list)
            else ()
        )

    def _int_field(key: str) -> int:
        try:
            return max(0, int(payload.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    candidate = ConceptCandidate(
        dimension=dimension,
        concept=str(payload.get("concept") or "").strip(),
        mechanism=str(payload.get("mechanism") or "").strip(),
        hook_question=str(payload.get("hook_question") or "").strip(),
        protagonist_identity=str(payload.get("protagonist_identity") or "").strip(),
        protagonist_private_desire=str(payload.get("protagonist_private_desire") or "").strip(),
        protagonist_flaw=str(payload.get("protagonist_flaw") or "").strip(),
        core_abnormality=str(payload.get("core_abnormality") or "").strip(),
        opening_crisis=str(payload.get("opening_crisis") or "").strip(),
        opponent_system=str(payload.get("opponent_system") or "").strip(),
        decision_proof=str(payload.get("decision_proof") or "").strip(),
        emotional_promise=str(payload.get("emotional_promise") or "").strip(),
        core_promise_invariant=str(payload.get("core_promise_invariant") or "").strip(),
        unit_families=_tuple_field("unit_families"),
        repeatable_story_unit=str(
            payload.get("repeatable_story_unit")
            or (
                payload.get("mechanism")
                if payload.get("unit_frequency") or payload.get("phase_transitions")
                else ""
            )
            or ""
        ).strip(),
        progress_bar=str(payload.get("progress_bar") or "").strip(),
        unit_frequency=str(payload.get("unit_frequency") or "").strip(),
        unit_count_estimate=_int_field("unit_count_estimate"),
        question_ladder=ladder,
        ch50=str(payload.get("ch50") or "").strip(),
        renewal_sources=_tuple_field("renewal_sources"),
        accumulation_tracks=_tuple_field("accumulation_tracks"),
        phase_transitions=_tuple_field("phase_transitions"),
        opposing_ecology=_tuple_field("opposing_ecology"),
        endgame_direction=str(payload.get("endgame_direction") or "").strip(),
    )
    return candidate if candidate.concept else None


def _parse_hook_variants(raw: str, dimension: str) -> list[ConceptCandidate]:
    """Parse three hook angles sharing one frozen premise card."""

    payload = _parse_json_object(raw)
    if payload is None:
        return []
    hooks = payload.get("hooks")
    if not isinstance(hooks, list):
        candidate = _parse_candidate(raw, dimension)
        return [candidate] if candidate is not None else []
    variants: list[ConceptCandidate] = []
    for index, hook in enumerate(hooks[:3]):
        if not isinstance(hook, dict):
            continue
        concept = str(hook.get("concept") or "").strip()
        if not concept:
            continue
        angle = str(hook.get("angle") or f"v{index + 1}").strip()
        variant_payload = dict(payload)
        variant_payload.pop("hooks", None)
        variant_payload["concept"] = concept
        candidate = _parse_candidate(
            json.dumps(variant_payload, ensure_ascii=False),
            f"{dimension}:{angle}",
        )
        if candidate is not None:
            variants.append(candidate)
    return variants


def _apply_seriality_payload(
    candidate: ConceptCandidate, raw: str
) -> ConceptCandidate | None:
    """Attach proof fields while keeping every frozen story-seed field unchanged."""

    from dataclasses import replace

    payload = _parse_json_object(raw)
    if payload is None:
        return None

    def _tuple_field(key: str) -> tuple[str, ...]:
        value = payload.get(key)
        return (
            tuple(str(item).strip() for item in value if str(item).strip())
            if isinstance(value, list)
            else ()
        )

    def _int_field(key: str) -> int:
        try:
            return max(0, int(payload.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    repeatable_unit = str(payload.get("repeatable_story_unit") or "").strip()
    return replace(
        candidate,
        repeatable_story_unit=repeatable_unit,
        core_promise_invariant=str(payload.get("core_promise_invariant") or "").strip(),
        constraint_ladder=_tuple_field("constraint_ladder"),
        role_ladder=_tuple_field("role_ladder"),
        world_ladder=_tuple_field("world_ladder"),
        unit_families=_tuple_field("unit_families"),
        progress_bar=str(payload.get("progress_bar") or "").strip(),
        unit_frequency=str(payload.get("unit_frequency") or "").strip(),
        unit_count_estimate=_int_field("unit_count_estimate"),
        question_ladder=_tuple_field("mystery_ladder"),
        ch50=str(payload.get("ch50") or "").strip(),
        renewal_sources=_tuple_field("renewal_sources"),
        accumulation_tracks=_tuple_field("accumulation_tracks"),
        phase_transitions=_tuple_field("phase_transitions"),
        opposing_ecology=_tuple_field("opposing_ecology"),
        endgame_direction=str(payload.get("endgame_direction") or "").strip(),
    )


async def _default_generator(
    session: Any,
    settings: Any,
    *,
    template: str,
    max_tokens: int = 900,
    logical_role: str = "planner",
    model_catalog_key: str | None = None,
) -> GeneratorFn:
    async def _call(system_prompt: str, user_prompt: str) -> tuple[str, Any]:
        from bestseller.services.llm import LLMCompletionRequest, complete_text

        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role=logical_role,
                model_tier="strong",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response="{}",
                prompt_template=template,
                prompt_version=prompt_version_stamp(system_prompt, user_prompt),
                max_tokens_override=max_tokens,
                model_catalog_key=model_catalog_key,
                metadata={
                    "concept_tournament_stage": template,
                    "model_route": model_catalog_key or "role-default",
                },
            ),
        )
        return completion.content or "", completion.llm_run_id

    return _call


def _render_avoid_mechanisms_block(avoid_mechanisms: list[dict[str, Any]]) -> str:
    count = sum(1 for item in avoid_mechanisms if isinstance(item, dict))
    if not count:
        return ""
    return (
        f"【跨书差异化】已有{count}条同题材机制指纹将在生成后做程序化比对；"
        "旧书标题、前提、金手指和意象原文均不进入提示词。"
        "请让本书的作用原理、局面变化和持续冲突从当前用户设定独立生长。\n"
    )


def _prefer_ontology_clean(
    finalists: list[Any],
    genre_intent_contract: Any,
) -> list[Any]:
    """Prefer champions that already sit inside the chosen genre's ontology.

    Deliberately a *preference*, not a filter. If every finalist trips the
    ontology check, the whole list is returned unchanged: emptying the pool
    would turn a book that merely needs a nudge into a book that cannot be
    created at all, and this repo has already paid for gates that starve their
    own candidate pool.

    The downstream tripwire stays exactly as strict as before — this only stops
    the tournament from *volunteering* a champion that the tripwire will kill.
    """

    if genre_intent_contract is None or len(finalists) < 2:
        return finalists
    try:
        from bestseller.services.genre_intent_contract import (  # noqa: PLC0415
            detect_genre_native_ontology_violations,
        )
    except Exception:  # pragma: no cover — defensive
        return finalists

    clean: list[Any] = []
    drifted: list[tuple[Any, tuple[str, ...]]] = []
    for candidate in finalists:
        surface = " ".join(
            str(getattr(candidate, field, "") or "")
            for field in ("concept", "mechanism", "hook_question", "progress_bar")
        )
        try:
            hits = detect_genre_native_ontology_violations(
                surface, genre_intent_contract
            )
        except Exception:  # pragma: no cover — never fail the tournament
            hits = ()
        if hits:
            drifted.append((candidate, tuple(hits)))
        else:
            clean.append(candidate)

    if not clean:
        logger.info(
            "concept tournament: every finalist trips the genre ontology "
            "(%s); keeping the full pool rather than starving it",
            ", ".join(sorted({h for _, hs in drifted for h in hs})) or "?",
        )
        return finalists
    if drifted:
        logger.info(
            "concept tournament: demoted %d ontology-drifting finalist(s) (%s)",
            len(drifted),
            ", ".join(sorted({h for _, hs in drifted for h in hs})),
        )
    return clean


async def run_concept_tournament(
    session: Any,
    settings: Any,
    *,
    genre: str,
    sub_genre: str,
    chapter_count: int,
    avoid_mechanisms: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
    generator: GeneratorFn | None = None,
    judge: GeneratorFn | None = None,
    expander: GeneratorFn | None = None,
    seriality_judge: GeneratorFn | None = None,
    premise_judge: GeneratorFn | None = None,
    rng: random.Random | None = None,
    seed_concept: str = "",
    retry_feedback: str = "",
    audience_orientation: str = "",
    cost_style: str = "standard",
    tone_preference: str = "",
    effect_skills: tuple[str, ...] | list[str] = (),
    creation_intent_block: str = "",
    allow_debt_theme: bool | None = None,
    allow_death_theme: bool | None = None,
    genre_intent_contract: Any = None,
    market_competitors: Sequence[Mapping[str, Any]] = (),
) -> ConceptTournamentResult:
    """跑一轮概念淘汰赛。异常转成 winner=None，由调用方按目标篇幅决定是否阻断。

    四个模型工序均可注入（测试）；``rng`` 可注入固定维度抽样。

    ``genre_intent_contract`` lets the tournament prefer a champion that already
    lives inside the chosen genre's ontology. Without it the tournament was free
    to crown a concept that the fail-closed ontology tripwire at the end of
    conception would then kill — generator and acceptor disagreeing, with a whole
    conception burned in between (2026-08-05, 东方玄幻 request crowned an
    underworld-civil-servant KPI comedy and the book died at the last gate).
    """

    cfg = config if config is not None else load_concept_tournament_config()
    if not isinstance(cfg, dict) or not bool(cfg.get("enabled", True)):
        return ConceptTournamentResult()

    result = ConceptTournamentResult()
    try:
        banned = resolve_banned_cliches(genre, sub_genre, cfg)
        result.banned_cliches = banned
        n_candidates = max(2, int(cfg.get("n_candidates", 4)))
        candidate_prompt_mode = str(
            cfg.get("candidate_prompt_mode") or "current"
        ).strip().lower()
        pool = [str(d) for d in (cfg.get("dimension_pool") or []) if str(d).strip()]
        rand = rng if rng is not None else random.Random()
        # 有建书要求时，别把候选摊到框架自己的九条路线上。那是固定轮转，用户选
        # 轻松＋喜剧＋爽感也照样把世界规则／势力选择／资源分配塞进一半候选，产出
        # 必然沉重，判官再正确地判它们不喜剧、不想点（2026-07-30 实测：10 个候选
        # 只过 1 个，新颖度 9/10 低于地板）。让故事从要求里长出来，候选之间只在
        # 故事自身的维度上被要求不同——那是区分约束，不是题材指令。
        # 触发条件是「从零生成」，不是「有没有 brief」。只选一个题材时
        # _creation_intent_prompt_block 按它的无选择契约返回空串（那是对的：不替
        # 用户注入任何东西），但那恰恰是最不该让框架替用户决定故事写什么的时候，
        # 而它此前正好回落到九条路线——用户最初反复失败的场景就是这个。
        if candidate_prompt_mode in {"native_baseline", "engine_first"}:
            dimensions = [
                f"{_GROWTH_LANE_PREFIX}#"
                f"{_GROWTH_DIFFERENTIATION_AXES[index % len(_GROWTH_DIFFERENTIATION_AXES)]}"
                for index in range(n_candidates)
            ]
        elif seed_concept.strip():
            source_dimensions = (
                _TARGETED_REPAIR_DIMENSIONS
                if retry_feedback.strip()
                else _SEED_REFINEMENT_DIMENSIONS
            )
            dimensions = list(source_dimensions[:n_candidates])
        else:
            control_count = min(
                2, max(1, int(cfg.get("control_candidates", 2))), n_candidates - 1
            )
            hybrid_count = (
                min(max(1, n_candidates - control_count), len(pool)) if pool else 0
            )
            controls = [_CONTROL_DIMENSION, _CHARACTER_CONTROL_DIMENSION][:control_count]
            dimensions = (
                rand.sample(pool, hybrid_count) if hybrid_count else []
            ) + controls

        using_default_generator = generator is None
        using_default_judge = judge is None
        generation_model_key = str(cfg.get("generation_model_key") or "").strip() or None
        judge_model_key = str(cfg.get("judge_model_key") or "").strip() or None
        finalist_judge_model_key = (
            str(cfg.get("finalist_judge_model_key") or "").strip() or None
        )
        raw_idea_judge_model_key = (
            str(cfg.get("raw_idea_judge_model_key") or "").strip()
            or finalist_judge_model_key
        )
        premise_judge_model_key = (
            str(cfg.get("premise_judge_model_key") or "").strip()
            or finalist_judge_model_key
        )
        result.generation_model_key = generation_model_key
        result.judge_model_key = judge_model_key
        result.finalist_judge_model_key = finalist_judge_model_key
        result.raw_idea_judge_model_key = raw_idea_judge_model_key
        result.premise_judge_model_key = premise_judge_model_key
        gen_fn = generator
        if using_default_generator:
            gen_fn = await _default_generator(
                session,
                settings,
                template="concept_tournament_candidate",
                max_tokens=900,
                logical_role="planner",
                model_catalog_key=generation_model_key,
            )
        engine_fn = generator
        if candidate_prompt_mode == "engine_first" and using_default_generator:
            engine_fn = await _default_generator(
                session,
                settings,
                template="concept_tournament_engine_kernel",
                max_tokens=max(
                    2200,
                    int(cfg.get("engine_kernel_max_tokens", 3500)),
                ),
                logical_role="planner",
                model_catalog_key=generation_model_key,
            )
        # The raw-idea pool is now generated as ONE batch (see config comment on
        # raw_idea_generation_batch_size): 8-12 full pitches in a single reply
        # need far more room than a single premise card, and at 3500 tokens the
        # reply truncates into half a pool.
        pool_fn = engine_fn
        if using_default_generator:
            pool_fn = await _default_generator(
                session,
                settings,
                template="concept_tournament_raw_idea_pool",
                max_tokens=max(3500, int(cfg.get("raw_idea_pool_max_tokens", 6000))),
                logical_role="planner",
                model_catalog_key=generation_model_key,
            )
        judge_fn = judge
        if using_default_judge:
            judge_fn = await _default_generator(
                session,
                settings,
                template="concept_tournament_judge",
                max_tokens=max(450, int(cfg.get("judge_max_tokens", 900))),
                logical_role="critic",
                model_catalog_key=judge_model_key,
            )
        # P0 故事层判卡（2026-08-18）：独立模板便于 llm_runs 归因与事后审计。
        # 注入自定义 judge（测试路径）时复用之，保持可测性。
        story_judge_fn = judge
        if using_default_judge:
            story_judge_fn = await _default_generator(
                session,
                settings,
                template="concept_tournament_story_judge",
                max_tokens=max(900, int(cfg.get("story_judge_max_tokens", 1600))),
                logical_role="critic",
                model_catalog_key=judge_model_key,
            )
        expand_fn = expander
        if expand_fn is None and using_default_generator:
            expand_fn = await _default_generator(
                session,
                settings,
                template="concept_tournament_seriality",
                max_tokens=2200,
                logical_role="planner",
                model_catalog_key=generation_model_key,
            )
        seriality_judge_fn = seriality_judge
        if seriality_judge_fn is None and using_default_judge:
            seriality_judge_fn = await _default_generator(
                session,
                settings,
                template="concept_tournament_seriality_judge",
                max_tokens=max(
                    450,
                    int(cfg.get("seriality_judge_max_tokens", 900)),
                ),
                logical_role="critic",
                model_catalog_key=judge_model_key,
            )
        finalist_judge_fn: GeneratorFn | None = None
        finalist_seriality_judge_fn: GeneratorFn | None = None
        raw_idea_rank_fn: GeneratorFn | None = None
        premise_judge_fn: GeneratorFn | None = premise_judge
        if using_default_judge and finalist_judge_model_key:
            finalist_judge_fn = await _default_generator(
                session,
                settings,
                template="concept_tournament_finalist_judge",
                max_tokens=max(
                    1800,
                    int(cfg.get("finalist_judge_max_tokens", 3000)),
                ),
                logical_role="critic",
                model_catalog_key=finalist_judge_model_key,
            )
            finalist_seriality_judge_fn = await _default_generator(
                session,
                settings,
                template="concept_tournament_finalist_seriality_judge",
                max_tokens=max(
                    1800,
                    int(cfg.get("finalist_seriality_judge_max_tokens", 3000)),
                ),
                logical_role="critic",
                model_catalog_key=finalist_judge_model_key,
            )
        if using_default_judge and raw_idea_judge_model_key:
            raw_idea_rank_fn = await _default_generator(
                session,
                settings,
                template="concept_tournament_raw_idea_rank",
                max_tokens=max(3000, int(cfg.get("raw_idea_rank_max_tokens", 4500))),
                logical_role="critic",
                model_catalog_key=raw_idea_judge_model_key,
            )
        if premise_judge_fn is None and using_default_judge and premise_judge_model_key:
            premise_judge_fn = await _default_generator(
                session,
                settings,
                template="concept_tournament_premise_judge",
                max_tokens=max(3000, int(cfg.get("premise_judge_max_tokens", 4500))),
                logical_role="critic",
                model_catalog_key=premise_judge_model_key,
            )

        avoid_block = _render_avoid_mechanisms_block(avoid_mechanisms or [])

        # ── 1) 候选生成 ────────────────────────────────────────────────
        build_candidate_messages = _candidate_message_builder(cfg)
        result.candidate_prompt_mode = candidate_prompt_mode
        candidates: list[ConceptCandidate] = []
        raw_pitch_by_seed: dict[str, dict[str, Any]] = {}
        work_items = [(dimension, seed_concept) for dimension in dimensions]
        if candidate_prompt_mode == "engine_first":
            raw_idea_prompt_arm = str(
                cfg.get("raw_idea_prompt_arm") or "enhanced"
            ).strip().lower()
            if raw_idea_prompt_arm not in {
                "minimal",
                "methodology",
                "consequence",
                "author_pitch",
                "guarded",
                "enhanced",
            }:
                raise ValueError(
                    f"unsupported raw_idea_prompt_arm: {raw_idea_prompt_arm}"
                )
            result.raw_idea_prompt_arm = raw_idea_prompt_arm
            pool_count = max(
                n_candidates,
                n_candidates * max(1, int(cfg.get("raw_idea_pool_multiplier", 2))),
            )
            generation_batch_size = max(
                1,
                int(cfg.get("raw_idea_generation_batch_size", pool_count)),
            )
            focus_values = cfg.get("raw_idea_batch_focuses") or []
            batch_focuses = [
                str(value).strip()
                for value in focus_values
                if str(value).strip()
            ] if isinstance(focus_values, list) else []
            parsed_pool: list[tuple[str, str]] = []
            seen_seeds: set[str] = set()
            async def _generate_pool_batch(batch_count: int, focus_hint: str) -> None:
                """One pool call; absorbs its ideas into the shared pool."""
                pool_system, pool_user = _build_raw_idea_pool_messages(
                    genre=genre,
                    sub_genre=sub_genre,
                    count=batch_count,
                    seed_concept=seed_concept,
                    prompt_arm=raw_idea_prompt_arm,
                    focus_hint=focus_hint,
                    audience_orientation=audience_orientation,
                    tone_preference=tone_preference,
                    effect_skills=effect_skills,
                    creation_intent_block=creation_intent_block,
                )
                result.candidate_prompt_chars += len(pool_system) + len(pool_user)
                result.candidate_generation_calls += 1
                pool_raw, pool_run_id = await pool_fn(pool_system, pool_user)
                if pool_run_id is not None:
                    result.llm_run_ids.append(pool_run_id)
                for record in _parse_raw_idea_records(pool_raw, limit=batch_count):
                    lane = str(record["lane"])
                    seed = str(record["seed"])
                    raw_guard = _candidate_hard_rejection_reason(
                        ConceptCandidate(dimension=lane, concept=seed),
                        seed_concept=seed_concept,
                        tone_preference=tone_preference,
                        effect_skills=effect_skills,
                        cost_style=cost_style,
                        allow_debt_theme=allow_debt_theme,
                        allow_death_theme=allow_death_theme,
                    )
                    if raw_guard:
                        result.engine_rejections.append(
                            {
                                "dimension": lane,
                                "scores": {},
                                "reason": "原始创意反污染门失败: " + raw_guard,
                                "failed_axes": ["creation_intent_pollution"],
                            }
                        )
                        continue
                    normalized_seed = "".join(seed.split())
                    if normalized_seed in seen_seeds:
                        continue
                    seen_seeds.add(normalized_seed)
                    parsed_pool.append((lane, seed))
                    raw_pitch_by_seed[seed] = record

            for batch_index, batch_start in enumerate(
                range(0, pool_count, generation_batch_size)
            ):
                batch_count = min(generation_batch_size, pool_count - batch_start)
                focus_hint = (
                    batch_focuses[batch_index % len(batch_focuses)]
                    if batch_focuses and batch_count == 1
                    else ""
                )
                await _generate_pool_batch(batch_count, focus_hint)

            # Top-up. Batching the pool is what breaks the mode collapse, but a
            # single long reply occasionally comes back short or unparseable
            # (measured 83% yield, including one empty reply in 6). batch=1 had
            # 100% yield and that must not be traded away: ask again for exactly
            # what is missing until the pool is whole or the call budget runs out.
            for _ in range(max(0, int(cfg.get("raw_idea_pool_topup_calls", 2)))):
                missing = pool_count - len(parsed_pool)
                if missing <= 0:
                    break
                logger.info(
                    "raw idea pool short (%d/%d); topping up",
                    len(parsed_pool), pool_count,
                )
                await _generate_pool_batch(missing, "")
            parsed_pool = parsed_pool[:pool_count]
            if parsed_pool:
                result.raw_ideas = [
                    raw_pitch_by_seed.get(seed, {"lane": lane, "seed": seed})
                    for lane, seed in parsed_pool
                ]
                work_items = [
                    (f"{lane}#{index}", seed)
                    for index, (lane, seed) in enumerate(parsed_pool)
                ]
                if raw_idea_rank_fn is not None:
                    ranking: list[dict[str, Any]] = []
                    rank_retry_calls = 0
                    rank_batch_size = max(
                        2, int(cfg.get("raw_idea_rank_batch_size", 6))
                    )
                    for batch_start in range(0, len(parsed_pool), rank_batch_size):
                        batch = parsed_pool[
                            batch_start : batch_start + rank_batch_size
                        ]
                        rank_system, rank_user = _build_raw_idea_rank_messages(
                            genre=genre,
                            sub_genre=sub_genre,
                            ideas=batch,
                            audience_orientation=audience_orientation,
                            pitch_by_seed=raw_pitch_by_seed,
                        )
                        rank_raw, rank_run_id = await raw_idea_rank_fn(
                            rank_system, rank_user
                        )
                        if rank_run_id is not None:
                            result.llm_run_ids.append(rank_run_id)
                        parsed_batch = [
                            item
                            for item in _parse_raw_idea_ranking(rank_raw)
                            if 0 <= int(item.get("index", -1)) < len(batch)
                        ]
                        seen_local = {int(item["index"]) for item in parsed_batch}
                        for item in parsed_batch:
                            item["index"] = int(item["index"]) + batch_start
                            ranking.append(item)
                        missing_local = [
                            index for index in range(len(batch)) if index not in seen_local
                        ]
                        # A truncated/invalid judge response must not silently remove half
                        # the search space. Retry only missing ideas in pairs so the same
                        # output-token ceiling cannot repeat the six-item truncation.
                        for retry_start in range(0, len(missing_local), 2):
                            source_indexes = missing_local[retry_start : retry_start + 2]
                            retry_batch = [batch[index] for index in source_indexes]
                            retry_system, retry_user = _build_raw_idea_rank_messages(
                                genre=genre,
                                sub_genre=sub_genre,
                                ideas=retry_batch,
                                audience_orientation=audience_orientation,
                                pitch_by_seed=raw_pitch_by_seed,
                            )
                            retry_raw, retry_run_id = await raw_idea_rank_fn(
                                retry_system, retry_user
                            )
                            rank_retry_calls += 1
                            if retry_run_id is not None:
                                result.llm_run_ids.append(retry_run_id)
                            for item in _parse_raw_idea_ranking(retry_raw):
                                retry_index = int(item.get("index", -1))
                                if not 0 <= retry_index < len(source_indexes):
                                    continue
                                item["index"] = (
                                    batch_start + source_indexes[retry_index]
                                )
                                ranking.append(item)
                    result.raw_idea_ranking = ranking
                    ranked_indexes = {
                        int(item.get("index", -1))
                        for item in ranking
                        if 0 <= int(item.get("index", -1)) < len(parsed_pool)
                    }
                    result.raw_idea_rank_coverage = {
                        "expected": len(parsed_pool),
                        "ranked": len(ranked_indexes),
                        "missing_indexes": sorted(
                            set(range(len(parsed_pool))) - ranked_indexes
                        ),
                        "retry_calls": rank_retry_calls,
                        "complete": len(ranked_indexes) == len(parsed_pool),
                    }
                    # Does the market already have this book? Asked HERE, at
                    # the only point where the answer can still change anything:
                    # before an expansion slot is spent on it. Deterministic, so
                    # rival premises never touch a prompt (quoting them at the
                    # generator is how the framework seeded its own motifs).
                    if market_competitors:
                        from bestseller.services.market_validation.analyzer import (  # noqa: PLC0415
                            concept_market_collisions,
                        )

                        for item in ranking:
                            idx = int(item.get("index", -1))
                            if not 0 <= idx < len(parsed_pool):
                                continue
                            hits = concept_market_collisions(
                                parsed_pool[idx][1], market_competitors
                            )
                            if hits:
                                item["market_collision"] = [
                                    {"title": t, "overlap": v} for t, v in hits
                                ]
                                result.engine_rejections.append(
                                    {
                                        "dimension": parsed_pool[idx][0],
                                        "scores": {},
                                        "reason": (
                                            "榜单已有高度相似作品，展开位让给未撞车的候选："
                                            + "、".join(t for t, _ in hits)
                                        ),
                                        "failed_axes": ["market_collision"],
                                    }
                                )
                    # 用户定罪的规则句结构是句法层的，确定性检出（LLM 判官
                    # 3 采样只抓到 1 次），命中者沉底展开队列并留案底。
                    from bestseller.services.hook_pull_judge import (  # noqa: PLC0415
                        detect_condemned_hook_structures,
                    )

                    _light_tone = (
                        str(tone_preference or "").strip().lower() == "light"
                    )
                    for item in ranking:
                        idx = int(item.get("index", -1))
                        if not 0 <= idx < len(parsed_pool):
                            continue
                        structure_hits = detect_condemned_hook_structures(
                            parsed_pool[idx][1]
                        )
                        if structure_hits:
                            item["condemned_structure"] = structure_hits
                            result.engine_rejections.append(
                                {
                                    "dimension": parsed_pool[idx][0],
                                    "scores": {},
                                    "reason": (
                                        "命中定罪句式（"
                                        + "、".join(structure_hits)
                                        + "），展开位让给结构干净的候选"
                                    ),
                                    "failed_axes": ["condemned_structure"],
                                }
                            )
                        # 默认债务/丧葬族沉底（2026-08-13《我替娘讨旧账》：
                        # 连续两本用户书撞进同一默认族）。用户自己的创意里
                        # 提过该族=用户的选择，豁免。
                        from bestseller.services.anti_default_motif import (  # noqa: PLC0415
                            default_debt_family_hits,
                            is_debt_dominated,
                        )

                        _user_named_family = bool(
                            default_debt_family_hits(str(seed_concept or ""))
                        )
                        if not _user_named_family and is_debt_dominated(
                            parsed_pool[idx][1]
                        ):
                            item["default_family"] = True
                            result.engine_rejections.append(
                                {
                                    "dimension": parsed_pool[idx][0],
                                    "scores": {},
                                    "reason": (
                                        "命中框架实测过度复用的默认主题族"
                                        "（用户未要求），展开位让给未收敛的候选"
                                    ),
                                    "failed_axes": ["default_family"],
                                }
                            )
                        # 用户选轻松调性时，胁迫式生死赌注的胚子沉底
                        # （2026-08-13《摸一摸，救我妹》：人质+限期+沉河
                        # 内核在 tone=light 下夺冠——选题层必须服从用户选项）。
                        if _light_tone and _coercion_stake_hits(parsed_pool[idx][1]):
                            item["tone_conflict"] = True
                            result.engine_rejections.append(
                                {
                                    "dimension": parsed_pool[idx][0],
                                    "scores": {},
                                    "reason": (
                                        "用户选择轻松调性，该胚子是胁迫式"
                                        "生死赌注开局，展开位让给合调性的候选"
                                    ),
                                    "failed_axes": ["tone_conflict"],
                                }
                            )
                    raw_floor = float(cfg.get("raw_idea_floor", 7.0))
                    card_count = max(1, int(cfg.get("premise_card_count", 4)))
                    qualified = _select_raw_ideas_for_expansion(
                        [
                            item
                            for item in ranking
                            if 0 <= int(item["index"]) < len(parsed_pool)
                        ],
                        raw_floor=raw_floor,
                        progression_floor=float(
                            cfg.get("raw_idea_progression_floor", 5.0)
                        ),
                        limit=card_count,
                    )
                    if not result.raw_idea_rank_coverage["complete"]:
                        result.engine_rejections.append(
                            {
                                "dimension": "raw_idea_pool",
                                "scores": {},
                                "reason": "原始创意独立裁判覆盖不完整，禁止从残缺样本中选冠军",
                                "failed_axes": ["raw_idea_rank_coverage"],
                                "missing_indexes": result.raw_idea_rank_coverage[
                                    "missing_indexes"
                                ],
                            }
                        )
                        qualified = []
                    work_items = [
                        (
                            f"{parsed_pool[int(item['index'])][0]}#{int(item['index'])}",
                            parsed_pool[int(item["index"])][1],
                        )
                        for item in qualified[:card_count]
                    ]

        # Fit axes for explicit creation options (tone/cost). They ride the
        # same judge call and the same floor as every other axis, so a card
        # that ignores what the user ticked fails HERE — before expansion —
        # instead of shipping and contradicting the book's own directives.
        intent_axes = creation_intent_judge_axes(
            tone_preference=tone_preference, cost_style=cost_style
        )
        engine_axes = (
            "seed_fidelity",
            "freshness",
            "click_seed",
            "action_conflict",
            "reader_promise",
            "character_choice",
            "scene_generation",
            "promise_survival",
            "deformable_loop",
            "post_reveal_engine",
            "genre_fidelity",
            *(key for key, _ in intent_axes),
        )
        engine_floor = float(cfg.get("engine_judge_floor", 7.0))
        prebuilt_kernels: dict[tuple[str, str], dict[str, Any]] = {}
        batch_review_complete = False
        if (
            candidate_prompt_mode == "engine_first"
            and premise_judge_fn is not None
            and work_items
        ):
            cards: list[tuple[str, str, dict[str, Any]]] = []
            for _gen_idx, (dimension, premise_seed) in enumerate(work_items):
                emit_activity(
                    "concept_tournament_progress",
                    {"stage": "engine_kernel", "index": _gen_idx, "total": len(work_items)},
                )
                try:
                    engine_system, engine_user = _build_engine_kernel_messages(
                        genre=genre,
                        sub_genre=sub_genre,
                        lane=dimension,
                        chapter_count=chapter_count,
                        seed_concept=premise_seed,
                        seed_support=raw_pitch_by_seed.get(premise_seed),
                        audience_orientation=audience_orientation,
                        cost_style=cost_style,
                        tone_preference=tone_preference,
                        effect_skills=effect_skills,
                        creation_intent_block=creation_intent_block,
                        banned=banned,
                    )
                    result.candidate_prompt_chars += len(engine_system) + len(engine_user)
                    result.candidate_generation_calls += 1
                    engine_raw, engine_run_id = await engine_fn(
                        engine_system, engine_user
                    )
                    if engine_run_id is not None:
                        result.llm_run_ids.append(engine_run_id)
                    kernel = _parse_json_object(engine_raw)
                    if kernel is None:
                        raise ValueError("engine kernel is not valid JSON")
                    card_record = {
                        "dimension": dimension,
                        "seed": premise_seed,
                        "card": kernel,
                    }
                    result.premise_cards.append(card_record)
                    missing_card_fields = _premise_card_audit(kernel)
                    if missing_card_fields:
                        repair_system, repair_user = (
                            _build_engine_kernel_repair_messages(
                                genre=genre,
                                sub_genre=sub_genre,
                                lane=dimension,
                                chapter_count=chapter_count,
                                seed_concept=premise_seed,
                                card=kernel,
                                missing_fields=missing_card_fields,
                                seed_support=raw_pitch_by_seed.get(premise_seed),
                                audience_orientation=audience_orientation,
                                cost_style=cost_style,
                                tone_preference=tone_preference,
                                effect_skills=effect_skills,
                                creation_intent_block=creation_intent_block,
                            )
                        )
                        result.candidate_prompt_chars += len(repair_system) + len(
                            repair_user
                        )
                        result.candidate_generation_calls += 1
                        repair_raw, repair_run_id = await engine_fn(
                            repair_system, repair_user
                        )
                        if repair_run_id is not None:
                            result.llm_run_ids.append(repair_run_id)
                        repaired_kernel = _parse_json_object(repair_raw)
                        repaired_missing = _premise_card_audit(repaired_kernel or {})
                        card_record["repair"] = {
                            "initial_missing_fields": missing_card_fields,
                            "remaining_missing_fields": repaired_missing,
                            "run_id": (
                                str(repair_run_id)
                                if repair_run_id is not None
                                else None
                            ),
                        }
                        if repaired_kernel is not None:
                            card_record["initial_card"] = kernel
                            card_record["card"] = repaired_kernel
                            kernel = repaired_kernel
                        missing_card_fields = repaired_missing
                    if missing_card_fields:
                        result.engine_rejections.append(
                            {
                                "dimension": dimension,
                                "scores": {},
                                "reason": "项目卡结构缺失: "
                                + "/".join(missing_card_fields),
                                "failed_axes": ["card_completeness"],
                            }
                        )
                        continue

                    # ── P0 故事层判卡（2026-08-18 接线）──────────────────────
                    # 概念层判据只看「概念一句话」，展开层的 wants/stakes/
                    # 系列引擎此前零业务判据（《丑石》三缺陷全部漏过；下一本
                    # 直接被榜单线拦截）。零杀权：两票定罪（判官单票噪声已
                    # 定量不可信），定罪也**不淘汰卡**——只做一次带证据引文的
                    # 方向性重展开并全程留痕进 conception_log。
                    if story_judge_fn is not None and bool(
                        cfg.get("story_layer_judge_enabled", True)
                    ):
                        _sl_votes: list[dict[str, Any]] = []
                        for _sl_i in range(
                            max(1, int(cfg.get("story_layer_votes", 2)))
                        ):
                            _sl_sys, _sl_user = _build_story_layer_judge_messages(
                                kernel,
                                genre=genre,
                                sub_genre=sub_genre,
                                audience_orientation=audience_orientation,
                            )
                            try:
                                _sl_raw, _sl_run_id = await story_judge_fn(
                                    _sl_sys, _sl_user
                                )
                            except Exception:
                                break
                            if _sl_run_id is not None:
                                result.llm_run_ids.append(_sl_run_id)
                            _sl_parsed = _parse_story_layer_verdict(_sl_raw)
                            if _sl_parsed is not None:
                                _sl_votes.append(_sl_parsed)
                        _sl_convicted: list[str] = []
                        if len(_sl_votes) >= 2:
                            _sl_fail_sets = [
                                set(v["failed_axes"]) for v in _sl_votes
                            ]
                            _sl_convicted = sorted(
                                set.intersection(*_sl_fail_sets)
                            )
                        card_record["story_layer"] = {
                            "votes": _sl_votes,
                            "convicted_axes": _sl_convicted,
                        }
                        if _sl_convicted:
                            _sl_first = _sl_votes[0].get("axes", {})
                            _sl_quotes: list[str] = []
                            for _axis in _sl_convicted:
                                _entry = _sl_first.get(_axis) or {}
                                for _qk in ("quote", "win_quote", "cost_quote"):
                                    _qv = str(_entry.get(_qk) or "").strip()
                                    if _qv:
                                        _sl_quotes.append(f"{_axis}={_qv}")
                            _sl_directions = "；".join(
                                d
                                for d in (
                                    v.get("revise_direction", "")
                                    for v in _sl_votes
                                )
                                if d
                            )
                            _sl_revise_user = (
                                engine_user
                                + "\n\n【故事层判卡未过——按方向修正后重新输出"
                                "完整JSON项目卡，未点名的部分保持原样】\n"
                                + f"未过项：{'、'.join(_sl_convicted)}\n"
                                + (
                                    "证据引文："
                                    + "；".join(_sl_quotes)
                                    + "\n"
                                    if _sl_quotes
                                    else ""
                                )
                                + (
                                    f"修正方向：{_sl_directions}\n"
                                    if _sl_directions
                                    else ""
                                )
                            )
                            result.candidate_generation_calls += 1
                            result.candidate_prompt_chars += len(
                                engine_system
                            ) + len(_sl_revise_user)
                            try:
                                _sl_new_raw, _sl_new_run_id = await engine_fn(
                                    engine_system, _sl_revise_user
                                )
                            except Exception:
                                _sl_new_raw, _sl_new_run_id = None, None
                            if _sl_new_run_id is not None:
                                result.llm_run_ids.append(_sl_new_run_id)
                            _sl_revised = (
                                _parse_json_object(_sl_new_raw)
                                if _sl_new_raw
                                else None
                            )
                            if _sl_revised is not None and not _premise_card_audit(
                                _sl_revised
                            ):
                                # 复核（2026-08-18《九姓井口只认我》定罪：revised
                                # 采纳后不复核=白判——终稿 wants 仍是防守型。
                                # 复核只花在已定罪的卡上；revised 定罪轴数没有
                                # 变少就不采纳，防止「改了个寂寞」甚至改更坏。
                                _sl_recheck: list[dict[str, Any]] = []
                                for _sl_j in range(
                                    max(1, int(cfg.get("story_layer_votes", 2)))
                                ):
                                    _rc_sys, _rc_user = (
                                        _build_story_layer_judge_messages(
                                            _sl_revised,
                                            genre=genre,
                                            sub_genre=sub_genre,
                                            audience_orientation=audience_orientation,
                                        )
                                    )
                                    try:
                                        _rc_raw, _rc_run_id = await story_judge_fn(
                                            _rc_sys, _rc_user
                                        )
                                    except Exception:
                                        break
                                    if _rc_run_id is not None:
                                        result.llm_run_ids.append(_rc_run_id)
                                    _rc_parsed = _parse_story_layer_verdict(_rc_raw)
                                    if _rc_parsed is not None:
                                        _sl_recheck.append(_rc_parsed)
                                _sl_recheck_convicted: list[str] = []
                                if len(_sl_recheck) >= 2:
                                    _sl_recheck_convicted = sorted(
                                        set.intersection(
                                            *(
                                                set(v["failed_axes"])
                                                for v in _sl_recheck
                                            )
                                        )
                                    )
                                card_record["story_layer"]["recheck_convicted"] = (
                                    _sl_recheck_convicted
                                )
                                if (
                                    len(_sl_recheck) >= 2
                                    and len(_sl_recheck_convicted)
                                    >= len(_sl_convicted)
                                ):
                                    # 复核显示没修好 → 保留原卡（零杀权，留痕）
                                    card_record["story_layer"]["revised"] = False
                                    card_record["story_layer"][
                                        "revise_rejected_reason"
                                    ] = "recheck_no_improvement"
                                else:
                                    card_record["story_layer"]["revised"] = True
                                    card_record.setdefault("initial_card", kernel)
                                    card_record["card"] = _sl_revised
                                    kernel = _sl_revised
                            else:
                                # 重展开失败/结构不全 → 保留原卡继续（零杀权）
                                card_record["story_layer"]["revised"] = False

                    # ── E 层编辑判官（2026-08-18 接线）────────────────────
                    # S 轴审故事结构，E 轴审设定质量（规则可演示/限制生剧情/
                    # 世界规则先行……榜单 70 本蒸馏）。两通道分开调用防注意力
                    # 稀释。零杀权：承重轴(e1/e2/e4)两票定罪 → 一次方向性
                    # 重展开+复核，加分项(e3/e6)只记录。
                    if story_judge_fn is not None and bool(
                        cfg.get("expansion_editor_judge_enabled", True)
                    ):
                        _ee_votes: list[dict[str, Any]] = []
                        for _ee_i in range(
                            max(1, int(cfg.get("story_layer_votes", 2)))
                        ):
                            _ee_sys, _ee_user = _build_expansion_editor_messages(
                                kernel, genre=genre, sub_genre=sub_genre
                            )
                            try:
                                _ee_raw, _ee_run_id = await story_judge_fn(
                                    _ee_sys, _ee_user
                                )
                            except Exception:
                                break
                            if _ee_run_id is not None:
                                result.llm_run_ids.append(_ee_run_id)
                            _ee_parsed = _parse_story_layer_verdict(
                                _ee_raw, axes=_EXPANSION_EDITOR_AXES
                            )
                            if _ee_parsed is not None:
                                _ee_votes.append(_ee_parsed)
                        _ee_convicted: list[str] = []
                        if len(_ee_votes) >= 2:
                            _ee_convicted = sorted(
                                set.intersection(
                                    *(set(v["failed_axes"]) for v in _ee_votes)
                                )
                                & _EXPANSION_EDITOR_CONVICTION_AXES
                            )
                        card_record["expansion_editor"] = {
                            "votes": _ee_votes,
                            "convicted_axes": _ee_convicted,
                        }
                        if _ee_convicted:
                            _ee_first = _ee_votes[0].get("axes", {})
                            _ee_quotes: list[str] = []
                            for _axis in _ee_convicted:
                                _entry = _ee_first.get(_axis) or {}
                                for _qk in ("quote", "world_rule", "exception"):
                                    _qv = str(_entry.get(_qk) or "").strip()
                                    if _qv:
                                        _ee_quotes.append(f"{_axis}={_qv}")
                            _ee_directions = "；".join(
                                d
                                for d in (
                                    v.get("revise_direction", "")
                                    for v in _ee_votes
                                )
                                if d
                            )
                            _ee_revise_user = (
                                engine_user
                                + "\n\n【编辑审设定未过——按方向修正后重新输出"
                                "完整JSON项目卡，未点名的部分保持原样】\n"
                                + f"未过项：{'、'.join(_ee_convicted)}\n"
                                + (
                                    "证据："
                                    + "；".join(_ee_quotes)
                                    + "\n"
                                    if _ee_quotes
                                    else ""
                                )
                                + (
                                    f"修正方向：{_ee_directions}\n"
                                    if _ee_directions
                                    else ""
                                )
                            )
                            result.candidate_generation_calls += 1
                            result.candidate_prompt_chars += len(
                                engine_system
                            ) + len(_ee_revise_user)
                            try:
                                _ee_new_raw, _ee_new_run_id = await engine_fn(
                                    engine_system, _ee_revise_user
                                )
                            except Exception:
                                _ee_new_raw, _ee_new_run_id = None, None
                            if _ee_new_run_id is not None:
                                result.llm_run_ids.append(_ee_new_run_id)
                            _ee_revised = (
                                _parse_json_object(_ee_new_raw)
                                if _ee_new_raw
                                else None
                            )
                            if _ee_revised is not None and not _premise_card_audit(
                                _ee_revised
                            ):
                                _ee_recheck: list[dict[str, Any]] = []
                                for _ee_j in range(
                                    max(1, int(cfg.get("story_layer_votes", 2)))
                                ):
                                    _er_sys, _er_user = (
                                        _build_expansion_editor_messages(
                                            _ee_revised,
                                            genre=genre,
                                            sub_genre=sub_genre,
                                        )
                                    )
                                    try:
                                        _er_raw, _er_run_id = await story_judge_fn(
                                            _er_sys, _er_user
                                        )
                                    except Exception:
                                        break
                                    if _er_run_id is not None:
                                        result.llm_run_ids.append(_er_run_id)
                                    _er_parsed = _parse_story_layer_verdict(
                                        _er_raw, axes=_EXPANSION_EDITOR_AXES
                                    )
                                    if _er_parsed is not None:
                                        _ee_recheck.append(_er_parsed)
                                _ee_recheck_convicted: list[str] = []
                                if len(_ee_recheck) >= 2:
                                    _ee_recheck_convicted = sorted(
                                        set.intersection(
                                            *(
                                                set(v["failed_axes"])
                                                for v in _ee_recheck
                                            )
                                        )
                                        & _EXPANSION_EDITOR_CONVICTION_AXES
                                    )
                                card_record["expansion_editor"][
                                    "recheck_convicted"
                                ] = _ee_recheck_convicted
                                if (
                                    len(_ee_recheck) >= 2
                                    and len(_ee_recheck_convicted)
                                    >= len(_ee_convicted)
                                ):
                                    card_record["expansion_editor"]["revised"] = (
                                        False
                                    )
                                    card_record["expansion_editor"][
                                        "revise_rejected_reason"
                                    ] = "recheck_no_improvement"
                                else:
                                    card_record["expansion_editor"]["revised"] = (
                                        True
                                    )
                                    card_record.setdefault("initial_card", kernel)
                                    card_record["card"] = _ee_revised
                                    kernel = _ee_revised
                            else:
                                card_record["expansion_editor"]["revised"] = False

                    kernel_guard = _candidate_hard_rejection_reason(
                        _attach_engine_kernel(
                            ConceptCandidate(
                                dimension=dimension,
                                concept=premise_seed,
                            ),
                            kernel,
                        ),
                        seed_concept=seed_concept,
                        tone_preference=tone_preference,
                        effect_skills=effect_skills,
                        cost_style=cost_style,
                        allow_debt_theme=allow_debt_theme,
                        allow_death_theme=allow_death_theme,
                    )
                    if kernel_guard:
                        result.engine_rejections.append(
                            {
                                "dimension": dimension,
                                "scores": {},
                                "reason": "项目卡反污染门失败: " + kernel_guard,
                                "failed_axes": ["creation_intent_pollution"],
                            }
                        )
                        continue
                    prebuilt_kernels[(dimension, premise_seed)] = kernel
                    cards.append((dimension, premise_seed, kernel))
                except Exception:
                    logger.warning(
                        "concept premise generation failed (dimension=%s)",
                        dimension,
                        exc_info=True,
                    )
            if cards:
                batch_system, batch_user = _build_engine_batch_judge_messages(
                    cards=cards,
                    genre=genre,
                    sub_genre=sub_genre,
                    chapter_count=chapter_count,
                    intent_axes=intent_axes,
                )
                batch_raw, batch_run_id = await premise_judge_fn(
                    batch_system, batch_user
                )
                if batch_run_id is not None:
                    result.llm_run_ids.append(batch_run_id)
                verdict_payload = _parse_json_object(batch_raw) or {}
                verdicts = verdict_payload.get("verdicts")
                if isinstance(verdicts, list):
                    approved: list[tuple[str, str]] = []
                    clean_near_passes: list[
                        tuple[float, float, str, str]
                    ] = []
                    verdict_by_index: dict[int, dict[str, Any]] = {}
                    for verdict in verdicts:
                        if not isinstance(verdict, dict):
                            continue
                        try:
                            card_index = int(verdict.get("index"))
                        except (TypeError, ValueError):
                            continue
                        if (
                            not 0 <= card_index < len(cards)
                            or card_index in verdict_by_index
                        ):
                            continue
                        verdict_by_index[card_index] = verdict

                    for card_index, (dimension, premise_seed, kernel) in enumerate(
                        cards
                    ):
                        verdict = verdict_by_index.get(card_index)
                        engine_scores, missing_axes = _parse_complete_axis_scores(
                            verdict, engine_axes
                        )
                        # Batch responses may be truncated or omit one field. Rejudge
                        # only that card; never convert an absent field into a zero.
                        if missing_axes:
                            retry_system, retry_user = _build_engine_judge_messages(
                                kernel=kernel,
                                genre=genre,
                                sub_genre=sub_genre,
                                chapter_count=chapter_count,
                                seed_concept=premise_seed,
                                intent_axes=intent_axes,
                            )
                            retry_raw, retry_run_id = await premise_judge_fn(
                                retry_system, retry_user
                            )
                            if retry_run_id is not None:
                                result.llm_run_ids.append(retry_run_id)
                            retry_verdict = _parse_json_object(retry_raw)
                            engine_scores, missing_axes = _parse_complete_axis_scores(
                                retry_verdict, engine_axes
                            )
                            verdict = retry_verdict
                        if engine_scores is None:
                            result.engine_rejections.append(
                                {
                                    "dimension": dimension,
                                    "scores": {},
                                    "reason": "独立项目卡裁判返回字段不完整: "
                                    + "/".join(missing_axes),
                                    "failed_axes": ["missing_verdict_fields"],
                                    "missing_verdict_fields": missing_axes,
                                }
                            )
                            continue
                        failed_engine_axes = [
                            axis
                            for axis in engine_axes
                            if engine_scores[axis] < engine_floor
                        ]
                        if failed_engine_axes:
                            result.engine_rejections.append(
                                {
                                    "dimension": dimension,
                                    "scores": engine_scores,
                                    "reason": str(verdict.get("reason") or ""),
                                    "failed_axes": failed_engine_axes,
                                }
                            )
                            clean_near_passes.append(
                                (
                                    min(engine_scores.values()),
                                    sum(engine_scores.values()),
                                    dimension,
                                    premise_seed,
                                )
                            )
                        else:
                            approved.append((dimension, premise_seed))
                    minimum_survivors = min(
                        len(cards),
                        max(
                            0,
                            int(cfg.get("premise_card_min_survivors", 0) or 0),
                        ),
                    )
                    if len(approved) < minimum_survivors:
                        approved_keys = set(approved)
                        for _, _, dimension, premise_seed in sorted(
                            clean_near_passes,
                            reverse=True,
                        ):
                            key = (dimension, premise_seed)
                            if key in approved_keys:
                                continue
                            approved.append(key)
                            approved_keys.add(key)
                            if len(approved) >= minimum_survivors:
                                break
                    work_items = approved
                    batch_review_complete = True
        # 挂在**所有分支的汇合点**：work_items 有 4 个赋值点（3266/3378/3587
        # 以及批量评审那条），只挂其中一条等于给自己留一条绕行路。读不到内核
        # 的分支按干净处理，天然 no-op。
        work_items = _demote_default_family_cards(
            work_items,
            prebuilt_kernels=prebuilt_kernels,
            seed_concept=seed_concept,
            result=result,
        )
        for _card_idx, (dimension, premise_seed) in enumerate(work_items):
            emit_activity(
                "concept_tournament_progress",
                {"stage": "premise_card", "index": _card_idx, "total": len(work_items)},
            )
            try:
                kernel: dict[str, Any] | None = prebuilt_kernels.get(
                    (dimension, premise_seed)
                )
                if candidate_prompt_mode == "engine_first":
                    if kernel is None:
                        engine_system, engine_user = _build_engine_kernel_messages(
                            genre=genre,
                            sub_genre=sub_genre,
                            lane=dimension,
                            chapter_count=chapter_count,
                            seed_concept=premise_seed,
                            seed_support=raw_pitch_by_seed.get(premise_seed),
                            audience_orientation=audience_orientation,
                            cost_style=cost_style,
                            tone_preference=tone_preference,
                            effect_skills=effect_skills,
                            creation_intent_block=creation_intent_block,
                            banned=banned,
                        )
                        result.candidate_prompt_chars += len(engine_system) + len(engine_user)
                        result.candidate_generation_calls += 1
                        engine_raw, engine_run_id = await engine_fn(
                            engine_system, engine_user
                        )
                        if engine_run_id is not None:
                            result.llm_run_ids.append(engine_run_id)
                        kernel = _parse_json_object(engine_raw)
                        if kernel is None:
                            raise ValueError("engine kernel is not valid JSON")
                        result.premise_cards.append(
                            {
                                "dimension": dimension,
                                "seed": premise_seed,
                                "card": kernel,
                            }
                        )
                        missing_card_fields = _premise_card_audit(kernel)
                        if missing_card_fields:
                            result.engine_rejections.append(
                                {
                                    "dimension": dimension,
                                    "scores": {},
                                    "reason": "项目卡结构缺失: "
                                    + "/".join(missing_card_fields),
                                    "failed_axes": ["card_completeness"],
                                }
                            )
                            continue
                    if not batch_review_complete and (
                        premise_judge_fn is not None or seriality_judge_fn is not None
                    ):
                        engine_judge_system, engine_judge_user = (
                            _build_engine_judge_messages(
                                kernel=kernel,
                                genre=genre,
                                sub_genre=sub_genre,
                                chapter_count=chapter_count,
                                seed_concept=premise_seed,
                                intent_axes=intent_axes,
                            )
                        )
                        engine_judge = premise_judge_fn or seriality_judge_fn
                        assert engine_judge is not None
                        engine_judge_raw, engine_judge_run_id = await engine_judge(
                            engine_judge_system, engine_judge_user
                        )
                        if engine_judge_run_id is not None:
                            result.llm_run_ids.append(engine_judge_run_id)
                        engine_verdict = _parse_json_object(engine_judge_raw)
                        engine_scores, missing_axes = _parse_complete_axis_scores(
                            engine_verdict, engine_axes
                        )
                        if engine_scores is None:
                            result.engine_rejections.append(
                                {
                                    "dimension": dimension,
                                    "scores": {},
                                    "reason": "独立项目卡裁判返回字段不完整: "
                                    + "/".join(missing_axes),
                                    "failed_axes": ["missing_verdict_fields"],
                                    "missing_verdict_fields": missing_axes,
                                }
                            )
                            continue
                        failed_engine_axes = [
                            axis
                            for axis in engine_axes
                            if engine_scores[axis] < engine_floor
                        ]
                        if failed_engine_axes:
                            result.engine_rejections.append(
                                {
                                    "dimension": dimension,
                                    "scores": engine_scores,
                                    "reason": str(
                                        (engine_verdict or {}).get("reason") or ""
                                    ),
                                    "failed_axes": failed_engine_axes,
                                }
                            )
                            continue
                    system, user = _build_hook_from_engine_messages(
                        genre=genre,
                        sub_genre=sub_genre,
                        kernel=kernel,
                        seed_concept=premise_seed,
                        audience_orientation=audience_orientation,
                        banned=banned,
                        retry_feedback=retry_feedback,
                    )
                else:
                    system, user = build_candidate_messages(
                        genre=genre,
                        sub_genre=sub_genre,
                        dimension=dimension,
                        chapter_count=chapter_count,
                        banned=banned,
                        avoid_mechanisms_block=avoid_block,
                        seed_concept=seed_concept,
                        retry_feedback=retry_feedback,
                        audience_orientation=audience_orientation,
                    )
                # cost_style rides on the chosen builder's output so all three
                # candidate prompt modes get it from this single injection point.
                from bestseller.services.ideology_kernel import cost_style_directive

                _cost_line = cost_style_directive(cost_style, is_en=False).strip()
                if _cost_line:
                    system = f"{system}{_cost_line}"
                result.candidate_prompt_chars += len(system) + len(user)
                result.candidate_generation_calls += 1
                raw, run_id = await gen_fn(system, user)
                if run_id is not None:
                    result.llm_run_ids.append(run_id)
                generated = (
                    _parse_hook_variants(raw, dimension)
                    if candidate_prompt_mode == "engine_first"
                    else [candidate]
                    if (candidate := _parse_candidate(raw, dimension)) is not None
                    else []
                )
                if candidate_prompt_mode == "engine_first" and premise_seed.strip():
                    generated.append(
                        ConceptCandidate(
                            dimension=f"{dimension}:raw",
                            concept=premise_seed.strip(),
                        )
                    )
                for candidate in generated:
                    if kernel is not None:
                        candidate = _attach_engine_kernel(candidate, kernel)
                    candidates.append(candidate)
            except Exception:
                logger.warning(
                    "concept candidate generation failed (dimension=%s)",
                    dimension, exc_info=True,
                )

        # ── 2) 确定性筛：俗套命中 + 最小故事种子审计 ───────────────────
        # 基线 eliminate（命中即出局）；wild_mode 覆盖为 penalize（改罚分不淘汰，
        # 避免 2 词元假命中误杀好候选——罚分在 composite 阶段扣）。
        from dataclasses import replace as _dc_replace

        cliche_mode = str(cfg.get("cliche_mode", "eliminate")).strip().lower()
        audit_mode = str(cfg.get("audit_mode", "eliminate")).strip().lower()
        cliche_penalty = float(cfg.get("cliche_penalty", 1.5))
        audit_penalty = float(cfg.get("audit_penalty", 1.0))

        screened: list[ConceptCandidate] = []
        annotated: list[ConceptCandidate] = []
        penalty_by_concept: dict[str, float] = {}
        for candidate in candidates:
            penalty = 0.0
            hard_reason = _candidate_hard_rejection_reason(
                candidate,
                seed_concept=seed_concept,
                tone_preference=tone_preference,
                effect_skills=effect_skills,
                cost_style=cost_style,
                allow_debt_theme=allow_debt_theme,
                allow_death_theme=allow_death_theme,
            )
            if hard_reason:
                annotated.append(
                    _dc_replace(
                        candidate,
                        rejected_reason="建书选项/反污染门失败: " + hard_reason,
                    )
                )
                continue
            anti_pattern = _deterministic_anti_pattern(candidate)
            if anti_pattern:
                annotated.append(
                    _dc_replace(
                        candidate,
                        rejected_reason=f"确定性反模式: {anti_pattern}",
                    )
                )
                continue
            hits = _cliche_hits(candidate, banned)
            if hits:
                if cliche_mode == "penalize":
                    penalty += cliche_penalty
                else:
                    annotated.append(
                        _dc_replace(candidate, rejected_reason=f"俗套命中: {'/'.join(hits[:3])}")
                    )
                    continue
            audit = _seed_audit(candidate)
            if audit:
                if audit_mode == "penalize":
                    penalty += audit_penalty
                else:
                    annotated.append(
                        _dc_replace(candidate, rejected_reason=f"故事种子审计: {audit}")
                    )
                    continue
            if penalty > 0:
                penalty_by_concept[candidate.concept] = penalty
            annotated.append(candidate)
            screened.append(candidate)
        result.candidates = annotated

        if not screened:
            # 与判官干涸取证对称:这条是确定性筛(俗套KO/反模式/种子审计)在判官之前
            # 全灭的路径,第10轮 wild 模式整批死在这里且零证据。
            for c in annotated:
                logger.warning(
                    "concept tournament screen-dry — candidate %r: rejected=%s",
                    (c.concept or "")[:60],
                    c.rejected_reason or "(unknown)",
                )
            logger.warning("concept tournament: all candidates rejected; no injection")
            return result

        # ── 3) 判官淘汰赛（新颖度对撞榜单参照）────────────────────────
        references: list[dict[str, str]] = []
        try:
            from bestseller.services.premise_appeal_arena import resolve_reference_set

            references = resolve_reference_set(genre, sub_genre)
        except Exception:
            logger.debug("reference set resolution failed", exc_info=True)

        weights = cfg.get("judge_weights") if isinstance(cfg.get("judge_weights"), dict) else {}
        w_fresh = float(weights.get("freshness", 0.4))
        w_click = float(weights.get("click", 0.4))
        w_unpred = float(weights.get("unpredictability", 0.2))
        w_character = float(weights.get("character_logic", 0.0))
        w_causality = float(weights.get("mechanism_causality", 0.0))
        w_genre = float(weights.get("genre_fidelity", 0.0))
        w_plain = float(weights.get("plain_language", 0.0))
        w_motion = float(weights.get("story_motion", 0.0))
        # 2026-08-25 新增：主角能动性。**刻意不进 _FLOOR_AXIS_LABELS**——
        # 只影响"谁赢"（composite 排序），不影响"谁出局"。加地板会缩小候选池，
        # 而 config 里 2026-07-17 记着收紧概念层阈值 → 淘汰赛干涸 → 回落保底
        # 概念（比任何被拒候选都差）。真机依据：custom-xuanhuan-1787625194 的
        # 故事引擎是纯被动循环，正文 has_decision 0/5——判官八条轴没有一条问
        # 「主角要不要做选择」，于是这类引擎在概念层完全无损通过。
        # 默认 0.0 与同组其余轴一致：真值一律来自配置。给非零默认会把权重
        # 加到**任何没声明该键的权重集**上（wild_mode 合计从 1.0 变 1.1，
        # 全量套件当场抓到），也让"改权重"这件事有两个来源。
        w_agency = float(weights.get("protagonist_agency", 0.0))

        judged: list[ConceptCandidate] = []
        for _judge_idx, candidate in enumerate(screened):
            # Heartbeat: each judge round is a sequential M3 call (50-249s). Without
            # touching progress the whole tournament attempt is one silent window and
            # can trip the 2700s no-progress watchdog. emit_activity is a no-op when
            # unbound (non-conception callers), so this is safe everywhere.
            emit_activity(
                "concept_tournament_progress",
                {"stage": "judge", "index": _judge_idx, "total": len(screened)},
            )
            try:
                system, user = _build_judge_messages(
                    candidate=candidate,
                    genre=genre,
                    sub_genre=sub_genre,
                    references=references,
                    audience_orientation=audience_orientation,
                )
                raw, run_id = await judge_fn(system, user)
                if run_id is not None:
                    result.llm_run_ids.append(run_id)
                verdict = _parse_json_object(raw)
                if verdict is None:
                    judged.append(candidate)  # 判官废→无分,靠别的候选竞争
                    continue
                fresh = max(0.0, min(10.0, float(verdict.get("freshness", 0))))
                click = max(0.0, min(10.0, float(verdict.get("click", 0))))
                predictable = max(0.0, min(10.0, float(verdict.get("predictable", 10))))
                character_logic = max(
                    0.0,
                    min(10.0, float(verdict.get("character_logic", 5))),
                )
                mechanism_causality = max(
                    0.0,
                    min(10.0, float(verdict.get("mechanism_causality", 5))),
                )
                genre_fidelity = max(
                    0.0,
                    min(10.0, float(verdict.get("genre_fidelity", 5))),
                )
                plain_language = max(
                    0.0,
                    min(10.0, float(verdict.get("plain_language", 5))),
                )
                story_motion = max(
                    0.0,
                    min(10.0, float(verdict.get("story_motion", 0))),
                )
                protagonist_agency = max(
                    0.0,
                    min(10.0, float(verdict.get("protagonist_agency", 5))),
                )
                composite = (
                    fresh * w_fresh
                    + click * w_click
                    + (10.0 - predictable) * w_unpred
                    + character_logic * w_character
                    + mechanism_causality * w_causality
                    + genre_fidelity * w_genre
                    + plain_language * w_plain
                    + story_motion * w_motion
                    + protagonist_agency * w_agency
                )
                # penalize 模式的确定性罚分（基线无罚分 → dict 空 → 值不变）。
                composite = max(
                    0.0, composite - penalty_by_concept.get(candidate.concept, 0.0)
                )
                hard_floors = (
                    cfg.get("judge_hard_floors")
                    if isinstance(cfg.get("judge_hard_floors"), dict)
                    else {}
                )
                failed_axes: list[str] = []
                failed_axes = _hard_floor_failed_axes(
                    {
                        "freshness": fresh,
                        "click": click,
                        "predictable": predictable,
                        "character_logic": character_logic,
                        "mechanism_causality": mechanism_causality,
                        "genre_fidelity": genre_fidelity,
                        "plain_language": plain_language,
                        "story_motion": story_motion,
                    },
                    hard_floors,
                )
                judged.append(
                    _dc_replace(
                        candidate,
                        judge_freshness=fresh,
                        judge_click=click,
                        judge_predictable=predictable,
                        judge_character_logic=character_logic,
                        judge_mechanism_causality=mechanism_causality,
                        judge_genre_fidelity=genre_fidelity,
                        judge_plain_language=plain_language,
                        judge_story_motion=story_motion,
                        judge_protagonist_agency=protagonist_agency,
                        judge_reason=str(verdict.get("reason") or ""),
                        composite=round(composite, 2) if not failed_axes else 0.0,
                        rejected_reason=(
                            None
                            if not failed_axes
                            else "钩子硬门失败: " + "/".join(failed_axes)
                        ),
                    )
                )
            except Exception:
                logger.warning("concept judge failed for one candidate", exc_info=True)
                judged.append(candidate)

        # 打完分的评审快照回写 result.candidates（供落库复盘）。
        scored_by_concept = {c.concept: c for c in judged}
        result.candidates = [
            scored_by_concept.get(c.concept, c) for c in result.candidates
        ]

        winner_min = float(cfg.get("winner_min", 5.5))
        contenders = [
            c
            for c in judged
            if c.rejected_reason is None
            and c.composite is not None
            and (c.composite or 0.0) >= winner_min
        ]
        if not contenders:
            # Forensics: a dry tournament forces conception onto its vanilla
            # concept, which then (justifiably) fails the logline gate on
            # unpredictability — but with no per-candidate trail there is no way
            # to tell miscalibrated floors from genuinely bad candidates. Four
            # consecutive real runs went dry with zero evidence (2026-07-16).
            for c in judged:
                logger.warning(
                    "concept tournament dry — candidate %r: composite=%s rejected=%s "
                    "fresh=%.1f click=%.1f predictable=%.1f char=%.1f mech=%.1f "
                    "genre=%.1f plain=%.1f motion=%.1f",
                    (c.concept or "")[:60],
                    c.composite,
                    c.rejected_reason or "低于 winner_min",
                    float(c.judge_freshness or 0.0),
                    float(c.judge_click or 0.0),
                    float(c.judge_predictable or 0.0),
                    float(c.judge_character_logic or 0.0),
                    float(c.judge_mechanism_causality or 0.0),
                    float(c.judge_genre_fidelity or 0.0),
                    float(c.judge_plain_language or 0.0),
                    float(c.judge_story_motion or 0.0),
                )
            logger.warning("concept tournament: no hook-qualified contenders; no injection")
            return result

        # ── 4) 独立终审少量钩子，防止生成模型家族自评放水 ──────────────
        if finalist_judge_fn is not None:
            hard_floors = (
                cfg.get("judge_hard_floors")
                if isinstance(cfg.get("judge_hard_floors"), dict)
                else {}
            )

            async def _independent_hook_review(
                current: ConceptCandidate,
            ) -> tuple[ConceptCandidate, list[str]]:
                system, user = _build_judge_messages(
                    candidate=current,
                    genre=genre,
                    sub_genre=sub_genre,
                    references=references,
                    audience_orientation=audience_orientation,
                )
                raw, run_id = await finalist_judge_fn(system, user)
                if run_id is not None:
                    result.llm_run_ids.append(run_id)
                verdict = _parse_json_object(raw) or {}
                scores = {
                    "freshness": max(0.0, min(10.0, float(verdict.get("freshness", 0)))),
                    "click": max(0.0, min(10.0, float(verdict.get("click", 0)))),
                    "predictable": max(
                        0.0, min(10.0, float(verdict.get("predictable", 10)))
                    ),
                    "character_logic": max(
                        0.0, min(10.0, float(verdict.get("character_logic", 0)))
                    ),
                    "mechanism_causality": max(
                        0.0, min(10.0, float(verdict.get("mechanism_causality", 0)))
                    ),
                    "genre_fidelity": max(
                        0.0, min(10.0, float(verdict.get("genre_fidelity", 0)))
                    ),
                    "plain_language": max(
                        0.0, min(10.0, float(verdict.get("plain_language", 0)))
                    ),
                    "story_motion": max(
                        0.0, min(10.0, float(verdict.get("story_motion", 0)))
                    ),
                }
                failed = _hard_floor_failed_axes(scores, hard_floors)
                composite = (
                    scores["freshness"] * w_fresh
                    + scores["click"] * w_click
                    + (10.0 - scores["predictable"]) * w_unpred
                    + scores["character_logic"] * w_character
                    + scores["mechanism_causality"] * w_causality
                    + scores["genre_fidelity"] * w_genre
                    + scores["plain_language"] * w_plain
                    + scores["story_motion"] * w_motion
                )
                reviewed = _dc_replace(
                    current,
                    judge_freshness=scores["freshness"],
                    judge_click=scores["click"],
                    judge_predictable=scores["predictable"],
                    judge_character_logic=scores["character_logic"],
                    judge_mechanism_causality=scores["mechanism_causality"],
                    judge_genre_fidelity=scores["genre_fidelity"],
                    judge_plain_language=scores["plain_language"],
                    judge_story_motion=scores["story_motion"],
                    judge_reason="独立终审: " + str(verdict.get("reason") or ""),
                    composite=round(composite, 2) if not failed else 0.0,
                    rejected_reason=(
                        None if not failed else "独立钩子终审失败: " + "/".join(failed)
                    ),
                )
                return reviewed, failed

            finalist_pool_size = max(1, int(cfg.get("finalist_pool_size", 4)))
            reviewed_contenders: list[ConceptCandidate] = []
            for contender in sorted(
                contenders, key=lambda c: c.composite or 0.0, reverse=True
            )[:finalist_pool_size]:
                reviewed, failed = await _independent_hook_review(contender)
                if failed and set(failed).issubset({"大白话", "想点欲"}):
                    repair_system, repair_user = _build_hook_copy_repair_messages(
                        candidate=contender,
                        feedback=reviewed.judge_reason,
                    )
                    result.candidate_prompt_chars += len(repair_system) + len(repair_user)
                    result.candidate_generation_calls += 1
                    repair_raw, repair_run_id = await gen_fn(
                        repair_system, repair_user
                    )
                    if repair_run_id is not None:
                        result.llm_run_ids.append(repair_run_id)
                    repair_payload = _parse_json_object(repair_raw) or {}
                    repaired_concept = str(repair_payload.get("concept") or "").strip()
                    if repaired_concept:
                        repaired = _dc_replace(
                            contender,
                            concept=repaired_concept,
                            rejected_reason=None,
                        )
                        if _seed_audit(repaired) is None and not _deterministic_anti_pattern(
                            repaired
                        ):
                            reviewed, failed = await _independent_hook_review(repaired)
                reviewed_contenders.append(reviewed)
            reviewed_by_dimension = {
                candidate.dimension: candidate for candidate in reviewed_contenders
            }
            result.candidates = [
                reviewed_by_dimension.get(candidate.dimension, candidate)
                for candidate in result.candidates
            ]
            contenders = [
                candidate
                for candidate in reviewed_contenders
                if candidate.rejected_reason is None
                and candidate.composite is not None
                and (candidate.composite or 0.0) >= winner_min
            ]
            if not contenders:
                logger.warning(
                    "concept tournament: no independently qualified hooks; no injection"
                )
                return result

        # Prompt presence is not adherence.  Before a contender becomes the
        # shared source for every downstream agent, deterministically reject
        # explicit-option drift and the recurring debt/death default motifs.
        guarded_contenders: list[ConceptCandidate] = []
        guarded_by_concept: dict[str, ConceptCandidate] = {}
        for contender in contenders:
            hard_reason = _candidate_hard_rejection_reason(
                contender,
                seed_concept=seed_concept,
                tone_preference=tone_preference,
                effect_skills=effect_skills,
                cost_style=cost_style,
                allow_debt_theme=allow_debt_theme,
                allow_death_theme=allow_death_theme,
            )
            guarded = (
                _dc_replace(
                    contender,
                    rejected_reason="建书选项/反污染门失败: " + hard_reason,
                )
                if hard_reason
                else contender
            )
            guarded_by_concept[guarded.concept] = guarded
            if guarded.rejected_reason is None:
                guarded_contenders.append(guarded)
        result.candidates = [
            guarded_by_concept.get(candidate.concept, candidate)
            for candidate in result.candidates
        ]
        contenders = guarded_contenders
        if not contenders:
            logger.warning(
                "concept tournament: all contenders failed creation-intent/pollution gate"
            )
            return result

        # ── 5) 长篇决赛：只扩展独立终审前列，禁止反向修改故事种子 ─────────
        finalist_count = max(1, int(cfg.get("seriality_finalist_count", 2)))
        # 承载力属于冻结项目卡，不属于 promise/paradox/scene 三种广告切口。
        # 同一项目只保留钩子分最高的一条进入承载证明，避免随机扩写互相矛盾。
        finalists: list[ConceptCandidate] = []
        seen_premises: set[str] = set()
        for contender in sorted(
            contenders, key=lambda c: c.composite or 0.0, reverse=True
        ):
            premise_key = contender.dimension.split(":", 1)[0]
            if premise_key in seen_premises:
                continue
            seen_premises.add(premise_key)
            finalists.append(contender)
            if len(finalists) >= finalist_count:
                break
        seriality_finalists: list[ConceptCandidate] = []
        active_seriality_judge_fn = (
            finalist_seriality_judge_fn or seriality_judge_fn
        )

        async def _request_seriality_proof(
            current: ConceptCandidate, *, feedback: str = ""
        ) -> ConceptCandidate:
            if expand_fn is None:
                return _dc_replace(
                    current, rejected_reason="长篇承载失败: 缺SerialityProof"
                )
            try:
                if feedback:
                    system, user = _build_seriality_repair_messages(
                        candidate=current,
                        genre=genre,
                        chapter_count=chapter_count,
                        feedback=feedback,
                    )
                else:
                    system, user = _build_seriality_messages(
                        candidate=current,
                        genre=genre,
                        chapter_count=chapter_count,
                    )
                raw, run_id = await expand_fn(system, user)
                if run_id is not None:
                    result.llm_run_ids.append(run_id)
                clean = _dc_replace(
                    current,
                    rejected_reason=None,
                    seriality_report={},
                    seriality_judge={},
                )
                parsed = _apply_seriality_payload(clean, raw)
                return parsed or _dc_replace(
                    clean,
                    rejected_reason="长篇承载失败: SerialityProof无法解析",
                )
            except Exception:
                logger.warning(
                    "seriality expansion failed for one finalist", exc_info=True
                )
                return _dc_replace(
                    current,
                    rejected_reason="长篇承载失败: SerialityProof生成异常",
                )

        def _audit_seriality_proof(current: ConceptCandidate) -> ConceptCandidate:
            audit = _seriality_audit(current, target_chapters=chapter_count)
            if audit:
                return _dc_replace(
                    current, rejected_reason=f"长篇承载失败: {audit}"
                )
            from bestseller.services.seriality_capacity import (
                evaluate_seriality_capacity,
            )

            report = evaluate_seriality_capacity(
                {
                    "repeatable_story_unit": current.repeatable_story_unit,
                    "unit_families": current.unit_families,
                    "unit_frequency": current.unit_frequency,
                    "unit_count_estimate": current.unit_count_estimate,
                    "renewal_sources": current.renewal_sources,
                    "accumulation_tracks": current.accumulation_tracks,
                    "phase_transitions": current.phase_transitions,
                    "opposing_ecology": current.opposing_ecology,
                    "mystery_ladder": current.question_ladder,
                    "endgame_direction": current.endgame_direction,
                },
                target_chapters=chapter_count,
                require_phase_coverage=True,
            ).to_dict()
            return _dc_replace(current, seriality_report=report, rejected_reason=None)

        async def _judge_seriality_proof(
            current: ConceptCandidate,
        ) -> ConceptCandidate:
            if active_seriality_judge_fn is None:
                return current
            try:
                system, user = _build_seriality_judge_messages(
                    candidate=current, chapter_count=chapter_count
                )
                raw, run_id = await active_seriality_judge_fn(system, user)
                if run_id is not None:
                    result.llm_run_ids.append(run_id)
                verdict = _parse_json_object(raw)
                axes = (
                    "renewability",
                    "escalation",
                    "anti_reset",
                    "coherence",
                    "promise_survival",
                    "unit_density",
                )
                scores: dict[str, Any] = {
                    axis: max(0.0, min(10.0, float((verdict or {}).get(axis, 0))))
                    for axis in axes
                }
                scores["reason"] = str((verdict or {}).get("reason") or "")
                seriality_floors = (
                    cfg.get("seriality_hard_floors")
                    if isinstance(cfg.get("seriality_hard_floors"), dict)
                    else {}
                )
                failed = [
                    axis
                    for axis in axes
                    if float(scores[axis])
                    < float(seriality_floors.get(axis, 7.0))
                ]
                return _dc_replace(
                    current,
                    seriality_judge=scores,
                    rejected_reason=(
                        None
                        if not failed
                        else "长篇质量门失败: " + "/".join(failed)
                    ),
                )
            except Exception:
                logger.warning("seriality judge failed for one finalist", exc_info=True)
                return _dc_replace(
                    current, rejected_reason="长篇质量门失败: 判官异常"
                )

        for candidate in finalists:
            expanded = candidate
            # 2026-08-25：这里原本写死 `chapter_count >= 200`。追读性判官评的六条轴
            # （renewability / escalation / anti_reset / coherence / promise_survival /
            # unit_density）**全是「读者要不要追下去」的判据**，而 200 章的书在真机上
            # 几乎不存在——于是它对所有正常书完全空转，`seriality_judge` 恒为 {}，
            # 既看不出「评了没发现」还是「压根没跑」。真机 custom-xuanhuan-1787625194
            # （12 章）：has_decision 0/5、3/5 章 flat，全部只能等 12 章写完由正文层
            # 整书质量门拦下来——先写再拦。
            #
            # 分档而不是一刀放开：200 章以上维持原样（判官带杀权）；以下只跑判官
            # 并**留痕，不发否决**。理由是 config 里 2026-07-17 记着的教训——收紧
            # 概念层阈值会让淘汰赛干涸，而干涸的下场是注入保底概念，比任何被拒
            # 候选都差。本仓库对新检测器的规矩也是「只挣重生和留痕，不发杀权」。
            _seriality_mode, result.seriality_stage = seriality_stage_mode(
                chapter_count, cfg
            )
            if _seriality_mode != "skipped":
                for proof_attempt in range(2):
                    has_proof = bool(
                        expanded.core_promise_invariant.strip()
                        and expanded.repeatable_story_unit.strip()
                        and len(expanded.unit_families) >= 4
                        and expanded.unit_frequency.strip()
                        and expanded.renewal_sources
                        and expanded.accumulation_tracks
                        and expanded.phase_transitions
                        and expanded.opposing_ecology
                        and expanded.question_ladder
                        and expanded.endgame_direction.strip()
                    )
                    if not has_proof or proof_attempt > 0:
                        feedback = ""
                        if proof_attempt > 0:
                            feedback = expanded.rejected_reason or "承载证明未通过"
                            judge_reason = str(expanded.seriality_judge.get("reason") or "")
                            if judge_reason:
                                feedback = f"{feedback}；判官：{judge_reason}"
                        expanded = await _request_seriality_proof(
                            expanded, feedback=feedback
                        )
                    if expanded.rejected_reason is None:
                        expanded = _audit_seriality_proof(expanded)
                    if expanded.rejected_reason is None:
                        expanded = await _judge_seriality_proof(expanded)
                    if expanded.rejected_reason is None or expand_fn is None:
                        break
                # 只收 **LLM 判官** 那把刀，确定性结构筛的牙必须留着。
                # 两个否决源的前缀不同，这是唯一能把它们分开的现成标记：
                #   「长篇承载失败: …」= _audit_seriality_proof，字段缺失/容量不足，
                #                        确定性可复算，从未被证伪，继续发否决；
                #   「长篇质量门失败: …」= _judge_seriality_proof，六轴 LLM 打分，
                #                        实测 AUC 0.37/0.38（与现实反相关），收刀。
                # 2026-08-28 第一版补丁把两者一起清了，被
                # test_candidate_without_capacity_proof_rejected_for_long_target
                # 当场抓出——那正是「一刀切会连好闸门一起废掉」的形状。
                _veto_is_from_judge = str(expanded.rejected_reason or "").startswith(
                    "长篇质量门失败"
                )
                if _seriality_mode == "advisory" and _veto_is_from_judge:
                    # advisory 档只留痕不否决：把判词搬进回执，清掉杀权。
                    # 没有这一步，一个从未在短篇上校准过的判官会直接开始毙候选，
                    # 那正是 2026-07-17 干涸事故的形状。
                    result.seriality_stage.setdefault("advisory_findings", []).append(
                        {
                            "concept": expanded.concept,
                            "reason": expanded.rejected_reason,
                            "scores": dict(expanded.seriality_judge or {}),
                        }
                    )
                    expanded = _dc_replace(expanded, rejected_reason=None)

            seriality_finalists.append(expanded)

        finalist_by_concept = {c.concept: c for c in seriality_finalists}
        result.candidates = [
            finalist_by_concept.get(c.concept, c) for c in result.candidates
        ]
        passed = [c for c in seriality_finalists if c.rejected_reason is None]
        if not passed:
            logger.warning("concept tournament: no seriality-qualified finalists")
            return result
        passed = _prefer_ontology_clean(passed, genre_intent_contract)
        result.winner = max(passed, key=lambda c: c.composite or 0.0)
        # 「无代价≠无限制」验收：只留痕不改判，按本仓库对新检测器的规矩
        # （2026-08-27）。此前 prompt 要求它、数据类没有它、零消费方——
        # 规则写了没实现，「限制」这一维度从未被审过。
        result.constraint_ladder_audit = audit_constraint_ladder(
            result.winner, chapter_count=chapter_count, cost_style=cost_style
        )
        return result
    except Exception:
        logger.warning("concept tournament failed (non-fatal); no injection", exc_info=True)
        result.winner = None
        return result


def render_high_concept_block(result: ConceptTournamentResult) -> str:
    """冠军概念 → 注入 ctx['description'] 的硬约束块（下游全 prompt 可见）。"""

    winner = result.winner
    if winner is None:
        return ""
    ladder = "→".join(winner.question_ladder[:3])
    parts = [
        "",
        "【本书已选定高概念——全程必须严格围绕它展开，禁止回归题材默认套路】",
        f"高概念：{winner.concept}",
        f"核心机制（可反复运转的引擎，不是一次性信息）：{winner.mechanism}",
        f"认知缺口：{winner.hook_question}",
        f"长线引擎：进度条={winner.progress_bar}；问题梯={ladder}；中期战场(约第50章)={winner.ch50}",
    ]
    if result.banned_cliches:
        parts.append("俗套库已在生成后完成程序化筛查；禁用样本文本不向下游传播。")
    return "\n".join(parts)


def dry_tournament_rejection_summary(
    candidates: list[ConceptCandidate] | tuple[ConceptCandidate, ...],
    *,
    max_lines: int = 6,
    concept_chars: int = 60,
) -> list[str]:
    """User-facing lines explaining WHY a dry tournament rejected everything.

    A dry run (winner=None after all attempts) used to surface only as a
    downstream logline-gate verdict, which misattributed the failure — the
    2026-07-24 user read a 3.0 cost_integrity reject and concluded the book
    had been "lost". The abort error must instead carry the tournament's own
    per-candidate rejection evidence, bounded so it stays readable.
    """

    lines: list[str] = []
    for candidate in candidates:
        if len(lines) >= max_lines:
            break
        concept = (candidate.concept or "").strip()
        if len(concept) > concept_chars:
            concept = concept[: concept_chars - 1] + "…"
        reason = (candidate.rejected_reason or "").strip() or "未通过（原因未记录）"
        label = concept or f"({candidate.dimension or '未命名候选'})"
        lines.append(f"候选「{label}」：{reason}")
    if not lines:
        # Screen-dry path: every candidate died before annotation, or the
        # generator produced none at all. Still give the user a real sentence.
        lines.append(
            "淘汰赛没有产出任何可评审的候选（生成或确定性筛全灭），"
            "本次创意方向可能与题材/受众锚点冲突。"
        )
    return lines


__all__ = [
    "ConceptCandidate",
    "ConceptTournamentResult",
    "dry_tournament_rejection_summary",
    "load_concept_tournament_config",
    "render_high_concept_block",
    "resolve_banned_cliches",
    "resolve_tournament_config",
    "run_concept_tournament",
]
