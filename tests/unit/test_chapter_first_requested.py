from types import SimpleNamespace

from bestseller.infra.db.models import ChapterModel
from bestseller.services.pipelines import _chapter_first_requested


def _settings(threshold=3500):
    return SimpleNamespace(
        pipeline=SimpleNamespace(
            enable_chapter_first_generation=True,
            chapter_first_max_chapter_number=3,
            chapter_first_short_chapter_threshold=threshold,
        )
    )


def test_default_threshold_returns_true_for_short_chapter():
    chapter = ChapterModel(chapter_number=10, chapter_goal="x", target_word_count=2200)

    assert _chapter_first_requested(_settings(), 10, None, chapter) is True


def test_threshold_zero_disables_short_chapter_path():
    chapter = ChapterModel(chapter_number=10, chapter_goal="x", target_word_count=2200)

    assert _chapter_first_requested(_settings(0), 10, None, chapter) is False


def test_explicit_overrides_threshold():
    chapter = ChapterModel(chapter_number=10, chapter_goal="x", target_word_count=2200)

    assert _chapter_first_requested(_settings(0), 10, True, chapter) is True


def test_long_chapter_still_uses_scene_mode():
    chapter = ChapterModel(chapter_number=10, chapter_goal="x", target_word_count=5000)

    assert _chapter_first_requested(_settings(), 10, None, chapter) is False
