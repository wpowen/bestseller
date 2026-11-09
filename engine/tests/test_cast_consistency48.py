"""INBOX48: address direction and conservative cast consistency checks."""

import yaml

from bookengine.address_contracts import address_rows
from bookengine.cast_consistency import cast_checkup, cast_findings, repair_cast_consistency
from bookengine.identity import validate_cast_contract
from tools.book_checkup import check_book


def bible():
    return {
        "characters": [
            {
                "name": "顾川序",
                "role": "protagonist",
                "identity": "被逐出驭兽宗的矿镇少年",
                "gender": "男",
                "address": "陆大哥",
                "address_origin": "顾川序在边境猎场初见时按斥候排行称呼",
                "address_target": "陆烈",
            },
            {
                "name": "顾湛宁",
                "role": "support",
                "identity": "顾川序尚未成年的妹妹",
                "gender": "女",
                "address": "阿序",
                "address_origin": "顾川序在矿道重逢时脱口而出",
                "address_target": "顾湛宁",
            },
            {
                "name": "陆烈",
                "role": "support",
                "identity": "猎场斥候出身的散修",
                "gender": "男",
                "address": "陆大哥",
                "address_origin": "顾川序在边境猎场初见时按斥候排行称呼",
                "address_target": "陆烈",
            },
            {
                "name": "沈夏珩",
                "role": "romance",
                "identity": "游历矿镇的年轻女药师",
                "gender": "女",
            },
            {"name": "顾安澜", "role": "support", "identity": "顾冬岚的妹妹", "gender": "女"},
            {"name": "顾冬岚", "role": "protagonist", "identity": "矿镇少年", "gender": "男"},
            {"name": "韩晏竹", "role": "antagonist", "identity": "封矿追捕者", "gender": "男"},
        ],
        "antagonists": [{"name": "韩晏竹", "stage": 1, "exit": 10}],
    }


def test_eval45_legacy_addresses_resolve_to_speaker_target_contract():
    rows = list(address_rows(bible()))
    matches = [row for row in rows if row["address"] == "陆大哥"]
    assert matches == [
        {
            "speaker": "顾川序",
            "name": "陆烈",
            "address": "陆大哥",
            "origin": "顾川序在边境猎场初见时按斥候排行称呼",
            "known": True,
        }
    ]
    assert any(row["speaker"] == "顾湛宁" and row["name"] == "顾川序" for row in rows if row["address"] == "阿序")


def test_new_bible_validation_rejects_target_owned_or_self_misdirected_addresses():
    try:
        validate_cast_contract(bible())
    except ValueError as exc:
        assert "address" in str(exc)
    else:
        raise AssertionError("eval-45 legacy mixed semantics must not pass new-bible validation")

    current = {
        "characters": [
            {"name": "顾川序", "gender": "男", "address": "陆大哥", "address_target": "陆烈"},
            {"name": "顾湛宁", "gender": "女", "address": "阿序", "address_target": "顾川序"},
            {"name": "陆烈", "gender": "男", "address": "阿序", "address_target": "顾川序"},
        ]
    }
    assert validate_cast_contract(current) is current


def test_eval39_gendered_address_pronoun_and_enemy_leader_are_found():
    text = (
        '背弓人回头瞪他："沈师兄在矿洞那边，不在这里。"\n\n'
        "最前方的人立在封石旁，腰间悬着一枚黑石雪路令牌。竟是沈夏珩。\n\n"
        "他隔着风雪朝这边看了一眼，随即转身，指挥众人把最后两块挡路石推入矿道，出口被彻底封死。"
    )
    findings = cast_findings(text, bible(), chapter=8)
    assert {row["kind"] for row in findings if row["status"] == "Inference"} >= {
        "gendered_address",
        "gender_pronoun",
        "identity_drift",
    }
    assert all(row["name"] == "沈夏珩" for row in findings if row["status"] == "Inference")


