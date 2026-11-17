"""Instruction 68: the repair tally and the new checkup sections read only saved files."""

import json

from bookengine.repair_ledger import repair_ledger
from tools.book_checkup import check_book

FIRST = (
    "石子翎走进木桩院，把那件发硬的旧衣搭在肩上，看着院子中央那根埋了半截、满是旧凹痕的震骨木桩，几个陪练都离它远远的。"
)
DRAFT = FIRST + "\n\n雷九抬起了棍。\n\n“走吧。”"
FINAL = DRAFT.replace("“走吧。”", FIRST.replace("石子翎走进木桩院", "他又走进木桩院"))


def _book(tmp_path):
    (tmp_path / "chapters").mkdir()
    (tmp_path / "steps").mkdir()
    calls = [{"stage": f"r2-ch1-{name}"} for name in ("draft", "finish", "momentum-hook-repair", "opening-repair")]
    (tmp_path / "calls.json").write_text(json.dumps(calls + [{"stage": "plan-q1-prepare"}]))
    record = {
        "polish": {"first_appearances": {"status": "rolled_back", "reason": "recheck_failed"}},
        "cleaning": {"text_hygiene": {"status": "repaired"}, "total": 0},
        "opening_promises": {"status": "rolled_back", "reason": "promise_recheck_failed"},
    }
    (tmp_path / "chapters/001.json").write_text(json.dumps(record))
    (tmp_path / "steps/r2-ch1-momentum.json").write_text(
        json.dumps({"hook": {"status": "repaired"}, "payoff": {"status": "not_due"}})
    )
    (tmp_path / "chapter-001.r2.draft.md").write_text(DRAFT)
    (tmp_path / "chapter-001.body.md").write_text(FINAL)
    (tmp_path / "story_bible.yaml").write_text("characters:\n- {name: 石子翎, role: protagonist}\n- {name: 雷九}\n")
    return tmp_path


def test_ledger_counts_calls_outcomes_and_what_repairs_left_behind(tmp_path):
    ledger = repair_ledger(_book(tmp_path))
    assert (
        ledger["calls"]["chapter_total"] == 4 and ledger["calls"]["draft"] == 1 and ledger["calls"]["after_draft"] == 3
    )
    assert ledger["outcomes"]["first_appearances"] == {"rolled_back": 1}
    assert ledger["outcomes"]["opening_promises"] == {"rolled_back": 1}
    assert ledger["outcomes"]["text_hygiene"] == {"repaired": 1}
    assert ledger["outcomes"]["momentum_hook"] == {"repaired": 1}
    assert ledger["rollback_reasons"]["first_appearances"] == {"recheck_failed": 1}
    assert ledger["draft_to_final"]["changed_chapters"] == 1
    assert ledger["draft_to_final"]["repair_paragraphs_restating_chapter"] == 1
    assert ledger["reference_only"] is True


def test_missing_files_give_an_empty_ledger(tmp_path):
    ledger = repair_ledger(tmp_path)
    assert ledger["calls"]["chapter_total"] == 0 and ledger["outcomes"] == {} and ledger["chapters_compared"] == 0


def test_checkup_carries_the_instruction_68_sections(tmp_path):
    report = check_book(_book(tmp_path), source="draft")
    assert report["premature_deaths"]["count"] == 0 and report["name_slips"]["count"] == 0
    assert set(report["scene_consistency"]["counts"]) == {"material_change", "acts_while_bound", "identity_label"}
    assert report["repair_ledger"]["calls"]["draft"] == 1
