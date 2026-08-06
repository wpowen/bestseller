"""简介独立文案工序（T6, 2026-07-09）。

审计根因：简介此前由 conception finalize 顺手直出——一次 LLM 调用同时产
premise/writing_profile/简介，输入是全部设计 JSON，机制黑话天然漏进读者文案
（真机案例：'保饭碗还是丢工作？'同义反复选择句、'共情被削薄'等设计术语直接
出现在简介里）。

设计原则：简介是产品不是元数据。本模块把简介从"顺手产物"改成独立文案工序：

  1. 输入收窄——只给 spine 六字段 + premise + 金手指一句大白话 + 画像锚 +
     题材情绪范例 + 平台字数带 + 书名。禁止传入三提案 JSON / kernel / 方法论块 /
     world_model：设计视角进不来，黑话就漏不出去。
  2. N 路候选——不同策略角度独立生成（场景钩/身份反差/金手指爽点/规则悬念，
     按题材路由），而不是让模型一次定稿。
  3. 确定性病理筛——``blurb_pathology.detect_blurb_pathology`` 杀病句/黑话/
     模板残留，``blurb_appeal_gate.evaluate_blurb_appeal`` 打点击力分。
  4. 画像判官淘汰赛——``persona_click_judge`` 模拟目标读者 3 秒点不点，冠军
     取平均分最高者；判官不可用时降级为 gate 分排序（不阻塞整个工序）。
  5. 定向打磨——冠军仍不达标时按反馈聚焦重写一次（有界，不无限循环）。
  6. 永不劣于现状——冠军为空、或全部候选都命中致命病理时直接回退 v0；干净的
     冠军若是靠画像判官选出来的，不再用确定性 gate 分去否决它（v0 从未跑过
     persona 评估，两者不是同一把尺）；只有判官不可用、排序降级为 gate 分时，
     才需要 gate 分真的赢过 v0 才放行，否则回退 v0，``fell_back_to_v0=True``。

零依赖 conception.py（避免循环导入——conception.py 反过来调用本模块）。
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
import json
import logging
from typing import Any, Protocol

from bestseller.services.blurb_pathology import PathologyFinding, detect_blurb_pathology

logger = logging.getLogger(__name__)

# ruff: noqa: RUF001, RUF002 — Chinese fixtures/prompts are intentional.

_DEFAULT_STRATEGIES: dict[str, tuple[str, ...]] = {
    "default": ("scene_hook", "identity_contrast", "golden_finger_flex"),
    "suspense": ("scene_hook", "identity_contrast", "rule_suspense"),
}

_STRATEGY_DIRECTIVES: dict[str, str] = {
    "scene_hook": (
        "开局策略=场景钩：首句必须是一个具体时刻——谁、在哪、正在做什么，"
        "读者要像看见一个画面，不许用抽象陈述句开头。"
    ),
    "identity_contrast": (
        "开局策略=身份反差：首句先亮出主角的身份，紧接一个反差（身份与处境、"
        "或身份与真相之间的错位），让人立刻想知道为什么。"
    ),
    "golden_finger_flex": (
        "开局策略=金手指高能：首句或次句必须让读者秒懂主角有什么不一样的"
        "本事或优势，直给爽点，不绕弯子。"
    ),
    "rule_suspense": (
        "开局策略=规则悬念：首句立一条具体、反常的规则或异象，让人立刻想"
        "知道这条规则背后藏着什么。"
    ),
}

_SUSPENSE_TOKENS = ("悬疑", "推理", "怪谈", "恐怖", "惊悚", "灵异", "诡异", "犯罪")


class GeneratorFn(Protocol):
    """(system_prompt, user_prompt) -> (raw_text, llm_run_id)."""

    def __call__(self, system_prompt: str, user_prompt: str) -> Awaitable[tuple[str, Any]]: ...


@dataclass(frozen=True)
class BlurbCandidate:
    """One generated simple candidate + its scoring evidence."""

    strategy: str
    synopsis: str
    gate_score: float | None = None
    pathology: tuple[PathologyFinding, ...] = ()
    persona_click_rate: float | None = None
    persona_avg_score: float | None = None
    llm_run_id: Any = None

    @property
    def has_fatal_pathology(self) -> bool:
        return any(f.severity == "fatal" for f in self.pathology)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "synopsis": self.synopsis[:220],
            "gate_score": self.gate_score,
            "pathology": [f.to_dict() for f in self.pathology],
            "persona_click_rate": self.persona_click_rate,
            "persona_avg_score": self.persona_avg_score,
        }


@dataclass
class BlurbCopywritingResult:
    """The full tournament record + the champion synopsis text."""

    champion: str
    champion_strategy: str
    candidates: list[BlurbCandidate] = field(default_factory=list)
    polish_rounds: int = 0
    fell_back_to_v0: bool = False
    persona_used: bool = False
    llm_run_ids: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "champion_strategy": self.champion_strategy,
            "candidates": [c.to_dict() for c in self.candidates],
            "polish_rounds": self.polish_rounds,
            "fell_back_to_v0": self.fell_back_to_v0,
            "persona_used": self.persona_used,
            "schema_version": "blurb-copywriting.v1",
        }


def load_copywriting_config(config: dict[str, Any] | None) -> dict[str, Any]:
    cfg = config.get("copywriting", {}) if isinstance(config, dict) else {}
    if not isinstance(cfg, dict):
        cfg = {}
    strategies = cfg.get("strategies") if isinstance(cfg.get("strategies"), dict) else {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "n_candidates": int(cfg.get("n_candidates", 3)),
        "persona_samples": int(cfg.get("persona_samples", 2)),
        "max_polish_rounds": int(cfg.get("max_polish_rounds", 1)),
        # The production acceptance bar is deliberately lower to avoid blocking a
        # whole book. A reader-facing candidate below this target still gets its
        # one bounded editorial pass before it can be surfaced as the champion.
        "target_gate_score": float(cfg.get("target_gate_score", 80)),
        "strategies": {
            "default": tuple(strategies.get("default") or _DEFAULT_STRATEGIES["default"]),
            "suspense": tuple(strategies.get("suspense") or _DEFAULT_STRATEGIES["suspense"]),
        },
    }


def _resolve_strategy_bucket(genre: str, sub_genre: str) -> str:
    from bestseller.services.genre_taxonomy import canonicalize

    canonical = str(canonicalize(genre, sub_genre) or "").lower()
    blob = f"{canonical} {genre} {sub_genre}".lower()
    if any(token in blob for token in _SUSPENSE_TOKENS):
        return "suspense"
    return "default"


def _truncate(text: str, limit: int) -> str:
    from bestseller.services.blurb_pathology import truncate_at_sentence

    return truncate_at_sentence(text or "", limit)


def _build_candidate_messages(
    strategy: str,
    *,
    spine: dict[str, Any],
    premise: str,
    golden_finger_line: str,
    title: str,
    tags: list[str],
    genre: str,
    sub_genre: str,
    platform: str | None,
    persona: Any,
    emotion_exemplars: tuple[str, ...],
    book_jargon_terms: tuple[str, ...],
    band: tuple[int, int],
) -> tuple[str, str]:
    directive = _STRATEGY_DIRECTIVES.get(strategy, _STRATEGY_DIRECTIVES["scene_hook"])
    lo, hi = band
    jargon_ban = "、".join(book_jargon_terms[:12]) if book_jargon_terms else "（无）"
    del emotion_exemplars  # (2026-08-01) framework event menus no longer enter prompts
    spine_block = "\n".join(
        f"  {k}：{v}" for k, v in spine.items() if str(v or "").strip()
    )
    system = (
        "你是顶尖中文网文详情页文案师，只写给完全不懂本书设定的陌生读者看。"
        "你的任务不是复述设定，是让人3秒内产生'这个我没见过但我秒懂'的冲动点击。"
    )
    user = (
        f"【故事脊柱】\n{spine_block}\n\n"
        f"【故事核】{_truncate(premise, 300)}\n\n"
        f"【金手指/核心规则一句话】{golden_finger_line or '（无）'}\n\n"
        f"【书名】{title}\n"
        f"【频道】{getattr(persona, 'channel', '通用')}\n"
        "【情绪事件】从本书自己的前提与冲突里选最强的高唤起事件前置，不套其他题材的情绪词。\n\n"
        f"{directive}\n\n"
        f"硬性要求：\n"
        f"①字数 {lo}-{hi} 字，分 2-4 段；\n"
        "②首句≤30字；③禁止出现设计/机制黑话——尤其是这些词："
        f"{jargon_ban}；出现即视为不合格；\n"
        "④不得剧透结局；结尾悬念必须落在一个【具体的、即将发生的】威胁、选择或期限上"
        "（如'第七天日落前根会碰到妹妹心口'），禁止'殊不知/却不知道/她自己都不知道/"
        "到底还瞒着她什么'这类全知旁白式吊胃口；\n"
        "⑤零AI腔（本以为/却没想到/命运的齿轮/何去何从/敬请期待）；\n"
        "⑥只输出正文，不要小标题；\n"
        "⑦设定里的学术词/机构名/生造术语（拓扑、语义、某某署这类）一律翻译成"
        "读者秒懂的大白话或具体画面——你在给完全不懂设定的人卖书，不是给设定"
        "集写目录；机制再聪明，说不成人话就是废稿。\n"
        '只输出 JSON：{"synopsis": "..."}，不要解释。'
    )
    return system, user


def _parse_synopsis_json(raw: str) -> str:
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
    if isinstance(payload, dict):
        return str(payload.get("synopsis") or "").strip()
    return ""


async def _default_generator(session: Any, settings: Any) -> GeneratorFn:
    async def _call(system_prompt: str, user_prompt: str) -> tuple[str, Any]:
        from bestseller.services.llm import LLMCompletionRequest, complete_text

        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="editor",
                model_tier="strong",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response="{}",
                prompt_template="blurb_copywriter_candidate",
                prompt_version="v1",
                max_tokens_override=700,
            ),
        )
        return completion.content or "", completion.llm_run_id

    return _call


async def _polish_champion(
    session: Any,
    settings: Any,
    *,
    synopsis: str,
    feedback: str,
    genre: str,
    sub_genre: str,
    language: str,
) -> tuple[str, Any]:
    """One bounded focused-rewrite pass on the tournament champion."""

    from bestseller.services.llm import LLMCompletionRequest, complete_text

    system = "你是顶尖中文网文详情页文案编辑，专精把不达标的简介按诊断意见改到位。"
    user = (
        f"题材：{genre}（{sub_genre}）\n当前简介：\n{synopsis}\n\n"
        f"诊断意见：\n{feedback}\n\n"
        "请按诊断意见逐条改写这段简介：先给具体冲突，再讲规则代价，最后留下一个"
        "必须继续看的选择。删掉口语凑句、设定清单、泛泛反问和任何解释给策划看的话。"
        "读者只该看到人物正在被什么逼到墙角。"
        '只输出 JSON：{"synopsis": "..."}，不要解释。'
    )
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="editor",
            model_tier="strong",
            system_prompt=system,
            user_prompt=user,
            fallback_response=json.dumps({"synopsis": synopsis}, ensure_ascii=False),
            prompt_template="blurb_copywriter_polish",
            prompt_version="v1",
            max_tokens_override=700,
            metadata={"language": language},
        ),
    )
    rewritten = _parse_synopsis_json(completion.content or "")
    return (rewritten or synopsis), completion.llm_run_id


async def run_blurb_copywriting(
    session: Any,
    settings: Any,
    *,
    spine: dict[str, Any],
    premise: str,
    golden_finger_line: str,
    title: str,
    tags: list[str],
    genre: str,
    sub_genre: str,
    platform: str | None,
    language: str,
    v0_synopsis: str,
    emotion_exemplars: tuple[str, ...] = (),
    book_jargon_terms: tuple[str, ...] = (),
    config: dict[str, Any] | None = None,
    generator: GeneratorFn | None = None,
    persona_judge: Any = None,
) -> BlurbCopywritingResult:
    """Run the independent blurb copywriting tournament. Never raises.

    ``generator``/``persona_judge`` are injectable for tests; production calls
    omit them and get the real LLM-backed defaults.
    """

    cfg = load_copywriting_config(config)
    if not cfg["enabled"]:
        return BlurbCopywritingResult(
            champion=v0_synopsis, champion_strategy="v0_disabled", fell_back_to_v0=True,
        )

    from bestseller.services.blurb_appeal_gate import (
        evaluate_blurb_appeal,
        platform_blurb_band,
    )
    from bestseller.services.genre_persona import resolve_persona

    llm_run_ids: list[Any] = []
    try:
        # 设置阶段本身也要 fail-open：画像/字数带/策略桶解析任何一步炸了都不该
        # 让"Never raises"的docstring落空——调用方(conception.py)虽然有外层
        # try/except兜底，但那样整个文案工序连"回退v0"的报告都拿不到，直接
        # 静默跳过；这里失败仍应产出一份可持久化的 v0 回退结果。
        persona = resolve_persona(genre, sub_genre, tuple(tags or ()))
        band = platform_blurb_band(platform, config)
        bucket = _resolve_strategy_bucket(genre, sub_genre)
        strategies = cfg["strategies"].get(bucket) or cfg["strategies"]["default"]
        strategies = tuple(strategies)[: max(1, cfg["n_candidates"])]
        gen_fn = generator
        if gen_fn is None:
            gen_fn = await _default_generator(session, settings)
    except Exception:
        logger.warning("blurb copywriting setup failed (non-fatal)", exc_info=True)
        return BlurbCopywritingResult(
            champion=v0_synopsis, champion_strategy="v0_setup_failed", fell_back_to_v0=True,
        )

    candidates: list[BlurbCandidate] = []
    for strategy in strategies:
        try:
            system, user = _build_candidate_messages(
                strategy,
                spine=spine, premise=premise, golden_finger_line=golden_finger_line,
                title=title, tags=tags, genre=genre, sub_genre=sub_genre,
                platform=platform, persona=persona, emotion_exemplars=emotion_exemplars,
                book_jargon_terms=book_jargon_terms, band=band,
            )
            raw, run_id = await gen_fn(system, user)
            if run_id is not None:
                llm_run_ids.append(run_id)
            synopsis = _parse_synopsis_json(raw)
            if not synopsis:
                continue
            pathology = tuple(
                detect_blurb_pathology(synopsis, book_jargon_terms=book_jargon_terms)
            )
            verdict = evaluate_blurb_appeal(
                title=title, synopsis=synopsis, premise=premise, tags=tags,
                genre=genre, sub_genre=sub_genre, language=language, platform=platform,
                book_jargon_terms=book_jargon_terms,
            )
            candidates.append(
                BlurbCandidate(
                    strategy=strategy, synopsis=synopsis,
                    gate_score=verdict.total, pathology=pathology,
                )
            )
        except Exception:
            logger.warning("blurb copywriting candidate '%s' failed", strategy, exc_info=True)

    persona_used = False
    if candidates:
        try:
            from bestseller.services.persona_click_judge import run_persona_click_judge

            persona_used = True
            persona_candidates = list(candidates)
            for idx, cand in enumerate(persona_candidates):
                report = await run_persona_click_judge(
                    session, settings,
                    title=title, synopsis=cand.synopsis, genre=genre, sub_genre=sub_genre,
                    tags=tuple(tags or ()), samples=cfg["persona_samples"], judge=persona_judge,
                )
                persona_candidates[idx] = BlurbCandidate(
                    strategy=cand.strategy, synopsis=cand.synopsis,
                    gate_score=cand.gate_score, pathology=cand.pathology,
                    persona_click_rate=report.click_rate if report.llm_used else None,
                    persona_avg_score=report.avg_score if report.llm_used else None,
                )
            candidates = persona_candidates
            if not any(c.persona_avg_score is not None for c in candidates):
                persona_used = False
        except Exception:
            logger.warning("persona tournament failed; ranking by gate score", exc_info=True)
            persona_used = False

    # Score every generated candidate for a complete audit trail, but keep
    # fatal-pathology candidates ineligible for selection.
    survivors = [c for c in candidates if not c.has_fatal_pathology] or list(candidates)

    def _rank_key(c: BlurbCandidate) -> tuple[float, float]:
        return (
            c.persona_avg_score if c.persona_avg_score is not None else -1.0,
            c.gate_score or 0.0,
        )

    champion = max(survivors, key=_rank_key) if survivors else None

    polish_rounds = 0
    if champion is not None:
        try:
            from bestseller.services.story_appeal import (
                build_improvement_feedback,
                load_story_appeal_config,
            )
        except ImportError:
            build_improvement_feedback = None  # type: ignore[assignment]
            load_story_appeal_config = None  # type: ignore[assignment]

        blurb_min = float(
            ((config or {}).get("meets_bar", {}) or {}).get("blurb_min", 68)
        )
        max_polish = cfg["max_polish_rounds"]
        needs_polish = (champion.gate_score or 0.0) < max(
            blurb_min, float(cfg["target_gate_score"])
        ) or any(
            f.severity == "warn" for f in champion.pathology
        )
        if needs_polish and max_polish > 0 and build_improvement_feedback:
            try:
                _appeal_cfg = load_story_appeal_config() if load_story_appeal_config else (config or {})
                verdict = evaluate_blurb_appeal(
                    title=title, synopsis=champion.synopsis, premise=premise, tags=tags,
                    genre=genre, sub_genre=sub_genre, language=language, platform=platform,
                    book_jargon_terms=book_jargon_terms,
                )
                from bestseller.domain.appeal import PremiseAppealVerdict, StoryAppealReport

                fake_report = StoryAppealReport(
                    genre=genre, sub_genre=sub_genre,
                    premise=PremiseAppealVerdict(total=0, grade="pass", gated_grade="pass"),
                    blurb=verdict, meets_bar=verdict.total >= blurb_min,
                    overall_grade=verdict.grade,
                )
                feedback = build_improvement_feedback(fake_report, _appeal_cfg)
                polished, run_id = await _polish_champion(
                    session, settings, synopsis=champion.synopsis, feedback=feedback,
                    genre=genre, sub_genre=sub_genre, language=language,
                )
                if run_id is not None:
                    llm_run_ids.append(run_id)
                polished_pathology = tuple(
                    detect_blurb_pathology(polished, book_jargon_terms=book_jargon_terms)
                )
                polished_verdict = evaluate_blurb_appeal(
                    title=title, synopsis=polished, premise=premise, tags=tags,
                    genre=genre, sub_genre=sub_genre, language=language, platform=platform,
                    book_jargon_terms=book_jargon_terms,
                )
                polish_rounds = 1
                if (
                    not any(f.severity == "fatal" for f in polished_pathology)
                    and polished_verdict.total >= (champion.gate_score or 0.0)
                ):
                    champion = BlurbCandidate(
                        strategy=champion.strategy, synopsis=polished,
                        gate_score=polished_verdict.total, pathology=polished_pathology,
                        persona_click_rate=champion.persona_click_rate,
                        persona_avg_score=champion.persona_avg_score,
                    )
            except Exception:
                logger.warning("blurb champion polish failed (non-fatal)", exc_info=True)

    v0_verdict_total = 0.0
    try:
        v0_verdict = evaluate_blurb_appeal(
            title=title, synopsis=v0_synopsis, premise=premise, tags=tags,
            genre=genre, sub_genre=sub_genre, language=language, platform=platform,
            book_jargon_terms=book_jargon_terms,
        )
        v0_verdict_total = v0_verdict.total
    except Exception:
        logger.warning("v0 synopsis scoring failed (non-fatal)", exc_info=True)

    # 结构性废单：champion 为空，或 champion 是"全员致命病理"兜底出来的候选
    # （此时 has_fatal_pathology 恒真——只要 survivors 里有一个干净候选，champion
    # 就不可能带 fatal 病理）。这两种情况必须回退 v0，不看任何分数。
    if champion is None or champion.has_fatal_pathology:
        return BlurbCopywritingResult(
            champion=v0_synopsis, champion_strategy="v0_fallback",
            candidates=candidates, polish_rounds=polish_rounds,
            fell_back_to_v0=True, persona_used=persona_used, llm_run_ids=llm_run_ids,
        )

    # 干净的冠军若是画像判官淘汰赛选出来的（persona_used），不再用确定性 gate
    # 分去否决它——gate 分和 persona 判断的读者视角不是同一把尺，v0 从未跑过
    # persona 评估，拿 gate 分单方面比会出现真实发生过的错序（真机验证：同一
    # 题材下，具体写实的候选 gate=66.0 分反而低于泛泛套话稿 gate=67.2 分）。
    # persona 淘汰赛已经是比 gate 分更贴近"读者会不会点"的信号，不该被它推翻。
    if persona_used and champion.persona_avg_score is not None:
        return BlurbCopywritingResult(
            champion=champion.synopsis, champion_strategy=champion.strategy,
            candidates=candidates, polish_rounds=polish_rounds,
            fell_back_to_v0=False, persona_used=persona_used, llm_run_ids=llm_run_ids,
        )

    # persona 不可用（判官全废/未启用），排序降级为确定性 gate 分——这时才用
    # 和 v0 同一把尺比较，比不过就回退。
    if (champion.gate_score or 0.0) < v0_verdict_total:
        return BlurbCopywritingResult(
            champion=v0_synopsis, champion_strategy="v0_fallback",
            candidates=candidates, polish_rounds=polish_rounds,
            fell_back_to_v0=True, persona_used=persona_used, llm_run_ids=llm_run_ids,
        )

    return BlurbCopywritingResult(
        champion=champion.synopsis, champion_strategy=champion.strategy,
        candidates=candidates, polish_rounds=polish_rounds,
        fell_back_to_v0=False, persona_used=persona_used, llm_run_ids=llm_run_ids,
    )


__all__ = [
    "BlurbCandidate",
    "BlurbCopywritingResult",
    "load_copywriting_config",
    "run_blurb_copywriting",
]
