"""去 AI 味跑了，产出被丢弃了（2026-08-26 真机 custom-xuanhuan-1787749718）。

用户报「正文前几个字一看就非常有 AI 味，去 AI 味逻辑为什么没生效」。
查下来它**跑了**——``scene_writer | deslop_revise`` 10 次。真实链条是：

1. 检测器命中 19 处（narrative_repetition 6 / verb_tic_spam 5 /
   staccato_saturation 2 …），但 **AI 味分 = 0.0**：这些类别属
   ``_ADVISORY_STRUCTURAL``，被有意封顶不计分（patcher 改不了句法）。
2. 触发用**类别**（``DESLOP_DISCOURSE_CATEGORIES``）——所以 deslop 被正确触发。
3. 采纳用 ``_content_badness``，而它以 **span 计数**为主。整章弥漫的病在
   detector 里只折成 1-2 个 span，一次真正清掉它的重写只减 1-2 分，
   随便冒出两个新 advisory span 就输回原稿。
4. 结果：ch3 / ch6 各跑了两次 deslop，**一个新版本都没产生**——产出被丢弃。
   ch4 带着 52.9% 的纯叙述碎句率出货（预算 25%，人类中位约 15%）。

``_keep_better_key`` 的 docstring 早就记着这个失效：
「ch26/32/33 each came back byte-identical to their diseased original for
exactly this reason」——他们为**时刻切片**打了补丁（``slice_first`` 把切片轴
提前比较），但**碎句是同一形状，没打**。本修复照搬同一处方。

同样只在病态带内生效（``_STACCATO_PATHOLOGICAL``），带外维持单标量行为不变，
healthy 稿一个字不受影响。
"""

from __future__ import annotations

import pytest

from bestseller.services.deslop_revise import (
    _STACCATO_PATHOLOGICAL,
    _content_badness,
    _keep_better_key,
    _staccato_ratio,
)

pytestmark = pytest.mark.unit

# 原稿：整章单句独段（真机 ch4 的形状，纯叙述碎句 52.9%）
_DIRTY = "风停了。\n\n她没动。\n\n崽也没动。\n\n火塘暗了一格。\n\n" * 8
# 重写：段落合并（碎句清零），但引入两处 advisory 句式 → span 数反而更多
_CLEAN = (
    "风停下来的时候她没动，崽也没动，火塘暗了一格。这不是犹豫，而是等。"
    "她脸色一沉，又把草碾了一遍。\n\n" * 9
)


def _key(text: str, *, staccato_first: bool):
    return _keep_better_key(
        text, "zh-CN", slice_first=False, staccato_first=staccato_first
    )


class TestTheDocumentedFailureMode:
    def test_vacuity_the_single_scalar_really_does_discard_the_better_rewrite(self):
        """空转检验：单标量下，清掉碎句的重写确实输给带病原稿。"""
        assert _staccato_ratio(_DIRTY) > _staccato_ratio(_CLEAN)
        assert _content_badness(_CLEAN, "zh-CN") > _content_badness(_DIRTY, "zh-CN")
        assert _key(_CLEAN, staccato_first=False) > _key(_DIRTY, staccato_first=False)

    def test_the_staccato_axis_keeps_the_rewrite(self):
        assert _key(_CLEAN, staccato_first=True) < _key(_DIRTY, staccato_first=True)


class TestOutsideTheBandNothingChanges:
    def test_healthy_drafts_keep_the_single_scalar_behaviour(self):
        """带外必须逐字节维持旧行为——否则会动到健康稿。"""
        low = (
            "她把湿草碾碎，往崽嘴边送了送，崽嗅了嗅没张嘴，她就把草收回掌心又碾了一遍，"
            "指腹上沾了一层青绿，闻着有股涩味。\n\n"
        ) * 6
        high_badness = low + "这不是犹豫，而是等。她脸色一沉，又把草碾了一遍。\n\n"
        assert _staccato_ratio(low) < _STACCATO_PATHOLOGICAL
        a = _key(low, staccato_first=False)
        b = _key(high_badness, staccato_first=False)
        assert a[0] == 0.0 and b[0] == 0.0, "带外碎句轴必须恒为 0，不参与排序"
        assert (a < b) == (
            _content_badness(low, "zh-CN") < _content_badness(high_badness, "zh-CN")
        ), "带外排序必须仍由单标量决定"

    def test_the_band_threshold_sits_above_the_budget(self):
        """预算 25%，病态带必须严格更高——否则刚超预算就改变排序语义。"""
        assert _STACCATO_PATHOLOGICAL > 0.25


class TestTheSliceAxisStillWorks:
    def test_slice_first_is_unaffected_by_the_new_axis(self):
        """切片轴的既有语义不能被挤掉。"""
        text = (
            "她在崩塌的那一瞬间抬手，指尖擦过那道裂缝，脚下的石板一寸一寸往下沉，"
            "她没有回头，只把手里的绳子又缠了两圈。\n\n"
        ) * 8
        k = _keep_better_key(text, "zh-CN", slice_first=True, staccato_first=False)
        # 2026-08-27 加了词族/复读两条轴后，元组变成
        # (碎句, 词族, 复读, 切片, badness)——切片下标从 1 移到 3。
        assert k[0] == 0.0, "碎句轴不参与"
        assert k[3] > 0.0, "切片轴仍生效（现位于第四位）"
