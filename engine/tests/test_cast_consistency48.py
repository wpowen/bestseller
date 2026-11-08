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
