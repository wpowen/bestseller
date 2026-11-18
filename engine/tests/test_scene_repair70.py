"""E7 repairs only explicit material continuity; ambiguous prose remains reference-only."""

from copy import deepcopy
import socket

import pytest

from bookengine.scene_consistency import material_findings, restraint_findings, scene_consistency_checkup
from bookengine.scene_repair import repair_scene_consistency, scene_repair_candidates
from bookengine.text import _paragraph_spans

BIBLE = {"characters": [{"name": "石子翎", "role": "protagonist"}, {"name": "雷九"}, {"name": "韩彦棕"}]}
BOUND = "黑绳勒住石子翎双臂，绳结扎进腕骨。"
ACTION = "石子翎沉腰贴地，一拳送出，撞开门板。"
FIRST = "雷九握着这根铁棍。"
LATER = "雷九举起原先那根木棍，撞开门板。"
EXPECTED = LATER.replace("木棍", "铁棍", 1)
TAIL = "院里的人都望着门口，谁也没有说话，檐下的雨还在一滴一滴往石阶上落。"
TEXT = "\n\n".join([FIRST, LATER, TAIL * 12])


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("scene repair tests must not call a model or network")

    monkeypatch.setattr(socket.socket, "connect", denied)


def forbidden(*args, **kwargs):
    pytest.fail("reference/invalid candidates must not reach model or state extraction")


def run(text=TEXT, answer=None, raw=None, invalid=False, checked=None):
    calls = []

    def ask(client, stage, *args, **kwargs):
        calls.append(stage)
        return deepcopy(answer if answer is not None else {"edits": [{"id": 2, "text": EXPECTED}]})

    def extract(client, stage, *args, **kwargs):
        calls.append(stage)
        return deepcopy(checked if checked is not None else {"events": []}), invalid, 0

    result = repair_scene_consistency(None, "ch1", text, raw or {}, False, {}, BIBLE, {"number": 1}, extract, ask)
    return result, calls


@pytest.mark.parametrize(
    "text",
    [
        BOUND + ACTION,
        "石子翎拿黑绳勒住雷九双臂。石子翎一拳打向门板。",
        "石子翎双手握紧捆好的行李。石子翎一拳打向门板。",
        "石子翎并未被绑住双臂。石子翎一拳打向门板。",
        BOUND + "雷九站到石子翎身前，一拳砸向门板。",
        BOUND + "雷九一拳打中石子翎。",
        BOUND + "石子翎用嘴拔出衣领里的木签。",
        BOUND + "石子翎用脚趾抓住地上的布条。",
        BOUND + "石子翎没有出拳，只以肩膀抵住门。",
        BOUND + "石子翎回想当年挥拳的样子。",
        BOUND + "石子翎踢断悬挂灯笼的绳索。石子翎一拳打向门板。",
        BOUND + "石子翎没能挣脱。" + ACTION,
        BOUND + "雷九解开韩彦棕的绳子。" + ACTION,
        BOUND + "雷九替石子翎割断绑绳。" + ACTION,
    ],
)
def test_restraints_never_auto_rewrite_even_when_a_candidate_would_hide_facts(text):
    text += "\n\n" + TAIL
    assert scene_repair_candidates(text, BIBLE) == []
    result, raw, failed, note = repair_scene_consistency(
        None, "ch1", text, {"summary": "saved"}, True, {}, BIBLE, {"number": 1}, forbidden, forbidden
    )
    assert result == text and raw == {"summary": "saved"} and failed
    assert note["status"] != "repaired"


def test_original_restraint_finding_remains_in_reference_checkup():
    text = BOUND + "\n\n" + ACTION + "\n\n" + TAIL
    assert restraint_findings(text, "石子翎")
    report = scene_consistency_checkup(BIBLE, [{"number": 1, "text": text}])
    assert report["reference_only"] and report["counts"]["acts_while_bound"] == 1
    assert scene_repair_candidates(text, BIBLE) == []


@pytest.mark.parametrize(
    "text",
    [
        "雷九握着铁棍。韩彦棕举起木棍。",
        "雷九丢下铁棍，换了一根木棍。",
        "雷九握着铁棍。那根铁棍像木棍一样轻。",
        "雷九握着铁棍。法术将它变成了一根木棍。",
        "雷九握紧这根铁棍。下一刻，这根木棍被他举过头顶。",
        "雷九把这根铁棍放在架上。雷九走到后院，拔起那根木棍。",
        FIRST + "韩彦棕从墙边走了过来。下一刻，他手里的木棍被举过头顶。",
        FIRST + "韩彦棕从墙边走了过来。" + LATER,
        FIRST + "韩彦棕举起原先那根木棍。",
        "雷九并未握着这根铁棍。" + LATER,
        "雷九看见这根铁棍。" + LATER,
        "雷九握着这根铁棍模型。" + LATER,
        FIRST + "雷九举起同一根木棍模型。",
        FIRST + "雷九举起另一根木棍。",
        FIRST + "雷九举起原先那根木棍，又将另一根木棍放下。",
        "“雷九握着这根铁棍。”" + LATER,
        FIRST + "“雷九举起原先那根木棍。”",
    ],
)
def test_ambiguous_material_ownership_or_object_identity_stays_reference(text):
    text += "\n\n" + TAIL
    assert scene_repair_candidates(text, BIBLE) == []
    result, _, _, note = repair_scene_consistency(
        None, "ch1", text, {}, False, {}, BIBLE, {"number": 1}, forbidden, forbidden
    )
    assert result == text and note["status"] != "repaired"


