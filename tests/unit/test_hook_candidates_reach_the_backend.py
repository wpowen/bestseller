"""系统生成的钩子候选必须真的送出去。

后端为**每个题材**生成 4 个钩子候选（``generate_hook_candidates``），随
``/api/writing-presets`` 一起发给浏览器。前端接住了：

    hookCandidates = d.hook_candidates || {};

然后整个页面里再也没有读过它一次——只有声明、赋值、清空三处引用。没有选择器、
不渲染、不提交。

于是构思端三级取种子链的第三级是断的：

    seed_concept = 手填创意 or 脑洞实验室选的 or selected_hook_spec.one_liner

``selected_hook_spec`` 取自 ``user_hints["hook_spec"]``，而表单从不发这个字段。
只选题材建书时三级全空，淘汰赛在**完全没有种子**的情况下硬造概念——那才是空题材
反复失败的原因，不是「用户必须手填创意」。

与 2026-07-24 修过的 ``concept_seed`` 断链完全同形：后端全链早已就绪，表单从未
把它送出去。

判据：用户没有给创意时，系统必须把**自己生成的**那份用上，而不是让淘汰赛空手起步。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_HTML = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "bestseller"
    / "web"
    / "novel_quickstart.html"
).read_text(encoding="utf-8")


class TestHookCandidatesAreSubmitted:
    def test_the_payload_carries_a_hook_spec(self) -> None:
        idx = _HTML.index("function buildQuickstartPayload()")
        body = _HTML[idx : idx + 1600]
        assert "hook_spec" in body, (
            "后端生成了钩子候选、前端也收到了，却从不发送——三级种子链的第三级是断的"
        )

    def test_a_helper_picks_the_candidate(self) -> None:
        assert "_autoHookSpecForSeeding" in _HTML

    def test_the_helper_reads_the_loaded_candidates(self) -> None:
        idx = _HTML.index("function _autoHookSpecForSeeding")
        body = _HTML[idx : idx + 900]
        assert "hookCandidates" in body, "必须用后端已经送来的那份，不要另造"

    def test_it_sends_the_spec_not_the_wrapper(self) -> None:
        """候选外层是 {combined_rank, score, spec…}，HookSpec 是里面的 spec。"""

        idx = _HTML.index("function _autoHookSpecForSeeding")
        body = _HTML[idx : idx + 900]
        assert ".spec" in body


class TestUserIntentWins:
    def test_an_explicit_seed_suppresses_the_auto_hook(self) -> None:
        """用户手填了创意，就不该再塞一个系统钩子进去和它打架。"""

        idx = _HTML.index("function _autoHookSpecForSeeding")
        body = _HTML[idx : idx + 900]
        assert "conceptSeedInput" in body

    def test_a_concept_lab_bundle_suppresses_the_auto_hook(self) -> None:
        idx = _HTML.index("function _autoHookSpecForSeeding")
        body = _HTML[idx : idx + 900]
        assert "selectedConceptBundle" in body or "bundle" in body


class TestItDegradesQuietly:
    def test_no_candidates_means_no_field(self) -> None:
        """题材没有候选时照常建书，不能因此报错或阻断。"""

        idx = _HTML.index("function _autoHookSpecForSeeding")
        body = _HTML[idx : idx + 900]
        assert "return undefined" in body or "return null" in body
