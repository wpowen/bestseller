"""INBOX54: explicit nonhuman speech contracts and literal identity checks."""

from bookengine.cast_consistency import cast_findings, repair_cast_consistency
from bookengine.nonhuman_speech import normalize_speech_contracts, speech_context, writer_speech_bible
from bookengine.q1_schema import DATA


def bible(can_speak="否", from_chapter=0):
    return {
        "characters": [
            {"name": "顾奕棣", "role": "protagonist", "identity": "被逐出驭兽宗的矿镇少年", "gender": "男"},
            {"name": "顾晴棣", "role": "support", "identity": "主角的妹妹", "gender": "女"},
            {
                "name": "雪沛宁",
                "role": "support",
                "identity": "怕冷贪吃的幼年雪兽",
                "gender": "雄性",
                "can_speak_human": can_speak,
                "human_speech_from_chapter": from_chapter,
            },
        ]
    }


def test_new_nonhuman_cards_default_to_no_human_speech_and_have_natural_writer_context():
    value = {"characters": bible()["characters"][:2] + [{"name": "雪沛宁", "identity": "怕冷贪吃的幼年雪兽"}]}

    normalized = normalize_speech_contracts(value)
    card = normalized["characters"][2]
    context = speech_context(normalized, 7)

    assert card["can_speak_human"] == "否"
    assert card["human_speech_from_chapter"] == 0
    assert context == [("非人角色表达限制", "雪沛宁：不能说人话，只能用兽鸣、动作和反应表达。")]
    assert "can_speak_human" not in context[0][1]
    template = DATA["schemas"]["prepare"]["bible"]["characters"][0]
    assert template["can_speak_human"] == "否" and template["human_speech_from_chapter"] == 0


def test_nonspeaking_nonhuman_address_becomes_narrative_only_and_is_not_an_oral_contract():
    from bookengine.address_contracts import address_rows

    value = bible()
    value["characters"][2].update(address="谦哥", address_target="顾奕棣", address_origin="自幼这样称呼")

    normalized = normalize_speech_contracts(value)
    beast = normalized["characters"][2]

    assert beast["narrative_address"] == "谦哥"
    assert all(key not in beast for key in ("address", "address_target", "address_origin"))
    assert not any(row["speaker"] == "雪沛宁" for row in address_rows(value))


def test_legacy_nonspeaking_address_is_hidden_from_writer_view_without_mutating_bible():
    value = bible()
    value["characters"][2].update(address="谦哥", address_target="顾奕棣", address_origin="旧书字段")

    writer_view = writer_speech_bible(value)

    assert value["characters"][2]["address"] == "谦哥"
    beast = writer_view["characters"][2]
    assert beast.get("address") is None and beast["narrative_address"] == "谦哥"


def test_prepare_response_normalizes_missing_nonhuman_speech_contract():
    from bookengine.q1_prepare import _ask

    class Client:
        prefix = ""
        relaxed = True
        plan_suffix = ""

        def stage(self, _stage, _prompt, _schema, **kwargs):
            value = {
                "bible": {
                    "characters": [
                        {"name": "顾奕棣", "role": "protagonist", "voice": "少言", "desire": "回家"},
                        {"name": "顾晴棣", "role": "support", "voice": "直接", "desire": "活下去"},
                        {
                            "name": "雪沛宁",
                            "role": "support",
                            "identity": "怕冷贪吃的幼年雪兽",
                            "voice": "低鸣",
                            "desire": "取暖",
                        },
                    ]
                }
            }
            normalized = kwargs["normalize"](value)
            kwargs["validate"](normalized)
            return normalized

    result = _ask(Client(), "prepare", "prepare", [], seed_text="")

    beast = result["bible"]["characters"][2]
    assert beast["can_speak_human"] == "否" and beast["human_speech_from_chapter"] == 0


def test_q1_chapter_appends_nonhuman_speech_context_to_writer_prompt(monkeypatch):
    from bookengine import q1_chapter
    from support_minimal_b import CaptureClient, MinimalBTests, body

    fixture = MinimalBTests()
    fixture.setUp()
    try:
        monkeypatch.setattr(
            q1_chapter,
            "speech_context",
            lambda _bible, chapter: [("非人角色表达限制", f"SPEECH_CONTEXT_SENTINEL_{chapter}")],
        )
        client = CaptureClient(fixture.out, [body(40, 190)])
        fixture.run_chapter(client)

        draft = next(call for call in client.calls if call["stage"] == "r2-ch2-draft")
        assert "SPEECH_CONTEXT_SENTINEL_2" in draft["prompt"]
    finally:
        fixture.doCleanups()


def test_eval52_nonhuman_human_speech_is_found_in_name_and_pronoun_attributions():
    text = (
        "“我能自己走。”雪沛宁含糊地顶回一句。\n\n"
        "雪沛宁没有立刻回答。它盯着那道光。\n\n“怕叫那个名字以后，我还是雪沛宁。”它终于说。\n\n"
        "“不是认主，是认名。”雪沛宁含混地叫了一声。"
    )

    findings = cast_findings(text, bible(), chapter=48)
    speech = [row for row in findings if row["kind"] == "nonhuman_speech"]

    assert len(speech) == 3
    assert {row["name"] for row in speech} == {"雪沛宁"}


