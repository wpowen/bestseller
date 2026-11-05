"""INBOX35: pure repetition detection and conservative, lossless deletion."""

import json

import pytest

from bookengine.prose_repetition import clean_repetitions


def records_of(result, kind):
    return [record for record in result["records"] if record["kind"] == kind]


def test_normalized_complete_sentences_report_original_positions_and_counts():
    first = "远处的钟声越过山谷传进营地。"
    second = "远处的钟声，越过山谷传进营地！"
    text = "  " + first + "\r\n\r\n\t" + second + "\r\n\r\n" + first + "\r\n"
    result = clean_repetitions(text, remove_safe=False)
    record = records_of(result, "sentence")[0]
    assert record["count"] == 3
    assert record["repetitions"] == 2
    assert [p["paragraph"] for p in record["occurrences"]] == [1, 2, 3]
    assert [p["line"] for p in record["occurrences"]] == [1, 3, 5]
    assert all(text[p["start"] : p["end"]] == p["text"] for p in record["occurrences"])
    assert result["cleaned"] == text
    assert result["counts"]["sentence_repetitions"] == 2
    assert result["removed"] == []
    json.dumps(result, ensure_ascii=False)


def test_safe_later_repetition_is_deleted_without_reformatting_surroundings():
    sentence = "远处的钟声越过山谷传进营地。"
    prefix = "\t" + sentence + "\r\n\r\n河边的篝火燃成了一片暗红。\r\n\r\n  "
    suffix = "山口的旗帜忽然倒向了东边。\r\n"
    text = prefix + sentence + suffix
    result = clean_repetitions(text)
    assert result["cleaned"] == prefix + suffix
    assert result["removed"] == [{"start": len(prefix), "end": len(prefix + sentence)}]
    assert not result["needs_repair"]
    assert clean_repetitions(result["cleaned"])["cleaned"] == result["cleaned"]


@pytest.mark.parametrize("text", ["", "走吧。\n\n走吧。", "一二三四五六七八九。" * 2, "长句仍然没有句末标点" * 2])
def test_short_or_unfinished_sentences_are_untouched(text):
    result = clean_repetitions(text)
    assert result["cleaned"] == text
    assert not records_of(result, "sentence")


def test_exact_ten_normalized_characters_is_included():
    result = clean_repetitions("一二三四五六七八九十。\n\n一二三四五六七八九十。")
    assert records_of(result, "sentence")[0]["count"] == 2


def test_different_numbers_negation_and_subjects_are_not_duplicates():
    text = (
        "巡逻队从营地带走了１２名伤员。巡逻队从营地带走了13名伤员。"
        "仪表显示室外温度已经降到-10度。仪表显示室外温度已经降到10度。"
        "河边的篝火没有照亮整座营地。河边的篝火照亮整座营地。"
        "张岚从东侧山谷走回了营地。李青从东侧山谷走回了营地。"
    )
    assert not records_of(clean_repetitions(text), "sentence")


def test_nfkc_and_inline_whitespace_are_comparison_only():
    text = "巡逻队从营地带走了１２名伤员。\n\n巡逻队从营地带走了 12 名伤员。"
    assert records_of(clean_repetitions(text, remove_safe=False), "sentence")[0]["count"] == 2


def test_embedded_dialogue_is_a_complete_sentence_but_attribution_is_not_deleted():
    quote = "山谷里的那座旧营地已经空了。"
    text = f"张岚望向山口说：“{quote}”\n\n李青低声回答：“{quote}”"
    result = clean_repetitions(text)
    record = records_of(result, "sentence")[0]
    assert record["count"] == 2
    assert result["cleaned"] == text
    assert record["occurrences"][1]["action"] == "repair"
    assert "dialogue_attribution" in record["occurrences"][1]["reasons"]


@pytest.mark.parametrize(
    "before,repeated,after",
    [
        ("李青转身走向了帐篷。", "张岚从东侧山谷走回了营地。", "他随即拔刀。"),
        ("他正站在门外。", "张岚从东侧山谷走回了营地。", "营地的火堆熄了。"),
        ("营地的火堆熄了。", "他从东侧山谷走回了旧营地。", "张岚抬起头。"),
        ("营地的火堆熄了。", "张岚从东侧山谷走回了营地。", "抬手推开了大门。"),
        ("营地的火堆熄了。", "张岚从东侧山谷走回了营地。", "这句话让李青怔住了。"),
        ("营地的火堆熄了。", "张岚从东侧山谷走回了营地。", "随即拔刀迎了上去。"),
    ],
)
def test_subject_or_reference_dependency_is_queued_for_repair(before, repeated, after):
    text = repeated + "\n\n" + before + repeated + after
    result = clean_repetitions(text)
    assert result["cleaned"] == text
    assert result["needs_repair"]
    assert records_of(result, "sentence")[0]["occurrences"][1]["action"] == "repair"


