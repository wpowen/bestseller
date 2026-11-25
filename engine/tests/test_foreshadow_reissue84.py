"""Unverified foreshadow payoffs get one follow-up window, then go to review instead of the writer."""

from bookengine.story_design import SPEC, normalize
from bookengine.story_duties import all_duties, due_duties
from bookengine.story_report import duty_summary
from test_story_design61 import make_bible, model_design

FORESHADOW = ("plant", "remind", "payoff", "truth")


def _setup():
    bible = make_bible()
    design = normalize(model_design(), bible, 50)
    duties = all_duties(design, bible)
    ledger = {
        d["id"]: dict(status="unverified" if d["kind"] in FORESHADOW else "done", chapter=d["chapter"]) for d in duties
    }
    return bible, design, duties, ledger


def test_old_unverified_payoffs_do_not_fill_every_later_chapter():
    bible, design, duties, ledger = _setup()
    late = [d for d in duties if d["kind"] in ("payoff", "truth")]
    for duty in late:
        after = duty["deadline"] + SPEC["carry_chapters"] + 1
        if after > 50:
            continue
        due = {row["id"] for row in due_duties(design, bible, after, "日常", ledger)}
        assert duty["id"] not in due, (duty["id"], after)


def test_newer_truth_reveal_is_not_starved_by_older_payoffs():
    bible, design, duties, ledger = _setup()
    truth = next(d for d in duties if d["kind"] == "truth" and d["chapter"] > 25)
    ledger.pop(truth["id"])
    due = {row["id"] for row in due_duties(design, bible, truth["chapter"], "日常", ledger)}
    assert truth["id"] in due


def test_unverified_payoff_is_reissued_within_the_follow_up_window():
    bible, design, duties, ledger = _setup()
    payoff = next(d for d in duties if d["kind"] == "payoff")
    others = {k: dict(v, status="done") for k, v in ledger.items() if k != payoff["id"]}
    for chapter in range(payoff["deadline"] + 1, payoff["deadline"] + SPEC["carry_chapters"] + 1):
        due = {
            row["id"]
            for row in due_duties(design, bible, chapter, "日常", dict(others, **{payoff["id"]: ledger[payoff["id"]]}))
        }
        assert payoff["id"] in due, chapter


def test_reissue_that_stays_unverified_is_not_issued_again():
    bible, design, duties, ledger = _setup()
    payoff = next(d for d in duties if d["kind"] == "payoff")
    retried = dict(status="unverified", chapter=payoff["deadline"] + 1)
    others = {k: dict(v, status="done") for k, v in ledger.items() if k != payoff["id"]}
    due = {
        row["id"]
        for row in due_duties(design, bible, payoff["deadline"] + 2, "日常", dict(others, **{payoff["id"]: retried}))
    }
    assert payoff["id"] not in due


def test_report_marks_unverified_foreshadow_for_review():
    bible, design, duties, ledger = _setup()
    payoff = next(d for d in duties if d["kind"] == "payoff")
    ledger[payoff["id"]] = dict(status="unverified", chapter=payoff["deadline"], quote="他看了一眼。")
    summary = duty_summary(design, bible, ledger, 50)
    item = next(row for row in summary["open"] if row["id"] == payoff["id"])
    assert item["needs_review"] is True
    assert item["chapter"] == payoff["deadline"]
    assert item["quote"] == "他看了一眼。"
