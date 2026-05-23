from __future__ import annotations

import json

from bestseller.services.forbidden_terms_learner import (
    learn_forbidden_term_candidates,
    update_guardrails_with_candidates,
)


def test_forbidden_terms_learner_extracts_candidates_from_rejected_drafts(tmp_path) -> None:
    rejected = tmp_path / "output/book/rejected-drafts"
    rejected.mkdir(parents=True)
    (rejected / "ch51.md").write_text(
        "镜影林渊 又一次 镜影林渊。沈家旧卷 反复 出现。",
        encoding="utf-8",
    )
    (rejected / "ch52.md").write_text(
        "镜影林渊 仍然 镜影林渊。沈家旧卷 再次 出现。",
        encoding="utf-8",
    )

    candidates = learn_forbidden_term_candidates(
        rejected,
        existing_terms={"沈家旧卷"},
        top_n=5,
        min_count=2,
    )

    terms = {candidate.term for candidate in candidates}
    assert "镜影林渊" in terms
    assert "沈家旧卷" not in terms


def test_update_guardrails_writes_candidate_pool(tmp_path) -> None:
    rejected = tmp_path / "rejected"
    rejected.mkdir()
    (rejected / "ch1.md").write_text("漂移词 漂移词 漂移词", encoding="utf-8")
    candidates = learn_forbidden_term_candidates(rejected, min_count=2)

    guardrails = update_guardrails_with_candidates(tmp_path / "story-bible", candidates)

    payload = json.loads(guardrails.read_text(encoding="utf-8"))
    assert payload["forbidden_terms_candidates"][0]["term"] == "漂移词"
