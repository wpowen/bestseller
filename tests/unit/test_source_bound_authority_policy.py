"""The locked creation snapshot, rather than cost style, owns source-bound routing."""

# ruff: noqa: RUF001 -- Chinese punctuation is part of the story fixtures.

import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.book_design_snapshot import build_book_design_snapshot
from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner as planner_services
from bestseller.services.planner import (
    _compile_source_bound_book_spec,
    _compile_source_bound_world_spec,
    _rebind_source_bound_cast_expansion,
    _rebind_source_bound_world_expansion,
    _source_bound_cast_needs_expansion,
    _source_bound_cast_enabled,
    _source_bound_expansion_contract,
    _source_bound_cast_expansion_prompts,
    _source_bound_quote_in_payload,
    _source_bound_evidence_options,
    _source_bound_resolve_evidence,
    _validate_source_bound_cast_expansion_payload,
    _source_bound_world_needs_expansion,
    _approve_source_bound_world_expansion,
    _review_source_bound_expansion,
)
from bestseller.services.planner import PlannerFallbackError
from test_source_bound_planning_model_completion_entry import entry as _canonical_entry

from bestseller.services.book_design import ensure_project_book_design_snapshot

canonical_planning_entry = _canonical_entry

pytestmark = pytest.mark.unit


def _project(*, cost_style: str = "minimal", genre: str = "fantasy") -> ProjectModel:
    return ProjectModel(
        id=uuid4(),
        slug="source-bound-authority-policy",
        title="Authority policy test",
        genre=genre,
        language="zh-CN",
        target_word_count=78_000,
        target_chapters=30,
        metadata_json={
            "story_enhancers": {"cost_style": cost_style},
            "book_design_snapshot_status": "locked",
            "book_design_snapshot": {
                "snapshot_id": "snapshot-1",
                "source_hash": "a" * 64,
                "protagonist": {"name": "林照"},
            },
        },
    )


@pytest.mark.parametrize("genre", ["fantasy", "science-fantasy", "romance"])
@pytest.mark.parametrize("cost_style", ["standard", "external", "minimal"])
def test_locked_snapshot_is_authoritative_for_every_cost_style_and_genre(
    genre: str, cost_style: str,
) -> None:
    assert _source_bound_cast_enabled(_project(genre=genre, cost_style=cost_style))


@pytest.mark.parametrize("genre", ["fantasy", "science-fantasy", "romance"])
@pytest.mark.parametrize("cost_style", ["standard", "external", "minimal"])
def test_source_bound_book_spec_exposes_prewrite_series_engine_contract(
    genre: str, cost_style: str,
) -> None:
    project = _project(genre=genre, cost_style=cost_style)
    book = _compile_source_bound_book_spec(project, "Premise is not an invented serial contract.")
    for key in ("reader_promise", "core_serial_engine", "first_three_chapter_hook",
                "chapter_ending_hook_strategy", "payoff_rhythm"):
        assert book["series_engine"][key] == ""
    assert book["_meta"]["planning_status"] == "projection_only"
    assert book["_meta"]["approved_complete"] is False
    assert "full_canonical_source_unavailable" in book["_meta"]["source_gaps"]
    assert "series_engine.core_serial_engine" in book["_meta"]["unresolved_fields"]


def test_source_bound_projection_preserves_two_approved_story_shapes() -> None:
    fantasy = _project(genre="fantasy", cost_style="standard")
    science_fantasy = _project(genre="science-fantasy", cost_style="external")
    for project, values in (
        (
            fantasy,
            {
                "one_liner": "守住废符铺，才能保住师父留下的最后一张符谱。",
                "wants": "守住废符铺",
                "stakes": "失去符铺与师父留下的唯一传承",
                "engine": "制符—交易—反制",
            },
        ),
        (
            science_fantasy,
            {
                "one_liner": "找回被删改的航线记录，才能洗清导航员的罪名。",
                "wants": "找回被删改的航线记录",
                "stakes": "失去航线身份与唯一证据链",
                "engine": "取证—验证—公开反制",
            },
        ),
    ):
        project.metadata_json["book_design_snapshot"]["approved_story_design"] = {
            "source_contract_hash": f"hash-{values['engine']}",
            "source_champion_id": f"champion-{values['engine']}",
            "hook_card": {"one_liner": values["one_liner"]},
            "story_spine": {
                "wants": values["wants"],
                "stakes": values["stakes"],
                "unit_engine_ref": values["engine"],
            },
            "seriality_proof": {
                "repeatable_story_unit": values["engine"],
                "unit_frequency": "每章推进一次",
                "renewal_sources": [values["engine"]],
            },
        }
        compiled = _compile_source_bound_book_spec(project, "fallback premise")
        assert compiled["logline"] == values["one_liner"]
        assert compiled["protagonist"]["external_goal"] == values["wants"]
        assert compiled["stakes"]["statement"] == values["stakes"]
        assert compiled["stakes"]["personal"] == ""
        assert compiled["series_engine"]["core_serial_engine"] == values["engine"]
        assert compiled["_meta"]["approved_complete"] is False
        assert values["engine"] in compiled["series_engine"]["renewal_sources"]
        assert compiled["series_engine"]["first_three_chapter_hook"] == ""
        assert compiled["series_engine"]["chapter_ending_hook_strategy"] == ""
        assert compiled["series_engine"]["unit_frequency"] == "每章推进一次"
        assert compiled["series_engine"]["golden_three_hooks"] == []
        assert "series_engine.first_three_chapter_hook" in compiled["_meta"]["unresolved_fields"]
        assert "series_engine.chapter_ending_hook_strategy" in compiled["_meta"]["unresolved_fields"]

    english = _project(genre="science-fantasy", cost_style="standard")
    english.language = "en"
    english.metadata_json["book_design_snapshot"]["approved_story_design"] = {
        "source_contract_hash": "hash-english",
        "source_champion_id": "champion-english",
        "hook_card": {"one_liner": "Recover the erased route record before the trial."},
        "story_spine": {"wants": "Recover the erased route record"},
        "seriality_proof": {
            "repeatable_story_unit": "Investigate, verify, and expose each route alteration",
            "renewal_sources": ["New route alterations surface"],
            "unit_frequency": "One reversal every two to four chapters",
        },
    }
    english_spec = _compile_source_bound_book_spec(english, "fallback premise")
    derived_hook = english_spec["series_engine"]["chapter_ending_hook_strategy"]
    assert "hash-english" not in derived_hook
    assert "每章" not in derived_hook
    assert english_spec["_meta"]["field_sources"]["series_engine.unit_frequency"] == "approved.seriality_proof.unit_frequency"


def test_source_bound_world_hard_limits_allow_reviewed_present_tense_entities() -> None:
    project = _project(cost_style="standard")
    world = _compile_source_bound_world_spec(project, "守住废符铺", {})
    assert world["power_system"] == {}
    assert world["rules"] == []
    assert world["history_key_events"] == []
    assert world["_meta"]["approved_complete"] is False
    assert "full_canonical_source_unavailable" in world["_meta"]["source_gaps"]


