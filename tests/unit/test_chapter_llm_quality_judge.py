from __future__ import annotations

import json

import pytest

from bestseller.domain.llm_quality_judge import quality_judge_result_from_mapping
from bestseller.services import chapter_llm_quality_judge, chapter_window_quality_judge
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.prompt_packs import get_prompt_pack
from bestseller.settings import load_settings


class FakeSession:
    pass


@pytest.mark.asyncio
async def test_chapter_llm_quality_judge_applies_golden_three_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_complete_text(session, settings, request):
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.85,
                    "dimension_scores": {
                        "opening_pull": 0.90,
                        "commercial_pull": 0.90,
                        "readability": 0.90,
                    },
                    "blocking_issues": [],
                    # Actionable rewrite plan lets the framework override
                    # the LLM's pass verdict when numeric thresholds fail.
                    # Without an actionable plan we don't fabricate
                    # blockers — see the silent-judge regression on
                    # 青囊不语问阴阳 ch1, 2026-05-25.
                    "rewrite_plan": {
                        "scope": "chapter",
                        "change": ["补强黄金三章卖点钩子"],
                        "instructions": "在前 1000 字内加入卖点关键词。",
                    },
                },
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
        )

    monkeypatch.setattr(chapter_llm_quality_judge, "complete_text", fake_complete_text)

    result = await chapter_llm_quality_judge.judge_chapter_commercial_quality(
        FakeSession(),
        load_settings(env={}),
        chapter_number=1,
        content_md="电梯里的血往上流。",
        generation_input={"quality_targets": {"golden_three": True}},
    )

    assert result.passed is False
    assert result.overall_score == 0.85


@pytest.mark.asyncio
async def test_chapter_llm_quality_judge_passes_front_ten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_complete_text(session, settings, request):
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.83,
                    "dimension_scores": {
                        "hook_strength": 0.82,
                        "continuity": 0.83,
                    },
                    "blocking_issues": [],
                    "rewrite_plan": {"scope": "chapter"},
                },
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
        )

    monkeypatch.setattr(chapter_llm_quality_judge, "complete_text", fake_complete_text)

    result = await chapter_llm_quality_judge.judge_chapter_commercial_quality(
        FakeSession(),
        load_settings(env={}),
        chapter_number=8,
        content_md="林渊把账页压进罗盘。",
    )

    assert result.passed is True


def _scored_completion(overall: float) -> LLMCompletionResult:
    return LLMCompletionResult(
        content=json.dumps(
            {
                "pass": True,
                "overall_score": overall,
                "dimension_scores": {"opening_pull": overall, "readability": overall},
                "blocking_issues": [],
                "rewrite_plan": {"scope": "chapter"},
            },
            ensure_ascii=False,
        ),
        provider="mock",
        model_name="mock-critic",
    )


@pytest.mark.asyncio
async def test_stable_judge_runs_samples_concurrently_with_own_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stable judge must run its N samples CONCURRENTLY, each with its own
    pooled session (regression: 3 sequential calls blew past the reviewer's
    timeout → blind accept-on-stall with no real verdict). When the DB pool is
    available the samples fan out via get_server_session + asyncio.gather.
    """

    scores = [0.70, 0.80, 0.90]
    calls = {"n": 0, "sessions": []}

    async def fake_complete_text(session, settings, request):
        idx = calls["n"]
        calls["n"] += 1
        calls["sessions"].append(id(session))
        return _scored_completion(scores[idx % len(scores)])

    import contextlib as _ctx

    sessions_handed_out: list[object] = []

    @_ctx.asynccontextmanager
    async def fake_get_server_session():
        s = FakeSession()
        sessions_handed_out.append(s)
        yield s

    monkeypatch.setattr(chapter_llm_quality_judge, "complete_text", fake_complete_text)
    import bestseller.infra.db.session as _sess_mod

    monkeypatch.setattr(_sess_mod, "get_server_session", fake_get_server_session)

    result = await chapter_llm_quality_judge.judge_chapter_commercial_quality_stable(
        FakeSession(),
        load_settings(env={}),
        chapter_number=5,
        content_md="天道面板弹出一行乱码。",
        samples=3,
    )

    # All 3 samples ran, each on a DISTINCT own session (concurrency-safe).
    assert calls["n"] == 3
    assert len(set(calls["sessions"])) == 3
    assert len(sessions_handed_out) == 3
    # Median of [0.70, 0.80, 0.90] == 0.80.
    assert result.overall_score == pytest.approx(0.80)


@pytest.mark.asyncio
async def test_stable_judge_falls_back_to_sequential_when_pool_uninitialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the DB pool is not initialized (unit/test context) the stable judge
    must fall back to sequential sampling on the shared session, not crash."""

    scores = [0.60, 0.90, 0.90]
    calls = {"n": 0}

    async def fake_complete_text(session, settings, request):
        idx = calls["n"]
        calls["n"] += 1
        return _scored_completion(scores[idx % len(scores)])

    async def boom():
        raise RuntimeError("Database not initialized. Call init_db() first.")

    import contextlib as _ctx

    @_ctx.asynccontextmanager
    async def fake_get_server_session():
        await boom()
        yield  # unreachable

    monkeypatch.setattr(chapter_llm_quality_judge, "complete_text", fake_complete_text)
    import bestseller.infra.db.session as _sess_mod

    monkeypatch.setattr(_sess_mod, "get_server_session", fake_get_server_session)

    result = await chapter_llm_quality_judge.judge_chapter_commercial_quality_stable(
        FakeSession(),
        load_settings(env={}),
        chapter_number=5,
        content_md="天道面板弹出一行乱码。",
        samples=3,
    )

    assert calls["n"] == 3
    # Median of [0.60, 0.90, 0.90] == 0.90.
    assert result.overall_score == pytest.approx(0.90)


