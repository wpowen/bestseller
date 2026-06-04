"""Unit tests for the Simulation Oracle (MiroFish 接入层 / Phase 1).

验证目标:
1. 角色定位推断正确(含 Phase0 发现的"对照主角"误判 bug)。
2. HeuristicOracle 产出 beat 覆盖全章 + 支线 + 动机漏洞。
3. 产出可被 BeatScheduleItem / PlotTreeNode / story_design_kernel_from_dict 接受。
4. augment_kernel 后**不触发** story_design_kernel_gate 的两个 critical
   (fallback_source_leak / beat_schedule_incomplete) —— 即直接解阻断。
5. 从真实小说导出请求正确。
6. 优雅降级:禁用 / 客户端失败均回退 HeuristicOracle,不阻断。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bestseller.services.prewrite_quality_profile import (
    evaluate_story_design_kernel_quality,
    has_kernel_leak,
)
from bestseller.services.simulation_oracle import (
    RANKING_PRESSURE_TERMS,
    CharacterSeed,
    EmergentBeat,
    HeuristicOracle,
    MiroFishClient,
    OracleConfig,
    OracleRequest,
    OracleResult,
    SimulationOracle,
    augment_kernel,
    evaluate_oracle_quality,
    export_request_from_novel,
    infer_entity_type,
    narrative_ontology,
)
from bestseller.services.story_design_kernel import (
    BeatScheduleItem,
    PlotTreeNode,
    story_design_kernel_from_dict,
)

NOVELS = Path(__file__).resolve().parents[2] / "output" / "ai-generated"


# --------------------------------------------------------------------------- #
# 1. 角色定位推断
# --------------------------------------------------------------------------- #


class TestInferEntityType:
    def test_protagonist_leading_token(self) -> None:
        assert infer_entity_type("主角，万相火脉觉醒者，目标是打破修行垄断。") == "Protagonist"

    def test_rival_not_misrouted_to_protagonist(self) -> None:
        # 关键回归:"对照主角的既得利益代表"含"主角"子串,绝不能判成 Protagonist
        assert infer_entity_type("九宗天骄，对照主角的既得利益代表。") == "Rival"

    def test_ally_and_mentor(self) -> None:
        assert infer_entity_type("阵法师，同伴，擅长推演与情报交易。") == "Ally"
        assert infer_entity_type("师尊，卷入旧案，立场复杂。") == "Mentor"

    def test_fallback_character(self) -> None:
        assert infer_entity_type("路边卖糖葫芦的老人。") == "Character"


# --------------------------------------------------------------------------- #
# 2. HeuristicOracle 产出
# --------------------------------------------------------------------------- #


def _sample_request(target: int = 30) -> OracleRequest:
    return OracleRequest(
        slug="demo",
        target_chapters=target,
        premise="少年逆袭打破修行垄断。",
        characters=(
            CharacterSeed(
                "林烬", "主角，万相火脉觉醒者，目标是打破修行垄断。", "Protagonist", "化海稳定。"
            ),
            CharacterSeed(
                "苏晚照", "阵法师，同伴，擅长推演与情报交易。", "Ally", "体系核心协助者。"
            ),
            CharacterSeed("宁玄策", "九宗天骄，对照主角的既得利益代表。", "Rival"),
        ),
        question="推演第四卷走向。",
    )


class TestHeuristicOracle:
    def test_beats_cover_all_chapters(self) -> None:
        result = HeuristicOracle().deduce(_sample_request(30))
        max_ch = max(
            int(b.chapter_range.split("-")[-1]) for b in result.beats
        )
        assert max_ch == 30
        assert result.source == "heuristic"

    def test_subplots_exclude_protagonist(self) -> None:
        result = HeuristicOracle().deduce(_sample_request())
        names = {sp.label for sp in result.subplots}
        assert any("苏晚照" in n for n in names)
        assert any("宁玄策" in n for n in names)
        assert not any("林烬" in n for n in names)

    def test_thin_rival_flagged(self) -> None:
        result = HeuristicOracle().deduce(_sample_request())
        flagged = {f.character for f in result.motivation_flags}
        assert "宁玄策" in flagged  # stake 过薄

    def test_natural_direction_mentions_protagonist(self) -> None:
        result = HeuristicOracle().deduce(_sample_request())
        assert "林烬" in result.natural_direction


# --------------------------------------------------------------------------- #
# 3. 产出 schema 同构 (能被 kernel 结构接受)
# --------------------------------------------------------------------------- #


class TestSchemaIsomorphism:
    def test_beat_validates_as_beat_schedule_item(self) -> None:
        result = HeuristicOracle().deduce(_sample_request())
        for beat in result.beats:
            BeatScheduleItem.model_validate(beat.to_kernel_dict())

    def test_subplot_validates_as_plot_tree_node(self) -> None:
        result = HeuristicOracle().deduce(_sample_request())
        for sp in result.subplots:
            node = PlotTreeNode.model_validate(sp.to_plot_node_dict())
            assert node.line_type != "main"
            assert node.dependency_on_mainline  # 非 main 必须有依赖

    def test_no_kernel_leak_in_output(self) -> None:
        result = HeuristicOracle().deduce(_sample_request())
        blob = json.dumps(
            [b.to_kernel_dict() for b in result.beats]
            + [s.to_plot_node_dict() for s in result.subplots],
            ensure_ascii=False,
        )
        assert not has_kernel_leak(blob)


# --------------------------------------------------------------------------- #
# 4. augment_kernel 解阻断 (核心价值证明)
# --------------------------------------------------------------------------- #


def _base_kernel_blocked() -> dict:
    """模拟当前被阻断的 kernel:beat 只覆盖开局 (会触发 beat_schedule_incomplete)。"""
    return {
        "reader_promise": "每章产生可见的状态变化。",
        "premise_contract": {
            "unique_hook": "空脉废人公开重写修行规则。",
            "core_question": "打破垄断者会否变成新的垄断者?",
            "commercial_pull": "逆袭爽点叠加制度反思。",
        },
        "character_conflict_contracts": [
            {
                "character_key": "林烬",
                "external_goal": "打破修行垄断。",
                "internal_need": "证明空脉者也配求道。",
                "pressure_source": "九宗既得利益的围剿。",
                "choice_axis": "独善其身 还是 立规救众。",
                "change_vector": "从挑战者转为立规者。",
            }
        ],
        "structure_strategy": {
            "macro_strategy": "逆袭叠加制度冲突。",
            "chapter_engine": "每章一次小兑现加一个新钩子。",
            "pacing_rule": "铺垫与爆发交替。",
            "freshness_rule": "每卷引入一个新的世界压强。",
        },
        "plot_tree": [
            {
                "key": "main",
                "line_type": "main",
                "label": "林烬重写修行规则",
                "role": "主线",
                "current_state": "规则刚向平民开放。",
                "target_state": "新秩序站稳并通过代价检验。",
                "failure_if_removed": "无主线则全书崩塌。",
            }
        ],
        "beat_schedule": [
            {
                "chapter_range": "1-3",
                "duty": "建立开局承诺。",
                "state_change": "主角觉醒火脉。",
                "payoff": "兑现第一个爽点。",
                "hook_or_aftereffect": "抛出旧案谜团。",
            }
        ],
        "change_vectors": ["从废人到立规者"],
    }


class TestAugmentKernelUnblocks:
    def test_blocked_before_augment(self) -> None:
        report = evaluate_story_design_kernel_quality(_base_kernel_blocked(), target_chapters=30)
        codes = {f.code for f in (*report.blocking_findings, *report.audit_findings)}
        assert "beat_schedule_incomplete" in codes  # 增强前确实被阻断

    def test_unblocked_after_augment(self) -> None:
        result = HeuristicOracle().deduce(_sample_request(30))
        augmented = augment_kernel(_base_kernel_blocked(), result, target_chapters=30)
        report = evaluate_story_design_kernel_quality(augmented, target_chapters=30)
        codes = {f.code for f in (*report.blocking_findings, *report.audit_findings)}
        assert "beat_schedule_incomplete" not in codes
        assert "fallback_source_leak" not in codes

    def test_augmented_kernel_validates(self) -> None:
        result = HeuristicOracle().deduce(_sample_request(30))
        augmented = augment_kernel(_base_kernel_blocked(), result, target_chapters=30)
        kernel = story_design_kernel_from_dict(augmented)  # 不抛 = 通过 pydantic 校验
        assert any(n.line_type == "main" for n in kernel.plot_tree)  # 保留 main 线
        assert len(kernel.plot_tree) > 1  # 注入了涌现支线

    def test_facade_augment(self) -> None:
        oracle = SimulationOracle(OracleConfig(enabled=False))
        augmented = oracle.augment_kernel(_base_kernel_blocked(), _sample_request(30))
        story_design_kernel_from_dict(augmented)


# --------------------------------------------------------------------------- #
# 4b. 榜单级质量门 (确保不只是过闸,而是达到榜单级具象度)
# --------------------------------------------------------------------------- #


class TestRankingQuality:
    def test_grounded_beats_are_concrete_and_named(self) -> None:
        result = HeuristicOracle().deduce(_sample_request(30))
        # 每个 beat 都含具体压力词 (非抽象空话)
        for beat in result.beats:
            blob = beat.duty + beat.state_change + beat.payoff + beat.hook_or_aftereffect
            assert any(t in blob for t in RANKING_PRESSURE_TERMS), beat.chapter_range
        # 开局 beat 落到真实主角/对手名上
        opening = result.beats[0]
        head = opening.duty + opening.state_change + opening.hook_or_aftereffect
        assert "林烬" in head or "宁玄策" in head

    def test_grounded_output_is_ranking_ready(self) -> None:
        result = HeuristicOracle().deduce(_sample_request(30))
        assert result.ranking_ready
        assert not result.needs_enrichment
        assert not result.quality_findings

    def test_generic_abstract_beats_flagged_not_ready(self) -> None:
        # 一份"抽象空话"产出 (无具体压力/能动性/损失) 必须被判不达榜单级
        generic = OracleResult(
            beats=(
                EmergentBeat("1-10", "推进剧情发展", "人物有所成长", "故事更精彩", "继续往下"),
                EmergentBeat("11-20", "继续推进", "气氛变好", "读者满意", "敬请期待"),
            ),
            source="heuristic",
        )
        report = evaluate_oracle_quality(generic, _sample_request(20))
        assert not report.ranking_ready
        assert report.concrete_beats < report.total_beats
        assert report.findings

    def test_not_ready_marks_kernel_for_enrichment(self) -> None:
        generic = OracleResult(
            beats=(EmergentBeat("1-30", "推进剧情", "成长", "精彩", "待续"),),
            ranking_ready=False,
            quality_findings=("beat 缺具体压力",),
            source="heuristic",
        )
        augmented = augment_kernel(_base_kernel_blocked(), generic, target_chapters=30)
        assert augmented["oracle_meta"]["needs_enrichment"] is True

    def test_ready_marks_kernel_ranking_ready(self) -> None:
        result = HeuristicOracle().deduce(_sample_request(30))
        augmented = augment_kernel(_base_kernel_blocked(), result, target_chapters=30)
        assert augmented["oracle_meta"]["ranking_ready"] is True


# --------------------------------------------------------------------------- #
# 5. 从真实小说导出
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not (NOVELS / "jin-tian-wen-dao").exists(), reason="需要 jin-tian-wen-dao 样本"
)
class TestExportFromNovel:
    def test_export_real_novel(self) -> None:
        req = export_request_from_novel(
            NOVELS / "jin-tian-wen-dao", target_chapters=40, volume_index=3
        )
        by_name = {c.name: c for c in req.characters}
        assert "林烬" in by_name
        # 关键:宁玄策必须是 Rival,不被"对照主角"误判
        assert by_name["宁玄策"].entity_type == "Rival"
        assert by_name["林烬"].entity_type == "Protagonist"
        # 快照应被挂上 (林烬有第30章后状态)
        assert by_name["林烬"].state

    def test_exported_request_drives_oracle(self) -> None:
        req = export_request_from_novel(NOVELS / "jin-tian-wen-dao", target_chapters=40)
        result = HeuristicOracle().deduce(req)
        assert max(int(b.chapter_range.split("-")[-1]) for b in result.beats) == 40


# --------------------------------------------------------------------------- #
# 6. 优雅降级
# --------------------------------------------------------------------------- #


class _ExplodingClient:
    def deduce(self, request: OracleRequest) -> object:
        raise RuntimeError("MiroFish 不可达")


class TestGracefulDegrade:
    def test_disabled_uses_heuristic(self) -> None:
        oracle = SimulationOracle(OracleConfig(enabled=False))
        result = oracle.deduce(_sample_request())
        assert result.source == "heuristic"

    def test_client_failure_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # SimulationOracle 是 frozen dataclass,patch 类方法而非实例属性
        monkeypatch.setattr(SimulationOracle, "_client", lambda self: _ExplodingClient())
        oracle = SimulationOracle(OracleConfig(enabled=True, base_url="http://x"))
        result = oracle.deduce(_sample_request())
        assert result.source == "heuristic"  # 失败回退,不抛

    def test_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIROFISH_ORACLE_ENABLED", "true")
        monkeypatch.setenv("MIROFISH_BASE_URL", "http://host:5001")
        cfg = OracleConfig.from_env()
        assert cfg.enabled and cfg.base_url == "http://host:5001"

    def test_narrative_ontology_shape(self) -> None:
        onto = narrative_ontology()
        assert len(onto["entity_types"]) == 10
        assert {"SEEKS", "OPPOSES", "OWES_DEBT_TO"} <= {e["name"] for e in onto["edge_types"]}

    def test_mirofish_client_constructs(self) -> None:
        # 仅构造,不发请求 (deduce 需真服务)
        client = MiroFishClient(OracleConfig(enabled=True, base_url="http://x"))
        assert client.config.base_url == "http://x"
