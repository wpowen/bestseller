from bestseller.services.prewrite_review import (
    PREWRITE_ACT_PLAN_MIN_CHAPTERS,
    required_prewrite_artifact_types,
)


def test_short_books_do_not_require_optional_act_plan() -> None:
    required = required_prewrite_artifact_types(target_chapters=20)

    assert "act_plan" not in required
    assert "volume_plan" in required
    assert "chapter_outline_batch" in required


def test_long_serials_require_act_plan() -> None:
    required = required_prewrite_artifact_types(
        target_chapters=PREWRITE_ACT_PLAN_MIN_CHAPTERS,
    )

    assert "act_plan" in required

