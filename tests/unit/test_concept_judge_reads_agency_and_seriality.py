"""概念层的两个测量缺口（2026-08-25 真机 custom-xuanhuan-1787625194）。

用户报「这本书完全没有任何可读性」。框架自己的整书质量账本同意：
7–11 章里 **has_decision 0/5**、3/5 章 ``functional_shape=flat``、``passed=false``。
但这些全是在 12 章写完**之后**才被拦下的——先写再拦。

往上游查，概念层有两个缺口：

E. 判官八条轴里**没有一条问「主角要不要做选择」**。批准的故事引擎是纯被动
   循环（外部压力每轮施加、主角每轮应对），正文 has_decision 0/5 是它的必然
   产物，不是写手写砸了。

D. 唯一评追读性的判官（renewability / escalation / anti_reset / coherence /
   promise_survival / unit_density）整段挂在写死的 ``chapter_count >= 200``
   后面，对所有正常长度的书完全空转；``seriality_judge`` 恒为 ``{}``，
   「评了没发现」与「压根没跑」不可区分。

两条的修法都刻意**不发杀权**：E 只进 composite 加权（改排序不改淘汰），
D 在短书上只留痕不否决。理由是 config 里 2026-07-17 的教训——收紧概念层
阈值会让淘汰赛干涸，而干涸的下场是注入保底概念，比任何被拒候选都差。
"""

from __future__ import annotations

import pytest

from bestseller.services.concept_tournament import (
    _FLOOR_AXIS_LABELS,
    _hard_floor_failed_axes,
    seriality_stage_mode,
)

pytestmark = pytest.mark.unit


class TestProtagonistAgencyAxis:
    def test_the_axis_is_asked_for_in_the_judge_prompt(self):
        """轴必须真的问出去——只加解析不加 prompt 等于恒拿默认分。"""
        from bestseller.services import concept_tournament as ct

        src = ct.__file__
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        assert "protagonist_agency 主角能动性" in text
        assert '"protagonist_agency": 0-10' in text

    def test_the_axis_has_no_hard_floor(self):
        """刻意不发杀权：只改排序不改淘汰，避免重演 2026-07-17 的干涸事故。"""
        floored = {key for key, _label, _default in _FLOOR_AXIS_LABELS}
        assert "protagonist_agency" not in floored

    def test_a_passive_engine_is_not_eliminated_by_the_new_axis_alone(self):
        """能动性极低但其余达标的候选仍进池——降权不是杀权。"""
        scores = {
            "freshness": 7.0,
            "click": 7.5,
            "predictable": 5.0,
            "character_logic": 7.5,
            "mechanism_causality": 7.5,
            "genre_fidelity": 7.5,
            "plain_language": 7.5,
            "story_motion": 7.5,
            "protagonist_agency": 1.0,
        }
        floors = {"catastrophe_floor": 4.0, "soft_miss_allowance": 3}
        assert _hard_floor_failed_axes(scores, floors) == []

    @pytest.mark.parametrize("marker,label", [
        ("\njudge_weights:", "基线"),
        ("\n  judge_weights:", "wild_mode"),
    ])
    def test_every_weight_set_sums_to_one(self, marker: str, label: str):
        """**每一套**权重都要配平，不只是基线。

        2026-08-25 实测：新轴在代码里给了非零默认（0.10），于是它被加到
        wild_mode 这套没声明该键的权重上，合计从 1.0 变 1.1，composite 与
        winner_min 的关系被悄悄改变——全量套件当场抓到
        （test_penalize_keeps_cliche_candidate_alive 期望 7.5 实得 8.0）。
        本用例是那条失败的结构化版本：加轴时忘了同步某一套，这里就红。
        """
        import re
        from pathlib import Path

        text = Path("config/concept_tournament.yaml").read_text(encoding="utf-8")
        assert marker in text, f"{label} 权重块不存在"
        block = text.split(marker, 1)[1].split("\n\n", 1)[0]
        values = [float(v) for v in re.findall(r"^ +[a-z_]+: ([0-9.]+)", block, re.M)]
        assert "protagonist_agency" in block, f"{label} 缺 protagonist_agency"
        assert abs(sum(values) - 1.0) < 1e-9, f"{label} 权重合计 {sum(values)}"

    def test_the_code_default_for_a_new_axis_is_zero(self):
        """新轴在代码里必须默认 0.0——真值只许有一个来源（配置）。"""
        import inspect

        from bestseller.services import concept_tournament as ct

        src = inspect.getsource(ct)
        assert 'weights.get("protagonist_agency", 0.0)' in src