def test_llm_quality_judge_coerces_loose_issue_and_rewrite_plan_shapes() -> None:
    result = quality_judge_result_from_mapping(
        {
            "pass": False,
            "overall_score": 0.82,
            "dimension_scores": {"hook_strength": 0.85},
            "blocking_issues": [
                {
                    "severity": "critical",
                    "dimension": "ending frame",
                    "detail": "章末停在未完成动作。",
                    "recommendation": "把最后一句改成现场动作帧。",
                },
                "对白后缺少落地画面。",
            ],
            "rewrite_plan": {
                "priority": "ending_resolution",
                "actions": ["让电梯门合上", "让铜钱发烫作为最后帧"],
                "章节结尾锚点": "电梯门合拢，铜钱在掌心发烫。",
            },
        },
        scope="chapter",
        min_overall=0.86,
        min_dimensions={},
    )

    assert result.passed is False
    assert result.blocking_issues[0].code == "ENDING_FRAME"
    assert result.blocking_issues[0].evidence == "章末停在未完成动作。"
    assert result.blocking_issues[1].code == "LLM_QUALITY_ISSUE_2"
    assert result.rewrite_plan.change == ("让电梯门合上", "让铜钱发烫作为最后帧")
    assert result.rewrite_plan.instructions == "电梯门合拢，铜钱在掌心发烫。"


def test_llm_quality_judge_serializes_structured_issue_fields() -> None:
    result = quality_judge_result_from_mapping(
        {
            "pass": False,
            "overall_score": 0.72,
            "dimension_scores": {"continuity": 0.70},
            "blocking_issues": [
                {
                    "code": "WINDOW_PATTERN_REPEAT",
                    "severity": "high",
                    "evidence": {
                        "pattern": [13, 14, 9, 13],
                        "line_index": 5,
                    },
                    "required_fix": ["合并重复节奏", "补一个新证据推进"],
                    "path": {"window": "chapters[-4:]"},
                }
            ],
            "rewrite_plan": {"scope": "window", "change": ["修复窗口重复"]},
        },
        scope="window",
        min_overall=0.79,
        min_dimensions={},
    )

    issue = result.blocking_issues[0]
    assert issue.code == "WINDOW_PATTERN_REPEAT"
    assert '"pattern": [13, 14, 9, 13]' in issue.evidence
    assert issue.required_fix == '["合并重复节奏", "补一个新证据推进"]'
    assert issue.path == '{"window": "chapters[-4:]"}'


def test_llm_quality_judge_does_not_fabricate_tiny_dimension_gap_blocker() -> None:
    result = quality_judge_result_from_mapping(
        {
            "pass": True,
            "overall_score": 0.89,
            "dimension_scores": {"knowledge_boundary": 0.85},
            "blocking_issues": [],
            "audit_issues": [
                {
                    "code": "KNOWLEDGE_BOUNDARY_MINOR",
                    "severity": "low",
                    "evidence": "个别句子可再收紧。",
                    "required_fix": "审稿时微调。",
                }
            ],
            "rewrite_plan": {
                "scope": "chapter",
                "change": ["可选微调认知边界"],
                "instructions": "非阻塞审稿建议。",
            },
        },
        scope="chapter",
        min_overall=0.86,
        min_dimensions={"knowledge_boundary": 0.86},
    )

    assert result.passed is True
    assert result.blocking_issues == ()


