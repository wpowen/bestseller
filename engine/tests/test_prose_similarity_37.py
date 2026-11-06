"""INBOX37 R4: calibrated narration candidates and guarded sentence repairs."""

import json
from pathlib import Path

import pytest

from bookengine.q1_repetition import repair_repetitions
from bookengine.text import _sentences
from tools.book_checkup import check_book


FIXTURE = Path(__file__).parent / "fixtures/review37/eval33-036.md"
FIRST, SECOND = FIXTURE.read_text().split("\n\n")[:2]
CARDS = (
    "作为南城的守将，他不得不拦住城门。\n\n"
    "作为巡河使，她已站在渡口等候。\n\n"
    "林绮是旧船厂的管事与修船师，抱着木匣站在门边。"
)


def analyze(text):
    from bookengine.prose_similarity import analyze_prose

    return analyze_prose(text)


def run_repair(text, ask, *, raw=None, state=None, bible=None, extract=None):
    return repair_repetitions(
        None,
        "ch36",
        text,
        raw or {},
        False,
        state or {},
        bible or {},
        {"number": 36},
        extract or (lambda *a, **kw: ({"events": []}, False, 0)),
        ask,
    )


def replacement_for(text, old, new):
    sid = next(i for i, (a, b) in enumerate(_sentences(text), 1) if old in text[a:b])
    return {"id": sid, "text": new}


def test_real_chapter_locates_actual_first_second_paragraphs_and_three_card_sentences():
    text = FIXTURE.read_text()
    result = analyze(text)
    assert result == analyze(text)
    pair = next(r for r in result["records"] if r["kind"] == "near_paragraph")
    assert [n["paragraph"] for n in pair["occurrences"]] == [1, 2]
    assert pair["jaccard"] == pytest.approx(0.3308823529)
    assert pair["containment"] == pytest.approx(0.7142857143)
    cards = [r for r in result["records"] if r["kind"] == "card_explanation"]
    assert len(cards) == 3
    for fragment in ("作为王庭统阵者", "作为九霄宗客卿", "白启榆是旧军械库的守库人与阵纹师"):
        assert any(fragment in r["text"] for r in cards)
    assert result["counts"] == {"near_paragraph_pairs": 1, "card_explanations": 3}
    assert result["rules"]["card_repair_above"] == 1
    for node in result["needs_repair"]:
        assert node["text"] == text[node["start"] : node["end"]]


@pytest.mark.parametrize("gap, expected", [(0, 1), (2, 1), (3, 0)])
def test_narration_pair_must_be_at_most_three_paragraphs_apart(gap, expected):
    text = "\n\n".join([FIRST, *["屋外有人经过。"] * gap, SECOND])
    assert analyze(text)["counts"]["near_paragraph_pairs"] == expected


@pytest.mark.parametrize("opener, closer", [("“", "”"), ("「", "」"), ('"', '"'), ("『", "』")])
def test_dialogue_and_mixed_speech_paragraphs_do_not_enter_similarity_repairs(opener, closer):
    text = opener + FIRST + closer + "\n\n" + opener + SECOND + closer
    assert analyze(text)["counts"] == {"near_paragraph_pairs": 0, "card_explanations": 0}
    assert not analyze("他说：" + opener + FIRST + closer + "\n\n" + SECOND)["needs_repair"]
    assert analyze(opener + CARDS + closer)["counts"]["card_explanations"] == 0


def test_unrelated_action_shared_name_and_normal_introductions_are_not_repair_candidates():
    text = (
        "林绮是码头掌柜。她把屋里的灯点亮，招呼客人坐下，转身去厨房取出一壶热茶。\n\n"
        "林绮打开铁箱，取出图纸塞进背包，跳上河边的小船。城里的钟声敲响，送船的人退开了。\n\n"
        "旧船厂是工人的食堂与宿舍。"
    )
    assert not analyze(text)["needs_repair"]


def test_single_card_sentence_is_reported_but_does_not_trigger_repair():
    result = analyze("作为南城的守将，他不得不拦住城门。")
    assert result["counts"]["card_explanations"] == 1
    assert not result["needs_repair"]


