from bookengine.explanation_density import analyze_explanation_density, repair_explanation_density
from bookengine.opening_exposition import opening_explanation_candidates


NAMEBOOK = "他要保住的东西不多，怀里那本被扣去大半的杂役名册算一个。若名字再被抹掉，连替自己辨明来历的凭据都没了。"
BYSTANDERS = (
    "几名奉命守住北辰剑阵、替宗门看护剑榜声望的寒峰剑宗弟子低声发笑。秦横抬脚踩上扫过的石面：“第一阶归我巡看。”"
)
REST = "顾寒蹲下，重新把雪从墙边扫开。秦横挪开了脚。"
TEXT = "\n\n".join(("风雪压低山门，顾寒扫雪。", NAMEBOOK, BYSTANDERS, REST * 70))
EDITS = [
    dict(
        id=2,
        original=NAMEBOOK,
        text="顾寒把怀里残破的杂役名册往深处塞了塞，自己的名字还在那上头。",
        reason="开头只保留此刻在护的东西",
    ),
    dict(
        id=3,
        original=BYSTANDERS,
        text="几个寒峰剑宗弟子低声发笑。秦横抬脚踩上扫过的石面：“第一阶归我巡看。”",
        reason="压掉围观者的履历",
    ),
]


def run(edits=EDITS, *, preservation=True, chapter=1):
    calls = []

    def ask(client, key, instruction, sections, schema, **kwargs):
        calls.append(key)
        if key.endswith("-repair"):
            assert "按顺序给出的完整开篇段" in dict(sections)
            return {"edits": edits}
        return {"facts_preserved": preservation, "exposition_reduced": True}

    def extract(client, name, text, state, bible, outline, rows, final):
        calls.append(name)
        assert final
        return {"events": []}, False, 0

    result = repair_explanation_density(None, "chapter", TEXT, {}, False, {}, {}, {"number": chapter}, extract, ask)
    return result, calls


def test_first_page_is_edited_below_chapter_density_and_rest_stays_identical():
    assert analyze_explanation_density(TEXT)["tier"] == "normal"
    _, candidates = opening_explanation_candidates(TEXT)
    assert list(candidates) == [2, 3]
    (text, _, _, note), calls = run()
    assert note["opening"]["status"] == "repaired"
    assert text.endswith(REST * 70) and "杂役名册" in text and "看护剑榜声望" not in text
    assert len(calls) == 3


def test_opening_repair_rejects_reassigned_dialogue_and_fact_loss():
    wrong_dialogue = [dict(EDITS[1], text="秦横抬脚踩上石面：“把东西拿过来。”")]
    for edits, preservation in ((wrong_dialogue, True), (EDITS, False)):
        (text, _, _, note), calls = run(edits, preservation=preservation)
        assert text == TEXT and note["opening"]["status"] == "rolled_back"
        assert all(not key.endswith("-final-state") for key in calls)


def test_later_chapters_keep_existing_whole_chapter_gate():
    (text, _, _, note), calls = run(chapter=2)
    assert calls == [] and "opening" not in note and text == TEXT