class TestSerialityStageTiering:
    def test_the_veto_is_withheld_by_default_because_the_judge_judges_backwards(self):
        """反相关的判官不许握杀权。

        scripts/seriality_judge_validation.py 两轮独立实测（真榜单书走生产
        展开 + 生产判官）：强侧=已跑到 ≥500 章且 ≥30 万在读，弱侧=<120 章且
        <2 万在读，排序能力 AUC = 0.37 / 0.38。低于 0.5 意味着它**判反了**
        ——给真写下去的书打分反而更低，强侧判失败率 86%/92%。
        而它在 ≥200 章时握有否决权，真机日志已见
        "concept tournament: no seriality-qualified finalists"。
        """
        mode, receipt = seriality_stage_mode(200, {})
        assert mode == "advisory"
        assert receipt["veto_withheld"] is True
        assert receipt["enforcing_allowed"] is False

    def test_the_veto_can_be_restored_once_the_judge_passes_a_validity_bar(self):
        """收刀不是删刀——过了效度线要能一行配置放回去。"""
        cfg = {"seriality_enforcing_enabled": True}
        mode, receipt = seriality_stage_mode(200, cfg)
        assert mode == "enforcing"
        assert receipt["enforcing_min_chapters"] == 200
        assert receipt["veto_withheld"] is False

    def test_the_shipped_config_withholds_the_veto(self):
        """默认值住在 yaml 里，别只在代码里为真——这类「两处不一致」是本仓库常见病。"""
        import pathlib as _pathlib

        import yaml

        cfg = yaml.safe_load(
            (_pathlib.Path(__file__).resolve().parents[2] / "config" / "concept_tournament.yaml")
            .read_text(encoding="utf-8")
        )
        assert cfg["seriality_enforcing_enabled"] is False
        assert seriality_stage_mode(500, cfg)[0] == "advisory"

    def test_a_normal_book_now_reaches_the_judge_in_advisory_mode(self):
        """真机那本 12 章书此前整段跳过；现在跑判官但不发否决。"""
        mode, _ = seriality_stage_mode(12, {})
        assert mode == "advisory"

    def test_a_very_short_piece_is_still_skipped(self):
        """短篇没有「长篇承载」可言，不制造新的失败模式。"""
        assert seriality_stage_mode(3, {})[0] == "skipped"

    def test_thresholds_are_configurable_not_hardcoded(self):
        """写死的魔数不可校准也不可回滚。"""
        cfg = {
            "seriality_min_chapters": 30,
            "seriality_advisory_min_chapters": 5,
            "seriality_enforcing_enabled": True,
        }
        assert seriality_stage_mode(30, cfg)[0] == "enforcing"
        assert seriality_stage_mode(29, cfg)[0] == "advisory"
        assert seriality_stage_mode(4, cfg)[0] == "skipped"

    def test_only_the_llm_judge_loses_its_veto_not_the_deterministic_screen(self):
        """收刀必须挑源头——一刀切会把好闸门一起废掉。

        两个否决源共用 ``rejected_reason``，靠前缀区分：
          「长篇承载失败: …」 = _audit_seriality_proof，字段缺失/容量不足，
                               确定性可复算，从未被证伪 → 继续发否决
          「长篇质量门失败: …」= _judge_seriality_proof，六轴 LLM 打分，
                               实测 AUC 0.37/0.38 与现实反相关 → 收刀
        本条锁住这个区分：第一版补丁把两者一起清了，被
        test_candidate_without_capacity_proof_rejected_for_long_target 抓出。
        """
        import inspect as _inspect

        from bestseller.services import concept_tournament as ct

        src = _inspect.getsource(ct)
        assert "_veto_is_from_judge" in src
        assert '"长篇质量门失败"' in src
        # 确定性筛的否决串必须仍然存在且不被同一分支清掉
        assert '"长篇承载失败: " + audit' in src or 'f"长篇承载失败: {audit}"' in src
        assert 'if _seriality_mode == "advisory" and _veto_is_from_judge:' in src

    def test_the_receipt_is_always_written(self):
        """三态都要留痕——此前 seriality_judge=={} 分不清没跑还是没发现。"""
        for chapters in (3, 12, 500):
            _mode, receipt = seriality_stage_mode(chapters, {})
            assert receipt["mode"] in {"enforcing", "advisory", "skipped"}
            assert receipt["chapter_count"] == chapters
            # 「本可杀但被收了刀」必须和「本来就够不着杀权档」分得开，
            # 否则下次又要重查它到底有没有开过火。
            assert receipt["veto_withheld"] is (chapters >= 200)

    def test_vacuity_the_old_hardcoded_gate_would_fail_this_suite(self):
        """空转检验：还原写死的 200 判定，确认本套件抓得住它。"""

        def old_gate(chapter_count: int) -> str:
            return "enforcing" if chapter_count >= 200 else "skipped"

        assert old_gate(12) == "skipped"
        assert seriality_stage_mode(12, {})[0] == "advisory", (
            "修复前 12 章书整段跳过，本套件第二条断言正是为它写的"
        )