def test_source_bound_world_fallback_allows_reviewed_present_tense_named_pressure() -> None:
    world = _compile_source_bound_world_spec(_project(cost_style="standard"), "守住废符铺", {})
    assert world["factions"] == []
    assert world["locations"] == []
    assert world["_meta"]["planning_status"] == "projection_only"
    assert "world_name" in world["_meta"]["unresolved_fields"]


def test_source_bound_world_rebind_preserves_baseline_and_appends_only_new_named_entities() -> None:
    baseline = {
        "world_premise": "approved premise",
        "rules": [{"rule_id": "R1", "description": "approved rule"}],
        "locations": [{"name": "旧码头", "purpose": "approved"}],
        "factions": [{"name": "原有商会", "goal": "守住航线"}],
    }
    rebound = _rebind_source_bound_world_expansion(
        baseline,
        {
            "world_premise": "forbidden replacement",
            "rules": [{"rule_id": "BAD"}],
            "locations": [
                {"name": "旧码头", "purpose": "model overwrite"},
                {
                    "name": "临时档案馆",
                    "story_role": "current hook location",
                    "power_system": {"name": "forbidden"},
                },
                {"purpose": "missing name"},
            ],
            "factions": [
                {"name": "原有商会", "goal": "model overwrite"},
                {
                    "name": "夜航会",
                    "goal": "current pressure",
                    "history_key_events": ["forbidden"],
                },
            ],
        },
    )
    assert rebound["world_premise"] == baseline["world_premise"]
    assert rebound["rules"] == baseline["rules"]
    assert rebound["locations"] == [
        {"name": "旧码头", "purpose": "approved"},
        {"name": "临时档案馆", "story_role": "current hook location"},
    ]
    assert rebound["factions"] == [
        {"name": "原有商会", "goal": "守住航线"},
        {"name": "夜航会", "goal": "current pressure"},
    ]


def test_source_bound_cast_rebind_preserves_identity_and_completes_unknown_members() -> None:
    baseline = {
        "protagonist": {"name": "林照", "gender": "female", "pronoun_set_zh": "她", "pronoun_set_en": "she/her"},
        "antagonist": None,
        "supporting_cast": [
            {"name": "守铺人", "gender": "female", "pronoun_set_zh": "她", "pronoun_set_en": "she/her", "goal": "批准目标"},
            {"name": "未定人", "gender": "unknown", "goal": "批准目标"},
        ],
    }
    rebound = _rebind_source_bound_cast_expansion(
        baseline,
        {
            "supporting_cast": [
                {"name": "守铺人", "gender": "male", "pronoun_set_zh": "他", "pronoun_set_en": "he/him", "goal": "模型改写"},
                {"name": "未定人", "gender": "male", "pronoun_set_zh": "他", "pronoun_set_en": "he/him"},
                {"name": "新配角", "gender": "female", "pronoun_set_zh": "她", "pronoun_set_en": "she/her"},
            ],
        },
    )

    by_name = {item["name"]: item for item in rebound["supporting_cast"]}
    assert by_name["守铺人"]["gender"] == "female"
    assert by_name["守铺人"]["pronoun_set_zh"] == "她"
    assert by_name["守铺人"]["goal"] == "批准目标"
    assert by_name["未定人"]["gender"] == "male"
    assert by_name["未定人"]["pronoun_set_zh"] == "他"
    assert by_name["新配角"]["gender"] == "female"


def test_source_bound_world_expansion_prompt_is_narrow_and_has_no_richness_floor() -> None:
    project = _project(cost_style="standard")
    prompt_builder = getattr(planner_services, "_source_bound_world_expansion_prompts")
    system_prompt, user_prompt = prompt_builder(
        project,
        "守住废符铺",
        {
            "protagonist": {"name": "林照"},
            "unique_hook": "守住废符铺",
            "series_engine": {"core_serial_engine": "制符—交易—反制"},
        },
        {"locations": [{"name": "废符铺"}], "factions": [{"name": "当前阻力"}]},
    )
    assert "locations" in user_prompt and "factions" in user_prompt
    assert "只返回" in user_prompt
    assert "最少5个地点" not in user_prompt
    assert "world_spec" not in system_prompt.lower()


def test_source_bound_cast_prompt_is_narrow_but_allows_present_personhood_fields() -> None:
    project = _project(cost_style="standard")
    system_prompt, user_prompt = _source_bound_cast_expansion_prompts(
        project,
        {"protagonist": {"name": "林照"}, "unique_hook": "守住废符铺"},
        {"locations": [{"name": "废符铺"}], "factions": [{"name": "当前阻力"}]},
        {"protagonist": {"name": "林照"}, "antagonist": None, "supporting_cast": []},
    )
    assert "只补齐" in user_prompt
    assert "隐藏 Boss" in user_prompt
    assert "psych" in user_prompt or "心理" in user_prompt
    assert "gender" in user_prompt and "pronoun_set_zh" in user_prompt
    assert "只返回缺失字段和当前人物增量" in user_prompt
    assert "最小形状" in user_prompt
    assert "禁止置于顶层" in user_prompt
    assert "identity_quote_candidates_not_attribution" in user_prompt
    assert "Mix visible and hidden threats" not in user_prompt
    assert "long-form commercial fiction" not in system_prompt
    _validate_source_bound_cast_expansion_payload(
        {
            "antagonist": {
                "name": "符铺对手",
                "gender": "male",
                "pronoun_set_zh": "他",
                "pronoun_set_en": "he/him",
                "voice_profile": {"speech_register": "短句"},
                "psych_profile": {"temperament": "谨慎"},
            },
            "supporting_cast": [],
            "conflict_map": [],
        }
    )
    with pytest.raises(ValueError, match="current cast fields"):
        _validate_source_bound_cast_expansion_payload({"antagonists": [{"name": "隐藏 Boss"}]})
    with pytest.raises(ValueError, match="schema invalid"):
        _validate_source_bound_cast_expansion_payload(
            {
                "antagonist": {
                    "name": "符铺对手",
                    "gender": "male",
                    "pronoun_set_zh": "他",
                    "pronoun_set_en": "he/him",
                    "voice_profile": "短句",
                },
                "conflict_map": [
                    {"conflict_pair": "林照 vs 符铺对手", "conflict_type": "阻力"}
                ],
            }
        )

    with pytest.raises(ValueError, match="must provide a non-unknown gender"):
        _validate_source_bound_cast_expansion_payload(
            {
                "supporting_cast": [{"name": "守铺人"}],
                "conflict_map": [],
            }
        )

    with pytest.raises(ValueError, match="merged supporting_cast.*must provide a non-unknown gender"):
        _validate_source_bound_cast_expansion_payload(
            {
                "supporting_cast": [
                    {
                        "name": "新配角",
                        "gender": "female",
                        "pronoun_set_zh": "她",
                        "pronoun_set_en": "she/her",
                    }
                ],
                "conflict_map": [],
            },
            fallback={
                "protagonist": {"name": "林照", "gender": "female", "pronoun_set_zh": "她", "pronoun_set_en": "she/her"},
                "supporting_cast": [{"name": "旧角色", "gender": "unknown"}],
            },
        )


