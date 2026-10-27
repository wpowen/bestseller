# ruff: noqa: E501, RUF001
from types import SimpleNamespace

import pytest

from bestseller.services import fanqie_long_semantic_review as review


def _finding(code="first_100_pressure_missing", target="opening.first_100", severity="critical"):
    return {
        "code": code,
        "severity": severity,
        "message": "关键词命中",
        "target": target,
        "evidence": "自动证据",
        "repair_hint": "检查正文上下文",
    }


def _completion(content, *, finish_reason="stop", fallback_used=False):
    return SimpleNamespace(
        content=content,
        llm_run_id="run-real-1",
        model_name="critic-model",
        provider="minimax",
        finish_reason=finish_reason,
        fallback_used=fallback_used,
    )


@pytest.mark.asyncio
async def test_not_supported_requires_scope_quote_and_dismisses_only_keyword_finding(monkeypatch):
    calls = []

    async def fake_complete(session, settings, request):
        calls.append(request)
        return _completion(
            '{"reviews":[{"code":"first_100_pressure_missing","target":"opening.first_100",'
            '"verdict":"not_supported","evidence":"她把截止时间写在收据背面",'
            '"reason":"缺失不成立：这是可见的日常限时行动，不是空泛背景"}]}'
        )

    monkeypatch.setattr(review, "complete_text", fake_complete)
    result = await review.review_fanqie_long_semantic_findings(
        None,
        SimpleNamespace(),
        project=SimpleNamespace(id="00000000-0000-0000-0000-000000000001", slug="book", genre="现实"),
        chapter_texts={1: "她把截止时间写在收据背面，必须在今晚交账。"},
        findings=[_finding()],
    )
    assert len(calls) == 1
    assert result.dismissed_codes == ["first_100_pressure_missing"]
    assert result.blocking_findings == []
    assert result.provider_receipt["llm_run_id"] == "run-real-1"
    assert result.provider_receipt["input_sha256"]
    assert result.provider_receipt["output_sha256"]
    assert result.provider_receipt["raw_review"]
    assert calls[0].max_tokens_override == 2500


@pytest.mark.asyncio
async def test_single_json_markdown_fence_is_accepted_without_relaxing_literal_evidence(monkeypatch):
    async def fake_complete(_session, _settings, _request):
        return _completion(
            '```json\n{"reviews":[{"code":"first_100_pressure_missing",'
            '"target":"opening.first_100","verdict":"not_supported",'
            '"evidence":"她把截止时间写在收据背面","reason":"缺失不成立：日常限时行动"}]}\n```'
        )

    monkeypatch.setattr(review, "complete_text", fake_complete)
    result = await review.review_fanqie_long_semantic_findings(
        None,
        SimpleNamespace(),
        chapter_texts={1: "她把截止时间写在收据背面，必须在今晚交账。"},
        findings=[_finding()],
    )
    assert result.reviews[0].verdict == "not_supported"

    async def escaped_complete(_session, _settings, _request):
        return _completion(
            '{"reviews":[{"code":"first_100_pressure_missing","target":"opening.first_100",'
            '"verdict":"not_supported","evidence":"她把截止时间写在收据背面\\\\n必须在今晚交账",'
            '"reason":"转写"}]}'
        )

    monkeypatch.setattr(review, "complete_text", escaped_complete)
    escaped_result = await review.review_fanqie_long_semantic_findings(
        None,
        SimpleNamespace(),
        chapter_texts={1: "她把截止时间写在收据背面，必须在今晚交账。"},
        findings=[_finding()],
    )
    assert escaped_result.reviews[0].verdict == "uncertain"


@pytest.mark.asyncio
async def test_every_critical_finding_must_be_covered_and_bad_evidence_blocks(monkeypatch):
    async def fake_complete(_session, _settings, _request):
        return _completion(
            '{"reviews":[{"code":"first_100_pressure_missing","target":"opening.first_100",'
            '"verdict":"not_supported","evidence":"不存在于正文", "reason":"误报"}]}'
        )

    monkeypatch.setattr(review, "complete_text", fake_complete)
    result = await review.review_fanqie_long_semantic_findings(
        None,
        SimpleNamespace(),
        chapter_texts={1: "她今天按时交账。"},
        findings=[
            _finding(),
            _finding("chapter_3_complete_loop_missing", "chapters.3"),
        ],
    )
    assert [item.code for item in result.reviews] == [
        "first_100_pressure_missing",
        "chapter_3_complete_loop_missing",
    ]
    assert all(item.verdict == "uncertain" for item in result.reviews)
    assert len(result.blocking_findings) == 2


