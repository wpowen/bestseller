"""卷计划引用规范化——验收端要求的文本必须可推导，不许指望 LLM 抄准长句。

2026-08-29 真机《破庙里我把玉玺摔成四瓣》定案：卷计划 prompt（含修复循环）
命令模型「逐字引用批准的 seriality_phase_ref」，但批准列表从没渲染进任何
prompt——模型被要求抄它看不见的 80-120 字长句，10/10 卷
phase_reference_invalid，建书死在 foundation（generate_foundation_plan
failed: Concept contract invalid）。
"""

import pytest

from bestseller.services.seriality_volume_gate import (
    canonicalize_seriality_volume_refs,
    evaluate_seriality_volume_mapping,
    render_seriality_volume_contract_block,
)

pytestmark = pytest.mark.unit

PHASES = [
    "第1至120章：单瓣入门与一县存身。用最小一瓣入主家祠堂上谱，被识破碎玺带来第一轮定位",
    "第121至250章：两瓣并拢与首场公开博弈。必须在宗法夺嗣前决定合并顺序",
    "第251至380章：三瓣在握与南北十郡对峙。私通被截获，三房公然摊牌",
    "第381至500章：四瓣并拢仪式与代奉危机总爆发。以正统承运者身份完成辨嗣终局",
]
FAMILIES = ["族内认嗣攻防", "碎玺定位追逃", "民心归属争夺", "老将忠诚互锁"]
TRACKS = ["已并拢的玺瓣数", "祠堂门禁控制权", "听玺司定位精度"]


def _contract():
    return {
        "seriality_proof": {
            "phase_transitions": PHASES,
            "unit_families": FAMILIES,
            "accumulation_tracks": TRACKS,
            "capacity_report": {"target_chapters": 500, "capacity_tier": "long"},
        }
    }


def _volume(n, chapters, **kw):
    base = {
        "volume_number": n,
        "chapter_count_target": chapters,
        "unit_family_ref": FAMILIES[(n - 1) % len(FAMILIES)],
        "renewable_unit_variant": f"第{n}卷专属变体",
        "accumulation_track_deltas": [
            {"track_ref": TRACKS[(n - 1) % len(TRACKS)], "delta": f"从状态{n}甲变为状态{n}乙不可逆"}
        ],
    }
    base.update(kw)
    return base


class TestRefIsDerivedFromId:
    def test_a_valid_id_overwrites_whatever_ref_the_model_wrote(self):
        plan = [_volume(1, 500, seriality_phase_id="phase-01", seriality_phase_ref="模型随手写的近似句")]
        out = canonicalize_seriality_volume_refs(plan, _contract())
        assert out[0]["seriality_phase_ref"] == PHASES[0]

    def test_a_close_but_inexact_ref_is_normalized_and_id_backfilled(self):
        plan = [_volume(1, 500, seriality_phase_ref=PHASES[1][:20])]
        out = canonicalize_seriality_volume_refs(plan, _contract())
        assert out[0]["seriality_phase_ref"] == PHASES[1]
        assert out[0]["seriality_phase_id"] == "phase-02"

    def test_missing_both_falls_back_to_chapter_range_inference(self):
        """阶段原文自带「第X至Y章」——比让模型抄句子可靠得多的信号源。"""
        plan = [
            _volume(1, 120),
            _volume(2, 130),
            _volume(3, 130),
            _volume(4, 120),
        ]
        out = canonicalize_seriality_volume_refs(plan, _contract())
        assert [v["seriality_phase_id"] for v in out] == [
            "phase-01", "phase-02", "phase-03", "phase-04",
        ]

    def test_idempotent(self):
        plan = [_volume(1, 500, seriality_phase_id="phase-01", seriality_phase_ref="x")]
        once = canonicalize_seriality_volume_refs(plan, _contract())
        twice = canonicalize_seriality_volume_refs(once, _contract())
        assert once == twice

    def test_no_contract_is_a_noop(self):
        plan = [_volume(1, 500)]
        assert canonicalize_seriality_volume_refs(plan, None) == plan


class TestFuzzyFamilyAndTrack:
    def test_family_prefix_is_normalized_to_approved_text(self):
        plan = [_volume(1, 500, seriality_phase_id="phase-01", unit_family_ref="族内认嗣")]
        out = canonicalize_seriality_volume_refs(plan, _contract())
        assert out[0]["unit_family_ref"] == FAMILIES[0]

    def test_unmatchable_family_is_left_for_the_gate(self):
        plan = [_volume(1, 500, seriality_phase_id="phase-01", unit_family_ref="完全无关内容")]
        out = canonicalize_seriality_volume_refs(plan, _contract())
        assert out[0]["unit_family_ref"] == "完全无关内容"


class TestPomiaoRegression:
    def test_the_exact_failure_shape_now_passes_after_canonicalization(self):
        """真机死因复刻：模型给了 id 但 ref 抄不准 → 规范化后过门。"""
        plan = []
        specs = [(1, "phase-01", 120), (2, "phase-02", 130), (3, "phase-03", 130), (4, "phase-04", 120)]
        for n, pid, ch in specs:
            plan.append(_volume(n, ch, seriality_phase_id=pid, seriality_phase_ref="近似复述而非逐字"))
        # 覆盖全部家族与积累轴
        plan[0]["unit_family_ref"] = FAMILIES[0]
        plan[1]["unit_family_ref"] = FAMILIES[1]
        plan[2]["unit_family_ref"] = FAMILIES[2]
        plan[3]["unit_family_ref"] = FAMILIES[3]
        for i, v in enumerate(plan):
            v["accumulation_track_deltas"] = [
                {"track_ref": TRACKS[j], "delta": f"卷{i+1}轴{j}从甲态永久变为乙态"}
                for j in range(len(TRACKS))
            ]
        before = evaluate_seriality_volume_mapping(plan, _contract())
        assert not before.passed  # 修复前：这就是 10/10 phase_reference_invalid 的形状
        out = canonicalize_seriality_volume_refs(plan, _contract())
        after = evaluate_seriality_volume_mapping(out, _contract())
        assert after.passed, [f.code + ":" + f.message for f in after.findings]


class TestContractIsShownToTheModel:
    def test_the_prompt_block_contains_numbered_phases_and_lists(self):
        block = render_seriality_volume_contract_block(_contract())
        assert "phase-01" in block and PHASES[0] in block
        assert FAMILIES[0] in block and TRACKS[0] in block

    def test_the_volume_prompt_actually_renders_it(self):
        """接线检查：块函数存在不等于被调用——grep 生成端源码确认消费。"""
        import inspect

        from bestseller.services import planner

        src = inspect.getsource(planner)
        assert "_seriality_contract_block" in src
        assert "render_seriality_volume_contract_block" in src
