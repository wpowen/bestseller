"""被清洗的那份不是出货的那份——读者承诺的招牌元病。

2026-08-24 真机（custom-xuanhuan-1787543232）：

    commercial_brief.reader_promise
      「一个连正经丹炉都没摸过的小学徒，靠捡别人扔掉的药渣，一题一题扛住
        老狐狸的刁难……」                                    ← 干净，被去行业词通道洗过
    metadata.reader_promise（**出货的那份**）
      「前几章给出主角的废料成丹天赋、第一道巡检怪题、第一次当面回怼长老的
        **爽点**……」                                        ← 脏，打分 14.0
    两者相同 = false

顺序是致命的：conception.py:6433 先把 commercial_brief 洗干净，7225 行再用
未清洗的 concept_bundle 覆盖 market_profile。**清洗在前，覆盖在后。**

`爽点` 一直在 trade_jargon 词表里 —— 检测器没瞎，是它量的那份不是发出去的那份。
这是本仓库反复复发的「同一事实住两地，后写的赢」。

修法用比较式而不是清单式：候选里挑**分数最低**的那一份，分数相同保持原顺序。
不发明文案，只在已有候选里选干净的。
"""

from bestseller.services.conception import prefer_cleaner_reader_copy

_DIRTY = "前几章给出主角的废料成丹天赋、第一道巡检怪题、第一次当面回怼长老的爽点"
_CLEAN = "一个连正经丹炉都没摸过的小学徒，靠捡别人扔掉的药渣，一题一题扛住老狐狸的刁难"
_ALSO_DIRTY = "前五章让读者看到主角的越阶爽感"


def test_the_clean_candidate_wins_regardless_of_order() -> None:
    assert prefer_cleaner_reader_copy([_DIRTY, _CLEAN]) == _CLEAN
    assert prefer_cleaner_reader_copy([_CLEAN, _DIRTY]) == _CLEAN


def test_ties_keep_the_first_candidate() -> None:
    """都干净时保持原有优先级，不许无谓改变既有行为。"""

    a, b = "少年握剑站在山门前", "风从山门吹过来"
    assert prefer_cleaner_reader_copy([a, b]) == a


def test_all_dirty_picks_the_least_bad() -> None:
    """全脏时挑分数最低的——不许因为都不干净就放弃选择。"""

    got = prefer_cleaner_reader_copy([_DIRTY, _ALSO_DIRTY])
    from bestseller.services.copy_flavor import detect_copy_flavor
    assert detect_copy_flavor(got).score == min(
        detect_copy_flavor(_DIRTY).score, detect_copy_flavor(_ALSO_DIRTY).score
    )


def test_empty_and_blank_candidates_are_skipped() -> None:
    assert prefer_cleaner_reader_copy(["", "   ", _CLEAN]) == _CLEAN
    assert prefer_cleaner_reader_copy([None, _CLEAN]) == _CLEAN


def test_no_candidates_returns_empty() -> None:
    assert prefer_cleaner_reader_copy([]) == ""
    assert prefer_cleaner_reader_copy([None, ""]) == ""


def test_the_overwrite_site_uses_it() -> None:
    """7225 那处覆盖必须走这个选择器，否则清洗照旧白做。"""

    from pathlib import Path

    import bestseller.services.conception as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    idx = src.find('market_profile["reader_promise"] = ')
    assert idx > 0
    assert "prefer_cleaner_reader_copy" in src[idx : idx + 320], src[idx : idx + 200]


# ── 2026-08-24 F 验收书 custom-xuanhuan-1787576409 抓到我自己的漏 ──
# 上一版把选择器挂在 `if concept_bundle is not None:` 分支里。那本书
# `concept_lab=false` → concept_bundle 为 None → **整条修复被绕过**：
#
#     commercial_brief.reader_promise（干净）
#       「看一个扫厕所的杂役怎么靠别人眼里的废纸浆，把自己泡成仙门外门弟子……」
#     metadata.reader_promise（出货，脏）
#       「前几章给出临时杂役身份、废符化浆入口、月底销毁日倒计时……」  → 6.0
#
# 我今天在别处的提交信息里写过「只挂一条分支等于给自己留一条绕行路」，
# 然后自己犯了。选择必须落在**所有分支的汇合点**——ConceptionResult 构造前。


def test_the_selection_sits_at_the_join_point_not_in_one_branch() -> None:
    """选择必须在 ConceptionResult 构造之前做，不能只在 concept_bundle 分支里。"""

    from pathlib import Path

    import bestseller.services.conception as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tail = src.split("    return ConceptionResult(", 1)[0]
    # 汇合点：最后一次出现选择器的位置，必须在 concept_bundle 分支之外
    idx_join = tail.rfind("prefer_cleaner_reader_copy")
    assert idx_join > 0, "构造前没有做统一选择"
    idx_bundle = tail.rfind("if concept_bundle is not None:")
    bundle_block_end = tail.find("\n    ", idx_bundle) if idx_bundle > 0 else -1
    assert not (0 < idx_join < bundle_block_end), "选择仍然只在 concept_bundle 分支里"