@pytest.mark.asyncio
async def test_invalid_finish_or_fallback_never_dismisses_keyword_findings(monkeypatch):
    async def fake_complete(_session, _settings, _request):
        return _completion(
            '{"reviews":[{"code":"first_100_pressure_missing","target":"opening.first_100",'
            '"verdict":"not_supported","evidence":"她今天按时交账", "reason":"合理"}]}',
            finish_reason="length",
            fallback_used=True,
        )

    monkeypatch.setattr(review, "complete_text", fake_complete)
    result = await review.review_fanqie_long_semantic_findings(
        None,
        SimpleNamespace(),
        chapter_texts={1: "她今天按时交账。"},
        findings=[_finding()],
    )
    assert result.reviews[0].verdict == "uncertain"
    assert result.blocking_findings[0].code == "first_100_pressure_missing"


@pytest.mark.asyncio
async def test_unknown_target_and_nonliteral_quote_fail_closed(monkeypatch):
    async def fake_complete(_session, _settings, _request):
        return _completion(
            '{"reviews":[{"code":"first_100_pressure_missing","target":"unknown.scope",'
            '"verdict":"not_supported","evidence":"她今天按时交账", "reason":"误报"}]}'
        )

    monkeypatch.setattr(review, "complete_text", fake_complete)
    result = await review.review_fanqie_long_semantic_findings(
        None,
        SimpleNamespace(),
        chapter_texts={1: "她今天按时交账。"},
        findings=[_finding()],
    )
    assert result.reviews[0].verdict == "uncertain"


@pytest.mark.asyncio
async def test_opening_scope_uses_cjk_slice_boundary(monkeypatch):
    outside = "章末才出现的回报"
    text = "甲" * 100 + outside

    async def fake_complete(_session, _settings, _request):
        return _completion(
            '{"reviews":[{"code":"first_100_pressure_missing","target":"opening.first_100",'
            '"verdict":"not_supported","evidence":"'
            + outside
            + '","reason":"误报"}]}'
        )

    monkeypatch.setattr(review, "complete_text", fake_complete)
    result = await review.review_fanqie_long_semantic_findings(
        None,
        SimpleNamespace(),
        chapter_texts={1: text},
        findings=[_finding()],
    )
    assert result.reviews[0].verdict == "uncertain"


@pytest.mark.asyncio
async def test_mock_provider_and_duplicate_rows_cannot_dismiss(monkeypatch):
    async def fake_complete(_session, _settings, _request):
        completion = _completion(
            '{"reviews":[{"code":"first_100_pressure_missing","target":"opening.first_100",'
            '"verdict":"not_supported","evidence":"她今天按时交账", "reason":"误报"},'
            '{"code":"first_100_pressure_missing","target":"opening.first_100",'
            '"verdict":"not_supported","evidence":"她今天按时交账", "reason":"误报"}]}'
        )
        completion.provider = "mock"
        return completion

    monkeypatch.setattr(review, "complete_text", fake_complete)
    result = await review.review_fanqie_long_semantic_findings(
        None,
        SimpleNamespace(),
        chapter_texts={1: "她今天按时交账。"},
        findings=[_finding()],
    )
    assert result.reviews[0].verdict == "uncertain"


@pytest.mark.asyncio
async def test_noncritical_findings_do_not_trigger_provider_call(monkeypatch):
    async def fail_complete(*_args, **_kwargs):
        raise AssertionError("non-critical findings must not call the critic")

    monkeypatch.setattr(review, "complete_text", fail_complete)
    result = await review.review_fanqie_long_semantic_findings(
        None,
        SimpleNamespace(),
        chapter_texts={1: "日常交账。"},
        findings=[_finding(severity="warning")],
    )
    assert result.called is False
    assert result.reviews == []
