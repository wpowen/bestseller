"""hook_strength_gate 自动降级回归测试。

背景：2026-06-11《神仙都是我招的》autowrite 在 foundation 第一步即被
hook_strength_gate 拍死——项目未提供 hook_spec 时，闸门用题材模板自动生成
候选钩子，再用 premise 锚点校验该候选；模板候选必然不含新书 premise 的
锚点词，hook_premise_mismatch 硬失败后直接抛 PlannerFallbackError 杀掉全流程。

契约（2026-06-11 起，见 commit e935e68「反常识钩子被拒不再硬毙整本书」）：
- 钩子引擎 soft-by-design：任何被 reject 的钩子（无论自动生成还是用户提供）都
  降级续跑（返回 None + auto_demoted payload），绝不抛 PlannerFallbackError——
  为一个可选的开篇增强而拍死一本 500 章的书是不可接受的。
- 自动生成候选被 reject → demote_reason="auto_generated_hook_rejected"。
- 用户显式提供的 hook_spec 被 reject → demote_reason="provided_hook_rejected"
  （仍降级，但记不同原因，便于审计区分作者声明 vs 框架兜底）。
- 用户提供的合格 hook_spec → 正常通过并返回 payload。
"""

from types import SimpleNamespace

import pytest

from bestseller.services.planner import _run_hook_strength_gate

PREMISE = (
    "失业HR陈屿入职三垣人力资源有限公司，按奇葩JD招人，后发现公司是天庭驻人间办事处。"
    "他的转正审批永远卡住，业绩越好编制越被剥离；工牌背面写着第七任。"
    "他招的第一个神是外卖小哥赵小磊。"
)

TITLE = "神仙都是我招的"


def _settings(enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        hook_engine=SimpleNamespace(
            enabled=enabled,
            min_h_norm=30.0,
            candidate_count=4,
            rank_weight_h_norm=0.62,
            rank_weight_novelty=0.28,
            rank_weight_duplicate_risk=0.10,
        )
    )


def _project(metadata: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        slug="test-auto-demote",
        title=TITLE,
        genre="都市脑洞·轻喜升级流",
        sub_genre="神明招募·职场喜剧",
        language="zh-CN",
        metadata_json=dict(metadata or {}),
    )


def _good_hook_spec() -> dict:
    # 要素齐全（rewards/constraints/anti_cheat/costs/misunderstanding/arc_engine）
    # 使 h_norm 首评即过线，不触发 repair_hook_spec_once（其改写可能丢失锚点词）。
    return {
        "mechanism_key": "divine_recruitment_offer",
        "genre": "都市脑洞·轻喜升级流",
        "setting_locale": "现代都市·天庭驻人间办事处",
        "protagonist_role": "失业HR陈屿",
        "base_desire": "失业三个月、第27次面试被刷的HR陈屿，只想拿下月薪八万的工作翻身，赢回转正与编制",
        "reversal": "公司竟是缺编一千年的天庭，他招的全是神明；而这场招聘真正的考核对象是他自己——天帝候补第七人",
        "rewards": [
            "识人面板：看穿所有人命格履历与神职适配度",
            "Offer即封神：签发录用通知即授神职",
            "他招进来的每一个神都接他电话——通讯录即神通",
            "权限逐卷升级：授职→调岗→开除恶神→创设新职位",
        ],
        "constraints": {
            "identity": "无命格者：看不见自己的面板，也不知道自己是候补",
            "rank": "业绩越好职级越降——天帝位无品阶，系统自动剥离他的编制",
            "principle": "只递offer不逼签字，绝不替人决定人生",
        },
        "anti_cheat": [
            "面板只给适配度不给答案，人心向背要靠蹲点背调的HR功夫",
            "中后期强敌不吃面板，吃的是他攒下的人情与制度设计",
        ],
        "costs": [
            "每发一份神职offer就背一份连带责任，所荐之神失职反噬荐主",
            "转正审批永远通不过，前六任的下场悬在头顶",
        ],
        "misunderstanding": "全天庭都以为他只是个会招人的凡人临时工，没人想到这场缺编千年的招聘真正考核的就是他",
        "arc_engine": [
            "每个神职空缺=一个招聘案件=一段人间故事",
            "暗线A：天帝候补第七人考核",
            "暗线B：候补不止一人——司衡每代泄露名单诱导内斗",
        ],
        "hook_type": "profession_reversal",
        "opening_frame": "失业第27次面试被刷当天，收到月薪八万的诡异offer",
        "one_liner": "失业HR入职月薪八万的诡异公司——神仙都是我招的：素人封神全网围观打脸，而真正被考核的是他，天帝候补第七人",
        "core_rule": "天庭缺编一千年，神职移交需人岗缘法匹配，凡人HR的识人面板与Offer封神权是唯一通道；但他看不见自己的面板，业绩越好转正越远、编制剥离越快，前六任HR全部出局",
    }


def _junk_hook_spec() -> dict:
    # 与 premise 锚点（转正/审批/编制 + 书名片段）零交集的钩子
    return {
        "mechanism_key": "generic_template",
        "genre": "玄幻",
        "base_desire": "少年想要变强",
        "reversal": "捡到一把剑发现是上古剑灵",
        "one_liner": "废柴少年捡剑逆袭",
        "core_rule": "剑灵每天教一招",
    }


@pytest.mark.asyncio
async def test_auto_generated_reject_demotes_instead_of_raising():
    """无 hook_spec 时模板候选被拒 → 返回 None + auto_demoted，不抛异常。"""
    spec, payload = await _run_hook_strength_gate(
        _settings(), project=_project(), premise=PREMISE
    )
    if payload is None:
        # 候选生成为空的边界：跳过钩子引擎本身就是非阻断行为，契约同样满足
        assert spec is None
    elif payload.get("auto_demoted"):
        assert spec is None
        assert payload["demote_reason"] == "auto_generated_hook_rejected"
    else:
        # 模板恰好对齐则正常通过——同样不允许抛异常
        assert payload.get("verdict") in {"pass", "warn_only"} or payload.get("passed")


@pytest.mark.asyncio
async def test_provided_junk_hook_demotes_with_provided_reason():
    """用户显式提供且与 premise 锚点零交集的钩子 → 降级续跑（不抛异常），
    并记 demote_reason="provided_hook_rejected" 以便审计区分作者声明 vs 兜底。"""
    project = _project({"hook_spec": _junk_hook_spec()})
    spec, payload = await _run_hook_strength_gate(
        _settings(), project=project, premise=PREMISE
    )
    assert spec is None
    assert payload is not None
    assert payload.get("auto_demoted") is True
    assert payload["demote_reason"] == "provided_hook_rejected"


@pytest.mark.asyncio
async def test_provided_good_hook_passes():
    """用户提供的对齐钩子 → 正常通过并返回原 spec。"""
    project = _project({"hook_spec": _good_hook_spec()})
    spec, payload = await _run_hook_strength_gate(
        _settings(), project=project, premise=PREMISE
    )
    assert spec is not None
    assert payload is not None and not payload.get("auto_demoted")


@pytest.mark.asyncio
async def test_gate_disabled_returns_none():
    spec, payload = await _run_hook_strength_gate(
        _settings(enabled=False), project=_project(), premise=PREMISE
    )
    assert spec is None and payload is None
