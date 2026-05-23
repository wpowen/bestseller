from __future__ import annotations

import json

from typer.testing import CliRunner

from bestseller.cli.main import app
from bestseller.services.fanqie_short_export import export_fanqie_short_rejected_draft

runner = CliRunner()


def test_rejected_draft_export_refreshes_forbidden_term_candidates(tmp_path) -> None:
    output_dir = tmp_path / "output/book"
    story_bible = output_dir / "story-bible"
    story_bible.mkdir(parents=True)
    (story_bible / "canon-guardrails.json").write_text(
        json.dumps({"forbidden_terms": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    export_fanqie_short_rejected_draft(
        output_dir,
        title="器语者",
        genre="奇幻",
        full_text="漂移词 漂移词 漂移词 角色仍然在行动。",
        review_report={"passed": False},
        target_word_count=20,
    )

    payload = json.loads(
        (story_bible / "canon-guardrails.json").read_text(encoding="utf-8")
    )
    terms = {item["term"] for item in payload["forbidden_terms_candidates"]}
    assert "漂移词" in terms


def test_forbidden_terms_scan_cli_updates_candidate_pool(tmp_path) -> None:
    output_dir = tmp_path / "output/book"
    rejected = output_dir / "rejected-drafts"
    story_bible = output_dir / "story-bible"
    rejected.mkdir(parents=True)
    story_bible.mkdir(parents=True)
    (rejected / "ch7.md").write_text(
        "漂移词 漂移词 漂移词。白名单词 白名单词。",
        encoding="utf-8",
    )
    (story_bible / "canon-guardrails.json").write_text(
        json.dumps({"forbidden_terms_whitelist": ["白名单词"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "forbidden-terms",
            "scan",
            "--project",
            "book",
            "--output-base-dir",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(
        (story_bible / "canon-guardrails.json").read_text(encoding="utf-8")
    )
    terms = {item["term"] for item in payload["forbidden_terms_candidates"]}
    assert "漂移词" in terms
    assert "白名单词" not in terms
    assert any(
        item.get("source_chapters") == ["ch7"]
        for item in payload["forbidden_terms_candidates"]
    )


def test_forbidden_terms_review_cli_promotes_candidate(tmp_path) -> None:
    story_bible = tmp_path / "output/book/story-bible"
    story_bible.mkdir(parents=True)
    (story_bible / "canon-guardrails.json").write_text(
        json.dumps(
            {
                "forbidden_terms": [],
                "forbidden_terms_candidates": [{"term": "漂移词", "count": 3}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "forbidden-terms",
            "review",
            "--project",
            "book",
            "--output-base-dir",
            str(tmp_path / "output"),
            "--promote",
            "漂移词",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(
        (story_bible / "canon-guardrails.json").read_text(encoding="utf-8")
    )
    assert payload["forbidden_terms"] == ["漂移词"]
    assert payload["forbidden_terms_candidates"] == []