def test_source_bound_entity_quote_accepts_same_mapping_name_value_only() -> None:
    payload = [
        {"name": "Archive Wardens", "goal": "seal evidence"},
        {"name": "North Archive", "story_role": "current lead"},
    ]
    assert _source_bound_quote_in_payload("Archive Wardens： seal evidence", payload)
    assert _source_bound_quote_in_payload("North Archive: current lead", payload)
    assert not _source_bound_quote_in_payload("Wrong Name: seal evidence", payload)
    assert not _source_bound_quote_in_payload(
        "Archive Wardens: seal evidence; North Archive: current lead", payload
    )
    assert not _source_bound_quote_in_payload("Archive Wardens: invented evidence", payload)


def test_source_bound_evidence_reference_rejects_unknown_field_and_forged_quote() -> None:
    base = {"world_spec": {"factions": [{"name": "Archive Wardens", "goal": "seal evidence"}]}}
    candidate = {"world_spec": {"factions": [{"name": "Archive Wardens", "goal": "seal evidence"}]}}
    source_options, candidate_options = _source_bound_evidence_options(
        base, candidate, ["world.factions"]
    )
    source_id = source_options[0]["quote_id"]
    candidate_id = candidate_options[0]["quote_id"]
    valid, valid_ok = _source_bound_resolve_evidence(
        [{"field": "world_spec.factions", "quote_id": source_id, "source_relation": "approved"}],
        source_options,
        candidate=False,
    )
    assert valid_ok is True
    assert valid[0]["field"] == "world.factions"
    assert valid[0]["quote"] == source_options[0]["quote"]

    unknown, unknown_ok = _source_bound_resolve_evidence(
        [{"field": "world.factions", "quote_id": "c999", "source_relation": "bad"}],
        candidate_options,
        candidate=True,
    )
    assert unknown_ok is False
    assert unknown == []
    wrong_field, wrong_field_ok = _source_bound_resolve_evidence(
        [{"field": "world.locations", "quote_id": candidate_id, "source_relation": "bad"}],
        candidate_options,
        candidate=True,
    )
    assert wrong_field_ok is False
    assert wrong_field == []
    forged, forged_ok = _source_bound_resolve_evidence(
        [{"field": "world.factions", "quote_id": candidate_id, "quote": "invented", "source_relation": "bad"}],
        candidate_options,
        candidate=True,
    )
    assert forged_ok is False
    assert forged == []


def test_source_bound_candidate_quote_bindings_are_field_scoped() -> None:
    from bestseller.services.planner import _source_bound_candidate_quote_bindings

    bindings = _source_bound_candidate_quote_bindings(
        [
            {"quote_id": "c0", "field": "cast.antagonist", "quote": "周彦"},
            {"quote_id": "c1", "field": "cast.supporting_cast", "quote": "陈守拙"},
            {"quote_id": "c2", "field": "cast.supporting_cast", "quote": "吴婆子"},
        ]
    )
    assert bindings == {
        "cast.antagonist": ["c0"],
        "cast.supporting_cast": ["c1", "c2"],
    }


def test_source_bound_quote_directory_keeps_authority_paths() -> None:
    from bestseller.services.planner import _source_bound_evidence_options

    source_options, _ = _source_bound_evidence_options(
        {
            "authority_snapshot": {"story_spine": {"wants": "protect the archive"}},
            "book_spec": {"tone": "light and brisk"},
        },
        {"world_spec": {}, "cast_spec": {}},
        [],
    )
    assert {item["source_path"] for item in source_options} == {
        "authority_snapshot.story_spine.wants",
        "book_spec.tone",
    }


def test_legacy_locked_snapshot_hash_stays_stable_without_projection() -> None:
    snapshot = build_book_design_snapshot(
        creation_intent={
            "genre_intent": {"channel": "male", "genre": "xianxia"},
            "chapter_count": 30,
        },
        protagonist="林照",
        target_words=78_000,
        chapter_count=30,
    )
    legacy_payload = snapshot.model_dump(mode="json")
    legacy_payload.pop("approved_story_design", None)
    legacy_payload["source_hash"] = snapshot.canonical_hash()

    restored = type(snapshot).model_validate(legacy_payload)

    assert restored.source_hash == restored.canonical_hash()


def test_auto_ensured_snapshot_keeps_unknown_seriality_unresolved() -> None:
    project = ProjectModel(
        id=uuid4(), slug="auto-snapshot-series-engine", title="自动快照回归",
        genre="science-fantasy", language="zh-CN", target_word_count=80_000, target_chapters=12,
        metadata_json={"premise": "沈砚，一名被放逐的导航员追查被篡改的边境航线记录。",
                       "creation_protagonist_name": "沈砚"},
    )
    ensure_project_book_design_snapshot(project)
    before = dict(project.metadata_json["book_design_snapshot"])
    spec = _compile_source_bound_book_spec(project, project.metadata_json["premise"])
    assert all(not spec["series_engine"].get(key) for key in (
        "first_three_chapter_hook", "chapter_ending_hook_strategy", "payoff_rhythm"))
    assert spec["_meta"]["approved_complete"] is False
    assert spec["protagonist"]["name"] == "沈砚"
    assert project.metadata_json["book_design_snapshot"] == before


def _review_assets(project: ProjectModel) -> tuple[dict, dict, dict, dict, dict]:
    project.metadata_json["book_design_snapshot"]["approved_story_design"] = {
        "hook_card": {"one_liner": "approved hook"},
        "story_spine": {"wants": "protect the archive"},
    }
    book = {"logline": "approved hook", "protagonist": {"name": "林照"}}
    world_base = {"world_premise": "approved hook", "locations": [], "factions": []}
    cast_base = {"protagonist": {"name": "林照"}, "antagonist": None, "supporting_cast": []}
    world = {
        **world_base,
        "locations": [{"name": "North Archive", "function": "evidence"}],
        "factions": [{"name": "Archive Wardens", "goal": "seal evidence"}],
        "_meta": {"source_bound_expansion": "derived_pending_review"},
    }
    cast = {
        **cast_base,
        "antagonist": {"name": "The Broker", "goal": "erase evidence"},
        "supporting_cast": [{"name": "Mira", "role": "witness"}],
        "_meta": {"source_bound_expansion": "derived_pending_review"},
    }
    return book, world_base, cast_base, world, cast


