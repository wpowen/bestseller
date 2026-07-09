"""T6 (2026-07-09) — 简介独立文案工序：输入白名单+N路候选+病理筛+画像判官淘汰赛。

真机根因：简介此前由 conception finalize 顺手直出，机制黑话/病句直接漏进
读者文案。本模块把简介改成独立文案工序，用注入的 fake generator/persona
judge 驱动全流程，验证：淘汰赛选分高者、致命病理候选出局、判官不可用降级
排序、全候选劣于v0则回退、disabled 是 no-op、冠军未达标触发一次定向打磨。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — Chinese fixtures are intentional.
import json

import pytest

from bestseller.services.blurb_copywriter import (
    load_copywriting_config,
    run_blurb_copywriting,
)

pytestmark = pytest.mark.unit

_SPINE = {
    "who": "十年凶宅试睡员闻雀，对异常早已麻木",
    "wants": "在槐安公寓602室熬满七天并交出合格报告",
    "stakes": "交不出报告就丢饭碗",
    "against": "规则系统本体",
    "why_now": "公司新一轮裁员名单即将落到他头上",
    "question": "他能不能在分不清真实与伪造之前，熬过第七天？",
}

_GOOD_SYNOPSIS = (
    "凶宅试睡员闻雀只剩这一单能保住饭碗。\n\n"
    "电梯按键下贴着一张手写规则：八点后不可停七楼。他偏偏在七点五十九按了下去。\n\n"
    "十年夜班让他对异常没了反应，别人吓得腿软，他能照常写完报告——这是本事，"
    "也是代价。\n\n"
    "他必须熬满七天，交出一份让系统满意的报告。"
)

_WEAK_SYNOPSIS = (
    "本以为只是一份普通的工作，却没想到命运的齿轮开始转动，主角将何去何从？"
    "一段不平凡的旅程就此展开，让我们拭目以待。"
)

_JARGON_SYNOPSIS = "闻雀的共情被削薄，规则反写了他，代价压制升级到无法挽回。"

_V0_SYNOPSIS = "保饭碗还是丢工作？闻雀只剩这一单。"


def _make_generator(responses: list[str]):
    calls = iter(responses)

    async def _gen(system_prompt: str, user_prompt: str):
        text = next(calls, responses[-1])
        return json.dumps({"synopsis": text}, ensure_ascii=False), f"run-{len(user_prompt) % 1000}"

    return _gen


def _make_persona_judge(scores: dict[str, tuple[bool, float]]):
    """按候选文本片段路由到不同分数——次序无关，靠 synopsis 内容匹配。"""

    async def _judge(system_prompt: str, user_prompt: str) -> str:
        for needle, (click, score) in scores.items():
            if needle in user_prompt:
                return json.dumps({"click": click, "score": score, "reason": "ok"})
        return json.dumps({"click": False, "score": 1.0, "reason": "no match"})

    return _judge


class TestTournamentSelection:
    async def test_higher_persona_score_candidate_wins(self):
        # 弱稿排第一个生成、强稿排第二个——若排序逻辑退化成"list 顺序/取第一个"，
        # 这里会选中弱稿而不是分数更高的强稿，测试才能真正钉住排序逻辑本身。
        generator = _make_generator([_WEAK_SYNOPSIS, _GOOD_SYNOPSIS])
        persona_judge = _make_persona_judge(
            {_GOOD_SYNOPSIS[:20]: (True, 9.0), _WEAK_SYNOPSIS[:20]: (True, 3.0)}
        )
        result = await run_blurb_copywriting(
            None, None,
            spine=_SPINE, premise="试睡员闻雀为保饭碗接下最后一单老楼夜班。",
            golden_finger_line="十年夜班让他对异常麻木，别人怕的东西他能照常记录。",
            title="闻雀试睡", tags=["悬疑", "规则怪谈"], genre="悬疑推理", sub_genre="规则怪谈",
            platform=None, language="zh-CN", v0_synopsis=_V0_SYNOPSIS,
            config={"copywriting": {"n_candidates": 2, "persona_samples": 1, "max_polish_rounds": 0}},
            generator=generator, persona_judge=persona_judge,
        )
        assert result.champion == _GOOD_SYNOPSIS
        assert result.fell_back_to_v0 is False
        assert result.persona_used is True

    async def test_persona_verdict_not_overridden_by_lower_gate_score_than_v0(
        self, monkeypatch
    ):
        """回归钉子(检测报告 P1-3)：淘汰赛内部以 persona 分为主尺选出干净的
        冠军后，不该再拿确定性 gate 分去跟从没跑过 persona 评估的 v0 比——
        真机验证过 gate 分会把具体好稿排到套话稿之下(66.0 vs 67.2)，这条
        规则字面上会让 persona 选出来的冠军系统性打不过 v0。这里直接控制两边
        gate_score(champion 故意打低分、v0 故意打高分)，证明只要 persona 判
        官选中了它，就该照样出场，不用 gate 分再否决一次。"""

        champion_text = _GOOD_SYNOPSIS
        v0 = "v0简介，从未参加淘汰赛，也从未被persona评估。"

        def fake_evaluate(*, synopsis, **kwargs):
            class _V:
                total = 40.0 if synopsis == champion_text else 90.0

            return _V()

        monkeypatch.setattr(
            "bestseller.services.blurb_appeal_gate.evaluate_blurb_appeal", fake_evaluate
        )
        generator = _make_generator([champion_text])
        persona_judge = _make_persona_judge({champion_text[:20]: (True, 9.0)})
        result = await run_blurb_copywriting(
            None, None,
            spine=_SPINE, premise="x", golden_finger_line="x",
            title="闻雀试睡", tags=[], genre="悬疑推理", sub_genre="规则怪谈",
            platform=None, language="zh-CN", v0_synopsis=v0,
            config={"copywriting": {"n_candidates": 1, "persona_samples": 1, "max_polish_rounds": 0}},
            generator=generator, persona_judge=persona_judge,
        )
        assert result.persona_used is True
        assert result.fell_back_to_v0 is False
        assert result.champion == champion_text

    async def test_fatal_pathology_candidate_is_excluded(self):
        # _JARGON_SYNOPSIS 命中本书派生黑话词表(削薄/反写/压制) → fatal病理出局，
        # 即使 persona 判官给它打高分也不该赢——它已经在筛选阶段被淘汰。
        generator = _make_generator([_JARGON_SYNOPSIS, _GOOD_SYNOPSIS])
        persona_judge = _make_persona_judge(
            {_JARGON_SYNOPSIS[:20]: (True, 10.0), _GOOD_SYNOPSIS[:20]: (True, 6.0)}
        )
        result = await run_blurb_copywriting(
            None, None,
            spine=_SPINE, premise="试睡员闻雀为保饭碗接下最后一单老楼夜班。",
            golden_finger_line="十年夜班让他对异常麻木。",
            title="闻雀试睡", tags=["悬疑"], genre="悬疑推理", sub_genre="规则怪谈",
            platform=None, language="zh-CN", v0_synopsis=_V0_SYNOPSIS,
            book_jargon_terms=("削薄", "反写", "压制"),
            config={"copywriting": {"n_candidates": 2, "persona_samples": 1, "max_polish_rounds": 0}},
            generator=generator, persona_judge=persona_judge,
        )
        assert result.champion != _JARGON_SYNOPSIS

    async def test_persona_unavailable_falls_back_to_gate_score_ranking(self):
        # 弱稿排第一个生成——若"判官挂了就退化成 list 顺序"，会错选弱稿；
        # 真实 gate 分（GOOD=66.0 > WEAK=60.8，已用 evaluate_blurb_appeal 核实）
        # 才应该决定冠军。
        generator = _make_generator([_WEAK_SYNOPSIS, _GOOD_SYNOPSIS])

        async def _boom(system_prompt: str, user_prompt: str) -> str:
            raise RuntimeError("judge down")

        result = await run_blurb_copywriting(
            None, None,
            spine=_SPINE, premise="试睡员闻雀为保饭碗接下最后一单老楼夜班。",
            golden_finger_line="十年夜班让他对异常麻木。",
            title="闻雀试睡", tags=["悬疑"], genre="悬疑推理", sub_genre="规则怪谈",
            platform=None, language="zh-CN", v0_synopsis=_V0_SYNOPSIS,
            config={"copywriting": {"n_candidates": 2, "persona_samples": 1, "max_polish_rounds": 0}},
            generator=generator, persona_judge=_boom,
        )
        # judge 全废 → llm_used=False → persona_avg_score 全 None → 降级按 gate_score 排序，
        # 仍必须产出冠军，不因判官挂了就中断整个工序。
        assert result.champion == _GOOD_SYNOPSIS
        assert result.fell_back_to_v0 is False


class TestFallbackToV0:
    async def test_all_candidates_worse_than_v0_falls_back(self):
        # 用一个必定命中同义反复病句检测的候选,模拟"全部候选都差"的极端情况。
        bad = "保饭碗还是丢工作？这是唯一的选择。"
        generator = _make_generator([bad, bad])
        persona_judge = _make_persona_judge({})
        result = await run_blurb_copywriting(
            None, None,
            spine=_SPINE, premise="x", golden_finger_line="x",
            title="闻雀试睡", tags=[], genre="悬疑推理", sub_genre="规则怪谈",
            platform=None, language="zh-CN",
            v0_synopsis=_GOOD_SYNOPSIS,  # v0 反而是好稿,候选全差
            config={"copywriting": {"n_candidates": 2, "persona_samples": 1, "max_polish_rounds": 0}},
            generator=generator, persona_judge=persona_judge,
        )
        assert result.fell_back_to_v0 is True
        assert result.champion == _GOOD_SYNOPSIS

    async def test_generator_raises_for_every_strategy_falls_back(self):
        async def _boom(system_prompt: str, user_prompt: str):
            raise RuntimeError("llm down")

        result = await run_blurb_copywriting(
            None, None,
            spine=_SPINE, premise="x", golden_finger_line="x",
            title="闻雀试睡", tags=[], genre="悬疑推理", sub_genre="规则怪谈",
            platform=None, language="zh-CN", v0_synopsis=_V0_SYNOPSIS,
            config={"copywriting": {"n_candidates": 2}},
            generator=_boom, persona_judge=None,
        )
        assert result.fell_back_to_v0 is True
        assert result.champion == _V0_SYNOPSIS

    async def test_fatal_pathology_champion_falls_back_even_if_gate_score_outscores_v0(
        self, monkeypatch
    ):
        """回归钉子：某轮编辑发现 fell_back_to_v0 用 AND 而不是 OR 组合两个回退
        条件——"全员致命病理"的 champion 只有在同时'分数也更低'时才会回退，
        分数万一(哪怕因为评分器本身的怪癖)比 v0 高，就会带着确认的致命病理
        被当冠军送出去。这里直接控制两边的 gate_score，证明"致命病理"单独就
        必须触发回退，不依赖分数比较、也不依赖 evaluate_blurb_appeal 自身的
        病理封顶兜底（防御纵深：两层都要对）。"""

        fatal_only_candidate = "保饭碗还是丢工作？"  # 真实 tautology_choice 病理
        v0 = "干净的v0简介，不含任何病理。"

        def fake_evaluate(*, synopsis, **kwargs):
            class _V:
                total = 90.0 if synopsis == fatal_only_candidate else 50.0

            return _V()

        monkeypatch.setattr(
            "bestseller.services.blurb_appeal_gate.evaluate_blurb_appeal", fake_evaluate
        )

        async def _persona(system_prompt: str, user_prompt: str) -> str:
            return json.dumps({"click": True, "score": 9.0, "reason": "ok"})

        generator = _make_generator([fatal_only_candidate])
        result = await run_blurb_copywriting(
            None, None,
            spine=_SPINE, premise="x", golden_finger_line="x",
            title="t", tags=[], genre="悬疑推理", sub_genre="规则怪谈",
            platform=None, language="zh-CN", v0_synopsis=v0,
            config={"copywriting": {"n_candidates": 1, "persona_samples": 1, "max_polish_rounds": 0}},
            generator=generator, persona_judge=_persona,
        )
        assert result.fell_back_to_v0 is True
        assert result.champion == v0


class TestDisabledIsNoop:
    async def test_disabled_config_never_calls_generator(self):
        called = {"n": 0}

        async def _gen(system_prompt: str, user_prompt: str):
            called["n"] += 1
            return json.dumps({"synopsis": "x"}), None

        result = await run_blurb_copywriting(
            None, None,
            spine=_SPINE, premise="x", golden_finger_line="x",
            title="t", tags=[], genre="悬疑推理", sub_genre="规则怪谈",
            platform=None, language="zh-CN", v0_synopsis=_V0_SYNOPSIS,
            config={"copywriting": {"enabled": False}},
            generator=_gen,
        )
        assert called["n"] == 0
        assert result.champion == _V0_SYNOPSIS
        assert result.fell_back_to_v0 is True


class TestChampionPolish:
    async def test_weak_champion_triggers_one_polish_round(self, monkeypatch):
        generator = _make_generator([_WEAK_SYNOPSIS])

        async def _persona(system_prompt: str, user_prompt: str) -> str:
            return json.dumps({"click": True, "score": 7.0, "reason": "ok"})

        polish_called = {"n": 0}

        import bestseller.services.blurb_copywriter as mod

        async def _fake_polish(session, settings, *, synopsis, feedback, genre, sub_genre, language):
            polish_called["n"] += 1
            return _GOOD_SYNOPSIS, "polish-run-id"

        monkeypatch.setattr(mod, "_polish_champion", _fake_polish)
        result = await run_blurb_copywriting(
            None, None,
            spine=_SPINE, premise="x", golden_finger_line="x",
            title="闻雀试睡", tags=[], genre="悬疑推理", sub_genre="规则怪谈",
            platform=None, language="zh-CN", v0_synopsis="差",
            config={"copywriting": {"n_candidates": 1, "persona_samples": 1, "max_polish_rounds": 1}},
            generator=generator, persona_judge=_persona,
        )

        assert polish_called["n"] == 1
        assert result.polish_rounds == 1
        assert result.champion == _GOOD_SYNOPSIS


class TestSetupFailOpen:
    """回归钉子(检测报告 P2-6)：docstring 声称 'Never raises'，但画像解析/
    字数带解析/策略桶解析/默认生成器构造此前都在 try/except 之外——任一步
    炸了整个函数就会真的抛，跟 docstring 和其余部分的 fail-open 设计矛盾。"""

    async def test_resolve_persona_failure_falls_back_to_v0_without_raising(
        self, monkeypatch
    ):
        def _boom(*args, **kwargs):
            raise RuntimeError("persona resolution broke")

        monkeypatch.setattr(
            "bestseller.services.genre_persona.resolve_persona", _boom
        )
        result = await run_blurb_copywriting(
            None, None,
            spine=_SPINE, premise="x", golden_finger_line="x",
            title="t", tags=[], genre="悬疑推理", sub_genre="规则怪谈",
            platform=None, language="zh-CN", v0_synopsis=_V0_SYNOPSIS,
            config={"copywriting": {"n_candidates": 1}},
        )
        assert result.fell_back_to_v0 is True
        assert result.champion == _V0_SYNOPSIS

    async def test_strategy_bucket_resolution_failure_falls_back_to_v0(
        self, monkeypatch
    ):
        import bestseller.services.blurb_copywriter as mod

        def _boom(genre, sub_genre):
            raise RuntimeError("canonicalize broke")

        monkeypatch.setattr(mod, "_resolve_strategy_bucket", _boom)
        result = await run_blurb_copywriting(
            None, None,
            spine=_SPINE, premise="x", golden_finger_line="x",
            title="t", tags=[], genre="悬疑推理", sub_genre="规则怪谈",
            platform=None, language="zh-CN", v0_synopsis=_V0_SYNOPSIS,
            config={"copywriting": {"n_candidates": 1}},
        )
        assert result.fell_back_to_v0 is True
        assert result.champion == _V0_SYNOPSIS


class TestConfigDefaults:
    def test_load_copywriting_config_defaults(self):
        cfg = load_copywriting_config({})
        assert cfg["enabled"] is True
        assert cfg["n_candidates"] == 3
        assert cfg["persona_samples"] == 2
        assert cfg["max_polish_rounds"] == 1
        assert "scene_hook" in cfg["strategies"]["default"]
        assert "rule_suspense" in cfg["strategies"]["suspense"]

    def test_load_copywriting_config_overrides(self):
        cfg = load_copywriting_config(
            {"copywriting": {"enabled": False, "n_candidates": 5}}
        )
        assert cfg["enabled"] is False
        assert cfg["n_candidates"] == 5


class TestResultToDict:
    async def test_to_dict_roundtrip_shape(self):
        generator = _make_generator([_GOOD_SYNOPSIS])
        persona_judge = _make_persona_judge({_GOOD_SYNOPSIS[:20]: (True, 8.0)})
        result = await run_blurb_copywriting(
            None, None,
            spine=_SPINE, premise="x", golden_finger_line="x",
            title="闻雀试睡", tags=[], genre="悬疑推理", sub_genre="规则怪谈",
            platform=None, language="zh-CN", v0_synopsis=_V0_SYNOPSIS,
            config={"copywriting": {"n_candidates": 1, "persona_samples": 1, "max_polish_rounds": 0}},
            generator=generator, persona_judge=persona_judge,
        )
        d = result.to_dict()
        assert d["schema_version"] == "blurb-copywriting.v1"
        assert isinstance(d["candidates"], list) and d["candidates"]
        assert "champion_strategy" in d

    async def test_report_candidates_carry_persona_scores(self):
        """L3 真机验收回归钉子(2026-07-09)：落库的淘汰赛报告里 persona 分全是
        null——survivors 拿到打分后的新实例，但 result.candidates 持久化的是
        打分前的原始快照，看报告的人无法复盘冠军凭什么赢。打完分必须回写。"""

        generator = _make_generator([_GOOD_SYNOPSIS, _WEAK_SYNOPSIS])
        persona_judge = _make_persona_judge(
            {_GOOD_SYNOPSIS[:20]: (True, 9.0), _WEAK_SYNOPSIS[:20]: (True, 3.0)}
        )
        result = await run_blurb_copywriting(
            None, None,
            spine=_SPINE, premise="x", golden_finger_line="x",
            title="闻雀试睡", tags=[], genre="悬疑推理", sub_genre="规则怪谈",
            platform=None, language="zh-CN", v0_synopsis=_V0_SYNOPSIS,
            config={"copywriting": {"n_candidates": 2, "persona_samples": 1, "max_polish_rounds": 0}},
            generator=generator, persona_judge=persona_judge,
        )
        assert result.persona_used is True
        by_strategy = {c.strategy: c for c in result.candidates}
        scores = {s: c.persona_avg_score for s, c in by_strategy.items()}
        assert all(v is not None for v in scores.values()), (
            f"candidates in the report must carry persona scores, got {scores}"
        )
        d = result.to_dict()
        assert all(c["persona_avg_score"] is not None for c in d["candidates"])
