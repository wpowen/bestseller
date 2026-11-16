"""Instruction 69: a de-AI replacement must not break quotes or repeat the text around its span.

Cases come from 《武馆新客·挨打成钢》 (books/webui/6c18dfd5-…), chapters 14 and 33.
"""

from deai.adoption import spill_reasons


def _hit(text, sentence):
    start = text.index(sentence)
    return dict(start=start, end=start + len(sentence), text=sentence)


def test_dropped_closing_quote_is_rejected():
    text = "石子翎继续道：“你怕失控，我更怕。可你忘了一件事——我也怕你不问。”\n\n苏文栩没有说话。"
    hit = _hit(text, "可你忘了一件事——我也怕你不问。”")
    assert spill_reasons(text, hit, "可你忘了一件事，我也怕你不问。") == ["quote_balance"]
    assert spill_reasons(text, hit, "可你忘了一件事，我也怕你不问。”") == []


def test_replacement_that_swallows_the_next_sentence_is_rejected():
    text = "副手笑道：“裴大人早知道总有人来取册，故留了新的阵。砸不死你，也会把你同伴埋在这里。”\n\n“苏姑娘！”"
    hit = _hit(text, "副手笑道：“裴大人早知道总有人来取册，故留了新的阵。")
    spilled = "副手笑道：“裴大人早知道有人来取册，留了新的阵。砸不死你，也会把你同伴埋在这里。”"
    assert set(spill_reasons(text, hit, spilled)) == {"quote_balance", "repeats_following_text"}
    assert spill_reasons(text, hit, "副手笑道：“裴大人早知道有人来取册，留了新的阵。") == []


def test_repeating_the_preceding_text_is_rejected():
    text = "他推门进去。屋里很暗，灯芯只剩一点红。"
    hit = _hit(text, "屋里很暗，灯芯只剩一点红。")
    assert spill_reasons(text, hit, "他推门进去。屋里很暗。") == ["repeats_preceding_text"]


def test_short_shared_punctuation_is_not_a_spill():
    text = "他笑了。“走吧。”她说。"
    hit = _hit(text, "他笑了。")
    assert spill_reasons(text, hit, "他咧嘴一笑。") == []
