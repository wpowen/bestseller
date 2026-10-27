import pytest

from bestseller.web import server


@pytest.mark.parametrize("intent,expected", [("pause", "paused"), ("stop", "stopped")])
@pytest.mark.parametrize("active,queued", [(True, 0), (False, 3), (False, 0)])
def test_operator_intent_overrides_stale_automatic_repair(intent, expected, active, queued):
    state, _ = server._reader_chapter_state(
        chapter_status="review",
        production_state="repairing",
        availability="repair_in_progress",
        auto_repair_in_progress=active,
        pending_rewrite_task_count=queued,
        production_intent=intent,
    )
    assert state == expected


@pytest.mark.parametrize("intent", ["pause", "stop"])
def test_paused_book_retains_passed_chapter_state(intent):
    assert (
        server._reader_chapter_state(
            chapter_status="complete",
            production_state="ok",
            availability="available",
            production_intent=intent,
        )[0]
        == "formal"
    )


def test_reader_state_projection_keeps_api_execution_fields():
    chapter = {
        "reader_state": "paused",
        "reader_label": "待编辑",
        "pending_rewrite_task_count": 3,
        "production_intent": "pause",
        "content_md": "private body",
    }
    assert server._reader_state_payload(chapter) == {
        "reader_state": "paused",
        "reader_label": "待编辑",
        "pending_rewrite_task_count": 3,
        "production_intent": "pause",
    }


@pytest.mark.parametrize("status", ["review", "revision", "complete"])
def test_readable_saved_draft_label_does_not_claim_review_approval(status):
    availability = server._reader_chapter_availability("ok", 1904)
    assert availability == "available"
    assert server._reader_chapter_state(
        chapter_status=status,
        production_state="ok",
        availability=availability,
    ) == ("formal", "已保存稿")


@pytest.mark.parametrize(
    "production,intent,active,queued,expected",
    [
        ("quality_debt", "run", False, 0, "quality_debt"),
        ("quality_debt", "pause", True, 2, "paused"),
        ("quality_debt", "stop", True, 2, "stopped"),
        ("quality_debt", "run", True, 0, "active_repair"),
        ("quality_debt", "run", False, 2, "queued_repair"),
        ("blocked", "run", False, 0, "blocked"),
        ("blocked", "run", False, 3, "blocked"),
        ("blocked", "run", True, 3, "active_repair"),
        ("blocked", "pause", False, 3, "paused"),
        ("blocked", "stop", False, 3, "stopped"),
        ("blocked", "pause", True, 3, "paused"),
        ("blocked", "stop", True, 3, "stopped"),
    ],
)
def test_quality_debt_is_advisory_without_changing_control_priorities(
    production, intent, active, queued, expected
):
    state, label = server._reader_chapter_state(
        chapter_status="revision",
        production_state=production,
        availability="repair_in_progress",
        production_intent=intent,
        auto_repair_in_progress=active,
        pending_rewrite_task_count=queued,
    )
    assert state == expected
    if expected == "quality_debt":
        assert label == "待编辑"


@pytest.mark.parametrize(
    "adoption,expected,label",
    [
        ("promoted", "adopted", "已采用"),
        ("accepted_with_debt", "accepted_with_debt", "已采用（含质量债）"),
    ],
)
@pytest.mark.parametrize("intent", ["run", "pause", "stop"])
@pytest.mark.parametrize("active", [False, True])
def test_verified_adoption_precedes_blocked_and_pending_repair(
    adoption, expected, label, intent, active
):
    assert server._reader_chapter_state(
        chapter_status="revision",
        production_state="blocked",
        availability="repair_in_progress",
        pending_rewrite_task_count=3,
        auto_repair_in_progress=active,
        production_intent=intent,
        adoption_state=adoption,
    ) == (expected, label)
