"""Instruction 79: a failed relationship repair no longer stops the book (user 2026-11-20: “修补失败就降级续写”).

《扫雪剑起》 (books/webui/24ddbcc8-…) stopped before chapter 34, the fourth relationship-chapter stop in one
book. The planner left relationship_change empty; the repair model returned a usable contract for 顾寒/沈孟遥,
but scene 1 stayed a solo fight through flying swords, so the scene check voided the whole reply, nothing was
saved, and the writer stage raised “关系合同缺失：already_attempted”.
"""

from copy import deepcopy

from bookengine.chapter_rhythm import relationship_contract
from bookengine.rhythm_contract_repair import apply_rhythm_contracts
from test_contract_stop72 import CONTRACT, bible, outline


def _empty_outline():
    return dict(outline(), relationship_change={})


def _solo_fight_reply():
    scenes = deepcopy(outline()["scenes"])
    scenes[0]["action"] = "顾寒挑起碎剑片，连续穿过三轮飞剑夹击"
    scenes[0]["choice"] = "顾寒只保留两式作为起落节点"
    return dict(relationship_change=dict(CONTRACT), scenes=scenes)


def test_contract_from_a_reply_whose_scenes_fail_the_check_is_kept_and_written(tmp_path):
    from test_rhythm_completion70 import OfflineClient

    client = OfflineClient(tmp_path, [_solo_fight_reply()])
    result = apply_rhythm_contracts(client, bible(), _empty_outline(), {}, chapter=14, kind="writer")
    note = result["rhythm_contract_repair"]
    assert relationship_contract(result) == CONTRACT
    assert result["scenes"][0]["action"] == outline()["scenes"][0]["action"]  # rejected scene text not applied
    assert note["reason"] == "model_relationship_scenes_invalid" and note["degraded"] is True
    assert note["writer_ready"] is False and note["writer_proceeded_with_contract"] is True

    changed = dict(_empty_outline(), payoff="新增字段")
    again = apply_rhythm_contracts(client, bible(), changed, {}, chapter=14, kind="writer")
    assert relationship_contract(again) == CONTRACT
    assert len(client.calls) == 1


def test_no_usable_contract_still_writes_and_records_the_gap(tmp_path):
    from test_rhythm_completion70 import OfflineClient

    client = OfflineClient(tmp_path, [dict(relationship_change={})])
    result = apply_rhythm_contracts(client, bible(), _empty_outline(), {}, chapter=14, kind="writer")
    note = result["rhythm_contract_repair"]
    assert not relationship_contract(result)
    assert note["degraded"] is True and note["writer_proceeded_without_contract"] is True
    again = apply_rhythm_contracts(client, bible(), dict(_empty_outline(), payoff="x"), {}, chapter=14, kind="writer")
    assert again["rhythm_contract_repair"]["writer_proceeded_without_contract"] is True
    assert len(client.calls) == 1


def test_an_old_failed_attempt_without_a_saved_patch_does_not_stop_the_writer(tmp_path):
    """The real chapter 34 record: failed, patch {}, and the outline changed before drafting."""
    from test_rhythm_completion70 import OfflineClient
    from bookengine.storage import save

    save(
        tmp_path / "rhythm-revisions" / "contract-attempt-014.json",
        dict(
            original=dict(_empty_outline(), title="旧版"),
            note=dict(status="failed", reason="model_relationship_scenes_invalid", writer_ready=False),
            patch={},
        ),
    )
    client = OfflineClient(tmp_path, [])
    result = apply_rhythm_contracts(client, bible(), _empty_outline(), {}, chapter=14, kind="writer")
    note = result["rhythm_contract_repair"]
    assert note["reason"] == "already_attempted" and note["degraded"] is True
    assert note["writer_proceeded_without_contract"] is True and client.calls == []
