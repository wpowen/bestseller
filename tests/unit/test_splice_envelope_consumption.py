"""T5 验收: splice gate 真实走 Phase A envelope, override 治理字段真被读到.

本测试不依赖 regex 触发特定 high code（regex 触发条件可能因文本格式而变），
直接验证三个核心合约：
  1. critical code → can_override=False, allowed_rationales=()
  2. high code → can_override=True, allowed_rationales 包含 LOGIC_INTEGRITY 等
  3. methodology_runtime._has_blocking_issue 真的读 can_override
"""


def test_splice_critical_issue_cannot_be_overridden():
    """CHAPTER_SPLICE_REPEATED_SENTENCE 是 critical → can_override=False。"""
    from bestseller.domain.gate_verdict import GateFinding
    from bestseller.services.chapter_splice_coherence_gate import (
        _finding_to_issue,
    )

    finding = GateFinding(
        code="CHAPTER_SPLICE_REPEATED_SENTENCE",
        severity="critical",
        message="test",
        path="line:1",
    )
    issue = _finding_to_issue(finding)
    assert issue.severity == "critical"
    assert issue.can_override is False
    assert tuple(issue.allowed_rationales) == ()


def test_splice_high_issue_can_be_overridden_with_rationales():
    """CHAPTER_SPLICE_LOCATION_DRIFT 是 high → can_override=True 带 rationales。"""
    from bestseller.domain.gate_verdict import GateFinding
    from bestseller.services.chapter_splice_coherence_gate import (
        _finding_to_issue,
    )

    for high_code, expected_rationales in [
        ("CHAPTER_SPLICE_LOCATION_DRIFT", ("LOGIC_INTEGRITY", "WORLD_RULE_CONSTRAINT")),
        (
            "CHAPTER_SPLICE_UNSEEDED_LOCATION_REFERENCE",
            ("WORLD_RULE_CONSTRAINT", "LOGIC_INTEGRITY"),
        ),
        ("CHAPTER_SPLICE_TIME_JUMP", ("ARC_TIMING", "EDITORIAL_INTENT")),
    ]:
        finding = GateFinding(
            code=high_code, severity="high", message="test", path="line:1",
        )
        issue = _finding_to_issue(finding)
        assert issue.severity == "high"
        assert issue.can_override is True, f"{high_code} should be overridable"
        assert tuple(issue.allowed_rationales) == expected_rationales, (
            f"{high_code} expected {expected_rationales}, "
            f"got {tuple(issue.allowed_rationales)}"
        )


def test_splice_unknown_high_code_falls_back_to_default_rationales():
    """未列出的 high code 应走默认 rationales ('EDITORIAL_INTENT', 'ARC_TIMING')。"""
    from bestseller.domain.gate_verdict import GateFinding
    from bestseller.services.chapter_splice_coherence_gate import (
        _finding_to_issue,
    )

    finding = GateFinding(code="CHAPTER_SPLICE_NEW_CODE", severity="high", message="x")
    issue = _finding_to_issue(finding)
    assert issue.can_override is True
    assert tuple(issue.allowed_rationales) == ("EDITORIAL_INTENT", "ARC_TIMING")


def test_splice_presence_contradiction_is_critical_no_override():
    """CHAPTER_SPLICE_PRESENCE_CONTRADICTION 是 critical → 不可 override。"""
    from bestseller.domain.gate_verdict import GateFinding
    from bestseller.services.chapter_splice_coherence_gate import (
        _finding_to_issue,
    )

    finding = GateFinding(
        code="CHAPTER_SPLICE_PRESENCE_CONTRADICTION", severity="critical", message="x"
    )
    issue = _finding_to_issue(finding)
    assert issue.severity == "critical"
    assert issue.can_override is False
    assert tuple(issue.allowed_rationales) == ()


