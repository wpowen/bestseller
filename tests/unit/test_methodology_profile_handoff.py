from types import SimpleNamespace

from bestseller.domain.project import ProjectCreate, WritingProfile
from bestseller.services.concept_methodology_agent import ConceptMethodology
from bestseller.services.planner import _concept_methodology_block
from bestseller.services.writing_profile import (
    build_project_metadata,
    render_writing_profile_prompt_block,
    resolve_project_create_writing_profile,
)


def test_selected_methodology_survives_creation_and_reaches_planner():
    methodology = ConceptMethodology(
        audience_orientation="male", mindset="METHOD_SENTINEL", source="llm"
    ).model_dump(mode="json")
    payload = ProjectCreate.model_validate({
        "slug": "methodology-handoff", "title": "test", "genre": "现实",
        "target_word_count": 6000, "target_chapters": 3,
        "writing_profile": {"concept_methodology": methodology},
    })
    profile = resolve_project_create_writing_profile(payload)
    metadata = build_project_metadata(payload, profile)
    assert metadata["writing_profile"]["concept_methodology"] == methodology
    project = SimpleNamespace(metadata_json=metadata, genre="现实")
    assert "METHOD_SENTINEL" in _concept_methodology_block(project, language="zh-CN")
    for mode in ("full", "scene"):
        assert "METHOD_SENTINEL" not in render_writing_profile_prompt_block(profile, mode=mode)


def test_new_profiles_do_not_share_methodology_or_restore_legacy_advice():
    first, second = WritingProfile(), WritingProfile()
    first.concept_methodology["mindset"] = "FIRST_BOOK_ONLY"
    assert second.concept_methodology == {}
    legacy_project = SimpleNamespace(metadata_json={
        "conception_artifacts": {"concept_methodology": first.concept_methodology}
    }, genre="现实")
    assert _concept_methodology_block(legacy_project, language="zh-CN") == ""
