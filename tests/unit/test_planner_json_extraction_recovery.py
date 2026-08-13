"""模型交出了好东西，解析层不许把它扔掉。

真机 2026-08-09（castspec-repro-1）：cast_spec 连挂 4 次、整本书死在 foundation。
把 4 次的原始响应捞出来看，**每一次都带着一份完整可用的 cast**（林晚秋 + 7~10 个配角），
是框架自己把它们丢了，两条独立的路：

1. 信封：模型把整份 CastSpec 套在 ``{"cast_spec": {...}}`` 里。``CastSpecInput``
   是 ``extra="ignore"``，于是信封被解析成「全默认值对象」；
   ``_repair_cast_identity_locks_for_planner`` 的 ``model_dump()`` 再把它落成
   全 null 的 5 键空壳——正是 deai-verify-20260808 落库的那个空壳。
2. 容器错位：顶层对象有语法错时，``{`` 扫描失败，``[`` 扫描却在正文深处匹配到
   第一个嵌套数组，把某个角色心理画像里的 ``["灾难化推演", …]`` 当成整份 CastSpec
   返回；而后面的 json-repair 本来能把真正的对象修好，却永远走不到。
"""

from __future__ import annotations

import json

from bestseller.services.planner import (
    _extract_json_payload,
    _unwrap_planner_envelope,
)


def test_envelope_is_unwrapped() -> None:
    payload = _unwrap_planner_envelope(
        {"cast_spec": {"protagonist": {"name": "林晚秋"}}},
        "cast_spec",
    )
    assert payload == {"protagonist": {"name": "林晚秋"}}


def test_envelope_unwrap_covers_repair_rounds_and_generic_keys() -> None:
    assert _unwrap_planner_envelope(
        {"CastSpec": {"protagonist": {"name": "林晚秋"}}},
        "cast_spec_personhood_repair",
    ) == {"protagonist": {"name": "林晚秋"}}
    assert _unwrap_planner_envelope(
        {"data": {"protagonist": {"name": "林晚秋"}}},
        "cast_spec",
    ) == {"protagonist": {"name": "林晚秋"}}


def test_real_payload_is_not_mistaken_for_an_envelope() -> None:
    """单字段的真实载荷不是信封 —— 收窄到「键名就是这份产物」才拆。"""

    only_protagonist = {"protagonist": {"name": "林晚秋"}}
    assert _unwrap_planner_envelope(only_protagonist, "cast_spec") == only_protagonist

    only_volumes = {"volumes": [{"volume_number": 1}]}
    assert _unwrap_planner_envelope(only_volumes, "volume_plan") == only_volumes

    # 键名对，但里面是空的：拆了也只会得到空壳，保持原样让空壳门去报错。
    assert _unwrap_planner_envelope({"cast_spec": {}}, "cast_spec") == {"cast_spec": {}}


def test_broken_object_is_repaired_not_replaced_by_a_nested_array() -> None:
    """顶层对象坏了，不许拿正文深处的嵌套数组顶替它。

    这是真机 attempt 3/4 的最小复现：对象里缺了一个逗号，而正文里有一个
    合法的嵌套数组排在后面。
    """

    broken = (
        '{"protagonist": {"name": "林晚秋", '
        '"psych_profile": {"biases": ["灾难化推演", "沉没成本执念", "低估自身筹码"]} '
        '"supporting_cast": []}'
    )
    # 前提确认：它确实不是合法 JSON，且确实含有一个可被误取的嵌套数组。
    try:
        json.loads(broken)
        raise AssertionError("fixture should not be valid JSON")
    except json.JSONDecodeError:
        pass

    result = _extract_json_payload(broken)

    assert isinstance(result, dict), f"nested array hijacked the payload: {result!r}"
    assert result["protagonist"]["name"] == "林晚秋"


def test_bare_array_payload_still_parses() -> None:
    """不许收紧到误伤：本来就是数组的产物（volume_plan）照旧。"""

    assert _extract_json_payload('[{"volume_number": 1}]') == [{"volume_number": 1}]


def test_prose_wrapped_object_still_parses() -> None:
    assert _extract_json_payload('这是结果：\n{"title": "十分钟"}\n以上。') == {
        "title": "十分钟"
    }
