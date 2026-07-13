# ruff: noqa: RUF001
from __future__ import annotations

from bestseller.domain.anti_commonsense_hook import HookSpec
from bestseller.services.hook_strength_gate import (
    evaluate_hook_strength_gate,
    repair_hook_spec_once,
    score_hook,
)


def _strong_spec() -> HookSpec:
    return HookSpec(
        mechanism_key="forced_loss",
        genre="都市",
        setting_locale="现代职场",
        protagonist_role="主角",
        base_desire="赚钱翻身",
        reversal="必须亏损、放弃收益并让第三方真实受益才能升级",
        rewards=("商业权限", "证据显影", "公开声望"),
        constraints={
            "time": "每次触发必须在公开时限内完成",
            "object": "必须绑定真实受益对象",
            "method": "不能虚假交易",
            "ban": "不能左右手倒钱刷奖励",
        },
        anti_cheat=("虚假交易不结算", "同一对象重复触发收益衰减", "绕开代价会反噬"),
        costs=("现金流断裂", "亲友误解", "公开声望绑架"),
        misunderstanding="所有人都以为主角败家，敌人把他当冤大头",
        arc_engine=("亏损规模", "受益对象", "市场反噬", "公开误判"),
        one_liner="主角想赚钱翻身，却必须越亏越强；赢来商业权限，也付出现实现金流断裂。",
        core_rule="每次获得商业回报都必须真实亏损并绑定公开误解与反作弊压力。",
    )


def test_score_hook_uses_multiplicative_h_norm() -> None:
    score = score_hook(_strong_spec())

    assert score.delta >= 6
    assert score.constraint >= 8
    assert score.penalty >= 7
    assert score.h_norm >= 45
    assert score.verdict == "expand"


def test_evaluate_hook_strength_gate_reports_rewrite_suggestions() -> None:
    report = evaluate_hook_strength_gate("一个普通人突然获得万能系统。", min_h_norm=30)

    assert not report.passed
    assert any(item.code == "below_h_norm_threshold" for item in report.findings)
    assert report.rewrite_suggestions


def test_repair_hook_spec_once_improves_failed_structured_hook() -> None:
    weak = HookSpec(
        mechanism_key="weak",
        genre="都市",
        base_desire="赚钱",
        reversal="获得系统",
        rewards=("钱",),
        constraints={"ban": "不能作弊"},
        anti_cheat=(),
        costs=(),
        misunderstanding=None,
        arc_engine=(),
        one_liner="主角想赚钱，获得万能系统。",
        core_rule="万能系统给钱。",
    )
    report = evaluate_hook_strength_gate(weak, min_h_norm=30)

    repaired = repair_hook_spec_once(weak, report)
    repaired_report = evaluate_hook_strength_gate(repaired, min_h_norm=30)

    assert repaired_report.h_norm > report.h_norm
    assert repaired.constraints.keys() >= {"ban", "time"}
    assert repaired.costs


def test_emotion_keywords_do_not_fake_expansion_capacity() -> None:
    """Adding 打脸/围观 copy must not masquerade as long-form capacity."""

    strong = _strong_spec()
    flat = _strong_spec()
    # Inject emotion word into the strong one, leave flat without.
    strong_spec = strong.model_copy(
        update={
            "one_liner": strong.one_liner + " 全网围观打脸炸场",
            "core_rule": strong.core_rule + " 立威破防。",
        }
    )
    flat_spec = flat.model_copy(
        update={
            "one_liner": "主角想赚钱翻身，却必须越亏越强；赢来商业权限，也付出现实现金流断裂。",
            "core_rule": "每次获得商业回报都必须真实亏损并绑定公开误解与反作弊压力。",
        }
    )
    assert score_hook(strong_spec).expansion == score_hook(flat_spec).expansion


def test_villain_visibility_keywords_raise_misunderstanding() -> None:
    """A hook that names a concrete opponent scores higher on misunderstanding than one that does not."""

    base = _strong_spec()
    visible = base.model_copy(update={"misunderstanding": "敌人和对手把主角当冤大头，婆家也在围观"})
    invisible = base.model_copy(update={"misunderstanding": "情况变得复杂"})
    assert score_hook(visible).misunderstanding >= score_hook(invisible).misunderstanding


def test_flat_spec_is_not_forced_to_add_emotion_slogans() -> None:
    flat = _strong_spec().model_copy(
        update={
            "one_liner": "主角想赚钱翻身，必须越亏越强。",
            "core_rule": "每次获得商业回报都必须真实亏损并绑定公开误解与反作弊压力。",
        }
    )
    report = evaluate_hook_strength_gate(flat, min_h_norm=30)
    assert not any(item.code == "weak_emotion_keywords" for item in report.findings)


