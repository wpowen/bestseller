"""Source-boundary regressions for Q3's local evaluation script."""

from pathlib import Path
import runpy

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "reports/parallel-2026-10-28/Q3/complete_generalization.py"
)


@pytest.fixture
def helpers():
    return runpy.run_path(str(SCRIPT))


def test_legacy_keeps_ordered_prose_and_dialogue_not_choices_or_hook(helpers):
    data = {
        "number": 1,
        "next_chapter_hook": "metadata",
        "nodes": [
            {"text": {"content": "雨停了。"}},
            {"dialogue": {"character_id": "p_1", "content": "走吧。"}},
            {"choice": {"prompt": "选项不是正文", "choices": []}},
            {"text": {"content": "他推开门。"}},
        ],
    }
    text, turns = helpers["legacy_body"](data)
    assert text == "雨停了。\n走吧。\n他推开门。"
    assert [(t.speaker, t.text) for t in turns] == [("p_1", "走吧。")]


def test_bad_legacy_text_is_rejected_not_stringified(helpers):
    with pytest.raises(ValueError):
        helpers["legacy_body"]({"nodes": [{"text": {"content": {"bad": 1}}}]})


def test_random_twenty_are_distinct_and_exclude_first_twenty(helpers):
    files = [Path(f"ch{i:04}.json") for i in range(1, 1201)]
    chosen = helpers["select_legacy"](files)
    assert chosen[:20] == files[:20]
    assert len(chosen) == len(set(chosen)) == 40
    assert not set(chosen[20:]) & set(chosen[:20])
    assert chosen == helpers["select_legacy"](list(reversed(files)))


def test_do_not_silently_shrink_requested_corpus(helpers):
    with pytest.raises(ValueError):
        helpers["select_legacy"]([Path(f"ch{i:04}.json") for i in range(1, 31)])


def test_precision_deduplicates_units_and_rejects_invalid_extraction():
    summarize = runpy.run_path(str(SCRIPT.with_name("summarize_editorial.py")))
    records = [
        {"unit_id": "a", "extraction_correct": True, "editorial_defect": True},
        {"unit_id": "a", "extraction_correct": True, "editorial_defect": True},
        {"unit_id": "b", "extraction_correct": False, "editorial_defect": True},
        {"unit_id": None, "extraction_correct": None, "editorial_defect": True},
    ]
    result = summarize["review_precision"](records)
    assert result["reviewed_units"] == 2
    assert result["editorial_true_positives"] == 1
    assert result["editorial_precision"] == 0.5


def test_zero_alerts_are_unknown_not_perfect_precision():
    summarize = runpy.run_path(str(SCRIPT.with_name("summarize_editorial.py")))
    assert summarize["review_precision"]([])["editorial_precision"] is None


def test_conflicting_duplicate_labels_are_not_silently_counted():
    summarize = runpy.run_path(str(SCRIPT.with_name("summarize_editorial.py")))
    records = [
        {"unit_id": "a", "extraction_correct": True, "editorial_defect": True},
        {"unit_id": "a", "extraction_correct": True, "editorial_defect": False},
    ]
    with pytest.raises(ValueError):
        summarize["review_precision"](records)
