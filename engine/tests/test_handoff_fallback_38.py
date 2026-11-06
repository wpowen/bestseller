"""F38 deterministic ending candidates inform writing without becoming truth."""

from bookengine.q1_continuity import advance_boundary_state, repair_opening
from bookengine.q1_extract import context


def test_failed_reextraction_produces_inference_from_known_names_in_last_600():
    text = "林舟在石桥。\n\n林舟回到旧钟铺，把门闩合上。"
    calls = []

    def ask(client, name, *args, **kwargs):
        calls.append(name)
        return {}

    *_, facts, note = repair_opening(
        None, "ch2", text, {}, False, {}, dict(locations=["旧钟铺"]), dict(number=2), None, ask
    )
    assert calls == ["ch2-boundary-reextract"]
    assert facts["end_location"] is None and note["status"] == "handoff_unknown"
    assert facts["end_location_candidate"]["status"] == "Inference"
    assert facts["end_location_candidate"]["locations"] == ["旧钟铺"]
    current = advance_boundary_state({}, facts, 2, text, dict(locations=["旧钟铺"]))
    assert "end_location" not in current
    assert current["end_location_candidate"]["locations"] == ["旧钟铺"]
    sections = context({}, {}, dict(number=1), dict(number=3), current, [], [], text)
    assert "旧钟铺" in sections[0][1] and "Inference" in sections[0][1]


def test_candidate_never_uses_a_plan_dialogue_negation_or_distant_mention():
    for text in [
        "他想起旧钟铺。",
        "他计划回旧钟铺。",
        "“明天到旧钟铺见。”",
        "他没有回到旧钟铺。",
        "旧钟铺。" + "风" * 610,
    ]:
        current = advance_boundary_state({}, {}, 2, text, dict(locations=["旧钟铺"]))
        assert not current.get("end_location_candidate")


def test_saved_grounded_end_supersedes_candidate_and_does_not_inherit_stale_location():
    state = dict(end_location="旧钟铺", end_location_candidate=dict(locations=["旧钟铺"], status="Inference"))
    known = advance_boundary_state(
        state, dict(end_location="石桥", end_quote="林舟在石桥。"), 2, "林舟在石桥。", dict(locations=["旧钟铺"])
    )
    assert known["end_location"] == "石桥" and "end_location_candidate" not in known
    missing = advance_boundary_state(state, {}, 2, "风停了。", {})
    assert "end_location" not in missing and "end_location_candidate" not in missing
