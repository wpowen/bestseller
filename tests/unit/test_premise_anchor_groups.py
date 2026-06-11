# ruff: noqa: RUF001, RUF002, RUF003
"""premise_anchor_groups 通用锚点推导回归测试。

背景：2026-06-11《神仙都是我招的》(slug zhaoshen-hr-1781168659) 的 1500 字
premise 只提取出 pressure=[审批/转正/编制] 和 title 两组锚点——根因是
_AUTO_ANCHOR_MARKERS 写死了旧书《临聘修仙从灵务局考编开始》的词表
(陆沉/灵务局/临聘/巡检/凶宅/风水师…)，对任何新 premise 只能提取出碰巧
撞上词表的词。

契约（通用型能力，禁止题材绑定）：
- 锚点必须从 premise 文本本身用通用模式推导（主角名/身份/机制/压力），
  词表只允许 *类别级* 通用词（跨题材通用的机制/压力名词），禁止任何
  具体某本书的专有名词。
- 保留轻量原则：具体锚点组不足 2 组时不强制对齐。
- title 锚点不再要求钩子连续包含书名 6 字片段，改为短 gram 稳健匹配。
"""

from __future__ import annotations

import inspect

from bestseller.domain.anti_commonsense_hook import HookSpec
import bestseller.services.hook_strength_gate as gate_module
from bestseller.services.hook_strength_gate import (
    evaluate_hook_strength_gate,
    hook_premise_alignment,
    premise_anchor_groups,
    repair_hook_spec_once,
)

ZHAOSHEN_PREMISE = (
    "失业HR陈屿（27岁，前大厂HRBP，因拒写恶意裁员名单被反向优化）"
    "入职薪资高得离谱的三垣人力资源有限公司，按奇葩JD招人，"
    "后发现公司是天庭驻人间办事处。这恰是凡人HR的专业。"
    "他的转正审批永远卡住，业绩越好编制越被剥离；工牌背面写着第七任。"
    "主角金手指：识人面板（看所有人命格履历与神职适配度）+Offer即封神（签发即授职）。"
    "他只递offer不逼签字。陈屿招的第一个神是外卖小哥赵小磊。"
)

ZHAOSHEN_CONTEXT = {
    "premise": ZHAOSHEN_PREMISE,
    "title": "神仙都是我招的",
    "genre": "都市脑洞·轻喜升级流",
    "sub_genre": "神明招募·职场喜剧",
}


def _spec(one_liner: str, core_rule: str, **overrides) -> HookSpec:
    payload = {
        "mechanism_key": "test",
        "genre": "都市",
        "base_desire": "翻身",
        "reversal": "必须付出代价才能兑现",
        "rewards": ("权限提升", "真相碎片"),
        "constraints": {"ban": "不能绕开核心规则", "time": "限时兑现"},
        "anti_cheat": ("重复触发收益衰减",),
        "costs": ("公开误解", "资源债务"),
        "misunderstanding": "旁人误读主角意图",
        "arc_engine": ("代价升级", "误解升级"),
        "one_liner": one_liner,
        "core_rule": core_rule,
    }
    payload.update(overrides)
    return HookSpec(**payload)


# ── ① 通用提取：新书 premise 必须提取出主角名/身份/机制 ──────────────


def test_new_premise_extracts_protagonist_name() -> None:
    groups = premise_anchor_groups(ZHAOSHEN_CONTEXT)
    assert "陈屿" in groups.get("protagonist", [])


def test_new_premise_extracts_identity_from_latin_role_token() -> None:
    groups = premise_anchor_groups(ZHAOSHEN_CONTEXT)
    assert "HR" in groups.get("identity", [])


def test_new_premise_extracts_core_mechanism() -> None:
    groups = premise_anchor_groups(ZHAOSHEN_CONTEXT)
    mechanism = groups.get("mechanism", [])
    assert any("识人面板" in anchor for anchor in mechanism)


def test_new_premise_keeps_generic_pressure_markers() -> None:
    groups = premise_anchor_groups(ZHAOSHEN_CONTEXT)
    pressure = set(groups.get("pressure", []))
    assert pressure & {"转正", "审批", "编制", "裁员", "失业"}


def test_extraction_is_genre_agnostic_for_unseen_premise() -> None:
    """完全不同题材（末世）也要靠通用模式提取出 >=2 组具体锚点。"""

    groups = premise_anchor_groups(
        {
            "premise": (
                "末世第三年，林晚是青城避难所的物资调度员，"
                "靠「以物换命」契约系统续命；林晚必须在倒计时归零前交付配额，"
                "否则被除名流放。"
            ),
            "title": "末世物资调度手册",
        }
    )
    assert "林晚" in groups.get("protagonist", [])
    assert any("调度员" in anchor for anchor in groups.get("identity", []))
    assert "以物换命" in groups.get("mechanism", [])
    assert set(groups.get("pressure", [])) & {"倒计时", "除名"}


