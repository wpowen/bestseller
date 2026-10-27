from types import SimpleNamespace

import pytest

from bestseller.services import reviews
from bestseller.settings import load_settings


def result():
    return SimpleNamespace(
        scores=SimpleNamespace(
            overall=0.54,
            goal=0.85,
            coverage=0.90,
            coherence=0.80,
            style=0.80,
            duplication_score=1.0,
        )
    )


def findings():
    return [
        SimpleNamespace(category=category, severity="high")
        for category in ("continuity", "ending_hook_effectiveness", "contract_alignment")
    ]


def test_dismissed_common_sense_uses_existing_advisory_verdict():
    verdict = reviews._chapter_verdict_after_adjudication(
        result(),
        findings(),
        settings=load_settings(),
        genre="现实百态",
        sub_genre="现实百态",
        chapter_number=2,
    )
    assert verdict == "pass"


@pytest.mark.parametrize("category", ["common_sense", "output_hygiene", "character_consistency"])
def test_undismissed_structural_findings_still_require_rewrite(category):
    kept = [*findings(), SimpleNamespace(category=category, severity="high")]
    assert (
        reviews._chapter_verdict_after_adjudication(
            result(),
            kept,
            settings=load_settings(),
            genre="现实百态",
            sub_genre="现实百态",
            chapter_number=2,
        )
        == "rewrite"
    )


def test_legacy_mode_still_blocks_echo_findings():
    settings = load_settings()
    settings.quality.chapter_verdict_advisory_echo_axes = False
    assert (
        reviews._chapter_verdict_after_adjudication(
            result(),
            findings(),
            settings=settings,
            genre="现实百态",
            sub_genre="现实百态",
            chapter_number=2,
        )
        == "rewrite"
    )


def test_later_chapter_still_requires_core_score_and_duplication_floor():
    reviewed = result()
    reviewed.scores.duplication_score = 0.2
    assert (
        reviews._chapter_verdict_after_adjudication(
            reviewed,
            [],
            settings=load_settings(),
            genre="现实百态",
            sub_genre="现实百态",
            chapter_number=4,
        )
        == "rewrite"
    )


def test_adjudication_branch_calls_shared_policy():
    import inspect
    import textwrap

    source = inspect.getsource(reviews.review_chapter_draft)
    start = source.index("            _new_verdict = _chapter_verdict_after_adjudication(")
    end = source.index("            # Record dismissed common-sense codes", start)
    namespace = dict(vars(reviews))
    namespace.update(
        review_result=result(),
        _kept=findings(),
        settings=load_settings(),
        project=SimpleNamespace(genre="现实百态", sub_genre="现实百态"),
        chapter=SimpleNamespace(chapter_number=2),
    )
    exec(textwrap.dedent(source[start:end]), namespace)
    assert namespace["_new_verdict"] == "pass"
