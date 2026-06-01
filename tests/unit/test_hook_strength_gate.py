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
