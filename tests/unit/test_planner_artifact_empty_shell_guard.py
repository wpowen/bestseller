"""产出即校验：fail-closed 的规划产物不许以空壳落库。

真机 2026-08-08（deai-verify-20260808）的因果链：

1. ``cast_spec`` 属于 ``fail_closed_artifacts``，而
   ``effective_merge_fallback = merge_fallback and not effective_abort_on_fallback``
   —— 对这批产物 fallback **不再合并**，模型输出独自成稿；
2. 这批产物的契约全是 optional-with-default（``book_spec`` 干脆没有 validator），
   模型回一个只有骨架没有内容的对象时，校验照样通过；
3. 空壳被原样写进 planning_artifact_versions，几步之后才在
   ``ensure_project_identity_manifest`` 炸掉，报错指向错误的步骤。

这里锁住第 2 步：空壳当场判为「这次生成失败了」，走 planner 既有的
retry-with-diagnostics 循环；重试用尽则 fail-fast，且错误信息点名真正失败的产物。
"""

from __future__ import annotations

import pytest

from bestseller.services.planner import _assert_planner_payload_has_content


def test_all_null_cast_spec_is_rejected() -> None:
    """真机落库的那个空壳原样。"""

    with pytest.raises(ValueError, match="cast_spec"):
        _assert_planner_payload_has_content(
            "cast_spec",
            {
                "antagonist": None,
                "protagonist": None,
                "conflict_map": [],
                "supporting_cast": [],
                "antagonist_forces": [],
            },
        )


def test_cast_spec_repair_variants_are_covered() -> None:
    """修复轮同样会覆写 artifact，必须走同一把尺子。"""

    with pytest.raises(ValueError, match="cast_spec_personhood_repair"):
        _assert_planner_payload_has_content(
            "cast_spec_personhood_repair",
            {"protagonist": None, "supporting_cast": []},
        )


def test_wrapper_shaped_cast_spec_is_rejected() -> None:
    """模型把 cast 包在自造的外层键里：契约字段一个没命中。

    ``CastSpecInput`` 是 ``extra="ignore"``，这种载荷会被解析成全默认值，
    随后 ``model_dump()`` 把外层键整个丢掉——正是空壳的另一条来路。
    """

    with pytest.raises(ValueError, match="cast_spec"):
        _assert_planner_payload_has_content(
            "cast_spec",
            {"CastSpec": {"protagonist": {"name": "林晚秋"}}},
        )


def test_empty_book_spec_is_rejected() -> None:
    """book_spec 连 validator 都没有，空壳此前完全无人拦。"""

    with pytest.raises(ValueError, match="book_spec"):
        _assert_planner_payload_has_content(
            "book_spec",
            {"title": "", "logline": None, "protagonist": {}, "narrative_lines": []},
        )


def test_empty_world_spec_is_rejected() -> None:
    with pytest.raises(ValueError, match="world_spec"):
        _assert_planner_payload_has_content(
            "world_spec",
            {
                "world_name": None,
                "world_premise": "",
                "rules": [],
                "power_system": {},
                "locations": [],
                "factions": [],
            },
        )


def test_empty_volume_plan_is_rejected() -> None:
    with pytest.raises(ValueError, match="volume_plan"):
        _assert_planner_payload_has_content("volume_plan", [])

    with pytest.raises(ValueError, match="volume_plan"):
        _assert_planner_payload_has_content("volume_plan", {"volumes": []})


def test_single_volume_dict_shape_passes() -> None:
    """``parse_volume_plan_input`` 也收「裸的单卷对象」，别把它当空壳误杀。"""

    _assert_planner_payload_has_content("volume_plan", {"volume_number": 1})


def test_populated_payloads_pass() -> None:
    """不许收紧到误伤：只要契约字段带了内容就放行。"""

    _assert_planner_payload_has_content(
        "cast_spec", {"protagonist": {"name": "林晚秋"}}
    )
    _assert_planner_payload_has_content(
        "cast_spec", {"supporting_cast": [{"name": "赵峰"}]}
    )
    _assert_planner_payload_has_content("book_spec", {"logline": "十分钟内还清三万债"})
    _assert_planner_payload_has_content(
        "world_spec", {"locations": [{"name": "电子厂"}]}
    )
    _assert_planner_payload_has_content("volume_plan", [{"volume_number": 1}])
    _assert_planner_payload_has_content(
        "volume_plan", {"volumes": [{"volume_number": 1}]}
    )


def test_unregistered_artifacts_are_untouched() -> None:
    """没登记的产物不归这把尺子管，别顺手加门。"""

    _assert_planner_payload_has_content("volume_1_chapter_outline_batch_1_3", {})
    _assert_planner_payload_has_content("story_design_kernel", {})