def test_eval57_single_quoted_address_spoken_by_nonspeaking_beast_is_detected():
    value = bible()
    value["characters"][2].update(address="谦哥", address_target="顾奕棣")
    text = "‘谦哥。’雪沛宁用脑袋拱了拱他的手。\n\n‘谦哥！’雪沛宁用身体顶住他的腿，回头急叫。"

    findings = cast_findings(text, value, chapter=2)

    speech = [row for row in findings if row["kind"] == "nonhuman_speech"]
    assert len(speech) == 2 and {row["observed"] for row in speech} == {"谦哥。", "谦哥！"}


def test_explicit_human_speaker_near_nonspeaking_beast_is_not_misattributed():
    value = bible()
    value["characters"][2].update(address="谦哥", address_target="顾奕棣")
    text = "顾晴棣喊：‘谦哥！’雪沛宁随即用身体顶住顾奕棣的腿。"

    assert not [row for row in cast_findings(text, value, chapter=2) if row["kind"] == "nonhuman_speech"]


def test_human_dialogue_after_touching_nonhuman_card_is_not_attributed_to_the_beast():
    value = bible()
    text = "顾奕棣摸了摸雪沛宁的耳根：‘鼻子不是拳头，能借，却不能乱借。’雪沛宁咬住他的袖口，低叫一声。"

    assert not [row for row in cast_findings(text, value, chapter=5) if row["kind"] == "nonhuman_speech"]


def test_human_dialogue_near_a_nonhuman_name_and_animal_calls_are_not_attributed_to_it():
    text = (
        "顾晴棣脸色骤白：“敲钟的人停了。”\n\n"
        "顾奕棣把药箱塞进雪沛宁背侧的皮囊，转身便走。沈弘策拦住他：“你带晴棣走支道。”\n\n"
        "雪沛宁低头咬住他的袖口，含糊地叫了一声。"
    )

    assert not [row for row in cast_findings(text, bible(), chapter=4) if row["kind"] == "nonhuman_speech"]


def test_speaking_contract_allows_speech_from_configured_chapter_but_requires_first_origin():
    text = "契印补全后，雪沛宁喉间第一次发出清楚人声：“我能自己走。”"

    assert not [row for row in cast_findings(text, bible("是", 27), chapter=27) if row["kind"].startswith("nonhuman")]
    before = cast_findings("“我能自己走。”雪沛宁说。", bible("是", 27), chapter=26)
    missing = cast_findings("“我能自己走。”雪沛宁说。", bible("是", 27), chapter=27)
    assert any(row["kind"] == "nonhuman_speech" for row in before)
    assert any(row["kind"] == "nonhuman_speech_origin" for row in missing)


def test_identity_literal_is_found_inside_an_action_sentence():
    text = "顾奕棣没答话。他蹲下去，让怕冷贪吃的幼年雪兽雪沛宁把鼻子按进雪层。"

    findings = cast_findings(text, bible(), chapter=23)

    assert [(row["kind"], row["name"], row["observed"]) for row in findings] == [
        ("identity_literal", "雪沛宁", "怕冷贪吃的幼年雪兽")
    ]


def test_nonhuman_speech_uses_existing_targeted_model_repair_and_recheck():
    text = "“我能自己走。”雪沛宁含糊地顶回一句。顾奕棣扶住它的前腹。"

    def ask(_client, _stage, _instruction, sections, _schema, **_kwargs):
        targets = next(body for title, body in sections if title == "待修句")
        assert "nonhuman_speech" in targets
        return {"edits": [{"id": 1, "text": "雪沛宁低呜一声，挣扎着想自己站稳。顾奕棣扶住它的前腹。"}]}

    repaired, _, failed, note = repair_cast_consistency(
        None,
        "chapter27",
        text,
        {"events": []},
        False,
        {},
        bible(),
        {"number": 27},
        lambda *_args, **_kwargs: ({"events": []}, False, None),
        ask,
    )

    assert repaired == "雪沛宁低呜一声，挣扎着想自己站稳。顾奕棣扶住它的前腹。"
    assert not [row for row in cast_findings(repaired, bible(), chapter=27) if row["kind"] == "nonhuman_speech"]
    assert failed is False and note["status"] == "repaired"


def test_multi_quote_nonhuman_line_targets_the_balanced_complete_paragraph():
    text = "“它认你了。”雪沛宁含混地叫了一声，随后连打三个喷嚏，“不是认主……是认名。”\n\n顾奕棣低头看着铜牌。"

    def ask(_client, _stage, _instruction, sections, _schema, **_kwargs):
        targets = next(body for title, body in sections if title == "待修句")
        assert "不是认主……是认名" in targets
        return {"edits": [{"id": 1, "text": "雪沛宁含混地叫了一声，随后连打三个喷嚏。"}]}

    repaired, _, failed, note = repair_cast_consistency(
        None,
        "chapter50",
        text,
        {"events": []},
        False,
        {},
        bible(),
        {"number": 50},
        lambda *_args, **_kwargs: ({"events": []}, False, None),
        ask,
    )

    assert repaired == "雪沛宁含混地叫了一声，随后连打三个喷嚏。\n\n顾奕棣低头看着铜牌。"
    assert repaired.count("“") == repaired.count("”")
    assert failed is False and note["status"] == "repaired"
