"""跨章重复要跟**所有**前序章比，不能只跟紧邻的上一章比。

2026-08-24 真机取证（书9，50 章）：

    13 章曾命中 CROSS_CHAPTER_REPETITION
    10 章修好了
    3 章带着未解决的重复出货 —— 第8章 **10 处**、第33章 1 处、第40章 2 处
    第8章：7 稿，auto_repair_attempts=2，auto_repair_exhausted=true

（第8章写于今天那条引文修复 d48500b5 之前——当时重写指令只告诉模型「有重复」，
不告诉它是哪一段，引文覆盖率 7%。那条已修到 90%。）

本条修的是另一件事：`pipelines.py` 那条 bundle 路径只把**紧邻的上一章**放进
`previous_chapter_texts`，而 `reviews.py` / `drafts.py` 用的是
`_collect_previous_current_chapter_texts`（取全部前序章的在架稿）。

后果：第 N 章与第 N-3 章逐字重复，这条路径看不见。而这条路径正是
**短书唯一走到的那条**——`reviews.py` 的 bundle 挂在
`target_chapters >= commercial_planning_min_target_chapters`（=50）后面，
12 章的书整块跳过，只剩 pipelines 这一条，而它只比一章。

同一个检查、两套输入口径，弱的那套服务的恰好是覆盖最薄的书。
"""

from __future__ import annotations

from bestseller.services.chapter_quality_bundle import (
    ChapterQualityBundleContext,
    run_chapter_quality_bundle,
)

_SHARED = "沈渡把手里的灰撒进风里。灰没飘远，落回焦土，落回那个被他踩灭的黑蝴蝶的残骸上。"


def _chapter(n: int, extra: str) -> str:
    return f"第{n}段开头各不相同的一段话，用来撑开段落数量。\n\n{extra}\n\n收尾的一段话也各不相同{n}。"


class TestAdjacentStillWorks:
    def test_duplicate_with_the_immediately_previous_chapter(self) -> None:
        ctx = ChapterQualityBundleContext(
            chapter_number=11,
            previous_chapter_texts=((10, _chapter(10, _SHARED)),),
            total_chapters=12,
        )
        report = run_chapter_quality_bundle(_chapter(11, _SHARED), ctx)
        codes = [f.code for f in report.findings]
        assert "CROSS_CHAPTER_REPETITION" in codes, codes


class TestDistantChapters:
    def test_duplicate_with_a_chapter_three_back_is_caught(self) -> None:
        """只比上一章的口径会漏掉这个——真机 pipelines 路径就是这样。"""

        ctx = ChapterQualityBundleContext(
            chapter_number=11,
            previous_chapter_texts=(
                (8, _chapter(8, _SHARED)),
                (9, _chapter(9, "第九章自己的一段话，与别处都不同。")),
                (10, _chapter(10, "第十章自己的一段话，与别处都不同。")),
            ),
            total_chapters=12,
        )
        report = run_chapter_quality_bundle(_chapter(11, _SHARED), ctx)
        codes = [f.code for f in report.findings]
        assert "CROSS_CHAPTER_REPETITION" in codes, codes

    def test_only_previous_chapter_misses_it(self) -> None:
        """记录旧口径的盲区——这条是为什么要改。"""

        ctx = ChapterQualityBundleContext(
            chapter_number=11,
            previous_chapter_texts=((10, _chapter(10, "第十章自己的一段话。")),),
            total_chapters=12,
        )
        report = run_chapter_quality_bundle(_chapter(11, _SHARED), ctx)
        assert "CROSS_CHAPTER_REPETITION" not in [f.code for f in report.findings]


def test_the_pipeline_path_supplies_all_prior_chapters() -> None:
    """pipelines 那条路径必须用全量收集器，不能只塞一个上一章。"""

    from pathlib import Path

    import bestseller.services.pipelines as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    # 断言 **context 拿到的是全量收集器的结果**；except 里保留「紧邻上一章」
    # 作为 fail-open 兜底是正确的，不该被这条禁掉。
    assigns = src.count("previous_chapter_texts=_prior_chapter_texts")
    assert assigns >= 2, f"只有 {assigns} 处用了全量收集器"
    assert "_collect_previous_current_chapter_texts" in src
