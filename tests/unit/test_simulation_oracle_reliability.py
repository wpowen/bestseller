"""Reliability & end-to-end verification for the Simulation Oracle.

验证两件事:
- **可用性**:真实小说数据走完整链路(export→deduce→augment→gate)能跑通且解阻断。
- **可靠性**:确定性、幂等、无副作用、边界鲁棒、降级安全、leak-free、对抗输入清洗。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bestseller.services.prewrite_quality_profile import (
    evaluate_story_design_kernel_quality,
    has_kernel_leak,
)
from bestseller.services.simulation_oracle import (
    CharacterSeed,
    HeuristicOracle,
    OracleRequest,
    SimulationOracle,
    augment_kernel,
    evaluate_oracle_quality,
    export_request_from_novel,
)
from bestseller.services.story_design_kernel import story_design_kernel_from_dict

NOVELS = Path(__file__).resolve().parents[2] / "output" / "ai-generated"


# --------------------------------------------------------------------------- #
# 共享:最小合法 base kernel scaffold(模拟 planner 产出的待增强骨架)
# --------------------------------------------------------------------------- #


def _scaffold(protagonist_key: str, antagonist: str = "对手") -> dict:
    """一个 beat 只覆盖开局的最小合法骨架(会触发 beat_schedule_incomplete)。"""
    return {
        "reader_promise": "每章产生可见的状态变化与代价。",
        "premise_contract": {
            "unique_hook": f"{protagonist_key}被迫当场反抗{antagonist}的封锁。",
            "core_question": "挑战旧秩序者会否变成新的压迫者?",
            "commercial_pull": "逆袭爽点叠加制度反思。",
        },
        "character_conflict_contracts": [
            {
                "character_key": protagonist_key,
                "external_goal": "打破既有垄断。",
                "internal_need": "证明自己配得上所求之道。",
                "pressure_source": f"{antagonist}一方的围剿与灭口威胁。",
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
                "label": f"{protagonist_key}重写规则",
                "role": "主线",
                "current_state": "规则刚被撬开一道缝。",
                "target_state": "新秩序站稳并通过代价检验。",
                "failure_if_removed": "无主线则全书崩塌。",
            }
        ],
        "beat_schedule": [
            {
                "chapter_range": "1-3",
                "duty": "建立开局承诺。",
                "state_change": "主角被迫出招。",
                "payoff": "兑现第一个爽点。",
                "hook_or_aftereffect": "抛出旧案谜团。",
            }
        ],
        "change_vectors": ["从弱到强、从挑战者到立规者"],
    }


def _request(target: int = 40) -> OracleRequest:
    return OracleRequest(
        slug="rel-demo",
        target_chapters=target,
        characters=(
            CharacterSeed("林烬", "主角，打破修行垄断。", "Protagonist", "化海稳定。"),
            CharacterSeed("苏晚照", "阵法师，同伴。", "Ally", "体系核心协助者。"),
            CharacterSeed("宁玄策", "九宗天骄，既得利益代表。", "Rival"),
        ),
    )


# --------------------------------------------------------------------------- #
# 可靠性
# --------------------------------------------------------------------------- #


class TestDeterminism:
    def test_deduce_is_deterministic(self) -> None:
        a = HeuristicOracle().deduce(_request())
        b = HeuristicOracle().deduce(_request())
        assert a == b  # frozen dataclass 值相等;无随机/时间依赖

    def test_augment_is_deterministic(self) -> None:
        result = HeuristicOracle().deduce(_request())
        k1 = augment_kernel(_scaffold("林烬"), result, target_chapters=40)
        k2 = augment_kernel(_scaffold("林烬"), result, target_chapters=40)
        assert json.dumps(k1, ensure_ascii=False, sort_keys=True) == json.dumps(
            k2, ensure_ascii=False, sort_keys=True
        )


class TestIdempotencyAndPurity:
    def test_augment_idempotent_no_subplot_duplication(self) -> None:
        result = HeuristicOracle().deduce(_request())
        once = augment_kernel(_scaffold("林烬"), result, target_chapters=40)
        twice = augment_kernel(once, result, target_chapters=40)
        assert len(twice["plot_tree"]) == len(once["plot_tree"])  # 不重复注入
        assert len(twice["beat_schedule"]) == len(once["beat_schedule"])

    def test_augment_does_not_mutate_input(self) -> None:
        base = _scaffold("林烬")
        snapshot = copy.deepcopy(base)
        result = HeuristicOracle().deduce(_request())
        augment_kernel(base, result, target_chapters=40)
        assert base == snapshot  # 入参未被原地修改(immutability)


class TestEdgeCases:
    @pytest.mark.parametrize("target", [0, 1, 2, 3, 50, 2000])
    def test_segment_beats_never_crash_and_cover(self, target: int) -> None:
        result = HeuristicOracle().deduce(_request(target))
        assert result.beats  # 任何章数都产出至少一个 beat
        max_ch = max(int(b.chapter_range.split("-")[-1]) for b in result.beats)
        if target >= 1:
            assert max_ch <= max(target, 1)

    @pytest.mark.parametrize("target", [10, 30, 100, 800, 2000])
    def test_large_books_unblock_gate(self, target: int) -> None:
        result = HeuristicOracle().deduce(_request(target))
        augmented = augment_kernel(_scaffold("林烬"), result, target_chapters=target)
        report = evaluate_story_design_kernel_quality(augmented, target_chapters=target)
        codes = {f.code for f in (*report.blocking_findings, *report.audit_findings)}
        assert "beat_schedule_incomplete" not in codes
        story_design_kernel_from_dict(augmented)  # 仍校验通过

    def test_no_protagonist(self) -> None:
        req = OracleRequest(
            slug="x",
            target_chapters=30,
            characters=(CharacterSeed("某甲", "路人。", "Character"),),
        )
        result = HeuristicOracle().deduce(req)
        assert result.beats  # 退化为"主角/对手"占位,不崩

    def test_no_characters_at_all(self) -> None:
        req = OracleRequest(slug="x", target_chapters=30, characters=())
        result = HeuristicOracle().deduce(req)
        assert result.beats and not result.subplots

    def test_no_rival_uses_placeholder(self) -> None:
        req = OracleRequest(
            slug="x",
            target_chapters=30,
            characters=(CharacterSeed("林烬", "主角。", "Protagonist"),),
        )
        result = HeuristicOracle().deduce(req)
        blob = result.beats[0].duty + result.beats[0].hook_or_aftereffect
        assert "林烬" in blob  # 主角名落地;对手退化为"对手"占位不崩


class TestExportRobustness:
    def test_missing_dir_does_not_crash(self, tmp_path: Path) -> None:
        req = export_request_from_novel(tmp_path / "nope", target_chapters=20)
        assert req.characters == () and req.canon_edges == ()
        # 空请求仍能驱动 oracle 且不崩
        result = HeuristicOracle().deduce(req)
        assert result.beats

    def test_empty_story_bible(self, tmp_path: Path) -> None:
        (tmp_path / "story-bible").mkdir(parents=True)
        req = export_request_from_novel(tmp_path, target_chapters=20)
        assert req.characters == ()


class TestLeakSafety:
    def test_adversarial_leak_tokens_sanitized(self) -> None:
        # 角色描述里混入会触发 fallback_source_leak 的英文兜底词
        req = OracleRequest(
            slug="x",
            target_chapters=30,
            characters=(
                CharacterSeed("林烬", "主角。", "Protagonist"),
                CharacterSeed(
                    "污染体", "fallback_progress siege_under_pressure 既得利益代表。", "Rival"
                ),
            ),
        )
        result = HeuristicOracle().deduce(req)
        augmented = augment_kernel(_scaffold("林烬", "污染体"), result, target_chapters=30)
        blob = json.dumps(augmented, ensure_ascii=False)
        assert not has_kernel_leak(blob)  # 注入后整 kernel 无泄漏
        report = evaluate_story_design_kernel_quality(augmented, target_chapters=30)
        codes = {f.code for f in (*report.blocking_findings, *report.audit_findings)}
        assert "fallback_source_leak" not in codes


class TestDegradeReliability:
    def test_facade_disabled_path_full_chain(self) -> None:
        oracle = SimulationOracle()  # 默认禁用 → heuristic
        augmented = oracle.augment_kernel(_scaffold("林烬"), _request(40))
        story_design_kernel_from_dict(augmented)
        assert augmented["oracle_meta"]["source"] == "heuristic"


# --------------------------------------------------------------------------- #
# 可用性:真实小说端到端
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not (NOVELS / "jin-tian-wen-dao").exists(), reason="需要 jin-tian-wen-dao 样本"
)
class TestEndToEndRealNovel:
    def test_full_chain_unblocks_and_validates(self) -> None:
        # 1. 从真实小说导出
        req = export_request_from_novel(
            NOVELS / "jin-tian-wen-dao", target_chapters=40, volume_index=3
        )
        assert any(c.entity_type == "Protagonist" for c in req.characters)

        # 2. 真实主角/对手名
        proto = req.protagonist.name
        rival = next(c.name for c in req.characters if c.entity_type == "Rival")

        # 3. 推演 → 增强一个被阻断的骨架
        result = HeuristicOracle().deduce(req)
        base = _scaffold(proto, rival)
        before = evaluate_story_design_kernel_quality(base, target_chapters=40)
        before_codes = {f.code for f in (*before.blocking_findings, *before.audit_findings)}
        assert "beat_schedule_incomplete" in before_codes  # 增强前确被阻断

        augmented = augment_kernel(base, result, target_chapters=40)

        # 4. 增强后:gate 无 critical + kernel 合法 + 榜单 verdict
        after = evaluate_story_design_kernel_quality(augmented, target_chapters=40)
        after_codes = {f.code for f in (*after.blocking_findings, *after.audit_findings)}
        assert "beat_schedule_incomplete" not in after_codes
        assert "fallback_source_leak" not in after_codes
        kernel = story_design_kernel_from_dict(augmented)
        assert any(n.line_type == "main" for n in kernel.plot_tree)
        assert len(kernel.plot_tree) > 1  # 注入了涌现支线

        # 5. 真实数据下榜单级质量自检可用
        quality = evaluate_oracle_quality(result, req)
        assert quality.total_beats >= 1
        # beat 落到真实主角名上
        head = result.beats[0].duty + result.beats[0].state_change
        assert proto in head
