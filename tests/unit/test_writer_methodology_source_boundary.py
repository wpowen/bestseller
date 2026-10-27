"""Optional craft cannot allocate a book's cast, emotion or pacing."""
from types import SimpleNamespace

import pytest

from bestseller.services.drafts import build_scene_draft_prompts
from bestseller.services.methodology import render_methodology_scene_rules


@pytest.mark.parametrize('chapter_number', [1, 3, 5, 47, 50])
def test_default_writer_has_no_positional_pressure_or_forced_reaction(chapter_number):
    project = SimpleNamespace(title='独行日记', slug='independent-diary', genre='日常',
        sub_genre='旅行', audience=None, language='zh-CN', target_chapters=50, metadata_json={})
    chapter = SimpleNamespace(chapter_number=chapter_number, chapter_goal='独自观察山雨', title='雨中')
    scene = SimpleNamespace(scene_number=1, title='山间停留', participants=['旅人'],
        purpose={'story':'观察山雨','emotion':'宁静'}, time_label='清晨', entry_state={'location':'山间'},
        exit_state={'weather':'雨停'}, scene_type='development', target_word_count=1100)
    style = SimpleNamespace(pov_type='third-limited', tone_keywords=['宁静'])
    _, prompt = build_scene_draft_prompts(project, chapter, scene, style, {'logline':'旅人的日常观察。'})
    for forced in ('【节奏：蓄力期】','【节奏：加速期】','【节奏：呼吸口】',
                   '【反应放大法】','至少2个角色提供反应视角','【黄金三章职责】'):
        assert forced not in prompt


@pytest.mark.parametrize('number', [1, 8, 50])
def test_opted_in_scene_rules_are_craft_not_fixed_story_recipe(number):
    prompt = render_methodology_scene_rules(chapter_number=number, is_opening=True,
                                           is_climax=True, pacing_mode='build')
    assert '不得为套用技法新增人物' in prompt
    for forced in ('第2章兑现','3+条线索','至少2个角色','制造不公/压迫/对比'):
        assert forced not in prompt
