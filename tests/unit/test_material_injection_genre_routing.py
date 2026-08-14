"""物料/语料注入必须按本书题材路由（2026-08-14 真机定罪）。

真机证据：一本东方玄幻书（custom-xuanhuan-1786628567）的场景写手 prompt 里，
「文化原型」块注入了 便利店饭团／外卖咖啡／工牌挂绳／电梯门禁卡／蓝牙耳机，
「风格参照」块注入了 *suspense-mystery / exorcism* 语料。两处都是写死的：

- ``_cultural_archetypes`` 无视 pack/题材，恒取 ``urban_modern + classical_chinese``；
  而每份 yaml 自己声明了 ``applicable_categories``（urban_modern 写的是
  都市轻喜/urban-contemporary），代码从未读过它。
- ``_reference_corpora`` 的兜底键写死 ``suspense-mystery``，而
  ``xuanhuan-power-fantasy.yaml`` 并不存在——于是玄幻书恒定拿到悬疑驱魔语料，
  同时 ``action-progression.yaml``（升级爽文·玄幻仙侠）明明存在却永远够不到。

这是「跨题材串味」家族的又一处源头：注入的物料就是种词，写手照着写。
修复原则：按本书题材匹配；匹配不上宁可**不注入**，绝不回退到某个具体题材。
"""

from __future__ import annotations

import pytest

from bestseller.services.material_injection_orchestrator import (
    _cultural_archetypes,
    _reference_corpora,
)

pytestmark = pytest.mark.unit


# ── 文化原型：只注入本书题材声明过的调色板 ────────────────────────────────


def test_xuanhuan_book_gets_no_modern_city_palette() -> None:
    block = _cultural_archetypes(
        "xuanhuan-power-fantasy", ("action-progression", "东方玄幻", "玄幻脑洞")
    )
    for token in ("便利店", "工牌", "门禁卡", "蓝牙耳机", "外卖咖啡"):
        assert token not in block, token


def test_urban_book_still_gets_its_own_palette() -> None:
    """修复不能把对的题材一起饿死。"""

    block = _cultural_archetypes("urban-contemporary", ("urban-contemporary", "都市轻喜"))
    assert "便利店" in block


def test_unknown_genre_injects_nothing_rather_than_a_wrong_one() -> None:
    assert _cultural_archetypes("no-such-pack", ("no-such-category",)) == ""
    assert _cultural_archetypes(None, None) == ""


# ── 风格参照：本书 pack → 本书题材 → 题材中立，绝不回退到具体题材 ──────────


def test_xuanhuan_book_never_gets_suspense_corpus() -> None:
    corpus = _reference_corpora("xuanhuan-power-fantasy", ("action-progression", "东方玄幻"))
    assert corpus, "玄幻书应当拿到升级爽文语料（它一直存在，只是被写死的兜底挡住）"
    for token in ("suspense-mystery", "exorcism", "悬疑", "驱魔"):
        assert token not in corpus, token


def test_suspense_book_still_gets_suspense_corpus() -> None:
    corpus = _reference_corpora("suspense-mystery", ("suspense-mystery", "悬疑灵异"))
    assert "suspense-mystery" in corpus or "悬疑" in corpus


def test_unmatched_genre_falls_back_to_neutral_corpus_only() -> None:
    """兜底只能是题材中立语料（generic.yaml 的注释本身就记着这段历史：
    判官侧早就改用它了，物料注入侧却一直没改）。"""

    corpus = _reference_corpora("no-such-pack", ("no-such-category",))
    assert "Genre-NEUTRAL" in corpus
    # 中立语料自己的注释会提到那段历史，但正文样本不得是某个具体题材的
    body = corpus.split("#")[-1]
    for token in ("驱魔", "尸"):
        assert token not in body, token