def test_both_overlap_gates_are_required_and_short_repeated_orders_are_excluded():
    # Eight shared 3-grams out of twelve: Jaccard .5, containment 2/3.
    assert not analyze("甲乙丙丁戊己庚辛壬癸子丑寅卯。\n\n甲乙丙丁戊己庚辛壬癸辰巳午未。")["needs_repair"]
    # Complete containment with excessive unrelated text fails Jaccard .32.
    assert not analyze(
        "甲乙丙丁戊己庚辛壬癸。\n\n甲乙丙丁戊己庚辛壬癸春夏秋冬东西南北红黄蓝绿风雨雷电山川湖海日月星辰。"
    )["needs_repair"]
    assert not analyze("停下别动。\n\n停下别动啊。")["needs_repair"]


def test_checkup_uses_same_counts_without_touching_source(tmp_path):
    path = tmp_path / "chapter-036.body.md"
    path.write_bytes(FIXTURE.read_bytes())
    report = check_book(tmp_path)["prose_similarity"]
    assert report["near_paragraph_pairs"] == 1
    assert report["card_explanations"] == 3
    assert report["chapters"][0]["chapter"] == 36
    assert report["rules"] == analyze(path.read_text())["rules"]
    assert path.read_bytes() == FIXTURE.read_bytes()


def test_near_repetition_is_submitted_to_model_without_deletion_when_no_edits():
    text = FIRST + "\n\n" + SECOND
    requests = []

    def ask(*args, **kwargs):
        requests.append(args)
        return {"edits": []}

    actual, _, _, note = run_repair(text, ask)
    assert requests and requests[0][1] == "ch36-repetition-repair"
    assert actual == text and note["status"] == "rolled_back"
    assert note["before"]["near_paragraph_pairs"] == 1


def test_near_repetition_can_adopt_only_model_edits_after_reextraction():
    text = FIRST + "\n\n" + SECOND + "\n\n库门里响起一阵轻响。"
    edits = [
        replacement_for(text, "姜孟遥退开的脚步停在窄道口。", ""),
        replacement_for(text, "作为王庭统阵者", "王庭统阵者手中的兵印仍亮着。"),
        replacement_for(text, "他抬手一压", ""),
        {"id": 1, "text": "不许采用的前文改写。"},
    ]
    requests = []

    def extract(*args, **kwargs):
        requests.append(args[1])
        assert args[2].startswith(FIRST)
        assert args[2].endswith("库门里响起一阵轻响。")
        return {"events": [], "summary": "复检后的摘要"}, False, 0

    actual, checked, failed, note = run_repair(text, lambda *a, **kw: {"edits": edits}, extract=extract)
    assert note["status"] == "repaired" and not failed
    assert note["before"]["near_paragraph_pairs"] == 1
    assert note["after"]["near_paragraph_pairs"] == 0
    assert checked["summary"] == "复检后的摘要"
    assert requests == ["ch36-repetition-recheck"]
    assert actual == FIRST + "\n\n王庭统阵者手中的兵印仍亮着。\n\n库门里响起一阵轻响。"


def test_above_threshold_cards_share_one_model_repair_and_preserve_unflagged_text():
    tail = "她蹲下去，木匣的铜扣压住了裙边。"
    text = CARDS + "\n\n" + tail
    originals = [text[a:b] for a, b in _sentences(text)][:3]
    replacements = ["他不得不拦住城门。", "她已站在渡口等候。", "林绮抱着木匣站在门边。"]
    requests = []

    def ask(*args, **kwargs):
        requests.append(args[1])
        hits = json.loads(dict(args[3])["可修改的句S编号"])
        assert [h["text"] for h in hits] == originals
        return {"edits": [dict(id=h["id"], text=r) for h, r in zip(hits, replacements)]}

    def extract(*args, **kwargs):
        requests.append(args[1])
        assert args[2].endswith(tail)
        return {"events": []}, False, 0

    actual, _, _, note = run_repair(text, ask, extract=extract)
    assert note["status"] == "repaired"
    assert actual == "\n\n".join([*replacements, tail])
    assert note["after"]["card_explanations"] == 0
    assert requests == ["ch36-repetition-repair", "ch36-repetition-recheck"]


