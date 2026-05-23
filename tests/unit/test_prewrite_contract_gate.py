from __future__ import annotations

import json

from bestseller.services.prewrite_contract_gate import (
    evaluate_prewrite_contract_coverage,
)


def test_prewrite_contract_gate_reads_chapter_anchor_from_story_bible(tmp_path) -> None:
    story_bible = tmp_path / "story-bible"
    story_bible.mkdir()
    (story_bible / "prewrite-contract.json").write_text(
        json.dumps(
            {
                "chapters": {
                    "72": {
                        "prewrite_anchor": "承接 ch71 回执镜片, 推进张家开门人。",
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    verdict = evaluate_prewrite_contract_coverage(
        chapter_no=72,
        story_bible_dir=story_bible,
    )

    assert verdict.passed is True


def test_prewrite_contract_gate_blocks_missing_anchor() -> None:
    verdict = evaluate_prewrite_contract_coverage(
        chapter_no=72,
        contract={"chapters": {"72": {"prewrite_anchor": ""}}},
    )

    assert verdict.verdict == "blocked"
    assert verdict.findings[0].code == "prewrite_anchor_missing"


def test_prewrite_contract_gate_blocks_missing_chapter_contract() -> None:
    verdict = evaluate_prewrite_contract_coverage(
        chapter_no=72,
        contract={"chapters": {"71": {"prewrite_anchor": "上一章"}}},
    )

    assert verdict.verdict == "blocked"
    assert verdict.findings[0].code == "prewrite_contract_chapter_missing"
