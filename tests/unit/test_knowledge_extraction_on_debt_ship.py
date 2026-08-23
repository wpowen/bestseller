"""带质量债出货的章也必须抽知识——否则知识库永远是空的。

2026-08-23 深度排查，用户报「书籍创建完成后没有完整走整个框架的流程，
时灵时不灵」。一条根因串起两个症状：

真机验证书 9（18 章）：管线调用 `extract_chapter_feedback` **0 次**
（库里唯一那次是我手工探针）。原因是章后知识抽取整块挂在这个条件里：

    if (chapter_review_result.verdict == "pass" and chapter_promoted) \\
            or accept_chapter_on_stall:

而 `chapter_promoted` 需要商业判官放行——真机 149 份判决 **0 通过**。
于是正常流程的章走的是另一条出口：`production_state="quality_debt"`、
`chapter_quality_debt_reason="chapter_not_promoted"`、workflow 标
`completed_with_quality_debt`——**那条路上一行知识抽取都没有**。
书 9 的 10 章正是这样出货的（status=revision 而非 complete）。

后果链：知识层不落库（canon 0 / 承诺 0）→ 项目级一致性审稿如实报出
canon_coverage / timeline_coverage / foreshadowing_balance 空洞 →
判 attention → 顶层 workflow 永不完成 → 自愈反复重启 → 用户看到的
「时而走到时而走不到」。

判据：章的正文已定稿、书已经往后写，它的事实就是这本书的事实。
「够不够好到能提升」是质量判断，「事实进不进知识库」是连续性判断，
两者不该共用一个开关。
"""

from __future__ import annotations

# ruff: noqa: RUF002 — 中文标点是刻意的。
import asyncio
from types import SimpleNamespace
from unittest import mock

from bestseller.services import pipelines


class _Sess:
    def __init__(self) -> None:
        self.nested = 0

    def begin_nested(self):
        outer = self

        class _Ctx:
            async def __aenter__(self):
                outer.nested += 1
                return outer

            async def __aexit__(self, *a):
                return False

        return _Ctx()


class TestHelper:
    def _settings(self, enabled: bool = True):
        return SimpleNamespace(pipeline=SimpleNamespace(enable_chapter_feedback=enabled))

    def test_runs_extraction_when_enabled(self) -> None:
        called: list[dict] = []

        async def _fake(session, settings, **kw):
            called.append(kw)

        with mock.patch(
            "bestseller.services.feedback.extract_chapter_feedback", _fake
        ):
            asyncio.run(
                pipelines._extract_chapter_knowledge_if_enabled(
                    _Sess(),
                    self._settings(),
                    project_id="p",
                    chapter=SimpleNamespace(chapter_number=3, id="c"),
                    chapter_md="正文",
                    workflow_run_id="w",
                )
            )
        assert len(called) == 1
        assert called[0]["chapter_md"] == "正文"

    def test_switch_off_is_a_noop(self) -> None:
        called: list[dict] = []

        async def _fake(session, settings, **kw):
            called.append(kw)

        with mock.patch(
            "bestseller.services.feedback.extract_chapter_feedback", _fake
        ):
            asyncio.run(
                pipelines._extract_chapter_knowledge_if_enabled(
                    _Sess(),
                    self._settings(enabled=False),
                    project_id="p",
                    chapter=SimpleNamespace(chapter_number=3, id="c"),
                    chapter_md="正文",
                    workflow_run_id="w",
                )
            )
        assert called == []

    def test_empty_text_is_a_noop(self) -> None:
        called: list[dict] = []

        async def _fake(session, settings, **kw):
            called.append(kw)

        with mock.patch(
            "bestseller.services.feedback.extract_chapter_feedback", _fake
        ):
            asyncio.run(
                pipelines._extract_chapter_knowledge_if_enabled(
                    _Sess(),
                    self._settings(),
                    project_id="p",
                    chapter=SimpleNamespace(chapter_number=3, id="c"),
                    chapter_md="   ",
                    workflow_run_id="w",
                )
            )
        assert called == []

    def test_extraction_failure_never_propagates(self) -> None:
        """知识抽取是增益不是主线——它挂了不能拖垮出章。"""

        async def _boom(session, settings, **kw):
            raise RuntimeError("judge down")

        with (
            mock.patch("bestseller.services.feedback.extract_chapter_feedback", _boom),
            mock.patch.object(
                pipelines, "_recover_session_after_nonfatal_error", mock.AsyncMock()
            ),
        ):
            asyncio.run(
                pipelines._extract_chapter_knowledge_if_enabled(
                    _Sess(),
                    self._settings(),
                    project_id="p",
                    chapter=SimpleNamespace(chapter_number=3, id="c"),
                    chapter_md="正文",
                    workflow_run_id="w",
                )
            )


class TestWiring:
    def test_debt_ship_path_extracts_knowledge_too(self) -> None:
        """接线钉：带质量债出货的出口也要抽知识。"""

        import inspect

        src = inspect.getsource(pipelines.run_chapter_pipeline)
        assert src.count("_extract_chapter_knowledge_if_enabled(") >= 2, (
            "提升路径与质量债出货路径都必须调用知识抽取"
        )
        # 质量债出口就在这个标记附近
        assert "chapter_not_promoted" in src
