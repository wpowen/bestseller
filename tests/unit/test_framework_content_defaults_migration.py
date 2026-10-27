from copy import deepcopy

import pytest


def _policy():
    from bestseller.services import content_origin_policy

    return content_origin_policy


@pytest.mark.parametrize("name", ["余陶", "Mira", "阿霁"])
def test_migration_is_source_bound_not_value_or_name_based(name):
    p = _policy()
    raw = {
        "character": {"romance_mode": "弱感情线", "relationship_tension": name},
        "market": {"opening_strategy": "在旧码头醒来"},
    }
    origins = {
        "character.romance_mode": p.origin_record(
            raw["character"]["romance_mode"], kind="explicit"
        ),
        "character.relationship_tension": p.origin_record(name, kind="framework_default"),
    }
    before = deepcopy(raw)
    result = p.migrate_content_defaults(
        raw,
        origins=origins,
        replacements={
            "character.romance_mode": "未指定",
            "character.relationship_tension": "未指定",
            "market.opening_strategy": "未指定",
        },
    )
    assert raw == before
    assert result.payload["character"] == {
        "romance_mode": "弱感情线",
        "relationship_tension": "未指定",
    }
    assert result.payload["market"] == before["market"]
    assert result.receipt["unresolved_paths"] == ["market.opening_strategy"]
    assert result.receipt["changes"][0]["original_value"] == name
    assert result.receipt["changes"][0]["path"] == "character.relationship_tension"
    assert result.receipt["original_payload"] == before
    again = p.migrate_content_defaults(
        result.payload,
        origins=result.origins,
        replacements={"character.relationship_tension": "未指定"},
    )
    assert not again.receipt["changes"]
    assert again.payload == result.payload


def test_changed_field_does_not_reuse_old_provenance():
    p = _policy()
    result = p.migrate_content_defaults(
        {"a": "author edited"},
        origins={"a": p.origin_record("default", kind="framework_default")},
        replacements={"a": "neutral"},
    )
    assert result.payload == {"a": "author edited"}
    assert result.receipt["unresolved_paths"] == ["a"]
    assert result.receipt["changes"] == []


def test_unadopted_preset_can_migrate_but_adopted_preserved():
    p = _policy()
    result = p.migrate_content_defaults(
        {"a": ["event"], "b": ["event"]},
        origins={
            "a": p.origin_record(["event"], kind="genre_preset"),
            "b": p.origin_record(["event"], kind="genre_preset", adopted=True),
        },
        replacements={"a": [], "b": []},
    )
    assert result.payload == {"a": [], "b": ["event"]}
    assert result.receipt["preserved_paths"] == ["b"]


def test_unknown_is_not_fabricated_as_explicit():
    p = _policy()
    result = p.migrate_content_defaults({"a": "x"}, origins={}, replacements={"a": "neutral"})
    assert result.origins["a"]["kind"] == "unknown"
    assert result.payload == {"a": "x"}
    assert result.receipt["unresolved_paths"] == ["a"]


def test_new_profile_metadata_tracks_omission_separately_from_explicit():
    from bestseller.domain.project import ProjectCreate
    from bestseller.services.writing_profile import (
        build_project_metadata,
        resolve_project_create_writing_profile,
    )

    payload = ProjectCreate(
        title="抽象试验",
        slug="abstract-test",
        genre="自定义实验叙事",
        target_word_count=78000,
        target_chapters=30,
        writing_profile={"character": {"romance_mode": "弱感情线"}},
    )
    metadata = build_project_metadata(payload, resolve_project_create_writing_profile(payload))
    origins = metadata["writing_profile_origins"]
    assert origins["character.romance_mode"]["kind"] == "explicit"
    assert origins["character.relationship_tension"]["kind"] == "framework_default"


def test_profile_read_applies_only_verified_old_defaults_without_mutating_storage():
    p = _policy()
    from types import SimpleNamespace

    from bestseller.services.writing_profile import get_project_writing_profile

    metadata = {
        "writing_profile": {"character": {"romance_mode": "custom old default"}},
        "writing_profile_origins": {
            "character.romance_mode": p.origin_record(
                "custom old default", kind="framework_default"
            )
        },
    }
    before = deepcopy(metadata)
    project = SimpleNamespace(
        metadata_json=metadata,
        genre="自定义实验叙事",
        sub_genre=None,
        audience=None,
        language="zh-CN",
    )
    assert get_project_writing_profile(project).character.romance_mode == "未指定"
    assert metadata == before


