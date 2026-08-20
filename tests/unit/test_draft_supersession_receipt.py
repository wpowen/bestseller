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
    assert "took_current:yes" in codes
    assert "chars:2768" in codes
    assert "supersedes:v3" in codes


def test_withheld_draft_records_the_reason():
    codes = draft_supersession_codes(
        origin="rewrite",
        took_current=False,
        chars=2055,
        supersedes_version=2,
        hold_reason="gate_rejected",
    )
    assert "took_current:no" in codes
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
