"""Offline P11 count-only replay; never imports DB or LLM entry points."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from bestseller.services.length_stability_gate import evaluate_length_control_candidate
from bestseller.services.word_targets import LENGTH_CONTROL_V1, plan_remaining_word_budget


def replay(input_path: Path, output_dir: Path) -> dict:
    rows = list(csv.DictReader(input_path.open()))
    by_key = {(r["book"], r["chapter"], r["version"]): r for r in rows}
    assert len(by_key) == len(rows) == 58
    output_dir.mkdir(parents=True, exist_ok=True)
    verdicts = []
    for row in rows:
        parent = by_key.get((row["book"], row["chapter"], row["parent_version"]))
        decision = evaluate_length_control_candidate(
            word_count=int(row["export_body_words"]),
            min_words=1800,
            target_words=2000,
            max_words=2300,
            protocol=LENGTH_CONTROL_V1,
            stage=(
                "deslop"
                if row["stage"] == "deslop"
                else "draft"
                if row["stage"] == "writer"
                else "rewrite"
            ),
            parent_word_count=int(parent["export_body_words"]) if parent else None,
        )
        verdicts.append(
            {
                "book": row["book"],
                "chapter": row["chapter"],
                "version": row["version"],
                "stage": row["stage"],
                "words": decision.word_count,
                "accepted_by_length_only": decision.accepted,
                "issue_code": decision.issue_code,
                "parent_growth_max_words": decision.parent_growth_max_words,
                "raw_sha256": row["raw_sha256"],
            }
        )
    with (output_dir / "history-verdicts.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(verdicts[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(verdicts)
    actual_prefixes, hypothetical = [], []
    for book in ("fa14", "d407"):
        adopted = ()
        for chapter in range(1, 5):
            source = next(
                r
                for r in rows
                if r["book"] == book
                and int(r["chapter"]) == chapter
                and (
                    r["is_adopted"] == "true"
                    or (book == "d407" and chapter == 4 and r["is_current"] == "true")
                )
            )
            plan = plan_remaining_word_budget(
                total_words=8000,
                total_chapters=4,
                adopted_word_counts=adopted,
                protocol=LENGTH_CONTROL_V1,
            )
            decision = (
                evaluate_length_control_candidate(
                    word_count=int(source["export_body_words"]),
                    **asdict(plan.next_band),
                    protocol=LENGTH_CONTROL_V1,
                )
                if plan.feasible
                else None
            )
            actual_prefixes.append(
                {
                    "book": book,
                    "chapter": chapter,
                    "historical_prefix_words": sum(adopted),
                    "remaining_targets": plan.targets,
                    "next_band": asdict(plan.next_band) if plan.next_band else None,
                    "historical_source_words": int(source["export_body_words"]),
                    "historical_source_is_adopted": source["is_adopted"] == "true",
                    "plan_feasible": plan.feasible,
                    "length_accepted": decision.accepted if decision else False,
                    "issue_code": decision.issue_code if decision else plan.issue_code,
                }
            )
            # Diagnostic only: grandfather the real prefix to inspect the next step.
            # A rejected historical candidate is NOT adopted by the v1 policy.
            if source["is_adopted"] == "true":
                adopted += (int(source["export_body_words"]),)
        simulated = ()
        replacements = 0
        events = []
        for chapter in range(1, 5):
            first = by_key[(book, str(chapter), "1")]
            plan = plan_remaining_word_budget(
                total_words=8000,
                total_chapters=4,
                adopted_word_counts=simulated,
                protocol=LENGTH_CONTROL_V1,
            )
            assert plan.feasible
            decision = evaluate_length_control_candidate(
                word_count=int(first["export_body_words"]),
                **asdict(plan.next_band),
                protocol=LENGTH_CONTROL_V1,
            )
            # Counts only: assume a rejected writer's ONE replacement hits the aim.
            # This is no rewritten text and no prediction that a model will do so.
            assumed_words = (
                decision.word_count if decision.accepted else plan.next_band.target_words
            )
            replacements += not decision.accepted
            simulated += (assumed_words,)
            events.append(
                {
                    "chapter": chapter,
                    "historical_first_words": decision.word_count,
                    "new_band": asdict(plan.next_band),
                    "initial_length_accepted": decision.accepted,
                    "assumed_replacement_words": assumed_words if not decision.accepted else None,
                }
            )
        final = plan_remaining_word_budget(
            total_words=8000,
            total_chapters=4,
            adopted_word_counts=simulated,
            protocol=LENGTH_CONTROL_V1,
        )
        assert final.feasible
        hypothetical.append(
            {
                "book": book,
                "scenario": "one_replacement_hits_aim_not_model_evidence",
                "events": events,
                "assumed_total": sum(simulated),
                "assumed_replacements": replacements,
                "book_tolerance_satisfied": final.feasible,
            }
        )
    summary = {
        "protocol": LENGTH_CONTROL_V1,
        "count_unit": "export-body-words-v1",
        "p11_input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "rows": len(rows),
        "initials_over_2300": sum(
            r["version"] == "1" and int(r["export_body_words"]) > 2300 for r in rows
        ),
        "deslop_growth_over_5pct": sum(
            r["stage"] == "deslop"
            and int(r["export_body_words"])
            > int(by_key[(r["book"], r["chapter"], r["parent_version"])]["export_body_words"])
            * 105
            // 100
            for r in rows
        ),
        "history_length_accepted": sum(v["accepted_by_length_only"] for v in verdicts),
        "fixed_historical_prefix_diagnostics": actual_prefixes,
        "hypothetical_generation_scenarios": hypothetical,
    }
    (output_dir / "replay-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    result = replay(arguments.input, arguments.output_dir)
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "rows",
                    "initials_over_2300",
                    "deslop_growth_over_5pct",
                    "history_length_accepted",
                )
            }
        )
    )
    for case in result["hypothetical_generation_scenarios"]:
        print(
            case["book"],
            "hypothetical total",
            case["assumed_total"],
            "assumed one-shot replacements",
            case["assumed_replacements"],
        )