@pytest.mark.asyncio
async def test_source_bound_expansion_review_requires_real_quotes_and_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(cost_style="standard")
    book, world_base, cast_base, world, cast = _review_assets(project)
    run_id = uuid4()

    async def fake_review(_session, _settings, request):
        import json
        import re

        assert request.logical_role == "critic"
        assert request.max_tokens_override >= 3000
        hashes = dict(re.findall(r"^(authority_snapshot_hash|candidate_hash)=([0-9a-f]{64})$", request.user_prompt, re.MULTILINE))
        assert hashes["authority_snapshot_hash"] == request.metadata["authority_snapshot_hash"]
        assert hashes["candidate_hash"] == request.metadata["candidate_hash"]

        return SimpleNamespace(
                content=json.dumps(
                    {
                "decision": "pass",
                "source_evidence": [
                    {"field": "world.locations", "quote": "approved hook", "source_relation": "from approved hook"},
                    {"field": "world.factions", "quote": "approved hook", "source_relation": "from approved hook"},
                    {"field": "cast.antagonist", "quote": "approved hook", "source_relation": "from approved hook"},
                    {"field": "cast.supporting_cast", "quote": "approved hook", "source_relation": "from approved hook"},
                ],
                "candidate_evidence": [
                    {"field": "world.locations", "quote": "North Archive", "source_relation": "derived location"},
                    {"field": "world.factions", "quote": "Archive Wardens", "source_relation": "derived faction"},
                    {"field": "cast.antagonist", "quote": "The Broker", "source_relation": "derived antagonist"},
                    {"field": "cast.supporting_cast", "quote": "Mira", "source_relation": "derived witness"},
                ],
                "feedback": "bounded review passed",
                    "authority_snapshot_hash": hashes["authority_snapshot_hash"],
                    "candidate_hash": hashes["candidate_hash"],
                    }, ensure_ascii=False
                ),
                provider="litellm",
                finish_reason="stop",
                llm_run_id=run_id,
                fallback_used=False,
            )

    monkeypatch.setattr("bestseller.services.planner.complete_text", fake_review)
    report = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert report["decision"] == "pass"
    assert report["evidence_verified"] is True
    assert report["llm_run_id"] == str(run_id)
    assert report["provider"] == "litellm"
    assert report["finish_reason"] == "stop"
    assert report["raw_response_hash"]
    assert report["authority_snapshot_hash_echo"] == report["authority_snapshot_hash"]
    assert report["candidate_hash_echo"] == report["candidate_hash"]
    assert report["candidate_hash"]


def _semantic_review_response(request: object, *, decision: str = "pass", finish_reason: str = "stop") -> SimpleNamespace:
    import json
    import re

    prompt = str(request.user_prompt)
    hashes = dict(
        re.findall(
            r"^(authority_snapshot_hash|candidate_hash)=([0-9a-f]{64})$",
            prompt,
            re.MULTILINE,
        )
    )
    # Decode the server's complete JSON value; unrelated following protocol
    # fields may evolve without becoming part of this fixture value.
    fields = json.JSONDecoder().raw_decode(prompt.split("changed_fields=", 1)[1])[0]
    candidate_options = json.JSONDecoder().raw_decode(
        prompt.split("candidate_quote_options=", 1)[1]
    )[0]
    candidate_quote_ids = {
        field: next(option["quote_id"] for option in candidate_options if option["field"] == field)
        for field in fields
    }
    return SimpleNamespace(
        content=json.dumps(
            {
                "decision": decision,
                "core_preserved": decision == "pass",
                "core_conflicts": [] if decision == "pass" else ["explicit core breach"],
                "quality_findings": [],
                "authority_snapshot_hash": hashes["authority_snapshot_hash"],
                "candidate_hash": hashes["candidate_hash"],
                "source_evidence": [
                    {"field": field, "quote_id": "s0", "source_relation": "approved hook"}
                    for field in fields
                ],
                "candidate_evidence": [
                    {"field": field, "quote_id": candidate_quote_ids[field], "source_relation": "bounded derivation"}
                    for field in fields
                ],
                "feedback": "bounded review",
            },
            ensure_ascii=False,
        ),
        provider="openai",
        finish_reason=finish_reason,
        llm_run_id=uuid4(),
        fallback_used=False,
    )


