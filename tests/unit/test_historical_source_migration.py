"""Historical source migration stays stable as missing provenance becomes unknown."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.content_origin_policy import origin_record
from bestseller.services.framework_state_migration import migrate_project_prompt_state

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("genre,language", [("现实生活", "zh-CN"), ("科幻", "en-US"), ("悬疑", "zh-CN")])
def test_missing_style_provenance_reaches_persisted_fixed_point_in_one_apply(genre, language):
    profile = {"style": {"prose_style": "Unverified historical craft preference"},
               "character": {"romance_mode": "Unverified relationship preference"}}
    p = SimpleNamespace(id=uuid4(), genre=genre, sub_genre=None, language=language,
                        audience="general", slug="unrelated-fixture", invariants_json=None,
                        metadata_json={"writing_profile": deepcopy(profile), "unrelated": {"keep": [1, 2]}})
    before = deepcopy(p.metadata_json)
    dry = migrate_project_prompt_state(p, apply=False)
    assert p.metadata_json == before
    first = migrate_project_prompt_state(p, apply=True)
    assert first == dry and first["changed"]
    assert p.metadata_json["writing_profile"] == profile
    assert p.metadata_json["writing_profile_origins"]["style.prose_style"]["kind"] == "unknown"
    archives = deepcopy(p.metadata_json["framework_state_migrations"])
    assert any(row["receipts"]["writing_profile"]["original_payload"] == profile
               for row in archives.values())
    persisted = deepcopy(p.metadata_json)
    second = migrate_project_prompt_state(p, apply=True)
    assert not second["changed"]
    assert p.metadata_json == persisted
    assert p.metadata_json["framework_state_migrations"] == archives
    assert p.metadata_json["unrelated"] == {"keep": [1, 2]}
    assert second["unresolved_paths"] == first["unresolved_paths"]


@pytest.mark.parametrize("adopted", [False, True])
def test_explicit_or_adopted_style_keeps_its_source_and_is_idempotent(adopted):
    value = "An independently chosen prose technique"
    evidence = origin_record(value, kind="explicit" if not adopted else "framework_default", adopted=adopted)
    p = SimpleNamespace(id=uuid4(), genre="奇幻", sub_genre=None, language="zh-CN", audience="general",
        slug="another-fixture", invariants_json=None, metadata_json={
            "writing_profile": {"style": {"prose_style": value}},
            "writing_profile_origins": {"style.prose_style": evidence}})
    migrate_project_prompt_state(p, apply=True)
    snapshot = deepcopy(p.metadata_json)
    assert p.metadata_json["writing_profile"]["style"]["prose_style"] == value
    assert p.metadata_json["writing_profile_origins"]["style.prose_style"] == evidence
    assert not migrate_project_prompt_state(p, apply=True)["changed"]
    assert p.metadata_json == snapshot
