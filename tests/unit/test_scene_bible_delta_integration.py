"""T6 验收: scene bible 增量接入 pipeline."""
import os
from unittest import mock

import pytest


def test_is_bible_incremental_flag_default_off():
    """Feature flag 默认 OFF。"""
    with mock.patch.dict(os.environ, {}, clear=True):
        from bestseller.services.story_bible import is_bible_incremental_enabled
        assert is_bible_incremental_enabled() is False


def test_is_bible_incremental_flag_truthy():
    """Feature flag 在多种 truthy 值下都 ON。"""
    for truthy in ("1", "true", "yes", "on", "TRUE", " 1 "):
        with mock.patch.dict(os.environ, {"BESTSELLER_BIBLE_INCREMENTAL_ENABLED": truthy}):
            from bestseller.services.story_bible import is_bible_incremental_enabled
            assert is_bible_incremental_enabled() is True, f"truthy={truthy!r}"


def test_is_bible_incremental_flag_falsy():
    """Feature flag 在 falsy 值下 OFF。"""
    for falsy in ("0", "false", "no", "off", "FALSE", "", "  "):
        with mock.patch.dict(os.environ, {"BESTSELLER_BIBLE_INCREMENTAL_ENABLED": falsy}):
            from bestseller.services.story_bible import is_bible_incremental_enabled
            assert is_bible_incremental_enabled() is False, f"falsy={falsy!r}"


def test_filter_fresh_deltas_drops_existing_keys():
    """filter_fresh_deltas 幂等：同一 delta 第二次投递被过滤。"""
    from bestseller.services.story_bible import (
        SceneBibleDelta,
        filter_fresh_deltas,
    )
    d1 = SceneBibleDelta(
        project_id="p1", chapter_number=3, scene_number=2,
        field_path="character.char_x.arc_state", target_code="char_x",
        value="破境",
    )
    d2 = SceneBibleDelta(
        project_id="p1", chapter_number=3, scene_number=2,
        field_path="character.char_y.arc_state", target_code="char_y",
        value="升境",
    )
    fresh = filter_fresh_deltas("p1", 3, [d1, d2], seen_keys={d1.delta_key})
    assert len(fresh) == 1
    assert fresh[0].delta_key == d2.delta_key


def test_scene_bible_delta_idempotency_key_format():
    """delta_key 格式：(project_id, chapter, scene, field_path, target_code)。"""
    from bestseller.services.story_bible import SceneBibleDelta

    d = SceneBibleDelta(
        project_id="p1", chapter_number=5, scene_number=3,
        field_path="relationship.r1.trust", target_code="r1", value="+0.2",
    )
    assert d.delta_key == "p1:5:3:relationship.r1.trust:r1"


def test_scene_bible_delta_validates_required_fields():
    """空 field_path/target_code/project_id 应抛 ValueError。"""
    from bestseller.services.story_bible import SceneBibleDelta

    with pytest.raises(ValueError, match="project_id"):
        SceneBibleDelta(
            project_id="", chapter_number=1, scene_number=1,
            field_path="x", target_code="y", value="z",
        )
    with pytest.raises(ValueError, match="field_path"):
        SceneBibleDelta(
            project_id="p", chapter_number=1, scene_number=1,
            field_path="", target_code="y", value="z",
        )
    with pytest.raises(ValueError, match="target_code"):
        SceneBibleDelta(
            project_id="p", chapter_number=1, scene_number=1,
            field_path="x", target_code="", value="z",
        )


def test_collect_scene_delta_seen_keys_filters_by_project_and_chapter():
    """collect_scene_delta_seen_keys 只返回匹配 project+chapter 的 keys。"""
    from bestseller.services.story_bible import (
        SceneBibleDelta,
        collect_scene_delta_seen_keys,
    )
    d1 = SceneBibleDelta("p1", 3, 1, "f1", "t1", "v")
    d2 = SceneBibleDelta("p1", 4, 1, "f1", "t1", "v")  # different chapter
    d3 = SceneBibleDelta("p2", 3, 1, "f1", "t1", "v")  # different project
    seen = collect_scene_delta_seen_keys("p1", 3, [d1, d2, d3])
    assert d1.delta_key in seen
    assert d2.delta_key not in seen
    assert d3.delta_key not in seen