def test_f42_single_subject_pronoun_drift_is_repairable():
    text = "顾安澜才俯身查看断绳，脚踝便被另一截绳索猛然拽住。他猝不及防，整个人被拖得向前滑去，掌心撑住石台。"
    findings = cast_findings(text, bible(), chapter=7)
    assert [(row["kind"], row["name"], row["observed"], row["expected"]) for row in findings] == [
        ("gender_pronoun", "顾安澜", "他", "她")
    ]


def test_eval49_inline_and_following_sentence_pronouns_share_the_checkup_rule():
    value = bible()
    value["characters"][0].update(name="顾昕冉", gender="男")
    inline = (
        "顾昕冉从矿洞口抽身，顶着卷过矿架的风雪，沿镇外几近没膝的积雪一路折返。"
        "抵达旧屋檐下时，她的铜哨已被风吹得发颤，肩头也落满冰碴。"
    )
    following = "顾昕冉没有答。她站在树影里，掌心旧伤被刀柄磨得发热。盐皮兽群正从寨门前的空地穿过。"

    for chapter, text in ((6, inline), (26, following)):
        findings = cast_findings(text, value, chapter=chapter)
        assert [(row["kind"], row["name"], row["observed"], row["expected"]) for row in findings] == [
            ("gender_pronoun", "顾昕冉", "她", "他")
        ]
        checkup = cast_checkup(value, [{"number": chapter, "text": text}])
        assert checkup["counts"]["gender_pronoun"] == 1
        assert checkup["findings"] == findings


def test_eval49_pronoun_subject_carries_only_through_a_bound_pronoun_sentence():
    value = bible()
    value["characters"][0].update(name="顾昕冉", gender="男")
    text = (
        "顾昕冉沿积雪一路折返。抵达旧屋檐下时，她的铜哨已被风吹得发颤。"
        "风铃响过三声，她顾不得进屋查看。可她再想不起回去的路。雪原在记忆里只剩一片白。她扶住墙。"
    )

    findings = cast_findings(text, value, chapter=6)

    assert [(row["observed"], row["expected"]) for row in findings] == [("她", "他"), ("她", "他"), ("她", "他")]


def test_named_man_looking_at_a_woman_does_not_bind_her_next_pronoun_to_him():
    value = bible()
    text = "顾冬岚看向她。她脸色苍白，却没有退缩。顾冬岚抬眼。她将铜片收回袖中。"

    assert cast_findings(text, value, chapter=3) == []


def test_contract_borrowing_and_memory_loss_keep_the_named_subject_bound():
    value = bible()
    text = (
        "顾冬岚握住契兽的前爪，契约纹路随即沉入筋骨。她没有窥见未来，只听见远处兽息。"
        "顾冬岚收刀时，脑中忽然空了一块。她记得来路。可她再想不起回家的方向。"
    )

    findings = cast_findings(text, value, chapter=26)

    assert [(row["observed"], row["expected"]) for row in findings] == [("她", "他"), ("她", "他"), ("她", "他")]


def test_eval49_pronoun_repair_targets_the_shared_finding_and_passes_recheck():
    value = bible()
    value["characters"][0].update(name="顾昕冉", gender="男")
    text = "顾昕冉没有答。她站在树影里，掌心旧伤被刀柄磨得发热。"

    def ask(_client, _stage, _instruction, sections, _schema, **_kwargs):
        targets = yaml.safe_load(next(body for title, body in sections if title == "待修句"))
        assert [(row["kind"], row["name"], row["observed"]) for row in targets] == [("gender_pronoun", "顾昕冉", "她")]
        return {"edits": [{"id": targets[0]["id"], "text": "他站在树影里，掌心旧伤被刀柄磨得发热。"}]}

    repaired, _, failed, note = repair_cast_consistency(
        None,
        "chapter26",
        text,
        {"events": []},
        False,
        {},
        value,
        {"number": 26},
        lambda *_args, **_kwargs: ({"events": []}, False, None),
        ask,
    )

    assert repaired == "顾昕冉没有答。他站在树影里，掌心旧伤被刀柄磨得发热。"
    assert cast_findings(repaired, value, chapter=26) == []
    assert failed is False and note["status"] == "repaired"


