"""上架简介必须用文案工序的冠军，不能拿 premise 顶替。

2026-08-24 真机，5 本书对照：

    末日书（LLM 路径）        promotional_brief.blurb == metadata.synopsis  ✅ 用了冠军
    书9（source-bound）      blurb = premise 逐字复制                       ❌
    端到端书（source-bound）  blurb = 另写的一句长句，不是冠军               ❌

`copywriting_tournament` 在 5 本书上**全部跑过**，4 本产出冠军
（`fell_back_to_v0=false`），冠军写进了 `ConceptionResult.synopsis`。
而 `_generate_promotional_brief` 的 source-bound 分支取的是
`snapshot.reader_promise → book_spec.logline → fallback`，**从不读它**。

于是用户在上架页看到的是：

    「青云宗杂役祝余天生一双能看见借债走向的眼睛……**第50章**，账本只剩最后几页」

—— 剧情流水账，还报章号。而同一本书的冠军简介是：

    「杂役弟子祝余刚被人当众借走全部真气，反手替那人把欠的真气还回了原主……
      他想收手，账本却自己翻到下一页——门外扔进来一只内门师兄的旧鞋，鞋里夹着欠条。」

又一例「路走到了，材料没拿」：工序跑了、产出了、还留了回执，取的时候取了别的。
"""

from __future__ import annotations

from types import SimpleNamespace

from bestseller.services.planner import source_bound_blurb


_CHAMPION = (
    "杂役弟子祝余刚被人当众借走全部真气，反手替那人把欠的真气还回了原主。\n\n"
    "他想收手，账本却自己翻到下一页——门外扔进来一只内门师兄的旧鞋，鞋里夹着欠条。"
)
_PREMISE = "青云宗杂役祝余天生一双能看见借债走向的眼睛，旁人修的是把别人的真气抢过来……第50章，账本只剩最后几页。"


def _project(**meta):
    return SimpleNamespace(metadata_json=meta, language="zh-CN", title="别人借力我替他们还债")


class TestChampionWins:
    def test_champion_synopsis_is_preferred(self) -> None:
        got = source_bound_blurb(
            _project(synopsis=_CHAMPION, premise=_PREMISE),
            snapshot={"reader_promise": "前几章给出还力打脸的反转节奏"},
            book_spec={"logline": "江湖练借力，他练还力"},
            fallback_blurb=_PREMISE,
        )
        assert got == _CHAMPION

    def test_champion_beats_reader_promise(self) -> None:
        """reader_promise 是生产口吻（前几章给出…），永远不该当简介。"""

        got = source_bound_blurb(
            _project(synopsis=_CHAMPION),
            snapshot={"reader_promise": "前几章给出主角被借走真气的反转节奏"},
            book_spec={},
            fallback_blurb="",
        )
        assert "前几章" not in got


class TestFallbackChain:
    def test_without_a_champion_the_old_chain_still_works(self) -> None:
        got = source_bound_blurb(
            _project(),
            snapshot={"reader_promise": "读者承诺文本"},
            book_spec={"logline": "一句话文本"},
            fallback_blurb="兜底文本",
        )
        assert got == "读者承诺文本"

    def test_falls_through_to_logline_then_fallback(self) -> None:
        assert source_bound_blurb(
            _project(), snapshot={}, book_spec={"logline": "一句话文本"},
            fallback_blurb="兜底文本") == "一句话文本"
        assert source_bound_blurb(
            _project(), snapshot={}, book_spec={}, fallback_blurb="兜底文本") == "兜底文本"

    def test_a_synopsis_identical_to_the_premise_is_not_a_champion(self) -> None:
        """冠军没产出时 synopsis 可能就是 premise —— 那不算文案，别当冠军用。"""

        got = source_bound_blurb(
            _project(synopsis=_PREMISE, premise=_PREMISE),
            snapshot={"reader_promise": "读者承诺文本"},
            book_spec={}, fallback_blurb="兜底文本",
        )
        assert got == "读者承诺文本"

    def test_a_too_short_synopsis_is_not_a_blurb(self) -> None:
        got = source_bound_blurb(
            _project(synopsis="短"), snapshot={"reader_promise": "读者承诺文本"},
            book_spec={}, fallback_blurb="兜底文本",
        )
        assert got == "读者承诺文本"


def test_the_compiler_actually_uses_it() -> None:
    from pathlib import Path

    import bestseller.services.planner as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    body = src.split("async def _generate_promotional_brief(", 1)[1][:2200]
    assert "source_bound_blurb" in body, body[:400]
