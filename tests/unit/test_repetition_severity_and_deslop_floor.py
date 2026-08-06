"""The padding defended itself.

Both fixtures below are verbatim from the 50-chapter book
custom-xuanhuan-1785980083, and the contrast is the point: same model, same
prompt stack, same book.

* ch11 measured 0 repeated phrases. It uses parallelism deliberately
  (「滑到掌心，滑到掌根，滑到中指指尖」) and reads fine.
* ch16 measured 198 phrases each repeated 5+ times, the top one 75 times, with
  47.6% of all its 4-grams inside a heavy repeat. Every sentence is the same
  template with new nouns. It shipped anyway.

Two independent defects let ch16 through:

1. ``_detect_repetition`` emitted one fixed ``warn`` no matter the magnitude —
   4 repeated phrases and 198 both scored 4.0 out of 100. A binary measurement
   for a defect that is purely a matter of degree.

2. ``deslop_revise`` refused any rewrite shorter than ``len(draft) * 0.6``.
   De-slopping a chapter that got long by repeating one beat necessarily makes
   it shorter, so the fix was rejected for being the fix, and the caller then
   ``break``s out of every remaining round. The more a chapter padded, the more
   padding it was required to keep.
"""

from __future__ import annotations

import pytest

from bestseller.services.ai_flavor.detector import _detect_repetition
from bestseller.services.deslop_revise import _deslop_length_floor

pytestmark = pytest.mark.unit


# ch16, verbatim. One template, new nouns, forever.
_LOCKED_FORMULA = '里头那一片——朝柳回那一片——斜了一寸。\n\n刀出鞘。\n\n柳回那只右脚从柴房中间那一片挪到柴房西墙那一片。挪过去那一下他自己那只左手在自己右腕子上终于按不住——按不住那一下他自己右手掌心底下那一点气从他右腕子上往右掌根底下走了一寸，走到右掌根底下那一下他自己右手掌心立刻被那一点气烫出一片红。\n\n是行气法。\n\n行气法那一层底子在他右掌根底下那一点气走到右掌根底下那一下终于和他自己右手掌心底下那一片皮肉接上——接上那一下他自己右手掌心从掌根到指尖那一条线都跟着热了一下。\n\n林驹那只右手终于把刀从刀鞘里拔出三寸。拔出三寸那一下刀刃上那一片寒光正好照在管事自己脸上。\n\n管事那只右脚在柴房门外那一片又顿了一下。\n\n林驹那只左脚终于跨进柴房。跨进柴房那一下刀从刀鞘里又拔出三寸——六寸。柳回那只右手终于从自己右腕子上挪到右掌根底下。挪到右掌根底下那一下他右手掌心底下那一点气从他右掌根底往右掌心底下走了一寸——走到右掌心底下那一下他右手掌心底下那一片皮肉都跟着烫了一下。\n\n林驹那只右手从刀柄上挪到刀刃上。挪到刀刃上那一下他自己左手终于从刀鞘口上挪到自己左肋底下——挪到左肋底下那一下他左手已经成掌。\n\n“周乙，”柳回又开口，声音从压平变成压低，“你那只左手还没摸到刀柄。”\n\n柳回那只右手忽然从右掌根底下往右掌心底下一翻——一翻那一瞬他自己右手掌心底下那一点气从他右掌心底往右掌指尖底下走了一寸，走到右掌指尖底下那一下他右手掌心底下那一片皮肉都跟着烫了一下。\n\n林驹那只右手从刀刃上挪到刀背。挪到刀背那一下刀从刀鞘里又拔出三寸——九寸。\n\n柳回那只右脚忽然从柴房西墙那一片往柴房中间那一片一踏——一踏那一下他自己右脚脚底下那一片青砖被他自己右脚踩出一声很轻的“咔”。\n\n“开山掌。”柳回开口，声音不高，被他自己压得很平。\n\n林驹那只左手终于从自己左肋底下往柳回那一片一推——一推那一瞬他自己左手掌心底下那一点气从他左掌心底往左掌指尖底下走了一寸，走到左掌指尖底下那一下他左手掌心底下那一片皮肉都跟着烫了一下。\n\n是马六的开山掌。\n\n柳回那只左手终于从自己右腕子上挪到自己左掌根底下。挪到左掌根底下那一下他左手掌根底下那一点气从他左掌根底往左掌心底下走了一寸——走到左掌心底下那一下他自己左手掌心底下那一片皮肉都跟着烫了一下。\n\n左掌根底下那一点气走得比右掌根底下那一点气慢了半寸。\n\n两路气并行。\n\n左掌根底下那一点气是开山掌——右掌根底下那一点气是行气法。\n\n林驹那只左手一推推到柳回那只左掌根底下。推到柳回左掌根底下那一下他左手掌心底下那一点气终于和柳回左掌根底下那一点气撞上——撞上那一下他自己左手掌心底下那一片皮肉都跟着烫了一下。\n\n行气法切出去那一点气终于打到林驹那只左肋底下。打到林驹左肋底下那一下林驹那只左手终于从柳回左掌根底下挪到自己左肋底下——挪到左肋底下那一下他左手掌心底下那一点气已'

