# ruff: noqa: RUF001, RUF002, RUF003 — Chinese market vocabulary is intentional.
"""建书管线挂钩的行为测试（不是源码 grep，是真跑 run_autowrite_pipeline 的构思前置段）。

为什么需要它：源码 pin 只能证明"代码写在那儿"，证明不了"flag 打开时真被调用、
且收到的题材键是对的"。而这个能力的失效全是无声的——2026-08-08 真栈跑出来的
两个 bug（挂钩在死分支 / 传了合成预设键导致整节空转）离线 9424 测试全绿都看不见。

做法：把构思、以及紧随其后的管线分流点都换成桩，让流程在挂钩执行完立刻停下，
然后断言挂钩收到的 request 和写进 metadata 的摘要。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit


class _StopAfterHookError(RuntimeError):
    """Sentinel: 让管线在挂钩之后、真正建库之前停下。"""


def _conception_result() -> Any:
    from bestseller.services.conception import ConceptionResult

    return ConceptionResult(
        writing_profile={"voice": "test"},
        premise="外门弟子捡到会收取寿命利息的剑诀。",
        title="我以阳寿换剑",
        conception_log=[],
        llm_run_ids=[],
        synopsis="他每用一次剑诀，就短命三天。",
    )


def _project_payload(metadata: dict[str, Any]) -> Any:
    from bestseller.domain.project import ProjectCreate

    return ProjectCreate(
        slug="mv-hook-probe",
        title="占位标题",
        genre="仙侠",
        sub_genre="古典仙侠",
        language="zh-CN",
        target_word_count=30000,
        target_chapters=3,
        metadata=metadata,
    )


async def _run_prepass(monkeypatch, *, flag: bool, metadata: dict[str, Any]) -> dict:
    """跑到挂钩为止，返回 {request, summary_metadata}。"""

    from bestseller.services import conception as conception_module
    from bestseller.services import pipelines
    from bestseller.services.market_validation import service as mv_service

    captured: dict[str, Any] = {}

    async def _fake_conception(*args, **kwargs):
        return _conception_result()

    async def _fake_validation(request, **kwargs):
        captured["request"] = request
        from bestseller.domain.market_validation import MarketValidationReport

        return MarketValidationReport(request=request)

    def _stop(*args, **kwargs):
        raise _StopAfterHookError

    monkeypatch.setattr(
        conception_module, "run_conception_pipeline", _fake_conception, raising=True
    )
    monkeypatch.setattr(
        mv_service, "run_market_validation", _fake_validation, raising=True
    )
    # 挂钩之后紧接着的分流点：在这里停，避免真的建库/规划。
    monkeypatch.setattr(
        pipelines, "_should_use_progressive_pipeline", _stop, raising=True
    )

    settings = SimpleNamespace(
        pipeline=SimpleNamespace(enable_market_validation=flag)
    )
    with pytest.raises(_StopAfterHookError):
        await pipelines.run_autowrite_pipeline(
            session=None,
            settings=settings,
            project_payload=_project_payload(metadata),
            premise="外门弟子捡到会收取寿命利息的剑诀。",
            use_conception=True,
        )
    return captured


@pytest.mark.asyncio
async def test_hook_fires_with_canonical_genre_key_when_enabled(monkeypatch) -> None:
    captured = await _run_prepass(
        monkeypatch,
        flag=True,
        metadata={"genre_canonical": "xianxia"},
    )

    request = captured.get("request")
    assert request is not None, "flag 打开时挂钩必须真被调用"
    assert request.genre_key == "xianxia"
    assert request.title_candidates == ("我以阳寿换剑",)
    assert request.concept
    assert request.blurb == "他每用一次剑诀，就短命三天。"


@pytest.mark.asyncio
async def test_hook_resolves_key_from_genre_intent_contract(monkeypatch) -> None:
    """网页建书只有 genre_intent_contract、没有 genre_canonical。"""

    captured = await _run_prepass(
        monkeypatch,
        flag=True,
        metadata={
            "genre_intent_contract": {
                "genre_key": "xianxia",
                "sub_genre_key": "urban-cultivation",
            }
        },
    )

    request = captured["request"]
    assert (request.genre_key, request.sub_genre_key) == (
        "xianxia",
        "urban-cultivation",
    )


@pytest.mark.asyncio
async def test_hook_never_runs_when_flag_off(monkeypatch) -> None:
    captured = await _run_prepass(monkeypatch, flag=False, metadata={})

    assert "request" not in captured


@pytest.mark.asyncio
async def test_hook_failure_does_not_break_creation(monkeypatch) -> None:
    """挂钩炸了也只能吞掉——建书流程必须继续往下走到分流点。"""

    from bestseller.services import conception as conception_module
    from bestseller.services import pipelines
    from bestseller.services.market_validation import service as mv_service

    async def _fake_conception(*args, **kwargs):
        return _conception_result()

    async def _boom(*args, **kwargs):
        raise RuntimeError("market source exploded")

    def _stop(*args, **kwargs):
        raise _StopAfterHookError

    monkeypatch.setattr(
        conception_module, "run_conception_pipeline", _fake_conception, raising=True
    )
    monkeypatch.setattr(mv_service, "run_market_validation", _boom, raising=True)
    monkeypatch.setattr(
        pipelines, "_should_use_progressive_pipeline", _stop, raising=True
    )

    settings = SimpleNamespace(
        pipeline=SimpleNamespace(enable_market_validation=True)
    )
    # 走到分流点=挂钩的异常被吞掉了；若冒出 RuntimeError 就是把建书搞崩了。
    with pytest.raises(_StopAfterHookError):
        await pipelines.run_autowrite_pipeline(
            session=None,
            settings=settings,
            project_payload=_project_payload({"genre_canonical": "xianxia"}),
            premise="外门弟子捡到会收取寿命利息的剑诀。",
            use_conception=True,
        )


@pytest.mark.asyncio
async def test_settings_without_pipeline_attribute_does_not_crash(monkeypatch) -> None:
    """settings 是桩对象（无 .pipeline）时，flag 判断本身不许抛。"""

    from bestseller.services import conception as conception_module
    from bestseller.services import pipelines

    async def _fake_conception(*args, **kwargs):
        return _conception_result()

    def _stop(*args, **kwargs):
        raise _StopAfterHookError

    monkeypatch.setattr(
        conception_module, "run_conception_pipeline", _fake_conception, raising=True
    )
    monkeypatch.setattr(
        pipelines, "_should_use_progressive_pipeline", _stop, raising=True
    )

    with pytest.raises(_StopAfterHookError):
        await pipelines.run_autowrite_pipeline(
            session=None,
            settings=SimpleNamespace(),  # 没有 .pipeline
            project_payload=_project_payload({}),
            premise="外门弟子捡到会收取寿命利息的剑诀。",
            use_conception=True,
        )
