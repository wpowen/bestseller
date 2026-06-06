from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services import gate_adjudicator as ga

pytestmark = pytest.mark.unit


def _finding(category: str, severity: str, message: str) -> SimpleNamespace:
    return SimpleNamespace(category=category, severity=severity, message=message)


def test_is_adjudicable_only_for_context_categories_and_blocking_severity() -> None:
    assert ga.is_adjudicable(_finding("common_sense", "high", "x")) is True
    assert ga.is_adjudicable(_finding("common_sense", "medium", "x")) is True
    # low severity never blocks → not worth adjudicating
    assert ga.is_adjudicable(_finding("common_sense", "low", "x")) is False
    # structural categories stay deterministic
    assert ga.is_adjudicable(_finding("output_hygiene", "high", "x")) is False
    assert ga.is_adjudicable(_finding("duplication", "high", "x")) is False


def test_partition_findings_preserves_order() -> None:
    findings = [
        _finding("output_hygiene", "high", "a"),
        _finding("common_sense", "high", "b"),
        _finding("duplication", "high", "c"),
        _finding("common_sense", "medium", "d"),
    ]
    adjudicable, other = ga.partition_findings(findings)
    assert [f.message for f in adjudicable] == ["b", "d"]
    assert [f.message for f in other] == ["a", "c"]


def test_parse_response_dismisses_only_explicit_dismiss() -> None:
    response = "1: CONFIRM - real defect\n2: DISMISS - crash explains it\n3: DISMISS"
    confirmed = ga.parse_adjudication_response(response, 3)
    assert confirmed == [True, False, False]


def test_parse_response_fail_closed_on_missing_or_garbage() -> None:
    # Only finding 2 has a parseable verdict; 1 and 3 default to CONFIRM (kept).
    response = "blah blah\n2: DISMISS - ok\nnonsense"
    confirmed = ga.parse_adjudication_response(response, 3)
    assert confirmed == [True, False, True]


def test_parse_response_tolerates_chinese_punctuation_and_markdown() -> None:
    response = "1：**DISMISS** - 车祸已交代\n2、CONFIRM"
    confirmed = ga.parse_adjudication_response(response, 2)
    assert confirmed == [False, True]


def test_build_prompts_lists_findings_and_includes_prose() -> None:
    findings = [_finding("common_sense", "high", "unexplained_body_state: 出血")]
    system, user = ga.build_adjudication_prompts(
        findings, text="车祸现场，他在流血。", genre="都市异能", sub_genre="身份反转", language="zh-CN"
    )
    assert "CONFIRM" in system and "DISMISS" in system
    assert "unexplained_body_state" in user
    assert "车祸现场" in user  # prose is included as context


@pytest.mark.asyncio
async def test_adjudicate_dismisses_false_positive(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_complete_text(session, settings, request):  # noqa: ANN001
        captured["user"] = request.user_prompt
        return SimpleNamespace(
            content="1: DISMISS - 车祸已经交代了流血原因",
            model_name="fake-critic",
            llm_run_id=None,
        )

    monkeypatch.setattr(ga, "complete_text", fake_complete_text)

    project = SimpleNamespace(id=None, slug="t", genre="都市异能", sub_genre="身份反转", language="zh-CN")
    settings = SimpleNamespace(pipeline=SimpleNamespace(gate_llm_adjudication_enabled=True))
    findings = [_finding("common_sense", "high", "unexplained_body_state: 出血")]

    result = await ga.adjudicate_findings(
        None, settings, project, chapter_number=1, text="车祸现场，他在流血。", findings=findings
    )
    assert result.changed is True
    assert len(result.dismissed) == 1
    assert not result.confirmed
    assert "车祸现场" in str(captured["user"])


@pytest.mark.asyncio
async def test_adjudicate_keeps_confirmed_finding(monkeypatch) -> None:
    async def fake_complete_text(session, settings, request):  # noqa: ANN001
        return SimpleNamespace(content="1: CONFIRM - 确实无来由", model_name="fake", llm_run_id=None)

    monkeypatch.setattr(ga, "complete_text", fake_complete_text)
    project = SimpleNamespace(id=None, slug="t", genre="灵异", sub_genre="", language="zh-CN")
    settings = SimpleNamespace(pipeline=SimpleNamespace(gate_llm_adjudication_enabled=True))
    findings = [_finding("common_sense", "high", "unexplained_body_state: 无来由的鼻血")]

    result = await ga.adjudicate_findings(
        None, settings, project, chapter_number=1, text="他突然流鼻血。", findings=findings
    )
    assert result.changed is False
    assert len(result.confirmed) == 1


@pytest.mark.asyncio
async def test_adjudicate_fail_closed_on_llm_error(monkeypatch) -> None:
    async def boom(session, settings, request):  # noqa: ANN001
        raise RuntimeError("llm down")

    monkeypatch.setattr(ga, "complete_text", boom)
    project = SimpleNamespace(id=None, slug="t", genre="都市异能", sub_genre="", language="zh-CN")
    settings = SimpleNamespace(pipeline=SimpleNamespace(gate_llm_adjudication_enabled=True))
    findings = [_finding("common_sense", "high", "x")]

    result = await ga.adjudicate_findings(
        None, settings, project, chapter_number=1, text="...", findings=findings
    )
    # On error nothing is dismissed — the finding is kept (fail-closed).
    assert result.changed is False
    assert len(result.confirmed) == 1


@pytest.mark.asyncio
async def test_adjudicate_noop_when_disabled(monkeypatch) -> None:
    called = False

    async def fake_complete_text(session, settings, request):  # noqa: ANN001
        nonlocal called
        called = True
        return SimpleNamespace(content="1: DISMISS", model_name="x", llm_run_id=None)

    monkeypatch.setattr(ga, "complete_text", fake_complete_text)
    project = SimpleNamespace(id=None, slug="t", genre="都市异能", sub_genre="", language="zh-CN")
    settings = SimpleNamespace(pipeline=SimpleNamespace(gate_llm_adjudication_enabled=False))
    findings = [_finding("common_sense", "high", "x")]

    result = await ga.adjudicate_findings(
        None, settings, project, chapter_number=1, text="...", findings=findings
    )
    assert result.changed is False
    assert called is False  # disabled → no LLM call


@pytest.mark.asyncio
async def test_adjudicate_noop_when_no_adjudicable_findings(monkeypatch) -> None:
    called = False

    async def fake_complete_text(session, settings, request):  # noqa: ANN001
        nonlocal called
        called = True
        return SimpleNamespace(content="", model_name="x", llm_run_id=None)

    monkeypatch.setattr(ga, "complete_text", fake_complete_text)
    project = SimpleNamespace(id=None, slug="t", genre="都市异能", sub_genre="", language="zh-CN")
    settings = SimpleNamespace(pipeline=SimpleNamespace(gate_llm_adjudication_enabled=True))
    findings = [_finding("duplication", "high", "x"), _finding("output_hygiene", "medium", "y")]

    result = await ga.adjudicate_findings(
        None, settings, project, chapter_number=1, text="...", findings=findings
    )
    assert result.changed is False
    assert called is False  # no adjudicable findings → fast path, no LLM call
