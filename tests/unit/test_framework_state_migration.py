from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.content_origin_policy import origin_record
from bestseller.services.framework_state_migration import migrate_project_prompt_state
from bestseller.services.invariants import seed_invariants, invariants_to_dict, project_invariants_from_row
from bestseller.services.prompt_source_policy import adopt_story_package, load_adopted_story_package, stamp_reference_cache
from bestseller.services.writing_profile import get_project_writing_profile
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def book():
    return SimpleNamespace(id=uuid4(), genre="现实生活", sub_genre=None, language="zh-CN", audience="general",
                           slug="independent-example", metadata_json={}, invariants_json=None)


def test_unknown_history_is_preserved_but_not_rendered_as_author_requirement():
    p = book()
    raw = {"character": {"romance_mode": "关系必须永远猜疑"}}
    p.metadata_json = {"writing_profile": deepcopy(raw), "growth_curve": "自动升级", "distilled_strategy_blocks": {"world": "旧门派"}}
    result = migrate_project_prompt_state(p, apply=False)
    assert result["changed"] and p.metadata_json["writing_profile"] == raw
    migrate_project_prompt_state(p, apply=True)
    assert p.metadata_json["writing_profile"] == raw
    assert get_project_writing_profile(p).character.romance_mode == "未指定"
    assert "distilled_strategy_blocks" not in p.metadata_json and "growth_curve" not in p.metadata_json
    snapshot = deepcopy(p.metadata_json)
    assert not migrate_project_prompt_state(p, apply=True)["changed"]
    assert p.metadata_json == snapshot


def test_author_choice_survives_migration_and_read():
    p = book(); value = "双向信任与共同成长"
    p.metadata_json = {"writing_profile": {"character": {"romance_mode": value}},
                       "writing_profile_origins": {"character.romance_mode": origin_record(value, kind="explicit")}}
    migrate_project_prompt_state(p, apply=True)
    assert get_project_writing_profile(p).character.romance_mode == value


def test_conflicting_growth_projection_is_archived_before_replacement():
    p = book(); chosen = "分阶段学会协作"
    p.metadata_json = {"writing_profile":{"character":{"growth_curve":chosen}},
        "writing_profile_origins":{"character.growth_curve":origin_record(chosen,kind="explicit")},
        "growth_curve":"旧来源的升级曲线"}
    migrate_project_prompt_state(p,apply=True)
    assert p.metadata_json["growth_curve"] == chosen
    assert any(row["source_values"].get("growth_curve")=="旧来源的升级曲线"
               for row in p.metadata_json["superseded_prompt_sources"].values())
    assert not migrate_project_prompt_state(p,apply=True)["changed"]


def test_reference_fingerprint_tracks_reader_contract_and_project_fields():
    from bestseller.services.prompt_source_policy import project_input_hash,valid_reference_value
    p=book();p.metadata_json["distilled_strategy_blocks"]={"world":"可选技法"}
    stamp_reference_cache(p,("distilled_strategy_blocks",),producer="strategy_compiler")
    before=project_input_hash(p)
    p.reader_contract_json={"core_fantasy":"新的承诺"}
    assert project_input_hash(p)!=before
    assert valid_reference_value(p,"distilled_strategy_blocks") is None


def test_old_unverified_effects_are_archived_without_touching_identity():
    p = book()
    raw = invariants_to_dict(seed_invariants(project_id=p.id, language=p.language,
        words_per_chapter=load_settings(env={}).generation.words_per_chapter))
    raw["opening_archetype_pool"] = ["humiliation"]
    p.invariants_json = deepcopy(raw)
    assert not project_invariants_from_row(p).opening_archetype_pool
    migrate_project_prompt_state(p, apply=True)
    assert p.invariants_json["project_id"] == raw["project_id"]
    assert p.invariants_json["opening_archetype_pool"] == []
    assert any(row["original_invariants"] == raw for row in p.metadata_json["framework_state_migrations"].values())
    assert not migrate_project_prompt_state(p, apply=True)["changed"]


def test_adopted_package_is_bound_to_project_and_bytes(tmp_path):
    p = book(); q = book(); path = tmp_path / "package.json"
    path.write_text('{"book":{"characters":[{"name":"许松"}]}}')
    adopt_story_package(p, path, requested_by="fixture-author")
    assert load_adopted_story_package(p)["book"]["characters"][0]["name"] == "许松"
    q.metadata_json = deepcopy(p.metadata_json)
    assert load_adopted_story_package(q) == {}
    path.write_text('{"book":{"characters":[{"name":"別人"}]}}')
    assert load_adopted_story_package(p)["book"]["characters"][0]["name"] == "许松"
    path.unlink()
    assert load_adopted_story_package(p)["book"]["characters"][0]["name"] == "许松"


def test_stamped_reference_survives_migration_but_not_changed_intent():
    p = book(); p.metadata_json["distilled_strategy_blocks"] = {"world": "技法数据"}
    stamp_reference_cache(p, ("distilled_strategy_blocks",), producer="strategy_compiler")
    assert not migrate_project_prompt_state(p, apply=True)["changed"]
    p.metadata_json["creation_intent_contract"] = {"tone_preference": "light"}
    assert migrate_project_prompt_state(p, apply=True)["retired_reference_keys"] == ["distilled_strategy_blocks"]


@pytest.mark.asyncio
async def test_legacy_acceptance_is_not_fresh_materialization(monkeypatch):
    from datetime import datetime,timezone
    from bestseller.services import truth_version
    p=book();p.metadata_json={"truth_version":5}
    async def prior(*args,**kwargs):
        return SimpleNamespace(id=uuid4(),created_at=datetime.now(timezone.utc),metadata_json={
            "truth_version":5,"legacy_truth_acceptance":{"mode":"legacy_compatibility"},
        })
    monkeypatch.setattr(truth_version,"_latest_completed_workflow_run",prior)
    statuses=await truth_version.get_truth_materialization_statuses(object(),p)
    assert statuses and all(row.status=="stale" for row in statuses)
