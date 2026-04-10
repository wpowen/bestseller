from __future__ import annotations

import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.domain.project import (
    AmazonKdpPublicationProfile,
    ProjectCreate,
    PublishingProfilesConfig,
)
from bestseller.domain.story_bible import (
    CastSpecInput,
    CharacterInput,
    ConflictForceInput,
)

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


def test_amazon_kdp_publication_profile_caps_keywords_and_categories() -> None:
    with pytest.raises(ValueError, match="7 keyword"):
        AmazonKdpPublicationProfile(keywords=[f"kw-{idx}" for idx in range(8)])

    with pytest.raises(ValueError, match="3 categories"):
        AmazonKdpPublicationProfile(categories=["a", "b", "c", "d"])


def test_amazon_kdp_paperback_requires_trim_size_when_enabled() -> None:
    with pytest.raises(ValueError, match="trim_size"):
        AmazonKdpPublicationProfile(
            paperback={"enabled": True},
        )


def test_project_create_accepts_publishing_profiles() -> None:
    project = ProjectCreate(
        slug="my-story",
        title="My Story",
        genre="fantasy",
        target_word_count=100000,
        target_chapters=40,
        publishing=PublishingProfilesConfig(
            amazon_kdp=AmazonKdpPublicationProfile(
                language="en-US",
                book_title="My Story",
                author_display_name="Owen Example",
                ai_generated_text="assisted",
                ai_generated_images="none",
                categories=["Fiction / Fantasy / Epic"],
            )
        ),
    )

    assert project.publishing is not None
    assert project.publishing.amazon_kdp is not None
    assert project.publishing.amazon_kdp.language == "en-US"


# ── ConflictForceInput & CastSpecInput ────────────────────────────


def test_conflict_force_input_basic() -> None:
    force = ConflictForceInput(
        name="地方恶霸势力",
        force_type="faction",
        active_volumes=[1, 2],
        threat_description="控制主角所在地区的黑恶势力",
        escalation_path="从地方压迫到暴露与上层的勾连",
    )
    assert force.name == "地方恶霸势力"
    assert force.force_type == "faction"
    assert force.active_volumes == [1, 2]
    assert force.character_ref is None


def test_conflict_force_input_character_ref() -> None:
    force = ConflictForceInput(
        name="Boss Zhang",
        force_type="character",
        character_ref="Zhang Wei",
    )
    assert force.character_ref == "Zhang Wei"


def test_conflict_force_input_empty_active_volumes_means_all() -> None:
    force = ConflictForceInput(name="天灾", force_type="environment")
    assert force.active_volumes == []


def test_cast_spec_backward_compat_without_forces() -> None:
    spec = CastSpecInput(
        protagonist=CharacterInput(name="Hero", role="protagonist"),
        antagonist=CharacterInput(name="Villain", role="antagonist"),
    )
    assert spec.antagonist_forces == []
    assert len(spec.all_characters()) == 2


def test_cast_spec_with_antagonist_forces() -> None:
    forces = [
        ConflictForceInput(name="Local bully", force_type="character", active_volumes=[1]),
        ConflictForceInput(name="Court intrigue", force_type="systemic", active_volumes=[2, 3]),
        ConflictForceInput(name="Final boss", force_type="character", active_volumes=[4, 5]),
    ]
    spec = CastSpecInput(
        protagonist=CharacterInput(name="Hero"),
        antagonist=CharacterInput(name="Final Boss"),
        antagonist_forces=forces,
        supporting_cast=[CharacterInput(name="Ally")],
    )
    assert len(spec.antagonist_forces) == 3
    assert spec.antagonist_forces[0].active_volumes == [1]
    assert len(spec.all_characters()) == 3  # protagonist + antagonist + ally