def _evidence_repair_response(
    request: object,
    *,
    malicious_decision: bool = False,
    invalid_quote: bool = False,
) -> SimpleNamespace:
    import json

    prompt = str(request.user_prompt)

    def _prompt_json(name: str, next_name: str) -> list[dict[str, str]]:
        value = prompt.split(f"{name}=", 1)[1].split(f"\n{next_name}=", 1)[0]
        return json.loads(value)

    missing_source = json.loads(
        prompt.split("missing_source_fields=", 1)[1].split("\nmissing_candidate_fields=", 1)[0]
    )
    missing_candidate = json.loads(
        prompt.split("missing_candidate_fields=", 1)[1].split("\nsource_quote_options=", 1)[0]
    )
    source_options = _prompt_json("source_quote_options", "candidate_quote_options")
    candidate_options = _prompt_json("candidate_quote_options", "candidate_payload")
    source_by_field = {field: source_options[0]["quote_id"] for field in missing_source}
    candidate_by_field = {
        field: next(option["quote_id"] for option in candidate_options if option["field"] == field)
        for field in missing_candidate
    }
    payload: dict[str, object] = {
        "source_evidence": [
            {"field": field, "quote_id": quote_id, "source_relation": "approved source"}
            for field, quote_id in source_by_field.items()
        ],
        "candidate_evidence": [
            {
                "field": field,
                "quote_id": "missing" if invalid_quote else quote_id,
                "source_relation": "bounded candidate",
            }
            for field, quote_id in candidate_by_field.items()
        ],
    }
    if malicious_decision:
        payload["decision"] = "revise"
    return SimpleNamespace(
        content=json.dumps(payload, ensure_ascii=False),
        provider="openai",
        finish_reason="stop",
        llm_run_id=uuid4(),
        fallback_used=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence_side", ["source", "candidate"])
async def test_source_bound_review_repairs_only_missing_evidence(
    monkeypatch: pytest.MonkeyPatch,
    evidence_side: str,
) -> None:
    import json

    project = _project(cost_style="standard")
    book, world_base, cast_base, world, cast = _review_assets(project)
    cast = {**cast, "protagonist": {**cast["protagonist"], "flaw": "derived present-tense flaw"}}
    calls = 0
    repair_requests: list[object] = []

    async def fake_review(_session, _settings, request):
        nonlocal calls
        calls += 1
        if calls == 1:
            response = _semantic_review_response(request)
            payload = json.loads(response.content)
            payload[f"{evidence_side}_evidence"] = [
                item for item in payload[f"{evidence_side}_evidence"] if item["field"] != "cast.protagonist"
            ]
            response.content = json.dumps(payload, ensure_ascii=False)
            return response
        repair_requests.append(request)
        return _evidence_repair_response(request)

    monkeypatch.setattr("bestseller.services.planner.complete_text", fake_review)
    report = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert calls == 2
    assert report["decision"] == "pass"
    assert report["draft_eligible"] is True
    assert report["evidence_repair_status"] == "passed"
    assert report["evidence_repair_attempts"][0]["reason"] == "evidence_repair_passed"
    unchanged_side = "candidate" if evidence_side == "source" else "source"
    assert f"missing_{unchanged_side}_fields=[]" in repair_requests[0].user_prompt


@pytest.mark.asyncio
async def test_source_bound_review_repairs_cross_field_candidate_quote_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A compact model response reusing c0 must enter the bounded repair path."""

    import json

    project = _project(cost_style="standard")
    book, world_base, cast_base, world, cast = _review_assets(project)
    calls = 0
    repair_requests: list[object] = []

    async def fake_review(_session, _settings, request):
        nonlocal calls
        calls += 1
        if calls == 1:
            response = _semantic_review_response(request)
            payload = json.loads(response.content)
            # Reproduce the observed provider behavior: the first candidate
            # quote is repeated for every changed field.
            payload["candidate_evidence"] = [
                {**item, "quote_id": "c0"}
                for item in payload["candidate_evidence"]
            ]
            response.content = json.dumps(payload, ensure_ascii=False)
            return response
        repair_requests.append(request)
        return _evidence_repair_response(request)

    monkeypatch.setattr("bestseller.services.planner.complete_text", fake_review)
    report = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert calls == 2
    assert report["decision"] == "pass"
    assert report["evidence_repair_status"] == "passed"
    assert repair_requests
    assert "candidate_quote_field_bindings=" in repair_requests[0].user_prompt
    assert '"cast.antagonist"' in repair_requests[0].user_prompt
    assert "Never reuse one candidate quote_id for a different missing field" in repair_requests[0].user_prompt


@pytest.mark.asyncio
async def test_source_bound_review_blocks_unverifiable_evidence_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    project = _project(cost_style="standard")
    book, world_base, cast_base, world, cast = _review_assets(project)
    cast = {**cast, "protagonist": {**cast["protagonist"], "flaw": "derived present-tense flaw"}}
    calls = 0

    async def fake_review(_session, _settings, request):
        nonlocal calls
        calls += 1
        if calls == 1:
            response = _semantic_review_response(request)
            payload = json.loads(response.content)
            payload["candidate_evidence"] = [
                item for item in payload["candidate_evidence"] if item["field"] != "cast.protagonist"
            ]
            response.content = json.dumps(payload, ensure_ascii=False)
            return response
        return _evidence_repair_response(request, invalid_quote=True)

    monkeypatch.setattr("bestseller.services.planner.complete_text", fake_review)
    report = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert calls == 2
    assert report["decision"] == "uncertain"
    assert report["draft_eligible"] is False
    assert report["evidence_repair_status"] == "blocked"
    assert report["evidence_repair_attempts"][0]["reason"] == "evidence_repair_unverifiable"


@pytest.mark.asyncio
async def test_source_bound_review_rejects_evidence_repair_that_changes_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    project = _project(cost_style="standard")
    book, world_base, cast_base, world, cast = _review_assets(project)
    cast = {**cast, "protagonist": {**cast["protagonist"], "flaw": "derived present-tense flaw"}}
    calls = 0

    async def fake_review(_session, _settings, request):
        nonlocal calls
        calls += 1
        if calls == 1:
            response = _semantic_review_response(request)
            payload = json.loads(response.content)
            payload["candidate_evidence"] = [
                item for item in payload["candidate_evidence"] if item["field"] != "cast.protagonist"
            ]
            response.content = json.dumps(payload, ensure_ascii=False)
            return response
        return _evidence_repair_response(request, malicious_decision=True)

    monkeypatch.setattr("bestseller.services.planner.complete_text", fake_review)
    report = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert calls == 2
    assert report["decision"] == "uncertain"
    assert report["evidence_repair_status"] == "blocked"
    assert report["evidence_repair_attempts"][0]["reason"] == "evidence_only_contract_violation"


@pytest.mark.asyncio
async def test_source_bound_review_does_not_repair_evidence_after_p0_semantic_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(cost_style="standard")
    book, world_base, cast_base, world, cast = _review_assets(project)
    cast = {**cast, "protagonist": {**cast["protagonist"], "flaw": "derived present-tense flaw"}}
    calls = 0

    async def fake_review(_session, _settings, request):
        nonlocal calls
        calls += 1
        return _semantic_review_response(request, decision="revise")

    monkeypatch.setattr("bestseller.services.planner.complete_text", fake_review)
    report = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert calls == 1
    assert report["decision"] == "revise"
    assert report["evidence_repair_status"] == "not_needed"
    assert report["evidence_repair_attempts"] == []


@pytest.mark.asyncio
async def test_source_bound_review_retries_one_real_length_receipt_with_same_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(cost_style="standard")
    book, world_base, cast_base, world, cast = _review_assets(project)
    cast = {**cast, "protagonist": {**cast["protagonist"], "flaw": "derived present-tense flaw"}}
    calls: list[object] = []

    async def fake_review(_session, _settings, request):
        calls.append(request)
        if len(calls) == 1:
            return SimpleNamespace(
                content="{\"decision\":\"pass\"}",
                provider="openai",
                finish_reason="length",
                llm_run_id=uuid4(),
                fallback_used=False,
            )
        return _semantic_review_response(request)

    monkeypatch.setattr("bestseller.services.planner.complete_text", fake_review)
    report = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert report["decision"] == "pass"
    assert report["evidence_verified"] is True
    assert len(calls) == 2
    assert calls[1].max_tokens_override > calls[0].max_tokens_override
    assert "STRICT LENGTH RETRY" in calls[1].user_prompt
    for request in calls:
        assert request.metadata["authority_snapshot_hash"] == calls[0].metadata["authority_snapshot_hash"]
        assert request.metadata["candidate_hash"] == calls[0].metadata["candidate_hash"]
    assert len(report["review_attempts"]) == 2
    assert report["review_attempts"][0]["finish_reason"] == "length"
    assert report["review_attempts"][1]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_source_bound_review_retries_only_once_then_blocks_two_lengths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(cost_style="standard")
    book, world_base, cast_base, world, cast = _review_assets(project)
    calls = 0

    async def fake_review(_session, _settings, request):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            content="{\"decision\":\"pass\"}",
            provider="openai",
            finish_reason="length",
            llm_run_id=uuid4(),
            fallback_used=False,
        )

    monkeypatch.setattr("bestseller.services.planner.complete_text", fake_review)
    report = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert calls == 2
    assert report["reason"] == "provider_receipt_rejected"
    assert report["decision"] == "uncertain"
    assert [item["finish_reason"] for item in report["review_attempts"]] == ["length", "length"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider, fallback_used",
    [("mock", False), ("fallback", True)],
)
async def test_source_bound_review_does_not_retry_mock_or_fallback_length(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    fallback_used: bool,
) -> None:
    project = _project(cost_style="standard")
    book, world_base, cast_base, world, cast = _review_assets(project)
    calls = 0

    async def fake_review(_session, _settings, request):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            content="{\"decision\":\"pass\"}",
            provider=provider,
            finish_reason="length",
            llm_run_id=uuid4(),
            fallback_used=fallback_used,
        )

    monkeypatch.setattr("bestseller.services.planner.complete_text", fake_review)
    report = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert calls == 1
    assert report["reason"] == "provider_receipt_rejected"
    assert len(report["review_attempts"]) == 1


@pytest.mark.asyncio
async def test_source_bound_review_does_not_retry_semantic_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(cost_style="standard")
    book, world_base, cast_base, world, cast = _review_assets(project)
    calls = 0

    async def fake_review(_session, _settings, request):
        nonlocal calls
        calls += 1
        return _semantic_review_response(request, decision="revise")

    monkeypatch.setattr("bestseller.services.planner.complete_text", fake_review)
    report = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert calls == 1
    assert report["decision"] == "revise"
    assert report["status"] == "blocked"


@pytest.mark.asyncio
async def test_source_bound_review_resolves_v5_alias_quote_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(cost_style="standard")
    book, world_base, cast_base, world, cast = _review_assets(project)
    expected_fields = [
        "world.locations",
        "world.factions",
        "cast.antagonist",
        "cast.supporting_cast",
    ]

    async def fake_review(_session, _settings, request):
        import json
        import re

        hashes = dict(
            re.findall(
                r"^(authority_snapshot_hash|candidate_hash)=([0-9a-f]{64})$",
                request.user_prompt,
                re.MULTILINE,
            )
        )
        assert '"quote_id": "s0"' in request.user_prompt
        assert '"quote_id": "c0"' in request.user_prompt
        by_field = {
            "world.locations": "c2",
            "world.factions": "c0",
            "cast.antagonist": "c4",
            "cast.supporting_cast": "c6",
        }
        fields = expected_fields
        return SimpleNamespace(
            content=json.dumps(
                {
                    "decision": "pass",
                    "core_preserved": True,
                    "core_conflicts": [],
                    "source_evidence": [
                        {
                            "field": field.replace("world.", "world_spec.").replace("cast.", "cast_spec."),
                            "quote_id": "s0",
                            "source_relation": "approved hook",
                        }
                        for field in fields
                    ],
                    "candidate_evidence": [
                        {
                            "field": field.replace("world.", "world_spec.").replace("cast.", "cast_spec."),
                            "quote_id": by_field[field],
                            "source_relation": "bounded present-tense derivation",
                        }
                        for field in fields
                    ],
                    "authority_snapshot_hash": hashes["authority_snapshot_hash"],
                    "candidate_hash": hashes["candidate_hash"],
                },
                ensure_ascii=False,
            ),
            provider="litellm",
            finish_reason="stop",
            llm_run_id=uuid4(),
            fallback_used=False,
        )

    monkeypatch.setattr("bestseller.services.planner.complete_text", fake_review)
    report = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert report["decision"] == "pass", (
        report.get("reason"),
        report.get("changed_fields"),
        [(item.get("field"), item.get("quote")) for item in report.get("source_evidence", [])],
        [(item.get("field"), item.get("quote")) for item in report.get("candidate_evidence", [])],
    )
    assert report["evidence_verified"] is True
    assert {item["field"] for item in report["candidate_evidence"]} == set(expected_fields)


@pytest.mark.asyncio
async def test_source_bound_review_separates_core_eligibility_from_quality_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(cost_style="standard")
    book, world_base, cast_base, world, cast = _review_assets(project)
    responses = iter(
        [
            {
                "decision": "uncertain",
                "core_preserved": True,
                "core_conflicts": [],
                "quality_findings": ["具体仙师仍需正文验证"],
            },
            {
                "decision": "pass",
                "core_preserved": False,
                "core_conflicts": ["替换核心能力"],
                "quality_findings": [],
            },
        ]
    )

    async def fake_review(_session, _settings, request):
        import json
        import re

        hashes = dict(
            re.findall(
                r"^(authority_snapshot_hash|candidate_hash)=([0-9a-f]{64})$",
                request.user_prompt,
                re.MULTILINE,
            )
        )
        base = next(responses)
        return SimpleNamespace(
            content=json.dumps(
                {
                    **base,
                    "authority_snapshot_hash": hashes["authority_snapshot_hash"],
                    "candidate_hash": hashes["candidate_hash"],
                    "source_evidence": [
                        {"field": field, "quote": "approved hook", "source_relation": "hook"}
                        for field in [
                            "world.locations",
                            "world.factions",
                            "cast.antagonist",
                            "cast.supporting_cast",
                        ]
                    ],
                    "candidate_evidence": [
                        {"field": "world.locations", "quote": "North Archive", "source_relation": "location"},
                        {"field": "world.factions", "quote": "Archive Wardens", "source_relation": "faction"},
                        {"field": "cast.antagonist", "quote": "The Broker", "source_relation": "antagonist"},
                        {"field": "cast.supporting_cast", "quote": "Mira", "source_relation": "supporting"},
                    ],
                },
                ensure_ascii=False,
            ),
            provider="litellm",
            finish_reason="stop",
            llm_run_id=uuid4(),
            fallback_used=False,
        )

    monkeypatch.setattr("bestseller.services.planner.complete_text", fake_review)
    eligible = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert eligible["decision"] == "uncertain"
    assert eligible["draft_eligible"] is True
    assert eligible["quality_findings"] == ["具体仙师仍需正文验证"]

    blocked = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert blocked["decision"] == "pass"
    assert blocked["core_conflicts"] == ["替换核心能力"]
    assert blocked["draft_eligible"] is False


@pytest.mark.asyncio
async def test_source_bound_expansion_review_blocks_revise_and_mock_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(cost_style="minimal")
    book, world_base, cast_base, world, cast = _review_assets(project)

    async def revise_review(_session, _settings, request):
        import json

        return SimpleNamespace(
            content=json.dumps(
                {
                    "decision": "revise",
                    "feedback": "second ability source",
                    "authority_snapshot_hash": request.metadata["authority_snapshot_hash"],
                    "candidate_hash": request.metadata["candidate_hash"],
                }
            ),
            provider="litellm",
            finish_reason="stop",
            llm_run_id=uuid4(),
            fallback_used=False,
        )

    monkeypatch.setattr("bestseller.services.planner.complete_text", revise_review)
    revise = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert revise["decision"] == "revise"
    assert revise["status"] == "blocked"

    unavailable = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=True)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived=world,
        cast_derived=cast,
        workflow_run_id=uuid4(),
    )
    assert unavailable["decision"] == "uncertain"
    assert unavailable["reason"] == "mock_provider_rejected"
    assert unavailable["status"] == "blocked"

    sparse = await _review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        cast_baseline=cast_base,
        world_derived={**world_base, "_meta": {"source_bound_expansion": "derived_pending_review"}},
        cast_derived={**cast_base, "_meta": {"source_bound_expansion": "derived_pending_review"}},
        workflow_run_id=uuid4(),
    )
    assert sparse["decision"] == "uncertain"
    assert sparse["reason"] == "no_actual_changed_fields"
    assert sparse["status"] == "blocked"


@pytest.mark.asyncio
async def test_world_expansion_must_pass_before_import_and_clears_pending_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(cost_style="standard")
    monkeypatch.setattr("bestseller.services.planner._source_bound_cast_enabled", lambda _: True)
    book, world_base, _cast_base, world, _cast = _review_assets(project)
    receipt_id = uuid4()

    async def approved_review(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "decision": "pass",
            "status": "passed",
            "changed_fields": ["world.locations", "world.factions"],
            "reason": "reviewed",
            "feedback": "fixture review receipt",
            "llm_run_id": str(receipt_id),
            "authority_snapshot_hash": "a" * 64,
            "canonical_baseline_hash": "b" * 64,
            "candidate_hash": "c" * 64,
            "authority_snapshot_hash_echo": "a" * 64,
            "candidate_hash_echo": "c" * 64,
            "source_evidence": [
                {"field": "world.locations", "quote": "approved hook", "source_relation": "hook"},
                {"field": "world.factions", "quote": "approved hook", "source_relation": "spine"},
            ],
            "candidate_evidence": [
                {"field": "world.locations", "quote": "North Archive", "source_relation": "present-tense location"},
                {"field": "world.factions", "quote": "Archive Wardens", "source_relation": "present-tense faction"},
            ],
            "evidence_verified": True,
            "raw_response_hash": "d" * 64,
            "provider": "litellm",
            "finish_reason": "stop",
        }

    monkeypatch.setattr("bestseller.services.planner._review_source_bound_expansion", approved_review)
    reviewed = await _approve_source_bound_world_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=world_base,
        world_derived=world,
        workflow_run_id=uuid4(),
    )

    assert reviewed["locations"] == world["locations"]
    assert reviewed["factions"] == world["factions"]
    meta = reviewed["_meta"]
    assert meta["source_bound_expansion"] == "reviewed"
    assert meta["source_bound_expansion_review"] == "passed"
    assert meta["source_bound_expansion_review_receipt"]["llm_run_id"] == str(receipt_id)
    assert meta["source_bound_expansion_review_receipt"]["decision"] == "pass"
    assert "changed_fields" in meta["source_bound_expansion_review_receipt"]
    assert "reason" in meta["source_bound_expansion_review_receipt"]
    assert "feedback" in meta["source_bound_expansion_review_receipt"]
    assert meta["source_bound_expansion_review_receipt"]["source_evidence"]


@pytest.mark.asyncio
async def test_world_expansion_rejection_blocks_before_any_consumption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project()
    monkeypatch.setattr("bestseller.services.planner._source_bound_cast_enabled", lambda _: True)
    book, world_base, _cast_base, world, _cast = _review_assets(project)

    async def rejected_review(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"decision": "revise", "status": "blocked", "feedback": "second ability source"}

    monkeypatch.setattr("bestseller.services.planner._review_source_bound_expansion", rejected_review)
    with pytest.raises(PlannerFallbackError, match="World-bound expansion semantic review blocked"):
        await _approve_source_bound_world_expansion(
            None,
            SimpleNamespace(llm=SimpleNamespace(mock=False)),
            project=project,
            book_spec=book,
            world_baseline=world_base,
            world_derived=world,
            workflow_run_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_world_expansion_pass_with_explicit_core_conflict_still_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project()
    monkeypatch.setattr("bestseller.services.planner._source_bound_cast_enabled", lambda _: True)
    book, world_base, _cast_base, world, _cast = _review_assets(project)

    async def false_pass(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "decision": "pass",
            "core_preserved": False,
            "core_conflicts": ["替换核心能力"],
            "draft_eligible": False,
            "feedback": "core conflict",
        }

    monkeypatch.setattr("bestseller.services.planner._review_source_bound_expansion", false_pass)
    with pytest.raises(PlannerFallbackError, match="World-bound expansion semantic review blocked"):
        await _approve_source_bound_world_expansion(
            None,
            SimpleNamespace(llm=SimpleNamespace(mock=False)),
            project=project,
            book_spec=book,
            world_baseline=world_base,
            world_derived=world,
            workflow_run_id=uuid4(),
        )


def test_world_review_precedes_world_import_and_cast_consumption() -> None:
    for function in (planner_services.generate_novel_plan, planner_services.generate_foundation_plan):
        source = inspect.getsource(function)
        review_pos = source.find("await _approve_source_bound_world_expansion(")
        import_pos = source.find("world_artifact = await import_planning_artifact(", review_pos)
        cast_pos = source.find("cast_spec_fallback", import_pos)
        assert review_pos >= 0
        assert import_pos > review_pos
        assert cast_pos > import_pos

@pytest.mark.asyncio
async def test_real_canonical_planner_wrapper_accepts_source_without_inventing_three_chapter_hooks(
    monkeypatch: pytest.MonkeyPatch, canonical_planning_entry,
) -> None:
    # Replace the old broad FakeSession surrogate with the real source-bound
    # producer, canonical DB readback and exact-source race guard. This covers
    # the producer boundary, not downstream prewrite/literary acceptance.
    import json
    from copy import deepcopy
    from sqlalchemy import select
    from test_source_bound_planning_model_completion_entry import completion_transport
    from bestseller.infra.db.models import PlanningArtifactVersionModel
    from bestseller.services.source_bound_planning_content import PLANNING_PROMPT_VERSION
    from bestseller.settings import load_settings

    entry = canonical_planning_entry
    source_before = deepcopy(entry.canonical.content)
    seriality = source_before["concept_contract"]["seriality_proof"]
    assert not seriality.get("first_three_chapter_hook")
    completion_transport(monkeypatch, entry)
    result, run_id = await planner_services._complete_source_bound_planning_content(
        entry.session, load_settings(), project=entry.project, premise="观测站故事",
        kind="book_spec", projection=entry.projection, workflow_run_id=entry.workflow.id,
    )
    assert run_id is not None and len(entry.calls) == 1
    request = entry.calls[0]
    rendered, _ = json.JSONDecoder().raw_decode(request.user_prompt)
    assert rendered["approved_sources"]["canonical_snapshot"]["content"] == source_before
    assert request.prompt_version == PLANNING_PROMPT_VERSION
    assert result["_meta"]["generation_llm_run_id"] == str(run_id)
    assert result["_meta"]["creator_authorship_asserted"] is False
    original = await entry.session.scalar(select(PlanningArtifactVersionModel).where(
        PlanningArtifactVersionModel.id == entry.canonical.id
    ))
    assert original.content == source_before and original.status == "canonical"



@pytest.mark.parametrize(
    "mutation",
    [
        lambda metadata: metadata.pop("book_design_snapshot"),
        lambda metadata: metadata["book_design_snapshot"].pop("snapshot_id"),
        lambda metadata: metadata["book_design_snapshot"].pop("source_hash"),
        lambda metadata: metadata["book_design_snapshot"].pop("protagonist"),
        lambda metadata: metadata.__setitem__("book_design_snapshot_status", "draft"),
    ],
)
def test_source_bound_authority_fails_closed_without_a_complete_locked_snapshot(mutation) -> None:
    project = _project(cost_style="standard")
    mutation(project.metadata_json)

    assert not _source_bound_cast_enabled(project)


@pytest.mark.parametrize("cost_style", ["standard", "external", "minimal"])
def test_all_cost_styles_allow_derived_asset_expansion_without_replacing_approved_core(
    cost_style: str,
) -> None:
    project = _project(cost_style=cost_style)
    book = {
        "protagonist": {"name": "林照"},
        "unique_hook": "守住废符铺",
        "series_engine": {"core_serial_engine": "制符—交易—反制"},
    }
    world = {
        "world_premise": "批准的符铺世界",
        "rules": [{"rule_id": "R1", "description": "批准规则"}],
        "factions": [{"name": "当前直接阻力"}],
    }
    assert _source_bound_world_needs_expansion(world)
    assert _source_bound_cast_needs_expansion({"protagonist": book["protagonist"]})
    contract = _source_bound_expansion_contract(
        project, asset="cast", book_spec=book, world_spec=world
    )
    assert "林照" in contract
    assert "没有明确来源限制时也可以规划多种能力或机制" in contract
    assert "不得新增第二能力来源" not in contract

    cast_fallback = {
        "protagonist": {"name": "林照", "goal": "守住废符铺"},
        "antagonist": None,
        "supporting_cast": [],
        "_meta": {"source_hash": "approved"},
    }
    expanded_cast = _rebind_source_bound_cast_expansion(
        cast_fallback,
        {
            "protagonist": {"name": "换主角", "golden_finger": "另一套能力", "voice_profile": {"speech_register": "对熟客用短句，对外人惜字"}},
            "antagonist": {"name": "符铺对手", "goal": "争夺符谱"},
            "supporting_cast": [{"name": "守铺人", "goal": "守住铺子"}],
        },
    )
    assert expanded_cast["protagonist"]["name"] == "林照"
    assert expanded_cast["protagonist"].get("golden_finger") != "另一套能力"
    assert expanded_cast["protagonist"]["voice_profile"]["speech_register"] == "对熟客用短句，对外人惜字"
    assert expanded_cast["antagonist"]["name"] == "符铺对手"
    assert expanded_cast["_meta"]["source_bound_expansion"] == "derived_pending_review"
    assert expanded_cast["_meta"]["source_bound_expansion_review"] == "independent_semantic_review_required"
    unavailable_cast = _rebind_source_bound_cast_expansion(cast_fallback, cast_fallback)
    assert unavailable_cast["_meta"]["source_bound_expansion"] == "unavailable"

    world_fallback = {**world, "_meta": {"source_hash": "approved"}}
    expanded_world = _rebind_source_bound_world_expansion(
        world_fallback,
        {
            "world_premise": "改写后的世界",
            "rules": [{"rule_id": "BAD", "description": "替换规则"}],
            "factions": [{"name": "符盟"}, {"name": "监察司"}],
        },
    )
    assert expanded_world["world_premise"] == "批准的符铺世界"
    assert expanded_world["rules"] == world["rules"]
    assert [item["name"] for item in expanded_world["factions"]] == [
        "当前直接阻力",
        "符盟",
        "监察司",
    ]
    unavailable_world = _rebind_source_bound_world_expansion(world_fallback, world_fallback)
    assert unavailable_world["_meta"]["source_bound_expansion"] == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode, expected_calls, reason",
    [
        ("normal", 1, None),
        ("malformed_then_valid", 2, None),
        ("malformed_twice", 2, "malformed_review_json"),
        ("wrong_hash", 1, "review_hash_mismatch"),
        ("missing_hash", 1, "review_hash_mismatch"),
        ("invalid_decision", 1, "invalid_decision"),
        ("missing_decision", 1, "invalid_decision"),
        ("fallback", 1, "provider_receipt_rejected"),
        ("malformed_then_fallback", 2, "provider_receipt_rejected"),
        ("length_then_malformed", 2, "malformed_review_json"),
        ("duplicate_keys", 2, "malformed_review_json"),
        ("ambiguous_envelope", 2, "malformed_review_json"),
        ("single_envelope", 1, None),
    ],
)
async def test_source_bound_review_strict_format_boundary(
    monkeypatch: pytest.MonkeyPatch, mode: str, expected_calls: int, reason: str | None
) -> None:
    import json

    project = _project(cost_style="standard")
    book, world_base, cast_base, world, cast = _review_assets(project)
    calls: list[object] = []
    responses: list[object] = []

    async def fake_review(_session, _settings, request):
        calls.append(request)
        response = _semantic_review_response(request)
        payload = json.loads(response.content)
        if mode == "wrong_hash":
            payload["candidate_hash"] = "0" * 64
        elif mode == "missing_hash":
            payload.pop("authority_snapshot_hash")
        elif mode == "invalid_decision":
            payload["decision"] = "approved"
        elif mode == "missing_decision":
            payload.pop("decision")
        response.content = json.dumps(payload)
        malformed = (
            mode in {"malformed_twice", "length_then_malformed"}
            or (mode in {"malformed_then_valid", "malformed_then_fallback"} and len(calls) == 1)
        )
        if malformed:
            # The observed failure closed quality_findings before stray strings.
            response.content = response.content.replace(
                '"quality_findings": []', '"quality_findings": ["first"], "stray", "another"'
            )
        if mode in {"single_envelope", "ambiguous_envelope"}:
            envelope = {"source_bound_expansion_semantic_review": payload}
            if mode == "ambiguous_envelope":
                envelope["decision"] = "revise"
            response.content = json.dumps(envelope)
        if mode == "duplicate_keys":
            response.content = '{"decision":"revise",' + response.content[1:]
        if mode == "fallback" or (mode == "malformed_then_fallback" and len(calls) == 2):
            response.fallback_used = True
        if mode == "length_then_malformed" and len(calls) == 1:
            response.finish_reason = "length"
        responses.append(response)
        return response

    monkeypatch.setattr("bestseller.services.planner.complete_text", fake_review)
    report = await _review_source_bound_expansion(
        None, SimpleNamespace(llm=SimpleNamespace(mock=False)), project=project,
        book_spec=book, world_baseline=world_base, cast_baseline=cast_base,
        world_derived=world, cast_derived=cast, workflow_run_id=uuid4(),
    )
    assert len(calls) == expected_calls
    assert len(report["review_attempts"]) == expected_calls
    assert [r["llm_run_id"] for r in report["review_attempts"]] == [
        str(r.llm_run_id) for r in responses
    ]
    if reason:
        assert report["reason"] == reason
        assert report["status"] == "blocked"
        assert report["draft_eligible"] is False
    else:
        assert report["decision"] == "pass"
        assert report["evidence_verified"] is True
    if expected_calls == 2:
        assert calls[1].user_prompt.startswith(calls[0].user_prompt)
        for key in ("authority_snapshot_hash", "candidate_hash"):
            assert calls[1].metadata[key] == calls[0].metadata[key]
        if mode != "length_then_malformed":
            assert calls[1].metadata["retry_reason"] == "malformed_review_json"
            assert calls[1].metadata["length_retry"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("supported", [None, False, True])
async def test_unknown_protagonist_gender_needs_specific_independent_evidence_confirmation(monkeypatch, supported):
    import json

    project = _project(cost_style="standard")
    book, world_base, cast_base, world, cast = _review_assets(project)
    cast_base["protagonist"] = {"name": "沈宁", "gender": "unknown"}
    cast["protagonist"] = {
        "name": "沈宁", "gender": "female", "pronoun_set_zh": "她", "pronoun_set_en": "she/her",
        "identity_evidence": "配角李梅是女性。" if supported is not True else "沈宁是女性。",
    }

    async def review(_session, _settings, request):
        assert "supporting character, spouse or opponent is NOT evidence" in request.user_prompt
        response = _semantic_review_response(request)
        payload = json.loads(response.content)
        if supported is not None:
            payload["protagonist_identity_supported"] = supported
        response.content = json.dumps(payload)
        return response

    monkeypatch.setattr(planner_services, "complete_text", review)
    report = await _review_source_bound_expansion(
        None, SimpleNamespace(llm=SimpleNamespace(mock=False)), project=project,
        book_spec=book, world_baseline=world_base, cast_baseline=cast_base,
        world_derived=world, cast_derived=cast, workflow_run_id=uuid4(),
    )
    assert report["protagonist_identity_check_required"] is True
    assert report["protagonist_identity_supported"] is supported
    if supported is True:
        assert report["decision"] == "pass"
    else:
        assert report["decision"] != "pass"
        assert report["reason"] == "protagonist_identity_evidence_unconfirmed"
