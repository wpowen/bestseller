"""Copy de-flavouring in the conception pipeline.

The schema rewrite fixed most of the register problem, but a schema is advice.
When the surrounding context is full of 追读/爽点 the model still reaches for
them, so the copy is read back and re-asked once. The interesting property is
not that a rewrite happens — it is what happens when the rewrite is *not*
better.
"""

from __future__ import annotations

from typing import Any

import pytest

from bestseller.services import conception
from bestseller.services.conception import (
    _brief_copy_flavour,
    _build_commercial_fallback,
    _rewrite_flavoured_copy,
)
from bestseller.services.copy_flavor import detect_copy_flavor

pytestmark = pytest.mark.unit


_FLAVOURED = {
    "reader_promise": "每章有人倒下、每章有新本事当场就用，下一个被点名的是谁永远吊着你的追读。",
    "selling_points": ["极简代价掠夺式成长，爽点兑现零延迟", "对手池同步升档"],
    "opening_strategy": "开篇必须亮出主角优势和即时危险。",
}

_CLEAN_REWRITE = {
    "reader_promise": "他每赢一场，对手练了半辈子的功夫就成了他的。输的人越强，他学得越快。",
    "selling_points": ["打赢就抢走对方的本事", "越打越强，从不倒退"],
}


class TestScoring:
    def test_only_reader_facing_fields_are_scored(self) -> None:
        """``opening_strategy`` is an instruction to the writer by design.

        Scrubbing it would break the generator to tidy up something no reader
        ever sees.
        """

        score, _ = _brief_copy_flavour({"opening_strategy": _FLAVOURED["opening_strategy"]})
        assert score == 0.0

    def test_flagged_copy_scores_above_zero_with_evidence(self) -> None:
        score, evidence = _brief_copy_flavour(_FLAVOURED)
        assert score > 0
        assert evidence, "无证据的判定无法核实"

    def test_clean_copy_scores_zero(self) -> None:
        score, _ = _brief_copy_flavour(_CLEAN_REWRITE)
        assert score == 0.0


class _StubCall:
    """Stands in for ``_llm_call_json``; records whether it was called."""

    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply
        self.calls = 0

    async def __call__(self, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], list[Any]]:
        self.calls += 1
        return self.reply, []


class TestRewriteLoop:
    @pytest.mark.asyncio
    async def test_clean_copy_costs_no_llm_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _StubCall(_CLEAN_REWRITE)
        monkeypatch.setattr(conception, "_llm_call_json", stub)

        result, run_ids = await _rewrite_flavoured_copy(
            None, None, brief=dict(_CLEAN_REWRITE), is_en=False, language="zh-CN"
        )

        assert stub.calls == 0
        assert result == _CLEAN_REWRITE
        assert run_ids == []

    @pytest.mark.asyncio
    async def test_a_cleaner_rewrite_is_adopted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _StubCall(_CLEAN_REWRITE)
        monkeypatch.setattr(conception, "_llm_call_json", stub)

        result, _ = await _rewrite_flavoured_copy(
            None, None, brief=dict(_FLAVOURED), is_en=False, language="zh-CN"
        )

        assert stub.calls == 1
        assert detect_copy_flavor(result["reader_promise"]).clean
        assert _brief_copy_flavour(result)[0] < _brief_copy_flavour(_FLAVOURED)[0]

    @pytest.mark.asyncio
    async def test_non_reader_facing_fields_survive_the_rewrite(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(conception, "_llm_call_json", _StubCall(_CLEAN_REWRITE))

        result, _ = await _rewrite_flavoured_copy(
            None, None, brief=dict(_FLAVOURED), is_en=False, language="zh-CN"
        )

        assert result["opening_strategy"] == _FLAVOURED["opening_strategy"]

    @pytest.mark.asyncio
    async def test_a_rewrite_that_is_no_better_is_discarded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The defect this guards against has shipped before.

        A repair loop that adopts its last attempt regardless of score is how a
        book gets published from its worst draft. Here the "rewrite" is worse
        than the input, so the input must survive untouched.
        """

        worse = {
            "reader_promise": "每章一个爽点，每卷一次升级，追读留存拉满，开篇必须亮出金手指。",
            "selling_points": ["极简代价", "节奏拉满"],
        }
        monkeypatch.setattr(conception, "_llm_call_json", _StubCall(worse))

        result, _ = await _rewrite_flavoured_copy(
            None, None, brief=dict(_FLAVOURED), is_en=False, language="zh-CN"
        )

        assert result == _FLAVOURED

    @pytest.mark.asyncio
    async def test_a_malformed_reply_keeps_the_original(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(conception, "_llm_call_json", _StubCall("not a dict"))  # type: ignore[arg-type]

        result, _ = await _rewrite_flavoured_copy(
            None, None, brief=dict(_FLAVOURED), is_en=False, language="zh-CN"
        )

        assert result == _FLAVOURED


class TestFallbackCopy:
    def test_the_hand_written_fallback_is_not_trade_jargon(self) -> None:
        """It used to read 「以{genre}核心爽点提供稳定追读回报。」

        When the model failed, the framework wrote the worst copy in the book
        by hand — and that string reached the listing.
        """

        brief = _build_commercial_fallback({"genre": "东方玄幻", "language": "zh-CN"})

        assert detect_copy_flavor(brief["reader_promise"]).clean

    def test_a_preset_directive_does_not_become_the_promise(self) -> None:
        brief = _build_commercial_fallback(
            {
                "genre": "东方玄幻",
                "language": "zh-CN",
                "existing_overrides": {
                    "market": {
                        "reader_promise": (
                            "开篇快速亮出主角差异化优势、当前利益、即时危险和连载钩子，"
                            "持续维持强追读。"
                        )
                    }
                },
            }
        )

        assert detect_copy_flavor(brief["reader_promise"]).clean

    def test_a_genuinely_reader_facing_override_is_still_honoured(self) -> None:
        """A filter, not a blanket ban: clean overrides must still win."""

        promise = "他捡到一块会说话的石头，石头只肯替他杀人。"
        brief = _build_commercial_fallback(
            {
                "genre": "东方玄幻",
                "language": "zh-CN",
                "existing_overrides": {"market": {"reader_promise": promise}},
            }
        )

        assert brief["reader_promise"] == promise
