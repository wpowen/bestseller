"""简介不许谈论章节——那是系统在描述自己的结构，不是故事在讲自己。

2026-08-23 真机（验证书 9）用户读后第一反应是「AI 味还是很足」。逐句看，
最刺眼的是这句：

    「上一章替他挡刀的人，**下一章**就得哭着求他还人情。」

真实平台简介绝不会用「章」当叙述单位——读者此刻还没开始读，「上一章」对他
没有指涉。这是生成端把**作品的组织结构**当成了故事内容写进对外文案。

而三层量具全都漏了它：
* 正文 AI 味检测器：对这份 128 字简介 0 命中（它测句法节奏/碎句/拟人，
  是为几千字正文校准的）；
* 简介病理检测器：0 命中；
* 确定性吸引力门：这份简介拿了 75.15 分、首轮过线。

所以「把去 AI 味接到简介路径」是个空修复（实测过），真正缺的是这条针对
对外文案的元语言判据。

判据只认**作品结构单位**（章/卷/节/篇 + 上一/下一/本/首/末），不认故事里
的实物账册（账本翻到下一页 ≠ 谈论章节），也不误伤「一章」出现在书名或
引号内的情形。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
from bestseller.services.blurb_pathology import detect_blurb_pathology

_REAL = (
    "杂役弟子祝余刚被人当众借走全部真气，反手替那人把欠的真气还回了原主。"
    "每还一笔，新债主自动上门；上一章替他挡刀的人，下一章就得哭着求他还人情。"
)


def _codes(text: str) -> list[str]:
    return [f.to_dict()["code"] for f in detect_blurb_pathology(text)]


class TestChapterMetaLeak:
    def test_real_machine_case_is_caught(self) -> None:
        assert "BLURB_CHAPTER_META" in _codes(_REAL)

    def test_next_chapter_alone_is_caught(self) -> None:
        assert "BLURB_CHAPTER_META" in _codes("他不知道，下一章等着他的是什么。")

    def test_volume_meta_is_caught(self) -> None:
        assert "BLURB_CHAPTER_META" in _codes("本卷结束时，他已经站到了山顶。")

    def test_finding_quotes_the_offending_text(self) -> None:
        finding = next(
            f for f in detect_blurb_pathology(_REAL)
            if f.to_dict()["code"] == "BLURB_CHAPTER_META"
        )
        d = finding.to_dict()
        assert "下一章" in str(d.get("excerpt") or "")
        # 只挣重写，不是致命病理（简介可以改，不该因此毙掉整本书）。
        assert d.get("severity") != "fatal"


class TestNoFalsePositives:
    def test_in_world_ledger_page_is_not_meta(self) -> None:
        # 「账本翻到下一页」是故事里的实物，不是在谈论作品结构。
        assert "BLURB_CHAPTER_META" not in _codes("账本自己翻到下一页，夹着一张欠条。")

    def test_ordinary_blurb_is_clean(self) -> None:
        assert "BLURB_CHAPTER_META" not in _codes(
            "少年韩立凭一个小瓶走上修仙路，资质平庸却要活到最后。"
        )

    def test_seal_object_zhang_is_not_meta(self) -> None:
        # 「印章」「盖章」里的「章」不是结构单位。
        assert "BLURB_CHAPTER_META" not in _codes("他把那枚私章按在契书上，血还没干。")
