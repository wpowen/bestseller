"""Regression tests for SceneOutlineInput list-field coercion.

The planner LLM (deepseek-v4-flash, MiniMax) frequently emits list-typed scene
fields as a single string — an arrow/comma-separated sequence
(``action_sequence='扫视→锁定→确认'``) or a placeholder (``relationship_debts='无'``).
Pydantic's list validation hard-failed on these, forcing the entire outline
batch to retry and wasting planning time. The model now coerces str→list.
"""

from __future__ import annotations

import pytest

from bestseller.domain.workflow import SceneOutlineInput


@pytest.mark.unit
def test_arrow_separated_string_becomes_list():
    s = SceneOutlineInput(
        scene_number=1,
        action_sequence="扫视食材→锁定参数→确认→发现异常",
    )
    assert s.action_sequence == ["扫视食材", "锁定参数", "确认", "发现异常"]


@pytest.mark.unit
@pytest.mark.parametrize("placeholder", ["无", "暂无", "没有", "None", "N/A", "-", "/"])
def test_placeholder_string_becomes_empty_list(placeholder: str):
    s = SceneOutlineInput(scene_number=1, relationship_debts=placeholder)
    assert s.relationship_debts == []


@pytest.mark.unit
def test_cjk_comma_separated_participants():
    s = SceneOutlineInput(scene_number=1, participants="苏澄、中年男人，张铁柱")
    assert s.participants == ["苏澄", "中年男人", "张铁柱"]


@pytest.mark.unit
def test_none_list_field_becomes_empty():
    s = SceneOutlineInput(scene_number=1, key_dialogue_beats=None, forbidden_actions=None)
    assert s.key_dialogue_beats == []
    assert s.forbidden_actions == []


@pytest.mark.unit
def test_proper_list_is_preserved():
    s = SceneOutlineInput(
        scene_number=1,
        action_sequence=["a", "b"],
        participants=["林渊"],
    )
    assert s.action_sequence == ["a", "b"]
    assert s.participants == ["林渊"]


@pytest.mark.unit
def test_full_batch_shape_that_previously_failed_validation():
    """The exact scene shape from the worker's 8-validation-error failure."""
    s = SceneOutlineInput(
        scene_number=1,
        scene_type="development",
        action_sequence="客人回头→追问身世→苏澄打断→悬念留存",
        relationship_debts="中年男人欠苏澄一个「身世故事」的答案",
        participants="苏澄、中年男人",
    )
    assert len(s.action_sequence) == 4
    assert s.relationship_debts == ["中年男人欠苏澄一个「身世故事」的答案"]
    assert s.participants == ["苏澄", "中年男人"]


@pytest.mark.unit
def test_scalar_scene_states_are_losslessly_wrapped_as_summaries():
    s = SceneOutlineInput(
        scene_number=1,
        entry_state="沈拓尚未记录异常",
        exit_state="沈拓落下第一档记录",
    )

    assert s.entry_state == {"summary": "沈拓尚未记录异常"}
    assert s.exit_state == {"summary": "沈拓落下第一档记录"}


@pytest.mark.unit
def test_null_scene_states_become_empty_mappings():
    s = SceneOutlineInput(scene_number=1, entry_state=None, exit_state=None)

    assert s.entry_state == {}
    assert s.exit_state == {}