# ch11, verbatim. Repetition here is rhetoric, not a locked template.
_DELIBERATE_PARALLELISM = (
    "它顺着经脉往右肩走，走过肩头没事，走过肘弯也没事。走到手腕那一刻，"
    "腕骨底下那道青紫处像有人拿针从里头戳出来，戳得他整条胳膊一颤。那根细线当场断了。\n\n"
    "他睁开眼，吐出一口气。气断了，丹田里那点热还在，没散，但也没有往前走一步。"
    "他咬住右手腕伤处的皮肉，牙关收紧。重新走。\n\n"
    "第二次，还是在腕骨处断。断的那一下他右掌五指不受控制地弹了一下，"
    "弹得柴房里那堆干稻草被掌风带得轻响。第三次、第四次，每一次断的位置都一样，"
    "断的时候腕上的疼比上一次更往骨头里钻。\n\n"
    "他不让自己停。\n\n"
    "马六跪在地上抖手那一幕在眼前一遍遍回放——马六的手摊在自己眼前，抖得像风吹的树叶。"
    "一个杂役练了半年的入门行气法，就那么凭空从他身体里被抽走。被一个连灵根都没有的废物抽走。\n\n"
    "第五次。\n\n"
    "气走到手腕时，断的地方没断。它顿了一下，那根线绷得笔直。柳回听见腕骨里头咔的一声响，"
    "气从那一点挤过去，挤得极慢，慢得像水从石缝里渗。它过了伤处之后，一路滑到掌心，滑到掌根，滑到中指指尖。\n\n"
    "走完一圈。\n\n"
    "柳回睁开眼，低头看自己的右手。月光底下，那只骨节分明的手掌摊在膝盖上，五指微微张开。"
    "指腹底下有一层极淡的热，热得像刚从火塘边烤完。"
)


def _ngram_span(text: str):
    spans = [s for s in _detect_repetition(text, lang="zh") if "ngram" in s.rule_id]
    return spans[0] if spans else None


class TestSeverityTracksMagnitude:
    def test_the_locked_formula_escalates_to_block(self) -> None:
        span = _ngram_span(_LOCKED_FORMULA)

        assert span is not None
        assert span.severity == "block"

    def test_deliberate_parallelism_is_not_a_block(self) -> None:
        """Same book, same model — this one reads fine and must not be punished."""

        span = _ngram_span(_DELIBERATE_PARALLELISM)

        assert span is None or span.severity == "warn"

    def test_the_verdict_quotes_its_magnitude(self) -> None:
        """A verdict with no number cannot be ranked against another verdict."""

        span = _ngram_span(_LOCKED_FORMULA)

        assert span is not None
        assert "%" in span.why

    def test_severity_separates_the_two_real_chapters(self) -> None:
        """The regression that matters: these two must never grade the same.

        Before this change both produced an identical 4.0-point warn, which is
        why the unreadable one shipped.
        """

        bad = _ngram_span(_LOCKED_FORMULA)
        ok = _ngram_span(_DELIBERATE_PARALLELISM)

        assert bad is not None
        assert ok is None or bad.severity != ok.severity


class TestDeslopLengthFloor:
    def test_a_bloated_draft_may_come_back_to_target(self) -> None:
        """ch16: 5759 chars against a 2600 target.

        The old floor was max(5759*0.6, 2600*0.7) = 3455, so a rewrite that
        removed the 47.6% padding was rejected as "too short".
        """

        floor = _deslop_length_floor(5759, 2600)

        assert floor <= 2600 * 0.7
        assert floor < 5759 * 0.6, "锚在注水稿上，就等于要求保留注水"

    def test_an_on_target_draft_cannot_be_gutted(self) -> None:
        """No padding to give back, so the relative guard still applies."""

        assert _deslop_length_floor(2600, 2600) >= 2600 * 0.6

    def test_an_already_short_draft_is_not_asked_to_grow(self) -> None:
        """It is a floor on the rewrite, never a demand to pad up to contract."""

        assert _deslop_length_floor(1500, 2600) <= 1500

    @pytest.mark.parametrize("current", [1500, 2600, 4000, 5759, 9000])
    def test_the_floor_never_exceeds_the_draft(self, current: int) -> None:
        assert _deslop_length_floor(current, 2600) <= current
