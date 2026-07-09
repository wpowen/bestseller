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
  3. 引擎审计（确定性）——候选必须答出进度条/问题梯前三级/第50章在打什么，
     答不出 = 撑不住长篇，出局。
  4. 判官淘汰赛——三轴（新颖度对撞榜单参照集/想点欲/不可预测性），冠军须过
     winner_min 线，否则不注入（宁可回落现状，不硬塞烂概念）。
  5. 冠军注入 ctx["description"]——它是全部下游 prompt（商业定位/市场/角色/
     世界观）的共同源头，零侵入全覆盖。

零依赖 conception.py（它反过来调用本模块）。fail-open：任何一步失败都回落
到现状（无注入），绝不阻断构思。
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Awaitable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import yaml

logger = logging.getLogger(__name__)

# ruff: noqa: RUF001, RUF002 — Chinese prompts/fixtures are intentional.

_CONTROL_DIMENSION = "纯题材对照"


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
    progress_bar: str = ""
    question_ladder: tuple[str, ...] = ()
    ch50: str = ""
    judge_freshness: float | None = None
    judge_click: float | None = None
    judge_predictable: float | None = None
    judge_reason: str = ""
    composite: float | None = None
    rejected_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "concept": self.concept,
            "mechanism": self.mechanism,
            "hook_question": self.hook_question,
            "progress_bar": self.progress_bar,
            "question_ladder": list(self.question_ladder),
            "ch50": self.ch50,
            "judge_freshness": self.judge_freshness,
            "judge_click": self.judge_click,
            "judge_predictable": self.judge_predictable,
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "winner_dimension": self.winner.dimension if self.winner else None,
            "winner_concept": self.winner.concept if self.winner else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "banned_cliches": list(self.banned_cliches),
            "schema_version": "concept-tournament.v1",
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
    关键词（≥2 字的连续词元）命中 2 个以上才算撞车，压误伤。
    """

    text = f"{candidate.concept} {candidate.mechanism} {candidate.hook_question}"
    hits: list[str] = []
    for phrase in banned:
        tokens = [t for t in _phrase_tokens(phrase) if len(t) >= 2]
        if not tokens:
            continue
        matched = sum(1 for t in tokens if t in text)
        if matched >= 2 or (len(tokens) == 1 and matched == 1):
            hits.append(phrase)
    return hits


def _phrase_tokens(phrase: str) -> list[str]:
    """把禁用短语切成词元：连续 2-4 字滑窗太糙，这里按常见分隔+双字滑窗。"""

    phrase = phrase.strip()
    if not phrase:
        return []
    # 双字滑窗：中文短语无空格，双字词元是俗套识别的最小稳定单元。
    return [phrase[i : i + 2] for i in range(0, len(phrase) - 1)]


def _engine_audit(candidate: ConceptCandidate) -> str | None:
    """几百章引擎审计：答不出进度条/问题梯(≥3且互异)/第50章 = 撑不住长篇。"""

    if not candidate.concept.strip() or not candidate.mechanism.strip():
        return "concept/mechanism 为空"
    if not candidate.progress_bar.strip():
        return "缺进度条(读者看什么在涨)"
    ladder = [q.strip() for q in candidate.question_ladder if q.strip()]
    if len(dict.fromkeys(ladder)) < 3:
        return "问题梯不足3级互异问题"
    if not candidate.ch50.strip():
        return "答不出第50章在打什么"
    normalized_ch50 = candidate.ch50.strip()
    if normalized_ch50 == candidate.progress_bar.strip():
        return "第50章与进度条同文(敷衍)"
    return None


def _build_candidate_messages(
    *,
    genre: str,
    sub_genre: str,
    dimension: str,
    chapter_count: int,
    banned: tuple[str, ...],
    avoid_mechanisms_block: str,
) -> tuple[str, str]:
    system = (
        "你是顶级网文制作人，专出'榜单编辑看到会立刻加价买断'的高概念。"
        "你深知平庸的本质是可预测：读者一句话能自动补全全书的概念一文不值。"
        "你的每个概念必须让人产生'等等，这怎么成立？'的认知缺口。"
    )
    if dimension == _CONTROL_DIMENSION:
        hybrid_directive = (
            "本路为纯题材对照组：不引入外部领域，但必须在题材内部找到一个"
            "反共识的切入角（反的是读者共识，不是主角处境）。"
        )
    else:
        hybrid_directive = (
            f"本路强制杂交：把【{genre}】与【{dimension}】这个异质领域硬性融合，"
            "在两者交点上找没人写过的位置。杂交必须是概念级的（世界规则/主角"
            "职业/冲突形态由该领域重塑），不是把该领域词汇当皮肤贴上去。"
        )
    ban_block = "\n".join(f"- {b}" for b in banned) if banned else "（无）"
    user = (
        f"【题材】{genre}（{sub_genre}）｜目标体量：{chapter_count}章起步、可扩展到几百章\n\n"
        f"{hybrid_directive}\n\n"
        f"【本题材烂大街俗套——出现任何一条的变体即为废稿】\n{ban_block}\n"
        f"{avoid_mechanisms_block}\n"
        "【硬性要求】\n"
        "①概念一句话≤60字，必须含认知缺口（读者无法自动补全后续）；\n"
        "②核心机制必须是可反复运转的引擎（每章都能产出新冲突），不是一次性信息"
        "（'主角知道哪里有宝'这类先知型金手指=废稿）；\n"
        "③必须自带长篇引擎：读者追更时看什么数字/阶梯在涨（进度条）、"
        "前三级悬念问题各是什么（问题梯，一级比一级大）、第50章大概在打什么仗；\n"
        "④主角的优势必须反共识（把某个被轻视的东西做成最强，或把某个共识证伪）。\n\n"
        "只输出 JSON：\n"
        "{\"concept\": \"一句话高概念\", \"mechanism\": \"核心机制一句话\", "
        "\"hook_question\": \"读者的认知缺口疑问\", \"progress_bar\": \"进度条\", "
        "\"question_ladder\": [\"一级悬念\", \"二级悬念\", \"三级悬念\"], "
        "\"ch50\": \"第50章大概在打什么\"}"
    )
    return system, user


def _build_judge_messages(
    *,
    candidate: ConceptCandidate,
    genre: str,
    references: list[dict[str, str]],
) -> tuple[str, str]:
    ref_lines = "\n".join(
        f"- 《{r.get('title', '')}》：{str(r.get('blurb', '')).strip()[:80]}"
        for r in references[:4]
    ) or "（无参照）"
    system = (
        "你是挑剔的网文榜单主编，每天毙掉几十个平庸选题。你只回答 JSON，"
        "评分严格：见过类似的就是不新鲜，能猜到后续就是可预测，不想点就是不想点。"
    )
    user = (
        f"【待评概念】（{genre}）\n"
        f"概念：{candidate.concept}\n机制：{candidate.mechanism}\n"
        f"认知缺口：{candidate.hook_question}\n\n"
        f"【榜单在售参照（对撞用）】\n{ref_lines}\n\n"
        "三轴打分（0-10，整数或一位小数）：\n"
        "1. freshness 新颖度：与参照集和你见过的全部网文对撞，这个概念的核心组合"
        "有没有人写过？换皮不算新。\n"
        "2. click 想点欲：只看这个概念一句话，目标读者3秒内想不想点进去？\n"
        "3. predictable 可预测性：你能不能自动补全这本书接下来的主线走向？"
        "（能补全=高分=坏事）\n"
        '只输出 JSON：{"freshness": 0-10, "click": 0-10, "predictable": 0-10, '
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
    return payload if isinstance(payload, dict) else None


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
    candidate = ConceptCandidate(
        dimension=dimension,
        concept=str(payload.get("concept") or "").strip(),
        mechanism=str(payload.get("mechanism") or "").strip(),
        hook_question=str(payload.get("hook_question") or "").strip(),
        progress_bar=str(payload.get("progress_bar") or "").strip(),
        question_ladder=ladder,
        ch50=str(payload.get("ch50") or "").strip(),
    )
    return candidate if candidate.concept else None


async def _default_generator(session: Any, settings: Any, *, template: str) -> GeneratorFn:
    async def _call(system_prompt: str, user_prompt: str) -> tuple[str, Any]:
        from bestseller.services.llm import LLMCompletionRequest, complete_text

        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="planner",
                model_tier="strong",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response="{}",
                prompt_template=template,
                prompt_version="v1",
                max_tokens_override=800,
            ),
        )
        return completion.content or "", completion.llm_run_id

    return _call


def _render_avoid_mechanisms_block(avoid_mechanisms: list[dict[str, Any]]) -> str:
    entries = [
        str(item.get("golden_finger") or item.get("premise") or "").strip()[:60]
        for item in avoid_mechanisms
        if isinstance(item, dict)
    ]
    entries = [e for e in entries if e][:6]
    if not entries:
        return ""
    lines = "\n".join(f"- {e}" for e in entries)
    return f"【本站同题材旧书已用机制——不得复用其核心】\n{lines}\n"


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
    rng: random.Random | None = None,
) -> ConceptTournamentResult:
    """跑一轮概念淘汰赛。永不 raise（fail-open：失败→winner=None→无注入）。

    ``generator``/``judge`` 可注入（测试）；``rng`` 可注入固定维度抽样。
    """

    cfg = config if config is not None else load_concept_tournament_config()
    if not isinstance(cfg, dict) or not bool(cfg.get("enabled", True)):
        return ConceptTournamentResult()

    result = ConceptTournamentResult()
    try:
        banned = resolve_banned_cliches(genre, sub_genre, cfg)
        result.banned_cliches = banned
        n_candidates = max(2, int(cfg.get("n_candidates", 4)))
        pool = [str(d) for d in (cfg.get("dimension_pool") or []) if str(d).strip()]
        rand = rng if rng is not None else random.Random()
        hybrid_count = min(max(1, n_candidates - 1), len(pool)) if pool else 0
        dimensions = (
            rand.sample(pool, hybrid_count) if hybrid_count else []
        ) + [_CONTROL_DIMENSION]

        gen_fn = generator
        if gen_fn is None:
            gen_fn = await _default_generator(
                session, settings, template="concept_tournament_candidate"
            )
        judge_fn = judge
        if judge_fn is None:
            judge_fn = await _default_generator(
                session, settings, template="concept_tournament_judge"
            )

        avoid_block = _render_avoid_mechanisms_block(avoid_mechanisms or [])

        # ── 1) 候选生成 ────────────────────────────────────────────────
        candidates: list[ConceptCandidate] = []
        for dimension in dimensions:
            try:
                system, user = _build_candidate_messages(
                    genre=genre, sub_genre=sub_genre, dimension=dimension,
                    chapter_count=chapter_count, banned=banned,
                    avoid_mechanisms_block=avoid_block,
                )
                raw, run_id = await gen_fn(system, user)
                if run_id is not None:
                    result.llm_run_ids.append(run_id)
                candidate = _parse_candidate(raw, dimension)
                if candidate is not None:
                    candidates.append(candidate)
            except Exception:
                logger.warning(
                    "concept candidate generation failed (dimension=%s)",
                    dimension, exc_info=True,
                )

        # ── 2) 确定性筛：俗套命中 + 引擎审计 ───────────────────────────
        from dataclasses import replace as _dc_replace

        screened: list[ConceptCandidate] = []
        annotated: list[ConceptCandidate] = []
        for candidate in candidates:
            hits = _cliche_hits(candidate, banned)
            if hits:
                annotated.append(
                    _dc_replace(candidate, rejected_reason=f"俗套命中: {'/'.join(hits[:3])}")
                )
                continue
            audit = _engine_audit(candidate)
            if audit:
                annotated.append(_dc_replace(candidate, rejected_reason=f"引擎审计: {audit}"))
                continue
            annotated.append(candidate)
            screened.append(candidate)
        result.candidates = annotated

        if not screened:
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

        judged: list[ConceptCandidate] = []
        for candidate in screened:
            try:
                system, user = _build_judge_messages(
                    candidate=candidate, genre=genre, references=references,
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
                composite = (
                    fresh * w_fresh + click * w_click + (10.0 - predictable) * w_unpred
                )
                judged.append(
                    _dc_replace(
                        candidate,
                        judge_freshness=fresh,
                        judge_click=click,
                        judge_predictable=predictable,
                        judge_reason=str(verdict.get("reason") or ""),
                        composite=round(composite, 2),
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

        contenders = [c for c in judged if c.composite is not None]
        if not contenders:
            logger.warning("concept tournament: no judged contenders; no injection")
            return result
        winner = max(contenders, key=lambda c: c.composite or 0.0)
        winner_min = float(cfg.get("winner_min", 5.5))
        if (winner.composite or 0.0) < winner_min:
            logger.warning(
                "concept tournament: best composite %.2f < winner_min %.2f; no injection",
                winner.composite or 0.0, winner_min,
            )
            return result
        result.winner = winner
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
    ban_lines = "\n".join(f"- {b}" for b in result.banned_cliches[:10])
    parts = [
        "",
        "【本书已选定高概念——全程必须严格围绕它展开，禁止回归题材默认套路】",
        f"高概念：{winner.concept}",
        f"核心机制（可反复运转的引擎，不是一次性信息）：{winner.mechanism}",
        f"认知缺口：{winner.hook_question}",
        f"长线引擎：进度条={winner.progress_bar}；问题梯={ladder}；中期战场(约第50章)={winner.ch50}",
    ]
    if ban_lines:
        parts.append(f"【本题材禁用俗套——沾任何一条即为不合格产物】\n{ban_lines}")
    return "\n".join(parts)


__all__ = [
    "ConceptCandidate",
    "ConceptTournamentResult",
    "load_concept_tournament_config",
    "render_high_concept_block",
    "resolve_banned_cliches",
    "run_concept_tournament",
]
