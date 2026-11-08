"""INBOX 46: publication hygiene detection and bounded adoption."""

from bookengine.text_hygiene import inspect_text


def test_detects_english_and_structure_notes_but_allows_position_labels():
    text = "守卫退到A12号位，狭窄处/low石面刮过他的肩背。\n\n第二场戏开始时，天色已经暗了。\n\n顾川序抬头看向雪坡。"

    result = inspect_text(text)

    assert result["counts"]["english_fragments"] == 1
    assert result["english"][0]["text"] == "low"
    assert result["counts"]["structural_notes"] == 1
    assert result["structure"][0]["text"] == "第二场戏"


def test_detects_nonadjacent_exact_paragraph_block_from_eval45_chapter24():
    first = "陆烈收拾好刀，走到岩口却没有立刻下山。他回头问顾川序：“你真要跟苏斐然守在这里？”"
    second = "苏斐然已蹲回伤者身边，没有催促，只等顾川序决定。"
    text = f"{first}\n\n{second}\n\n众人先把伤者送离旧猎路。\n\n{first}\n\n{second}"

    result = inspect_text(text)

    assert result["counts"]["exact_paragraph_repetitions"] == 2
    assert result["counts"]["paragraph_repetitions"] == 2
    assert [(row["reference_paragraph"], row["paragraph"]) for row in result["paragraphs"]] == [(1, 4), (2, 5)]


def test_detects_near_explanation_paragraph_from_eval45_chapter38():
    first = (
        "方才两人同时伸手时，绳索之所以被拉向相反方向，并非因为他抓绳、霜砚秋拉绳。"
        "绳结内侧早已塞进了一块带倒刺的兽骨。她当时伸手，根本不是要救他，而是在把骨刺从绳结里拔出来。"
    )
    second = (
        "顾川序这才发现，两人同时伸手时，绳索之所以被拉向相反方向，并非因为他抓绳、霜砚秋拉绳。"
        "绳结内侧早已塞进一块带倒刺的兽骨，骨刺正卡住绳股，分别借两人的力道绷向两端。"
    )

    result = inspect_text(f"{first}\n\n{second}\n\n霜砚秋抬起头。")

    assert result["counts"]["near_paragraph_repetitions"] == 1
    assert result["counts"]["paragraph_repetitions"] == 1
    issue = result["paragraphs"][0]
    assert issue["kind"] == "near_paragraph"
    assert issue["reference_paragraph"] == 1 and issue["paragraph"] == 2
    assert issue["similarity"] >= 0.65