def test_splice_as_checker_report_consumer_actually_reads_can_override():
    """as_checker_report 产出的 report 必须让 methodology_runtime 的
    _has_blocking_issue 真实读到 can_override 字段并据此判断。"""
    from bestseller.domain.gate_verdict import GateFinding, GateVerdict
    from bestseller.services.chapter_splice_coherence_gate import (
        as_checker_report,
    )
    from bestseller.services.methodology_runtime import (
        _has_blocking_issue,
        _review_severity,
    )

    # 构造 1 critical (不可 override) + 1 high (可 override)
    critical = GateFinding(
        code="CHAPTER_SPLICE_REPEATED_SENTENCE",
        severity="critical", message="dup", path="line:1",
    )
    high = GateFinding(
        code="CHAPTER_SPLICE_LOCATION_DRIFT",
        severity="high", message="drift", path="location:market",
    )
    verdict = GateVerdict(
        gate_name="chapter_splice_coherence",
        verdict="blocked",
        coverage=0.0,
        findings=(critical, high),
        metrics={"finding_count": 2},
        summary="2 findings",
    )
    report = as_checker_report(verdict, chapter_number=5)

    # 1. report.hard_violations 真包含 critical（不可 override）
    hard_ids = {i.id for i in report.hard_violations}
    assert "CHAPTER_SPLICE_REPEATED_SENTENCE" in hard_ids

    # 2. report.soft_suggestions 真包含 high（可 override）
    soft_ids = {i.id for i in report.soft_suggestions}
    assert "CHAPTER_SPLICE_LOCATION_DRIFT" in soft_ids

    # 3. methodology_runtime._has_blocking_issue 真读到 can_override
    #    critical 不可 override → _has_blocking_issue 应为 True
    assert _has_blocking_issue((report,)) is True

    # 4. _review_severity 真区分 critical (可 override=False) vs high (可 override=True)
    crit_issue = next(
        i for i in report.issues if i.id == "CHAPTER_SPLICE_REPEATED_SENTENCE"
    )
    high_issue = next(
        i for i in report.issues if i.id == "CHAPTER_SPLICE_LOCATION_DRIFT"
    )
    assert _review_severity(crit_issue) == "critical"
    assert _review_severity(high_issue) == "major"


def test_splice_shared_adapters_dedupe_and_preserve_governance_fields():
    """splice 的三个下游出口必须复用同一套去重和 override 口径。"""
    from bestseller.domain.gate_verdict import GateFinding, GateVerdict
    from bestseller.services.chapter_splice_coherence_gate import (
        as_checker_report,
        as_gate_summary,
        as_quality_findings,
        as_repair_patch_points,
        blocking_splice_findings,
    )

    duplicate_high = GateFinding(
        code="CHAPTER_SPLICE_LOCATION_DRIFT",
        severity="high",
        message="同章地点锚点不一致",
        path="location:旧货市场",
        repair_action="统一地点命名或补明确转场。",
    )
    verdict = GateVerdict(
        gate_name="chapter_splice_coherence",
        verdict="blocked",
        coverage=0.0,
        findings=(duplicate_high, duplicate_high),
        metrics={"chapter_number": 9, "finding_count": 2},
        summary="duplicate high finding",
    )

    report = as_checker_report(verdict, chapter_number=9)
    quality_findings = as_quality_findings(verdict, chapter_number=9)
    blocking_findings = blocking_splice_findings(verdict)
    patch_points = as_repair_patch_points(blocking_findings)
    summary = as_gate_summary(verdict, chapter_number=9)

    assert len(report.issues) == 1
    assert report.issues[0].can_override is True
    assert tuple(report.issues[0].allowed_rationales) == (
        "LOGIC_INTEGRITY",
        "WORLD_RULE_CONSTRAINT",
    )
    assert len(quality_findings) == 1
    assert quality_findings[0].evidence["can_override"] is True
    assert quality_findings[0].evidence["allowed_rationales"] == [
        "LOGIC_INTEGRITY",
        "WORLD_RULE_CONSTRAINT",
    ]
    assert len(patch_points) == 1
    assert patch_points[0]["cause_id"] == "CHAPTER_SPLICE_LOCATION_DRIFT"
    assert len(summary["blocking_findings"]) == 1
