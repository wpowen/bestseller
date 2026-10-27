"""W4-2 回归：合成 reader persona 的失败**不得独立授权**阻断/重写（2026-09-10）。

病灶（计划 §3 W4-2 + `W4-AUTHORITY-MAP.md`）：`pipelines.py` 把 persona 失败的三条
finding 以 ``severity="critical"`` 原样并入 `RetentionGateReport`，并把其
``auto_repair_codes`` 直接追加。于是**一个确定性计数公式**（hook/payoff/emotion
过 sigmoid，阈值 0.62 高于写手实测 ~0.51 上限）可以**独立**把章节判为不合格并
路由到修复/重写。计划要求：纯合成偏好不应自动拥有换故事或整章改写权。

修：`pipelines._merge_persona_gate_findings` —— persona 失败只有在与独立证据
（retention gate 自身的阻断 finding）**相互印证**时才参与阻断；否则降为 advisory
只作留痕。retention gate 自身的判定完全不变（不关闭事实/状态检查）。

2026-09-11 (W5 审查 P1) 收紧：初版判据是 ``bool(base_repair_codes)``，等于把
**共现**当成**互证** —— 任意一个独立失败都能让全部 persona 失败升级并追加全部
persona 修复码。"开头钩子缺失"证明不了"爽点密度不足"。
现改为**逐项**判定，依据 :data:`PERSONA_CORROBORATION_LINKS` 中登记的、针对同一
具体问题的证据关系；未登记关系的一律 advisory。``require_corroboration=False``
是显式回滚开关，authority 记为 ``override_unrestricted``，**不得**冒充 corroborated。

验收要求（计划 §3 W4）：读取**决策回执**证明低代理分不能独自发出强制创作命令
——因此下面的断言落在 `RetentionGateReport.passed` / `has_critical` 与返回的
修复码上，而不是只看 prompt 字符串。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.persona_quality_gate import PERSONA_CORROBORATION_LINKS
from bestseller.services.pipelines import _merge_persona_gate_findings
from bestseller.services.retention_safety_gate import RetentionGateReport

pytestmark = pytest.mark.unit


def _persona_findings():
    return [
        SimpleNamespace(code="PERSONA_ABANDON_RATE_HIGH", detail="abandon_rate=0.64 > 0.35"),
        SimpleNamespace(code="PERSONA_WEIGHTED_SCORE_LOW", detail="weighted_score=0.37 < 0.62"),
        SimpleNamespace(code="PERSONA_PAYOFF_DENSITY_LOW", detail="avg payoff_density=0.10 < 0.22"),
    ]


def _persona_codes():
    return [
        "PERSONA_ABANDON_RATE_HIGH",
        "PERSONA_WEIGHTED_SCORE_LOW",
        "PERSONA_PAYOFF_DENSITY_LOW",
    ]


def _report(findings, codes, chapter_position=3):
    return RetentionGateReport(
        chapter_position=chapter_position,
        findings=tuple(findings),
        auto_repair_codes=tuple(codes),
    )


# ── 核心：低代理分独立出现时不能阻断 ──────────────────────────────────────


def test_low_persona_score_alone_cannot_block():
    findings, codes, authority, corroborated, _hits = _merge_persona_gate_findings(
        base_findings=[],
        base_repair_codes=[],
        persona_findings=_persona_findings(),
        persona_repair_codes=_persona_codes(),
        require_corroboration=True,
    )
    report = _report(findings, codes)
    assert codes == [], "合成 persona 不得独立追加任何修复码"
    assert report.passed is True, "低代理分不得独自把章节判为不合格"
    assert report.has_critical is False, "独立出现时不得以 critical 并入"
    assert authority == "advisory"
    assert corroborated == ()
    # 留痕仍在：三条 finding 全部记录，但降为 advisory
    assert [f.code for f in findings] == _persona_codes()
    assert {f.severity for f in findings} == {"advisory"}


def test_concerns_are_still_traceable_when_advisory():
    """降级不等于丢弃：来源与原文（concern/detail）仍可追踪。"""

    findings, _codes, authority, _corroborated, _hits = _merge_persona_gate_findings(
        base_findings=[],
        base_repair_codes=[],
        persona_findings=_persona_findings(),
        persona_repair_codes=_persona_codes(),
        require_corroboration=True,
    )
    assert authority == "advisory"
    details = " ".join(f.detail for f in findings)
    assert "abandon_rate=0.64" in details and "weighted_score=0.37" in details


# ── 互证：独立证据已在阻断时，persona 参与阻断 ────────────────────────────


def test_unrelated_real_failure_does_not_escalate_persona():
    """存在**无关**真实失败时，persona 仍不得升级（2026-09-11 W5 审查 P1）。

    旧判据是 ``bool(base_repair_codes)``：只要有 HOOK_ECHO_MISSING，三条 persona
    失败就全部升为 critical 并追加全部 persona 修复码。缺少开头钩子回响**不能**
    自行证明爽点密度不足，更无权替整套合成偏好背书。
    """

    findings, codes, authority, corroborated, _hits = _merge_persona_gate_findings(
        base_findings=[
            SimpleNamespace(
                code="HOOK_ECHO_MISSING", severity="critical", detail="opening echoes"
            )
        ],
        base_repair_codes=["HOOK_ECHO_MISSING"],
        persona_findings=_persona_findings(),
        persona_repair_codes=_persona_codes(),
        require_corroboration=True,
    )
    report = _report(findings, codes)
    # 真实失败本身必须原样保留
    assert "HOOK_ECHO_MISSING" in codes, "独立失败不得丢失"
    assert report.passed is False, "真实失败照旧阻断"
    # 但 persona 不得借它扩张
    assert not [c for c in codes if c.startswith("PERSONA_")], (
        "无关真实失败不得追加任何 persona 修复义务"
    )
    assert authority == "advisory"
    assert corroborated == ()
    persona_severities = {f.severity for f in findings if f.code.startswith("PERSONA_")}
    assert persona_severities == {"advisory"}, "未互证项仍是 advisory 留痕"


def test_declared_link_escalates_only_that_item():
    """登记了证据关系的**那一项**才升级，其余项仍是 advisory。

    关系表当前为空是有意为之（三条聚合代理量都没有通过验证的同证据关系）。此处
    用 monkeypatch 注入一条关系，验证逐项机制本身正确，而不是验证虚构的关系。
    """

    import unittest.mock as mock

    from bestseller.services import persona_quality_gate

    # 关系表是只读 mappingproxy（防就地篡改），因此替换模块属性而非打补丁就地改。
    with mock.patch.object(
        persona_quality_gate,
        "PERSONA_CORROBORATION_LINKS",
        {"PERSONA_PAYOFF_DENSITY_LOW": frozenset({"SPECIFIC_PAYOFF_FAILURE"})},
    ):
        findings, codes, authority, corroborated, hits = _merge_persona_gate_findings(
            base_findings=[],
            base_repair_codes=["SPECIFIC_PAYOFF_FAILURE"],
            persona_findings=_persona_findings(),
            persona_repair_codes=_persona_codes(),
            require_corroboration=True,
        )
    assert authority == "corroborated"
    assert corroborated == ("PERSONA_PAYOFF_DENSITY_LOW",), "只有登记的那一项升级"
    assert hits == ("SPECIFIC_PAYOFF_FAILURE",)
    # 只有互证项自己的修复码被追加
    assert "PERSONA_PAYOFF_DENSITY_LOW" in codes
    assert "PERSONA_ABANDON_RATE_HIGH" not in codes
    assert "PERSONA_WEIGHTED_SCORE_LOW" not in codes
    severities = {f.code: f.severity for f in findings if f.code.startswith("PERSONA_")}
    assert severities["PERSONA_PAYOFF_DENSITY_LOW"] == "critical"
    assert severities["PERSONA_ABANDON_RATE_HIGH"] == "advisory"
    assert severities["PERSONA_WEIGHTED_SCORE_LOW"] == "advisory"


def test_no_link_registered_by_default():
    """空关系表是有意策略，必须被锁住，防止有人偷偷把共现塞回来。"""

    assert dict(PERSONA_CORROBORATION_LINKS) == {}, (
        "新增 persona→独立证据关系必须在 persona_quality_gate 写明可定位证据，"
        "不得退回「只要有失败就算互证」"
    )


def test_corroboration_disabled_is_labelled_override_not_corroborated():
    """require_corroboration=False → 旧行为，但 authority 不得冒充 corroborated。"""

    findings, codes, authority, corroborated, _hits = _merge_persona_gate_findings(
        base_findings=[],
        base_repair_codes=[],
        persona_findings=_persona_findings(),
        persona_repair_codes=_persona_codes(),
        require_corroboration=False,
    )
    report = _report(findings, codes)
    assert authority == "override_unrestricted", "回滚开关不是互证"
    assert report.passed is False
    assert set(_persona_codes()).issubset(set(codes))


def test_independent_evidence_is_never_dropped():
    """不得因此关闭真正的事实/状态检查：独立修复码永远保留。"""

    for require in (True, False):
        _findings, codes, _authority, _corroborated, _hits = _merge_persona_gate_findings(
            base_findings=[],
            base_repair_codes=["CHAPTER_TOO_SHORT", "TIMELINE_CONFLICT"],
            persona_findings=_persona_findings(),
            persona_repair_codes=_persona_codes(),
            require_corroboration=require,
        )
        assert "CHAPTER_TOO_SHORT" in codes
        assert "TIMELINE_CONFLICT" in codes


def test_merge_is_pure_and_does_not_mutate_inputs():
    base_codes = ["HOOK_ECHO_MISSING"]
    persona_codes = _persona_codes()
    base_codes_copy = list(base_codes)
    persona_codes_copy = list(persona_codes)
    _merge_persona_gate_findings(
        base_findings=[],
        base_repair_codes=base_codes,
        persona_findings=_persona_findings(),
        persona_repair_codes=persona_codes,
        require_corroboration=False,
    )
    assert base_codes == base_codes_copy
    assert persona_codes == persona_codes_copy


# ── 配置：默认开启互证要求，且生效配置确实为 true ────────────────────────


def test_reader_quality_config_defaults_to_requiring_corroboration():
    from bestseller.services.quality_gates_config import (
        ReaderQualityGateConfig,
        _build_reader_quality_gate,
    )

    assert ReaderQualityGateConfig().persona_block_requires_corroboration is True
    assert _build_reader_quality_gate({}).persona_block_requires_corroboration is True
    assert (
        _build_reader_quality_gate(
            {"persona_block_requires_corroboration": False}
        ).persona_block_requires_corroboration
        is False
    )


def test_effective_config_requires_corroboration():
    from bestseller.services import quality_gates_config

    quality_gates_config.reset_quality_gates_cache()
    cfg = quality_gates_config.get_quality_gates_config()
    assert cfg.reader_quality.persona_block_requires_corroboration is True
    # 不关闭 persona gate 本身（它仍运行并留痕），也不关任何事实检查
    assert cfg.reader_quality.block_on_persona_failure is True


def test_pipelines_uses_the_helper_and_records_authority():
    """接线事实：授权判定必须走 helper，且回执记录 authority。"""

    import inspect

    from bestseller.services import pipelines

    src = inspect.getsource(pipelines._evaluate_retention_safety_after_assembly)
    assert "_merge_persona_gate_findings(" in src
    assert '"authority"' in src
    assert '"would_have_blocked"' in src
    assert "synthetic_persona_alone_cannot_block" in src
