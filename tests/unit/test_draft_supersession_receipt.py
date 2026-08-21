"""稿件更替留痕（2026-08-20 真机《罚我守坟》定罪）。

全库 **518 个 chapter_draft_versions**：`promotion_state` 100% 停在
`candidate`（连 is_current=true 的那些也是），`promotion_score` /
`promotion_reason_codes` / `promoted_at` **一个都没写过**，
`draft_promotion_decisions` 表 **0 行**。
`services/draft_promotion.py` 那套按分选优（select_best_eligible_draft /
quarantine / 决策表）在生产里从未跑过。

真正决定「哪一稿上线」的是 `is_current`：四处构造点各自
「先把当前那版翻 False，再插一条新的」，其中两处其实**是带条件的**
（`not _keeps_prior_draft`、`not quality_gate_rejected_current_promotion`），
但那个条件的结果没有任何地方记录。

后果：真机 ch20 的五个版本 AI 味分数 84/88/**32**/**68(current)**/96，
手上有 32 分的干净稿而上线 68 分的——而**没有一行记录说明为什么**。
「算出了更好的稿却发布了更差的」在记忆里已定罪三次，每次都难归因，
就是因为这个记账真空。

本次只补记账，不改任何选优口径（没有量具之前不改口径，是今天反复吃过的
教训）。写进的是此前 100% 空置的 promotion_reason_codes，纯增量。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services.draft_promotion import draft_supersession_codes

pytestmark = pytest.mark.unit


def test_codes_record_origin_and_whether_it_took_current():
    codes = draft_supersession_codes(
        origin="chapter_first",
        took_current=True,
        chars=2768,
        supersedes_version=3,
    )
    assert "origin:chapter_first" in codes
    assert "wrote_as_current:yes" in codes
    # chars 已删（会过期的副本，见文末测试）
    assert "supersedes:v3" in codes


def test_withheld_draft_records_the_reason():
    codes = draft_supersession_codes(
        origin="rewrite",
        took_current=False,
        chars=2055,
        supersedes_version=2,
        hold_reason="gate_rejected",
    )
    assert "wrote_as_current:no" in codes
    assert "hold:gate_rejected" in codes


def test_codes_are_plain_strings():
    """promotion_reason_codes 既有消费方按字符串码读，不得塞结构化对象。"""
    codes = draft_supersession_codes(origin="revision", took_current=True, chars=1)
    assert all(isinstance(c, str) for c in codes)


@pytest.mark.parametrize(
    "module_name, needle",
    [
        ("bestseller.services.drafts", "draft_supersession_codes"),
        ("bestseller.services.reviews", "draft_supersession_codes"),
        ("bestseller.services.chapter_revision", "draft_supersession_codes"),
    ],
)
def test_every_construction_site_is_wired(module_name: str, needle: str):
    """四处构造点都要接线——只接一处正是今天反复撞的元病。"""
    import importlib

    module = importlib.import_module(module_name)
    assert needle in inspect.getsource(module)


def test_drafts_wires_both_of_its_sites():
    from bestseller.services import drafts

    assert inspect.getsource(drafts).count("draft_supersession_codes(") == 2


# ── 回执记的是「写入时的意图」，不是「最终是否上线」───────────────────
# 2026-08-22 真机《书院笔仙》ch15 v2 的回执写着 took_current:no，
# 而它**就是当前稿**——因为 took_current 取自构造时的
# `not quality_gate_rejected_current_promotion`，这一版后来通过别的路径
# 成了当前稿。用一个字段冒充两件事，会让以后的归因读错。
# 最终状态由 chapter_draft_versions.is_current 表达，回执只负责记意图，
# 字段名必须说清楚这一点。


def test_receipt_field_name_says_it_is_write_time_intent():
    codes = draft_supersession_codes(origin="rewrite", took_current=False, chars=10)
    assert any(c.startswith("wrote_as_current:") for c in codes), (
        "字段名要写明这是写入时的意图，不能叫 took_current 让人以为是最终结果"
    )
    assert not any(c.startswith("took_current:") for c in codes)


def test_intent_is_still_recorded_both_ways():
    yes = draft_supersession_codes(origin="chapter_first", took_current=True, chars=10)
    no = draft_supersession_codes(origin="rewrite", took_current=False, chars=10)
    assert "wrote_as_current:yes" in yes
    assert "wrote_as_current:no" in no


# ── chars 是会过期的副本，删掉（2026-08-22 真机《书院笔仙》）──────────────
# 回执记的 chars 与行里实际内容长度对不上 24/111 次。原因：章级稿在创建之后
# 有 6 条路径原地改 content_md（micro-trim / 钩子桥接 / deslop / AI味补丁 /
# 复检补丁 / 终局补丁），而回执是创建那一刻算的，之后没人更新它。
#
# 这与本回执的第一个缺陷（took_current 用一个字段冒充意图与结果）同源：
# **同一事实住两地，后写的赢**。行的真实长度永远可以 length(content_md) 查到，
# 一份会过期的副本比没有更糟。


def test_receipt_does_not_duplicate_derivable_length():
    codes = draft_supersession_codes(origin="rewrite", took_current=True, chars=3062)
    assert not any(c.startswith("chars:") for c in codes), (
        "chars 复制了 length(content_md)，会在事后原地改写时过期"
    )


def test_receipt_still_records_what_is_not_derivable():
    codes = draft_supersession_codes(
        origin="rewrite", took_current=False, chars=0,
        supersedes_version=2, hold_reason="quality_gate_rejected",
    )
    assert "origin:rewrite" in codes
    assert "wrote_as_current:no" in codes
    assert "supersedes:v2" in codes
    assert "hold:quality_gate_rejected" in codes