def test_eval49_clear_pronoun_uses_deterministic_leaf_fix_when_model_returns_no_edit():
    value = bible()
    value["characters"][0].update(name="顾昕冉", gender="男")
    text = "顾昕冉没有答。她站在树影里，掌心旧伤被刀柄磨得发热。"

    repaired, _, failed, note = repair_cast_consistency(
        None,
        "chapter26",
        text,
        {"events": []},
        False,
        {},
        value,
        {"number": 26},
        lambda *_args, **_kwargs: ({"events": []}, False, None),
        lambda *_args, **_kwargs: {"edits": []},
    )

    assert repaired == "顾昕冉没有答。他站在树影里，掌心旧伤被刀柄磨得发热。"
    assert cast_findings(repaired, value, chapter=26) == []
    assert failed is False and note["status"] == "repaired"


def test_ambiguous_pronoun_stays_unknown_and_is_not_a_repair_target():
    text = "顾安澜扶住顾冬岚，两人一起越过断桥。他回头看了一眼。"
    findings = cast_findings(text, bible(), chapter=7)
    assert findings == [
        {
            "kind": "gender_pronoun",
            "chapter": 7,
            "name": "",
            "status": "Unknown",
            "observed": "他",
            "expected": "",
            "reason": "前句出现多名人物，代词指代不清",
            "quote": "他回头看了一眼。",
            "start": text.index("他回头"),
            "end": len(text),
            "id": 2,
        }
    ]


def test_eval52_explicit_named_subject_binds_following_pronoun_despite_other_people():
    value = bible()
    value["characters"][3].update(name="沈弘策", gender="女")
    value["characters"][4].update(name="顾晴棣", gender="女")
    value["characters"][5].update(name="顾奕棣", gender="男")
    samples = {
        14: "沈弘策正蹲在伤员身边。他听见脚步，抬眼看见顾奕棣。",
        15: "沈弘策没有再争。他将药箱背紧，指向左侧一道覆雪的凹槽。",
        44: (
            "沈弘策把药车停稳，俯身翻检门边残存石简。纸片早被风雪磨薄，她看了许久，仍只摇头。"
            "“契印槽是宗族旧制，弃名槽没有注记。”他语速越来越快。"
        ),
        48: "沈弘策蹲在侧廊入口，正替顾晴棣查看冻伤的右手。听见这句话，他抬头道。",
    }

    for chapter, text in samples.items():
        findings = cast_findings(text, value, chapter=chapter)
        assert [(row["status"], row["name"], row["observed"], row["expected"]) for row in findings] == [
            ("Inference", "沈弘策", "他", "她")
        ]


def test_eval52_explicit_named_subject_pronouns_receive_deterministic_leaf_repairs():
    value = bible()
    value["characters"][3].update(name="沈弘策", gender="女")
    value["characters"][4].update(name="顾晴棣", gender="女")
    samples = {
        14: ("沈弘策正蹲在伤员身边。他听见脚步。", "沈弘策正蹲在伤员身边。她听见脚步。"),
        15: ("沈弘策没有再争。他将药箱背紧。", "沈弘策没有再争。她将药箱背紧。"),
        44: (
            "沈弘策把药车停稳。她看了许久。\n\n“弃名槽没有注记。”他语速越来越快。",
            "沈弘策把药车停稳。她看了许久。\n\n“弃名槽没有注记。”她语速越来越快。",
        ),
        48: (
            "沈弘策蹲在侧廊入口，正替顾晴棣查看冻伤的右手。听见这句话，他抬头道。",
            "沈弘策蹲在侧廊入口，正替顾晴棣查看冻伤的右手。听见这句话，她抬头道。",
        ),
    }

    for chapter, (text, expected) in samples.items():
        repaired, _, failed, note = repair_cast_consistency(
            None,
            f"chapter{chapter}",
            text,
            {"events": []},
            False,
            {},
            value,
            {"number": chapter},
            lambda *_args, **_kwargs: ({"events": []}, False, None),
            lambda *_args, **_kwargs: {"edits": []},
        )

        assert repaired == expected
        assert cast_findings(repaired, value, chapter=chapter) == []
        assert failed is False and note["status"] == "repaired"


