"""概念淘汰赛（services/concept_tournament.py）L1 测试。

真机根因（《谁敢动我山头》custom-xianxia-1783586500, 2026-07-09）：概念层
finalize 一锤子买卖 → 输出题材语料众数（废脉藏宝/破宗门重建/债主逼门），
读者可自动补全全书。本工序 = 反俗套禁用 + 杂交 N 候选 + 引擎审计 +
判官对撞榜单参照集，冠军注入 ctx["description"] 源头。

全部 LLM 走注入的 fake generator/judge；真实效果由真机对照书验证。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — Chinese fixtures are intentional.
import json
import random

import pytest

from bestseller.services.concept_tournament import (
    ConceptCandidate,
    ConceptTournamentResult,
    load_concept_tournament_config,
    render_high_concept_block,
    resolve_banned_cliches,
    run_concept_tournament,
)

pytestmark = pytest.mark.unit

# 一个合格的高概念候选（杂交=电网调度×仙侠，引擎字段齐全，不撞俗套）
GOOD_PAYLOAD = {
    "concept": "灵气是按'功德账户'配给的公共电网，主角是唯一敢给仙门拉闸限电的调度员。",
    "mechanism": "每次拉闸都重新定价一段修真界的权力关系，被限电者必须向调度台出让秘密或资源。",
    "hook_question": "凡人调度员凭什么拉闸？谁给他的权限，代价记在谁头上？",
    "progress_bar": "调度权限等级：村级电闸→坊市→州府→天庭主干网",
    "question_ladder": ["谁建的灵气电网", "功德账户由谁记账", "第一任调度员为什么消失"],
    "ch50": "州府断电七日之战：三大宗门联手要夺调度台改自由市场",
}

# 撞俗套的候选（废脉+宝脉 双词元命中 xianxia 禁用清单）
CLICHE_PAYLOAD = {
    **GOOD_PAYLOAD,
    "concept": "主角发现脚下废脉其实是上古宝脉，重建破败宗门。",
    "mechanism": "废脉里挖出的灵石能换资源，宗门逐步崛起。",
}

# 引擎残缺的候选（问题梯只有一级）
NO_ENGINE_PAYLOAD = {
    **GOOD_PAYLOAD,
    "concept": "修真界的丹药全部由一家神秘钱庄统一定价，主角是钱庄唯一的凡人柜员。",
    "question_ladder": ["钱庄老板是谁"],
}

WEAK_PAYLOAD = {
    **GOOD_PAYLOAD,
    "concept": "少年得到强大传承，一路修炼变强，最终问鼎巅峰。",
    "mechanism": "修炼吸收灵气突破境界。",
}


def _gen_from(payloads: list[dict]) -> callable:
    calls = iter(payloads)

    async def _gen(system: str, user: str):
        payload = next(calls, payloads[-1])
        return json.dumps(payload, ensure_ascii=False), f"run-{id(payload) % 997}"

    return _gen


def _judge_scoring(scores: dict[str, tuple[float, float, float]]) -> callable:
    """按概念文本片段路由 (freshness, click, predictable)。未命中给低分。"""

    async def _judge(system: str, user: str):
        for needle, (fresh, click, pred) in scores.items():
            if needle in user:
                return (
                    json.dumps(
                        {"freshness": fresh, "click": click, "predictable": pred,
                         "reason": "test"},
                        ensure_ascii=False,
                    ),
                    None,
                )
        return json.dumps({"freshness": 2, "click": 2, "predictable": 9, "reason": "平庸"}), None

    return _judge


_CFG = {
    "enabled": True,
    "n_candidates": 3,
    "winner_min": 5.5,
    "judge_weights": {"freshness": 0.4, "click": 0.4, "unpredictability": 0.2},
    "dimension_pool": ["电网调度与停电分配", "保险与精算定价", "殡葬入殓与遗产执行"],
    "cliche_seeds": {
        "generic": ["穿越自带系统面板"],
        "xianxia": ["废脉其实是宝脉", "破宗门重建崛起"],
    },
}


class TestResolveBannedCliches:
    def test_merges_generic_and_canonical_genre(self):
        bans = resolve_banned_cliches("古典仙侠", "古典仙侠", _CFG)
        assert "穿越自带系统面板" in bans
        assert "废脉其实是宝脉" in bans

    def test_unknown_genre_falls_back_to_generic_only(self):
        bans = resolve_banned_cliches("完全未知题材zzz", None, _CFG)
        assert bans == ("穿越自带系统面板",)

    def test_real_config_has_xianxia_seeds(self):
        # 真实配置：canonical 键必须命中（此前踩过 xianxia-classic≠xianxia 的坑）。
        load_concept_tournament_config.cache_clear()
        bans = resolve_banned_cliches("古典仙侠", "古典仙侠")
        assert any("废脉" in b for b in bans)


class TestDeterministicScreens:
    @pytest.mark.asyncio
    async def test_cliche_candidate_rejected(self):
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([CLICHE_PAYLOAD, GOOD_PAYLOAD]),
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (9, 9, 2)}),
            rng=random.Random(7),
        )
        rejected = [c for c in result.candidates if c.rejected_reason]
        assert any("俗套命中" in (c.rejected_reason or "") for c in rejected)
        assert result.winner is not None
        assert result.winner.concept == GOOD_PAYLOAD["concept"]

    @pytest.mark.asyncio
    async def test_engine_incomplete_candidate_rejected(self):
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([NO_ENGINE_PAYLOAD, GOOD_PAYLOAD]),
            judge=_judge_scoring({GOOD_PAYLOAD["concept"][:12]: (9, 9, 2)}),
            rng=random.Random(7),
        )
        assert any("引擎审计" in (c.rejected_reason or "") for c in result.candidates)
        assert result.winner is not None
        assert result.winner.concept == GOOD_PAYLOAD["concept"]


class TestJudgeTournament:
    @pytest.mark.asyncio
    async def test_higher_composite_wins_order_independent(self):
        # 弱候选排第一个生成——若选择退化成 list 顺序会选错。
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([WEAK_PAYLOAD, GOOD_PAYLOAD]),
            judge=_judge_scoring({
                WEAK_PAYLOAD["concept"][:10]: (3, 4, 9),
                GOOD_PAYLOAD["concept"][:10]: (9, 8, 2),
            }),
            rng=random.Random(7),
        )
        assert result.winner is not None
        assert result.winner.concept == GOOD_PAYLOAD["concept"]
        # 评审分回写进 candidates 快照（可落库复盘,同 blurb 淘汰赛 F4 教训）
        scored = {c.concept: c for c in result.candidates if c.composite is not None}
        assert GOOD_PAYLOAD["concept"] in scored
        assert WEAK_PAYLOAD["concept"] in scored

    @pytest.mark.asyncio
    async def test_all_below_winner_min_yields_no_winner(self):
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([WEAK_PAYLOAD]),
            judge=_judge_scoring({WEAK_PAYLOAD["concept"][:10]: (3, 3, 9)}),
            rng=random.Random(7),
        )
        # composite = 3*0.4+3*0.4+(10-9)*0.2 = 2.6 < 5.5 → 不注入,回落现状
        assert result.winner is None
        assert result.candidates  # 但候选记录保留供复盘

    @pytest.mark.asyncio
    async def test_predictability_drags_composite_down(self):
        # 同 freshness/click,可预测性 9 vs 1 → 差 1.6 分,足以翻盘。
        a = {**GOOD_PAYLOAD, "concept": "概念Alpha：灵气电网调度员的拉闸权战争。"}
        b = {**GOOD_PAYLOAD, "concept": "概念Beta：灵气保险精算师给渡劫定保费。"}
        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="古典仙侠", chapter_count=20,
            config=_CFG, generator=_gen_from([a, b]),
            judge=_judge_scoring({
                "概念Alpha": (7, 7, 9),   # composite 5.8
                "概念Beta": (7, 7, 1),    # composite 7.4
            }),
            rng=random.Random(7),
        )
        assert result.winner is not None
        assert "概念Beta" in result.winner.concept


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_disabled_returns_empty_without_calls(self):
        called = {"n": 0}

        async def _gen(system, user):
            called["n"] += 1
            return "{}", None

        result = await run_concept_tournament(
            None, None, genre="仙侠", sub_genre="", chapter_count=20,
            config={"enabled": False}, generator=_gen,
        )
        assert called["n"] == 0
        assert result.winner is None
        assert result.candidates == []

    @pytest.mark.asyncio
    async def test_generator_always_raising_yields_no_winner_without_raise(self):
        async def _boom(system, user):
            raise RuntimeError("llm down")

        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="", chapter_count=20,
            config=_CFG, generator=_boom, judge=_boom, rng=random.Random(7),
        )
        assert result.winner is None

    @pytest.mark.asyncio
    async def test_judge_garbage_yields_no_winner(self):
        async def _garbage(system, user):
            return "不是JSON", None

        result = await run_concept_tournament(
            None, None, genre="古典仙侠", sub_genre="", chapter_count=20,
            config=_CFG, generator=_gen_from([GOOD_PAYLOAD]), judge=_garbage,
            rng=random.Random(7),
        )
        # 判官全废 → 无 composite → 无冠军(宁缺毋滥),但不 raise
        assert result.winner is None


class TestRenderHighConceptBlock:
    def test_winner_block_carries_concept_engine_and_bans(self):
        winner = ConceptCandidate(
            dimension="电网调度与停电分配",
            concept=GOOD_PAYLOAD["concept"],
            mechanism=GOOD_PAYLOAD["mechanism"],
            hook_question=GOOD_PAYLOAD["hook_question"],
            progress_bar=GOOD_PAYLOAD["progress_bar"],
            question_ladder=tuple(GOOD_PAYLOAD["question_ladder"]),
            ch50=GOOD_PAYLOAD["ch50"],
            composite=8.0,
        )
        result = ConceptTournamentResult(
            winner=winner, banned_cliches=("废脉其实是宝脉",),
        )
        block = render_high_concept_block(result)
        assert GOOD_PAYLOAD["concept"] in block
        assert "进度条=" in block and "问题梯=" in block and "第50章" in block
        assert "废脉其实是宝脉" in block
        assert "禁止回归题材默认套路" in block

    def test_no_winner_renders_empty(self):
        assert render_high_concept_block(ConceptTournamentResult()) == ""


# ── conception.py 接线结构钉（同 T4/T6 既有测试惯例）─────────────────────────


class TestConceptionWiring:
    def _source(self) -> str:
        import inspect

        from bestseller.services import conception as conception_services

        return inspect.getsource(conception_services.run_conception_pipeline)

    def test_tournament_runs_before_round0_and_after_mechanism_dedup(self):
        source = self._source()
        dedup_pos = source.index("_attach_mechanism_dedup(session, settings, ctx)")
        tournament_pos = source.index("run_concept_tournament(")
        round0_pos = source.index("Round 0: Autonomous Commercial Positioning")
        assert dedup_pos < tournament_pos < round0_pos, (
            "tournament must consume avoid_mechanisms (after dedup) and inject "
            "before any downstream agent prompt is built (before Round 0)"
        )

    def test_skipped_when_user_provided_concept_lab(self):
        source = self._source()
        idx = source.index("run_concept_tournament(")
        guard_region = source[max(0, idx - 1200) : idx]
        assert 'if not ctx.get("concept_lab"):' in guard_region, (
            "explicit user concepts must never be overridden by the tournament"
        )

    def test_fail_open_and_injection_shape(self):
        source = self._source()
        idx = source.index("run_concept_tournament(")
        region = source[max(0, idx - 1200) : idx + 2200]
        assert "except Exception:" in region
        assert 'logger.warning("Concept tournament failed (non-fatal)' in region
        # 注入=augment description(全 prompt 源头) + high_concept 观测键 + 消毒。
        assert '_sanitize_forbidden_default_motifs(' in region
        assert 'ctx["description"] = f"{ctx.get(\'description\') or \'\'}\\n{_hc_block}"' in region
        assert 'ctx["high_concept"] = _ct_result.winner.to_dict()' in region

    def test_tournament_record_lands_in_conception_log(self):
        source = self._source()
        idx = source.index('"agent": "concept_tournament"')
        region = source[max(0, idx - 300) : idx + 200]
        assert "conception_log.append(" in region
