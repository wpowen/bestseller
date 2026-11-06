"""INBOX35: stage handoffs do not depend on extracted opening locations."""

from copy import deepcopy

import pytest

from bookengine.q1_continuity import repair_opening


def bible():
    return dict(
        volumes=[
            dict(number=1, start=1, end=10, location="渡口"),
            dict(number=2, start=11, end=20, location="山城"),
            dict(number=3, start=21, end=30, location="王都"),
        ]
    )


def check(text, **kwargs):
    from bookengine.stage_transition import stage_transition

    return stage_transition(bible(), 11, text, **kwargs)


def test_stage_jump_repairs_even_without_extracted_opening_location():
    original = "林舟伏在城楼背后。\n\n守卫没有发现他。\n\n第三段逐字保留。"
    replacement = "离开渡口后，林舟赶到山城，伏在城楼背后。"
    state = dict(end_location="渡口", present=["林舟"])
    before = deepcopy(state)
    calls = []

    def ask(*args, **kwargs):
        calls.append(args[1])
        return dict(replace_count=1, paragraphs=[replacement]) if args[1].endswith("-opening-repair") else {}

    def extract(*args, **kwargs):
        pytest.fail("Opening recheck must not extract whole-chapter state")

    actual, raw, failed, facts, note = repair_opening(
        None, "ch11", original, {}, False, state, bible(), {"number": 11}, extract, ask
    )
    assert actual == replacement + original[len(original.split("\n\n")[0]) :]
    assert calls == ["ch11-boundary-reextract", "ch11-opening-repair", "ch11-opening-boundary-recheck"]
    assert note["status"] == "repaired" and not failed
    assert facts["opening_location"] is None  # planning never becomes an observed fact
    assert raw["events"] == [] and raw["opening_location"] is None and state == before
    assert note["recheck"]["evidence"] == "Inference"


@pytest.mark.parametrize(
    "text",
    ["林舟离开渡口，赶到山城。", "三天后，林舟伏在城楼背后。", "与此同时，山城的城楼上，林舟伏低身子。"],
)
def test_movement_elapsed_time_and_explicit_scene_switch_are_grounded(text):
    result = check(text)
    assert result["status"] == "present" and result["issue"] is None
    assert result["quotes"] and all(q in text[:300] for q in result["quotes"])


@pytest.mark.parametrize(
    "text",
    [
        "林舟伏在城楼背后。",
        "林舟伏在城楼背后。三日前，这里发生过争斗。他记得昨夜的谈话。",
        "林舟想起离开渡口、赶到山城的旧事。",
        "林舟说：‘三天后再离开渡口。’",
        "林舟在山城喝茶。" + "风声很轻。" * 65 + "三天后，他才离开。",
    ],
)
def test_missing_transition_is_not_hidden_by_recollection_dialogue_or_late_signal(text):
    result = check(text)
    assert result["status"] == "missing" and result["issue"] == "missing_stage_transition"
    assert all(q in text[:300] for q in result["quotes"])


def test_checks_300_characters_even_when_the_opening_has_many_short_paragraphs():
    assert check("城门关着。\n\n没有灯。\n\n半个时辰后，林舟来到山城。列队已经散了。")["status"] == "present"
    assert check("风" * 300 + "三天后，林舟来到山城。")["status"] == "missing"


@pytest.mark.parametrize(
    "text", ["他穿过三条街，在城楼下停住。", "他沿山道走了两个时辰，终于看见城楼。", "另一边，城楼下已经点起灯。"]
)
def test_transition_does_not_require_repeating_the_canonical_stage_location(text):
    assert check(text)["status"] == "present"


@pytest.mark.parametrize(
    "text", ["他并未离开渡口。", "三天后再去山城，他这样盘算。", "他准备赶到山城。", "三天后再离开渡口。"]
)
def test_negated_movement_and_future_plans_are_not_transitions(text):
    assert check(text)["status"] == "missing"


@pytest.mark.parametrize(
    "text",
    [
        "他想起渡口的旧事。半个时辰后，他来到山城。",
        "他想起渡口的旧事。转眼已过三天，林舟到了城楼下。",
        "他想起渡口的旧事。三天已经过去，林舟到了城楼下。",
        "他想起渡口的旧事。林舟穿过三条街，在城楼下停住。",
    ],
)
def test_recollection_does_not_hide_a_transition_in_the_next_sentence(text):
    assert check(text)["status"] == "present"


def test_stage_boundary_without_actual_endpoint_uses_plan_and_never_mutates_it():
    from bookengine.stage_transition import stage_transition

    design = bible()
    before = deepcopy(design)
    result = stage_transition(design, 11, "林舟伏在城楼背后。")
    assert result["previous"] == "渡口" and result["opening"] == "山城"
    assert result["chapter"] == 11 and result["status"] == "missing"
    assert stage_transition(design, 11, "")["status"] == "unknown"
    assert design == before