class TestSerialityExpansionDoesNotSeedTheAnswer:
    """展开 prompt 不许把答案写进去——种词会被整批复印成骨架。

    2026-08-28 定案。原先两处把答案写死：
        指令  「unit_families 至少4类不同冲突语法，例如发现、交易、关系选择、
              公开博弈、建设、反制、内部裂变」
        模板  '"unit_families":["发现","交易","关系选择","公开博弈"]'
              —— 这个 JSON 模板里每个字段都是占位形状（"来源1"/"势力1"/"盘面1"），
                 唯独它填了真答案。
    实测（scripts/seriality_judge_validation.py，14 本真书走生产展开+生产判官）：
        7 个种词的平均复印数  强侧 6.3/7  弱侧 5.8/7
        八个结构字段的强弱差  全部 ±0.5 以内（1706 章爆款与 88 章断更书同形）
        判官排序能力 AUC      0.37（低于 0.5 = 判反了）
    去掉两处种词后，同 seed 同批书重跑：
        六轴全部由负转正（+0.91 ~ +1.81），AUC 0.37 → 0.75
    判官从来就不瞎，是种词把它的输入抹平了。
    """

    _SEEDS = ("关系选择", "公开博弈", "内部裂变")

    def _prompt(self) -> str:
        from bestseller.services.concept_tournament import (
            ConceptCandidate,
            _build_seriality_messages,
        )

        system, user = _build_seriality_messages(
            candidate=ConceptCandidate(dimension="bench", concept="测试"),
            genre="玄幻",
            chapter_count=500,
        )
        return system + "\n" + user

    def test_no_conflict_family_seed_words_in_the_prompt(self):
        text = self._prompt()
        leaked = [w for w in self._SEEDS if w in text]
        assert not leaked, f"冲突家族种词回流到展开 prompt：{leaked}"

    def test_the_json_template_keeps_unit_families_as_a_placeholder(self):
        """模板里它必须和其他字段一样是占位形状，不能是可抄的真答案。"""
        text = self._prompt()
        assert '"unit_families":["冲突家族1"' in text.replace(" ", "")

    def test_the_lower_bound_survives(self):
        """去种词必须留下限——2026-08-24 记着：无下限一轮只回 4 条。"""
        assert "至少4类" in self._prompt()


class TestSerialityJudgeVerdictIsPersisted:
    """判官判词必须落进契约——收了杀权之后，留痕是它唯一的职责。

    2026-08-28 真机《破庙里我把玉玺摔成四瓣》实录：
    concept_tournament_finalist_seriality_judge 跑了 3 次，落库后
    metadata.seriality_proof.seriality_judge 却是 null——组装契约时
    只搬了确定性的 capacity_report，六轴判词被整块丢掉。
    「评了没发现」与「压根没跑」于是再次不可区分。
    """

    def _contract(self, judge: dict | None):
        from bestseller.services.concept_contract import build_concept_contract
        from bestseller.services.concept_tournament import ConceptCandidate

        winner = ConceptCandidate(
            dimension="bench",
            concept="一句话",
            mechanism="机制",
            hook_question="问题",
            repeatable_story_unit="每轮循环",
            unit_families=("甲", "乙", "丙", "丁"),
            unit_frequency="2-4章一次",
            unit_count_estimate=170,
            renewal_sources=("来源甲", "来源乙", "来源丙"),
            accumulation_tracks=("积累甲", "积累乙"),
            phase_transitions=("第1-250章", "第251-500章"),
            opposing_ecology=("势力甲", "势力乙"),
            question_ladder=("问一", "问二"),
            endgame_direction="终局",
            core_promise_invariant="承诺",
            seriality_judge=dict(judge or {}),
        )
        return build_concept_contract(
            winner=winner,
            story_spine={},
            target_chapters=500,
            genre="玄幻",
            sub_genre="东方玄幻",
        )

    def test_the_verdict_survives_into_the_contract(self):
        scores = {"renewability": 5.0, "escalation": 4.0, "reason": "单元易枯竭"}
        proof = self._contract(scores)["seriality_proof"]
        assert proof["seriality_judge"]["renewability"] == 5.0
        assert proof["seriality_judge"]["reason"] == "单元易枯竭"

    def test_the_key_exists_even_when_the_judge_said_nothing(self):
        """恒写：空与缺失同样是信息，但两者不能长得一样。"""
        proof = self._contract(None)["seriality_proof"]
        assert "seriality_judge" in proof
        assert proof["seriality_judge"] == {}
