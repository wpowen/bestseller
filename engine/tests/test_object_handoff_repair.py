"""High-confidence same-object handoff checks and local scene rollback."""

from pathlib import Path

from bookengine.scene_repair import object_handoff_findings, repair_scene_consistency, scene_repair_candidates


BIBLE = {"characters": [{"name": "甲"}, {"name": "乙"}]}
TAIL = "\n\n院门外的风一直没有停。"


def test_same_specific_object_picked_by_another_actor_is_a_candidate():
    text = "甲捡起灰册。\n\n乙弯腰捡起灰册，只看封面。\n\n乙把灰册还给甲。" + TAIL
    (hit,) = object_handoff_findings(text, BIBLE)
    assert hit["first_actor"] == "甲" and hit["actor"] == "乙"
    assert hit["object"] == "灰册"
    assert hit["repairable"] is True
    assert hit["replacement"] == "乙从甲手里取过灰册，只看封面。"
    assert scene_repair_candidates(text, BIBLE) == [hit]


def test_explicit_exchange_or_drop_allows_a_later_pickup():
    exchanged = "甲捡起灰册。\n\n甲把灰册递给乙，乙接过灰册。\n\n乙捡起灰册。" + TAIL
    dropped = "甲捡起灰册。\n\n甲放下灰册，乙再捡起灰册。" + TAIL
    assert object_handoff_findings(exchanged, BIBLE) == []
    assert object_handoff_findings(dropped, BIBLE) == []


def test_indefinite_same_class_objects_are_left_ambiguous():
    text = "甲捡起一本册子。\n\n乙捡起一本册子。" + TAIL
    assert object_handoff_findings(text, BIBLE) == []


def test_explicit_same_object_is_not_discarded_as_indefinite():
    text = "甲捡起灰册。\n\n乙捡起同一本灰册。\n\n乙把灰册还给甲。" + TAIL
    assert len(object_handoff_findings(text, BIBLE)) == 1


def test_without_later_holder_or_return_evidence_it_stays_unconfirmed():
    text = "甲捡起灰册。\n\n乙弯腰捡起灰册，只看封面。" + TAIL
    findings = object_handoff_findings(text, BIBLE)
    assert len(findings) == 1 and findings[0]["repairable"] is False
    assert scene_repair_candidates(text, BIBLE) == []


def test_return_direction_and_event_polarity_must_be_explicit():
    cases = [
        "乙没把灰册还给甲。",
        "甲命令乙把灰册还给甲。",
        "“乙把灰册还给甲。”",
        "甲把灰册塞回乙手里。",
        "乙把另一本灰册还给甲。",
        "翌日乙把灰册还给甲。",
    ]
    for returned in cases:
        text = f"甲捡起灰册。\n\n乙弯腰捡起灰册。\n\n{returned}" + TAIL
        assert scene_repair_candidates(text, BIBLE) == []


def test_scene_break_or_non_subject_pickup_stays_unconfirmed():
    scene_break = "甲捡起灰册。\n\n次日乙弯腰捡起灰册。\n\n乙把灰册还给甲。" + TAIL
    ambiguous = "甲捡起钱包。\n\n甲看见乙身边的钱包，弯腰捡起。\n\n甲把钱包还给乙。" + TAIL
    assert scene_repair_candidates(scene_break, BIBLE) == []
    assert scene_repair_candidates(ambiguous, BIBLE) == []


def test_only_direct_named_return_authorizes_the_automatic_candidate():
    base = "青鸟捡起灰册。\n\n白石弯腰捡起灰册。\n\n{}\n\n尾。"
    direct = base.format("白石把灰册还给青鸟。")
    indirect = [
        base.format("白石把灰册还给青鸟的女儿。"),
        base.format("周围传来吟唱：“南山栖鸟。白石把灰册还给青鸟。”"),
        base.format('有人宣读记录："案中有三人。白石把灰册还给青鸟。"'),
        base.format("白石放下灰册。次日白石把灰册还给青鸟。"),
        base.replace("青鸟捡起", "青鸟的女儿捡起").format("白石把灰册还给青鸟。"),
        base.format("假如白石把灰册还给青鸟，事情就简单了。"),
        base.format("白石的丫头把灰册还给青鸟。"),
        base.format("赤云拿走灰册，交给白石。\n\n白石把灰册还给青鸟。"),
        base.format("青鸟的女儿把灰册还给青鸟。"),
    ]
    assert len(scene_repair_candidates(direct, {"characters": [{"name": "青鸟"}, {"name": "白石"}]})) == 1
    assert all(
        scene_repair_candidates(text, {"characters": [{"name": name} for name in ("青鸟", "白石", "赤云")]}) == []
        for text in indirect
    )


def test_unconfirmed_handoff_is_saved_for_review_without_changing_or_rechecking():
    text = "甲捡起灰册。乙弯腰捡起灰册。" + TAIL
    result, raw, failed, note = repair_scene_consistency(
        None, "ch1", text, {"summary": "saved"}, False, {}, BIBLE, {"number": 1}, None, None
    )
    assert result == text and not failed
    assert note["edits"] == [] and note["review_only"][0]["replacement"] is None


def test_quotes_and_negation_are_not_pickup_events():
    text = "“甲捡起灰册。”\n\n乙没有捡起灰册。" + TAIL
    assert object_handoff_findings(text, BIBLE) == []


def test_recheck_accepts_local_neutralization_and_preserves_other_bytes():
    text = "甲捡起灰册。\n\n乙弯腰捡起灰册，只看封面。\n\n乙把灰册还给甲。" + TAIL
    calls = []

    def extract(_client, stage, candidate, *_args, **_kwargs):
        calls.append((stage, candidate))
        return {"events": []}, False, 0

    result, raw, failed, note = repair_scene_consistency(
        None, "ch1", text, {"summary": "saved"}, False, {}, BIBLE, {"number": 1}, extract, None
    )
    assert result == "甲捡起灰册。\n\n乙从甲手里取过灰册，只看封面。\n\n乙把灰册还给甲。" + TAIL
    assert raw == {"events": []} and not failed
    assert note["status"] == "repaired" and calls[0][0] == "ch1-scene-consistency-state"


def test_recheck_failure_keeps_the_draft_untouched():
    text = "甲捡起灰册。\n\n乙弯腰捡起灰册，只看封面。\n\n乙把灰册还给甲。" + TAIL

    def extract(*_args, **_kwargs):
        return {"events": ["contradiction"]}, True, 0

    result, raw, failed, note = repair_scene_consistency(
        None, "ch1", text, {"summary": "saved"}, False, {}, BIBLE, {"number": 1}, extract, None
    )
    assert result == text and raw == {"summary": "saved"} and not failed
    assert note["status"] == "rolled_back" and note["reason"] == "state_recheck_failed"


def test_real_anonymous_b1_trial_finds_the_gray_book_without_model_calls():
    path = Path(__file__).resolve().parents[2] / "output/小说质量开发验证-20261121/运行留档/B/chapter-001.body.md"
    if not path.is_file():
        return
    text = path.read_text()
    bible = {"characters": [{"name": name} for name in ("顾寒", "周厉", "沈孟遥", "石九")]}
    hits = object_handoff_findings(text, bible)
    assert len(hits) == 1
    assert hits[0]["object"] == "灰册"
    assert hits[0]["first_actor"] == "周厉" and hits[0]["actor"] == "顾寒"
    assert hits[0]["repairable"] is True
    assert "顾寒从周厉手里取过灰册时" in hits[0]["replacement"]
