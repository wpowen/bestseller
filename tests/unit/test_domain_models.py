from __future__ import annotations

import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.domain.project import ProjectCreate


pytestmark = pytest.mark.unit


def test_project_create_normalizes_slug() -> None:
    project = ProjectCreate(
        slug="My-Story",
        title="My Story",
        genre="fantasy",
        target_word_count=100000,
        target_chapters=60,
    )

    assert project.slug == "my-story"


def test_project_create_rejects_invalid_slug() -> None:
    with pytest.raises(ValueError):
        ProjectCreate(
            slug="Bad Slug!",
            title="Bad Story",
            genre="fantasy",
            target_word_count=100000,
            target_chapters=60,
        )


def test_planning_artifact_create_keeps_content() -> None:
    artifact = PlanningArtifactCreate(
        artifact_type=ArtifactType.BOOK_SPEC,
        content={"logline": "A hero must survive."},
    )

    assert artifact.artifact_type is ArtifactType.BOOK_SPEC
    assert artifact.content["logline"] == "A hero must survive."
