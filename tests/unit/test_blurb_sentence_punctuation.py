# ruff: noqa: RUF001 — Chinese and ASCII question marks are test inputs.

import pytest

from bestseller.services.blurb_appeal_gate import (
    _first_sentence,
    _last_sentence,
    _score_hook_strength,
    _score_open_loop_end,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("punctuation", ["？", "?"])
def test_real_question_remains_available_to_hook_and_ending_scorers(punctuation):
    text = f"他还能护这孩子多远{punctuation}"
    assert _first_sentence(text).endswith(punctuation)
    assert _last_sentence(text).endswith(punctuation)
    assert _score_hook_strength(text, {})[0] == 4.4
    assert _score_open_loop_end(text, {})[0] == 5.0


def test_prior_question_does_not_turn_declarative_ending_into_question():
    text = "他走向哪里？他已经回到家中。"
    assert _last_sentence(text) == "他已经回到家中。"
    assert _score_open_loop_end(text, {})[0] == 3.0


def test_closing_dialogue_quote_is_not_a_separate_last_sentence():
    text = "他举起火把。\n“你跟得上吗？”"
    assert _last_sentence(text) == "“你跟得上吗？”"
    assert _score_open_loop_end(text, {})[0] == 5.0