def test_two_coordinated_named_subjects_remain_unknown():
    value = bible()
    value["characters"][3].update(name="沈弘策", gender="女")
    value["characters"][4].update(name="顾晴棣", gender="女")
    text = "沈弘策和顾晴棣一起抬起药箱。他转身走向侧廊。"

    findings = cast_findings(text, value, chapter=48)

    assert len(findings) == 1
    assert findings[0]["status"] == "Unknown"
    assert findings[0]["name"] == ""


def test_eval52_pronoun_name_residue_is_found_without_matching_ordinary_words():
    value = bible()
    value["characters"][3].update(name="雪沛宁", gender="雄性", identity="幼年雪兽")
    text = "它雪沛宁恋恋不舍地叫了两声。顾冬岚看见他顾不上回头，只能继续往前走。"

    findings = cast_findings(text, value, chapter=50)

    assert [(row["kind"], row["name"], row["observed"], row["expected"]) for row in findings] == [
        ("pronoun_name_residue", "雪沛宁", "它雪沛宁", "雪沛宁")
    ]


def test_eval52_pronoun_name_residue_receives_deterministic_leaf_repair():
    value = bible()
    value["characters"][3].update(name="雪沛宁", gender="雄性", identity="幼年雪兽")
    text = "它雪沛宁恋恋不舍地叫了两声。顾晴棣轻声说。"

    repaired, _, failed, note = repair_cast_consistency(
        None,
        "chapter50",
        text,
        {"events": []},
        False,
        {},
        value,
        {"number": 50},
        lambda *_args, **_kwargs: ({"events": []}, False, None),
        lambda *_args, **_kwargs: {"edits": []},
    )

    assert repaired == "雪沛宁恋恋不舍地叫了两声。顾晴棣轻声说。"
    assert cast_findings(repaired, value, chapter=50) == []
    assert failed is False and note["status"] == "repaired"


def test_checkup_counts_address_gender_and_identity_without_claiming_human_acceptance():
    chapters = [
        {
            "number": 8,
            "text": "最前方的人立在封石旁，竟是沈夏珩。他指挥众人封死矿道。",
        }
    ]
    result = cast_checkup(bible(), chapters)
    assert result["counts"]["identity_drift"] == 1
    assert result["counts"]["gender_pronoun"] == 1
    assert result["address_contract"]["semantic_rows"]
    assert result["authority"] == "agent_editorial_not_human"


def test_whole_book_checkup_registers_cast_consistency_without_rewriting_book(tmp_path):
    source = yaml.safe_dump(bible(), allow_unicode=True)
    (tmp_path / "story_bible.yaml").write_text(source, encoding="utf-8")
    text = "竟是沈夏珩。他指挥众人封死矿道。"
    (tmp_path / "chapter-008.body.md").write_text(text, encoding="utf-8")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    result = check_book(tmp_path, source="draft")["cast_consistency"]
    assert result["counts"]["gender_pronoun"] == 1
    assert result["authority"] == "agent_editorial_not_human"
    assert all(path.read_bytes() == content for path, content in before.items())


def test_targeted_repair_is_adopted_only_after_deterministic_recheck():
    text = "顾安澜才俯身查看断绳，脚踝便被另一截绳索猛然拽住。他猝不及防，整个人被拖得向前滑去。"

    def ask(_client, _stage, _instruction, sections, _schema, **_kwargs):
        targets = next(value for key, value in sections if key == "待修句")
        assert "gender_pronoun" in targets and "顾安澜" in targets
        return {"edits": [{"id": 2, "text": "她猝不及防，整个人被拖得向前滑去。"}]}

    def extract(_client, _stage, candidate, _state, _bible, _outline, _rows, final=False):
        assert final is True
        return {"events": [], "summary": candidate[:20]}, False, None

    repaired, raw, failed, note = repair_cast_consistency(
        None,
        "chapter7",
        text,
        {"events": []},
        False,
        {},
        bible(),
        {"number": 7},
        extract,
        ask,
    )
    assert "她猝不及防" in repaired
    assert raw["summary"] and failed is False
    assert note["status"] == "repaired" and note["after"] == []