def test_no_book_specific_marker_table_remains() -> None:
    """旧书专有名词表必须删除——模块源码不得再包含题材绑定词表。"""

    assert not hasattr(gate_module, "_AUTO_ANCHOR_MARKERS")
    source = inspect.getsource(gate_module)
    for book_specific in ("陆沉", "灵务局", "困魂镜", "凶宅", "风水师", "双穿门"):
        assert book_specific not in source, f"题材绑定词 {book_specific} 仍在源码里"


# ── ③ title 锚点稳健化 ────────────────────────────────────────────────


def test_title_anchors_include_short_grams() -> None:
    groups = premise_anchor_groups(ZHAOSHEN_CONTEXT)
    assert "神仙" in groups.get("title", [])


def test_hook_matching_title_gram_counts_title_group() -> None:
    aligned_spec = _spec(
        one_liner="失业HR陈屿入职诡异公司，神仙都归他招，越招职级越降。",
        core_rule="识人面板看穿神职适配度，Offer签发即封神，但他的转正审批永远卡住。",
        protagonist_role="失业HR陈屿",
    )
    aligned, matched, _ = hook_premise_alignment(aligned_spec, ZHAOSHEN_CONTEXT)
    assert aligned
    assert len(matched) >= 2


# ── ② 轻量原则：锚点不足 2 组不强制对齐 ──────────────────────────────


def test_alignment_not_enforced_with_insufficient_anchors() -> None:
    vague_context = {"premise": "一个普通人获得系统，开始升级。"}
    mismatched = _spec(
        one_liner="废柴少年捡剑逆袭",
        core_rule="剑灵每天教一招",
    )
    aligned, _, _ = hook_premise_alignment(mismatched, vague_context)
    assert aligned


def test_template_hook_still_rejected_against_rich_premise() -> None:
    junk = _spec(
        one_liner="废柴少年捡剑逆袭",
        core_rule="剑灵每天教一招",
    )
    report = evaluate_hook_strength_gate(
        junk,
        min_h_norm=30,
        premise_context=ZHAOSHEN_CONTEXT,
    )
    assert any(item.code == "hook_premise_mismatch" for item in report.findings)
    assert report.verdict == "reject"


# ── repair_hook_spec_once 锚点保持 ───────────────────────────────────


def test_repair_preserves_anchor_alignment_when_rewrite_drops_anchors() -> None:
    """改写钩子时若锚点词只活在 one_liner 里，修复后必须保持对齐。"""

    spec = _spec(
        one_liner="失业HR陈屿靠识人面板招神，神仙都是他招的，越招转正越远。",
        core_rule="每次兑现都要付出代价。",
        rewards=("钱",),
        constraints={"ban": "不能作弊"},
        anti_cheat=(),
        costs=(),
        misunderstanding=None,
        arc_engine=(),
    )
    aligned_before, _, _ = hook_premise_alignment(spec, ZHAOSHEN_CONTEXT)
    assert aligned_before

    report = evaluate_hook_strength_gate(
        spec,
        min_h_norm=99,
        premise_context=ZHAOSHEN_CONTEXT,
    )
    repaired = repair_hook_spec_once(spec, report, premise_context=ZHAOSHEN_CONTEXT)
    aligned_after, _, _ = hook_premise_alignment(repaired, ZHAOSHEN_CONTEXT)
    assert aligned_after


def test_repair_without_premise_context_keeps_legacy_signature() -> None:
    spec = _spec(
        one_liner="主角想赚钱，获得万能系统。",
        core_rule="万能系统给钱。",
        rewards=("钱",),
        constraints={"ban": "不能作弊"},
        anti_cheat=(),
        costs=(),
        misunderstanding=None,
        arc_engine=(),
    )
    report = evaluate_hook_strength_gate(spec, min_h_norm=30)
    repaired = repair_hook_spec_once(spec, report)
    assert repaired.costs


# ── 结构化通道仍然优先可用 ───────────────────────────────────────────


def test_structured_channels_still_consumed() -> None:
    groups = premise_anchor_groups(
        {
            "premise": "短premise。",
            "main_characters": [{"name": "苏离", "identity": "急诊科医生"}],
            "story_title_dna": {
                "protagonist": "苏离",
                "central_action": "倒卖阳寿",
                "stakes": "每次交易折损十年记忆",
            },
        }
    )
    assert "苏离" in groups.get("protagonist", [])
    assert "倒卖阳寿" in groups.get("mechanism", [])
