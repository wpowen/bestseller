from __future__ import annotations

from bestseller.services.chapter_entry_exit_gate import evaluate_chapter_entry_exit


def test_chapter_entry_exit_gate_passes_grounded_entries_and_exits() -> None:
    verdict = evaluate_chapter_entry_exit(
        {
            "chapter_no": 70,
            "entries": [
                {
                    "name": "苏婉宁",
                    "kind": "character",
                    "is_new": True,
                    "entry_verb": "推门",
                    "entry_context": "带着尸检报告进入镜债现场",
                }
            ],
            "exits": [{"name": "林渊", "exit_state": "拿到回执镜片"}],
        }
    )

    assert verdict.passed is True


def test_chapter_entry_exit_gate_flags_new_character_without_entry_verb() -> None:
    verdict = evaluate_chapter_entry_exit(
        {
            "chapter_no": 70,
            "entries": [
                {
                    "name": "苏婉宁",
                    "kind": "character",
                    "is_new": True,
                    "entry_context": "突然出现在现场",
                }
            ],
            "exits": [{"name": "林渊", "exit_state": ""}],
        }
    )

    assert verdict.verdict == "blocked"
    assert {"entry_verb_missing", "exit_state_missing"}.issubset(
        {finding.code for finding in verdict.findings}
    )
