"""反债重写反馈要引回构思**自己写下的词**，否则模型换个近义词照写同一族。

2026-08-23 真机（验证书 9）：原稿判定债务族支配 → 触发重写 → **重写稿依然
是债务族支配** → 不采纳 → 保留原稿出货。反馈本身是对的（刻意不点任何族内
词，避免种词），但它只说「换一个完全不同的机制家族」，没告诉模型这次踩到的
是哪些词，于是模型把「旧账」换成「欠条」再换成「人情债」——始终在同一族里。

关键区分（本仓库 2026-08-06 定案「否定式指令点名母题词=种词」）：
* **静态禁词表**会把新词汇塞进模型脑子里 → 是种词，禁止；
* **引回模型这次自己写的词**不引入任何新词汇 → 不是种词，且让指令可执行。

同一条引文接地思路已在本仓库多处验证（简介矛盾判官、逻辑轴检察官、
事件级调性判官）。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
from bestseller.services.anti_default_motif import default_debt_family_matches
from bestseller.services.conception import _render_debt_rewrite_feedback


class TestMatches:
    def test_returns_the_actual_words_not_regex_sources(self) -> None:
        got = default_debt_family_matches(
            "他替死人还旧账，灵堂里开棺见了最后一面，阳寿只剩三年。"
        )
        assert "账" in got or "旧账" in got
        # 2026-08-24：丧葬子族改按**事件**匹配后，引回的是「灵堂」「开棺」这类
        # 事件词，而不再是裸「棺」。裸字在真实出版语料上 15.2%→2.1%（n=2974，
        # 误报样例：丧心病狂／垂头丧气／不见棺材不流泪／丧失繁殖能力）。本用例
        # 的设计意图是「引回模型自己写下的词、绝不漏正则源码」，那一条没变。
        assert any(("灵堂" in g or "开棺" in g) for g in got), got
        assert any("阳寿" in g for g in got)
        # 绝不能把正则源码漏出去
        assert not any("(?!" in g for g in got)

    def test_empty_text_is_empty(self) -> None:
        assert default_debt_family_matches("") == ()

    def test_clean_text_has_no_matches(self) -> None:
        assert default_debt_family_matches("少年握剑站在山门前，风很大。") == ()

    def test_matches_are_deduped_and_capped(self) -> None:
        got = default_debt_family_matches("账账账账账账账账账账债债债债")
        assert len(got) <= 8
        assert len(set(got)) == len(got)


class TestFeedback:
    def test_matched_words_are_quoted_back(self) -> None:
        text = _render_debt_rewrite_feedback(
            is_en=False, matched_terms=("旧账", "棺", "阳寿")
        )
        for term in ("旧账", "棺", "阳寿"):
            assert term in text
        # 必须说清「换族」而不是「换个同义词」
        assert "近义" in text or "同义" in text

    def test_without_matches_it_stays_the_old_vocabulary_free_instruction(self) -> None:
        text = _render_debt_rewrite_feedback(is_en=False)
        assert "完全不同" in text
        # 无命中时绝不能凭空点名任何族内词（种词）
        for seeded in ("债", "阳寿", "灵堂", "殡"):
            assert seeded not in text

    def test_english_branch_also_quotes(self) -> None:
        text = _render_debt_rewrite_feedback(is_en=True, matched_terms=("debt", "coffin"))
        assert "debt" in text and "coffin" in text


class TestWiring:
    def test_conception_passes_the_concepts_own_matches(self) -> None:
        """接线钉：重生反馈必须带上这次构思的实际命中词。"""

        import inspect

        from bestseller.services import conception

        src = inspect.getsource(conception.run_conception_pipeline)
        assert "default_debt_family_matches(" in src
        assert "matched_terms=" in src