@pytest.mark.parametrize("unsafe", ["invalid", "contradiction", "retired", "location", "lost_boundary"])
def test_similarity_repair_rolls_back_failed_safety_rechecks(unsafe):
    first = FIRST.replace("走到", "挨近")
    text = first + "\n\n" + SECOND
    replacement = "姜孟遥抓住库门的门环，不肯松手。"
    if unsafe == "retired":
        replacement = "廖远派人守住库门。"
    if unsafe == "contradiction":
        replacement = "廖远站在库门，正盯着门环。"
    if unsafe == "location":
        replacement = "姜孟遥站在海港，不肯松手。"
    patch = replacement_for(text, "姜孟遥退开的脚步停在窄道口。", replacement)
    # Replace the entire repeated paragraph using its three sentence IDs.
    edits = [patch]
    for old in ("作为王庭统阵者", "他抬手一压"):
        edits.append(replacement_for(text, old, ""))
    raw = dict(opening_location="窄道", opening_quote=first, end_location="窄道口", end_quote=SECOND)
    extractions = []

    def extract(*args, **kwargs):
        extractions.append(args[1])
        candidate = args[2]
        result = dict(raw, end_quote=replacement, end_location="库门")
        if unsafe == "lost_boundary":
            result.pop("opening_location")
        elif unsafe == "location":
            result.update(opening_location="海港", opening_quote=replacement, end_location="海港")
        if unsafe == "contradiction":
            result["events"] = [dict(kind="character", name="廖远", location="库门", action="stay", paragraph=2)]
        assert candidate != text
        return result, unsafe == "invalid", 0

    actual, checked, failed, note = run_repair(
        text,
        lambda *a, **kw: {"edits": edits},
        raw=raw,
        state=dict(end_location="窄道", _dead=["廖远"]),
        bible=dict(antagonists=[dict(name="廖远", exit=35)]) if unsafe == "retired" else {},
        extract=extract,
    )
    assert actual == text and checked == raw and failed is False
    assert note["status"] == "rolled_back"
    assert extractions == ([] if unsafe == "retired" else ["ch36-repetition-recheck"])


def test_nonreducing_edits_are_rolled_back_without_reextraction():
    text = CARDS
    patch = replacement_for(text, "作为南城", "作为南城的守将，他已经拦住城门。")

    def extract(*a, **kw):
        pytest.fail("unimproved candidate must not be reextracted")

    actual, _, _, note = run_repair(text, lambda *a, **kw: {"edits": [patch]}, extract=extract)
    assert actual == text and note["status"] == "rolled_back"


def test_reducing_cards_must_not_introduce_exact_repetition_or_erase_stage_transition():
    tail = "城外的雨水已经打湿了守门人的衣裳。"
    text = CARDS + "\n\n风声停了。\n\n院里没有人。\n\n" + tail
    patch = replacement_for(text, "作为南城", tail)

    def extract(*a, **kw):
        pytest.fail("candidate with an increased diagnostic must be rejected before extraction")

    actual, _, _, note = run_repair(text, lambda *a, **kw: {"edits": [patch]}, extract=extract)
    assert actual == text and note["status"] == "rolled_back"

    text = "作为南城的守将，他离开渡口后，赶到山城。\n\n作为巡河使，她已站在渡口等候。"
    patch = replacement_for(text, "作为南城", "他守在城门，握紧了长刀。")
    bible = dict(
        volumes=[dict(number=1, start=1, end=35, location="渡口"), dict(number=2, start=36, end=50, location="山城")]
    )
    actual, _, _, note = run_repair(
        text,
        lambda *a, **kw: {"edits": [patch]},
        bible=bible,
        state=dict(end_location="渡口"),
        extract=extract,
    )
    assert actual == text and note["status"] == "rolled_back"
