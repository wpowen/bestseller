"""发布门对已让步的章，只保留结构性否决。

``collect_publication_blockers`` 里有两类检查：

* **结构性**——没有当前稿、状态不可发布、缺场景来源记录。这些意味着「这章根本
  没写完」，必须继续拦，否则导出的是一本残缺的书。
* **内容质量**——长度、成品卫生、常识因果、重复检测。这些是「写完了但不够好」。

对 ``quality_debt`` 的章，第二类是在重审一个**质量系统自己做过的裁决**：那个状态
的字面意思就是「预算耗尽，决定发布这份稿」。

真机取证（2026-07-28，urban-power-reversal-1785231106）：全程无人干预跑到
``completed``、三章 promoted、debt_chapters=[1,2,3]，导出仍被拦：

    第3章：常识因果门禁 lay_character_rule_knowledge_leak

而这段代码**自己的注释**已经写着「export gate re-runs the same blind regex, so
honor that ruling instead of re-litigating prose causality without context」——
它早就有一个豁免（``common_sense_dismissed_codes``），只是没覆盖「修复循环已经
让步」这一种。

上一轮我把让步加在了终局质量门，而拦人的是发布门，它先抛——修错了位置。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import exports

pytestmark = pytest.mark.unit


class TestContentFindingsAreConcededForDebtChapters:
    def test_the_gate_tracks_whether_a_chapter_is_conceded(self) -> None:
        source = inspect.getsource(exports.collect_publication_blockers)
        assert "_conceded" in source, (
            "发布门必须知道哪些章已经被修复循环让步"
        )

    def test_content_findings_route_through_the_concession_helper(self) -> None:
        source = inspect.getsource(exports.collect_publication_blockers)
        assert "_content_blocker(" in source, (
            "内容类 finding 必须走可让步的入口，不能直接 append 到 blockers"
        )

    def test_the_common_sense_finding_is_a_content_finding(self) -> None:
        """真机上就是它拦的。"""

        source = inspect.getsource(exports.collect_publication_blockers)
        block = source[source.index("common_sense.findings") :]
        head = block[: block.index("except")]
        assert "_content_blocker(" in head


class TestStructuralFindingsStillBlockAlways:
    @pytest.mark.parametrize(
        "marker",
        ["not publishable", "scene provenance", "chapter is not finished"],
    )
    def test_structural_checks_do_not_use_the_concession_helper(
        self, marker: str
    ) -> None:
        """「没写完」不因为带 debt 就变成可发布。"""

        source = inspect.getsource(exports.collect_publication_blockers)
        idx = source.index(marker)
        window = source[max(0, idx - 400) : idx]
        assert "_content_blocker(" not in window


class TestConcessionsAreVisible:
    def test_a_conceded_finding_is_recorded(self) -> None:
        source = inspect.getsource(exports.collect_publication_blockers)
        assert "debt_warnings" in source, "让步必须留痕，不能静默放行"