@pytest.mark.parametrize("name", ["雷九", "陌生甲"])
@pytest.mark.parametrize(("first", "later"), [("铁棍", "木棍"), ("铜刀", "竹刀"), ("铁剑", "银剑")])
def test_explicit_same_object_and_same_named_holder_are_the_only_material_candidates(name, first, later):
    bible = {"characters": [{"name": name}]}
    text = f"{name}握着这根{first}。\n\n{name}举起同一根{later}，撞开门板。\n\n{TAIL}"
    (hit,) = scene_repair_candidates(text, bible)
    assert hit["kind"] == "material_change" and hit["name"] == name
    assert hit["first_quote"] in text and hit["quote"] in text


@pytest.mark.parametrize("suffix", ["", "\n", "\r\n"])
def test_exact_material_replacement_preserves_all_other_bytes_and_readback(tmp_path, suffix):
    original = TEXT + suffix
    (result, _, failed, note), calls = run(original)
    assert note["status"] == "repaired" and not failed
    assert result == original.replace(LATER, EXPECTED, 1)
    assert calls == ["ch1-scene-consistency-state"]
    adopted = tmp_path / "adopted.md"
    adopted.write_text(result, newline="")
    reread = adopted.read_bytes().decode()
    assert reread == result
    assert reread[_paragraph_spans(reread)[-1][0] :] == original[_paragraph_spans(original)[-1][0] :]


@pytest.mark.parametrize(
    "replacement",
    [
        EXPECTED.replace("雷九", "韩彦棕"),
        EXPECTED.replace("雷九", "他"),
        EXPECTED.replace("雷九", ""),
        EXPECTED.replace("撞开门板", "抵住门板"),
        EXPECTED.replace("撞开门板", "没有撞开门板"),
        EXPECTED.replace("撞开门板", ""),
        "雷九放下铁棍，转而捡起木棍，撞开门板。",
        "雷九挣脱绑绳，举起原先那根铁棍，撞开门板。",
        EXPECTED + "雷九又一拳打倒守卫。",
        "雷九拿着铁棍。",
    ],
)
def test_untrusted_rewrites_are_never_requested_or_applied(replacement):
    (result, _, _, note), calls = run(answer={"edits": [{"id": 2, "text": replacement}]})
    assert result == TEXT.replace(LATER, EXPECTED, 1) and note["status"] == "repaired"
    assert calls == ["ch1-scene-consistency-state"]


@pytest.mark.parametrize(
    "answer",
    [
        {"edits": [{"id": 999, "text": EXPECTED}]},
        {"edits": [{"id": 2, "text": EXPECTED}, {"id": 3, "text": "换掉收尾。"}]},
        {"edits": [{"id": 2, "text": EXPECTED}, {"id": 2, "text": EXPECTED}]},
        {"edits": [{"id": "2", "text": EXPECTED}]},
        {"edits": [{"id": 2, "text": ""}]},
        {"edits": "invalid"},
        {"edits": []},
    ],
)
def test_model_edit_schema_has_no_path_to_change_the_text(answer):
    (result, _, _, note), calls = run(answer=answer)
    assert result == TEXT.replace(LATER, EXPECTED, 1) and note["status"] == "repaired"
    assert calls == ["ch1-scene-consistency-state"]


def test_exact_replacement_does_not_use_a_client_stage_or_an_ask_adapter():
    class NoRewriteClient:
        stage = staticmethod(forbidden)

    def extract(client, stage, *args, **kwargs):
        assert stage == "ch1-scene-consistency-state"
        return {"events": []}, False, 0

    result, _, _, note = repair_scene_consistency(
        NoRewriteClient(), "ch1", TEXT, {}, False, {}, BIBLE, {"number": 1}, extract, forbidden
    )
    assert result == TEXT.replace(LATER, EXPECTED, 1) and note["status"] == "repaired"


@pytest.mark.parametrize("paragraph", [LATER, LATER + "门外有人低低地笑了。"])
def test_conflicts_anywhere_in_the_final_paragraph_are_excluded_before_asking(paragraph):
    text = FIRST + "\n\n" + paragraph
    assert material_findings(text)
    assert scene_repair_candidates(text, BIBLE) == []
    result, _, _, note = repair_scene_consistency(
        None, "ch1", text, {}, False, {}, BIBLE, {"number": 1}, forbidden, forbidden
    )
    assert result == text and note["status"] != "repaired"


def test_invalid_state_rolls_back_the_exact_material_edit():
    (result, _, _, note), _ = run(invalid=True)
    assert result == TEXT and note["reason"] == "state_recheck_failed"


def test_empty_payoff_quote_does_not_pass_recheck():
    (result, _, _, note), _ = run(
        raw={"payoff": {"fulfilled": True, "quote": "撞开门板"}},
        checked={"events": [], "payoff": {"fulfilled": True, "quote": ""}},
    )
    assert result == TEXT and note["reason"] == "state_recheck_failed"
