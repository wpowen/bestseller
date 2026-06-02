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


def test_emotion_keyword_dimension_raises_expansion() -> None:
    """A hook that names a 爆款 emotion word scores higher on expansion than one without."""

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
    assert score_hook(strong_spec).expansion > score_hook(flat_spec).expansion


def test_villain_visibility_keywords_raise_misunderstanding() -> None:
    """A hook that names a concrete opponent scores higher on misunderstanding than one that does not."""

    base = _strong_spec()
    visible = base.model_copy(update={"misunderstanding": "敌人和对手把主角当冤大头，婆家也在围观"})
    invisible = base.model_copy(update={"misunderstanding": "情况变得复杂"})
    assert score_hook(visible).misunderstanding >= score_hook(invisible).misunderstanding


def test_weak_emotion_keywords_finding_is_emitted_for_flat_spec() -> None:
    flat = _strong_spec().model_copy(
        update={
            "one_liner": "主角想赚钱翻身，必须越亏越强。",
            "core_rule": "每次获得商业回报都必须真实亏损并绑定公开误解与反作弊压力。",
        }
    )
    report = evaluate_hook_strength_gate(flat, min_h_norm=30)
    assert any(item.code == "weak_emotion_keywords" for item in report.findings)


def test_repair_hook_spec_once_adds_emotion_arc_axes() -> None:
    """weak_emotion_keywords finding triggers arc_engine bumps with emotion markers."""

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
    assert "打脸" in arc_blob or "围观" in arc_blob
