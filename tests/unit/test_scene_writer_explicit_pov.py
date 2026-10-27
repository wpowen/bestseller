"""Exercise the actual EN/ZH scene writer prompt, without patching its builder."""

from copy import deepcopy
from uuid import uuid4

import pytest
from test_explicit_author_pov import project_case

from bestseller.infra.db.models import ChapterModel, SceneCardModel
from bestseller.services.drafts import build_scene_draft_prompts
from bestseller.services.writing_profile import get_project_runtime_writing_profile

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("language,header", [("en", "POV: "), ("zh-CN", "视角：")])
@pytest.mark.parametrize("raw_profile", [True, False])
def test_real_scene_writer_prompts_explicit_first_agrees_with_profile(
    language, header, raw_profile
):
    project, style = project_case(raw_profile=raw_profile)
    project.language = language
    before = deepcopy(project.metadata_json)
    chapter = ChapterModel(
        id=uuid4(), project_id=project.id, chapter_number=7, title="Winter signal",
        chapter_goal="Record the next observation.", information_revealed=[],
        information_withheld=[], foreshadowing_actions={}, metadata_json={},
        target_word_count=2400,
    )
    scene = SceneCardModel(
        id=uuid4(), project_id=project.id, chapter_id=chapter.id, scene_number=1,
        scene_type="investigation", title="An observation", time_label="now",
        participants=["Mira"], purpose={"story": "Check the signal"},
        entry_state={}, exit_state={}, key_dialogue_beats=[], sensory_anchors={},
        forbidden_actions=[], metadata_json={}, target_word_count=2400,
    )
    profile = get_project_runtime_writing_profile(project, style)
    system, user = build_scene_draft_prompts(
        project, chapter, scene, style,
        context_budget_tokens=100000, total_input_budget_tokens=100000,
    )
    assert profile.style.pov_type == "first-person"
    assert header + profile.style.pov_type + "\n" in user
    assert header + "third-limited" not in user
    assert "third-limited" not in system + user
    assert project.metadata_json == before
    assert style.pov_type == "third-limited"