def test_highly_similar_adjacent_paragraphs_keep_unique_content_for_repair():
    first = "张岚沿着山口的石阶走向旧营地，巡逻队正在门前等候，远处的钟声越过山谷传来。"
    second = first.replace("石阶", "小径")
    text = first + "\n\n" + second
    result = clean_repetitions(text)
    record = records_of(result, "paragraph")[0]
    assert record["similarity"] >= 0.85
    assert record["count"] == 2
    assert record["occurrences"][1]["reasons"] == ["unique_content"]
    assert result["cleaned"] == text
    assert result["counts"]["adjacent_paragraph_pairs"] == 1


def test_exact_adjacent_paragraph_deletion_has_no_overlapping_sentence_edits():
    paragraph = "远处的钟声越过山谷传进营地。河边的篝火燃成了一片暗红。"
    text = paragraph + "\n\n" + paragraph + "\n\n" + paragraph
    result = clean_repetitions(text)
    assert result["cleaned"] == paragraph + "\n\n\n\n"
    assert len(result["removed"]) == 2
    assert result["counts"]["sentence_repetitions"] == 4
    assert not result["needs_repair"]


def test_nonadjacent_near_paragraphs_are_not_flagged():
    first = "张岚沿着山口的石阶走向旧营地，巡逻队正在门前等候，远处的钟声越过山谷传来。"
    text = first + "\n\n山里的雪忽然停住了。\n\n" + first.replace("石阶", "小径")
    assert not records_of(clean_repetitions(text), "paragraph")


def test_dialogue_quote_pairs_survive_deletion_of_a_safe_standalone_quote():
    quote = "山谷里的那座旧营地已经空了。"
    text = f"“{quote}”\n\n山口的旗帜忽然倒向了东边。\n\n“{quote}”"
    result = clean_repetitions(text)
    assert result["cleaned"] == f"“{quote}”\n\n山口的旗帜忽然倒向了东边。\n\n"


def test_eval_33_opening_shape_detects_both_copied_sentences():
    # Original structure, independent names and prose: no real-library dependency.
    action = "许澜从旧铜盒里取出一枚骨针，针尖朝南。"
    quote = "旧王城石阶下面，有什么醒了。"
    text = f"{action}人群越过山口，许澜望向远方：“{quote}”\n\n{action}“{quote}”"
    records = records_of(clean_repetitions(text, remove_safe=False), "sentence")
    assert len(records) == 2
    assert all(record["count"] == 2 for record in records)


@pytest.mark.parametrize(
    "later",
    [
        "李青低声说“山谷里的那座旧营地已经空了。”",
        "“山谷里的那座旧营地已经空了。”李青低声说道。",
        "山谷里的那座旧营地已经空了。这句话",
        "山谷里的那座旧营地已经空了。随后抬手",
    ],
)
def test_incomplete_dependencies_and_unpunctuated_attribution_are_kept(later):
    text = "“山谷里的那座旧营地已经空了。”\n\n" + later
    result = clean_repetitions(text)
    assert result["cleaned"] == text
    assert result["needs_repair"]


def test_deletion_ranges_reconstruct_output_and_repeated_runs_are_deterministic():
    sentence = "远处的钟声越过山谷传进营地。"
    text = sentence + "\n\n" + sentence + "河边的篝火燃成了一片暗红。\n\n" + sentence
    result = clean_repetitions(text)
    assert clean_repetitions(text) == result
    rebuilt, cursor = "", 0
    for span in result["removed"]:
        assert cursor <= span["start"] < span["end"]
        rebuilt += text[cursor : span["start"]]
        cursor = span["end"]
    assert result["cleaned"] == rebuilt + text[cursor:]


@pytest.mark.parametrize("left,right", [("‘", "’"), ("'", "'"), ('"', '"'), ("「『", "』」")])
def test_other_balanced_quote_styles_are_never_left_dangling(left, right):
    sentence = "山谷里的那座旧营地已经空了。"
    first = left + sentence + right
    text = first + "\n\n山口的旗帜忽然倒向了东边。\n\n" + first
    assert clean_repetitions(text)["cleaned"] == text[: -len(first)]


def test_malformed_closing_quote_is_kept_for_repair():
    sentence = "山谷里的那座旧营地已经空了。"
    text = sentence + "\n\n山口的旗帜忽然倒向了东边。\n\n" + sentence + "”"
    result = clean_repetitions(text)
    assert result["cleaned"] == text
    assert result["needs_repair"]


def test_decimal_values_do_not_merge_with_integer_values():
    text = "仪表显示室外温度已经降到1.5度。仪表显示室外温度已经降到15度。"
    assert not records_of(clean_repetitions(text), "sentence")


def test_soft_line_break_inside_a_complete_sentence_is_normalized_only_for_comparison():
    first = "远处的钟声越过山谷传进营地。"
    wrapped = "远处的钟声越过\r\n山谷传进营地。"
    text = first + "\r\n\r\n" + wrapped
    result = clean_repetitions(text, remove_safe=False)
    record = records_of(result, "sentence")[0]
    assert [node["text"] for node in record["occurrences"]] == [first, wrapped]
    assert result["cleaned"] == text
