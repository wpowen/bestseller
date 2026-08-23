"""一个会被系统自己擦掉的配置开关，等于不存在。

`chapter_first_stop_after_repair_exhaustion` 决定「一章修复预算耗尽时是否停掉
整本书等人工」。真机（书 9，2026-08-24）它造成了 **24 次** chapter_pipeline 停摆，
书跑不到后段。

但它此前**只**活在 ``projects.metadata`` 里，而跑书中的管线会用一份旧的内存副本
整块重写那个 JSONB —— 我把它设成 False 并核对落库成功，**几分钟后监控报「被抹掉」**，
我写进去的两个键（开关本身 + 运维说明）一起消失。这是 2026-07-21 就记录在案的
「跑书中改 projects.metadata 会被静默覆盖」，今天现场复现了一次。

修：给它一个持久的家（settings，可由 env 设定），project metadata 仍然优先
（它存活时表达的是 per-book 意图）。**默认值保持 True，行为不变。**
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import inspect

import pytest

from bestseller.services import pipelines as pipeline_services
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit

_ENV_KEY = "BESTSELLER__PIPELINE__CHAPTER_FIRST_STOP_AFTER_REPAIR_EXHAUSTION"


def test_the_default_is_unchanged() -> None:
    """修的是「能不能持久设定」，不是默认行为。"""

    assert load_settings(env={}).pipeline.chapter_first_stop_after_repair_exhaustion is True


def test_env_can_turn_it_off_durably() -> None:
    settings = load_settings(env={_ENV_KEY: "false"})
    assert settings.pipeline.chapter_first_stop_after_repair_exhaustion is False


def test_env_can_turn_it_on_explicitly() -> None:
    settings = load_settings(env={_ENV_KEY: "true"})
    assert settings.pipeline.chapter_first_stop_after_repair_exhaustion is True


def test_project_metadata_still_wins_when_it_survives() -> None:
    """per-book 意图优先级不变——只是不再是**唯一**的家。"""

    source = inspect.getsource(pipeline_services)
    idx = source.index('"chapter_first_stop_after_repair_exhaustion", _stop_default')
    head = source[max(0, idx - 400) : idx]
    assert "_project_meta.get(" in head, "project metadata 必须仍然优先于 settings"


def test_the_settings_default_is_actually_consulted() -> None:
    """接线断言：默认值来自 settings，而不是又写死一个 True。"""

    source = inspect.getsource(pipeline_services)
    assert "settings.pipeline," in source
    assert '"chapter_first_stop_after_repair_exhaustion",' in source
    assert '_project_meta.get("chapter_first_stop_after_repair_exhaustion", True)' not in source