def test_only_changed_location_stage_starts_require_body_transition():
    from bookengine.stage_transition import stage_transition

    for number in (1, 10, 12, 20):
        assert stage_transition(bible(), number, "林舟伏在城楼背后。") is None
    assert stage_transition({}, 11, "林舟伏在城楼背后。") is None
    assert stage_transition(bible(), 11, "林舟伏在城楼背后。", "山城") is None
    design = bible()
    design["volumes"][1]["location"] = "渡口"
    assert stage_transition(design, 11, "林舟守在渡口。") is None


def test_outline_stage_contract_also_exposes_jump_when_bible_is_unavailable():
    from bookengine.stage_transition import stage_transition

    outline = dict(number=11, stage=dict(number=2, start=11, location="山城"))
    assert stage_transition({}, 11, "林舟伏在城楼背后。", "渡口", outline=outline)["status"] == "missing"
    outline = dict(number=11, stage="山城", transition="离开渡口，赶到山城。")
    assert stage_transition({}, 11, "林舟伏在城楼背后。", "渡口", outline=outline)["status"] == "missing"


def test_stage_plan_requires_nonblank_transition_on_each_later_stage_start():
    from bookengine.stage_transition import planning_transition_issues

    batch = [dict(number=n) for n in (1, 10, 11, 12, 21)]
    before = deepcopy(batch)
    issues = planning_transition_issues(bible(), batch)
    assert [i["chapters"] for i in issues] == [[11], [21]]
    assert all(i["targets"] == i["chapters"] for i in issues)
    assert batch == before
    for transition in (None, "", "  ", [], {}):
        assert planning_transition_issues(bible(), [dict(number=11, transition=transition)])
    assert not planning_transition_issues(bible(), [dict(number=11, transition="半日后为寻人赶到山城。")])
    # Even when two stages share a location the planning field is still required.
    design = bible()
    design["volumes"][1]["location"] = "渡口"
    assert planning_transition_issues(design, [dict(number=11)])


@pytest.mark.parametrize("hard_conflict", [False, True])
def test_stage_repair_rechecks_body_and_rolls_back_bad_candidates(hard_conflict):
    original = "林舟伏在城楼背后。\n\n守卫没有发现他。\n\n后文不变。"
    raw = dict(events=[])
    for replacement in ("林舟换了个姿势。", "三天后。林舟站在山城。"):

        def ask(*args, **kwargs):
            return dict(replace_count=1, paragraphs=[replacement]) if args[1].endswith("-opening-repair") else {}

        def extract(*args, **kwargs):
            pytest.fail("Opening recheck must not extract whole-chapter state")

        actual, actual_raw, failed, _, note = repair_opening(
            None,
            "ch11",
            original,
            raw,
            True,
            {"_dead": ["林舟"]} if hard_conflict else {},
            bible(),
            {"number": 11},
            extract,
            ask,
        )
        accepted = not hard_conflict and replacement.startswith("三天后")
        assert note["status"] == ("repaired" if accepted else "rolled_back")
        assert actual == (replacement + original[len(original.split("\n\n")[0]) :] if accepted else original)
        if not accepted:
            assert actual_raw is raw and failed is True
        else:
            assert failed is True  # boundary-only recheck cannot clear failed state extraction


def test_elapsed_time_satisfies_stage_check_without_legacy_location_repair():
    text = "三天后，林舟已在山城。\n\n他伏在城楼背后。"
    raw = dict(opening_location="山城", opening_quote="三天后，林舟已在山城。")

    def unexpected(*args, **kwargs):
        if args[1].endswith("-boundary-reextract"):
            return {}
        pytest.fail("a valid stage transition must not trigger the stricter non-stage movement repair")

    actual, _, _, _, note = repair_opening(
        None, "ch11", text, raw, False, {"end_location": "渡口"}, bible(), {"number": 11}, unexpected, unexpected
    )
    assert actual == text and note["status"] == "handoff_unknown"
    assert note["missing"] == ["end_location"]


def test_a_transition_repair_cannot_adopt_a_new_hard_fact_conflict():
    original = "城楼下没有声音。\n\n后文保留。"
    replacement = "三天后。林舟站在山城。"
    raw = dict(events=[])

    def ask(*args, **kwargs):
        return dict(replace_count=1, paragraphs=[replacement])

    def extract(*args, **kwargs):
        event = dict(kind="character", name="林舟", location="山城", action="stay", paragraph=1, quote=replacement)
        return dict(events=[event]), False, 0

    actual, actual_raw, _, _, note = repair_opening(
        None, "ch11", original, raw, False, {"_dead": ["林舟"]}, bible(), {"number": 11}, extract, ask
    )
    assert actual == original and actual_raw is raw and note["status"] == "rolled_back"
