from bestseller.infra.db.models import ChapterModel
from bestseller.services.rewrite_convergence import assess_convergence, record_rewrite_attempt


def _chapter(history=None, failures=0):
    return ChapterModel(
        chapter_number=1,
        chapter_goal="x",
        metadata_json={"rewrite_history": history or [], "rewrite_escalation_failures": failures},
    )


def test_diverging_when_block_codes_increase_3_times():
    chapter = _chapter(
        [
            {"block_codes": ["A"], "audit_codes": []},
            {"block_codes": ["A", "B"], "audit_codes": []},
        ]
    )

    state = assess_convergence(chapter, new_candidate_audit={"block_codes": ["A", "B", "C"]})

    assert state.is_diverging is True
    assert state.recommended_action == "escalate"


def test_stuck_when_same_codes_3_times():
    chapter = _chapter(
        [
            {"block_codes": ["A"], "audit_codes": []},
            {"block_codes": ["A"], "audit_codes": []},
        ]
    )

    state = assess_convergence(chapter, new_candidate_audit={"block_codes": ["A"]})

    assert state.is_stuck is True


def test_oscillating_when_codes_alternate_2_sets_4_times():
    chapter = _chapter(
        [
            {"block_codes": ["A"], "audit_codes": []},
            {"block_codes": ["B"], "audit_codes": []},
            {"block_codes": ["A"], "audit_codes": []},
        ]
    )

    state = assess_convergence(chapter, new_candidate_audit={"block_codes": ["B"]})

    assert state.is_oscillating is True


def test_recommended_action_continue_when_codes_decreasing():
    chapter = _chapter(
        [
            {"block_codes": ["A", "B"], "audit_codes": []},
            {"block_codes": ["A"], "audit_codes": []},
        ]
    )

    state = assess_convergence(chapter, new_candidate_audit={"block_codes": []})

    assert state.recommended_action == "continue"


def test_history_capped_at_10_entries():
    chapter = _chapter()
    for version in range(12):
        record_rewrite_attempt(
            chapter,
            version=version,
            block_codes=["A"],
            word_count=1,
            audit_codes=[],
        )

    assert len(chapter.metadata_json["rewrite_history"]) == 10
