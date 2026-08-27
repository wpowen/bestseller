"""弥漫型病要各有一条轴，否则重写永远输给带病原稿（2026-08-27）。

上一轮只给**碎句**加了轴（``staccato_first``）。真机
custom-xuanhuan-1787757487 证明那不够——三章的碎句都在病态带外
（20.0% / 8.7% / 29.4%，带 35%），而每章真正在犯的是别的病：

    ch1  verb_tic_spam, narrative_repetition
    ch2  negated_definition×3, narrative_repetition×2, verb_tic_spam
    ch3  narrative_repetition×2, verb_tic_spam, dash_density

deslop 的 ``scene_writer|deslop_revise`` 照常跑，但 ch1/ch3 的调用发生在
**末版之后且零新版本**——产出仍被丢弃，因为这两条病在 ``_content_badness``
里同样只折成 1-2 个 span。

**人类语料标定（2955 篇出版章，2026-08-27）**：

    verb_tic/万字     中位 6.3   p90 21.5  p95 28.8  **p99 49.3**  max 149.4
    repetition_load   中位 0.8%  p90 3.5%  p95 4.7%  p99 8.8%

    本书三章 verb_tic  60.0 / 101.2 / 121.5  → **全部越过人类 p99**
             rep_load   0.4% /  1.7% /  2.6%  → **全部低于人类 p90**

所以 verb_tic 是真病（ch3 是 p99 的 2.5 倍），而 narrative_repetition
在人类正常区间内——**它被标记只是因为「≥4 个重复短语」这个计数条件，
不代表严重**。轴仍然要给它留，否则下次真犯时又是同样的丢弃。

两条密度都不是新造的：从检测器内部抽出为公开函数
（``verb_tic_density`` / ``narrative_repetition_load``），避免同一事实住两地。
"""

from __future__ import annotations

import pytest

from bestseller.services.ai_flavor.detector import (
    narrative_repetition_load,
    verb_tic_density,
)
from bestseller.services.deslop_revise import (
    _REPETITION_PATHOLOGICAL,
    _VERB_TIC_PATHOLOGICAL,
    _content_badness,
    _keep_better_key,
)

pytestmark = pytest.mark.unit

_TIC_DIRTY = (
    "他撞开门，热气烫在脸上，风钻进领口，他攥紧刀，爬上台阶，"
    "又撞了一次，烫得他缩手，钻心地疼，攥不住，爬不动。" * 10
)
_TIC_CLEAN = (
    "他推开门，热气扑在脸上，风从领口进来，他握紧刀，走上台阶。"
    "这不是犹豫，而是等。他脸色一沉，又推了一次。" * 10
)


def _key(text: str, **kw):
    kw.setdefault("slice_first", False)
    return _keep_better_key(text, "zh-CN", **kw)


class TestVerbTicAxis:
    def test_vacuity_without_the_axis_the_clean_rewrite_is_discarded(self):
        """空转检验：单标量下，压掉词族的重写确实输给带病原稿。"""
        assert verb_tic_density(_TIC_DIRTY) > verb_tic_density(_TIC_CLEAN)
        assert _content_badness(_TIC_CLEAN, "zh-CN") > _content_badness(
            _TIC_DIRTY, "zh-CN"
        )
        assert _key(_TIC_CLEAN) > _key(_TIC_DIRTY)

    def test_the_axis_keeps_the_rewrite(self):
        assert _key(_TIC_CLEAN, verb_tic_first=True) < _key(
            _TIC_DIRTY, verb_tic_first=True
        )

    def test_the_threshold_is_the_human_p99(self):
        """阈值必须有标定来源——人类 2955 章 p99≈49.3。"""
        assert 45.0 <= _VERB_TIC_PATHOLOGICAL <= 55.0


class TestRepetitionAxis:
    def test_the_axis_exists_even_though_it_was_not_this_book_s_disease(self):
        """真机三章 rep_load 0.4-2.6%，在人类 p90 以下——轴仍要留。"""
        dirty = "他把刀放在桌上，他把刀放在桌上，他把刀放在桌上，" * 20
        clean = (
            "他把刀放在桌上，然后退开半步。屋里安静下来，谁都没有说话。"
            "窗外的风把窗纸吹得响了一下，火苗歪了歪，又立直。"
            "老陈从门口进来，看了一眼那把刀，什么也没问，坐到了对面。"
            "茶壶在炉子上咕嘟着，水汽把梁上的灰熏出一道浅痕。"
        )
        assert narrative_repetition_load(dirty) > narrative_repetition_load(clean)
        assert _key(clean, repetition_first=True) < _key(dirty, repetition_first=True)

    def test_the_threshold_sits_above_the_human_p99(self):
        """人类 p99≈8.8%；沿用检测器自己的 severe 线 0.15，不另造数。"""
        assert _REPETITION_PATHOLOGICAL >= 0.09


class TestOutsideEveryBandNothingChanges:
    def test_healthy_drafts_still_sort_by_the_single_scalar(self):
        low = "他推开门，热气扑在脸上，风从领口进来，他握紧刀，走上台阶，站定了。" * 8
        high = low + "这不是犹豫，而是等。他脸色一沉。"
        a, b = _key(low), _key(high)
        assert a[:4] == (0.0, 0.0, 0.0, 0.0), "带外每条轴都必须恒为 0"
        assert b[:4] == (0.0, 0.0, 0.0, 0.0)
        assert (a < b) == (
            _content_badness(low, "zh-CN") < _content_badness(high, "zh-CN")
        )


class TestTheMetricsHaveOneHome:
    def test_the_densities_come_from_the_detector_not_a_local_copy(self):
        """同一事实住两地是本仓库的招牌病——密度只许有一个来源。"""
        import inspect

        from bestseller.services import deslop_revise

        src = inspect.getsource(deslop_revise)
        assert "from bestseller.services.ai_flavor.detector import" in src
        assert "def verb_tic_density" not in src
        assert "def narrative_repetition_load" not in src

    def test_the_detector_still_uses_the_same_helper_it_exposes(self):
        import inspect

        from bestseller.services.ai_flavor import detector

        src = inspect.getsource(detector)
        assert "_repeated_gram_profile(content_md)" in src