def test_chapter_window_judge_downgrades_metadata_only_blockers() -> None:
    result = quality_judge_result_from_mapping(
        {
            "pass": False,
            "overall_score": 0.38,
            "blocking_issues": [
                {
                    "code": "FRONT10_FORBIDDEN_SIGNAL",
                    "severity": "critical",
                    "evidence": (
                        "auto_repair_last_block_codes contains "
                        "FRONT10_FORBIDDEN_SIGNAL: copper_coin发烫"
                    ),
                    "required_fix": "清除历史修复标记。",
                }
            ],
            "rewrite_plan": {"scope": "window", "change": ["清除禁用信号"]},
        },
        scope="window",
        min_overall=0.79,
        min_dimensions={},
    )

    filtered = chapter_window_quality_judge._downgrade_unsupported_window_blockers(
        result,
        chapters=[
            {
                "chapter_number": 2,
                "content_excerpt": "林渊把铜钱按在门缝下。铜钱在掌心震了一下。",
            }
        ],
    )

    assert filtered.passed is True
    assert filtered.blocking_issues == ()
    assert filtered.audit_issues[-1].code == "FRONT10_FORBIDDEN_SIGNAL"
    assert filtered.audit_issues[-1].severity == "low"


def test_chapter_window_judge_keeps_forbidden_signal_with_content_evidence() -> None:
    result = quality_judge_result_from_mapping(
        {
            "pass": False,
            "overall_score": 0.70,
            "blocking_issues": [
                {
                    "code": "FRONT10_FORBIDDEN_SIGNAL",
                    "severity": "critical",
                    "evidence": "正文写到铜钱发烫。",
                    "required_fix": "改成震动或缺角渗黑水。",
                }
            ],
            "rewrite_plan": {"scope": "window", "change": ["清除禁用信号"]},
        },
        scope="window",
        min_overall=0.79,
        min_dimensions={},
    )

    filtered = chapter_window_quality_judge._downgrade_unsupported_window_blockers(
        result,
        chapters=[
            {
                "chapter_number": 2,
                "content_excerpt": "林渊刚碰到门缝，铜钱发烫。",
            }
        ],
    )

    assert filtered.passed is False
    assert filtered.blocking_issues[0].code == "FRONT10_FORBIDDEN_SIGNAL"


@pytest.mark.asyncio
async def test_chapter_judge_prompt_includes_methodology_for_golden_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_complete_text(session, settings, request):
        captured["user"] = request.user_prompt
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.92,
                    "dimension_scores": {
                        "opening_pull": 0.92,
                        "commercial_pull": 0.92,
                        "readability": 0.92,
                    },
                    "blocking_issues": [],
                    "audit_issues": [],
                    "rewrite_plan": {"scope": "chapter"},
                },
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
        )

    monkeypatch.setattr(chapter_llm_quality_judge, "complete_text", fake_complete_text)

    await chapter_llm_quality_judge.judge_chapter_commercial_quality(
        FakeSession(),
        load_settings(env={}),
        chapter_number=1,
        content_md="林渊走进楼道。",
        pack=get_prompt_pack("suspense-mystery"),
    )

    assert "评估时必须参照的方法论标准" in captured["user"]
    assert "【opening_rules】" in captured["user"]
    # 弹簧法(压抑-打脸)是爽文向方法，悬疑判官不再被兜底注入，防题材固化。
    assert "【spring_model】" not in captured["user"]

    await chapter_llm_quality_judge.judge_chapter_commercial_quality(
        FakeSession(),
        load_settings(env={}),
        chapter_number=1,
        content_md="林渊走进楼道。",
        pack=get_prompt_pack("xianxia-upgrade-core"),
    )
    assert "【spring_model】" in captured["user"]


@pytest.mark.asyncio
async def test_chapter_judge_prompt_excludes_opening_rules_for_chapter_11(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_complete_text(session, settings, request):
        captured["user"] = request.user_prompt
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.86,
                    "dimension_scores": {},
                    "blocking_issues": [],
                    "audit_issues": [],
                    "rewrite_plan": {"scope": "chapter"},
                },
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
        )

    monkeypatch.setattr(chapter_llm_quality_judge, "complete_text", fake_complete_text)

    await chapter_llm_quality_judge.judge_chapter_commercial_quality(
        FakeSession(),
        load_settings(env={}),
        chapter_number=11,
        content_md="林渊把账页合上。",
        pack=get_prompt_pack("suspense-mystery"),
    )

    assert "【opening_rules】" not in captured["user"]
    assert "【spring_model】" not in captured["user"]
