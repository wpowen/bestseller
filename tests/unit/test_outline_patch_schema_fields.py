"""Narrative field patches stay typed, atomic and limited to the current window."""

import copy
from typing import get_args, get_origin

import pytest
from sqlalchemy.engine import make_url

from bestseller.domain.workflow import ChapterOutlineInput, SceneOutlineInput
from bestseller.services import planner
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit

CHAPTER_STRINGS = (
    "hook_type", "target_emotion", "chapter_event_role", "protagonist_inner_state",
)
CHAPTER_LISTS = (
    "chapter_concrete_actions", "chapter_object_uses", "key_reveals",
    "anti_copy_boundary_notes",
)
SCENE_STRINGS = (
    "scene_type", "time_label", "signature_image", "protagonist_state", "object_signal",
)
SCENE_LISTS = ("relationship_debts", "information_introduced", "information_held_back")
STRING_TARGETS = (
    [("chapter", field) for field in CHAPTER_STRINGS]
    + [("scene", field) for field in SCENE_STRINGS]
    + [("causal_contract", "final_state")]
)
LIST_TARGETS = (
    [("chapter", field) for field in CHAPTER_LISTS]
    + [("scene", field) for field in SCENE_LISTS]
)


@pytest.fixture(autouse=True)
def isolated_sqlite(isolate_settings_environment):
    url = make_url(load_settings().database.url)
    assert url.get_backend_name() == "sqlite" and url.database == ":memory:"


def _outline():
    return {
        "chapters": [{
            "chapter_number": 1, "volume_number": 1, "title": "The register",
            "goal": "Compare the dates in the register.",
            "main_conflict": "The depot closes at dusk.",
            "hook_description": "A receipt carries a different date.",
            "causal_contract": {"pressure": "The depot closes at dusk."},
            "scenes": [{"scene_number": 1, "participants": ["Courier"],
                        "purpose": {"story": "Read the register."}}],
        }],
        "_meta": {"source_hash": "preserve-source"},
    }


def _target(outline, scope):
    chapter = outline["chapters"][0]
    if scope == "scene":
        return chapter["scenes"][0]
    return chapter["causal_contract"] if scope == "causal_contract" else chapter


def _patch(scope, field, value):
    return {"scope": scope, "field": field, "value": value,
            "chapter_number": 1, **({"scene_number": 1} if scope == "scene" else {})}


@pytest.mark.parametrize("scope,field", STRING_TARGETS)
def test_new_string_fields_replace_only_the_named_field(scope, field):
    original = _outline()
    _target(original, scope)[field] = "Old note."
    before = copy.deepcopy(original)
    expected = copy.deepcopy(original)
    _target(expected, scope)[field] = "A corrected note."
    assert planner._apply_outline_commercial_patches(
        original, [_patch(scope, field, "A corrected note.")], strict=True,
    ) == expected
    assert original == before


@pytest.mark.parametrize("scope,field", LIST_TARGETS)
@pytest.mark.parametrize("value", [["Read the register.", "Compare the dates."], []])
def test_new_lists_replace_and_can_clear_rejected_contents(scope, field, value):
    original = _outline()
    _target(original, scope)[field] = ["Rejected old note."]
    before = copy.deepcopy(original)
    expected = copy.deepcopy(original)
    _target(expected, scope)[field] = value
    assert planner._apply_outline_commercial_patches(
        original, [_patch(scope, field, value)], strict=True,
    ) == expected
    assert original == before


@pytest.mark.parametrize("scope,field", LIST_TARGETS)
@pytest.mark.parametrize("value", ["prose", {"note": "text"}, ("text",), ["text", 7], [False], None])
def test_malformed_lists_fail_atomically_before_schema_coercion(scope, field, value):
    original = _outline()
    before = copy.deepcopy(original)
    with pytest.raises(planner.PlannerFallbackError, match="array of strings|value is null"):
        planner._apply_outline_commercial_patches(
            original, [_patch("chapter", "goal", "Check the receipt."),
                       _patch(scope, field, value)], strict=True,
        )
    assert original == before


@pytest.mark.parametrize("scope,field", STRING_TARGETS)
@pytest.mark.parametrize("value", [7, False, ["text"], {"note": "text"}])
def test_new_string_fields_reject_other_types_atomically(scope, field, value):
    original = _outline()
    before = copy.deepcopy(original)
    with pytest.raises(planner.PlannerFallbackError, match="must be a string"):
        planner._apply_outline_commercial_patches(
            original, [_patch("chapter", "goal", "Check the receipt."),
                       _patch(scope, field, value)], strict=True,
        )
    assert original == before


@pytest.mark.parametrize("scope,field,value", [
    ("chapter", "chapter_number", 2), ("chapter", "volume_number", 2),
    ("chapter", "title", "Replacement title"), ("chapter", "scenes", []),
    ("chapter", "world_asset_refs", ["new-reference"]),
    ("chapter", "location_refs", ["new-reference"]),
    ("scene", "scene_number", 2), ("scene", "title", "New title"),
    ("scene", "location_ref", "new-reference"),
    ("chapter", "invented_schema_extra", "new rule"),
])
def test_identity_order_references_and_unknown_fields_remain_closed(scope, field, value):
    original = _outline()
    before = copy.deepcopy(original)
    with pytest.raises(planner.PlannerFallbackError, match="outside the repair protocol"):
        planner._apply_outline_commercial_patches(
            original, [_patch(scope, field, value)], strict=True,
        )
    assert original == before


def test_protocol_matches_real_domain_types_and_advertises_list_shapes():
    protocol = planner._outline_patch_protocol(_outline())
    for scope, fields, model in (
        ("chapter", CHAPTER_LISTS, ChapterOutlineInput),
        ("scene", SCENE_LISTS, SceneOutlineInput),
    ):
        for field in fields:
            annotation = model.model_fields[field].annotation
            assert get_origin(annotation) is list and get_args(annotation) == (str,)
            assert field in protocol["fields"][scope]
            assert protocol["value_types"][field] == "array of strings"
    for scope, field in STRING_TARGETS:
        assert field in protocol["fields"][scope]
        if scope != "causal_contract":
            model = ChapterOutlineInput if scope == "chapter" else SceneOutlineInput
            annotation = model.model_fields[field].annotation
            assert annotation is str or str in get_args(annotation)
