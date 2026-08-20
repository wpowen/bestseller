"""命名池检查的人物语境要求（2026-08-20 真机《罚我守坟》定罪）。

真机 23 章共报 NAMING_OUT_OF_POOL **75 次**，去重后 43 个「人名」：

  余温/余温烙/余温烫、和卖菜/和坟口/和方才/和昨夜、常嘴唇/常站/常脚尖、
  平整、成旧疤/成暗红/成疤/成青紫、方皮肉/方顿住、时发亮/时末/时进山、
  水印/水洒/水顺、皮烧得/皮翘出/皮肉底、章押/章盖、米粒大、陈粮、齐齐向 …

**42/43 是误报**（唯一真名是「吴六」）。全部形如「常见姓氏字 + 后续散字」——
和/成/方/时/水/皮/章/平/齐/余/常/朱/明/米/陈 全是姓氏，在中文散文里遍地都是。

病根不是漏了哪个词，是**形状反了**：先用姓氏正则捞候选，再靠一张
手工黑名单做减法。那张 `_ZH_COMMON_WORD_2ND_CHARS` 表的注释里写满了
「Qiyouhun audit / Qingnang audit / opening-rescue audit / v4 audit」——
每来一本新书就往表里加一批字，永远收敛不了。

改成**要求正面证据**：候选必须在本章至少出现一次人物语境
（说/道/问/答等言行动词紧随、叫/名叫/唤作 引出、师兄/长老/执事 等称谓紧随、
引号对白归属）。真机 43 个候选剔除 43-1=42… 实测剔除率 98%，「吴六」
靠「卖葱那个叫吴六」保住。

这是**纯减法**：只可能减少 finding，不可能新增，因此不涉及任何杀权变化。
"""

from __future__ import annotations

import pytest

from bestseller.services.output_validator import _zh_name_has_person_context

pytestmark = pytest.mark.unit


def test_prose_fragments_have_no_person_context():
    text = (
        "他把封皮翻过来，边缘结成旧疤，颜色成暗红。"
        "米粒大的一点朱印压在陈粮袋口，章盖得很浅。"
    )
    for fragment in ("成旧疤", "成暗红", "米粒大", "陈粮", "章盖"):
        assert not _zh_name_has_person_context(fragment, text), fragment


def test_speech_attribution_is_person_context():
    assert _zh_name_has_person_context("吴六", "吴六说了句什么，他没听清。")
    assert _zh_name_has_person_context("吴六", "钟楼打断她，“卖葱那个叫吴六。”")


def test_appellation_is_person_context():
    assert _zh_name_has_person_context("季伯", "季伯执事把簿册合上。")


def test_quoted_dialogue_attribution_is_person_context():
    assert _zh_name_has_person_context("苗青灯", "苗青灯道：“你认得他？”")
