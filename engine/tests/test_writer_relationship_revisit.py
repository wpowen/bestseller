"""Observe relationship continuity at the normal prose writer's request boundary."""

from copy import deepcopy

import pytest

from bookengine.relationship_revisit import GUIDANCE
from bookengine.storage import save
from support_minimal_b import CaptureClient, MinimalBTests as WriterFixture
from test_chapter_rhythm39 import chapter


class DraftCaptured(Exception):
    pass


@pytest.mark.parametrize("grounded", [True, False])
def test_relationship_revisit_reaches_the_packed_writer_request(grounded):
    fixture = WriterFixture()
    fixture.setUp()
    try:
        outline = chapter(2, "情感")
        outline["relationship_change"]["before"] = "两人对旧仓的蓝封条仍有分歧"
        choice = "主角承诺只替同伴守这一次门，明日起各自担责"
        outline["scenes"][-1].update(obstacle="守门意味着今天就要留名", choice=choice, result="两人在册上并排签名")
        fact = "同伴把仓门换封的日期告诉主角"
        exchange = ["同伴指着纸背说：“仓门上只有蓝封条。”", "“盖印的人呢？”", "“没看清。”"]
        quote = "同伴把换封的日期写给主角。"
        body = "\n\n".join(["主角接过纸包。", *exchange, quote])
        save(fixture.out / "chapter-001.body.md", body)
        event = dict(chapter=1, summary=fact, quote=quote if grounded else "没有写进正文的一句话")
        state = dict(continuity_ledger=dict(critical_events=[event]))
        rows = [
            dict(
                number=1, summary="主角和同伴查过蓝封条，盖印的人还未查到", status="complete", fact_source="final_body"
            )
        ]
        before = deepcopy((outline, state, rows))
        client = CaptureClient(fixture.out, [], {"r2-ch2-draft": DraftCaptured()})
        with pytest.raises(DraftCaptured):
            fixture.run_chapter(client, state, rows, outline)
        assert [call["stage"] for call in client.calls] == ["r2-ch2-draft"]
        prompt = client.calls[0]["prompt"]
        if grounded:
            assert prompt.index(choice) < prompt.index(fact) < prompt.index(GUIDANCE) < 300
            assert prompt.count(GUIDANCE) == 1
            assert "上次交谈的原话\n" + "\n".join(exchange) in prompt
            assert prompt.index(exchange[-1]) < prompt.index(outline["relationship_change"]["before"])
            assert len(prompt) < client.context_limit
        else:
            assert GUIDANCE not in prompt and "上次交谈的原话" not in prompt
            assert "本章主场景硬合同（优先于文风与局部措辞" in prompt
        assert (outline, state, rows) == before
        assert (fixture.out / "chapter-001.body.md").read_text() == body
    finally:
        fixture.doCleanups()
