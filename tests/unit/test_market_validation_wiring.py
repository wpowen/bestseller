# ruff: noqa: RUF002, RUF003 — Chinese market vocabulary is intentional.
"""市场验证挂钩的接线 pin（源码结构断言）。

历史教训（p1-block-zero-hit / enable_fanqie_market_profile 死 flag）：flag 定义了
≠ 有人消费；挂钩接在 CLI 路径 ≠ 网页建书路径会走到。这里把两条真实建书路径的
挂钩都 pin 住，未来重构删掉任何一处会当场红。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SRC = Path(__file__).resolve().parents[2] / "src" / "bestseller"


def _read(relative: str) -> str:
    return (_SRC / relative).read_text(encoding="utf-8")


def test_cli_pipeline_conception_prepass_carries_hook() -> None:
    source = _read("services/pipelines.py")

    assert source.count("enable_market_validation") >= 1
    assert "run_market_validation" in source
    # advisory 契约：挂钩必须包在 try/except 里 fail-open，不允许裸调用
    hook_index = source.find("run_market_validation")
    assert "except Exception" in source[hook_index : hook_index + 4000]


def test_web_creation_path_carries_hook_and_artifact_persist() -> None:
    source = _read("web/server.py")

    # 网页建书是真实主路径：flag 消费 + 报告生成 + 全量 artifact 落库缺一不可
    assert source.count("enable_market_validation") >= 1
    assert "run_market_validation" in source
    assert "persist_market_validation_report" in source
    assert "market_validation_completed" in source  # progress 可视化事件
    hook_index = source.find("run_market_validation(")
    assert "except Exception" in source[hook_index : hook_index + 6000]


def test_flag_reads_are_defensive_at_every_hook() -> None:
    """flag 判断本身不许炸。

    2026-08-08 自伤：``getattr(settings.pipeline, "enable_market_validation", False)``
    在 ``settings`` 是桩对象（无 ``.pipeline``）时抛 AttributeError，而这句在
    try 之外 ⇒ 一个 advisory 附加能力把整个建书任务搞崩。两处挂钩都必须先
    ``getattr(settings, "pipeline", None)`` 再取 flag。
    """

    for relative in ("web/server.py", "services/pipelines.py"):
        source = _read(relative)
        assert 'getattr(settings, "pipeline", None)' in source, relative
        # 裸取 settings.pipeline 后直接读 flag 的写法必须绝迹
        assert (
            'getattr(\n                settings.pipeline, "enable_market_validation"'
            not in source
        ), relative
        assert (
            'getattr(settings.pipeline, "enable_market_validation"' not in source
        ), relative


def test_all_call_sites_use_the_shared_request_builder() -> None:
    """题材键解析只许有一个实现。

    L3 真栈抓到过：网页路径自己拼 request、把合成预设键当规范键，热度整节空转。
    三个入口（网页/CLI 管线/命令行）必须都走 build_creation_request。
    """

    for relative in ("web/server.py", "services/pipelines.py", "cli/market_validation.py"):
        source = _read(relative)
        assert "build_creation_request" in source, relative
        # 不允许任何调用点再手搓 MarketValidationRequest(...) 绕过解析器
        assert "MarketValidationRequest(" not in source, relative


def test_flag_defined_in_settings_and_default_yaml() -> None:
    settings_source = _read("settings.py")
    default_yaml = (
        Path(__file__).resolve().parents[2] / "config" / "default.yaml"
    ).read_text(encoding="utf-8")

    assert "enable_market_validation: bool = False" in settings_source
    assert "enable_market_validation: false" in default_yaml
