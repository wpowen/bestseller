from bestseller.services.judge_rubrics import get_judge_rubric


def test_load_chapter_window_rubric():
    rubric = get_judge_rubric("chapter_window")
    assert rubric.system_prompt.startswith("你是连载小说滑窗留存审稿人")
    assert "hook_health" in rubric.rubric_items


def test_rubric_prompt_block():
    rubric = get_judge_rubric("chapter_commercial")
    block = rubric.render_prompt_block()
    assert "quality_gates.yaml" in block
    assert "opening_pull" in block