def test_legacy_unknown_profile_does_not_become_explicit_by_reading():
    import json
    from types import SimpleNamespace

    from bestseller.services.writing_profile import get_project_writing_profile

    metadata = {"writing_profile": {"character": {"romance_mode": "历史值"}}}
    original = deepcopy(metadata)
    original_bytes = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    project = SimpleNamespace(
        metadata_json=metadata,
        genre="自定义实验叙事",
        sub_genre=None,
        audience=None,
        language="zh-CN",
    )
    p = _policy()
    for _ in range(2):
        profile = get_project_writing_profile(project)
        assert profile.character.romance_mode == "未指定"
        origin = profile._content_origins["character.romance_mode"]
        assert origin["kind"] == "unknown"
        assert origin["adopted"] is False
        # Provenance still describes the preserved history, not fabricated
        # author approval of either the old value or the neutral runtime view.
        assert origin["content_hash"] == p.content_hash("历史值")
        assert project.metadata_json is metadata
        assert metadata == original
        assert json.dumps(metadata, ensure_ascii=False).encode("utf-8") == original_bytes
        assert "writing_profile_origins" not in metadata

    # The same bytes are valid when the author explicitly chose/adopted them.
    # Neutralization must use provenance, never a value-based denylist.
    for kind, adopted in (("explicit", False), ("genre_preset", True)):
        selected = deepcopy(original)
        selected["writing_profile_origins"] = {
            "character.romance_mode": p.origin_record(
                "历史值", kind=kind, adopted=adopted
            )
        }
        selected_before = deepcopy(selected)
        project.metadata_json = selected
        profile = get_project_writing_profile(project)
        assert profile.character.romance_mode == "历史值"
        assert profile._content_origins["character.romance_mode"]["kind"] == kind
        assert profile._content_origins["character.romance_mode"]["adopted"] is adopted
        assert selected == selected_before


def test_invariants_migration_archives_deck_and_events_without_value_heuristics():
    p = _policy()
    from bestseller.services.invariants import migrate_invariant_content_defaults

    raw = {
        "opening_archetype_pool": ["humiliation"],
        "hype_scheme": {"recipe_deck": [{"key": "custom"}]},
        "protagonist_name": "随意名字",
    }
    origins = {
        key: p.origin_record(raw[key], kind="framework_invariant")
        for key in ("opening_archetype_pool", "hype_scheme")
    }
    result = migrate_invariant_content_defaults(raw, origins=origins)
    assert result.payload["opening_archetype_pool"] == []
    assert result.payload["hype_scheme"] != raw["hype_scheme"]
    assert result.payload["protagonist_name"] == "随意名字"
    assert result.receipt["original_payload"] == raw
    again = migrate_invariant_content_defaults(result.payload, origins=result.origins)
    assert not again.receipt["changes"]


def test_overlapping_migration_paths_are_rejected():
    p = _policy()
    with pytest.raises(ValueError, match="overlap"):
        p.migrate_content_defaults({"a": {"b": 1}}, origins={}, replacements={"a": {}, "a.b": 0})
    with pytest.raises(ValueError, match="overlap"):
        p.migrate_content_defaults(
            {"a": {"b": 1}, "a-other": 2}, origins={},
            replacements={"a": {}, "a-other": 0, "a.b": 0},
        )


def test_genre_technique_does_not_get_deleted_on_profile_read():
    from types import SimpleNamespace

    from bestseller.services.writing_profile import (
        get_project_writing_profile,
        resolve_writing_profile,
    )

    chosen = resolve_writing_profile(None, genre="仙侠")
    project = SimpleNamespace(
        metadata_json={
            "writing_profile": chosen.model_dump(mode="json"),
            "writing_profile_origins": chosen._content_origins,
        },
        genre="仙侠",
        sub_genre=None,
        audience=None,
        language="zh-CN",
    )
    reread = get_project_writing_profile(project)
    assert reread.style.prose_style == chosen.style.prose_style
    assert reread.world.rule_hardness == chosen.world.rule_hardness


def test_migration_id_binds_source_evidence():
    p = _policy()
    payload = {"a": "value"}
    unknown = p.migrate_content_defaults(payload, origins={}, replacements={"a": "neutral"})
    explicit = p.migrate_content_defaults(
        payload,
        origins={"a": p.origin_record("value", kind="explicit")},
        replacements={"a": "neutral"},
    )
    assert unknown.receipt["migration_id"] != explicit.receipt["migration_id"]


def test_resolving_a_resolved_profile_does_not_relabel_defaults_as_explicit():
    from bestseller.services.writing_profile import resolve_writing_profile

    first = resolve_writing_profile(None, genre="自定义实验叙事")
    second = resolve_writing_profile(first, genre="自定义实验叙事")
    assert second._content_origins["character.romance_mode"]["kind"] == "framework_default"
