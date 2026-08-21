"""书名淘汰赛回执必须真的落库（2026-08-22 真机 custom-xuanhuan-1787328262）。

首跑验证：淘汰赛确实跑了（llm_runs 里 title_tournament_candidates +
title_tournament_arena 各 1 次），书名也确实换成了《书院笔仙》——
但我写进 `writing_profile.market["title_tournament"]` 的**回执没活下来**。

原因：`writing_profile` 落库时走 pydantic 模型，额外键被 extra=ignore 吃掉
（同族先例见 memory empty-castspec-envelope-and-fragment-hijack：
`{"cast_spec":{...}}` 信封被 extra=ignore 吃掉）。既有代码写在同一位置的
`title_workflow_primary` 同样不见了——不是我引入的，是这条路本来就不落库。

照 `motif_amplification` 的先例修：它的注释写着「advisory……但**结论必须落库
可查，否则等于没检测**」。回执改走 ConceptionResult 字段 + web 层 artifacts。
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from bestseller.services.conception import ConceptionResult

pytestmark = pytest.mark.unit


def test_result_carries_the_receipt_as_a_first_class_field():
    names = {f.name for f in dataclasses.fields(ConceptionResult)}
    assert "title_tournament" in names, (
        "回执必须是 ConceptionResult 的字段——塞进 writing_profile 会被 extra=ignore 吃掉"
    )


def test_receipt_defaults_to_empty_not_none():
    result = ConceptionResult(
        writing_profile={}, premise="", title="", conception_log=[], llm_run_ids=[]
    )
    assert result.title_tournament == {}


def test_pipeline_populates_the_field():
    from bestseller.services import conception

    src = inspect.getsource(conception.run_conception_pipeline)
    assert "title_tournament=" in src or "_tt_receipt" in src


def test_web_layer_persists_it_as_a_book_artifact():
    from bestseller.web import server

    src = inspect.getsource(server)
    assert 'conception_artifacts["title_tournament"]' in src, (
        "必须像 motif_amplification 一样落成可查的书籍产物"
    )
