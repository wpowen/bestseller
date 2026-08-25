"""身份分裂：检查清单里没有散文产物（2026-08-25 真机 custom-xuanhuan-1787625194）。

那本书出厂时主角有两个名字：

  身份骨架（book_spec / cast_spec / identity_manifest / 快照 / logline）  阿灶  231 次
  读者可见（premise / synopsis / 上架简介 / world_spec.factions）        姜燎   34 次

正文里两个名字同时在场，且**第 3 章起叙述从「阿灶」改口成「姜燎」再没改回**
（1–2 章 阿灶 26/53 次，3–11 章 姜燎 18–28 次）——第 3 章正是决定追读的位置。

两处根因：

R1 一致性检查只遍历**结构化身份产物**（story_spine / concept_contract.story_spine /
   hook_card / concept_contract.hook_card / identity_manifest）。真机上这五个全是
   「阿灶」、与 expected 一致 → ``issues: []`` **零检出**。premise / synopsis
   不在清单里，而「姜燎」只住在那儿。

R1' ``_identity_mismatch_is_advisory`` 把三份构思正文**拼成一个 blob** 判
   `canonical in blob`。logline 含「阿灶」即命中 → 判 advisory。拼接粒度看得见
   「0/3」，看不见「1/3 vs 2/3」。

R1'' ``advisory_codes`` 此前**无条件**计算，与 ``issues: []`` 同时出现，
   读起来像「抓到了但放行」，实际是「压根没抓到」。
"""

from __future__ import annotations

import pytest

from bestseller.services.book_design import _identity_mismatch_is_advisory

pytestmark = pytest.mark.unit

# 真机原文（截断到判定所需长度）
_LOGLINE = "阿灶只闻了三下锅里忽大忽小的灵火，便把半勺米醋换成井水。"
_PREMISE = "姜燎十九岁，被青雀酒楼以「偷师厨心方」逐出灶口，在街角支起一口黑锅。"
_SYNOPSIS = "黑锅、废料、一袋过期的灶底料，姜燎被逐出师门的全部家当，就这三样。"


def _real_book_meta(canonical: str = "阿灶") -> dict:
    return {
        "logline": _LOGLINE,
        "premise": _PREMISE,
        "synopsis": _SYNOPSIS,
        "creation_protagonist_name": canonical,
        "creation_protagonist_source": "llm_premise_identity_resolution",
    }


def test_the_real_book_is_now_judged_a_split_not_a_rendering_variant():
    """规范名只占 1/3，另一个名字占 2/3 → 分裂，不得 advisory。"""
    assert _identity_mismatch_is_advisory(_real_book_meta()) is False


def test_vacuity_the_old_blob_criterion_would_have_passed_this_book():
    """空转检验：还原「拼成 blob 任意命中」，确认它确实放行了这本书。"""
    blob = " ".join([_LOGLINE, _PREMISE, _SYNOPSIS])
    assert "阿灶" in blob, "旧判据靠 logline 命中放行——本用例正是为它写的"


def test_a_name_owning_the_majority_stays_advisory():
    """规范名占多数 → 写法差异，维持 advisory（不制造新的停产）。"""
    meta = _real_book_meta("姜燎")
    assert _identity_mismatch_is_advisory(meta) is True


def test_artifacts_that_name_nobody_are_not_counted_as_dissent():
    """没提任何人名的产物不该被算作反对票，否则会误判成分裂。"""
    meta = {
        "logline": _LOGLINE,
        "premise": "他在坠龙渊底摆馄饨摊三十年，灶下压着一头没死透的母龙。",
        "synopsis": "",
        "creation_protagonist_name": "阿灶",
        "creation_protagonist_source": "llm_premise_identity_resolution",
    }
    # 非空产物 2 份，规范名占 1 份 = 半数 → 仍算写法差异，不停产。
    assert _identity_mismatch_is_advisory(meta) is True


def test_the_prose_artifacts_now_produce_an_issue():
    """R1：散文产物必须真的进 issue 列表——检不出等于没有这道门。"""
    from bestseller.services.book_design import _canonical_name_coverage

    present, populated = _canonical_name_coverage(_real_book_meta(), "阿灶")
    assert (present, populated) == (1, 3)
    assert present * 2 < populated, "1/3 应判分裂"


def test_advisory_code_is_not_emitted_without_a_matching_issue():
    """R1''：advisory_codes 不再是「无条件策略标志」，否则回执会误导排查。"""
    import inspect

    from bestseller.services import book_design

    src = inspect.getsource(book_design)
    assert "_has_identity_issue" in src
    assert "_has_identity_issue and _identity_mismatch_is_advisory" in src
