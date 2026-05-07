from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.canon_guardrails import (
    canon_guardrails_from_mapping,
    load_canon_guardrails_for_project,
)

pytestmark = pytest.mark.unit


def test_parse_mapping_supports_terms_and_state_rules() -> None:
    guardrails = canon_guardrails_from_mapping(
        {
            "forbidden_terms": [
                "守夜人",
                {"term": "北马", "reason": "旧设定", "suggestion": "新势力"},
            ],
            "state_rules": [
                {
                    "subject": "小雨",
                    "status": "已获救",
                    "applies_after_chapter": 4,
                    "forbidden_patterns": ["小雨.{0,20}被困在镜子"],
                }
            ],
        }
    )

    assert [term.term for term in guardrails.forbidden_terms] == ["守夜人", "北马"]
    assert guardrails.forbidden_terms[1].reason == "旧设定"
    assert guardrails.state_rules[0].subject == "小雨"
    assert guardrails.state_rules[0].applies_after_chapter == 4
    assert guardrails.state_rules[0].forbidden_patterns == ("小雨.{0,20}被困在镜子",)


def test_load_project_guardrails_merges_metadata_and_output_file(tmp_path) -> None:
    story_bible = tmp_path / "book" / "story-bible"
    story_bible.mkdir(parents=True)
    (story_bible / "canon-guardrails.json").write_text(
        """
        {
          "forbidden_terms": [{"term": "陈守正"}],
          "state_rules": [
            {
              "subject": "周雪",
              "forbidden_patterns": ["周雪.{0,20}正在被手机吞"]
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    project = SimpleNamespace(
        slug="book",
        metadata_json={
            "canon_guardrails": {
                "forbidden_terms": [{"term": "守夜人"}],
            }
        },
    )

    guardrails = load_canon_guardrails_for_project(
        project,
        output_base_dir=tmp_path,
    )

    assert {term.term for term in guardrails.forbidden_terms} == {
        "守夜人",
        "陈守正",
    }
    assert guardrails.state_rules[0].subject == "周雪"