def test_repair_does_not_add_emotion_arc_axes() -> None:
    """Legacy findings must not inject 打脸/围观 into the story engine."""

    weak = HookSpec(
        mechanism_key="weak",
        genre="都市",
        base_desire="赚钱",
        reversal="获得系统",
        rewards=("钱",),
        constraints={"ban": "不能作弊"},
        anti_cheat=(),
        costs=(),
        misunderstanding=None,
        arc_engine=(),
        one_liner="主角想赚钱，获得万能系统。",
        core_rule="万能系统给钱。",
    )
    # Force the emotion finding to fire even if the gate would otherwise pass.
    from bestseller.services.hook_strength_gate import HookStrengthFinding, HookStrengthGateReport

    fake_report = HookStrengthGateReport(
        findings=(
            HookStrengthFinding(
                code="weak_emotion_keywords",
                severity="low",
                message="Hook lacks CN emotion vocabulary markers.",
                path="one_liner",
                repair_action="Inject 打脸/翻盘/etc.",
            ),
        ),
        h_norm=50.0,
        passed=True,
        rewrite_suggestions=(),
        score=score_hook(weak),
        verdict="pass",
    )
    repaired = repair_hook_spec_once(weak, fake_report)
    arc_blob = " ".join(repaired.arc_engine)
    assert "打脸" not in arc_blob and "围观" not in arc_blob


def test_hook_strength_gate_rejects_premise_mismatched_hook() -> None:
    premise_context = {
        "premise": (
            "灵气复苏第七年，沈砚是海城灵务署最底层的临聘巡检员，"
            "靠岗位权限、公务工单和考编资格在修仙公共系统里升级。"
        ),
        "title": "临聘仙官从工单考编开始",
        "genre": "都市修仙·职业升级流",
    }
    mismatched = HookSpec(
        mechanism_key="script_within_script",
        genre="悬疑",
        base_desire="接近真凶",
        reversal="越接近真凶，越发现自己只是嵌套剧本里的演员",
        rewards=("第四面墙突破", "真相碎片", "权限提升"),
        constraints={
            "method": "必须承认自己被写进剧本",
            "ban": "不能直接跳出嵌套叙事",
            "time": "每轮真相必须公开兑现",
        },
        anti_cheat=("跳过剧本会反噬", "重复真相收益衰减"),
        costs=("真相反噬", "自我身份塌陷", "公开误解升级"),
        misunderstanding="旁人以为主角在演戏，敌人把他当剧本变量",
        arc_engine=("剧本层级", "真相反噬", "误解升级", "打脸升级"),
        one_liner="想接近真凶？可以，但越接近真凶，越发现自己只是嵌套剧本里的演员。",
        core_rule="揭谜必须先承认自己也在被写，第四面墙突破会留下真相反噬。",
    )

    report = evaluate_hook_strength_gate(
        mismatched,
        min_h_norm=30,
        premise_context=premise_context,
    )

    assert not report.passed
    assert any(
        item.code == "hook_premise_mismatch" and item.severity == "high"
        for item in report.findings
    )
    assert report.verdict == "reject"


def test_hook_strength_gate_allows_premise_aligned_hook() -> None:
    premise_context = {
        "premise": (
            "灵气复苏第七年，沈砚是海城灵务署最底层的临聘巡检员，"
            "靠岗位权限、公务工单和考编资格在修仙公共系统里升级。"
        ),
        "title": "临聘仙官从工单考编开始",
        "genre": "都市修仙·职业升级流",
    }
    aligned = HookSpec(
        mechanism_key="bureaucratic_cultivation",
        genre="都市修仙",
        setting_locale="海城灵务署",
        protagonist_role="沈砚，灵务署临聘巡检员",
        base_desire="考上正式编制并保住妹妹灵石配额",
        reversal="每次想升职都必须先用最低权限接下别人不敢签的公务工单",
        rewards=("岗位权限提升", "考编积分", "灵石配额", "公开打脸"),
        constraints={
            "object": "必须绑定真实灵务署工单",
            "method": "只能用岗位权限和合规条款破局",
            "ban": "不能绕过审批私斗",
            "time": "每张工单都有办结时限",
        },
        anti_cheat=("越权执法会扣考编分", "重复工单收益衰减", "伪造工单直接除名"),
        costs=("背锅处分", "考编扣分", "妹妹配额被卡", "上司公开误解"),
        misunderstanding="上司以为沈砚抢功，围观修士等着看临聘巡检员塌房",
        arc_engine=("岗位权限", "公务工单", "考编积分", "灵务署派系", "打脸升级"),
        one_liner="沈砚想考进灵务署正式编，却只能靠最低岗位权限签高危工单；每次破局都涨考编分，也背上公开处分。",
        core_rule="公务工单越危险，岗位权限兑现越高；越权会扣考编分，合规破局才能当众打脸。",
    )

    report = evaluate_hook_strength_gate(
        aligned,
        min_h_norm=30,
        premise_context=premise_context,
    )

    assert report.passed
    assert not any(item.code == "hook_premise_mismatch" for item in report.findings)
